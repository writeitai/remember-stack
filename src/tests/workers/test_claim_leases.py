"""Real-PostgreSQL proofs for claim leases and the reaper.

A claim holds its row only while its worker renews the lease. A worker killed
mid-task stops renewing; the reaper returns its row to ``pending`` (or
dead-letters it on the last attempt) and another worker runs it. Complete, fail
and renewal are conditional on the attempt, so a worker whose claim was taken
back cannot finish a newer attempt.
"""

from collections.abc import Iterator
from pathlib import Path
import signal
import subprocess
import sys
import textwrap
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

from rememberstack.adapters.testing import RecordingTaskQueue
from rememberstack.model import ClaimedWork
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EnqueueWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import RecordCall
from rememberstack.model import RunResultOutcome
from rememberstack.model import WorkNotRunningError
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
_DEPLOYMENT_ID = UUID("50000000-0000-0000-0000-000000000063")
_VERSION = "lease-test-2026-11"
_STAGE = PipelineStage.EXTRACT_CLAIMS


@pytest.fixture(scope="module")
def database_url() -> str:
    """The accepted integration database URL, or a module-wide skip."""
    try:
        return load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for claim-lease proofs")


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
            slug="lease-test",
            name="Claim lease proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def _ledger(engine: Engine, *, lease_s: float = 60.0) -> WorkLedger:
    return WorkLedger(
        engine=engine,
        settings=WorkLedgerSettings(
            retry_backoff_base_s=0.0, retry_backoff_max_s=0.0, lease_s=lease_s
        ),
    )


def _work(*, stage: PipelineStage = _STAGE) -> EnqueueWork:
    return EnqueueWork(
        deployment_id=_DEPLOYMENT_ID,
        target_kind=ProcessingTarget.CHUNK,
        target_id=uuid4(),
        stage=stage,
        component_version=_VERSION,
        content_hash="sha256:test",
        lane=ProcessingLane.STEADY,
    )


def _claim(ledger: WorkLedger, *, stage: PipelineStage = _STAGE) -> ClaimedWork:
    claimed = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
    )
    assert isinstance(claimed, ClaimedWork)
    return claimed


