"""Real-PostgreSQL proofs for the D159 forget: hide, sweep, guard (design §5)."""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
import re
import subprocess
from typing import cast
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

from rememberstack.adapters.selfhost import LocalGitRepository
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.model import ClaimedWork
from rememberstack.model import DecisionRecord
from rememberstack.model import DecisionType
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentForgottenError
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import EnqueueWork
from rememberstack.model import Forget
from rememberstack.model import ForgetStatus
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import SelectionResponse
from rememberstack.ports import ObjectPurgePort
from rememberstack.ports import ProjectionPurgePort
from rememberstack.spine import ClaimCatalog
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import EntityProfileRefresher
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import KnowledgeControlPlane
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.forget import sweep_work
from rememberstack.spine.selection_catalog import SelectionCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import ForgetHandler
from rememberstack.workers import ForgetKnowledgeEraser
from rememberstack.workers import ForgetService

_ROOT = Path(__file__).resolve().parents[3]
_NOW = datetime(2026, 7, 21, tzinfo=UTC)
_TOKEN = "S55-UNIQUE-FORGET-TOKEN"
_TARGET_CONTENT_HASH = "a" * 64
_CONTROL_CONTENT_HASH = "b" * 64

_DEPLOYMENT_ID = UUID("75000000-0000-0000-0000-000000000001")
_TARGET_DOC_ID = UUID("75000000-0000-0000-0000-000000000002")
_CONTROL_DOC_ID = UUID("75000000-0000-0000-0000-000000000003")
_TAG_ID = UUID("75000000-0000-0000-0000-000000000156")
_TARGET_VERSION_ID = UUID("75000000-0000-0000-0000-000000000004")
_CONTROL_VERSION_ID = UUID("75000000-0000-0000-0000-000000000005")
_TARGET_GENERATION_ID = UUID("75000000-0000-0000-0000-0000000000a1")
_CONTROL_GENERATION_ID = UUID("75000000-0000-0000-0000-0000000000a2")
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
_MERGED_SURVIVOR_ID = UUID("75000000-0000-0000-0000-000000000029")
_TARGET_MEASUREMENT_ID = UUID("75000000-0000-0000-0000-000000000030")
_TARGET_IDS = (
    _TARGET_DOC_ID,
    _TARGET_VERSION_ID,
    _TARGET_REPRESENTATION_ID,
    _TARGET_CHUNK_ID,
    _TARGET_CLAIM_ID,
    _EXCLUSIVE_MENTION_ID,
    _TARGET_SHARED_MENTION_ID,
)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for real forget proofs")
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
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE mentions, resolution_decisions, chunks,"
                " chunk_claims, claims, claim_extraction_decisions,"
                " testimony_currency_events, relation_evidence,"
                " observation_evidence, deployments CASCADE"
            )
        )
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="forget",
            name="Forget proof",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
            knowledge_repo_uri="mem://knowledge.git",
        )
    )
    with database_engine.begin() as connection:
        _seed_documents(connection=connection)
        _seed_structure_provenance(connection=connection)
        _seed_evidence(connection=connection)
        _seed_postgres_p1_residuals(connection=connection)
        _seed_knowledge_and_residuals(connection=connection)
    return database_engine


def _hide(*, engine: Engine) -> Forget:
    """Request the forget of the target lineage (D159 §3.2)."""
    forget, created = ForgetService(
        engine=engine, catalog=ForgetCatalog(engine=engine)
    ).request(deployment_id=_DEPLOYMENT_ID, doc_id=_TARGET_DOC_ID, forget_id=_FORGET_ID)
    assert created
    return forget


def test_hide_is_one_transaction_and_a_repeat_returns_the_record(
    seeded_engine: Engine,
) -> None:
    """Tombstone, cascade, record and the first sweep land together (§3.2)."""
    forget = _hide(engine=seeded_engine)
    assert forget.status is ForgetStatus.ERASING
    assert forget.version_ids == (_TARGET_VERSION_ID,)
    assert forget.content_hashes == (_TARGET_CONTENT_HASH,)
    assert forget.source_identity_hash is None  # an upload is guarded by its bytes
    with seeded_engine.connect() as connection:
        hidden = connection.execute(
            text(
                "SELECT d.deleted_at IS NOT NULL, v.deleted_at IS NOT NULL"
                " FROM documents d JOIN document_versions v USING (doc_id)"
                " WHERE d.doc_id = :doc"
            ),
            {"doc": _TARGET_DOC_ID},
        ).one()
        assert tuple(hidden) == (True, True)
        assert not connection.execute(
            text("SELECT is_current_testimony FROM claims WHERE claim_id = :c"),
            {"c": _TARGET_CLAIM_ID},
        ).scalar_one()
        sweeps = connection.execute(
            text(
                "SELECT payload FROM processing_state WHERE target_kind = 'forget'"
                " AND target_id = :f AND stage = 'forget'"
            ),
            {"f": _FORGET_ID},
        ).scalars()
        assert [payload["sweep"] for payload in sweeps] == [1]
        record = json.dumps(
            connection.execute(
                text("SELECT to_jsonb(f) FROM forgets f WHERE forget_id = :f"),
                {"f": _FORGET_ID},
            ).scalar_one()
        )
        assert _TOKEN not in record
    again, created = ForgetService(
        engine=seeded_engine, catalog=ForgetCatalog(engine=seeded_engine)
    ).request(deployment_id=_DEPLOYMENT_ID, doc_id=_TARGET_DOC_ID)
    assert not created and again == forget
    with pytest.raises(DocumentNotFoundError):
        ForgetService(
            engine=seeded_engine, catalog=ForgetCatalog(engine=seeded_engine)
        ).request(deployment_id=_DEPLOYMENT_ID, doc_id=uuid4())


