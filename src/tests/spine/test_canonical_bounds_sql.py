"""D107 §5: the SQL canonical-bounds functions agree with their Python twin."""

from collections.abc import Iterator
from datetime import datetime
from datetime import timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.core.temporal import canonical_bounds
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply the structural head so the migration's functions exist."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for the SQL twin proof")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("precision", "valid_from", "valid_until"),
    [
        ("year", "2022-01-01T00:00", "2022-12-31T00:00"),
        ("year", "2015-01-01T00:00", "2018-12-31T00:00"),
        ("day", "2023-05-07T12:00", "2023-05-07T12:00"),
        ("month", "2022-12-03T00:00", "2022-12-03T00:00"),
        ("quarter", "2022-11-15T00:00", "2022-11-15T00:00"),
        ("instant", "2022-08-21T16:30", "2022-08-21T16:30"),
        ("open", "2019-01-01T00:00", None),
    ],
)
def test_sql_twin_matches_python(
    database_engine: Engine, precision: str, valid_from: str, valid_until: str | None
) -> None:
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT claim_canonical_start(CAST(:f AS timestamptz),"
                " CAST(:p AS claim_valid_precision)) AS s,"
                " claim_canonical_end(CAST(:f AS timestamptz),"
                " CAST(:u AS timestamptz), CAST(:p AS claim_valid_precision)) AS e"
            ),
            {"f": valid_from, "u": valid_until, "p": precision},
        ).one()
    expected = canonical_bounds(
        valid_from=_ts(valid_from),
        valid_until=_ts(valid_until) if valid_until else None,
        precision=precision,
    )
    assert row.s == expected.start
    assert row.e == expected.end


def test_sql_twin_unknown_precision_is_no_interval(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT claim_canonical_start(NULL, 'unknown') AS s,"
                " claim_canonical_end(NULL, NULL, 'unknown') AS e"
            )
        ).one()
    assert row.s is None and row.e is None
