"""D91 ledger-backed P1 maintain units: enqueue coalesce, reclaim, complete."""

from collections.abc import Mapping
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from pathlib import Path
from typing import Any
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
from rememberstack.model import QueueRoute
from rememberstack.model import WorkNotFoundError
from rememberstack.model import WorkNotRunningError
from rememberstack.model.p1_maintain import P1MaintainCompleteRequest
from rememberstack.model.p1_maintain import P1MaintainEnqueueRequest
from rememberstack.model.p1_maintain import P1MaintainEnqueueResult
from rememberstack.model.p1_maintain import P1MaintainMode
from rememberstack.ports.queue import TaskQueuePort
from rememberstack.spine.p1_maintain_lock import p1_table_maintain_lock_key
from rememberstack.spine.work_ledger import enqueue_on
from rememberstack.spine.work_ledger import WorkLedger
from rememberstack.spine.work_ledger import WorkLedgerSettings

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
    reclaim_min_s: float = Field(default=60.0, ge=0)
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
        queue: TaskQueuePort | None = None,
    ) -> None:
        """Bind the catalog to the ledger and default-off maintain gates."""
        self._engine = engine
        self._ledger = ledger
        self._settings = settings or P1MaintainSettings()
        self._queue = queue
        self._last_reclaim_at: datetime | None = None

    @classmethod
    def from_engine(
        cls,
        *,
        engine: Engine,
        settings: P1MaintainSettings | None = None,
        queue: TaskQueuePort | None = None,
    ) -> Self:
        """Compose a catalog that shares the engine with a fresh ledger."""
        return cls(
            engine=engine,
            ledger=WorkLedger(engine=engine, settings=WorkLedgerSettings()),
            settings=settings,
            queue=queue,
        )

    def enqueue(self, *, request: P1MaintainEnqueueRequest) -> P1MaintainEnqueueResult:
        """Open or coalesce one (root, table, mode) unit under an xact lock."""
        skipped = self._gate_skip(request=request)
        if skipped is not None:
            return P1MaintainEnqueueResult(skipped=skipped)
        request = request.model_copy(
            update={"lance_root_key": lance_root_key(lance_root=request.lance_root_key)}
        )
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
                _coalesce_open(
                    connection=connection,
                    unit_id=open_row["unit_id"],
                    processing_id=open_row["processing_id"],
                    reason=request.reason,
                    force=request.force,
                    not_before=request.not_before,
                    rerun=False,
                )
                return P1MaintainEnqueueResult(
                    unit_id=open_row["unit_id"],
                    processing_id=open_row["processing_id"],
                    coalesced=True,
                )
            if open_row is not None and open_row["status"] == "running":
                if _unit_is_reclaimable(
                    row=dict(open_row),
                    now=datetime.now(tz=UTC),
                    heartbeat_stale=self._heartbeat_stale(),
                    running_stale=timedelta(seconds=self._settings.running_stale_s),
                ):
                    _fail_running_on(
                        connection=connection,
                        processing_id=open_row["processing_id"],
                        expected_attempt=int(open_row["attempts"]),
                        error="stale maintain claim reclaimed on enqueue",
                    )
                else:
                    _coalesce_open(
                        connection=connection,
                        unit_id=open_row["unit_id"],
                        processing_id=open_row["processing_id"],
                        reason=request.reason,
                        force=request.force,
                        not_before=None,
                        rerun=True,
                    )
                    return P1MaintainEnqueueResult(
                        unit_id=open_row["unit_id"],
                        processing_id=open_row["processing_id"],
                        rerun_requested=True,
                    )
            return _insert_open_unit(connection=connection, request=request)

    def reclaim_stale(self, *, deployment_id: UUID) -> int:
        """Fail reclaimable running maintain rows with an attempt fence.

        A heartbeat is trusted only when ``claimed_attempt`` matches the
        running ledger attempt. Unstamped or leftover attempt-A beats fall
        through to the wall-clock arm, which also requires the table lock
        to be free.
        """
        now = datetime.now(tz=UTC)
        if (
            self._last_reclaim_at is not None
            and (now - self._last_reclaim_at).total_seconds()
            < self._settings.reclaim_min_s
        ):
            return 0
        self._last_reclaim_at = now
        heartbeat_stale = self._heartbeat_stale()
        running_stale = timedelta(seconds=self._settings.running_stale_s)
        reclaimed = 0
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_RUNNING_UNITS,
                    {
                        "version": P1_MAINTAIN_COMPONENT_VERSION,
                        "deployment_id": deployment_id,
                    },
                )
                .mappings()
                .all()
            )
            connection.commit()
        for row in rows:
            mapping = dict(row)
            if not _unit_is_reclaimable(
                row=mapping,
                now=now,
                heartbeat_stale=heartbeat_stale,
                running_stale=running_stale,
            ):
                continue
            if _trusted_heartbeat(row=mapping) is None and not self._table_lock_is_free(
                lance_root_key=str(mapping["lance_root_key"]),
                table_name=str(mapping["table_name"]),
            ):
                continue
            try:
                scheduled = self._ledger.fail(
                    processing_id=mapping["processing_id"],
                    error="stale maintain claim reclaimed",
                    retryable=True,
                    expected_attempt=int(mapping["attempts"]),
                )
            except (WorkNotRunningError, WorkNotFoundError):
                continue
            self._announce(
                processing_id=mapping["processing_id"],
                deployment_id=deployment_id,
                not_before=scheduled,
            )
            reclaimed += 1
        return reclaimed

    def complete(
        self,
        *,
        request: P1MaintainCompleteRequest,
        follow_up: tuple[EnqueueWork, ...] = (),
    ) -> tuple[EnqueueOutcome, ...]:
        """Succeed one attempt and maybe insert a successor in the same transaction."""
        outcomes = complete_maintain_p1_on(
            engine=self._engine, request=request, follow_up=follow_up
        )
        due = request.deferred_successor_not_before or datetime.now(tz=UTC)
        with self._engine.connect() as connection:
            deployment_id = connection.execute(
                text("SELECT deployment_id FROM p1_maintain_units WHERE unit_id = :id"),
                {"id": request.unit_id},
            ).scalar_one()
        for outcome in outcomes:
            if outcome.created:
                self._announce(
                    processing_id=outcome.processing_id,
                    deployment_id=deployment_id,
                    not_before=due,
                )
        return outcomes

    def _heartbeat_stale(self) -> timedelta:
        """Age after which a trusted heartbeat is considered dead."""
        return timedelta(
            seconds=self._settings.heartbeat_s * self._settings.heartbeat_stale_mult
        )

    def _announce(
        self, *, processing_id: UUID, deployment_id: UUID, not_before: datetime | None
    ) -> None:
        """Announce retry/successor work after the ledger transaction commits."""
        if self._queue is None or not_before is None:
            return
        self._queue.announce(
            processing_id=processing_id,
            route_snapshot=QueueRoute(
                deployment_id=deployment_id,
                stage=PipelineStage.MAINTAIN_P1_INDEX,
                lane=None,
            ),
            not_before_snapshot=not_before,
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
        peeked = (
            connection.execute(_LOOKUP_UNIT, {"unit_id": request.unit_id})
            .mappings()
            .first()
        )
        if peeked is None:
            raise WorkNotFoundError(f"maintain unit {request.unit_id} does not exist")
        connection.execute(
            _ENQUEUE_LOCK,
            {
                "key": p1_maintain_enqueue_lock_key(
                    lance_root_key=str(peeked["lance_root_key"]),
                    table_name=str(peeked["table_name"]),
                    mode=str(peeked["mode"]),
                )
            },
        )
        unit = (
            connection.execute(_LOCK_UNIT, {"unit_id": request.unit_id})
            .mappings()
            .first()
        )
        if unit is None:
            raise WorkNotFoundError(f"maintain unit {request.unit_id} does not exist")
        updated = connection.execute(
            _COMPLETE_FENCED,
            {
                "processing_id": request.processing_id,
                "expected_attempt": request.expected_attempt,
                "unit_id": request.unit_id,
                "deployment_id": unit["deployment_id"],
                "version": P1_MAINTAIN_COMPONENT_VERSION,
            },
        ).rowcount
        if updated == 0:
            raise WorkNotRunningError(
                f"processing row {request.processing_id} is not running "
                f"at attempt {request.expected_attempt} for unit {request.unit_id}"
            )
        connection.execute(
            _WRITE_UNIT_RESULT, {"unit_id": request.unit_id, "result": request.result}
        )
        connection.execute(_CLEAR_RERUN, {"unit_id": request.unit_id})
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
                    unit=dict(unit),
                    reason=reason,
                    not_before=request.deferred_successor_not_before,
                )
            )
        outcomes.extend(
            enqueue_on(connection=connection, work=work) for work in follow_up
        )
        return tuple(outcomes)


