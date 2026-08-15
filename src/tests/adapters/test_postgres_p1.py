"""Focused D94 proofs for PostgreSQL-native P1 writes and ranked filtering."""

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.postgres_p1 import P1SearchUnavailableError
from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1EntityRow
from rememberstack.model import P1FactRow
from rememberstack.model.assured_operations import AtFactTime
from rememberstack.model.assured_operations import CurrentFactTime
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import FactCatalog
from rememberstack.spine.settings import load_database_settings
from tests.surfaces.lineage_seed import seed_entity_mention
from tests.surfaces.lineage_seed import seed_live_document_lineage

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("5f000000-0000-0000-0000-000000000094")
_OTHER_DEPLOYMENT_ID = UUID("5f000000-0000-0000-0000-000000000095")
_MODEL = "qwen/qwen3-embedding-8b"
_NOW = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)


def _vector(*, axis: int) -> tuple[float, ...]:
    """Return one non-zero fixed-width basis vector."""
    values = [0.0] * P1_VECTOR_DIMENSIONS
    values[axis] = 1.0
    return tuple(values)


def test_fact_upserts_commit_each_authority_row_independently() -> None:
    """A P1 batch never holds one fact lock while waiting on a peer row."""
    engine = MagicMock(spec=Engine)
    connection = engine.begin.return_value.__enter__.return_value
    connection.execute.return_value.rowcount = 1
    index = PostgresP1Index(engine=engine, embedding_model=_MODEL)

    index.upsert_facts(
        rows=tuple(
            P1FactRow(
                fact_id=uuid4(),
                deployment_id=_DEPLOYMENT_ID,
                kind="observation",
                label=f"independent fact {axis}",
                status="active",
                valid_from=None,
                valid_until=None,
                ingested_at=_NOW,
                invalidated_at=None,
                vector=_vector(axis=axis),
            )
            for axis in (0, 1)
        )
    )

    assert engine.begin.call_count == 2
    assert connection.execute.call_count == 2


def test_unscoped_fact_search_orders_by_vector_distance_first() -> None:
    """An empty entity scope keeps the HNSW-compatible leading sort key."""
    engine = MagicMock(spec=Engine)
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = True
    index = PostgresP1Index(engine=engine, embedding_model=_MODEL)

    assert (
        index.search_facts_scored(
            deployment_id=str(_DEPLOYMENT_ID),
            vector=_vector(axis=0),
            k=5,
            kind="relation",
            time=CurrentFactTime(),
            evaluated_at=_NOW,
        )
        == ()
    )

    ranked_sql = str(connection.execute.call_args_list[-1].args[0])
    assert "coverage" not in ranked_sql
    assert "ORDER BY indexed.embedding <=> CAST(:query_vector AS vector)" in ranked_sql
    assert "ORDER BY distance, qualifier, item_id" in ranked_sql


def test_entity_scoped_fact_search_keeps_coverage_before_similarity() -> None:
    """A real entity scope still ranks multi-anchor coverage before distance."""
    engine = MagicMock(spec=Engine)
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = True
    index = PostgresP1Index(engine=engine, embedding_model=_MODEL)

    assert (
        index.search_facts_scored(
            deployment_id=str(_DEPLOYMENT_ID),
            vector=_vector(axis=0),
            k=5,
            kind="relation",
            time=CurrentFactTime(),
            evaluated_at=_NOW,
            entity_ids=(str(uuid4()),),
        )
        == ()
    )

    ranked_sql = str(connection.execute.call_args_list[-1].args[0])
    assert "AS coverage" in ranked_sql
    assert "ORDER BY coverage DESC," in ranked_sql


