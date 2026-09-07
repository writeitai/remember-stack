"""Evidence-grounded endpoint choices for D110's autonomous temporal reviewer."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.queue import UTCDateTime
from rememberstack.model.temporal_write import TemporalFactRef


class CorrectionTestimony(BaseModel):
    """A retained linked witness; source time is context and never endpoint authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    claim_id: UUID
    doc_id: UUID
    text: str
    asserted_at: UTCDateTime | None
    window: ClaimTemporalWindow
    stance: Literal["supports", "contradicts"]
    is_current: bool


class CorrectionEndpointCandidate(BaseModel):
    """One canonical endpoint supported by named current, already-linked claims."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    candidate_id: UUID
    endpoint: Literal["from", "until"]
    value: UTCDateTime
    claim_ids: tuple[UUID, ...] = Field(min_length=1)
    document_ids: tuple[UUID, ...] = Field(min_length=1)


class CorrectionFactContext(BaseModel):
    """A named fact whose complete revision participates in correction preparation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    fact: TemporalFactRef
    state: FactTemporalState
    statement: str


class TemporalCorrectionInputs(BaseModel):
    """Bounded semantic context plus complete prepared endpoint authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    target: CorrectionFactContext
    testimony: tuple[CorrectionTestimony, ...]
    candidates: tuple[CorrectionEndpointCandidate, ...]
    # Complete slices in the target's exclusion scope, not unrelated entity facts.
    # The preparing journal reader must certify this set and its revisions.
    neighbours: tuple[CorrectionFactContext, ...]
    omitted_testimony_count: int = Field(default=0, ge=0)
    required_context_complete: bool


class TemporalCorrectionVerdict(BaseModel):
    """A model selects existing candidate IDs; arbitrary timestamp fields are forbidden."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    outcome: Literal["correct", "noop", "uncertain"]
    start_candidate_id: UUID | None = None
    end_candidate_id: UUID | None = None
    supporting_claim_ids: tuple[UUID, ...] = ()
    contrary_claim_ids: tuple[UUID, ...] = ()
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1)
