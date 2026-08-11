"""Transactional operations on the D67 work ledger (processing_state + cost_ledger).

The ledger is the sole work-truth (D12/D67): enqueue is idempotent on the
(deployment, target, stage, component version) key; claiming uses SKIP LOCKED
over the (deployment, stage, lane) route; attempts count handler executions that
actually began; failures keep their full traceback; the DLQ is
status='dead_letter' rows; billed calls copy their attribution from the locked
running row and callers can never supply it.
"""

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from typing import cast
from typing import Self
from uuid import UUID
from uuid import uuid4

from pydantic import model_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy import bindparam
from sqlalchemy import Connection
from sqlalchemy import DateTime
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.model import BudgetParked
from rememberstack.model import ClaimedWork
from rememberstack.model import CostBudget
from rememberstack.model import CostBudgetStatus
from rememberstack.model import CostTierSpend
from rememberstack.model import DeadLetterReplayResult
from rememberstack.model import EnqueueOutcome
from rememberstack.model import EnqueueWork
from rememberstack.model import ForgetInProgressError
from rememberstack.model import LaneRouteError
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import QueueRoute
from rememberstack.model import RecordCall
from rememberstack.model import WorkNotDeadLetterError
from rememberstack.model import WorkNotFoundError
from rememberstack.model import WorkNotRunningError
from rememberstack.spine.admission import active_forget_id_on
from rememberstack.spine.catalog_contract import lane_is_valid


