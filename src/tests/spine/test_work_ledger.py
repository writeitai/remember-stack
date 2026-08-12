"""Real-PostgreSQL proofs for the D67 work ledger and the WP-0.3 no-op worker chain."""

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from decimal import Decimal
import json
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
from rememberstack.core import source_identity_hash
from rememberstack.model import ClaimedWork
from rememberstack.model import CostBudget
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EnqueueWork
from rememberstack.model import ForgetInProgressError
from rememberstack.model import ForgottenSourceError
from rememberstack.model import LaneRouteError
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import RecordCall
from rememberstack.model import RunResultOutcome
from rememberstack.model import UnknownStageHandlerError
from rememberstack.model import WorkNotRunningError
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.spine.work_ledger import _ADVISORY_LOCK_REPRESENTATION
from rememberstack.surfaces import cli_main
from rememberstack.workers import HandlerOutcome
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import Worker

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("30000000-0000-0000-0000-000000000001")
_VERSION = "worker-test-2026-07"


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL ledger proofs"
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
    """Give every proof a fresh deployment (processing rows FK onto it)."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="worker-test",
            name="Worker ledger proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


@pytest.fixture()
def ledger(database_engine: Engine) -> WorkLedger:
    """A ledger with zero retry backoff so retries are immediately claimable."""
    return WorkLedger(
        engine=database_engine,
        settings=WorkLedgerSettings(retry_backoff_base_s=0.0, retry_backoff_max_s=0.0),
    )


def test_representation_advisory_lock_executes_on_postgres(
    database_engine: Engine,
) -> None:
    """D84 uses a real PostgreSQL advisory-lock overload, not an imagined one."""
    with database_engine.begin() as connection:
        connection.execute(
            _ADVISORY_LOCK_REPRESENTATION, {"representation_id": uuid4()}
        )


def _work(
    *,
    stage: PipelineStage = PipelineStage.EXTRACT_CLAIMS,
    lane: ProcessingLane | None = ProcessingLane.STEADY,
    target_id: UUID | None = None,
) -> EnqueueWork:
    """One enqueueable unit of work with a stable default shape."""
    return EnqueueWork(
        deployment_id=_DEPLOYMENT_ID,
        target_kind=ProcessingTarget.CHUNK,
        target_id=target_id or uuid4(),
        stage=stage,
        component_version=_VERSION,
        content_hash="sha256:test",
        lane=lane,
    )


def test_enqueue_is_idempotent_and_promotes_backfill_to_steady(
    ledger: WorkLedger,
) -> None:
    """The D12 key admits one unit of work; steady promotes a pending backfill row."""
    target_id = uuid4()
    first = ledger.enqueue(
        work=_work(lane=ProcessingLane.BACKFILL, target_id=target_id)
    )
    duplicate = ledger.enqueue(
        work=_work(lane=ProcessingLane.BACKFILL, target_id=target_id)
    )
    promoted = ledger.enqueue(
        work=_work(lane=ProcessingLane.STEADY, target_id=target_id)
    )

    assert first.created and not first.promoted_to_steady
    assert not duplicate.created and not duplicate.promoted_to_steady
    assert not promoted.created and promoted.promoted_to_steady
    assert promoted.processing_id == first.processing_id

    demote_attempt = ledger.enqueue(
        work=_work(lane=ProcessingLane.BACKFILL, target_id=target_id)
    )
    assert not demote_attempt.created and not demote_attempt.promoted_to_steady
    claimed = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )
    assert isinstance(claimed, ClaimedWork)
    assert claimed.processing_id == first.processing_id


def test_lane_pairing_is_enforced_at_enqueue_and_claim(ledger: WorkLedger) -> None:
    """The D67 pairing rule rejects laned K/P work and unlaned plane-E work."""
    with pytest.raises(LaneRouteError):
        ledger.enqueue(work=_work(stage=PipelineStage.EXTRACT_CLAIMS, lane=None))
    with pytest.raises(LaneRouteError):
        ledger.enqueue(
            work=_work(
                stage=PipelineStage.COMPILE_KNOWLEDGE, lane=ProcessingLane.STEADY
            )
        )
    with pytest.raises(LaneRouteError):
        ledger.claim_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.COMPILE_KNOWLEDGE,
            lane=ProcessingLane.BACKFILL,
        )


def test_cost_attribution_is_copied_from_the_running_row(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """A billed call copies stage/lane/attempt from the claimed row, idempotently."""
    ledger.enqueue(work=_work())
    claimed = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )
    assert isinstance(claimed, ClaimedWork)

    call = RecordCall(
        processing_id=claimed.processing_id,
        call_key="selection",
        cost_usd=Decimal("0.01"),
    )
    assert ledger.record_call(call=call) is True
    assert ledger.record_call(call=call) is False  # ack-lost retry cannot double-bill

    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT stage, lane, attempt FROM cost_ledger"
                    " WHERE processing_id = :processing_id"
                ),
                {"processing_id": claimed.processing_id},
            )
            .mappings()
            .one()
        )
    assert row["stage"] == PipelineStage.EXTRACT_CLAIMS.value
    assert row["lane"] == ProcessingLane.STEADY.value
    assert row["attempt"] == 1

    ledger.complete(processing_id=claimed.processing_id)
    with pytest.raises(WorkNotRunningError):
        ledger.record_call(
            call=RecordCall(processing_id=claimed.processing_id, call_key="late")
        )


def test_budget_parking_consumes_no_attempt_and_keeps_no_error(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """Parking is pending + defer_reason budget + future not_before, nothing else."""
    enqueued = ledger.enqueue(work=_work())
    ledger.park_for_budget(
        processing_id=enqueued.processing_id,
        resume_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT status, defer_reason, attempts, last_error"
                    " FROM processing_state WHERE processing_id = :processing_id"
                ),
                {"processing_id": enqueued.processing_id},
            )
            .mappings()
            .one()
        )
    assert row["status"] == "pending"
    assert row["defer_reason"] == "budget"
    assert row["attempts"] == 0
    assert row["last_error"] is None
    assert (
        ledger.claim_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.EXTRACT_CLAIMS,
            lane=ProcessingLane.STEADY,
        )
        is None
    )  # parked work is not due


def test_running_work_can_never_be_budget_parked(ledger: WorkLedger) -> None:
    """Parking a running attempt would allow a second concurrent claim — refused."""
    ledger.enqueue(work=_work())
    claimed = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )
    assert isinstance(claimed, ClaimedWork)
    with pytest.raises(WorkNotRunningError):
        ledger.park_for_budget(
            processing_id=claimed.processing_id,
            resume_at=datetime.now(tz=UTC) + timedelta(hours=1),
        )


class _CountingHandler:
    """A successful handler that exposes whether budget pre-flight let it execute."""

    def __init__(self) -> None:
        """Start with no handler executions."""
        self.calls = 0

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Count one execution and complete without follow-up work."""
        del work, meter
        self.calls += 1
        return HandlerOutcome()


