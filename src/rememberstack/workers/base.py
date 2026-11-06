"""The handler-registration model and the worker runner (WP-0.3, D12/D52/D67).

A stage handler transforms one claimed unit of work and names its chain
follow-ups; the runner is the single exception boundary (core value 6): a
handler failure is logged with its full traceback, recorded on the row, and
either retried with backoff or dead-lettered — it never disappears.
"""

from collections.abc import Callable
from datetime import datetime
from datetime import UTC
import logging
import threading
import time
import traceback
from typing import Protocol
from typing import runtime_checkable
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict

from rememberstack.model import BudgetParked
from rememberstack.model import ClaimedWork
from rememberstack.model import EnqueueWork
from rememberstack.model import ExpiredLease
from rememberstack.model import HandlerAlreadyRegisteredError
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import QueueRoute
from rememberstack.model import RecordCall
from rememberstack.model import RunResultOutcome
from rememberstack.model import TelemetryAttribute
from rememberstack.model import TelemetryEvent
from rememberstack.model import UnknownStageHandlerError
from rememberstack.model import WorkNotRunningError
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.queue import TaskQueuePort
from rememberstack.ports.telemetry import TelemetryPort
from rememberstack.spine.work_ledger import WorkLedger

_logger = logging.getLogger(__name__)


class NoRouteHandlerError(Exception):
    """Convert found no configured route before any I/O or provider work (D117)."""

    def __init__(self, message: str, *, mime: str) -> None:
        """Keep the stored MIME the handler found unroutable."""
        super().__init__(message)
        self.mime = mime


class ExtractChunkBarrier(BaseModel):
    """D84: after a chunk extract succeeds, complete+barrier in one ledger txn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    version_id: UUID
    representation_id: UUID
    chunker_version: str
    extractor_version: str
    content_hash: str
    lane: ProcessingLane | None
    normalize_component_version: str


class SelectionChunkBarrier(ExtractChunkBarrier):
    """Complete Selection before any chunk consumes the shared source references."""


class ClaimNormalizeBarrier(BaseModel):
    """D88: after a claim normalize succeeds, complete+barrier in one ledger txn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    version_id: UUID
    representation_id: UUID
    doc_id: UUID
    chunker_version: str
    extractor_version: str
    content_hash: str
    lane: ProcessingLane | None
    normalize_component_version: str
    obs_flush_component_version: str


class EntityObsFlushBarrier(BaseModel):
    """D90: after an entity obs-flush unit succeeds, complete+barrier in one txn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    version_id: UUID
    representation_id: UUID
    unit_id: UUID
    subject_entity_id: UUID
    normalizer_version: str
    chunker_version: str
    extractor_version: str
    content_hash: str
    lane: ProcessingLane | None
    obs_flush_component_version: str
    doc_id: UUID | None = None


class EmptyObsFlushComplete(BaseModel):
    """D90: true-empty observation set — durable empty_complete + super/embed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    version_id: UUID
    representation_id: UUID
    doc_id: UUID | None
    normalizer_version: str
    chunker_version: str
    extractor_version: str
    content_hash: str
    lane: ProcessingLane | None


class ClaimsBoundary(BaseModel):
    """D143: ``embed_chunk`` finished; the ledger decides the claims layer.

    The handler still names its ordinary follow-ups (the per-chunk extract jobs
    or the D90 empty path); an unconfigured deployment runs exactly those, and
    a configured one records a gap and lets the lineage order decide.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    version_id: UUID
    lane: ProcessingLane | None


class HandlerOutcome(BaseModel):
    """What a successful handler produced: the chain follow-ups to enqueue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    follow_up: tuple[EnqueueWork, ...] = ()
    selection_chunk_barrier: SelectionChunkBarrier | None = None
    extract_chunk_barrier: ExtractChunkBarrier | None = None
    claim_normalize_barrier: ClaimNormalizeBarrier | None = None
    entity_obs_flush_barrier: EntityObsFlushBarrier | None = None
    empty_obs_flush: EmptyObsFlushComplete | None = None
    claims_boundary: ClaimsBoundary | None = None


@runtime_checkable
class TerminalFailureFinalizer(Protocol):
    """Optional handler capability: finalize domain state on exhausted retries.

    A retryable failure that dead-letters on its final attempt ends the work,
    but the stage's domain row (e.g. a document version left `converting`)
    would otherwise keep claiming in-flight progress forever. A handler that
    implements this gets one call to make that state truthful.
    """

    def finalize_terminal_failure(self, *, work: ClaimedWork, error: str) -> None:
        """Record the terminal failure on the stage's domain state."""
        ...


