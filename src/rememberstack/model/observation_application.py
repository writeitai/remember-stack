"""Exact observation source and generation coordinates for D113 application."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.queue import ProcessingLane
from rememberstack.model.queue import UTCDateTime
from rememberstack.model.temporal_write import TemporalBlockState
from rememberstack.model.temporal_write import TemporalEffect
from rememberstack.model.temporal_write import TemporalEvidenceRef


class ObservationVersionCoordinates(BaseModel):
    """A version's source contract and independently pinned semantic/flush generations."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    deployment_id: UUID
    version_id: UUID
    representation_id: UUID
    chunker_version: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    normalizer_version: str = Field(min_length=1)
    adjudicator_version: str = Field(min_length=1)
    flush_version: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    lane: ProcessingLane


class ObservationAdmissionHead(BaseModel):
    """One immutable ordinal of a closed canonical entity batch, before inference."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    batch_id: UUID
    assertion_id: UUID
    ordinal: int = Field(gt=0)
    expected_inputs: int = Field(gt=0)
    adjudicator_version: str
    canonical_subject_entity_id: UUID


class ObservationTestimony(BaseModel):
    """One exact source witness; publication time is context, not a verdict boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    claim_id: UUID
    doc_id: UUID
    text: str
    asserted_at: UTCDateTime | None
    window: ClaimTemporalWindow
    evidence: TemporalEvidenceRef
    stance: Literal["supports", "contradicts"] = "supports"


class StagedObservation(BaseModel):
    """The original normalized statement plus its presently resolved canonical subject."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    assertion_id: UUID
    receipt_id: UUID
    normalizer_version: str
    normalized_subject_entity_id: UUID
    subject_entity_id: UUID
    statement: str
    shape_kind: FactTemporalKind
    testimony: ObservationTestimony


class ObservationApplicationCandidate(BaseModel):
    """A complete locked fact with bounded testimony text and full evidence-window metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    observation_id: UUID
    statement: str
    normalizer_version: str
    state: FactTemporalState
    operation_id: UUID
    testimony: tuple[ObservationTestimony, ...]
    omitted_testimony: int = Field(ge=0)
    evidence_windows: tuple[ClaimTemporalWindow, ...]
    legacy_claim_ids: tuple[UUID, ...]


class ObservationCurrentSupport(BaseModel):
    """A completed source application's separately owned current evidence location."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    assertion: StagedObservation
    adjudicator_version: str
    original_observation_id: UUID
    current_observation_id: UUID
    support_owner_operation_id: UUID
    support_checkpoint_id: UUID | None


class ObservationResplitInputs(BaseModel):
    """Complete source applications displaced by a proposed state cap, or legacy blockers."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    applications: tuple[ObservationCurrentSupport, ...]
    blocking_legacy_claim_ids: tuple[UUID, ...]


class ObservationApplicationInputs(BaseModel):
    """Complete preparation authority, shared by helpers regardless of which unit they lease."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    deployment_id: UUID
    assertion: StagedObservation
    candidates: tuple[ObservationApplicationCandidate, ...]
    current_support: tuple[ObservationCurrentSupport, ...]
    blocks: tuple[TemporalBlockState, ...]
    policy_fingerprint: str


class ObservationApplicationPreparation(BaseModel):
    """One recorded head attempt; remote inference must begin after its transaction closes."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    head: ObservationAdmissionHead
    preparation_id: UUID
    input_fingerprint: str
    inputs: ObservationApplicationInputs
    new_observation_id: UUID
    recorded_at: UTCDateTime


class ObservationPairDecision(BaseModel):
    """A grounded relationship to one named existing observation identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    observation_id: UUID
    outcome: Literal[
        "evidence", "incoming_succeeds", "existing_succeeds", "contradict", "coexist"
    ]


class ObservationIdentityVerdict(BaseModel):
    """One selected observation identity; repeated or overlapping events may remain separate."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    decisions: tuple[ObservationPairDecision, ...] = ()
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1)


class ObservationApplicationOutput(BaseModel):
    """Recorded semantic choice; dependent application planning precedes runtime publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    verdict: ObservationIdentityVerdict
    method: Literal["exact", "novelty_gate", "small_model", "frontier_llm"]
    model: str | None = None
    disposition: Literal["accepted", "uncertain", "refused"] = "accepted"
    rejected_verdict: ObservationIdentityVerdict | None = None
    nominated_observation_ids: tuple[UUID, ...] = ()
    omitted_candidates: int = Field(default=0, ge=0)


class ObservationSupportMove(BaseModel):
    """D113 machine-readable authority for one source application's evidence relocation."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
    schema_version: Literal["observation-support-move:1"] = Field(
        default="observation-support-move:1", alias="schema"
    )
    assertion_id: UUID
    adjudicator_version: str
    previous_observation_id: UUID
    destination_observation_id: UUID
    previous_support_owner_operation_id: UUID
    establishing_operation_id: UUID
    causal_cap_operation_id: UUID


class ObservationNewFact(BaseModel):
    """A declared revision-zero row whose seed must commit in the same application group."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    observation_id: UUID
    subject_entity_id: UUID
    statement: str
    normalizer_version: str
    ingested_at: UTCDateTime


class ObservationPlannedEffect(BaseModel):
    """One typed journal effect and its ordered evidence/support changes, without new inference."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    effect: TemporalEffect
    attach_claim_id: UUID | None = None
    remove_claim_id: UUID | None = None
    support_move: ObservationSupportMove | None = None


class ObservationApplicationPlan(BaseModel):
    """The whole dependent atomic result, including every required state re-split."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    identity_outcome: Literal["new", "evidence"]
    original_observation_id: UUID
    initial_support_operation_id: UUID
    new_facts: tuple[ObservationNewFact, ...]
    steps: tuple[ObservationPlannedEffect, ...] = Field(min_length=1)