def test_unscoped_fact_nomination_defers_authority_to_its_caller() -> None:
    """The fact-context candidate path is vector-first and authority-join free."""
    engine = MagicMock(spec=Engine)
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = True
    index = PostgresP1Index(engine=engine, embedding_model=_MODEL)

    assert (
        index.nominate_facts_scored(
            deployment_id=str(_DEPLOYMENT_ID),
            vector=_vector(axis=0),
            k=5,
            kind="observation",
            time=CurrentFactTime(),
            evaluated_at=_NOW,
        )
        == ()
    )

    ranked_sql = str(connection.execute.call_args_list[-1].args[0])
    assert "memory_v1" not in ranked_sql
    assert "v_memory_fact_visible" not in ranked_sql
    assert "indexed.invalidated_at IS NULL" in ranked_sql
    assert "ORDER BY indexed.embedding <=> CAST(:query_vector AS vector)" in ranked_sql


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose one isolated PostgreSQL test spine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for D94 adapter proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def seeded(database_engine: Engine) -> dict[str, object]:
    """Seed two searchable rows under real PostgreSQL authority views."""
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="postgres-p1",
            name="PostgreSQL P1",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    entity_a, entity_b, entity_c = uuid4(), uuid4(), uuid4()
    both_claim, one_claim = uuid4(), uuid4()
    two_anchor_fact, one_anchor_fact = uuid4(), uuid4()
    with database_engine.begin() as connection:
        for entity_id, name in (
            (entity_a, "Aster"),
            (entity_b, "Beacon"),
            (entity_c, "Cedar"),
        ):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, type,"
                    " canonical_name, normalized_name) VALUES (:entity,"
                    " :deployment, 'Concept', :name, lower(:name))"
                ),
                {"entity": entity_id, "deployment": _DEPLOYMENT_ID, "name": name},
            )
        both = seed_live_document_lineage(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            label="postgres-p1-both",
            at=_NOW,
        )
        one = seed_live_document_lineage(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            label="postgres-p1-one",
            at=_NOW,
        )
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, is_current_testimony, extractor_version,"
                " ingested_at, asserted_at) VALUES"
                " (:both_claim, :deployment, :both_doc, :both_chunk,"
                " 'Aster and Beacon share a launch plan', 'shared launch plan',"
                " 0, 18, true, true, true, 'd94-test', :at, :at),"
                " (:one_claim, :deployment, :one_doc, :one_chunk,"
                " 'Aster keeps a detailed checklist', 'detailed checklist',"
                " 0, 18, true, true, true, 'd94-test', :at, :at)"
            ),
            {
                "both_claim": both_claim,
                "one_claim": one_claim,
                "deployment": _DEPLOYMENT_ID,
                "both_doc": both.doc_id,
                "both_chunk": both.chunk_id,
                "one_doc": one.doc_id,
                "one_chunk": one.chunk_id,
                "at": _NOW,
            },
        )
        for entity_id in (entity_a, entity_b):
            seed_entity_mention(
                connection=connection,
                deployment_id=_DEPLOYMENT_ID,
                entity_id=entity_id,
                doc_id=both.doc_id,
                chunk_id=both.chunk_id,
                claim_id=both_claim,
                surface_form=f"anchor-{entity_id}",
                at=_NOW,
                resolver_version="d94-test",
            )
        seed_entity_mention(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            entity_id=entity_a,
            doc_id=one.doc_id,
            chunk_id=one.chunk_id,
            claim_id=one_claim,
            surface_form="Aster",
            at=_NOW,
            resolver_version="d94-test",
        )
        connection.execute(
            text(
                "UPDATE chunks SET location_facts_json ="
                " jsonb_build_object('schema', 'location_facts.v1', 'facts',"
                " jsonb_build_object('source_shape', CASE"
                "   WHEN chunk_id = :both_chunk THEN 'transcript' ELSE 'document' END),"
                " 'elements', '[]'::jsonb)"
                " WHERE chunk_id IN (:both_chunk, :one_chunk)"
            ),
            {"both_chunk": both.chunk_id, "one_chunk": one.chunk_id},
        )
        for fact_id, object_id, label, valid_from, valid_until, ingested_at in (
            (two_anchor_fact, entity_b, "Aster coordinates Beacon", None, None, _NOW),
            (one_anchor_fact, entity_c, "Aster coordinates Cedar", None, None, _NOW),
        ):
            connection.execute(
                text(
                    "INSERT INTO relations (relation_id, deployment_id,"
                    " subject_entity_id, predicate, object_entity_id,"
                    " normalizer_version, fact_label, valid_from, valid_until,"
                    " ingested_at) VALUES (:fact, :deployment, :subject,"
                    " 'works_for', :object, 'd94-test', :label, :valid_from,"
                    " :valid_until, :ingested_at)"
                ),
                {
                    "fact": fact_id,
                    "deployment": _DEPLOYMENT_ID,
                    "subject": entity_a,
                    "object": object_id,
                    "label": label,
                    "valid_from": valid_from,
                    "valid_until": valid_until,
                    "ingested_at": ingested_at,
                },
            )
            claim_id = both_claim if fact_id == two_anchor_fact else one_claim
            doc_id = both.doc_id if fact_id == two_anchor_fact else one.doc_id
            connection.execute(
                text(
                    "INSERT INTO relation_evidence (deployment_id, relation_id,"
                    " claim_id, doc_id, stance, normalizer_version) VALUES"
                    " (:deployment, :fact, :claim, :doc, 'supports', 'd94-test')"
                ),
                {
                    "deployment": _DEPLOYMENT_ID,
                    "fact": fact_id,
                    "claim": claim_id,
                    "doc": doc_id,
                },
            )

    index = PostgresP1Index(engine=database_engine, embedding_model=_MODEL)
    index.configure_channels(deployment_id=_DEPLOYMENT_ID)
    near, far = _vector(axis=0), _vector(axis=1)
    index.upsert_chunks(
        rows=(
            P1ChunkRow(
                chunk_id=both.chunk_id,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=both.doc_id,
                version_id=both.version_id,
                section_role="body",
                text="Aster and Beacon discuss a shared launch plan.",
                vector=far,
                policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
                embedder_generation=_MODEL,
                embedding_text_hash=embedding_text_hash(
                    "Aster and Beacon discuss a shared launch plan."
                ),
                source_kind="upload",
                source_shape="transcript",
            ),
            P1ChunkRow(
                chunk_id=one.chunk_id,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=one.doc_id,
                version_id=one.version_id,
                section_role="body",
                text="Aster keeps a detailed checklist.",
                vector=near,
                policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
                embedder_generation=_MODEL,
                embedding_text_hash=embedding_text_hash(
                    "Aster keeps a detailed checklist."
                ),
                source_kind="upload",
                source_shape="document",
            ),
        )
    )
    index.upsert_claims(
        rows=(
            P1ClaimRow(
                claim_id=both_claim,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=both.doc_id,
                chunk_id=both.chunk_id,
                text="Aster and Beacon share a launch plan",
                is_current_testimony=True,
                is_attributed=False,
                vector=far,
            ),
            P1ClaimRow(
                claim_id=one_claim,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=one.doc_id,
                chunk_id=one.chunk_id,
                text="Aster keeps a detailed checklist",
                is_current_testimony=True,
                is_attributed=False,
                vector=near,
            ),
        )
    )
    index.upsert_facts(
        rows=tuple(
            P1FactRow(
                fact_id=fact_id,
                deployment_id=_DEPLOYMENT_ID,
                kind="relation",
                label=label,
                status="active",
                valid_from=valid_from,
                valid_until=valid_until,
                ingested_at=ingested_at,
                invalidated_at=None,
                vector=vector,
            )
            for fact_id, label, valid_from, valid_until, ingested_at, vector in (
                (two_anchor_fact, "Aster coordinates Beacon", None, None, _NOW, far),
                (one_anchor_fact, "Aster coordinates Cedar", None, None, _NOW, near),
            )
        )
    )
    return {
        "index": index,
        "entity_a": entity_a,
        "entity_b": entity_b,
        "both_claim": both_claim,
        "both_chunk": both.chunk_id,
        "two_anchor_fact": two_anchor_fact,
        "near": near,
    }


