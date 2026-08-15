"""Real-PostgreSQL proofs for the D74 hard-forget catalog."""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from pathlib import Path
from typing import cast
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import ForgetManifest
from rememberstack.model import ForgetManifestStatus
from rememberstack.ports import ForgetManifestPort
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import ForgetCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import HardForgetHandler
from rememberstack.workers import HardForgetReadiness
from rememberstack.workers import HardForgetService

_ROOT = Path(__file__).resolve().parents[3]
_NOW = datetime(2026, 7, 21, tzinfo=UTC)
_TOKEN = "S55-UNIQUE-FORGET-TOKEN"
_TARGET_CONTENT_HASH = "a" * 64
_CONTROL_CONTENT_HASH = "b" * 64

_DEPLOYMENT_ID = UUID("75000000-0000-0000-0000-000000000001")
_TARGET_DOC_ID = UUID("75000000-0000-0000-0000-000000000002")
_CONTROL_DOC_ID = UUID("75000000-0000-0000-0000-000000000003")
_TARGET_VERSION_ID = UUID("75000000-0000-0000-0000-000000000004")
_CONTROL_VERSION_ID = UUID("75000000-0000-0000-0000-000000000005")
_TARGET_REPRESENTATION_ID = UUID("75000000-0000-0000-0000-000000000006")
_CONTROL_REPRESENTATION_ID = UUID("75000000-0000-0000-0000-000000000007")
_TARGET_CHUNK_ID = UUID("75000000-0000-0000-0000-000000000008")
_CONTROL_CHUNK_ID = UUID("75000000-0000-0000-0000-000000000009")
_TARGET_CLAIM_ID = UUID("75000000-0000-0000-0000-000000000010")
_CONTROL_CLAIM_ID = UUID("75000000-0000-0000-0000-000000000011")
_EXCLUSIVE_ENTITY_ID = UUID("75000000-0000-0000-0000-000000000012")
_SHARED_ENTITY_ID = UUID("75000000-0000-0000-0000-000000000013")
_CONTROL_ENTITY_ID = UUID("75000000-0000-0000-0000-000000000014")
_EXCLUSIVE_RELATION_ID = UUID("75000000-0000-0000-0000-000000000015")
_SHARED_RELATION_ID = UUID("75000000-0000-0000-0000-000000000016")
_EXCLUSIVE_OBSERVATION_ID = UUID("75000000-0000-0000-0000-000000000017")
_SHARED_OBSERVATION_ID = UUID("75000000-0000-0000-0000-000000000018")
_SCOPE_ID = UUID("75000000-0000-0000-0000-000000000019")
_ARTIFACT_ID = UUID("75000000-0000-0000-0000-000000000020")
_SUBSCRIPTION_ID = UUID("75000000-0000-0000-0000-000000000021")
_PLAN_RUN_ID = UUID("75000000-0000-0000-0000-000000000022")
_FORGET_ID = UUID("75000000-0000-0000-0000-000000000023")
_EXCLUSIVE_MENTION_ID = UUID("75000000-0000-0000-0000-000000000026")
_TARGET_SHARED_MENTION_ID = UUID("75000000-0000-0000-0000-000000000027")
_CONTROL_SHARED_MENTION_ID = UUID("75000000-0000-0000-0000-000000000028")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real hard-forget proofs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def seeded_engine(database_engine: Engine) -> Engine:
    """Give the proof a fresh deployment with target and independent control data."""
    _restore_pre_forget_postgres(engine=database_engine)
    return database_engine


class _PortableManifestStore:
    """Retain one D74 manifest outside the restored PostgreSQL state."""

    def __init__(self, *, manifest: ForgetManifest) -> None:
        """Bind the immutable portable intent used by the restore drill."""
        self._manifest = manifest

    def append(self, *, manifest: ForgetManifest) -> None:
        """Reject an unexpected attempt to replace the retained intent."""
        assert manifest == self._manifest

    def manifests(self, *, deployment_id: UUID) -> tuple[ForgetManifest, ...]:
        """Enumerate the retained intent only for its original deployment."""
        if deployment_id == self._manifest.deployment_id:
            return (self._manifest,)
        return ()


class _NoPreparingRequest:
    """Refuse request admission in a restore with no local preparation row."""

    def request(self, **_: object) -> ForgetManifest:
        """Fail if readiness invents a new request instead of materializing intent."""
        raise AssertionError("portable restore has no preparing request")


class _PostgresRestoreHandler:
    """Run only the real PostgreSQL leg of the independently composed restore drill."""

    def __init__(self, *, catalog: ForgetCatalog) -> None:
        """Bind the real catalog used by readiness rematerialization."""
        self._catalog = catalog

    def honor(self, *, manifest: ForgetManifest) -> None:
        """Scrub, verify, and complete the rematerialized PostgreSQL intent."""
        self._catalog.scrub_postgres(manifest=manifest)
        self._catalog.verify_postgres_scrubbed(manifest=manifest)
        self._catalog.mark_complete(manifest=manifest)


def _postgres_restore_readiness(
    *, engine: Engine, manifest: ForgetManifest
) -> HardForgetReadiness:
    """Compose real PostgreSQL readiness with already-covered external store ports."""
    catalog = ForgetCatalog(engine=engine)
    return HardForgetReadiness(
        catalog=catalog,
        manifest_store=cast(
            ForgetManifestPort, _PortableManifestStore(manifest=manifest)
        ),
        request_service=cast(HardForgetService, _NoPreparingRequest()),
        handler=cast(HardForgetHandler, _PostgresRestoreHandler(catalog=catalog)),
    )


