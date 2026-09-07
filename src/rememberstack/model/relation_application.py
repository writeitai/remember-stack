"""Prepared, ordered relation identity decisions and their exact temporal inputs."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.queue import UTCDateTime
from rememberstack.model.temporal_write import TemporalBlockState
from rememberstack.model.temporal_write import TemporalEvidenceRef


class RelationTestimony(BaseModel):
    """One exact source witness provided to identity adjudication."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    claim_id: UUID
    doc_id: UUID
    text: str
    asserted_at: UTCDateTime | None
    window: ClaimTemporalWindow
    evidence: TemporalEvidenceRef


class StagedRelation(BaseModel):
    """An admitted assertion before it acquires a fact identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    assertion_id: UUID
    normalizer_version: str
    subject_entity_id: UUID
    predicate: str
    object_entity_id: UUID
    subject_name: str
    object_name: str
    shape_kind: FactTemporalKind
    testimony: RelationTestimony


class RelationApplicationCandidate(BaseModel):
    """A locked fact and the bounded source sample consumed by the identity ladder."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    relation_id: UUID
    object_entity_id: UUID
    object_name: str
    state: FactTemporalState
    testimony: tuple[RelationTestimony, ...]
    omitted_testimony: int = Field(ge=0)
    operation_id: UUID


class RelationApplicationInputs(BaseModel):
    """All authority and policy inputs whose change invalidates a prepared answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    deployment_id: UUID
    assertion: StagedRelation
    candidates: tuple[RelationApplicationCandidate, ...]
    blocks: tuple[TemporalBlockState, ...]
    is_change_prone: bool
    predicate_status: str
    policy_fingerprint: str


class RelationApplicationPreparation(BaseModel):
    """One durable head attempt; remote inference runs after its transaction ends."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    batch_id: UUID
    ordinal: int = Field(gt=0)
    adjudicator_version: str
    preparation_id: UUID
    input_fingerprint: str
    inputs: RelationApplicationInputs
    new_relation_id: UUID
    recorded_at: UTCDateTime


class RelationPairDecision(BaseModel):
    """A semantic verdict relative to a named existing candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    relation_id: UUID
    support_target_id: UUID | None = None
    outcome: Literal[
        "evidence", "incoming_succeeds", "existing_succeeds", "contradict", "coexist"
    ]


class RelationIdentityVerdict(BaseModel):
    """One whole-block identity decision; deterministic state support overrides omission."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    decisions: tuple[RelationPairDecision, ...] = ()
    confidence: float = Field(ge=0, le=1)
    rationale: str


class RelationApplicationOutput(BaseModel):
    """The first complete recorded inference result for a pinned head attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    verdict: RelationIdentityVerdict
    method: Literal["exact", "novelty_gate", "small_model", "frontier_llm"]
    model: str | None = None


class RelationApplicationResult(BaseModel):
    """Idempotent application receipt, also used to repair a version's completion."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    assertion_id: UUID
    relation_ids: tuple[UUID, ...] = Field(min_length=1)
    identity_outcome: Literal["new", "evidence"]
    affected_relation_ids: tuple[UUID, ...]

    @property
    def relation_id(self) -> UUID:
        """Expose a single identity only when the receipt has exactly one target."""
        if len(self.relation_ids) != 1:
            raise ValueError("multi-target state support has no primary relation")
        return self.relation_ids[0]
