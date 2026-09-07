"""Typed inputs to the D110 guarded temporal application journal."""

from enum import StrEnum
from typing import Literal
from typing import Self
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import JsonValue
from pydantic import model_validator

from rememberstack.model.fact_temporal import FactTemporalBasis
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.queue import UTCDateTime


class FactPlane(StrEnum):
    """Closed identifier vocabulary for fact tables and their primary keys."""

    RELATION = "relation"
    OBSERVATION = "observation"


class TemporalOperationKind(StrEnum):
    """Exact D110 effect kinds; narrative adjudication remains the semantic authority."""

    SEED = "seed"
    EVIDENCE = "evidence"
    CORRECTION = "correction"
    COMPENSATION = "compensation"
    CAP = "cap"
    SOURCE_REMOVAL = "source_removal"
    MIGRATION = "migration"
    IDENTITY = "identity"
    FORGET_RECOMPUTE = "forget_recompute"


class TemporalFactRef(BaseModel):
    """A deployment-local fact handle used to establish the complete lock set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plane: FactPlane
    fact_id: UUID


class TemporalBlock(BaseModel):
    """Canonical subject block; observations intentionally span all statement keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plane: FactPlane
    subject_entity_id: UUID
    predicate: str | None = None

    @model_validator(mode="after")
    def require_plane_key(self) -> Self:
        """Only relation blocks have a nonempty predicate coordinate."""
        if self.plane is FactPlane.RELATION and not self.predicate:
            raise ValueError("relation block requires a predicate")
        if self.plane is FactPlane.OBSERVATION and self.predicate is not None:
            raise ValueError("observation block spans the canonical entity")
        return self


class TemporalBlockState(BaseModel):
    """A locked block head used in prepared fingerprints and recorded replay order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    block_key: str
    revision: int = Field(ge=0)
    sequence: int = Field(ge=0)
    operation_id: UUID | None


class TemporalSourceKind(StrEnum):
    """The published D110 cache dependency registry vocabulary."""

    RELATION = "relation"
    OBSERVATION = "observation"
    ENTITY = "entity"
    PREDICATE = "predicate"
    DOCUMENT = "document"
    DOC_SOURCE = "doc_source"
    SCOPE = "scope"
    RULE_OWNER = "rule_owner"
    PAGE_PUBLICATION = "page_publication"
    STRUCTURAL = "structural"
    BROAD_RULE = "broad_rule"


class TemporalSourceRef(BaseModel):
    """One exact cache registry identity, declared before acquiring any source lock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: TemporalSourceKind
    source_id: UUID
    key: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_identity_key(self) -> Self:
        """Actual object identities retain their canonical UUID rather than an alias."""
        if self.kind in (
            TemporalSourceKind.RELATION,
            TemporalSourceKind.OBSERVATION,
            TemporalSourceKind.ENTITY,
            TemporalSourceKind.DOCUMENT,
            TemporalSourceKind.SCOPE,
            TemporalSourceKind.PAGE_PUBLICATION,
        ) and self.key != str(self.source_id):
            raise ValueError("an object source key must be its canonical UUID")
        return self


class TemporalEvidenceRef(BaseModel):
    """Exact consumed claim version, currency and role captured before inference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: UUID
    role: Literal[
        "support", "contrary", "historical", "candidate_from", "candidate_until"
    ]
    was_current: bool
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class TemporalDecision(BaseModel):
    """One existing-plane narrative adjudication, committed with its typed effect."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    adjudication_id: UUID
    outcome: str = Field(min_length=1)
    method: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    triggering_claim_id: UUID | None = None
    triggering_assertion_id: UUID | None = None
    related_fact_id: UUID | None = None
    decided_by: Literal["auto", "human"] = "auto"
    features: dict[str, JsonValue] = Field(default_factory=dict)


