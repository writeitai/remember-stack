"""The D138 ``card`` route: a short description of a file memory does not read.

A card names the file (name, source path), its family, format and byte size,
plus what the format declares cheaply: an image's width, height and format
from its header (Pillow; pixels are not decoded), a zip's member list from
its central directory, a tar's member list from a bounded streamed read. A
file over its family's reading limit gets a card that says so instead of a
reading. The whole card is labelled ``file_card`` / ``computed`` and is never
claim-extracted; the original stays stored and served as always (D51).

Bytes that contradict their family (an image Pillow reads that is not an
image, a zip or tar that is not one) fail the version. Formats this runtime
cannot read by design (HEIC, zstd-compressed tar) are carded with a warning.
"""

import bz2
import gzip
import io
import logging
import lzma
import tarfile
from typing import Final
import zipfile
import zlib

from PIL import Image
from PIL import UnidentifiedImageError

from rememberstack.core.format_registry import family_for_mime
from rememberstack.core.format_registry import FORMAT_MIMES
from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import DerivationRange
from rememberstack.model import FileHints
from rememberstack.model import ManifestComponent

_logger = logging.getLogger(__name__)

CARD_CONVERTER_VERSION: Final = "card-2026.09b"
"""Pins the card's fields, listing limits and Markdown shape."""

MAX_LISTED_MEMBERS: Final = 200
"""Archive members listed at most (D138 §6 starting value)."""

MAX_TAR_SCAN_BYTES: Final = 64_000_000
"""A tar listing reads at most this many decompressed bytes."""

MAX_SHOWN_PATH_CHARS: Final = 300
"""Member paths longer than this are cut on the card."""

_ZIP_MIME: Final = "application/zip"
_TAR_MIME: Final = FORMAT_MIMES["tar"]
_ZSTD_MAGIC: Final = b"\x28\xb5\x2f\xfd"


class CardConverter:
    """Describe a file without reading it: images, media, archives, binaries."""

    accepts_file_hints: bool = True

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "card"

    @property
    def version(self) -> str:
        """The pinned card route version (D38)."""
        return CARD_CONVERTER_VERSION

    def convert(
        self, *, content: bytes, mime: str, hints: FileHints | None = None
    ) -> ConversionResult:
        """Build the card; its facts come from headers and directories only."""
        family = family_for_mime(mime=mime)
        file_name = _one_line(value=hints.file_name if hints is not None else None)
        source_path = _one_line(value=hints.source_path if hints is not None else None)
        fields = [
            ("File name", file_name or "unknown"),
            ("Source path", source_path or "unknown"),
            ("Family", family.name),
            ("Format", mime),
            ("Byte size", f"{len(content):,} bytes"),
        ]
        sections: list[str] = []
        warnings: list[str] = []
        limit = family.reading_limit_bytes
        if limit is not None and len(content) > limit:
            fields.append(
                (
                    "Not read",
                    f"the file is larger than the {limit:,}-byte reading limit "
                    f"for {family.name} files",
                )
            )
        elif family.name == "image":
            fields.append(_image_field(content=content, mime=mime, warnings=warnings))
        elif mime == _ZIP_MIME:
            sections.append(_zip_listing(content=content))
        elif mime == _TAR_MIME:
            sections.append(_tar_listing(content=content, warnings=warnings))
        document_md = (
            "\n".join(
                (
                    f"# {file_name or 'Unnamed file'}",
                    "",
                    "File card: memory describes this file but does not read it; "
                    "open the original to see its contents.",
                    "",
                    *(f"- {label}: {value}" for label, value in fields),
                    *(f"\n{section}" for section in sections),
                )
            )
            + "\n"
        )
        return ConversionResult(
            document_md=document_md,
            warnings=tuple(warnings),
            manifest=ConverterManifest(
                components=(
                    ManifestComponent(
                        name="card",
                        version=CARD_CONVERTER_VERSION,
                        execution="library-local",
                    ),
                ),
                coverage=ConversionCoverage(policy="card", complete=False),
                derivation_ranges=(
                    DerivationRange(
                        start=0,
                        end=len(document_md),
                        derivation_kind="file_card",
                        evidence_mode="computed",
                    ),
                ),
            ),
        )


