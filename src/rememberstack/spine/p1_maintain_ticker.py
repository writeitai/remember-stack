"""D93 locked ticker: choose ensure / compact / retrain per Lance table."""

from datetime import datetime
from datetime import timedelta
from datetime import UTC
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model.p1_maintain import MaintainReport
from rememberstack.model.p1_maintain import TableMaintainStats
from rememberstack.ports.p1_index import P1IndexMaintenancePort
from rememberstack.spine.admission import active_forget_id_on
from rememberstack.spine.p1_maintain_lock import hold_p1_table_maintain_locks
from rememberstack.spine.p1_maintain_lock import P1_MAINTAIN_TABLES
from rememberstack.spine.p1_maintain_lock import P1MaintainLockTimeout

P1Operation = Literal["ensure", "compact", "retrain", "skip"]

HEAVY_CHANGED_ROW_FRAC: dict[str, float] = {
    "chunks": 0.05,
    "claims": 0.15,
    "facts": 0.25,
    "entities": 0.25,
}
HEAVY_CHANGE_MASS: dict[str, float] = {
    "chunks": 2_000_000.0,
    "claims": 5_000_000.0,
    "facts": 8_000_000.0,
    "entities": 2_000_000.0,
}
HEAVY_ROW_GROWTH_PCT: dict[str, float] = {
    "chunks": 5.0,
    "claims": 15.0,
    "facts": 25.0,
    "entities": 25.0,
}
HEAVY_UNINDEXED_RATIO = 0.15


class P1MaintainSettings(BaseSettings):
    """Gates and dirt thresholds for the P1 maintain ticker (D93)."""

    model_config = SettingsConfigDict(
        env_prefix="REMEMBERSTACK_P1_MAINTAIN_", extra="ignore"
    )

    maintenance_enabled: bool = False
    heavy_enabled: bool = False
    poll_s: float = Field(default=60.0, gt=0)
    lock_try_ms: float = Field(default=50.0, gt=0)
    optimize_unindexed_rows: int = Field(default=100_000, ge=0)
    optimize_small_fragments: int = Field(default=2_000, ge=0)
    heavy_rebuild_min_hours: float = Field(default=24.0, ge=0)


class TickOutcome:
    """What one table tick did (or why it skipped)."""

    def __init__(
        self,
        *,
        table: str,
        operation: P1Operation,
        reason: str,
        report: MaintainReport | None = None,
    ) -> None:
        """Record one table decision."""
        self.table = table
        self.operation = operation
        self.reason = reason
        self.report = report


