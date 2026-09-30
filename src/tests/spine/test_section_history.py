"""Section keys, content hashes, backfill and ``section_history`` on PostgreSQL (D140 §4, §6.2).

Documents run through the real E0 chain (ingest → convert → structure) over a
local object store, so the section rows are the rows production writes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from datetime import UTC
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

from remember.models import CurrentReadTime
from remember.models import SectionHistoryRequest
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.core import ConversionRouter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import RunResultOutcome
from rememberstack.model import SectionTreeRecord
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.section_history import SectionHistory
from rememberstack.spine.section_index_backfill import SectionIndexBackfill
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import ConvertHandler
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import StructureHandler
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("62000000-0000-0000-0000-0000000d0140")

_EDITION_1 = (
    "# Travel policy\n\nIntro.\n\n"
    "## Per-diem allowance {#per-diem}\n\nThe allowance is 40 per day.\n\n"
    "### Meals {#meals}\n\nMeals are included.\n\n"
    "## Approvals {#approvals}\n\nManagers approve.\n"
)
# The child paragraph changes: per-diem's subtree changes, its own text does not.
_EDITION_2 = _EDITION_1.replace("Meals are included.", "Meals are excluded.")
# The per-diem section is removed.
_EDITION_3 = (
    "# Travel policy\n\nIntro.\n\n## Approvals {#approvals}\n\nManagers approve.\n"
)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head over the integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for section proofs")
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
            slug="section-history-test",
            name="Section history proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


class _Rig:
    """Ingest, convert and structure versions of one lineage."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        self.engine = engine
        self.artifact_store = LocalFSObjectStore(root=root / "artifacts")
        raw_store = LocalFSObjectStore(root=root / "raw")
        self.catalog = DocumentCatalog(engine=engine)
        ledger = WorkLedger(
            engine=engine,
            settings=WorkLedgerSettings(
                retry_backoff_base_s=0.0, retry_backoff_max_s=0.0
            ),
        )
        routes = {"text/markdown": MarkdownPassthroughConverter()}
        self.ingestor = UploadIngestor(
            catalog=self.catalog,
            raw_store=raw_store,
            admission=ForgetCatalog(engine=engine),
            routable_mimes=frozenset(routes),
        )
        registry = HandlerRegistry()
        registry.register(
            stage=PipelineStage.CONVERT,
            handler=ConvertHandler(
                catalog=self.catalog,
                raw_store=raw_store,
                artifact_store=self.artifact_store,
                router=ConversionRouter(routes=routes),
            ),
        )
        registry.register(
            stage=PipelineStage.STRUCTURE,
            handler=StructureHandler(
                catalog=self.catalog, artifact_store=self.artifact_store
            ),
        )
        self.worker = Worker(ledger=ledger, registry=registry)
        self.history = SectionHistory(engine=engine)

    def ingest(self, *, body: str, process: bool = True) -> IngestedVersion:
        ingested = self.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="drive",
            source_ref="travel-policy",
            upload=DocumentUpload(
                filename="travel.md", mime="text/markdown", content=body.encode()
            ),
            versioning_mode="snapshot",
            source_modified_at=None,
            source_version_ref=None,
            sync_cycle_id=None,
        )
        if process:
            for stage in (PipelineStage.CONVERT, PipelineStage.STRUCTURE):
                outcome = self.worker.run_one(
                    deployment_id=_DEPLOYMENT_ID,
                    stage=stage,
                    lane=ProcessingLane.STEADY,
                ).outcome
                assert outcome is RunResultOutcome.SUCCEEDED
        return ingested

    def read(self, *, doc_id: UUID, key: str, **arguments: object):
        return self.history.section_history(
            deployment_id=_DEPLOYMENT_ID,
            request=SectionHistoryRequest.model_validate(
                {"doc_id": doc_id, "section_key": key, **arguments}
            ),
        )

    def sections(self, *, version_id: UUID) -> list[dict[str, object]]:
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT s.* FROM document_sections s"
                        " JOIN document_versions v ON v.version_id = s.version_id"
                        " JOIN document_representations r"
                        "   ON r.representation_id = v.current_representation_id"
                        "  AND r.current_structure_generation_id"
                        "      = s.structure_generation_id"
                        " WHERE s.version_id = :v ORDER BY s.ordinal"
                    ),
                    {"v": version_id},
                ).mappings()
            ]


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _Rig:
    return _Rig(engine=database_engine, root=tmp_path)


