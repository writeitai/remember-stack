"""Supplied references, the crossref worker and ``document_references`` on PostgreSQL.

D140 §6: documents run through the real E0 chain (ingest → convert →
structure → chunk) over a local object store; reference sets go through the
real PUT path, the crossref sub-worker writes the rows, and reads run the
real ``document_references`` statements. The running example is the design's:
the Travel Policy's approvals section refers to the Expense Policy.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import UTC
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
from sqlalchemy.exc import IntegrityError

from remember.models import DocumentReferencesPage
from remember.models import DocumentReferencesRequest
from remember.models import EffectivePeriodInput
from remember.models import SectionHistoryRequest
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.core import ConversionRouter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.model import ChunkNotFoundError
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentUpload
from rememberstack.model import DocumentVersionNotFoundError
from rememberstack.model import IngestedVersion
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ReferenceBodyError
from rememberstack.model import RunResultOutcome
from rememberstack.spine import ChunkCatalog
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import document_references as references_module
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.document_references import DocumentReferences
from rememberstack.spine.effective_time import EffectiveTimeCatalog
from rememberstack.spine.references import ExtractedReference
from rememberstack.spine.references import ReferenceCatalog
from rememberstack.spine.section_history import SectionHistory
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import ChunkHandler
from rememberstack.workers import ConvertHandler
from rememberstack.workers import CrossrefHandler
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import StructureHandler
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker
from rememberstack.workers.e1 import ChunkerParams
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("64000000-0000-0000-0000-0000000d0140")

_TRAVEL = (
    "# Travel policy\n\nIntro.\n\n"
    "## Per-diem allowance {#per-diem}\n\nThe allowance is 40 per day.\n\n"
    "### Meals {#meals}\n\nMeals are included.\n\n"
    "## Approvals {#approvals}\n\nApprovals follow the expense policy.\n"
)
_EXPENSE_1 = (
    "# Expense policy\n\nIntro.\n\n"
    "## Approvals process {#approvals-process}\n\nManagers approve.\n\n"
    "## Limits {#limits}\n\nTen per receipt.\n"
)
_EXPENSE_2 = _EXPENSE_1.replace("Managers approve.", "Directors approve.")
_EXPENSE_3 = "# Expense policy\n\nIntro.\n\n## Limits {#limits}\n\nTwenty.\n"


def _day(year: int, month: int, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _ref(**overrides: object) -> dict[str, object]:
    reference: dict[str, object] = {
        "kind": "refers_to",
        "from_section_key": "approvals",
        "target": {
            "source_kind": "intranet",
            "source_ref": "policy/expense",
            "section_key": "approvals-process",
        },
    }
    reference.update(overrides)
    return reference


def _ndjson(*references: dict[str, object]) -> bytes:
    return "".join(json.dumps(item) + "\n" for item in references).encode()


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head over the integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for reference proofs")
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
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="references-test",
            name="Reference proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


class _HookedStore(LocalFSObjectStore):
    """An artifact store that runs one callback on the next read."""

    before_read: Callable[[], None] | None = None

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        hook, self.before_read = self.before_read, None
        if hook is not None:
            hook()
        return super().read_bytes(key=key)


class _Rig:
    """Ingest and process lineages; write and read references."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        self.engine = engine
        self.artifact_store = _HookedStore(root=root / "artifacts")
        raw_store = LocalFSObjectStore(root=root / "raw")
        catalog = DocumentCatalog(engine=engine)
        ledger = WorkLedger(
            engine=engine,
            settings=WorkLedgerSettings(
                retry_backoff_base_s=0.0, retry_backoff_max_s=0.0
            ),
        )
        routes = {"text/markdown": MarkdownPassthroughConverter()}
        self.ingestor = UploadIngestor(
            catalog=catalog,
            raw_store=raw_store,
            admission=ForgetCatalog(engine=engine),
            routable_mimes=frozenset(routes),
        )
        self.references = ReferenceCatalog(
            engine=engine, artifact_store=self.artifact_store
        )
        registry = HandlerRegistry()
        registry.register(
            stage=PipelineStage.CONVERT,
            handler=ConvertHandler(
                catalog=catalog,
                raw_store=raw_store,
                artifact_store=self.artifact_store,
                router=ConversionRouter(routes=routes),
            ),
        )
        registry.register(
            stage=PipelineStage.STRUCTURE,
            handler=StructureHandler(
                catalog=catalog, artifact_store=self.artifact_store
            ),
        )
        registry.register(
            stage=PipelineStage.CHUNK,
            handler=ChunkHandler(
                catalog=ChunkCatalog(engine=engine),
                artifact_store=self.artifact_store,
                params=ChunkerParams(),
            ),
        )
        registry.register(
            stage=PipelineStage.CROSSREF,
            handler=CrossrefHandler(references=self.references),
        )
        self.worker = Worker(ledger=ledger, registry=registry)
        self.reader = DocumentReferences(engine=engine)
        self.periods = EffectiveTimeCatalog(engine=engine)

    def ingest(
        self,
        *,
        ref: str,
        body: str,
        process: bool = True,
        version_key: str | None = None,
        effective_from: datetime | None = None,
        effective_until: datetime | None = None,
    ) -> IngestedVersion:
        ingested = self.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="intranet",
            source_ref=ref,
            upload=DocumentUpload(
                filename=f"{ref.rsplit('/', 1)[-1]}.md",
                mime="text/markdown",
                content=body.encode(),
                version_key=version_key,
                effective_from=effective_from,
                effective_until=effective_until,
            ),
            versioning_mode="snapshot",
            source_modified_at=None,
            source_version_ref=None,
            sync_cycle_id=None,
        )
        if process:
            self.drain(
                stages=(
                    PipelineStage.CONVERT,
                    PipelineStage.STRUCTURE,
                    PipelineStage.CHUNK,
                )
            )
        return ingested

    def drain(
        self,
        *,
        stages: tuple[PipelineStage, ...] = (
            PipelineStage.CONVERT,
            PipelineStage.STRUCTURE,
            PipelineStage.CHUNK,
            PipelineStage.CROSSREF,
        ),
    ) -> int:
        """Run the stages until none has work; returns the jobs run."""
        ran = 0
        while True:
            progressed = False
            for stage in stages:
                outcome = self.worker.run_one(
                    deployment_id=_DEPLOYMENT_ID,
                    stage=stage,
                    lane=ProcessingLane.STEADY,
                ).outcome
                if outcome is RunResultOutcome.NO_WORK:
                    continue
                assert outcome is RunResultOutcome.SUCCEEDED, (stage, outcome)
                progressed = True
                ran += 1
            if not progressed:
                return ran

    def put(self, *, version: IngestedVersion, body: bytes):
        return self.references.set_references(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=version.doc_id,
            version_id=version.version_id,
            body=body,
        )

    def generations(self, *, version: IngestedVersion):
        return self.references.reference_generations(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=version.doc_id,
            version_id=version.version_id,
        ).generations

    def read(self, **arguments: object) -> DocumentReferencesPage:
        return self.reader.document_references(
            deployment_id=_DEPLOYMENT_ID,
            request=DocumentReferencesRequest.model_validate(arguments),
        )

    def declare(
        self,
        *,
        version: IngestedVersion,
        start: datetime,
        until: datetime | None = None,
    ) -> None:
        self.periods.set_effective_periods(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=version.doc_id,
            version_id=version.version_id,
            periods=(
                EffectivePeriodInput(effective_from=start, effective_until=until),
            ),
        )

    def rows(self, *, version: IngestedVersion) -> list[dict[str, object]]:
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT x.*, g.status FROM document_crossrefs x"
                        " JOIN document_reference_generations g"
                        "   ON g.generation_id = x.generation_id"
                        " WHERE x.from_version_id = :v ORDER BY x.crossref_id"
                    ),
                    {"v": version.version_id},
                ).mappings()
            ]

    def execute(self, sql: str, **parameters: object) -> None:
        with self.engine.begin() as connection:
            connection.execute(text(sql), parameters)


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _Rig:
    return _Rig(engine=database_engine, root=tmp_path)


