"""D134 document filters compiled to SQL predicates, shared by every reader.

``search_documents`` and the ``documents`` filter on claim, chunk and fact
search accept the same :class:`DocumentSearchFilters`. This module is the one
place that turns them into bound predicates over ``document_metadata``,
``document_people`` and D156's ``document_tags``, so the readers can never
disagree about what "authors include Alice" or "tagged project:x" means. It
builds SQL text and parameters only; it runs nothing.

D134's fields are tested on a document version; D156 tags on the version's
lineage (``document_tags.doc_id``), so every version of a lineage carries its
current tags. Tags must be resolved to ``tag_id`` strings first
(``rememberstack.spine.tags.resolve_filter_tags_on``), which is also where an
unregistered tag is refused.

With a D140 time scope, the filter and the scope's per-occurrence test hold on
the **same** occurrence (D156 §4.3): :func:`claim_selected_matching` for
claims, :func:`matching_occurrence_exists` with ``in_scope`` for a fact's
supporting claims.

Every statement that uses it binds ``:deployment_id``; the scoped forms also
bind the ``TextScope`` parameters.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from typing import Literal
from uuid import UUID

from remember.models import DocumentSearchFilters
from rememberstack.core.document_metadata import normalize_name
from rememberstack.core.text_scope import lineage_periodised
from rememberstack.core.text_scope import WINDOW_SQL


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
    tag_predicates, tag_parameters = tag_predicates_on(
        filters=filters, doc=doc, prefix=prefix
    )
    predicates.extend(tag_predicates)
    parameters.update(tag_parameters)
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


def tag_predicates_on(
    *, filters: DocumentSearchFilters, doc: str, prefix: str
) -> tuple[list[str], dict[str, Any]]:
    """D156 §4.1 predicates over lineage ``doc``'s current tags.

    One ``EXISTS`` per ``all`` tag and one for the ``any`` list, so the
    planner can start from the tag index (``ix_document_tags_tag``) for a
    narrow filter; ``none`` is one ``NOT EXISTS``. An untagged lineage fails
    ``all`` and ``any`` and matches ``none``.
    """
    tags = filters.tags
    if tags is None:
        return [], {}
    predicates: list[str] = []
    parameters: dict[str, Any] = {}

    def assigned(*, parameter: str, many: bool) -> str:
        tag_test = (
            f"assigned.tag_id = ANY(CAST(:{parameter} AS uuid[]))"
            if many
            else f"assigned.tag_id = CAST(:{parameter} AS uuid)"
        )
        return (
            "EXISTS (SELECT 1 FROM document_tags assigned"
            " WHERE assigned.deployment_id = :deployment_id"
            f" AND assigned.doc_id = {doc} AND {tag_test})"
        )

    for index, tag_id in enumerate(resolved_tag_ids(tags.all)):
        parameter = f"{prefix}tags_all_{index}"
        predicates.append(assigned(parameter=parameter, many=False))
        parameters[parameter] = tag_id
    if tags.any:
        parameter = f"{prefix}tags_any"
        predicates.append(assigned(parameter=parameter, many=True))
        parameters[parameter] = resolved_tag_ids(tags.any)
    if tags.none:
        parameter = f"{prefix}tags_none"
        predicates.append(f"NOT {assigned(parameter=parameter, many=True)}")
        parameters[parameter] = resolved_tag_ids(tags.none)
    return predicates, parameters


def resolved_tag_ids(tags: Sequence[str]) -> list[str]:
    """The ``tag_id`` strings of a resolved tag list, without repeats.

    A name here means the caller skipped resolution, which would otherwise
    surface as a database cast error; it is refused plainly instead.
    """
    try:
        return list(dict.fromkeys(str(UUID(tag)) for tag in tags))
    except ValueError as error:
        raise ValueError(
            "document filter tags must be resolved to tag ids before compiling"
        ) from error


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


def live_matching_versions(
    *, filters: DocumentSearchFilters, prefix: str
) -> tuple[str, dict[str, Any]]:
    """The live versions whose metadata matches, as a ``version_id`` query.

    The set form of :func:`live_version_matches` (same parameters for the
    same ``prefix``), for statements that start from the matching versions.
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
        "SELECT dm.version_id FROM document_metadata dm"
        " JOIN document_versions dv"
        "   ON dv.deployment_id = dm.deployment_id AND dv.version_id = dm.version_id"
        " JOIN documents dd"
        "   ON dd.deployment_id = dv.deployment_id AND dd.doc_id = dv.doc_id"
        " WHERE dm.deployment_id = :deployment_id"
        "   AND dv.deleted_at IS NULL AND dd.deleted_at IS NULL"
        f"{where}"
    ), parameters


