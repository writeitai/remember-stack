"""D74 erasure capability tests for the existing self-host stores."""

from datetime import datetime
from datetime import UTC
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from rememberstack.adapters.selfhost import LanceChunkIndex
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.selfhost import LocalMountPublisher
from rememberstack.adapters.selfhost import SelfHostProjectionPurger
from rememberstack.model import ForgetInProgressError
from rememberstack.model import ObjectKey
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1EntityRow
from rememberstack.model import P1FactRow
from rememberstack.ports import ObjectPurgePort
from rememberstack.ports import P1PurgePort
from rememberstack.ports import ProjectionPurgePort
from rememberstack.spine import ProjectionCatalog

_DEPLOYMENT_ID = UUID("74000000-0000-0000-0000-000000000001")
_OTHER_DEPLOYMENT_ID = UUID("74000000-0000-0000-0000-000000000002")
_DOC_ID = UUID("74000000-0000-0000-0000-000000000003")
_VERSION_ID = UUID("74000000-0000-0000-0000-000000000004")
_NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


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


def test_lance_purge_removes_only_nominated_deployment_rows(tmp_path: Path) -> None:
    """Erase all four P1 channels by UUID and accept an exact retry."""
    index = LanceChunkIndex(root=tmp_path / "lance")
    adapter: P1PurgePort = index
    forgotten = _ids(suffix=10)
    survivor = _ids(suffix=20)
    _seed_p1(index=index, deployment_id=_DEPLOYMENT_ID, ids=forgotten)
    _seed_p1(index=index, deployment_id=_OTHER_DEPLOYMENT_ID, ids=survivor)

    adapter.purge_rows(
        deployment_id=_DEPLOYMENT_ID,
        chunk_ids=(forgotten[0],),
        claim_ids=(forgotten[1],),
        fact_ids=(forgotten[2],),
        entity_ids=(forgotten[3],),
    )
    adapter.verify_rows_purged(
        deployment_id=_DEPLOYMENT_ID,
        chunk_ids=(forgotten[0],),
        claim_ids=(forgotten[1],),
        fact_ids=(forgotten[2],),
        entity_ids=(forgotten[3],),
    )
    adapter.purge_rows(
        deployment_id=_DEPLOYMENT_ID,
        chunk_ids=(forgotten[0],),
        claim_ids=(forgotten[1],),
        fact_ids=(forgotten[2],),
        entity_ids=(forgotten[3],),
    )

    assert index.table_count(table="chunks") == 1
    assert index.table_count(table="claims") == 1
    assert index.table_count(table="facts") == 1
    assert index.table_count(table="entities") == 1
    assert index.chunk_vectors(
        deployment_id=str(_OTHER_DEPLOYMENT_ID), chunk_ids=(str(survivor[0]),)
    ) == {str(survivor[0]): (0.0, 1.0)}
    assert index.entity_vectors(
        deployment_id=str(_OTHER_DEPLOYMENT_ID), entity_ids=(str(survivor[3]),)
    ) == {str(survivor[3]): (0.0, 1.0)}


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
    p2_copy = tmp_path / "p2-cache" / str(_DEPLOYMENT_ID) / "old-version"
    p3_copy = tmp_path / "mounts" / str(_DEPLOYMENT_ID) / "p3-old-version"
    p2_copy.mkdir(parents=True)
    p3_copy.mkdir(parents=True)
    (p2_copy / "graph.lbdb").write_bytes(b"old")
    (p3_copy / "index.md").write_bytes(b"old")
    catalog = RecordingProjectionCatalog()
    adapter: ProjectionPurgePort = SelfHostProjectionPurger(
        object_purger=object_store,
        catalog=cast(ProjectionCatalog, catalog),
        p2_cache_root=tmp_path / "p2-cache",
        mount_root=tmp_path / "mounts",
    )

    adapter.purge_projections(deployment_id=_DEPLOYMENT_ID, prefixes=(prefix,))
    adapter.purge_projections(deployment_id=_DEPLOYMENT_ID, prefixes=(prefix,))
    adapter.verify_projections_purged(deployment_id=_DEPLOYMENT_ID, prefixes=(prefix,))

    assert not (tmp_path / "snapshots" / prefix.root).exists()
    assert not p2_copy.exists()
    assert not p3_copy.exists()
    assert catalog.purged == (_DEPLOYMENT_ID, (prefix.root,))


def _ids(*, suffix: int) -> tuple[UUID, UUID, UUID, UUID]:
    """Return stable chunk, claim, fact, and entity IDs for one fixture row set."""
    return (
        UUID(f"74000000-0000-0000-0000-{suffix:012d}"),
        UUID(f"74000000-0000-0000-0000-{suffix + 1:012d}"),
        UUID(f"74000000-0000-0000-0000-{suffix + 2:012d}"),
        UUID(f"74000000-0000-0000-0000-{suffix + 3:012d}"),
    )


def _seed_p1(
    *, index: LanceChunkIndex, deployment_id: UUID, ids: tuple[UUID, UUID, UUID, UUID]
) -> None:
    """Write one related row into each P1 channel."""
    chunk_id, claim_id, fact_id, entity_id = ids
    vector = (0.0, 1.0)
    index.upsert_chunks(
        rows=(
            P1ChunkRow(
                chunk_id=chunk_id,
                deployment_id=deployment_id,
                doc_id=_DOC_ID,
                version_id=_VERSION_ID,
                section_role="body",
                text="fixture chunk",
                vector=vector,
            ),
        )
    )
    index.upsert_claims(
        rows=(
            P1ClaimRow(
                claim_id=claim_id,
                deployment_id=deployment_id,
                doc_id=_DOC_ID,
                chunk_id=chunk_id,
                text="fixture claim",
                is_current_testimony=True,
                is_attributed=True,
                vector=vector,
            ),
        )
    )
    index.upsert_facts(
        rows=(
            P1FactRow(
                fact_id=fact_id,
                deployment_id=deployment_id,
                kind="relation",
                label="fixture fact",
                status="active",
                valid_from=None,
                valid_until=None,
                ingested_at=_NOW,
                invalidated_at=None,
                vector=vector,
            ),
        )
    )
    index.upsert_entities(
        rows=(
            P1EntityRow(
                entity_id=entity_id,
                deployment_id=deployment_id,
                type="Person",
                canonical_name="Fixture Entity",
                vector=vector,
            ),
        )
    )
