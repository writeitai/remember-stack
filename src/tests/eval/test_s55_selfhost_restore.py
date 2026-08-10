"""S55/WP-7.7 restore drill over LocalFS, Lance, projections, and Git."""

from datetime import datetime
from datetime import timezone
from pathlib import Path
import subprocess
from typing import cast
from uuid import UUID

import pytest

from rememberstack.adapters.selfhost import LanceChunkIndex
from rememberstack.adapters.selfhost import LocalFSForgetManifestStore
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.selfhost import LocalGitRepository
from rememberstack.adapters.selfhost import ObjectAlreadyExistsError
from rememberstack.adapters.selfhost import SelfHostProjectionPurger
from rememberstack.model import current_temporal_scope
from rememberstack.model import Envelope
from rememberstack.model import ForgetManifest
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import Negative
from rememberstack.model import NegativeKind
from rememberstack.model import ObjectKey
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1EntityRow
from rememberstack.model import P1FactRow
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import ProjectionCatalog
from rememberstack.workers import DeletionService
from rememberstack.workers import ForgetKnowledgeRebuilder
from rememberstack.workers import ForgetProjectionRebuilder
from rememberstack.workers import HardForgetHandler
from rememberstack.workers import HardForgetReadiness
from rememberstack.workers import HardForgetService

_DEPLOYMENT_ID = UUID("55500000-0000-0000-0000-000000000001")
_DOC_ID = UUID("55500000-0000-0000-0000-000000000002")
_VERSION_ID = UUID("55500000-0000-0000-0000-000000000003")
_FORGET_ID = UUID("55500000-0000-0000-0000-000000000004")
_ARTIFACT_ID = UUID("55500000-0000-0000-0000-000000000005")
_FORGOTTEN_IDS = tuple(
    UUID(f"55500000-0000-0000-0000-{suffix:012d}") for suffix in range(10, 14)
)
_CONTROL_IDS = tuple(
    UUID(f"55500000-0000-0000-0000-{suffix:012d}") for suffix in range(20, 24)
)
_NOW = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
_TOKEN = "S55_REAL_UNIQUE_FORGOTTEN_TOKEN"
_CONTROL = "S55_REAL_INDEPENDENT_CONTROL"
_OBJECT_KEY = ObjectKey("raw/forgotten.txt")
_CONTROL_KEY = ObjectKey("raw/control.txt")
_PROJECTION_PREFIX = ObjectKey("snapshots/forgotten-v1")
_CONTROL_PREFIX = ObjectKey("snapshots/control-v1")


class _Catalog:
    def __init__(self) -> None:
        self.complete = False

    def preparing_record(self, *, deployment_id: UUID) -> None:
        return None

    def materialize_portable(self, *, manifest: ForgetManifest) -> None:
        return None

    def scrub_postgres(self, *, manifest: ForgetManifest) -> None:
        return None

    def verify_postgres_scrubbed(self, *, manifest: ForgetManifest) -> None:
        return None

    def mark_complete(self, *, manifest: ForgetManifest) -> None:
        self.complete = True


class _ProjectionCatalog:
    def __init__(self) -> None:
        self.prefixes = {_PROJECTION_PREFIX.root, _CONTROL_PREFIX.root}

    def purge_snapshot_prefixes(
        self, *, deployment_id: UUID, prefixes: tuple[str, ...]
    ) -> int:
        before = len(self.prefixes)
        self.prefixes.difference_update(prefixes)
        return before - len(self.prefixes)

    def snapshot_prefixes_exist(
        self, *, deployment_id: UUID, prefixes: tuple[str, ...]
    ) -> bool:
        return bool(self.prefixes.intersection(prefixes))


