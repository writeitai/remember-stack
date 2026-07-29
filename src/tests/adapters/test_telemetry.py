"""Provider-neutral and self-host telemetry adapter contracts."""

from datetime import datetime
from datetime import UTC
from io import StringIO
import json
from typing import cast
from uuid import UUID

from rememberstack.adapters.selfhost import FanoutTelemetry
from rememberstack.adapters.selfhost import JsonLineTelemetry
from rememberstack.model import ClaimedWork
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import RunResultOutcome
from rememberstack.model import TelemetryAttribute
from rememberstack.model import TelemetryEvent
from rememberstack.spine import WorkLedger
from rememberstack.workers import HandlerOutcome
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import RunResult
from rememberstack.workers import Worker

_DEPLOYMENT_ID = UUID("74000000-0000-0000-0000-000000000001")
_PROCESSING_ID = UUID("74000000-0000-0000-0000-000000000002")


def _event() -> TelemetryEvent:
    return TelemetryEvent(
        name="worker.run",
        occurred_at=datetime(2026, 7, 21, tzinfo=UTC),
        attributes=(TelemetryAttribute(name="outcome", value="dead_lettered"),),
    )


def test_json_lines_preserves_exception_cause_chain() -> None:
    """The local exporter keeps structured data and both chained exceptions."""
    stream = StringIO()
    telemetry = JsonLineTelemetry(stream=stream)
    try:
        try:
            raise KeyError("root cause")
        except KeyError as cause:
            raise ValueError("outer failure") from cause
    except ValueError as error:
        telemetry.export_exception(event=_event(), exception=error)

    payload = json.loads(stream.getvalue())
    assert payload["name"] == "worker.run"
    assert payload["exception"]["type"] == "ValueError"
    assert "KeyError: 'root cause'" in payload["exception"]["traceback"]
    assert "ValueError: outer failure" in payload["exception"]["traceback"]


def test_json_lines_flushes_one_event_per_line() -> None:
    """Ordinary event output is valid compact JSON with one trailing newline."""
    stream = StringIO()
    JsonLineTelemetry(stream=stream).export_event(event=_event())
    assert stream.getvalue().count("\n") == 1
    assert json.loads(stream.getvalue())["attributes"][0] == {
        "name": "outcome",
        "value": "dead_lettered",
    }


class _MemoryLedger:
    """Small row-backed ledger double for the worker's committed failure path."""

    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = [
            {
                "processing_id": _PROCESSING_ID,
                "status": "pending",
                "attempts": 0,
                "last_error": None,
            }
        ]
        self._claimed = False

    def claim_one(self, **_: object) -> ClaimedWork | None:
        if self._claimed:
            return None
        self._claimed = True
        self.rows[0]["status"] = "running"
        self.rows[0]["attempts"] = 1
        return ClaimedWork(
            processing_id=_PROCESSING_ID,
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.DOCUMENT,
            target_id=UUID("74000000-0000-0000-0000-000000000003"),
            stage=PipelineStage.CONVERT,
            component_version="convert-test",
            content_hash="content-test",
            lane=ProcessingLane.STEADY,
            attempt=1,
            payload=None,
        )

    def fail(
        self, *, processing_id: UUID, error: str, retryable: bool
    ) -> datetime | None:
        assert processing_id == _PROCESSING_ID
        assert retryable is False
        self.rows[0]["status"] = "dead_letter"
        self.rows[0]["last_error"] = error
        return None


class _PermanentFailure:
    def handle(self, *, work: ClaimedWork, meter: object) -> HandlerOutcome:
        del work, meter
        raise NonRetryableHandlerError("authoritative handler failure")


class _RaisingTelemetry:
    def export_event(self, **_: object) -> None:
        raise RuntimeError("optional sink unavailable")

    def export_exception(self, **_: object) -> None:
        raise RuntimeError("optional sink unavailable")


def test_worker_result_and_ledger_survive_raising_optional_fanout_sink() -> None:
    """A second sink cannot alter the result after the authoritative row commit."""
    ledger = _MemoryLedger()
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.CONVERT, handler=_PermanentFailure())
    stream = StringIO()
    telemetry = FanoutTelemetry(
        sinks=(JsonLineTelemetry(stream=stream), _RaisingTelemetry())
    )

    result = Worker(
        ledger=cast(WorkLedger, ledger), registry=registry, telemetry=telemetry
    ).run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.CONVERT,
        lane=ProcessingLane.STEADY,
    )

    assert result == RunResult(
        processing_id=_PROCESSING_ID, outcome=RunResultOutcome.DEAD_LETTERED
    )
    ledger_row = ledger.rows[0]
    assert ledger_row["processing_id"] == _PROCESSING_ID
    assert ledger_row["status"] == "dead_letter"
    assert ledger_row["attempts"] == 1
    assert "NonRetryableHandlerError: authoritative handler failure" in str(
        ledger_row["last_error"]
    )
    local_row = json.loads(stream.getvalue())
    assert local_row["attributes"][5] == {"name": "outcome", "value": "dead_lettered"}
    assert local_row["exception"]["message"] == "authoritative handler failure"
