"""D138 §3/§4: family detection order, stored MIMEs, stock routes and limits."""

import pytest

from rememberstack.core import STOCK_CONVERSION_ROUTE_NAMES
from rememberstack.core.format_registry import detect_mime
from rememberstack.core.format_registry import exceeds_reading_limit
from rememberstack.core.format_registry import FAMILIES
from rememberstack.core.format_registry import family_for_mime
from rememberstack.core.format_registry import SNIFF_BYTES

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _detect(
    file_name: str,
    *,
    declared: str = "application/octet-stream",
    content: bytes = b"\x00binary",
    routed: frozenset[str] = frozenset(),
) -> str:
    """Detect with a binary body unless a case needs text."""
    return detect_mime(
        file_name=file_name,
        declared_mime=declared,
        content=content,
        routed_mimes=routed,
    )


@pytest.mark.parametrize(
    ("file_name", "family"),
    (
        ("notes.md", "markdown"),
        ("GUIDE.RST", "markdown"),
        ("notes.txt", "text"),
        ("talk.vtt", "text"),
        ("server.log", "log"),
        ("main.py", "code"),
        ("app.tsx", "code"),
        ("query.sql", "code"),
        ("settings.json", "config"),
        ("compose.yml", "config"),
        ("logo.svg", "config"),
        ("page.html", "html"),
        ("book.epub", "ebook"),
        ("analysis.ipynb", "notebook"),
        ("mail.eml", "email"),
        ("plan.docx", "word"),
        ("plan.doc", "word"),
        ("deck.pptx", "presentation"),
        ("paper.pdf", "pdf"),
        ("sheet.xlsx", "spreadsheet"),
        ("data.csv", "delimited"),
        ("data.parquet", "dataset"),
        ("photo.JPG", "image"),
        ("clip.mov", "media"),
        ("bundle.zip", "archive"),
        ("backup.tar.gz", "archive"),
        ("backup.tar.zst", "archive"),
        ("notes.pages", "binary"),
    ),
)
def test_extension_decides_first(file_name: str, family: str) -> None:
    """The lower-cased extension wins over the declaration and the bytes."""
    detected = _detect(file_name, declared="text/markdown", content=b"# text\n")
    assert family_for_mime(mime=detected).name == family


def test_extension_stores_format_or_canonical_mime() -> None:
    """Distinct formats keep their own MIME; others store the family's."""
    assert _detect("plan.doc") == "application/msword"
    assert _detect("plan.docm") == _DOCX
    assert _detect("photo.jpeg") == "image/jpeg"
    assert _detect("photo.png") == "image/png"
    assert _detect("backup.tar.gz") == "application/x-tar"
    assert _detect("single.gz") == "application/gzip"
    assert _detect("main.go") == "text/x-code"
    assert _detect("app.yaml") == "text/x-config"


@pytest.mark.parametrize(
    ("file_name", "family"),
    (
        ("README", "text"),
        ("docs/LICENSE", "text"),
        ("Dockerfile", "code"),
        ("Makefile", "code"),
        ("CODEOWNERS", "code"),
        ("WORKSPACE", "code"),
        (".gitignore", "config"),
        (".editorconfig", "config"),
        (".npmrc", "config"),
        (".env", "config"),
        (".env.local", "config"),
    ),
)
def test_named_files_and_dotfiles_are_text_families(
    file_name: str, family: str
) -> None:
    """Extensionless named files and dotfiles never fall through to the sniff."""
    assert family_for_mime(mime=_detect(file_name)).name == family


def test_a_specific_declared_mime_decides_without_an_extension() -> None:
    """Step 2: a declaration the registry knows decides; aliases store canonically."""
    assert _detect("notes", declared="text/markdown") == "text/markdown"
    assert _detect("upload", declared="application/pdf") == "application/pdf"
    assert _detect("upload", declared="Image/JPEG; q=1") == "image/jpeg"
    # a registry alias stores its family's canonical MIME
    assert _detect("payload", declared="application/json") == "text/x-config"
    assert _detect("notes", declared="text/x-markdown") == "text/markdown"