@runtime_checkable
class StageHandler(Protocol):
    """One stage's transformation over one claimed unit of work."""

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Process the claimed work and return its chain follow-ups.

        Raise `NonRetryableHandlerError` for permanent failures (the work
        dead-letters immediately); any other exception is a retryable failure.
        """
        ...


class _LedgerCostMeter:
    """Bind provider accounting to one claimed processing attempt."""

    def __init__(self, *, ledger: WorkLedger, work: ClaimedWork) -> None:
        """Bind every call record to the claimed attempt of its row."""
        self._ledger = ledger
        self._work = work

    def record(
        self,
        *,
        call_key: str,
        tier: str | None,
        usage: ProviderCallUsage,
        outcome: str = "ok",
    ) -> None:
        """Persist one provider-reported call through the spine attribution path."""
        self._ledger.record_call(
            call=RecordCall(
                processing_id=self._work.processing_id,
                attempt=self._work.attempt,
                call_key=call_key,
                model_name=usage.model_name,
                tier=tier,
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
                cost_usd=usage.cost_usd,
                latency_ms=usage.latency_ms,
                outcome=outcome,
            )
        )


class _LeaseRenewal:
    """Renew one claimed attempt's lease from a background thread while it runs.

    A failed renewal is logged and retried at the next interval; a refused one
    (the reaper already took the row back) stops renewing, and the attempt's
    complete or fail is refused later for the same reason.
    """

    def __init__(self, *, ledger: WorkLedger, work: ClaimedWork) -> None:
        """Bind the renewal to one attempt; nothing runs until entered."""
        self._ledger = ledger
        self._work = work
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._renew, name=f"lease-{work.processing_id}", daemon=True
        )

    def __enter__(self) -> None:
        """Start renewing."""
        self._thread.start()

    def __exit__(self, *_: object) -> None:
        """Stop renewing and wait for an in-progress renewal to finish."""
        self._stopped.set()
        self._thread.join()

    def _renew(self) -> None:
        while not self._stopped.wait(self._ledger.lease_renewal_interval_s):
            try:
                renewed = self._ledger.renew_lease(
                    processing_id=self._work.processing_id, attempt=self._work.attempt
                )
            except Exception:  # noqa: BLE001 — retry at the next interval
                _logger.exception(
                    "lease renewal failed for %s attempt %s",
                    self._work.processing_id,
                    self._work.attempt,
                )
                continue
            if not renewed:
                _logger.warning(
                    "lease lost for %s attempt %s in stage %s",
                    self._work.processing_id,
                    self._work.attempt,
                    self._work.stage,
                )
                return


class RunResult(BaseModel):
    """What one runner pass did: which row it ran and how the attempt ended."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    processing_id: UUID | None
    outcome: RunResultOutcome


class HandlerRegistry:
    """Exactly one handler per stage; registration conflicts are typed errors."""

    def __init__(self) -> None:
        """Start empty; stages register at composition time (profiles own wiring)."""
        self._handlers: dict[PipelineStage, StageHandler] = {}

    def register(self, *, stage: PipelineStage, handler: StageHandler) -> None:
        """Bind a handler to a stage; a second binding for the stage is an error."""
        if stage in self._handlers:
            raise HandlerAlreadyRegisteredError(
                f"stage {stage} already has a registered handler"
            )
        self._handlers[stage] = handler

    @property
    def stages(self) -> tuple[PipelineStage, ...]:
        """The stages with a registered handler, in registration order."""
        return tuple(self._handlers)

    def handler_for(self, *, stage: PipelineStage) -> StageHandler:
        """Return the stage's handler; an unregistered stage is a typed error."""
        handler = self._handlers.get(stage)
        if handler is None:
            raise UnknownStageHandlerError(f"no handler registered for stage {stage}")
        return handler


