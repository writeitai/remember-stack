"""Fail closed when run identity, BEAM results or the latency workload drifts."""

from datetime import datetime
from datetime import UTC
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from benchmarks import d163_qualification as qualification
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.surfaces.query_sandbox import audit
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.limits import INTERACTIVE_LIMITS
from rememberstack.surfaces.query_sandbox.result import QueryResult
from rememberstack.surfaces.query_sandbox.result import ResultLimits


def _capture(*, head: str) -> dict[str, Any]:
    evaluated_at = "2025-01-01T00:00:00Z"
    beam_rows = {
        "facts_predicates": [["first", 2], ["second", 2]],
        "facts_sample": [],
        "claims_live_sample": [],
        "facts_sample_corrected": [["relation", "fact", "first", "label"]],
    }
    return {
        "schema_head": head,
        "surface_manifest_hash": qualification.MANIFEST_BY_HEAD[head],
        "sandbox_settings": {"jit": "on"},
        "case": {"evaluated_at": evaluated_at},
        "cuts": {
            "contradiction/broad": [],
            "native_final/broad": [],
            **{f"sandbox/{view}": [] for view in qualification.BROAD_VIEWS},
        },
        "sandbox": {
            view: {
                "termination": "completed",
                "error": None,
                "truncation": None,
                "limits": {"statement_timeout_ms": 5000},
                "work": {"plan": {"Execution Time": 1}, "error": None},
            }
            for view in qualification.BROAD_VIEWS
        },
        "raw": {},
        "ordered": {
            "final/broad": [("observation", "fact", {"returned": 1, "total": 2})]
        },
        "beam_evaluated_at": evaluated_at,
        "beam": {
            name: {
                "rows": rows,
                "columns": [],
                "ordered_result": name != "facts_sample",
                "truncation_reason": None,
                "limits": {"row_cap": 40, "statement_timeout_ms": 5000},
                "termination_reason": "failed"
                if name == "facts_sample"
                else "completed",
                "error_code": "execution_error" if name == "facts_sample" else None,
            }
            for name, rows in beam_rows.items()
        },
    }


def test_compare_rejects_baseline_runtime_on_both_schema_heads() -> None:
    baseline = _capture(head=qualification.BASELINE_HEAD)
    target = {**baseline, "schema_head": qualification.TARGET_HEAD}
    connection = MagicMock(spec=Connection)
    with pytest.raises(AssertionError, match="runtime manifest does not match"):
        qualification.compare(connection, baseline, target)
    connection.execute.assert_not_called()


def test_compare_detects_beam_order_and_outcome_changes() -> None:
    baseline = _capture(head=qualification.BASELINE_HEAD)
    target = _capture(head=qualification.TARGET_HEAD)
    connection = MagicMock(spec=Connection)
    connection.execute.return_value.one.return_value = (0, 0)
    assert all(
        qualification.compare(connection, baseline, target)["beam_matches"].values()
    )

    target["beam"]["facts_predicates"]["rows"].reverse()
    target["beam"]["facts_sample"]["error_code"] = "statement_timeout"
    assert qualification.compare(connection, baseline, target)["beam_matches"] == {
        "facts_predicates": False,
        "facts_sample": False,
        "claims_live_sample": True,
        "facts_sample_corrected": True,
    }


def test_compare_detects_a_different_tied_predicate_at_the_beam_cutoff() -> None:
    baseline = _capture(head=qualification.BASELINE_HEAD)
    target = _capture(head=qualification.TARGET_HEAD)
    leaders = [[f"predicate-{rank}", 26 - rank] for rank in range(24)]
    for record in (baseline, target):
        record["beam"]["facts_predicates"]["rows"] = [*leaders, ["first", 2]]
    connection = MagicMock(spec=Connection)
    connection.execute.return_value.one.return_value = (0, 0)
    assert all(
        qualification.compare(connection, baseline, target)["beam_matches"].values()
    )

    target["beam"]["facts_predicates"]["rows"][24] = ["second", 2]
    assert qualification.compare(connection, baseline, target)["beam_matches"] == {
        "facts_predicates": False,
        "facts_sample": True,
        "claims_live_sample": True,
        "facts_sample_corrected": True,
    }