class WorkLedgerSettings(BaseSettings):
    """Retry backoff plus explicit route budgets for one worker deployment."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_WORK_", extra="ignore")

    retry_backoff_base_s: float = 2.0
    retry_backoff_max_s: float = 60.0
    budgets: tuple[CostBudget, ...] = ()

    @model_validator(mode="after")
    def require_unique_valid_budget_routes(self) -> Self:
        """Reject ambiguous ceilings and stage/lane pairs that cannot be queued."""
        routes: set[tuple[UUID, PipelineStage, ProcessingLane | None]] = set()
        for budget in self.budgets:
            if not lane_is_valid(
                stage=budget.stage,
                lane=None if budget.lane is None else budget.lane.value,
            ):
                raise ValueError(
                    f"stage {budget.stage} does not accept budget lane {budget.lane!r}"
                )
            route = (budget.deployment_id, budget.stage, budget.lane)
            if route in routes:
                raise ValueError(
                    "only one cost budget may be configured per deployment, stage, and lane"
                )
            routes.add(route)
        return self


@dataclass(frozen=True)
class _BudgetWindowSpend:
    """The database-clock window and deduplicated spend used by one pre-flight."""

    started_at: datetime
    ends_at: datetime
    spent_usd: Decimal


class WorkLedger:
    """The spine's typed gateway to processing_state and cost_ledger (D12/D67)."""

    def __init__(self, *, engine: Engine, settings: WorkLedgerSettings) -> None:
        """Bind the ledger to an explicit engine and explicit settings."""
        self._engine = engine
        self._settings = settings

    def enqueue(self, *, work: EnqueueWork) -> EnqueueOutcome:
        """Insert one unit of work idempotently and return what happened.

        A duplicate of an existing (deployment, target, stage, version) key never
        creates a second unit of work. A steady enqueue promotes a pending/failed
        backfill duplicate to the steady lane (live work keeps its freshness
        guarantee); a backfill enqueue can never demote steady work (D67).
        """
        _require_valid_lane(stage=work.stage, lane=work.lane)
        with self._engine.begin() as connection:
            return enqueue_on(connection=connection, work=work)

    def claim_one(
        self, *, deployment_id: UUID, stage: PipelineStage, lane: ProcessingLane | None
    ) -> ClaimedWork | BudgetParked | None:
        """Claim, budget-park, or find no due row on one route.

        The due row is locked before the current route-window spend is checked.
        Exhaustion durably parks it until the aligned window rolls, without
        consuming an attempt or touching its last error. Otherwise claiming
        clears any defer reason, moves it to running, and increments attempts
        exactly once immediately before the handler begins.
        """
        _require_valid_lane(stage=stage, lane=lane)
        with self._engine.begin() as connection:
            if stage is not PipelineStage.HARD_FORGET:
                active_forget = active_forget_id_on(
                    connection=connection, deployment_id=deployment_id
                )
                if active_forget is not None:
                    raise ForgetInProgressError(
                        f"deployment {deployment_id} is honoring forget_id"
                        f" {active_forget}"
                    )
            row = (
                connection.execute(
                    _CLAIM_SELECT,
                    {"deployment_id": deployment_id, "stage": stage, "lane": lane},
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            budget = self._budget_for(
                deployment_id=deployment_id, stage=stage, lane=lane
            )
            if budget is not None:
                spend = _budget_window_spend(connection=connection, budget=budget)
                if spend.spent_usd >= budget.ceiling_usd:
                    connection.execute(
                        _PARK_BUDGET,
                        {
                            "processing_id": row["processing_id"],
                            "resume_at": spend.ends_at,
                        },
                    )
                    return BudgetParked(
                        processing_id=row["processing_id"],
                        resume_at=spend.ends_at,
                        spent_usd=spend.spent_usd,
                        ceiling_usd=budget.ceiling_usd,
                    )
            started = (
                connection.execute(
                    _CLAIM_START, {"processing_id": row["processing_id"]}
                )
                .mappings()
                .one()
            )
            return _claimed_work(row=started)

    def budget_status(self, *, deployment_id: UUID) -> tuple[CostBudgetStatus, ...]:
        """Return current spend and parked work for every configured deployment budget."""
        statuses: list[CostBudgetStatus] = []
        with self._engine.connect() as connection:
            for budget in self._settings.budgets:
                if budget.deployment_id != deployment_id:
                    continue
                spend = _budget_window_spend(connection=connection, budget=budget)
                tier_rows = connection.execute(
                    _BUDGET_TIER_SPEND,
                    {
                        "deployment_id": budget.deployment_id,
                        "stage": budget.stage,
                        "lane": budget.lane,
                        "window_started_at": spend.started_at,
                        "window_ends_at": spend.ends_at,
                    },
                ).mappings()
                tiers = tuple(
                    CostTierSpend(
                        tier=cast(str | None, row["tier"]),
                        cost_usd=_decimal(row["cost_usd"]),
                    )
                    for row in tier_rows
                )
                parked_work = int(
                    connection.execute(
                        _BUDGET_PARKED_COUNT,
                        {
                            "deployment_id": budget.deployment_id,
                            "stage": budget.stage,
                            "lane": budget.lane,
                        },
                    ).scalar_one()
                )
                remaining = max(Decimal(0), budget.ceiling_usd - spend.spent_usd)
                statuses.append(
                    CostBudgetStatus(
                        deployment_id=budget.deployment_id,
                        stage=budget.stage,
                        lane=budget.lane,
                        window_seconds=budget.window_seconds,
                        window_started_at=spend.started_at,
                        window_ends_at=spend.ends_at,
                        ceiling_usd=budget.ceiling_usd,
                        spent_usd=spend.spent_usd,
                        remaining_usd=remaining,
                        exhausted=spend.spent_usd >= budget.ceiling_usd,
                        parked_work=parked_work,
                        tiers=tiers,
                    )
                )
        return tuple(statuses)

    def complete(
        self, *, processing_id: UUID, follow_up: tuple[EnqueueWork, ...] = ()
    ) -> tuple[EnqueueOutcome, ...]:
        """Mark a running attempt succeeded and enqueue its chain follow-ups atomically.

        The chain rule (a completing stage enqueues the next stage for its target)
        commits in the same transaction as the success mark, so a crash can never
        record success without the follow-up work existing.
        """
        for work in follow_up:
            _require_valid_lane(stage=work.stage, lane=work.lane)
        with self._engine.begin() as connection:
            updated = connection.execute(
                _COMPLETE, {"processing_id": processing_id}
            ).rowcount
            if updated == 0:
                raise WorkNotRunningError(
                    f"processing row {processing_id} is not running; cannot complete"
                )
            return tuple(
                enqueue_on(connection=connection, work=work) for work in follow_up
            )

    def complete_chunk_extract(
        self,
        *,
        processing_id: UUID,
        barrier: object,
        follow_up: tuple[EnqueueWork, ...] = (),
    ) -> tuple[EnqueueOutcome, ...]:
        """D84: mark one chunk extract succeeded and maybe enqueue normalize.

        After this row is succeeded in the same transaction, require every chunk
        of the representation to have a succeeded extract_claims row at the
        extractor version **and** claim/decision/occurrence extract evidence.
        Only then enqueue version-level ``normalize_relations`` (idempotent).
        """
        from rememberstack.workers.base import ExtractChunkBarrier

        if not isinstance(barrier, ExtractChunkBarrier):
            raise TypeError("barrier must be ExtractChunkBarrier")
        for work in follow_up:
            _require_valid_lane(stage=work.stage, lane=work.lane)
        with self._engine.begin() as connection:
            # Serialize barrier evaluation for one representation (P1.1 dual-review).
            # PostgreSQL supports one bigint key or two integer keys, never two
            # bigint keys. Hash a namespaced UUID to one stable bigint, matching
            # the repository's other entity-scoped advisory locks.
            connection.execute(
                _ADVISORY_LOCK_REPRESENTATION,
                {"representation_id": barrier.representation_id},
            )
            updated = connection.execute(
                _COMPLETE, {"processing_id": processing_id}
            ).rowcount
            if updated == 0:
                raise WorkNotRunningError(
                    f"processing row {processing_id} is not running; cannot complete"
                )
            outcomes = [
                enqueue_on(connection=connection, work=work) for work in follow_up
            ]
            if _extract_barrier_ready(
                connection=connection,
                deployment_id=barrier.deployment_id,
                representation_id=barrier.representation_id,
                chunker_version=barrier.chunker_version,
                extractor_version=barrier.extractor_version,
            ):
                # D88: materialize the full claim normalize set atomically with
                # the extract barrier handoff (no partial fan-out).
                outcomes.extend(
                    _enqueue_claim_normalize_fanout(
                        connection=connection,
                        deployment_id=barrier.deployment_id,
                        version_id=barrier.version_id,
                        representation_id=barrier.representation_id,
                        chunker_version=barrier.chunker_version,
                        extractor_version=barrier.extractor_version,
                        normalize_component_version=barrier.normalize_component_version,
                        content_hash=barrier.content_hash,
                        lane=barrier.lane,
                    )
                )
            return tuple(outcomes)

    def complete_claim_normalize(
        self,
        *,
        processing_id: UUID,
        barrier: object,
        follow_up: tuple[EnqueueWork, ...] = (),
    ) -> tuple[EnqueueOutcome, ...]:
        """D88: mark one claim normalize succeeded and maybe enqueue downstream.

        Serializes on a representation-scoped advisory lock (same race class as
        D84). When every expected claim has succeeded at the fan-out normalizer
        version, enqueues the ordered observation flush stage for the version.
        """
        from rememberstack.model import ProcessingTarget
        from rememberstack.workers.base import ClaimNormalizeBarrier

        if not isinstance(barrier, ClaimNormalizeBarrier):
            raise TypeError("barrier must be ClaimNormalizeBarrier")
        for work in follow_up:
            _require_valid_lane(stage=work.stage, lane=work.lane)
        with self._engine.begin() as connection:
            claim_id = connection.execute(
                _SELECT_TARGET_ID, {"processing_id": processing_id}
            ).scalar_one()
            # Discover every version that lists this claim as a D56 occurrence
            # (including the work's own payload coordinates). Acquire ALL
            # representation locks in sorted order BEFORE complete+barrier so
            # concurrent multi-claim completes cannot deadlock.
            by_rep: dict[str, list[dict[str, object]]] = {}
            primary = {
                "deployment_id": barrier.deployment_id,
                "version_id": barrier.version_id,
                "representation_id": barrier.representation_id,
                "chunker_version": barrier.chunker_version,
                "doc_id": barrier.doc_id,
            }
            by_rep[str(barrier.representation_id)] = [primary]
            for row in connection.execute(
                _VERSIONS_WITH_CLAIM_OCCURRENCE,
                {
                    "claim_id": claim_id,
                    "extractor_version": barrier.extractor_version,
                    "deployment_id": barrier.deployment_id,
                },
            ).mappings():
                key = str(row["representation_id"])
                if (
                    row["representation_id"] == barrier.representation_id
                    and row["version_id"] == barrier.version_id
                ):
                    continue
                by_rep.setdefault(key, []).append(dict(row))
            for rep_key in sorted(by_rep):
                connection.execute(
                    _ADVISORY_LOCK_NORMALIZE_BARRIER,
                    {"representation_id": UUID(rep_key)},
                )
            updated = connection.execute(
                _COMPLETE, {"processing_id": processing_id}
            ).rowcount
            if updated == 0:
                raise WorkNotRunningError(
                    f"processing row {processing_id} is not running; cannot complete"
                )
            outcomes = [
                enqueue_on(connection=connection, work=work) for work in follow_up
            ]
            for rep_key in sorted(by_rep):
                for candidate in by_rep[rep_key]:
                    deployment_id = UUID(str(candidate["deployment_id"]))
                    version_id = UUID(str(candidate["version_id"]))
                    representation_id = UUID(str(candidate["representation_id"]))
                    chunker_version = str(candidate["chunker_version"])
                    # Do not open a sibling barrier until its extract set has
                    # closed (extract barrier ready) — otherwise a partial
                    # D56 attach can look like a complete normalize set.
                    if not _extract_barrier_ready(
                        connection=connection,
                        deployment_id=deployment_id,
                        representation_id=representation_id,
                        chunker_version=chunker_version,
                        extractor_version=barrier.extractor_version,
                    ):
                        continue
                    if not _normalize_claim_barrier_ready(
                        connection=connection,
                        deployment_id=deployment_id,
                        version_id=version_id,
                        representation_id=representation_id,
                        chunker_version=chunker_version,
                        extractor_version=barrier.extractor_version,
                        normalize_version=barrier.normalize_component_version,
                    ):
                        continue
                    outcomes.append(
                        enqueue_on(
                            connection=connection,
                            work=EnqueueWork(
                                deployment_id=deployment_id,
                                target_kind=ProcessingTarget.DOCUMENT_VERSION,
                                target_id=version_id,
                                stage=PipelineStage.ADJUDICATE_OBSERVATIONS,
                                component_version=barrier.obs_flush_component_version,
                                content_hash=barrier.content_hash,
                                lane=barrier.lane,
                                payload={
                                    "version_id": str(version_id),
                                    "representation_id": str(representation_id),
                                    "doc_id": str(candidate["doc_id"]),
                                    "normalizer_version": (
                                        barrier.normalize_component_version
                                    ),
                                    "chunker_version": chunker_version,
                                    "extractor_version": barrier.extractor_version,
                                },
                            ),
                        )
                    )
            return tuple(outcomes)

    def fail(
        self, *, processing_id: UUID, error: str, retryable: bool
    ) -> datetime | None:
        """Record a failed attempt with its full traceback; never bury it (core value 6).

        A retryable failure with attempts remaining schedules a retry backoff
        (status failed, defer_reason retry_backoff, not_before in the future) and
        returns the scheduled time — the caller re-announces it through the
        queue port (packaging §3: retry paths call the port in both profiles). A
        failure at the attempt limit, or a non-retryable one, dead-letters the
        row and returns None.
        """
        with self._engine.begin() as connection:
            row = (
                connection.execute(_SELECT_FOR_FAIL, {"processing_id": processing_id})
                .mappings()
                .first()
            )
            if row is None:
                raise WorkNotFoundError(
                    f"processing row {processing_id} does not exist"
                )
            if row["status"] != "running":
                raise WorkNotRunningError(
                    f"processing row {processing_id} is not running; cannot fail it"
                )
            attempts, max_attempts = int(row["attempts"]), int(row["max_attempts"])
            if retryable and attempts < max_attempts:
                backoff_s = min(
                    self._settings.retry_backoff_base_s * 2 ** (attempts - 1),
                    self._settings.retry_backoff_max_s,
                )
                scheduled = connection.execute(
                    _FAIL_RETRY,
                    {
                        "processing_id": processing_id,
                        "error": error,
                        "backoff_s": backoff_s,
                    },
                ).scalar_one()
                return scheduled
            connection.execute(
                _FAIL_DEAD_LETTER, {"processing_id": processing_id, "error": error}
            )
            return None

    def park_for_budget(self, *, processing_id: UUID, resume_at: datetime) -> None:
        """Park queued work until its budget window rolls (D67).

        Parking happens at claim-time pre-flight, before an attempt starts: it
        applies only to pending/failed rows (a running attempt is never parked — that
        would allow a second concurrent claim), sets defer_reason budget with a
        future not_before, consumes no attempt, and touches no error state, so
        it can never cause dead-lettering.
        """
        with self._engine.begin() as connection:
            updated = connection.execute(
                _PARK_BUDGET, {"processing_id": processing_id, "resume_at": resume_at}
            ).rowcount
            if updated == 0:
                raise WorkNotRunningError(
                    f"processing row {processing_id} is not queued; only queued "
                    "work can be budget-parked"
                )

    def wake(self, *, processing_id: UUID) -> None:
        """Announce an existing committed row on the self-host wake channel.

        The initial wake after enqueue is the schema-owned insert trigger; this
        primitive re-announces for retry, replay, and janitor paths. It never
        creates or mutates work state (the port contract), and SQL stays in the
        spine — adapters call this, never NOTIFY directly.
        """
        with self._engine.begin() as connection:
            connection.execute(_WAKE, {"processing_id": str(processing_id)})

    def replay_dead_letter(
        self,
        *,
        deployment_id: UUID,
        processing_id: UUID,
        attempt_allowance: int = 1,
        lane: ProcessingLane | None = None,
        not_before: datetime | None = None,
    ) -> DeadLetterReplayResult:
        """Reopen exactly one deployment-owned dead letter for explicit replay.

        The transition preserves the attempts already consumed and the complete
        previous error. It grants only the requested additional attempt budget,
        defaults to due now, and may reroute only to a lane accepted by the
        row's immutable stage. Delivery is announced separately after commit.
        """
        if attempt_allowance < 1:
            raise ValueError("dead-letter replay requires at least one new attempt")
        if not_before is not None and (
            not_before.tzinfo is None or not_before.utcoffset() != timedelta(0)
        ):
            raise ValueError("dead-letter replay not_before must be UTC")
        with self._engine.begin() as connection:
            existing = (
                connection.execute(
                    _SELECT_DEAD_LETTER_FOR_REPLAY,
                    {"deployment_id": deployment_id, "processing_id": processing_id},
                )
                .mappings()
                .one_or_none()
            )
            if existing is None:
                raise WorkNotFoundError(
                    f"processing row {processing_id} does not exist in deployment "
                    f"{deployment_id}"
                )
            if existing["status"] != "dead_letter":
                raise WorkNotDeadLetterError(
                    f"processing row {processing_id} has status {existing['status']}; "
                    "only dead-letter rows can be replayed"
                )
            stage = PipelineStage(existing["stage"])
            replay_lane = (
                None
                if existing["lane"] is None and lane is None
                else lane or ProcessingLane(existing["lane"])
            )
            _require_valid_lane(stage=stage, lane=replay_lane)
            attempts = int(existing["attempts"])
            if attempts + attempt_allowance > 32_767:
                raise ValueError("dead-letter replay attempt budget exceeds smallint")
            replayed = (
                connection.execute(
                    _REPLAY_DEAD_LETTER,
                    {
                        "processing_id": processing_id,
                        "lane": replay_lane,
                        "attempt_allowance": attempt_allowance,
                        "not_before": not_before,
                    },
                )
                .mappings()
                .one()
            )
        return DeadLetterReplayResult(
            processing_id=processing_id,
            route=QueueRoute(
                deployment_id=deployment_id, stage=stage, lane=replay_lane
            ),
            not_before=replayed["not_before"],
            attempts=int(replayed["attempts"]),
            max_attempts=int(replayed["max_attempts"]),
        )

    def record_call(self, *, call: RecordCall) -> bool:
        """Attribute one billed call to the running attempt; idempotent per call key.

        Stage, lane, attempt, and target attribution are copied from the locked
        running row (D67) — a caller or delivery envelope can never choose them.
        Returns False when the (processing, attempt, call_key) row already exists,
        so an acknowledged-late retry cannot double-bill.
        """
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    _SELECT_FOR_COST, {"processing_id": call.processing_id}
                )
                .mappings()
                .first()
            )
            if row is None:
                raise WorkNotFoundError(
                    f"processing row {call.processing_id} does not exist"
                )
            if row["status"] != "running":
                raise WorkNotRunningError(
                    f"processing row {call.processing_id} is not running; "
                    "cost attribution requires a running attempt"
                )
            inserted = connection.execute(
                _INSERT_COST,
                {
                    "cost_id": uuid4(),
                    "deployment_id": row["deployment_id"],
                    "processing_id": call.processing_id,
                    "stage": row["stage"],
                    "lane": row["lane"],
                    "target_kind": row["target_kind"],
                    "target_id": row["target_id"],
                    "component_version": row["component_version"],
                    "attempt": row["attempts"],
                    "call_key": call.call_key,
                    "model_name": call.model_name,
                    "tier": call.tier,
                    "tokens_in": call.tokens_in,
                    "tokens_out": call.tokens_out,
                    "cost_usd": call.cost_usd,
                    "latency_ms": call.latency_ms,
                },
            ).rowcount
            return inserted == 1

    def _budget_for(
        self, *, deployment_id: UUID, stage: PipelineStage, lane: ProcessingLane | None
    ) -> CostBudget | None:
        """Return the one validated ceiling for a route, if the operator configured it."""
        return next(
            (
                budget
                for budget in self._settings.budgets
                if budget.deployment_id == deployment_id
                and budget.stage == stage
                and budget.lane == lane
            ),
            None,
        )


