"""D91 ledger-backed P1 maintain units: enqueue coalesce, reclaim, complete."""

from datetime import datetime
from datetime import timedelta
from datetime import UTC
from pathlib import Path
from typing import Self
from uuid import UUID
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy import bindparam
from sqlalchemy import Connection
from sqlalchemy import DateTime
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import EnqueueOutcome
from rememberstack.model import EnqueueWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.model import WorkNotFoundError
from rememberstack.model import WorkNotRunningError
from rememberstack.model.p1_maintain import P1MaintainCompleteRequest
from rememberstack.model.p1_maintain import P1MaintainEnqueueRequest
from rememberstack.model.p1_maintain import P1MaintainEnqueueResult
from rememberstack.model.p1_maintain import P1MaintainMode
from rememberstack.spine.p1_maintain_lock import p1_table_maintain_lock_key
from rememberstack.spine.work_ledger import enqueue_on
from rememberstack.spine.work_ledger import WorkLedger

P1_MAINTAIN_COMPONENT_VERSION = "p1-lance-maintain-2026.08"
_ADMIN_FORCE_REASON = "admin_force"
_ENQUEUE_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
_TRY_TABLE_LOCK = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
_UNLOCK_TABLE = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")


class P1MaintainSettings(BaseSettings):
    """Gates and reclaim floors for continuous P1 Lance maintenance (D91)."""

    model_config = SettingsConfigDict(
        env_prefix="REMEMBERSTACK_P1_MAINTAIN_", extra="ignore"
    )

    maintenance_enabled: bool = False
    heavy_enabled: bool = False
    reclaim_min_s: float = Field(default=60.0, gt=0)
    running_stale_s: float = Field(default=7200.0, gt=0)
    heartbeat_s: float = Field(default=60.0, gt=0)
    heartbeat_stale_mult: float = Field(default=3.0, gt=0)


def lance_root_key(*, lance_root: Path | str) -> str:
    """Canonical string identity of one physical Lance estate."""
    return str(Path(lance_root).resolve())


def p1_maintain_enqueue_lock_key(
    *, lance_root_key: str, table_name: str, mode: str
) -> str:
    """Xact lock material so coalesce cannot insert a second open unit."""
    return f"p1-lance-enqueue:{lance_root_key}:{table_name}:{mode}"


def maintain_content_hash(
    *, lance_root_key: str, table_name: str, mode: str, unit_id: UUID
) -> str:
    """Diagnostic content_hash required by processing_state NOT NULL."""
    return f"p1-maintain:{lance_root_key}:{table_name}:{mode}:{unit_id}"


