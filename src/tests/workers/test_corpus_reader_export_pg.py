"""D155 real-PostgreSQL selection and pure export; no store publication."""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.core.corpus_export import corpus_suffix
from rememberstack.core.corpus_export import corpus_type
from rememberstack.core.corpus_export import CorpusDocument
from rememberstack.core.corpus_export import render_navigation
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.corpus_export import CorpusReaderCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers.corpus_export import load_document_export
from tests.corpus_export_support import authority
from tests.corpus_export_support import digest
from tests.database_reset import reset_database


def _config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", load_database_settings().sqlalchemy_url())
    return config


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    try:
        config = _config()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for D155 selection proofs")
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    database = create_engine(load_database_settings().sqlalchemy_url())
    try:
        yield database
    finally:
        database.dispose()


def _seed(engine: Engine, source: CorpusDocument) -> None:
    """One served lineage and explicit immutable E0 inventory addresses."""
    params = {
        "d": source.deployment_id,
        "doc": source.doc_id,
        "v": source.version_id,
        "r": source.representation_id,
        "g": source.structure_generation_id,
        "raw": source.raw_uri,
        "mime": source.mime,
        "hash": source.content_hash,
        "md": source.markdown_uri,
        "mh": source.markdown_hash,
        "manifest": source.conversion_uri,
        "sh": source.manifest_hash,
        "pageindex": source.pageindex_uri,
        "title": source.title,
        "lineage_title": source.lineage_title,
        "filename": source.file_name,
        "family": source.family,
        "route": source.route,
        "date": source.source_date,
        "version_no": source.version_no,
        "placement": source.placement_path,
        "summary": source.root_summary,
        "source_kind": source.source_kind,
    }
    statements = (
        """INSERT INTO documents (doc_id, deployment_id, source_kind, source_ref, title)
        VALUES (:doc, :d, :source_kind, :doc, :lineage_title) ON CONFLICT DO NOTHING""",
        """INSERT INTO content_objects (deployment_id, content_hash, mime, byte_size, raw_uri)
        VALUES (:d, :hash, :mime, 100, :raw) ON CONFLICT DO NOTHING""",
        """INSERT INTO document_versions (version_id, deployment_id, doc_id, content_hash,
        version_no, status, source_modified_at) VALUES (:v, :d, :doc, :hash, :version_no, 'ready', :date)""",
        """INSERT INTO document_metadata (deployment_id, version_id, doc_id, family, title,
        file_name, metadata_mapping_version) VALUES (:d, :v, :doc, :family, :title, :filename, 'test')""",
        """INSERT INTO document_representations (representation_id, deployment_id, version_id,
        route, converter_name, converter_version, blockizer_version, markdown_uri, markdown_hash,
        conversion_uri, manifest_hash, blocks_uri, meta_uri, pageindex_uri, status)
        VALUES (:r, :d, :v, :route, 'test', 'test', 'test', :md, :mh, :manifest, :sh,
        'private/blocks.json', 'private/meta.json', 'legacy/stale-pageindex.json', 'ready')""",
        """INSERT INTO document_structure_generations (structure_generation_id, deployment_id,
        doc_id, version_id, representation_id, skeleton_version, skeleton_hash,
        skeleton_producer_family, roles_version, summary_version, placement_version,
        route_tag, candidate_skeleton_hash, stats_version, stats, pageindex_uri)
        VALUES (:g, :d, :doc, :v, :r, 'test', 'test', 'N/A', 'test', 'test', 'test',
        'parser', 'test', 'test', '{}'::jsonb, :pageindex)""",
        """INSERT INTO document_sections (section_id, deployment_id, doc_id, version_id,
        representation_id, structure_generation_id, node_path, block_start, block_end,
        role, char_start, char_end, ordinal, summary, placement_path, title)
        VALUES (:g, :d, :doc, :v, :r, :g, '0', 0, 0, 'body', 0, 10, 0, :summary, :placement, :title)""",
        "UPDATE document_representations SET current_structure_generation_id = :g WHERE representation_id = :r",
        "UPDATE document_versions SET current_representation_id = :r WHERE version_id = :v",
        "UPDATE documents SET current_version_id = :v WHERE doc_id = :doc",
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement), params)


@pytest.fixture()
def corpus(engine: Engine):
    source, artifacts, raw = authority()
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE deployments CASCADE"))
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=source.deployment_id,
            slug="d155-reader",
            name="D155 reader proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpus",
        )
    )
    _seed(engine, source)
    return source, artifacts, raw


def _select(engine: Engine, *, doc_id: UUID | None = None) -> list[CorpusDocument]:
    with CorpusReaderCatalog(engine=engine).documents(
        deployment_id=UUID(int=1), doc_id=doc_id
    ) as rows:
        return list(rows)


