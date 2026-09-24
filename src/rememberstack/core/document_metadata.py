"""D134 pure rules: format family from MIME, people normalization, name text.

``family_for_mime`` is the one Python mapping from a stored MIME type to the
coarse format family recorded on ``document_metadata.family``. The p9_34
migration backfills existing versions with the identical mapping in SQL; a
database test pins the two together.
"""

from collections.abc import Iterable
from typing import Final
import unicodedata

INGEST_METADATA_MAPPING_VERSION: Final = "e0-ingest-metadata-2026.09"
"""The mapping that writes a version's metadata row at ingest (D134)."""

_MARKDOWN: Final = frozenset({"text/markdown", "text/x-markdown"})
_HTML: Final = frozenset({"text/html", "application/xhtml+xml"})
_OFFICE_EXACT: Final = frozenset({"application/msword", "application/rtf"})
_OFFICE_PREFIXES: Final = (
    "application/vnd.openxmlformats-officedocument.",
    "application/vnd.oasis.opendocument.",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
)


def family_for_mime(*, mime: str) -> str:
    """Map a MIME type to its format family.

    Families: ``markdown``, ``html``, ``pdf``, ``image``, ``audio``,
    ``video``, ``office``, ``text`` (any other ``text/*``) and ``other``.
    Parameters (``; charset=…``) and case are ignored.
    """
    canonical = mime.split(";", 1)[0].strip().lower()
    if canonical in _MARKDOWN:
        return "markdown"
    if canonical in _HTML:
        return "html"
    if canonical == "application/pdf":
        return "pdf"
    for prefix, family in (
        ("image/", "image"),
        ("audio/", "audio"),
        ("video/", "video"),
    ):
        if canonical.startswith(prefix):
            return family
    if canonical in _OFFICE_EXACT or canonical.startswith(_OFFICE_PREFIXES):
        return "office"
    if canonical.startswith("text/"):
        return "text"
    return "other"


def normalize_name(*, value: str | None) -> str | None:
    """Lower case, accents removed, whitespace collapsed; None when empty."""
    if value is None:
        return None
    decomposed = unicodedata.normalize("NFKD", value)
    unaccented = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    collapsed = " ".join(unaccented.lower().split())
    return collapsed or None


def normalize_address(*, value: str | None) -> str | None:
    """Normalize an email address or handle exactly like a name (D134).

    Lower case, accents removed, whitespace collapsed; None when empty.
    """
    return normalize_name(value=value)


def name_text(*, parts: Iterable[str | None]) -> str:
    """Space-join the non-empty name parts (file name, title, source path)."""
    return " ".join(part.strip() for part in parts if part is not None and part.strip())