class P1MaintainCatalog:
    """Coalesce, reclaim, and complete table-scoped maintain units."""

    def __init__(
        self,
        *,
        engine: Engine,
        ledger: WorkLedger,
        settings: P1MaintainSettings | None = None,
    ) -> None:
        """Bind the catalog to the ledger and default-off maintain gates."""
        self._engine = engine
        self._ledger = ledger
        self._settings = settings or P1MaintainSettings()

    @classmethod
    def from_engine(
        cls, *, engine: Engine, settings: P1MaintainSettings | None = None
    ) -> Self:
        """Compose a catalog that shares the engine with a fresh ledger."""
        return cls(engine=engine, ledger=WorkLedger(engine=engine), settings=settings)

    def enqueue(self, *, request: P1MaintainEnqueueRequest) -> P1MaintainEnqueueResult:
        """Open or coalesce one (root, table, mode) unit under an xact lock."""
        skipped = self._gate_skip(request=request)
        if skipped is not None:
            return P1MaintainEnqueueResult(skipped=skipped)
        with self._engine.begin() as connection:
            connection.execute(
                _ENQUEUE_LOCK,
                {
                    "key": p1_maintain_enqueue_lock_key(
                        lance_root_key=request.lance_root_key,
                        table_name=request.table_name.value,
                        mode=request.mode.value,
                    )
                },
            )
            open_row = (
                connection.execute(
                    _SELECT_OPEN_UNIT,
                    {
                        "lance_root_key": request.lance_root_key,
                        "table_name": request.table_name.value,
                        "mode": request.mode.value,
                        "version": P1_MAINTAIN_COMPONENT_VERSION,
                    },
                )
                .mappings()
                .first()
            )
            if open_row is not None and open_row["status"] in ("pending", "failed"):
                connection.execute(
                    _BUMP_REQUESTED,
                    {"unit_id": open_row["unit_id"], "reason": request.reason},
                )
                if request.not_before is not None:
                    connection.execute(
                        _MAYBE_LOWER_NOT_BEFORE,
                        {
                            "not_before": request.not_before,
                            "processing_id": open_row["processing_id"],
                        },
                    )
                return P1MaintainEnqueueResult(
                    unit_id=open_row["unit_id"],
                    processing_id=open_row["processing_id"],
                    coalesced=True,
                )
            if open_row is not None and open_row["status"] == "running":
                connection.execute(
                    _MARK_RERUN,
                    {"unit_id": open_row["unit_id"], "reason": request.reason},
                )
                return P1MaintainEnqueueResult(
                    unit_id=open_row["unit_id"],
                    processing_id=open_row["processing_id"],
                    rerun_requested=True,
                )
            unit_id = uuid4()
            connection.execute(
                _INSERT_UNIT,
                {
                    "unit_id": unit_id,
                    "deployment_id": request.deployment_id,
                    "lance_root_key": request.lance_root_key,
                    "table_name": request.table_name.value,
                    "mode": request.mode.value,
                    "reason": request.reason,
                },
            )
            outcome = enqueue_on(
                connection=connection,
                work=EnqueueWork(
                    deployment_id=request.deployment_id,
                    target_kind=ProcessingTarget.P1_MAINTAIN_UNIT,
                    target_id=unit_id,
                    stage=PipelineStage.MAINTAIN_P1_INDEX,
                    component_version=P1_MAINTAIN_COMPONENT_VERSION,
                    content_hash=maintain_content_hash(
                        lance_root_key=request.lance_root_key,
                        table_name=request.table_name.value,
                        mode=request.mode.value,
                        unit_id=unit_id,
                    ),
                    lane=None,
                    payload={
                        "mode": request.mode.value,
                        "table": request.table_name.value,
                        "force": request.force,
                        "reason": request.reason,
                    },
                    not_before=request.not_before,
                ),
            )
            return P1MaintainEnqueueResult(
                unit_id=unit_id, processing_id=outcome.processing_id, created=True
            )

    def reclaim_stale(self) -> int:
        """Fail reclaimable running maintain rows with an attempt fence.

        Heartbeat-stale rows reclaim without probing the table lock. The
        wall-clock arm (no heartbeat) also requires the table maintain lock
        to be free so a live owner whose heartbeat thread died is not stolen.
        """
        heartbeat_stale = timedelta(
            seconds=self._settings.heartbeat_s * self._settings.heartbeat_stale_mult
        )
        running_stale = timedelta(seconds=self._settings.running_stale_s)
        reclaimed = 0
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_RUNNING_UNITS, {"version": P1_MAINTAIN_COMPONENT_VERSION}
                )
                .mappings()
                .all()
            )
            connection.commit()
        now = datetime.now(tz=UTC)
        for row in rows:
            if not _unit_is_reclaimable(
                row=row,
                now=now,
                heartbeat_stale=heartbeat_stale,
                running_stale=running_stale,
            ):
                continue
            if row["last_heartbeat_at"] is None and not self._table_lock_is_free(
                lance_root_key=str(row["lance_root_key"]),
                table_name=str(row["table_name"]),
            ):
                continue
            try:
                self._ledger.fail(
                    processing_id=row["processing_id"],
                    error="stale maintain claim reclaimed",
                    retryable=True,
                    expected_attempt=int(row["attempts"]),
                )
            except (WorkNotRunningError, WorkNotFoundError):
                continue
            reclaimed += 1
        return reclaimed

    def complete(
        self,
        *,
        request: P1MaintainCompleteRequest,
        follow_up: tuple[EnqueueWork, ...] = (),
    ) -> tuple[EnqueueOutcome, ...]:
        """Succeed one attempt and maybe insert a successor in the same transaction."""
        return complete_maintain_p1_on(
            engine=self._engine, request=request, follow_up=follow_up
        )

    def _gate_skip(self, *, request: P1MaintainEnqueueRequest) -> str | None:
        """Return a skip reason when continuous enqueue is gated off."""
        admin_force = request.reason == _ADMIN_FORCE_REASON or request.force
        if not self._settings.maintenance_enabled and not admin_force:
            return "maintenance_disabled"
        if (
            request.mode is P1MaintainMode.HEAVY
            and not self._settings.heavy_enabled
            and not admin_force
        ):
            return "heavy_disabled"
        return None

    def _table_lock_is_free(self, *, lance_root_key: str, table_name: str) -> bool:
        """Probe the table maintain session lock and drop it immediately if taken."""
        key = p1_table_maintain_lock_key(
            lance_root=lance_root_key, table_name=table_name
        )
        with self._engine.connect() as connection:
            locked = connection.execute(_TRY_TABLE_LOCK, {"key": key}).scalar()
            if locked is True:
                connection.execute(_UNLOCK_TABLE, {"key": key})
            connection.commit()
        return locked is True


