"""Conservative byte classification before E0 routing and storage."""

from __future__ import annotations

import io
import re
import zipfile

from rememberstack.model.content_detection import ContentDetectionError

_OFFICE_MIMES = {
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
}
_TEXT_HINTS = {"text/markdown", "text/x-markdown"}
_TEXT_APPLICATION_HINTS = {
    "application/javascript",
    "application/ecmascript",
    "application/json",
    "application/ld+json",
    "application/x-ndjson",
    "application/x-yaml",
    "application/yaml",
    "application/toml",
    "application/x-sh",
}
_IMAGE_MIME_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
    "image/heif": "image/heic",
}
_AUDIO_MIME_ALIASES = {"audio/x-m4a": "audio/mp4"}


def _looks_like_bmp(*, content: bytes) -> bool:
    """Require BMP file and DIB header fields, not the printable `BM` alone."""
    if len(content) < 26 or not content.startswith(b"BM"):
        return False
    size = int.from_bytes(content[2:6], "little")
    pixel_offset = int.from_bytes(content[10:14], "little")
    dib_size = int.from_bytes(content[14:18], "little")
    return (
        26 <= size <= len(content)
        and content[6:10] == b"\x00" * 4
        and 26 <= pixel_offset <= size
        and 12 <= dib_size <= len(content) - 14
    )


def _looks_like_id3(*, content: bytes) -> bool:
    """Require a bounded ID3v2 header and synchsafe tag length."""
    if len(content) < 10 or not content.startswith(b"ID3"):
        return False
    if content[3] not in {2, 3, 4} or content[4] == 0xFF:
        return False
    if any(part & 0x80 for part in content[6:10]):
        return False
    tag_size = 0
    for part in content[6:10]:
        tag_size = (tag_size << 7) | part
    return tag_size <= len(content) - 10


def _looks_like_gif(*, content: bytes) -> bool:
    """Require a logical screen descriptor after the printable GIF marker."""
    return (
        len(content) >= 13
        and content[:6] in {b"GIF87a", b"GIF89a"}
        and int.from_bytes(content[6:8], "little") > 0
        and int.from_bytes(content[8:10], "little") > 0
        and (
            any(byte < 32 or byte > 126 for byte in content[10:13])
            or (
                len(content) >= 15
                and content[13] in {0x21, 0x2C}
                and content.endswith(b"\x3b")
            )
        )
    )


def _looks_like_flac(*, content: bytes) -> bool:
    """Require the mandatory 34-byte STREAMINFO block after the marker."""
    return (
        len(content) >= 42
        and content.startswith(b"fLaC")
        and content[4] & 0x7F == 0
        and int.from_bytes(content[5:8], "big") == 34
    )


def _looks_like_ogg(*, content: bytes) -> bool:
    """Require an Ogg page header and its segment table."""
    return (
        len(content) >= 27
        and content.startswith(b"OggS\x00")
        and len(content) >= 27 + content[26]
    )