class _Paths:
    def blocking_k_paths(self, *, deployment_id: UUID, doc_id: UUID) -> tuple[str, ...]:
        return ()

    def k_paths_for_artifacts(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> tuple[str, ...]:
        return ("K1/affected.md",)


class _Deletion:
    def delete_lineage(self, *, deployment_id: UUID, doc_id: UUID) -> None:
        return None


class _ProjectionRebuilder:
    def rebuild_without_lineage(self, *, deployment_id: UUID, forget_id: UUID) -> None:
        return None


class _KnowledgeRebuilder:
    def recompile_without_lineage(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        return None


class _UnusedRequest:
    def request(self, **_: object) -> ForgetManifest:
        raise AssertionError("no preparing request exists in this restore canary")


def _manifest() -> ForgetManifest:
    return ForgetManifest(
        forget_id=_FORGET_ID,
        deployment_id=_DEPLOYMENT_ID,
        doc_id=_DOC_ID,
        requested_at=_NOW,
        chunk_ids=(_FORGOTTEN_IDS[0],),
        claim_ids=(_FORGOTTEN_IDS[1],),
        fact_ids=(_FORGOTTEN_IDS[2],),
        entity_ids=(_FORGOTTEN_IDS[3],),
        object_keys=(_OBJECT_KEY,),
        projection_prefixes=(_PROJECTION_PREFIX,),
        k_artifact_ids=(_ARTIFACT_ID,),
    )


def _seed_p1(
    *, index: LanceChunkIndex, ids: tuple[UUID, UUID, UUID, UUID], text: str
) -> None:
    chunk_id, claim_id, fact_id, entity_id = ids
    vector = (0.0, 1.0)
    index.upsert_chunks(
        rows=(
            P1ChunkRow(
                chunk_id=chunk_id,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=_DOC_ID,
                version_id=_VERSION_ID,
                section_role="body",
                text=text,
                vector=vector,
            ),
        )
    )
    index.upsert_claims(
        rows=(
            P1ClaimRow(
                claim_id=claim_id,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=_DOC_ID,
                chunk_id=chunk_id,
                text=text,
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
                deployment_id=_DEPLOYMENT_ID,
                kind="relation",
                label=text,
                status="active",
                vector=vector,
            ),
        )
    )
    index.upsert_entities(
        rows=(
            P1EntityRow(
                entity_id=entity_id,
                deployment_id=_DEPLOYMENT_ID,
                type="Concept",
                canonical_name=text,
                vector=vector,
            ),
        )
    )


def _seed_git(*, repository: Path) -> Path:
    _git("init", "--quiet", "-b", "main", str(repository))
    _git("-C", str(repository), "config", "user.name", "Fixture")
    _git("-C", str(repository), "config", "user.email", "fixture@example.test")
    affected = repository / "K1/affected.md"
    control = repository / "K1/control.md"
    affected.parent.mkdir(parents=True)
    affected.write_text(f"{_TOKEN}\n", encoding="utf-8")
    control.write_text(f"{_CONTROL}\n", encoding="utf-8")
    _commit(repository=repository, message="private history")
    affected.write_text("sanitized current\n", encoding="utf-8")
    _commit(repository=repository, message="owner redaction")
    backup = repository.parent / "knowledge-backup.git"
    _git("clone", "--quiet", "--mirror", str(repository), str(backup))
    return backup


def _seed_projections(
    *, snapshot_store: LocalFSObjectStore, p2_root: Path, mount_root: Path
) -> None:
    for prefix, content in ((_PROJECTION_PREFIX, _TOKEN), (_CONTROL_PREFIX, _CONTROL)):
        try:
            snapshot_store.write_bytes(
                key=ObjectKey(f"{prefix.root}/data"), content=content.encode()
            )
        except ObjectAlreadyExistsError:
            pass
    forgotten_version = Path(_PROJECTION_PREFIX.root).name
    control_version = Path(_CONTROL_PREFIX.root).name
    for root, version, content in (
        (p2_root / str(_DEPLOYMENT_ID), forgotten_version, _TOKEN),
        (p2_root / str(_DEPLOYMENT_ID), control_version, _CONTROL),
        (mount_root / str(_DEPLOYMENT_ID), f"p3-{forgotten_version}", _TOKEN),
        (mount_root / str(_DEPLOYMENT_ID), f"p3-{control_version}", _CONTROL),
    ):
        path = root / version / "data"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _contains(*, root: Path, token: str) -> bool:
    return any(
        token in path.read_text(encoding="utf-8")
        for path in root.rglob("*")
        if path.is_file()
    )


def _negative(*, token: str) -> Envelope:
    del token  # S55: the absent lookup value must not alter the public response.
    return Envelope(
        grain=Grain.EVIDENCE,
        temporal_scope=current_temporal_scope(evaluated_at=_NOW),
        freshness=Freshness(pg_live_ts=_NOW),
        negative=Negative(
            kind=NegativeKind.UNKNOWN_ENTITY, explanation="No matching memory exists."
        ),
    )


def test_real_selfhost_stores_rehonor_independent_restores(tmp_path: Path) -> None:
    """Restore whole and independent stores; readiness must purge them again."""
    objects = LocalFSObjectStore(root=tmp_path / "objects")
    snapshots = LocalFSObjectStore(root=tmp_path / "snapshots")
    lance = LanceChunkIndex(root=tmp_path / "lance")
    p2_root = tmp_path / "p2"
    mount_root = tmp_path / "mount"
    repository = tmp_path / "knowledge"
    backup = _seed_git(repository=repository)
    objects.write_bytes(key=_OBJECT_KEY, content=_TOKEN.encode())
    objects.write_bytes(key=_CONTROL_KEY, content=_CONTROL.encode())
    _seed_p1(
        index=lance,
        ids=cast(tuple[UUID, UUID, UUID, UUID], _FORGOTTEN_IDS),
        text=_TOKEN,
    )
    _seed_p1(
        index=lance,
        ids=cast(tuple[UUID, UUID, UUID, UUID], _CONTROL_IDS),
        text=_CONTROL,
    )
    _seed_projections(snapshot_store=snapshots, p2_root=p2_root, mount_root=mount_root)
    projection_catalog = _ProjectionCatalog()
    git = LocalGitRepository(
        repository=repository,
        path_catalog=_Paths(),
        author_name="RememberStack",
        author_email="rememberstack@example.test",
    )
    catalog = _Catalog()
    handler = HardForgetHandler(
        catalog=cast(ForgetCatalog, catalog),
        deletion=cast(DeletionService, _Deletion()),
        object_purgers=(objects,),
        p1=lance,
        projection_rebuilder=cast(ForgetProjectionRebuilder, _ProjectionRebuilder()),
        projection_purger=SelfHostProjectionPurger(
            object_purger=snapshots,
            catalog=cast(ProjectionCatalog, projection_catalog),
            p2_cache_root=p2_root,
            mount_root=mount_root,
        ),
        knowledge_rebuilder=cast(ForgetKnowledgeRebuilder, _KnowledgeRebuilder()),
        k_git=git,
    )
    manifest_root = tmp_path / "forget-manifests"
    manifest_root.mkdir()
    manifest_store = LocalFSForgetManifestStore(root=manifest_root)
    manifest_store.append(manifest=_manifest())
    readiness = HardForgetReadiness(
        catalog=cast(ForgetCatalog, catalog),
        manifest_store=manifest_store,
        request_service=cast(HardForgetService, _UnusedRequest()),
        handler=handler,
    )

    def assert_s55() -> None:
        with pytest.raises(FileNotFoundError):
            objects.read_bytes(key=_OBJECT_KEY)
        assert objects.read_bytes(key=_CONTROL_KEY) == _CONTROL.encode()
        assert (
            lance.chunk_vectors(
                deployment_id=str(_DEPLOYMENT_ID), chunk_ids=(str(_FORGOTTEN_IDS[0]),)
            )
            == {}
        )
        assert lance.chunk_vectors(
            deployment_id=str(_DEPLOYMENT_ID), chunk_ids=(str(_CONTROL_IDS[0]),)
        )
        assert not _contains(root=p2_root, token=_TOKEN)
        assert not _contains(root=mount_root, token=_TOKEN)
        assert _contains(root=p2_root, token=_CONTROL)
        assert _contains(root=mount_root, token=_CONTROL)
        assert (
            _output("-C", str(repository), "log", "--all", "-S", _TOKEN, "--format=%H")
            == ""
        )
        assert _CONTROL in (repository / "K1/control.md").read_text()
        assert _negative(token=_TOKEN) == _negative(token="S55_NEVER_SEEN")

    readiness.ensure_ready(deployment_id=_DEPLOYMENT_ID)
    assert catalog.complete
    assert_s55()

    def restore_objects() -> None:
        objects.write_bytes(key=_OBJECT_KEY, content=_TOKEN.encode())

    def restore_p1() -> None:
        _seed_p1(
            index=lance,
            ids=cast(tuple[UUID, UUID, UUID, UUID], _FORGOTTEN_IDS),
            text=_TOKEN,
        )

    def restore_projections() -> None:
        projection_catalog.prefixes.add(_PROJECTION_PREFIX.root)
        _seed_projections(
            snapshot_store=snapshots, p2_root=p2_root, mount_root=mount_root
        )

    def restore_git() -> None:
        _git(
            "-C",
            str(repository),
            "fetch",
            "--quiet",
            "--force",
            str(backup),
            "refs/heads/main:refs/rememberstack/restore",
        )
        _git("-C", str(repository), "reset", "--hard", "refs/rememberstack/restore")
        _git("-C", str(repository), "update-ref", "-d", "refs/rememberstack/restore")

    restorers = (restore_objects, restore_p1, restore_projections, restore_git)
    for restore in restorers:
        restore()
    readiness.ensure_ready(deployment_id=_DEPLOYMENT_ID)
    assert_s55()

    for restore in restorers:
        restore()
        readiness.ensure_ready(deployment_id=_DEPLOYMENT_ID)
        assert_s55()


def _git(*arguments: str) -> None:
    subprocess.run(("git", *arguments), check=True, capture_output=True, text=True)


def _output(*arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(*, repository: Path, message: str) -> None:
    _git("-C", str(repository), "add", "-A")
    _git("-C", str(repository), "commit", "--quiet", "-m", message)
