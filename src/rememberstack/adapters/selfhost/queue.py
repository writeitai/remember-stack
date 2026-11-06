"""The self-host delivery shell: LISTEN/NOTIFY wake-ups + SKIP LOCKED claiming (D62/D67).

Work is Postgres rows; this shell only delivers wake-ups. The schema-owned
insert trigger provides the transactional initial wake; `announce` re-announces
existing rows through the spine's notification primitive; the worker loop
sleeps on LISTEN with a slow fallback poll and claims only the routes of its
worker class, consulting a token bucket around each claim.
"""

from collections.abc import Callable
import logging
import threading
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
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        """Take one token if available; never blocks. Safe across threads."""
        with self._lock:
            now = self._clock()
            self._tokens = min(
                self._capacity,
                self._tokens + (now - self._refilled_at) * self._rate_per_s,
            )
            self._refilled_at = now
            if self._tokens < 1.0:
                return False
            self._tokens -= 1.0
            return True


class SelfHostWorkerLoop:
    """One worker class's loop: concurrent slots drain due work across its stages.

    The loop serves every route of its stages, steady lanes before backfill
    lanes, and within a lane the stages in the order given (callers list
    downstream stages first, so work already in the pipeline drains before new
    uploads take the slots). ``concurrency`` slots each run one task at a time
    in their own thread. The calling thread sleeps on LISTEN with a slow
    fallback poll; every wake-up, and every successful claim, wakes one
    idle slot, so idle slots look for work one at a time instead of all at
    once. The calling thread also sweeps expired claims (D144) at most once
    per fallback-poll interval.
    """

    def __init__(
        self,
        *,
        worker: Worker,
        ledger: WorkLedger,
        deployment_id: UUID,
        stages: tuple[PipelineStage, ...],
        bucket: TokenBucket,
        database_url: str,
        fallback_poll_s: float = 30.0,
        concurrency: int = 1,
    ) -> None:
        """Bind the loop to its stages, rate limit, slots and LISTEN connection."""
        if concurrency < 1:
            raise ValueError("a worker loop needs at least one slot")
        self._worker = worker
        self._ledger = ledger
        self._deployment_id = deployment_id
        self._routes = tuple(
            (stage, lane)
            for lane in (ProcessingLane.STEADY, ProcessingLane.BACKFILL)
            for stage in stages
        )
        self._bucket = bucket
        self._database_url = database_url
        self._fallback_poll_s = fallback_poll_s
        self._concurrency = concurrency
        self._reaped_at = float("-inf")
        self._wake = threading.Condition()
        self._wake_pending = False

    def drain_due(self) -> tuple[RunResult, ...]:
        """Run due work in the calling thread until none remains or tokens run out."""
        results: list[RunResult] = []
        while (result := self._run_next()) is not None:
            results.append(result)
        return tuple(results)

    def run_for(self, *, duration_s: float) -> tuple[RunResult, ...]:
        """Listen and run the slots for a bounded time, then let in-flight tasks finish.

        The production loop is this method called with a large duration under a
        supervisor; tests call it with a short one. Missed notifications are
        covered by the fallback poll, per the packaging design. A slot that
        raises stops the loop, and the error is re-raised here.
        """
        stop = threading.Event()
        results: list[RunResult] = []
        errors: list[BaseException] = []
        slots = [
            threading.Thread(
                target=self._slot,
                kwargs={"stop": stop, "results": results, "errors": errors},
                name=f"worker-slot-{index}",
                daemon=True,
            )
            for index in range(self._concurrency)
        ]
        started: list[threading.Thread] = []
        deadline = time.monotonic() + duration_s
        try:
            with psycopg.connect(self._database_url, autocommit=True) as connection:
                connection.execute(f"LISTEN {_WAKE_CHANNEL}")
                for slot in slots:
                    slot.start()
                    started.append(slot)
                self._reap_if_due()
                self._signal()
                while not stop.is_set() and (
                    (remaining := deadline - time.monotonic()) > 0
                ):
                    timeout = min(remaining, self._fallback_poll_s)
                    woken = list(connection.notifies(timeout=timeout, stop_after=1))
                    if woken:
                        _logger.debug("wake received: %s", woken[0].payload)
                    self._reap_if_due()
                    self._signal()
        finally:
            stop.set()
            with self._wake:
                self._wake.notify_all()
            for slot in started:
                slot.join()
        if errors:
            raise errors[0]
        return tuple(results)

    def _slot(
        self,
        *,
        stop: threading.Event,
        results: list[RunResult],
        errors: list[BaseException],
    ) -> None:
        """Run due work until stopped; sleep until signalled when none is due."""
        try:
            while not stop.is_set():
                result = self._run_next()
                if result is not None:
                    results.append(result)
                    continue
                with self._wake:
                    while not self._wake_pending and not stop.is_set():
                        self._wake.wait()
                    self._wake_pending = False
        except BaseException as error:  # re-raised by run_for
            errors.append(error)
            stop.set()
            with self._wake:
                self._wake.notify_all()

    def _run_next(self) -> RunResult | None:
        """Claim and run the first due unit of work in route order, if any."""
        if not self._bucket.try_acquire():
            return None
        for stage, lane in self._ledger.due_routes(
            deployment_id=self._deployment_id, routes=self._routes
        ):
            result = self._worker.run_one(
                deployment_id=self._deployment_id,
                stage=stage,
                lane=lane,
                # A claim succeeded, so more may be due: let one idle slot
                # look too. Only real claims wake slots, so a route the probe
                # lists but nobody can claim never keeps slots spinning.
                on_claimed=self._signal,
            )
            if result.outcome is not RunResultOutcome.NO_WORK:
                return result
        return None

    def _signal(self) -> None:
        """Wake one idle slot (or the next slot to go idle) to look for work."""
        with self._wake:
            self._wake_pending = True
            self._wake.notify()

    def _reap_if_due(self) -> None:
        """Sweep expired claims when a fallback-poll interval has passed."""
        if time.monotonic() - self._reaped_at >= self._fallback_poll_s:
            self._reaped_at = time.monotonic()
            self._worker.reap_expired_leases(deployment_id=self._deployment_id)