def matching_occurrence_exists(
    *,
    filters: DocumentSearchFilters,
    claim: str,
    prefix: str,
    in_scope: Literal["any", "selected_periodised", "evidence"] = "any",
) -> tuple[str, dict[str, Any]]:
    """One predicate: claim ``claim`` has a live occurrence in a matching version.

    An occurrence is a ``chunk_claims`` row; it is live when its chunk belongs
    to its version's current reading. A claim reused across versions (D56)
    has one occurrence per version, so each version is tested on its own.

    ``in_scope`` adds the D140 time scope's per-occurrence test to the **same**
    occurrence (D156 §4.3), reading the scope's bound parameters:

    - ``selected_periodised``: the occurrence lies in a periodised version the
      scope selects (the claim rule of ``text_scope.occurrence_selected``);
    - ``evidence``: the occurrence lies in a selected version or in any live
      version of an undeclared lineage (the fact evidence gate of
      ``text_scope.fact_in_scope``).
    """
    version_sql, parameters = live_version_matches(
        filters=filters, version="occurrence_chunk.version_id", prefix=prefix
    )
    scope_join = ""
    scope_sql = ""
    if in_scope != "any":
        scope_join = (
            " JOIN document_version_scope occurrence_scope"
            "   ON occurrence_scope.deployment_id = occurrence_chunk.deployment_id"
            "  AND occurrence_scope.version_id = occurrence_chunk.version_id"
        )
        selected = (
            f"occurrence_scope.selectable AND occurrence_scope.in_force && {WINDOW_SQL}"
        )
        scope_sql = (
            f" AND occurrence_scope.periodised AND {selected}"
            if in_scope == "selected_periodised"
            else f" AND (NOT occurrence_scope.periodised OR ({selected}))"
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
        f"{scope_join}"
        " WHERE occurrence.deployment_id = :deployment_id"
        f"   AND occurrence.claim_id = {claim}"
        f"{scope_sql}"
        f"   AND {version_sql})"
    ), parameters


def claim_selected_matching(
    *,
    filters: DocumentSearchFilters,
    claim: str,
    doc: str,
    current_testimony: str,
    prefix: str,
) -> tuple[str, dict[str, Any]]:
    """One predicate: the time scope selects claim ``claim`` through a matching occurrence.

    The filtered form of ``text_scope.claim_selected`` (D140 §3.4 with D156
    §4.3). A claim of a periodised lineage qualifies only through one
    occurrence that both lies in a version the scope selects and lies in a
    matching version; two different occurrences passing one test each are not
    enough. A claim of an undeclared lineage keeps today's current-testimony
    rule, which tests no occurrence, plus a live matching occurrence.
    """
    any_occurrence, parameters = matching_occurrence_exists(
        filters=filters, claim=claim, prefix=prefix
    )
    selected_occurrence, _ = matching_occurrence_exists(
        filters=filters, claim=claim, prefix=prefix, in_scope="selected_periodised"
    )
    return (
        f"((NOT {lineage_periodised(doc=doc)} AND {current_testimony}"
        f" AND {any_occurrence}) OR {selected_occurrence})"
    ), parameters


def any_live_document_matches(
    *, filters: DocumentSearchFilters, prefix: str
) -> tuple[str, dict[str, Any]]:
    """A statement answering whether any live version matches the filter (§4.5).

    Run only when a completed filtered read is empty, to say whether no live
    document matched or the matching documents hold nothing relevant.
    """
    versions_sql, parameters = live_matching_versions(filters=filters, prefix=prefix)
    return f"SELECT EXISTS ({versions_sql})", parameters


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