def test_configured_budget_parks_reports_and_resumes_without_losing_work(
    database_engine: Engine,
) -> None:
    """A fixture ceiling parks before an attempt and the next window resumes normally."""
    budget = CostBudget(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
        window_seconds=86_400,
        ceiling_usd=Decimal("1.00"),
    )
    budgeted = WorkLedger(
        engine=database_engine,
        settings=WorkLedgerSettings(
            retry_backoff_base_s=0.0, retry_backoff_max_s=0.0, budgets=(budget,)
        ),
    )

    billed = budgeted.enqueue(work=_work())
    claimed = budgeted.claim_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )
    assert isinstance(claimed, ClaimedWork)
    assert claimed.processing_id == billed.processing_id
    assert budgeted.record_call(
        call=RecordCall(
            processing_id=claimed.processing_id,
            call_key="selection",
            tier="selection",
            cost_usd=Decimal("0.75"),
        )
    )
    assert budgeted.record_call(
        call=RecordCall(
            processing_id=claimed.processing_id,
            call_key="decontextualize",
            tier="frontier",
            cost_usd=Decimal("0.50"),
        )
    )
    budgeted.complete(processing_id=claimed.processing_id)

    waiting = budgeted.enqueue(work=_work())
    handler = _CountingHandler()
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.EXTRACT_CLAIMS, handler=handler)
    queue = RecordingTaskQueue()
    worker = Worker(ledger=budgeted, registry=registry, queue=queue)

    parked_result = worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )
    assert parked_result.processing_id == waiting.processing_id
    assert parked_result.outcome is RunResultOutcome.BUDGET_PARKED
    assert handler.calls == 0
    (announcement,) = queue.announcements
    assert announcement.processing_id == waiting.processing_id

    with database_engine.connect() as connection:
        parked = (
            connection.execute(
                text(
                    "SELECT status, defer_reason, attempts, last_error, not_before"
                    " FROM processing_state WHERE processing_id = :processing_id"
                ),
                {"processing_id": waiting.processing_id},
            )
            .mappings()
            .one()
        )
    assert parked["status"] == "pending"
    assert parked["defer_reason"] == "budget"
    assert parked["attempts"] == 0
    assert parked["last_error"] is None
    assert announcement.not_before_snapshot == parked["not_before"]

    (status,) = budgeted.budget_status(deployment_id=_DEPLOYMENT_ID)
    assert status.spent_usd == Decimal("1.250000")
    assert status.remaining_usd == Decimal(0)
    assert status.exhausted
    assert status.parked_work == 1
    assert {tier.tier: tier.cost_usd for tier in status.tiers} == {
        "frontier": Decimal("0.500000"),
        "selection": Decimal("0.750000"),
    }

    with database_engine.begin() as connection:
        connection.execute(
            text("UPDATE cost_ledger SET occurred_at = occurred_at - interval '2 days'")
        )
        connection.execute(
            text(
                "UPDATE processing_state SET not_before = now()"
                " WHERE processing_id = :processing_id"
            ),
            {"processing_id": waiting.processing_id},
        )

    resumed = worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )
    assert resumed.processing_id == waiting.processing_id
    assert resumed.outcome is RunResultOutcome.SUCCEEDED
    assert handler.calls == 1


