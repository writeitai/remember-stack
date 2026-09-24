"""Typed models for the remember memory client (D62/D65).

Dependency-light: standard library and Pydantic only.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import UTC
from enum import StrEnum
from typing import Annotated
from typing import Final
from typing import Literal
from typing import Self
from typing import TypeAlias
from uuid import UUID

from pydantic import AfterValidator
from pydantic import AwareDatetime
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import JsonValue
from pydantic import model_validator

# ---------------------------------------------------------------------------
# Helpers & UTC DateTime
# ---------------------------------------------------------------------------


def _require_utc(value: datetime) -> datetime:
    """Require an aware datetime whose UTC offset is exactly zero."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return value


UTCDateTime: TypeAlias = Annotated[
    datetime, Field(strict=True), AfterValidator(_require_utc)
]


# ---------------------------------------------------------------------------
# Client Data Plane & Readiness Models (D36/D37/D62)
# ---------------------------------------------------------------------------

_SECRET_CONFIGURATION_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "credential",
        "credentials",
        "password",
        "refreshtoken",
        "secret",
        "token",
    }
)


def _find_secret_key(value: object) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = key.casefold().replace("-", "").replace("_", "")
            if normalized in _SECRET_CONFIGURATION_KEYS:
                return key
            found = _find_secret_key(nested)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _find_secret_key(nested)
            if found is not None:
                return found
    return None


class ToolDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    description: str
    input_schema: dict[str, object]
    result_schema: dict[str, object]
    result_contract: str = Field(min_length=1)
    output_grain: str | None
    answer_intent: str
    mutates: bool | None = None
    version: int | None = Field(default=None, ge=1)
    implementation_plan_hash: str | None = Field(
        default=None, min_length=64, max_length=64
    )


class PipelineStageReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    stage: str
    component_version: str
    status: Literal[
        "missing", "pending", "running", "succeeded", "failed", "dead_letter", "skipped"
    ]
    finished_at: datetime | None = None


class VersionPipelineReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version_id: UUID
    ready: bool
    stages: tuple[PipelineStageReadiness, ...]


DocumentStatus = Literal[
    "ingesting", "converting", "structuring", "ready", "failed", "deleted"
]
DocumentStatusFilter = Literal[
    "ingesting", "converting", "structuring", "ready", "failed"
]


class DocumentVersionSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version_id: UUID
    version_no: int
    status: DocumentStatus
    ingested_at: datetime
    error: str | None = None


class DocumentSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    doc_id: UUID
    title: str | None = None
    source_kind: str
    source_uri: str | None = None
    first_seen_at: datetime
    latest: DocumentVersionSummary
    serving: bool


class DocumentPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    documents: tuple[DocumentSummary, ...]
    cursor: str | None = None


class DocumentDeletion(BaseModel):
    """What deleting one document changed in the live memory.

    The counts describe this call. A call that finishes a deletion another
    path started reports only the work it finished.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    doc_id: UUID
    deleted_at: datetime
    claims_retired: int = Field(ge=0)
    relations_closed: int = Field(ge=0)
    observations_closed: int = Field(ge=0)


DOCUMENT_SEARCH_MAX_K: Final = 200
DOCUMENT_SEARCH_DEFAULT_K: Final = 20


class DocumentSearchFilters(BaseModel):
    """General document metadata filters (D134 §3); every one given must hold.

    ``authors`` and ``recipients`` match a person when any listed term equals
    their normalized address or appears as whole words in their normalized
    name (lower case, accents removed): ``"alice"`` matches "Alice Novák".
    Date ranges are inclusive and exclude documents that do not declare the
    date.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    family: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    recipients: tuple[str, ...] = ()
    created_from: AwareDatetime | None = None
    created_to: AwareDatetime | None = None
    modified_from: AwareDatetime | None = None
    modified_to: AwareDatetime | None = None
    language: str | None = None
    thread_ref: str | None = None
    doc_ids: tuple[UUID, ...] = ()


