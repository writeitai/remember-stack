"""Bounded, content-local classification for the managed doc-text profile."""

from __future__ import annotations

from dataclasses import dataclass
import re

from rememberstack.model.metering import ManagedTextClassificationError

DOC_TEXT_CLASSIFIER_VERSION = "doc-text-classifier-v1"
DOC_TEXT_MEASUREMENT_ALGORITHM_VERSION = "unicode-whitespace-scalars-v1"
DOC_TEXT_PROCESSING_PROFILE_ID = "doc-text-standard-v1"
DOC_TEXT_MAX_SOURCE_BYTES = 10_000_000

_BINARY_MAGICS: tuple[bytes, ...] = (
    b"%PDF-",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"PK\x03\x04",
    b"\x1f\x8b",
    b"RIFF",
    b"OggS",
    b"ID3",
    b"\x00\x00\x00\x18ftyp",
    b"\x00\x00\x00\x20ftyp",
)
_DOC_TEXT_MIMES = frozenset(
    {"", "application/octet-stream", "text/markdown", "text/plain", "text/x-markdown"}
)
_STRUCTURED_TEXT_PREFIX = re.compile(
    rb"(?:\{\\rtf|<!doctype\s+html|<html(?:\s|>)|<\?xml(?:\s|>)|<svg(?:\s|>)|<[/!?]?[a-z][^>]{0,128}>)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ClassifiedText:
    """The only native operands allowed out of the bounded classifier."""

    normalized_character_count: int
    canonical_source_bytes: int
    canonical_mime: str


def normalized_character_count(*, text: str) -> int:
    """Count Unicode scalars after collapsing each maximal whitespace run."""
    count = 0
    in_whitespace = False
    for scalar in text:
        whitespace = scalar.isspace()
        if whitespace:
            if not in_whitespace:
                count += 1
        else:
            count += 1
        in_whitespace = whitespace
    return count


def classify_doc_text(*, content: bytes, declared_mime: str) -> ClassifiedText:
    """Inspect bounded bytes and accept only unambiguous native Unicode text.

    Caller MIME and filename never turn binary material into text.  Once bytes
    are proven textual, MIME only preserves the markdown rendering hint; every
    other declaration is normalized to plain text for the stock local route.
    """
    if len(content) > DOC_TEXT_MAX_SOURCE_BYTES:
        raise ManagedTextClassificationError(code="source_bytes_limit_exceeded")
    if not content:
        raise ManagedTextClassificationError(code="empty_text")
    if any(content.startswith(magic) for magic in _BINARY_MAGICS):
        raise ManagedTextClassificationError(code="rate_class_unavailable")
    mime = declared_mime.partition(";")[0].strip().lower()
    if mime not in _DOC_TEXT_MIMES:
        raise ManagedTextClassificationError(code="rate_class_ambiguous")
    stripped = content.lstrip()
    if _STRUCTURED_TEXT_PREFIX.match(stripped) or stripped.startswith(b"JVBERi0"):
        raise ManagedTextClassificationError(code="rate_class_ambiguous")
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ManagedTextClassificationError(code="rate_class_ambiguous") from error
    if any(
        (ord(scalar) < 32 and scalar not in {"\t", "\n", "\r"}) or ord(scalar) == 127
        for scalar in decoded
    ):
        raise ManagedTextClassificationError(code="rate_class_ambiguous")
    quantity = normalized_character_count(text=decoded)
    if quantity == 0 or decoded.isspace():
        raise ManagedTextClassificationError(code="empty_text")
    canonical_mime = (
        "text/markdown"
        if mime in {"text/markdown", "text/x-markdown"}
        else "text/plain"
    )
    return ClassifiedText(
        normalized_character_count=quantity,
        canonical_source_bytes=len(content),
        canonical_mime=canonical_mime,
    )
