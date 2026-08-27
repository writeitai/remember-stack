"""Typed WP-6.6 authored-page declarations, review flags, and dispatches."""

from enum import StrEnum
from pathlib import PurePosixPath
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from rememberstack.model.knowledge import KnowledgeCitation
from rememberstack.model.knowledge import KnowledgeEvidenceDelta
from rememberstack.model.knowledge import KnowledgeLayer
from rememberstack.model.knowledge import KnowledgePageKind
from rememberstack.model.knowledge import KnowledgeRuleParams


def _safe_markdown_path(*, value: str, field: str) -> str:
    """Require one normalized repository-relative Markdown path."""
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or str(path) != value
        or path.suffix != ".md"
    ):
        raise ValueError(f"{field} must be a normalized relative Markdown path")
    return value


def _citation_key(*, citation: KnowledgeCitation) -> tuple[str, ...]:
    """Return one stable total-order key for a citation declaration."""
    return (
        citation.role.value,
        "" if citation.claim_lineage_id is None else str(citation.claim_lineage_id),
        citation.claim_chunk_content_hash or "",
        "" if citation.relation_id is None else str(citation.relation_id),
        "" if citation.doc_id is None else str(citation.doc_id),
    )


def _rule_key(*, rule: KnowledgeRuleParams) -> str:
    """Return one stable serialization key for a typed watch rule."""
    return rule.model_dump_json(exclude_none=True)


class KnowledgeAuthoredReviewReason(StrEnum):
    """Why an authored owner needs attention without an automatic rewrite."""

    EVIDENCE_CHANGED = "evidence_changed"
    PAGE_RECOMPILED = "page_recompiled"
    DECLARATION_MISSING = "declaration_missing"
    TOMBSTONE = "tombstone"


