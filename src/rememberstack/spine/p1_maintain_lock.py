"""Table-scoped Postgres advisory locks for P1 Lance maintenance (D91)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from time import monotonic
from time import sleep
from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Engine

P1_MAINTAIN_TABLES: Final = ("chunks", "claims", "facts", "entities")
DEFAULT_P1_MAINTAIN_LOCK_WAIT: Final = timedelta(seconds=30)
DEFAULT_P1_MAINTAIN_LOCK_POLL: Final = timedelta(milliseconds=50)

_TRY_ACQUIRE = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
_RELEASE = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")


class P1MaintainLockTimeout(Exception):
    """A table maintain lock stayed contended past the bounded wait."""


def p1_table_maintain_lock_key(*, lance_root: Path | str, table_name: str) -> str:
    """Stable lock material shared by maintain, purge, and backfill finalizer."""
    return f"p1-lance-maintain:{Path(lance_root).resolve()}:{table_name}"


def _unlock_result_ok(value: object) -> bool:
    """Treat Postgres ``pg_advisory_unlock`` false/NULL as an unsuccessful release."""
    return value is True


@contextmanager
def hold_p1_table_maintain_locks(
    *,
    engine: Engine,
    lance_root: Path | str,
    tables: tuple[str, ...] = P1_MAINTAIN_TABLES,
    timeout: timedelta = DEFAULT_P1_MAINTAIN_LOCK_WAIT,
    poll_interval: timedelta = DEFAULT_P1_MAINTAIN_LOCK_POLL,
    try_once: bool = False,
) -> Iterator[None]:
    """Hold session locks for each table in a fixed order (deadlock-safe).

    Acquisition uses ``pg_try_advisory_lock``. The ticker passes
    ``try_once=True`` (one attempt, immediate skip). Forget polls until
    ``timeout`` so delete_unverified is not interleaved with compact/retrain.
    An empty ``tables`` tuple is a no-op.
    """
    if not try_once and timeout <= timedelta(0):
        raise ValueError("P1 maintain lock timeout must be positive")
    if not try_once and poll_interval <= timedelta(0):
        raise ValueError("P1 maintain lock poll interval must be positive")
    keys = tuple(
        p1_table_maintain_lock_key(lance_root=lance_root, table_name=name)
        for name in sorted(tables)
    )
    acquired: list[str] = []
    deadline = monotonic() + timeout.total_seconds()
    poll_s = poll_interval.total_seconds()
    with engine.connect() as connection:
        try:
            for key in keys:
                while True:
                    locked = connection.execute(_TRY_ACQUIRE, {"key": key}).scalar()
                    connection.commit()
                    if locked is True:
                        acquired.append(key)
                        break
                    if try_once:
                        raise P1MaintainLockTimeout(f"P1 maintain lock busy {key}")
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise P1MaintainLockTimeout(
                            f"timed out waiting for P1 maintain lock {key}"
                        )
                    sleep(min(poll_s, remaining))
            yield
        finally:
            release_errors: list[BaseException] = []
            for key in reversed(acquired):
                try:
                    unlocked = connection.execute(_RELEASE, {"key": key}).scalar()
                    if not _unlock_result_ok(unlocked):
                        release_errors.append(
                            RuntimeError(
                                f"pg_advisory_unlock returned {unlocked!r} for {key}"
                            )
                        )
                except BaseException as error:  # noqa: BLE001 — unlock every key
                    release_errors.append(error)
            try:
                connection.commit()
            except BaseException as error:  # noqa: BLE001
                release_errors.append(error)
            if release_errors:
                connection.invalidate()
                raise release_errors[0]