def test_structure_persists_keys_and_hashes_for_every_section(rig: _Rig) -> None:
    ingested = rig.ingest(body=_EDITION_1)
    rows = rig.sections(version_id=ingested.version_id)
    assert [(row["title"], row["section_key"]) for row in rows] == [
        ("travel", None),
        ("Travel policy", None),
        ("Per-diem allowance", "per-diem"),
        ("Meals", "meals"),
        ("Approvals", "approvals"),
    ]
    assert all(row["own_content_hash"] and row["subtree_content_hash"] for row in rows)


def test_restructuring_a_version_keeps_its_keys_in_a_new_generation(rig: _Rig) -> None:
    """Keys are unique per structure generation, not per version (§4.2)."""
    ingested = rig.ingest(body=_EDITION_1)
    version = rig.sections(version_id=ingested.version_id)
    representation_id = version[0]["representation_id"]
    assert isinstance(representation_id, UUID)
    current = rig.catalog.current_section_tree(representation_id=representation_id)
    assert current is not None
    generation_id = uuid4()
    rig.catalog.record_section_tree(
        record=SectionTreeRecord(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=ingested.doc_id,
            version_id=ingested.version_id,
            representation_id=representation_id,
            structure_generation_id=generation_id,
            sections=current.sections,
            placement_path=current.placement_path,
            structurer_name=current.route_tag.value,
            structurer_version=current.structurer_version,
            skeleton_version=current.skeleton_version,
            skeleton_hash=current.skeleton_hash,
            skeleton_producer_family=current.skeleton_producer_family,
            skeleton_check_version=current.skeleton_check_version,
            roles_version=current.roles_version,
            selecting_check_id=current.selecting_check_id,
            route_tag=current.route_tag,
            candidate_skeleton_hash=current.candidate_skeleton_hash,
            stats_version=current.stats_version,
            stats=current.stats,
            pageindex_uri=f"test://{generation_id}.json",
        )
    )
    with rig.engine.connect() as connection:
        holders = connection.execute(
            text(
                "SELECT count(DISTINCT structure_generation_id) FROM document_sections"
                " WHERE version_id = :v AND section_key = 'per-diem'"
            ),
            {"v": ingested.version_id},
        ).scalar_one()
    assert holders == 2
    page = rig.read(doc_id=ingested.doc_id, key="per-diem")
    assert [row.status for row in page.rows] == ["present"]


def test_undeclared_history_statuses_and_change_flags(rig: _Rig) -> None:
    first = rig.ingest(body=_EDITION_1)
    rig.ingest(body=_EDITION_2)
    rig.ingest(body=_EDITION_3)
    rig.ingest(body=_EDITION_3 + "\nA pending edit.\n", process=False)

    page = rig.read(doc_id=first.doc_id, key="per-diem")

    assert page.periodised is False
    assert [(row.version_no, row.status) for row in page.rows] == [
        (1, "present"),
        (2, "present"),
        (3, "absent"),
        (4, "processing"),
    ]
    one, two = (row.section for row in page.rows[:2])
    assert one is not None and two is not None
    assert (one.changed, one.own_changed) == (None, None)
    assert (two.changed, two.own_changed) == (True, False)
    assert one.title == "Per-diem allowance"
    assert len(one.first_chunk_ids) <= 1
    # served version carries the unbounded interval, others none
    assert [len(row.effective) for row in page.rows] == [0, 0, 1, 0]
    assert page.amendments == ()

    current = rig.read(doc_id=first.doc_id, key="per-diem", time={"mode": "current"})
    assert [(row.version_no, row.status) for row in current.rows] == [(3, "absent")]


