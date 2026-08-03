"""The response envelope (D49): the answer's machine-readable self-account.

Every query-engine result carries its grain, validity, freshness stamps, the
nominate-then-drop honesty count (D48), and — when the answer is a "no" — a
typed negative from the fixed taxonomy (retrieval §5). The walking skeleton
carries the minimal envelope; the full contract grows on these same fields.
"""

from enum import StrEnum
from typing import Annotated
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from rememberstack.model.adjudication import TranscriptEntry
from rememberstack.model.queue import UTCDateTime


class Grain(StrEnum):
    """The D49 grain type-system: what kind of truth a result is."""

    FACT = "fact"
    EVIDENCE = "evidence"
    COMPILED = "compiled"
    COMPOSITE = "composite"


class NegativeKind(StrEnum):
    """The fixed negative-answer taxonomy (S29/S39/S55).

    Deliberately no `denied` kind: content-level authorization is a library
    non-goal (retrieval §9), and hard-deleted (forgotten) content is
    indistinguishable-from-never-existed (S55), so it surfaces as
    `unknown_entity`/`known_empty`, never a distinct kind. Freezing the
    taxonomy now is safe precisely because of these two omissions —
    retrofitting a kind onto a deployed API breaks consumers.
    """

    UNKNOWN_ENTITY = "unknown_entity"
    KNOWN_EMPTY = "known_empty"
    BOUNDARY = "boundary"


class IdentityRegime(StrEnum):
    """Which identity boundary answered a read (S61).

    `current` (the default) follows today's aliases and merge redirects even
    under a past `believed_at`; `as_of` means the identity boundary was
    reconstructed as it stood at the queried instant (the transcript-based
    `identity_as_of` recipe). The envelope always states which, so an audit
    read can never silently mix today's identities with yesterday's beliefs.
    """

    CURRENT = "current"
    AS_OF = "as_of"


class FactSupport(StrEnum):
    """Whether a fact still has current-testimony support (D54).

    `current` is the normal state; `withdrawn` means every source that
    asserted the fact has stopped (an open `support_withdrawn` review flag) —
    the fact is *flagged, not vanished*, so an agent sees the ground moved
    before planning against it. A withdrawn fact is still returned.
    """

    CURRENT = "current"
    WITHDRAWN = "withdrawn"


class Negative(BaseModel):
    """One typed 'no': each kind demands a different agent reaction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: NegativeKind
    explanation: Annotated[str, Field(min_length=1)]
    workaround: str | None = None


class Validity(BaseModel):
    """A result's bi-temporal state as hydration re-read it (D48)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid_from: UTCDateTime | None
    valid_until: UTCDateTime | None
    ingested_at: UTCDateTime
    invalidated_at: UTCDateTime | None


class KFreshness(BaseModel):
    """The compiled-grain honesty block (retrieval §5): a K page's timestamp.

    A compiled answer is pre-paid synthesis *with a timestamp*, so any answer
    that consumed a K page carries when it compiled, whether it is stale
    (inputs changed since), and how many evidence-change flags are still open
    against it — the reader-facing flag surface (k_layers spike 9). An agent
    sees "this page has 3 unresolved flags" before planning against it (S34).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    compiled_at: UTCDateTime | None = None
    stale: bool = False
    open_flags: int = Field(default=0, ge=0)


class Freshness(BaseModel):
    """Per-source freshness stamps (S42): what lag the answer could carry.

    Each contributing channel also exposes its **`believed_at` horizon**: the
    oldest system-time a query can reach before the channel can no longer
    answer. `None` means unbounded — under D69 the hot P2 relation view keeps
    every relation whose endpoints stay emitted, so P2's horizon is null.
    Whenever a horizon is finite, a `believed_at` before it must return a
    `boundary` (retrieval §3), never a silent truncation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pg_live_ts: UTCDateTime
    p1_written_inline: bool = True  # the skeleton writes P1 inline; a real
    # write-lag horizon replaces this constant with measurement (retrieval §5)
    p1_believed_at_horizon: UTCDateTime | None = None  # None = unbounded
    p2_snapshot_version: str | None = None  # which graph snapshot answered
    p2_snapshot_ts: UTCDateTime | None = None
    p2_believed_at_horizon: UTCDateTime | None = None  # None = unbounded (D69)
    k: KFreshness | None = None  # present only when the answer consumed a K page


