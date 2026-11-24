"""D159 upgrade: a D135-deleted document becomes an archive that unarchive undoes.

Design §5 test 13 (the D135 part). The real chain builds a document and its
fact; the internal lineage removal produces exactly the state D135's delete
left (lineage and versions tombstoned at one instant, the episode's currency
events and closure recorded). The store is then taken back to the previous
release's schema, a second lineage is marked connector-deleted and a third is
owned by a D74 forget, and the upgrade runs: the deletions become caller and
source archives whose episode carries the archive reconciliation id, so
unarchive restores the testimony and reopens the fact. The D74 lineage is left
to the forget slice.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.lifecycle import archive_reconciliation_id
from rememberstack.spine.migrations.versions import p9_54_0075_archive as migration
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import DocumentArchiver
from tests.database_reset import reset_database
from tests.workers.test_lifecycle_reconciliation import _DEPLOYMENT_ID
from tests.workers.test_lifecycle_reconciliation import _FACT_SENTENCE
from tests.workers.test_lifecycle_reconciliation import _LifecycleRig
from tests.workers.test_lifecycle_reconciliation import _TABLES
from tests.workers.test_lifecycle_reconciliation import _withdraw_support

_ROOT = Path(__file__).resolve().parents[3]
_PREVIOUS = "p9_53_0074"


@pytest.fixture(scope="module")
def migration_config() -> Iterator[tuple[Config, Engine]]:
    """A store at head, reset before and after the module."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for the upgrade proof")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield config, engine
    finally:
        engine.dispose()
        reset_database(config=config)
        command.upgrade(config=config, revision="head")


def test_archive_reconciliation_id_matches_the_frozen_upgrade_copy() -> None:
    """The upgrade relabels with the same id the runtime derives."""
    doc_id = uuid4()
    at = datetime.fromisoformat("2026-11-20T10:11:12.123456+01:00")
    assert migration.archive_reconciliation_id(
        doc_id=doc_id, archived_at=at
    ) == archive_reconciliation_id(doc_id=doc_id, archived_at=at)


def _bootstrap(engine: Engine) -> None:
    """A fresh deployment and empty lifecycle tables."""
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
        for table in _TABLES:
            connection.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="archive-upgrade",
            name="Archive upgrade proof",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def _scalar(engine: Engine, sql: str, **params: object) -> object:
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar()


def test_d135_deletions_become_archives_that_unarchive_undoes(
    migration_config: tuple[Config, Engine], tmp_path: Path
) -> None:
    config, engine = migration_config
    _bootstrap(engine)
    rig = _LifecycleRig(engine=engine, root=tmp_path)
    caller = rig.observe(source_ref="caller.md", content=f"{_FACT_SENTENCE}\n")
    source = rig.observe(source_ref="source.md", content="Acme opened an office.\n")
    forgotten = rig.observe(source_ref="d74.md", content="Bob runs the depot.\n")
    rig.drain()
    assert rig.relation()["invalidated_at"] is None
    for added in (caller, source, forgotten):
        rig.deletion.delete_lineage(deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id)
    assert rig.relation()["invalidated_at"] is not None
    deleted_at = _scalar(
        engine, "SELECT deleted_at FROM documents WHERE doc_id = :d", d=caller.doc_id
    )
    assert isinstance(deleted_at, datetime)

    command.downgrade(config=config, revision=_PREVIOUS)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE documents SET deleted_sync_cycle_id = :cycle WHERE doc_id = :d"
            ),
            {"cycle": uuid4(), "d": source.doc_id},
        )
        connection.execute(
            text(
                "INSERT INTO forget_manifests (forget_id, deployment_id, doc_id,"
                " schema_version, manifest_hash, manifest, accepted_at, status,"
                " completed_at) VALUES (:f, :dep, :d, 1, 'h', '{}', now(),"
                " 'complete', now())"
            ),
            {"f": uuid4(), "dep": _DEPLOYMENT_ID, "d": forgotten.doc_id},
        )
    command.upgrade(config=config, revision="head")

    with engine.connect() as connection:
        rows = {
            row["doc_id"]: row
            for row in connection.execute(
                text(
                    "SELECT doc_id, archived_at, archived_by, deleted_at FROM documents"
                )
            ).mappings()
        }
    assert rows[caller.doc_id]["archived_at"] == deleted_at
    assert rows[caller.doc_id]["archived_by"] == "caller"
    assert rows[caller.doc_id]["deleted_at"] is None
    assert rows[source.doc_id]["archived_by"] == "source"
    assert rows[forgotten.doc_id]["archived_at"] is None  # the forget slice's
    assert rows[forgotten.doc_id]["deleted_at"] is not None
    assert (
        _scalar(
            engine,
            "SELECT count(*) FROM document_versions"
            " WHERE doc_id = :d AND deleted_at IS NOT NULL",
            d=caller.doc_id,
        )
        == 0
    )  # only the lineage deletion's version tombstones are cleared
    rid = archive_reconciliation_id(doc_id=caller.doc_id, archived_at=deleted_at)
    assert (
        _scalar(
            engine,
            "SELECT count(*) FROM testimony_currency_events"
            " WHERE doc_id = :d AND reconciliation_id = :r"
            " AND reason = 'version_archived'",
            d=caller.doc_id,
            r=rid,
        )
        == 1
    )
    assert (
        _scalar(
            engine,
            "SELECT count(*) FROM relation_adjudications"
            " WHERE outcome = 'retracted_source_removal'"
            " AND features ->> 'reconciliation_id' = :r"
            " AND (features ->> 'archive')::boolean",
            r=str(rid),
        )
        == 1
    )

    restored = DocumentArchiver(
        engine=engine, profile_refresher=rig.profile_refresher
    ).unarchive_document(deployment_id=_DEPLOYMENT_ID, doc_id=caller.doc_id)

    assert (restored.claims_restored, restored.facts_reopened) == (1, 1)
    assert rig.relation()["invalidated_at"] is None
    assert rig.relation()["evidence_count"] == 1
    assert (
        _scalar(
            engine,
            "SELECT archived_at FROM documents WHERE doc_id = :d",
            d=caller.doc_id,
        )
        is None
    )


