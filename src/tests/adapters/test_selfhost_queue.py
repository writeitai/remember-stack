"""Real-PostgreSQL proofs for the self-host delivery shell (WP-0.4a, zero GCP)."""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import SelfHostTaskQueue
from rememberstack.adapters.selfhost import SelfHostWorkerLoop
from rememberstack.adapters.selfhost import TokenBucket
from rememberstack.model import ClaimedWork
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EnqueueWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import QueueRoute
from rememberstack.model import RunResultOutcome
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.queue import TaskQueuePort
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import HandlerOutcome
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import Worker
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("40000000-0000-0000-0000-000000000001")
_VERSION = "selfhost-test-2026-07"


@pytest.fixture(scope="module")
def database_url() -> str:
    """The accepted integration database URL, or a module-wide skip."""
    try:
        return load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL shell proofs"
        )


@pytest.fixture(scope="module")
def database_engine(database_url: str) -> Iterator[Engine]:
    """Apply structural head and expose the integration engine."""
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def bootstrapped_deployment(database_engine: Engine) -> None:
    """Give every proof a fresh deployment."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="selfhost-test",
            name="Self-host shell proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


@pytest.fixture()
def ledger(database_engine: Engine) -> WorkLedger:
    """A ledger with zero backoff so retries are immediately claimable."""
    return WorkLedger(
        engine=database_engine,
        settings=WorkLedgerSettings(retry_backoff_base_s=0.0, retry_backoff_max_s=0.0),
    )


def _psycopg_url(database_url: str) -> str:
    """The plain psycopg DSN for the SQLAlchemy integration URL."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _work(*, target_id: UUID | None = None) -> EnqueueWork:
    """One plane-E unit of work with a stable default shape."""
    return EnqueueWork(
        deployment_id=_DEPLOYMENT_ID,
        target_kind=ProcessingTarget.CHUNK,
        target_id=target_id or uuid4(),
        stage=PipelineStage.EXTRACT_CLAIMS,
        component_version=_VERSION,
        content_hash="sha256:test",
        lane=ProcessingLane.STEADY,
    )


def _drain_notifications(connection: psycopg.Connection) -> list[str]:
    """Collect every pending queue_wake payload without blocking."""
    return [note.payload for note in connection.notifies(timeout=0.5, stop_after=1)]


def test_wake_is_transactional_commit_delivers_rollback_does_not(
    database_url: str, database_engine: Engine, ledger: WorkLedger
) -> None:
    """The crash test: NOTIFY fires only when the enqueue transaction commits."""
    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as listener:
        listener.execute("LISTEN queue_wake")

        # A rolled-back insert (crash before commit) delivers no wake.
        with pytest.raises(RuntimeError, match="deliberate crash"):
            with database_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO processing_state (processing_id, deployment_id,"
                        " target_kind, target_id, stage, component_version,"
                        " content_hash, lane) VALUES (:pid, :dep, 'chunk', :tid,"
                        " 'extract_claims', :ver, 'sha256:test', 'steady')"
                    ),
                    {
                        "pid": uuid4(),
                        "dep": _DEPLOYMENT_ID,
                        "tid": uuid4(),
                        "ver": _VERSION,
                    },
                )
                raise RuntimeError("deliberate crash before commit")
        assert _drain_notifications(listener) == []

        # A committed enqueue delivers exactly its row's wake.
        outcome = ledger.enqueue(work=_work())
        assert _drain_notifications(listener) == [str(outcome.processing_id)]


def test_announce_reannounces_an_existing_row(
    database_url: str, ledger: WorkLedger
) -> None:
    """The port adapter wakes listeners for an already-committed row."""
    enqueued = ledger.enqueue(work=_work())
    adapter: TaskQueuePort = SelfHostTaskQueue(ledger=ledger)
    assert isinstance(adapter, TaskQueuePort)

    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as listener:
        listener.execute("LISTEN queue_wake")
        claimed = ledger.claim_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.EXTRACT_CLAIMS,
            lane=ProcessingLane.STEADY,
        )
        assert isinstance(claimed, ClaimedWork)
        ledger.fail(
            processing_id=claimed.processing_id,
            error="Traceback: transient",
            retryable=True,
        )
        adapter.announce(
            processing_id=enqueued.processing_id,
            route_snapshot=_route_snapshot(),
            not_before_snapshot=_now_utc(),
        )
        assert _drain_notifications(listener) == [str(enqueued.processing_id)]


class _NoOpHandler:
    """Succeed without work — the demo chain's terminal stage handler."""

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Do nothing and chain nothing."""
        del work, meter
        return HandlerOutcome()


def test_demo_chain_runs_on_selfhost_shell_with_zero_gcp_deps(
    database_url: str, ledger: WorkLedger
) -> None:
    """WP-0.4a acceptance: the worker loop drains real enqueued work, no GCP anywhere."""
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.EXTRACT_CLAIMS, handler=_NoOpHandler())
    loop = SelfHostWorkerLoop(
        worker=Worker(ledger=ledger, registry=registry),
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
        bucket=TokenBucket(rate_per_s=100.0, capacity=10.0),
        database_url=_psycopg_url(database_url),
        fallback_poll_s=0.2,
    )
    first = ledger.enqueue(work=_work())
    results = loop.run_for(duration_s=0.5)
    outcomes = {result.processing_id: result.outcome for result in results}
    assert outcomes[first.processing_id] is RunResultOutcome.SUCCEEDED


def test_token_bucket_limits_claims_and_refills() -> None:
    """The bucket denies when empty and refills against the injected clock."""
    now = {"value": 0.0}

    def clock() -> float:
        return now["value"]

    bucket = TokenBucket(rate_per_s=1.0, capacity=2.0, clock=clock)
    assert bucket.try_acquire()
    assert bucket.try_acquire()
    assert not bucket.try_acquire()
    now["value"] = 1.0
    assert bucket.try_acquire()


def _route_snapshot() -> QueueRoute:
    """A route snapshot for announce calls (hints only, never state)."""
    return QueueRoute(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )


def _now_utc() -> datetime:
    """An aware-UTC timestamp for announce snapshots."""
    return datetime.now(tz=UTC)