def _insert_successor(
    *,
    connection: Connection,
    unit: Mapping[str, Any],
    reason: str,
    not_before: datetime | None,
) -> EnqueueOutcome:
    """Insert a fresh unit + pending ledger row for the same physical key."""
    mapping = dict(unit)
    force = bool(mapping.get("force"))
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
            "force": force,
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
                "force": force,
                "reason": reason,
            },
            not_before=not_before,
        ),
    )


def _insert_open_unit(
    *, connection: Connection, request: P1MaintainEnqueueRequest
) -> P1MaintainEnqueueResult:
    """Insert a fresh unit and unlaned pending ledger row."""
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
            "force": request.force,
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


def _coalesce_open(
    *,
    connection: Connection,
    unit_id: UUID,
    processing_id: UUID,
    reason: str,
    force: bool,
    not_before: datetime | None,
    rerun: bool,
) -> None:
    """Bump an open unit and monotonically preserve force."""
    connection.execute(
        _MARK_RERUN if rerun else _BUMP_REQUESTED,
        {"unit_id": unit_id, "reason": reason, "force": force},
    )
    connection.execute(
        _OR_PAYLOAD_FORCE, {"processing_id": processing_id, "force": force}
    )
    if not_before is not None:
        connection.execute(
            _MAYBE_LOWER_NOT_BEFORE,
            {"not_before": not_before, "processing_id": processing_id},
        )