class EntityCandidate(BaseModel):
    """One ranked resolve candidate (never a silent guess, S51)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: UUID
    canonical_name: str
    type: str
    tier: str  # which resolution tier surfaced it (T0 in the skeleton)
    context_hits: int = 0


class CoMember(BaseModel):
    """One other side of a contradiction, surfaced with the fact (S23).

    A light record — enough to see the competing claim and hydrate it — so a
    contradiction block can carry several sides without recursion.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: UUID
    label: str
    evidence_count: int
    validity: Validity


class Contradiction(BaseModel):
    """The S23 contract block: a fact's live contradiction, never one-sided.

    Returning one side of a live contradiction group without its others is a
    contract violation, not a ranking choice ("contradictions are surfaced,
    never silently resolved"). The bounded form: co-members come back INLINE
    up to a guaranteed cap (typical groups are 2–3 sides — both FY2023
    revenue figures together, each with its own evidence handle); beyond the
    cap the block still always carries `group_id`, `returned`, `total`, and a
    `continuation`. One-sided is never a valid answer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: UUID
    co_members: tuple[CoMember, ...] = ()
    returned: int = Field(ge=0)
    total: int = Field(ge=0)
    continuation: str | None = None


class FactResult(BaseModel):
    """One fact-grain record: a live relation or observation, hydrated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: UUID
    kind: str  # relation | observation
    label: str
    evidence_count: int
    validity: Validity
    contradiction_group: UUID | None = None  # the raw group id (S23)
    contradiction: Contradiction | None = None  # the surfaced co-members (S23)
    support: FactSupport = FactSupport.CURRENT  # D54: withdrawn is flagged, not gone


class EvidenceResult(BaseModel):
    """One evidence-grain record: a claim with its provenance anchors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: UUID
    doc_id: UUID
    chunk_id: UUID
    claim_text: str
    source_span: str
    char_start: int
    char_end: int
    is_attributed: bool
    is_current_testimony: bool
    asserted_at: UTCDateTime | None = None
    claim_valid_from: UTCDateTime | None = None
    claim_valid_until: UTCDateTime | None = None
    claim_valid_precision: str = "unknown"
    claim_valid_kind: str | None = None
    document_title: str | None = None
    source_kind: str | None = None


class FactEvidence(BaseModel):
    """One explicit fact-to-claim association in a flat compound answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: UUID
    claim_id: UUID
    stance: Literal["supports", "contradicts"]


class EvidenceTotal(BaseModel):
    """Exact evidence disclosure for one fact and one evidence stance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: UUID
    stance: Literal["supports", "contradicts"]
    returned: int = Field(ge=0)
    total: int = Field(ge=0)

    @model_validator(mode="after")
    def _returned_does_not_exceed_total(self) -> "EvidenceTotal":
        if self.returned > self.total:
            raise ValueError("returned evidence cannot exceed its exact total")
        return self


class ChunkEvidenceResult(BaseModel):
    """One live source chunk, distinct from an extracted claim or fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: UUID
    doc_id: UUID
    version_id: UUID
    representation_id: UUID
    chunk_text: str
    context_prefix: str | None = None
    char_start: int
    char_end: int
    section_role: str
    document_title: str | None = None
    source_kind: str
    source_modified_at: UTCDateTime | None = None
    published_at: UTCDateTime | None = None


class SourceRecord(BaseModel):
    """One hydrated source document handle (S5: down to the artifact URI)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: UUID
    title: str | None
    source_kind: str
    markdown_uri: str | None
    mention_count: int | None = Field(default=None, ge=0)
    first_mentioned_at: UTCDateTime | None = None
    last_mentioned_at: UTCDateTime | None = None


class GraphNode(BaseModel):
    """One entity the traversal reached, with its hop distance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: UUID
    name: str
    type: str
    hops: int = Field(ge=0)


class GraphEdge(BaseModel):
    """One traversed relation, carrying its bi-temporal state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relation_id: UUID
    subject_id: UUID
    object_id: UUID
    predicate: str
    fact: str | None
    evidence_count: int
    valid_from: UTCDateTime | None
    valid_until: UTCDateTime | None
    ingested_at: UTCDateTime | None
    invalidated_at: UTCDateTime | None
    support: FactSupport = FactSupport.CURRENT  # D54: withdrawn is flagged, not gone


class GraphPath(BaseModel):
    """One connection between two entities — a COMPOUND result.

    A path revalidates as a unit (S17/S21): if hydration drops any edge,
    the whole path drops, because a path with a hole is not a shorter
    path — it is a different (and false) claim about connection.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    length: int = Field(ge=1)
    nodes: tuple[GraphNode, ...] = Field(min_length=2)
    edges: tuple[GraphEdge, ...] = Field(min_length=1)


