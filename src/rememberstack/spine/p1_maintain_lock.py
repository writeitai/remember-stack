"""Table-scoped Postgres advisory locks for P1 Lance maintenance (D91)."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Engine

P1_MAINTAIN_TABLES: Final = ("chunks", "claims", "facts", "entities")

_ACQUIRE = text("SELECT pg_advisory_lock(hashtextextended(:key, 0))")
_RELEASE = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")


def p1_table_maintain_lock_key(*, lance_root: Path | str, table_name: str) -> str:
    """Stable lock material shared by maintain, purge, and backfill finalizer."""
    return f"p1-lance-maintain:{Path(lance_root).resolve()}:{table_name}"


@contextmanager
def hold_p1_table_maintain_locks(
    *,
    engine: Engine,
    lance_root: Path | str,
    tables: tuple[str, ...] = P1_MAINTAIN_TABLES,
) -> Iterator[None]:
    """Hold session locks for each table in a fixed order (deadlock-safe)."""
    keys = tuple(
        p1_table_maintain_lock_key(lance_root=lance_root, table_name=name)
        for name in sorted(tables)
    )
    acquired: list[str] = []
    with engine.connect() as connection:
        try:
            for key in keys:
                connection.execute(_ACQUIRE, {"key": key})
                acquired.append(key)
            connection.commit()
            yield
        finally:
            release_errors: list[BaseException] = []
            for key in reversed(acquired):
                try:
                    connection.execute(_RELEASE, {"key": key})
                except BaseException as error:  # noqa: BLE001 — unlock every key
                    release_errors.append(error)
            try:
                connection.commit()
            except BaseException as error:  # noqa: BLE001
                release_errors.append(error)
            if release_errors:
                connection.invalidate()
                raise release_errors[0]
