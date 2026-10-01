"""The D140 time scope compiled to SQL fragments (no database)."""

from __future__ import annotations

from datetime import datetime
from datetime import UTC

import pytest

from remember.mcp_tools import tool
from remember.mcp_tools._documents import parse_search_documents_arguments
from remember.models import AtReadTime
from remember.models import HistoryReadTime
from remember.models import OverlapReadTime
from remember.models import SearchRequest
from rememberstack.core.text_scope import claim_selected
from rememberstack.core.text_scope import fact_in_scope
from rememberstack.core.text_scope import TextScope
from rememberstack.core.text_scope import version_selected
from rememberstack.core.text_scope import WINDOW_SQL
from rememberstack.model.assured_operations import AtFactTime
from rememberstack.model.assured_operations import OverlapFactTime

_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_AT = datetime(2026, 2, 1, tzinfo=UTC)
_TO = datetime(2026, 12, 31, tzinfo=UTC)


def test_each_mode_renders_its_inclusive_window() -> None:
    """The window is what the mode stands for, as on fact windows."""
    current = TextScope.of(time=None, evaluated_at=_NOW)
    assert (current.mode, current.lower, current.upper) == ("current", _NOW, _NOW)
    at = TextScope.of(time=AtReadTime(at=_AT), evaluated_at=_NOW)
    assert (at.mode, at.lower, at.upper, at.at) == ("at", _AT, _AT, _AT)
    overlap = TextScope.of(
        time=OverlapReadTime.model_validate({"from": _AT, "to": _TO}), evaluated_at=_NOW
    )
    assert (overlap.lower, overlap.upper) == (_AT, _TO)
    assert (overlap.range_start, overlap.range_end) == (_AT, _TO)
    history = TextScope.of(time=HistoryReadTime(), evaluated_at=_NOW)
    assert (history.lower, history.upper) == (None, _NOW)


def test_fact_time_selectors_are_accepted_too() -> None:
    """Operations pass FactTime; it is the same vocabulary."""
    assert TextScope.of(time=AtFactTime(at=_AT), evaluated_at=_NOW).upper == _AT
    overlap = TextScope.of(
        time=OverlapFactTime.model_validate({"from": _AT, "to": _TO}), evaluated_at=_NOW
    )
    assert (overlap.lower, overlap.upper) == (_AT, _TO)


def test_parameters_carry_both_clocks() -> None:
    scope = TextScope.of(time=AtReadTime(at=_AT), evaluated_at=_NOW, believed_at=_NOW)
    assert scope.parameters() == {
        "scope_lower": _AT,
        "scope_upper": _AT,
        "scope_mode": "at",
        "scope_at": _AT,
        "scope_range_start": None,
        "scope_range_end": None,
        "scope_evaluated_at": _NOW,
        "scope_believed_at": _NOW,
    }


def test_version_probe_is_a_primary_key_lookup_on_the_projection() -> None:
    probe = version_selected(version="published.version_id")
    assert "public.document_version_scope" in probe
    assert "scope_row.version_id = published.version_id" in probe
    assert "scope_row.selectable" in probe
    assert f"scope_row.in_force && {WINDOW_SQL}" in probe


def test_claim_selection_keeps_todays_rule_for_undeclared_lineages() -> None:
    predicate = claim_selected(
        claim="indexed.claim_id", doc="indexed.doc_id", current_testimony="TODAY"
    )
    assert "(NOT EXISTS" in predicate
    assert "periodised) AND TODAY)" in predicate
    assert "public.chunk_claims occurrence" in predicate
    assert "current_representation_id" in predicate


def test_fact_gate_reads_the_projection_or_the_ledgers() -> None:
    current = fact_in_scope(fact_kind="relation", fact_id="fact.fact_id")
    assert "public.relation_evidence" in current
    assert "fact_in_scope_support" not in current
    assert "NOT gate_relation_scope.periodised" in current
    pinned = fact_in_scope(
        fact_kind="observation", fact_id="fact.fact_id", pinned_belief=True
    )
    # a past belief instant is answered by the ledger function alone, which
    # reads the evidence itself; no vacuous "no live support" pass remains
    assert "memory_v1.fact_in_scope_support(" in pinned
    assert "NOT EXISTS" not in pinned
    with pytest.raises(ValueError, match="unknown fact kind"):
        fact_in_scope(fact_kind="entity", fact_id="x")


def test_time_is_on_the_wire_contracts() -> None:
    """Search bodies, the testimony operation and search_documents take time."""
    assert (
        SearchRequest.model_validate({"query": "x", "time": {"mode": "history"}}).time
        == HistoryReadTime()
    )
    assert SearchRequest(query="x").time is None
    definition = tool("claims_and_sources_context")
    assert "time" in definition.input_schema["properties"]  # type: ignore[operator]
    assert definition.tool_version == 3
    documents = tool("search_documents")
    assert "time" in documents.input_schema["properties"]  # type: ignore[operator]
    assert documents.tool_version == 2
    request = parse_search_documents_arguments(
        arguments={"time": {"mode": "at", "at": "2026-02-01T00:00:00Z"}}
    )
    assert request.time == AtReadTime(at=_AT)
