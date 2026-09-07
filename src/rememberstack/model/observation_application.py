"""Exact observation source and generation coordinates for D113 application."""

from typing import Literal
from typing import Self
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.queue import ProcessingLane
from rememberstack.model.queue import UTCDateTime
from rememberstack.model.temporal_write import FactPlane
from rememberstack.model.temporal_write import TemporalBlockState
from rememberstack.model.temporal_write import TemporalEffect
from rememberstack.model.temporal_write import TemporalEvidenceRef
from rememberstack.model.temporal_write import TemporalOperationKind


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
    withdrawn_at: UTCDateTime | None = None
    withdrawal_reason: (
        Literal["reextracted", "version_superseded", "version_deleted"] | None
    ) = None


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
    evidence: tuple[TemporalEvidenceRef, ...] = ()


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
    flag_support_withdrawn: bool = False

    @model_validator(mode="after")
    def require_effect_actions(self) -> Self:
        """Evidence actions and support movements must agree with their exact typed journal effect."""
        if self.effect.fact.plane is not FactPlane.OBSERVATION:
            raise ValueError("an observation plan cannot write a relation")
        if self.attach_claim_id is not None and self.remove_claim_id is not None:
            raise ValueError("one observation step cannot attach and remove testimony")
        claim = self.attach_claim_id or self.remove_claim_id
        if claim is not None and (
            self.effect.kind
            not in (TemporalOperationKind.SEED, TemporalOperationKind.EVIDENCE)
            or self.effect.result is not TemporalResult.APPLIED
            or self.effect.decision.triggering_claim_id != claim
            or not any(
                item.claim_id == claim and item.role == "support"
                for item in self.effect.evidence
            )
        ):
            raise ValueError(
                "an evidence action needs an applied identity effect and its exact source witness"
            )
        if self.flag_support_withdrawn and (
            self.effect.kind is not TemporalOperationKind.EVIDENCE
            or self.effect.result is not TemporalResult.APPLIED
            or self.effect.before.invalidated_at != self.effect.after.invalidated_at
        ):
            raise ValueError(
                "D54 support flagging cannot close belief or change world time"
            )
        move = self.support_move
        if move is not None and (
            self.attach_claim_id is None
            or move.previous_observation_id == move.destination_observation_id
            or move.establishing_operation_id != self.effect.operation_id
            or move.destination_observation_id != self.effect.fact.fact_id
            or move.assertion_id != self.effect.decision.triggering_assertion_id
            or not {
                move.previous_support_owner_operation_id,
                move.causal_cap_operation_id,
            }.issubset(self.effect.semantic_predecessors)
            or self.effect.decision.features.get("support_move")
            != move.model_dump(mode="json", by_alias=True)
        ):
            raise ValueError(
                "support movement must match its establishing effect and causal authority"
            )
        if move is None and "support_move" in self.effect.decision.features:
            raise ValueError(
                "a support movement payload requires its typed execution action"
            )
        return self


class ObservationApplicationPlan(BaseModel):
    """The whole dependent atomic result, including every required state re-split."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    identity_outcome: Literal["new", "evidence"]
    original_observation_id: UUID
    initial_support_operation_id: UUID
    new_facts: tuple[ObservationNewFact, ...]
    steps: tuple[ObservationPlannedEffect, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_complete_effect_chain(self) -> Self:
        """A serializable plan preserves the original receipt and seeds every declared new fact exactly once."""
        operations = [step.effect.operation_id for step in self.steps]
        creations = {item.observation_id: item for item in self.new_facts}
        if len(set(operations)) != len(operations) or len(creations) != len(
            self.new_facts
        ):
            raise ValueError(
                "observation plan repeats an operation or new fact identity"
            )
        initial = self.steps[0].effect
        if (
            initial.operation_id != self.initial_support_operation_id
            or initial.fact.fact_id != self.original_observation_id
        ):
            raise ValueError(
                "observation plan must begin with its immutable original identity effect"
            )
        expected = (
            TemporalOperationKind.SEED
            if self.identity_outcome == "new"
            else TemporalOperationKind.EVIDENCE
        )
        if initial.kind is not expected or initial.result is not TemporalResult.APPLIED:
            raise ValueError(
                "original observation outcome disagrees with its support effect"
            )
        states: dict[UUID, FactTemporalState] = {}
        seeded: set[UUID] = set()
        seen: set[UUID] = set()
        for step in self.steps:
            effect = step.effect
            identity = effect.fact.fact_id
            if set(effect.semantic_predecessors) & (set(operations) - seen):
                raise ValueError("observation plan depends on a future operation")
            if identity in states and effect.before != states[identity]:
                raise ValueError(
                    "observation plan has a broken per-fact revision chain"
                )
            if identity in creations and identity not in seeded:
                if (
                    effect.kind is not TemporalOperationKind.SEED
                    or effect.result is not TemporalResult.APPLIED
                ):
                    raise ValueError(
                        "a new observation must begin with an applied seed"
                    )
                if effect.before.ingested_at != creations[identity].ingested_at:
                    raise ValueError(
                        "new observation and seed use different belief creation instants"
                    )
                seeded.add(identity)
            elif effect.kind is TemporalOperationKind.SEED:
                raise ValueError("an observation seed lacks a unique declared creation")
            if (
                effect.kind is TemporalOperationKind.SEED
                and step.attach_claim_id != effect.after.seed_claim_id
            ):
                raise ValueError(
                    "a new observation seed requires its exact evidence attachment"
                )
            states[identity] = effect.after
            seen.add(effect.operation_id)
        if seeded != set(creations):
            raise ValueError("an observation plan leaves an unseeded speculative fact")
        return self