class DocumentSearchRequest(BaseModel):
    """One ``search_documents`` call.

    With a ``query`` the results are ranked by name and content matches and
    there is no cursor. Without one they are every document the filters
    match, newest declared creation date first, paged by ``cursor``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    query: str | None = Field(default=None, min_length=1, max_length=4096)
    filters: DocumentSearchFilters = DocumentSearchFilters()
    versions: Literal["current", "all"] = "current"
    k: int = Field(default=DOCUMENT_SEARCH_DEFAULT_K, ge=1, le=DOCUMENT_SEARCH_MAX_K)
    cursor: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def cursor_pages_filters_only(self) -> Self:
        """A ranked query has no stable order to page, so it takes no cursor."""
        if self.query is not None and self.cursor is not None:
            raise ValueError("cursor pages filter-only searches; drop query or cursor")
        return self


class DocumentSearchPerson(BaseModel):
    """One author or recipient as the document declares them."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str | None = None
    address: str | None = None


class DocumentSearchResult(BaseModel):
    """One matching document, described by the version it was judged by."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    doc_id: UUID
    version_id: UUID
    version_no: int
    status: DocumentStatus
    lineage_title: str | None = None
    file_name: str | None = None
    title: str | None = None
    source_path: str | None = None
    family: str
    created_at: datetime | None = None
    modified_at: datetime | None = None
    language: str | None = None
    thread_ref: str | None = None
    authors: tuple[DocumentSearchPerson, ...] = ()
    recipients: tuple[DocumentSearchPerson, ...] = ()
    extra: dict[str, JsonValue] = Field(default_factory=dict)
    overview: str | None = None
    other_matching_version_ids: tuple[UUID, ...] = ()
    matched_by: tuple[Literal["name", "content"], ...] = ()
    score: float | None = None


class DocumentPeopleMatch(BaseModel):
    """One distinct person an authors/recipients filter matched."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    role: Literal["author", "recipient"]
    name: str | None = None
    address: str | None = None
    documents: int = Field(ge=0)


class DocumentSearchPage(BaseModel):
    """A page of ``search_documents`` results."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    documents: tuple[DocumentSearchResult, ...]
    cursor: str | None = None
    as_of: datetime
    people_matched: tuple[DocumentPeopleMatch, ...] = ()


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4096)
    k: int = Field(default=10, ge=1, le=400)
    channel: Literal["semantic", "bm25"] = "semantic"


ADJACENT_CHUNKS_MIN_WINDOW: Final = 1
ADJACENT_CHUNKS_MAX_WINDOW: Final = 2


class AdjacentChunksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: UUID
    window: int = Field(
        default=1, ge=ADJACENT_CHUNKS_MIN_WINDOW, le=ADJACENT_CHUNKS_MAX_WINDOW
    )


class ReadinessRequirements(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    pipeline: bool
    p1: bool
    live_graph: bool
    p3: bool


class CapabilityReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    required: bool
    ready: bool
    checked_at: datetime
    reason: str
    version: str | None = None
    built_at: datetime | None = None
    published_at: datetime | None = None


class PipelineReadinessReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ready: bool
    versions: tuple[VersionPipelineReadiness, ...]
    capabilities: dict[
        Literal["pipeline", "p1", "live_graph", "p3"], CapabilityReadiness
    ]
    document_binding_generation: str | None = Field(default=None)
    model_bindings: dict[str, str] = Field(default_factory=dict)
    build_revision: str = Field(default="")


class DeploymentBuildInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    build_revision: str = Field(default="")
    model_bindings: dict[str, str] = Field(default_factory=dict)
    document_binding_generation: str | None = Field(default=None)
    # Catalogue tool name -> tool_version for every memory tool this deployment
    # serves (D136). A host renders a tool only at an equal version.
    tools: dict[str, int] = Field(default_factory=dict)


class ConnectorCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    credential_ref: str | None = None

    @model_validator(mode="after")
    def _credentials_are_references(self) -> Self:
        secret_key = _find_secret_key(self.configuration)
        if secret_key is not None:
            raise ValueError(
                f"configuration field {secret_key!r} looks like a credential;"
                " store it deployment-side and use credential_ref"
            )
        return self


class ConnectorDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    connector_id: UUID
    kind: str
    name: str
    status: Literal["active", "paused", "error"]
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    credential_ref: str | None = None
    message: str | None = None


class IngestedVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    deployment_id: UUID
    doc_id: UUID
    version_id: UUID
    content_hash: str
    created: bool
    # The engine always sets these three. They default to None only so this
    # client still parses receipts from released engines that predate them
    # (the client-vs-engine compatibility matrix).
    mime: str | None = None
    """The MIME type recorded for these bytes, which conversion uses."""
    title: str | None = None
    """The document's title. Set by the first ingest of the lineage."""
    versioning_mode: Literal["snapshot", "living"] | None = None
    """The lineage's versioning mode. Set by the first ingest of the lineage."""
    parked: Literal["no_route"] | None = None
    """``no_route`` when conversion is parked waiting for a route for this MIME type.

    The original is stored, but it is not converted, searched or extracted
    until an operator adds a conversion route and releases the parked work.
    ``None`` means only that it is not parked for ``no_route``; processing
    state comes from readiness."""
    processing_admission: Literal["not_required", "pending"] = Field(
        default="not_required", exclude=True
    )


