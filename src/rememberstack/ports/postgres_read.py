"""Typed connection admission for bounded PostgreSQL read paths."""

from contextlib import AbstractContextManager
from typing import Protocol

from sqlalchemy.engine import Connection


class PostgresReadPoolPort(Protocol):
    """Admit one PostgreSQL read connection inside an absolute deadline."""

    def connect(
        self, *, deadline: float | None, isolation_level: str | None = None
    ) -> AbstractContextManager[Connection]:
        """Return an admitted connection or raise when its budget expires."""
        ...
