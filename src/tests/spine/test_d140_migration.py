"""PostgreSQL proofs for migration p9_38_0059 (D140).

The upgrade fills the selection projection for every existing lineage (all
undeclared), refuses a store whose lineage-grain ``document_crossrefs`` holds
rows, and recreates the reference objects and the live graph. The downgrade
restores the prior schema when no D140 data exists and refuses otherwise.
"""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.spine.catalog_contract import verify_schema
from rememberstack.spine.graph_catalog import graph_catalog_problems
from rememberstack.spine.settings import load_database_settings
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_BEFORE = "p9_37_0058"
_HEAD = "p9_38_0059"
_DEPLOYMENT_ID = UUID("14000000-0000-0000-0000-0000000000a1")


@pytest.fixture()
def migrated() -> Iterator[tuple[Config, Engine]]:
    """A store at the revision before D140; restored to head afterwards."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for PostgreSQL proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision=_BEFORE)
    engine = create_engine(database_url)
    try:
        yield config, engine
    finally:
        engine.dispose()
        reset_database(config=config)
        command.upgrade(config=config, revision="head")


def _revision(engine: Engine) -> str:
    """The applied Alembic revision."""
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()


def _lineage(
    connection: Connection,
    *,
    ref: str,
    versions: int,
    served: int | None,
    deleted_versions: tuple[int, ...] = (),
    deleted: bool = False,
) -> tuple[UUID, list[UUID]]:
    """One lineage with ready versions; ``served`` is the 1-based served one."""
    doc_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO documents (doc_id, deployment_id, source_kind, source_ref,"
            " title, deleted_at) VALUES (:doc, :d, 'intranet', :ref, :ref,"
            " CASE WHEN :deleted THEN now() END)"
        ),
        {"doc": doc_id, "d": _DEPLOYMENT_ID, "ref": ref, "deleted": deleted},
    )
    version_ids: list[UUID] = []
    for number in range(1, versions + 1):
        version_id, representation_id = uuid4(), uuid4()
        content_hash = f"{ref}-{number}"
        connection.execute(
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                " raw_uri) VALUES (:d, :h, 'text/markdown', 'raw')"
            ),
            {"d": _DEPLOYMENT_ID, "h": content_hash},
        )
        connection.execute(
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, status, deleted_at) VALUES (:v, :d,"
                " :doc, :h, :n, 'ready', CASE WHEN :gone THEN now() END)"
            ),
            {
                "v": version_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "h": content_hash,
                "n": number,
                "gone": number in deleted_versions,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, status)"
                " VALUES (:r, :d, :v, 'passthrough', 'ready')"
            ),
            {"r": representation_id, "d": _DEPLOYMENT_ID, "v": version_id},
        )
        connection.execute(
            text(
                "UPDATE document_versions SET current_representation_id = :r"
                " WHERE version_id = :v"
            ),
            {"r": representation_id, "v": version_id},
        )
        version_ids.append(version_id)
    if served is not None:
        connection.execute(
            text("UPDATE documents SET current_version_id = :v WHERE doc_id = :doc"),
            {"v": version_ids[served - 1], "doc": doc_id},
        )
    return doc_id, version_ids


def _seed(engine: Engine) -> dict[str, tuple[UUID, list[UUID]]]:
    """A deployment with the lineage shapes the fill must handle."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO deployments (deployment_id, slug, name, raw_bucket,"
                " artifacts_bucket, corpusfs_bucket) VALUES (:d, 'd140-migration',"
                " 'D140 migration', 'mem://raw', 'mem://artifacts', 'mem://corpusfs')"
            ),
            {"d": _DEPLOYMENT_ID},
        )
        return {
            "served": _lineage(
                connection, ref="served", versions=3, served=2, deleted_versions=(3,)
            ),
            "unserved": _lineage(connection, ref="unserved", versions=1, served=None),
            "deleted": _lineage(
                connection, ref="deleted", versions=1, served=1, deleted=True
            ),
        }


