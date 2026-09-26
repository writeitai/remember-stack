"""D134 document filters compiled to SQL predicates, shared by every reader.

``search_documents`` and the ``documents`` filter on claim, chunk and fact
search accept the same :class:`DocumentSearchFilters`. This module is the one
place that turns them into bound predicates over ``document_metadata`` and
``document_people``, so the two can never disagree about what "authors
include Alice" means. It builds SQL text and parameters only; it runs nothing.

Every statement that uses it binds ``:deployment_id``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from remember.models import DocumentSearchFilters
from rememberstack.core.document_metadata import normalize_name


def metadata_predicates(
    *,
    filters: DocumentSearchFilters,
    metadata: str,
    version: str,
    doc: str,
    prefix: str,
) -> tuple[list[str], dict[str, Any]]:
    """Predicates over one ``document_metadata`` row and its version/lineage.

    ``metadata`` is the row's alias, ``version`` and ``doc`` the SQL
    expressions for its version and lineage ids; ``prefix`` namespaces the
    bound parameters so the predicates can sit inside a larger statement.
    """
    predicates: list[str] = []
    parameters: dict[str, Any] = {}
    if filters.family:
        predicates.append(f"{metadata}.family = ANY(CAST(:{prefix}families AS text[]))")
        parameters[f"{prefix}families"] = list(filters.family)
    for column, bound, operator in (
        ("created_at", "created_from", ">="),
        ("created_at", "created_to", "<="),
        ("modified_at", "modified_from", ">="),
        ("modified_at", "modified_to", "<="),
    ):
        value = getattr(filters, bound)
        if value is not None:
            predicates.append(f"{metadata}.{column} {operator} :{prefix}{bound}")
            parameters[f"{prefix}{bound}"] = value
    for column in ("language", "thread_ref"):
        value = getattr(filters, column)
        if value is not None:
            predicates.append(f"{metadata}.{column} = :{prefix}{column}")
            parameters[f"{prefix}{column}"] = value
    if filters.doc_ids:
        predicates.append(f"{doc} = ANY(CAST(:{prefix}doc_ids AS uuid[]))")
        parameters[f"{prefix}doc_ids"] = [str(doc_id) for doc_id in filters.doc_ids]
    for role, terms in (
        ("author", people_terms(filters.authors)),
        ("recipient", people_terms(filters.recipients)),
    ):
        if terms:
            parameter = f"{prefix}{role}_terms"
            predicates.append(
                "EXISTS (SELECT 1 FROM document_people p"
                " WHERE p.deployment_id = :deployment_id"
                f" AND p.version_id = {version}"
                f" AND p.role = '{role}'"
                f" AND {person_matches(parameter=parameter)})"
            )
            parameters[parameter] = list(terms)
    return predicates, parameters


def live_version_matches(
    *, filters: DocumentSearchFilters, version: str, prefix: str
) -> tuple[str, dict[str, Any]]:
    """One predicate: the version ``version`` is live and its metadata matches.

    Live means neither the version nor its lineage is deleted; a forgotten
    lineage has no metadata row, so it never matches either.
    """
    predicates, parameters = metadata_predicates(
        filters=filters,
        metadata="dm",
        version="dm.version_id",
        doc="dm.doc_id",
        prefix=prefix,
    )
    where = "".join(f" AND {predicate}" for predicate in predicates)
    return (
        "EXISTS (SELECT 1 FROM document_metadata dm"
        " JOIN document_versions dv"
        "   ON dv.deployment_id = dm.deployment_id AND dv.version_id = dm.version_id"
        " JOIN documents dd"
        "   ON dd.deployment_id = dv.deployment_id AND dd.doc_id = dv.doc_id"
        " WHERE dm.deployment_id = :deployment_id"
        f"   AND dm.version_id = {version}"
        "   AND dv.deleted_at IS NULL AND dd.deleted_at IS NULL"
        f"{where})"
    ), parameters


def matching_occurrence_exists(
    *, filters: DocumentSearchFilters, claim: str, prefix: str
) -> tuple[str, dict[str, Any]]:
    """One predicate: claim ``claim`` has a live occurrence in a matching version.

    An occurrence is a ``chunk_claims`` row; it is live when its chunk belongs
    to its version's current reading. A claim reused across versions (D56)
    has one occurrence per version, so each version is tested on its own.
    """
    version_sql, parameters = live_version_matches(
        filters=filters, version="occurrence_chunk.version_id", prefix=prefix
    )
    return (
        "EXISTS (SELECT 1 FROM chunk_claims occurrence"
        " JOIN chunks occurrence_chunk"
        "   ON occurrence_chunk.deployment_id = occurrence.deployment_id"
        "  AND occurrence_chunk.chunk_id = occurrence.chunk_id"
        " JOIN document_versions occurrence_version"
        "   ON occurrence_version.deployment_id = occurrence_chunk.deployment_id"
        "  AND occurrence_version.version_id = occurrence_chunk.version_id"
        "  AND occurrence_version.current_representation_id"
        "      = occurrence_chunk.representation_id"
        " WHERE occurrence.deployment_id = :deployment_id"
        f"   AND occurrence.claim_id = {claim}"
        f"   AND {version_sql})"
    ), parameters


def person_matches(*, parameter: str) -> str:
    """A person matches a term by exact address or whole words of the name."""
    return (
        f"EXISTS (SELECT 1 FROM unnest(CAST(:{parameter} AS text[])) AS term(value)"
        " WHERE p.normalized_address = term.value"
        " OR position(' ' || term.value || ' ' IN"
        " ' ' || coalesce(p.normalized_name, '') || ' ') > 0)"
    )


def people_terms(values: Sequence[str]) -> tuple[str, ...]:
    """Normalize people filter terms the way stored names and addresses are."""
    normalized = (normalize_name(value=value) for value in values)
    return tuple(dict.fromkeys(term for term in normalized if term))


def is_empty(filters: DocumentSearchFilters) -> bool:
    """Whether the filter restricts nothing (every field at its default)."""
    return filters == DocumentSearchFilters()
