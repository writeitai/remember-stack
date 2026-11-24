"""D158 §2.1 worker boundaries: the code and level each failure logs.

A retry that will run again is a WARNING; the last attempt, a non-retryable
failure and a dead-lettered lease are the item's one ERROR; a failed requeue
wake is logged and the reaper sweep continues.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
from typing import Any
from typing import cast
from uuid import uuid4

import pytest

from rememberstack.model import ClaimedWork
from rememberstack.model import ExpiredLease
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.model import ProviderCallError
from rememberstack.model import RunResultOutcome
from rememberstack.model import WorkNotRunningError
from rememberstack.spine.work_ledger import WorkLedger
from rememberstack.workers.base import HandlerRegistry
from rememberstack.workers.base import Worker

DEPLOYMENT = uuid4()


def _work(*, attempt: int = 1) -> ClaimedWork:
    """One claimed structure attempt."""
    return ClaimedWork(
        processing_id=uuid4(),
        deployment_id=DEPLOYMENT,
        target_kind=ProcessingTarget.DOCUMENT_VERSION,
        target_id=uuid4(),
        stage=PipelineStage.STRUCTURE,
        component_version="test",
        content_hash="hash",
        lane=None,
        attempt=attempt,
        payload=None,
    )


class _Ledger:
    """The ledger calls a failing attempt and the reaper make."""

    lease_renewal_interval_s = 3600.0

    def __init__(
        self,
        *,
        retry_at: datetime | None = None,
        expired: tuple[ExpiredLease, ...] = (),
    ) -> None:
        """Answer ``fail`` with ``retry_at`` and the reaper with ``expired``."""
        self.retry_at = retry_at
        self.expired = expired

    lost = False

    def fail(self, **_: object) -> datetime | None:
        """Schedule a retry, dead-letter when ``retry_at`` is None, or refuse."""
        if self.lost:
            raise WorkNotRunningError("the reaper took the claim back")
        return self.retry_at

    def reap_expired_leases(self, **_: object) -> tuple[ExpiredLease, ...]:
        """Return the configured expired leases."""
        return self.expired


class _Failing:
    """A stage handler that raises the given exception."""

    def __init__(self, error: Exception) -> None:
        """Keep the exception to raise."""
        self.error = error

    finalized = 0

    def handle(self, **_: object) -> Any:
        """Fail."""
        raise self.error

    def finalize_terminal_failure(self, **_: object) -> None:
        """Count terminal finalizations."""
        self.finalized += 1


class _Queue:
    """A queue whose first wake fails."""

    def __init__(self) -> None:
        """Record the announced processing ids."""
        self.announced: list[object] = []

    def announce(self, *, processing_id: object, **_: object) -> None:
        """Fail the first announcement, accept the rest."""
        if not self.announced:
            self.announced.append(None)
            raise ConnectionError("queue unavailable")
        self.announced.append(processing_id)


def _worker(
    *, ledger: _Ledger, handler: object = None, queue: _Queue | None = None
) -> Worker:
    """A worker over the fakes, for the structure stage."""
    registry = HandlerRegistry()
    registry.register(
        stage=PipelineStage.STRUCTURE,
        handler=handler or _Failing(RuntimeError("unused")),  # type: ignore[arg-type]
    )
    return Worker(
        ledger=cast(WorkLedger, ledger),
        registry=registry,
        queue=queue,  # type: ignore[arg-type]
    )


def _coded(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    """(level, error_code) of every engine WARNING-or-above record.

    An uncoded record appears with code ``"-"``, so a duplicate fails.
    """
    return [
        (record.levelname, record.__dict__.get("error_code", "-"))
        for record in caplog.records
        if record.name.startswith("rememberstack") and record.levelno >= 30
    ]


@pytest.mark.parametrize(
    ("error", "retry_at", "outcome", "coded"),
    [
        (
            RuntimeError("transient"),
            datetime.now(UTC),
            RunResultOutcome.RETRY_SCHEDULED,
            ("WARNING", "worker_item_retry"),
        ),
        (
            RuntimeError("transient"),
            None,
            RunResultOutcome.DEAD_LETTERED,
            ("ERROR", "worker_item_failed"),
        ),
        (
            NonRetryableHandlerError("bad input"),
            None,
            RunResultOutcome.DEAD_LETTERED,
            ("ERROR", "worker_item_failed"),
        ),
    ],
)
def test_item_failure_logs_retry_as_warning_and_last_attempt_as_error(
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    retry_at: datetime | None,
    outcome: RunResultOutcome,
    coded: tuple[str, str],
) -> None:
    """Exactly one coded record per failed attempt, at the D158 level."""
    handler = _Failing(error)
    worker = _worker(ledger=_Ledger(retry_at=retry_at), handler=handler)
    with caplog.at_level("WARNING", logger="rememberstack"):
        result = worker.run_claimed(claimed=_work(), started_ns=0)
    assert result.outcome is outcome
    assert _coded(caplog) == [coded]
    assert handler.finalized == (outcome is RunResultOutcome.DEAD_LETTERED)
    record = next(r for r in caplog.records if "error_code" in r.__dict__)
    assert record.exc_info is not None and "processing_id" in record.__dict__


@pytest.mark.parametrize(
    "error", [RuntimeError("timeout"), NonRetryableHandlerError("bad input")]
)
def test_a_failure_after_losing_the_lease_is_one_lease_lost_warning(
    caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    """The reaper owns the outcome: one WARNING keeps the failure, no ERROR."""
    ledger = _Ledger(retry_at=datetime.now(UTC))
    ledger.lost = True
    worker = _worker(ledger=ledger, handler=_Failing(error))
    with caplog.at_level("WARNING", logger="rememberstack"):
        result = worker.run_claimed(claimed=_work(), started_ns=0)
    assert result.outcome is RunResultOutcome.LEASE_LOST
    assert _coded(caplog) == [("WARNING", "worker_lease_lost")]
    record = caplog.records[0]
    assert record.exc_info is not None and record.exc_info[1] is error
    assert record.__dict__["exception_type"] == type(error).__name__


def test_provider_failures_log_their_type_without_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Rule 4: provider text may echo the document, so no ``exc_info``."""
    error = ProviderCallError("upstream echoed: SECRET DOCUMENT TEXT")
    worker = _worker(ledger=_Ledger(retry_at=None), handler=_Failing(error))
    with caplog.at_level("WARNING", logger="rememberstack"):
        worker.run_claimed(claimed=_work(), started_ns=0)
    assert _coded(caplog) == [("ERROR", "worker_item_failed")]
    record = caplog.records[0]
    assert record.exc_info is None
    assert record.__dict__["exception_type"] == "ProviderCallError"
    assert "SECRET" not in caplog.text


def test_reaper_codes_each_lease_and_survives_a_failed_wake(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Requeued is a WARNING, dead-lettered an ERROR; a failed wake continues."""
    requeued = [ExpiredLease(work=_work(), requeued=True) for _ in range(2)]
    dead = ExpiredLease(work=_work(attempt=3), requeued=False)
    queue = _Queue()
    worker = _worker(ledger=_Ledger(expired=(*requeued, dead)), queue=queue)
    with caplog.at_level("WARNING", logger="rememberstack"):
        assert len(worker.reap_expired_leases(deployment_id=DEPLOYMENT)) == 3
    assert queue.announced == [None, requeued[1].work.processing_id]
    assert _coded(caplog) == [
        ("WARNING", "worker_lease_expired_retry"),
        ("ERROR", "worker_requeue_announce_failed"),
        ("WARNING", "worker_lease_expired_retry"),
        ("ERROR", "worker_lease_expired"),
    ]
