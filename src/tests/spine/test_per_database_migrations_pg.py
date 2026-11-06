"""Several deployment databases on one server upgrade independently and at once.

Each deployment is its own database, and an operator upgrades them by running
migrations against each one, in parallel when there are many. Databases share
only cluster-global objects (roles), so those are what these proofs cover.
"""

from collections.abc import Iterator
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url

from rememberstack.spine.migrations.versions.p9_02_0023_query_space_roles import (
    create_role_if_absent_sql,
)
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def database_url() -> str:
    """The configured server; scratch databases are created beside it."""
    try:
        return load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for migration proofs")


@pytest.fixture
def admin(database_url: str) -> Iterator[Engine]:
    engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


def _url(database_url: str, name: str) -> str:
    return (
        make_url(database_url).set(database=name).render_as_string(hide_password=False)
    )


def _drop(admin: Engine, name: str) -> None:
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        for prefix in ("rememberstack_query_", "rememberstack_graph_"):
            connection.execute(text(f'DROP ROLE IF EXISTS "{prefix}{name}"'))


def test_shared_role_creation_tolerates_a_concurrent_creator(
    database_url: str, admin: Engine
) -> None:
    """The second of two simultaneous creators waits, then finds the role."""
    role = f"rememberstack_race_{uuid4().hex[:8]}"
    sql = text(create_role_if_absent_sql(role, "NOLOGIN"))
    engine = create_engine(database_url)
    errors: list[BaseException] = []
    try:
        with engine.connect() as first, engine.connect() as second:
            first.execute(sql)  # created, not yet committed
            second_pid = second.execute(text("SELECT pg_backend_pid()")).scalar_one()

            def create_again() -> None:
                try:
                    second.execute(sql)
                    second.commit()
                except BaseException as error:  # noqa: BLE001 — reported below
                    errors.append(error)

            racer = threading.Thread(target=create_again)
            racer.start()
            deadline = time.monotonic() + 10
            with admin.connect() as observer:
                while (
                    observer.execute(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity"
                            " WHERE pid = :pid"
                        ),
                        {"pid": second_pid},
                    ).scalar_one_or_none()
                    != "Lock"
                ):
                    assert time.monotonic() < deadline, "the racer never waited"
                    time.sleep(0.05)
            first.commit()
            racer.join(timeout=10)
            assert not racer.is_alive()
        assert errors == []
        with admin.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM pg_roles WHERE rolname = :role"),
                    {"role": role},
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))


def test_two_databases_upgrade_concurrently(
    database_url: str, admin: Engine, tmp_path: Path
) -> None:
    """Two fresh databases migrated by two processes at once both reach head."""
    names = [f"rs_upgrade_{index}_{uuid4().hex[:8]}" for index in range(2)]
    try:
        with admin.connect() as connection:
            for name in names:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
        runs = []
        for name in names:
            # One alembic.ini per database, as an operator's loop would point
            # each run at its own database.
            ini = tmp_path / f"{name}.ini"
            ini.write_text(
                (_ROOT / "alembic.ini")
                .read_text(encoding="utf-8")
                .replace(
                    "[alembic]\n",
                    "[alembic]\nsqlalchemy.url = "
                    + _url(database_url, name).replace("%", "%%")
                    + "\n",
                    1,
                ),
                encoding="utf-8",
            )
            runs.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "alembic",
                        "-c",
                        str(ini),
                        "upgrade",
                        "head",
                    ],
                    cwd=_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            )
        for run in runs:
            output, _ = run.communicate(timeout=600)
            assert run.returncode == 0, output[-4000:]
        heads = set()
        for name in names:
            engine = create_engine(_url(database_url, name))
            try:
                with engine.connect() as connection:
                    heads.add(
                        connection.execute(
                            text("SELECT version_num FROM alembic_version")
                        ).scalar_one()
                    )
            finally:
                engine.dispose()
        assert len(heads) == 1
        with admin.connect() as connection:
            roles = connection.execute(
                text(
                    "SELECT rolname FROM pg_roles"
                    " WHERE rolname = ANY(:names) ORDER BY rolname"
                ),
                {
                    "names": [
                        f"{prefix}{name}"
                        for name in names
                        for prefix in ("rememberstack_query_", "rememberstack_graph_")
                    ]
                },
            ).scalars()
            assert len(list(roles)) == 4
    finally:
        for name in names:
            _drop(admin, name)


def test_a_database_name_too_long_for_its_roles_is_refused(
    database_url: str, admin: Engine
) -> None:
    """A name whose role names PostgreSQL would truncate never gets migrated."""
    name = f"rs_long_{uuid4().hex}{uuid4().hex}"[:44]
    assert len(name) == 44
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        config = Config(str(_ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", _url(database_url, name))
        with pytest.raises(RuntimeError, match="longer than 43 bytes"):
            command.upgrade(config=config, revision="head")
    finally:
        _drop(admin, name)
