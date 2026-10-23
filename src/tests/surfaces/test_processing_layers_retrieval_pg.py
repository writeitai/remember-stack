"""D143 §7 retrieval over processing layers, on PostgreSQL.

A live lineage with chunks and a claim is published into the real P1
channels. The proofs switch layers off (by writing the configuration row the
way ``setup`` does) or narrow a request with ``layers``, and check what each
read consults, refuses and discloses — including, by capturing every SQL
statement, that a read whose claims layer is not admitted never touches a claim
relation.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from datetime import UTC
import re
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import BoundedPostgresReadPool
from rememberstack.adapters import PostgresP1Index
from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.model import AssuredOperationName
from rememberstack.model import ContextBundleV2
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import Grain
from rememberstack.model import NegativeKind
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ClaimRow
from rememberstack.model.client import ProcessingLayer
from rememberstack.model.processing_layers import LayerSelectionError
from rememberstack.model.processing_layers import LayerUnavailableError
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.processing_layers import ProcessingLayerCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.operation_executor import OperationExecutor
from tests.database_reset import reset_database
from tests.surfaces.lineage_seed import seed_live_document_lineage

_ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("d1430000-0000-0000-0000-00000000a7a1")
_MODEL = "qwen/qwen3-embedding-8b"
_AT = datetime(2025, 1, 1, tzinfo=UTC)
_CLAIM_RELATIONS = re.compile(
    r"\b(claims|claims_live|claims_visible_history|claims_canonical"
    r"|claim_occurrences_live|chunk_claims)\b",
    re.IGNORECASE,
)


def _vector(*, axis: int) -> tuple[float, ...]:
    values = [0.0] * P1_VECTOR_DIMENSIONS
    values[axis] = 1.0
    return tuple(values)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Structural head on the disposable integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for D143 retrieval proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@dataclass(frozen=True, slots=True)
class _Rig:
    engine: Engine
    query: QueryEngine
    operations: OperationExecutor
    chunk_id: UUID
    claim_id: UUID

    def configure(self, *, claims: bool, facts: bool) -> None:
        """What ``setup`` writes for these settings."""
        ProcessingLayerCatalog(engine=self.engine).reconcile_settings(
            deployment_id=_DEPLOYMENT_ID, claims_enabled=claims, facts_enabled=facts
        )

    def unindex(self) -> None:
        """Drop every search row, so the next read finds no candidate at all."""
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM chunk_search"))
            connection.execute(
                text(
                    "UPDATE claims SET embedding = NULL, embedding_model = NULL,"
                    " embedding_input_policy_version = NULL, embedding_text_hash = NULL"
                )
            )

    def run(
        self,
        *,
        name: AssuredOperationName,
        arguments: dict[str, object],
        layers: frozenset[ProcessingLayer] | None = None,
    ) -> object:
        """Run one assured operation through the executor."""
        from rememberstack.spine import AssuredOperationRegistry
        from rememberstack.spine import seed_canonical_operations

        registry = AssuredOperationRegistry(engine=self.engine)
        seed_canonical_operations(registry=registry, deployment_id=_DEPLOYMENT_ID)
        operation = registry.by_name(deployment_id=_DEPLOYMENT_ID, name=name.value)
        assert operation is not None
        return self.operations.execute(
            deployment_id=_DEPLOYMENT_ID,
            operation=operation,
            arguments=arguments,
            layers=layers,
        )


@pytest.fixture()
def rig(database_engine: Engine) -> _Rig:
    """One live lineage with a chunk and a current claim, both published."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d143-retrieval",
            name="D143 retrieval",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    pool = BoundedPostgresReadPool(
        engine=database_engine, max_concurrency=4, pool_wait_seconds=5.0
    )
    index = PostgresP1Index(
        engine=database_engine, embedding_model=_MODEL, read_pool=pool
    )
    index.configure_channels(deployment_id=_DEPLOYMENT_ID)
    doc_id = uuid4()
    chunk_id = uuid4()
    claim_id = uuid4()
    body = "the harbour tariff is forty crowns"
    with database_engine.begin() as connection:
        lineage = seed_live_document_lineage(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            doc_id=doc_id,
            chunk_ids=(chunk_id,),
            label="tariff",
            title="tariff policy",
            source_ref="tariff",
        )
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, is_current_testimony, extractor_version,"
                " ingested_at, asserted_at) VALUES (:claim, :d, :doc, :chunk, :body,"
                " :body, 0, :end, true, true, true, 'd143-test', :at, :at)"
            ),
            {
                "claim": claim_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "chunk": chunk_id,
                "body": body,
                "end": len(body),
                "at": _AT,
            },
        )
        connection.execute(
            text(
                "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
                " evidence_spans, source_locators, created_at) VALUES (:d, :chunk,"
                " :claim, CAST(:spans AS jsonb), CAST('{}' AS jsonb), :at)"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "chunk": chunk_id,
                "claim": claim_id,
                "spans": f'[{{"char_start": 0, "char_end": {len(body)}}}]',
                "at": _AT,
            },
        )
    index.upsert_chunks(
        rows=(
            P1ChunkRow(
                chunk_id=chunk_id,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=doc_id,
                version_id=lineage.version_id,
                section_role="body",
                text=body,
                vector=_vector(axis=0),
                policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
                embedder_generation=_MODEL,
                embedding_text_hash=embedding_text_hash(body),
                source_kind="upload",
                source_shape="document",
            ),
        )
    )
    index.upsert_claims(
        rows=(
            P1ClaimRow(
                claim_id=claim_id,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=doc_id,
                chunk_id=chunk_id,
                text=body,
                is_current_testimony=True,
                is_attributed=False,
                vector=_vector(axis=0),
            ),
        )
    )
    provider = MagicMock()
    provider.embed.return_value = SimpleNamespace(
        vectors=(_vector(axis=0),), usage=None
    )
    query = QueryEngine(
        engine=database_engine,
        search_index=index,
        model_provider=provider,
        embedding_model=_MODEL,
        fact_read_pool=pool,
    )
    return _Rig(
        engine=database_engine,
        query=query,
        operations=OperationExecutor(query_engine=query),
        chunk_id=chunk_id,
        claim_id=claim_id,
    )