def _statuses(page: DocumentReferencesPage) -> list[str]:
    return [row.status for row in page.rows]


# --- generations -------------------------------------------------------------


def test_put_on_a_processing_version_activates_once_structure_exists(rig: _Rig) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL, process=False)
    put = rig.put(version=travel, body=_ndjson(_ref()))
    assert (put.outcome, put.generation.status) == ("created", "pending")
    assert rig.rows(version=travel) == []

    rig.drain()

    [generation] = rig.generations(version=travel)
    assert (generation.status, generation.item_count) == ("active", 1)
    [row] = rig.rows(version=travel)
    assert (row["from_section_key"], row["to_doc_id"], row["resolved"]) == (
        "approvals",
        None,
        False,
    )
    with rig.engine.connect() as connection:
        artifact = connection.execute(
            text("SELECT artifact_uri FROM document_reference_generations")
        ).scalar_one()
    assert artifact == (
        f"{travel.doc_id}/{travel.content_hash}/references/{generation.input_hash}.ndjson"
    )
    stored = rig.artifact_store.read_bytes(key=ObjectKey(artifact))
    assert json.loads(stored)["from_section_key"] == "approvals"


def test_set_a_b_a_reactivates_as_a_new_generation_reusing_the_artifact(
    rig: _Rig,
) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    body_a = _ndjson(_ref())
    body_b = _ndjson(_ref(), _ref(from_section_key="per-diem", kind="implements"))
    first = rig.put(version=travel, body=body_a)
    rig.drain()
    second = rig.put(version=travel, body=body_b)
    rig.drain()
    third = rig.put(version=travel, body=body_a)
    rig.drain()

    generations = {g.generation_id: g for g in rig.generations(version=travel)}
    assert [
        generations[g.generation.generation_id].status for g in (first, second, third)
    ] == ["superseded", "superseded", "active"]
    assert third.generation.generation_id != first.generation.generation_id
    assert third.generation.request_seq == 3
    assert third.generation.input_hash == first.generation.input_hash
    assert len(rig.rows(version=travel)) == 1 + 2 + 1  # rows stay with their generation
    page = rig.read(doc_id=travel.doc_id, direction="outgoing")
    assert len(page.rows) == 1


