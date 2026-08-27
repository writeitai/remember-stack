"""Shared bounded admission for interactive PostgreSQL retrieval reads."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore
from time import monotonic

from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError


class BoundedPostgresReadPool:
    """Keep one private engine inside an explicit concurrency and wait budget."""

    def __init__(
        self, *, engine: Engine, max_concurrency: int, pool_wait_seconds: float
    ) -> None:
        """Bind a dedicated engine and its application-level admission limit."""
        if max_concurrency <= 0:
            raise ValueError("PostgreSQL read max_concurrency must be positive")
        if pool_wait_seconds <= 0:
            raise ValueError("PostgreSQL read pool_wait_seconds must be positive")
        self._engine = engine
        self._pool_wait_seconds = pool_wait_seconds
        self._slots = BoundedSemaphore(value=max_concurrency)

    @contextmanager
    def connect(
        self, *, deadline: float | None, isolation_level: str | None = None
    ) -> Iterator[Connection]:
        """Admit and yield one connection without outwaiting the caller budget."""
        wait_seconds = self._pool_wait_seconds
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise SQLAlchemyTimeoutError(
                    "PostgreSQL read operation deadline expired before admission"
                )
            wait_seconds = min(wait_seconds, remaining)
        if not self._slots.acquire(timeout=wait_seconds):
            raise SQLAlchemyTimeoutError(
                "PostgreSQL read concurrency limit reached before admission"
            )
        try:
            if deadline is not None and monotonic() >= deadline:
                raise SQLAlchemyTimeoutError(
                    "PostgreSQL read operation deadline expired before checkout"
                )
            connection = self._engine.connect()
            if isolation_level is not None:
                connection = connection.execution_options(
                    isolation_level=isolation_level
                )
            with connection:
                yield connection
        finally:
            self._slots.release()