# ---------------------------------------------------------------------------
# Envelope & Retrieval Models (D48/D49/D54)
# ---------------------------------------------------------------------------


class Grain(StrEnum):
    FACT = "fact"
    EVIDENCE = "evidence"
    COMPILED = "compiled"
    COMPOSITE = "composite"


class NegativeKind(StrEnum):
    UNKNOWN_ENTITY = "unknown_entity"
    KNOWN_EMPTY = "known_empty"
    BOUNDARY = "boundary"


class IdentityRegime(StrEnum):
    CURRENT = "current"
    AS_OF = "as_of"


class CurrentTemporalScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["current"] = "current"
    evaluated_at: UTCDateTime
    believed_at: UTCDateTime
    identity_regime: IdentityRegime = IdentityRegime.CURRENT


class AtTemporalScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["at"] = "at"
    at: UTCDateTime
    evaluated_at: UTCDateTime
    believed_at: UTCDateTime
    identity_regime: IdentityRegime = IdentityRegime.CURRENT


class OverlapTemporalScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["overlap"] = "overlap"
    from_: UTCDateTime = Field(alias="from")
    to: UTCDateTime
    evaluated_at: UTCDateTime
    believed_at: UTCDateTime
    identity_regime: IdentityRegime = IdentityRegime.CURRENT

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.to < self.from_:
            raise ValueError("temporal scope 'to' must be at or after 'from'")
        return self


class HistoryTemporalScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["history"] = "history"
    evaluated_at: UTCDateTime
    believed_at: UTCDateTime
    identity_regime: IdentityRegime = IdentityRegime.CURRENT


class AsOfTemporalScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["as_of"] = "as_of"
    valid_at: UTCDateTime
    evaluated_at: UTCDateTime
    believed_at: UTCDateTime
    identity_regime: IdentityRegime = IdentityRegime.CURRENT


TemporalScope = Annotated[
    CurrentTemporalScope
    | AtTemporalScope
    | OverlapTemporalScope
    | HistoryTemporalScope
    | AsOfTemporalScope,
    Field(discriminator="mode"),
]


def current_temporal_scope(
    *, evaluated_at: datetime | None = None
) -> CurrentTemporalScope:
    instant = evaluated_at or datetime.now(UTC)
    return CurrentTemporalScope(evaluated_at=instant, believed_at=instant)


class FactSupport(StrEnum):
    CURRENT = "current"
    WITHDRAWN = "withdrawn"


class ClaimValidPrecision(StrEnum):
    """How narrow an author's stated validity window was (D48)."""

    UNKNOWN = "unknown"
    INSTANT = "instant"
    DAY = "day"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    OPEN = "open"


