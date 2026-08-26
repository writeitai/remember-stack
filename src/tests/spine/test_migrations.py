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
        "p9_04_0025",
        "p9_05_0026",
        "p9_06_0027",
        "p9_07_0028",
        "p9_08_0029",
        "p9_09_0030",
        "p9_10_0031",
        "p9_11_0032",
        "p9_13_0034",
        "p9_14_0035",
        "p9_15_0036",
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
            "CREATE INDEX ix_claims_valid_window ON public.claims USING btree"
            " (deployment_id, claim_valid_from, claim_valid_until) WHERE"
            " (claim_valid_precision <> 'unknown'::claim_valid_precision)"
        ),
        "ix_resdec_entity_live": (
            "CREATE INDEX ix_resdec_entity_live ON ONLY public.resolution_decisions"
            " USING btree (deployment_id, entity_id, mention_id) WHERE"
            " (superseded_by IS NULL)"
        ),
    }


def test_postgres_p1_schema_searches_and_audits_chunk_authority() -> None:
    """Prove D94 vector/BM25 search, fixed dimensions, and the orphan auditor."""
    database_url = _database_url()
    config = _alembic_config(database_url=database_url)
    command.upgrade(config=config, revision="head")
    deployment_id = uuid4()
    chunk_id = uuid4()
    claim_id = uuid4()
    query_vector = "[1," + "0," * 1534 + "0]"
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    text(
                        "INSERT INTO deployments (deployment_id, slug, name, raw_bucket,"
                        " artifacts_bucket, corpusfs_bucket) VALUES"
                        " (:deployment, 'd94-schema', 'D94 schema', 'mem://raw',"
                        " 'mem://artifacts', 'mem://corpusfs')"
                    ),
                    {"deployment": deployment_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                        " representation_id, ordinal, block_start, block_end,"
                        " chunk_content_hash, extraction_input_hash, char_start, char_end)"
                        " VALUES (:chunk, :deployment, :doc, :version, :representation,"
                        " 0, 0, 0, 'd94-content', 'd94-input', 0, 26)"
                    ),
                    {
                        "chunk": chunk_id,
                        "deployment": deployment_id,
                        "doc": uuid4(),
                        "version": uuid4(),
                        "representation": uuid4(),
                    },
                )
                attestation = {
                    "model": "qwen/qwen3-embedding-8b",
                    "policy": "p1-search-input-1",
                    "hash": "a" * 64,
                    "vector": query_vector,
                }
                connection.execute(
                    text(
                        "INSERT INTO chunk_search (deployment_id, chunk_id, search_text,"
                        " embedding, embedding_model, embedding_input_policy_version,"
                        " embedding_text_hash) VALUES (:deployment, :chunk,"
                        " 'a concise retrieval contract', CAST(:vector AS vector),"
                        " :model, :policy, :hash)"
                    ),
                    {"deployment": deployment_id, "chunk": chunk_id, **attestation},
                )
                connection.execute(
                    text(
                        "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                        " claim_text, source_span, char_start, char_end, anchor_ok,"
                        " window_membership_ok, extractor_version, embedding,"
                        " embedding_model, embedding_input_policy_version,"
                        " embedding_text_hash) VALUES (:claim, :deployment, :doc, :chunk,"
                        " 'PostgreSQL ranks this claim', 'PostgreSQL ranks this claim',"
                        " 0, 27, true, true, 'extractor-d94', CAST(:vector AS vector),"
                        " :model, :policy, :hash)"
                    ),
                    {
                        "claim": claim_id,
                        "deployment": deployment_id,
                        "doc": uuid4(),
                        "chunk": chunk_id,
                        **attestation,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO p1_search_channels (deployment_id, target, channel,"
                        " embedding_model, embedding_dimension,"
                        " embedding_input_policy_version, text_config, ready) VALUES"
                        " (:deployment, 'chunks', 'semantic', :model, 1536, :policy,"
                        " NULL, true),"
                        " (:deployment, 'chunks', 'bm25', NULL, NULL, NULL, 'simple', true)"
                    ),
                    {
                        "deployment": deployment_id,
                        "model": attestation["model"],
                        "policy": attestation["policy"],
                    },
                )

                vector_types = {
                    str(row[0]): str(row[1])
                    for row in connection.execute(
                        text(
                            "SELECT c.relname, format_type(a.atttypid, a.atttypmod)"
                            " FROM pg_attribute AS a"
                            " JOIN pg_class AS c ON c.oid = a.attrelid"
                            " JOIN pg_namespace AS n ON n.oid = c.relnamespace"
                            " WHERE n.nspname = 'public' AND a.attname = 'embedding'"
                            " AND c.relname = ANY(:tables)"
                        ),
                        {
                            "tables": [
                                "chunk_search",
                                "claims",
                                "relations",
                                "observations",
                                "entities",
                            ]
                        },
                    ).all()
                }
                assert vector_types == {
                    "chunk_search": "vector(1536)",
                    "claims": "vector(1536)",
                    "relations": "vector(1536)",
                    "observations": "vector(1536)",
                    "entities": "vector(1536)",
                }
                assert (
                    connection.execute(
                        text(
                            "SELECT relkind FROM pg_class WHERE oid = 'claims'::regclass"
                        )
                    ).scalar_one()
                    == "r"
                )
                connection.execute(
                    text("SET LOCAL hnsw.iterative_scan = 'strict_order'")
                )
                semantic_id = connection.execute(
                    text(
                        "SELECT chunk_id FROM chunk_search"
                        " WHERE deployment_id = :deployment"
                        " ORDER BY embedding <=> CAST(:vector AS vector) LIMIT 1"
                    ),
                    {"deployment": deployment_id, "vector": query_vector},
                ).scalar_one()
                lexical_id = connection.execute(
                    text(
                        "SELECT chunk_id FROM chunk_search"
                        " WHERE deployment_id = :deployment"
                        " ORDER BY search_text <@>"
                        " to_bm25query('retrieval', 'ix_chunk_search_bm25') LIMIT 1"
                    ),
                    {"deployment": deployment_id},
                ).scalar_one()
                claim_lexical_id = connection.execute(
                    text(
                        "SELECT claim_id FROM claims WHERE deployment_id = :deployment"
                        " AND is_current_testimony"
                        " ORDER BY claim_text <@>"
                        " to_bm25query('PostgreSQL', 'ix_claims_current_bm25') LIMIT 1"
                    ),
                    {"deployment": deployment_id},
                ).scalar_one()
                assert semantic_id == lexical_id == chunk_id
                assert claim_lexical_id == claim_id

                connection.execute(
                    text("DELETE FROM chunks WHERE chunk_id = :chunk"),
                    {"chunk": chunk_id},
                )
                with pytest.raises(
                    SchemaContractError,
                    match="chunk_search contains 1 rows without chunk authority",
                ):
                    verify_schema(connection=connection)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


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
    assert len(fresh_inventory.tables) == 72
    assert fresh_inventory.empty_tables == ("deployments", "entity_types", "predicates")

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
    assert head_before_noop == head_after_noop == "p9_15_0036"
    assert _inventory(database_url=database_url) == restored_inventory


def test_global_resolution_eval_migration_preserves_the_default_band() -> None:
    """I.3 keeps the global band, drops type strata, and downgrades explicitly."""
    database_url = _database_url()
    config = _alembic_config(database_url=database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="p9_14_0035")
    deployment_id = uuid4()
    pair_id = uuid4()
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO deployments (deployment_id, slug, name, raw_bucket,"
                    " artifacts_bucket, corpusfs_bucket) VALUES"
                    " (:deployment, 'i3-migration', 'I.3 migration', 'mem://raw',"
                    " 'mem://artifacts', 'mem://corpusfs')"
                ),
                {"deployment": deployment_id},
            )
            connection.execute(
                text(
                    "INSERT INTO resolver_versions (deployment_id, resolver_version,"
                    " tier_config, thresholds_by_type) VALUES"
                    " (:deployment, 'resolver-i3', '{}'::jsonb,"
                    ' \'{"default": {"t3_accept": 0.91, "t3_reject": 0.63},'
                    ' "Person": {"t3_accept": 0.99, "t3_reject": 0.80}}\'::jsonb)'
                ),
                {"deployment": deployment_id},
            )
            connection.execute(
                text(
                    "INSERT INTO golden_pairs (pair_id, deployment_id, entity_type,"
                    " surface_a, surface_b, label, hardness, adjudicated_by) VALUES"
                    " (:pair, :deployment, 'Person', 'John Smith', 'John Smith',"
                    " 'no_match', 'hard_negative', 'human-test')"
                ),
                {"pair": pair_id, "deployment": deployment_id},
            )

        command.upgrade(config=config, revision="p9_15_0036")
        with engine.connect() as connection:
            resolver_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_schema = 'public'"
                        " AND table_name = 'resolver_versions'"
                    )
                ).scalars()
            )
            golden_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_schema = 'public' AND table_name = 'golden_pairs'"
                    )
                ).scalars()
            )
            thresholds = connection.execute(
                text(
                    "SELECT thresholds FROM resolver_versions"
                    " WHERE deployment_id = :deployment"
                ),
                {"deployment": deployment_id},
            ).scalar_one()
            pair_count = connection.execute(
                text("SELECT count(*) FROM golden_pairs WHERE pair_id = :pair"),
                {"pair": pair_id},
            ).scalar_one()
            golden_index = connection.execute(
                text("SELECT to_regclass('public.ix_golden_type')")
            ).scalar_one()
        assert "thresholds" in resolver_columns
        assert "thresholds_by_type" not in resolver_columns
        assert "entity_type" not in golden_columns
        assert thresholds == {"t3_accept": 0.91, "t3_reject": 0.63}
        assert pair_count == 1
        assert golden_index is None

        command.downgrade(config=config, revision="p9_14_0035")
        with engine.connect() as connection:
            restored_thresholds = connection.execute(
                text(
                    "SELECT thresholds_by_type FROM resolver_versions"
                    " WHERE deployment_id = :deployment"
                ),
                {"deployment": deployment_id},
            ).scalar_one()
            restored_type = connection.execute(
                text("SELECT entity_type FROM golden_pairs WHERE pair_id = :pair"),
                {"pair": pair_id},
            ).scalar_one()
            restored_index = connection.execute(
                text("SELECT to_regclass('public.ix_golden_type')")
            ).scalar_one()
        assert restored_thresholds == {
            "default": {"t3_accept": 0.91, "t3_reject": 0.63}
        }
        assert restored_type == "Unknown"
        assert restored_index == "ix_golden_type"
    finally:
        engine.dispose()
        command.downgrade(config=config, revision="base")
        command.upgrade(config=config, revision="head")