def _image_field(*, content: bytes, mime: str, warnings: list[str]) -> tuple[str, str]:
    """Width, height and format from the image header; pixels stay undecoded.

    A format Pillow reads whose header it cannot read is not that image and
    fails the version; a format Pillow cannot read (HEIC) is carded with a
    warning.
    """
    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            image_format = image.format or "unknown"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as err:
        Image.init()
        if mime in set(Image.MIME.values()):
            raise ConversionError(
                f"bytes stored as {mime!r} are not a readable image"
            ) from err
        _logger.warning("image header of %s cannot be read here", mime, exc_info=True)
        reason = f"not read (this runtime cannot read {mime} headers)"
        warnings.append(f"image dimensions {reason}")
        return ("Dimensions", reason)
    return ("Dimensions", f"{width} × {height} px, {image_format}")


def _zip_listing(*, content: bytes) -> str:
    """Member count and the first members with sizes, from the central directory."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            count = len(infos)
            shown = [
                (info.filename, info.file_size) for info in infos[:MAX_LISTED_MEMBERS]
            ]
    except (zipfile.BadZipFile, OSError, ValueError) as err:
        raise ConversionError("bytes stored as a zip archive are not one") from err
    heading = f"## Members ({count:,})"
    if count > len(shown):
        heading += f"\n\nThe first {len(shown)} members are listed."
    return heading + "\n\n" + _member_lines(members=shown)


def _tar_listing(*, content: bytes, warnings: list[str]) -> str:
    """Members from a streamed read bounded to 200 members and 64 MB.

    The bound applies to decompressed bytes before tarfile parses them, so
    no header (a PAX record included) can expand past it.
    """
    if content.startswith(_ZSTD_MAGIC):
        reason = "zstd-compressed tar archives cannot be listed by this runtime"
        warnings.append(reason)
        return f"## Members\n\nNot listed: {reason}."
    stream = _BoundedReader(
        raw=_decompressed(content=content), limit=MAX_TAR_SCAN_BYTES
    )
    members: list[tuple[str, int]] = []
    partial = False
    try:
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for member in archive:
                if len(members) == MAX_LISTED_MEMBERS:
                    partial = True
                    break
                members.append((member.name, member.size))
                if member.offset_data + member.size > MAX_TAR_SCAN_BYTES:
                    partial = True
                    break
    except (tarfile.TarError, EOFError, OSError, zlib.error, lzma.LZMAError) as err:
        if not stream.exhausted:
            raise ConversionError(
                "bytes stored as a tar archive are not a readable one"
            ) from err
        partial = True
    partial = partial or stream.exhausted
    if partial:
        warnings.append(f"tar listing stopped after {len(members)} members")
    heading = (
        f"## Members (at least {len(members):,}; the listing is partial)"
        if partial
        else f"## Members ({len(members):,})"
    )
    return heading + "\n\n" + _member_lines(members=members)


def _decompressed(*, content: bytes) -> io.BufferedIOBase:
    """The tar stream: decompressed lazily when gzip, bzip2 or xz."""
    raw = io.BytesIO(content)
    if content.startswith(b"\x1f\x8b"):
        return gzip.GzipFile(fileobj=raw)
    if content.startswith(b"BZh"):
        return bz2.BZ2File(raw)
    if content.startswith(b"\xfd7zXZ\x00"):
        return lzma.LZMAFile(raw)
    return raw


class _BoundedReader(io.RawIOBase):
    """A read-only stream that ends after ``limit`` bytes of its source."""

    def __init__(self, *, raw: io.BufferedIOBase, limit: int) -> None:
        """Wrap ``raw``; reads past ``limit`` bytes return end of stream."""
        self._raw = raw
        self._remaining = limit
        self.exhausted = False

    def readable(self) -> bool:
        """The stream is readable."""
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        """Fill ``buffer`` from the source without passing the limit."""
        if self._remaining <= 0:
            self.exhausted = True
            return 0
        wanted = min(len(buffer), self._remaining)
        data = self._raw.read(wanted)
        buffer[: len(data)] = data
        self._remaining -= len(data)
        self.exhausted = self._remaining <= 0
        return len(data)


def _member_lines(*, members: list[tuple[str, int]]) -> str:
    """One Markdown list line per member: its (cut) path and size."""
    return "\n".join(
        f"- {_one_line(value=name)[:MAX_SHOWN_PATH_CHARS]} ({size:,} bytes)"
        for name, size in members
    )


def _one_line(*, value: str | None) -> str:
    """A name or path on one line: carriage returns and newlines become spaces."""
    return (value or "").replace("\r", " ").replace("\n", " ")
