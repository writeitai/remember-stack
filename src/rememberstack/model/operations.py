"""Typed, bounded operational inspection and dead-letter replay values."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model.processing import ProcessingStatus
from rememberstack.model.processing import ProcessingTarget
from rememberstack.model.queue import PipelineStage
from rememberstack.model.queue import ProcessingLane
from rememberstack.model.queue import QueueRoute
from rememberstack.model.queue import UTCDateTime


class PipelineRouteStatus(BaseModel):
    """One deployment route/status aggregate; the enum vocabulary bounds its size."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: PipelineStage
    lane: ProcessingLane | None
    status: ProcessingStatus
    count: int = Field(ge=0)


class DeadLetterGroup(BaseModel):
    """One dead-letter aggregate used to spot a failing version or exception."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: PipelineStage
    error_class: str
    component_version: str
    count: int = Field(ge=1)
    oldest_enqueued_at: UTCDateTime
    latest_finished_at: UTCDateTime | None


class DeadLetterRecord(BaseModel):
    """One bounded DLQ sample retaining the complete diagnostic and replay input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    processing_id: UUID
    target_kind: ProcessingTarget
    target_id: UUID
    stage: PipelineStage
    component_version: str
    content_hash: str
    lane: ProcessingLane | None
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    error_class: str
    last_error: str | None
    payload: dict[str, object] | None
    enqueued_at: UTCDateTime
    finished_at: UTCDateTime | None


class DeadLetterReport(BaseModel):
    """Deployment DLQ totals plus independently bounded aggregate/detail samples."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int = Field(ge=0)
    group_total: int = Field(ge=0)
    groups: tuple[DeadLetterGroup, ...]
    items: tuple[DeadLetterRecord, ...]


class PoisonTargetRecord(BaseModel):
    """A target dead-lettered by at least two component versions for one stage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_kind: ProcessingTarget
    target_id: UUID
    stage: PipelineStage
    component_version_total: int = Field(ge=2)
    component_versions: tuple[str, ...]
    dead_letters: int = Field(ge=2)


class PoisonTargetReport(BaseModel):
    """Total poison targets and a bounded sample."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int = Field(ge=0)
    items: tuple[PoisonTargetRecord, ...]


class ProjectionSnapshotState(BaseModel):
    """The current P3 CorpusFS snapshot pointer, if one exists."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: UUID
    plane: Literal["P3_corpusfs"]
    version: str
    store_uri: str
    row_counts: dict[str, object] | None
    built_at: UTCDateTime
    published_at: UTCDateTime | None


class CurrencyMismatch(BaseModel):
    """A claim whose cached currency differs from the append-only ledger truth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: UUID
    cached_current: bool
    ledger_current: bool


class CurrencyLedgerAudit(BaseModel):
    """Deployment claim count and bounded currency-ledger mismatch evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claims: int = Field(ge=0)
    mismatch_total: int = Field(ge=0)
    mismatches: tuple[CurrencyMismatch, ...]


class OperationalReport(BaseModel):
    """One coherent, deployment-scoped operational inspection snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    generated_at: UTCDateTime
    routes: tuple[PipelineRouteStatus, ...]
    dead_letters: DeadLetterReport
    poison_targets: PoisonTargetReport
    latest_projections: tuple[ProjectionSnapshotState, ...]
    currency: CurrencyLedgerAudit


class DeadLetterReplayResult(BaseModel):
    """Authoritative route and due time after reopening one dead-letter row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    processing_id: UUID
    route: QueueRoute
    not_before: UTCDateTime
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
