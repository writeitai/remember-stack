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


def _odt_bytes() -> bytes:
    """An OpenDocument package: its MIME is read from the ``mimetype`` member."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr("META-INF/manifest.xml", "<manifest/>")
    return output.getvalue()


def _bmff_bytes(*, brand: bytes, compatible: bytes | None = None) -> bytes:
    """Build a file-type box with major and optional compatible brands."""
    size = 20 if compatible is not None else 16
    return size.to_bytes(4, "big") + b"ftyp" + brand + b"\x00" * 4 + (compatible or b"")


@pytest.mark.parametrize(
    ("content", "declared", "expected"),
    [
        (b"hello\r\nworld", "text/csv", "text/plain"),
        (b"", "text/markdown", "text/markdown"),
        (b"<html><p>Hello</p></html>", "text/html", "text/html"),
        (b"<html><p>Hello</p></html>", "text/plain", "text/html"),
        (b"const answer = 42;\n", "application/javascript", "text/plain"),
        (b"# Notes\n", "application/x-unknown", "text/plain"),
        (b'{"answer":42}\n', "application/json", "text/plain"),
        (b"\xef\xbb\xbf# Heading\r\n", "text/markdown", "text/markdown"),
        (
            b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF",
            "application/pdf",
            "application/pdf",
        ),
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
        (
            _docx_bytes(),
            "application/zip",
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
    """An unrecognized binary cannot be declared as text."""
    with pytest.raises(ContentDetectionError) as raised:
        detect_content_mime(content=b"\x00\x01payload", declared_mime="text/plain")
    assert raised.value.code == "content_type_mismatch"


def test_pdf_example_inside_prose_does_not_override_text() -> None:
    """A quoted complete PDF example does not turn Markdown into a PDF."""
    note = b"A PDF example follows:\n%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF"
    assert (
        detect_content_mime(content=note, declared_mime="text/markdown")
        == "text/markdown"
    )


@pytest.mark.parametrize(
    "content", (b"PAR1\nmagic notes\nPAR1", b"8BPS is the Photoshop marker\n")
)
def test_printable_format_markers_are_text(content: bytes) -> None:
    """A printable marker alone never establishes a binary format."""
    assert (
        detect_content_mime(content=content, declared_mime="text/plain") == "text/plain"
    )


def test_cr_delimited_pdf_is_detected() -> None:
    """PDF syntax also permits carriage returns between object tokens."""
    content = b"%PDF-1.7\r%\xff\r1 0 obj\r<<>>\rendobj\r%%EOF"
    assert (
        detect_content_mime(content=content, declared_mime="application/pdf")
        == "application/pdf"
    )


@pytest.mark.parametrize("declared", ("text/plain", "application/octet-stream"))
@pytest.mark.parametrize("content", (b"BM25 ranking", b"ID3 tags are metadata"))
def test_printable_magic_prefixes_remain_text(content: bytes, declared: str) -> None:
    """A few printable letters do not establish a binary file header."""
    assert detect_content_mime(content=content, declared_mime=declared) == "text/plain"


@pytest.mark.parametrize("content", (b"\xff\xfeA\x00", b"\xff\xfb\x90\x00\x00\x01"))
def test_short_binary_cannot_spoof_mpeg(content: bytes) -> None:
    """A partial frame and UTF-16 BOM get a binary card, never audio."""
    assert (
        detect_content_mime(content=content, declared_mime="application/octet-stream")
        == "application/octet-stream"
    )


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


def _verdict(content: object, declared: str) -> str:
    try:
        return detect_content_mime(content=content, declared_mime=declared)  # type: ignore[arg-type]
    except ContentDetectionError as error:
        return f"refused:{error.code}"


_MAPPED_SAMPLES: tuple[tuple[bytes, str], ...] = (
    (b"", "text/markdown"),
    (b"hello\r\nworld", "text/csv"),
    (b"  \n\t <!DOCTYPE html><p>x</p>", "text/plain"),
    (b"\xef\xbb\xbf<html>\n", "text/html"),
    (b"bad \xff utf8", "text/plain"),
    (b"bell \x07 char", "text/plain"),
    (b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF", "application/pdf"),
    (b"%PDF-1.4 1 0 obj\n<<>>\nendobj\n%%EOF\n", "application/pdf"),
    (b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n", "application/pdf"),
    (b"Quoting %PDF-1.7 in prose\n1 0 obj\nendobj\n%%EOF", "text/plain"),
    (_docx_bytes(), "application/zip"),
    (b"PK\x03\x04not really a zip", "application/zip"),
    (b"\x89PNG\r\n\x1a\n" + b"bytes", "image/png"),
    (b"GIF89a\x01\x00\x01\x00\x80\x00\x00;", "image/gif"),
    (
        b"PAR1" + b"\x00" * 8 + (4).to_bytes(4, "little") + b"PAR1",
        "application/x-unknown",
    ),
    # a multibyte character split across the 1 MiB decoding windows
    (b"a" * ((1 << 20) - 1) + "é".encode() + b"tail", "text/plain"),
    (b"a" * ((1 << 20) - 1) + b"\xc3", "text/plain"),
    (b" " * (1 << 20) + b"<html>", "text/plain"),
    (b"<!doctype" + b" " * (1 << 20) + b"html>", "text/plain"),
    (b"\xc2\xa0<html>", "text/plain"),
    (_odt_bytes(), "application/vnd.oasis.opendocument.text"),
    (_bmff_bytes(brand=b"isom", compatible=b"M4A "), "audio/mp4"),
    (_bmff_bytes(brand=b"avif", compatible=b"heic"), "image/heic"),
)


@pytest.mark.parametrize(("content", "declared"), _MAPPED_SAMPLES)
def test_a_memory_mapped_file_is_classified_like_its_bytes(
    content: bytes, declared: str, tmp_path: Path
) -> None:
    """D147: registering a stored object maps it; the verdict must not change."""
    import mmap

    path = tmp_path / "object"
    path.write_bytes(content)
    if not content:
        # mmap cannot map an empty file; registration passes b"" instead
        return
    with (
        path.open("rb") as handle,
        mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped,
    ):
        assert _verdict(mapped, declared) == _verdict(content, declared)


def test_a_mapped_file_is_classified_in_bounded_memory(tmp_path: Path) -> None:
    """D147: a large stored object is classified without copying it whole."""
    import mmap
    import tracemalloc

    size = 32 << 20
    samples = {
        "whitespace": b" " * size + b"<html>",
        "bmff": (size).to_bytes(4, "big") + b"ftypisom" + b"\x00" * (size - 12),
    }
    for name, content in samples.items():
        path = tmp_path / name
        path.write_bytes(content)
        with (
            path.open("rb") as handle,
            mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped,
        ):
            tracemalloc.start()
            try:
                detect_content_mime(content=mapped, declared_mime="")  # type: ignore[arg-type]
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        assert peak < 8 << 20, (name, peak)


def test_the_zip_view_seeks_like_bytesio() -> None:
    """Malformed packages must fail the same way through the bounded view."""
    from rememberstack.core.content_detection import _BoundedReader

    reference, view = io.BytesIO(b"abcdef"), _BoundedReader(b"abcdef")
    for offset, whence in ((2, 0), (-5, 1), (-2, 2), (10, 0), (-10, 2)):
        assert view.seek(offset, whence) == reference.seek(offset, whence)
        assert view.read(3) == reference.read(3)
    with pytest.raises(ValueError, match="negative seek value"):
        view.seek(-1, 0)


def test_an_inflating_mimetype_member_is_read_in_bounded_memory() -> None:
    """A declared-small ``mimetype`` that inflates hugely is never inflated whole."""
    import tracemalloc
    import zlib

    mime = b"application/vnd.oasis.opendocument.text"
    bomb = mime + b"\x00" * (64 << 20)
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/manifest.xml", "<manifest/>")
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, bomb)
    content = output.getvalue()
    # Declare the member as the 39-byte MIME with the CRC of those bytes, as a
    # crafted package would; both headers carry the size and the CRC.
    crc = zlib.crc32(mime).to_bytes(4, "little")
    true_size = len(bomb).to_bytes(4, "little")
    small = len(mime).to_bytes(4, "little")
    content = content.replace(zlib.crc32(bomb).to_bytes(4, "little"), crc).replace(
        true_size, small
    )
    tracemalloc.start()
    try:
        verdict = _verdict(content, "application/vnd.oasis.opendocument.text")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < len(content) + (8 << 20)
    assert verdict == "application/vnd.oasis.opendocument.text"


@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_a_mapped_package_never_inflates_an_unboundable_mimetype(
    method: int, tmp_path: Path
) -> None:
    """BZIP2 and LZMA output cannot be bounded; a mapped file does not read them."""
    import mmap
    import tracemalloc
    import zlib

    mime = b"application/vnd.oasis.opendocument.text"
    bomb = mime + b"\x00" * (32 << 20)
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w") as archive:
        archive.writestr("META-INF/manifest.xml", "<manifest/>")
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = method
        archive.writestr(info, bomb)
    # Declare the member as the small MIME with its CRC, as a crafted file would.
    content = (
        output.getvalue()
        .replace(
            zlib.crc32(bomb).to_bytes(4, "little"),
            zlib.crc32(mime).to_bytes(4, "little"),
        )
        .replace(len(bomb).to_bytes(4, "little"), len(mime).to_bytes(4, "little"))
    )
    path = tmp_path / "package"
    path.write_bytes(content)
    with (
        path.open("rb") as handle,
        mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped,
    ):
        tracemalloc.start()
        try:
            verdict = _verdict(mapped, "application/zip")
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert verdict == "application/octet-stream"
    assert peak < 8 << 20