class P1MaintainTicker:
    """One locked pass over present P1 tables."""

    def __init__(
        self,
        *,
        engine: Engine,
        lance_root: Path,
        maintenance: P1IndexMaintenancePort,
        settings: P1MaintainSettings | None = None,
        deployment_id: UUID | None = None,
    ) -> None:
        """Bind the ticker to Postgres, the Lance port, and default-off gates."""
        self._engine = engine
        self._lance_root = Path(lance_root)
        self._maintenance = maintenance
        self._settings = settings or P1MaintainSettings()
        self._deployment_id = deployment_id
        self._root_key = str(self._lance_root.resolve())

    def tick(self) -> tuple[TickOutcome, ...]:
        """Try each present table once. Writers are never locked out."""
        if not self._settings.maintenance_enabled:
            return tuple(
                TickOutcome(table=name, operation="skip", reason="maintenance_disabled")
                for name in P1_MAINTAIN_TABLES
            )
        if self._forget_is_open():
            return tuple(
                TickOutcome(table=name, operation="skip", reason="forget_in_progress")
                for name in P1_MAINTAIN_TABLES
            )
        outcomes: list[TickOutcome] = []
        for table in P1_MAINTAIN_TABLES:
            outcomes.append(self._tick_table(table=table))
        return tuple(outcomes)

    def record_vector_rewrites(
        self, *, table: str, changed_rows: int, change_mass: float
    ) -> None:
        """Bump durable change-mass after a writer actually rewrote vectors."""
        record_p1_vector_rewrites(
            engine=self._engine,
            lance_root=self._lance_root,
            table=table,
            changed_rows=changed_rows,
            change_mass=change_mass,
        )

    def _tick_table(self, *, table: str) -> TickOutcome:
        """One try-lock attempt; skip immediately if purge or another tick holds."""
        try:
            with hold_p1_table_maintain_locks(
                engine=self._engine,
                lance_root=self._lance_root,
                tables=(table,),
                try_once=True,
            ):
                return self._run_locked(table=table)
        except P1MaintainLockTimeout:
            return TickOutcome(table=table, operation="skip", reason="lock_busy")

    def _run_locked(self, *, table: str) -> TickOutcome:
        """Choose ensure, compact, or retrain from live Lance stats."""
        try:
            stats = self._maintenance.maintenance_stats(table=table)
        except Exception as error:  # noqa: BLE001 — table may not exist yet
            self._stamp_error(table=table, error=str(error))
            return TickOutcome(
                table=table, operation="skip", reason="stats_unavailable"
            )
        if stats.row_count == 0 and stats.num_fragments == 0:
            return TickOutcome(table=table, operation="skip", reason="absent")
        if self._needs_ensure(stats=stats):
            return self._run_operation(table=table, stats=stats, operation="ensure")
        if self._needs_compact(stats=stats):
            return self._run_operation(table=table, stats=stats, operation="compact")
        if self._settings.heavy_enabled and self._needs_retrain(stats=stats):
            return self._run_operation(table=table, stats=stats, operation="retrain")
        return TickOutcome(table=table, operation="skip", reason="clean")

    def _run_operation(
        self, *, table: str, stats: TableMaintainStats, operation: P1Operation
    ) -> TickOutcome:
        """Run one Lance op, stamp only a real success, survive op errors."""
        stored = self._read_stats_row(table=table)
        due_rows = _as_int(stored.get("changed_rows_since_heavy") if stored else 0)
        due_mass = _as_float(stored.get("change_mass_since_heavy") if stored else 0.0)
        try:
            if operation == "ensure":
                report = self._maintenance.ensure_search_indexes(tables=(table,))
            elif operation == "compact":
                report = self._maintenance.optimize_tables(tables=(table,))
            else:
                report = self._maintenance.rebuild_vector_indexes(tables=(table,))
        except Exception as error:  # noqa: BLE001 — keep the ticker loop alive
            self._stamp_error(table=table, error=str(error))
            return TickOutcome(table=table, operation="skip", reason="op_failed")
        skipped = next((item.skipped for item in report.tables if item.skipped), None)
        if operation == "retrain" and skipped:
            self._stamp_error(table=table, error=f"retrain_skipped:{skipped}")
            return TickOutcome(
                table=table, operation="skip", reason=f"retrain_skipped:{skipped}"
            )
        self._stamp_success(
            table=table,
            stats=_after_stats(report=report, fallback=stats),
            operation=operation,
            due_rows=due_rows,
            due_mass=due_mass,
        )
        reason = {
            "ensure": "missing_or_wrong_index",
            "compact": "dirt",
            "retrain": "change_or_unindexed",
        }[operation]
        return TickOutcome(
            table=table, operation=operation, reason=reason, report=report
        )

    def _needs_ensure(self, *, stats: TableMaintainStats) -> bool:
        """Ensure when live indexes are missing/wrong or the last error said so."""
        if stats.row_count > 0 and not stats.indexes_healthy:
            return True
        stored = self._read_stats_row(table=stats.table)
        if stored is None:
            return stats.row_count > 0
        return (
            stored["last_error"] is not None
            and "index" in str(stored["last_error"]).lower()
        )

    def _needs_compact(self, *, stats: TableMaintainStats) -> bool:
        """Compact when unindexed tails or small fragments exceed thresholds."""
        return (
            stats.unindexed_rows >= self._settings.optimize_unindexed_rows
            or stats.num_small_fragments >= self._settings.optimize_small_fragments
        )

    def _needs_retrain(self, *, stats: TableMaintainStats) -> bool:
        """Retrain when per-table change-mass, growth, or leftover unindexed trips."""
        stored = self._read_stats_row(table=stats.table)
        if stored is not None and stored["operator_state"] == "awaiting_operator":
            return False
        last_heavy = stored.get("last_heavy_at") if stored is not None else None
        if isinstance(last_heavy, datetime):
            age = datetime.now(tz=UTC) - last_heavy
            if age < timedelta(hours=self._settings.heavy_rebuild_min_hours):
                return False
        raw_changed = (
            stored.get("changed_rows_since_heavy") if stored is not None else 0
        )
        changed_rows = raw_changed if isinstance(raw_changed, int) else 0
        raw_mass = stored.get("change_mass_since_heavy") if stored is not None else 0.0
        change_mass = raw_mass if isinstance(raw_mass, (int, float)) else 0.0
        raw_baseline = (
            stored.get("last_heavy_row_count") if stored is not None else None
        )
        baseline = (
            raw_baseline
            if isinstance(raw_baseline, int) and raw_baseline > 0
            else stats.row_count
        )
        if changed_rows / max(baseline, 1) >= HEAVY_CHANGED_ROW_FRAC[stats.table]:
            return True
        if change_mass >= HEAVY_CHANGE_MASS[stats.table]:
            return True
        if (
            isinstance(raw_baseline, int)
            and raw_baseline > 0
            and (stats.row_count - raw_baseline) * 100 / raw_baseline
            >= HEAVY_ROW_GROWTH_PCT[stats.table]
        ):
            return True
        last_operation = stored.get("last_operation") if stored is not None else None
        if (
            last_operation == "compact"
            and stats.row_count > 0
            and stats.unindexed_rows / stats.row_count >= HEAVY_UNINDEXED_RATIO
        ):
            return True
        return False

    def _forget_is_open(self) -> bool:
        """Skip the whole estate while hard-forget is honoring a manifest."""
        if self._deployment_id is None:
            return False
        with self._engine.connect() as connection:
            active = active_forget_id_on(
                connection=connection, deployment_id=self._deployment_id
            )
            connection.commit()
        return active is not None

    def _read_stats_row(self, *, table: str) -> dict[str, object] | None:
        """Load the durable control row, or None before the first write."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    _SELECT_STATS,
                    {"lance_root_key": self._root_key, "table_name": table},
                )
                .mappings()
                .first()
            )
            connection.commit()
        return dict(row) if row is not None else None

    def _stamp_success(
        self,
        *,
        table: str,
        stats: TableMaintainStats,
        operation: str,
        due_rows: int = 0,
        due_mass: float = 0.0,
    ) -> None:
        """Write after-snapshot counters after a successful op."""
        with self._engine.begin() as connection:
            connection.execute(
                _UPSERT_SUCCESS,
                {
                    "lance_root_key": self._root_key,
                    "table_name": table,
                    "row_count": stats.row_count,
                    "unindexed": stats.unindexed_rows,
                    "fragments": stats.num_fragments,
                    "small_fragments": stats.num_small_fragments,
                    "operation": operation,
                    "stamp_light": operation == "compact",
                    "stamp_heavy": operation == "retrain",
                    "clear_mass": operation == "retrain",
                    "due_rows": due_rows,
                    "due_mass": due_mass,
                },
            )

    def _stamp_error(self, *, table: str, error: str) -> None:
        """Record a probe/op failure without inventing row counts."""
        with self._engine.begin() as connection:
            connection.execute(
                _UPSERT_ERROR,
                {"lance_root_key": self._root_key, "table_name": table, "error": error},
            )


def record_p1_vector_rewrites(
    *,
    engine: Engine,
    lance_root: Path,
    table: str,
    changed_rows: int,
    change_mass: float,
) -> None:
    """Writer hook: increment table stats after a Lance vector rewrite."""
    if changed_rows < 0 or change_mass < 0:
        raise ValueError("vector rewrite counters must be non-negative")
    if table not in P1_MAINTAIN_TABLES:
        raise ValueError(f"unknown P1 table {table}")
    with engine.begin() as connection:
        connection.execute(
            _UPSERT_CHANGE,
            {
                "lance_root_key": str(Path(lance_root).resolve()),
                "table_name": table,
                "changed_rows": changed_rows,
                "change_mass": change_mass,
            },
        )


def _after_stats(
    *, report: MaintainReport, fallback: TableMaintainStats
) -> TableMaintainStats:
    """Prefer the port's after-snapshot when the op reported one."""
    return report.tables[0] if report.tables else fallback


