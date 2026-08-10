"""Pure guards that retained operations consume the accepted query authorities."""

from rememberstack.spine import CANONICAL_OPERATIONS
from rememberstack.surfaces.query_engine import _CONFIRM_CHUNKS
from rememberstack.surfaces.query_engine import _CONFIRM_CHUNKS_SCOPED
from rememberstack.surfaces.query_engine import _CONFIRM_CLAIMS_CURRENT
from rememberstack.surfaces.query_engine import _CONFIRM_CLAIMS_CURRENT_SCOPED
from rememberstack.surfaces.query_engine import _CONFIRM_FACT_CONTEXT
from rememberstack.surfaces.query_engine import _CONTRADICTION_MEMBERS
from rememberstack.surfaces.query_engine import _CURRENT_FACT_LABELS
from rememberstack.surfaces.query_engine import _MULTI_HOP_EDGE_EVIDENCE
from rememberstack.surfaces.query_engine import _RESOLVE_CONTEXT_HITS
from rememberstack.surfaces.query_engine import _RESOLVE_T0


def test_public_catalog_is_exactly_the_four_assured_operations() -> None:
    """Demoted patterns cannot re-enter API, CLI, SDK, or MCP discovery."""
    assert {operation.name.value for operation in CANONICAL_OPERATIONS} == {
        "resolve_entity",
        "testimony_context",
        "fact_context",
        "answer_context",
    }


def test_entity_resolution_uses_memory_v1_identity_and_adjacency() -> None:
    """Resolution does not recreate survivor or current-edge predicates."""
    identity_sql = str(_RESOLVE_T0)
    adjacency_sql = str(_RESOLVE_CONTEXT_HITS)
    assert "memory_v1.entity_aliases_current" in identity_sql
    assert "memory_v1.entities_current" in identity_sql
    assert "FROM aliases" not in identity_sql
    assert "memory_v1.graph_edges_current" in adjacency_sql
    assert "FROM relations" not in adjacency_sql


def test_fact_context_uses_fact_and_contradiction_authorities() -> None:
    """Current membership, D54 state, and co-members come from memory_v1."""
    confirmation_sql = str(_CONFIRM_FACT_CONTEXT)
    assert "memory_v1.facts_visible_history" in confirmation_sql
    assert "fact.valid_until > :evaluated_at" in confirmation_sql
    assert "fact.fact_kind = requested.kind" in confirmation_sql
    assert "JOIN relations" not in confirmation_sql
    assert "JOIN observations" not in confirmation_sql
    for statement in _CONTRADICTION_MEMBERS.values():
        contradiction_sql = str(statement)
        assert "memory_v1.contradiction_members_current" in contradiction_sql
        assert "memory_v1.facts_current" not in contradiction_sql
        assert "contradiction_group = ANY(CAST(:groups AS uuid[]))" in (
            contradiction_sql
        )
        assert "FROM relations" not in contradiction_sql
        assert "FROM observations" not in contradiction_sql
    label_sql = str(_CURRENT_FACT_LABELS)
    assert "memory_v1.facts_current" in label_sql
    assert "fact.fact_id = ANY(CAST(:fact_ids AS uuid[]))" in label_sql
    assert "FROM relations" not in label_sql
    assert "FROM observations" not in label_sql


def test_testimony_context_confirms_claims_and_chunks_through_memory_v1() -> None:
    """The retained evidence operation cannot revive orphan or stale content."""
    claim_sql = str(_CONFIRM_CLAIMS_CURRENT)
    chunk_sql = str(_CONFIRM_CHUNKS)
    assert "memory_v1.claims_live" in claim_sql
    assert "JOIN memory_v1.documents_live" in claim_sql
    assert "FROM claims" not in claim_sql
    assert "memory_v1.chunks_live" in chunk_sql
    assert "JOIN memory_v1.sections_live" in chunk_sql
    assert "JOIN memory_v1.documents_live" in chunk_sql
    assert "FROM chunks" not in chunk_sql
    for scoped in (_CONFIRM_CLAIMS_CURRENT_SCOPED, _CONFIRM_CHUNKS_SCOPED):
        scoped_sql = str(scoped)
        assert "memory_v1.mentions_live" in scoped_sql
        assert "resolved_entity_id = ANY(CAST(:entity_ids AS uuid[]))" in scoped_sql


def test_graph_enrichment_confirms_against_the_current_edge_authority() -> None:
    """The remaining internal graph hydrator cannot publish visible history as current."""
    graph_sql = str(_MULTI_HOP_EDGE_EVIDENCE)
    assert "memory_v1.graph_edges_current" in graph_sql
    assert "graph_edges_visible_history" not in graph_sql
    assert "r.support_state = 'withdrawn'" in graph_sql
