"""Real-PostgreSQL proofs for bounded version-bump backfill seeding."""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import BackfillNotDrainedError
from rememberstack.model import BackfillSeedRequest
from rememberstack.model import ClaimedWork
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EnqueueWork
from rememberstack.model import LaneRouteError
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model.p1_maintain import MaintainReport
from rememberstack.model.p1_maintain import TableMaintainStats
from rememberstack.spine import BackfillFinalizer
from rememberstack.spine import BackfillSeeder
from rememberstack.spine import BackfillSeederSettings
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.p1_maintain_lock import p1_table_maintain_lock_key
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("71000000-0000-0000-0000-000000000001")
_OLD_VERSION = "extractor-2026.06"
_NEW_VERSION = "extractor-2026.07"


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL backfill proofs"
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
    """Give each proof a fresh deployment and empty work ledger."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="backfill-test",
            name="Backfill seeder proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


@pytest.fixture()
def ledger(database_engine: Engine) -> WorkLedger:
    """The ordinary D12 ledger used by both prior and newly seeded work."""
    return WorkLedger(engine=database_engine, settings=WorkLedgerSettings())


def _prior_work(*, target_id: UUID) -> EnqueueWork:
    """One completed old-version target eligible for a version bump."""
    return EnqueueWork(
        deployment_id=_DEPLOYMENT_ID,
        target_kind=ProcessingTarget.DOCUMENT_VERSION,
        target_id=target_id,
        stage=PipelineStage.EXTRACT_CLAIMS,
        component_version=_OLD_VERSION,
        content_hash=f"sha256:{target_id.int}",
        lane=ProcessingLane.STEADY,
        payload={"version_id": str(target_id), "source": "immutable-input"},
    )


def _complete_prior_work(*, ledger: WorkLedger, target_ids: tuple[UUID, ...]) -> None:
    """Land successful prior-version rows, as a real completed campaign would."""
    for target_id in target_ids:
        ledger.enqueue(work=_prior_work(target_id=target_id))
    for _ in target_ids:
        claimed = ledger.claim_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.EXTRACT_CLAIMS,
            lane=ProcessingLane.STEADY,
        )
        assert isinstance(claimed, ClaimedWork)
        ledger.complete(processing_id=claimed.processing_id)


class _RecordingIndexMaintenance:
    """A structural P1 maintenance fake recording explicit index builds."""

    def __init__(self) -> None:
        self.builds = 0
        self.ensures = 0

    def build_search_indexes(self) -> None:
        """Record one post-backfill build."""
        self.builds += 1

    def ensure_search_indexes(self, *, tables=None) -> MaintainReport:
        """Count per-table ensure calls from the finalizer."""
        self.ensures += 1
        return MaintainReport()

    def optimize_tables(
        self, *, tables=None, cleanup_older_than=None
    ) -> MaintainReport:
        """Unused D91 port surface for the structural fake."""
        return MaintainReport()

    def rebuild_vector_indexes(self, *, tables=None) -> MaintainReport:
        """Unused D91 port surface for the structural fake."""
        return MaintainReport()

    def rebuild_text_indexes(self, *, tables=None) -> MaintainReport:
        """Unused D91 port surface for the structural fake."""
        return MaintainReport()

    def maintenance_stats(self, *, table: str) -> TableMaintainStats:
        """Unused D91 port surface for the structural fake."""
        return TableMaintainStats(table=table)


def test_version_bump_is_bounded_resumable_and_cannot_starve_steady_work(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """WP-7.1: backfill stays separate while a live duplicate remains steady."""
    target_ids = tuple(UUID(int=value) for value in (1, 2, 3, 4))
    _complete_prior_work(ledger=ledger, target_ids=target_ids)
    seeder = BackfillSeeder(
        engine=database_engine, settings=BackfillSeederSettings(batch_size=2)
    )
    request = BackfillSeedRequest(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        component_version=_NEW_VERSION,
    )

    first = seeder.seed_batch(request=request)
    assert first.selected == 2
    assert first.created == 2
    assert first.already_present == 0
    assert not first.complete

    # Live ingestion discovers one remaining target before the next seed pass.
    live = ledger.enqueue(
        work=_prior_work(target_id=target_ids[3]).model_copy(
            update={"component_version": _NEW_VERSION, "lane": ProcessingLane.STEADY}
        )
    )
    assert live.created

    second = seeder.seed_batch(request=request)
    replay = seeder.seed_batch(request=request)
    assert second.selected == 1 and second.created == 1 and second.complete
    assert replay.selected == 0 and replay.complete

    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT target_id, lane::text AS lane, payload"
                    " FROM processing_state"
                    " WHERE deployment_id = :deployment_id"
                    " AND stage = :stage AND component_version = :version"
                    " ORDER BY target_id"
                ),
                {
                    "deployment_id": _DEPLOYMENT_ID,
                    "stage": PipelineStage.EXTRACT_CLAIMS.value,
                    "version": _NEW_VERSION,
                },
            )
            .mappings()
            .all()
        )
    assert [row["lane"] for row in rows] == [
        "backfill",
        "backfill",
        "backfill",
        "steady",
    ]
    assert rows[0]["payload"] == {
        "version_id": str(target_ids[0]),
        "source": "immutable-input",
    }

    # Separate route claims are the structural priority rule: backfill volume
    # cannot make a steady claim wait behind it.
    claimed_live = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )
    assert isinstance(claimed_live, ClaimedWork)
    assert claimed_live.target_id == target_ids[3]
    claimed_backfill = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.BACKFILL,
    )
    assert isinstance(claimed_backfill, ClaimedWork)
    assert claimed_backfill.target_id in target_ids[:3]


def test_search_indexes_build_only_after_backfill_has_drained(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """The explicit post-bulk-load index build has a ledger-backed barrier."""
    target_id = UUID(int=10)
    _complete_prior_work(ledger=ledger, target_ids=(target_id,))
    seeded = BackfillSeeder(
        engine=database_engine, settings=BackfillSeederSettings(batch_size=10)
    ).seed_batch(
        request=BackfillSeedRequest(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.EXTRACT_CLAIMS,
            component_version=_NEW_VERSION,
        )
    )
    assert seeded.complete and seeded.created == 1

    maintenance = _RecordingIndexMaintenance()
    finalizer = BackfillFinalizer(
        engine=database_engine,
        search_index_maintenance=maintenance,
        lance_root=Path("/tmp/rememberstack-test-lance"),
    )
    with pytest.raises(BackfillNotDrainedError):
        finalizer.build_search_indexes(deployment_id=_DEPLOYMENT_ID)
    assert maintenance.ensures == 0

    claimed = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.BACKFILL,
    )
    assert isinstance(claimed, ClaimedWork)
    ledger.complete(processing_id=claimed.processing_id)

    finalizer.build_search_indexes(deployment_id=_DEPLOYMENT_ID)
    assert maintenance.ensures == 4


def test_p1_maintain_lock_key_is_root_and_table() -> None:
    """Custom and default roots must not collide; Path and str resolve alike."""
    left = p1_table_maintain_lock_key(
        lance_root="/tmp/rememberstack-a/lance", table_name="facts"
    )
    right = p1_table_maintain_lock_key(
        lance_root=Path("/tmp/rememberstack-a/lance"), table_name="facts"
    )
    other = p1_table_maintain_lock_key(
        lance_root="/tmp/rememberstack-b/lance", table_name="facts"
    )
    assert left == right
    assert left != other
    assert left.endswith(":facts")


def test_unlaned_stage_cannot_be_seeded_as_backfill(database_engine: Engine) -> None:
    """K/P scheduled stages keep their single unlaned route."""
    seeder = BackfillSeeder(
        engine=database_engine, settings=BackfillSeederSettings(batch_size=1)
    )
    with pytest.raises(LaneRouteError):
        seeder.seed_batch(
            request=BackfillSeedRequest(
                deployment_id=_DEPLOYMENT_ID,
                stage=PipelineStage.COMPILE_KNOWLEDGE,
                component_version="knowledge-writer-next",
            )
        )