def test_multi_anchor_coverage_precedes_similarity_and_candidate_cut(
    seeded: dict[str, object],
) -> None:
    """Two-anchor rows win at k=1 even when one-anchor rows are more similar."""
    index = seeded["index"]
    assert isinstance(index, PostgresP1Index)
    entity_ids = (str(seeded["entity_a"]), str(seeded["entity_b"]))
    query = seeded["near"]
    assert isinstance(query, tuple)

    claims = index.search_claims_scored(
        deployment_id=str(_DEPLOYMENT_ID),
        vector=query,
        k=1,
        current_only=True,
        entity_ids=entity_ids,
    )
    chunks = index.search_chunks_scored(
        deployment_id=str(_DEPLOYMENT_ID), vector=query, k=1, entity_ids=entity_ids
    )
    facts = index.search_facts_scored(
        deployment_id=str(_DEPLOYMENT_ID),
        vector=query,
        k=1,
        kind="relation",
        time=CurrentFactTime(),
        evaluated_at=_NOW,
        entity_ids=entity_ids,
    )

    assert tuple(item.item_id for item in claims) == (str(seeded["both_claim"]),)
    assert tuple(item.item_id for item in chunks) == (str(seeded["both_chunk"]),)
    assert tuple(item.item_id for item in facts) == (str(seeded["two_anchor_fact"]),)