def test_sweep_erases_the_subtree_and_keeps_the_control_exactly(
    seeded_engine: Engine,
) -> None:
    """§5 tests 1 and 3: erased, and nothing extra removed; a repeat is a no-op."""
    before = _control_counts(engine=seeded_engine)
    catalog = ForgetCatalog(engine=seeded_engine)
    forget = _hide(engine=seeded_engine)
    findings = catalog.sweep_postgres(forget=forget)

    assert findings.removed
    assert findings.compiled_artifact_ids == (_ARTIFACT_ID,)
    assert {key.root for key in findings.object_keys} == {
        f"{_TARGET_DOC_ID}/{_TARGET_CONTENT_HASH}/original.txt",
        # transcripts of the rebuilt page's compilation and of every plan run
        "mem://artifacts/writer-transcript.json",
        "mem://artifacts/planner-transcript.json",
    }
    assert str(_TARGET_DOC_ID) in catalog.needles(forget=forget)
    _assert_scrubbed_and_control_survives(engine=seeded_engine)
    assert _control_counts(engine=seeded_engine) == before
    again = catalog.sweep_postgres(forget=forget)
    assert not again.removed
    # The rebuild stays pending on the record until step 4 succeeds.
    assert again.compiled_artifact_ids == (_ARTIFACT_ID,)
    catalog.clear_k_pending(forget=forget, artifact_ids=(_ARTIFACT_ID,))
    assert catalog.sweep_postgres(forget=forget).compiled_artifact_ids == ()
    # forget-reapply: a restored K repository may hold the recorded page again.
    reapplied = catalog.sweep_postgres(forget=forget, reapply=True)
    assert reapplied.compiled_artifact_ids == (_ARTIFACT_ID,)


_ID_COLUMNS = (
    "doc_id",
    "version_id",
    "representation_id",
    "section_id",
    "chunk_id",
    "claim_id",
    "mention_id",
    "from_doc_id",
    "to_doc_id",
    "from_version_id",
    "current_version_id",
    "claim_lineage_id",
)
# Every public table with a column that names a document, version,
# representation, section, chunk, claim or mention. "swept" rows are deleted or
# unbound by the sweep; the others are reviewed content-free rows that keep the
# IDs only (D159 §3.1). A new table fails this test until it is classified.
_CLASSIFIED: dict[str, str] = {
    "chunk_claims": "swept",
    "chunk_search": "swept",
    "chunks": "swept",
    "claim_extraction_decisions": "swept",
    "claims": "swept",
    "document_crossrefs": "swept",
    "document_effective_periods": "swept",
    "document_effective_time_events": "swept",
    "document_entity_bindings": "swept",
    "document_metadata": "swept",
    "document_names": "swept",
    "document_people": "swept",
    "document_reference_generations": "swept",
    "document_representations": "swept",
    "document_sections": "swept",
    "document_skeleton_checks": "swept",
    "document_structure_generations": "swept",
    "document_tags": "swept",
    "document_version_scope": "swept",
    "document_versions": "swept",
    "documents": "swept",
    "fact_applications": "swept",
    "grounding_audits": "swept",
    "knowledge_artifact_evidence": "swept",
    "mentions": "swept",
    "normalization_outputs": "swept",
    "normalize_observation_staging": "swept",
    "obs_flush_entity_units": "swept",
    "obs_flush_version_state": "swept",
    "observation_evidence": "swept",
    "processing_layer_gaps": "swept",
    "relation_evidence": "swept",
    "resolution_decisions": "swept",
    "selection_results": "swept",
    "forgets": "the forget record itself (IDs and hashes only)",
    "managed_ingest_measurements": "metering receipt: counts and the UUIDs as billing metadata",
    "testimony_currency_events": "content-free currency history (IDs, reason, time)",
}