def _as_int(value: object) -> int:
    """Coerce a stats counter; unknown types count as zero."""
    return value if isinstance(value, int) else 0


def _as_float(value: object) -> float:
    """Coerce a stats mass; unknown types count as zero."""
    return float(value) if isinstance(value, (int, float)) else 0.0


_SELECT_STATS = text(
    """
    SELECT last_heavy_at, last_error, operator_state, changed_rows_since_heavy,
           change_mass_since_heavy, last_heavy_row_count, last_operation
    FROM p1_lance_table_stats
    WHERE lance_root_key = :lance_root_key AND table_name = :table_name
    """
)

_UPSERT_CHANGE = text(
    """
    INSERT INTO p1_lance_table_stats (
      lance_root_key, table_name, changed_rows_since_heavy, change_mass_since_heavy,
      last_maintain_enqueue_at, updated_at
    ) VALUES (
      :lance_root_key, :table_name, :changed_rows, :change_mass, now(), now()
    )
    ON CONFLICT (lance_root_key, table_name) DO UPDATE SET
      changed_rows_since_heavy = p1_lance_table_stats.changed_rows_since_heavy
        + EXCLUDED.changed_rows_since_heavy,
      change_mass_since_heavy = p1_lance_table_stats.change_mass_since_heavy
        + EXCLUDED.change_mass_since_heavy,
      last_maintain_enqueue_at = now(),
      updated_at = now()
    """
)

