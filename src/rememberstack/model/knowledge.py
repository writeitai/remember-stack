"""Typed Plane-K control-plane values (D45)."""

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
from pydantic import JsonValue
from pydantic import model_validator

from rememberstack.model.mounts import PublishedMounts
from rememberstack.model.queue import UTCDateTime

SHA256: TypeAlias = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
KNOWLEDGE_WRITER_OUTPUT_PATHS = (
    "output/prose.md",
    "output/citations.json",
    "output/summary.md",
    "output/suggestions.json",
)


def _sorted_unique_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    """Canonicalize a set-valued string parameter."""
    return tuple(sorted(set(values)))


def _sorted_unique_uuids(values: tuple[UUID, ...]) -> tuple[UUID, ...]:
    """Canonicalize a set-valued UUID parameter."""
    return tuple(sorted(set(values), key=str))


def _sorted_unique_candidate_layers(
    values: tuple["KnowledgeCandidateLayer", ...],
) -> tuple["KnowledgeCandidateLayer", ...]:
    """Canonicalize a set-valued candidate-layer parameter."""
    return tuple(sorted(set(values), key=lambda value: value.value))


CanonicalStrings: TypeAlias = Annotated[
    tuple[str, ...], AfterValidator(_sorted_unique_strings)
]
CanonicalUUIDs: TypeAlias = Annotated[
    tuple[UUID, ...], AfterValidator(_sorted_unique_uuids)
]


class KnowledgeRuleKind(StrEnum):
    """The closed, mechanically evaluated D45 routing-rule vocabulary."""

    ENTITY = "entity"
    ENTITY_SUBTREE = "entity_subtree"
    PREDICATE_BEAT = "predicate_beat"
    DOC_SET = "doc_set"
    SCOPE_INTERESTS = "scope_interests"
    MANUAL = "manual"


class KnowledgeRuleKeyKind(StrEnum):
    """The coarse keys supported by the routing inverted index."""

    ENTITY = "entity"
    PREDICATE = "predicate"
    DOC_SOURCE = "doc_source"


class KnowledgeCandidateLayer(StrEnum):
    """Candidate grains a rule may select for a compiled page."""

    RELATIONS = "relations"
    OBSERVATIONS = "observations"
    CLAIMS = "claims"


CanonicalCandidateLayers: TypeAlias = Annotated[
    tuple[KnowledgeCandidateLayer, ...], AfterValidator(_sorted_unique_candidate_layers)
]


class KnowledgeLayer(StrEnum):
    """The three content tiers sharing one compile mechanism (D47)."""

    K1 = "K1"
    K2 = "K2"
    K3 = "K3"


class KnowledgePageKind(StrEnum):
    """The D46 page ownership contract."""

    COMPILED = "compiled"
    AUTHORED = "authored"


class KnowledgeArtifactStatus(StrEnum):
    """Lifecycle states of one Plane-K artifact handle."""

    ACTIVE = "active"
    STALE = "stale"
    QUARANTINED = "quarantined"
    TOMBSTONED = "tombstoned"


class KnowledgePlanAction(StrEnum):
    """Append-only planner structure actions supported by the schema."""

    CREATE_PAGE = "create_page"
    SPLIT_PAGE = "split_page"
    MERGE_PAGES = "merge_pages"
    MOVE_PAGE = "move_page"
    RETIRE_PAGE = "retire_page"
    ADJUST_RULE = "adjust_rule"
    CONVERT_KIND = "convert_kind"


class KnowledgePlanTrigger(StrEnum):
    """Reasons a planner may make a durable structure decision."""

    ORPHAN_EVIDENCE = "orphan_evidence"
    SIZE_OVERFLOW = "size_overflow"
    REFLECTION = "reflection"
    WRITER_SUGGESTION = "writer_suggestion"
    HUMAN = "human"


class KnowledgePlanStatus(StrEnum):
    """Review state for a structure decision."""

    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"


