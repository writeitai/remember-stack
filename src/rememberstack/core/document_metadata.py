"""D134 pure rules: format family from MIME, people normalization, name text.

``family_for_mime`` is the one Python mapping from a stored MIME type to the
D138 format family recorded on ``document_metadata.family``; it reads the
format registry. The p9_37 migration re-derives existing versions' families
with this same function; a database test pins the two together.
"""

from collections.abc import Iterable
from typing import Final
import unicodedata

from rememberstack.core.format_registry import family_for_mime as format_family_for_mime

INGEST_METADATA_MAPPING_VERSION: Final = "e0-ingest-metadata-2026.09b:d138-families"
"""The mapping that writes a version's metadata row at ingest (D134)."""


def family_for_mime(*, mime: str) -> str:
    """Map a stored MIME type to its D138 format family name.

    The families are the D138 §4 table (``markdown``, ``text``, ``code``,
    ``word``, ``image``, ``binary``, …); unknown types fall back by prefix
    (``image/*`` → ``image``, ``audio/*`` and ``video/*`` → ``media``, other
    ``text/*`` → ``other_text``, anything else → ``binary``). Parameters
    (``; charset=…``) and case are ignored.
    """
    return format_family_for_mime(mime=mime).name


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
