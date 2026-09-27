"""The D138 ``card`` route: a short description of a file memory does not read.

A card names the file (name, source path), its family, format and byte size,
plus what the format declares cheaply: an image's width, height and format
from its header (Pillow; pixels are not decoded), a zip's member list from
its central directory, a tar's member list from a bounded streamed read. A
file over its family's reading limit gets a card that says so instead of a
reading. The whole card is labelled ``file_card`` / ``computed`` and is never
claim-extracted; the original stays stored and served as always (D51).
"""

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
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import DerivationRange
from rememberstack.model import FileHints
from rememberstack.model import ManifestComponent

_logger = logging.getLogger(__name__)

CARD_CONVERTER_VERSION: Final = "card-2026.09"
"""Pins the card's fields, listing limits and Markdown shape."""

MAX_LISTED_MEMBERS: Final = 200
"""Archive members listed at most (D138 §6 starting value)."""

MAX_TAR_SCAN_BYTES: Final = 64_000_000
"""A tar listing stops once its streamed read passes this many bytes."""

_ZIP_MIME: Final = "application/zip"
_TAR_MIME: Final = FORMAT_MIMES["tar"]


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
        file_name = hints.file_name if hints is not None else None
        source_path = hints.source_path if hints is not None else None
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
            fields.append(_image_field(content=content, warnings=warnings))
        elif mime == _ZIP_MIME:
            sections.append(_zip_listing(content=content, warnings=warnings))
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


def _image_field(*, content: bytes, warnings: list[str]) -> tuple[str, str]:
    """Width, height and format from the image header; pixels stay undecoded."""
    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            image_format = image.format or "unknown"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as err:
        _logger.warning("image header could not be read", exc_info=True)
        reason = f"not read ({type(err).__name__}: {err})"
        warnings.append(f"image dimensions {reason}")
        return ("Dimensions", reason)
    return ("Dimensions", f"{width} × {height} px, {image_format}")


def _zip_listing(*, content: bytes, warnings: list[str]) -> str:
    """Member count and the first members with sizes, from the central directory."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [(info.filename, info.file_size) for info in archive.infolist()]
    except (zipfile.BadZipFile, OSError, ValueError) as err:
        return _unlisted(err=err, kind="zip", warnings=warnings)
    shown = members[:MAX_LISTED_MEMBERS]
    heading = f"## Members ({len(members):,})"
    if len(members) > len(shown):
        heading += f"\n\nThe first {len(shown)} members are listed."
    return heading + "\n\n" + _member_lines(members=shown)


def _tar_listing(*, content: bytes, warnings: list[str]) -> str:
    """Members from a streamed read that stops at 200 members or 64 MB."""
    members: list[tuple[str, int]] = []
    partial = False
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r|*") as archive:
            for member in archive:
                if len(members) == MAX_LISTED_MEMBERS:
                    partial = True
                    break
                members.append((member.name, member.size))
                if member.offset_data + member.size > MAX_TAR_SCAN_BYTES:
                    partial = True
                    break
    except (tarfile.TarError, EOFError, OSError, zlib.error, lzma.LZMAError) as err:
        return _unlisted(err=err, kind="tar", warnings=warnings)
    heading = (
        f"## Members (at least {len(members):,}; the listing is partial)"
        if partial
        else f"## Members ({len(members):,})"
    )
    return heading + "\n\n" + _member_lines(members=members)


def _unlisted(*, err: Exception, kind: str, warnings: list[str]) -> str:
    """Say on the card that the archive could not be listed, and why."""
    _logger.warning("%s archive could not be listed", kind, exc_info=True)
    reason = f"the {kind} archive could not be listed ({type(err).__name__}: {err})"
    warnings.append(reason)
    return f"## Members\n\nNot listed: {reason}."


def _member_lines(*, members: list[tuple[str, int]]) -> str:
    """One Markdown list line per member: its path and size."""
    return "\n".join(
        f"- {name.replace(chr(10), ' ')} ({size:,} bytes)" for name, size in members
    )
