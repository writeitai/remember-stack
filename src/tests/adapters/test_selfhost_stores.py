"""Proofs for the local-FS object store and local mount publisher (WP-0.4a)."""

from pathlib import Path
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


class _OpenAdmission:
    def assert_available(self, *, deployment_id: UUID) -> None:
        return None


def test_object_store_round_trip_and_immutability(tmp_path: Path) -> None:
    """Bytes round-trip under a key; a second write to the key is refused."""
    store: ObjectStorePort = LocalFSObjectStore(root=tmp_path / "objects")
    assert isinstance(store, ObjectStorePort)
    key = ObjectKey("raw/doc-1/original.pdf")

    store.write_bytes(key=key, content=b"immutable bytes", storage_class="cold")
    assert store.read_bytes(key=key) == b"immutable bytes"
    with pytest.raises(ObjectAlreadyExistsError):
        store.write_bytes(key=key, content=b"replacement", storage_class="cold")
    assert store.read_bytes(key=key) == b"immutable bytes"


def test_object_store_refuses_keys_that_escape_the_root(tmp_path: Path) -> None:
    """A traversal key can never resolve outside the store root."""
    store = LocalFSObjectStore(root=tmp_path / "objects")
    with pytest.raises(ObjectKeyEscapesRootError):
        store.write_bytes(
            key=ObjectKey("../outside.txt"), content=b"nope", storage_class="cold"
        )


def test_object_store_refuses_unlabelled_dynamic_write(tmp_path: Path) -> None:
    """An untyped caller cannot bypass the required metadata with None."""
    store = LocalFSObjectStore(root=tmp_path / "objects")
    with pytest.raises(ValueError, match="invalid storage class"):
        store.write_bytes(
            key=ObjectKey("raw/unknown"),
            content=b"bytes",
            storage_class=None,  # type: ignore[arg-type]
        )
    assert not (tmp_path / "objects/raw/unknown").exists()


def test_mount_publisher_creates_the_four_views(tmp_path: Path) -> None:
    """Publishing yields exactly the P3, artifacts, raw, and knowledge views."""
    publisher: MountPublisherPort = LocalMountPublisher(
        root=tmp_path / "mounts", admission=_OpenAdmission()
    )
    assert isinstance(publisher, MountPublisherPort)
    deployment_id = uuid4()

    mounts = publisher.publish(deployment_id=deployment_id)

    assert mounts.deployment_id == deployment_id
    assert mounts.read_only is True
    for locator in (mounts.p3, mounts.artifacts, mounts.raw, mounts.knowledge):
        assert Path(locator).is_dir()