class KnowledgeEvidenceRole(StrEnum):
    """How a K citation uses one evidence target."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CITES = "cites"


class EntityRuleParams(BaseModel):
    """Everything about one entity, optionally narrowed by fact layer/predicate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[KnowledgeRuleKind.ENTITY] = KnowledgeRuleKind.ENTITY
    entity_id: UUID
    predicates: CanonicalStrings = ()
    layers: CanonicalCandidateLayers = (
        KnowledgeCandidateLayer.RELATIONS,
        KnowledgeCandidateLayer.OBSERVATIONS,
        KnowledgeCandidateLayer.CLAIMS,
    )


class EntitySubtreeRuleParams(BaseModel):
    """One entity plus the transitive subjects that are ``part_of`` it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[KnowledgeRuleKind.ENTITY_SUBTREE] = KnowledgeRuleKind.ENTITY_SUBTREE
    root_entity_id: UUID
    predicates: CanonicalStrings = ()
    layers: CanonicalCandidateLayers = (
        KnowledgeCandidateLayer.RELATIONS,
        KnowledgeCandidateLayer.OBSERVATIONS,
        KnowledgeCandidateLayer.CLAIMS,
    )


class PredicateBeatRuleParams(BaseModel):
    """Relations of one governed predicate, optionally pinned to either endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[KnowledgeRuleKind.PREDICATE_BEAT] = KnowledgeRuleKind.PREDICATE_BEAT
    predicate: str
    subject_entity_id: UUID | None = None
    object_entity_id: UUID | None = None


class DocSetRuleParams(BaseModel):
    """Evidence from one source family, with optional snapshot metadata filters.

    ``source_kind`` is required because it is the only document key represented
    by ``knowledge_rule_keys``. MIME, origin, and time are exact secondary SQL
    filters after that coarse key hit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[KnowledgeRuleKind.DOC_SET] = KnowledgeRuleKind.DOC_SET
    source_kind: str
    mime: str | None = None
    origin: Literal["external", "system_generated"] | None = None
    source_modified_from: UTCDateTime | None = None
    source_modified_until: UTCDateTime | None = None

    @model_validator(mode="after")
    def require_ordered_time_range(self) -> "DocSetRuleParams":
        """Reject a document window whose upper bound precedes its lower bound."""
        if (
            self.source_modified_from is not None
            and self.source_modified_until is not None
            and self.source_modified_until < self.source_modified_from
        ):
            raise ValueError(
                "source_modified_until must not precede source_modified_from"
            )
        return self


class ScopeInterestsRuleParams(BaseModel):
    """Delegate selection to the existing registry interests of one scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[KnowledgeRuleKind.SCOPE_INTERESTS] = KnowledgeRuleKind.SCOPE_INTERESTS
    scope_id: UUID


class ManualRuleParams(BaseModel):
    """An explicit editorial assignment of entities or evidence IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[KnowledgeRuleKind.MANUAL] = KnowledgeRuleKind.MANUAL
    entity_ids: CanonicalUUIDs = ()
    relation_ids: CanonicalUUIDs = ()
    observation_ids: CanonicalUUIDs = ()
    claim_ids: CanonicalUUIDs = ()
    doc_ids: CanonicalUUIDs = ()

    @model_validator(mode="after")
    def require_a_target(self) -> "ManualRuleParams":
        """Reject a manual rule that can never select anything."""
        if not any(
            (
                self.entity_ids,
                self.relation_ids,
                self.observation_ids,
                self.claim_ids,
                self.doc_ids,
            )
        ):
            raise ValueError("manual rule requires at least one target")
        return self


KnowledgeRuleParams = Annotated[
    EntityRuleParams
    | EntitySubtreeRuleParams
    | PredicateBeatRuleParams
    | DocSetRuleParams
    | ScopeInterestsRuleParams
    | ManualRuleParams,
    Field(discriminator="kind"),
]


class KnowledgePlanDecisionCreate(BaseModel):
    """One planner structure decision to append to the durable transcript."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: UUID
    deployment_id: UUID
    scope_id: UUID | None = None
    action: KnowledgePlanAction
    payload: dict[str, JsonValue]
    trigger: KnowledgePlanTrigger
    planner_version: str
    status: KnowledgePlanStatus = KnowledgePlanStatus.PROPOSED