def _projection(engine: Engine) -> dict[UUID, tuple[str, bool, bool]]:
    """Every projection row as (in_force text, periodised, selectable)."""
    with engine.connect() as connection:
        return {
            row[0]: (row[1], row[2], row[3])
            for row in connection.execute(
                text(
                    "SELECT version_id, in_force::text, periodised, selectable"
                    " FROM document_version_scope"
                )
            )
        }


def test_the_upgrade_fills_the_projection_for_every_existing_lineage(
    migrated: tuple[Config, Engine],
) -> None:
    """Undeclared: the served version is unbounded, the others empty."""
    config, engine = migrated
    lineages = _seed(engine)

    command.upgrade(config=config, revision=_HEAD)

    _, (first, second, _deleted) = lineages["served"]
    _, (only,) = lineages["unserved"]
    assert _projection(engine) == {
        first: ("{}", False, True),
        second: ("{(,)}", False, True),
        only: ("{}", False, True),
    }
    with engine.connect() as connection:
        assert graph_catalog_problems(connection=connection) == ()
    # the fill equals a rebuild of every live lineage
    before = _projection(engine)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "SELECT refresh_document_version_scope(deployment_id, doc_id)"
                    " FROM documents"
                )
            )
            rebuilt = {
                row[0]: (row[1], row[2], row[3])
                for row in connection.execute(
                    text(
                        "SELECT version_id, in_force::text, periodised, selectable"
                        " FROM document_version_scope"
                    )
                )
            }
        finally:
            transaction.rollback()
    assert rebuilt == before


def test_an_empty_store_downgrades_and_upgrades_again(
    migrated: tuple[Config, Engine],
) -> None:
    """Without D140 data the downgrade restores the prior objects exactly."""
    config, engine = migrated
    _seed(engine)
    command.upgrade(config=config, revision=_HEAD)

    command.downgrade(config=config, revision=_BEFORE)

    assert _revision(engine) == _BEFORE
    with engine.connect() as connection:
        kinds = connection.execute(
            text("SELECT enum_range(NULL::crossref_kind)::text")
        ).scalar_one()
        assert kinds == "{cites,links_to,attaches,replies_to}"
        for relation in (
            "document_version_scope",
            "document_effective_periods",
            "document_effective_time_events",
            "document_reference_generations",
            "memory_v1.chunks_all_versions_live",
            "memory_v1.document_effective_periods_live",
        ):
            assert (
                connection.execute(
                    text("SELECT to_regclass(:name)"), {"name": relation}
                ).scalar_one()
                is None
            ), relation
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name IN ('chunks', 'document_sections',"
                    " 'document_versions', 'document_crossrefs')"
                    " AND column_name IN ('text_origin_at', 'reuse_identity_hash',"
                    " 'section_key', 'own_content_hash', 'subtree_content_hash',"
                    " 'version_key', 'from_version_id', 'generation_id')"
                )
            ).scalar_one()
            == 0
        )
        assert graph_catalog_problems(connection=connection) == ()
    command.upgrade(config=config, revision=_HEAD)
    assert _revision(engine) == _HEAD
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    with engine.connect() as connection:
        verify_schema(connection=connection)


@pytest.mark.parametrize(
    "d140_row",
    [
        "UPDATE document_versions SET version_key = 'edition-1'"
        " WHERE version_id = :version",
        "INSERT INTO document_effective_periods (period_id, deployment_id, doc_id,"
        " version_id, effective_from, declared_by, retracted_at)"
        " VALUES (gen_random_uuid(), :d, :doc, :version, now(), 'ingest', now())",
        "INSERT INTO document_effective_time_events (deployment_id, doc_id, event)"
        " VALUES (:d, :doc, 'cleared')",
        "INSERT INTO document_reference_generations (generation_id, deployment_id,"
        " doc_id, version_id, origin, input_hash, request_seq, artifact_uri, status)"
        " VALUES (gen_random_uuid(), :d, :doc, :version, 'supplied', 'h', 1,"
        " 'refs.ndjson', 'pending')",
    ],
)
def test_the_downgrade_refuses_when_d140_data_exists(
    migrated: tuple[Config, Engine], d140_row: str
) -> None:
    """A version key, a declaration (even retracted), an event or a generation."""
    config, engine = migrated
    lineages = _seed(engine)
    command.upgrade(config=config, revision=_HEAD)
    doc_id, (version_id, *_) = lineages["served"]
    with engine.begin() as connection:
        connection.execute(
            text(d140_row), {"d": _DEPLOYMENT_ID, "doc": doc_id, "version": version_id}
        )

    with pytest.raises(RuntimeError, match="D140 downgrade requires"):
        command.downgrade(config=config, revision=_BEFORE)
    assert _revision(engine) == _HEAD