class TemporalMatch(StrEnum):
    """Whether a fact definitely or possibly overlapped the query window."""

    CONFIRMED = "confirmed"
    POSSIBLE = "possible"


class Negative(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: NegativeKind
    explanation: Annotated[str, Field(min_length=1)]
    workaround: str | None = None


class Validity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    valid_from: UTCDateTime | None
    valid_until: UTCDateTime | None
    valid_precision: ClaimValidPrecision = ClaimValidPrecision.UNKNOWN
    ingested_at: UTCDateTime
    invalidated_at: UTCDateTime | None


class KFreshness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    compiled_at: UTCDateTime | None = None
    stale: bool = False
    open_flags: int = Field(default=0, ge=0)


class Freshness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    pg_live_ts: UTCDateTime
    p1_written_inline: bool = True
    p1_believed_at_horizon: UTCDateTime | None = None
    k: KFreshness | None = None


class EntityCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entity_id: UUID
    canonical_name: str
    tier: str
    context_hits: int = 0


class CoMember(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    fact_id: UUID
    label: str
    evidence_count: int
    validity: Validity


class Contradiction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    group_id: UUID
    co_members: tuple[CoMember, ...] = ()
    returned: int = Field(ge=0)
    total: int = Field(ge=0)
    continuation: str | None = None


class FactResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    fact_id: UUID
    kind: str
    label: str
    evidence_count: int
    validity: Validity
    temporal_match: TemporalMatch = TemporalMatch.POSSIBLE
    contradiction_group: UUID | None = None
    contradiction: Contradiction | None = None
    support: FactSupport = FactSupport.CURRENT


class EvidenceSpan(BaseModel):
    """One half-open supporting range on the selected occurrence (D119)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)

    @model_validator(mode="after")
    def _end_after_start(self) -> Self:
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        return self


class EvidenceResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    claim_id: UUID
    doc_id: UUID
    chunk_id: UUID
    claim_text: str
    source_span: str
    char_start: int
    char_end: int
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    is_attributed: bool
    is_current_testimony: bool
    asserted_at: UTCDateTime | None = None
    claim_valid_from: UTCDateTime | None = None
    claim_valid_until: UTCDateTime | None = None
    claim_valid_precision: str = "unknown"
    claim_valid_kind: str | None = None
    document_title: str | None = None
    source_kind: str | None = None
    corroboration_count: int | None = Field(default=None, ge=1)
    grouped_claim_ids: tuple[UUID, ...] = ()


class FactEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    fact_kind: Literal["relation", "observation"]
    fact_id: UUID
    claim_id: UUID
    stance: Literal["supports", "contradicts"]


class EvidenceTotal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    fact_kind: Literal["relation", "observation"]
    fact_id: UUID
    stance: Literal["supports", "contradicts"]
    returned: int = Field(ge=0)
    total: int = Field(ge=0)

    @model_validator(mode="after")
    def _returned_does_not_exceed_total(self) -> Self:
        if self.returned > self.total:
            raise ValueError("returned evidence cannot exceed its exact total")
        return self


class ChunkEvidenceResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    chunk_id: UUID
    doc_id: UUID
    version_id: UUID
    representation_id: UUID
    chunk_text: str
    context_prefix: str | None = None
    char_start: int
    char_end: int
    section_role: str | None
    document_title: str | None = None
    source_kind: str
    source_modified_at: UTCDateTime | None = None
    published_at: UTCDateTime | None = None


class SourceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    doc_id: UUID
    title: str | None
    source_kind: str
    markdown_uri: str | None
    mention_count: int | None = Field(default=None, ge=0)
    first_mentioned_at: UTCDateTime | None = None
    last_mentioned_at: UTCDateTime | None = None


class TranscriptEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    subject_kind: str
    outcome: str
    method: str
    confidence: float | None
    related_id: UUID | None
    decided_by: str
    decided_at: UTCDateTime
    features: dict[str, object] | None


class GraphNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entity_id: UUID
    name: str
    hops: int = Field(ge=0)


class GraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    relation_id: UUID
    subject_id: UUID
    object_id: UUID
    predicate: str
    fact: str | None
    evidence_count: int
    valid_from: UTCDateTime | None
    valid_until: UTCDateTime | None
    valid_precision: ClaimValidPrecision = ClaimValidPrecision.UNKNOWN
    ingested_at: UTCDateTime | None
    invalidated_at: UTCDateTime | None
    support: FactSupport = FactSupport.CURRENT


class GraphPath(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    length: int = Field(ge=1)
    nodes: tuple[GraphNode, ...] = Field(min_length=2)
    edges: tuple[GraphEdge, ...] = Field(min_length=1)


class RankedItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    item_id: UUID
    score: float
    signals: dict[str, float] = Field(default_factory=dict)


class ChangeRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: str
    change: str
    id: UUID
    label: str | None
    at: UTCDateTime


class AggregateBucket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str | None
    count: int = Field(ge=0)
    entity_id: UUID | None = None


class AggregateReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    form: str
    buckets: tuple[AggregateBucket, ...] = ()
    total: int = Field(ge=0)
    bounded_by: str | None = None


class PageRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    artifact_id: UUID
    page_kind: str
    git_path: str | None
    page_summary: str | None
    last_compiled_at: UTCDateTime | None
    status: str
    stale: bool = False
    open_review_flags: int = Field(default=0, ge=0)
    redaction_required: bool = False


class ScanRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: str
    id: UUID
    label: str | None
    at: UTCDateTime | None = None


class Truncation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    truncated: bool
    returned: int = Field(ge=0)
    estimated_total: int = Field(ge=0)
    total_is_exact: bool = True
    continuation: str | None = None
    reason: str | None = None


class Envelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    grain: Grain
    temporal_scope: TemporalScope
    entities: tuple[EntityCandidate, ...] = ()
    facts: tuple[FactResult, ...] = ()
    evidence: tuple[EvidenceResult, ...] = ()
    fact_evidence: tuple[FactEvidence, ...] = ()
    evidence_totals: tuple[EvidenceTotal, ...] = ()
    chunks: tuple[ChunkEvidenceResult, ...] = ()
    sources: tuple[SourceRecord, ...] = ()
    transcript: tuple[TranscriptEntry, ...] = ()
    nodes: tuple[GraphNode, ...] = ()
    paths: tuple[GraphPath, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    ranking: tuple[RankedItem, ...] = ()
    changes: tuple[ChangeRecord, ...] = ()
    aggregate: AggregateReport | None = None
    pages: tuple[PageRef, ...] = ()
    freshness: Freshness
    truncation: Truncation | None = None
    dropped_by_hydration: int = 0
    excluded_unstamped: int = Field(default=0, ge=0)
    negative: Negative | None = None


class ContextBundleV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    contract: Literal["ContextBundle/v2"] = "ContextBundle/v2"
    claims_and_sources: Envelope
    facts: Envelope

    @model_validator(mode="after")
    def _child_grains_are_exact(self) -> Self:
        if self.claims_and_sources.grain is not Grain.EVIDENCE:
            raise ValueError("ContextBundle claims_and_sources must be evidence grain")
        if self.facts.grain is not Grain.FACT:
            raise ValueError("ContextBundle facts must be fact grain")
        return self


# ---------------------------------------------------------------------------
# Query Result Wrapper
# ---------------------------------------------------------------------------


class QueryResultDict(dict[str, object]):
    """Dictionary result wrapper providing attribute access (.rows, .columns, .truncated)."""

    @property
    def rows(self) -> list[dict[str, object]]:
        val = self.get("rows", [])
        return val if isinstance(val, list) else []  # type: ignore

    @property
    def columns(self) -> list[str]:
        val = self.get("columns", [])
        return val if isinstance(val, list) else []  # type: ignore

    @property
    def truncated(self) -> bool:
        return bool(self.get("truncated", False))
