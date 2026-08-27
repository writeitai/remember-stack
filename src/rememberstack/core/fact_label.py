"""One deterministic prose contract for relation facts and entity profiles."""

from typing import Final

_PREDICATE_SURFACE: Final[dict[str, str]] = {
    "works_for": "works for",
    "works_at": "works at",
    "part_of": "is part of",
    "member_of": "is a member of",
    "located_in": "is located in",
    "based_in": "is based in",
    "created": "created",
    "owns": "owns",
    "uses": "uses",
    "about": "is about",
    "related_to": "is related to",
    "reports_to": "reports to",
    "employed_by": "is employed by",
    "subsidiary_of": "is a subsidiary of",
    "founded": "founded",
    "authored": "authored",
    "mentions": "mentions",
}


def deterministic_fact_label(*, subject: str, predicate: str, object_name: str) -> str:
    """Build canonical relation prose without an LLM or label-cache dependency."""
    fallback = predicate.replace("_", " ")
    surface = _PREDICATE_SURFACE.get(predicate, fallback)
    return f"{subject} {surface} {object_name}"
