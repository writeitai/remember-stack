"""E0 document-layer values: uploads, ledger records, and stage-load shapes (D36/D37).

Object URIs in these models are provider-neutral object-store *keys*; which
bucket a key resolves in (raw vs artifacts) is deployment configuration, and
the composing profile binds one `ObjectStorePort` per bucket.
"""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model.queue import UTCDateTime

NonEmptyString = Annotated[str, Field(min_length=1)]


class IngestPrincipalKind(StrEnum):
    """What kind of actor ingested a version.

    The three are genuinely different referents and are never collapsed:
    ``USER`` is a person, ``API_CREDENTIAL`` is a machine credential a person
    once minted, and ``SERVICE`` is the deployment's own automation.
    Attributing credential activity to that person would be false attribution.
    """

    USER = "user"
    API_CREDENTIAL = "api_credential"
    SERVICE = "service"


class IngestPrincipal(BaseModel):
    """The typed actor a caller claims created this version.

    ``external_ref`` is opaque to the engine — the caller's stable id for the
    principal — and is treated as erasable PII.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: IngestPrincipalKind
    external_ref: NonEmptyString = Field(max_length=255)


class DocumentUpload(BaseModel):
    """One file handed to the upload connector: bytes plus what the caller knows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: NonEmptyString
    mime: NonEmptyString
    content: bytes
    title: str | None = None


class IngestedVersion(BaseModel):
    """What one ingest did: the lineage/version it landed on, and whether it was new.

    `created=False` is the D55 content-hash no-op: identical bytes re-ingested
    never create a second version or re-run the chain.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    doc_id: UUID
    version_id: UUID
    content_hash: str
    created: bool
    ingested_by_principal_id: UUID | None = None


class UploadRecord(BaseModel):
    """The complete row-write input for recording one upload in the spine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    doc_id: UUID
    source_kind: NonEmptyString
    source_ref: NonEmptyString
    source_uri: str | None
    title: str | None
    content_hash: NonEmptyString
    mime: NonEmptyString
    byte_size: int = Field(ge=0)
    raw_uri: NonEmptyString
    versioning_mode: str = "snapshot"  # snapshot (fail-safe) | living (D55)
    source_modified_at: UTCDateTime | None = None
    source_version_ref: str | None = None
    sync_cycle_id: UUID | None = None
    ingested_by: IngestPrincipal | None = None


class ConvertSource(BaseModel):
    """Everything the convert stage loads about its claimed document version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    doc_id: UUID
    version_id: UUID
    content_hash: str
    mime: str
    raw_uri: str
    title: str | None


class RepresentationRecord(BaseModel):
    """One conversion run's immutable output row (D65): the reading of a version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    representation_id: UUID
    deployment_id: UUID
    version_id: UUID
    route: NonEmptyString
    converter_name: NonEmptyString
    converter_version: NonEmptyString
    blockizer_version: NonEmptyString
    markdown_uri: NonEmptyString
    blocks_uri: NonEmptyString
    conversion_uri: NonEmptyString
    meta_uri: NonEmptyString
    markdown_hash: NonEmptyString
    manifest_hash: NonEmptyString


class StructureSource(BaseModel):
    """Everything the structure stage loads about its claimed representation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    doc_id: UUID
    version_id: UUID
    representation_id: UUID
    blocks_uri: str
    markdown_uri: str
    title: str | None
    source_kind: str


class SyntheticRootRecord(BaseModel):
    """The single full-document root section every document gets (D39).

    The root spans the whole block grid and character range of `document.md`;
    an empty document still gets the row (zero-width span) so E1/E2/P3 always
    have a path and role to read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    doc_id: UUID
    version_id: UUID
    representation_id: UUID
    block_count: int = Field(ge=0)
    markdown_chars: int = Field(ge=0)
    title: str | None
    structurer_version: NonEmptyString


class DocumentVersionNotFoundError(Exception):
    """A stage referenced a document version the spine does not know."""


class RepresentationNotFoundError(Exception):
    """A stage referenced a document representation the spine does not know."""


class SourceItem(BaseModel):
    """One observation a watched source reports in a poll (lifecycle §2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_ref: NonEmptyString  # connector-native stable id (path, file id)
    revision: NonEmptyString  # revision/etag; unchanged revision = no fetch
    modified_at: UTCDateTime
    deleted: bool = False
    filename: str = ""
    mime: str = "text/markdown"


class SyncCycleSummary(BaseModel):
    """What one recorded sync cycle did (connector_sync_cycles, D55/F8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cycle_id: UUID
    observed: int
    ingested: tuple[UUID, ...] = ()  # version ids created this cycle
    unchanged: int = 0
    debounced: int = 0
    deletions_observed: tuple[UUID, ...] = ()  # lineage ids tombstoned
    failed: int = 0  # items lost to per-item errors; the cycle is lossy
