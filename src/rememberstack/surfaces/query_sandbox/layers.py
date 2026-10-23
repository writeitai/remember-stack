"""D143 §7.5: the processing layer behind every sandbox view and function.

A view or function belongs to the layer whose data it reads; one reading
several belongs to the highest. The map lives here, beside the sandbox, and is
deliberately not part of the hashed query-surface manifest: the manifest, its
hash and ``/query/space`` stay what they are whatever a deployment serves.
"""

from collections.abc import Sequence
import json
from typing import Final

from rememberstack.model.client import ProcessingLayer

_SOURCES = ProcessingLayer.SOURCES
_CLAIMS = ProcessingLayer.CLAIMS
_FACTS = ProcessingLayer.FACTS

VIEW_LAYERS: Final[dict[str, ProcessingLayer]] = {
    "documents_live": _SOURCES,
    "document_versions_visible": _SOURCES,
    "sections_live": _SOURCES,
    "chunks_live": _SOURCES,
    "chunks_all_versions_live": _SOURCES,
    "document_effective_periods_live": _SOURCES,
    "document_crossrefs_live": _SOURCES,
    "claims_visible_history": _CLAIMS,
    "claims_canonical": _CLAIMS,
    "claims_live": _CLAIMS,
    "claim_occurrences_live": _CLAIMS,
    "testimony_currency_events_visible": _CLAIMS,
    "entity_document_mentions": _FACTS,
    "entities_current": _FACTS,
    "entity_aliases_current": _FACTS,
    "mentions_live": _FACTS,
    "identity_events_visible": _FACTS,
    "fact_claim_evidence_live": _FACTS,
    "evidence_lineage": _FACTS,
    "facts_visible_history": _FACTS,
    "facts_current": _FACTS,
    "contradiction_members_current": _FACTS,
    "graph_edges_current": _FACTS,
    "graph_edges_visible_history": _FACTS,
    "pages_live": _FACTS,
    "page_evidence_visible": _FACTS,
    # It reads fact and claim change events, so it needs the highest of them.
    "changes_visible": _FACTS,
}

FUNCTION_LAYERS: Final[dict[str, ProcessingLayer]] = {
    "semantic_chunks": _SOURCES,
    "lexical_chunks": _SOURCES,
    "fetch_chunk_bodies": _SOURCES,
    "versions_in_scope": _SOURCES,
    "effective_intervals": _SOURCES,
    "graph_citation_path": _SOURCES,
    "canonical_bounds": _SOURCES,
    "semantic_claims": _CLAIMS,
    "lexical_claims": _CLAIMS,
    "semantic_facts": _FACTS,
    "semantic_entities": _FACTS,
    "facts_as_of": _FACTS,
    "graph_neighborhood": _FACTS,
    "graph_path": _FACTS,
    "fact_in_scope_support": _FACTS,
}

_ENTITY_FILTERED: Final = frozenset({"semantic_claims", "lexical_claims"})
"""Claim searches whose ``entity_id`` filter reads facts-owned mentions."""

_FILTERS_ARGUMENT: Final = 2
"""Position of the ``filters`` argument of a claim search."""


def statement_layers(
    *, views: Sequence[str], functions: Sequence[str]
) -> dict[ProcessingLayer, tuple[str, ...]]:
    """The layers a statement's views and functions read, with their names."""
    needed: dict[ProcessingLayer, list[str]] = {}
    for name in views:
        needed.setdefault(VIEW_LAYERS.get(name, _SOURCES), []).append(name)
    for name in functions:
        needed.setdefault(FUNCTION_LAYERS.get(name, _SOURCES), []).append(name)
    return {layer: tuple(names) for layer, names in needed.items()}


def entity_filtered_claim_search(
    *,
    bindings: Sequence[tuple[str, str, tuple[object, ...]]],
    parameters: Sequence[object],
) -> str | None:
    """The claim search that filters by ``entity_id``, literal or bound, if any.

    Such a search reads mentions, so it needs the facts layer as a mandatory
    dependency (D143 §7.2).
    """
    for _cte, function, arguments in bindings:
        if function not in _ENTITY_FILTERED or len(arguments) <= _FILTERS_ARGUMENT:
            continue
        filters = _argument_value(
            argument=arguments[_FILTERS_ARGUMENT], parameters=parameters
        )
        if isinstance(filters, (str, bytes)):
            try:
                filters = json.loads(filters)
            except ValueError:
                continue  # the nomination step rejects malformed filters
        if isinstance(filters, dict) and "entity_id" in filters:
            return function
    return None


def _argument_value(*, argument: object, parameters: Sequence[object]) -> object:
    """A literal argument, or the bound value of a ``$n`` placeholder."""
    if (
        isinstance(argument, tuple)
        and len(argument) == 3
        and argument[0] == "$"
        and isinstance(argument[1], int)
    ):
        index = argument[1] - 1
        return parameters[index] if 0 <= index < len(parameters) else None
    return argument
