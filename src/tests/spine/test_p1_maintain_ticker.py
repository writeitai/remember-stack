"""Postgres proofs for the D93 locked maintain ticker."""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model.p1_maintain import MaintainReport
from rememberstack.model.p1_maintain import TableMaintainStats
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.p1_maintain_lock import hold_p1_table_maintain_locks
from rememberstack.spine.p1_maintain_ticker import P1MaintainSettings
from rememberstack.spine.p1_maintain_ticker import P1MaintainTicker
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("91000000-0000-0000-0000-000000000091")
_LANCE = Path("/tmp/rememberstack-d91-ticker-lance")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head for ticker proofs."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for D93 ticker proofs")
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
    """Fresh deployment so forget probes have a tenant."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d91-ticker",
            name="D93 ticker proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


class _FakeMaintenance:
    """Records port calls and returns scripted stats."""

    def __init__(self) -> None:
        self.ensures = 0
        self.optimizes = 0
        self.rebuilds = 0
        self.skip_rebuild: str | None = None
        self.stats = TableMaintainStats(table="facts", row_count=10, num_fragments=1)

    def ensure_search_indexes(self, *, tables=None) -> MaintainReport:
        """Count ensure calls."""
        self.ensures += 1
        return MaintainReport(tables=(self.stats,))

    def optimize_tables(
        self, *, tables=None, cleanup_older_than=None
    ) -> MaintainReport:
        """Count compact calls."""
        self.optimizes += 1
        return MaintainReport(tables=(self.stats,))

    def rebuild_vector_indexes(self, *, tables=None) -> MaintainReport:
        """Count retrain calls."""
        self.rebuilds += 1
        skipped = self.skip_rebuild
        return MaintainReport(
            tables=(self.stats.model_copy(update={"skipped": skipped}),)
        )

    def rebuild_text_indexes(self, *, tables=None) -> MaintainReport:
        """Unused in these proofs."""
        return MaintainReport()

    def build_search_indexes(self) -> None:
        """Unused convenience surface."""
        return None

    def maintenance_stats(self, *, table: str) -> TableMaintainStats:
        """Return the scripted snapshot, tagged with the requested table."""
        return self.stats.model_copy(update={"table": table})


def _ticker(
    *,
    engine: Engine,
    maintenance: _FakeMaintenance,
    enabled: bool = True,
    heavy: bool = False,
    unindexed: int = 0,
    small_fragments: int = 0,
) -> P1MaintainTicker:
    """Ticker with explicit gates and dirt thresholds."""
    maintenance.stats = maintenance.stats.model_copy(
        update={
            "unindexed_rows": unindexed,
            "num_small_fragments": small_fragments,
            "row_count": max(10, unindexed),
            "num_fragments": 1,
        }
    )
    return P1MaintainTicker(
        engine=engine,
        lance_root=_LANCE,
        maintenance=maintenance,
        settings=P1MaintainSettings(
            maintenance_enabled=enabled,
            heavy_enabled=heavy,
            optimize_unindexed_rows=100,
            optimize_small_fragments=10,
            heavy_rebuild_min_hours=0,
            lock_try_ms=80,
        ),
        deployment_id=_DEPLOYMENT_ID,
    )


def test_gates_off_skip_every_table(database_engine: Engine) -> None:
    """Default-off ticker must not touch Lance."""
    fake = _FakeMaintenance()
    ticker = _ticker(engine=database_engine, maintenance=fake, enabled=False)
    outcomes = ticker.tick()
    assert {item.reason for item in outcomes} == {"maintenance_disabled"}
    assert fake.ensures == fake.optimizes == fake.rebuilds == 0


def test_first_tick_ensures_then_dirt_compacts(database_engine: Engine) -> None:
    """No stats row + rows present → ensure; later dirt → compact."""
    fake = _FakeMaintenance()
    first = _ticker(engine=database_engine, maintenance=fake).tick()
    assert any(item.operation == "ensure" for item in first)
    assert fake.ensures == 4
    fake = _FakeMaintenance()
    second = _ticker(engine=database_engine, maintenance=fake, unindexed=500).tick()
    assert any(item.operation == "compact" for item in second)
    assert fake.optimizes == 4
    assert fake.rebuilds == 0


def test_vector_rewrite_plus_heavy_gate_retrains(database_engine: Engine) -> None:
    """Change-mass with heavy_enabled chooses retrain when compact is not due."""
    fake = _FakeMaintenance()
    ticker = _ticker(engine=database_engine, maintenance=fake, heavy=True)
    ticker.record_vector_rewrites(table="facts", changed_rows=3, change_mass=100.0)
    outcomes = ticker.tick()
    facts = next(item for item in outcomes if item.table == "facts")
    assert facts.operation == "retrain"
    assert fake.rebuilds >= 1


def test_small_fact_rewrite_does_not_retrain(database_engine: Engine) -> None:
    """Facts need a 25% changed-row fraction; one rewrite of ten is not enough."""
    fake = _FakeMaintenance()
    ticker = _ticker(engine=database_engine, maintenance=fake, heavy=True)
    ticker.tick()
    ticker.record_vector_rewrites(table="facts", changed_rows=1, change_mass=10.0)
    fake.rebuilds = 0
    outcomes = ticker.tick()
    facts = next(item for item in outcomes if item.table == "facts")
    assert facts.operation == "skip"
    assert fake.rebuilds == 0


def test_unhealthy_indexes_ensure_after_first_stamp(database_engine: Engine) -> None:
    """Live index health, not the mere presence of a stats row, drives ensure."""
    fake = _FakeMaintenance()
    ticker = _ticker(engine=database_engine, maintenance=fake)
    ticker.tick()
    fake.ensures = 0
    fake.stats = fake.stats.model_copy(update={"indexes_healthy": False})
    outcomes = ticker.tick()
    assert any(item.operation == "ensure" for item in outcomes)
    assert fake.ensures == 4


def test_unindexed_ratio_retrains_only_after_compact(database_engine: Engine) -> None:
    """15% unindexed is a post-compact signal, not a first-touch heavy trigger."""
    fake = _FakeMaintenance()
    ticker = _ticker(engine=database_engine, maintenance=fake, heavy=True, unindexed=2)
    ticker.tick()
    fake.rebuilds = 0
    outcomes = ticker.tick()
    facts = next(item for item in outcomes if item.table == "facts")
    assert facts.operation == "skip"
    assert fake.rebuilds == 0


def test_skipped_retrain_keeps_change_mass(database_engine: Engine) -> None:
    """below_min_rows must not stamp last_heavy or wipe writer counters."""
    fake = _FakeMaintenance()
    fake.skip_rebuild = "below_min_rows"
    ticker = _ticker(engine=database_engine, maintenance=fake, heavy=True)
    ticker.tick()
    ticker.record_vector_rewrites(table="facts", changed_rows=5, change_mass=200.0)
    fake.rebuilds = 0
    outcomes = ticker.tick()
    facts = next(item for item in outcomes if item.table == "facts")
    assert facts.operation == "skip"
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT changed_rows_since_heavy FROM p1_lance_table_stats "
                "WHERE table_name = 'facts'"
            )
        ).one()
    assert row[0] == 5


def test_busy_table_lock_skips_without_waiting(database_engine: Engine) -> None:
    """A held purge lock makes the ticker skip that table immediately."""
    fake = _FakeMaintenance()
    ticker = _ticker(engine=database_engine, maintenance=fake)
    with hold_p1_table_maintain_locks(
        engine=database_engine,
        lance_root=_LANCE,
        tables=("facts",),
        timeout=timedelta(seconds=2),
    ):
        outcomes = ticker.tick()
    facts = next(item for item in outcomes if item.table == "facts")
    assert facts.operation == "skip"
    assert facts.reason == "lock_busy"