class KnowledgeArtifactCreate(BaseModel):
    """The Postgres handle for one Plane-K git path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: UUID
    deployment_id: UUID
    layer: KnowledgeLayer
    page_kind: KnowledgePageKind
    git_path: str
    scope_id: UUID | None = None
    parent_artifact_id: UUID | None = None
    curation_path: str | None = None
    artifact_kind: str | None = None
    writer_version: str | None = None


class KnowledgePageRuleCreate(BaseModel):
    """One page-owned routing rule and the plan decision that authorized it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: UUID
    deployment_id: UUID
    artifact_id: UUID
    plan_decision_id: UUID
    params: KnowledgeRuleParams


class KnowledgeRuleKey(BaseModel):
    """One materialized coarse key for routing changed evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: KnowledgeRuleKeyKind
    value: str


class KnowledgeCitation(BaseModel):
    """One citation with exactly one schema-supported evidence target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: KnowledgeEvidenceRole
    claim_lineage_id: UUID | None = None
    claim_chunk_content_hash: str | None = None
    relation_id: UUID | None = None
    doc_id: UUID | None = None

    @model_validator(mode="after")
    def require_exactly_one_target(self) -> "KnowledgeCitation":
        """Mirror the ``knowledge_artifact_evidence`` exactly-one CHECK."""
        claim_coordinate_present = self.claim_lineage_id is not None
        if claim_coordinate_present != (self.claim_chunk_content_hash is not None):
            raise ValueError("claim citation requires its complete stable coordinate")
        if (
            sum(
                target
                for target in (
                    claim_coordinate_present,
                    self.relation_id is not None,
                    self.doc_id is not None,
                )
            )
            != 1
        ):
            raise ValueError("citation requires exactly one evidence target")
        return self


class KnowledgeEvidenceTarget(BaseModel):
    """One role-independent claim, relation, or document exclusion target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_lineage_id: UUID | None = None
    claim_chunk_content_hash: str | None = None
    relation_id: UUID | None = None
    doc_id: UUID | None = None

    @model_validator(mode="after")
    def require_exactly_one_target(self) -> "KnowledgeEvidenceTarget":
        """Keep exclusions at the same exactly-one evidence grain as citations."""
        claim_coordinate_present = self.claim_lineage_id is not None
        if claim_coordinate_present != (self.claim_chunk_content_hash is not None):
            raise ValueError("claim exclusion requires its complete stable coordinate")
        if (
            sum(
                target
                for target in (
                    claim_coordinate_present,
                    self.relation_id is not None,
                    self.doc_id is not None,
                )
            )
            != 1
        ):
            raise ValueError("evidence target requires exactly one target")
        return self


class KnowledgeCompilationWrite(BaseModel):
    """Validated compile metadata to commit atomically to the control plane."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    compilation_id: UUID
    deployment_id: UUID
    artifact_id: UUID
    inputs_hash: SHA256
    candidate_count: int = Field(ge=0)
    uncited_count: int = Field(ge=0)
    claims_cut_count: int = Field(default=0, ge=0)
    citations: tuple[KnowledgeCitation, ...]
    suggestions: tuple["KnowledgeWriterSuggestion", ...] = ()
    evidence_invalidated: int = Field(default=0, ge=0)
    writer_version: str
    page_summary: str
    content_hash: SHA256
    tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    session_transcript_uri: str | None = None

    @model_validator(mode="after")
    def require_bounded_uncited_count(self) -> "KnowledgeCompilationWrite":
        """Keep the offered-but-unused count within the candidate manifest."""
        if self.uncited_count > self.candidate_count:
            raise ValueError("uncited_count cannot exceed candidate_count")
        return self


