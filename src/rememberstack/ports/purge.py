"""D159 erasure capabilities of the already-selected serving stores."""

from collections.abc import Callable
from collections.abc import Iterator
from typing import Protocol
from typing import runtime_checkable
from uuid import UUID

from rememberstack.model import ObjectKey
from rememberstack.model import ObjectPurgeEvidence


@runtime_checkable
class ObjectPurgePort(Protocol):
    """Erase nominated objects, including retained versions and unfinished uploads."""

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
    ) -> int:
        """Delete exact keys/prefixes; absence succeeds.

        Returns how many objects, retained versions and unfinished uploads
        were removed, so a repeated purge of an erased set answers zero.
        """
        ...

    def verify_objects_purged(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> ObjectPurgeEvidence | None:
        """Verify object absence, returning the unfinished-upload coverage."""
        ...


@runtime_checkable
class ProjectionPurgePort(Protocol):
    """Erase one document's corpus copies and the navigation that listed it."""

    def erase_document(self, *, deployment_id: UUID, doc_id: UUID) -> bool:
        """Delete its folders and rewrite navigation; whether anything was removed."""
        ...


class CorpusCachePurgePort(Protocol):
    """The composing host's controlled mount sessions, outside object storage."""

    def invalidate(
        self, *, deployment_id: UUID, doc_id: UUID, paths: tuple[str, ...]
    ) -> None:
        """Best effort: drop the document's paths and these rewritten files.

        Sessions keep running; an offline client catches up when it
        reconnects. A failure here never holds a forget open (D159 §3.7).
        """
        ...


@runtime_checkable
class KGitPurgePort(Protocol):
    """Erase affected Plane-K paths from all reachable Git history."""

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
