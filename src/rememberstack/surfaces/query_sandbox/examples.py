"""The shipped `examples.*` queries (§5).

These are starting points, not platform operations. The platform wrote them, so
they are honest about what they compute and every one parses through the same
grammar an ad-hoc statement does — but their MEANING is not a platform
guarantee. Copy one, change its filters, and the copy is customer-authored,
which is the intended use rather than a misuse.

They are deliberately plain SQL over the `memory_v1` views. An example that
needed a trick the surface does not otherwise support would be teaching the
wrong thing.
"""

from __future__ import annotations

from typing import Final

#: name -> (purpose, SQL). The purpose is what a caller sees in discovery; it
#: says what the query answers, not how, because the how is right there.
EXAMPLE_QUERIES: Final[dict[str, tuple[str, str]]] = {
    "claims_verbatim": (
        "Claims as asserted, with the source that asserted them",
        "SELECT claim_id, claim_text, source_handle, source_kind, asserted_at"
        " FROM claims_live"
        " WHERE ($1::uuid IS NULL OR doc_id = $1::uuid)"
        " ORDER BY asserted_at DESC, claim_id"
        " LIMIT 50",
    ),
    "claims_about": (
        "Claims that mention an entity, newest assertion first",
        "SELECT c.claim_id, c.claim_text, c.source_handle, c.asserted_at"
        " FROM claims_live AS c"
        " JOIN mentions_live AS m"
        "   ON m.deployment_id = c.deployment_id AND m.claim_id = c.claim_id"
        " WHERE m.resolved_entity_id = $1::uuid"
        " ORDER BY c.asserted_at DESC, c.claim_id"
        " LIMIT 50",
    ),
    "claims_as_of": (
        "Claims as they stood at one instant on the assertion clock",
        "SELECT claim_id, claim_text, source_handle, asserted_at"
        " FROM claims_visible_history"
        " WHERE asserted_at <= $1::timestamptz"
        "   AND (claim_valid_until IS NULL OR claim_valid_until > $1::timestamptz)"
        " ORDER BY asserted_at DESC, claim_id"
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
        "Every live document that mentions an entity, most mentions first",
        "SELECT doc_id, mention_count"
        " FROM entity_document_mentions"
        " WHERE entity_id = $1::uuid"
        " ORDER BY mention_count DESC, doc_id"
        " LIMIT 50",
    ),
    "pages_about": (
        "Compiled pages that cite an entity",
        "SELECT p.page_id, p.title"
        " FROM pages_live AS p"
        " JOIN page_evidence_visible AS e"
        "   ON e.deployment_id = p.deployment_id AND e.page_id = p.page_id"
        " JOIN mentions_live AS m"
        "   ON m.deployment_id = e.deployment_id AND m.claim_id = e.claim_id"
        " WHERE m.resolved_entity_id = $1::uuid"
        " GROUP BY p.page_id, p.title"
        " ORDER BY p.title"
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
        "Which entity a mention resolved to, at one instant on both clocks",
        "SELECT fact_id, fact_kind, subject_entity_id, object_entity_id,"
        "       support_state_current, applied_valid_at, applied_believed_at"
        " FROM facts_as_of($1::timestamptz, $2::timestamptz, 200)"
        " ORDER BY fact_kind, fact_id",
    ),
    "entity_timeline": (
        "One entity's live mentions, in the order the system learned them",
        "SELECT m.claim_id, m.doc_id, c.asserted_at"
        " FROM mentions_live AS m"
        " JOIN claims_live AS c"
        "   ON c.deployment_id = m.deployment_id AND c.claim_id = m.claim_id"
        " WHERE m.resolved_entity_id = $1::uuid"
        " ORDER BY c.asserted_at, m.claim_id"
        " LIMIT 100",
    ),
    "explain": (
        "The plan for a statement, without running it",
        "SELECT claim_id, claim_text FROM claims_live"
        " WHERE doc_id = $1::uuid"
        " ORDER BY asserted_at DESC"
        " LIMIT 20",
    ),
    "multi_hop_context": (
        "Evidence along a route between two entities",
        "WITH route AS ("
        "  SELECT path_id, path_position, relation_id, fact_label"
        "  FROM graph_path($1::uuid, $2::uuid, 4)"
        ")"
        " SELECT r.path_id, r.path_position, r.fact_label,"
        "        e.claim_id, c.claim_text, c.source_handle"
        " FROM route AS r"
        " LEFT JOIN fact_claim_evidence_live AS e ON e.fact_id = r.relation_id"
        " LEFT JOIN claims_live AS c"
        "   ON c.deployment_id = e.deployment_id AND c.claim_id = e.claim_id"
        " ORDER BY r.path_id, r.path_position, e.claim_id"
        " LIMIT 100",
    ),
    "changed_since": (
        "What the system learned after an instant",
        "SELECT change_kind, subject_id, changed_at"
        " FROM changes_visible"
        " WHERE changed_at > $1::timestamptz"
        " ORDER BY changed_at DESC"
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