def complete_maintain_p1_on(
    *,
    engine: Engine,
    request: P1MaintainCompleteRequest,
    follow_up: tuple[EnqueueWork, ...] = (),
) -> tuple[EnqueueOutcome, ...]:
    """Attempt-fenced maintain complete used by WorkLedger and the catalog."""
    with engine.begin() as connection:
        unit = (
            connection.execute(_SELECT_UNIT, {"unit_id": request.unit_id})
            .mappings()
            .first()
        )
        if unit is None:
            raise WorkNotFoundError(f"maintain unit {request.unit_id} does not exist")
        connection.execute(
            _ENQUEUE_LOCK,
            {
                "key": p1_maintain_enqueue_lock_key(
                    lance_root_key=str(unit["lance_root_key"]),
                    table_name=str(unit["table_name"]),
                    mode=str(unit["mode"]),
                )
            },
        )
        updated = connection.execute(
            _COMPLETE_FENCED,
            {
                "processing_id": request.processing_id,
                "expected_attempt": request.expected_attempt,
            },
        ).rowcount
        if updated == 0:
            raise WorkNotRunningError(
                f"processing row {request.processing_id} is not running "
                f"at attempt {request.expected_attempt}"
            )
        connection.execute(
            _WRITE_UNIT_RESULT, {"unit_id": request.unit_id, "result": request.result}
        )
        outcomes: list[EnqueueOutcome] = []
        wants_successor = not request.skip_successor and (
            bool(unit["rerun_requested"])
            or request.deferred_successor_not_before is not None
        )
        if wants_successor:
            reason = request.successor_reason or (
                "deferred_heavy"
                if request.deferred_successor_not_before is not None
                else "rerun"
            )
            outcomes.append(
                _insert_successor(
                    connection=connection,
                    unit=unit,
                    reason=reason,
                    not_before=request.deferred_successor_not_before,
                )
            )
            connection.execute(_CLEAR_RERUN, {"unit_id": request.unit_id})
        outcomes.extend(
            enqueue_on(connection=connection, work=work) for work in follow_up
        )
        return tuple(outcomes)


def _insert_successor(
    *, connection: Connection, unit: object, reason: str, not_before: datetime | None
) -> EnqueueOutcome:
    """Insert a fresh unit + pending ledger row for the same physical key."""
    mapping = dict(unit)  # type: ignore[arg-type]
    unit_id = uuid4()
    connection.execute(
        _INSERT_UNIT,
        {
            "unit_id": unit_id,
            "deployment_id": mapping["deployment_id"],
            "lance_root_key": mapping["lance_root_key"],
            "table_name": mapping["table_name"],
            "mode": mapping["mode"],
            "reason": reason,
        },
    )
    return enqueue_on(
        connection=connection,
        work=EnqueueWork(
            deployment_id=mapping["deployment_id"],
            target_kind=ProcessingTarget.P1_MAINTAIN_UNIT,
            target_id=unit_id,
            stage=PipelineStage.MAINTAIN_P1_INDEX,
            component_version=P1_MAINTAIN_COMPONENT_VERSION,
            content_hash=maintain_content_hash(
                lance_root_key=str(mapping["lance_root_key"]),
                table_name=str(mapping["table_name"]),
                mode=str(mapping["mode"]),
                unit_id=unit_id,
            ),
            lane=None,
            payload={
                "mode": str(mapping["mode"]),
                "table": str(mapping["table_name"]),
                "force": False,
                "reason": reason,
            },
            not_before=not_before,
        ),
    )


