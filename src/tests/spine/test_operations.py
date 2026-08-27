"""WP-7.4 bounded inspection, explicit DLQ replay, and worker telemetry proofs."""

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from decimal import Decimal
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

from rememberstack.adapters.testing import RecordingTaskQueue
from rememberstack.adapters.testing import RecordingTelemetry
from rememberstack.model import CostBudget
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EnqueueWork
from rememberstack.model import LaneRouteError
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import RecordCall
from rememberstack.model import RunResultOutcome
from rememberstack.model import WorkNotDeadLetterError
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import error_class_from_traceback
from rememberstack.spine import OperationalCatalog
from rememberstack.spine import OperationalSettings
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import DeadLetterReplayer
from rememberstack.workers import HandlerOutcome
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import Worker

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("74000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head to the caller-provided isolated PostgreSQL DB."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real operational proofs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def bootstrapped_deployment(database_engine: Engine) -> None:
    """Give every proof fresh deployment-owned state."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="operations-test",
            name="Operational correctness proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


class _PermanentFailure:
    """Raise a fresh permanent error and retain its identity for telemetry."""

    def __init__(self) -> None:
        self.errors: list[NonRetryableHandlerError] = []

    def handle(self, **_: object) -> HandlerOutcome:
        error = NonRetryableHandlerError("broken payload")
        self.errors.append(error)
        raise error


class _Success:
    """Complete without follow-up work."""

    def handle(self, **_: object) -> HandlerOutcome:
        return HandlerOutcome()


class _RetryableFailure:
    """Raise an ordinary error so the worker schedules another attempt."""

    def handle(self, **_: object) -> HandlerOutcome:
        raise RuntimeError("retry this work")


class _BrokenExporter:
    """Prove telemetry failures are not swallowed after the state commit."""

    def export_event(self, **_: object) -> None:
        raise RuntimeError("export unavailable")

    def export_exception(self, **_: object) -> None:
        raise RuntimeError("export unavailable")


def _work(*, target_id: UUID, component_version: str) -> EnqueueWork:
    return EnqueueWork(
        deployment_id=_DEPLOYMENT_ID,
        target_kind=ProcessingTarget.DOCUMENT,
        target_id=target_id,
        stage=PipelineStage.CONVERT,
        component_version=component_version,
        content_hash=f"hash-{component_version}",
        lane=ProcessingLane.STEADY,
        payload={"component": component_version},
    )


def test_bounded_inspection_replay_and_original_exception(
    database_engine: Engine,
) -> None:
    """Totals stay complete while samples cap; replay preserves failure history."""
    ledger = WorkLedger(engine=database_engine, settings=WorkLedgerSettings())
    target_id = uuid4()
    processing_ids = tuple(
        ledger.enqueue(
            work=_work(target_id=target_id, component_version=version)
        ).processing_id
        for version in ("convert-v1", "convert-v2")
    )
    handler = _PermanentFailure()
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.CONVERT, handler=handler)
    telemetry = RecordingTelemetry()
    worker = Worker(ledger=ledger, registry=registry, telemetry=telemetry)

    assert (
        worker.run_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.CONVERT,
            lane=ProcessingLane.STEADY,
        ).outcome
        is RunResultOutcome.DEAD_LETTERED
    )
    assert (
        worker.run_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.CONVERT,
            lane=ProcessingLane.STEADY,
        ).outcome
        is RunResultOutcome.DEAD_LETTERED
    )
    before_no_work = len(telemetry.exceptions)
    assert (
        worker.run_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.CONVERT,
            lane=ProcessingLane.STEADY,
        ).outcome
        is RunResultOutcome.NO_WORK
    )
    assert len(telemetry.exceptions) == before_no_work
    assert [exported[1] for exported in telemetry.exceptions] == handler.errors
    assert all(event.name == "worker.run" for event, _ in telemetry.exceptions)

    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projection_snapshots (snapshot_id, deployment_id, "
                "plane, version, gcs_uri, status, is_latest, published_at) "
                "VALUES (:snapshot_id, :deployment_id, "
                "CAST(:plane AS projection_plane), :version, :uri, 'published', "
                "true, now())"
            ),
            [
                {
                    "snapshot_id": uuid4(),
                    "deployment_id": _DEPLOYMENT_ID,
                    "plane": plane,
                    "version": f"{plane}-drill",
                    "uri": f"mem://{plane}",
                }
                for plane in ("P3_corpusfs",)
            ],
        )

    report = OperationalCatalog(
        engine=database_engine, settings=OperationalSettings(sample_limit=1)
    ).inspect(deployment_id=_DEPLOYMENT_ID)
    assert report.dead_letters.total == 2
    assert report.dead_letters.group_total == 2
    assert len(report.dead_letters.groups) == 1
    assert len(report.dead_letters.items) == 1
    assert report.dead_letters.groups[0].error_class.endswith(
        "NonRetryableHandlerError"
    )
    assert report.poison_targets.total == 1
    assert len(report.poison_targets.items) == 1
    assert report.poison_targets.items[0].component_versions == ("convert-v1",)
    assert report.poison_targets.items[0].component_version_total == 2
    assert sum(route.count for route in report.routes) == 2
    assert tuple(snapshot.plane for snapshot in report.latest_projections) == (
        "P3_corpusfs",
    )

    processing_id = processing_ids[0]
    with database_engine.connect() as connection:
        failed = (
            connection.execute(
                text(
                    "SELECT attempts, last_error FROM processing_state "
                    "WHERE processing_id = :processing_id"
                ),
                {"processing_id": processing_id},
            )
            .mappings()
            .one()
        )
    queue = RecordingTaskQueue()
    replayed = DeadLetterReplayer(ledger=ledger, queue=queue).replay(
        deployment_id=_DEPLOYMENT_ID,
        processing_id=processing_id,
        attempt_allowance=1,
        lane=ProcessingLane.BACKFILL,
    )
    assert replayed.attempts == failed["attempts"] == 1
    assert replayed.max_attempts == 2
    assert replayed.route.lane is ProcessingLane.BACKFILL
    assert queue.announcements[0].processing_id == processing_id
    with database_engine.connect() as connection:
        reopened = (
            connection.execute(
                text(
                    "SELECT status::text, attempts, max_attempts, last_error, "
                    "defer_reason, finished_at FROM processing_state "
                    "WHERE processing_id = :processing_id"
                ),
                {"processing_id": processing_id},
            )
            .mappings()
            .one()
        )
    assert reopened == {
        "status": "pending",
        "attempts": 1,
        "max_attempts": 2,
        "last_error": failed["last_error"],
        "defer_reason": None,
        "finished_at": None,
    }
    with pytest.raises(WorkNotDeadLetterError):
        ledger.replay_dead_letter(
            deployment_id=_DEPLOYMENT_ID, processing_id=processing_id
        )

    scheduled_at = datetime.now(UTC) + timedelta(hours=1)
    scheduled = ledger.replay_dead_letter(
        deployment_id=_DEPLOYMENT_ID,
        processing_id=processing_ids[1],
        attempt_allowance=2,
        not_before=scheduled_at,
    )
    assert scheduled.attempts == 1
    assert scheduled.max_attempts == 3
    assert scheduled.not_before == scheduled_at
    with database_engine.connect() as connection:
        defer_reason = connection.execute(
            text(
                "SELECT defer_reason::text FROM processing_state "
                "WHERE processing_id = :processing_id"
            ),
            {"processing_id": processing_ids[1]},
        ).scalar_one()
    assert defer_reason == "scheduled"

    unlaned_id = ledger.enqueue(
        work=EnqueueWork(
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.ENTITY,
            target_id=uuid4(),
            stage=PipelineStage.REFRESH_PROFILE,
            component_version="profile-broken",
            content_hash="profile-hash",
            lane=None,
        )
    ).processing_id
    unlaned_registry = HandlerRegistry()
    unlaned_registry.register(
        stage=PipelineStage.REFRESH_PROFILE, handler=_PermanentFailure()
    )
    Worker(ledger=ledger, registry=unlaned_registry).run_one(
        deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.REFRESH_PROFILE, lane=None
    )
    with pytest.raises(LaneRouteError):
        ledger.replay_dead_letter(
            deployment_id=_DEPLOYMENT_ID,
            processing_id=unlaned_id,
            lane=ProcessingLane.STEADY,
        )
    with pytest.raises(ValueError, match="at least one"):
        ledger.replay_dead_letter(
            deployment_id=_DEPLOYMENT_ID, processing_id=unlaned_id, attempt_allowance=0
        )


def test_currency_audit_uses_the_lifecycle_invariant(database_engine: Engine) -> None:
    """A no-event claim must retain the schema's initial current=true state."""
    doc_id, claim_id = uuid4(), uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind, "
                "source_ref, title) VALUES (:doc, :deployment, 'test', 'currency', "
                "'Currency')"
            ),
            {"doc": doc_id, "deployment": _DEPLOYMENT_ID},
        )
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id, "
                "claim_text, source_span, char_start, char_end, anchor_ok, "
                "window_membership_ok, extractor_version, is_current_testimony) "
                "VALUES (:claim, :deployment, :doc, :chunk, 'claim', 'span', 0, 4, "
                "true, true, 'extractor-v1', false)"
            ),
            {
                "claim": claim_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "chunk": uuid4(),
            },
        )
    catalog = OperationalCatalog(
        engine=database_engine, settings=OperationalSettings(sample_limit=1)
    )
    audit = catalog.inspect(deployment_id=_DEPLOYMENT_ID).currency
    assert audit.claims == 1
    assert audit.mismatch_total == 1
    assert audit.mismatches[0].claim_id == claim_id
    assert audit.mismatches[0].ledger_current is True
    with database_engine.begin() as connection:
        connection.execute(
            text("UPDATE claims SET is_current_testimony = true WHERE claim_id = :id"),
            {"id": claim_id},
        )
    assert catalog.inspect(deployment_id=_DEPLOYMENT_ID).currency.mismatch_total == 0


