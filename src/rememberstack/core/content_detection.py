"""Conservative byte classification before E0 routing and storage."""

from __future__ import annotations

import io
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
    if content.startswith(b"%PDF-") or b"%PDF-" in content[:1024]:
        return "application/pdf"
    for signature, mime in (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"II*\x00", "image/tiff"),
        (b"MM\x00*", "image/tiff"),
        (b"BM", "image/bmp"),
        (b"fLaC", "audio/flac"),
        (b"ID3", "audio/mpeg"),
        (b"OggS", "audio/ogg"),
        (b"\x1a\x45\xdf\xa3", "video/webm"),
    ):
        if content.startswith(signature):
            return mime
    if content.startswith(b"RIFF") and len(content) >= 12:
        if content[8:12] == b"WAVE":
            return "audio/wav"
        if content[8:12] == b"AVI ":
            return "video/x-msvideo"
        if content[8:12] == b"WEBP":
            return "image/webp"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        return "video/mp4"
    if len(content) >= 4 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0:
        if content[1] & 0x06 == 0 and content[2] & 0xF0 == 0xF0:
            return "audio/aac"
        if content[1] & 0x06 != 0 and content[2] & 0xF0 != 0xF0:
            return "audio/mpeg"
    if content.startswith(b"PK\x03\x04"):
        office = _office_zip_mime(content=content)
        if office is not None:
            return office
        raise ContentDetectionError(code="unsupported_binary_content")
    if content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/x-ole-office"
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ContentDetectionError(code="unsupported_binary_content") from error
    if not decoded or any(
        (ord(char) < 32 and char not in "\t\r\n") or ord(char) == 127
        for char in decoded
    ):
        raise ContentDetectionError(code="unsupported_binary_content")
    return "text/plain"


def detect_content_mime(*, content: bytes, declared_mime: str) -> str:
    """Return byte-authoritative MIME, refusing a contradictory declaration."""
    detected = _detected_mime(content=content)
    declared = declared_mime.partition(";")[0].strip().lower()
    if detected.startswith("text/"):
        if declared not in {"", "application/octet-stream"} and not declared.startswith(
            "text/"
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
