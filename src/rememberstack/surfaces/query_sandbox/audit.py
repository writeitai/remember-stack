"""Async-lightweight telemetry, kill switches, and admission (design §7).

The query path never writes synchronously (operator performance directive,
2026-08-04): `AuditTrail.emit` is a bounded in-process queue put that drops
on overflow and counts the drops; a background consumer (wired by the host
process) drains to whatever sink the operator configures. Kill switches stop
NEW work at admission; running statements finish under their own caps.
Content never enters the trail — hashes, identifiers, counts, and codes only
(§7 retention: no raw SQL, parameter values, rows, or private errors).
"""

from collections import defaultdict
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
import queue
import threading
import time
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from rememberstack.surfaces.query_sandbox.result import QueryResult


@dataclass(frozen=True)
class AuditEvent:
    """One attempt's non-content telemetry record."""

    request_id: UUID
    deployment_id: UUID
    principal: str
    query_hash: str
    surface_manifest_hash: str
    query_language: str
    admission: str
    row_cap: int
    byte_cap: int
    statement_timeout_ms: int
    analytical_tier: bool
    referenced_views: tuple[str, ...]
    referenced_functions: tuple[str, ...]
    termination_reason: str
    error_code: str | None
    returned_row_count: int
    returned_byte_count: int
    elapsed_ms: float
    emitted_at: datetime


class AuditTrail:
    """A bounded fire-and-forget event queue with drop accounting."""

    def __init__(self, *, capacity: int = 4096) -> None:
        self._queue: queue.Queue[AuditEvent] = queue.Queue(maxsize=capacity)
        self.dropped = 0
        self._enabled = True

    @classmethod
    def disabled(cls) -> "AuditTrail":
        trail = cls(capacity=1)
        trail._enabled = False
        return trail

    def emit(self, *, outcome: "QueryResult", principal: str) -> None:
        if not self._enabled:
            return
        event = AuditEvent(
            request_id=outcome.request_id,
            deployment_id=outcome.deployment_id,
            principal=principal,
            query_hash=outcome.query_hash,
            surface_manifest_hash=outcome.surface_manifest_hash,
            query_language=outcome.query_language,
            admission=(
                "rejected" if outcome.termination_reason == "rejected" else "admitted"
            ),
            row_cap=outcome.limits.row_cap,
            byte_cap=outcome.limits.byte_cap,
            statement_timeout_ms=outcome.limits.statement_timeout_ms,
            analytical_tier=outcome.limits.analytical_tier,
            referenced_views=outcome.referenced_views,
            referenced_functions=outcome.referenced_functions,
            termination_reason=outcome.termination_reason,
            error_code=outcome.error_code.value if outcome.error_code else None,
            returned_row_count=outcome.returned_row_count,
            returned_byte_count=outcome.returned_byte_count,
            elapsed_ms=outcome.elapsed_ms,
            emitted_at=datetime.now().astimezone(),
        )
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    def drain(self, *, sink: Callable[[AuditEvent], None], limit: int = 1000) -> int:
        """Consume up to `limit` events into `sink`; the host wires the cadence."""
        consumed = 0
        while consumed < limit:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            sink(event)
            consumed += 1
        return consumed


@dataclass
class KillSwitches:
    """Operator disablement plus in-process concurrency admission.

    A blocked deployment/principal rejects NEW requests at admission with
    `quota_exceeded` ("disabled by the operator"); nothing about a running
    statement changes. Concurrency admission is a plain counter under a lock —
    the per-pool posture (D68: the connection is the deployment) keeps this
    process-local accounting honest for one gateway process; multi-process
    coordination is the pool's job, not the sandbox's.
    """

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _blocked_deployments: set[UUID] = field(default_factory=set)
    _blocked_principals: set[str] = field(default_factory=set)
    _by_principal: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _by_deployment: dict[UUID, int] = field(default_factory=lambda: defaultdict(int))
    # Rolling 60-second statement-seconds, as (finished_at, seconds) pairs.
    _spend_principal: dict[str, deque] = field(
        default_factory=lambda: defaultdict(deque)
    )
    _spend_deployment: dict[UUID, deque] = field(
        default_factory=lambda: defaultdict(deque)
    )

    def block_deployment(self, deployment_id: UUID) -> None:
        with self._lock:
            self._blocked_deployments.add(deployment_id)

    def unblock_deployment(self, deployment_id: UUID) -> None:
        with self._lock:
            self._blocked_deployments.discard(deployment_id)

    def block_principal(self, principal: str) -> None:
        with self._lock:
            self._blocked_principals.add(principal)

    def unblock_principal(self, principal: str) -> None:
        with self._lock:
            self._blocked_principals.discard(principal)

    def blocked(self, *, deployment_id: UUID, principal: str) -> bool:
        with self._lock:
            return (
                deployment_id in self._blocked_deployments
                or principal in self._blocked_principals
            )

    def record_spend(
        self, *, deployment_id: UUID, principal: str, seconds: float
    ) -> None:
        """Charge a finished statement's wall-clock to the rolling windows."""
        now = time.monotonic()
        with self._lock:
            self._spend_principal[principal].append((now, seconds))
            self._spend_deployment[deployment_id].append((now, seconds))

    def _rolling(self, window: deque, *, now: float) -> float:
        while window and now - window[0][0] > 60.0:
            window.popleft()
        return sum(seconds for _, seconds in window)

    def admit(
        self,
        *,
        deployment_id: UUID,
        principal: str,
        per_principal: int,
        per_deployment: int,
        principal_seconds_per_minute: float | None = None,
        deployment_seconds_per_minute: float | None = None,
    ) -> str | None:
        """None when admitted (and counted); a caller-safe reason otherwise."""
        with self._lock:
            now = time.monotonic()
            if principal_seconds_per_minute is not None and (
                self._rolling(self._spend_principal[principal], now=now)
                >= principal_seconds_per_minute
            ):
                return "the principal's rolling statement-second quota is spent"
            if deployment_seconds_per_minute is not None and (
                self._rolling(self._spend_deployment[deployment_id], now=now)
                >= deployment_seconds_per_minute
            ):
                return "the deployment's rolling statement-second quota is spent"
            if self._by_principal[principal] >= per_principal:
                return "the principal's concurrent-statement cap is reached"
            if self._by_deployment[deployment_id] >= per_deployment:
                return "the deployment's concurrent-statement cap is reached"
            self._by_principal[principal] += 1
            self._by_deployment[deployment_id] += 1
            return None

    def release(self, *, deployment_id: UUID, principal: str) -> None:
        with self._lock:
            self._by_principal[principal] = max(0, self._by_principal[principal] - 1)
            self._by_deployment[deployment_id] = max(
                0, self._by_deployment[deployment_id] - 1
            )
