"""Real-PostgreSQL proofs for D91 maintain units, coalesce, and attempt fence."""

from collections.abc import Iterator
from datetime import timedelta
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

from rememberstack.model import ClaimedWork
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EnqueueWork
from rememberstack.model import LaneRouteError
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import WorkNotRunningError
from rememberstack.model.p1_maintain import P1MaintainEnqueueRequest
from rememberstack.model.p1_maintain import P1MaintainMode
from rememberstack.model.p1_maintain import P1MaintainTable
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import P1_MAINTAIN_COMPONENT_VERSION
from rememberstack.spine import P1MaintainCatalog
from rememberstack.spine import P1MaintainSettings
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.catalog_contract import lane_is_valid
from rememberstack.spine.p1_maintain_lock import hold_p1_table_maintain_locks
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("91000000-0000-0000-0000-000000000001")
_LANCE_KEY = "/tmp/rememberstack-d91-pr3-lance"


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for D91 maintain ledger proofs"
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
    """Give each proof a fresh deployment and empty maintain tables."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d91-maintain",
            name="D91 maintain ledger proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


@pytest.fixture()
def ledger(database_engine: Engine) -> WorkLedger:
    """A ledger with zero retry backoff so reclaimed work is immediately due."""
    return WorkLedger(
        engine=database_engine,
        settings=WorkLedgerSettings(retry_backoff_base_s=0.0, retry_backoff_max_s=0.0),
    )


def _catalog(
    *,
    database_engine: Engine,
    ledger: WorkLedger,
    maintenance_enabled: bool = True,
    heavy_enabled: bool = False,
    running_stale_s: float = 7200.0,
    heartbeat_s: float = 60.0,
) -> P1MaintainCatalog:
    """Catalog with explicit gates for one proof."""
    return P1MaintainCatalog(
        engine=database_engine,
        ledger=ledger,
        settings=P1MaintainSettings(
            maintenance_enabled=maintenance_enabled,
            heavy_enabled=heavy_enabled,
            running_stale_s=running_stale_s,
            heartbeat_s=heartbeat_s,
            heartbeat_stale_mult=3.0,
        ),
    )


def _request(
    *,
    mode: P1MaintainMode = P1MaintainMode.LIGHT,
    reason: str = "threshold",
    force: bool = False,
    table_name: P1MaintainTable = P1MaintainTable.FACTS,
) -> P1MaintainEnqueueRequest:
    """One enqueue request against the test root."""
    return P1MaintainEnqueueRequest(
        deployment_id=_DEPLOYMENT_ID,
        lance_root_key=_LANCE_KEY,
        table_name=table_name,
        mode=mode,
        reason=reason,
        force=force,
    )


def test_catalog_contract_accepts_maintain_tables(database_engine: Engine) -> None:
    """Head inventory includes the D91 tables, index, and unlaned stage."""
    with database_engine.connect() as connection:
        names = {
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                    " AND tablename IN ('p1_maintain_units', 'p1_lance_table_stats')"
                )
            )
        }
        index_exists = connection.execute(
            text(
                "SELECT 1 FROM pg_indexes WHERE schemaname = 'public'"
                " AND indexname = 'ix_p1_maintain_units_key'"
            )
        ).first()
    assert names == {"p1_maintain_units", "p1_lance_table_stats"}
    assert index_exists is not None
    assert lane_is_valid(stage="maintain_p1_index", lane=None)
    assert not lane_is_valid(stage="maintain_p1_index", lane="backfill")


def test_unlaned_maintain_rejects_a_lane(ledger: WorkLedger) -> None:
    """Enqueue of maintain_p1_index with a non-null lane is a hard error."""
    with pytest.raises(LaneRouteError):
        ledger.enqueue(
            work=EnqueueWork(
                deployment_id=_DEPLOYMENT_ID,
                target_kind=ProcessingTarget.P1_MAINTAIN_UNIT,
                target_id=uuid4(),
                stage=PipelineStage.MAINTAIN_P1_INDEX,
                component_version=P1_MAINTAIN_COMPONENT_VERSION,
                content_hash="p1-maintain:test",
                lane=ProcessingLane.STEADY,
            )
        )


def test_gates_default_off_skip_continuous_enqueue(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """Continuous enqueue is silent while both gates stay false."""
    catalog = _catalog(
        database_engine=database_engine,
        ledger=ledger,
        maintenance_enabled=False,
        heavy_enabled=False,
    )
    skipped = catalog.enqueue(request=_request())
    heavy = catalog.enqueue(
        request=_request(mode=P1MaintainMode.HEAVY, reason="threshold")
    )
    forced = catalog.enqueue(
        request=_request(mode=P1MaintainMode.HEAVY, reason="admin_force", force=True)
    )
    assert skipped.skipped == "maintenance_disabled"
    assert heavy.skipped == "maintenance_disabled"
    assert forced.created is True
    assert forced.unit_id is not None


def test_heavy_gate_blocks_when_maintenance_is_on(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """Light may enqueue when only the master gate is on; heavy stays off."""
    catalog = _catalog(
        database_engine=database_engine,
        ledger=ledger,
        maintenance_enabled=True,
        heavy_enabled=False,
    )
    light = catalog.enqueue(request=_request())
    heavy = catalog.enqueue(request=_request(mode=P1MaintainMode.HEAVY))
    assert light.created is True
    assert heavy.skipped == "heavy_disabled"


def test_pending_coalesce_does_not_create_a_second_unit(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """A second light enqueue bumps requested_at instead of inserting."""
    catalog = _catalog(database_engine=database_engine, ledger=ledger)
    first = catalog.enqueue(request=_request(reason="post_write"))
    second = catalog.enqueue(request=_request(reason="schedule"))
    assert first.created is True
    assert second.coalesced is True
    assert second.unit_id == first.unit_id
    with database_engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM p1_maintain_units")
        ).scalar_one()
        reason = connection.execute(
            text("SELECT reason FROM p1_maintain_units WHERE unit_id = :unit_id"),
            {"unit_id": first.unit_id},
        ).scalar_one()
    assert count == 1
    assert "post_write" in reason and "schedule" in reason


def test_running_enqueue_sets_rerun_and_complete_inserts_successor(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """An enqueue that races a live claim is consumed atomically at complete."""
    catalog = _catalog(database_engine=database_engine, ledger=ledger)
    opened = catalog.enqueue(request=_request(reason="threshold"))
    claimed = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.MAINTAIN_P1_INDEX, lane=None
    )
    assert isinstance(claimed, ClaimedWork)
    raced = catalog.enqueue(request=_request(reason="post_write"))
    assert raced.rerun_requested is True
    outcomes = ledger.complete_maintain_p1(
        processing_id=claimed.processing_id,
        unit_id=opened.unit_id or claimed.target_id,
        expected_attempt=claimed.attempt,
    )
    assert len(outcomes) == 1
    successor = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.MAINTAIN_P1_INDEX, lane=None
    )
    assert isinstance(successor, ClaimedWork)
    assert successor.target_id != claimed.target_id
    assert successor.payload is not None
    assert successor.payload["reason"] == "rerun"


def test_stale_attempt_cannot_complete_or_fail_replacement(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """Attempt A complete/fail are rejected while attempt B is running."""
    catalog = _catalog(
        database_engine=database_engine, ledger=ledger, running_stale_s=0.001
    )
    opened = catalog.enqueue(request=_request())
    first = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.MAINTAIN_P1_INDEX, lane=None
    )
    assert isinstance(first, ClaimedWork)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing_state SET started_at = now() - interval '1 hour'"
                " WHERE processing_id = :processing_id"
            ),
            {"processing_id": first.processing_id},
        )
    assert catalog.reclaim_stale() == 1
    second = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.MAINTAIN_P1_INDEX, lane=None
    )
    assert isinstance(second, ClaimedWork)
    assert second.processing_id == first.processing_id
    assert second.attempt == first.attempt + 1
    with pytest.raises(WorkNotRunningError):
        ledger.complete_maintain_p1(
            processing_id=first.processing_id,
            unit_id=opened.unit_id or first.target_id,
            expected_attempt=first.attempt,
        )
    with pytest.raises(WorkNotRunningError):
        ledger.fail(
            processing_id=first.processing_id,
            error="stale owner",
            retryable=True,
            expected_attempt=first.attempt,
        )
    ledger.complete_maintain_p1(
        processing_id=second.processing_id,
        unit_id=opened.unit_id or second.target_id,
        expected_attempt=second.attempt,
        skip_successor=True,
    )


def test_reclaim_skips_live_lock_holder_on_wall_clock_arm(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """Wall-clock fallback must not steal a table whose maintain lock is held."""
    catalog = _catalog(
        database_engine=database_engine, ledger=ledger, running_stale_s=0.001
    )
    catalog.enqueue(request=_request())
    claimed = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.MAINTAIN_P1_INDEX, lane=None
    )
    assert isinstance(claimed, ClaimedWork)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing_state SET started_at = now() - interval '1 hour'"
                " WHERE processing_id = :processing_id"
            ),
            {"processing_id": claimed.processing_id},
        )
    with hold_p1_table_maintain_locks(
        engine=database_engine,
        lance_root=_LANCE_KEY,
        tables=("facts",),
        timeout=timedelta(seconds=2),
    ):
        assert catalog.reclaim_stale() == 0
    assert catalog.reclaim_stale() == 1


def test_heartbeat_stale_reclaims_without_lock_probe(
    database_engine: Engine, ledger: WorkLedger
) -> None:
    """A frozen heartbeat is enough to reclaim even while the table lock is held."""
    catalog = _catalog(database_engine=database_engine, ledger=ledger, heartbeat_s=1.0)
    opened = catalog.enqueue(request=_request())
    claimed = ledger.claim_one(
        deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.MAINTAIN_P1_INDEX, lane=None
    )
    assert isinstance(claimed, ClaimedWork)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE p1_maintain_units"
                " SET last_heartbeat_at = now() - interval '10 seconds'"
                " WHERE unit_id = :unit_id"
            ),
            {"unit_id": opened.unit_id},
        )
    with hold_p1_table_maintain_locks(
        engine=database_engine,
        lance_root=_LANCE_KEY,
        tables=("facts",),
        timeout=timedelta(seconds=2),
    ):
        assert catalog.reclaim_stale() == 1