def test_retry_of_the_intent_is_a_no_op(rig: _Rig) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    created = rig.put(version=travel, body=_ndjson(_ref()))
    # same set, other key order and spacing: canonical bytes are equal
    retried = rig.put(
        version=travel,
        body=b'{"target": {"section_key": "approvals-process", "source_ref":'
        b' "policy/expense", "source_kind": "intranet"}, "kind": "refers_to",'
        b' "from_section_key": "approvals"}',
    )
    assert retried.outcome == "unchanged"
    assert retried.generation.generation_id == created.generation.generation_id
    rig.drain()
    again = rig.put(version=travel, body=_ndjson(_ref()))
    assert (again.outcome, again.generation.status) == ("unchanged", "active")
    assert len(rig.generations(version=travel)) == 1


def test_put_equal_to_active_cancels_the_pending_generation(rig: _Rig) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    active = rig.put(version=travel, body=_ndjson(_ref()))
    rig.drain()
    pending = rig.put(version=travel, body=_ndjson(_ref(kind="implements")))
    cancelled = rig.put(version=travel, body=_ndjson(_ref()))

    assert cancelled.outcome == "pending_cancelled"
    assert cancelled.generation.generation_id == active.generation.generation_id
    statuses = {g.generation_id: g.status for g in rig.generations(version=travel)}
    assert statuses[pending.generation.generation_id] == "superseded"
    assert statuses[active.generation.generation_id] == "active"
    rig.drain()  # the cancelled generation's job finds it superseded
    assert {row["kind"] for row in rig.rows(version=travel)} == {"refers_to"}


def test_concurrent_puts_are_ordered_by_request_seq(rig: _Rig) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    bodies = [_ndjson(_ref(context=f"variant {index}")) for index in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(lambda body: rig.put(version=travel, body=body), bodies)
        )

    seqs = sorted(result.generation.request_seq or 0 for result in results)
    assert seqs == [1, 2, 3, 4, 5, 6]
    generations = rig.generations(version=travel)
    assert [g.status for g in generations if g.status == "pending"] == ["pending"]
    newest = max(generations, key=lambda g: g.request_seq or 0)
    assert newest.status == "pending"
    rig.drain()
    assert newest.generation_id in {
        g.generation_id for g in rig.generations(version=travel) if g.status == "active"
    }


