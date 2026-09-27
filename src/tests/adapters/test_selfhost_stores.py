"""Proofs for the local-FS object store and local mount publisher (WP-0.4a)."""

import os
from pathlib import Path
from typing import cast
from uuid import UUID
from uuid import uuid4

import pytest

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.selfhost import LocalMountPublisher
from rememberstack.adapters.selfhost import ObjectAlreadyExistsError
from rememberstack.adapters.selfhost import ObjectKeyEscapesRootError
from rememberstack.model import ObjectKey
from rememberstack.ports.mounts import MountPublisherPort
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.spine.projection import ProjectionCatalog


class _OpenAdmission:
    def assert_available(self, *, deployment_id: UUID) -> None:
        return None


def test_object_store_round_trip_and_immutability(tmp_path: Path) -> None:
    """Bytes round-trip under a key; a second write to the key is refused."""
    store: ObjectStorePort = LocalFSObjectStore(root=tmp_path / "objects")
    assert isinstance(store, ObjectStorePort)
    key = ObjectKey("raw/doc-1/original.pdf")

    store.write_bytes(key=key, content=b"immutable bytes")
    assert store.read_bytes(key=key) == b"immutable bytes"
    with pytest.raises(ObjectAlreadyExistsError):
        store.write_bytes(key=key, content=b"replacement")
    assert store.read_bytes(key=key) == b"immutable bytes"


def test_object_store_refuses_keys_that_escape_the_root(tmp_path: Path) -> None:
    """A traversal key can never resolve outside the store root."""
    store = LocalFSObjectStore(root=tmp_path / "objects")
    with pytest.raises(ObjectKeyEscapesRootError):
        store.write_bytes(key=ObjectKey("../outside.txt"), content=b"nope")


def test_mount_publisher_creates_the_three_views(tmp_path: Path) -> None:
    """Publishing yields the P3, artifacts and raw views, and no empty
    Plane-K placeholder when no knowledge checkout is configured."""
    publisher: MountPublisherPort = LocalMountPublisher(
        root=tmp_path / "mounts", admission=_OpenAdmission()
    )
    assert isinstance(publisher, MountPublisherPort)
    deployment_id = uuid4()

    mounts = publisher.publish(deployment_id=deployment_id)

    assert mounts.deployment_id == deployment_id
    assert mounts.read_only is True
    for locator in (mounts.p3, mounts.artifacts, mounts.raw):
        assert Path(locator).is_dir()
    assert mounts.knowledge is None
    assert not (tmp_path / "mounts" / str(deployment_id) / "knowledge").exists()


class _NoSnapshotCatalog:
    def latest_snapshot(self, *, deployment_id: UUID, plane: str) -> None:
        return None


def test_the_p3_link_survives_a_different_host_path(tmp_path: Path) -> None:
    """The p3 link is relative, so a tree published inside a container into a
    bind mount resolves on the host even when the host path differs."""
    publisher = LocalMountPublisher(
        root=tmp_path / "container-path",
        catalog=cast(ProjectionCatalog, _NoSnapshotCatalog()),
        corpusfs_store=LocalFSObjectStore(root=tmp_path / "corpusfs"),
        admission=_OpenAdmission(),
    )
    deployment_id = uuid4()
    mounts = publisher.publish(deployment_id=deployment_id)
    link = Path(mounts.p3)
    assert link.is_symlink()
    assert not Path(os.readlink(link)).is_absolute()

    (tmp_path / "container-path").rename(tmp_path / "host-path")

    moved = tmp_path / "host-path" / str(deployment_id) / "p3"
    assert "No P3 snapshot" in (moved / "llms.txt").read_text(encoding="utf-8")
