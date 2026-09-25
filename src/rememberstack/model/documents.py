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

from remember.models import IngestedVersion as IngestedVersion  # noqa: F401
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
    external_ref: Annotated[
        str,
        # Printable ASCII only: the reference travels in an HTTP header, and a
        # header cannot carry non-ASCII. Constraining it makes that an explicit
        # 422 instead of an encoding crash at the transport. Callers use opaque
        # ids (``user:{uuid}``), so this costs nothing real.
        Field(min_length=1, max_length=255, pattern=r"^[\x20-\x7e]+$"),
    ]


class DocumentUpload(BaseModel):
    """One file handed to the upload connector: bytes plus what the caller knows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: NonEmptyString
    mime: NonEmptyString
    content: bytes
    title: str | None = None
    source_path: str | None = None
    """Where the file lives at its source (a folder path, a URL), as observed
    now; recorded per version in D134 document metadata and names."""


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
    file_name: str | None = None
    """The file name observed with these bytes (D134 metadata and names)."""
    declared_title: str | None = None
    """The title the caller declared, if any; ``title`` above is the lineage
    title, which falls back to the file stem."""
    source_path: str | None = None
    """The source location observed with these bytes (D134)."""


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


class DocumentNotFoundError(LookupError):
    """No live document lineage has this id in the deployment.

    Raised for an id the deployment never held and for a document that is
    already deleted: from a caller's point of view both are absent (D135).
    """


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
    source_path: str | None = None  # where the item lives in the source (D134)


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