def test_worker_does_not_activate_a_generation_superseded_while_it_ran(
    rig: _Rig,
) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    stale = rig.put(version=travel, body=_ndjson(_ref()))

    newer: list[object] = []
    rig.artifact_store.before_read = lambda: newer.append(
        rig.put(version=travel, body=_ndjson(_ref(kind="implements")))
    )
    outcome = rig.references.materialize_supplied(
        deployment_id=_DEPLOYMENT_ID, generation_id=stale.generation.generation_id
    )

    assert outcome == "discarded"
    assert newer
    assert rig.rows(version=travel) == []
    assert (
        rig.references.materialize_supplied(
            deployment_id=_DEPLOYMENT_ID, generation_id=stale.generation.generation_id
        )
        == "skipped"
    )
    rig.drain()
    assert {row["kind"] for row in rig.rows(version=travel)} == {"implements"}


def test_unknown_source_section_rejects_the_set_and_keeps_the_active_one(
    rig: _Rig,
) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    rig.put(version=travel, body=_ndjson(_ref()))
    rig.drain()

    rejected = rig.put(
        version=travel,
        body=_ndjson(_ref(kind="implements"), _ref(from_section_key="nowhere")),
    )

    assert rejected.generation.status == "rejected"
    [error] = rejected.generation.errors
    assert (error.item, error.field) == (2, "from_section_key")
    assert "nowhere" in error.reason
    assert [g.status for g in rig.generations(version=travel)] == ["rejected", "active"]
    assert {row.kind for row in rig.read(doc_id=travel.doc_id).rows} == {"refers_to"}

    # validated by the worker when the version was still processing
    other = rig.ingest(ref="policy/other", body=_TRAVEL, process=False)
    pending = rig.put(version=other, body=_ndjson(_ref(from_section_key="nowhere")))
    assert pending.generation.status == "pending"
    rig.drain()
    [late] = rig.generations(version=other)
    assert late.status == "rejected"
    assert late.errors[0].item == 1


def test_invalid_bodies_and_absent_versions_record_nothing(rig: _Rig) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    with pytest.raises(ReferenceBodyError) as error:
        rig.put(version=travel, body=_ndjson(_ref()) + b'{"kind": "amends"}\n')
    assert error.value.line == 2
    with pytest.raises(DocumentVersionNotFoundError):
        rig.references.set_references(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=travel.doc_id,
            version_id=uuid4(),
            body=b"",
        )
    with pytest.raises(DocumentNotFoundError):
        rig.references.reference_generations(
            deployment_id=_DEPLOYMENT_ID, doc_id=uuid4(), version_id=travel.version_id
        )
    assert rig.generations(version=travel) == ()


def test_composite_foreign_keys_refuse_cross_lineage_rows(rig: _Rig) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    expense = rig.ingest(ref="policy/expense", body=_EXPENSE_1)
    put = rig.put(version=travel, body=_ndjson(_ref()))
    with pytest.raises(IntegrityError):
        rig.execute(
            "INSERT INTO document_reference_generations (generation_id,"
            " deployment_id, doc_id, version_id, origin, input_hash, request_seq,"
            " artifact_uri, status) VALUES (:g, :d, :doc, :v, 'supplied', 'h', 9,"
            " 'uri', 'pending')",
            g=uuid4(),
            d=_DEPLOYMENT_ID,
            doc=travel.doc_id,
            v=expense.version_id,
        )
    for from_doc, from_version in (
        (expense.doc_id, travel.version_id),  # a version of another lineage
        (expense.doc_id, expense.version_id),  # a generation of another version
    ):
        with pytest.raises(IntegrityError):
            rig.execute(
                "INSERT INTO document_crossrefs (crossref_id, deployment_id,"
                " from_doc_id, from_version_id, generation_id, kind, origin)"
                " VALUES (:id, :d, :doc, :v, :g, 'refers_to', 'supplied')",
                id=uuid4(),
                d=_DEPLOYMENT_ID,
                doc=from_doc,
                v=from_version,
                g=put.generation.generation_id,
            )


# --- binding and resolution --------------------------------------------------


