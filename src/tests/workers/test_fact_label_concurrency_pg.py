"""Real PostgreSQL proofs for concurrent, checkpointed fact projection."""

from collections.abc import Callable
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from threading import Lock
import time
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.model import ClaimedWork
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.profile_refresher import ProfileRefreshContendedError
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import FactCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import LabelFactsHandler
from rememberstack.workers import P1Settings
from rememberstack.workers.p1 import label_relation_component_version
from tests.database_reset import reset_database
from tests.surfaces.lineage_seed import LiveDocumentLineage
from tests.surfaces.lineage_seed import seed_live_document_lineage

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT = UUID("90000000-0000-0000-0000-000000000321")
_MODEL = "test/fact-projection"


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head in the explicitly configured integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for concurrency proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@dataclass(frozen=True)
class _Seed:
    """Two document scopes and three facts sharing one subject entity."""

    documents: tuple[LiveDocumentLineage, LiveDocumentLineage]
    relations: tuple[UUID, UUID]
    observation: UUID


@pytest.fixture
def seeded(database_engine: Engine) -> _Seed:
    """Create live evidence: doc zero supports all facts, doc one the first."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT,
            slug="fact-label-concurrency",
            name="Fact label concurrency",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    relations = (UUID(int=1001), UUID(int=1002))
    observation = UUID(int=1003)
    entities = (uuid4(), uuid4(), uuid4())
    with database_engine.begin() as connection:
        for entity_id, name in zip(entities, ("Aster", "Beacon", "Cedar"), strict=True):
            connection.execute(
                text("""INSERT INTO entities
                    (entity_id,deployment_id,canonical_name,normalized_name)
                    VALUES (:id,:dep,:name,lower(:name))"""),
                {"id": entity_id, "dep": _DEPLOYMENT, "name": name},
            )
        documents = tuple(
            seed_live_document_lineage(
                connection=connection, deployment_id=_DEPLOYMENT, label=f"scope-{n}"
            )
            for n in range(2)
        )
        claims = (uuid4(), uuid4())
        for doc, claim_id in zip(documents, claims, strict=True):
            connection.execute(
                text("""INSERT INTO claims
                    (claim_id,deployment_id,doc_id,chunk_id,claim_text,source_span,
                     char_start,char_end,anchor_ok,window_membership_ok,
                     is_current_testimony,extractor_version)
                    VALUES (:id,:dep,:doc,:chunk,'Aster works with others',
                     'Aster works with others',0,23,true,true,true,'concurrency-test')"""),
                {
                    "id": claim_id,
                    "dep": _DEPLOYMENT,
                    "doc": doc.doc_id,
                    "chunk": doc.chunk_id,
                },
            )
            connection.execute(
                text("""INSERT INTO chunk_claims
                    (deployment_id,chunk_id,claim_id,evidence_spans)
                    VALUES (:dep,:chunk,:claim,jsonb_build_array(
                     jsonb_build_object('char_start',0,'char_end',23)))"""),
                {"dep": _DEPLOYMENT, "chunk": doc.chunk_id, "claim": claim_id},
            )
        for fact_id, object_id in zip(relations, entities[1:], strict=True):
            connection.execute(
                text("""INSERT INTO relations
                    (relation_id,deployment_id,subject_entity_id,predicate,
                     object_entity_id,normalizer_version)
                    VALUES (:id,:dep,:subject,'works_for',:object,'concurrency-test')"""),
                {
                    "id": fact_id,
                    "dep": _DEPLOYMENT,
                    "subject": entities[0],
                    "object": object_id,
                },
            )
            for doc, claim_id in zip(documents, claims, strict=True):
                if fact_id == relations[1] and doc == documents[1]:
                    continue
                connection.execute(
                    text("""INSERT INTO relation_evidence
                        (deployment_id,relation_id,claim_id,doc_id,stance,normalizer_version)
                        VALUES (:dep,:id,:claim,:doc,'supports','concurrency-test')"""),
                    {
                        "dep": _DEPLOYMENT,
                        "id": fact_id,
                        "claim": claim_id,
                        "doc": doc.doc_id,
                    },
                )
        connection.execute(
            text("""INSERT INTO observations
                (observation_id,deployment_id,subject_entity_id,statement,normalizer_version)
                VALUES (:id,:dep,:subject,'Aster keeps a checklist.','concurrency-test')"""),
            {"id": observation, "dep": _DEPLOYMENT, "subject": entities[0]},
        )
        connection.execute(
            text("""INSERT INTO observation_evidence
                (deployment_id,observation_id,claim_id,doc_id,stance,normalizer_version)
                VALUES (:dep,:id,:claim,:doc,'supports','concurrency-test')"""),
            {
                "dep": _DEPLOYMENT,
                "id": observation,
                "claim": claims[0],
                "doc": documents[0].doc_id,
            },
        )
    PostgresP1Index(engine=database_engine, embedding_model=_MODEL).configure_channels(
        deployment_id=_DEPLOYMENT
    )
    return _Seed(
        documents=(documents[0], documents[1]),
        relations=relations,
        observation=observation,
    )


class _Provider(FakeModelProvider):
    """Record requests safely; the callback may block, fail, or mutate authority."""

    def __init__(
        self, *, callback: Callable[[EmbeddingRequest], None] | None = None
    ) -> None:
        """Keep blocking coordination outside the provider's bookkeeping lock."""
        super().__init__()
        self.requests: list[EmbeddingRequest] = []
        self._callback = callback
        self._mutex = Lock()

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Count attempted paid calls, including a callback-injected failure."""
        with self._mutex:
            self.requests.append(request)
        if self._callback is not None:
            self._callback(request)
        with self._mutex:
            return super().embed(request=request)


def _work(*, kind: ProcessingTarget, target_id: UUID) -> ClaimedWork:
    """Build one independently claimed projection scope."""
    field = {
        ProcessingTarget.DOCUMENT: "doc_id",
        ProcessingTarget.RELATION: "relation_id",
        ProcessingTarget.OBSERVATION: "observation_id",
    }[kind]
    return ClaimedWork(
        processing_id=uuid4(),
        deployment_id=_DEPLOYMENT,
        target_kind=kind,
        target_id=target_id,
        stage=PipelineStage.LABEL_RELATION,
        component_version=label_relation_component_version(embedding_model=_MODEL),
        content_hash="fact-concurrency-test",
        lane=None,
        attempt=1,
        payload={field: str(target_id)},
    )


def _handler(
    *,
    engine: Engine,
    provider: _Provider,
    catalog: FactCatalog | None = None,
    refresher: RecordingProfileRefresher | None = None,
    batch_size: int = 64,
) -> LabelFactsHandler:
    """Compose the production catalog, handler and PostgreSQL vector writer."""
    return LabelFactsHandler(
        facts=catalog or FactCatalog(engine=engine),
        model_provider=provider,
        fact_index=PostgresP1Index(engine=engine, embedding_model=_MODEL),
        settings=P1Settings(embedding_model=_MODEL, embed_batch_size=batch_size),
        profile_refresher=refresher or RecordingProfileRefresher(),
    )


def _embedded_count(*, engine: Engine) -> int:
    """Count durable natural-row embeddings in the owned test project."""
    with engine.connect() as connection:
        return connection.execute(
            text("""SELECT
            (SELECT count(*) FROM relations WHERE embedding IS NOT NULL) +
            (SELECT count(*) FROM observations WHERE embedding IS NOT NULL)""")
        ).scalar_one()


def test_unrelated_facts_enter_provider_together_without_open_transactions(
    database_engine: Engine, seeded: _Seed
) -> None:
    """Two relations and an observation sharing a subject must run concurrently."""
    barrier = Barrier(parties=3, timeout=10)

    def together(request: EmbeddingRequest) -> None:
        """Wait until all fact kinds reach external I/O, then inspect SQL sessions."""
        del request
        barrier.wait()
        with database_engine.connect() as connection:
            owners = (
                connection.execute(
                    text("""SELECT a.xact_start
                FROM pg_stat_activity a WHERE a.pid IN (
                  SELECT pid FROM pg_locks WHERE locktype='advisory' AND granted)
                  AND a.datname=current_database()""")
                )
                .scalars()
                .all()
            )
        assert len(owners) == 3
        assert all(start is None for start in owners)
        barrier.wait()

    provider = _Provider(callback=together)
    handler = _handler(engine=database_engine, provider=provider)
    jobs = [
        _work(kind=ProcessingTarget.RELATION, target_id=item)
        for item in seeded.relations
    ] + [_work(kind=ProcessingTarget.OBSERVATION, target_id=seeded.observation)]
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(handler.handle, work=job, meter=NoopCostMeter())
            for job in jobs
        ]
        for future in futures:
            future.result(timeout=20)
    assert len(provider.requests) == 3
    assert _embedded_count(engine=database_engine) == 3


def test_overlapping_document_and_fact_jobs_pay_once(
    database_engine: Engine, seeded: _Seed
) -> None:
    """Both jobs discover missing state; the waiter rechecks the persisted result."""
    barrier = Barrier(parties=2, timeout=10)

    class DiscoverTogether(FactCatalog):
        """Synchronize after discovery, before the production lock implementation."""

        @contextmanager
        def label_lock(
            self,
            *,
            deployment_id: UUID,
            relation_ids: tuple[UUID, ...],
            observation_ids: tuple[UUID, ...],
        ) -> Iterator[None]:
            """Force the race without replacing real PostgreSQL reservations."""
            barrier.wait()
            with super().label_lock(
                deployment_id=deployment_id,
                relation_ids=relation_ids,
                observation_ids=observation_ids,
            ):
                yield

    provider = _Provider()
    refresher = RecordingProfileRefresher()
    handler = _handler(
        engine=database_engine,
        provider=provider,
        catalog=DiscoverTogether(engine=database_engine),
        refresher=refresher,
    )
    jobs = (
        _work(kind=ProcessingTarget.DOCUMENT, target_id=seeded.documents[1].doc_id),
        _work(kind=ProcessingTarget.RELATION, target_id=seeded.relations[0]),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(handler.handle, work=job, meter=NoopCostMeter())
            for job in jobs
        ]
        for future in futures:
            future.result(timeout=20)
    assert len(provider.requests) == 1
    assert _embedded_count(engine=database_engine) == 1
    assert refresher.fact_refreshes == [((seeded.relations[0],), ())] * 2


def test_cached_shared_fact_does_not_reserve_unrelated_pending_work(
    database_engine: Engine, seeded: _Seed
) -> None:
    """A lock on a completed shared fact cannot serialize a document's new facts."""
    provider = _Provider()
    handler = _handler(engine=database_engine, provider=provider)
    handler.handle(
        work=_work(kind=ProcessingTarget.RELATION, target_id=seeded.relations[0]),
        meter=NoopCostMeter(),
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        with FactCatalog(engine=database_engine).label_lock(
            deployment_id=_DEPLOYMENT,
            relation_ids=(seeded.relations[0],),
            observation_ids=(),
        ):
            future = executor.submit(
                handler.handle,
                work=_work(
                    kind=ProcessingTarget.DOCUMENT, target_id=seeded.documents[0].doc_id
                ),
                meter=NoopCostMeter(),
            )
            future.result(timeout=5)
    assert len(provider.requests) == 2
    assert len(provider.requests[1].texts) == 2
    assert _embedded_count(engine=database_engine) == 3


def test_failed_batch_releases_reservations_and_reuses_completed_vectors(
    database_engine: Engine, seeded: _Seed
) -> None:
    """Retry from another session embeds only the failed and remaining batches."""
    attempted: list[str] = []

    def fail_second_once(request: EmbeddingRequest) -> None:
        """Simulate a provider outage after one durable single-fact batch."""
        attempted.extend(request.texts)
        if len(attempted) == 2:
            raise RuntimeError("provider interrupted")

    provider = _Provider(callback=fail_second_once)
    job = _work(kind=ProcessingTarget.DOCUMENT, target_id=seeded.documents[0].doc_id)
    with pytest.raises(RuntimeError, match="provider interrupted"):
        _handler(engine=database_engine, provider=provider, batch_size=1).handle(
            work=job, meter=NoopCostMeter()
        )
    assert _embedded_count(engine=database_engine) == 1
    retry_engine = create_engine(database_engine.url)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _handler(engine=retry_engine, provider=provider, batch_size=1).handle,
                work=job,
                meter=NoopCostMeter(),
            )
            try:
                future.result(timeout=5)
            finally:
                # Close idle original sessions if a regression leaked a lock.
                database_engine.dispose()
        assert len(attempted) == 4
        assert attempted[0] not in attempted[1:]
        assert attempted[1] == attempted[2]
        assert _embedded_count(engine=retry_engine) == 3
    finally:
        retry_engine.dispose()


