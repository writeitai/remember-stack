"""D74 erasure capabilities of the already-selected serving stores."""

from typing import Protocol
from typing import runtime_checkable
from uuid import UUID

from rememberstack.model import ObjectKey


@runtime_checkable
class ObjectPurgePort(Protocol):
    """Idempotently erase manifest-nominated immutable objects."""

    def purge_objects(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Delete exact keys and every object below exact prefixes; absence succeeds."""
        ...

    def verify_objects_purged(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Raise unless every exact key and prefix is absent from the active store."""
        ...


@runtime_checkable
class ProjectionPurgePort(Protocol):
    """Erase old P2/P3 durable prefixes and local serving copies."""

    def purge_projections(
        self, *, deployment_id: UUID, prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Delete every nominated projection copy; absence succeeds."""
        ...

    def verify_projections_purged(
        self, *, deployment_id: UUID, prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Raise unless durable old prefixes and local serving copies are absent."""
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
