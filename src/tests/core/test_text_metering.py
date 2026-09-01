"""Adversarial vectors for the bounded managed doc-text classifier."""

import pytest

from rememberstack.core.text_metering import classify_doc_text
from rememberstack.core.text_metering import DOC_TEXT_MAX_SOURCE_BYTES
from rememberstack.core.text_metering import normalized_character_count
from rememberstack.model import ManagedTextClassificationError


@pytest.mark.parametrize(
    ("value", "expected"),
    [("hello", 5), ("a  \n\t b", 3), ("  alpha\u2003beta  ", 12), ("A😀B", 3)],
)
def test_normalized_character_count_collapses_unicode_whitespace(
    value: str, expected: int
) -> None:
    """Every maximal Unicode whitespace run counts as one scalar."""
    assert normalized_character_count(text=value) == expected


@pytest.mark.parametrize(
    "payload",
    [
        b"%PDF-1.7\n1 0 obj\n(scanned-looking text)",
        b"\x89PNG\r\n\x1a\n" + b"text-looking-tail",
        b"\xff\xd8\xff\xe0image-only",
        b"PK\x03\x04archive-with-text.txt",
    ],
)
def test_binary_and_scanned_sources_never_misclassify_as_doc_text(
    payload: bytes,
) -> None:
    """Magic wins over a hostile caller claiming text/plain."""
    with pytest.raises(ManagedTextClassificationError) as raised:
        classify_doc_text(content=payload, declared_mime="text/plain")
    assert raised.value.code == "rate_class_unavailable"


@pytest.mark.parametrize("payload", [b"hello\x00world", b"\xff\xfeA\x00"])
def test_ambiguous_binary_text_fails_before_measurement(payload: bytes) -> None:
    """NUL/control or invalid UTF-8 cannot reach the cheap text route."""
    with pytest.raises(ManagedTextClassificationError) as raised:
        classify_doc_text(content=payload, declared_mime="text/markdown")
    assert raised.value.code == "rate_class_ambiguous"


def test_mime_does_not_hide_native_text_or_change_quantity() -> None:
    """Verified native text is measured from bytes, not the caller MIME label."""
    result = classify_doc_text(
        content="A\u2009 B".encode(), declared_mime="application/octet-stream"
    )
    assert result.canonical_mime == "text/plain"
    assert result.normalized_character_count == 3
    assert result.canonical_source_bytes == 6


def test_bound_exceed_is_typed_before_any_source_version() -> None:
    """The published v1 profile has a hard, byte-counted source ceiling."""
    with pytest.raises(ManagedTextClassificationError) as raised:
        classify_doc_text(
            content=b"a" * (DOC_TEXT_MAX_SOURCE_BYTES + 1), declared_mime="text/plain"
        )
    assert raised.value.code == "source_bytes_limit_exceeded"