class RankedItem(BaseModel):
    """One item in a fused or reranked ordering (retrieval §3: `fuse`/`rerank`).

    `score` is the operator's output — the RRF sum for `fuse`, the signal
    value for `rerank` — and the tuple order IS the rank. `signals` keeps
    each contributing value visible, because the rerankers are meant to be
    inspectable stages (D9), not a black-box sort.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: UUID
    score: float
    signals: dict[str, float] = Field(default_factory=dict)


class ChangeRecord(BaseModel):
    """One entry in the `delta` change feed (S13/S14/S30).

    `kind` is what changed (relation | observation | claim | page) and
    `change` is how (new | invalidated | capped | recompiled). `at` is the
    instant that placed it in the feed — the ingestion, invalidation, or
    recompilation time the caller's `since` was compared against — so a
    follow-up `delta` can resume from the last `at` it saw.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str  # relation | observation | claim | page
    change: str  # new | invalidated | capped | recompiled
    id: UUID
    label: str | None
    at: UTCDateTime


class AggregateBucket(BaseModel):
    """One group in an enumerated aggregate (retrieval §9): a key and its count.

    `key` is the group label — a predicate, an object entity, a timeline
    period, or an entity id rendered as text — and `null` for the single
    bucket of a plain count. `entity_id` is populated when the group IS an
    entity (group-by-object, delta-top-entities, typed-absence), so the
    agent can hop straight to it without re-resolving the label.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str | None
    count: int = Field(ge=0)
    entity_id: UUID | None = None


class AggregateReport(BaseModel):
    """An enumerated aggregate's result: the form asked, and its buckets.

    Aggregation is enumerated, never general (retrieval §9): each `form`
    is a bounded SQL shape with a predictable cost. `total` is the sum
    across buckets (or the single count); `bounded_by` names the cap when
    the shape rides a bounded feed (e.g. delta-top-entities), so a reader
    knows the ranking is over the window, not all of history.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    form: str
    buckets: tuple[AggregateBucket, ...] = ()
    total: int = Field(ge=0)
    bounded_by: str | None = None


class PageRef(BaseModel):
    """One K page the `pages_about` discovery index reports (S31/S45).

    The rule-key inverted index that routes writes, read backwards: which
    pages exist about an entity or key. `stale` mirrors the refresh state —
    a page whose inputs changed but has not recompiled — so discovery never
    presents an out-of-date page as fresh without saying so.
    """

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
    """One row of a `scan` batch export (S53): id, kind, label, feed instant.

    The batch surface streams the same zero-LLM reads as the interactive
    primitives under a separate resource pool (retrieval §9). A row is
    deliberately minimal — id plus enough to route a hydrate — because a
    scan is an export to a compiler or auditor, not a rendered answer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    id: UUID
    label: str | None
    at: UTCDateTime | None = None


class Truncation(BaseModel):
    """The explicit cap marker (S18/S49): no silent top-k ever.

    ``estimated_total`` is what the traversal could see before the cap;
    ``continuation`` carries the opaque cursor a follow-up call passes back.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    truncated: bool
    returned: int = Field(ge=0)
    estimated_total: int = Field(ge=0)
    total_is_exact: bool = True  # false when the count itself hit its cap
    continuation: str | None = None