def _looks_like_mpeg_frame(*, content: bytes) -> bool:
    """Accept a complete MPEG audio frame with non-reserved header fields."""
    if len(content) < 24 or content[0] != 0xFF or content[1] & 0xE0 != 0xE0:
        return False
    version = (content[1] >> 3) & 0x03
    layer = (content[1] >> 1) & 0x03
    bitrate_index = content[2] >> 4
    sample_index = (content[2] >> 2) & 0x03
    if version == 1 or layer == 0 or bitrate_index in {0, 15} or sample_index == 3:
        return False
    rates = {
        3: (44100, 48000, 32000),
        2: (22050, 24000, 16000),
        0: (11025, 12000, 8000),
    }
    bitrates = {
        (True, 3): (
            0,
            32,
            64,
            96,
            128,
            160,
            192,
            224,
            256,
            288,
            320,
            352,
            384,
            416,
            448,
        ),
        (True, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
        (True, 1): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
        (False, 3): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
        (False, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
        (False, 1): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    }
    bitrate = bitrates[(version == 3, layer)][bitrate_index] * 1000
    rate = rates[version][sample_index]
    padding = (content[2] >> 1) & 1
    if layer == 3:
        frame_size = ((12 * bitrate // rate) + padding) * 4
    else:
        factor = 72 if layer == 1 and version != 3 else 144
        frame_size = factor * bitrate // rate + padding
    return frame_size <= len(content)


def _looks_like_adts(*, content: bytes) -> bool:
    """Require a complete AAC ADTS frame rather than a two-byte sync guess."""
    if len(content) < 7 or content[0] != 0xFF or content[1] & 0xF6 != 0xF0:
        return False
    if (content[2] >> 2) & 0x0F == 0x0F:
        return False
    frame_size = ((content[3] & 0x03) << 11) | (content[4] << 3) | (content[5] >> 5)
    return 7 <= frame_size <= len(content)


def _bmff_mime(*, content: bytes) -> str | None:
    """Classify ISO BMFF by validated major and compatible file-type brands."""
    if len(content) < 16 or content[4:8] != b"ftyp":
        return None
    size = int.from_bytes(content[:4], "big")
    if not 16 <= size <= len(content):
        return None
    brands = {content[8:12]}
    brands.update(content[offset : offset + 4] for offset in range(16, size, 4))
    if brands & {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1"}:
        return "image/heic"
    if brands & {b"avif", b"avis"}:
        return "image/avif"
    if brands & {b"M4A ", b"M4B ", b"M4P "}:
        return "audio/mp4"
    return "video/mp4"


def has_pdf_body(*, content: bytes) -> bool:
    """Find a PDF header in a bounded preamble followed by an object body."""
    header = re.search(rb"%PDF-[12]\.\d", content[:8200])
    return bool(
        header is not None
        and header.start() <= 8192
        and re.search(rb"\d+\s+\d+\s+obj\b", content[header.end() :])
    )


def _office_zip_mime(*, content: bytes) -> str | None:
    """Recognize document package members without decompressing untrusted payloads."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" in names:
                for body, mime in (
                    (
                        "word/document.xml",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                    (
                        "xl/workbook.xml",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                    (
                        "ppt/presentation.xml",
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    ),
                ):
                    if body in names:
                        return mime
            if "mimetype" in names and "META-INF/manifest.xml" in names:
                member = archive.getinfo("mimetype")
                if member.file_size <= 128:
                    mime = archive.read(member).decode("ascii", errors="ignore")
                    if mime in _OFFICE_MIMES:
                        return mime
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        pass
    return None


def _detected_mime(*, content: bytes) -> str:
    """Choose a class from signatures, package structure, or strict UTF-8."""
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    for signature, mime in (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"II*\x00", "image/tiff"),
        (b"MM\x00*", "image/tiff"),
        (b"\x1a\x45\xdf\xa3", "video/webm"),
    ):
        if content.startswith(signature):
            return mime
    if _looks_like_bmp(content=content):
        return "image/bmp"
    if _looks_like_gif(content=content):
        return "image/gif"
    if _looks_like_flac(content=content):
        return "audio/flac"
    if _looks_like_ogg(content=content):
        return "audio/ogg"
    if _looks_like_id3(content=content):
        return "audio/mpeg"
    if content.startswith(b"RIFF") and len(content) >= 12:
        riff_size = int.from_bytes(content[4:8], "little") + 8
        if 12 <= riff_size <= len(content):
            if content[8:12] == b"WAVE":
                return "audio/wav"
            if content[8:12] == b"AVI ":
                return "video/x-msvideo"
            if content[8:12] == b"WEBP":
                return "image/webp"
    bmff = _bmff_mime(content=content)
    if bmff is not None:
        return bmff
    if _looks_like_adts(content=content):
        return "audio/aac"
    if _looks_like_mpeg_frame(content=content):
        return "audio/mpeg"
    if content.startswith(b"PK\x03\x04"):
        office = _office_zip_mime(content=content)
        if office is not None:
            return office
        raise ContentDetectionError(code="unsupported_binary_content")
    if content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/x-ole-office"
    if has_pdf_body(content=content):
        if b"%%EOF" in content:
            return "application/pdf"
        raise ContentDetectionError(code="unsupported_binary_content")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ContentDetectionError(code="unsupported_binary_content") from error
    if any(
        (ord(char) < 32 and char not in "\t\r\n") or ord(char) == 127
        for char in decoded
    ):
        raise ContentDetectionError(code="unsupported_binary_content")
    stripped_text = decoded.lstrip("\ufeff \t\r\n")
    if re.match(r"(?:<!doctype\s+html\b|<html(?:\s|>))", stripped_text, re.IGNORECASE):
        return "text/html"
    return "text/plain"


def detect_content_mime(*, content: bytes, declared_mime: str) -> str:
    """Return byte-authoritative MIME, refusing a contradictory declaration."""
    detected = _detected_mime(content=content)
    declared = declared_mime.partition(";")[0].strip().lower()
    declared = _IMAGE_MIME_ALIASES.get(declared, declared)
    declared = _AUDIO_MIME_ALIASES.get(declared, declared)
    if detected.startswith("text/"):
        if detected == "text/html":
            if declared not in {"", "application/octet-stream", "text/html"}:
                raise ContentDetectionError(code="content_type_mismatch")
            return detected
        if (
            declared not in {"", "application/octet-stream"}
            and declared not in _TEXT_APPLICATION_HINTS
            and not declared.startswith("text/")
        ):
            raise ContentDetectionError(code="content_type_mismatch")
        return "text/markdown" if declared in _TEXT_HINTS else "text/plain"
    detected_class = (
        "office"
        if detected in _OFFICE_MIMES or detected == "application/x-ole-office"
        else detected.split("/", 1)[0]
    )
    declared_class = (
        "office" if declared in _OFFICE_MIMES else declared.split("/", 1)[0]
    )
    if (
        declared not in {"", "application/octet-stream"}
        and declared_class != detected_class
    ):
        raise ContentDetectionError(code="content_type_mismatch")
    if (
        detected.startswith("image/")
        and declared.startswith("image/")
        and declared != detected
    ):
        raise ContentDetectionError(code="content_type_mismatch")
    if detected == "application/x-ole-office":
        if declared in {
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.ms-powerpoint",
        }:
            return declared
        raise ContentDetectionError(code="ambiguous_office_format")
    return detected