class _Statements:
    """Every SQL statement an engine sends while the capture is open."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.sql: list[str] = []

    def __enter__(self) -> _Statements:
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *_: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._record)

    def _record(
        self, _conn: object, _cursor: object, statement: str, *_: object
    ) -> None:
        self.sql.append(statement)

    def touches_claims(self) -> list[str]:
        """Statements that read a claim table or view."""
        return [sql for sql in self.sql if _CLAIM_RELATIONS.search(sql)]


def test_unconfigured_reads_every_layer_and_the_selection_defaults_to_it(
    rig: _Rig,
) -> None:
    """No configuration: claims and chunks both answer; the default equals the
    explicit full selection."""
    default = rig.query.claims_and_sources_context(
        deployment_id=_DEPLOYMENT_ID, query="harbour tariff"
    )
    explicit = rig.query.claims_and_sources_context(
        deployment_id=_DEPLOYMENT_ID,
        query="harbour tariff",
        layers=frozenset({ProcessingLayer.SOURCES, ProcessingLayer.CLAIMS}),
    )
    assert [item.claim_id for item in default.evidence] == [rig.claim_id]
    assert [item.chunk_id for item in default.chunks] == [rig.chunk_id]
    volatile = {"freshness", "temporal_scope"}
    assert default.model_dump(exclude=volatile) == explicit.model_dump(exclude=volatile)


def test_a_sources_only_selection_never_touches_a_claim_relation(rig: _Rig) -> None:
    """``layers=sources`` answers from chunks and sends no SQL to claim relations."""
    with _Statements(rig.engine) as statements:
        answer = rig.query.claims_and_sources_context(
            deployment_id=_DEPLOYMENT_ID,
            query="harbour tariff",
            layers=frozenset({ProcessingLayer.SOURCES}),
        )
    assert answer.evidence == ()
    assert [item.chunk_id for item in answer.chunks] == [rig.chunk_id]
    assert statements.sql
    assert statements.touches_claims() == []


def test_claims_off_refuses_claim_search_and_answers_from_chunks(rig: _Rig) -> None:
    """Claims disabled: search_claims is refused; the mixed read uses chunks
    only, touches no claim relation, and an empty answer is a boundary."""
    rig.configure(claims=False, facts=False)
    with pytest.raises(LayerUnavailableError) as refused:
        rig.query.search_claims(deployment_id=_DEPLOYMENT_ID, query="harbour tariff")
    assert refused.value.layer is ProcessingLayer.CLAIMS
    with _Statements(rig.engine) as statements:
        answer = rig.query.claims_and_sources_context(
            deployment_id=_DEPLOYMENT_ID, query="harbour tariff"
        )
    assert answer.evidence == ()
    assert [item.chunk_id for item in answer.chunks] == [rig.chunk_id]
    assert statements.touches_claims() == []
    rig.unindex()
    empty = rig.query.claims_and_sources_context(
        deployment_id=_DEPLOYMENT_ID, query="zebra quantum"
    )
    assert empty.negative is not None
    assert empty.negative.kind is NegativeKind.BOUNDARY
    assert "claims" in empty.negative.explanation
    with pytest.raises(LayerUnavailableError):
        rig.query.claims_and_sources_context(
            deployment_id=_DEPLOYMENT_ID,
            query="harbour tariff",
            layers=frozenset({ProcessingLayer.CLAIMS}),
        )


def test_facts_off_refuses_fact_reads_and_combined_builds_a_boundary_child(
    rig: _Rig,
) -> None:
    """Facts disabled: fact reads are refused; combined_context answers the
    evidence side and returns a synthetic fact-grain boundary child."""
    rig.configure(claims=True, facts=False)
    for read in (
        lambda: rig.query.resolve(deployment_id=_DEPLOYMENT_ID, name="Alice"),
        lambda: rig.query.lookup_relations(deployment_id=_DEPLOYMENT_ID),
        lambda: rig.query.facts_context(deployment_id=_DEPLOYMENT_ID, query="tariff"),
    ):
        with pytest.raises(LayerUnavailableError) as refused:
            read()
        assert refused.value.layer is ProcessingLayer.FACTS
    bundle = rig.run(
        name=AssuredOperationName.COMBINED_CONTEXT,
        arguments={"query": "harbour tariff"},
    )
    assert isinstance(bundle, ContextBundleV2)
    assert [item.claim_id for item in bundle.claims_and_sources.evidence] == [
        rig.claim_id
    ]
    assert bundle.facts.grain is Grain.FACT
    assert bundle.facts.facts == ()
    assert bundle.facts.negative is not None
    assert bundle.facts.negative.kind is NegativeKind.BOUNDARY
    assert "facts" in bundle.facts.negative.explanation


def test_mandatory_facts_dependency_and_empty_selections(rig: _Rig) -> None:
    """entity_ids needs facts: excluded is 422, disabled is 409. A selection
    that leaves a read no channel is 422; combined_context(layers=facts) builds
    the symmetric evidence-grain child."""
    entity = (uuid4(),)
    with pytest.raises(LayerSelectionError):
        rig.query.claims_and_sources_context(
            deployment_id=_DEPLOYMENT_ID,
            query="harbour tariff",
            entity_ids=entity,
            layers=frozenset({ProcessingLayer.SOURCES, ProcessingLayer.CLAIMS}),
        )
    with pytest.raises(LayerSelectionError):
        rig.query.claims_and_sources_context(
            deployment_id=_DEPLOYMENT_ID,
            query="harbour tariff",
            layers=frozenset({ProcessingLayer.FACTS}),
        )
    bundle = rig.run(
        name=AssuredOperationName.COMBINED_CONTEXT,
        arguments={"query": "harbour tariff"},
        layers=frozenset({ProcessingLayer.FACTS}),
    )
    assert isinstance(bundle, ContextBundleV2)
    assert bundle.claims_and_sources.grain is Grain.EVIDENCE
    assert bundle.claims_and_sources.evidence == ()
    assert bundle.claims_and_sources.chunks == ()
    assert bundle.claims_and_sources.negative is not None
    assert bundle.claims_and_sources.negative.kind is NegativeKind.BOUNDARY
    rig.configure(claims=True, facts=False)
    with pytest.raises(LayerUnavailableError):
        rig.query.claims_and_sources_context(
            deployment_id=_DEPLOYMENT_ID, query="harbour tariff", entity_ids=entity
        )


def test_backfilling_claims_turn_an_empty_answer_into_a_boundary(rig: _Rig) -> None:
    """A layer still being backfilled is consulted, but an empty answer from it
    is a boundary, never known_empty."""
    rig.configure(claims=False, facts=False)
    with rig.engine.begin() as connection:
        version_id = connection.execute(
            text("SELECT version_id FROM chunks WHERE chunk_id = :c"),
            {"c": rig.chunk_id},
        ).scalar_one()
        doc_id = connection.execute(
            text("SELECT doc_id FROM chunks WHERE chunk_id = :c"), {"c": rig.chunk_id}
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO processing_layer_gaps (deployment_id, version_id, doc_id,"
                " layer, lane) VALUES (:d, :v, :doc, 'claims', 'backfill')"
            ),
            {"d": _DEPLOYMENT_ID, "v": version_id, "doc": doc_id},
        )
    rig.configure(claims=True, facts=False)
    states = ProcessingLayerCatalog(engine=rig.engine).states(
        deployment_id=_DEPLOYMENT_ID
    )
    assert states.claims == "backfilling"
    rig.unindex()
    empty = rig.query.search_claims(deployment_id=_DEPLOYMENT_ID, query="zebra quantum")
    assert empty.negative is not None
    assert empty.negative.kind is NegativeKind.BOUNDARY
    assert "backfilled" in empty.negative.explanation