@pytest.mark.parametrize("new_status", ["ingesting", "converting", "failed", "parked"])
def test_served_v1_is_the_whole_reading_while_v2_is_stored(
    engine: Engine, corpus, new_status: str
) -> None:
    source, artifacts, _ = corpus
    before = load_document_export(_select(engine)[0], artifact_store=artifacts)
    pending_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO content_objects
            (deployment_id, content_hash, mime, byte_size, raw_uri)
            VALUES (:d, :h, 'video/mp4', 200, 'registered-upload/v2-never-read')"""),
            {"d": source.deployment_id, "h": digest(b"v2 original")},
        )
        connection.execute(
            text("""INSERT INTO document_versions
            (deployment_id, doc_id, version_id, content_hash, version_no, status)
            VALUES (:d, :doc, :v, :h, 2, CAST(:status AS document_status))"""),
            {
                "d": source.deployment_id,
                "doc": source.doc_id,
                "v": pending_id,
                "h": digest(b"v2 original"),
                "status": "ingesting" if new_status == "parked" else new_status,
            },
        )
        connection.execute(
            text("""INSERT INTO document_metadata
            (deployment_id, doc_id, version_id, family, title, file_name, metadata_mapping_version)
            VALUES (:d, :doc, :v, 'media', 'New pending title', 'pending.mp4', 'test')"""),
            {"d": source.deployment_id, "doc": source.doc_id, "v": pending_id},
        )
        if new_status == "parked":
            connection.execute(
                text("""INSERT INTO processing_state (processing_id, deployment_id,
                target_kind, target_id, stage, component_version, content_hash, status, defer_reason)
                VALUES (:p, :d, 'document_version', :v, 'convert', 'test', 'test-hash', 'pending', 'no_route')"""),
                {"p": uuid4(), "d": source.deployment_id, "v": pending_id},
            )
        connection.execute(
            text(
                "UPDATE documents SET title = 'Pending lineage title' WHERE doc_id = :doc"
            ),
            {"doc": source.doc_id},
        )
    selected = _select(engine)
    assert len(selected) == 1 and selected[0].version_id == source.version_id
    after = load_document_export(selected[0], artifact_store=artifacts)
    assert after.path == before.path
    assert after.revision == before.revision and after.files == before.files
    assert after.document.display_title == source.title
    assert {
        file.path: file
        for file in after.files
        if file.path not in {"document.json", "_index.md"}
    } == {
        file.path: file
        for file in before.files
        if file.path not in {"document.json", "_index.md"}
    }
    assert pending_id.hex.encode() not in b"".join(render_navigation(selected).values())
    assert not any("v2" in path for path in artifacts.reads)


def test_active_representation_and_structure_only(engine: Engine, corpus) -> None:
    source, artifacts, _ = corpus
    stale_id, other_rep = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO document_representations
            (representation_id, deployment_id, version_id, route, status, markdown_uri)
            VALUES (:r, :d, :v, 'other', 'ready', 'stale/markdown.md')"""),
            {"r": other_rep, "d": source.deployment_id, "v": source.version_id},
        )
        connection.execute(
            text("""INSERT INTO document_structure_generations
            (structure_generation_id, deployment_id, doc_id, version_id, representation_id,
             skeleton_version, skeleton_hash, skeleton_producer_family, route_tag,
             candidate_skeleton_hash, stats_version, stats, pageindex_uri)
            VALUES (:g, :d, :doc, :v, :r, 'stale', 'stale', 'N/A', 'parser', 'stale',
             'test', '{}'::jsonb, 'stale/pageindex.json')"""),
            {
                "g": stale_id,
                "d": source.deployment_id,
                "doc": source.doc_id,
                "v": source.version_id,
                "r": source.representation_id,
            },
        )
        connection.execute(
            text("""INSERT INTO document_sections (section_id, deployment_id,
            doc_id, version_id, representation_id, structure_generation_id, node_path,
            block_start, block_end, role, char_start, char_end, ordinal, summary, placement_path)
            VALUES (:s, :d, :doc, :v, :r, :g, '0', 0, 0, 'body', 0, 10, 0, 'STALE SUMMARY', 'stale-topic')"""),
            {
                "s": uuid4(),
                "d": source.deployment_id,
                "doc": source.doc_id,
                "v": source.version_id,
                "r": source.representation_id,
                "g": stale_id,
            },
        )
    selected = _select(engine)[0]
    assert selected.structure_generation_id == source.structure_generation_id
    assert selected.representation_id == source.representation_id
    export = load_document_export(selected, artifact_store=artifacts)
    assert selected.pageindex_uri == source.pageindex_uri
    assert "stale" not in export.path
    assert b"STALE SUMMARY" not in b"".join(render_navigation([selected]).values())


@pytest.mark.parametrize(
    "unavailable", ["raw_only", "deleted_lineage", "deleted_version", "purged_original"]
)
def test_no_folder_without_live_served_reading(
    engine: Engine, corpus, unavailable: str
) -> None:
    source, _, _ = corpus
    statements = {
        "raw_only": "UPDATE documents SET current_version_id = NULL WHERE doc_id = :id",
        "deleted_lineage": "UPDATE documents SET deleted_at = now() WHERE doc_id = :id",
        "deleted_version": "UPDATE document_versions SET deleted_at = now() WHERE doc_id = :id",
        "purged_original": "UPDATE content_objects SET purged_at = now()",
    }
    with engine.begin() as connection:
        connection.execute(text(statements[unavailable]), {"id": source.doc_id})
    assert _select(engine) == []
    assert _select(engine, doc_id=source.doc_id) == []