def _restore_pre_forget_postgres(*, engine: Engine) -> None:
    """Replace local state with the fixed pre-forget PostgreSQL snapshot."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE mentions, resolution_decisions, chunks,"
                " chunk_claims, claims, claim_extraction_decisions,"
                " testimony_currency_events, relation_evidence,"
                " observation_evidence, deployments CASCADE"
            )
        )
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="hard-forget",
            name="Hard forget proof",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
            knowledge_repo_uri="mem://knowledge.git",
        )
    )
    with engine.begin() as connection:
        _seed_documents(connection=connection)
        _seed_structure_provenance(connection=connection)
        _seed_evidence(connection=connection)
        _seed_postgres_p1_residuals(connection=connection)
        _seed_knowledge_and_residuals(connection=connection)


def test_inventory_scrub_and_verification_preserve_independent_evidence(
    seeded_engine: Engine,
) -> None:
    """Exercise the real inventory and scrub SQL against lineage-shaped residuals."""
    catalog = ForgetCatalog(engine=seeded_engine)
    catalog.prepare(
        deployment_id=_DEPLOYMENT_ID, doc_id=_TARGET_DOC_ID, forget_id=_FORGET_ID
    )
    manifest = catalog.inventory_and_store_manifest(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=_TARGET_DOC_ID,
        forget_id=_FORGET_ID,
        requested_at=_NOW,
    )

    assert manifest.chunk_ids == (_TARGET_CHUNK_ID,)
    assert manifest.claim_ids == (_TARGET_CLAIM_ID,)
    assert manifest.mention_ids == (_EXCLUSIVE_MENTION_ID, _TARGET_SHARED_MENTION_ID)
    assert manifest.resolved_entity_ids == (_EXCLUSIVE_ENTITY_ID, _SHARED_ENTITY_ID)
    assert set(manifest.fact_ids) == {_EXCLUSIVE_RELATION_ID, _EXCLUSIVE_OBSERVATION_ID}
    assert manifest.entity_ids == (_EXCLUSIVE_ENTITY_ID,)
    assert manifest.k_artifact_ids == (_ARTIFACT_ID,)
    assert {key.root for key in manifest.object_keys} == {
        "mem://artifacts/target-blocks.json",
        "mem://artifacts/target-conversion.json",
        "mem://artifacts/target-markdown.md",
        "mem://artifacts/target-meta.json",
        "mem://artifacts/target-pageindex.json",
        "mem://artifacts/target-structure-old-pageindex.json",
        "mem://artifacts/target-structure-new-pageindex.json",
        "mem://artifacts/writer-transcript.json",
        f"mem://raw/target.bin?marker={_TOKEN}",
        "mem://artifacts/planner-transcript.json",
    }

    catalog.accept_and_enqueue(manifest=manifest)
    catalog.scrub_postgres(manifest=manifest)
    catalog.verify_postgres_scrubbed(manifest=manifest)
    catalog.mark_complete(manifest=manifest)

    record = catalog.record_for_doc(deployment_id=_DEPLOYMENT_ID, doc_id=_TARGET_DOC_ID)
    assert record is not None
    assert record.status is ForgetManifestStatus.COMPLETE
    _assert_scrubbed_and_control_survives(engine=seeded_engine)


def test_readiness_rehonors_manifest_after_old_postgres_restore(
    seeded_engine: Engine,
) -> None:
    """Restore pre-forget PostgreSQL; portable readiness must scrub it again."""
    catalog = ForgetCatalog(engine=seeded_engine)
    catalog.prepare(
        deployment_id=_DEPLOYMENT_ID, doc_id=_TARGET_DOC_ID, forget_id=_FORGET_ID
    )
    manifest = catalog.inventory_and_store_manifest(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=_TARGET_DOC_ID,
        forget_id=_FORGET_ID,
        requested_at=_NOW,
    )
    catalog.accept_and_enqueue(manifest=manifest)
    catalog.scrub_postgres(manifest=manifest)
    catalog.verify_postgres_scrubbed(manifest=manifest)
    catalog.mark_complete(manifest=manifest)

    _restore_pre_forget_postgres(engine=seeded_engine)
    assert (
        catalog.record_for_doc(deployment_id=_DEPLOYMENT_ID, doc_id=_TARGET_DOC_ID)
        is None
    )

    honored = _postgres_restore_readiness(
        engine=seeded_engine, manifest=manifest
    ).ensure_ready(deployment_id=_DEPLOYMENT_ID)

    assert honored == (_FORGET_ID,)
    record = catalog.record_for_doc(deployment_id=_DEPLOYMENT_ID, doc_id=_TARGET_DOC_ID)
    assert record is not None
    assert record.status is ForgetManifestStatus.COMPLETE
    _assert_scrubbed_and_control_survives(engine=seeded_engine)


_TARGET_CHECK_ID = UUID("75000000-0000-0000-0000-0000000000c1")
_TARGET_GENERATION_ID = UUID("75000000-0000-0000-0000-0000000000c2")
_TARGET_OLD_GENERATION_ID = UUID("75000000-0000-0000-0000-0000000000c3")


def _seed_structure_provenance(*, connection: Connection) -> None:
    """A check plus two immutable generations/sidecars for hard-forget."""
    connection.execute(
        text(
            "INSERT INTO document_skeleton_checks ("
            " check_id, processing_id, deployment_id, doc_id, version_id,"
            " representation_id, candidate_skeleton_hash, stats_version, stats,"
            " sampled_input_hash, check_outcome, checker_component_version,"
            " checker_model, checker_model_hash, checker_prompt_hash,"
            " checker_schema_hash"
            ") VALUES ("
            " :check_id, :check_id, :d, :doc, :version, :representation,"
            " 'hash', 'stats-v', CAST('{}' AS jsonb), NULL, 'coherent',"
            " 'check-v', 'model', 'mh', 'ph', 'sh')"
        ),
        {
            "check_id": _TARGET_CHECK_ID,
            "d": _DEPLOYMENT_ID,
            "doc": _TARGET_DOC_ID,
            "version": _TARGET_VERSION_ID,
            "representation": _TARGET_REPRESENTATION_ID,
        },
    )
    for generation_id, pageindex_uri in (
        (
            _TARGET_OLD_GENERATION_ID,
            "mem://artifacts/target-structure-old-pageindex.json",
        ),
        (_TARGET_GENERATION_ID, "mem://artifacts/target-structure-new-pageindex.json"),
    ):
        connection.execute(
            text(
                "INSERT INTO document_structure_generations ("
                " structure_generation_id, deployment_id, doc_id, version_id,"
                " representation_id, skeleton_version, skeleton_hash,"
                " skeleton_producer_family, selecting_check_id, route_tag,"
                " candidate_skeleton_hash, stats_version, stats, pageindex_uri"
                ") VALUES ("
                " :generation_id, :d, :doc, :version, :representation,"
                " 'skeleton-v', 'hash', 'N/A', :check_id, 'parser', 'hash',"
                " 'stats-v', CAST('{}' AS jsonb), :pageindex_uri)"
            ),
            {
                "generation_id": generation_id,
                "d": _DEPLOYMENT_ID,
                "doc": _TARGET_DOC_ID,
                "version": _TARGET_VERSION_ID,
                "representation": _TARGET_REPRESENTATION_ID,
                "check_id": _TARGET_CHECK_ID,
                "pageindex_uri": pageindex_uri,
            },
        )


def _seed_documents(*, connection: Connection) -> None:
    """Seed two complete E0/E1 lineages and an inbound source-bearing crossref."""
    for content_hash, raw_uri in (
        (_TARGET_CONTENT_HASH, f"mem://raw/target.bin?marker={_TOKEN}"),
        (_CONTROL_CONTENT_HASH, "mem://raw/control.bin"),
    ):
        connection.execute(
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                " byte_size, raw_uri) VALUES (:d, :hash, 'text/plain', 128, :uri)"
            ),
            {"d": _DEPLOYMENT_ID, "hash": content_hash, "uri": raw_uri},
        )
    for doc_id, source_ref, title in (
        (_TARGET_DOC_ID, f"target-ref-{_TOKEN}", f"Target {_TOKEN}"),
        (_CONTROL_DOC_ID, "control-ref", "Control"),
    ):
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, source_uri, title) VALUES"
                " (:doc, :d, 'upload', :ref, :ref, :title)"
            ),
            {"doc": doc_id, "d": _DEPLOYMENT_ID, "ref": source_ref, "title": title},
        )
    for version_id, doc_id, content_hash, source_ref in (
        (
            _TARGET_VERSION_ID,
            _TARGET_DOC_ID,
            _TARGET_CONTENT_HASH,
            f"target-version-{_TOKEN}",
        ),
        (
            _CONTROL_VERSION_ID,
            _CONTROL_DOC_ID,
            _CONTROL_CONTENT_HASH,
            "control-version",
        ),
    ):
        connection.execute(
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, source_version_ref, status, error) VALUES"
                " (:version, :d, :doc, :hash, 1, :ref, 'ready', :error)"
            ),
            {
                "version": version_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "hash": content_hash,
                "ref": source_ref,
                "error": _TOKEN if doc_id == _TARGET_DOC_ID else None,
            },
        )
    for representation_id, version_id, prefix in (
        (_TARGET_REPRESENTATION_ID, _TARGET_VERSION_ID, "target"),
        (_CONTROL_REPRESENTATION_ID, _CONTROL_VERSION_ID, "control"),
    ):
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, markdown_uri, pageindex_uri,"
                " conversion_uri, blocks_uri, meta_uri, status, error) VALUES"
                " (:representation, :d, :version, 'digital', :markdown, :pageindex,"
                " :conversion, :blocks, :meta, 'ready', :error)"
            ),
            {
                "representation": representation_id,
                "d": _DEPLOYMENT_ID,
                "version": version_id,
                "markdown": f"mem://artifacts/{prefix}-markdown.md",
                "pageindex": f"mem://artifacts/{prefix}-pageindex.json",
                "conversion": f"mem://artifacts/{prefix}-conversion.json",
                "blocks": f"mem://artifacts/{prefix}-blocks.json",
                "meta": f"mem://artifacts/{prefix}-meta.json",
                "error": _TOKEN if prefix == "target" else None,
            },
        )
        connection.execute(
            text(
                "UPDATE document_versions SET current_representation_id = :rep"
                " WHERE deployment_id = :d AND version_id = :version"
            ),
            {"rep": representation_id, "d": _DEPLOYMENT_ID, "version": version_id},
        )
    connection.execute(
        text(
            "UPDATE documents SET current_version_id = CASE"
            " WHEN doc_id = :target_doc THEN :target_version ELSE :control_version END"
            " WHERE deployment_id = :d"
        ),
        {
            "d": _DEPLOYMENT_ID,
            "target_doc": _TARGET_DOC_ID,
            "target_version": _TARGET_VERSION_ID,
            "control_version": _CONTROL_VERSION_ID,
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_crossrefs (crossref_id, deployment_id,"
            " from_doc_id, to_doc_id, kind, raw_citation, context, resolved) VALUES"
            " (:id, :d, :control, :target, 'cites', :token, :token, true)"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000024"),
            "d": _DEPLOYMENT_ID,
            "control": _CONTROL_DOC_ID,
            "target": _TARGET_DOC_ID,
            "token": _TOKEN,
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_crossrefs (crossref_id, deployment_id,"
            " from_doc_id, to_doc_id, kind, raw_citation, context, resolved) VALUES"
            " (:id, :d, :target, :control, 'links_to', :token, :token, true)"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000043"),
            "d": _DEPLOYMENT_ID,
            "target": _TARGET_DOC_ID,
            "control": _CONTROL_DOC_ID,
            "token": _TOKEN,
        },
    )


def _seed_evidence(*, connection: Connection) -> None:
    """Seed exclusive and independently supported entities and facts."""
    for chunk_id, doc_id, version_id, representation_id, marker in (
        (
            _TARGET_CHUNK_ID,
            _TARGET_DOC_ID,
            _TARGET_VERSION_ID,
            _TARGET_REPRESENTATION_ID,
            _TOKEN,
        ),
        (
            _CONTROL_CHUNK_ID,
            _CONTROL_DOC_ID,
            _CONTROL_VERSION_ID,
            _CONTROL_REPRESENTATION_ID,
            "control",
        ),
    ):
        connection.execute(
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end,"
                " context_prefix, created_at) VALUES"
                " (:chunk, :d, :doc, :version, :representation, 0, 0, 0, :hash,"
                " :input, 0, 20, :marker, :at)"
            ),
            {
                "chunk": chunk_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "version": version_id,
                "representation": representation_id,
                "hash": f"chunk-{marker}",
                "input": f"input-{marker}",
                "marker": marker,
                "at": _NOW,
            },
        )
    for claim_id, doc_id, chunk_id, body in (
        (_TARGET_CLAIM_ID, _TARGET_DOC_ID, _TARGET_CHUNK_ID, _TOKEN),
        (_CONTROL_CLAIM_ID, _CONTROL_DOC_ID, _CONTROL_CHUNK_ID, "control claim"),
    ):
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, added_context,"
                " anchor_ok, window_membership_ok, extractor_version, ingested_at)"
                " VALUES (:claim, :d, :doc, :chunk, :body, :body, 0, 20, :context,"
                " true, true, 'extractor-test', :at)"
            ),
            {
                "claim": claim_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "chunk": chunk_id,
                "body": body,
                "context": f'["{body}"]',
                "at": _NOW,
            },
        )
        connection.execute(
            text(
                "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
                " source_locators, created_at) VALUES (:d, :chunk, :claim,"
                " :locators, :at)"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "chunk": chunk_id,
                "claim": claim_id,
                "locators": f'[{{"marker":"{body}"}}]',
                "at": _NOW,
            },
        )
    connection.execute(
        text(
            "INSERT INTO claim_extraction_decisions (decision_id, deployment_id,"
            " doc_id, chunk_id, claim_id, decision_type, source_span, edit_detail,"
            " extractor_version, decided_at) VALUES (:id, :d, :doc, :chunk,"
            " :claim, 'decontext_edit', :token, jsonb_build_object('marker',"
            " CAST(:token AS text)), 'extractor-test', :at)"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000040"),
            "d": _DEPLOYMENT_ID,
            "doc": _TARGET_DOC_ID,
            "chunk": _TARGET_CHUNK_ID,
            "claim": _TARGET_CLAIM_ID,
            "token": _TOKEN,
            "at": _NOW,
        },
    )
    connection.execute(
        text(
            "INSERT INTO grounding_audits (audit_id, deployment_id, claim_id,"
            " verdict, judge_version, rationale) VALUES"
            " (:id, :d, :claim, 'sampled_pass', 'judge-test', :token)"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000041"),
            "d": _DEPLOYMENT_ID,
            "claim": _TARGET_CLAIM_ID,
            "token": _TOKEN,
        },
    )
    for entity_id, name, profile in (
        (_EXCLUSIVE_ENTITY_ID, f"Exclusive {_TOKEN}", _TOKEN),
        (_SHARED_ENTITY_ID, "Shared", "shared profile"),
        (_CONTROL_ENTITY_ID, "Control", "control profile"),
    ):
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, type,"
                " canonical_name, normalized_name, profile_summary) VALUES"
                " (:entity, :d, 'Person', :name, lower(:name), :profile)"
            ),
            {
                "entity": entity_id,
                "d": _DEPLOYMENT_ID,
                "name": name,
                "profile": profile,
            },
        )
    connection.execute(
        text(
            "INSERT INTO aliases (alias_id, deployment_id, entity_id, alias_text,"
            " normalized_lemma, provenance) VALUES (:id, :d, :entity, :token,"
            " lower(:token), 'source')"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000025"),
            "d": _DEPLOYMENT_ID,
            "entity": _EXCLUSIVE_ENTITY_ID,
            "token": _TOKEN,
        },
    )
    _seed_mention(
        connection=connection,
        mention_id=_EXCLUSIVE_MENTION_ID,
        doc_id=_TARGET_DOC_ID,
        claim_id=_TARGET_CLAIM_ID,
        entity_id=_EXCLUSIVE_ENTITY_ID,
        surface=_TOKEN,
    )
    _seed_mention(
        connection=connection,
        mention_id=_TARGET_SHARED_MENTION_ID,
        doc_id=_TARGET_DOC_ID,
        claim_id=_TARGET_CLAIM_ID,
        entity_id=_SHARED_ENTITY_ID,
        surface="Shared",
    )
    _seed_mention(
        connection=connection,
        mention_id=_CONTROL_SHARED_MENTION_ID,
        doc_id=_CONTROL_DOC_ID,
        claim_id=_CONTROL_CLAIM_ID,
        entity_id=_SHARED_ENTITY_ID,
        surface="Shared",
    )
    _seed_facts(connection=connection)
    _seed_resolution_residuals(connection=connection)


def _seed_postgres_p1_residuals(*, connection: Connection) -> None:
    """Seed source-bearing D94 state that hard-forget must erase selectively."""
    connection.execute(
        text(
            "INSERT INTO chunk_search (deployment_id, chunk_id, search_text,"
            " embedding, embedding_model, embedding_input_policy_version,"
            " embedding_text_hash) VALUES"
            " (:d, :target_chunk, :target_text,"
            " array_fill(0.125::real, ARRAY[1536])::vector, 'd94-test',"
            " 'e1-embed-input-v1:char', 'target-hash'),"
            " (:d, :control_chunk, 'control search text',"
            " array_fill(0.25::real, ARRAY[1536])::vector, 'd94-test',"
            " 'e1-embed-input-v1:char', 'control-hash')"
        ),
        {
            "d": _DEPLOYMENT_ID,
            "target_chunk": _TARGET_CHUNK_ID,
            "target_text": f"search residual {_TOKEN}",
            "control_chunk": _CONTROL_CHUNK_ID,
        },
    )
    connection.execute(
        text(
            "UPDATE entities SET"
            " embedding = array_fill(0.5::real, ARRAY[1536])::vector,"
            " embedding_model = 'd94-test',"
            " embedding_input_policy_version = 'entity-canonical-name-v1',"
            " embedding_text_hash = 'entity-hash'"
            " WHERE deployment_id = :d AND entity_id = ANY(:entity_ids)"
        ),
        {"d": _DEPLOYMENT_ID, "entity_ids": [_EXCLUSIVE_ENTITY_ID, _CONTROL_ENTITY_ID]},
    )


def _seed_mention(
    *,
    connection: Connection,
    mention_id: UUID,
    doc_id: UUID,
    claim_id: UUID,
    entity_id: UUID,
    surface: str,
) -> None:
    """Attach one mention to its resolved entity."""
    connection.execute(
        text(
            "INSERT INTO mentions (mention_id, deployment_id, surface_form,"
            " normalized_lemma, claim_id, chunk_id, doc_id, context, created_at)"
            " VALUES (:mention, :d, :surface, lower(:surface), :claim, :chunk,"
            " :doc, :surface, :at)"
        ),
        {
            "mention": mention_id,
            "d": _DEPLOYMENT_ID,
            "surface": surface,
            "claim": claim_id,
            "chunk": (
                _TARGET_CHUNK_ID if doc_id == _TARGET_DOC_ID else _CONTROL_CHUNK_ID
            ),
            "doc": doc_id,
            "at": _NOW,
        },
    )
    connection.execute(
        text(
            "INSERT INTO resolution_decisions (decision_id, deployment_id,"
            " mention_id, entity_id, method, confidence, features, resolver_version,"
            " decided_at) VALUES (:decision, :d, :mention, :entity, 'T3', 0.9,"
            " :features, 'resolver-test', :at)"
        ),
        {
            "decision": UUID(int=mention_id.int + 100),
            "d": _DEPLOYMENT_ID,
            "mention": mention_id,
            "entity": entity_id,
            "features": f'{{"marker":"{surface}"}}',
            "at": _NOW,
        },
    )


def _seed_resolution_residuals(*, connection: Connection) -> None:
    """Plant source-bearing ER audit material linked to the target lineage."""
    connection.execute(
        text(
            "INSERT INTO generic_identifier_guard (deployment_id, normalized_lemma,"
            " distinct_entity_count, reason) VALUES (:d, lower(:token), 2, :token)"
        ),
        {"d": _DEPLOYMENT_ID, "token": _TOKEN},
    )
    connection.execute(
        text(
            "INSERT INTO merge_events (merge_id, deployment_id, survivor_id,"
            " absorbed_id, trigger_lemmas, evidence,"
            " pre_merge_membership_snapshot) VALUES"
            " (:id, :d, :shared, :exclusive, ARRAY[CAST(:token AS text)],"
            " jsonb_build_object('marker', CAST(:token AS text)),"
            " jsonb_build_object('mention_id', CAST(:mention AS text),"
            " 'marker', CAST(:token AS text)))"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000039"),
            "d": _DEPLOYMENT_ID,
            "shared": _SHARED_ENTITY_ID,
            "exclusive": _EXCLUSIVE_ENTITY_ID,
            "token": _TOKEN,
            "mention": _EXCLUSIVE_MENTION_ID,
        },
    )
    connection.execute(
        text(
            "INSERT INTO resolution_exclusions (deployment_id, entity_id_low,"
            " entity_id_high, reason, created_by) VALUES"
            " (:d, :exclusive, :shared, :token, 'human')"
        ),
        {
            "d": _DEPLOYMENT_ID,
            "exclusive": _EXCLUSIVE_ENTITY_ID,
            "shared": _SHARED_ENTITY_ID,
            "token": _TOKEN,
        },
    )


def _seed_facts(*, connection: Connection) -> None:
    """Seed target-exclusive facts and shared facts with control support."""
    for relation_id, subject, object_id, label in (
        (_EXCLUSIVE_RELATION_ID, _EXCLUSIVE_ENTITY_ID, _CONTROL_ENTITY_ID, _TOKEN),
        (_SHARED_RELATION_ID, _SHARED_ENTITY_ID, _CONTROL_ENTITY_ID, "shared fact"),
    ):
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id, fact_label,"
                " normalizer_version, evidence_count, ingested_at) VALUES"
                " (:relation, :d, :subject, 'related_to', :object, :label,"
                " 'normalizer-test', 1, :at)"
            ),
            {
                "relation": relation_id,
                "d": _DEPLOYMENT_ID,
                "subject": subject,
                "object": object_id,
                "label": label,
                "at": _NOW,
            },
        )
    for relation_id, claim_id, doc_id in (
        (_EXCLUSIVE_RELATION_ID, _TARGET_CLAIM_ID, _TARGET_DOC_ID),
        (_SHARED_RELATION_ID, _TARGET_CLAIM_ID, _TARGET_DOC_ID),
        (_SHARED_RELATION_ID, _CONTROL_CLAIM_ID, _CONTROL_DOC_ID),
    ):
        connection.execute(
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id,"
                " claim_id, doc_id, stance, normalizer_version, created_at) VALUES"
                " (:d, :relation, :claim, :doc, 'supports', 'normalizer-test', :at)"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "relation": relation_id,
                "claim": claim_id,
                "doc": doc_id,
                "at": _NOW,
            },
        )
    for observation_id, statement in (
        (_EXCLUSIVE_OBSERVATION_ID, _TOKEN),
        (_SHARED_OBSERVATION_ID, "shared observation"),
    ):
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, normalizer_version, evidence_count,"
                " ingested_at) VALUES (:observation, :d, :entity, :statement,"
                " 'normalizer-test', 1, :at)"
            ),
            {
                "observation": observation_id,
                "d": _DEPLOYMENT_ID,
                "entity": (
                    _EXCLUSIVE_ENTITY_ID
                    if observation_id == _EXCLUSIVE_OBSERVATION_ID
                    else _SHARED_ENTITY_ID
                ),
                "statement": statement,
                "at": _NOW,
            },
        )
    for observation_id, claim_id, doc_id in (
        (_EXCLUSIVE_OBSERVATION_ID, _TARGET_CLAIM_ID, _TARGET_DOC_ID),
        (_SHARED_OBSERVATION_ID, _TARGET_CLAIM_ID, _TARGET_DOC_ID),
        (_SHARED_OBSERVATION_ID, _CONTROL_CLAIM_ID, _CONTROL_DOC_ID),
    ):
        connection.execute(
            text(
                "INSERT INTO observation_evidence (deployment_id, observation_id,"
                " claim_id, doc_id, stance, normalizer_version, created_at) VALUES"
                " (:d, :observation, :claim, :doc, 'supports',"
                " 'normalizer-test', :at)"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "observation": observation_id,
                "claim": claim_id,
                "doc": doc_id,
                "at": _NOW,
            },
        )
    for table, fact_column, fact_id in (
        ("relation_adjudications", "relation_id", _SHARED_RELATION_ID),
        ("observation_adjudications", "observation_id", _SHARED_OBSERVATION_ID),
    ):
        connection.execute(
            text(
                f"INSERT INTO {table} (adjudication_id, deployment_id,"
                f" {fact_column}, outcome, method, triggering_claim_id, features,"
                " adjudicator_version) VALUES (:id, :d, :fact, 'noop', 'exact',"
                " :claim, :features, 'adjudicator-test')"
            ),
            {
                "id": UUID(int=fact_id.int + 100),
                "d": _DEPLOYMENT_ID,
                "fact": fact_id,
                "claim": _TARGET_CLAIM_ID,
                "features": f'{{"marker":"{_TOKEN}"}}',
            },
        )


def _seed_knowledge_and_residuals(*, connection: Connection) -> None:
    """Plant source-bearing K, eval, review, and queue rows."""
    connection.execute(
        text(
            "INSERT INTO scopes (scope_id, deployment_id, slug, name, git_path)"
            " VALUES (:scope, :d, 'proof', 'Proof', 'k/proof')"
        ),
        {"scope": _SCOPE_ID, "d": _DEPLOYMENT_ID},
    )
    connection.execute(
        text(
            "INSERT INTO knowledge_artifacts (artifact_id, deployment_id, layer,"
            " page_kind, scope_id, git_path, curation_path, page_summary,"
            " content_hash, inputs_hash, writer_version) VALUES"
            " (:artifact, :d, 'K2', 'compiled', :scope, 'k/proof/page.md',"
            " 'k/proof/page.curation.md', :token, :token, :token, 'writer-test')"
        ),
        {
            "artifact": _ARTIFACT_ID,
            "d": _DEPLOYMENT_ID,
            "scope": _SCOPE_ID,
            "token": _TOKEN,
        },
    )
    connection.execute(
        text(
            "INSERT INTO knowledge_artifact_evidence (evidence_link_id,"
            " deployment_id, artifact_id, doc_id, role) VALUES"
            " (:id, :d, :artifact, :doc, 'cites')"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000029"),
            "d": _DEPLOYMENT_ID,
            "artifact": _ARTIFACT_ID,
            "doc": _TARGET_DOC_ID,
        },
    )
    connection.execute(
        text(
            "INSERT INTO knowledge_compilations (compilation_id, deployment_id,"
            " artifact_id, inputs_hash, candidate_count, cited_count,"
            " uncited_count, writer_version, session_transcript_uri) VALUES"
            " (:id, :d, :artifact, :token, 1, 1, 0, 'writer-test',"
            " 'mem://artifacts/writer-transcript.json')"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000030"),
            "d": _DEPLOYMENT_ID,
            "artifact": _ARTIFACT_ID,
            "token": _TOKEN,
        },
    )
    connection.execute(
        text(
            "INSERT INTO knowledge_plan_runs (run_id, deployment_id, scope_id,"
            " run_kind, trigger, component_version, input_hash,"
            " session_transcript_uri, status) VALUES"
            " (:run, :d, :scope, 'planner', 'human', 'planner-test', :token,"
            " 'mem://artifacts/planner-transcript.json', 'succeeded')"
        ),
        {"run": _PLAN_RUN_ID, "d": _DEPLOYMENT_ID, "scope": _SCOPE_ID, "token": _TOKEN},
    )
    connection.execute(
        text(
            "INSERT INTO knowledge_plan_decisions (decision_id, deployment_id,"
            " scope_id, action, payload, trigger, planner_version, plan_run_id)"
            " VALUES (:id, :d, :scope, 'create_page', :payload, 'human',"
            " 'planner-test', :run)"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000031"),
            "d": _DEPLOYMENT_ID,
            "scope": _SCOPE_ID,
            "payload": f'{{"marker":"{_TOKEN}"}}',
            "run": _PLAN_RUN_ID,
        },
    )
    connection.execute(
        text(
            "INSERT INTO knowledge_refresh_queue (refresh_id, deployment_id,"
            " artifact_id, scope_id, trigger, payload) VALUES"
            " (:id, :d, :artifact, :scope, 'evidence_changed', :payload)"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000032"),
            "d": _DEPLOYMENT_ID,
            "artifact": _ARTIFACT_ID,
            "scope": _SCOPE_ID,
            "payload": f'{{"marker":"{_TOKEN}"}}',
        },
    )
    connection.execute(
        text(
            "INSERT INTO knowledge_subscriptions (subscription_id, deployment_id,"
            " scope_id, name, workflow_endpoint, debounce_seconds) VALUES"
            " (:subscription, :d, :scope, 'proof', 'mem://proof', 1)"
        ),
        {"subscription": _SUBSCRIPTION_ID, "d": _DEPLOYMENT_ID, "scope": _SCOPE_ID},
    )
    connection.execute(
        text(
            "INSERT INTO knowledge_dispatches (dispatch_id, deployment_id,"
            " subscription_id, payload) VALUES (:id, :d, :subscription, :payload)"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000033"),
            "d": _DEPLOYMENT_ID,
            "subscription": _SUBSCRIPTION_ID,
            "payload": f'{{"marker":"{_TOKEN}","doc_id":"{_TARGET_DOC_ID}"}}',
        },
    )
    connection.execute(
        text(
            "INSERT INTO eval_runs (eval_run_id, deployment_id, suite,"
            " component_version, metrics) VALUES"
            " (:id, :d, 'retrieval', 'proof', :payload)"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000034"),
            "d": _DEPLOYMENT_ID,
            "payload": f'{{"marker":"{_TOKEN}"}}',
        },
    )
    connection.execute(
        text(
            "INSERT INTO canary_cases (canary_id, deployment_id, suite,"
            " description, input, expected) VALUES"
            " (:id, :d, 'retrieval', :token, :payload, :payload)"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000035"),
            "d": _DEPLOYMENT_ID,
            "token": _TOKEN,
            "payload": f'{{"marker":"{_TOKEN}"}}',
        },
    )
    connection.execute(
        text(
            "INSERT INTO golden_claim_labels (label_id, deployment_id,"
            " proposition, context, expected_outcome, adjudicated_by) VALUES"
            " (:id, :d, :token, :token, 'keep', 'proof')"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000036"),
            "d": _DEPLOYMENT_ID,
            "token": _TOKEN,
        },
    )
    connection.execute(
        text(
            "INSERT INTO golden_pairs (pair_id, deployment_id, entity_type,"
            " surface_a, surface_b, context_a, context_b, label, hardness,"
            " adjudicated_by) VALUES (:id, :d, 'Person', :token, :token, :token,"
            " :token, 'match', 'easy', 'proof')"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000037"),
            "d": _DEPLOYMENT_ID,
            "token": _TOKEN,
        },
    )
    connection.execute(
        text(
            "INSERT INTO review_queue (review_id, deployment_id, item_kind,"
            " candidate, blast_radius, confidence, expected_impact) VALUES"
            " (:id, :d, 'support_withdrawn', :candidate, 1, 0.5, 0.5)"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000038"),
            "d": _DEPLOYMENT_ID,
            "candidate": (
                f'{{"marker":"{_TOKEN}","entity_id":"{_EXCLUSIVE_ENTITY_ID}"}}'
            ),
        },
    )
    connection.execute(
        text(
            "INSERT INTO processing_state (processing_id, deployment_id,"
            " target_kind, target_id, stage, component_version, content_hash,"
            " lane, last_error, payload) VALUES (:id, :d, 'document', :doc,"
            " 'convert', 'converter-test', :hash, 'steady', :token,"
            " jsonb_build_object('marker', CAST(:token AS text)))"
        ),
        {
            "id": UUID("75000000-0000-0000-0000-000000000042"),
            "d": _DEPLOYMENT_ID,
            "doc": _TARGET_DOC_ID,
            "hash": _TARGET_CONTENT_HASH,
            "token": _TOKEN,
        },
    )


def _assert_scrubbed_and_control_survives(*, engine: Engine) -> None:
    """Prove nominated residuals are absent and independent support remains."""
    with engine.connect() as connection:
        target = connection.execute(
            text(
                "SELECT source_ref, source_uri, title, current_version_id, deleted_at"
                " FROM documents WHERE deployment_id = :d AND doc_id = :doc"
            ),
            {"d": _DEPLOYMENT_ID, "doc": _TARGET_DOC_ID},
        ).one()
        assert target[:4] == (None, None, None, None)
        assert target.deleted_at is not None
        assert _count(connection, "chunks", "doc_id", _TARGET_DOC_ID) == 0
        assert _count(connection, "chunk_search", "chunk_id", _TARGET_CHUNK_ID) == 0
        assert _count(connection, "chunk_search", "chunk_id", _CONTROL_CHUNK_ID) == 1
        assert _count(connection, "claims", "doc_id", _TARGET_DOC_ID) == 0
        assert _count(connection, "mentions", "doc_id", _TARGET_DOC_ID) == 0
        assert _count(connection, "chunk_claims", "claim_id", _TARGET_CLAIM_ID) == 0
        assert (
            _count(connection, "claim_extraction_decisions", "doc_id", _TARGET_DOC_ID)
            == 0
        )
        assert (
            _count(
                connection, "document_structure_generations", "doc_id", _TARGET_DOC_ID
            )
            == 0
        )
        assert (
            _count(connection, "document_skeleton_checks", "doc_id", _TARGET_DOC_ID)
            == 0
        )
        assert _count(connection, "grounding_audits", "claim_id", _TARGET_CLAIM_ID) == 0
        assert (
            _count(connection, "relations", "relation_id", _EXCLUSIVE_RELATION_ID) == 0
        )
        assert (
            _count(
                connection, "observations", "observation_id", _EXCLUSIVE_OBSERVATION_ID
            )
            == 0
        )
        exclusive_entity = connection.execute(
            text(
                "SELECT canonical_name, normalized_name, status, profile_summary"
                " FROM entities WHERE deployment_id = :d AND entity_id = :entity"
            ),
            {"d": _DEPLOYMENT_ID, "entity": _EXCLUSIVE_ENTITY_ID},
        ).one()
        assert exclusive_entity == ("", "", "retired", None)
        exclusive_embedding = connection.execute(
            text(
                "SELECT embedding, embedding_model,"
                " embedding_input_policy_version, embedding_text_hash"
                " FROM entities WHERE deployment_id = :d AND entity_id = :entity"
            ),
            {"d": _DEPLOYMENT_ID, "entity": _EXCLUSIVE_ENTITY_ID},
        ).one()
        assert exclusive_embedding == (None, None, None, None)
        assert connection.execute(
            text(
                "SELECT embedding IS NOT NULL FROM entities"
                " WHERE deployment_id = :d AND entity_id = :entity"
            ),
            {"d": _DEPLOYMENT_ID, "entity": _CONTROL_ENTITY_ID},
        ).scalar_one()
        assert _count(connection, "aliases", "entity_id", _EXCLUSIVE_ENTITY_ID) == 0
        assert (
            _deployment_rows(connection=connection, table="generic_identifier_guard")
            == 0
        )
        merge_audit = connection.execute(
            text(
                "SELECT trigger_lemmas, evidence, pre_merge_membership_snapshot"
                " FROM merge_events WHERE deployment_id = :d"
            ),
            {"d": _DEPLOYMENT_ID},
        ).one()
        assert merge_audit == ([], None, {})
        assert (
            connection.execute(
                text(
                    "SELECT reason FROM resolution_exclusions WHERE deployment_id = :d"
                ),
                {"d": _DEPLOYMENT_ID},
            ).scalar_one()
            is None
        )
        assert (
            _count(connection, "document_crossrefs", "to_doc_id", _TARGET_DOC_ID) == 0
        )
        assert (
            _count(connection, "document_crossrefs", "from_doc_id", _TARGET_DOC_ID) == 0
        )
        processing = connection.execute(
            text(
                "SELECT payload, last_error FROM processing_state"
                " WHERE deployment_id = :d AND stage = 'convert'"
            ),
            {"d": _DEPLOYMENT_ID},
        ).one()
        assert processing == (None, None)

        assert _count(connection, "documents", "doc_id", _CONTROL_DOC_ID) == 1
        assert _count(connection, "claims", "doc_id", _CONTROL_DOC_ID) == 1
        assert _count(connection, "relations", "relation_id", _SHARED_RELATION_ID) == 1
        assert (
            _count(connection, "observations", "observation_id", _SHARED_OBSERVATION_ID)
            == 1
        )
        assert _count(connection, "entities", "entity_id", _SHARED_ENTITY_ID) == 1
        for table in ("relation_adjudications", "observation_adjudications"):
            adjudication = connection.execute(
                text(
                    f"SELECT triggering_claim_id, features FROM {table}"
                    " WHERE deployment_id = :d"
                ),
                {"d": _DEPLOYMENT_ID},
            ).one()
            assert adjudication == (None, None)

        artifact = connection.execute(
            text(
                "SELECT page_summary, content_hash, inputs_hash, status"
                " FROM knowledge_artifacts"
                " WHERE deployment_id = :d AND artifact_id = :artifact"
            ),
            {"d": _DEPLOYMENT_ID, "artifact": _ARTIFACT_ID},
        ).one()
        assert artifact == (None, None, None, "stale")
        for table in (
            "knowledge_artifact_evidence",
            "knowledge_compilations",
            "knowledge_plan_decisions",
            "knowledge_plan_runs",
            "knowledge_refresh_queue",
            "knowledge_dispatches",
            "eval_runs",
            "canary_cases",
            "golden_claim_labels",
            "golden_pairs",
            "review_queue",
        ):
            assert _deployment_rows(connection=connection, table=table) == 0
        assert (
            _deployment_rows(connection=connection, table="knowledge_subscriptions")
            == 1
        )

        token_residuals = connection.execute(
            text(
                "SELECT count(*) FROM ("
                " SELECT raw_uri AS value FROM content_objects WHERE deployment_id = :d"
                " UNION ALL SELECT source_ref FROM documents WHERE deployment_id = :d"
                " UNION ALL SELECT source_uri FROM documents WHERE deployment_id = :d"
                " UNION ALL SELECT title FROM documents WHERE deployment_id = :d"
                " UNION ALL SELECT source_version_ref FROM document_versions"
                "   WHERE deployment_id = :d"
                " UNION ALL SELECT error FROM document_versions WHERE deployment_id = :d"
                " UNION ALL SELECT claim_text FROM claims WHERE deployment_id = :d"
                " UNION ALL SELECT source_span FROM claims WHERE deployment_id = :d"
                " UNION ALL SELECT search_text FROM chunk_search"
                "   WHERE deployment_id = :d"
                " UNION ALL SELECT surface_form FROM mentions WHERE deployment_id = :d"
                " UNION ALL SELECT fact_label FROM relations WHERE deployment_id = :d"
                " UNION ALL SELECT statement FROM observations WHERE deployment_id = :d"
                " UNION ALL SELECT canonical_name FROM entities WHERE deployment_id = :d"
                " UNION ALL SELECT profile_summary FROM entities WHERE deployment_id = :d"
                " UNION ALL SELECT page_summary FROM knowledge_artifacts"
                "   WHERE deployment_id = :d"
                ") residual WHERE value LIKE '%' || :token || '%'"
            ),
            {"d": _DEPLOYMENT_ID, "token": _TOKEN},
        ).scalar_one()
        assert token_residuals == 0


def _count(connection: Connection, table: str, column: str, value: UUID) -> int:
    """Count one exact UUID in a test-controlled table and column."""
    return int(
        connection.execute(
            text(
                f"SELECT count(*) FROM {table}"
                f" WHERE deployment_id = :d AND {column} = :value"
            ),
            {"d": _DEPLOYMENT_ID, "value": value},
        ).scalar_one()
    )


def _deployment_rows(*, connection: Connection, table: str) -> int:
    """Count deployment-scoped rows in one test-controlled table."""
    return int(
        connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE deployment_id = :d"),
            {"d": _DEPLOYMENT_ID},
        ).scalar_one()
    )
