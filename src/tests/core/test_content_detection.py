"""Byte-authoritative file classes and their contradiction boundary."""

import ast
import io
from pathlib import Path
import zipfile

import pytest

from rememberstack.core.content_detection import detect_content_mime
from rememberstack.core.storage_routing import storage_class_for
from rememberstack.core.storage_routing import storage_class_for_derived
from rememberstack.core.storage_routing import storage_class_for_internal
from rememberstack.core.storage_routing import storage_class_for_snapshot
from rememberstack.model import ContentDetectionError


def _docx_bytes() -> bytes:
    """Build a small package with the structural members of an OOXML document."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("notes/%PDF-1.7", "not a PDF")
    return output.getvalue()


def _bmff_bytes(*, brand: bytes, compatible: bytes | None = None) -> bytes:
    """Build a file-type box with major and optional compatible brands."""
    size = 20 if compatible is not None else 16
    return size.to_bytes(4, "big") + b"ftyp" + brand + b"\x00" * 4 + (compatible or b"")


@pytest.mark.parametrize(
    ("content", "declared", "expected"),
    [
        (b"hello\r\nworld", "text/csv", "text/plain"),
        (b"const answer = 42;\n", "application/javascript", "text/plain"),
        (b'{"answer":42}\n', "application/json", "text/plain"),
        (b"\xef\xbb\xbf# Heading\r\n", "text/markdown", "text/markdown"),
        (b"%PDF-1.7\n" + b"x" * 1_000_000, "application/pdf", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n" + b"bytes", "image/png", "image/png"),
        (b"GIF89a\x01\x00\x01\x00\x80\x00\x00;", "image/gif", "image/gif"),
        (
            b"BM"
            + (26).to_bytes(4, "little")
            + b"\x00" * 4
            + (26).to_bytes(4, "little")
            + (12).to_bytes(4, "little")
            + b"\x00" * 8,
            "image/bmp",
            "image/bmp",
        ),
        (b"ID3\x04\x00\x00\x00\x00\x00\x00", "audio/mpeg", "audio/mpeg"),
        (b"fLaC\x80\x00\x00\x22" + b"\x00" * 34, "audio/flac", "audio/flac"),
        (b"OggS\x00" + b"\x00" * 22, "audio/ogg", "audio/ogg"),
        (_bmff_bytes(brand=b"isom"), "video/mp4", "video/mp4"),
        (_bmff_bytes(brand=b"M4A "), "audio/mp4", "audio/mp4"),
        (_bmff_bytes(brand=b"M4A "), "application/octet-stream", "audio/mp4"),
        (_bmff_bytes(brand=b"isom", compatible=b"M4A "), "audio/mp4", "audio/mp4"),
        (_bmff_bytes(brand=b"mp42", compatible=b"M4A "), "audio/x-m4a", "audio/mp4"),
        (_bmff_bytes(brand=b"heic"), "image/heic", "image/heic"),
        (_bmff_bytes(brand=b"heic"), "image/heif", "image/heic"),
        (_bmff_bytes(brand=b"heic"), "application/octet-stream", "image/heic"),
        (
            _docx_bytes(),
            "application/octet-stream",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ],
)
def test_detected_mime_comes_from_bytes(
    content: bytes, declared: str, expected: str
) -> None:
    """Each supported class and ambiguous text has one authoritative result."""
    assert detect_content_mime(content=content, declared_mime=declared) == expected


@pytest.mark.parametrize(
    ("content", "declared"),
    [
        (b"%PDF-1.7\n", "image/png"),
        (b"\x89PNG\r\n\x1a\nbytes", "application/pdf"),
        (b"ID3\x04\x00\x00\x00\x00\x00\x00", "text/plain"),
        (_bmff_bytes(brand=b"isom"), "text/plain"),
        (_bmff_bytes(brand=b"M4A "), "video/mp4"),
        (_bmff_bytes(brand=b"heic"), "video/mp4"),
        (_docx_bytes(), "text/plain"),
        (b"hello", "video/mp4"),
        (b"\x89PNG\r\n\x1a\nbytes", "image/jpeg"),
    ],
)
def test_contradictory_declarations_are_typed(content: bytes, declared: str) -> None:
    """A cheap or different declared class cannot change routing or billing."""
    with pytest.raises(ContentDetectionError) as raised:
        detect_content_mime(content=content, declared_mime=declared)
    assert raised.value.code == "content_type_mismatch"


def test_unknown_binary_is_not_text() -> None:
    """An unrecognized binary cannot fall through to the text rate class."""
    with pytest.raises(ContentDetectionError) as raised:
        detect_content_mime(content=b"\x00\x01payload", declared_mime="text/plain")
    assert raised.value.code == "unsupported_binary_content"


@pytest.mark.parametrize("declared", ("text/plain", "application/octet-stream"))
@pytest.mark.parametrize("content", (b"BM25 ranking", b"ID3 tags are metadata"))
def test_printable_magic_prefixes_remain_text(content: bytes, declared: str) -> None:
    """A few printable letters do not establish a binary file header."""
    assert detect_content_mime(content=content, declared_mime=declared) == "text/plain"


@pytest.mark.parametrize("content", (b"\xff\xfeA\x00", b"\xff\xfb\x90\x00\x00\x01"))
def test_short_binary_cannot_spoof_mpeg(content: bytes) -> None:
    """A partial frame and UTF-16 BOM cannot enter the audio class."""
    with pytest.raises(ContentDetectionError) as raised:
        detect_content_mime(content=content, declared_mime="application/octet-stream")
    assert raised.value.code == "unsupported_binary_content"


def test_complete_mpeg_frame_is_audio() -> None:
    """A complete MPEG-1 Layer III frame still routes as audio."""
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    assert (
        detect_content_mime(content=frame, declared_mime="audio/mpeg") == "audio/mpeg"
    )


def test_pdf_token_inside_text_or_other_signatures_does_not_override_class() -> None:
    """A mention of a PDF header is not a PDF, even inside another file."""
    note = b"How PDFs start: %PDF-1.7\r\n"
    png = b"\x89PNG\r\n\x1a\n" + b"caption %PDF-1.7"
    assert (
        detect_content_mime(content=note, declared_mime="text/markdown")
        == "text/markdown"
    )
    assert (
        detect_content_mime(content=png, declared_mime="application/octet-stream")
        == "image/png"
    )
    assert detect_content_mime(
        content=_docx_bytes(), declared_mime="application/octet-stream"
    ).endswith("wordprocessingml.document")


def test_ascii_prefixed_pdf_at_header_boundary_remains_pdf() -> None:
    """A PDF with a first-kilobyte preamble and body markers is detected."""
    content = b"x" * 1024 + b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF"
    assert (
        detect_content_mime(content=content, declared_mime="application/octet-stream")
        == "application/pdf"
    )


def test_shifted_pdf_with_long_trailing_padding_stays_pdf() -> None:
    """A shifted PDF cannot fall through to the cheaper text class."""
    content = b"x" * 1025 + b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n" + b" " * 2049
    assert (
        detect_content_mime(content=content, declared_mime="application/octet-stream")
        == "application/pdf"
    )
    with pytest.raises(ContentDetectionError) as raised:
        detect_content_mime(content=content, declared_mime="text/plain")
    assert raised.value.code == "content_type_mismatch"


@pytest.mark.parametrize("declared", ("text/plain", "application/octet-stream"))
@pytest.mark.parametrize("content", (b"GIF89a is a format", b"GIF89a\r\nhello there"))
def test_printable_gif_prefix_remains_text(content: bytes, declared: str) -> None:
    """A GIF prefix alone is not an image header."""
    assert detect_content_mime(content=content, declared_mime=declared) == "text/plain"


@pytest.mark.parametrize("declared", ("image/jpg", "image/pjpeg"))
def test_jpeg_aliases_are_canonicalized(declared: str) -> None:
    """A common image MIME spelling is the same detected JPEG subtype."""
    assert (
        detect_content_mime(content=b"\xff\xd8\xff\xe0image", declared_mime=declared)
        == "image/jpeg"
    )


def test_storage_policy_covers_each_object_kind() -> None:
    """Read-facing objects stay hot; replay-only objects stay cold."""
    assert storage_class_for(mime="video/mp4") == "hot"
    assert storage_class_for(mime="application/pdf") == "cold"
    assert storage_class_for_derived() == "hot"
    assert storage_class_for_snapshot() == "hot"
    assert storage_class_for_internal() == "cold"


def test_every_production_object_write_declares_a_class() -> None:
    """Catch a new unlabelled object write at the syntax boundary."""
    root = Path(__file__).parents[2] / "rememberstack"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not isinstance(
                call.func, ast.Attribute
            ):
                continue
            if call.func.attr != "write_bytes":
                continue
            keywords = {argument.arg for argument in call.keywords}
            if "key" in keywords:
                assert "storage_class" in keywords, f"{path}:{call.lineno}"
    workflow = Path(__file__).parents[3] / ".github/workflows/ci.yml"
    for line_number, line in enumerate(
        workflow.read_text(encoding="utf-8").splitlines(), 1
    ):
        if "store.write_bytes(key=" in line:
            assert "storage_class=" in line, f"{workflow}:{line_number}"
