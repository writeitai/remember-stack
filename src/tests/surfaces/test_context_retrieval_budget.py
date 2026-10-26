"""Result-depth, search-work, and source-evidence guarantees at larger bounds."""

from datetime import datetime
from datetime import UTC
import json
from typing import cast
from uuid import UUID

import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy.engine import RowMapping

from remember.client import MemoryClient
from remember.mcp_tools import tool
from remember.retrieval import ContextRetrievalBudget
from rememberstack.model import ContextBundleV2
from rememberstack.model import current_temporal_scope
from rememberstack.model import Envelope
from rememberstack.model import Freshness
from rememberstack.model import Grain
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


@pytest.mark.parametrize(
    "name", ["facts_context", "claims_and_sources_context", "combined_context"]
)
def test_sdk_convenience_calls_forward_explicit_depth(name: str) -> None:
    """Typed convenience methods expose the same configurable budget as raw operations."""
    evaluated_at = datetime(2026, 10, 22, tzinfo=UTC)
    fact = Envelope(
        grain=Grain.FACT,
        temporal_scope=current_temporal_scope(evaluated_at=evaluated_at),
        freshness=Freshness(pg_live_ts=evaluated_at),
    )
    source = Envelope(
        grain=Grain.EVIDENCE,
        temporal_scope=current_temporal_scope(evaluated_at=evaluated_at),
        freshness=Freshness(pg_live_ts=evaluated_at),
    )
    result = (
        ContextBundleV2(facts=fact, claims_and_sources=source)
        if name == "combined_context"
        else fact
        if name == "facts_context"
        else source
    )
    seen: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Capture only the public argument object and return a valid typed response."""
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=result.model_dump(mode="json"))

    with httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    ) as raw:
        client = MemoryClient(client=raw)
        method = getattr(client, name)
        method(query="preferences", k=50, candidate_k=200)
    assert seen == [{"query": "preferences", "k": 50, "candidate_k": 200}]