def test_chunk_source_shape_and_fact_time_filter_before_limit(
    database_engine: Engine, seeded: dict[str, object]
) -> None:
    """Normalized authority filters apply inside the ranked SQL statement."""
    index = seeded["index"]
    assert isinstance(index, PostgresP1Index)
    query = seeded["near"]
    assert isinstance(query, tuple)

    chunks = index.search_chunks_lexical_scored(
        deployment_id=str(_DEPLOYMENT_ID),
        query="launch plan",
        k=1,
        equality_filters={"source_shape": "transcript"},
    )
    assert tuple(item.item_id for item in chunks) == (str(seeded["both_chunk"]),)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE relations SET valid_from = :from_time, valid_until = :to_time"
                " WHERE relation_id = :fact"
            ),
            {
                "fact": seeded["two_anchor_fact"],
                "from_time": _NOW - timedelta(days=10),
                "to_time": _NOW - timedelta(days=2),
            },
        )
    try:
        current = index.search_facts_scored(
            deployment_id=str(_DEPLOYMENT_ID),
            vector=query,
            k=1,
            kind="relation",
            candidate_keys=(("relation", str(seeded["two_anchor_fact"])),),
            time=CurrentFactTime(),
            evaluated_at=_NOW,
        )
        past = index.search_facts_scored(
            deployment_id=str(_DEPLOYMENT_ID),
            vector=query,
            k=1,
            kind="relation",
            candidate_keys=(("relation", str(seeded["two_anchor_fact"])),),
            time=AtFactTime(at=_NOW - timedelta(days=3)),
            evaluated_at=_NOW,
        )
        assert current == ()
        assert tuple(item.item_id for item in past) == (str(seeded["two_anchor_fact"]),)
    finally:
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE relations SET valid_from = NULL, valid_until = NULL"
                    " WHERE relation_id = :fact"
                ),
                {"fact": seeded["two_anchor_fact"]},
            )


def test_channel_readiness_fails_closed(
    database_engine: Engine, seeded: dict[str, object]
) -> None:
    """A mismatched or unpublished channel never falls back to an exact scan."""
    index = seeded["index"]
    assert isinstance(index, PostgresP1Index)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE p1_search_channels SET ready = false"
                " WHERE deployment_id = :deployment AND target = 'claims'"
                " AND channel = 'semantic'"
            ),
            {"deployment": _DEPLOYMENT_ID},
        )
    try:
        with pytest.raises(P1SearchUnavailableError, match="not ready"):
            index.search_claims_scored(
                deployment_id=str(_DEPLOYMENT_ID),
                vector=_vector(axis=0),
                k=1,
                current_only=True,
            )
    finally:
        index.configure_channels(deployment_id=_DEPLOYMENT_ID)