class KnowledgeCompilationFailure(BaseModel):
    """One visible terminal writer attempt that never became publishable output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    compilation_id: UUID
    deployment_id: UUID
    artifact_id: UUID
    inputs_hash: SHA256
    candidate_count: int = Field(ge=0)
    claims_cut_count: int = Field(default=0, ge=0)
    writer_version: str = Field(min_length=1)
    failure: str = Field(min_length=1)
    session_transcript_uri: str | None = None


class KnowledgeFactFingerprint(BaseModel):
    """The D45 state of one relation or observation candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["relation", "observation"]
    fact_id: UUID
    valid_from: UTCDateTime | None = None
    valid_until: UTCDateTime | None = None
    invalidated_at: UTCDateTime | None = None
    evidence_count: int = Field(ge=0)
    contradict_count: int = Field(ge=0)
    contradiction_group: UUID | None = None


class KnowledgeClaimFingerprint(BaseModel):
    """Stable D54 claim grain: document lineage plus chunk content, never claim ID."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lineage_id: UUID
    chunk_content_hash: str


class KnowledgeRuleConfiguration(BaseModel):
    """The hash-visible configuration of one active page rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: UUID
    kind: KnowledgeRuleKind
    params: dict[str, JsonValue]


class KnowledgeInputSnapshot(BaseModel):
    """Every deterministic input that decides whether a compiled page is stale."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    facts: tuple[KnowledgeFactFingerprint, ...] = ()
    claims: tuple[KnowledgeClaimFingerprint, ...] = ()
    rules: tuple[KnowledgeRuleConfiguration, ...] = ()
    curation_hash: str | None = None
    child_summary_hashes: tuple[str, ...] = ()
    shared_model_summary_hash: str | None = None
    writer_version: str


class KnowledgeFactSheetFact(BaseModel):
    """One display-grade relation or observation selected by a page rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["relation", "observation"]
    fact_id: UUID
    label: str = Field(min_length=1)
    valid_from: UTCDateTime | None = None
    valid_until: UTCDateTime | None = None
    ingested_at: UTCDateTime
    invalidated_at: UTCDateTime | None = None
    evidence_count: int = Field(ge=0)
    contradict_count: int = Field(ge=0)
    contradiction_group: UUID | None = None


class KnowledgeFactSheetSnapshot(BaseModel):
    """One repeatable-read rule result ready for deterministic rendering."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: UUID
    deployment_id: UUID
    evidence_as_of: UTCDateTime
    input_snapshot: KnowledgeInputSnapshot
    facts: tuple[KnowledgeFactSheetFact, ...]


class KnowledgeRenderedFactSheet(BaseModel):
    """Rendered band plus the exact counts used by deterministic summaries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    markdown: str
    current_relation_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    contradiction_group_count: int = Field(ge=0)


class KnowledgeWriterFactReference(BaseModel):
    """One claim-to-fact evidence edge exposed inside a writer bundle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["relation", "observation"]
    fact_id: UUID
    stance: Literal["supports", "contradicts"]


class KnowledgeWriterClaim(BaseModel):
    """One current claim body carrying its stable D54 candidate coordinate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: UUID
    lineage_id: UUID
    chunk_content_hash: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    source_span: str = Field(min_length=1)
    document_title: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    fact_references: tuple[KnowledgeWriterFactReference, ...] = ()


class KnowledgeWriterClaimGroup(BaseModel):
    """All current claims at one stable lineage-plus-chunk candidate coordinate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lineage_id: UUID
    chunk_content_hash: str = Field(min_length=1)
    claims: tuple[KnowledgeWriterClaim, ...] = Field(min_length=1)


class KnowledgeWriterBundle(BaseModel):
    """Exact fact candidates plus the deterministic capped claim offering."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_sheet: KnowledgeFactSheetSnapshot
    claim_groups: tuple[KnowledgeWriterClaimGroup, ...] = ()
    claim_candidate_count: int = Field(ge=0)
    claims_cut_count: int = Field(ge=0)

    @model_validator(mode="after")
    def require_honest_claim_cut(self) -> "KnowledgeWriterBundle":
        """Make the capped bundle account for every rule-matched claim coordinate."""
        if len(self.claim_groups) + self.claims_cut_count != self.claim_candidate_count:
            raise ValueError("offered and cut claim counts must equal claim candidates")
        return self