def test_late_binding_resolves_when_the_target_is_first_ingested(rig: _Rig) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    rig.put(version=travel, body=_ndjson(_ref()))
    rig.drain()

    before = rig.read(doc_id=travel.doc_id, section_key="approvals")
    [row] = before.rows
    assert (row.status, row.target) == ("target_unavailable", None)
    assert (row.named_target.source_ref, row.named_target.section_key) == (
        "policy/expense",
        "approvals-process",
    )
    assert row.source.section_title == "Approvals"

    expense = rig.ingest(ref="policy/expense", body=_EXPENSE_1)

    [stored] = rig.rows(version=travel)
    assert (stored["to_doc_id"], stored["resolved"]) == (expense.doc_id, True)
    [row] = rig.read(doc_id=travel.doc_id, section_key="approvals").rows
    assert row.status == "resolved"
    assert row.target is not None
    assert (row.target.doc_id, row.target.version_id) == (
        expense.doc_id,
        expense.version_id,
    )
    assert row.target.section_title == "Approvals process"
    assert row.target.first_chunk_ids
    # a set written after the target exists resolves at materialization
    rig.put(version=travel, body=_ndjson(_ref(kind="implements")))
    rig.drain()
    assert {
        r["to_doc_id"] for r in rig.rows(version=travel) if r["status"] == "active"
    } == {expense.doc_id}


def test_temporal_join_spans_a_target_amendment(rig: _Rig) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    first = rig.ingest(
        ref="policy/expense",
        body=_EXPENSE_1,
        version_key="edition-1",
        effective_from=_day(2024, 1),
    )
    second = rig.ingest(
        ref="policy/expense",
        body=_EXPENSE_2,
        version_key="edition-2",
        effective_from=_day(2024, 7),
    )
    rig.put(version=travel, body=_ndjson(_ref()))
    rig.drain()

    page = rig.read(
        doc_id=travel.doc_id,
        section_key="approvals",
        time={
            "mode": "overlap",
            "from": "2024-01-01T00:00:00Z",
            "to": "2024-12-31T00:00:00Z",
        },
    )

    assert _statuses(page) == ["resolved", "resolved"]
    targets = [row.target for row in page.rows]
    assert [t.version_id for t in targets if t] == [first.version_id, second.version_id]
    one, two = (t for t in targets if t)
    assert (one.applies_during.from_, one.applies_during.until) == (  # type: ignore[union-attr]
        _day(2024, 1),
        _day(2024, 7),
    )
    assert (two.applies_during.from_, two.applies_during.until) == (  # type: ignore[union-attr]
        _day(2024, 7),
        _day(2024, 12, 31),
    )
    assert not one.concurrent and not two.concurrent
    assert one.effective[0].until == _day(2024, 7)

    before = rig.read(
        doc_id=travel.doc_id, time={"mode": "at", "at": "2023-06-01T00:00:00Z"}
    )
    assert _statuses(before) == ["target_not_in_force"]
    assert before.rows[0].target is not None
    assert before.rows[0].target.doc_id == first.doc_id


def test_overlapping_target_declarations_are_returned_concurrently(rig: _Rig) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    first = rig.ingest(
        ref="policy/expense",
        body=_EXPENSE_1,
        effective_from=_day(2024, 1),
        effective_until=_day(2025, 1),
    )
    second = rig.ingest(
        ref="policy/expense", body=_EXPENSE_2, effective_from=_day(2024, 7)
    )
    rig.put(version=travel, body=_ndjson(_ref()))
    rig.drain()

    page = rig.read(
        doc_id=travel.doc_id, time={"mode": "at", "at": "2024-09-01T00:00:00Z"}
    )

    assert {row.target.version_id for row in page.rows if row.target} == {
        first.version_id,
        second.version_id,
    }
    assert all(row.target and row.target.concurrent for row in page.rows)