def _unit_is_reclaimable(
    *, row: object, now: datetime, heartbeat_stale: timedelta, running_stale: timedelta
) -> bool:
    """Return whether a running unit has missed its liveness proof."""
    mapping = dict(row)  # type: ignore[arg-type]
    heartbeat = mapping["last_heartbeat_at"]
    if heartbeat is not None:
        return now - heartbeat >= heartbeat_stale
    started = mapping["started_at"]
    if started is None:
        return False
    return now - started >= running_stale


_SELECT_OPEN_UNIT = text(
    """
    SELECT u.unit_id, ps.processing_id, ps.status
    FROM p1_maintain_units AS u
    JOIN processing_state AS ps
      ON ps.target_id = u.unit_id
     AND ps.deployment_id = u.deployment_id
     AND ps.stage = 'maintain_p1_index'
     AND ps.target_kind = 'p1_maintain_unit'
     AND ps.component_version = :version
    WHERE u.lance_root_key = :lance_root_key
      AND u.table_name = :table_name
      AND u.mode = :mode
      AND ps.status IN ('pending', 'failed', 'running')
    ORDER BY CASE ps.status
               WHEN 'running' THEN 0
               WHEN 'pending' THEN 1
               ELSE 2
             END,
             u.requested_at
    FOR UPDATE OF u, ps
    """
)

_BUMP_REQUESTED = text(
    """
    UPDATE p1_maintain_units
    SET requested_at = now(),
        reason = CASE
          WHEN position(:reason IN reason) > 0 THEN reason
          ELSE reason || ',' || :reason
        END
    WHERE unit_id = :unit_id
    """
)

_MAYBE_LOWER_NOT_BEFORE = text(
    """
    UPDATE processing_state
    SET not_before = CASE
          WHEN not_before IS NULL OR :not_before < not_before THEN :not_before
          ELSE not_before
        END
    WHERE processing_id = :processing_id
      AND status IN ('pending', 'failed')
    """
).bindparams(bindparam("not_before", type_=DateTime(timezone=True)))

_MARK_RERUN = text(
    """
    UPDATE p1_maintain_units
    SET rerun_requested = true,
        reason = CASE
          WHEN position(:reason IN reason) > 0 THEN reason
          ELSE reason || ',' || :reason
        END
    WHERE unit_id = :unit_id
    """
)

_INSERT_UNIT = text(
    """
    INSERT INTO p1_maintain_units (
      unit_id, deployment_id, lance_root_key, table_name, mode, reason
    ) VALUES (
      :unit_id, :deployment_id, :lance_root_key, :table_name, :mode, :reason
    )
    """
)

_SELECT_UNIT = text(
    """
    SELECT unit_id, deployment_id, lance_root_key, table_name, mode,
           rerun_requested
    FROM p1_maintain_units
    WHERE unit_id = :unit_id
    FOR UPDATE
    """
)

_SELECT_RUNNING_UNITS = text(
    """
    SELECT u.unit_id, u.lance_root_key, u.table_name, u.last_heartbeat_at,
           ps.processing_id, ps.attempts, ps.started_at
    FROM p1_maintain_units AS u
    JOIN processing_state AS ps
      ON ps.target_id = u.unit_id
     AND ps.deployment_id = u.deployment_id
     AND ps.stage = 'maintain_p1_index'
     AND ps.target_kind = 'p1_maintain_unit'
     AND ps.component_version = :version
    WHERE ps.status = 'running'
    """
)

_COMPLETE_FENCED = text(
    """
    UPDATE processing_state
    SET status = 'succeeded', finished_at = now()
    WHERE processing_id = :processing_id
      AND status = 'running'
      AND attempts = :expected_attempt
    """
)

_WRITE_UNIT_RESULT = text(
    """
    UPDATE p1_maintain_units
    SET result = :result
    WHERE unit_id = :unit_id
    """
).bindparams(bindparam("result", type_=JSON))

_CLEAR_RERUN = text(
    """
    UPDATE p1_maintain_units
    SET rerun_requested = false
    WHERE unit_id = :unit_id
    """
)