def test_schema_coverage_classifies_every_document_reference(
    seeded_engine: Engine,
) -> None:
    """§5 test 2: every referencing table is swept or on the reviewed allow-list."""
    with seeded_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.table_name, c.column_name
                FROM information_schema.columns c
                JOIN pg_class k ON k.relname = c.table_name
                JOIN pg_namespace n ON n.oid = k.relnamespace
                 AND n.nspname = c.table_schema
                WHERE c.table_schema = 'public' AND k.relkind IN ('r', 'p')
                  AND NOT k.relispartition
                  AND c.table_name NOT LIKE 'template\\_%'
                  AND c.column_name = ANY(CAST(:columns AS text[]))
                """
            ),
            {"columns": list(_ID_COLUMNS)},
        ).all()
    columns: dict[str, list[str]] = {}
    for table, column in rows:
        columns.setdefault(table, []).append(column)
    assert set(columns) == set(_CLASSIFIED), (
        "classify every table that references a document in _CLASSIFIED"
    )
    catalog = ForgetCatalog(engine=seeded_engine)
    forget = _hide(engine=seeded_engine)
    catalog.sweep_postgres(forget=forget)
    with seeded_engine.connect() as connection:
        for table, names in sorted(columns.items()):
            if _CLASSIFIED[table] != "swept":
                continue
            for column in names:
                remaining = connection.execute(
                    text(
                        f"SELECT count(*) FROM {table}"
                        f" WHERE {column} = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": list(_TARGET_IDS)},
                ).scalar_one()
                assert remaining == 0, f"{table}.{column} still names the document"


def test_late_writer_residue_is_removed_by_the_next_sweep(
    seeded_engine: Engine,
) -> None:
    """§5 test 6 (PostgreSQL): late rows and a stale label are residue."""
    catalog = ForgetCatalog(engine=seeded_engine)
    forget = _hide(engine=seeded_engine)
    assert catalog.sweep_postgres(forget=forget).removed
    assert not catalog.sweep_postgres(forget=forget).removed
    late_claim = uuid4()
    with seeded_engine.begin() as connection:
        # A worker that kept writing after the hide.
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version) VALUES (:claim, :d,"
                " :doc, :chunk, :token, :token, 0, 20, true, true, 'late')"
            ),
            {
                "claim": late_claim,
                "d": _DEPLOYMENT_ID,
                "doc": _TARGET_DOC_ID,
                "chunk": _TARGET_CHUNK_ID,
                "token": _TOKEN,
            },
        )
        # A stale label job writing the shared fact's old wording back.
        connection.execute(
            text(
                "UPDATE relations SET fact_label = :token,"
                " fact_label_version = 'stale' WHERE relation_id = :r"
            ),
            {"token": f"{_TOKEN} old wording", "r": _SHARED_RELATION_ID},
        )
    assert catalog.sweep_postgres(forget=forget).removed
    with seeded_engine.connect() as connection:
        assert _count(connection, "claims", "claim_id", late_claim) == 0
        assert (
            connection.execute(
                text("SELECT fact_label FROM relations WHERE relation_id = :r"),
                {"r": _SHARED_RELATION_ID},
            ).scalar_one()
            is None
        )
    assert not catalog.sweep_postgres(forget=forget).removed


def test_work_of_a_forgotten_document_is_never_claimed_and_loses_its_lease(
    seeded_engine: Engine,
) -> None:
    """§3.2: claiming and renewing check the tombstone; the sweep skips the rows."""
    ledger = WorkLedger(engine=seeded_engine, settings=WorkLedgerSettings())
    running = ledger.enqueue(
        work=EnqueueWork(
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.CHUNK,
            target_id=_TARGET_CHUNK_ID,
            stage=PipelineStage.EMBED_CHUNK,
            component_version="forget-test",
            content_hash="c",
            lane=ProcessingLane.STEADY,
        )
    )
    claimed = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EMBED_CHUNK,
        lane=ProcessingLane.STEADY,
    )
    assert isinstance(claimed, ClaimedWork)
    assert claimed.processing_id == running.processing_id
    for target_kind, target_id in (
        (ProcessingTarget.DOCUMENT_VERSION, _TARGET_VERSION_ID),
        (ProcessingTarget.CLAIM, _TARGET_CLAIM_ID),
        (ProcessingTarget.CHUNK, _CONTROL_CHUNK_ID),
    ):
        ledger.enqueue(
            work=EnqueueWork(
                deployment_id=_DEPLOYMENT_ID,
                target_kind=target_kind,
                target_id=target_id,
                stage=PipelineStage.EMBED_CLAIM,
                component_version="forget-test",
                content_hash="c",
                lane=ProcessingLane.STEADY,
            )
        )
    forget = _hide(engine=seeded_engine)

    assert not ledger.renew_lease(
        processing_id=claimed.processing_id, attempt=claimed.attempt
    )
    other = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EMBED_CLAIM,
        lane=ProcessingLane.STEADY,
    )
    # §5 test 5: the control document's work proceeds; the target's never runs.
    assert isinstance(other, ClaimedWork) and other.target_id == _CONTROL_CHUNK_ID
    assert (
        ledger.claim_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.EMBED_CLAIM,
            lane=ProcessingLane.STEADY,
        )
        is None
    )
    ForgetCatalog(engine=seeded_engine).sweep_postgres(forget=forget)
    with seeded_engine.connect() as connection:
        statuses = connection.execute(
            text(
                "SELECT status::text FROM processing_state WHERE deployment_id = :d"
                " AND component_version = 'forget-test'"
                " AND target_id <> :control ORDER BY status"
            ),
            {"d": _DEPLOYMENT_ID, "control": _CONTROL_CHUNK_ID},
        ).scalars()
        assert set(statuses) == {"skipped"}


def test_reingest_guard_refuses_bytes_and_identity_and_names_the_record(
    seeded_engine: Engine,
) -> None:
    """§3.6 / §5 test 9 without the flag: refused before bytes and at registration."""
    catalog = ForgetCatalog(engine=seeded_engine)
    with seeded_engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET source_kind = 'filesystem' WHERE doc_id = :doc"),
            {"doc": _TARGET_DOC_ID},
        )
    forget = _hide(engine=seeded_engine)
    assert forget.source_identity_hash is not None
    with pytest.raises(DocumentForgottenError) as refused:
        catalog.guard_ingest(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="upload",
            source_ref=_TARGET_CONTENT_HASH,
            content_hash=_TARGET_CONTENT_HASH,
        )
    assert refused.value.forget_id == _FORGET_ID
    with pytest.raises(DocumentForgottenError):
        catalog.guard_ingest(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="filesystem",
            source_ref=f"target-ref-{_TOKEN}",
            content_hash="c" * 64,
        )
    catalog.guard_ingest(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="filesystem",
        source_ref="another-file",
        content_hash="c" * 64,
    )
    # A released hash (a deliberate re-add) no longer guards; the sweep keys stay.
    with seeded_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE forgets SET released_hashes = CAST(:hashes AS text[])"
                " WHERE forget_id = :f"
            ),
            {"hashes": [_TARGET_CONTENT_HASH], "f": _FORGET_ID},
        )
    catalog.guard_ingest(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="upload",
        source_ref=_TARGET_CONTENT_HASH,
        content_hash=_TARGET_CONTENT_HASH,
    )


def test_shared_survivor_profile_rebuild_removes_forgotten_phrase(
    seeded_engine: Engine,
) -> None:
    """A shared entity's profile is cleared and rebuilt from what survives."""
    from rememberstack.spine.clustering import apply_merge

    old_input = f"Shared\n{_TOKEN}\nshared observation"
    with seeded_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                " normalized_name) VALUES (:entity, :deployment,"
                " 'Shared Survivor', 'shared survivor')"
            ),
            {"entity": _MERGED_SURVIVOR_ID, "deployment": _DEPLOYMENT_ID},
        )
        merge_id = apply_merge(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            survivor_id=_MERGED_SURVIVOR_ID,
            absorbed_id=_SHARED_ENTITY_ID,
            trigger_lemmas=["shared"],
            evidence={"proof": "forget redirect"},
            blast_radius=2,
            decided_by="auto",
        )
        assert merge_id is not None
        connection.execute(
            text(
                "UPDATE entities SET profile_summary = :summary,"
                " embedding = array_fill(0.75::real, ARRAY[1536])::vector,"
                " embedding_model = 'profile-test',"
                " embedding_input_policy_version = 'entity-profile-v2',"
                " embedding_text_hash = :hash"
                " WHERE deployment_id = :deployment"
                " AND entity_id = ANY(CAST(:entities AS uuid[]))"
            ),
            {
                "summary": f"{_TOKEN}; shared observation",
                "hash": embedding_text_hash(old_input),
                "deployment": _DEPLOYMENT_ID,
                "entities": [_SHARED_ENTITY_ID, _MERGED_SURVIVOR_ID],
            },
        )
    forget = _hide(engine=seeded_engine)
    findings = ForgetCatalog(engine=seeded_engine).sweep_postgres(forget=forget)
    assert {_SHARED_ENTITY_ID, _MERGED_SURVIVOR_ID} <= set(findings.profile_entity_ids)
    provider = FakeModelProvider()
    refresh = EntityProfileRefresher(
        engine=seeded_engine, model_provider=provider, embedding_model="profile-test"
    ).refresh_many(
        deployment_id=_DEPLOYMENT_ID,
        entity_ids=findings.profile_entity_ids,
        call_key=f"profile:forget:{_FORGET_ID}",
    )
    shared = next(item for item in refresh if item.entity_id == _MERGED_SURVIVOR_ID)
    assert shared.has_evidence and shared.updated
    assert _TOKEN not in shared.salient_facts
    assert provider.embedded_texts
    assert all(_TOKEN not in profile_input for profile_input in provider.embedded_texts)
    with seeded_engine.connect() as connection:
        for entity_id in (_MERGED_SURVIVOR_ID, _SHARED_ENTITY_ID):
            row = connection.execute(
                text(
                    "SELECT profile_summary, embedding IS NOT NULL,"
                    " embedding_text_hash FROM entities"
                    " WHERE deployment_id = :deployment AND entity_id = :entity"
                ),
                {"deployment": _DEPLOYMENT_ID, "entity": entity_id},
            ).one()
            assert _TOKEN not in str(row.profile_summary)
            assert "control claim" in str(row.profile_summary)
            assert row[1] is True
            assert row.embedding_text_hash != embedding_text_hash(old_input)