class Worker:
    """Claims due work on one route and runs its handler behind one error boundary."""

    def __init__(
        self,
        *,
        ledger: WorkLedger,
        registry: HandlerRegistry,
        queue: TaskQueuePort | None = None,
        telemetry: TelemetryPort | None = None,
    ) -> None:
        """Bind the runner to its ledger, handler registry, and optional queue port.

        When a queue port is provided, scheduled retries are re-announced
        through it (packaging §3: retry paths call the port in both profiles).
        """
        self._ledger = ledger
        self._registry = registry
        self._queue = queue
        self._telemetry = telemetry

    def run_one(
        self,
        *,
        deployment_id: UUID,
        stage: PipelineStage,
        lane: ProcessingLane | None,
        processing_id: UUID | None = None,
        on_claimed: Callable[[], None] | None = None,
    ) -> RunResult:
        """Claim and run at most one due unit of work on the route.

        ``processing_id`` restricts the claim to that one row (D147).
        ``on_claimed`` is called after a claim succeeds and before the handler
        runs (a concurrent worker loop uses it to wake another slot).
        """
        started_ns = time.monotonic_ns()
        self._registry.handler_for(stage=stage)  # before any claim:
        # an unregistered stage must never strand a claimed row as running.
        claimed = self._ledger.claim_one(
            deployment_id=deployment_id,
            stage=stage,
            lane=lane,
            processing_id=processing_id,
        )
        if claimed is None:
            return RunResult(processing_id=None, outcome=RunResultOutcome.NO_WORK)
        if isinstance(claimed, BudgetParked):
            if self._queue is not None:
                self._queue.announce(
                    processing_id=claimed.processing_id,
                    route_snapshot=QueueRoute(
                        deployment_id=deployment_id, stage=stage, lane=lane
                    ),
                    not_before_snapshot=claimed.resume_at,
                )
            self._export_event(
                event=_worker_event(
                    deployment_id=deployment_id,
                    processing_id=claimed.processing_id,
                    stage=stage,
                    lane=lane,
                    attempt=None,
                    outcome=RunResultOutcome.BUDGET_PARKED,
                    started_ns=started_ns,
                )
            )
            return RunResult(
                processing_id=claimed.processing_id,
                outcome=RunResultOutcome.BUDGET_PARKED,
            )
        if on_claimed is not None:
            on_claimed()
        return self.run_claimed(claimed=claimed, started_ns=started_ns)

    def run_claimed(self, *, claimed: ClaimedWork, started_ns: int) -> RunResult:
        """Run one claimed attempt and record how it ended.

        This is the worker's single catch-to-log boundary (core value 6): the
        full traceback is logged and recorded on the row; a
        `NonRetryableHandlerError` dead-letters immediately, anything else
        retries with backoff until the attempt limit dead-letters it.
        """
        handler = self._registry.handler_for(stage=claimed.stage)
        meter = _LedgerCostMeter(ledger=self._ledger, work=claimed)
        try:
            with _LeaseRenewal(ledger=self._ledger, work=claimed):
                outcome = handler.handle(work=claimed, meter=meter)
        except NoRouteHandlerError as no_route:
            try:
                self._ledger.park_no_route(
                    processing_id=claimed.processing_id,
                    attempt=claimed.attempt,
                    mime=no_route.mime,
                )
            except WorkNotRunningError:
                return self._lease_lost(claimed=claimed, started_ns=started_ns)
            self._export_event(
                event=_worker_event(
                    deployment_id=claimed.deployment_id,
                    processing_id=claimed.processing_id,
                    stage=claimed.stage,
                    lane=claimed.lane,
                    attempt=None,
                    outcome=RunResultOutcome.NO_ROUTE_PARKED,
                    started_ns=started_ns,
                )
            )
            return RunResult(
                processing_id=claimed.processing_id,
                outcome=RunResultOutcome.NO_ROUTE_PARKED,
            )
        except (NonRetryableHandlerError, ProviderAccountingError) as exception:
            _logger.exception(
                "non-retryable failure in stage %s for %s",
                claimed.stage,
                claimed.processing_id,
            )
            try:
                self._ledger.fail(
                    processing_id=claimed.processing_id,
                    attempt=claimed.attempt,
                    error=traceback.format_exc(),
                    retryable=False,
                )
            except WorkNotRunningError:
                return self._lease_lost(claimed=claimed, started_ns=started_ns)
            self._finalize_terminal_failure(
                handler=handler, claimed=claimed, exception=exception
            )
            self._export_exception(
                event=_worker_event(
                    deployment_id=claimed.deployment_id,
                    processing_id=claimed.processing_id,
                    stage=claimed.stage,
                    lane=claimed.lane,
                    attempt=claimed.attempt,
                    outcome=RunResultOutcome.DEAD_LETTERED,
                    started_ns=started_ns,
                ),
                exception=exception,
            )
            return RunResult(
                processing_id=claimed.processing_id,
                outcome=RunResultOutcome.DEAD_LETTERED,
            )
        except Exception as exception:
            self._record_failed_provider_usage(meter=meter, exception=exception)
            _logger.exception(
                "retryable failure in stage %s for %s",
                claimed.stage,
                claimed.processing_id,
            )
            try:
                retry_at = self._ledger.fail(
                    processing_id=claimed.processing_id,
                    attempt=claimed.attempt,
                    error=traceback.format_exc(),
                    retryable=True,
                )
            except WorkNotRunningError:
                return self._lease_lost(claimed=claimed, started_ns=started_ns)
            result_outcome = (
                RunResultOutcome.RETRY_SCHEDULED
                if retry_at is not None
                else RunResultOutcome.DEAD_LETTERED
            )
            if result_outcome is RunResultOutcome.DEAD_LETTERED:
                self._finalize_terminal_failure(
                    handler=handler, claimed=claimed, exception=exception
                )
            if retry_at is not None and self._queue is not None:
                self._queue.announce(
                    processing_id=claimed.processing_id,
                    route_snapshot=QueueRoute(
                        deployment_id=claimed.deployment_id,
                        stage=claimed.stage,
                        lane=claimed.lane,
                    ),
                    not_before_snapshot=retry_at,
                )
            self._export_exception(
                event=_worker_event(
                    deployment_id=claimed.deployment_id,
                    processing_id=claimed.processing_id,
                    stage=claimed.stage,
                    lane=claimed.lane,
                    attempt=claimed.attempt,
                    outcome=result_outcome,
                    started_ns=started_ns,
                ),
                exception=exception,
            )
            return RunResult(
                processing_id=claimed.processing_id, outcome=result_outcome
            )
        try:
            if outcome.claims_boundary is not None:
                self._ledger.complete_at_claims_boundary(
                    processing_id=claimed.processing_id,
                    attempt=claimed.attempt,
                    boundary=outcome.claims_boundary,
                    follow_up=outcome.follow_up,
                    empty=outcome.empty_obs_flush,
                )
            elif outcome.selection_chunk_barrier is not None:
                self._ledger.complete_chunk_selection(
                    processing_id=claimed.processing_id,
                    attempt=claimed.attempt,
                    barrier=outcome.selection_chunk_barrier,
                    follow_up=outcome.follow_up,
                )
            elif outcome.extract_chunk_barrier is not None:
                self._ledger.complete_chunk_extract(
                    processing_id=claimed.processing_id,
                    attempt=claimed.attempt,
                    barrier=outcome.extract_chunk_barrier,
                    follow_up=outcome.follow_up,
                )
            elif outcome.claim_normalize_barrier is not None:
                self._ledger.complete_claim_normalize(
                    processing_id=claimed.processing_id,
                    attempt=claimed.attempt,
                    barrier=outcome.claim_normalize_barrier,
                    follow_up=outcome.follow_up,
                )
            elif outcome.entity_obs_flush_barrier is not None:
                self._ledger.complete_entity_obs_flush(
                    processing_id=claimed.processing_id,
                    attempt=claimed.attempt,
                    barrier=outcome.entity_obs_flush_barrier,
                    follow_up=outcome.follow_up,
                )
            elif outcome.empty_obs_flush is not None:
                self._ledger.complete_empty_obs_flush(
                    processing_id=claimed.processing_id,
                    attempt=claimed.attempt,
                    empty=outcome.empty_obs_flush,
                    follow_up=outcome.follow_up,
                )
            else:
                self._ledger.complete(
                    processing_id=claimed.processing_id,
                    attempt=claimed.attempt,
                    follow_up=outcome.follow_up,
                )
        except WorkNotRunningError:
            return self._lease_lost(claimed=claimed, started_ns=started_ns)
        self._export_event(
            event=_worker_event(
                deployment_id=claimed.deployment_id,
                processing_id=claimed.processing_id,
                stage=claimed.stage,
                lane=claimed.lane,
                attempt=claimed.attempt,
                outcome=RunResultOutcome.SUCCEEDED,
                started_ns=started_ns,
            )
        )
        return RunResult(
            processing_id=claimed.processing_id, outcome=RunResultOutcome.SUCCEEDED
        )

    def reap_expired_leases(self, *, deployment_id: UUID) -> tuple[ExpiredLease, ...]:
        """Take back expired claims on this worker's stages and act on each.

        A requeued row is announced like a scheduled retry; a dead-lettered one
        gets its handler's terminal-failure finalizer, like any other attempt
        that ended the work.
        """
        expired = self._ledger.reap_expired_leases(
            deployment_id=deployment_id, stages=self._registry.stages
        )
        for lease in expired:
            work = lease.work
            _logger.warning(
                "lease expired for %s attempt %s in stage %s; %s",
                work.processing_id,
                work.attempt,
                work.stage,
                "requeued" if lease.requeued else "dead-lettered",
            )
            if not lease.requeued:
                self._finalize_terminal_failure(
                    handler=self._registry.handler_for(stage=work.stage),
                    claimed=work,
                    exception=TimeoutError("lease expired on the last attempt"),
                )
            elif self._queue is not None:
                self._queue.announce(
                    processing_id=work.processing_id,
                    route_snapshot=QueueRoute(
                        deployment_id=work.deployment_id,
                        stage=work.stage,
                        lane=work.lane,
                    ),
                    not_before_snapshot=datetime.now(UTC),
                )
        return expired

    def _lease_lost(self, *, claimed: ClaimedWork, started_ns: int) -> RunResult:
        """The attempt's claim was taken back while it ran; record nothing."""
        _logger.warning(
            "attempt %s of %s in stage %s finished after losing its lease;"
            " its result is discarded",
            claimed.attempt,
            claimed.processing_id,
            claimed.stage,
        )
        self._export_event(
            event=_worker_event(
                deployment_id=claimed.deployment_id,
                processing_id=claimed.processing_id,
                stage=claimed.stage,
                lane=claimed.lane,
                attempt=claimed.attempt,
                outcome=RunResultOutcome.LEASE_LOST,
                started_ns=started_ns,
            )
        )
        return RunResult(
            processing_id=claimed.processing_id, outcome=RunResultOutcome.LEASE_LOST
        )

    def _finalize_terminal_failure(
        self, *, handler: StageHandler, claimed: ClaimedWork, exception: Exception
    ) -> None:
        """Every terminal failure lets the handler make domain state truthful."""
        if not isinstance(handler, TerminalFailureFinalizer):
            return
        try:
            handler.finalize_terminal_failure(work=claimed, error=str(exception))
        except Exception:  # noqa: BLE001 — finalization must not mask the DLQ
            _logger.exception(
                "terminal-failure finalizer failed in stage %s for %s",
                claimed.stage,
                claimed.processing_id,
            )

    @staticmethod
    def _record_failed_provider_usage(
        *, meter: CostMeterPort, exception: Exception
    ) -> None:
        """Meter a billable call whose response failed after usage was parsed."""
        if not isinstance(exception, ProviderCallError) or exception.usage is None:
            return
        try:
            meter.record(
                call_key="provider_failure",
                tier="failed_response",
                usage=exception.usage,
                outcome="provider_error",
            )
        except Exception as accounting_exception:  # pragma: no cover - DB outage path
            exception.add_note(
                "failed to persist usage from the failed provider response:"
                f" {accounting_exception}"
            )
            _logger.exception("failed to meter a usage-bearing provider exception")

    def _export_event(self, *, event: TelemetryEvent) -> None:
        """Export one completed transition when telemetry is configured."""
        if self._telemetry is not None:
            self._telemetry.export_event(event=event)

    def _export_exception(
        self, *, event: TelemetryEvent, exception: BaseException
    ) -> None:
        """Export the original exception object without hiding exporter failure."""
        if self._telemetry is not None:
            self._telemetry.export_exception(event=event, exception=exception)


def _worker_event(
    *,
    deployment_id: UUID,
    processing_id: UUID,
    stage: PipelineStage,
    lane: ProcessingLane | None,
    attempt: int | None,
    outcome: RunResultOutcome,
    started_ns: int,
) -> TelemetryEvent:
    """Build the one stable provider-neutral worker event vocabulary."""
    return TelemetryEvent(
        name="worker.run",
        occurred_at=datetime.now(UTC),
        attributes=(
            TelemetryAttribute(name="deployment_id", value=str(deployment_id)),
            TelemetryAttribute(name="processing_id", value=str(processing_id)),
            TelemetryAttribute(name="stage", value=stage.value),
            TelemetryAttribute(name="lane", value=None if lane is None else lane.value),
            TelemetryAttribute(name="attempt", value=attempt),
            TelemetryAttribute(name="outcome", value=outcome.value),
            TelemetryAttribute(
                name="duration_ms", value=(time.monotonic_ns() - started_ns) / 1e6
            ),
        ),
    )