def test_coordinate_binding_downgrade_restores_prior_view_metadata() -> None:
    """The correction migration must leave exact p9.03 metadata on downgrade."""
    database_url = _database_url()
    config = _alembic_config(database_url=database_url)

    def metadata() -> dict[str, tuple[str, str | None, str]]:
        """Read corrected view metadata that the downgrade must restore."""
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT n.nspname, c.relname,"
                        " pg_get_userbyid(c.relowner) AS owner,"
                        " obj_description(c.oid, 'pg_class') AS comment,"
                        " pg_get_viewdef(c.oid, true) AS definition"
                        " FROM pg_class AS c"
                        " JOIN pg_namespace AS n ON n.oid = c.relnamespace"
                        " WHERE (n.nspname = 'public'"
                        " AND c.relname IN"
                        " ('v_graph_survivor', 'v_memory_entity_survivor'))"
                        " OR (n.nspname = 'memory_v1'"
                        " AND c.relname = 'identity_events_visible')"
                    )
                ).mappings()
                return {
                    f"{row['nspname']}.{row['relname']}": (
                        str(row["owner"]),
                        None if row["comment"] is None else str(row["comment"]),
                        str(row["definition"]),
                    )
                    for row in rows
                }
        finally:
            engine.dispose()

    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="p9_03_0024")
    expected = metadata()
    assert set(expected) == {
        "memory_v1.identity_events_visible",
        "public.v_graph_survivor",
        "public.v_memory_entity_survivor",
    }

    try:
        command.upgrade(config=config, revision="head")
        command.downgrade(config=config, revision="p9_03_0024")
        assert metadata() == expected
    finally:
        command.upgrade(config=config, revision="head")