_UPSERT_SUCCESS = text(
    """
    INSERT INTO p1_lance_table_stats (
      lance_root_key, table_name, row_count, last_unindexed_rows,
      last_num_fragments, last_num_small_fragments, last_operation,
      last_error, last_light_at, last_heavy_at, last_heavy_row_count,
      changed_rows_since_heavy, change_mass_since_heavy, updated_at
    ) VALUES (
      :lance_root_key, :table_name, :row_count, :unindexed,
      :fragments, :small_fragments, :operation,
      NULL,
      CASE WHEN :stamp_light THEN now() ELSE NULL END,
      CASE WHEN :stamp_heavy THEN now() ELSE NULL END,
      :row_count,
      0,
      0,
      now()
    )
    ON CONFLICT (lance_root_key, table_name) DO UPDATE SET
      row_count = EXCLUDED.row_count,
      last_unindexed_rows = EXCLUDED.last_unindexed_rows,
      last_num_fragments = EXCLUDED.last_num_fragments,
      last_num_small_fragments = EXCLUDED.last_num_small_fragments,
      last_operation = EXCLUDED.last_operation,
      last_error = NULL,
      last_light_at = CASE WHEN :stamp_light THEN now()
                           ELSE p1_lance_table_stats.last_light_at END,
      last_heavy_at = CASE WHEN :stamp_heavy THEN now()
                           ELSE p1_lance_table_stats.last_heavy_at END,
      last_heavy_row_count = CASE
        WHEN :stamp_heavy THEN EXCLUDED.row_count
        WHEN p1_lance_table_stats.last_heavy_row_count IS NULL THEN EXCLUDED.row_count
        ELSE p1_lance_table_stats.last_heavy_row_count
      END,
      changed_rows_since_heavy = CASE WHEN :clear_mass
        THEN GREATEST(0, p1_lance_table_stats.changed_rows_since_heavy - :due_rows)
        ELSE p1_lance_table_stats.changed_rows_since_heavy END,
      change_mass_since_heavy = CASE WHEN :clear_mass
        THEN GREATEST(0.0, p1_lance_table_stats.change_mass_since_heavy - :due_mass)
        ELSE p1_lance_table_stats.change_mass_since_heavy END,
      updated_at = now()
    """
)

_UPSERT_ERROR = text(
    """
    INSERT INTO p1_lance_table_stats (
      lance_root_key, table_name, last_error, updated_at
    ) VALUES (
      :lance_root_key, :table_name, :error, now()
    )
    ON CONFLICT (lance_root_key, table_name) DO UPDATE SET
      last_error = EXCLUDED.last_error,
      updated_at = now()
    """
)
