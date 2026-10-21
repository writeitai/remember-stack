"""Shared bounded admission for interactive PostgreSQL retrieval reads."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from datetime import UTC
from threading import BoundedSemaphore
from time import monotonic

from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from rememberstack.ports.postgres_read import ReadOperation


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
        self._snapshot_connection: ContextVar[Connection | None] = ContextVar(
            "rememberstack_retrieval_snapshot_connection", default=None
        )
        self._operation: ContextVar[ReadOperation | None] = ContextVar(
            "rememberstack_retrieval_operation", default=None
        )

    @contextmanager
    def connect(
        self, *, deadline: float | None, isolation_level: str | None = None
    ) -> Iterator[Connection]:
        """Admit and yield one connection without outwaiting the caller budget."""
        bound = self._snapshot_connection.get()
        if bound is not None:
            operation = self._operation.get()
            if operation is not None:
                bound_statement_timeout(bound, deadline=operation.deadline)
            yield bound
            return
        with self._admitted_connection(
            deadline=deadline, isolation_level=isolation_level
        ) as connection:
            yield connection

    def in_snapshot(self) -> bool:
        """Whether the current context already holds a bound snapshot."""
        return self._snapshot_connection.get() is not None

    def operation(self) -> ReadOperation | None:
        """The enclosing snapshot's pinned deadline and instant, if any."""
        return self._operation.get()

    @contextmanager
    def snapshot(
        self,
        *,
        deadline: float,
        import_snapshot: str | None = None,
        evaluated_at: datetime | None = None,
    ) -> Iterator[Connection]:
        """Hold one bounded read-only MVCC snapshot across nested retrieval reads.

        ``import_snapshot`` (D142 §8.2) adopts another transaction's exported
        snapshot, so the SQL sandbox's request and its P1 nominations read one
        database state; the import is the transaction's first statement.
        REPEATABLE READ freezes rows, not clocks: ``evaluated_at`` (default
        this transaction's ``now()``) is pinned with ``deadline`` for every
        nested read, which bounds its statements by the remaining time.
        """
        if self._snapshot_connection.get() is not None:
            raise RuntimeError("retrieval snapshots cannot be nested")
        with self._admitted_connection(
            deadline=deadline, isolation_level="REPEATABLE READ"
        ) as connection:
            if import_snapshot is not None:
                connection.exec_driver_sql(
                    "SET TRANSACTION SNAPSHOT " + _quote_snapshot_id(import_snapshot)
                )
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            # REPEATABLE READ fixes its MVCC cut at the first query, not at
            # checkout. Establish it before embedding/provider work or any
            # nested P1 call can delay the operation's database instant.
            instant = connection.exec_driver_sql("SELECT now()").scalar_one()
            operation = ReadOperation(
                deadline=deadline,
                evaluated_at=(evaluated_at or instant).astimezone(UTC),
            )
            token = self._snapshot_connection.set(connection)
            operation_token = self._operation.set(operation)
            try:
                yield connection
            finally:
                self._operation.reset(operation_token)
                self._snapshot_connection.reset(token)

    @contextmanager
    def _admitted_connection(
        self, *, deadline: float | None, isolation_level: str | None
    ) -> Iterator[Connection]:
        """Open a new physical connection under this pool's admission limit."""
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


def _quote_snapshot_id(value: str) -> str:
    """Quote an exported snapshot id; ``SET TRANSACTION`` takes no parameters."""
    if not value or any(not (ch.isalnum() or ch == "-") for ch in value):
        raise ValueError("an exported snapshot id is hexadecimal digits and dashes")
    return "'" + value + "'"


def bound_statement_timeout(connection: Connection, *, deadline: float) -> None:
    """Clamp the transaction's statement timeout to the time left (D142 §8.6).

    ``SET LOCAL`` lasts to the end of the transaction, so every nested read of
    one operation re-clamps it before running. An expired deadline raises
    before any statement runs.
    """
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise SQLAlchemyTimeoutError("PostgreSQL read operation deadline expired")
    connection.exec_driver_sql(
        f"SET LOCAL statement_timeout = {max(1, int(remaining * 1000))}"
    )