def test_budget_settings_are_unique_and_cli_inspection_uses_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The environment declares one unambiguous route ceiling visible through the CLI."""
    configured = {
        "deployment_id": str(_DEPLOYMENT_ID),
        "stage": PipelineStage.EXTRACT_CLAIMS.value,
        "lane": ProcessingLane.STEADY.value,
        "window_seconds": 3600,
        "ceiling_usd": "2.50",
    }
    monkeypatch.setenv("REMEMBERSTACK_WORK_BUDGETS", json.dumps([configured]))
    settings = WorkLedgerSettings()
    assert settings.budgets[0].ceiling_usd == Decimal("2.50")
    with pytest.raises(ValidationError, match="only one cost budget"):
        WorkLedgerSettings(budgets=(settings.budgets[0], settings.budgets[0]))

    assert cli_main(["budget", "inspect", "--deployment", str(_DEPLOYMENT_ID)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == PipelineStage.EXTRACT_CLAIMS.value
    assert payload["lane"] == ProcessingLane.STEADY.value
    assert payload["ceiling_usd"] == "2.50"


class _ChainingNoOpHandler:
    """The demo no-op handler: succeeds and chains the next stage for its target."""

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Produce the chain follow-up without doing any real work."""
        del meter
        return HandlerOutcome(
            follow_up=(
                EnqueueWork(
                    deployment_id=work.deployment_id,
                    target_kind=work.target_kind,
                    target_id=work.target_id,
                    stage=PipelineStage.EMBED_CHUNK,
                    component_version=work.component_version,
                    content_hash=work.content_hash,
                    lane=work.lane,
                ),
            )
        )


class _AlwaysFailingHandler:
    """A handler whose every execution raises — exercising retry then dead-letter."""

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Fail unconditionally with a real traceback."""
        del meter
        raise RuntimeError(f"deliberate failure for {work.processing_id}")


class _BillableFailingHandler:
    """A provider failure whose malformed output still carries billed usage."""

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Expose usage to the worker error boundary without recording it here."""
        del work, meter
        raise ProviderCallError(
            "malformed provider response",
            usage=ProviderCallUsage(
                model_name="provider/model",
                tokens_in=12,
                tokens_out=3,
                cost_usd=Decimal("0.25"),
                latency_ms=8,
            ),
        )


