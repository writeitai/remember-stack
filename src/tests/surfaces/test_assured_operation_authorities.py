"""Pure guards that retained operations consume the accepted query authorities."""

from datetime import datetime
from datetime import UTC
import inspect
from time import monotonic
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from rememberstack.adapters import PostgresP1Index
from rememberstack.model.assured_operations import CurrentFactTime
from rememberstack.spine import CANONICAL_OPERATIONS
from rememberstack.surfaces.query_engine import _configure_fact_context_connection
from rememberstack.surfaces.query_engine import _CONFIRM_CHUNKS
from rememberstack.surfaces.query_engine import _CONFIRM_CHUNKS_SCOPED
from rememberstack.surfaces.query_engine import _CONFIRM_CLAIMS_CURRENT
from rememberstack.surfaces.query_engine import _CONFIRM_CLAIMS_CURRENT_SCOPED
from rememberstack.surfaces.query_engine import _confirm_fact_context
from rememberstack.surfaces.query_engine import _CONFIRM_FACT_CONTEXT_BY_KIND
from rememberstack.surfaces.query_engine import _CONTRADICTION_MEMBERS
from rememberstack.surfaces.query_engine import _CURRENT_FACT_EVIDENCE
from rememberstack.surfaces.query_engine import _CURRENT_FACT_LABELS
from rememberstack.surfaces.query_engine import _fact_context_confirmation_batch_size
from rememberstack.surfaces.query_engine import _FACT_CONTEXT_CONTRADICTION_MEMBERS
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
    confirmation_sql = "\n".join(
        str(statement) for statement in _CONFIRM_FACT_CONTEXT_BY_KIND.values()
    )
    evidence_sql = str(_CURRENT_FACT_EVIDENCE)
    ranked_sql = inspect.getsource(PostgresP1Index.search_facts_scored)
    assert "memory_v1.facts_visible_history" in confirmation_sql
    assert "requested AS MATERIALIZED" in confirmation_sql
    assert "fact.fact_id = ANY(CAST(:fact_ids AS uuid[]))" in confirmation_sql
    assert "fact.fact_kind = 'relation'" in confirmation_sql
    assert "fact.fact_kind = 'observation'" in confirmation_sql
    assert "fact.predicate = :predicate" in confirmation_sql
    assert "memory_v1.facts_visible_history" in ranked_sql
    assert "coverage DESC" in ranked_sql
    assert "entity_ids" in ranked_sql
    assert "fact.valid_until > :evaluated_at" in confirmation_sql
    assert "CAST(:fact_kinds AS text[])" not in confirmation_sql
    assert "JOIN relations" not in confirmation_sql
    assert "JOIN observations" not in confirmation_sql
    assert "review_queue" not in confirmation_sql
    assert "relation_evidence" not in confirmation_sql
    assert "observation_evidence" not in confirmation_sql
    assert "v_memory_evidence_lineage_live" in evidence_sql
    assert "memory_v1.evidence_lineage" not in evidence_sql
    assert "memory_v1.claims_live" in evidence_sql
    assert "memory_v1.documents_live" in evidence_sql
    assert "relation_evidence" not in evidence_sql
    assert "observation_evidence" not in evidence_sql
    assert "PARTITION BY requested.kind, requested.fact_id" in evidence_sql
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


def test_fact_context_bounds_planning_and_keeps_contradictions_kind_qualified() -> None:
    """Interactive fact reads stop before transport and avoid nested-loop expansion."""
    connection = MagicMock()

    _configure_fact_context_connection(connection=connection, deadline=125.0, now=100.0)

    assert [call.args[0] for call in connection.exec_driver_sql.call_args_list] == [
        "SET LOCAL statement_timeout = '25000ms'",
        "SET LOCAL transaction_timeout = '25000ms'",
        "SET LOCAL jit = off",
        "SET LOCAL join_collapse_limit = 1",
        "SET LOCAL from_collapse_limit = 1",
        "SET LOCAL max_parallel_workers_per_gather = 0",
        "SET LOCAL enable_nestloop = off",
    ]
    contradiction_sql = str(_FACT_CONTEXT_CONTRADICTION_MEMBERS)
    assert "memory_v1.facts_visible_history" in contradiction_sql
    assert "fact.ingested_at <= :evaluated_at" in contradiction_sql
    assert "fact.fact_kind AS kind" in contradiction_sql


def test_fact_confirmation_splits_kinds_and_restores_global_order() -> None:
    """A generic prepared plan cannot mix kind branches or reorder nominations."""
    observation_first = uuid4()
    relation_second = uuid4()
    observation_high_coverage = uuid4()
    connection = MagicMock()
    observed: list[tuple[str, list[object]]] = []

    def execute(statement: object, parameters: dict[str, object]) -> MagicMock:
        """Return kind-specific rows while recording the fixed SQL branch."""
        statement_sql = str(statement)
        fact_kind = (
            "relation" if "'relation'::text AS kind" in statement_sql else "observation"
        )
        raw_fact_ids = parameters["fact_ids"]
        assert isinstance(raw_fact_ids, list)
        fact_ids = list(raw_fact_ids)
        observed.append((fact_kind, fact_ids))
        result = MagicMock()
        result.mappings.return_value = (
            [{"coverage": 1, "kind": "relation", "fact_id": relation_second}]
            if fact_kind == "relation"
            else [
                {"coverage": 1, "kind": "observation", "fact_id": observation_first},
                {
                    "coverage": 2,
                    "kind": "observation",
                    "fact_id": observation_high_coverage,
                },
            ]
        )
        return result

    connection.execute.side_effect = execute
    rows = _confirm_fact_context(
        connection=connection,
        deployment_id=uuid4(),
        candidate_keys=(
            ("observation", observation_first),
            ("relation", relation_second),
            ("observation", observation_high_coverage),
        ),
        time=CurrentFactTime(),
        evaluated_at=datetime.now(UTC),
        entity_ids=(),
        ranking_entity_ids=(),
        predicate=None,
        deadline=monotonic() + 30,
    )

    assert observed == [
        ("relation", [relation_second]),
        ("observation", [observation_first, observation_high_coverage]),
    ]
    assert [row["fact_id"] for row in rows] == [
        observation_high_coverage,
        observation_first,
        relation_second,
    ]


def test_fact_context_confirmation_batch_tracks_requested_output() -> None:
    """Default fact context does not expand the maximum 30-row deep batch."""
    assert _fact_context_confirmation_batch_size(k=1) == 16
    assert _fact_context_confirmation_batch_size(k=15) == 16
    assert _fact_context_confirmation_batch_size(k=20) == 21
    assert _fact_context_confirmation_batch_size(k=30) == 30


def test_fact_context_refuses_another_statement_after_its_shared_deadline() -> None:
    """A refill loop cannot multiply the transport timeout by its batch count."""
    connection = MagicMock()

    with pytest.raises(TimeoutError, match="operation budget"):
        _configure_fact_context_connection(
            connection=connection, deadline=100.0, now=100.0
        )

    connection.exec_driver_sql.assert_not_called()


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