def test_success_state_commits_before_exporter_failure(database_engine: Engine) -> None:
    """Telemetry is strict, but its failure cannot roll back completed work."""
    ledger = WorkLedger(engine=database_engine, settings=WorkLedgerSettings())
    processing_id = ledger.enqueue(
        work=_work(target_id=uuid4(), component_version="convert-success")
    ).processing_id
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.CONVERT, handler=_Success())
    worker = Worker(ledger=ledger, registry=registry, telemetry=_BrokenExporter())
    with pytest.raises(RuntimeError, match="export unavailable"):
        worker.run_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.CONVERT,
            lane=ProcessingLane.STEADY,
        )
    with database_engine.connect() as connection:
        status = connection.execute(
            text(
                "SELECT status::text FROM processing_state "
                "WHERE processing_id = :processing_id"
            ),
            {"processing_id": processing_id},
        ).scalar_one()
    assert status == "succeeded"


def test_retry_is_announced_before_exporter_failure(database_engine: Engine) -> None:
    """Strict telemetry failure cannot suppress an already-committed retry wake."""
    ledger = WorkLedger(
        engine=database_engine,
        settings=WorkLedgerSettings(retry_backoff_base_s=0.0, retry_backoff_max_s=0.0),
    )
    processing_id = ledger.enqueue(
        work=_work(target_id=uuid4(), component_version="convert-retry")
    ).processing_id
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.CONVERT, handler=_RetryableFailure())
    queue = RecordingTaskQueue()
    worker = Worker(
        ledger=ledger, registry=registry, queue=queue, telemetry=_BrokenExporter()
    )

    with pytest.raises(RuntimeError, match="export unavailable"):
        worker.run_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.CONVERT,
            lane=ProcessingLane.STEADY,
        )

    assert queue.announcements[0].processing_id == processing_id
    with database_engine.connect() as connection:
        status = connection.execute(
            text(
                "SELECT status::text FROM processing_state "
                "WHERE processing_id = :processing_id"
            ),
            {"processing_id": processing_id},
        ).scalar_one()
    assert status == "failed"