def test_profile_only_retry_does_not_repeat_paid_fact_work(
    database_engine: Engine, seeded: _Seed
) -> None:
    """The original work scope is refreshed even when every vector is a cache hit."""

    class ContendedOnce(RecordingProfileRefresher):
        """Model the bounded profile retry observed in the live ingestion run."""

        def refresh_for_facts(
            self,
            *,
            deployment_id: UUID,
            relation_ids: tuple[UUID, ...],
            observation_ids: tuple[UUID, ...],
            meter: CostMeterPort | None = None,
            call_key: str = "refresh_profile",
        ) -> None:
            """Record both repair attempts but interrupt only the first one."""
            super().refresh_for_facts(
                deployment_id=deployment_id,
                relation_ids=relation_ids,
                observation_ids=observation_ids,
                meter=meter,
                call_key=call_key,
            )
            if len(self.fact_refreshes) == 1:
                raise ProfileRefreshContendedError("profile inputs changed")

    provider = _Provider()
    refresher = ContendedOnce()
    handler = _handler(engine=database_engine, provider=provider, refresher=refresher)
    job = _work(kind=ProcessingTarget.DOCUMENT, target_id=seeded.documents[0].doc_id)
    with pytest.raises(ProfileRefreshContendedError):
        handler.handle(work=job, meter=NoopCostMeter())
    handler.handle(work=job, meter=NoopCostMeter())
    assert len(provider.requests) == 1
    assert len(refresher.fact_refreshes) == 2
    assert set(refresher.fact_refreshes[1][0]) == set(seeded.relations)
    assert refresher.fact_refreshes[1][1] == (seeded.observation,)