def _expire(engine: Engine, processing_id: UUID) -> None:
    """Move a lease into the past, as if its worker stopped renewing it."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing_state SET lease_until = now() - interval '1 second'"
                " WHERE processing_id = :processing_id"
            ),
            {"processing_id": processing_id},
        )


def _row(engine: Engine, processing_id: UUID) -> dict[str, object]:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                text(
                    "SELECT status::text AS status, attempts, last_error, lease_until"
                    " FROM processing_state WHERE processing_id = :processing_id"
                ),
                {"processing_id": processing_id},
            )
            .mappings()
            .one()
        )


class _Success:
    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        del work, meter
        return HandlerOutcome()


class _FinalizingSuccess(_Success):
    def __init__(self) -> None:
        self.finalized: list[tuple[UUID, str]] = []

    def finalize_terminal_failure(self, *, work: ClaimedWork, error: str) -> None:
        self.finalized.append((work.processing_id, error))


def _worker(
    ledger: WorkLedger, handler: object, queue: RecordingTaskQueue | None = None
) -> Worker:
    registry = HandlerRegistry()
    registry.register(stage=_STAGE, handler=handler)  # type: ignore[arg-type]
    return Worker(ledger=ledger, registry=registry, queue=queue)


def test_a_claim_records_its_lease(database_engine: Engine) -> None:
    """Claiming sets lease_until about lease_s ahead of the database clock."""
    ledger = _ledger(database_engine, lease_s=30.0)
    ledger.enqueue(work=_work())
    claimed = _claim(ledger)
    with database_engine.connect() as connection:
        remaining = connection.execute(
            text(
                "SELECT extract(epoch FROM lease_until - now())"
                " FROM processing_state WHERE processing_id = :processing_id"
            ),
            {"processing_id": claimed.processing_id},
        ).scalar_one()
    assert 25 < float(remaining) <= 30


def test_a_stale_attempt_cannot_renew_complete_or_fail_a_newer_one(
    database_engine: Engine,
) -> None:
    """Once its claim is reaped and reclaimed, attempt 1 can change nothing."""
    ledger = _ledger(database_engine)
    ledger.enqueue(work=_work())
    first = _claim(ledger)
    _expire(database_engine, first.processing_id)
    (reaped,) = ledger.reap_expired_leases(
        deployment_id=_DEPLOYMENT_ID, stages=(_STAGE,)
    )
    assert reaped.requeued and reaped.work.attempt == 1
    second = _claim(ledger)
    assert second.processing_id == first.processing_id and second.attempt == 2

    assert not ledger.renew_lease(processing_id=first.processing_id, attempt=1)
    with pytest.raises(WorkNotRunningError):
        ledger.record_call(
            call=RecordCall(
                processing_id=first.processing_id, attempt=1, call_key="selection"
            )
        )
    with pytest.raises(WorkNotRunningError):
        ledger.complete(processing_id=first.processing_id, attempt=1)
    with pytest.raises(WorkNotRunningError):
        ledger.fail(
            processing_id=first.processing_id,
            attempt=1,
            error="late failure",
            retryable=True,
        )
    assert _row(database_engine, first.processing_id)["status"] == "running"

    assert ledger.renew_lease(processing_id=second.processing_id, attempt=2)
    assert ledger.record_call(
        call=RecordCall(
            processing_id=second.processing_id, attempt=2, call_key="selection"
        )
    )
    ledger.complete(processing_id=second.processing_id, attempt=2)
    assert _row(database_engine, second.processing_id)["status"] == "succeeded"


def test_the_reaper_requeues_expired_claims_and_dead_letters_exhausted_ones(
    database_engine: Engine,
) -> None:
    """Only expired running rows on the named stages move, as the attempt count says."""
    ledger = _ledger(database_engine)
    retryable_id = ledger.enqueue(work=_work()).processing_id
    exhausted_id = ledger.enqueue(work=_work()).processing_id
    live_id = ledger.enqueue(work=_work()).processing_id
    other_stage_id = ledger.enqueue(
        work=_work(stage=PipelineStage.EMBED_CLAIM)
    ).processing_id
    claimed = {_claim(ledger).processing_id for _ in range(3)}
    assert claimed == {retryable_id, exhausted_id, live_id}
    _claim(ledger, stage=PipelineStage.EMBED_CLAIM)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing_state SET max_attempts = 1"
                " WHERE processing_id = :processing_id"
            ),
            {"processing_id": exhausted_id},
        )
    for processing_id in (retryable_id, exhausted_id, other_stage_id):
        _expire(database_engine, processing_id)

    reaped = ledger.reap_expired_leases(deployment_id=_DEPLOYMENT_ID, stages=(_STAGE,))

    assert {(lease.work.processing_id, lease.requeued) for lease in reaped} == {
        (retryable_id, True),
        (exhausted_id, False),
    }
    retryable = _row(database_engine, retryable_id)
    assert retryable["status"] == "pending" and retryable["attempts"] == 1
    assert "lease expired" in str(retryable["last_error"])
    exhausted = _row(database_engine, exhausted_id)
    assert exhausted["status"] == "dead_letter" and exhausted["attempts"] == 1
    assert _row(database_engine, live_id)["status"] == "running"
    assert _row(database_engine, other_stage_id)["status"] == "running"
    assert _claim(ledger).processing_id == retryable_id

    # A sweeper without handlers sweeps every stage of the database.
    (swept,) = ledger.reap_expired_leases(deployment_id=_DEPLOYMENT_ID)
    assert (swept.work.processing_id, swept.requeued) == (other_stage_id, True)


def test_the_worker_announces_requeued_rows_and_finalizes_dead_letters(
    database_engine: Engine,
) -> None:
    """Reaping through the worker announces retries and finalizes terminal ones."""
    ledger = _ledger(database_engine)
    requeued_id = ledger.enqueue(work=_work()).processing_id
    exhausted_id = ledger.enqueue(work=_work()).processing_id
    _claim(ledger)
    _claim(ledger)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing_state SET max_attempts = 1"
                " WHERE processing_id = :processing_id"
            ),
            {"processing_id": exhausted_id},
        )
    _expire(database_engine, requeued_id)
    _expire(database_engine, exhausted_id)
    handler = _FinalizingSuccess()
    queue = RecordingTaskQueue()

    _worker(ledger, handler, queue).reap_expired_leases(deployment_id=_DEPLOYMENT_ID)

    assert [call.processing_id for call in queue.announcements] == [requeued_id]
    assert [processing_id for processing_id, _ in handler.finalized] == [exhausted_id]


def test_the_worker_renews_its_lease_while_the_handler_runs(
    database_engine: Engine,
) -> None:
    """A handler that outlives its first lease is not reaped and completes."""
    lease_s = 0.6
    ledger = _ledger(database_engine, lease_s=lease_s)
    processing_id = ledger.enqueue(work=_work()).processing_id
    reaped_while_running: list[object] = []

    class _OutliveTheLease:
        def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
            del work, meter
            time.sleep(lease_s * 3)
            reaped_while_running.extend(
                ledger.reap_expired_leases(
                    deployment_id=_DEPLOYMENT_ID, stages=(_STAGE,)
                )
            )
            return HandlerOutcome()

    result = _worker(ledger, _OutliveTheLease()).run_one(
        deployment_id=_DEPLOYMENT_ID, stage=_STAGE, lane=ProcessingLane.STEADY
    )
    assert reaped_while_running == []
    assert result.outcome is RunResultOutcome.SUCCEEDED
    row = _row(database_engine, processing_id)
    assert row["status"] == "succeeded" and row["attempts"] == 1


def test_a_worker_whose_claim_was_taken_back_reports_lease_lost(
    database_engine: Engine,
) -> None:
    """The runner discards a result whose attempt no longer holds the row."""
    ledger = _ledger(database_engine)
    processing_id = ledger.enqueue(work=_work())

    class _LoseLease:
        def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
            del meter
            _expire(database_engine, work.processing_id)
            ledger.reap_expired_leases(deployment_id=_DEPLOYMENT_ID, stages=(_STAGE,))
            return HandlerOutcome()

    result = _worker(ledger, _LoseLease()).run_one(
        deployment_id=_DEPLOYMENT_ID, stage=_STAGE, lane=ProcessingLane.STEADY
    )
    assert result.outcome is RunResultOutcome.LEASE_LOST
    assert _row(database_engine, processing_id.processing_id)["status"] == "pending"


_HANGING_WORKER = textwrap.dedent(
    """
    import sys
    import time
    from uuid import UUID

    from sqlalchemy import create_engine

    from rememberstack.model import PipelineStage
    from rememberstack.model import ProcessingLane
    from rememberstack.spine import WorkLedger
    from rememberstack.spine import WorkLedgerSettings
    from rememberstack.workers import HandlerOutcome
    from rememberstack.workers import HandlerRegistry
    from rememberstack.workers import Worker


    class Hang:
        def handle(self, *, work, meter):
            time.sleep(600)
            return HandlerOutcome()


    database_url, deployment_id, lease_s = sys.argv[1], sys.argv[2], sys.argv[3]
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.EXTRACT_CLAIMS, handler=Hang())
    ledger = WorkLedger(
        engine=create_engine(database_url),
        settings=WorkLedgerSettings(lease_s=float(lease_s)),
    )
    Worker(ledger=ledger, registry=registry).run_one(
        deployment_id=UUID(deployment_id),
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )
    """
)


def _wait_for(predicate: object, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return
        time.sleep(0.1)
    raise AssertionError("condition not reached in time")


def test_a_worker_killed_mid_task_is_reaped_and_its_task_runs_again(
    database_url: str, database_engine: Engine
) -> None:
    """SIGKILL a worker inside its handler; the task is retried, not stranded."""
    lease_s = 1.0
    ledger = _ledger(database_engine, lease_s=lease_s)
    processing_id = ledger.enqueue(work=_work()).processing_id
    survivor = _worker(ledger, _Success())
    child = subprocess.Popen(  # noqa: S603 — fixed interpreter and script
        [
            sys.executable,
            "-c",
            _HANGING_WORKER,
            database_url,
            str(_DEPLOYMENT_ID),
            str(lease_s),
        ]
    )
    try:
        _wait_for(
            lambda: _row(database_engine, processing_id)["status"] == "running",
            timeout_s=60,
        )
        # Alive, the worker renews: after more than a lease, nothing is reaped.
        time.sleep(lease_s * 2.5)
        assert survivor.reap_expired_leases(deployment_id=_DEPLOYMENT_ID) == ()

        child.send_signal(signal.SIGKILL)
        child.wait(timeout=10)
        assert _row(database_engine, processing_id)["status"] == "running"

        reaped: list[object] = []
        _wait_for(
            lambda: (
                reaped.extend(
                    survivor.reap_expired_leases(deployment_id=_DEPLOYMENT_ID)
                )
                or reaped
            ),
            timeout_s=lease_s * 5,
        )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)

    row = _row(database_engine, processing_id)
    assert row["status"] == "pending" and row["attempts"] == 1
    result = survivor.run_one(
        deployment_id=_DEPLOYMENT_ID, stage=_STAGE, lane=ProcessingLane.STEADY
    )
    assert result.outcome is RunResultOutcome.SUCCEEDED
    row = _row(database_engine, processing_id)
    assert row["status"] == "succeeded" and row["attempts"] == 2
