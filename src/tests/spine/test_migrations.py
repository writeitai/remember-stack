"""Real-PostgreSQL lifecycle tests for the Phase 0 Alembic schema chain."""

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text

from rememberstack.spine.catalog_contract import CatalogInventory
from rememberstack.spine.catalog_contract import SchemaContractError
from rememberstack.spine.catalog_contract import verify_schema
from rememberstack.spine.catalog_contract import verify_schema_absent
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).parents[3]
_VERSIONS = _ROOT / "src/rememberstack/spine/migrations/versions"


def _database_url() -> str:
    """Resolve the isolated integration database or skip non-database local runs."""
    try:
        return load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for the real PostgreSQL lifecycle"
        )


def _alembic_config(*, database_url: str) -> Config:
    """Create a repository-root Alembic configuration with an explicit test URL."""
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _inventory(*, database_url: str) -> CatalogInventory:
    """Verify and return the current catalog using a short-lived connection."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return verify_schema(connection=connection)
    finally:
        engine.dispose()


def _verify_absent(*, database_url: str) -> None:
    """Verify downgrade cleanup using a short-lived connection."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            verify_schema_absent(connection=connection)
    finally:
        engine.dispose()


def _head_revision(*, database_url: str) -> str:
    """Read the applied Alembic head from the isolated database."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return str(
                connection.execute(
                    statement=text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_revision_graph_is_one_linear_structural_chain() -> None:
    """Keep the migration graph linear and free of bootstrap/seed DML."""
    config = Config(str(_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    revisions = tuple(script.walk_revisions(base="base", head="heads"))

    assert tuple(revision.revision for revision in reversed(revisions)) == (
        "p0_02_0001",
        "p0_02_0002",
        "p0_02_0003",
        "p0_02_0004",
        "p0_02_0005",
        "p0_02_0006",
        "p2_06_0007",
        "p3_01_0008",
        "p3_05_0009",
        "p3_07_0010",
        "p4_01_0011",
        "p6_02_0012",
        "p6_04_0013",
        "p6_05_0014",
        "p6_06_0015",
        "p7_02_0016",
        "p7_05_0017",
        "p1_03_0018",
    )
    assert len(script.get_heads()) == 1

    migration_source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(_VERSIONS.glob("p*_*.py"))
    ).lower()
    assert "insert into" not in migration_source
    assert "bootstrap_deployment" not in migration_source


def test_claim_citation_coordinate_migration_deduplicates_real_rows() -> None:
    """Two extraction generations collapse to one stable citation and downgrade cleanly."""
    database_url = _database_url()
    config = _alembic_config(database_url=database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="p6_04_0013")
    deployment_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()
    artifact_id = uuid4()
    claim_ids = (uuid4(), uuid4())
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO deployments (deployment_id, slug, name, raw_bucket,"
                    " artifacts_bucket, corpusfs_bucket)"
                    " VALUES (:deployment, 'citation-migration', 'Citation migration',"
                    " 'mem://raw', 'mem://artifacts', 'mem://corpusfs')"
                ),
                {"deployment": deployment_id},
            )
            connection.execute(
                text(
                    "INSERT INTO documents (doc_id, deployment_id, source_kind, source_ref)"
                    " VALUES (:doc, :deployment, 'test', 'citation-migration')"
                ),
                {"doc": doc_id, "deployment": deployment_id},
            )
            connection.execute(
                text(
                    "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                    " representation_id, ordinal, block_start, block_end,"
                    " chunk_content_hash, extraction_input_hash, char_start, char_end)"
                    " VALUES (:chunk, :deployment, :doc, :version, :representation,"
                    " 0, 0, 0, 'stable-coordinate', 'input-coordinate', 0, 20)"
                ),
                {
                    "chunk": chunk_id,
                    "deployment": deployment_id,
                    "doc": doc_id,
                    "version": uuid4(),
                    "representation": uuid4(),
                },
            )
            for ordinal, claim_id in enumerate(claim_ids):
                connection.execute(
                    text(
                        "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                        " claim_text, source_span, char_start, char_end, anchor_ok,"
                        " window_membership_ok, extractor_version)"
                        " VALUES (:claim, :deployment, :doc, :chunk, :body, :body,"
                        " 0, 20, true, true, :extractor)"
                    ),
                    {
                        "claim": claim_id,
                        "deployment": deployment_id,
                        "doc": doc_id,
                        "chunk": chunk_id,
                        "body": f"extraction generation {ordinal}",
                        "extractor": f"extractor-{ordinal}",
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO knowledge_artifacts (artifact_id, deployment_id, layer,"
                    " page_kind, git_path) VALUES"
                    " (:artifact, :deployment, 'K1', 'compiled', 'k/citation.md')"
                ),
                {"artifact": artifact_id, "deployment": deployment_id},
            )
            for claim_id in claim_ids:
                connection.execute(
                    text(
                        "INSERT INTO knowledge_artifact_evidence (evidence_link_id,"
                        " deployment_id, artifact_id, claim_id, role)"
                        " VALUES (:link, :deployment, :artifact, :claim, 'supports')"
                    ),
                    {
                        "link": uuid4(),
                        "deployment": deployment_id,
                        "artifact": artifact_id,
                        "claim": claim_id,
                    },
                )

        command.upgrade(config=config, revision="p6_05_0014")
        with engine.connect() as connection:
            stable_rows = connection.execute(
                text(
                    "SELECT claim_lineage_id, claim_chunk_content_hash"
                    " FROM knowledge_artifact_evidence WHERE artifact_id = :artifact"
                ),
                {"artifact": artifact_id},
            ).all()
        assert stable_rows == [(doc_id, "stable-coordinate")]

        command.downgrade(config=config, revision="p6_04_0013")
        with engine.connect() as connection:
            restored_claim_id = connection.execute(
                text(
                    "SELECT claim_id FROM knowledge_artifact_evidence"
                    " WHERE artifact_id = :artifact"
                ),
                {"artifact": artifact_id},
            ).scalar_one()
        assert restored_claim_id in claim_ids
    finally:
        engine.dispose()
        command.downgrade(config=config, revision="base")
        command.upgrade(config=config, revision="head")


def test_postgresql_fresh_downgrade_reupgrade_mutation_and_noop_lifecycle() -> None:
    """Exercise the complete PostgreSQL 16+ lifecycle and negative catalog proof."""
    database_url = _database_url()
    config = _alembic_config(database_url=database_url)

    command.downgrade(config=config, revision="base")
    _verify_absent(database_url=database_url)

    command.upgrade(config=config, revision="head")
    fresh_inventory = _inventory(database_url=database_url)
    assert fresh_inventory.server_version.startswith("PostgreSQL 1")
    assert fresh_inventory.hash_child_counts == {
        "observation_evidence": 64,
        "relation_evidence": 64,
    }
    assert len(fresh_inventory.tables) == 60
    assert fresh_inventory.empty_tables == (
        "deployments",
        "entity_types",
        "predicate_signatures",
        "predicates",
    )

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(statement=text("DROP TABLE relation_evidence_p63"))
        with engine.connect() as connection:
            with pytest.raises(SchemaContractError, match="relation_evidence_p63"):
                verify_schema(connection=connection)
    finally:
        engine.dispose()

    command.downgrade(config=config, revision="base")
    _verify_absent(database_url=database_url)
    command.upgrade(config=config, revision="head")
    restored_inventory = _inventory(database_url=database_url)
    assert restored_inventory == fresh_inventory

    head_before_noop = _head_revision(database_url=database_url)
    command.upgrade(config=config, revision="head")
    head_after_noop = _head_revision(database_url=database_url)
    assert head_before_noop == head_after_noop == "p1_03_0018"
    assert _inventory(database_url=database_url) == restored_inventory
