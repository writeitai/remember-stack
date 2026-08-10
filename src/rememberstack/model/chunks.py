"""E1 chunk-layer values: packing inputs/outputs, catalog records, P1 rows (D58).

Chunks are non-overlapping runs of whole blocks within one section; their
identity is the ordered block-hash sequence, which is what makes reuse (D56)
a sequence comparison instead of a semantic judgment.
"""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model.queue import UTCDateTime

_NonEmpty = Annotated[str, Field(min_length=1)]


class SectionSpan(BaseModel):
    """One section's block range and signals, as the chunker consumes it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: UUID
    node_path: str
    role: str
    block_start: int = Field(ge=0)
    block_end: int = Field(ge=-1)
    summary: str | None = None
    title: str | None = None


class ChunkSource(BaseModel):
    """Everything the chunk stage loads about its claimed representation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    doc_id: UUID
    version_id: UUID
    representation_id: UUID
    markdown_uri: str
    blocks_uri: str
    title: str | None
    source_kind: str
    source_modified_at: UTCDateTime | None
    published_at: UTCDateTime | None
    language: str | None
    structurer_version: str
    sections: tuple[SectionSpan, ...]
    # D80 connector packaging (defaults until connectors emit typed metadata).
    source_shape: str = "document"
    channel_ref: str | None = None
    thread_ref: str | None = None
    author_ref: str | None = None
    message_ts: str | None = None


class PackedChunk(BaseModel):
    """One packed run of whole blocks: the chunker's pure output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ordinal: int = Field(ge=0)
    section_id: UUID
    block_start: int = Field(ge=0)
    block_end: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    chunk_content_hash: _NonEmpty
    token_count: int = Field(ge=0)


class ChunkRecord(BaseModel):
    """One chunk row for the spine ledger (text and vectors live elsewhere, D37/D8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: UUID
    deployment_id: UUID
    doc_id: UUID
    version_id: UUID
    representation_id: UUID
    section_id: UUID
    ordinal: int = Field(ge=0)
    block_start: int = Field(ge=0)
    block_end: int = Field(ge=0)
    chunk_content_hash: _NonEmpty
    extraction_input_hash: _NonEmpty
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    token_count: int = Field(ge=0)
    chunker_version: _NonEmpty


class ChunkForEmbedding(BaseModel):
    """One chunk row as the embed stage loads it (spans + signals, no body)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: UUID
    doc_id: UUID
    version_id: UUID
    ordinal: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    chunk_content_hash: str
    extraction_input_hash: str
    section_role: str
    section_path: str
    section_id: UUID | None = None
    """The section row the chunk was cut under — the cross-generation guard
    input for summary orientation (optional: legacy constructors omit it and
    the guard simply does not arm)."""
    section_title: str | None = None
    context_prefix: str | None = None
    """Legacy free-form prefix and/or D80 location_header stamp."""
    prefixer_version: str | None = None
    location_header: str | None = None
    embedding_text_hash: str | None = None
    embedding_input_policy_version: str | None = None
    policy_generation: str | None = None
    embedding_ref: str | None = None
    embedding_version: str | None = None
    location_facts_json: str | None = None


class CarryForwardSource(BaseModel):
    """A prior version's chunk whose embedding identity can be reused (D80).

    Reuse requires matching embedding_text_hash, policy_generation, and
    embedder generation — not content hash alone when location participates.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: UUID
    location_header: str | None = None
    embedding_text_hash: str
    policy_generation: str
    context_prefix: str | None = None
    """Legacy alias for location_header when migrating older rows."""


class EmbeddingUpdate(BaseModel):
    """The embed stage's write-back onto one chunk row (D80 stamps)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: UUID
    embedding_ref: _NonEmpty
    embedding_version: _NonEmpty
    location_header: str | None = None
    embedding_text_hash: _NonEmpty
    embedding_input_policy_version: _NonEmpty
    policy_generation: _NonEmpty
    location_facts_json: str | None = None
    # Legacy columns kept for transition / older readers.
    context_prefix: str | None = None
    prefixer_version: str | None = None


class ContextPrefix(BaseModel):
    """Legacy structured response of the E1 context-prefix LLM call (retired default)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prefix: _NonEmpty


class P1ChunkRow(BaseModel):
    """One row of the P1 chunk table: text + vector + filter scalars (D8/D80).

    Generation-safe key is ``(chunk_id, policy_generation, embedder_generation)``.
    Legacy constructors may omit D80 fields; production E1 always stamps them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: UUID
    deployment_id: UUID
    doc_id: UUID
    version_id: UUID
    section_role: str
    text: _NonEmpty
    vector: Annotated[tuple[float, ...], Field(min_length=1)]
    policy_generation: str = "legacy"
    embedder_generation: str = "legacy"
    embedding_text_hash: str = ""
    source_kind: str = "unknown"
    source_shape: str = "document"


class P1ChunkText(BaseModel):
    """The text-bearing part of one nominated P1 chunk row.

    The query engine confirms the UUID and source coordinate against Postgres
    before this projection text can enter an evidence envelope (D48).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: UUID
    section_role: str
    indexed_text: _NonEmpty


class ChunkSourceNotFoundError(Exception):
    """The chunk stage referenced a representation the spine does not know."""


class P1ClaimRow(BaseModel):
    """One row of the P1 claims channel: the needle index (D8/D58).

    `is_current_testimony` is the scalar the DEFAULT claims channel filters
    on (retrieval §5): current-testimony-only unless an explicit historical
    query asks otherwise.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: UUID
    deployment_id: UUID
    doc_id: UUID
    chunk_id: UUID
    text: Annotated[str, Field(min_length=1)]
    is_current_testimony: bool
    is_attributed: bool
    vector: Annotated[tuple[float, ...], Field(min_length=1)]


class P1FactRow(BaseModel):
    """One fact label plus rebuildable D87 pre-ranking eligibility metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: UUID
    deployment_id: UUID
    kind: Annotated[str, Field(min_length=1)]  # relation | observation
    label: Annotated[str, Field(min_length=1)]
    status: Annotated[str, Field(min_length=1)]  # active | invalidated
    valid_from: UTCDateTime | None
    valid_until: UTCDateTime | None
    ingested_at: UTCDateTime
    invalidated_at: UTCDateTime | None
    vector: Annotated[tuple[float, ...], Field(min_length=1)]


class P1FactMetadataRow(BaseModel):
    """Mutable fact scope fields refreshed without recomputing its vector."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: UUID
    deployment_id: UUID
    kind: Annotated[str, Field(min_length=1)]
    status: Annotated[str, Field(min_length=1)]
    valid_from: UTCDateTime | None
    valid_until: UTCDateTime | None
    ingested_at: UTCDateTime
    invalidated_at: UTCDateTime | None