class EnvelopePart(BaseModel):
    """One single-grain section of a composite answer (S47).

    A mixed answer — S47's "everything Alice *said* about pricing, plus what
    we *believe*" — is EXPLICITLY two-part, never blended: each part carries
    its own grain and its own single-grain results, so the fact/evidence
    discipline is never diluted. Single-grain answers skip `parts` and read
    flat off the top-level fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    grain: Grain  # strictly single-grain: fact | evidence | compiled (S47)
    label: str | None = None  # e.g. "said" vs "believed"
    facts: tuple[FactResult, ...] = ()
    evidence: tuple[EvidenceResult, ...] = ()
    chunks: tuple[ChunkEvidenceResult, ...] = ()
    sources: tuple[SourceRecord, ...] = ()
    nodes: tuple[GraphNode, ...] = ()
    aggregate: AggregateReport | None = None
    pages: tuple[PageRef, ...] = ()
    truncation: Truncation | None = None

    @model_validator(mode="after")
    def _payload_matches_grain(self) -> "EnvelopePart":
        """A part is strictly single-grain: it carries only the payload its
        own grain owns — a fact part holds facts (and graph nodes / an
        aggregate), an evidence part holds claims, a compiled part holds K
        pages. Carrying another grain's payload, or being composite, is the
        blending S47 forbids. `sources` (hydration handles) is cross-grain."""
        if self.grain is Grain.COMPOSITE:
            raise ValueError("a composite part is not single-grain (S47)")
        owned = {
            Grain.FACT: {"facts", "nodes", "aggregate"},
            Grain.EVIDENCE: {"evidence", "chunks"},
            Grain.COMPILED: {"pages"},
        }[self.grain]
        populated = {
            name
            for name, value in (
                ("facts", self.facts),
                ("evidence", self.evidence),
                ("chunks", self.chunks),
                ("nodes", self.nodes),
                ("aggregate", self.aggregate),
                ("pages", self.pages),
            )
            if value
        }
        stray = populated - owned
        if stray:
            raise ValueError(
                f"a {self.grain.value}-grain part carries {sorted(stray)}"
                " belonging to another grain (S47)"
            )
        return self


class Envelope(BaseModel):
    """The D49 envelope: results plus the answer's machine-readable self-account.

    A single-grain answer (the common case) reads flat: the top-level
    `grain` and the matching result tuple. A `composite` answer sets
    `grain = composite` and carries `parts[]`, each strictly single-grain
    (S47) — so a caller never has to disentangle blended grains.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    grain: Grain
    parts: tuple["EnvelopePart", ...] = ()  # S47: composite ⇒ read parts[]
    as_of_valid_at: UTCDateTime | None = None  # echo of the applied valid_at
    as_of_believed_at: UTCDateTime | None = None  # echo of the applied believed_at
    identity_regime: IdentityRegime = IdentityRegime.CURRENT  # S61: which regime
    entities: tuple[EntityCandidate, ...] = ()
    facts: tuple[FactResult, ...] = ()
    evidence: tuple[EvidenceResult, ...] = ()
    fact_evidence: tuple[FactEvidence, ...] = ()  # explicit fact/claim/stance links
    evidence_totals: tuple[EvidenceTotal, ...] = ()  # exact per-fact stance counts
    chunks: tuple[ChunkEvidenceResult, ...] = ()
    sources: tuple[SourceRecord, ...] = ()
    transcript: tuple["TranscriptEntry", ...] = ()  # S8: the audit surface
    nodes: tuple[GraphNode, ...] = ()  # S18: neighborhood members
    paths: tuple[GraphPath, ...] = ()  # S17/S21: compound connections
    edges: tuple[GraphEdge, ...] = ()  # the traversed relations
    ranking: tuple[RankedItem, ...] = ()  # S46: fused / reranked order
    changes: tuple[ChangeRecord, ...] = ()  # S13/S14/S30: the delta feed
    aggregate: AggregateReport | None = None  # S26–S30/S40: enumerated only
    pages: tuple[PageRef, ...] = ()  # S31/S45: pages_about discovery
    freshness: Freshness
    truncation: Truncation | None = None  # S18/S49: caps are never silent
    dropped_by_hydration: int = 0
    excluded_unstamped: int = Field(default=0, ge=0)
    negative: Negative | None = None

    @model_validator(mode="after")
    def _composite_uses_parts(self) -> "Envelope":
        """Enforce the S47 discipline: `parts` belong only to a composite
        answer, and a composite's data lives IN its parts, never blended into
        the top-level result tuples. (Each part's own single-grain purity is
        checked on `EnvelopePart`.) A flat compound answer with no parts —
        hydrate's fact-with-evidence bundle — is untouched."""
        if not self.parts:
            return self
        if self.grain is not Grain.COMPOSITE:
            raise ValueError("only a composite envelope carries parts[]")
        blended = self.aggregate is not None or any(
            (
                self.entities,
                self.facts,
                self.evidence,
                self.fact_evidence,
                self.evidence_totals,
                self.chunks,
                self.sources,
                self.transcript,
                self.nodes,
                self.paths,
                self.edges,
                self.ranking,
                self.changes,
                self.pages,
            )
        )
        if blended:
            raise ValueError(
                "a composite answer's data lives in parts[], never blended"
                " into the top-level result tuples (S47)"
            )
        return self
