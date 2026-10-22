"""Result-depth, search-work, and source-evidence guarantees at larger bounds."""

from typing import cast
from uuid import UUID

from pydantic import ValidationError
import pytest
from sqlalchemy.engine import RowMapping

from remember.mcp_tools import tool
from remember.retrieval import ContextRetrievalBudget
from rememberstack.surfaces.query_engine import _select_fact_evidence


@pytest.mark.parametrize(
    "k,depth", [(1, 60), (15, 60), (50, 200), (60, 240), (100, 400)]
)
def test_omitted_candidate_depth_uses_bounded_overfetch(k: int, depth: int) -> None:
    """Small pools retain a floor and larger result counts expand search work."""
    budget = ContextRetrievalBudget(k=k)
    assert budget.candidate_depth == depth
    assert budget.resolved().model_dump() == {"k": k, "candidate_k": depth}


@pytest.mark.parametrize(
    "arguments",
    [
        {"k": 0},
        {"k": 101},
        {"k": True},
        {"candidate_k": 0},
        {"candidate_k": 401},
        {"candidate_k": False},
        {"k": 50, "candidate_k": 49},
    ],
)
def test_invalid_budgets_are_rejected(arguments: dict[str, object]) -> None:
    """A malformed or undersized pool cannot silently truncate requested work."""
    with pytest.raises(ValidationError):
        ContextRetrievalBudget.model_validate(arguments)


def test_explicit_search_work_is_independent_of_returned_count() -> None:
    """Consumers can increase recall work without increasing response counts."""
    budget = ContextRetrievalBudget(k=50, candidate_k=400)
    assert budget.k == 50
    assert budget.candidate_depth == 400


@pytest.mark.parametrize(
    "name", ["facts_context", "claims_and_sources_context", "combined_context"]
)
def test_every_context_tool_exposes_the_same_depth_controls(name: str) -> None:
    """MCP and API consumers can request the same bounds in every context path."""
    properties = cast(
        dict[str, dict[str, object]], tool(name).input_schema["properties"]
    )
    assert properties["k"]["default"] == 50
    assert properties["k"]["maximum"] == 100
    assert properties["candidate_k"]["maximum"] == 400


@pytest.mark.parametrize("count", [50, 60, 100])
def test_large_fact_results_preserve_both_available_evidence_stances(
    count: int,
) -> None:
    """Later facts receive evidence before early facts consume deeper support."""
    keys = tuple(("observation", UUID(int=i + 1)) for i in range(count))
    evidence = {
        (kind, fact_id, stance): [
            cast(
                RowMapping,
                {"kind": kind, "fact_id": fact_id, "stance": stance, "rank": rank},
            )
            for rank in range(3)
        ]
        for kind, fact_id in keys
        for stance in ("supports", "contradicts")
    }
    selected = _select_fact_evidence(
        fact_keys=keys,
        evidence_by_fact_stance=evidence,
        evidence_per_fact=3,
        budget=max(60, 2 * count),
    )
    assert len(selected) == 2 * count
    assert {(row["fact_id"], row["stance"]) for row in selected} == {
        (fact_id, stance)
        for _, fact_id in keys
        for stance in ("supports", "contradicts")
    }
    assert all(row["rank"] == 0 for row in selected)
