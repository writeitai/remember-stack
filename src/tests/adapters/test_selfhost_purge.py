"""D74 erasure capability tests for the existing self-host stores."""

from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.selfhost import LocalMountPublisher
from rememberstack.adapters.selfhost import SelfHostProjectionPurger
from rememberstack.model import ForgetInProgressError
from rememberstack.model import ObjectKey
from rememberstack.ports import ObjectPurgePort
from rememberstack.ports import ProjectionPurgePort
from rememberstack.spine import ProjectionCatalog

_DEPLOYMENT_ID = UUID("74000000-0000-0000-0000-000000000001")


class _ClosedAdmission:
    def assert_available(self, *, deployment_id: UUID) -> None:
        raise ForgetInProgressError(str(deployment_id))


def test_mount_publication_checks_admission_before_writing(tmp_path: Path) -> None:
    """A closed deployment cannot expose a new or restored serving mount."""
    root = tmp_path / "mounts"
    publisher = LocalMountPublisher(root=root, admission=_ClosedAdmission())

    with pytest.raises(ForgetInProgressError):
        publisher.publish(deployment_id=_DEPLOYMENT_ID)

    assert not root.exists()


def test_local_object_purge_is_exact_prefix_aware_and_idempotent(
    tmp_path: Path,
) -> None:
    """Delete nominated bytes and markers while preserving unrelated objects."""
    store = LocalFSObjectStore(root=tmp_path / "objects")
    adapter: ObjectPurgePort = store
    exact = ObjectKey("raw/forgotten.bin")
    under_prefix = ObjectKey("artifacts/forgotten/transcript.json")
    similar_prefix = ObjectKey("artifacts/forgotten-extra/control.json")
    survivor = ObjectKey("raw/control.bin")
    store.write_bytes(key=exact, content=b"forgotten", storage_class="cold")
    store.write_bytes(key=under_prefix, content=b"forgotten")
    store.write_bytes(key=similar_prefix, content=b"control")
    store.write_bytes(key=survivor, content=b"control")

    adapter.purge_objects(keys=(exact,), prefixes=(ObjectKey("artifacts/forgotten"),))
    adapter.purge_objects(keys=(exact,), prefixes=(ObjectKey("artifacts/forgotten"),))
    adapter.verify_objects_purged(
        keys=(exact,), prefixes=(ObjectKey("artifacts/forgotten"),)
    )

    assert not (tmp_path / "objects/raw/forgotten.bin").exists()
    assert not (tmp_path / "objects/raw/forgotten.bin.storage-class").exists()
    assert not (tmp_path / "objects/artifacts/forgotten").exists()
    assert store.read_bytes(key=similar_prefix) == b"control"
    assert store.read_bytes(key=survivor) == b"control"


class RecordingProjectionCatalog:
    """Record exact old registry prefixes acknowledged by the adapter."""

    def __init__(self) -> None:
        self.purged: tuple[UUID, tuple[str, ...]] | None = None

    def purge_snapshot_prefixes(
        self, *, deployment_id: UUID, prefixes: tuple[str, ...]
    ) -> int:
        self.purged = (deployment_id, prefixes)
        return len(prefixes)

    def snapshot_prefixes_exist(
        self, *, deployment_id: UUID, prefixes: tuple[str, ...]
    ) -> bool:
        return False


def test_projection_purge_removes_durable_registry_and_local_copies(
    tmp_path: Path,
) -> None:
    """Acknowledge only after every self-host projection surface is absent."""
    object_store = LocalFSObjectStore(root=tmp_path / "snapshots")
    prefix = ObjectKey("graph/snapshots/old-version")
    object_store.write_bytes(
        key=ObjectKey(f"{prefix.root}/MANIFEST.json"), content=b"old"
    )
    p3_copy = tmp_path / "mounts" / str(_DEPLOYMENT_ID) / "p3-old-version"
    p3_copy.mkdir(parents=True)
    (p3_copy / "index.md").write_bytes(b"old")
    catalog = RecordingProjectionCatalog()
    adapter: ProjectionPurgePort = SelfHostProjectionPurger(
        object_purger=object_store,
        catalog=cast(ProjectionCatalog, catalog),
        mount_root=tmp_path / "mounts",
    )

    adapter.purge_projections(deployment_id=_DEPLOYMENT_ID, prefixes=(prefix,))
    adapter.purge_projections(deployment_id=_DEPLOYMENT_ID, prefixes=(prefix,))
    adapter.verify_projections_purged(deployment_id=_DEPLOYMENT_ID, prefixes=(prefix,))

    assert not (tmp_path / "snapshots" / prefix.root).exists()
    assert not p3_copy.exists()
    assert catalog.purged == (_DEPLOYMENT_ID, (prefix.root,))
