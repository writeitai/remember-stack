"""Collect the work rows a task or request creates, to announce them (D147).

The self-host shell needs nothing from this module: the insert trigger wakes
listeners on commit. A host that delivers work by push (one announcement per
row) cannot listen on every database, so it collects the rows created while
it runs a task or serves a request, and announces them through its
``TaskQueuePort`` once that work has committed.

Collection is best effort: a row created on a thread that does not inherit
the caller's context variables is not collected. Announcing twice, or
announcing a row whose transaction rolled back, is harmless, because the
receiver re-reads the ledger. A host still runs a janitor that re-announces
due rows, which is what makes a missed announcement harmless.
"""

from collections.abc import Iterable
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from datetime import UTC
import logging
from uuid import UUID

from rememberstack.model import QueueRoute
from rememberstack.ports.queue import TaskQueuePort

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewWork:
    """One work row created (or re-routed) while a collector was active."""

    processing_id: UUID
    route: QueueRoute
    not_before: datetime | None


_COLLECTED: ContextVar[list[NewWork] | None] = ContextVar(
    "rememberstack_new_work", default=None
)


@contextmanager
def collect_new_work() -> Iterator[list[NewWork]]:
    """Collect the rows created inside the block, in creation order."""
    collected: list[NewWork] = []
    token = _COLLECTED.set(collected)
    try:
        yield collected
    finally:
        _COLLECTED.reset(token)


def record_new_work(*, work: NewWork) -> None:
    """Remember one created row when a collector is active; otherwise nothing."""
    collected = _COLLECTED.get()
    if collected is not None:
        collected.append(work)


def announce_new_work(*, queue: TaskQueuePort, work: Iterable[NewWork]) -> None:
    """Announce each collected row once; a failed announcement is only logged.

    The work already committed, so a failure here must not fail it; the
    host's janitor re-announces due rows whose announcement was lost.
    """
    seen: set[UUID] = set()
    for item in work:
        if item.processing_id in seen:
            continue
        seen.add(item.processing_id)
        try:
            queue.announce(
                processing_id=item.processing_id,
                route_snapshot=item.route,
                not_before_snapshot=item.not_before or datetime.now(UTC),
            )
        except Exception:  # noqa: BLE001 — the janitor recovers lost announcements
            _logger.exception(
                "announcing new work %s failed; the janitor re-announces it",
                item.processing_id,
            )
