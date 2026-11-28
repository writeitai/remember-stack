"""Bind D163 sandbox reads to the PostgreSQL session that verified the fixture."""

from collections.abc import Callable
from uuid import uuid4

import psycopg

from rememberstack.spine.migrations.versions.p9_02_0023_query_space_roles import (
    query_role_name,
)


def bind_query_login(
    owner: psycopg.Connection, dsn: str
) -> Callable[[], psycopg.Connection]:
    """Verify the query login against the live owner backend on every connection."""
    database = owner.info.dbname
    role = query_role_name(database)
    owner_pid = owner.info.backend_pid
    marker = f"d163-{uuid4().hex}"
    owner.execute(
        "SELECT pg_catalog.set_config('application_name', %s, false)", (marker,)
    )

    def connect() -> psycopg.Connection:
        reader = psycopg.connect(dsn, autocommit=True)
        try:
            # Physical copies can share the database name, OID and system ID.
            observed = reader.execute(
                """
                SELECT pg_catalog.current_database(), session_user, current_user, EXISTS (
                    SELECT FROM pg_catalog.pg_stat_activity
                    WHERE pid = %s AND datname = pg_catalog.current_database()
                      AND application_name = %s
                )
                """,
                (owner_pid, marker),
            ).fetchone()
            if observed != (database, role, role, True) or reader.info.user != role:
                raise RuntimeError(
                    "query DSN must reach the verified database as its restricted query login"
                )
            reader.autocommit = False
            return reader
        except BaseException:
            reader.close()
            raise

    return connect