def test_intentional_null_topic_has_no_topic_folder_and_no_model_work(
    engine: Engine, corpus
) -> None:
    source, artifacts, _ = corpus
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_sections SET placement_path = NULL WHERE section_id = :g"
            ),
            {"g": source.structure_generation_id},
        )
    selected = _select(engine)[0]
    first = load_document_export(selected, artifact_store=artifacts)
    assert first.path.startswith("pdf/annual-report--")
    assert first == load_document_export(_select(engine)[0], artifact_store=artifacts)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM processing_state")
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(text("SELECT count(*) FROM cost_ledger")).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT corpus_published_revision FROM documents")
            ).scalar_one()
            is None
        )


def test_suffix_survives_actual_metadata_scrub_without_stored_suffix(
    engine: Engine, corpus
) -> None:
    source, _, _ = corpus
    suffix = corpus_suffix(_select(engine)[0].doc_id)
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM document_metadata WHERE doc_id = :doc"),
            {"doc": source.doc_id},
        )
        connection.execute(
            text("""UPDATE documents SET title = NULL, source_ref = NULL,
            source_uri = NULL, deleted_at = now(), current_version_id = NULL WHERE doc_id = :doc"""),
            {"doc": source.doc_id},
        )
    assert _select(engine) == []
    assert corpus_suffix(source.doc_id) == suffix
    with engine.connect() as connection:
        names = set(
            connection.execute(
                text("""SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'documents'""")
            ).scalars()
        )
    assert not any("suffix" in name for name in names)


def test_read_is_deployment_scoped_and_repeatable(engine: Engine, corpus) -> None:
    source, _, _ = corpus
    catalog = CorpusReaderCatalog(engine=engine)
    with catalog.documents(deployment_id=UUID(int=9999)) as rows:
        assert list(rows) == []
    with catalog.documents(deployment_id=source.deployment_id) as rows:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE document_metadata SET title = 'Later title' WHERE version_id = :v"
                ),
                {"v": source.version_id},
            )
        assert next(rows).display_title == source.display_title
    assert _select(engine)[0].display_title == "Later title"
    assert _select(engine, doc_id=UUID(int=9999)) == []


def test_attachment_type_uses_its_own_format(engine: Engine, corpus) -> None:
    source, _, _ = corpus
    attachment = replace(
        source,
        doc_id=uuid4(),
        version_id=uuid4(),
        representation_id=uuid4(),
        structure_generation_id=uuid4(),
        source_kind="gmail",
        title="Attached PDF",
    )
    _seed(engine, attachment)
    generation = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO document_reference_generations
            (generation_id, deployment_id, doc_id, version_id, origin, representation_id,
             crossref_version, input_hash, status)
            VALUES (:g, :d, :doc, :v, 'extracted', :r, 'test', 'test', 'active')"""),
            {
                "g": generation,
                "d": source.deployment_id,
                "doc": source.doc_id,
                "v": source.version_id,
                "r": source.representation_id,
            },
        )
        connection.execute(
            text("""INSERT INTO document_crossrefs (crossref_id, deployment_id,
            from_doc_id, from_version_id, generation_id, to_doc_id, kind, origin)
            VALUES (:id, :d, :doc, :v, :g, :attachment, 'attaches', 'extracted')"""),
            {
                "id": uuid4(),
                "d": source.deployment_id,
                "doc": source.doc_id,
                "v": source.version_id,
                "g": generation,
                "attachment": attachment.doc_id,
            },
        )
    selected = _select(engine, doc_id=attachment.doc_id)[0]
    assert corpus_type(selected) == "pdf"
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_reference_generations SET status = 'superseded' WHERE generation_id = :g"
            ),
            {"g": generation},
        )
    assert _select(engine, doc_id=attachment.doc_id)[0] == selected
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET deleted_at = now() WHERE doc_id = :doc"),
            {"doc": source.doc_id},
        )
    assert _select(engine, doc_id=attachment.doc_id)[0] == selected


def test_schema_change_is_only_nullable_marker_and_cleanup_paths(
    engine: Engine,
) -> None:
    def shape():
        with engine.connect() as connection:
            return set(
                connection.execute(
                    text("""SELECT table_schema, table_name, column_name,
                data_type, udt_name, is_nullable, column_default FROM information_schema.columns
                WHERE table_schema IN ('public', 'memory_v1')""")
                ).tuples()
            )

    config = _config()
    # D156 tags sit above the corpus revisions; compare those revisions alone.
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE tags CASCADE"))
    command.downgrade(config=config, revision="p9_51_0072")
    after = shape()
    command.downgrade(config=config, revision="p9_48_0069")
    try:
        before = shape()
        assert after - before == {
            (
                "public",
                "documents",
                "corpus_published_revision",
                "text",
                "text",
                "YES",
                None,
            ),
            ("public", "documents", "corpus_paths", "ARRAY", "_text", "YES", None),
        }
        assert before - after == set()
        command.upgrade(config=config, revision="p9_51_0072")
        assert shape() == after
    finally:
        command.upgrade(config=config, revision="head")
