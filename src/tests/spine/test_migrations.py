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
        "p1_04_0019",
        "p5_07_0020",
        "p8_01_0021",
        "p8_01_0022",
        "p9_01_0022",
        "p9_02_0023",
        "p9_03_0024",
    )
    assert len(script.get_heads()) == 1

    migration_source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(_VERSIONS.glob("p*_*.py"))
    ).lower()
    # D79's structural migration performs the one required legacy-generation
    # backfill; no deployment/bootstrap seed DML belongs in this chain.
    assert migration_source.count("insert into") == 1
    assert "bootstrap_deployment" not in migration_source


def test_batch_b_indexes_have_bound_columns_and_predicates() -> None:
    """Lock the two new partial indexes, not merely their catalog names."""
    database_url = _database_url()
    config = _alembic_config(database_url=database_url)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            definitions = {
                row["indexname"]: " ".join(row["indexdef"].split())
                for row in connection.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes"
                        " WHERE schemaname = 'public'"
                        " AND indexname IN"
                        " ('ix_claims_valid_window', 'ix_resdec_entity_live')"
                    )
                ).mappings()
            }
    finally:
        engine.dispose()

    assert definitions == {
        "ix_claims_valid_window": (
            "CREATE INDEX ix_claims_valid_window ON ONLY public.claims USING btree"
            " (deployment_id, claim_valid_from, claim_valid_until) WHERE"
            " (claim_valid_precision <> 'unknown'::claim_valid_precision)"
        ),
        "ix_resdec_entity_live": (
            "CREATE INDEX ix_resdec_entity_live ON ONLY public.resolution_decisions"
            " USING btree (deployment_id, entity_id, mention_id) WHERE"
            " (superseded_by IS NULL)"
        ),
    }


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


def test_d79_migration_backfills_existing_tree_as_legacy_generation() -> None:
    """Existing first-write section rows gain one immutable legacy wrapper/current pointer."""
    database_url = _database_url()
    config = _alembic_config(database_url=database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="p1_03_0018")
    deployment_id = uuid4()
    doc_id = uuid4()
    version_id = uuid4()
    representation_id = uuid4()
    section_id = uuid4()
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO deployments (deployment_id, slug, name, raw_bucket,"
                    " artifacts_bucket, corpusfs_bucket)"
                    " VALUES (:deployment, 'd79-backfill', 'D79 backfill',"
                    " 'mem://raw', 'mem://artifacts', 'mem://corpusfs')"
                ),
                {"deployment": deployment_id},
            )
            connection.execute(
                text(
                    "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                    " byte_size, raw_uri) VALUES"
                    " (:deployment, 'legacy-content', 'text/markdown', 1, 'raw')"
                ),
                {"deployment": deployment_id},
            )
            connection.execute(
                text(
                    "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                    " source_ref, title) VALUES"
                    " (:doc, :deployment, 'test', 'legacy', 'Legacy')"
                ),
                {"doc": doc_id, "deployment": deployment_id},
            )
            connection.execute(
                text(
                    "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                    " content_hash, version_no, status) VALUES"
                    " (:version, :deployment, :doc, 'legacy-content', 1, 'ready')"
                ),
                {"version": version_id, "deployment": deployment_id, "doc": doc_id},
            )
            connection.execute(
                text(
                    "INSERT INTO document_representations (representation_id,"
                    " deployment_id, version_id, route, structurer_name,"
                    " structurer_version, pageindex_uri, status) VALUES"
                    " (:representation, :deployment, :version, 'passthrough',"
                    " 'pageindex_llm', 'e0-structure-2026.07c:temp0-1',"
                    " 'legacy/pageindex.json', 'ready')"
                ),
                {
                    "representation": representation_id,
                    "deployment": deployment_id,
                    "version": version_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO document_sections (section_id, deployment_id, doc_id,"
                    " version_id, representation_id, node_path, block_start, block_end,"
                    " title, role, char_start, char_end, ordinal, structurer_version)"
                    " VALUES (:section, :deployment, :doc, :version, :representation,"
                    " '0', 0, 0, 'Legacy', 'body', 0, 1, 0,"
                    " 'e0-structure-2026.07c:temp0-1')"
                ),
                {
                    "section": section_id,
                    "deployment": deployment_id,
                    "doc": doc_id,
                    "version": version_id,
                    "representation": representation_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO document_sections (section_id, deployment_id, doc_id,"
                    " version_id, representation_id, node_path, block_start, block_end,"
                    " title, role, char_start, char_end, ordinal, structurer_version)"
                    " VALUES (:section, :deployment, :doc, :version, :representation,"
                    " '0.1', 0, 0, 'Legacy Child', 'body', 0, 1, 1,"
                    " 'e0-structure-2026.07c:temp0-1')"
                ),
                {
                    "section": uuid4(),
                    "deployment": deployment_id,
                    "doc": doc_id,
                    "version": version_id,
                    "representation": representation_id,
                },
            )

        command.upgrade(config=config, revision="head")
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT g.structure_generation_id, g.route_tag::text AS route,"
                        " g.skeleton_version, g.skeleton_producer_family,"
                        " g.roles_version, g.summary_version,"
                        " g.placement_version, g.pageindex_uri,"
                        " r.current_structure_generation_id"
                        " FROM document_structure_generations g"
                        " JOIN document_representations r"
                        " ON r.representation_id = g.representation_id"
                        " WHERE g.representation_id = :representation"
                    ),
                    {"representation": representation_id},
                )
                .mappings()
                .one()
            )
        assert row["route"] == "legacy"
        assert row["skeleton_version"] == "e0-structure-2026.07c:temp0-1"
        assert row["skeleton_producer_family"] == "legacy-unknown"
        assert row["roles_version"] == "e0-structure-2026.07c:temp0-1"
        assert row["summary_version"] is None
        assert row["placement_version"] is None
        assert row["pageindex_uri"] == "legacy/pageindex.json"
        assert row["current_structure_generation_id"] == row["structure_generation_id"]
        with engine.connect() as connection:
            sections = (
                connection.execute(
                    text(
                        "SELECT node_path, structure_generation_id, normalized_title"
                        " FROM document_sections"
                        " WHERE representation_id = :representation ORDER BY node_path"
                    ),
                    {"representation": representation_id},
                )
                .mappings()
                .all()
            )
        # a MULTI-section legacy tree wraps under ONE generation (review gap)
        assert [section["node_path"] for section in sections] == ["0", "0.1"]
        assert {section["structure_generation_id"] for section in sections} == {
            row["structure_generation_id"]
        }
        assert {section["normalized_title"] for section in sections} == {""}
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
    assert len(fresh_inventory.tables) == 62
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
    assert head_before_noop == head_after_noop == "p9_03_0024"
    assert _inventory(database_url=database_url) == restored_inventory