def test_paging_pins_instants_and_resumes_in_order(rig: _Rig) -> None:
    first = rig.ingest(body=_EDITION_1)
    rig.ingest(body=_EDITION_2)
    rig.ingest(body=_EDITION_3)

    page_one = rig.read(doc_id=first.doc_id, key="per-diem", k=2)
    assert [row.version_no for row in page_one.rows] == [1, 2]
    assert page_one.cursor is not None
    page_two = rig.read(
        doc_id=first.doc_id, key="per-diem", k=2, cursor=page_one.cursor
    )
    assert [row.version_no for row in page_two.rows] == [3]
    assert page_two.cursor is None
    assert page_two.evaluated_at == page_one.evaluated_at
    assert page_two.believed_at == page_one.believed_at
    with pytest.raises(ValueError, match="different"):
        rig.read(doc_id=first.doc_id, key="meals", cursor=page_one.cursor)


def test_unindexed_version_is_not_indexed_until_backfilled(rig: _Rig) -> None:
    first = rig.ingest(body=_EDITION_1)
    rig.ingest(body=_EDITION_2)
    before = {
        row["node_path"]: (
            row["section_key"],
            row["own_content_hash"],
            row["subtree_content_hash"],
        )
        for row in rig.sections(version_id=first.version_id)
    }
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_sections SET section_key = NULL,"
                " own_content_hash = NULL, subtree_content_hash = NULL"
                " WHERE version_id = :v"
            ),
            {"v": first.version_id},
        )

    page = rig.read(doc_id=first.doc_id, key="per-diem")
    assert [row.status for row in page.rows] == ["not_indexed", "present"]
    assert page.rows[1].section is not None
    assert page.rows[1].section.changed is None  # no indexed predecessor

    backfill = SectionIndexBackfill(
        engine=rig.engine, artifact_store=rig.artifact_store
    )
    result = backfill.run(deployment_id=_DEPLOYMENT_ID)
    assert result.generations_indexed == 1
    assert result.sections_updated == len(before)
    after = {
        row["node_path"]: (
            row["section_key"],
            row["own_content_hash"],
            row["subtree_content_hash"],
        )
        for row in rig.sections(version_id=first.version_id)
    }
    assert after == before
    assert backfill.run(deployment_id=_DEPLOYMENT_ID).sections_updated == 0

    page = rig.read(doc_id=first.doc_id, key="per-diem")
    assert [row.status for row in page.rows] == ["present", "present"]


def test_absent_lineage_is_not_found(rig: _Rig) -> None:
    with pytest.raises(DocumentNotFoundError):
        rig.read(doc_id=uuid4(), key="per-diem")


def test_periodised_history_orders_by_effective_start(rig: _Rig) -> None:
    """Edition 2 back-fills the older period: rows follow effective start."""
    first = rig.ingest(body=_EDITION_1)
    second = rig.ingest(body=_EDITION_2)
    now = datetime.now(UTC)
    _declare(
        rig=rig,
        doc_id=first.doc_id,
        version_id=first.version_id,
        effective_from=now - timedelta(days=100),
    )
    _declare(
        rig=rig,
        doc_id=first.doc_id,
        version_id=second.version_id,
        effective_from=now - timedelta(days=400),
    )

    page = rig.read(doc_id=first.doc_id, key="per-diem")

    assert page.periodised is True
    assert [row.version_no for row in page.rows] == [2, 1]
    assert page.rows[0].effective[0].until is not None  # derived end
    assert page.rows[0].effective[0].until_declared is False
    current = rig.read(doc_id=first.doc_id, key="per-diem", time=CurrentReadTime())
    assert [row.version_no for row in current.rows] == [1]


def _declare(
    *, rig: _Rig, doc_id: UUID, version_id: UUID, effective_from: datetime
) -> None:
    """Declare one open period through the FOUNDATION period write path."""
    from rememberstack.spine.effective_periods import (  # noqa: PLC0415
        EffectivePeriodCatalog,
    )

    EffectivePeriodCatalog(engine=rig.engine).set_effective_periods(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=doc_id,
        version_id=version_id,
        periods=({"effective_from": effective_from},),
    )