def _extract_barrier_ready(
    *,
    connection: Connection,
    deployment_id: UUID,
    representation_id: UUID,
    chunker_version: str,
    extractor_version: str,
) -> bool:
    """True when every chunk of this grid has succeeded extract work + output."""
    expected = connection.execute(
        _BARRIER_EXPECTED_CHUNKS,
        {"representation_id": representation_id, "chunker_version": chunker_version},
    ).scalar_one()
    if int(expected) == 0:
        return True
    ready = connection.execute(
        _BARRIER_READY_CHUNKS,
        {
            "deployment_id": deployment_id,
            "representation_id": representation_id,
            "chunker_version": chunker_version,
            "extractor_version": extractor_version,
            "stage": PipelineStage.EXTRACT_CLAIMS.value,
        },
    ).scalar_one()
    return int(ready) == int(expected)


def _enqueue_claim_normalize_fanout(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    representation_id: UUID,
    chunker_version: str,
    extractor_version: str,
    normalize_component_version: str,
    content_hash: str,
    lane: ProcessingLane | None,
) -> list[EnqueueOutcome]:
    """D88: insert every claim normalize row for this representation (idempotent).

    The expected set is pinned to the extract generation in force when the
    extract barrier fired (design §5.1) so a later extractor re-run cannot
    enlarge an already-materialized barrier.
    """
    from rememberstack.model import ProcessingTarget

    # Lazy import: workers.e3 does not import WorkLedger at module load.
    from rememberstack.workers.e3 import OBS_FLUSH_VERSION

    obs_flush_version = OBS_FLUSH_VERSION
    rows = (
        connection.execute(
            _SELECT_CLAIMS_FOR_NORMALIZE_FANOUT,
            {
                "deployment_id": deployment_id,
                "version_id": version_id,
                "representation_id": representation_id,
                "chunker_version": chunker_version,
                "extractor_version": extractor_version,
            },
        )
        .mappings()
        .all()
    )
    if not rows:
        # Empty extract: skip claim grain; open obs-flush → supersession → embed.
        return [
            enqueue_on(
                connection=connection,
                work=EnqueueWork(
                    deployment_id=deployment_id,
                    target_kind=ProcessingTarget.DOCUMENT_VERSION,
                    target_id=version_id,
                    stage=PipelineStage.ADJUDICATE_OBSERVATIONS,
                    component_version=obs_flush_version,
                    content_hash=content_hash,
                    lane=lane,
                    payload={
                        "version_id": str(version_id),
                        "representation_id": str(representation_id),
                        "doc_id": None,
                        "normalizer_version": normalize_component_version,
                        "chunker_version": chunker_version,
                        "extractor_version": extractor_version,
                    },
                ),
            )
        ]
    outcomes: list[EnqueueOutcome] = []
    doc_id = rows[0]["doc_id"]
    for row in rows:
        outcomes.append(
            enqueue_on(
                connection=connection,
                work=EnqueueWork(
                    deployment_id=deployment_id,
                    target_kind=ProcessingTarget.CLAIM,
                    target_id=row["claim_id"],
                    stage=PipelineStage.NORMALIZE_RELATIONS,
                    component_version=normalize_component_version,
                    content_hash=content_hash,
                    lane=lane,
                    payload={
                        "version_id": str(version_id),
                        "representation_id": str(representation_id),
                        "claim_id": str(row["claim_id"]),
                        "doc_id": str(row["doc_id"]),
                        "chunker_version": chunker_version,
                        "extractor_version": extractor_version,
                    },
                ),
            )
        )
    # If every claim job already succeeded (replay/migration), fire downstream.
    if _normalize_claim_barrier_ready(
        connection=connection,
        deployment_id=deployment_id,
        version_id=version_id,
        representation_id=representation_id,
        chunker_version=chunker_version,
        extractor_version=extractor_version,
        normalize_version=normalize_component_version,
    ):
        outcomes.append(
            enqueue_on(
                connection=connection,
                work=EnqueueWork(
                    deployment_id=deployment_id,
                    target_kind=ProcessingTarget.DOCUMENT_VERSION,
                    target_id=version_id,
                    stage=PipelineStage.ADJUDICATE_OBSERVATIONS,
                    component_version=obs_flush_version,
                    content_hash=content_hash,
                    lane=lane,
                    payload={
                        "version_id": str(version_id),
                        "representation_id": str(representation_id),
                        "doc_id": str(doc_id),
                        "normalizer_version": normalize_component_version,
                        "chunker_version": chunker_version,
                        "extractor_version": extractor_version,
                    },
                ),
            )
        )
    return outcomes


