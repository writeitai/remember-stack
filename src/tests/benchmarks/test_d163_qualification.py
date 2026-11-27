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
        "case": {},
        "cuts": {},
        "raw": {},
        "ordered": {},
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
