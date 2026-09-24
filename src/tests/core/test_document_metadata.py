"""D134 pure rules: family mapping, normalization, name text, and the model."""

from datetime import datetime
from datetime import timezone

from pydantic import ValidationError
import pytest

from rememberstack.adapters import MarkitdownConverter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.core.document_metadata import family_for_mime
from rememberstack.core.document_metadata import name_text
from rememberstack.core.document_metadata import normalize_address
from rememberstack.core.document_metadata import normalize_name
from rememberstack.model.document_metadata import DocumentMetadata
from rememberstack.model.document_metadata import DocumentPerson

FAMILY_CASES: tuple[tuple[str, str], ...] = (
    ("text/markdown", "markdown"),
    ("text/x-markdown", "markdown"),
    ("text/html", "html"),
    ("application/xhtml+xml", "html"),
    ("application/pdf", "pdf"),
    ("image/png", "image"),
    ("audio/mpeg", "audio"),
    ("video/mp4", "video"),
    (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "office",
    ),
    ("application/vnd.oasis.opendocument.text", "office"),
    ("application/msword", "office"),
    ("application/rtf", "office"),
    ("application/vnd.ms-excel", "office"),
    ("application/vnd.ms-powerpoint", "office"),
    ("text/plain", "text"),
    ("text/csv", "text"),
    ("application/json", "other"),
    ("application/octet-stream", "other"),
    ("message/rfc822", "other"),
    ("application/vnd.ms-outlook", "other"),
    ("Text/HTML; charset=utf-8", "html"),
)


@pytest.mark.parametrize(("mime", "family"), FAMILY_CASES)
def test_family_for_mime(mime: str, family: str) -> None:
    """Each MIME maps to one coarse family; parameters and case are ignored."""
    assert family_for_mime(mime=mime) == family


def test_normalize_name_lowercases_unaccents_and_collapses_whitespace() -> None:
    assert normalize_name(value="  Jiří   NOVÁK\t") == "jiri novak"
    assert normalize_name(value="   ") is None
    assert normalize_name(value=None) is None


def test_normalize_address_matches_name_normalization() -> None:
    """D134: addresses normalize like names (lower, unaccented, collapsed)."""
    assert normalize_address(value=" Alice@ACME.com ") == "alice@acme.com"
    assert normalize_address(value="Jiří.Novák@Example.CZ") == "jiri.novak@example.cz"
    assert normalize_address(value="@Team  Lead") == "@team lead"
    assert normalize_address(value="Zoë@x.io") == normalize_name(value="Zoë@x.io")
    assert normalize_address(value="") is None
    assert normalize_address(value=None) is None


def test_name_text_space_joins_non_empty_parts() -> None:
    assert (
        name_text(parts=("Q3_sales.xlsx", " Q3 sales ", "finance/2025"))
        == "Q3_sales.xlsx Q3 sales finance/2025"
    )
    assert name_text(parts=("report.md", None, "  ")) == "report.md"
    assert name_text(parts=(None, None, None)) == ""


def test_document_person_needs_a_name_or_an_address() -> None:
    assert DocumentPerson(address="@alice").name is None
    with pytest.raises(ValidationError):
        DocumentPerson()
    with pytest.raises(ValidationError):
        DocumentPerson(name="  ", address="")


def test_document_metadata_requires_utc_dates_and_forbids_unknown_fields() -> None:
    sent = datetime(2025, 3, 1, 9, 30, tzinfo=timezone.utc)
    metadata = DocumentMetadata(
        title="Audit",
        authors=(DocumentPerson(name="Alice Novak", address="alice@acme.com"),),
        created_at=sent,
        extra={"reply_to": "bob@acme.com"},
    )
    assert metadata.recipients == ()
    assert metadata.created_at == sent
    with pytest.raises(ValidationError):
        DocumentMetadata(created_at=datetime(2025, 3, 1, 9, 30))
    with pytest.raises(ValidationError):
        DocumentMetadata.model_validate({"subject": "Audit"})


def test_markitdown_returns_the_declared_html_title() -> None:
    result = MarkitdownConverter().convert(
        content=b"<html><head><title> Q3 Sales </title></head><body>hi</body></html>",
        mime="text/html",
    )
    assert result.metadata == DocumentMetadata(title="Q3 Sales")


def test_markitdown_without_a_title_returns_no_metadata() -> None:
    result = MarkitdownConverter().convert(
        content=b"<html><body><p>no title here</p></body></html>", mime="text/html"
    )
    assert result.metadata is None


def test_passthrough_returns_no_metadata() -> None:
    result = MarkdownPassthroughConverter().convert(
        content=b"# Heading\n", mime="text/markdown"
    )
    assert result.metadata is None