def _normalize_claim_barrier_ready(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    representation_id: UUID,
    chunker_version: str,
    extractor_version: str,
    normalize_version: str,
) -> bool:
    """True when every expected claim has a succeeded claim-grain normalize row."""
    expected = connection.execute(
        _BARRIER_EXPECTED_CLAIMS,
        {
            "deployment_id": deployment_id,
            "version_id": version_id,
            "representation_id": representation_id,
            "chunker_version": chunker_version,
            "extractor_version": extractor_version,
        },
    ).scalar_one()
    if int(expected) == 0:
        return True
    ready = connection.execute(
        _BARRIER_READY_CLAIMS,
        {
            "deployment_id": deployment_id,
            "version_id": version_id,
            "representation_id": representation_id,
            "chunker_version": chunker_version,
            "extractor_version": extractor_version,
            "normalize_version": normalize_version,
            "stage": PipelineStage.NORMALIZE_RELATIONS.value,
        },
    ).scalar_one()
    return int(ready) == int(expected)


def _budget_window_spend(
    *, connection: Connection, budget: CostBudget
) -> _BudgetWindowSpend:
    """Read one aligned window and its deduplicated cost using the database clock."""
    row = (
        connection.execute(
            _BUDGET_WINDOW_SPEND,
            {
                "deployment_id": budget.deployment_id,
                "stage": budget.stage,
                "lane": budget.lane,
                "window_seconds": budget.window_seconds,
            },
        )
        .mappings()
        .one()
    )
    return _BudgetWindowSpend(
        started_at=cast(datetime, row["window_started_at"]),
        ends_at=cast(datetime, row["window_ends_at"]),
        spent_usd=_decimal(row["spent_usd"]),
    )


