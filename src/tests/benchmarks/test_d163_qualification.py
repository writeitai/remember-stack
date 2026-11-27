"""Fail closed when run identity, BEAM results or the latency workload drifts."""

from copy import deepcopy
from datetime import datetime
from datetime import UTC
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from benchmarks import d163_qualification as qualification
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine


def _capture(*, head: str) -> dict[str, Any]:
    return {
        "schema_head": head,
        "surface_manifest_hash": qualification.MANIFEST_BY_HEAD[head],
        "sandbox_settings": {"jit": "on"},
        "case": {},
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
        "beam": {
            "facts_predicates": {
                "rows": [["first", 2], ["second", 2]],
                "termination_reason": "completed",
                "error_code": None,
            },
            "facts_sample": {
                "rows": [],
                "termination_reason": "failed",
                "error_code": "execution_error",
            },
            "claims_live_sample": {
                "rows": [],
                "termination_reason": "completed",
                "error_code": None,
            },
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

    target = deepcopy(target)
    target["beam"]["facts_predicates"]["rows"].reverse()
    target["beam"]["facts_sample"]["error_code"] = "statement_timeout"
    assert qualification.compare(connection, baseline, target)["beam_matches"] == {
        "facts_predicates": False,
        "facts_sample": False,
        "claims_live_sample": True,
    }


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