def _fail_running_on(
    *, connection: Connection, processing_id: UUID, expected_attempt: int, error: str
) -> None:
    """Attempt-fence a running row to retryable failed inside the caller TX."""
    updated = connection.execute(
        _FAIL_RUNNING,
        {
            "processing_id": processing_id,
            "expected_attempt": expected_attempt,
            "error": error,
        },
    ).rowcount
    if updated == 0:
        raise WorkNotRunningError(
            f"processing row {processing_id} is not running at attempt {expected_attempt}"
        )


def _trusted_heartbeat(*, row: Mapping[str, Any]) -> datetime | None:
    """Return last_heartbeat_at only when it belongs to the running attempt."""
    heartbeat = row.get("last_heartbeat_at")
    claimed = row.get("claimed_attempt")
    attempts = row.get("attempts")
    if heartbeat is None or claimed is None or attempts is None:
        return None
    if int(claimed) != int(attempts):
        return None
    return heartbeat


def _unit_is_reclaimable(
    *,
    row: Mapping[str, Any],
    now: datetime,
    heartbeat_stale: timedelta,
    running_stale: timedelta,
) -> bool:
    """Return whether a running unit has missed its liveness proof."""
    mapping = dict(row)
    heartbeat = _trusted_heartbeat(row=mapping)
    if heartbeat is not None:
        return now - heartbeat >= heartbeat_stale
    started = mapping["started_at"]
    if started is None:
        return False
    return now - started >= running_stale


_SELECT_OPEN_UNIT = text(
    """
    SELECT u.unit_id, u.force, u.last_heartbeat_at, u.claimed_attempt,
           ps.processing_id, ps.status, ps.attempts, ps.started_at
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
        force = force OR :force,
        reason = CASE
          WHEN :reason = ANY (string_to_array(reason, ',')) THEN reason
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
        force = force OR :force,
        reason = CASE
          WHEN :reason = ANY (string_to_array(reason, ',')) THEN reason
          ELSE reason || ',' || :reason
        END
    WHERE unit_id = :unit_id
    """
)

_INSERT_UNIT = text(
    """
    INSERT INTO p1_maintain_units (
      unit_id, deployment_id, lance_root_key, table_name, mode, reason, force
    ) VALUES (
      :unit_id, :deployment_id, :lance_root_key, :table_name, :mode, :reason, :force
    )
    """
)

_LOOKUP_UNIT = text(
    """
    SELECT unit_id, deployment_id, lance_root_key, table_name, mode,
           rerun_requested, force
    FROM p1_maintain_units
    WHERE unit_id = :unit_id
    """
)

_LOCK_UNIT = text(
    """
    SELECT unit_id, deployment_id, lance_root_key, table_name, mode,
           rerun_requested, force
    FROM p1_maintain_units
    WHERE unit_id = :unit_id
    FOR UPDATE
    """
)

_SELECT_RUNNING_UNITS = text(
    """
    SELECT u.unit_id, u.lance_root_key, u.table_name, u.last_heartbeat_at,
           u.claimed_attempt, ps.processing_id, ps.attempts, ps.started_at
    FROM p1_maintain_units AS u
    JOIN processing_state AS ps
      ON ps.target_id = u.unit_id
     AND ps.deployment_id = u.deployment_id
     AND ps.stage = 'maintain_p1_index'
     AND ps.target_kind = 'p1_maintain_unit'
     AND ps.component_version = :version
    WHERE ps.status = 'running'
      AND u.deployment_id = :deployment_id
    """
)

_COMPLETE_FENCED = text(
    """
    UPDATE processing_state
    SET status = 'succeeded', finished_at = now()
    WHERE processing_id = :processing_id
      AND status = 'running'
      AND attempts = :expected_attempt
      AND target_id = :unit_id
      AND target_kind = 'p1_maintain_unit'
      AND stage = 'maintain_p1_index'
      AND component_version = :version
      AND deployment_id = :deployment_id
    """
)

_OR_PAYLOAD_FORCE = text(
    """
    UPDATE processing_state
    SET payload = jsonb_set(
          COALESCE(payload, '{}'::jsonb),
          '{force}',
          to_jsonb(
            COALESCE((payload ->> 'force')::boolean, false) OR :force
          )
        )
    WHERE processing_id = :processing_id
    """
)

_FAIL_RUNNING = text(
    """
    UPDATE processing_state
    SET status = 'failed',
        defer_reason = 'retry_backoff',
        not_before = now(),
        last_error = :error
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
