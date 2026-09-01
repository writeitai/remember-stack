"""PostgreSQL acceptance for managed text admission and receipt replay."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import cast
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid4
from uuid import uuid5

from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model import ObjectKey
from rememberstack.model.metering import ManagedDocumentVersionOutcomeV2
from rememberstack.model.metering import ManagedIngestMeasurementV2
from rememberstack.model.metering import ManagedMeterScope
from rememberstack.model.metering import MeterAdmissionResult
from rememberstack.model.metering import MeterReceiptConflict
from rememberstack.ports.metering import MeterReceiptPort
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import managed_metering as managed_metering_module
from rememberstack.spine.managed_metering import ManagedMeterCatalog
from rememberstack.spine.managed_metering import MeterDrainResult
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
        self.stage_status: dict[UUID, str] = {}

    def inspect(self, *, version_ids: tuple[UUID, ...], **kwargs: object) -> FakeReport:
        """Return exact completion state for the requested single version."""
        del kwargs
        version_id = version_ids[0]
        is_ready = version_id in self.ready
        status = self.stage_status.get(
            version_id, "succeeded" if is_ready else "pending"
        )
        return FakeReport(
            versions=(
                FakeVersion(
                    ready=is_ready,
                    stages=(
                        FakeStage(
                            status=status,
                            finished_at=(
                                datetime(2026, 9, 2, 2, tzinfo=timezone.utc)
                                if status in {"succeeded", "failed", "dead_letter"}
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
        self.conflict_next = False
        self.measurements: list[ManagedIngestMeasurementV2] = []
        self.outcomes: list[ManagedDocumentVersionOutcomeV2] = []
        self.holds: dict[UUID, tuple[UUID, UUID]] = {}

    def admit_measurement(
        self, *, measurement: ManagedIngestMeasurementV2
    ) -> MeterAdmissionResult:
        """Return no-op, one-shot parked, or an idempotent two-hold approval."""
        self.measurements.append(measurement)
        if self.conflict_next:
            self.conflict_next = False
            raise MeterReceiptConflict("synthetic changed receipt")
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
    raw_store = LocalFSObjectStore(root=tmp_path / "raw")
    meter = ManagedMeterCatalog(
        engine=database_engine,
        receipts=receipts,
        readiness=cast(PipelineReadinessCatalog, readiness),
        raw_store=raw_store,
    )
    ingestor = UploadIngestor(
        catalog=DocumentCatalog(engine=database_engine),
        raw_store=raw_store,
        admission=AllowIngest(),
        meter_scope=ManagedMeterScope(
            org_id=_ORG_ID,
            project_id=_PROJECT_ID,
            identity_key=SecretStr("umc_mik_abcdefghijklmnopqrstuvwxyz0123456789ABCD"),
        ),
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
    with database_engine.connect() as connection:
        raw_uri = str(
            connection.execute(
                text(
                    "SELECT c.raw_uri FROM content_objects c "
                    "JOIN document_versions v USING (deployment_id, content_hash) "
                    "WHERE v.version_id = :version_id"
                ),
                {"version_id": first.version_id},
            ).scalar_one()
        )
    with pytest.raises(FileNotFoundError):
        raw_store.read_bytes(key=ObjectKey(raw_uri))
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
    assert raw_store.read_bytes(key=ObjectKey(raw_uri)) == b"hello  world"

    readiness.ready.add(first.version_id)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE managed_ingest_measurements "
                "SET outcome_next_attempt_at = NULL WHERE version_id = :version_id"
            ),
            {"version_id": first.version_id},
        )
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
    repeated_noop = ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="first-third.txt", mime="text/plain", content=b"hello  world"
        ),
    )
    assert repeated_noop.created is False
    assert meter.drain_once() == MeterDrainResult()

    failed = ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="failed.txt", mime="text/plain", content=b"different text"
        ),
    )
    assert meter.drain_once().measurements_accepted == 1

    retried = ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="retried.txt", mime="text/plain", content=b"retry this work"
        ),
    )
    assert meter.drain_once().measurements_accepted == 1
    readiness.stage_status[retried.version_id] = "failed"
    transient = meter.drain_once()
    assert transient.outcomes_created == 0
    assert all(
        outcome.document_version_id != str(retried.version_id)
        for outcome in receipts.outcomes
    )
    readiness.stage_status[retried.version_id] = "dead_letter"
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE managed_ingest_measurements "
                "SET outcome_next_attempt_at = NULL WHERE version_id = :version_id"
            ),
            {"version_id": retried.version_id},
        )
    terminal = meter.drain_once()
    assert terminal.outcomes_created == 1
    assert terminal.outcomes_accepted == 1
    assert receipts.outcomes[-1].document_version_id == str(retried.version_id)
    assert receipts.outcomes[-1].outcome == "failed"
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

    forgotten = ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="forgotten.txt", mime="text/plain", content=b"forget this"
        ),
    )
    assert meter.drain_once().measurements_accepted == 1
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET status = 'deleted' "
                "WHERE version_id = :version_id"
            ),
            {"version_id": forgotten.version_id},
        )
    forgotten_result = meter.drain_once()
    assert forgotten_result.outcomes_created == 1
    assert forgotten_result.outcomes_accepted == 1
    assert receipts.outcomes[-1].outcome == "failed"
    assert receipts.outcomes[-1].reason_code == "source_forgotten"

    with database_engine.connect() as connection:
        identities = (
            connection.execute(
                text(
                    "SELECT opaque_lineage_id, opaque_source_version_id, doc_id "
                    "FROM managed_ingest_measurements "
                    "WHERE version_id = :version_id "
                    "AND document_version_disposition = 'new_version'"
                ),
                {"version_id": first.version_id},
            )
            .mappings()
            .one()
        )
    public_lineage_guess = str(
        uuid5(
            NAMESPACE_URL,
            f"rememberstack:meter-lineage:{_DEPLOYMENT_ID}:{identities['doc_id']}",
        )
    )
    public_version_guess = str(
        uuid5(
            NAMESPACE_URL,
            f"rememberstack:meter-source-version:{_DEPLOYMENT_ID}:{first.version_id}",
        )
    )
    assert identities["opaque_lineage_id"] != public_lineage_guess
    assert identities["opaque_source_version_id"] != public_version_guess

    receipts.conflict_next = True
    quarantined = ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="conflict.txt", mime="text/plain", content=b"conflicting receipt"
        ),
    )
    assert meter.drain_once().measurements_parked == 1
    with database_engine.connect() as connection:
        state = connection.execute(
            text(
                "SELECT delivery_state FROM managed_ingest_measurements "
                "WHERE version_id = :version_id"
            ),
            {"version_id": quarantined.version_id},
        ).scalar_one()
    assert state == "quarantined"
    assert meter.drain_once().measurements_parked == 0
    healed = ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="conflict-retry.txt",
            mime="text/plain",
            content=b"conflicting receipt",
        ),
    )
    assert healed.created is False and healed.version_id == quarantined.version_id
    assert meter.drain_once().measurements_accepted == 1

    cancelled = ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="cancelled.txt", mime="text/plain", content=b"cancel before retry"
        ),
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE managed_ingest_measurements "
                "SET staged_content = NULL, delivery_state = 'cancelled', "
                "decision_reason = 'source_forgotten' "
                "WHERE version_id = :version_id"
            ),
            {"version_id": cancelled.version_id},
        )
        cancelled_measurement_id = UUID(
            str(
                connection.execute(
                    text(
                        "SELECT measurement_id FROM managed_ingest_measurements "
                        "WHERE version_id = :version_id"
                    ),
                    {"version_id": cancelled.version_id},
                ).scalar_one()
            )
        )
    meter._record_measurement_retry(
        measurement_id=cancelled_measurement_id, reason_code="control_plane_unavailable"
    )
    with database_engine.connect() as connection:
        cancelled_state = connection.execute(
            text(
                "SELECT delivery_state, decision_reason "
                "FROM managed_ingest_measurements WHERE version_id = :version_id"
            ),
            {"version_id": cancelled.version_id},
        ).one()
    assert tuple(cancelled_state) == ("cancelled", "source_forgotten")


def test_due_measurement_scan_never_loads_staged_source_bytes() -> None:
    """The bounded replay scan carries only the content-free receipt envelope."""
    statement = str(managed_metering_module._DUE_MEASUREMENTS)
    assert "SELECT *" not in statement
    assert "staged_content" not in statement
