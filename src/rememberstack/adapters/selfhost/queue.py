"""The self-host delivery shell: LISTEN/NOTIFY wake-ups + SKIP LOCKED claiming (D62/D67).

Work is Postgres rows; this shell only delivers wake-ups. The schema-owned
insert trigger provides the transactional initial wake; `announce` re-announces
existing rows through the spine's notification primitive; the worker loop
sleeps on LISTEN with a slow fallback poll and claims only its configured
route, consulting a token bucket around each claim.
"""

from collections.abc import Callable
import logging
import time
from uuid import UUID

import psycopg

from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import QueueRoute
from rememberstack.model import RunResultOutcome
from rememberstack.model import UTCDateTime
from rememberstack.spine.work_ledger import WorkLedger
from rememberstack.workers import RunResult
from rememberstack.workers import Worker

_logger = logging.getLogger(__name__)

_WAKE_CHANNEL = "queue_wake"


class SelfHostTaskQueue:
    """The delivery-only announce adapter: wake an existing committed row.

    Satisfies the task-queue port. Route and due-time snapshots are accepted
    per the port contract but carry no state — the woken worker re-reads
    Postgres, which is the only authority (D67).
    """

    def __init__(self, *, ledger: WorkLedger) -> None:
        """Bind the adapter to the spine's notification primitive."""
        self._ledger = ledger

    def announce(
        self,
        *,
        processing_id: UUID,
        route_snapshot: QueueRoute,
        not_before_snapshot: UTCDateTime,
    ) -> None:
        """Schedule at-least-once delivery by waking listeners for the row."""
        del route_snapshot, not_before_snapshot  # snapshots are hints, never state
        self._ledger.wake(processing_id=processing_id)


class TokenBucket:
    """A per-route claim rate limit: refill at a fixed rate up to a capacity."""

    def __init__(
        self,
        *,
        rate_per_s: float,
        capacity: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Start full; `clock` is injectable for deterministic tests."""
        self._rate_per_s = rate_per_s
        self._capacity = capacity
        self._clock = clock
        self._tokens = capacity
        self._refilled_at = clock()

    def try_acquire(self) -> bool:
        """Take one token if available; never blocks."""
        now = self._clock()
        self._tokens = min(
            self._capacity, self._tokens + (now - self._refilled_at) * self._rate_per_s
        )
        self._refilled_at = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True


class SelfHostWorkerLoop:
    """One route's worker loop: sleep on LISTEN, drain due work, fall back to polling."""

    def __init__(
        self,
        *,
        worker: Worker,
        deployment_id: UUID,
        stage: PipelineStage,
        lane: ProcessingLane | None,
        bucket: TokenBucket,
        database_url: str,
        fallback_poll_s: float = 30.0,
        serve_backfill: bool = False,
    ) -> None:
        """Bind the loop to its route, rate limit, and LISTEN connection source.

        ``serve_backfill`` lets a steady-route loop also claim its stage's
        backfill route whenever the steady route has no due row (D143 §5.5),
        so priority stays structural: steady first, every time.
        """
        self._worker = worker
        self._deployment_id = deployment_id
        self._stage = stage
        self._lane = lane
        self._bucket = bucket
        self._database_url = database_url
        self._fallback_poll_s = fallback_poll_s
        self._serve_backfill = serve_backfill and lane is ProcessingLane.STEADY

    def drain_due(self) -> tuple[RunResult, ...]:
        """Claim and run due work on the route until none remains or tokens run out."""
        results: list[RunResult] = []
        while self._bucket.try_acquire():
            result = self._worker.run_one(
                deployment_id=self._deployment_id, stage=self._stage, lane=self._lane
            )
            if result.outcome is RunResultOutcome.NO_WORK and self._serve_backfill:
                result = self._worker.run_one(
                    deployment_id=self._deployment_id,
                    stage=self._stage,
                    lane=ProcessingLane.BACKFILL,
                )
            if result.outcome is RunResultOutcome.NO_WORK:
                break
            results.append(result)
        return tuple(results)

    def run_for(self, *, duration_s: float) -> tuple[RunResult, ...]:
        """Listen, drain on every wake, and fall back to polling, for a bounded time.

        The production loop is this method called with a large duration under a
        supervisor; tests call it with a short one. Missed notifications are
        covered by the fallback poll, per the packaging design.
        """
        results: list[RunResult] = list(self.drain_due())
        deadline = time.monotonic() + duration_s
        with psycopg.connect(self._database_url, autocommit=True) as connection:
            connection.execute(f"LISTEN {_WAKE_CHANNEL}")
            while (remaining := deadline - time.monotonic()) > 0:
                timeout = min(remaining, self._fallback_poll_s)
                woken = list(connection.notifies(timeout=timeout, stop_after=1))
                if woken:
                    _logger.debug("wake received: %s", woken[0].payload)
                results.extend(self.drain_due())
        return tuple(results)
