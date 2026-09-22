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
    return output.getvalue()


@pytest.mark.parametrize(
    ("content", "declared", "expected"),
    [
        (b"hello\r\nworld", "text/csv", "text/plain"),
        (b"\xef\xbb\xbf# Heading\r\n", "text/markdown", "text/markdown"),
        (b"%PDF-1.7\n" + b"x" * 1_000_000, "application/pdf", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n" + b"bytes", "image/png", "image/png"),
        (b"ID3\x04\x00\x00\x00\x00\x00\x00", "audio/mpeg", "audio/mpeg"),
        (b"\x00\x00\x00\x18ftypisom", "video/mp4", "video/mp4"),
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
        (b"\x00\x00\x00\x18ftypisom", "text/plain"),
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