def _decimal(value: object) -> Decimal:
    """Normalize a PostgreSQL numeric aggregate without introducing float rounding."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _require_valid_lane(*, stage: PipelineStage, lane: ProcessingLane | None) -> None:
    """Reject a lane value that is illegal for the stage's route (D67 pairing)."""
    if not lane_is_valid(stage=stage, lane=None if lane is None else lane.value):
        raise LaneRouteError(
            f"stage {stage} does not accept lane {lane!r}: plane-E stages require "
            "steady or backfill; scheduled K/P stages must be unlaned"
        )


def enqueue_on(*, connection: Connection, work: EnqueueWork) -> EnqueueOutcome:
    """Run the idempotent insert (+ steady-promotion rule) on an open transaction.

    Public for spine services whose own row writes must commit atomically with
    the work they chain (e.g. document ingest enqueueing convert): the caller
    owns the transaction; the initial-wake trigger fires on its commit.
    """
    _require_valid_lane(stage=work.stage, lane=work.lane)
    inserted = (
        connection.execute(
            _INSERT_WORK,
            {
                "processing_id": uuid4(),
                "deployment_id": work.deployment_id,
                "target_kind": work.target_kind,
                "target_id": work.target_id,
                "stage": work.stage,
                "component_version": work.component_version,
                "content_hash": work.content_hash,
                "lane": work.lane,
                "payload": work.payload,
                "not_before": work.not_before,
            },
        )
        .mappings()
        .first()
    )
    if inserted is not None:
        return EnqueueOutcome(
            processing_id=inserted["processing_id"],
            created=True,
            promoted_to_steady=False,
        )
    existing = (
        connection.execute(
            _SELECT_EXISTING,
            {
                "deployment_id": work.deployment_id,
                "target_kind": work.target_kind,
                "target_id": work.target_id,
                "stage": work.stage,
                "component_version": work.component_version,
            },
        )
        .mappings()
        .one()
    )
    promoted = False
    if (
        work.lane is ProcessingLane.STEADY
        and existing["lane"] == ProcessingLane.BACKFILL.value
        and existing["status"] in ("pending", "failed")
    ):
        promoted = (
            connection.execute(
                _PROMOTE_TO_STEADY, {"processing_id": existing["processing_id"]}
            ).rowcount
            == 1
        )
        if promoted:
            # Promotion re-routes live work: wake steady listeners on commit
            # (a backfill row parked under the backfill budget also became due).
            connection.execute(_WAKE, {"processing_id": str(existing["processing_id"])})
    return EnqueueOutcome(
        processing_id=existing["processing_id"],
        created=False,
        promoted_to_steady=promoted,
    )


