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

from datetime import datetime
from datetime import timezone
from typing import Final
from uuid import UUID

#: Deterministic operator-owned fixture identities for empty-deployment proofs.
#: Not corpus seed data — only bound parameters for the four §5 fixture classes.
_FIXTURE_ENTITY: Final = UUID("00000000-0000-4000-8000-0000000000e1")
_FIXTURE_OTHER: Final = UUID("00000000-0000-4000-8000-0000000000e2")
_FIXTURE_INSTANT: Final = datetime(2024, 1, 1, tzinfo=timezone.utc)

#: name -> (purpose, SQL). The purpose is what a caller sees in discovery; it
#: says what the query answers, not how, because the how is right there.
EXAMPLE_QUERIES: Final[dict[str, tuple[str, str]]] = {
    "claims_verbatim": (
        "Claims as asserted, nominated semantically and joined to live testimony",
        "SELECT s.claim_id, s.rank, s.channel, c.claim_text, c.source_handle,"
        "       c.source_kind, c.asserted_at"
        " FROM semantic_claims($1, 20) AS s"
        " JOIN claims_live AS c ON c.claim_id = s.claim_id"
        " ORDER BY s.rank, s.claim_id"
        " LIMIT 20",
    ),
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
        "SELECT p.artifact_id, p.page_kind, p.git_path, p.status"
        " FROM pages_live AS p"
        " JOIN page_evidence_visible AS e"
        "   ON e.deployment_id = p.deployment_id AND e.artifact_id = p.artifact_id"
        " JOIN mentions_live AS m"
        "   ON m.deployment_id = e.deployment_id"
        "  AND m.claim_id = e.target_id"
        "  AND e.target_kind = 'claim'"
        " WHERE m.resolved_entity_id = $1::uuid"
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
        "SELECT f.fact_kind, f.fact_id, f.predicate, f.fact_label,"
        "       f.valid_from, f.valid_until, f.ingested_at,"
        "       e.stance, e.claim_id, e.source_handle, e.asserted_at,"
        "       l.doc_id, l.claim_count, l.representative_claim_id,"
        "       d.title AS source_title, d.source_kind AS document_source_kind"
        " FROM facts_visible_history AS f"
        " JOIN fact_claim_evidence_live AS e"
        "   ON e.deployment_id = f.deployment_id"
        "  AND e.fact_kind = f.fact_kind"
        "  AND e.fact_id = f.fact_id"
        " JOIN evidence_lineage AS l"
        "   ON l.deployment_id = f.deployment_id"
        "  AND l.fact_kind = f.fact_kind"
        "  AND l.fact_id = f.fact_id"
        "  AND l.doc_id = e.doc_id"
        "  AND l.stance = e.stance"
        " JOIN documents_live AS d"
        "   ON d.deployment_id = l.deployment_id AND d.doc_id = l.doc_id"
        " WHERE f.fact_id = $1::uuid"
        " ORDER BY e.stance, e.asserted_at DESC NULLS LAST, e.claim_id"
        " LIMIT 100",
    ),
    "multi_hop_context": (
        "Evidence along a route between two entities, with semantic nominations",
        "WITH route AS ("
        "  SELECT path_id, path_position, relation_id, fact_label, predicate"
        "  FROM graph_path($1::uuid, $2::uuid, 4)"
        "),"
        " nominated AS ("
        "  SELECT claim_id, rank FROM semantic_claims($3, 20)"
        " )"
        " SELECT r.path_id, r.path_position, r.fact_label, r.predicate,"
        "        e.claim_id, e.stance, c.claim_text, c.source_handle, n.rank"
        " FROM route AS r"
        " JOIN fact_claim_evidence_live AS e"
        "   ON e.fact_id = r.relation_id AND e.fact_kind = 'relation'"
        " JOIN claims_live AS c"
        "   ON c.deployment_id = e.deployment_id AND c.claim_id = e.claim_id"
        " JOIN nominated AS n ON n.claim_id = c.claim_id"
        " ORDER BY r.path_id, r.path_position, n.rank, e.claim_id"
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
        "SELECT path_id, hop, from_entity_id, to_entity_id, predicate, fact_label"
        " FROM graph_neighborhood($1::uuid, 2)"
        " ORDER BY path_id",
    ),
    "graph_path": (
        "Routes between two entities, each returned whole",
        "SELECT path_id, path_length, path_position, predicate, fact_label"
        " FROM graph_path($1::uuid, $2::uuid, 4)"
        " ORDER BY path_id, path_position",
    ),
}