def test_the_upgrade_refuses_lineage_grain_crossrefs(
    migrated: tuple[Config, Engine],
) -> None:
    """A reference naming no source version cannot be given one by guessing."""
    config, engine = migrated
    lineages = _seed(engine)
    source, _ = lineages["served"]
    target, _ = lineages["unserved"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO document_crossrefs (crossref_id, deployment_id,"
                " from_doc_id, to_doc_id, kind, resolved) VALUES (:x, :d, :s, :t,"
                " 'cites', true)"
            ),
            {"x": uuid4(), "d": _DEPLOYMENT_ID, "s": source, "t": target},
        )

    with pytest.raises(Exception, match="document_crossrefs holds rows"):
        command.upgrade(config=config, revision=_HEAD)
    assert _revision(engine) == _BEFORE


def test_references_are_visible_only_through_their_active_generation(
    migrated: tuple[Config, Engine],
) -> None:
    """The public view and the graph edge read active generations of live versions."""
    config, engine = migrated
    lineages = _seed(engine)
    command.upgrade(config=config, revision=_HEAD)
    source, (old_version, served_version, _) = lineages["served"]
    target, _ = lineages["unserved"]
    with engine.begin() as connection:
        for version_id, status in ((served_version, "active"), (old_version, "active")):
            generation_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO document_reference_generations (generation_id,"
                    " deployment_id, doc_id, version_id, origin, input_hash,"
                    " request_seq, artifact_uri, status) VALUES (:g, :d, :doc, :v,"
                    " 'supplied', 'h', 1, 'refs.ndjson', :status)"
                ),
                {
                    "g": generation_id,
                    "d": _DEPLOYMENT_ID,
                    "doc": source,
                    "v": version_id,
                    "status": status,
                },
            )
            # two references to the same target and kind from each version
            for section in ("approvals", "per-diem"):
                connection.execute(
                    text(
                        "INSERT INTO document_crossrefs (crossref_id, deployment_id,"
                        " from_doc_id, from_version_id, generation_id,"
                        " from_section_key, kind, origin, to_source_kind,"
                        " to_source_ref, to_doc_id, resolved) VALUES (:x, :d, :s,"
                        " :v, :g, :section, 'refers_to', 'supplied', 'intranet',"
                        " 'unserved', :t, true)"
                    ),
                    {
                        "x": uuid4(),
                        "d": _DEPLOYMENT_ID,
                        "s": source,
                        "v": version_id,
                        "g": generation_id,
                        "section": section,
                        "t": target,
                    },
                )
    with engine.connect() as connection:
        public = connection.execute(
            text(
                "SELECT from_version_id, from_section_key, binding, origin"
                " FROM memory_v1.document_crossrefs_live ORDER BY 1, 2"
            )
        ).all()
        edges = connection.execute(
            text(
                "SELECT from_doc_id, to_doc_id, kind"
                " FROM rememberstack_graph_internal.crossrefs_live"
            )
        ).all()
    # the public view has version grain: both versions' references
    assert len(public) == 4
    assert {row.binding for row in public} == {"floating"}
    # the graph has lineage grain, from the version in force now (the served one)
    assert [tuple(row) for row in edges] == [(source, target, "refers_to")]

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_reference_generations SET status = 'superseded'"
                " WHERE version_id = :v"
            ),
            {"v": served_version},
        )
    with engine.connect() as connection:
        remaining = connection.execute(
            text(
                "SELECT DISTINCT from_version_id FROM memory_v1.document_crossrefs_live"
            )
        ).scalars()
        assert list(remaining) == [old_version]
        assert (
            connection.execute(
                text("SELECT count(*) FROM rememberstack_graph_internal.crossrefs_live")
            ).scalar_one()
            == 0
        )