def _claimed_work(*, row: RowMapping) -> ClaimedWork:
    """Build the typed claimed-work record from a returned ledger row."""
    return ClaimedWork(
        processing_id=row["processing_id"],
        deployment_id=row["deployment_id"],
        target_kind=row["target_kind"],
        target_id=row["target_id"],
        stage=row["stage"],
        component_version=row["component_version"],
        content_hash=row["content_hash"],
        lane=None if row["lane"] is None else ProcessingLane(row["lane"]),
        attempt=int(row["attempts"]),
        payload=row["payload"],
    )


_INSERT_WORK = text(
    """
    INSERT INTO processing_state (
        processing_id, deployment_id, target_kind, target_id, stage,
        component_version, content_hash, lane, payload, not_before
    ) VALUES (
        :processing_id, :deployment_id, :target_kind, :target_id, :stage,
        :component_version, :content_hash, :lane,
        :payload, COALESCE(:not_before, now())
    )
    ON CONFLICT (deployment_id, target_kind, target_id, stage, component_version)
    DO NOTHING
    RETURNING processing_id
    """
).bindparams(bindparam("payload", type_=JSON))

_SELECT_EXISTING = text(
    """
    SELECT processing_id, lane, status
    FROM processing_state
    WHERE deployment_id = :deployment_id
      AND target_kind = :target_kind
      AND target_id = :target_id
      AND stage = :stage
      AND component_version = :component_version
    """
)

