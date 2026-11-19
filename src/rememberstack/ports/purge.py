"""D74 erasure capabilities of the already-selected serving stores."""

from collections.abc import Callable
from collections.abc import Iterator
from typing import Protocol
from typing import runtime_checkable
from uuid import UUID

from rememberstack.model import ObjectKey
from rememberstack.model import ObjectPurgeEvidence


@runtime_checkable
class ObjectPurgePort(Protocol):
    """Erase nominated objects and report unfinished-upload coverage."""

    def iter_purge_keys(self) -> Iterator[ObjectKey]:
        """Discover current, retained/deleted and unfinished object names.

        Unlike a reader listing this includes keys hidden by delete markers.
        Unsupported multipart listing keeps the provider-managed contract.
        """
        ...

    def purge_objects(
        self,
        *,
        keys: tuple[ObjectKey, ...],
        prefixes: tuple[ObjectKey, ...],
        before_write: Callable[[], None] | None = None,
    ) -> ObjectPurgeEvidence | None:
        """Delete exact keys/prefixes; absence succeeds.

        S3 returns unfinished-upload coverage; stores without multipart uploads
        may return None. Unsupported listing delegates cleanup to the provider,
        never attests that unfinished uploads are absent.
        """
        ...

    def verify_objects_purged(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> ObjectPurgeEvidence | None:
        """Verify object absence, returning the same unfinished-upload coverage."""
        ...


@runtime_checkable
class ProjectionPurgePort(Protocol):
    """Erase one document's corpus copies and repair scrubbed navigation."""

    def quiesce_projections(self, *, deployment_id: UUID, forget_id: UUID) -> None:
        """Stop controlled serving and acknowledge external-write drain before scrub."""
        ...

    def purge_projections(
        self, *, deployment_id: UUID, doc_id: UUID, forget_id: UUID
    ) -> ObjectPurgeEvidence | None:
        """Erase suffix folders/history/uploads and acknowledge controlled caches."""
        ...

    def verify_projections_purged(
        self, *, deployment_id: UUID, doc_id: UUID, forget_id: UUID
    ) -> None:
        """Verify suffix absence, regenerated navigation and cache acknowledgement."""
        ...


class CorpusCachePurgePort(Protocol):
    """The composing host's controlled serving sessions, outside object storage."""

    def quiesce(self, *, deployment_id: UUID, forget_id: UUID) -> None:
        """Stop serving, drain external writes and acknowledge stopped sessions."""
        ...

    def purge(self, *, deployment_id: UUID, doc_id: UUID, forget_id: UUID) -> None:
        """Acknowledge erasure of this document from every controlled cache."""
        ...

    def verify(self, *, deployment_id: UUID, doc_id: UUID, forget_id: UUID) -> None:
        """Raise unless sessions are stopped and their caches acknowledge erasure."""
        ...


@runtime_checkable
class KGitPurgePort(Protocol):
    """Erase affected Plane-K paths from all reachable Git history."""

    def blocking_redaction_paths(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> tuple[str, ...]:
        """Return sorted authored-body or curation paths that still cite a lineage."""
        ...

    def purge_artifacts(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        """Erase affected history and retain only already-sanitized current files."""
        ...

    def verify_artifacts_purged(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        """Raise unless the store-local receipt and affected-path history are clean."""
        ...
