"""PostgreSQL acceptance for managed text admission and receipt replay."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import cast
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model.metering import ManagedDocumentVersionOutcomeV2
from rememberstack.model.metering import ManagedIngestMeasurementV2
from rememberstack.model.metering import ManagedMeterScope
from rememberstack.model.metering import MeterAdmissionResult
from rememberstack.ports.metering import MeterReceiptPort
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine.managed_metering import ManagedMeterCatalog
from rememberstack.spine.readiness import PipelineReadinessCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import UploadIngestor

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("90000000-0000-0000-0000-000000000045")
_ORG_ID = UUID("a0000000-0000-0000-0000-000000000045")
_PROJECT_ID = UUID("b0000000-0000-0000-0000-000000000045")


class AllowIngest:
    """Permit test ingress after the production D74 call site is exercised."""

    def guard_ingest(self, **kwargs: object) -> None:
        """Accept the already bounded synthetic fixture."""
        del kwargs


@dataclass(frozen=True, slots=True)
class FakeStage:
    """Minimal terminal stage shape consumed by the meter catalog."""

    status: str
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class FakeVersion:
    """Minimal version readiness shape consumed by the meter catalog."""

    ready: bool
    stages: tuple[FakeStage, ...]


@dataclass(frozen=True, slots=True)
class FakeReport:
    """Minimal readiness report shape consumed by the meter catalog."""

    versions: tuple[FakeVersion, ...]


class ControlledReadiness:
    """Keep versions pending until the test explicitly marks them complete."""

    def __init__(self) -> None:
        """Start with no ready versions."""
        self.ready: set[UUID] = set()

    def inspect(self, *, version_ids: tuple[UUID, ...], **kwargs: object) -> FakeReport:
        """Return exact completion state for the requested single version."""
        del kwargs
        version_id = version_ids[0]
        is_ready = version_id in self.ready
        return FakeReport(
            versions=(
                FakeVersion(
                    ready=is_ready,
                    stages=(
                        FakeStage(
                            status="succeeded" if is_ready else "pending",
                            finished_at=(
                                datetime(2026, 9, 2, 2, tzinfo=timezone.utc)
                                if is_ready
                                else None
                            ),
                        ),
                    ),
                ),
            )
        )


class ControlledReceipts(MeterReceiptPort):
    """Record wire receipts and provide stable deployment admission decisions."""

    def __init__(self) -> None:
        """Start approved, with an opt-in one-shot parking decision."""
        self.park_next = False
        self.measurements: list[ManagedIngestMeasurementV2] = []
        self.outcomes: list[ManagedDocumentVersionOutcomeV2] = []
        self.holds: dict[UUID, tuple[UUID, UUID]] = {}

    def admit_measurement(
        self, *, measurement: ManagedIngestMeasurementV2
    ) -> MeterAdmissionResult:
        """Return no-op, one-shot parked, or an idempotent two-hold approval."""
        self.measurements.append(measurement)
        if measurement.document_version_disposition == "no_op":
            return MeterAdmissionResult(decision="no_op")
        if self.park_next:
            self.park_next = False
            return MeterAdmissionResult(
                decision="parked", reason_code="INSUFFICIENT_BALANCE"
            )
        processing, storage = self.holds.setdefault(
            measurement.measurement_id, (uuid4(), uuid4())
        )
        return MeterAdmissionResult(
            decision="approved",
            processing_hold_id=processing,
            storage_growth_hold_id=storage,
        )

    def acknowledge_outcome(self, *, outcome: ManagedDocumentVersionOutcomeV2) -> None:
        """Capture the terminal receipt as the durable remote acknowledgement."""
        self.outcomes.append(outcome)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Migrate an isolated PostgreSQL database to the managed-metering head."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for SQL proofs")
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
def bootstrapped(database_engine: Engine) -> None:
    """Reset and bootstrap the exact deployment used by each proof."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="managed-metering",
            name="Managed metering",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def _work_count(*, engine: Engine, version_id: UUID) -> int:
    """Count convert work for one version without relying on queue delivery."""
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM processing_state "
                    "WHERE target_id = :version_id AND stage::text = 'convert'"
                ),
                {"version_id": version_id},
            ).scalar_one()
        )