def test_a_closure_without_a_currency_event_and_older_tombstones(
    migration_config: tuple[Config, Engine], tmp_path: Path
) -> None:
    """A deletion that closed a fact without retiring a claim (the claim had
    already lost currency to a re-extraction) is still found by its run id
    and relabelled; a version tombstone from before the lineage's deletion
    survives the upgrade."""
    config, engine = migration_config
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    _bootstrap(engine)
    rig = _LifecycleRig(engine=engine, root=tmp_path)
    first = rig.observe(
        source_ref="reviewed.md",
        content=f"{_FACT_SENTENCE}\n",
        versioning_mode="snapshot",
    )
    rig.drain()
    older = rig.observe(
        source_ref="reviewed.md",
        content="Acme opened an office.\n",
        versioning_mode="snapshot",
    )
    rig.drain()
    rig.deletion.delete_version(version_id=older.version_id)
    _withdraw_support(rig, first.doc_id)
    rig.deletion.delete_lineage(deployment_id=_DEPLOYMENT_ID, doc_id=first.doc_id)
    assert rig.relation()["invalidated_at"] is not None

    command.downgrade(config=config, revision=_PREVIOUS)
    command.upgrade(config=config, revision="head")

    assert (
        _scalar(
            engine,
            "SELECT count(*) FROM relation_adjudications"
            " WHERE outcome = 'retracted_source_removal'"
            " AND (features ->> 'archive')::boolean",
        )
        == 1
    )
    assert (
        _scalar(
            engine,
            "SELECT deleted_at IS NOT NULL FROM document_versions WHERE version_id = :v",
            v=older.version_id,
        )
        is True
    )
    assert (
        _scalar(
            engine,
            "SELECT deleted_at IS NULL FROM document_versions WHERE version_id = :v",
            v=first.version_id,
        )
        is True
    )

    reopened = DocumentArchiver(
        engine=engine, profile_refresher=rig.profile_refresher
    ).unarchive_document(deployment_id=_DEPLOYMENT_ID, doc_id=first.doc_id)

    # the claim lost currency to the re-extraction, not to the archive, and
    # the deletion had answered its review: nothing supports the fact, which
    # stays closed — the relabelled closure is simply not reopened
    assert (reopened.claims_restored, reopened.facts_reopened) == (0, 0)
    assert rig.relation()["invalidated_at"] is not None


def test_the_upgrade_leaves_documents_live_inert_below_it(
    migration_config: tuple[Config, Engine],
) -> None:
    """Downgrading keeps the appended view column as a NULL, and re-upgrading
    restores the live column."""
    config, engine = migration_config
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    command.downgrade(config=config, revision=_PREVIOUS)
    column = (
        "SELECT data_type FROM information_schema.columns"
        " WHERE table_schema = 'memory_v1' AND table_name = 'documents_live'"
        " AND column_name = 'archived_at'"
    )
    assert _scalar(engine, column) == "timestamp with time zone"
    assert (
        _scalar(
            engine,
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name = 'documents' AND column_name = 'archived_at'",
        )
        == 0
    )
    command.upgrade(config=config, revision="head")
    assert _scalar(engine, column) == "timestamp with time zone"
    assert _scalar(
        engine,
        "SELECT to_regprocedure("
        "'rememberstack_graph_internal.fact_believed_at(text, uuid, timestamptz)') IS NOT NULL",
    )