def test_compare_requires_fixed_instant_beam_sandbox_responses() -> None:
    baseline = _capture(head=qualification.BASELINE_HEAD)
    target = _capture(head=qualification.TARGET_HEAD)
    target.pop("beam_evaluated_at")
    connection = MagicMock(spec=Connection)
    with pytest.raises(AssertionError, match="fixed-instant BEAM sandbox responses"):
        qualification.compare(connection, baseline, target)
    connection.execute.assert_not_called()


def test_compare_detects_a_native_contradiction_total_change() -> None:
    baseline = _capture(head=qualification.BASELINE_HEAD)
    target = _capture(head=qualification.TARGET_HEAD)
    connection = MagicMock(spec=Connection)
    connection.execute.return_value.one.return_value = (0, 0)
    assert qualification.compare(connection, baseline, target)["ordered_top_k_match"]

    target["ordered"]["final/broad"][0][2]["total"] = 1
    assert not qualification.compare(connection, baseline, target)[
        "ordered_top_k_match"
    ]


def test_compare_requires_matching_sandbox_settings() -> None:
    baseline = _capture(head=qualification.BASELINE_HEAD)
    target = _capture(head=qualification.TARGET_HEAD)
    connection = MagicMock(spec=Connection)
    target["sandbox_settings"]["jit"] = "off"
    with pytest.raises(AssertionError, match="identical sandbox JIT settings"):
        qualification.compare(connection, baseline, target)
    target.pop("sandbox_settings")
    with pytest.raises(AssertionError, match="recapture both releases"):
        qualification.compare(connection, baseline, target)
    connection.execute.assert_not_called()


def test_compare_requires_native_final_confirmation_cuts() -> None:
    baseline = _capture(head=qualification.BASELINE_HEAD)
    target = _capture(head=qualification.TARGET_HEAD)
    connection = MagicMock(spec=Connection)
    for record in (baseline, target):
        record["cuts"].pop("native_final/broad")
    with pytest.raises(AssertionError, match="native final confirmation cuts"):
        qualification.compare(connection, baseline, target)
    connection.execute.assert_not_called()


def test_compare_requires_broad_sandbox_cuts() -> None:
    baseline = _capture(head=qualification.BASELINE_HEAD)
    target = _capture(head=qualification.TARGET_HEAD)
    connection = MagicMock(spec=Connection)
    for record in (baseline, target):
        record["cuts"].pop("sandbox/evidence_lineage")
    with pytest.raises(AssertionError, match="all broad sandbox cuts"):
        qualification.compare(connection, baseline, target)
    connection.execute.assert_not_called()


def test_compare_checks_broad_sandbox_completion_and_executed_work() -> None:
    baseline = _capture(head=qualification.BASELINE_HEAD)
    target = _capture(head=qualification.TARGET_HEAD)
    connection = MagicMock(spec=Connection)
    connection.execute.return_value.one.return_value = (0, 0)
    assert all(
        qualification.compare(connection, baseline, target)[
            "sandbox_completion_matches"
        ].values()
    )

    outcomes = target["sandbox"]
    outcomes["claims_live"]["work"] = {"plan": None, "error": "statement_timeout"}
    outcomes["evidence_lineage"].update(termination="failed", error="statement_timeout")
    matches = qualification.compare(connection, baseline, target)[
        "sandbox_completion_matches"
    ]
    assert {view for view, match in matches.items() if not match} == {
        "claims_live",
        "evidence_lineage",
    }


@pytest.fixture
def case() -> qualification.Case:
    return qualification.Case(
        deployment_id=uuid4(),
        embedding_model="test-model",
        vector=(1.0,),
        evaluated_at=datetime.now(UTC),
        entity_ids=(str(uuid4()),),
        batch_sizes=qualification.LATENCY_BATCH_SIZES,
    )


@pytest.mark.parametrize(
    ("stratum", "update", "message"),
    [
        ("hosted", {"batch_sizes": {"observation": 804}}, "535 relation"),
        (
            "300k",
            {"batch_sizes": {"observation": 804, "relation": 534}},
            "535 relation",
        ),
        ("3m", {"entity_ids": ()}, "scoped native read"),
        ("120-leaves", {"entity_ids": ()}, "scoped native read"),
    ],
)
def test_latency_refuses_incomplete_workload(
    case: qualification.Case, stratum: str, update: dict[str, Any], message: str
) -> None:
    engine = MagicMock(spec=Engine)
    with pytest.raises(AssertionError, match=message):
        qualification.latency(
            engine,
            case.model_copy(update=update),
            timeout=1,
            samples=40,
            explain=False,
            stratum=stratum,
        )
    engine.connect.assert_not_called()


