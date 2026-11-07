"""PostgreSQL acceptance for the managed meter: measure in convert, park, release."""

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
from rememberstack.core import ConversionRouter
from rememberstack.core import Converter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.core.metering import normalized_character_count
from rememberstack.model import ConversionResult
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import RunResultOutcome
from rememberstack.model.metering import ManagedDocumentVersionOutcome
from rememberstack.model.metering import ManagedIngestMeasurement
from rememberstack.model.metering import ManagedMeterScope
from rememberstack.model.metering import MeterAdmissionResult
from rememberstack.model.metering import MeterReceiptConflict
from rememberstack.ports.metering import MeterReceiptPort
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.managed_metering import ManagedMeterCatalog
from rememberstack.spine.managed_metering import MeterDrainResult
from rememberstack.spine.readiness import PipelineReadinessCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import ConvertHandler
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker
from tests.database_reset import reset_database

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
        self.measurements: list[ManagedIngestMeasurement] = []
        self.outcomes: list[ManagedDocumentVersionOutcome] = []
        self.holds: dict[UUID, tuple[UUID, UUID]] = {}

    def admit_measurement(
        self, *, measurement: ManagedIngestMeasurement
    ) -> MeterAdmissionResult:
        """Return one-shot parked or an idempotent two-hold approval."""
        self.measurements.append(measurement)
        if self.conflict_next:
            self.conflict_next = False
            raise MeterReceiptConflict("synthetic changed receipt")
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

    def acknowledge_outcome(self, *, outcome: ManagedDocumentVersionOutcome) -> None:
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
    reset_database(config=config)
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


def _convert_row(*, engine: Engine, version_id: UUID) -> dict[str, object]:
    """The version's convert work row: its status and defer reason."""
    with engine.connect() as connection:
        return dict(
            connection.execute(
                text(
                    "SELECT status::text AS status, defer_reason::text AS defer_reason,"
                    " attempts FROM processing_state"
                    " WHERE target_id = :version_id AND stage::text = 'convert'"
                ),
                {"version_id": version_id},
            )
            .mappings()
            .one()
        )


def _measurement(*, engine: Engine, version_id: UUID) -> dict[str, object] | None:
    """The version's measurement row, or None."""
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT cost_class, native_quantity, unit, format_family,"
                    " canonical_source_bytes, processing_profile_id, delivery_state"
                    " FROM managed_ingest_measurements WHERE version_id = :version_id"
                ),
                {"version_id": version_id},
            )
            .mappings()
            .one_or_none()
        )
    return None if row is None else dict(row)


def _structure_count(*, engine: Engine, version_id: UUID) -> int:
    """Count structure work chained for one version."""
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM processing_state "
                    "WHERE target_id = :version_id AND stage::text = 'structure'"
                ),
                {"version_id": version_id},
            ).scalar_one()
        )


