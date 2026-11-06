"""Typed connection admission for bounded PostgreSQL read paths."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class ReadOperation:
    """What one ranked operation pins for every nested read (D142 §8.2).

    ``deadline`` is the operation's absolute ``monotonic()`` deadline;
    ``evaluated_at`` is the authority instant every default ``current`` scope
    and confirmation uses (the snapshot's transaction time, or the exporting
    transaction's when a snapshot is imported).
    """

    deadline: float
    evaluated_at: datetime


class PostgresReadPoolPort(Protocol):
    """Admit one PostgreSQL read connection inside an absolute deadline."""

    def connect(
        self, *, deadline: float | None, isolation_level: str | None = None
    ) -> AbstractContextManager[Connection]:
        """Return an admitted connection or raise when its budget expires."""
        ...

    def snapshot(
        self,
        *,
        deadline: float,
        import_snapshot: str | None = None,
        evaluated_at: datetime | None = None,
    ) -> AbstractContextManager[Connection]:
        """Bind one read-only repeatable-read connection for nested reads.

        ``import_snapshot`` adopts another transaction's exported snapshot;
        ``evaluated_at`` pins the operation instant (default: the snapshot's
        transaction time).
        """
        ...

    def operation(self) -> ReadOperation | None:
        """The enclosing snapshot's pinned deadline and instant, if any."""
        ...