_PROMOTE_TO_STEADY = text(
    """
    UPDATE processing_state
    SET lane = 'steady',
        defer_reason = CASE WHEN defer_reason = 'budget' THEN NULL
                            ELSE defer_reason END,
        not_before = CASE WHEN defer_reason = 'budget' THEN now()
                          ELSE not_before END
    WHERE processing_id = :processing_id
      AND lane = 'backfill'
      AND status IN ('pending', 'failed')
    """
)

_CLAIM_SELECT = text(
    """
    SELECT processing_id
    FROM processing_state
    WHERE deployment_id = :deployment_id
      AND stage = :stage
      AND lane IS NOT DISTINCT FROM :lane
      AND status IN ('pending', 'failed')
      AND not_before <= now()
      AND attempts < max_attempts
    ORDER BY not_before, enqueued_at, processing_id
    LIMIT 1
    FOR UPDATE SKIP LOCKED
    """
)

_CLAIM_START = text(
    """
    UPDATE processing_state
    SET status = 'running',
        defer_reason = NULL,
        attempts = attempts + 1,
        started_at = now()
    WHERE processing_id = :processing_id
    RETURNING processing_id, deployment_id, target_kind, target_id, stage,
              component_version, content_hash, lane, attempts, payload
    """
)

_SELECT_TARGET_ID = text(
    """
    SELECT target_id FROM processing_state WHERE processing_id = :processing_id
    """
)

_VERSIONS_WITH_CLAIM_OCCURRENCE = text(
    """
    SELECT DISTINCT c.deployment_id, c.version_id, c.representation_id,
           c.chunker_version, cl.doc_id
    FROM chunk_claims cc
    JOIN chunks c ON c.chunk_id = cc.chunk_id
    JOIN claims cl ON cl.claim_id = cc.claim_id
    WHERE cc.claim_id = :claim_id
      AND c.deployment_id = :deployment_id
      AND cl.extractor_version = :extractor_version
    """
)

_COMPLETE = text(
    """
    UPDATE processing_state
    SET status = 'succeeded', finished_at = now()
    WHERE processing_id = :processing_id AND status = 'running'
    """
)

_SELECT_FOR_FAIL = text(
    """
    SELECT status, attempts, max_attempts
    FROM processing_state
    WHERE processing_id = :processing_id
    FOR UPDATE
    """
)

_FAIL_RETRY = text(
    """
    UPDATE processing_state
    SET status = 'failed',
        defer_reason = 'retry_backoff',
        not_before = now() + make_interval(secs => :backoff_s),
        last_error = :error
    WHERE processing_id = :processing_id
    RETURNING not_before
    """
)

_FAIL_DEAD_LETTER = text(
    """
    UPDATE processing_state
    SET status = 'dead_letter',
        defer_reason = NULL,
        last_error = :error,
        finished_at = now()
    WHERE processing_id = :processing_id
    """
)

_PARK_BUDGET = text(
    """
    UPDATE processing_state
    SET status = 'pending', defer_reason = 'budget', not_before = :resume_at
    WHERE processing_id = :processing_id AND status IN ('pending', 'failed')
    """
)

_SELECT_DEAD_LETTER_FOR_REPLAY = text(
    """
    SELECT status, stage, lane, attempts
    FROM processing_state
    WHERE deployment_id = :deployment_id
      AND processing_id = :processing_id
    FOR UPDATE
    """
)

_REPLAY_DEAD_LETTER = text(
    """
    UPDATE processing_state
    SET status = 'pending',
        lane = :lane,
        defer_reason = CASE
            WHEN :not_before IS NOT NULL AND :not_before > now()
                THEN 'scheduled'::processing_defer_reason
            ELSE NULL
        END,
        not_before = COALESCE(:not_before, now()),
        max_attempts = attempts + :attempt_allowance,
        finished_at = NULL
    WHERE processing_id = :processing_id
      AND status = 'dead_letter'
    RETURNING attempts, max_attempts, not_before
    """
).bindparams(bindparam("not_before", type_=DateTime(timezone=True)))

_BUDGET_WINDOW_SPEND = text(
    """
    WITH bounds AS (
        SELECT
            to_timestamp(
                floor(extract(epoch FROM now()) / :window_seconds)
                * :window_seconds
            ) AS window_started_at,
            to_timestamp(
                (floor(extract(epoch FROM now()) / :window_seconds) + 1)
                * :window_seconds
            ) AS window_ends_at
    )
    SELECT bounds.window_started_at,
           bounds.window_ends_at,
           COALESCE(sum(cost_ledger.cost_usd), 0) AS spent_usd
    FROM bounds
    LEFT JOIN cost_ledger
      ON cost_ledger.deployment_id = :deployment_id
     AND cost_ledger.stage = :stage
     AND cost_ledger.lane IS NOT DISTINCT FROM :lane
     AND cost_ledger.occurred_at >= bounds.window_started_at
     AND cost_ledger.occurred_at < bounds.window_ends_at
    GROUP BY bounds.window_started_at, bounds.window_ends_at
    """
)

_BUDGET_TIER_SPEND = text(
    """
    SELECT tier, COALESCE(sum(cost_usd), 0) AS cost_usd
    FROM cost_ledger
    WHERE deployment_id = :deployment_id
      AND stage = :stage
      AND lane IS NOT DISTINCT FROM :lane
      AND occurred_at >= :window_started_at
      AND occurred_at < :window_ends_at
    GROUP BY tier
    ORDER BY tier NULLS FIRST
    """
)

