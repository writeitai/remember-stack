"""Structural contracts for D74 portable intent and erasure capabilities."""

from uuid import UUID

from rememberstack.model import ForgetManifest
from rememberstack.model import ObjectKey
from rememberstack.ports import ForgetManifestPort
from rememberstack.ports import KGitPurgePort
from rememberstack.ports import ObjectPurgePort
from rememberstack.ports import ProjectionPurgePort


class RecordingForgetStore:
    """Minimal manifest fake with the port's immutable append shape."""

    def __init__(self) -> None:
        self.items: dict[UUID, ForgetManifest] = {}

    def append(self, *, manifest: ForgetManifest) -> None:
        current = self.items.get(manifest.forget_id)
        if (
            current is not None
            and current.canonical_bytes() != manifest.canonical_bytes()
        ):
            raise ValueError("conflicting manifest")
        self.items[manifest.forget_id] = manifest

    def manifests(self, *, deployment_id: UUID) -> tuple[ForgetManifest, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self.items.values()
                    if item.deployment_id == deployment_id
                ),
                key=lambda item: str(item.forget_id),
            )
        )


class RecordingPurgeAdapter:
    """One structural fake implementing all four narrow store capabilities."""

    def purge_objects(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        pass

    def verify_objects_purged(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        pass

    def purge_projections(
        self, *, deployment_id: UUID, prefixes: tuple[ObjectKey, ...]
    ) -> None:
        pass

    def verify_projections_purged(
        self, *, deployment_id: UUID, prefixes: tuple[ObjectKey, ...]
    ) -> None:
        pass

    def purge_artifacts(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        pass

    def verify_artifacts_purged(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        pass

    def blocking_redaction_paths(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> tuple[str, ...]:
        return ()


_manifest_assignment: ForgetManifestPort = RecordingForgetStore()
_object_assignment: ObjectPurgePort = RecordingPurgeAdapter()
_projection_assignment: ProjectionPurgePort = RecordingPurgeAdapter()
_k_assignment: KGitPurgePort = RecordingPurgeAdapter()


def test_d74_protocols_are_runtime_checkable_and_capability_sized() -> None:
    """Keep one two-operation intent port and four single-operation purge hooks."""
    assert isinstance(_manifest_assignment, ForgetManifestPort)
    assert isinstance(_object_assignment, ObjectPurgePort)
    assert isinstance(_projection_assignment, ProjectionPurgePort)
    assert isinstance(_k_assignment, KGitPurgePort)

    assert _public_operations(ForgetManifestPort) == {"append", "manifests"}
    assert _public_operations(ObjectPurgePort) == {
        "purge_objects",
        "verify_objects_purged",
    }
    assert _public_operations(ProjectionPurgePort) == {
        "purge_projections",
        "verify_projections_purged",
    }
    assert _public_operations(KGitPurgePort) == {
        "blocking_redaction_paths",
        "purge_artifacts",
        "verify_artifacts_purged",
    }


def _public_operations(protocol: type[object]) -> set[str]:
    return {
        name
        for name, value in protocol.__dict__.items()
        if not name.startswith("_") and callable(value)
    }
