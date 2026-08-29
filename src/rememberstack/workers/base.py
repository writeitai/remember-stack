"""The handler-registration model and the worker runner (WP-0.3, D12/D52/D67).

A stage handler transforms one claimed unit of work and names its chain
follow-ups; the runner is the single exception boundary (core value 6): a
handler failure is logged with its full traceback, recorded on the row, and
either retried with backoff or dead-lettered — it never disappears.
"""

from datetime import datetime
from datetime import UTC
import logging
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
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.queue import TaskQueuePort
from rememberstack.ports.telemetry import TelemetryPort
from rememberstack.spine.work_ledger import WorkLedger

_logger = logging.getLogger(__name__)


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


class HandlerOutcome(BaseModel):
    """What a successful handler produced: the chain follow-ups to enqueue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    follow_up: tuple[EnqueueWork, ...] = ()
    extract_chunk_barrier: ExtractChunkBarrier | None = None
    claim_normalize_barrier: ClaimNormalizeBarrier | None = None
    entity_obs_flush_barrier: EntityObsFlushBarrier | None = None
    empty_obs_flush: EmptyObsFlushComplete | None = None


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

    def __init__(self, *, ledger: WorkLedger, processing_id: UUID) -> None:
        """Bind every call record to the authoritative running row."""
        self._ledger = ledger
        self._processing_id = processing_id

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
                processing_id=self._processing_id,
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
        self, *, deployment_id: UUID, stage: PipelineStage, lane: ProcessingLane | None
    ) -> RunResult:
        """Claim and run at most one due unit of work on the route.

        This is the worker's single catch-to-log boundary (core value 6): the
        full traceback is logged and recorded on the row; a
        `NonRetryableHandlerError` dead-letters immediately, anything else
        retries with backoff until the attempt limit dead-letters it.
        """
        started_ns = time.monotonic_ns()
        handler = self._registry.handler_for(stage=stage)  # before any claim:
        # an unregistered stage must never strand a claimed row as running.
        claimed = self._ledger.claim_one(
            deployment_id=deployment_id, stage=stage, lane=lane
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
        meter = _LedgerCostMeter(
            ledger=self._ledger, processing_id=claimed.processing_id
        )
        try:
            outcome = handler.handle(work=claimed, meter=meter)
        except (NonRetryableHandlerError, ProviderAccountingError) as exception:
            _logger.exception(
                "non-retryable failure in stage %s for %s",
                claimed.stage,
                claimed.processing_id,
            )
            self._ledger.fail(
                processing_id=claimed.processing_id,
                error=traceback.format_exc(),
                retryable=False,
            )
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
            retry_at = self._ledger.fail(
                processing_id=claimed.processing_id,
                error=traceback.format_exc(),
                retryable=True,
            )
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
                        deployment_id=deployment_id, stage=stage, lane=lane
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
        if outcome.extract_chunk_barrier is not None:
            self._ledger.complete_chunk_extract(
                processing_id=claimed.processing_id,
                barrier=outcome.extract_chunk_barrier,
                follow_up=outcome.follow_up,
            )
        elif outcome.claim_normalize_barrier is not None:
            self._ledger.complete_claim_normalize(
                processing_id=claimed.processing_id,
                barrier=outcome.claim_normalize_barrier,
                follow_up=outcome.follow_up,
            )
        elif outcome.entity_obs_flush_barrier is not None:
            self._ledger.complete_entity_obs_flush(
                processing_id=claimed.processing_id,
                barrier=outcome.entity_obs_flush_barrier,
                follow_up=outcome.follow_up,
            )
        elif outcome.empty_obs_flush is not None:
            self._ledger.complete_empty_obs_flush(
                processing_id=claimed.processing_id,
                empty=outcome.empty_obs_flush,
                follow_up=outcome.follow_up,
            )
        else:
            self._ledger.complete(
                processing_id=claimed.processing_id, follow_up=outcome.follow_up
            )
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