def test_a_routed_declared_mime_decides_as_the_overlay_adds_it() -> None:
    """A MIME the deployment's route table names is known, and kept as declared."""
    routed = frozenset({"application/x-custom"})
    assert (
        _detect("scan.custom", declared="application/x-custom", routed=routed)
        == "application/x-custom"
    )
    # unknown and unrouted: the declaration is not taken, the bytes decide
    assert _detect("scan.custom", declared="application/x-custom") == (
        "application/octet-stream"
    )


@pytest.mark.parametrize("declared", ("application/octet-stream", "text/plain", ""))
def test_unspecific_declarations_fall_through_to_the_content(declared: str) -> None:
    """Step 3: text/plain is a guess, so unknown text is other_text, not prose."""
    assert _detect("blob", declared=declared, content=b"plain words\n") == (
        "text/x-other-text"
    )
    assert _detect("blob", declared=declared, content=b"a\x00b") == (
        "application/octet-stream"
    )


def test_content_sniff_reads_only_the_first_64_kib() -> None:
    """NUL or invalid UTF-8 inside 64 KiB is binary; beyond it is not looked at."""
    late_nul = b"a" * SNIFF_BYTES + b"\x00"
    assert _detect("blob", content=late_nul) == "text/x-other-text"
    assert _detect("blob", content=b"\xff\xfe text") == "application/octet-stream"
    # a multi-byte character cut by the window is still text
    straddling = b"a" + "é".encode() * SNIFF_BYTES
    assert _detect("blob", content=straddling) == "text/x-other-text"
    assert _detect("blob", content=b"") == "text/x-other-text"


def test_every_extension_belongs_to_exactly_one_family() -> None:
    """The table is the whole contract: no extension is claimed twice."""
    seen: dict[str, str] = {}
    for family in FAMILIES:
        for extension in family.extensions:
            assert extension not in seen, (extension, seen.get(extension))
            seen[extension] = family.name
    assert {family.name for family in FAMILIES} >= {
        "markdown",
        "text",
        "other_text",
        "log",
        "code",
        "config",
        "binary",
    }


def test_stock_routes_cover_every_family_with_a_converter() -> None:
    """Every stored MIME of a family whose converter ships is routed to it."""
    for family in FAMILIES:
        if family.converter is None:
            continue
        assert STOCK_CONVERSION_ROUTE_NAMES[family.mime] == family.converter
    for file_name in ("photo.heic", "clip.mkv", "backup.tar.xz", "old.7z"):
        assert STOCK_CONVERSION_ROUTE_NAMES[_detect(file_name)] == "card"
    # families whose converter is not built yet park, and so do the formats
    # LibreOffice converts first; the Office Open XML documents keep their
    # markitdown route
    assert "application/msword" not in STOCK_CONVERSION_ROUTE_NAMES
    assert _detect("sheet.ods") not in STOCK_CONVERSION_ROUTE_NAMES
    assert STOCK_CONVERSION_ROUTE_NAMES["text/csv"] == "table"
    assert STOCK_CONVERSION_ROUTE_NAMES[_DOCX] == "markitdown"


def test_reading_limits_apply_to_office_pdf_and_spreadsheets_only() -> None:
    """D138 §3 starting values: 100 MB office/PDF, 200 MB spreadsheets."""
    assert not exceeds_reading_limit(mime="application/pdf", byte_size=100_000_000)
    assert exceeds_reading_limit(mime="application/pdf", byte_size=100_000_001)
    assert exceeds_reading_limit(mime=_DOCX, byte_size=100_000_001)
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert not exceeds_reading_limit(mime=xlsx, byte_size=150_000_000)
    assert exceeds_reading_limit(mime=xlsx, byte_size=200_000_001)
    assert not exceeds_reading_limit(mime="text/plain", byte_size=10**12)
