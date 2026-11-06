"""Real-PostgreSQL proofs for stage-class workers (D146).

One worker process serves every stage of its class with several tasks in
flight. Downstream stages drain first, steady lanes before backfill lanes, and
two deployments in two databases run side by side in one process without
touching each other's rows.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import threading
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
from sqlalchemy.engine import make_url

from rememberstack.adapters.selfhost import SelfHostWorkerLoop
from rememberstack.adapters.selfhost import TokenBucket
from rememberstack.model import ClaimedWork
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EnqueueWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import RunResultOutcome
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import HandlerOutcome
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import Worker
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_A = UUID("60000000-0000-0000-0000-00000000000a")
_DEPLOYMENT_B = UUID("60000000-0000-0000-0000-00000000000b")
_VERSION = "worker-class-test-2026-11"
_UPSTREAM = PipelineStage.EXTRACT_CLAIMS
_DOWNSTREAM = PipelineStage.EMBED_CLAIM
_STAGES = (_DOWNSTREAM, _UPSTREAM)  # downstream first, as the profile lists them


def _migrated_engine(url: str, *, deployment_id: UUID, slug: str) -> Engine:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(url, pool_size=20, max_overflow=20)
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=deployment_id,
            slug=slug,
            name=f"Worker class proofs {slug}",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return engine


@pytest.fixture(scope="module")
def database_url() -> str:
    """The accepted integration database URL, or a module-wide skip."""
    try:
        return load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for worker class proofs")


@pytest.fixture(scope="module")
def second_database_url(database_url: str) -> str:
    """A second database on the same server: a second deployment's own store."""
    url = make_url(database_url)
    name = f"{url.database}_second"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            ).first()
            if exists is None:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()
    return url.set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def migrated_engine(database_url: str) -> Iterator[Engine]:
    """A migrated store holding deployment A, shared by the module."""
    engine = _migrated_engine(database_url, deployment_id=_DEPLOYMENT_A, slug="a")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def engine(migrated_engine: Engine) -> Engine:
    """Deployment A's store with an empty work ledger."""
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE processing_state CASCADE"))
    return migrated_engine


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _ledger(engine: Engine) -> WorkLedger:
    return WorkLedger(
        engine=engine,
        settings=WorkLedgerSettings(retry_backoff_base_s=0.0, retry_backoff_max_s=0.0),
    )


def _enqueue(
    ledger: WorkLedger,
    *,
    deployment_id: UUID,
    stage: PipelineStage,
    lane: ProcessingLane = ProcessingLane.STEADY,
) -> UUID:
    return ledger.enqueue(
        work=EnqueueWork(
            deployment_id=deployment_id,
            target_kind=ProcessingTarget.CHUNK,
            target_id=uuid4(),
            stage=stage,
            component_version=_VERSION,
            content_hash="sha256:test",
            lane=lane,
        )
    ).processing_id


@dataclass(frozen=True)
class _Run:
    deployment_id: UUID
    stage: PipelineStage
    lane: ProcessingLane | None
    started: float
    finished: float


class _Recording:
    """A handler shared by every stage and slot: records runs and peak overlap."""

    def __init__(self, *, seconds: float = 0.0) -> None:
        self._seconds = seconds
        self._lock = threading.Lock()
        self._in_flight = 0
        self.peak = 0
        self.runs: list[_Run] = []

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        del meter
        started = time.monotonic()
        with self._lock:
            self._in_flight += 1
            self.peak = max(self.peak, self._in_flight)
        time.sleep(self._seconds)
        with self._lock:
            self._in_flight -= 1
            self.runs.append(
                _Run(
                    deployment_id=work.deployment_id,
                    stage=work.stage,
                    lane=work.lane,
                    started=started,
                    finished=time.monotonic(),
                )
            )
        return HandlerOutcome()


def _loop(
    *,
    engine: Engine,
    database_url: str,
    deployment_id: UUID,
    handler: _Recording,
    concurrency: int,
) -> SelfHostWorkerLoop:
    ledger = _ledger(engine)
    registry = HandlerRegistry()
    for stage in _STAGES:
        registry.register(stage=stage, handler=handler)
    return SelfHostWorkerLoop(
        worker=Worker(ledger=ledger, registry=registry),
        ledger=ledger,
        deployment_id=deployment_id,
        stages=_STAGES,
        bucket=TokenBucket(rate_per_s=1_000.0, capacity=1_000.0),
        database_url=_psycopg_url(database_url),
        fallback_poll_s=0.2,
        concurrency=concurrency,
    )


