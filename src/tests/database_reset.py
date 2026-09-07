"""Destructive reset of the explicitly configured disposable integration database.

D114 cannot safely downgrade a populated store back to source-time windows.
Tests that need a fresh schema therefore discard their disposable schema instead
of weakening the production migration's restore requirement. Earlier revision
fixtures still exercise their ordinary Alembic downgrades.
"""

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy import text

from rememberstack.spine.settings import load_database_settings


def reset_database(*, config: Config) -> None:
    """Discard test data at D114; otherwise exercise the prior downgrade chain."""
    url = (
        config.get_main_option("sqlalchemy.url")
        or load_database_settings().sqlalchemy_url()
    )
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            if (
                connection.execute(
                    text("SELECT to_regclass('public.alembic_version')")
                ).scalar_one()
                is None
            ):
                return
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
            if revision == "p9_28_0049":
                connection.execute(text("DROP SCHEMA IF EXISTS memory_v1 CASCADE"))
                connection.execute(
                    text("DROP SCHEMA IF EXISTS rememberstack_graph_internal CASCADE")
                )
                connection.execute(text("DROP EXTENSION IF EXISTS pg_partman CASCADE"))
                connection.execute(text("DROP SCHEMA IF EXISTS partman CASCADE"))
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(
                    text("CREATE SCHEMA public AUTHORIZATION pg_database_owner")
                )
                connection.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))
                return
    finally:
        engine.dispose()
    command.downgrade(config=config, revision="base")
