"""Typed D74 hard-forget manifests, progress, and boundary failures."""

from enum import StrEnum
import hashlib
import json
from typing import Annotated
from typing import Self
from typing import TypeAlias
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator
from pydantic import StringConstraints

from rememberstack.model.object_store import ObjectKey
from rememberstack.model.queue import UTCDateTime

Sha256: TypeAlias = Annotated[
    str, StringConstraints(pattern=r"^[0-9a-f]{64}$", strict=True)
]


class ForgetManifestStatus(StrEnum):
    """Exact values of the binding PostgreSQL ``forget_manifest_status`` enum."""

    PREPARING = "preparing"
    ACCEPTED = "accepted"
    COMPLETE = "complete"


class ForgetManifest(BaseModel):
    """One immutable, content-free lineage-erasure manifest (D74)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    forget_id: UUID
    deployment_id: UUID
    doc_id: UUID
    requested_at: UTCDateTime
    source_identity_hash: Sha256 | None = None
    content_hashes: tuple[Sha256, ...] = ()
    chunk_ids: tuple[UUID, ...] = ()
    claim_ids: tuple[UUID, ...] = ()
    mention_ids: tuple[UUID, ...] = ()
    resolved_entity_ids: tuple[UUID, ...] = ()
    fact_ids: tuple[UUID, ...] = ()
    entity_ids: tuple[UUID, ...] = ()
    object_keys: tuple[ObjectKey, ...] = ()
    k_artifact_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def require_canonical_sets(self) -> Self:
        """Require every tuple-set to be sorted and duplicate-free for stable bytes."""
        fields = (
            "content_hashes",
            "chunk_ids",
            "claim_ids",
            "mention_ids",
            "resolved_entity_ids",
            "fact_ids",
            "entity_ids",
            "object_keys",
            "k_artifact_ids",
        )
        for field in fields:
            values = getattr(self, field)
            rendered = tuple(_sort_value(value=value) for value in values)
            if rendered != tuple(sorted(set(rendered))):
                raise ValueError(f"{field} must be sorted and duplicate-free")
        return self

    def canonical_bytes(self) -> bytes:
        """Render stable portable bytes used by the append idempotency contract."""
        payload = self.model_dump(mode="json")
        return json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    def sha256(self) -> str:
        """Return the lowercase SHA-256 of the canonical manifest bytes."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class ForgetManifestRecord(BaseModel):
    """Local PostgreSQL materialization and verification progress for one manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    forget_id: UUID
    deployment_id: UUID
    doc_id: UUID
    manifest: ForgetManifest | None
    manifest_hash: Sha256 | None
    status: ForgetManifestStatus
    prepared_at: UTCDateTime
    accepted_at: UTCDateTime | None
    completed_at: UTCDateTime | None
    last_verified_at: UTCDateTime | None


class ForgetError(RuntimeError):
    """Base class for a known hard-forget boundary failure."""


class ForgetInProgressError(ForgetError):
    """A public or ordinary-work call reached a deployment under the D74 barrier."""


class ForgetManifestConflictError(ForgetError):
    """One forget identity was reused with different immutable manifest bytes."""


class ForgetManifestNotFoundError(ForgetError):
    """The requested deployment-owned forget manifest does not exist."""


class ForgetTargetNotFoundError(ForgetError):
    """The requested deployment does not own the target document lineage."""


class ForgetRedactionRequiredError(ForgetError):
    """Owner-controlled K paths still cite the target and must be redacted first."""

    def __init__(self, *, paths: tuple[str, ...]) -> None:
        """Retain the exact sorted blocking repository paths for the caller."""
        super().__init__("owner redaction is required before hard-forget acceptance")
        self.paths = paths


class ForgottenSourceError(ForgetError):
    """An ingest matched an irreversible source-identity or content guard."""


def _sort_value(*, value: object) -> str:
    """Render one manifest set member to its canonical lexical sort key."""
    if isinstance(value, ObjectKey):
        return value.root
    return str(value)