def _version_status(*, engine: Engine, version_id: UUID) -> str:
    """Read the durable pre-dispatch version state."""
    with engine.connect() as connection:
        return str(
            connection.execute(
                text("SELECT status::text FROM document_versions WHERE version_id=:id"),
                {"id": version_id},
            ).scalar_one()
        )


def test_managed_ingest_waits_for_two_holds_and_replays_terminal_outcomes(
    database_engine: Engine, tmp_path: Path
) -> None:
    """Park, approve, no-op, succeed, and fail through the real SQL spine."""
    receipts = ControlledReceipts()
    readiness = ControlledReadiness()
    meter = ManagedMeterCatalog(
        engine=database_engine,
        receipts=receipts,
        readiness=cast(PipelineReadinessCatalog, readiness),
    )
    ingestor = UploadIngestor(
        catalog=DocumentCatalog(engine=database_engine),
        raw_store=LocalFSObjectStore(root=tmp_path / "raw"),
        admission=AllowIngest(),
        meter_scope=ManagedMeterScope(org_id=_ORG_ID, project_id=_PROJECT_ID),
    )

    receipts.park_next = True
    first = ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="first.txt", mime="text/plain", content=b"hello  world"
        ),
    )
    assert first.created is True and first.processing_admission == "pending"
    assert _version_status(engine=database_engine, version_id=first.version_id) == (
        "ingesting"
    )
    assert _work_count(engine=database_engine, version_id=first.version_id) == 0
    parked = meter.drain_once()
    assert parked.measurements_parked == 1
    assert _work_count(engine=database_engine, version_id=first.version_id) == 0

    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE managed_ingest_measurements SET next_attempt_at = NULL "
                "WHERE version_id = :version_id"
            ),
            {"version_id": first.version_id},
        )
    approved = meter.drain_once()
    assert approved.measurements_accepted == 1
    assert _version_status(engine=database_engine, version_id=first.version_id) == (
        "converting"
    )
    assert _work_count(engine=database_engine, version_id=first.version_id) == 1

    readiness.ready.add(first.version_id)
    succeeded = meter.drain_once()
    assert succeeded.outcomes_created == 1
    assert succeeded.outcomes_accepted == 1
    assert receipts.outcomes[-1].outcome == "succeeded"
    assert receipts.outcomes[-1].document_version_id == str(first.version_id)
    assert receipts.outcomes[-1].version_commit_sequence == 1

    noop = ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="first-again.txt", mime="text/plain", content=b"hello  world"
        ),
    )
    assert noop.created is False
    no_op_result = meter.drain_once()
    assert no_op_result.measurements_accepted == 1
    assert no_op_result.outcomes_accepted == 1
    assert receipts.outcomes[-1].outcome == "no_op"
    assert _work_count(engine=database_engine, version_id=noop.version_id) == 1

    failed = ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="failed.txt", mime="text/plain", content=b"different text"
        ),
    )
    assert meter.drain_once().measurements_accepted == 1
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET status = 'failed' "
                "WHERE version_id = :version_id"
            ),
            {"version_id": failed.version_id},
        )
    failure = meter.drain_once()
    assert failure.outcomes_created == 1
    assert failure.outcomes_accepted == 1
    assert receipts.outcomes[-1].outcome == "failed"
    assert receipts.outcomes[-1].reason_code == "pipeline_terminal_failure"

    with database_engine.connect() as connection:
        leaked = connection.execute(
            text(
                "SELECT count(*) FROM managed_ingest_measurements "
                "WHERE opaque_lineage_id LIKE '%hello%' "
                "OR opaque_source_version_id LIKE '%hello%'"
            )
        ).scalar_one()
    assert leaked == 0