@pytest.mark.parametrize("phase", ("selection", "claimify"))
def test_late_extraction_response_cannot_publish_after_the_hide(
    seeded_engine: Engine, phase: str
) -> None:
    """Both publication APIs refuse a response once the lineage is hidden."""
    _hide(engine=seeded_engine)
    with pytest.raises(LookupError):
        if phase == "selection":
            SelectionCatalog(engine=seeded_engine).freeze(
                deployment_id=_DEPLOYMENT_ID,
                representation_id=_TARGET_REPRESENTATION_ID,
                chunk_id=_TARGET_CHUNK_ID,
                extractor_version="forget-test",
                input_hash="hash",
                selection=SelectionResponse(candidates=()),
                cards=(),
                diagnostics=(),
                truncated=False,
            )
        else:
            ClaimCatalog(engine=seeded_engine).record_extraction(
                claims=(),
                decisions=(
                    DecisionRecord(
                        decision_id=_FORGET_ID,
                        deployment_id=_DEPLOYMENT_ID,
                        doc_id=_TARGET_DOC_ID,
                        chunk_id=_TARGET_CHUNK_ID,
                        claim_id=None,
                        decision_type=DecisionType.CLAIMIFY_OMITTED,
                        source_span=_TOKEN,
                        reason=None,
                        edit_detail=None,
                        protected_class=None,
                        extractor_version="forget-test",
                    ),
                ),
                claimify_input_hash="hash",
            )
    with seeded_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM selection_results"
                    " WHERE extractor_version = 'forget-test'"
                )
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM claim_extraction_decisions"
                    " WHERE extractor_version='forget-test'"
                )
            ).scalar_one()
            == 0
        )