def _due(*, engine: Engine) -> None:
    """Make every backed-off measurement and outcome check due now."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE managed_ingest_measurements SET next_attempt_at = NULL,"
                " outcome_next_attempt_at = NULL"
            )
        )


_SCOPE = ManagedMeterScope(
    org_id=_ORG_ID,
    project_id=_PROJECT_ID,
    identity_key=SecretStr("umc_mik_abcdefghijklmnopqrstuvwxyz0123456789ABCD"),
)


class FakePdf:
    """A ``pdf`` route that counts pages and records when it is asked to OCR."""

    def __init__(self, *, pages: int = 3) -> None:
        """Start with no OCR calls."""
        self.pages = pages
        self.ocr_calls = 0

    @property
    def name(self) -> str:
        """The PDF route name."""
        return "pdf"

    @property
    def version(self) -> str:
        """A fixed test version."""
        return "fake-pdf-v1"

    def count_pages(self, *, content: bytes) -> int:
        """The page count, without OCR."""
        del content
        return self.pages

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """The 'OCR' reading; counted so a test can prove it waited."""
        del content, mime
        self.ocr_calls += 1
        return MarkdownPassthroughConverter().convert(
            content=b"# Scanned\n\nocr text", mime="text/markdown"
        )


class NamedConverter:
    """A passthrough reading under another route name."""

    def __init__(self, *, name: str) -> None:
        """Bind the route name."""
        self._name = name
        self.calls = 0

    @property
    def name(self) -> str:
        """The route name under test."""
        return self._name

    @property
    def version(self) -> str:
        """A fixed test version."""
        return "named-v1"

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """Read the bytes as Markdown."""
        del mime
        self.calls += 1
        return MarkdownPassthroughConverter().convert(
            content=content, mime="text/markdown"
        )


@dataclass
class Rig:
    """A metered deployment: ingest, the convert worker and the meter drain."""

    engine: Engine
    receipts: ControlledReceipts
    readiness: ControlledReadiness
    raw_store: LocalFSObjectStore
    ingestor: UploadIngestor
    meter: ManagedMeterCatalog
    worker: Worker
    ledger: WorkLedger

    def convert(self) -> RunResultOutcome:
        """Run one convert attempt."""
        return self.worker.run_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.CONVERT,
            lane=ProcessingLane.STEADY,
        ).outcome

    def upload(self, *, name: str, mime: str, content: bytes) -> UUID:
        """Ingest one file and return its version."""
        return self.ingestor.ingest(
            deployment_id=_DEPLOYMENT_ID,
            upload=DocumentUpload(filename=name, mime=mime, content=content),
        ).version_id


def _rig(*, engine: Engine, tmp_path: Path, routes: dict[str, Converter]) -> Rig:
    """Compose a metered deployment over the real SQL spine."""
    receipts = ControlledReceipts()
    readiness = ControlledReadiness()
    raw_store = LocalFSObjectStore(root=tmp_path / "raw")
    catalog = DocumentCatalog(engine=engine)
    ledger = WorkLedger(engine=engine, settings=WorkLedgerSettings())
    registry = HandlerRegistry()
    registry.register(
        stage=PipelineStage.CONVERT,
        handler=ConvertHandler(
            catalog=catalog,
            raw_store=raw_store,
            artifact_store=LocalFSObjectStore(root=tmp_path / "artifacts"),
            router=ConversionRouter(routes=routes),
            meter_scope=_SCOPE,
        ),
    )
    return Rig(
        engine=engine,
        receipts=receipts,
        readiness=readiness,
        raw_store=raw_store,
        ingestor=UploadIngestor(
            catalog=catalog,
            raw_store=raw_store,
            admission=AllowIngest(),
            routable_mimes=frozenset(routes),
            meter_scope=_SCOPE,
        ),
        meter=ManagedMeterCatalog(
            engine=engine,
            receipts=receipts,
            readiness=cast(PipelineReadinessCatalog, readiness),
        ),
        worker=Worker(ledger=ledger, registry=registry),
        ledger=ledger,
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


def test_text_is_read_measured_and_parked_until_both_holds_exist(
    database_engine: Engine, tmp_path: Path
) -> None:
    """Read locally, count document.md, park; approval chains structure once."""
    rig = _rig(
        engine=database_engine,
        tmp_path=tmp_path,
        routes={"text/plain": MarkdownPassthroughConverter()},
    )
    content = b"hello  world\n\nagain"
    version = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(filename="first.txt", mime="text/plain", content=content),
    )
    assert version.created is True and version.processing_admission == "pending"
    assert _version_status(engine=database_engine, version_id=version.version_id) == (
        "converting"
    )
    source = DocumentCatalog(engine=database_engine).convert_source(
        version_id=version.version_id
    )
    assert rig.raw_store.read_bytes(key=ObjectKey(source.raw_uri)) == content

    assert rig.convert() is RunResultOutcome.ADMISSION_PARKED
    row = _convert_row(engine=database_engine, version_id=version.version_id)
    assert row == {"status": "pending", "defer_reason": "admission", "attempts": 0}
    measured = _measurement(engine=database_engine, version_id=version.version_id)
    assert measured is not None
    assert measured["cost_class"] == "doc-text"
    assert measured["unit"] == "normalized_character"
    assert measured["native_quantity"] == normalized_character_count(
        text=MarkdownPassthroughConverter()
        .convert(content=content, mime="text/plain")
        .document_md
    )
    assert measured["canonical_source_bytes"] == len(content)
    assert measured["processing_profile_id"] == "doc-text-standard-v1"
    assert _structure_count(engine=database_engine, version_id=version.version_id) == 0
    assert rig.convert() is RunResultOutcome.NO_WORK

    rig.receipts.park_next = True
    assert rig.meter.drain_once().measurements_parked == 1
    assert _convert_row(engine=database_engine, version_id=version.version_id)[
        "defer_reason"
    ] == ("admission")
    _due(engine=database_engine)
    assert rig.meter.drain_once().measurements_accepted == 1
    assert (
        _convert_row(engine=database_engine, version_id=version.version_id)[
            "defer_reason"
        ]
        is None
    )
    sent = rig.receipts.measurements[-1]
    assert (sent.cost_class, sent.unit, sent.format_family) == (
        "doc-text",
        "normalized_character",
        "text",
    )

    assert rig.convert() is RunResultOutcome.SUCCEEDED
    assert _structure_count(engine=database_engine, version_id=version.version_id) == 1

    rig.readiness.ready.add(version.version_id)
    _due(engine=database_engine)
    succeeded = rig.meter.drain_once()
    assert succeeded.outcomes_created == 1 and succeeded.outcomes_accepted == 1
    assert rig.receipts.outcomes[-1].outcome == "succeeded"
    assert rig.receipts.outcomes[-1].version_commit_sequence == 1

    again = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(filename="again.txt", mime="text/plain", content=content),
    )
    assert again.created is False
    assert rig.meter.drain_once() == MeterDrainResult()
    assert len(rig.receipts.measurements) == 2


def test_a_pdf_is_counted_before_ocr_and_ocr_waits_for_approval(
    database_engine: Engine, tmp_path: Path
) -> None:
    """Every PDF page is one doc-scan unit, counted from the page tree first."""
    pdf = FakePdf(pages=3)
    rig = _rig(
        engine=database_engine, tmp_path=tmp_path, routes={"application/pdf": pdf}
    )
    content = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF"
    version_id = rig.upload(name="scan.pdf", mime="application/pdf", content=content)
    assert rig.convert() is RunResultOutcome.ADMISSION_PARKED
    assert pdf.ocr_calls == 0
    measured = _measurement(engine=database_engine, version_id=version_id)
    assert measured is not None
    assert (measured["cost_class"], measured["native_quantity"], measured["unit"]) == (
        "doc-scan",
        3,
        "page",
    )
    assert measured["processing_profile_id"] == "doc-scan-standard-v1"
    assert measured["format_family"] == "pdf"
    assert rig.meter.drain_once().measurements_accepted == 1
    assert rig.convert() is RunResultOutcome.SUCCEEDED
    assert pdf.ocr_calls == 1
    assert _structure_count(engine=database_engine, version_id=version_id) == 1


def test_a_card_is_free_and_an_unmetered_route_is_refused(
    database_engine: Engine, tmp_path: Path
) -> None:
    """A card sends no measurement; a provider route without a class never runs."""
    vision = NamedConverter(name="image_ocr_description")
    rig = _rig(
        engine=database_engine,
        tmp_path=tmp_path,
        routes={"text/plain": NamedConverter(name="card"), "image/png": vision},
    )
    card = rig.upload(name="notes.txt", mime="text/plain", content=b"carded")
    assert rig.convert() is RunResultOutcome.SUCCEEDED
    assert _measurement(engine=database_engine, version_id=card) is None
    assert _structure_count(engine=database_engine, version_id=card) == 1

    image = rig.upload(
        name="photo.png", mime="image/png", content=b"\x89PNG\r\n\x1a\nimage"
    )
    assert rig.convert() is RunResultOutcome.DEAD_LETTERED
    assert vision.calls == 0
    assert _measurement(engine=database_engine, version_id=image) is None
    assert _version_status(engine=database_engine, version_id=image) == "failed"


def test_an_approval_that_wins_the_race_with_the_park_releases_the_claim(
    database_engine: Engine, tmp_path: Path
) -> None:
    """The park reads the approval under the lock it takes, so work never strands."""
    from rememberstack.model import ClaimedWork
    from rememberstack.model.metering import ManagedMeasurementDraft

    rig = _rig(
        engine=database_engine,
        tmp_path=tmp_path,
        routes={"text/plain": MarkdownPassthroughConverter()},
    )
    version_id = rig.upload(name="race.txt", mime="text/plain", content=b"race")
    claimed = rig.ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.CONVERT,
        lane=ProcessingLane.STEADY,
    )
    assert isinstance(claimed, ClaimedWork)
    catalog = DocumentCatalog(engine=database_engine)
    source = catalog.convert_source(version_id=version_id)
    catalog.record_measurement(
        draft=ManagedMeasurementDraft(
            measurement_id=uuid4(),
            ingest_attempt_id="race",
            org_id=_ORG_ID,
            project_id=_PROJECT_ID,
            format_family="text",
            cost_class="doc-text",
            native_quantity=4,
            canonical_source_bytes=4,
            classifier_version="test",
            measurement_algorithm_version="test",
            processing_profile_id="doc-text-standard-v1",
            measured_at=datetime.now(timezone.utc),
            identity_key=_SCOPE.identity_key,
        ),
        deployment_id=_DEPLOYMENT_ID,
        doc_id=source.doc_id,
        version_id=version_id,
        convert_component_version=claimed.component_version,
        lane="steady",
    )
    assert rig.meter.drain_once().measurements_accepted == 1
    rig.ledger.park_for_admission(
        processing_id=claimed.processing_id, attempt=claimed.attempt
    )
    assert _convert_row(engine=database_engine, version_id=version_id) == {
        "status": "pending",
        "defer_reason": None,
        "attempts": 0,
    }


def test_failed_forgotten_and_conflicting_measurements(
    database_engine: Engine, tmp_path: Path
) -> None:
    """Terminal failure and forgetting report failed; a conflict quarantines."""
    rig = _rig(
        engine=database_engine,
        tmp_path=tmp_path,
        routes={"text/plain": MarkdownPassthroughConverter()},
    )
    retried = rig.upload(name="retried.txt", mime="text/plain", content=b"retry")
    assert rig.convert() is RunResultOutcome.ADMISSION_PARKED
    assert rig.meter.drain_once().measurements_accepted == 1
    assert rig.convert() is RunResultOutcome.SUCCEEDED
    rig.readiness.stage_status[retried] = "failed"
    assert rig.meter.drain_once().outcomes_created == 0
    rig.readiness.stage_status[retried] = "dead_letter"
    _due(engine=database_engine)
    terminal = rig.meter.drain_once()
    assert terminal.outcomes_created == 1 and terminal.outcomes_accepted == 1
    assert rig.receipts.outcomes[-1].outcome == "failed"
    assert rig.receipts.outcomes[-1].reason_code == "pipeline_terminal_failure"

    forgotten = rig.upload(name="forgotten.txt", mime="text/plain", content=b"forget")
    assert rig.convert() is RunResultOutcome.ADMISSION_PARKED
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET status = 'deleted' WHERE version_id = :v"
            ),
            {"v": forgotten},
        )
    result = rig.meter.drain_once()
    assert result.measurements_accepted == 1 and result.outcomes_accepted == 1
    assert rig.receipts.outcomes[-1].reason_code == "source_forgotten"

    with database_engine.connect() as connection:
        identities = (
            connection.execute(
                text(
                    "SELECT opaque_lineage_id, opaque_source_version_id, doc_id "
                    "FROM managed_ingest_measurements WHERE version_id = :v"
                ),
                {"v": retried},
            )
            .mappings()
            .one()
        )
    assert identities["opaque_lineage_id"] != str(
        uuid5(
            NAMESPACE_URL,
            f"rememberstack:meter-lineage:{_DEPLOYMENT_ID}:{identities['doc_id']}",
        )
    )
    assert identities["opaque_source_version_id"] != str(
        uuid5(
            NAMESPACE_URL,
            f"rememberstack:meter-source-version:{_DEPLOYMENT_ID}:{retried}",
        )
    )

    conflict = rig.upload(name="conflict.txt", mime="text/plain", content=b"conflict")
    assert rig.convert() is RunResultOutcome.ADMISSION_PARKED
    rig.receipts.conflict_next = True
    assert rig.meter.drain_once().measurements_parked == 1
    measured = _measurement(engine=database_engine, version_id=conflict)
    assert measured is not None and measured["delivery_state"] == "quarantined"
    assert rig.meter.drain_once().measurements_parked == 0
    assert _convert_row(engine=database_engine, version_id=conflict)[
        "defer_reason"
    ] == ("admission")


def test_a_stored_object_is_metered_like_an_upload(
    database_engine: Engine, tmp_path: Path
) -> None:
    """D147: a directly uploaded object is read, measured and parked like ingest."""
    rig = _rig(
        engine=database_engine,
        tmp_path=tmp_path,
        routes={"text/plain": MarkdownPassthroughConverter()},
    )
    content = b"stored  hello\n\nworld"
    key = ObjectKey("uploads/meteredmetered0001")
    rig.raw_store.write_bytes(key=key, content=content, storage_class="cold")
    version = rig.ingestor.ingest_stored(
        deployment_id=_DEPLOYMENT_ID,
        key=key,
        size=len(content),
        mime="text/plain",
        filename="stored.txt",
    )
    assert version.created is True and version.processing_admission == "pending"
    assert rig.convert() is RunResultOutcome.ADMISSION_PARKED
    measured = _measurement(engine=database_engine, version_id=version.version_id)
    assert measured is not None
    assert measured["canonical_source_bytes"] == len(content)
    assert rig.raw_store.read_bytes(key=key) == content


class _PresigningStore(LocalFSObjectStore):
    """A local store that lends objects by a fake URL (D147)."""

    def presign_get(self, *, key: ObjectKey, ttl_s: int) -> str:
        """A URL naming the key."""
        return f"https://raw.invalid/{key.root}?ttl={ttl_s}"


class FakeUrlPdf(FakePdf):
    """A by-URL ``pdf`` route: it counts and converts from the URL only."""

    accepts_url = True
    url_ttl_s = 60

    def __init__(self, *, pages: int = 5) -> None:
        """Record the URLs it was handed."""
        super().__init__(pages=pages)
        self.counted_urls: list[str] = []

    def count_pages(self, *, content: bytes) -> int:
        """Never used: the worker must not read a by-URL object."""
        raise AssertionError("a by-URL route was asked to count bytes")

    def count_pages_from_url(self, *, url: str, size: int) -> int:
        """Count from the URL."""
        del size
        self.counted_urls.append(url)
        return self.pages

    def convert_from_url(
        self, *, url: str, size: int, mime: str, hints: object
    ) -> ConversionResult:
        """The 'OCR' reading by URL."""
        del url, size, hints
        return self.convert(content=b"", mime=mime)


def test_a_by_url_pdf_is_counted_from_its_url(
    database_engine: Engine, tmp_path: Path
) -> None:
    """With a presigning store the worker never reads the PDF to count it."""
    pdf = FakeUrlPdf(pages=5)
    rig = _rig(
        engine=database_engine, tmp_path=tmp_path, routes={"application/pdf": pdf}
    )
    store = _PresigningStore(root=tmp_path / "raw")
    handler = ConvertHandler(
        catalog=DocumentCatalog(engine=database_engine),
        raw_store=store,
        artifact_store=LocalFSObjectStore(root=tmp_path / "artifacts"),
        router=ConversionRouter(routes={"application/pdf": pdf}),
        meter_scope=_SCOPE,
    )
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.CONVERT, handler=handler)
    rig.worker = Worker(ledger=rig.ledger, registry=registry)
    version_id = rig.upload(
        name="big.pdf",
        mime="application/pdf",
        content=b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF",
    )
    assert rig.convert() is RunResultOutcome.ADMISSION_PARKED
    assert len(pdf.counted_urls) == 1 and pdf.ocr_calls == 0
    measured = _measurement(engine=database_engine, version_id=version_id)
    assert measured is not None and measured["native_quantity"] == 5
    assert rig.meter.drain_once().measurements_accepted == 1
    assert rig.convert() is RunResultOutcome.SUCCEEDED
    assert pdf.ocr_calls == 1