#: Operator-owned parameter metadata for the four §5 fixture classes, per
#: shipped example. Positive requires successful completion (empty rows are
#: fine). Empty and tombstone must return no rows. Cap must stay within the
#: requested max_rows. On an empty deployment with a no-op search adapter the
#: same bound parameters satisfy all four classes without seed infrastructure.
EXAMPLE_FIXTURE_PARAMETERS: Final[
    dict[str, dict[str, tuple[object, ...] | int | None]]
] = {
    "claims_verbatim": {
        "positive": ("memory",),
        "empty": ("memory",),
        "tombstone": ("memory",),
        "cap": ("memory",),
        "cap_max_rows": 5,
    },
    "claims_about": {
        "positive": (_FIXTURE_ENTITY,),
        "empty": (_FIXTURE_ENTITY,),
        "tombstone": (_FIXTURE_ENTITY,),
        "cap": (_FIXTURE_ENTITY,),
        "cap_max_rows": 5,
    },
    "claims_as_of": {
        "positive": (_FIXTURE_INSTANT, _FIXTURE_INSTANT),
        "empty": (_FIXTURE_INSTANT, _FIXTURE_INSTANT),
        "tombstone": (_FIXTURE_INSTANT, _FIXTURE_INSTANT),
        "cap": (_FIXTURE_INSTANT, _FIXTURE_INSTANT),
        "cap_max_rows": 5,
    },
    "claims_hybrid_rrf": {
        "positive": ("memory",),
        "empty": ("memory",),
        "tombstone": ("memory",),
        "cap": ("memory",),
        "cap_max_rows": 5,
    },
    "chunks_hybrid_rrf": {
        "positive": ("memory",),
        "empty": ("memory",),
        "tombstone": ("memory",),
        "cap": ("memory",),
        "cap_max_rows": 5,
    },
    "chunk_neighbors": {
        "positive": (_FIXTURE_ENTITY,),
        "empty": (_FIXTURE_ENTITY,),
        "tombstone": (_FIXTURE_ENTITY,),
        "cap": (_FIXTURE_ENTITY,),
        "cap_max_rows": 5,
    },
    "documents_about": {
        "positive": (_FIXTURE_ENTITY,),
        "empty": (_FIXTURE_ENTITY,),
        "tombstone": (_FIXTURE_ENTITY,),
        "cap": (_FIXTURE_ENTITY,),
        "cap_max_rows": 5,
    },
    "pages_about": {
        "positive": (_FIXTURE_ENTITY,),
        "empty": (_FIXTURE_ENTITY,),
        "tombstone": (_FIXTURE_ENTITY,),
        "cap": (_FIXTURE_ENTITY,),
        "cap_max_rows": 5,
    },
    "relation_current": {
        "positive": (_FIXTURE_ENTITY,),
        "empty": (_FIXTURE_ENTITY,),
        "tombstone": (_FIXTURE_ENTITY,),
        "cap": (_FIXTURE_ENTITY,),
        "cap_max_rows": 5,
    },
    "observation_current": {
        "positive": (_FIXTURE_ENTITY,),
        "empty": (_FIXTURE_ENTITY,),
        "tombstone": (_FIXTURE_ENTITY,),
        "cap": (_FIXTURE_ENTITY,),
        "cap_max_rows": 5,
    },
    "identity_as_of": {
        "positive": (_FIXTURE_ENTITY, _FIXTURE_INSTANT),
        "empty": (_FIXTURE_ENTITY, _FIXTURE_INSTANT),
        "tombstone": (_FIXTURE_ENTITY, _FIXTURE_INSTANT),
        "cap": (_FIXTURE_ENTITY, _FIXTURE_INSTANT),
        "cap_max_rows": 5,
    },
    "entity_timeline": {
        "positive": (_FIXTURE_ENTITY,),
        "empty": (_FIXTURE_ENTITY,),
        "tombstone": (_FIXTURE_ENTITY,),
        "cap": (_FIXTURE_ENTITY,),
        "cap_max_rows": 5,
    },
    "explain": {
        "positive": (_FIXTURE_ENTITY,),
        "empty": (_FIXTURE_ENTITY,),
        "tombstone": (_FIXTURE_ENTITY,),
        "cap": (_FIXTURE_ENTITY,),
        "cap_max_rows": 5,
    },
    "multi_hop_context": {
        "positive": (_FIXTURE_ENTITY, _FIXTURE_OTHER, "memory"),
        "empty": (_FIXTURE_ENTITY, _FIXTURE_OTHER, "memory"),
        "tombstone": (_FIXTURE_ENTITY, _FIXTURE_OTHER, "memory"),
        "cap": (_FIXTURE_ENTITY, _FIXTURE_OTHER, "memory"),
        "cap_max_rows": 5,
    },
    "changed_since": {
        "positive": (_FIXTURE_INSTANT,),
        "empty": (_FIXTURE_INSTANT,),
        "tombstone": (_FIXTURE_INSTANT,),
        "cap": (_FIXTURE_INSTANT,),
        "cap_max_rows": 5,
    },
    "graph_neighborhood": {
        "positive": (_FIXTURE_ENTITY,),
        "empty": (_FIXTURE_ENTITY,),
        "tombstone": (_FIXTURE_ENTITY,),
        "cap": (_FIXTURE_ENTITY,),
        "cap_max_rows": 5,
    },
    "graph_path": {
        "positive": (_FIXTURE_ENTITY, _FIXTURE_OTHER),
        "empty": (_FIXTURE_ENTITY, _FIXTURE_OTHER),
        "tombstone": (_FIXTURE_ENTITY, _FIXTURE_OTHER),
        "cap": (_FIXTURE_ENTITY, _FIXTURE_OTHER),
        "cap_max_rows": 5,
    },
}


def example_operator_fixtures(name: str) -> dict[str, dict[str, object]]:
    """Materialize the four §5 operator fixtures for one shipped example.

    Returns a mapping kind -> {parameters, max_rows?} suitable for building
    `OperatorFixture` values at the call site (keeps this module free of the
    registry import cycle).
    """
    if name not in EXAMPLE_FIXTURE_PARAMETERS:
        raise KeyError(f"no operator fixtures for example {name!r}")
    meta = EXAMPLE_FIXTURE_PARAMETERS[name]
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
