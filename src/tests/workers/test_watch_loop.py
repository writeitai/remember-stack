"""WP-3.1/3.2 acceptance: versioned ingest + the watch loop, end to end.

A watched directory drives the full lifecycle front door: cycle rows, the
no-fetch revision no-op, the debounce window, a changed file becoming a new
VERSION of its lineage (the full chain runs on it, the old version
superseded), and source deletion tombstoning the lineage — loud, recorded.
"""

from collections.abc import Iterator
import os
from pathlib import Path
import time
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LocalDirectoryWatcher
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.core import ConversionRouter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import RunResultOutcome
from rememberstack.model import SyntheticRootRecord
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import SyncCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import ConvertHandler
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import StructureHandler
from rememberstack.workers import SyncCycleRunner
from rememberstack.workers import SyncSettings
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("c1000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL watch proofs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def bootstrapped_deployment(database_engine: Engine) -> None:
    """A fresh deployment per proof."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="watch-test",
            name="Watch loop proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


class _WatchRig:
    """A watched directory + the E0 chain + the cycle runner."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        """Compose the watcher, ingest path, and the convert/structure chain."""
        self.engine = engine
        self.source_dir = root / "watched"
        self.watcher = LocalDirectoryWatcher(root=self.source_dir)
        raw_store = LocalFSObjectStore(root=root / "raw")
        artifact_store = LocalFSObjectStore(root=root / "artifacts")
        catalog = DocumentCatalog(engine=engine)
        self.catalog = catalog
        self.runner = SyncCycleRunner(
            catalog=SyncCatalog(engine=engine),
            ingestor=UploadIngestor(
                catalog=catalog,
                raw_store=raw_store,
                admission=ForgetCatalog(engine=engine),
                routable_mimes=frozenset({"text/markdown"}),
            ),
            settings=SyncSettings(debounce_quiet_seconds=0.0),
        )
        registry = HandlerRegistry()
        registry.register(
            stage=PipelineStage.CONVERT,
            handler=ConvertHandler(
                catalog=catalog,
                raw_store=raw_store,
                artifact_store=artifact_store,
                router=ConversionRouter(
                    routes={"text/markdown": MarkdownPassthroughConverter()}
                ),
            ),
        )
        registry.register(
            stage=PipelineStage.STRUCTURE,
            handler=StructureHandler(catalog=catalog, artifact_store=artifact_store),
        )
        self.worker = Worker(
            ledger=WorkLedger(
                engine=engine,
                settings=WorkLedgerSettings(
                    retry_backoff_base_s=0.0, retry_backoff_max_s=0.0
                ),
            ),
            registry=registry,
        )

    def cycle(self, source: object = None):
        """One recorded poll pass (optionally through a wrapped source)."""
        return self.runner.run_cycle(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="watched_directory",
            source=source or self.watcher,  # type: ignore[arg-type]
        )

    def drain_e0(self) -> None:
        """Run convert + structure until idle."""
        for stage in (PipelineStage.CONVERT, PipelineStage.STRUCTURE):
            while (
                self.worker.run_one(
                    deployment_id=_DEPLOYMENT_ID,
                    stage=stage,
                    lane=ProcessingLane.STEADY,
                ).outcome
                is not RunResultOutcome.NO_WORK
            ):
                pass

    def write(self, *, name: str, content: str, mtime_offset: float = -300.0) -> None:
        """Write a source file, backdated past the debounce window."""
        path = self.source_dir / name
        path.write_text(content)
        stamp = time.time() + mtime_offset
        os.utime(path, (stamp, stamp))


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _WatchRig:
    """A fresh watched rig per proof."""
    return _WatchRig(engine=database_engine, root=tmp_path)


def test_edit_becomes_a_new_version_of_the_same_lineage(rig: _WatchRig) -> None:
    """The D55 heart: same source_ref across edits = one lineage, two
    versions; the chain runs on each; the old version is superseded when
    the new becomes current; no-op cycles ingest nothing."""
    rig.write(name="roster.md", content="# Roster\n\nAlice leads the team.\n")
    first = rig.cycle()
    assert len(first.ingested) == 1
    rig.drain_e0()

    # an unchanged poll: revision no-op — nothing fetched, nothing ingested
    second = rig.cycle()
    assert second.ingested == ()
    assert second.unchanged == 1

    rig.write(name="roster.md", content="# Roster\n\nBob leads the team.\n")
    third = rig.cycle()
    assert len(third.ingested) == 1
    rig.drain_e0()

    with rig.engine.connect() as connection:
        lineages = connection.execute(
            text(
                "SELECT count(*) FROM documents WHERE deployment_id = :d"
                " AND source_kind = 'watched_directory'"
            ),
            {"d": _DEPLOYMENT_ID},
        ).scalar_one()
        versions = (
            connection.execute(
                text(
                    "SELECT version_no, status::text AS status, superseded_at,"
                    " sync_cycle_id, source_modified_at"
                    " FROM document_versions ORDER BY version_no"
                )
            )
            .mappings()
            .all()
        )
        mode = connection.execute(
            text(
                "SELECT versioning_mode::text FROM documents WHERE source_kind ="
                " 'watched_directory'"
            )
        ).scalar_one()
        current = connection.execute(
            text(
                "SELECT v.version_no FROM documents d"
                " JOIN document_versions v ON v.version_id = d.current_version_id"
                " WHERE d.source_kind = 'watched_directory'"
            )
        ).scalar_one()
    assert lineages == 1  # one lineage across the edit
    assert [row["version_no"] for row in versions] == [1, 2]
    assert versions[0]["superseded_at"] is not None  # v1 superseded
    assert versions[1]["superseded_at"] is None
    assert all(row["status"] == "ready" for row in versions)
    assert all(row["sync_cycle_id"] is not None for row in versions)
    assert all(row["source_modified_at"] is not None for row in versions)
    assert mode == "living"  # the edit-in-place heuristic
    assert current == 2

    # Codex review: a DELAYED completion of the older version (an out-of-order
    # or replayed chain finish) must never drag the currency pointer back.
    with rig.engine.connect() as connection:
        stale = (
            connection.execute(
                text(
                    "SELECT doc_id, version_id, current_representation_id"
                    " FROM document_versions WHERE version_no = 1"
                )
            )
            .mappings()
            .one()
        )
    rig.catalog.record_synthetic_root(
        record=SyntheticRootRecord(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=stale["doc_id"],
            version_id=stale["version_id"],
            representation_id=stale["current_representation_id"],
            block_count=1,
            markdown_chars=10,
            title="roster",
            structurer_version="synthetic-root-1",
        )
    )
    with rig.engine.connect() as connection:
        still_current = connection.execute(
            text(
                "SELECT v.version_no FROM documents d"
                " JOIN document_versions v ON v.version_id = d.current_version_id"
            )
        ).scalar_one()
    assert still_current == 2  # the pointer only moves forward


def test_initial_sync_can_route_the_corpus_to_backfill(rig: _WatchRig) -> None:
    """The sync setting routes an initial watched corpus without a second ingest path."""
    rig.runner._settings = SyncSettings(  # noqa: SLF001 - integration composition
        debounce_quiet_seconds=0.0, lane=ProcessingLane.BACKFILL
    )
    rig.write(name="archive.md", content="# Archive\n\nHistorical material.\n")

    result = rig.cycle()

    assert len(result.ingested) == 1
    with rig.engine.connect() as connection:
        lane = connection.execute(
            text(
                "SELECT lane::text FROM processing_state"
                " WHERE target_id = :version_id AND stage = 'convert'"
            ),
            {"version_id": result.ingested[0]},
        ).scalar_one()
    assert lane == ProcessingLane.BACKFILL.value


def test_debounce_coalesces_active_edits(rig: _WatchRig) -> None:
    """A freshly modified file waits out the quiet window."""
    rig.runner._settings = SyncSettings(debounce_quiet_seconds=3600.0)  # noqa: SLF001
    rig.write(name="live.md", content="editing right now\n", mtime_offset=-1.0)
    summary = rig.cycle()
    assert summary.debounced == 1
    assert summary.ingested == ()
    # once quiet long enough, the next cycle ingests exactly one version:
    rig.write(name="live.md", content="editing right now\n", mtime_offset=-7200.0)
    settled = rig.cycle()
    assert len(settled.ingested) == 1


def test_source_deletion_tombstones_the_lineage(rig: _WatchRig) -> None:
    """Delete observed → the lineage is tombstoned (audit-visible), and the
    observation is idempotent across cycles."""
    rig.write(name="gone.md", content="short-lived\n")
    rig.cycle()
    (rig.source_dir / "gone.md").unlink()
    removal = rig.cycle()
    assert len(removal.deletions_observed) == 1
    again = rig.cycle()
    assert again.deletions_observed == ()  # already tombstoned: no re-fire
    with rig.engine.connect() as connection:
        tombstone = (
            connection.execute(
                text(
                    "SELECT deleted_at, deleted_sync_cycle_id"
                    " FROM documents WHERE source_ref = 'gone.md'"
                )
            )
            .mappings()
            .one()
        )
        cycles = connection.execute(
            text(
                "SELECT count(*) FROM connector_sync_cycles"
                " WHERE completed_at IS NOT NULL"
            )
        ).scalar_one()
    assert tombstone["deleted_at"] is not None
    # the deletion is stamped with the cycle that observed it (D55 barrier):
    assert tombstone["deleted_sync_cycle_id"] == removal.cycle_id
    assert cycles >= 3  # every pass recorded and completed


def test_recreated_source_resurrects_its_lineage(rig: _WatchRig) -> None:
    """Codex review: delete-and-recreate must self-heal (D55) — the recreated
    file is the SAME lineage, live again, not a permanently dead row that
    gets refetched forever."""
    rig.write(name="phoenix.md", content="first life\n", mtime_offset=-400.0)
    rig.cycle()
    (rig.source_dir / "phoenix.md").unlink()
    rig.cycle()
    rig.write(name="phoenix.md", content="second life\n", mtime_offset=-200.0)
    revived = rig.cycle()
    assert len(revived.ingested) == 1
    with rig.engine.connect() as connection:
        lineage = (
            connection.execute(
                text(
                    "SELECT deleted_at, deleted_sync_cycle_id"
                    " FROM documents WHERE source_ref = 'phoenix.md'"
                )
            )
            .mappings()
            .one()  # .one() also proves it stayed a single lineage
        )
        top_version = connection.execute(
            text("SELECT max(version_no) FROM document_versions")
        ).scalar_one()
    assert lineage["deleted_at"] is None
    assert lineage["deleted_sync_cycle_id"] is None
    assert top_version == 2  # a new version on the resurrected lineage


class _CountingSource:
    """Wrap a watched source to count fetches (proving the no-fetch exits)."""

    def __init__(self, inner: LocalDirectoryWatcher) -> None:
        """Wrap the real watcher."""
        self.inner = inner
        self.fetches = 0

    def poll(self, *, known):  # noqa: ANN001, ANN201 — port passthrough
        """Delegate."""
        return self.inner.poll(known=known)

    def fetch(self, *, source_ref: str) -> bytes:
        """Count, then delegate."""
        self.fetches += 1
        return self.inner.fetch(source_ref=source_ref)


def test_revision_churn_with_same_bytes_fetches_once(rig: _WatchRig) -> None:
    """Codex review: a touch (new revision, identical bytes) is fetched once —
    the content-hash no-op advances the revision cursor, so later polls take
    the no-fetch exit instead of refetching forever."""
    counting = _CountingSource(rig.watcher)
    rig.write(name="touched.md", content="stable words\n", mtime_offset=-400.0)
    first = rig.cycle(source=counting)
    assert len(first.ingested) == 1
    assert counting.fetches == 1
    rig.write(name="touched.md", content="stable words\n", mtime_offset=-200.0)
    churn = rig.cycle(source=counting)
    assert churn.unchanged == 1  # fetched, recognized as the same content
    assert counting.fetches == 2
    settled = rig.cycle(source=counting)
    assert settled.unchanged == 1  # revision no-op: cursor advanced, no fetch
    assert counting.fetches == 2


def test_content_revert_becomes_a_new_forward_version(rig: _WatchRig) -> None:
    """Codex review: content reverted A→B→A is a new observation — a third
    version moving the lineage forward, never a silent fall-back to the old
    version while the pointer serves stale content."""
    for offset, content in ((-600.0, "state A\n"), (-400.0, "state B\n")):
        rig.write(name="revert.md", content=content, mtime_offset=offset)
        rig.cycle()
        rig.drain_e0()
    rig.write(name="revert.md", content="state A\n", mtime_offset=-200.0)
    reverted = rig.cycle()
    assert len(reverted.ingested) == 1
    rig.drain_e0()
    with rig.engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT version_no, content_hash FROM document_versions"
                    " ORDER BY version_no"
                )
            )
            .mappings()
            .all()
        )
        current = connection.execute(
            text(
                "SELECT v.version_no FROM documents d"
                " JOIN document_versions v ON v.version_id = d.current_version_id"
            )
        ).scalar_one()
    assert [row["version_no"] for row in rows] == [1, 2, 3]
    assert rows[2]["content_hash"] == rows[0]["content_hash"]  # same object
    assert current == 3


class _FlakySource:
    """Wrap a watched source so fetching one ref always fails."""

    def __init__(self, inner: LocalDirectoryWatcher, *, poison: str) -> None:
        """Wrap the real watcher; `poison` is the ref that fails."""
        self.inner = inner
        self.poison = poison

    def poll(self, *, known):  # noqa: ANN001, ANN201 — port passthrough
        """Delegate."""
        return self.inner.poll(known=known)

    def fetch(self, *, source_ref: str) -> bytes:
        """Fail on the poisoned ref, delegate otherwise."""
        if source_ref == self.poison:
            raise ConnectionError("source went away mid-cycle")
        return self.inner.fetch(source_ref=source_ref)


def test_one_bad_item_does_not_strand_the_cycle(rig: _WatchRig) -> None:
    """Codex review: a per-item failure is counted on the cycle row, the rest
    of the pass lands, and the cycle still completes — an eternally open
    cycle could never pass reconciliation's finalization barrier."""
    rig.write(name="good.md", content="lands fine\n")
    rig.write(name="bad.md", content="never fetched\n")
    summary = rig.cycle(source=_FlakySource(rig.watcher, poison="bad.md"))
    assert summary.failed == 1
    assert len(summary.ingested) == 1
    with rig.engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT completed_at, failed_items FROM connector_sync_cycles"
                    " WHERE cycle_id = :cycle_id"
                ),
                {"cycle_id": summary.cycle_id},
            )
            .mappings()
            .one()
        )
    assert row["completed_at"] is not None
    assert row["failed_items"] == 1
    # the poisoned file is not lost — a later healthy cycle picks it up:
    healed = rig.cycle()
    assert len(healed.ingested) == 1


def test_symlinks_escaping_the_root_are_refused(rig: _WatchRig, tmp_path: Path) -> None:
    """Codex review: a symlink pointing outside the watched root must neither
    be polled nor fetchable — the watcher is not a read primitive for the
    rest of the filesystem."""
    outside = tmp_path / "outside-secret.md"
    outside.write_text("not yours\n")
    rig.write(name="honest.md", content="fine\n")
    (rig.source_dir / "sneaky.md").symlink_to(outside)
    summary = rig.cycle()
    assert summary.observed == 1  # only honest.md; the escape was skipped
    with pytest.raises(PermissionError):
        rig.watcher.fetch(source_ref="../outside-secret.md")