def test_ranked_search_never_crosses_deployments(
    database_engine: Engine, seeded: dict[str, object]
) -> None:
    """A closer vector in another deployment cannot enter the result set."""
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_OTHER_DEPLOYMENT_ID,
            slug="postgres-p1-other",
            name="PostgreSQL P1 other deployment",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    first_entity = seeded["entity_a"]
    assert isinstance(first_entity, UUID)
    other_entity = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, type,"
                " canonical_name, normalized_name) VALUES"
                " (:entity, :deployment, 'Concept', 'Nearest foreign row',"
                " 'nearest foreign row')"
            ),
            {"entity": other_entity, "deployment": _OTHER_DEPLOYMENT_ID},
        )
    index = seeded["index"]
    assert isinstance(index, PostgresP1Index)
    index.upsert_entities(
        rows=(
            P1EntityRow(
                entity_id=first_entity,
                deployment_id=_DEPLOYMENT_ID,
                canonical_name="Aster",
                type="Concept",
                vector=_vector(axis=1),
            ),
            P1EntityRow(
                entity_id=other_entity,
                deployment_id=_OTHER_DEPLOYMENT_ID,
                canonical_name="Nearest foreign row",
                type="Concept",
                vector=_vector(axis=0),
            ),
        )
    )

    results = index.search_entities_scored(
        deployment_id=str(_DEPLOYMENT_ID), vector=_vector(axis=0), k=1
    )

    assert tuple(item.item_id for item in results) == (str(first_entity),)


def test_observation_statement_is_the_embedding_fallback(
    database_engine: Engine, seeded: dict[str, object]
) -> None:
    """An observation without a distinct label embeds its statement."""
    observation_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, obs_label, normalizer_version)"
                " VALUES (:observation, :deployment, :entity, :statement, NULL,"
                " 'd94-test')"
            ),
            {
                "observation": observation_id,
                "deployment": _DEPLOYMENT_ID,
                "entity": seeded["entity_a"],
                "statement": "Aster uses a documented release checklist.",
            },
        )
        connection.execute(
            text(
                "INSERT INTO observation_evidence (deployment_id, observation_id,"
                " claim_id, doc_id, stance, normalizer_version)"
                " SELECT :deployment, :observation, claim_id, doc_id, 'supports',"
                " 'd94-test' FROM claims WHERE claim_id = :claim"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "observation": observation_id,
                "claim": seeded["both_claim"],
            },
        )

    with database_engine.connect() as connection:
        doc_id = connection.execute(
            text("SELECT doc_id FROM claims WHERE claim_id = :claim"),
            {"claim": seeded["both_claim"]},
        ).scalar_one()
    observations = FactCatalog(engine=database_engine).observations_for_embedding(
        deployment_id=_DEPLOYMENT_ID, doc_id=doc_id, embedding_model=_MODEL
    )

    assert tuple(item.observation_id for item in observations) == (observation_id,)
    assert observations[0].obs_label == "Aster uses a documented release checklist."
    index = seeded["index"]
    assert isinstance(index, PostgresP1Index)
    index.upsert_facts(
        rows=(
            P1FactRow(
                fact_id=observation_id,
                deployment_id=_DEPLOYMENT_ID,
                kind="observation",
                label=observations[0].obs_label,
                status=observations[0].status,
                valid_from=observations[0].valid_from,
                valid_until=observations[0].valid_until,
                ingested_at=observations[0].ingested_at,
                invalidated_at=observations[0].invalidated_at,
                vector=_vector(axis=2),
            ),
        )
    )
    with database_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT embedding IS NOT NULL FROM observations"
                " WHERE deployment_id = :deployment"
                " AND observation_id = :observation"
            ),
            {"deployment": _DEPLOYMENT_ID, "observation": observation_id},
        ).scalar_one()