def test_latency_refuses_a_missing_fact_gate(
    case: qualification.Case, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = qualification.Trace()
    trace.gates.append(
        ("observation", qualification.Statement(text("SELECT 1"), {}), [])
    )
    monkeypatch.setattr(
        qualification, "_record_nomination", lambda *args, **kw: (trace, [])
    )
    engine = MagicMock(spec=Engine)
    with pytest.raises(AssertionError, match="both fact kinds"):
        qualification.latency(
            engine, case, timeout=1, samples=40, explain=False, stratum="hosted"
        )
    engine.connect.assert_not_called()


@pytest.fixture
def sandbox_result(case: qualification.Case) -> QueryResult:
    return QueryResult(
        request_id=uuid4(),
        deployment_id=case.deployment_id,
        surface_manifest_hash="manifest",
        query_hash="query",
        execution_started_at=case.evaluated_at,
        elapsed_ms=0,
        limits=ResultLimits(
            row_cap=40,
            byte_cap=1_048_576,
            statement_timeout_ms=5000,
            analytical_tier=False,
        ),
    )


def test_sandbox_samples_share_pacing_across_queries(
    sandbox_result: QueryResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = 0.0

    def now() -> float:
        return clock

    def advance(seconds: float) -> None:
        nonlocal clock
        clock += seconds

    monkeypatch.setattr(qualification, "monotonic", now)
    monkeypatch.setattr(qualification, "sleep", advance)
    monkeypatch.setattr(audit, "time", SimpleNamespace(monotonic=now))
    monkeypatch.setattr(qualification, "BROAD_VIEWS", ())
    kills = audit.KillSwitches()
    limits = INTERACTIVE_LIMITS
    identity = {"deployment_id": sandbox_result.deployment_id, "principal": "agent"}

    def run(*, sql: str, max_rows: int | None) -> QueryResult:
        assert max_rows == 40
        assert (
            kills.admit(
                **identity,
                per_principal=limits.concurrent_per_principal,
                per_deployment=limits.concurrent_per_deployment,
                principal_seconds_per_minute=limits.principal_statement_seconds_per_minute,
                deployment_seconds_per_minute=limits.deployment_statement_seconds_per_minute,
            )
            is None
        ), sql
        advance(4)
        kills.record_spend(**identity, seconds=4)
        kills.release(**identity)
        return sandbox_result

    sandbox = MagicMock(spec=QuerySandboxExecutor)
    sandbox.query_sql.side_effect = run
    sampler = qualification._SandboxSampler(sandbox)
    qualification._beam_queries(sampler)
    checks = qualification._sandbox_queries(sampler, samples=4)
    assert sandbox.query_sql.call_count == 13
    assert clock > 13 * 4  # Waiting happens outside the measured samples.
    for state in checks.values():
        assert state["samples"] == 4
        assert state["p50_s"] == state["p95_s"] == 4
        assert state["admission_failures"] == {}


def test_sandbox_admission_failure_is_not_a_timed_sample(
    sandbox_result: QueryResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qualification, "BROAD_VIEWS", ())
    sampler = MagicMock(spec=qualification._SandboxSampler)
    rejection = sandbox_result.model_copy(
        update={
            "termination_reason": "rejected",
            "error_code": QueryErrorCode.QUOTA_EXCEEDED,
        }
    )
    sampler.run.side_effect = [
        (sandbox_result, 1),  # warm-up
        (sandbox_result, 2),
        (rejection, 0.001),
        (rejection, 0.001),  # the next query's warm-up
    ]
    checks = qualification._sandbox_queries(sampler, samples=3)
    predicates = checks["beam_predicates"]
    assert predicates["samples"] == 1
    assert predicates["p50_s"] == predicates["p95_s"] == 2
    assert predicates["admission_failures"] == {"quota_exceeded": 1}
    assert predicates["termination"] == "rejected"
    sample = checks["beam_sample_corrected"]
    assert sample["samples"] == 0 and "p95_s" not in sample
    assert sample["admission_failures"] == {"quota_exceeded": 1}