class TemporalEffect(BaseModel):
    """A completely prepared, authorized effect awaiting locked revalidation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: UUID
    fact: TemporalFactRef
    kind: TemporalOperationKind
    result: TemporalResult
    before: FactTemporalState
    after: FactTemporalState
    decision: TemporalDecision
    evidence: tuple[TemporalEvidenceRef, ...]
    semantic_predecessors: tuple[UUID, ...] = ()
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_generation: str = Field(min_length=1)
    policy_generation: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    recorded_at: UTCDateTime
    discrepancy_id: UUID | None = None
    reverses_operation_id: UUID | None = None
    support_state: Literal["complete", "unproven"] = "complete"
    footprint_complete: bool = True
    replay_class: Literal["ordinary", "checkpoint_root"] = "ordinary"

    @model_validator(mode="after")
    def require_effect_coherence(self) -> Self:
        """Reject incomplete receipts, unowned endpoint changes and clock rewriting."""
        if self.before.ingested_at != self.after.ingested_at:
            raise ValueError("an existing fact keeps its belief ingestion instant")
        if self.result is not TemporalResult.APPLIED:
            if self.before != self.after:
                raise ValueError("non-applied effect cannot change fact state")
        elif self.after.revision != self.before.revision + 1:
            raise ValueError("applied effect advances the fact revision exactly once")
        compensation = self.kind is TemporalOperationKind.COMPENSATION
        if compensation != (self.reverses_operation_id is not None):
            raise ValueError("only compensation references a reversed effect")
        if self.reverses_operation_id == self.operation_id:
            raise ValueError("an effect cannot reverse itself")
        if (
            compensation
            and self.reverses_operation_id not in self.semantic_predecessors
        ):
            raise ValueError(
                "compensation consumes the reversed effect as semantic authority"
            )
        if (
            self.kind
            not in (
                TemporalOperationKind.SEED,
                TemporalOperationKind.MIGRATION,
                TemporalOperationKind.FORGET_RECOMPUTE,
            )
            and self.before.kind != self.after.kind
        ):
            raise ValueError("ordinary verdicts cannot change fact kind")
        if self.kind is TemporalOperationKind.SEED:
            if (
                self.fact.plane is FactPlane.RELATION
                and self.decision.triggering_assertion_id is None
            ):
                raise ValueError("relation seed requires its triggering assertion ID")
            if (
                self.result is not TemporalResult.APPLIED
                or self.decision.outcome != "add"
            ):
                raise ValueError("a seed is an applied add adjudication")
            if self.before.revision != 0 or self.after.seed_claim_id is None:
                raise ValueError(
                    "seed requires a new fact and its recorded creating claim"
                )
            if self.decision.triggering_claim_id != self.after.seed_claim_id:
                raise ValueError("add adjudication must name the same seed claim")
            if not any(
                item.claim_id == self.after.seed_claim_id and item.role == "support"
                for item in self.evidence
            ):
                raise ValueError(
                    "seed testimony must be included in the consumed support"
                )
        if (
            self.replay_class == "checkpoint_root"
            and self.kind is not TemporalOperationKind.FORGET_RECOMPUTE
        ):
            raise ValueError("only sanitized forget can create a checkpoint root")
        old, new = self.before.verdict, self.after.verdict
        changed_start = (old.start, old.start_basis) != (new.start, new.start_basis)
        changed_end = (old.end, old.end_basis) != (new.end, new.end_basis)
        # Checkpoint roots deliberately take ownership of both clean components.
        root = self.replay_class == "checkpoint_root"
        if self.after.from_operation_id != (
            self.operation_id
            if changed_start or root
            else self.before.from_operation_id
        ):
            raise ValueError(
                "start ownership disagrees with the recorded value/basis change"
            )
        if self.after.until_operation_id != (
            self.operation_id if changed_end or root else self.before.until_operation_id
        ):
            raise ValueError(
                "end ownership disagrees with the recorded value/basis change"
            )
        if "temporal_effect" in self.decision.features:
            raise ValueError(
                "temporal_effect is reserved for the verified journal snapshot"
            )
        evidence_keys = {(item.claim_id, item.role) for item in self.evidence}
        if len(evidence_keys) != len(self.evidence):
            raise ValueError("each consumed claim role is recorded exactly once")
        if self.operation_id in self.semantic_predecessors:
            raise ValueError("an effect cannot be its own predecessor")
        self._require_operation_authority()
        if (
            self.kind
            in (TemporalOperationKind.CORRECTION, TemporalOperationKind.COMPENSATION)
            and self.result is TemporalResult.APPLIED
        ):
            if self.support_state != "complete" or not self.footprint_complete:
                raise ValueError(
                    "correction requires complete support and read footprint"
                )
            if not any(
                item.was_current
                and item.role in ("support", "candidate_from", "candidate_until")
                for item in self.evidence
            ):
                raise ValueError("correction requires current supporting testimony")
        return self

    def _require_operation_authority(self) -> None:
        """Keep each ordinary operation within its distinct field and clock authority."""
        if self.result is not TemporalResult.APPLIED or self.kind in (
            TemporalOperationKind.SEED,
            TemporalOperationKind.MIGRATION,
            TemporalOperationKind.FORGET_RECOMPUTE,
        ):
            return
        before, after = self.before, self.after
        if before.seed_claim_id != after.seed_claim_id:
            raise ValueError("ordinary operations preserve the recorded seed")
        if self.kind is not TemporalOperationKind.SOURCE_REMOVAL:
            if before.invalidated_at != after.invalidated_at:
                raise ValueError("only source removal changes ordinary belief closure")
        elif (
            before.invalidated_at is not None
            and before.invalidated_at != after.invalidated_at
        ):
            raise ValueError("source removal preserves an existing belief closure")
        elif after.invalidated_at is None:
            raise ValueError("source removal must close belief time")
        if self.kind in (
            TemporalOperationKind.CAP,
            TemporalOperationKind.CORRECTION,
            TemporalOperationKind.COMPENSATION,
        ) and (
            before.occurrence != after.occurrence
            or before.contradiction_group != after.contradiction_group
        ):
            raise ValueError(
                "endpoint verdicts preserve occurrence and contradiction metadata"
            )
        if self.kind in (
            TemporalOperationKind.EVIDENCE,
            TemporalOperationKind.IDENTITY,
        ):
            if before.verdict != after.verdict:
                raise ValueError(
                    "evidence and identity writes cannot invent verdict boundaries"
                )
            return
        old, new = before.verdict, after.verdict
        if self.kind in (
            TemporalOperationKind.CAP,
            TemporalOperationKind.SOURCE_REMOVAL,
        ):
            if (old.start, old.start_basis) != (new.start, new.start_basis):
                raise ValueError("a cap cannot change the start")
            changed = (old.end, old.end_basis) != (new.end, new.end_basis)
            if self.kind is TemporalOperationKind.CAP and not changed:
                raise ValueError("an applied cap must shorten the end")
            if changed and (
                before.kind is not FactTemporalKind.STATE
                or new.end is None
                or (old.end is not None and new.end >= old.end)
                or new.end_basis
                is not (
                    FactTemporalBasis.VERDICT
                    if self.kind is TemporalOperationKind.CAP
                    else FactTemporalBasis.SOURCE_REMOVED
                )
            ):
                raise ValueError(
                    "a cap must shorten a state with the correct authority basis"
                )
        if self.kind is TemporalOperationKind.CORRECTION:
            if old == new:
                raise ValueError("an applied correction must change a boundary")
            if (old.start, old.start_basis) != (new.start, new.start_basis) and (
                new.start is None
                or (old.start is not None and new.start > old.start)
                or new.start_basis is not FactTemporalBasis.VERDICT
            ):
                raise ValueError(
                    "ordinary correction can only date or move the start earlier"
                )
            if (old.end, old.end_basis) != (new.end, new.end_basis) and (
                before.kind is not FactTemporalKind.STATE
                or new.end is None
                or (old.end is not None and new.end > old.end)
                or new.end_basis is not FactTemporalBasis.VERDICT
            ):
                raise ValueError(
                    "ordinary correction can only acquire or shorten a state end"
                )