def _control_counts(*, engine: Engine) -> dict[str, int]:
    """Exact control-side counts that a forget must leave unchanged (§5 test 3)."""
    with engine.connect() as connection:
        return {
            "documents": _count(connection, "documents", "doc_id", _CONTROL_DOC_ID),
            "versions": _count(
                connection, "document_versions", "doc_id", _CONTROL_DOC_ID
            ),
            "claims": _count(connection, "claims", "doc_id", _CONTROL_DOC_ID),
            "chunks": _count(connection, "chunks", "doc_id", _CONTROL_DOC_ID),
            "mentions": _count(connection, "mentions", "doc_id", _CONTROL_DOC_ID),
            "relation_evidence": _count(
                connection, "relation_evidence", "doc_id", _CONTROL_DOC_ID
            ),
            "observation_evidence": _count(
                connection, "observation_evidence", "doc_id", _CONTROL_DOC_ID
            ),
            "shared_relation": _count(
                connection, "relations", "relation_id", _SHARED_RELATION_ID
            ),
            "shared_observation": _count(
                connection, "observations", "observation_id", _SHARED_OBSERVATION_ID
            ),
            "shared_entity": _count(
                connection, "entities", "entity_id", _SHARED_ENTITY_ID
            ),
            "control_entity": _count(
                connection, "entities", "entity_id", _CONTROL_ENTITY_ID
            ),
            "control_original": int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM content_objects WHERE content_hash = :h"
                        " AND purged_at IS NULL"
                    ),
                    {"h": _CONTROL_CONTENT_HASH},
                ).scalar_one()
            ),
        }


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
        (_TARGET_CONTENT_HASH, f"{_TARGET_DOC_ID}/{_TARGET_CONTENT_HASH}/original.txt"),
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
    for version_id, doc_id, marker in (
        (_TARGET_VERSION_ID, _TARGET_DOC_ID, _TOKEN),
        (_CONTROL_VERSION_ID, _CONTROL_DOC_ID, "control"),
    ):
        # D134 metadata, people and observed names are source-bearing.
        connection.execute(
            text(
                "INSERT INTO document_metadata (deployment_id, version_id, doc_id,"
                " family, file_name, source_path, title, thread_ref, extra,"
                " metadata_mapping_version) VALUES (:d, :version, :doc, 'text',"
                " :file_name, :path, :marker, :marker, CAST(:extra AS jsonb),"
                " 'test')"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "version": version_id,
                "doc": doc_id,
                "file_name": f"{marker}.txt",
                "path": f"folder/{marker}",
                "marker": marker,
                "extra": json.dumps({"reply_to": marker}),
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_people (deployment_id, version_id, role,"
                " ordinal, display_name, address, normalized_name,"
                " normalized_address, provenance) VALUES (:d, :version, 'author',"
                " 0, :marker, :address, :marker, :address, 'source')"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "version": version_id,
                "marker": marker,
                "address": f"{marker}@example.com",
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_names (deployment_id, version_id,"
                " observed_at, file_name, title, source_path, name_text, origin)"
                " VALUES (:d, :version, now(), :file_name, :marker, :path,"
                " :name_text, 'ingest')"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "version": version_id,
                "file_name": f"{marker}.txt",
                "marker": marker,
                "path": f"folder/{marker}",
                "name_text": f"{marker}.txt {marker} folder/{marker}",
            },
        )
    connection.execute(
        text(
            """
            INSERT INTO managed_ingest_measurements (
              measurement_id, deployment_id, doc_id, version_id,
              ingest_attempt_id, org_id, project_id, opaque_lineage_id,
              opaque_source_version_id, format_family, cost_class,
              native_quantity, unit, canonical_source_bytes,
              classifier_version, measurement_algorithm_version,
              processing_profile_id, measured_at, convert_component_version,
              lane, delivery_state, decision_reason
            ) VALUES (
              :measurement_id, :deployment_id, :doc_id, :version_id,
              'ing_forget_proof', :org_id, :project_id, 'opaque-lineage',
              'opaque-version', 'text', 'doc-text', 24, 'normalized_character',
              :source_bytes, 'route-cost-class-v1',
              'document-md-unicode-whitespace-scalars-v1',
              'doc-text-standard-v1', :measured_at, 'e0-test', 'steady',
              'parked', 'control_plane_unavailable'
            )
            """
        ),
        {
            "measurement_id": _TARGET_MEASUREMENT_ID,
            "deployment_id": _DEPLOYMENT_ID,
            "doc_id": _TARGET_DOC_ID,
            "version_id": _TARGET_VERSION_ID,
            "org_id": UUID("75000000-0000-0000-0000-000000000031"),
            "project_id": UUID("75000000-0000-0000-0000-000000000032"),
            "source_bytes": len(_TOKEN.encode("utf-8")),
            "measured_at": _NOW,
        },
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
    # D140: references are made by source versions through a generation
    for generation_id, doc_id, version_id in (
        (_CONTROL_GENERATION_ID, _CONTROL_DOC_ID, _CONTROL_VERSION_ID),
        (_TARGET_GENERATION_ID, _TARGET_DOC_ID, _TARGET_VERSION_ID),
    ):
        connection.execute(
            text(
                "INSERT INTO document_reference_generations (generation_id,"
                " deployment_id, doc_id, version_id, origin, input_hash,"
                " request_seq, artifact_uri, status) VALUES (:g, :d, :doc, :v,"
                " 'supplied', :token, 1, :uri, 'active')"
            ),
            {
                "g": generation_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "v": version_id,
                "token": _TOKEN,
                "uri": f"mem://artifacts/{doc_id}/references/{_TOKEN}.ndjson",
            },
        )
    for crossref_id, source_doc, source_version, generation_id, target, kind in (
        (
            UUID("75000000-0000-0000-0000-000000000024"),
            _CONTROL_DOC_ID,
            _CONTROL_VERSION_ID,
            _CONTROL_GENERATION_ID,
            _TARGET_DOC_ID,
            "cites",
        ),
        (
            UUID("75000000-0000-0000-0000-000000000043"),
            _TARGET_DOC_ID,
            _TARGET_VERSION_ID,
            _TARGET_GENERATION_ID,
            _CONTROL_DOC_ID,
            "links_to",
        ),
    ):
        connection.execute(
            text(
                "INSERT INTO document_crossrefs (crossref_id, deployment_id,"
                " from_doc_id, from_version_id, generation_id, to_doc_id, kind,"
                " origin, raw_citation, context, resolved) VALUES (:id, :d,"
                " :source, :version, :generation, :target,"
                " CAST(:kind AS crossref_kind), 'supplied', :token, :token, true)"
            ),
            {
                "id": crossref_id,
                "d": _DEPLOYMENT_ID,
                "source": source_doc,
                "version": source_version,
                "generation": generation_id,
                "target": target,
                "kind": kind,
                "token": _TOKEN,
            },
        )
    # D140: the target carries a version key, a declared period and its event
    connection.execute(
        text("UPDATE document_versions SET version_key = :token WHERE version_id = :v"),
        {"token": f"edition-{_TOKEN}", "v": _TARGET_VERSION_ID},
    )
    connection.execute(
        text(
            "INSERT INTO document_effective_periods (period_id, deployment_id,"
            " doc_id, version_id, effective_from, declared_by)"
            " VALUES (gen_random_uuid(), :d, :doc, :v, :at, 'ingest')"
        ),
        {
            "d": _DEPLOYMENT_ID,
            "doc": _TARGET_DOC_ID,
            "v": _TARGET_VERSION_ID,
            "at": _NOW,
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_effective_time_events (deployment_id, doc_id, event)"
            " VALUES (:d, :doc, 'declared')"
        ),
        {"d": _DEPLOYMENT_ID, "doc": _TARGET_DOC_ID},
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
                " source_locators, evidence_spans, created_at)"
                " VALUES (:d, :chunk, :claim, :locators, CAST(:spans AS jsonb), :at)"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "chunk": chunk_id,
                "claim": claim_id,
                "locators": f'[{{"marker":"{body}"}}]',
                "spans": '[{"char_start": 0, "char_end": 1}]',
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
                "INSERT INTO entities (entity_id, deployment_id,"
                " canonical_name, normalized_name, profile_summary) VALUES"
                " (:entity, :d, :name, lower(:name), :profile)"
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
    for doc_id, entity_id in (
        (_TARGET_DOC_ID, _EXCLUSIVE_ENTITY_ID),
        (_CONTROL_DOC_ID, _SHARED_ENTITY_ID),
    ):
        connection.execute(
            text(
                "INSERT INTO document_entity_bindings ("
                " deployment_id, doc_id, canonical_lemma, entity_id)"
                " VALUES (:deployment, :doc, 'shared', :entity)"
            ),
            {"deployment": _DEPLOYMENT_ID, "doc": doc_id, "entity": entity_id},
        )
    # D156: a registered tag assigned to both lineages.
    connection.execute(
        text(
            "INSERT INTO tags (deployment_id, tag_id, name, description)"
            " VALUES (:deployment, :tag, 'project:forget', 'Forget proof.')"
            " ON CONFLICT DO NOTHING"
        ),
        {"deployment": _DEPLOYMENT_ID, "tag": _TAG_ID},
    )
    for doc_id in (_TARGET_DOC_ID, _CONTROL_DOC_ID):
        connection.execute(
            text(
                "INSERT INTO document_tags (deployment_id, doc_id, tag_id)"
                " VALUES (:deployment, :doc, :tag)"
            ),
            {"deployment": _DEPLOYMENT_ID, "doc": doc_id, "tag": _TAG_ID},
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
            " entity_id_high, reason, created_by, basis, is_effective) VALUES"
            " (:d, :exclusive, :shared, :token, 'human', 'human', true)"
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
            "INSERT INTO golden_pairs (pair_id, deployment_id,"
            " surface_a, surface_b, context_a, context_b, label, hardness,"
            " adjudicated_by) VALUES (:id, :d, :token, :token, :token,"
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
    """The target's subtree is gone, shared facts survive with clean wording."""
    with engine.connect() as connection:
        assert _count(connection, "documents", "doc_id", _TARGET_DOC_ID) == 0
        assert _count(connection, "document_versions", "doc_id", _TARGET_DOC_ID) == 0
        assert (
            _count(
                connection,
                "document_representations",
                "representation_id",
                _TARGET_REPRESENTATION_ID,
            )
            == 0
        )
        # The receipt keeps its counts and the erased version's UUID.
        meter = connection.execute(
            text(
                "SELECT doc_id, version_id, delivery_state"
                " FROM managed_ingest_measurements"
                " WHERE measurement_id = :measurement_id"
            ),
            {"measurement_id": _TARGET_MEASUREMENT_ID},
        ).one()
        assert tuple(meter) == (_TARGET_DOC_ID, _TARGET_VERSION_ID, "parked")
        assert _count(connection, "chunks", "doc_id", _TARGET_DOC_ID) == 0
        assert _count(connection, "chunk_search", "chunk_id", _TARGET_CHUNK_ID) == 0
        assert _count(connection, "chunk_search", "chunk_id", _CONTROL_CHUNK_ID) == 1
        assert _count(connection, "claims", "doc_id", _TARGET_DOC_ID) == 0
        assert _count(connection, "mentions", "doc_id", _TARGET_DOC_ID) == 0
        assert (
            _count(connection, "document_entity_bindings", "doc_id", _TARGET_DOC_ID)
            == 0
        )
        assert (
            _count(connection, "document_entity_bindings", "doc_id", _CONTROL_DOC_ID)
            == 1
        )
        # D156: the lineage's assignments go; the registry stays.
        assert _count(connection, "document_tags", "doc_id", _TARGET_DOC_ID) == 0
        assert _count(connection, "document_tags", "doc_id", _CONTROL_DOC_ID) == 1
        assert _count(connection, "tags", "tag_id", _TAG_ID) == 1
        assert _count(connection, "chunk_claims", "claim_id", _TARGET_CLAIM_ID) == 0
        for table in ("document_metadata", "document_people", "document_names"):
            assert _count(connection, table, "version_id", _TARGET_VERSION_ID) == 0
            assert _count(connection, table, "version_id", _CONTROL_VERSION_ID) == 1
        for table in (
            "claim_extraction_decisions",
            "document_structure_generations",
            "document_skeleton_checks",
            "document_reference_generations",
            "document_effective_periods",
            "document_effective_time_events",
            "document_version_scope",
        ):
            assert _count(connection, table, "doc_id", _TARGET_DOC_ID) == 0, table
        assert _count(connection, "document_version_scope", "doc_id", _CONTROL_DOC_ID)
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
                "SELECT canonical_name, normalized_name, status, profile_summary,"
                " embedding, embedding_model, embedding_text_hash"
                " FROM entities WHERE deployment_id = :d AND entity_id = :entity"
            ),
            {"d": _DEPLOYMENT_ID, "entity": _EXCLUSIVE_ENTITY_ID},
        ).one()
        assert tuple(exclusive_entity) == ("", "", "retired", None, None, None, None)
        assert _count(connection, "aliases", "entity_id", _EXCLUSIVE_ENTITY_ID) == 0
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
        # D140 §9: the control's reference TO the forgotten lineage is the
        # control's own content; it survives unbound, ready to re-bind
        assert connection.execute(
            text(
                "SELECT to_doc_id, resolved FROM document_crossrefs"
                " WHERE deployment_id = :d AND from_doc_id = :doc"
            ),
            {"d": _DEPLOYMENT_ID, "doc": _CONTROL_DOC_ID},
        ).one() == (None, False)
        assert (
            _count(connection, "document_crossrefs", "from_doc_id", _TARGET_DOC_ID) == 0
        )
        processing = connection.execute(
            text(
                "SELECT payload, last_error FROM processing_state"
                " WHERE deployment_id = :d AND stage = 'convert'"
                " AND target_id = :doc"
            ),
            {"d": _DEPLOYMENT_ID, "doc": _TARGET_DOC_ID},
        ).one()
        assert processing == (None, None)
        original = connection.execute(
            text(
                "SELECT raw_uri, purged_at IS NOT NULL FROM content_objects"
                " WHERE deployment_id = :d AND content_hash = :h"
            ),
            {"d": _DEPLOYMENT_ID, "h": _TARGET_CONTENT_HASH},
        ).one()
        assert tuple(original) == (f"forgotten/{_TARGET_CONTENT_HASH}", True)

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
            assert _deployment_rows(connection=connection, table=table) == 0, table
        assert (
            _deployment_rows(connection=connection, table="knowledge_subscriptions")
            == 1
        )

        token_residuals = connection.execute(
            text(
                "SELECT count(*) FROM ("
                " SELECT raw_uri AS value FROM content_objects WHERE deployment_id = :d"
                " UNION ALL SELECT collected::text FROM forgets WHERE deployment_id = :d"
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
                " UNION ALL SELECT alias_text FROM aliases WHERE deployment_id = :d"
                " UNION ALL SELECT profile_summary FROM entities WHERE deployment_id = :d"
                " UNION ALL SELECT page_summary FROM knowledge_artifacts"
                "   WHERE deployment_id = :d"
                " UNION ALL SELECT file_name FROM document_metadata"
                "   WHERE deployment_id = :d"
                " UNION ALL SELECT source_path FROM document_metadata"
                "   WHERE deployment_id = :d"
                " UNION ALL SELECT title FROM document_metadata WHERE deployment_id = :d"
                " UNION ALL SELECT extra::text FROM document_metadata"
                "   WHERE deployment_id = :d"
                " UNION ALL SELECT display_name FROM document_people"
                "   WHERE deployment_id = :d"
                " UNION ALL SELECT address FROM document_people WHERE deployment_id = :d"
                " UNION ALL SELECT name_text FROM document_names"
                "   WHERE deployment_id = :d"
                " UNION ALL SELECT payload::text FROM processing_state"
                "   WHERE deployment_id = :d"
                " UNION ALL SELECT last_error FROM processing_state"
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


def test_mutable_fact_payloads_and_date_witnesses_are_erased(
    seeded_engine: Engine,
) -> None:
    """D118 outputs, mixed-source model payloads and date witnesses are erased."""
    from uuid import uuid4

    own, surviving = uuid4(), uuid4()
    with seeded_engine.begin() as connection:
        for claim, app, phrase in (
            (_TARGET_CLAIM_ID, own, _TOKEN),
            (_CONTROL_CLAIM_ID, surviving, "independent assertion"),
        ):
            connection.execute(
                text("""INSERT INTO normalization_outputs
                (deployment_id,claim_id,normalizer_version,output,accepted_outputs)
                VALUES(:dep,:claim,'forget-test',jsonb_build_object('observations',
                    jsonb_build_array(jsonb_build_object('statement',CAST(:phrase AS text)))), '[]')"""),
                {"dep": _DEPLOYMENT_ID, "claim": claim, "phrase": phrase},
            )
            connection.execute(
                text("""INSERT INTO fact_applications(application_id,deployment_id,
                claim_id,normalizer_version,output_kind,output_ordinal,adjudicator_version,
                subject_entity_id,attempt_id,input_hash,input_claim_ids,prepared,decision)
                VALUES(:id,:dep,:claim,'forget-test','observation',0,'forget-test',:subject,
                :attempt,'hash',CAST(:claims AS uuid[]),jsonb_build_object('source',CAST(:phrase AS text)),
                jsonb_build_object('rationale',CAST(:phrase AS text)))"""),
                {
                    "id": app,
                    "dep": _DEPLOYMENT_ID,
                    "claim": claim,
                    "subject": _SHARED_ENTITY_ID,
                    "attempt": uuid4(),
                    "claims": [_TARGET_CLAIM_ID, _CONTROL_CLAIM_ID],
                    "phrase": _TOKEN,
                },
            )
        for chunk, representation, phrase, app in (
            (_TARGET_CHUNK_ID, _TARGET_REPRESENTATION_ID, _TOKEN, own),
            (
                _CONTROL_CHUNK_ID,
                _CONTROL_REPRESENTATION_ID,
                "independent reference",
                surviving,
            ),
        ):
            connection.execute(
                text("""INSERT INTO selection_results
                    (deployment_id,chunk_id,representation_id,extractor_version,
                     selection_input_hash,output,cards)
                    VALUES (:dep,:chunk,:rep,'forget-test','hash',
                      jsonb_build_object('source',CAST(:phrase AS text)),
                      jsonb_build_array(jsonb_build_object('quote',CAST(:phrase AS text))))"""),
                {
                    "dep": _DEPLOYMENT_ID,
                    "chunk": chunk,
                    "rep": representation,
                    "phrase": phrase,
                },
            )
            connection.execute(
                text("""INSERT INTO application_context_bindings
                    (deployment_id,application_id,ordinal,entity_id,resolver_decision_id)
                    VALUES (:dep,:app,0,:entity,:decision)"""),
                {
                    "dep": _DEPLOYMENT_ID,
                    "app": app,
                    "entity": _SHARED_ENTITY_ID,
                    "decision": uuid4(),
                },
            )
        connection.execute(
            text("""UPDATE observations SET valid_from='2022-01-01Z',
            valid_until='2023-01-01Z',valid_precision='year',window_claim_ids=CAST(:claims AS uuid[]),
            period_basis='basis-over-forgotten-claims'
            WHERE observation_id=:id"""),
            {"claims": [_TARGET_CLAIM_ID], "id": _SHARED_OBSERVATION_ID},
        )
        # D140 §8.2: a stored window-review answer for the touched fact
        connection.execute(
            text("""INSERT INTO fact_window_review_attempts
            (deployment_id,fact_kind,fact_id,input_digest,claimed_by,answer,answered_at)
            VALUES (:dep,'observation',:id,'digest','job',
              jsonb_build_object('rationale',CAST(:phrase AS text)),now())"""),
            {"dep": _DEPLOYMENT_ID, "id": _SHARED_OBSERVATION_ID, "phrase": _TOKEN},
        )
    catalog = ForgetCatalog(engine=seeded_engine)
    forget = _hide(engine=seeded_engine)
    assert catalog.sweep_postgres(forget=forget).removed
    assert not catalog.sweep_postgres(forget=forget).removed
    with seeded_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM fact_applications WHERE application_id=:id"),
                {"id": own},
            ).scalar_one()
            == 0
        )
        assert connection.execute(
            text("SELECT chunk_id FROM selection_results WHERE deployment_id=:dep"),
            {"dep": _DEPLOYMENT_ID},
        ).scalars().all() == [_CONTROL_CHUNK_ID]
        assert connection.execute(
            text(
                "SELECT application_id FROM application_context_bindings WHERE deployment_id=:dep"
            ),
            {"dep": _DEPLOYMENT_ID},
        ).scalars().all() == [surviving]
        row = connection.execute(
            text(
                "SELECT attempt_id,prepared,decision,input_claim_ids FROM fact_applications WHERE application_id=:id"
            ),
            {"id": surviving},
        ).one()
        assert tuple(row) == (None, None, None, [_CONTROL_CLAIM_ID])
        row = connection.execute(
            text(
                "SELECT valid_from,valid_until,valid_precision,window_claim_ids,statement FROM observations WHERE observation_id=:id"
            ),
            {"id": _SHARED_OBSERVATION_ID},
        ).one()
        assert tuple(row[:4]) == (None, None, "unknown", [])
        assert _TOKEN not in row[4]
        assert (
            connection.execute(
                text("SELECT period_basis FROM observations WHERE observation_id=:id"),
                {"id": _SHARED_OBSERVATION_ID},
            ).scalar_one()
            is None
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM fact_window_review_attempts WHERE fact_id=:id"
                ),
                {"id": _SHARED_OBSERVATION_ID},
            ).scalar_one()
            == 0
        )


def test_readiness_answers_for_a_forgotten_version_as_for_an_unknown_one(
    seeded_engine: Engine,
) -> None:
    """§5 tests 1 and 4: public /readiness reveals nothing about a forgotten version."""
    from rememberstack.model import ReadinessRequirements
    from rememberstack.spine import PipelineReadinessCatalog

    readiness = PipelineReadinessCatalog(engine=seeded_engine, expected_components={})
    require = ReadinessRequirements(pipeline=True, p1=False, live_graph=False, p3=False)

    def answer(version_id: UUID) -> str:
        report = readiness.inspect(
            deployment_id=_DEPLOYMENT_ID, version_ids=(version_id,), require=require
        )
        dumped = re.sub(r'"checked_at":"[^"]*"', "", report.model_dump_json())
        return dumped.replace(str(version_id), "<id>")

    forget = _hide(engine=seeded_engine)
    assert answer(_TARGET_VERSION_ID) == answer(uuid4())
    ForgetCatalog(engine=seeded_engine).sweep_postgres(forget=forget)
    assert answer(_TARGET_VERSION_ID) == answer(uuid4())


class _NoObjects:
    """Object and corpus stores hold nothing for this proof."""

    def purge_objects(self, **_: object) -> int:
        return 0

    def erase_document(self, **_: object) -> bool:
        return False


def _k_repository(*, root: Path) -> Path:
    """The K repository: a compiled page in the snapshot citing the document,
    an authored page whose old revision cited only its exclusive relation, and
    an authored page that cites the document now."""
    repository = root / "knowledge"
    for arguments in (
        ("init", "--quiet", "-b", "main", str(repository)),
        ("-C", str(repository), "config", "user.name", "Fixture"),
        ("-C", str(repository), "config", "user.email", "fixture@example.test"),
    ):
        subprocess.run(("git", *arguments), check=True, capture_output=True)
    notes = repository / "k/notes"
    notes.mkdir(parents=True)
    (notes / "decision.md").write_text(
        f"{_TOKEN} per relation {_EXCLUSIVE_RELATION_ID}\n", encoding="utf-8"
    )
    (notes / "open.md").write_text(f"Cites doc:{_TARGET_DOC_ID}\n", encoding="utf-8")
    _k_commit(repository=repository, message="authored")
    (notes / "decision.md").write_text("Redacted long ago.\n", encoding="utf-8")
    _k_commit(repository=repository, message="redacted")
    adapter = LocalGitRepository(
        repository=repository, author_name="Fixture", author_email="f@example.test"
    )
    checkout = root / "seed"
    adapter.checkout(destination=checkout)
    (checkout / "k/proof").mkdir(parents=True)
    (checkout / "k/proof/page.md").write_text(f"# {_TOKEN}\n", encoding="utf-8")
    adapter.publish(worktree=checkout, compiled_paths={"k/proof/page.md"})
    return repository


def _k_commit(*, repository: Path, message: str) -> None:
    for arguments in (("add", "-A"), ("commit", "--quiet", "-m", message)):
        subprocess.run(
            ("git", "-C", str(repository), *arguments), check=True, capture_output=True
        )


def _k_objects(repository: Path) -> bytes:
    """Every object the K repository stores, reachable or not."""
    return subprocess.run(
        ("git", "-C", str(repository), "cat-file", "--batch-all-objects", "--batch"),
        check=True,
        capture_output=True,
    ).stdout


def _settle(*, handler: ForgetHandler) -> None:
    """Run the forget's sweeps the way workers would, until none follows."""
    work = sweep_work(
        deployment_id=_DEPLOYMENT_ID,
        forget_id=_FORGET_ID,
        sweep=1,
        failures=0,
        not_before=None,
        first=True,
    )
    for _ in range(10):
        outcome = handler.handle(
            work=ClaimedWork(
                processing_id=uuid4(),
                deployment_id=work.deployment_id,
                target_kind=work.target_kind,
                target_id=work.target_id,
                stage=work.stage,
                component_version=work.component_version,
                content_hash=work.content_hash,
                lane=work.lane,
                attempt=1,
                payload=work.payload,
            ),
            meter=NoopCostMeter(),
        )
        if not outcome.follow_up:
            return
        (work,) = outcome.follow_up
    raise AssertionError("the forget never settled")


def test_knowledge_plane_erasure_and_the_authored_wait(
    seeded_engine: Engine, tmp_path: Path
) -> None:
    """§5 tests 1 (K legs) and 8: compiled page gone, old revision rewritten,
    the citing authored page flagged and waited on, then completed by the K sync."""
    repository = _k_repository(root=tmp_path)
    open_page = "k/notes/open.md"
    authored_id = uuid4()
    with seeded_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO knowledge_artifacts (artifact_id, deployment_id, layer,"
                " page_kind, git_path) VALUES (:a, :d, 'K2', 'authored', :path)"
            ),
            {"a": authored_id, "d": _DEPLOYMENT_ID, "path": open_page},
        )
    catalog = ForgetCatalog(engine=seeded_engine)
    control = KnowledgeControlPlane(engine=seeded_engine)
    knowledge = ForgetKnowledgeEraser(
        catalog=catalog,
        control_plane=control,
        repository=LocalGitRepository(
            repository=repository, author_name="R", author_email="r@example.test"
        ),
    )
    handler = ForgetHandler(
        service=ForgetService(engine=seeded_engine, catalog=catalog),
        catalog=catalog,
        profile_refresher=RecordingProfileRefresher(),
        object_purgers=(cast(ObjectPurgePort, _NoObjects()),),
        projection_purger=cast(ProjectionPurgePort, _NoObjects()),
        knowledge=knowledge,
        quiet_period_s=0,
    )
    _hide(engine=seeded_engine)

    _settle(handler=handler)

    record = catalog.record(deployment_id=_DEPLOYMENT_ID, forget_id=_FORGET_ID)
    assert record.status is ForgetStatus.WAITING_FOR_REDACTION
    assert record.blocking_k_paths == (open_page,)
    assert control.authored_review_state(artifact_id=authored_id).redaction_required
    objects = _k_objects(repository)
    assert _TOKEN.encode() not in objects
    assert str(_EXCLUSIVE_RELATION_ID).encode() not in objects
    assert (repository / "k/notes/decision.md").read_text() == "Redacted long ago.\n"
    with seeded_engine.connect() as connection:
        page = connection.execute(
            text(
                "SELECT status::text, content_hash FROM knowledge_artifacts"
                " WHERE artifact_id = :a"
            ),
            {"a": _ARTIFACT_ID},
        ).one()
    assert tuple(page) == ("stale", None)  # ordinary K work rebuilds it

    # The owner redacts; the K sync (here: its check over a fresh checkout)
    # rewrites that page's history and completes the forget.
    (repository / open_page).write_text("Redacted by the owner.\n", encoding="utf-8")
    _k_commit(repository=repository, message="owner redacts")
    checkout = tmp_path / "k-sync"
    LocalGitRepository(
        repository=repository, author_name="R", author_email="r@example.test"
    ).checkout(destination=checkout)
    knowledge.sync_redactions(deployment_id=_DEPLOYMENT_ID, worktree=checkout)

    record = catalog.record(deployment_id=_DEPLOYMENT_ID, forget_id=_FORGET_ID)
    assert (record.status, record.blocking_k_paths) == (ForgetStatus.COMPLETE, ())
    assert str(_TARGET_DOC_ID).encode() not in _k_objects(repository)
