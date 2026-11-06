"""Contract tests for the dedicated self-host forget-manifest root."""

from datetime import datetime
from datetime import timezone
from pathlib import Path
from uuid import UUID

import pytest

from rememberstack.adapters.selfhost import LocalFSForgetManifestStore
from rememberstack.model import ForgetManifest
from rememberstack.model import ForgetManifestConflictError
from rememberstack.ports import ForgetManifestPort

_DEPLOYMENT_ID = UUID("74000000-0000-0000-0000-000000000001")
_OTHER_DEPLOYMENT_ID = UUID("74000000-0000-0000-0000-000000000002")
_FORGET_ID = UUID("74000000-0000-0000-0000-000000000003")
_DOC_ID = UUID("74000000-0000-0000-0000-000000000004")


def _manifest(
    *,
    forget_id: UUID = _FORGET_ID,
    deployment_id: UUID = _DEPLOYMENT_ID,
    doc_id: UUID = _DOC_ID,
) -> ForgetManifest:
    return ForgetManifest(
        forget_id=forget_id,
        deployment_id=deployment_id,
        doc_id=doc_id,
        requested_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )


def test_root_must_be_independently_provisioned(tmp_path: Path) -> None:
    """Refuse a missing ordinary-data-style root instead of guessing it is safe."""
    with pytest.raises(FileNotFoundError):
        LocalFSForgetManifestStore(root=tmp_path / "missing")


def test_append_is_durable_idempotent_and_port_conformant(tmp_path: Path) -> None:
    """Persist canonical bytes once and acknowledge exact retries."""
    root = tmp_path / "forget-intent"
    root.mkdir()
    store: ForgetManifestPort = LocalFSForgetManifestStore(root=root)
    manifest = _manifest()

    store.append(manifest=manifest)
    store.append(manifest=manifest)

    assert store.manifests(deployment_id=_DEPLOYMENT_ID) == (manifest,)
    assert (root / f"{_FORGET_ID}.json").read_bytes() == manifest.canonical_bytes()
    assert not tuple(root.glob(".*.tmp"))


def test_append_rejects_forget_id_reuse_with_different_bytes(tmp_path: Path) -> None:
    """Never reinterpret a durable forget identity after acceptance."""
    root = tmp_path / "forget-intent"
    root.mkdir()
    store = LocalFSForgetManifestStore(root=root)
    store.append(manifest=_manifest())

    with pytest.raises(ForgetManifestConflictError):
        store.append(
            manifest=_manifest(
                deployment_id=_OTHER_DEPLOYMENT_ID,
                doc_id=UUID("74000000-0000-0000-0000-000000000005"),
            )
        )


def test_enumeration_filters_deployment_and_detects_filename_tampering(
    tmp_path: Path,
) -> None:
    """Return only owned intent and fail visibly on a mismatched durable identity."""
    root = tmp_path / "forget-intent"
    root.mkdir()
    store = LocalFSForgetManifestStore(root=root)
    own = _manifest()
    other = _manifest(
        forget_id=UUID("74000000-0000-0000-0000-000000000006"),
        deployment_id=_OTHER_DEPLOYMENT_ID,
    )
    store.append(manifest=other)
    store.append(manifest=own)

    assert store.manifests(deployment_id=_DEPLOYMENT_ID) == (own,)

    tampered = root / "74000000-0000-0000-0000-000000000007.json"
    tampered.write_bytes(own.canonical_bytes())
    with pytest.raises(ForgetManifestConflictError):
        store.manifests(deployment_id=_DEPLOYMENT_ID)


def test_object_store_manifests_keep_the_same_contract() -> None:
    """D147: the bucket-backed store appends once, filters and detects tampering."""
    from rememberstack.adapters.selfhost import MinIOObjectStore
    from rememberstack.adapters.selfhost import ObjectStoreForgetManifestStore
    from rememberstack.core.storage_routing import HOT
    from rememberstack.model import ObjectKey
    from tests.adapters.test_minio_store import _MemoryS3

    client = _MemoryS3()
    bucket = MinIOObjectStore(bucket="forget", client=client)
    store: ForgetManifestPort = ObjectStoreForgetManifestStore(store=bucket)
    own = _manifest()
    other = _manifest(
        forget_id=UUID("74000000-0000-0000-0000-000000000006"),
        deployment_id=_OTHER_DEPLOYMENT_ID,
    )
    store.append(manifest=own)
    store.append(manifest=own)
    store.append(manifest=other)

    assert store.manifests(deployment_id=_DEPLOYMENT_ID) == (own,)
    assert client.objects[("forget", f"forget-manifests/{_FORGET_ID}.json")][0] == (
        own.canonical_bytes()
    )
    with pytest.raises(ForgetManifestConflictError):
        store.append(manifest=_manifest(doc_id=UUID(int=5)))

    bucket.write_bytes(
        key=ObjectKey("forget-manifests/74000000-0000-0000-0000-000000000007.json"),
        content=own.canonical_bytes(),
        storage_class=HOT,
    )
    with pytest.raises(ForgetManifestConflictError):
        store.manifests(deployment_id=_DEPLOYMENT_ID)