class KnowledgeWriterCoverage(BaseModel):
    """Mechanical offered/cited/uncited candidate accounting for one writer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_count: int = Field(ge=0)
    cited_candidate_count: int = Field(ge=0)
    uncited_count: int = Field(ge=0)

    @model_validator(mode="after")
    def require_complete_partition(self) -> "KnowledgeWriterCoverage":
        """Partition every offered candidate into cited or uncited exactly once."""
        if self.cited_candidate_count + self.uncited_count != self.candidate_count:
            raise ValueError("cited and uncited counts must partition candidates")
        return self


class KnowledgeWriterSuggestion(BaseModel):
    """A writer-proposed planner input that WP-6.4 records but never applies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: KnowledgePlanAction
    rationale: str = Field(min_length=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class KnowledgeAgentSandboxPolicy(BaseModel):
    """The binding D52 limits applied to one stock-harness Plane-K session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    network_access: Literal["none"] = "none"
    memory_access: Literal["read_only"] = "read_only"
    repository_write_access: Literal[False] = False
    accepted_output_paths: tuple[str, ...]

    @field_validator("accepted_output_paths")
    @classmethod
    def require_safe_declared_outputs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep each worker's declared output surface normalized and inside output/."""
        if not value or len(set(value)) != len(value):
            raise ValueError("agent output paths must be non-empty and unique")
        for raw_path in value:
            path = PurePosixPath(raw_path)
            if (
                path.is_absolute()
                or ".." in path.parts
                or str(path) != raw_path
                or not raw_path.startswith("output/")
            ):
                raise ValueError("agent output path must be normalized under output/")
        return value


class KnowledgeWriterSandboxPolicy(KnowledgeAgentSandboxPolicy):
    """The fixed declared output surface of one prose-writer session."""

    accepted_output_paths: tuple[str, ...] = KNOWLEDGE_WRITER_OUTPUT_PATHS

    @field_validator("accepted_output_paths")
    @classmethod
    def require_declared_writer_outputs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Prevent runtime configuration from widening the writer's output surface."""
        if value != KNOWLEDGE_WRITER_OUTPUT_PATHS:
            raise ValueError("accepted writer output paths are fixed by contract")
        return value


class KnowledgeAgentSessionRequest(BaseModel):
    """One isolated stock-harness invocation prepared by a Plane-K worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: UUID
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)
    input_files: dict[str, str]
    mounts: PublishedMounts
    sandbox: KnowledgeAgentSandboxPolicy

    @field_validator("input_files")
    @classmethod
    def require_safe_input_paths(cls, value: dict[str, str]) -> dict[str, str]:
        """Keep compiler-prepared inputs normalized and outside the output surface."""
        for raw_path in value:
            path = PurePosixPath(raw_path)
            if (
                not raw_path
                or path.is_absolute()
                or ".." in path.parts
                or str(path) != raw_path
                or raw_path.startswith("output/")
            ):
                raise ValueError("agent input path must be normalized and read-only")
        return value


class KnowledgeWriterSessionRequest(KnowledgeAgentSessionRequest):
    """One prose-writer invocation with the fixed writer output contract."""

    sandbox: KnowledgeAgentSandboxPolicy = Field(
        default_factory=KnowledgeWriterSandboxPolicy
    )

    @model_validator(mode="after")
    def require_writer_output_contract(self) -> "KnowledgeWriterSessionRequest":
        """Reject callers that replace the fixed prose-writer output surface."""
        if self.sandbox.accepted_output_paths != KNOWLEDGE_WRITER_OUTPUT_PATHS:
            raise ValueError("accepted writer output paths are fixed by contract")
        return self


class KnowledgeAgentSessionResult(BaseModel):
    """Raw declared files and complete process transcript from one harness session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: UUID
    exit_code: int | None
    timed_out: bool = False
    output_files: dict[str, str] = Field(default_factory=dict)
    transcript: str = Field(min_length=1)
    tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_consistent_terminal_state(self) -> "KnowledgeWriterSessionResult":
        """Distinguish a timeout from an ordinary process exit unambiguously."""
        if self.timed_out == (self.exit_code is not None):
            raise ValueError("timed-out sessions have no exit code; exited sessions do")
        return self


KnowledgeWriterSessionResult = KnowledgeAgentSessionResult


class KnowledgeCompileContext(BaseModel):
    """Git/model inputs supplied to the Postgres-owned manifest computation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    curation_hash: str | None = None
    shared_model_summary_hash: str | None = None
    writer_version: str


class KnowledgeCompileArtifact(BaseModel):
    """One compiled artifact as seen by the dependency scheduler."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: UUID
    deployment_id: UUID
    scope_id: UUID | None = None
    parent_artifact_id: UUID | None = None
    git_path: str
    curation_path: str | None = None
    artifact_kind: str | None = None
    page_summary: str | None = None
    content_hash: SHA256 | None = None
    stale: bool

    @field_validator("git_path", "curation_path")
    @classmethod
    def require_safe_markdown_path(cls, value: str | None) -> str | None:
        """Keep driver-owned writes relative, normalized, and Markdown-only."""
        if value is None:
            return None
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or str(path) != value
            or path.suffix != ".md"
        ):
            raise ValueError("git_path must be a normalized relative Markdown path")
        return value


