"""Typed Plane-K planner, reflection, and quarantine values (D45/D46)."""

from decimal import Decimal
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated
from typing import Literal
from typing import TypeAlias
from uuid import UUID

from pydantic import AfterValidator
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from rememberstack.model.knowledge import KnowledgeAgentSandboxPolicy
from rememberstack.model.knowledge import KnowledgeAgentSessionRequest
from rememberstack.model.knowledge import KnowledgeArtifactStatus
from rememberstack.model.knowledge import KnowledgeLayer
from rememberstack.model.knowledge import KnowledgePageKind
from rememberstack.model.knowledge import KnowledgePlanAction
from rememberstack.model.knowledge import KnowledgePlanStatus
from rememberstack.model.knowledge import KnowledgePlanTrigger
from rememberstack.model.knowledge import KnowledgeRuleParams
from rememberstack.model.knowledge import KnowledgeWriterSuggestion
from rememberstack.model.queue import UTCDateTime

KNOWLEDGE_PLANNER_OUTPUT_PATHS = ("output/decisions.json",)


def _canonical_uuids(values: tuple[UUID, ...]) -> tuple[UUID, ...]:
    """Return stable unique UUIDs for set-valued planner fields."""
    return tuple(sorted(set(values), key=str))


CanonicalUUIDs: TypeAlias = Annotated[
    tuple[UUID, ...], AfterValidator(_canonical_uuids)
]


def _normalized_git_path(*, value: str, field: str) -> str:
    """Reject absolute, escaping, or non-normalized repository paths."""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError(f"{field} must be a normalized repository path")
    return value


class KnowledgePlanRunKind(StrEnum):
    """The proposing seat that produced one structural planning run."""

    PLANNER = "planner"
    REFLECTION = "reflection"


class KnowledgePlanRunStatus(StrEnum):
    """Terminal state of one transcript-bearing planning run."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class KnowledgePlanBand(StrEnum):
    """Deterministic D24-style consequence of a structural proposal."""

    AUTO_APPLY = "auto_apply"
    REVIEW = "review"


class KnowledgeQuarantineStatus(StrEnum):
    """Resolution state of a direct edit to a compiled page."""

    PROPOSED = "proposed"
    CURATION_ACCEPTED = "curation_accepted"
    ADOPTED = "adopted"
    REJECTED = "rejected"


class KnowledgePlannedPage(BaseModel):
    """One compiled page and its complete mechanically evaluable rule set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: KnowledgeLayer
    git_path: str = Field(min_length=1)
    curation_path: str = Field(min_length=1)
    writer_version: str = Field(min_length=1)
    rules: tuple[KnowledgeRuleParams, ...] = Field(min_length=1)
    scope_id: UUID | None = None
    parent_artifact_id: UUID | None = None
    artifact_kind: str | None = None

    @field_validator("git_path", "curation_path")
    @classmethod
    def require_safe_paths(cls, value: str, info: object) -> str:
        """Keep planner-created files inside the repository namespace."""
        field_name = getattr(info, "field_name", "path")
        return _normalized_git_path(value=value, field=str(field_name))

    @model_validator(mode="after")
    def require_disjoint_body_and_curation_paths(self) -> "KnowledgePlannedPage":
        """Prevent the machine-owned body from aliasing its human-owned sidecar."""
        if self.git_path == self.curation_path:
            raise ValueError("planned page body and curation paths must differ")
        return self