def test_pinned_target_resolves_outside_its_force_and_by_key_only(rig: _Rig) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    first = rig.ingest(
        ref="policy/expense",
        body=_EXPENSE_1,
        version_key="edition-1",
        effective_from=_day(2024, 1),
    )
    rig.ingest(
        ref="policy/expense",
        body=_EXPENSE_2,
        version_key="edition-2",
        effective_from=_day(2024, 7),
    )
    pinned_target = {
        "source_kind": "intranet",
        "source_ref": "policy/expense",
        "version_key": "edition-1",
        "section_key": "approvals-process",
    }
    rig.put(
        version=travel,
        body=_ndjson(
            _ref(binding="pinned", target=pinned_target),
            _ref(
                kind="implements",
                binding="pinned",
                target={**pinned_target, "version_key": "edition-9"},
            ),
        ),
    )
    rig.drain()

    page = rig.read(
        doc_id=travel.doc_id, time={"mode": "at", "at": "2025-06-01T00:00:00Z"}
    )
    by_kind = {row.kind: row for row in page.rows}
    pinned = by_kind["refers_to"]
    assert pinned.status == "resolved"
    assert pinned.target is not None
    assert pinned.target.version_id == first.version_id
    assert pinned.target.effective[0].until == _day(2024, 7)
    assert by_kind["implements"].status == "pinned_version_unavailable"

    rig.execute(
        "UPDATE document_versions SET deleted_at = now() WHERE version_id = :v",
        v=first.version_id,
    )
    page = rig.read(
        doc_id=travel.doc_id, time={"mode": "at", "at": "2025-06-01T00:00:00Z"}
    )
    assert {row.kind: row.status for row in page.rows}["refers_to"] == (
        "pinned_version_unavailable"
    )


def test_section_statuses_of_a_readable_target(rig: _Rig) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    rig.ingest(ref="policy/expense", body=_EXPENSE_1, effective_from=_day(2024, 1))
    second = rig.ingest(
        ref="policy/expense", body=_EXPENSE_2, effective_from=_day(2024, 7)
    )
    rig.ingest(ref="policy/expense", body=_EXPENSE_3, effective_from=_day(2025, 1))
    rig.put(version=travel, body=_ndjson(_ref()))
    rig.drain()

    def status_at(at: str) -> list[str]:
        return _statuses(rig.read(doc_id=travel.doc_id, time={"mode": "at", "at": at}))

    assert status_at("2024-03-01T00:00:00Z") == ["resolved"]
    assert status_at("2025-06-01T00:00:00Z") == ["section_not_in_version"]
    rig.execute(
        "UPDATE document_sections SET section_key = NULL, own_content_hash = NULL,"
        " subtree_content_hash = NULL WHERE version_id = :v",
        v=second.version_id,
    )
    assert status_at("2024-09-01T00:00:00Z") == ["section_not_indexed"]


def test_target_in_force_but_processing(rig: _Rig) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    rig.ingest(ref="policy/expense", body=_EXPENSE_1, effective_from=_day(2024, 1))
    rig.put(version=travel, body=_ndjson(_ref()))
    rig.drain()
    pending = rig.ingest(
        ref="policy/expense",
        body=_EXPENSE_2,
        process=False,
        effective_from=_day(2025, 1),
    )

    [row] = rig.read(
        doc_id=travel.doc_id, time={"mode": "at", "at": "2025-06-01T00:00:00Z"}
    ).rows

    assert row.status == "target_processing"
    assert row.target is not None
    assert row.target.version_id == pending.version_id
    assert row.target.applies_during is not None
    assert (row.target.section_title, row.target.first_chunk_ids) == (None, ())


# --- reading -----------------------------------------------------------------


