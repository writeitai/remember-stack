"""The shipped `examples.*` queries (§2, §5).

These are starting points, not platform operations. The platform wrote them, so
they are honest about what they compute and every one parses through the same
grammar an ad-hoc statement does — but their MEANING is not a platform
guarantee. Copy one, change its filters, and the copy is customer-authored,
which is the intended use rather than a misuse.

Bodies follow the exact §2 binding mappings. Every demotion example uses the
§3.3 D48 INNER JOIN/`EXISTS` authorization template; none carries a legacy
LEFT JOIN orphan branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Final
from uuid import UUID

from rememberstack.core.open_query_prose import CLAIMS_VERBATIM_PURPOSE
from rememberstack.core.open_query_prose import CLAIMS_VERBATIM_SQL

#: Deterministic never-present identities for the empty fixture class.
_EMPTY_ENTITY: Final = UUID("00000000-0000-4000-8000-0000000000e1")
_EMPTY_CHUNK: Final = UUID("00000000-0000-4000-8000-0000000000c1")
_EMPTY_FACT: Final = UUID("00000000-0000-4000-8000-0000000000f1")
_EMPTY_INSTANT: Final = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FAR_FUTURE: Final = datetime(2099, 1, 1, tzinfo=timezone.utc)

#: name -> (purpose, SQL). The purpose is what a caller sees in discovery; it
#: says what the query answers, not how, because the how is right there.
#: ``claims_verbatim`` purpose/SQL are owned by core/open_query_prose so the
#: semantic-to-relational worked example cannot drift from this registry body.
EXAMPLE_QUERIES: Final[dict[str, tuple[str, str]]] = {
    "claims_verbatim": (CLAIMS_VERBATIM_PURPOSE, CLAIMS_VERBATIM_SQL),
    "claims_about": (
        "Claims that mention an entity, via live claim occurrences",
        "SELECT c.claim_id, c.claim_text, c.source_handle, c.asserted_at,"
        "       o.chunk_id, o.derivation_kind"
        " FROM mentions_live AS m"
        " JOIN claim_occurrences_live AS o"
        "   ON o.deployment_id = m.deployment_id AND o.claim_id = m.claim_id"
        " JOIN claims_live AS c"
        "   ON c.deployment_id = o.deployment_id AND c.claim_id = o.claim_id"
        " WHERE m.resolved_entity_id = $1::uuid"
        " ORDER BY c.asserted_at DESC, c.claim_id"
        " LIMIT 50",
    ),
    "claims_as_of": (
        "Claims whose immutable validity window overlaps an inclusive interval",
        "SELECT c.claim_id, c.claim_text, c.source_handle,"
        "       c.claim_valid_from, c.claim_valid_until, c.claim_valid_precision,"
        "       ("
        "         SELECT count(*) FROM claims_visible_history AS u"
        "         WHERE u.claim_valid_precision = 'unknown'"
        "           AND u.claim_valid_from <= $2::timestamptz"
        "           AND (u.claim_valid_until IS NULL"
        "                OR u.claim_valid_until >= $1::timestamptz)"
        "       ) AS unknown_precision_excluded"
        " FROM claims_visible_history AS c"
        " WHERE c.claim_valid_precision <> 'unknown'"
        "   AND c.claim_valid_from <= $2::timestamptz"
        "   AND (c.claim_valid_until IS NULL"
        "        OR c.claim_valid_until >= $1::timestamptz)"
        " ORDER BY c.claim_valid_from DESC, c.claim_id"
        " LIMIT 50",
    ),
    "claims_hybrid_rrf": (
        "Semantic and lexical claim channels fused by reciprocal rank",
        "WITH semantic AS (SELECT claim_id, rank FROM semantic_claims($1, 20)),"
        " lexical AS (SELECT claim_id, rank FROM lexical_claims($1, 20))"
        " SELECT coalesce(s.claim_id, l.claim_id) AS claim_id,"
        "        coalesce(1.0 / (60 + s.rank), 0)"
        "          + coalesce(1.0 / (60 + l.rank), 0) AS fused"
        " FROM semantic AS s"
        " FULL JOIN lexical AS l ON l.claim_id = s.claim_id"
        " ORDER BY fused DESC, claim_id"
        " LIMIT 20",
    ),
    "chunks_hybrid_rrf": (
        "Semantic and lexical chunk channels fused by reciprocal rank",
        "WITH semantic AS (SELECT chunk_id, rank FROM semantic_chunks($1, 20)),"
        " lexical AS (SELECT chunk_id, rank FROM lexical_chunks($1, 20))"
        " SELECT coalesce(s.chunk_id, l.chunk_id) AS chunk_id,"
        "        coalesce(1.0 / (60 + s.rank), 0)"
        "          + coalesce(1.0 / (60 + l.rank), 0) AS fused"
        " FROM semantic AS s"
        " FULL JOIN lexical AS l ON l.chunk_id = s.chunk_id"
        " ORDER BY fused DESC, chunk_id"
        " LIMIT 20",
    ),
    "chunk_neighbors": (
        "The chunks either side of one chunk in its current section",
        "SELECT n.chunk_id, n.ordinal, n.section_id"
        " FROM chunks_live AS n"
        " JOIN chunks_live AS anchor"
        "   ON anchor.deployment_id = n.deployment_id"
        "  AND anchor.section_id = n.section_id"
        " WHERE anchor.chunk_id = $1::uuid"
        "   AND n.ordinal BETWEEN anchor.ordinal - 2 AND anchor.ordinal + 2"
        " ORDER BY n.ordinal",
    ),
    "documents_about": (
        "Every live document that mentions an entity, with its live metadata",
        "SELECT d.doc_id, d.title, d.source_kind, m.mention_count,"
        "       m.first_mentioned_at, m.last_mentioned_at"
        " FROM entity_document_mentions AS m"
        " JOIN documents_live AS d"
        "   ON d.deployment_id = m.deployment_id AND d.doc_id = m.doc_id"
        " WHERE m.entity_id = $1::uuid"
        " ORDER BY m.mention_count DESC, d.doc_id"
        " LIMIT 50",
    ),
    "pages_about": (
        "Compiled pages that cite an entity through live page evidence",
        # Document-target page evidence joins entity_document_mentions so the
        # body has a positive path on the Batch A corpus (claim-target page
        # evidence alone may not share a live mention with the entity).
        "SELECT p.artifact_id, p.page_kind, p.git_path, p.status"
        " FROM pages_live AS p"
        " JOIN page_evidence_visible AS e"
        "   ON e.deployment_id = p.deployment_id AND e.artifact_id = p.artifact_id"
        " JOIN entity_document_mentions AS m"
        "   ON m.deployment_id = e.deployment_id"
        "  AND e.target_kind = 'document'"
        "  AND m.doc_id = e.target_id"
        " WHERE m.entity_id = $1::uuid"
        " GROUP BY p.artifact_id, p.page_kind, p.git_path, p.status"
        " ORDER BY p.git_path, p.artifact_id"
        " LIMIT 50",
    ),
    "relation_current": (
        "Current relations for an entity, as adjudicated",
        "SELECT fact_id, predicate, subject_entity_id, object_entity_id,"
        "       evidence_count, contradict_count, support_state"
        " FROM facts_current"
        " WHERE fact_kind = 'relation'"
        "   AND (subject_entity_id = $1::uuid OR object_entity_id = $1::uuid)"
        " ORDER BY evidence_count DESC, fact_id"
        " LIMIT 50",
    ),
    "observation_current": (
        "Current observations about an entity",
        "SELECT fact_id, fact_label, evidence_count, contradict_count,"
        "       support_state, evaluated_at"
        " FROM facts_current"
        " WHERE fact_kind = 'observation' AND subject_entity_id = $1::uuid"
        " ORDER BY evidence_count DESC, fact_id"
        " LIMIT 50",
    ),
    "identity_as_of": (
        "Bounded identity-event transcript as of one decision instant",
        "SELECT object_kind, event_id, entity_id, related_entity_id, mention_id,"
        "       outcome, method, decided_by, decided_at, is_superseded"
        " FROM identity_events_visible"
        " WHERE entity_id = $1::uuid"
        "   AND decided_at <= $2::timestamptz"
        " ORDER BY decided_at DESC, event_id"
        " LIMIT 100",
    ),
    "entity_timeline": (
        "One entity's visible facts grouped by a disclosed time bucket",
        "SELECT date_trunc('day', valid_from) AS bucket,"
        "       fact_kind, count(*) AS fact_count"
        " FROM facts_visible_history"
        " WHERE subject_entity_id = $1::uuid"
        "    OR object_entity_id = $1::uuid"
        " GROUP BY 1, 2"
        " ORDER BY 1 NULLS LAST, 2"
        " LIMIT 200",
    ),
    "explain": (
        "Why the system holds a fact: history, live evidence, lineage, and source",
        # CTE anchors push the fact_id predicate into each view before join so
        # the planner does not expand the full history × evidence cross product.
        "WITH f AS ("
        "  SELECT * FROM facts_visible_history WHERE fact_id = $1::uuid"
        "),"
        " e AS ("
        "  SELECT * FROM fact_claim_evidence_live WHERE fact_id = $1::uuid"
        "),"
        " l AS ("
        "  SELECT * FROM evidence_lineage WHERE fact_id = $1::uuid"
        ")"
        " SELECT f.fact_kind, f.fact_id, f.predicate, f.fact_label,"
        "       f.valid_from, f.valid_until, f.ingested_at,"
        "       e.stance, e.claim_id, e.source_handle, e.asserted_at,"
        "       l.doc_id, l.claim_count, l.representative_claim_id,"
        "       d.title AS source_title, d.source_kind AS document_source_kind"
        " FROM f"
        " JOIN e"
        "   ON e.deployment_id = f.deployment_id"
        "  AND e.fact_kind = f.fact_kind"
        "  AND e.fact_id = f.fact_id"
        " JOIN l"
        "   ON l.deployment_id = f.deployment_id"
        "  AND l.fact_kind = f.fact_kind"
        "  AND l.fact_id = f.fact_id"
        "  AND l.doc_id = e.doc_id"
        "  AND l.stance = e.stance"
        " JOIN documents_live AS d"
        "   ON d.deployment_id = l.deployment_id AND d.doc_id = l.doc_id"
        " ORDER BY e.stance, e.asserted_at DESC NULLS LAST, e.claim_id"
        " LIMIT 100",
    ),
    "multi_hop_context": (
        "Evidence along a route between two entities, with semantic nominations",
        "WITH route AS ("
        "  SELECT hops, relation_ids, node_ids"
        "  FROM graph_path($1::uuid, $2::uuid, $3::uuid, 4)"
        "),"
        " nominated AS ("
        "  SELECT claim_id, rank FROM semantic_claims($4, 20)"
        " )"
        " SELECT r.hops, r.node_ids, e.fact_id AS relation_id,"
        "        e.claim_id, e.stance, c.claim_text, c.source_handle, n.rank"
        " FROM route AS r"
        " JOIN fact_claim_evidence_live AS e"
        "   ON e.fact_id = ANY(r.relation_ids) AND e.fact_kind = 'relation'"
        " JOIN claims_live AS c"
        "   ON c.deployment_id = e.deployment_id AND c.claim_id = e.claim_id"
        " JOIN nominated AS n ON n.claim_id = c.claim_id"
        " ORDER BY r.hops, r.node_ids, n.rank, e.claim_id"
        " LIMIT 100",
    ),
    "changed_since": (
        "What the system learned after an instant",
        "SELECT object_kind, object_id, occurred_at, label"
        " FROM changes_visible"
        " WHERE occurred_at > $1::timestamptz"
        " ORDER BY occurred_at DESC"
        " LIMIT 100",
    ),
    "graph_neighborhood": (
        "Relations within N hops of an entity",
        "SELECT hops, relation_ids, node_ids"
        " FROM graph_neighborhood($1::uuid, $2::uuid, 2)"
        " ORDER BY hops, relation_ids",
    ),
    "graph_path": (
        "Routes between two entities, each returned whole",
        "SELECT hops, relation_ids, node_ids"
        " FROM graph_path($1::uuid, $2::uuid, $3::uuid, 4)"
        " ORDER BY hops, relation_ids",
    ),
    "graph_citation_path": (
        "Directed citation routes between two live documents",
        "SELECT hops, crossref_ids, document_ids"
        " FROM graph_citation_path($1::uuid, $2::uuid, $3::uuid, 6)"
        " ORDER BY hops, crossref_ids",
    ),
}


#: Query text that a fixture search port maps to live claim/chunk nominations.
SEARCH_POSITIVE_QUERY: Final = "live-hit"
#: Query text that a fixture search port maps to zero nominations.
SEARCH_EMPTY_QUERY: Final = "empty-miss"
#: Query text that a fixture search port maps to tombstoned (unconfirmed) IDs.
SEARCH_TOMBSTONE_QUERY: Final = "tombstone-miss"


@dataclass(frozen=True)
class ExampleFixtureHandles:
    """Shared corpus handles that make the four fixture classes distinct.

    Built from the Batch A query-space corpus (or any deployment that exposes
    the same shape). Positive parameters address live content; empty addresses
    never-present IDs; tombstone addresses deleted/merged content that must
    not surface; cap reuses positive parameters under a tight row bound.
    """

    deployment_id: UUID
    live_entity: UUID
    other_entity: UUID
    empty_entity: UUID
    tombstone_entity: UUID
    live_chunk: UUID
    empty_chunk: UUID
    tombstone_chunk: UUID
    live_fact: UUID
    empty_fact: UUID
    tombstone_fact: UUID
    live_from_doc: UUID
    live_to_doc: UUID
    empty_doc: UUID
    tombstone_doc: UUID
    live_from: datetime
    live_to: datetime
    empty_from: datetime
    empty_to: datetime
    tombstone_from: datetime
    tombstone_to: datetime
    live_since: datetime
    empty_since: datetime
    tombstone_since: datetime


def example_fixture_parameters(
    name: str, *, handles: ExampleFixtureHandles
) -> dict[str, tuple[object, ...] | int]:
    """Distinct operator-owned parameters for the four §5 classes.

    The four classes must not collapse to identical parameters: positive uses
    live handles, empty uses never-present handles, tombstone uses deleted or
    merged handles, and cap reuses positive under `cap_max_rows`.
    """
    h = handles
    table: dict[str, dict[str, tuple[object, ...] | int]] = {
        "claims_verbatim": {
            "positive": (SEARCH_POSITIVE_QUERY,),
            "empty": (SEARCH_EMPTY_QUERY,),
            "tombstone": (SEARCH_TOMBSTONE_QUERY,),
            "cap": (SEARCH_POSITIVE_QUERY,),
            "cap_max_rows": 1,
        },
        "claims_about": {
            "positive": (h.live_entity,),
            "empty": (h.empty_entity,),
            "tombstone": (h.tombstone_entity,),
            "cap": (h.live_entity,),
            "cap_max_rows": 1,
        },
        "claims_as_of": {
            "positive": (h.live_from, h.live_to),
            "empty": (h.empty_from, h.empty_to),
            "tombstone": (h.tombstone_from, h.tombstone_to),
            "cap": (h.live_from, h.live_to),
            "cap_max_rows": 1,
        },
        "claims_hybrid_rrf": {
            "positive": (SEARCH_POSITIVE_QUERY,),
            "empty": (SEARCH_EMPTY_QUERY,),
            "tombstone": (SEARCH_TOMBSTONE_QUERY,),
            "cap": (SEARCH_POSITIVE_QUERY,),
            "cap_max_rows": 1,
        },
        "chunks_hybrid_rrf": {
            "positive": (SEARCH_POSITIVE_QUERY,),
            "empty": (SEARCH_EMPTY_QUERY,),
            "tombstone": (SEARCH_TOMBSTONE_QUERY,),
            "cap": (SEARCH_POSITIVE_QUERY,),
            "cap_max_rows": 1,
        },
        "chunk_neighbors": {
            "positive": (h.live_chunk,),
            "empty": (h.empty_chunk,),
            "tombstone": (h.tombstone_chunk,),
            "cap": (h.live_chunk,),
            "cap_max_rows": 1,
        },
        "documents_about": {
            "positive": (h.live_entity,),
            "empty": (h.empty_entity,),
            "tombstone": (h.tombstone_entity,),
            "cap": (h.live_entity,),
            "cap_max_rows": 1,
        },
        "pages_about": {
            "positive": (h.live_entity,),
            "empty": (h.empty_entity,),
            "tombstone": (h.tombstone_entity,),
            "cap": (h.live_entity,),
            "cap_max_rows": 1,
        },
        "relation_current": {
            "positive": (h.live_entity,),
            "empty": (h.empty_entity,),
            "tombstone": (h.tombstone_entity,),
            "cap": (h.live_entity,),
            "cap_max_rows": 1,
        },
        "observation_current": {
            "positive": (h.live_entity,),
            "empty": (h.empty_entity,),
            "tombstone": (h.tombstone_entity,),
            "cap": (h.live_entity,),
            "cap_max_rows": 1,
        },
        "identity_as_of": {
            "positive": (h.live_entity, h.live_to),
            "empty": (h.empty_entity, h.empty_to),
            "tombstone": (h.tombstone_entity, h.tombstone_to),
            "cap": (h.live_entity, h.live_to),
            "cap_max_rows": 1,
        },
        "entity_timeline": {
            "positive": (h.live_entity,),
            "empty": (h.empty_entity,),
            "tombstone": (h.tombstone_entity,),
            "cap": (h.live_entity,),
            "cap_max_rows": 1,
        },
        "explain": {
            "positive": (h.live_fact,),
            "empty": (h.empty_fact,),
            "tombstone": (h.tombstone_fact,),
            "cap": (h.live_fact,),
            "cap_max_rows": 1,
        },
        "multi_hop_context": {
            "positive": (
                h.deployment_id,
                h.live_entity,
                h.other_entity,
                SEARCH_POSITIVE_QUERY,
            ),
            "empty": (
                h.deployment_id,
                h.empty_entity,
                h.other_entity,
                SEARCH_EMPTY_QUERY,
            ),
            "tombstone": (
                h.deployment_id,
                h.tombstone_entity,
                h.other_entity,
                SEARCH_TOMBSTONE_QUERY,
            ),
            "cap": (
                h.deployment_id,
                h.live_entity,
                h.other_entity,
                SEARCH_POSITIVE_QUERY,
            ),
            "cap_max_rows": 1,
        },
        "changed_since": {
            "positive": (h.live_since,),
            "empty": (h.empty_since,),
            "tombstone": (h.tombstone_since,),
            "cap": (h.live_since,),
            "cap_max_rows": 1,
        },
        "graph_neighborhood": {
            "positive": (h.deployment_id, h.live_entity),
            "empty": (h.deployment_id, h.empty_entity),
            "tombstone": (h.deployment_id, h.tombstone_entity),
            "cap": (h.deployment_id, h.live_entity),
            "cap_max_rows": 1,
        },
        "graph_path": {
            "positive": (h.deployment_id, h.live_entity, h.other_entity),
            "empty": (h.deployment_id, h.empty_entity, h.other_entity),
            "tombstone": (h.deployment_id, h.tombstone_entity, h.other_entity),
            "cap": (h.deployment_id, h.live_entity, h.other_entity),
            "cap_max_rows": 1,
        },
        "graph_citation_path": {
            "positive": (h.deployment_id, h.live_from_doc, h.live_to_doc),
            "empty": (h.deployment_id, h.empty_doc, h.live_to_doc),
            "tombstone": (h.deployment_id, h.tombstone_doc, h.live_to_doc),
            "cap": (h.deployment_id, h.live_from_doc, h.live_to_doc),
            "cap_max_rows": 1,
        },
    }
    if name not in table:
        raise KeyError(f"no operator fixtures for example {name!r}")
    return table[name]


def example_operator_fixtures(
    name: str, *, handles: ExampleFixtureHandles
) -> dict[str, dict[str, object]]:
    """Materialize the four §5 operator fixtures for one shipped example.

    Returns a mapping kind -> {parameters, max_rows?} suitable for building
    `OperatorFixture` values at the call site (keeps this module free of the
    registry import cycle). Requires corpus handles so the four classes stay
    meaningfully distinct.
    """
    meta = example_fixture_parameters(name, handles=handles)
    cap_max = meta["cap_max_rows"]
    assert isinstance(cap_max, int)
    out: dict[str, dict[str, object]] = {}
    for kind in ("positive", "empty", "tombstone", "cap"):
        params = meta[kind]
        assert isinstance(params, tuple)
        entry: dict[str, object] = {"parameters": params}
        if kind == "cap":
            entry["max_rows"] = cap_max
        out[kind] = entry
    return out