class _KnowledgePlanProposalBase(BaseModel):
    """Fields every planner or reflection proposal must justify."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rationale: str = Field(min_length=1)
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))


class KnowledgeCreatePageProposal(_KnowledgePlanProposalBase):
    """Create one evidence-derived compiled page."""

    action: Literal[KnowledgePlanAction.CREATE_PAGE] = KnowledgePlanAction.CREATE_PAGE
    page: KnowledgePlannedPage


class KnowledgeSplitPageProposal(_KnowledgePlanProposalBase):
    """Turn one compiled page into a parent over new mechanically routed children."""

    action: Literal[KnowledgePlanAction.SPLIT_PAGE] = KnowledgePlanAction.SPLIT_PAGE
    source_artifact_id: UUID
    pages: tuple[KnowledgePlannedPage, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_children_of_source(self) -> "KnowledgeSplitPageProposal":
        """Make the split topology explicit instead of guessing parentage at apply time."""
        if any(
            page.parent_artifact_id != self.source_artifact_id for page in self.pages
        ):
            raise ValueError("every split page must name the source page as parent")
        paths = [page.git_path for page in self.pages]
        curation_paths = [page.curation_path for page in self.pages]
        if len(set(paths)) != len(paths) or len(set(curation_paths)) != len(
            curation_paths
        ):
            raise ValueError("split page paths must be unique")
        return self


class KnowledgeMergePagesProposal(_KnowledgePlanProposalBase):
    """Retire several compiled pages into one new compiled target."""

    action: Literal[KnowledgePlanAction.MERGE_PAGES] = KnowledgePlanAction.MERGE_PAGES
    source_artifact_ids: CanonicalUUIDs = Field(min_length=2)
    page: KnowledgePlannedPage

    @model_validator(mode="after")
    def reject_source_as_target_parent(self) -> "KnowledgeMergePagesProposal":
        """Prevent an immediate cycle through a page the merge will tombstone."""
        if self.page.parent_artifact_id in self.source_artifact_ids:
            raise ValueError("merge target parent cannot be a merge source")
        return self


class KnowledgeMovePageProposal(_KnowledgePlanProposalBase):
    """Move one compiled page without changing its evidence ownership."""

    action: Literal[KnowledgePlanAction.MOVE_PAGE] = KnowledgePlanAction.MOVE_PAGE
    artifact_id: UUID
    old_git_path: str = Field(min_length=1)
    new_git_path: str = Field(min_length=1)
    old_curation_path: str = Field(min_length=1)
    new_curation_path: str = Field(min_length=1)
    old_parent_artifact_id: UUID | None
    new_parent_artifact_id: UUID | None = None

    @field_validator(
        "old_git_path", "new_git_path", "old_curation_path", "new_curation_path"
    )
    @classmethod
    def require_safe_paths(cls, value: str, info: object) -> str:
        """Keep both sides of a move inside the repository namespace."""
        field_name = getattr(info, "field_name", "path")
        return _normalized_git_path(value=value, field=str(field_name))

    @model_validator(mode="after")
    def require_a_real_move(self) -> "KnowledgeMovePageProposal":
        """Reject a path-preserving move that changes no structure."""
        if self.new_parent_artifact_id == self.artifact_id:
            raise ValueError("move_page cannot make an artifact its own parent")
        if self.old_git_path == self.new_git_path and (
            self.old_curation_path == self.new_curation_path
            and self.old_parent_artifact_id == self.new_parent_artifact_id
        ):
            raise ValueError("move_page must change path or parent")
        if self.new_git_path == self.new_curation_path:
            raise ValueError("moved body and curation paths must differ")
        return self


class KnowledgeRetirePageProposal(_KnowledgePlanProposalBase):
    """Retire one compiled page whose evidence no longer needs a home."""

    action: Literal[KnowledgePlanAction.RETIRE_PAGE] = KnowledgePlanAction.RETIRE_PAGE
    artifact_id: UUID


class KnowledgeAdjustRuleProposal(_KnowledgePlanProposalBase):
    """Replace one compiled page's routing-rule union atomically."""

    action: Literal[KnowledgePlanAction.ADJUST_RULE] = KnowledgePlanAction.ADJUST_RULE
    artifact_id: UUID
    rules: tuple[KnowledgeRuleParams, ...] = Field(min_length=1)


class KnowledgeConvertKindProposal(_KnowledgePlanProposalBase):
    """Adopt a compiled page or hand an authored page to the compiler."""

    action: Literal[KnowledgePlanAction.CONVERT_KIND] = KnowledgePlanAction.CONVERT_KIND
    artifact_id: UUID
    from_kind: KnowledgePageKind
    to_kind: KnowledgePageKind
    writer_version: str | None = None
    curation_path: str | None = None
    rules: tuple[KnowledgeRuleParams, ...] = ()

    @field_validator("curation_path")
    @classmethod
    def require_safe_optional_curation_path(cls, value: str | None) -> str | None:
        """Keep handover sidecars inside the repository namespace."""
        if value is None:
            return None
        return _normalized_git_path(value=value, field="curation_path")

    @model_validator(mode="after")
    def require_one_valid_direction(self) -> "KnowledgeConvertKindProposal":
        """Bind adoption and handover to their distinct ownership inputs."""
        if self.from_kind == self.to_kind:
            raise ValueError("convert_kind must change page kind")
        if self.to_kind is KnowledgePageKind.AUTHORED:
            if (
                self.writer_version is not None
                or self.curation_path is not None
                or self.rules
            ):
                raise ValueError("adoption preserves rules and removes writer inputs")
            return self
        if self.writer_version is None or self.curation_path is None or not self.rules:
            raise ValueError(
                "handover requires writer version, curation path, and rules"
            )
        return self


KnowledgePlanProposal: TypeAlias = Annotated[
    KnowledgeCreatePageProposal
    | KnowledgeSplitPageProposal
    | KnowledgeMergePagesProposal
    | KnowledgeMovePageProposal
    | KnowledgeRetirePageProposal
    | KnowledgeAdjustRuleProposal
    | KnowledgeConvertKindProposal,
    Field(discriminator="action"),
]