class _UnaccountedPaidResponseHandler:
    """A completed call whose provider accounting cannot be recovered."""

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Expose the terminal accounting failure to the worker boundary."""
        del work, meter
        raise ProviderAccountingError("provider accounting unavailable")


def test_worker_does_not_automatically_retry_unaccounted_paid_response(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """Fail closed without silently buying the same generation again."""
    registry = HandlerRegistry()
    registry.register(
        stage=PipelineStage.EXTRACT_CLAIMS, handler=_UnaccountedPaidResponseHandler()
    )
    worker = Worker(ledger=ledger, registry=registry)
    enqueued = ledger.enqueue(work=_work())

    result = worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )

    assert result.outcome is RunResultOutcome.DEAD_LETTERED
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT status, attempts, max_attempts FROM processing_state"
                    " WHERE processing_id = :processing_id"
                ),
                {"processing_id": enqueued.processing_id},
            )
            .mappings()
            .one()
        )
    assert dict(row) == {"status": "dead_letter", "attempts": 1, "max_attempts": 3}


def test_worker_meters_usage_bearing_provider_failure_before_retry(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """A malformed billable response cannot disappear from route budgets."""
    registry = HandlerRegistry()
    registry.register(
        stage=PipelineStage.EXTRACT_CLAIMS, handler=_BillableFailingHandler()
    )
    worker = Worker(ledger=ledger, registry=registry)
    enqueued = ledger.enqueue(work=_work())

    result = worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )

    assert result.outcome is RunResultOutcome.RETRY_SCHEDULED
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT call_key, tier, model_name, tokens_in, tokens_out,"
                    " cost_usd FROM cost_ledger"
                    " WHERE processing_id = :processing_id"
                ),
                {"processing_id": enqueued.processing_id},
            )
            .mappings()
            .one()
        )
    assert dict(row) == {
        "call_key": "provider_failure",
        "tier": "failed_response",
        "model_name": "provider/model",
        "tokens_in": 12,
        "tokens_out": 3,
        "cost_usd": Decimal("0.250000"),
    }


def test_demo_no_op_worker_chain_retry_and_dead_letter(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """WP-0.3 acceptance: enqueue → run → state row + chain → retry → dead-letter."""
    registry = HandlerRegistry()
    registry.register(
        stage=PipelineStage.EXTRACT_CLAIMS, handler=_ChainingNoOpHandler()
    )
    registry.register(stage=PipelineStage.EMBED_CHUNK, handler=_AlwaysFailingHandler())
    worker = Worker(ledger=ledger, registry=registry)

    target_id = uuid4()
    ledger.enqueue(work=_work(target_id=target_id))

    success = worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )
    assert success.outcome == "succeeded"

    outcomes = [
        worker.run_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.EMBED_CHUNK,
            lane=ProcessingLane.STEADY,
        ).outcome
        for _ in range(4)
    ]
    assert outcomes == [
        "retry_scheduled",
        "retry_scheduled",
        "dead_lettered",
        "no_work",
    ]

    with database_engine.connect() as connection:
        rows = {
            row["stage"]: row
            for row in connection.execute(
                text(
                    "SELECT stage, status, attempts, last_error"
                    " FROM processing_state WHERE target_id = :target_id"
                ),
                {"target_id": target_id},
            ).mappings()
        }
    assert rows["extract_claims"]["status"] == "succeeded"
    dead = rows["embed_chunk"]
    assert dead["status"] == "dead_letter"
    assert dead["attempts"] == 3  # initial + two retries (D12/D67)
    assert "RuntimeError: deliberate failure" in dead["last_error"]
    assert "Traceback" in dead["last_error"]  # full traceback, never trimmed (value 6)


def test_promotion_clears_backfill_budget_parking(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """Codex review 1: a promoted row escapes the backfill budget window."""
    target_id = uuid4()
    enqueued = ledger.enqueue(
        work=_work(lane=ProcessingLane.BACKFILL, target_id=target_id)
    )
    ledger.park_for_budget(
        processing_id=enqueued.processing_id,
        resume_at=datetime.now(tz=UTC) + timedelta(days=1),
    )
    promoted = ledger.enqueue(
        work=_work(lane=ProcessingLane.STEADY, target_id=target_id)
    )
    assert promoted.promoted_to_steady

    claimed = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )
    assert isinstance(claimed, ClaimedWork)
    assert claimed.processing_id == enqueued.processing_id


def test_unregistered_stage_never_strands_a_claimed_row(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """Codex review 3: handler resolution precedes the claim — no attempt consumed."""
    enqueued = ledger.enqueue(work=_work())
    worker = Worker(ledger=ledger, registry=HandlerRegistry())
    with pytest.raises(UnknownStageHandlerError):
        worker.run_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.EXTRACT_CLAIMS,
            lane=ProcessingLane.STEADY,
        )
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT status, attempts FROM processing_state"
                    " WHERE processing_id = :processing_id"
                ),
                {"processing_id": enqueued.processing_id},
            )
            .mappings()
            .one()
        )
    assert row["status"] == "pending"
    assert row["attempts"] == 0


def test_scheduled_retry_is_announced_through_the_queue_port(
    ledger: WorkLedger,
) -> None:
    """Codex review 4: retry paths call the port with the scheduled time."""
    from rememberstack.adapters.testing import RecordingTaskQueue

    recorder = RecordingTaskQueue()
    registry = HandlerRegistry()
    registry.register(
        stage=PipelineStage.EXTRACT_CLAIMS, handler=_AlwaysFailingHandler()
    )
    worker = Worker(ledger=ledger, registry=registry, queue=recorder)
    enqueued = ledger.enqueue(work=_work())

    result = worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )
    assert result.outcome is RunResultOutcome.RETRY_SCHEDULED
    (announcement,) = recorder.announcements
    assert announcement.processing_id == enqueued.processing_id
    assert announcement.route_snapshot.stage is PipelineStage.EXTRACT_CLAIMS


def test_active_forget_blocks_ordinary_claims_but_authorizes_its_worker(
    ledger: WorkLedger, database_engine: Engine
) -> None:
    """Apply the D74 barrier without deadlocking the unlaned coordinator."""
    ordinary = ledger.enqueue(work=_work())
    forget_id = uuid4()
    hard_forget = ledger.enqueue(
        work=EnqueueWork(
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.DOCUMENT,
            target_id=uuid4(),
            stage=PipelineStage.HARD_FORGET,
            component_version="hard-forget-v1",
            content_hash="a" * 64,
            lane=None,
        )
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO forget_manifests (
                    forget_id, deployment_id, doc_id, schema_version, status
                ) VALUES (:forget_id, :deployment_id, :doc_id, 1, 'preparing')
                """
            ),
            {
                "forget_id": forget_id,
                "deployment_id": _DEPLOYMENT_ID,
                "doc_id": uuid4(),
            },
        )

    with pytest.raises(ForgetInProgressError):
        ledger.claim_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.EXTRACT_CLAIMS,
            lane=ProcessingLane.STEADY,
        )
    claimed = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.HARD_FORGET, lane=None
    )

    assert ordinary.created is True
    assert isinstance(claimed, ClaimedWork)
    assert claimed.processing_id == hard_forget.processing_id