class KnowledgePageCompileRequest(BaseModel):
    """Deterministic context passed to the future per-page compiler seam."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact: KnowledgeCompileArtifact
    child_summaries: dict[UUID, str] = Field(default_factory=dict)
    shared_model_summary: str | None = None
    curation_hash: str | None = None
    curation_markdown: str | None = None
    previous_markdown: str | None = None
    exclusions: tuple[KnowledgeEvidenceTarget, ...] = ()


class KnowledgePageCompileOutput(BaseModel):
    """Structured page output consumed and validated by the commit driver."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    compilation: KnowledgeCompilationWrite
    markdown: str


class KnowledgePendingCycle(BaseModel):
    """One recoverable publish batch whose Postgres finalize step is pending."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cycle_id: UUID
    deployment_id: UUID
    compilations: tuple[KnowledgeCompilationWrite, ...] = Field(min_length=1)


class KnowledgeCommitCycleResult(BaseModel):
    """Observable outcome of one locked checkout/compile/publish cycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    checkout_revision: str
    published_revision: str | None = None
    compiled_artifact_ids: tuple[UUID, ...] = ()
    recovered_cycle_ids: tuple[UUID, ...] = ()
    quarantined_artifact_ids: tuple[UUID, ...] = ()
    stamped_plan_decision_ids: tuple[UUID, ...] = ()
    registered_authored_artifact_ids: tuple[UUID, ...] = ()
    synced_authored_artifact_ids: tuple[UUID, ...] = ()
    authored_lint_flag_artifact_ids: tuple[UUID, ...] = ()


class KnowledgeEvidenceDelta(BaseModel):
    """Fact and document identifiers changed since the previous K cycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relation_ids: tuple[UUID, ...] = ()
    observation_ids: tuple[UUID, ...] = ()
    claim_ids: tuple[UUID, ...] = ()
    doc_ids: tuple[UUID, ...] = ()


class KnowledgeArtifactHash(BaseModel):
    """One artifact's recorded and freshly computed staleness keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: UUID
    recorded_hash: str | None
    computed_hash: str