def test_due_routes_lists_only_routes_with_claimable_work_in_order(
    engine: Engine,
) -> None:
    """The one-query probe answers in the caller's order and skips idle routes."""
    ledger = _ledger(engine)
    _enqueue(ledger, deployment_id=_DEPLOYMENT_A, stage=_UPSTREAM)
    _enqueue(
        ledger,
        deployment_id=_DEPLOYMENT_A,
        stage=_DOWNSTREAM,
        lane=ProcessingLane.BACKFILL,
    )
    routes = (
        (_DOWNSTREAM, ProcessingLane.STEADY),
        (_UPSTREAM, ProcessingLane.STEADY),
        (_DOWNSTREAM, ProcessingLane.BACKFILL),
        (_UPSTREAM, ProcessingLane.BACKFILL),
    )
    assert ledger.due_routes(deployment_id=_DEPLOYMENT_A, routes=routes) == (
        (_UPSTREAM, ProcessingLane.STEADY),
        (_DOWNSTREAM, ProcessingLane.BACKFILL),
    )


def test_one_slot_drains_downstream_stages_first_and_steady_before_backfill(
    database_url: str, engine: Engine
) -> None:
    """Fair order: steady downstream, steady upstream, then the backfill lanes."""
    ledger = _ledger(engine)
    for stage, lane in (
        (_UPSTREAM, ProcessingLane.BACKFILL),
        (_UPSTREAM, ProcessingLane.STEADY),
        (_DOWNSTREAM, ProcessingLane.BACKFILL),
        (_UPSTREAM, ProcessingLane.STEADY),
        (_DOWNSTREAM, ProcessingLane.STEADY),
    ):
        _enqueue(ledger, deployment_id=_DEPLOYMENT_A, stage=stage, lane=lane)
    handler = _Recording()
    loop = _loop(
        engine=engine,
        database_url=database_url,
        deployment_id=_DEPLOYMENT_A,
        handler=handler,
        concurrency=1,
    )

    results = loop.drain_due()

    assert [result.outcome for result in results] == [RunResultOutcome.SUCCEEDED] * 5
    assert [(run.stage, run.lane) for run in handler.runs] == [
        (_DOWNSTREAM, ProcessingLane.STEADY),
        (_UPSTREAM, ProcessingLane.STEADY),
        (_UPSTREAM, ProcessingLane.STEADY),
        (_DOWNSTREAM, ProcessingLane.BACKFILL),
        (_UPSTREAM, ProcessingLane.BACKFILL),
    ]


def test_slots_run_tasks_of_several_stages_at_once(
    database_url: str, engine: Engine
) -> None:
    """With four slots, four tasks overlap and both stages are in flight together.

    Three downstream tasks fill three slots first; the fourth slot takes an
    upstream task alongside them.
    """
    ledger = _ledger(engine)
    for stage in (_UPSTREAM, _DOWNSTREAM) * 3:
        _enqueue(ledger, deployment_id=_DEPLOYMENT_A, stage=stage)
    handler = _Recording(seconds=0.6)
    loop = _loop(
        engine=engine,
        database_url=database_url,
        deployment_id=_DEPLOYMENT_A,
        handler=handler,
        concurrency=4,
    )

    results = loop.run_for(duration_s=2.0)

    assert len(results) == 6
    assert {result.outcome for result in results} == {RunResultOutcome.SUCCEEDED}
    assert handler.peak == 4
    assert _overlap(handler.runs, _UPSTREAM, _DOWNSTREAM)


class _HeldLedger(WorkLedger):
    """Lists due work but never hands it out, like a held review route."""

    def __init__(self, *, engine: Engine) -> None:
        super().__init__(
            engine=engine,
            settings=WorkLedgerSettings(
                retry_backoff_base_s=0.0, retry_backoff_max_s=0.0
            ),
        )
        self.probes = 0

    def due_routes(
        self, **kwargs: object
    ) -> tuple[tuple[PipelineStage, ProcessingLane], ...]:
        self.probes += 1
        return super().due_routes(**kwargs)  # type: ignore[arg-type]

    def claim_one(self, **_: object) -> None:  # type: ignore[override]
        return None