def test_completed_manifest_is_an_irreversible_exact_ingest_guard(
    database_engine: Engine,
) -> None:
    """Reject both source identity and raw hash while admitting unrelated input."""
    forgotten_content_hash = "b" * 64
    forgotten_identity_hash = source_identity_hash(
        deployment_id=_DEPLOYMENT_ID, source_kind="drive", source_ref="file-1"
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO forget_manifests (
                    forget_id, deployment_id, doc_id, schema_version,
                    manifest_hash, manifest, source_identity_hash, content_hashes,
                    status, accepted_at, completed_at
                ) VALUES (
                    :forget_id, :deployment_id, :doc_id, 1,
                    :manifest_hash, '{}'::jsonb, :source_identity_hash,
                    ARRAY[:content_hash], 'complete', now(), now()
                )
                """
            ),
            {
                "forget_id": uuid4(),
                "deployment_id": _DEPLOYMENT_ID,
                "doc_id": uuid4(),
                "manifest_hash": "a" * 64,
                "source_identity_hash": forgotten_identity_hash,
                "content_hash": forgotten_content_hash,
            },
        )
    catalog = ForgetCatalog(engine=database_engine)

    with pytest.raises(ForgottenSourceError):
        catalog.guard_ingest(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="drive",
            source_ref="file-1",
            content_hash="c" * 64,
        )
    with pytest.raises(ForgottenSourceError):
        catalog.guard_ingest(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="drive",
            source_ref="different-file",
            content_hash=forgotten_content_hash,
        )
    catalog.guard_ingest(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="drive",
        source_ref="different-file",
        content_hash="c" * 64,
    )