class KnowledgeSubscriptionStatus(StrEnum):
    """Lifecycle states of one registered workflow subscriber."""

    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class KnowledgeDispatchStatus(StrEnum):
    """Delivery mirror stored beside one append-only dispatch payload."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class KnowledgeAuthoredDeclaration(BaseModel):
    """The optional ``cites`` and ``watch`` declarations in one authored page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    citations: tuple[KnowledgeCitation, ...] | None = None
    watch_rules: tuple[KnowledgeRuleParams, ...] | None = None
    watched_page_paths: tuple[str, ...] | None = None

    @field_validator("citations")
    @classmethod
    def canonicalize_citations(
        cls, value: tuple[KnowledgeCitation, ...] | None
    ) -> tuple[KnowledgeCitation, ...] | None:
        """Deduplicate explicit citation coordinates deterministically."""
        if value is None:
            return None
        return tuple(sorted(set(value), key=lambda item: _citation_key(citation=item)))

    @field_validator("watch_rules")
    @classmethod
    def canonicalize_rules(
        cls, value: tuple[KnowledgeRuleParams, ...] | None
    ) -> tuple[KnowledgeRuleParams, ...] | None:
        """Deduplicate watch rules by their canonical typed representation."""
        if value is None:
            return None
        unique = {_rule_key(rule=item): item for item in value}
        return tuple(unique[key] for key in sorted(unique))

    @field_validator("watched_page_paths")
    @classmethod
    def canonicalize_page_paths(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        """Normalize and deduplicate page-watch paths."""
        if value is None:
            return None
        return tuple(
            sorted(
                {
                    _safe_markdown_path(value=path, field="watched_page_paths")
                    for path in value
                }
            )
        )


class KnowledgeAuthoredPageSync(BaseModel):
    """One authored Markdown file observed at an exact git revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    git_path: str
    markdown: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    git_revision: str = Field(min_length=1)
    declaration: KnowledgeAuthoredDeclaration
    layer: KnowledgeLayer = KnowledgeLayer.K1

    @field_validator("git_path")
    @classmethod
    def require_git_path(cls, value: str) -> str:
        """Keep authored files inside the repository Markdown namespace."""
        return _safe_markdown_path(value=value, field="git_path")


class KnowledgeAuthoredSyncResult(BaseModel):
    """What one checkout-to-Postgres authored synchronization changed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    registered_artifact_ids: tuple[UUID, ...] = ()
    synced_artifact_ids: tuple[UUID, ...] = ()
    lint_flag_artifact_ids: tuple[UUID, ...] = ()


class KnowledgeAuthoredPageSyncResult(BaseModel):
    """The atomic database outcome for one authored Markdown file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: UUID
    registered: bool = False
    content_changed: bool = False
    lint_flagged: bool = False


class KnowledgeSubscriptionCreate(BaseModel):
    """One workflow endpoint and its mechanically evaluated interests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    deployment_id: UUID
    name: str = Field(min_length=1)
    workflow_endpoint: str = Field(min_length=1)
    debounce_seconds: int = Field(gt=0)
    scope_id: UUID | None = None
    created_by: str = Field(min_length=1)
    rules: tuple[KnowledgeRuleParams, ...] = ()
    watched_page_paths: tuple[str, ...] = ()

    @field_validator("rules")
    @classmethod
    def canonicalize_subscription_rules(
        cls, value: tuple[KnowledgeRuleParams, ...]
    ) -> tuple[KnowledgeRuleParams, ...]:
        """Deduplicate subscription rules deterministically."""
        unique = {_rule_key(rule=item): item for item in value}
        return tuple(unique[key] for key in sorted(unique))

    @field_validator("watched_page_paths")
    @classmethod
    def canonicalize_subscription_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Normalize subscription page-watch paths."""
        return tuple(
            sorted(
                {
                    _safe_markdown_path(value=path, field="watched_page_paths")
                    for path in value
                }
            )
        )


class KnowledgeAuthoredReviewPayload(BaseModel):
    """A mergeable delta carried by a standing authored-review flag or dispatch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reasons: tuple[KnowledgeAuthoredReviewReason, ...] = Field(min_length=1)
    delta: KnowledgeEvidenceDelta = Field(default_factory=KnowledgeEvidenceDelta)
    page_refs: tuple[str, ...] = ()
    citations_added: tuple[str, ...] = ()
    citations_removed: tuple[str, ...] = ()
    evidence_invalidated: int = Field(default=0, ge=0)
    redaction_required: bool = False

    @field_validator("reasons")
    @classmethod
    def canonicalize_reasons(
        cls, value: tuple[KnowledgeAuthoredReviewReason, ...]
    ) -> tuple[KnowledgeAuthoredReviewReason, ...]:
        """Keep merged flag reasons set-valued and deterministic."""
        return tuple(sorted(set(value), key=lambda item: item.value))

    @field_validator("page_refs", "citations_added", "citations_removed")
    @classmethod
    def canonicalize_strings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep payload identifier collections deterministic."""
        return tuple(sorted(set(value)))


class KnowledgeNotificationResult(BaseModel):
    """IDs of authored flags and subscription batches affected by one route."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authored_artifact_ids: tuple[UUID, ...] = ()
    dispatch_ids: tuple[UUID, ...] = ()


class KnowledgeAuthoredReviewState(BaseModel):
    """Reader-facing warning state for one authored page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: UUID
    open_flag_count: int = Field(ge=0)
    redaction_required: bool = False
    payloads: tuple[KnowledgeAuthoredReviewPayload, ...] = ()


class KnowledgeDispatchRecord(BaseModel):
    """One claimed subscriber delivery read from the append-only dispatch ledger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dispatch_id: UUID
    deployment_id: UUID
    subscription_id: UUID
    workflow_endpoint: str
    payload: KnowledgeAuthoredReviewPayload
    status: KnowledgeDispatchStatus


class KnowledgeDispatchMaterialization(BaseModel):
    """One due dispatch and its idempotent D67 processing row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dispatch_id: UUID
    processing_id: UUID
    created: bool


class KnowledgeArtifactPathState(BaseModel):
    """Git path ownership needed by checkout synchronization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: UUID
    git_path: str
    page_kind: KnowledgePageKind
    curation_path: str | None = None


class KnowledgeWorkflowDelivery(BaseModel):
    """The idempotent delta-carrying call made to an external workflow endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dispatch_id: UUID
    workflow_endpoint: str
    payload: KnowledgeAuthoredReviewPayload


def merge_knowledge_deltas(
    *, left: KnowledgeEvidenceDelta, right: KnowledgeEvidenceDelta
) -> KnowledgeEvidenceDelta:
    """Union two evidence deltas without losing their typed grains."""
    return KnowledgeEvidenceDelta(
        relation_ids=tuple(
            sorted(set(left.relation_ids).union(right.relation_ids), key=str)
        ),
        observation_ids=tuple(
            sorted(set(left.observation_ids).union(right.observation_ids), key=str)
        ),
        claim_ids=tuple(sorted(set(left.claim_ids).union(right.claim_ids), key=str)),
        doc_ids=tuple(sorted(set(left.doc_ids).union(right.doc_ids), key=str)),
    )


def merge_authored_review_payloads(
    *, left: KnowledgeAuthoredReviewPayload, right: KnowledgeAuthoredReviewPayload
) -> KnowledgeAuthoredReviewPayload:
    """Coalesce two notification payloads into one deterministic debounce batch."""
    return KnowledgeAuthoredReviewPayload(
        reasons=(*left.reasons, *right.reasons),
        delta=merge_knowledge_deltas(left=left.delta, right=right.delta),
        page_refs=(*left.page_refs, *right.page_refs),
        citations_added=(*left.citations_added, *right.citations_added),
        citations_removed=(*left.citations_removed, *right.citations_removed),
        evidence_invalidated=left.evidence_invalidated + right.evidence_invalidated,
        redaction_required=left.redaction_required or right.redaction_required,
    )