def test_work_that_is_listed_but_cannot_be_claimed_does_not_spin_the_slots(
    database_url: str, engine: Engine
) -> None:
    """Only real claims wake further slots; otherwise slots wait for a wake-up."""
    _enqueue(_ledger(engine), deployment_id=_DEPLOYMENT_A, stage=_UPSTREAM)
    ledger = _HeldLedger(engine=engine)
    registry = HandlerRegistry()
    for stage in _STAGES:
        registry.register(stage=stage, handler=_Recording())
    loop = SelfHostWorkerLoop(
        worker=Worker(ledger=ledger, registry=registry),
        ledger=ledger,
        deployment_id=_DEPLOYMENT_A,
        stages=_STAGES,
        bucket=TokenBucket(rate_per_s=1_000.0, capacity=1_000.0),
        database_url=_psycopg_url(database_url),
        fallback_poll_s=0.25,
        concurrency=4,
    )

    assert loop.run_for(duration_s=1.0) == ()
    # One probe per wake-up (start plus about four polls), not a busy loop.
    assert ledger.probes <= 8


def test_idle_wake_ups_spend_no_claim_tokens(database_url: str, engine: Engine) -> None:
    """Probing for work is free; only a claim takes a token from the bucket."""
    handler = _Recording()
    ledger = _ledger(engine)
    registry = HandlerRegistry()
    for stage in _STAGES:
        registry.register(stage=stage, handler=handler)
    loop = SelfHostWorkerLoop(
        worker=Worker(ledger=ledger, registry=registry),
        ledger=ledger,
        deployment_id=_DEPLOYMENT_A,
        stages=_STAGES,
        bucket=TokenBucket(rate_per_s=0.001, capacity=1.0),  # one claim in total
        database_url=_psycopg_url(database_url),
        fallback_poll_s=0.1,
        concurrency=2,
    )
    outcomes: list[RunResultOutcome] = []
    runner = threading.Thread(
        target=lambda: outcomes.extend(
            result.outcome for result in loop.run_for(duration_s=1.5)
        )
    )
    runner.start()
    time.sleep(0.8)  # several idle wake-ups pass first
    _enqueue(ledger, deployment_id=_DEPLOYMENT_A, stage=_UPSTREAM)
    runner.join()

    assert outcomes == [RunResultOutcome.SUCCEEDED]


def test_two_deployments_in_two_databases_interleave_in_one_process(
    database_url: str, second_database_url: str, engine: Engine
) -> None:
    """Two deployments' loops run side by side; each drains only its own store."""
    second = _migrated_engine(
        second_database_url, deployment_id=_DEPLOYMENT_B, slug="b"
    )
    try:
        stores = (
            (engine, database_url, _DEPLOYMENT_A),
            (second, second_database_url, _DEPLOYMENT_B),
        )
        for store, _, deployment_id in stores:
            for stage in (_UPSTREAM, _DOWNSTREAM) * 3:
                _enqueue(_ledger(store), deployment_id=deployment_id, stage=stage)
        handler = _Recording(seconds=0.4)
        loops = [
            _loop(
                engine=store,
                database_url=url,
                deployment_id=deployment_id,
                handler=handler,
                concurrency=3,
            )
            for store, url, deployment_id in stores
        ]
        outcomes: dict[UUID, tuple[RunResultOutcome, ...]] = {}

        def run(loop: SelfHostWorkerLoop, deployment_id: UUID) -> None:
            outcomes[deployment_id] = tuple(
                result.outcome for result in loop.run_for(duration_s=2.0)
            )

        threads = [
            threading.Thread(target=run, args=(loop, deployment_id))
            for loop, (_, _, deployment_id) in zip(loops, stores, strict=True)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert outcomes == {
            _DEPLOYMENT_A: (RunResultOutcome.SUCCEEDED,) * 6,
            _DEPLOYMENT_B: (RunResultOutcome.SUCCEEDED,) * 6,
        }
        assert handler.peak > 3  # more than one loop's slots were busy at once
        runs_a = [run for run in handler.runs if run.deployment_id == _DEPLOYMENT_A]
        runs_b = [run for run in handler.runs if run.deployment_id == _DEPLOYMENT_B]
        assert any(
            a.started < b.finished and b.started < a.finished
            for a in runs_a
            for b in runs_b
        )
        for store, _, deployment_id in stores:
            with store.connect() as connection:
                rows = connection.execute(
                    text("SELECT deployment_id, status::text FROM processing_state")
                ).all()
            assert {tuple(row) for row in rows} == {(deployment_id, "succeeded")}
    finally:
        second.dispose()


def _overlap(runs: list[_Run], first: PipelineStage, second: PipelineStage) -> bool:
    return any(
        a.started < b.finished and b.started < a.finished
        for a in runs
        if a.stage is first
        for b in runs
        if b.stage is second
    )
