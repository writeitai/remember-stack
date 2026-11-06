"""Alembic runtime configured through the typed spine settings boundary."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from rememberstack.spine.settings import load_database_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None

# Each database gets its own roles, ``rememberstack_query_<database>`` and
# ``rememberstack_graph_<database>`` (roles are cluster-global). PostgreSQL
# silently truncates names to 63 bytes, so two longer database names sharing
# a prefix would share one role and its grants on both databases.
_MAX_DATABASE_NAME_BYTES = 63 - len("rememberstack_query_")


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
        if len(str(database).encode("utf-8")) > _MAX_DATABASE_NAME_BYTES:
            raise RuntimeError(
                f"database name {database!r} is longer than"
                f" {_MAX_DATABASE_NAME_BYTES} bytes; its per-database roles"
                " would be truncated and could be shared with another database"
            )
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