def _wait_for_advisory_wait(*, engine: Engine, application_name: str) -> None:
    """Observe an actual server-side waiter rather than relying on a sleep."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.execute(
                text("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                    WHERE application_name=:name AND wait_event='advisory')"""),
                {"name": application_name},
            ).scalar_one()
        if waiting:
            return
        time.sleep(0.01)
    raise AssertionError("worker never waited on the held fact reservation")


def _reserve(*, engine: Engine, relation_ids: tuple[UUID, ...]) -> None:
    """Acquire and release a real reservation from another PostgreSQL session."""
    with FactCatalog(engine=engine).label_lock(
        deployment_id=_DEPLOYMENT, relation_ids=relation_ids, observation_ids=()
    ):
        pass


def test_opposite_order_waits_without_reserving_the_later_fact(
    database_engine: Engine, seeded: _Seed
) -> None:
    """Caller order cannot cause B-then-A acquisition and a circular wait."""
    name = f"fact-order-{uuid4()}"
    contender = create_engine(
        database_engine.url, connect_args={"application_name": name}
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            with FactCatalog(engine=database_engine).label_lock(
                deployment_id=_DEPLOYMENT,
                relation_ids=(seeded.relations[0],),
                observation_ids=(),
            ):
                waiting = executor.submit(
                    _reserve,
                    engine=contender,
                    relation_ids=tuple(reversed(seeded.relations)),
                )
                _wait_for_advisory_wait(engine=database_engine, application_name=name)
                probe = executor.submit(
                    _reserve,
                    engine=database_engine,
                    relation_ids=(seeded.relations[1],),
                )
                probe.result(timeout=3)
            waiting.result(timeout=5)
    finally:
        contender.dispose()


def test_partial_acquisition_timeout_releases_earlier_reservations(
    database_engine: Engine, seeded: _Seed
) -> None:
    """A pooled session must not keep A after failing to acquire held B."""
    contender = create_engine(
        database_engine.url,
        pool_size=1,
        max_overflow=0,
        connect_args={"options": "-c statement_timeout=250"},
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            with FactCatalog(engine=database_engine).label_lock(
                deployment_id=_DEPLOYMENT,
                relation_ids=(seeded.relations[1],),
                observation_ids=(),
            ):
                waiting = executor.submit(
                    _reserve, engine=contender, relation_ids=seeded.relations
                )
                with pytest.raises(DBAPIError, match="statement timeout"):
                    waiting.result(timeout=3)
                probe = executor.submit(
                    _reserve,
                    engine=database_engine,
                    relation_ids=(seeded.relations[0],),
                )
                try:
                    probe.result(timeout=3)
                finally:
                    contender.dispose()
    finally:
        contender.dispose()


@pytest.mark.parametrize("mutation", ("label", "window", "delete"))
def test_fact_mutation_during_embedding_rejects_stale_projection(
    database_engine: Engine, seeded: _Seed, mutation: str
) -> None:
    """Provider-spanning reservations never block authority or recreate a fact."""
    relation_id = seeded.relations[0]

    def mutate(request: EmbeddingRequest) -> None:
        """Commit a competing authority mutation while the vector is in flight."""
        del request
        statements = {
            "label": "UPDATE relations SET fact_label='New accepted label' WHERE relation_id=:id",
            "window": """UPDATE relations SET valid_from='2026-11-11T00:00:00Z',
                valid_precision='open',window_claim_ids=ARRAY(
                  SELECT claim_id FROM relation_evidence WHERE relation_id=:id)
                WHERE relation_id=:id""",
            "delete": "DELETE FROM relations WHERE relation_id=:id",
        }
        with database_engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout='2s'"))
            connection.execute(text(statements[mutation]), {"id": relation_id})

    provider = _Provider(callback=mutate)
    _handler(engine=database_engine, provider=provider).handle(
        work=_work(kind=ProcessingTarget.RELATION, target_id=relation_id),
        meter=NoopCostMeter(),
    )
    assert len(provider.requests) == 1
    assert _embedded_count(engine=database_engine) == 0
    with database_engine.connect() as connection:
        remaining = connection.execute(
            text("SELECT count(*) FROM relations WHERE relation_id=:id"),
            {"id": relation_id},
        ).scalar_one()
    assert remaining == (0 if mutation == "delete" else 1)