def test_incoming_chunk_and_section_scopes(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    expense = rig.ingest(ref="policy/expense", body=_EXPENSE_1)
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    rig.put(
        version=travel,
        body=_ndjson(
            _ref(),
            _ref(
                from_section_key="meals",
                kind="implements",
                target={
                    "source_kind": "intranet",
                    "source_ref": "policy/expense",
                    "section_key": "limits",
                },
            ),
        ),
    )
    rig.drain()

    incoming = rig.read(doc_id=expense.doc_id, direction="incoming")
    assert {(row.direction, row.kind) for row in incoming.rows} == {
        ("incoming", "refers_to"),
        ("incoming", "implements"),
    }
    narrowed = rig.read(
        doc_id=expense.doc_id, section_key="limits", direction="incoming"
    )
    assert [row.kind for row in narrowed.rows] == ["implements"]
    # per-diem's descendants include meals
    from_per_diem = rig.read(
        doc_id=travel.doc_id, section_key="per-diem", direction="outgoing"
    )
    assert [row.source.section_key for row in from_per_diem.rows] == ["meals"]
    kinds = rig.read(doc_id=travel.doc_id, kinds=["refers_to"])
    assert [row.kind for row in kinds.rows] == ["refers_to"]

    with rig.engine.connect() as connection:
        chunk_id = connection.execute(
            text(
                "SELECT c.chunk_id FROM chunks c JOIN document_sections s"
                "  ON s.version_id = c.version_id AND s.section_key = 'approvals'"
                " WHERE c.version_id = :v AND c.block_start <= s.block_start"
                "   AND c.block_end >= s.block_start"
            ),
            {"v": travel.version_id},
        ).scalar_one()
    by_chunk = rig.read(chunk_id=chunk_id, direction="outgoing")
    assert [row.source.section_key for row in by_chunk.rows] == ["approvals"]
    assert by_chunk.rows[0].source.window.from_ == by_chunk.rows[0].source.window.until
    with pytest.raises(ChunkNotFoundError):
        rig.read(chunk_id=uuid4())

    monkeypatch.setattr(references_module, "DOCUMENT_REFERENCES_MAX_DESCENDANT_KEYS", 1)
    broad = rig.read(doc_id=travel.doc_id, section_key="per-diem")
    assert broad.rows == ()
    assert broad.too_broad is not None and broad.too_broad.descendant_keys == 2


def test_paging_walks_every_row_once_with_pinned_instants(rig: _Rig) -> None:
    rig.ingest(ref="policy/expense", body=_EXPENSE_1)
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    kinds = ["refers_to", "implements", "cites", "links_to", "replies_to"]
    rig.put(version=travel, body=_ndjson(*(_ref(kind=kind) for kind in kinds)))
    rig.drain()
    everything = rig.read(doc_id=travel.doc_id)
    assert len(everything.rows) == 5 + 0  # outgoing; travel has no incoming

    seen: list[UUID] = []
    cursor = None
    pages = []
    while True:
        page = rig.read(doc_id=travel.doc_id, k=2, cursor=cursor)
        pages.append(page)
        seen.extend(row.crossref_id for row in page.rows)
        cursor = page.cursor
        if cursor is None:
            break
    assert [len(page.rows) for page in pages] == [2, 2, 1]
    assert seen == [row.crossref_id for row in everything.rows]
    assert len({page.evaluated_at for page in pages}) == 1
    assert len({page.believed_at for page in pages}) == 1
    with pytest.raises(ValueError, match="different"):
        rig.read(doc_id=travel.doc_id, section_key="approvals", cursor=pages[0].cursor)


def test_amends_references_appear_in_section_history(rig: _Rig) -> None:
    expense = rig.ingest(ref="policy/expense", body=_EXPENSE_1)
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    rig.put(
        version=travel,
        body=_ndjson(
            _ref(
                kind="amends",
                change_date_known=True,
                change_effective_from="2026-01-01T00:00:00Z",
                source_label="amendment-2",
            ),
            _ref(kind="amends", change_date_known=False, from_section_key="per-diem"),
        ),
    )
    rig.drain()

    history = SectionHistory(engine=rig.engine).section_history(
        deployment_id=_DEPLOYMENT_ID,
        request=SectionHistoryRequest(
            doc_id=expense.doc_id, section_key="approvals-process"
        ),
    )
    assert [
        (a.change_effective_from, a.change_date_known, a.source_label)
        for a in history.amendments
    ] == [(_day(2026, 1), True, "amendment-2"), (None, False, None)]


def test_extracted_generation_activates_on_the_current_representation(
    rig: _Rig,
) -> None:
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    rig.put(version=travel, body=_ndjson(_ref()))
    rig.drain()
    with rig.engine.connect() as connection:
        representation_id = connection.execute(
            text(
                "SELECT current_representation_id FROM document_versions WHERE version_id = :v"
            ),
            {"v": travel.version_id},
        ).scalar_one()

    def extract(version: str, representation: UUID):
        return rig.references.record_extracted(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=travel.doc_id,
            version_id=travel.version_id,
            representation_id=representation,
            crossref_version=version,
            references=(
                ExtractedReference(
                    kind="links_to",
                    from_section_key="per-diem",
                    from_char_start=10,
                    from_char_end=20,
                    to_source_kind="intranet",
                    to_source_ref="policy/expense",
                    raw_citation="expense policy",
                ),
            ),
        )

    first = extract("x-1", representation_id)
    assert first.status == "active"
    assert extract("x-1", representation_id).generation_id == first.generation_id
    stale = extract("x-1", uuid4())  # not the current reading: stays pending
    assert stale.status == "pending"
    second = extract("x-2", representation_id)
    assert second.status == "active"
    statuses = {g.generation_id: g.status for g in rig.generations(version=travel)}
    assert statuses[first.generation_id] == "superseded"
    page = rig.read(doc_id=travel.doc_id)
    assert sorted((row.origin, row.kind) for row in page.rows) == [
        ("extracted", "links_to"),
        ("supplied", "refers_to"),
    ]


# --- deletion and forgetting -------------------------------------------------


def test_soft_deleted_lineages_are_unavailable_and_hide_their_references(
    rig: _Rig,
) -> None:
    expense = rig.ingest(ref="policy/expense", body=_EXPENSE_1)
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    rig.put(version=travel, body=_ndjson(_ref()))
    rig.drain()

    rig.execute(
        "UPDATE documents SET deleted_at = now() WHERE doc_id = :doc",
        doc=expense.doc_id,
    )
    [row] = rig.read(doc_id=travel.doc_id).rows
    assert (row.status, row.target) == ("target_unavailable", None)
    assert row.named_target.source_ref == "policy/expense"
    with pytest.raises(DocumentNotFoundError):
        rig.read(doc_id=expense.doc_id, direction="incoming")
    # revival: to_doc_id was kept, so it resolves again without re-binding
    rig.execute(
        "UPDATE documents SET deleted_at = NULL WHERE doc_id = :doc", doc=expense.doc_id
    )
    assert _statuses(rig.read(doc_id=travel.doc_id)) == ["resolved"]

    rig.execute(
        "UPDATE documents SET deleted_at = now() WHERE doc_id = :doc", doc=travel.doc_id
    )
    assert rig.read(doc_id=expense.doc_id, direction="incoming").rows == ()


def test_hard_forget_drops_own_rows_and_unbinds_incoming_ones(rig: _Rig) -> None:
    expense = rig.ingest(ref="policy/expense", body=_EXPENSE_1)
    travel = rig.ingest(ref="policy/travel", body=_TRAVEL)
    rig.put(version=travel, body=_ndjson(_ref()))
    rig.put(
        version=expense,
        body=_ndjson(
            _ref(
                from_section_key="limits",
                target={"source_kind": "intranet", "source_ref": "policy/travel"},
            )
        ),
    )
    rig.drain()

    forget = ForgetCatalog(engine=rig.engine)
    forget_id = uuid4()
    forget.prepare(
        deployment_id=_DEPLOYMENT_ID, doc_id=expense.doc_id, forget_id=forget_id
    )
    manifest = forget.inventory_and_store_manifest(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=expense.doc_id,
        forget_id=forget_id,
        requested_at=datetime.now(UTC),
    )
    assert any("/references/" in key.root for key in manifest.object_keys)
    forget.accept_and_enqueue(manifest=manifest)
    forget.scrub_postgres(manifest=manifest)
    forget.verify_postgres_scrubbed(manifest=manifest)

    with rig.engine.connect() as connection:
        for table, column in (
            ("document_reference_generations", "doc_id"),
            ("document_crossrefs", "from_doc_id"),
            ("document_crossrefs", "to_doc_id"),
        ):
            count = connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE {column} = :doc"),
                {"doc": expense.doc_id},
            ).scalar_one()
            assert count == 0, (table, column)
    [incoming] = rig.rows(version=travel)
    assert (incoming["to_doc_id"], incoming["resolved"], incoming["to_source_ref"]) == (
        None,
        False,
        "policy/expense",
    )
    [row] = rig.read(doc_id=travel.doc_id).rows
    assert row.status == "target_unavailable"
