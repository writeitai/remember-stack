"""Alembic runtime configured through the typed spine settings boundary."""

from logging.config import fileConfig
import re

from alembic import context
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from rememberstack.spine.settings import load_database_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None

# Each database gets its own roles, ``rememberstack_query_<database>`` and
# ``rememberstack_graph_<database>`` (roles are cluster-global), created with
# unquoted names. PostgreSQL folds unquoted names to lower case and silently
# truncates them to 63 bytes, so two databases whose names differ only in
# case or beyond the limit would share one role and its grants on both. Only
# names that map to themselves are accepted.
_MAX_DATABASE_NAME_BYTES = 63 - len("rememberstack_query_")
_DATABASE_NAME = re.compile(r"[a-z_][a-z0-9_]*")


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
    """Run migrations through a non-pooled SQLAlchemy connection."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )

    with connectable.connect() as connection:
        database = connection.exec_driver_sql("SELECT current_database()").scalar_one()
        # End the read's implicit transaction so Alembic owns the next one.
        connection.rollback()
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
        connection.exec_driver_sql("SELECT pg_advisory_lock(1919251316, 153)")
        connection.rollback()
        try:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            # NullPool also physically closes this session if an interrupted
            # connection cannot execute the explicit unlock.
            connection.rollback()
            connection.exec_driver_sql("SELECT pg_advisory_unlock(1919251316, 153)")
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