def test_budget_park_emits_one_worker_event_without_running_handler(
    database_engine: Engine,
) -> None:
    """A preflight park is visible and consumes no handler attempt."""
    ledger = WorkLedger(
        engine=database_engine,
        settings=WorkLedgerSettings(
            budgets=(
                CostBudget(
                    deployment_id=_DEPLOYMENT_ID,
                    stage=PipelineStage.CONVERT,
                    lane=ProcessingLane.STEADY,
                    window_seconds=3_600,
                    ceiling_usd=Decimal("1.00"),
                ),
            )
        ),
    )
    charged = ledger.enqueue(
        work=_work(target_id=uuid4(), component_version="convert-charged")
    ).processing_id
    claimed = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.CONVERT,
        lane=ProcessingLane.STEADY,
    )
    assert claimed is not None
    ledger.record_call(
        call=RecordCall(
            processing_id=charged,
            call_key="provider-call",
            model_name="fixture-model",
            cost_usd=Decimal("1.00"),
        )
    )
    ledger.complete(processing_id=charged)
    parked_id = ledger.enqueue(
        work=_work(target_id=uuid4(), component_version="convert-parked")
    ).processing_id
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.CONVERT, handler=_Success())
    telemetry = RecordingTelemetry()

    result = Worker(ledger=ledger, registry=registry, telemetry=telemetry).run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.CONVERT,
        lane=ProcessingLane.STEADY,
    )

    assert result.processing_id == parked_id
    assert result.outcome is RunResultOutcome.BUDGET_PARKED
    assert len(telemetry.events) == 1
    attributes = {item.name: item.value for item in telemetry.events[0].attributes}
    assert attributes["outcome"] == "budget_parked"
    assert attributes["attempt"] is None
    with database_engine.connect() as connection:
        state = (
            connection.execute(
                text(
                    "SELECT status::text, defer_reason::text, attempts "
                    "FROM processing_state WHERE processing_id = :processing_id"
                ),
                {"processing_id": parked_id},
            )
            .mappings()
            .one()
        )
    assert state == {"status": "pending", "defer_reason": "budget", "attempts": 0}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("Traceback\nValueError: bad", "ValueError"),
        ("Traceback\npackage.CustomError", "package.CustomError"),
        ("not an exception line", "unknown"),
        (None, "unknown"),
    ],
)
def test_error_class_derivation_is_pure_and_total(
    error: str | None, expected: str
) -> None:
    assert error_class_from_traceback(error) == expected
