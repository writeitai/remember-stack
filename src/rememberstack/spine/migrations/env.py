"""Alembic runtime configured through the typed spine settings boundary."""

import logging
from logging.config import fileConfig
import math
import re
from time import sleep

from alembic import context
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from rememberstack.spine.settings import load_database_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None

# Each database gets its own roles, ``rememberstack_query_<database>`` and
# ``rememberstack_graph_<database>`` (roles are cluster-global), created with
# unquoted names. PostgreSQL folds unquoted names to lower case and silently
# truncates them to 63 bytes, so two databases whose names differ only in
# case or beyond the limit would share one role and its grants on both. Only
# names that map to themselves are accepted.
_MAX_DATABASE_NAME_BYTES = 63 - len("rememberstack_query_")
_DATABASE_NAME = re.compile(r"[a-z_][a-z0-9_]*")
_LOCK_RETRY_DELAYS = (0.25, 0.5, 1.0, 2.0, 4.0)
_LOGGER = logging.getLogger("alembic.runtime.migration")


def _database_url() -> str:
    """Resolve an explicit Alembic URL or the validated application setting."""
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        return configured_url
    return load_database_settings().sqlalchemy_url()


def run_migrations_offline() -> None:
    """Run migrations without creating a database engine."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Retry a contended revision from Alembic's last committed version."""
    lock_seconds = float(config.get_main_option("migration_lock_timeout_seconds", "3"))
    if not math.isfinite(lock_seconds) or lock_seconds <= 0:
        raise ValueError("migration_lock_timeout_seconds must be finite and positive")
    lock_timeout_ms = max(1, int(lock_seconds * 1000))
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    for attempt in range(len(_LOCK_RETRY_DELAYS) + 1):
        try:
            _run_online_attempt(engine=connectable, lock_timeout_ms=lock_timeout_ms)
            return
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) != "55P03" or attempt == len(
                _LOCK_RETRY_DELAYS
            ):
                raise
            delay = _LOCK_RETRY_DELAYS[attempt]
            _LOGGER.warning(
                "Migration lock unavailable (SQLSTATE 55P03). Retrying in %s s.", delay
            )
            sleep(delay)


def _run_online_attempt(*, engine: Engine, lock_timeout_ms: int) -> None:
    """Hold the native migration lock through one pass, including autocommit DDL."""
    with engine.connect() as connection:
        # Downgrade guards must see writes that commit while they wait for a
        # table lock, even if Alembic has already read the revision.
        connection.execution_options(isolation_level="READ COMMITTED")
        database = connection.exec_driver_sql("SELECT current_database()").scalar_one()
        if (
            len(str(database)) > _MAX_DATABASE_NAME_BYTES
            or _DATABASE_NAME.fullmatch(str(database)) is None
        ):
            raise RuntimeError(
                f"database name {database!r} must be at most"
                f" {_MAX_DATABASE_NAME_BYTES} characters of a-z, 0-9 and _"
                " (not starting with a digit); otherwise its per-database roles"
                " could be shared with another database"
            )
        # A session lock survives the commits Alembic must perform for
        # CREATE INDEX CONCURRENTLY. Acquire it BEFORE Alembic reads revision
        # state, so a second upgrader cannot act on that same old revision.
        # Advisory locks are database-local; the first key namespaces ours.
        try:
            # A session setting survives both revision commits and concurrent
            # index builds. Commit before Alembic inspects the transaction state.
            connection.exec_driver_sql(f"SET lock_timeout = '{lock_timeout_ms}ms'")
            connection.exec_driver_sql("SELECT pg_advisory_lock(1919251316, 153)")
            connection.commit()
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                transaction_per_migration=True,
            )
            with context.begin_transaction():
                context.run_migrations()
        except BaseException:
            # Close an interrupted session instead of hiding its original
            # failure with an unlock attempted on a broken connection.
            connection.invalidate()
            raise
        else:
            connection.rollback()
            connection.exec_driver_sql("SELECT pg_advisory_unlock(1919251316, 153)")
            connection.exec_driver_sql("RESET lock_timeout")
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