class KnowledgePlannerSandboxPolicy(KnowledgeAgentSandboxPolicy):
    """The fixed declared output surface of a planner or reflection session."""

    accepted_output_paths: tuple[str, ...] = KNOWLEDGE_PLANNER_OUTPUT_PATHS

    @field_validator("accepted_output_paths")
    @classmethod
    def require_declared_planner_output(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Prevent a structural agent from widening its accepted write surface."""
        if value != KNOWLEDGE_PLANNER_OUTPUT_PATHS:
            raise ValueError("accepted planner output paths are fixed by contract")
        return value


class KnowledgePlannerSessionRequest(KnowledgeAgentSessionRequest):
    """One planner or reflection invocation with a decisions-only write surface."""

    sandbox: KnowledgeAgentSandboxPolicy = Field(
        default_factory=KnowledgePlannerSandboxPolicy
    )

    @model_validator(mode="after")
    def require_planner_output_contract(self) -> "KnowledgePlannerSessionRequest":
        """Reject callers that replace the fixed decisions-only output surface."""
        if self.sandbox.accepted_output_paths != KNOWLEDGE_PLANNER_OUTPUT_PATHS:
            raise ValueError("accepted planner output paths are fixed by contract")
        return self


class KnowledgeOrphanAggregate(BaseModel):
    """New candidate identities with no compiled-page rule home for one entity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: UUID
    candidate_keys: tuple[str, ...] = Field(min_length=1)

    @field_validator("candidate_keys")
    @classmethod
    def canonicalize_candidate_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Count stable candidate identities once per entity."""
        return tuple(sorted(set(value)))

    @property
    def candidate_count(self) -> int:
        """Return the exact number of unique currently unhoused candidates."""
        return len(self.candidate_keys)


class KnowledgePlannerArtifactState(BaseModel):
    """The structural and health fields a planner may inspect for one page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: UUID
    layer: KnowledgeLayer
    page_kind: KnowledgePageKind
    status: KnowledgeArtifactStatus
    git_path: str
    scope_id: UUID | None = None
    parent_artifact_id: UUID | None = None
    artifact_kind: str | None = None
    candidate_count: int = Field(default=0, ge=0)
    uncited_count: int = Field(default=0, ge=0)
    page_size_bytes: int = Field(default=0, ge=0)


class KnowledgeCompiledContentState(BaseModel):
    """Last accepted body identity used for deterministic drift detection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: UUID
    deployment_id: UUID
    git_path: str
    content_hash: str


class KnowledgePlanningSnapshot(BaseModel):
    """Bounded structural triggers and health metrics supplied to one agent run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    scope_id: UUID | None = None
    artifacts: tuple[KnowledgePlannerArtifactState, ...]
    orphan_aggregates: tuple[KnowledgeOrphanAggregate, ...] = ()
    overflow_artifact_ids: CanonicalUUIDs = ()
    writer_suggestions: tuple[KnowledgeWriterSuggestion, ...] = ()


class KnowledgePlanRunWrite(BaseModel):
    """One terminal transcript-bearing planner or reflection run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    deployment_id: UUID
    scope_id: UUID | None = None
    run_kind: KnowledgePlanRunKind
    trigger: KnowledgePlanTrigger
    component_version: str = Field(min_length=1)
    input_hash: str = Field(min_length=1)
    session_transcript_uri: str = Field(min_length=1)
    status: KnowledgePlanRunStatus
    failure: str | None = None
    tokens: int | None = Field(default=None, ge=0)
    cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))

    @model_validator(mode="after")
    def require_terminal_failure_shape(self) -> "KnowledgePlanRunWrite":
        """Keep success and failure rows unambiguous without trimming exceptions."""
        if (self.status is KnowledgePlanRunStatus.FAILED) != (self.failure is not None):
            raise ValueError("only failed plan runs carry a failure traceback")
        return self


class KnowledgePlanDecisionResult(BaseModel):
    """The durable consequence assigned to one validated structural proposal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: UUID
    action: KnowledgePlanAction
    status: KnowledgePlanStatus
    band: KnowledgePlanBand
    blast_radius: int = Field(ge=0)
    expected_impact: Decimal = Field(ge=Decimal("0"))


class KnowledgePendingPlanDecision(BaseModel):
    """One accepted structural decision not yet bound to a git revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: UUID
    proposal: KnowledgePlanProposal
    decided_at: UTCDateTime
    artifact_paths: dict[UUID, str] = Field(default_factory=dict)

    @field_validator("artifact_paths")
    @classmethod
    def require_safe_artifact_paths(cls, value: dict[UUID, str]) -> dict[UUID, str]:
        """Keep reconciled file targets inside the repository namespace."""
        return {
            artifact_id: _normalized_git_path(value=path, field="artifact_paths")
            for artifact_id, path in value.items()
        }


class KnowledgeQuarantineRecord(BaseModel):
    """A direct compiled-body edit preserved for explicit triage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quarantine_id: UUID
    decision_id: UUID
    deployment_id: UUID
    artifact_id: UUID
    recorded_content_hash: str
    detected_content_hash: str
    proposed_sidecar_entry: str = Field(min_length=1)
    status: KnowledgeQuarantineStatus
    detected_at: UTCDateTime | None = None
    resolved_at: UTCDateTime | None = None
