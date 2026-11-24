"""Typed D159 forget records and the forget boundary failures."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict

from rememberstack.model.queue import UTCDateTime


class ForgetStatus(StrEnum):
    """Exact values of the binding PostgreSQL ``forget_status`` enum."""

    ERASING = "erasing"
    WAITING_FOR_REDACTION = "waiting_for_redaction"
    COMPLETE = "complete"


class Forget(BaseModel):
    """One content-free forget record (D159 §3.5): IDs, hashes and progress."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    forget_id: UUID
    deployment_id: UUID
    doc_id: UUID
    version_ids: tuple[UUID, ...]
    content_hashes: tuple[str, ...]
    source_identity_hash: str | None
    status: ForgetStatus
    blocking_k_paths: tuple[str, ...] = ()
    last_error: str | None = None
    requested_at: UTCDateTime
    completed_at: UTCDateTime | None = None


class KnowledgeErasure(BaseModel):
    """What one Plane-K erasure pass did (D159 §3.3 step 4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    removed: bool
    """It removed forgotten residue: compiled pages or rewritten history."""
    blocking_paths: tuple[str, ...] = ()
    """Current authored pages and sidecars that still name the document."""


class ForgetError(RuntimeError):
    """Base class for a known forget boundary failure."""


class ForgetNotFoundError(ForgetError):
    """The deployment holds no forget record with this identity."""


class DocumentForgottenError(ForgetError):
    """An ingest matched a forget record's source identity or content hash.

    The HTTP surface answers 409 ``document_forgotten`` with the record's
    ``forget_id`` and ``forgotten_at`` (D159 §3.6).
    """

    def __init__(self, *, forget_id: UUID, forgotten_at: datetime) -> None:
        """Keep the matching record's identity and time for the caller."""
        super().__init__(f"ingest matches forget_id {forget_id}")
        self.forget_id = forget_id
        self.forgotten_at = forgotten_at