_BUDGET_PARKED_COUNT = text(
    """
    SELECT count(*)
    FROM processing_state
    WHERE deployment_id = :deployment_id
      AND stage = :stage
      AND lane IS NOT DISTINCT FROM :lane
      AND status = 'pending'
      AND defer_reason = 'budget'
    """
)

_SELECT_FOR_COST = text(
    """
    SELECT deployment_id, status, stage, lane, attempts,
           target_kind, target_id, component_version
    FROM processing_state
    WHERE processing_id = :processing_id
    FOR UPDATE
    """
)

_INSERT_COST = text(
    """
    INSERT INTO cost_ledger (
        cost_id, deployment_id, processing_id, stage, lane, target_kind,
        target_id, component_version, attempt, call_key, model_name, tier,
        tokens_in, tokens_out, cost_usd, latency_ms
    ) VALUES (
        :cost_id, :deployment_id, :processing_id, :stage, :lane, :target_kind,
        :target_id, :component_version, :attempt, :call_key, :model_name, :tier,
        :tokens_in, :tokens_out, :cost_usd, :latency_ms
    )
    ON CONFLICT (deployment_id, processing_id, attempt, call_key) DO NOTHING
    """
)

_WAKE = text(
    """
    SELECT pg_notify('queue_wake', :processing_id)
    """
)

_ADVISORY_LOCK_REPRESENTATION = text(
    """
    SELECT pg_advisory_xact_lock(
        hashtextextended('d84-representation:' || CAST(:representation_id AS text), 0)
    )
    """
)

_ADVISORY_LOCK_NORMALIZE_BARRIER = text(
    """
    SELECT pg_advisory_xact_lock(
        hashtextextended(
            'd88-normalize-barrier:' || CAST(:representation_id AS text), 0
        )
    )
    """
)

# D56: occurrence map is chunk_claims; claims.chunk_id is origin only.
_SELECT_CLAIMS_FOR_NORMALIZE_FANOUT = text(
    """
    SELECT cl.claim_id, cl.doc_id
    FROM claims cl
    JOIN chunk_claims cc ON cc.claim_id = cl.claim_id
    JOIN chunks c ON c.chunk_id = cc.chunk_id
    WHERE cl.deployment_id = :deployment_id
      AND c.deployment_id = :deployment_id
      AND c.version_id = :version_id
      AND c.representation_id = :representation_id
      AND c.chunker_version = :chunker_version
      AND cl.extractor_version = :extractor_version
    ORDER BY cl.ingested_at, cl.claim_id
    """
)

_BARRIER_EXPECTED_CLAIMS = text(
    """
    SELECT count(*)::bigint
    FROM claims cl
    JOIN chunk_claims cc ON cc.claim_id = cl.claim_id
    JOIN chunks c ON c.chunk_id = cc.chunk_id
    WHERE cl.deployment_id = :deployment_id
      AND c.deployment_id = :deployment_id
      AND c.version_id = :version_id
      AND c.representation_id = :representation_id
      AND c.chunker_version = :chunker_version
      AND cl.extractor_version = :extractor_version
    """
)

_BARRIER_READY_CLAIMS = text(
    """
    SELECT count(*)::bigint
    FROM claims cl
    JOIN chunk_claims cc ON cc.claim_id = cl.claim_id
    JOIN chunks c ON c.chunk_id = cc.chunk_id
    JOIN processing_state p
      ON p.deployment_id = :deployment_id
     AND p.target_kind = 'claim'
     AND p.target_id = cl.claim_id
     AND p.stage = CAST(:stage AS pipeline_stage)
     AND p.component_version = :normalize_version
     AND p.status = 'succeeded'
    WHERE cl.deployment_id = :deployment_id
      AND c.deployment_id = :deployment_id
      AND c.version_id = :version_id
      AND c.representation_id = :representation_id
      AND c.chunker_version = :chunker_version
      AND cl.extractor_version = :extractor_version
    """
)

_BARRIER_EXPECTED_CHUNKS = text(
    """
    SELECT count(*)::bigint
    FROM chunks
    WHERE representation_id = :representation_id
      AND chunker_version = :chunker_version
    """
)

_BARRIER_READY_CHUNKS = text(
    """
    SELECT count(*)::bigint
    FROM chunks c
    JOIN processing_state p
      ON p.deployment_id = :deployment_id
     AND p.target_kind = 'chunk'
     AND p.target_id = c.chunk_id
     AND p.stage = CAST(:stage AS pipeline_stage)
     AND p.component_version = :extractor_version
     AND p.status = 'succeeded'
    WHERE c.representation_id = :representation_id
      AND c.chunker_version = :chunker_version
      AND (
            EXISTS (
                SELECT 1 FROM claims cl
                WHERE cl.chunk_id = c.chunk_id
                  AND cl.extractor_version = :extractor_version
            )
            OR EXISTS (
                SELECT 1 FROM claim_extraction_decisions d
                WHERE d.chunk_id = c.chunk_id
                  AND d.extractor_version = :extractor_version
            )
            OR EXISTS (
                SELECT 1 FROM chunk_claims cc
                JOIN claims cl ON cl.claim_id = cc.claim_id
                WHERE cc.chunk_id = c.chunk_id
                  AND cl.extractor_version = :extractor_version
            )
          )
    """
)
