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


@pytest.mark.parametrize(
    "session_zone", ["America/New_York", "Asia/Kolkata", "Pacific/Auckland"]
)
@pytest.mark.parametrize(
    ("precision", "valid_from", "valid_until"),
    [
        (
            "month",
            "2023-03-01T00:00",
            "2023-03-01T00:00",
        ),  # a month boundary that local calendars shift
        ("day", "2023-03-12T00:00", "2023-03-12T00:00"),  # a US DST transition day
        ("year", "2022-01-01T00:00", "2022-12-31T00:00"),
        (
            "quarter",
            "2022-11-15T00:00",
            None,
        ),  # bounded precision with a null stored end
        ("instant", "2022-08-21T16:30", "2022-08-21T16:30"),
    ],
)
def test_sql_twin_is_session_timezone_independent(
    database_engine: Engine,
    session_zone: str,
    precision: str,
    valid_from: str,
    valid_until: str | None,
) -> None:
    """IMMUTABLE must mean it: the same inputs give the same UTC-aligned bounds
    whatever the session TimeZone, or the expression index would be unsafe."""
    with database_engine.connect() as connection:
        connection.execute(text(f"SET LOCAL TIME ZONE '{session_zone}'"))
        row = connection.execute(
            text(
                "SELECT claim_canonical_start(CAST(:f AS timestamptz),"
                " CAST(:p AS claim_valid_precision)) AS s,"
                " claim_canonical_end(CAST(:f AS timestamptz),"
                " CAST(:u AS timestamptz), CAST(:p AS claim_valid_precision)) AS e"
            ),
            {
                "f": valid_from + "+00:00",
                "u": (valid_until + "+00:00") if valid_until else None,
                "p": precision,
            },
        ).one()
    expected = canonical_bounds(
        valid_from=_ts(valid_from),
        valid_until=_ts(valid_until) if valid_until else None,
        precision=precision,
    )
    assert row.s == expected.start, (session_zone, precision, "start")
    assert row.e == expected.end, (session_zone, precision, "end")


@pytest.mark.parametrize(
    ("precision", "valid_from", "valid_until"),
    [
        ("year", "2022-01-01T00:00", "2022-12-31T00:00"),
        ("day", "2023-05-07T12:00", "2023-05-07T12:00"),
        ("instant", "2023-05-07T12:00:00.000001", "2023-05-07T12:00:00.000001"),
        ("open", "2019-01-01T00:00", None),
        ("unknown", "2022-01-01T00:00", "2022-12-31T00:00"),
    ],
)
def test_query_space_canonical_bounds_wraps_the_public_twins(
    database_engine: Engine,
    precision: str,
    valid_from: str,
    valid_until: str | None,
) -> None:
    """memory_v1.canonical_bounds is the public twins, published as text precision."""
    expected = canonical_bounds(
        valid_from=_ts(valid_from),
        valid_until=_ts(valid_until) if valid_until else None,
        precision=precision,
    )
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT canon_start, canon_end"
                " FROM memory_v1.canonical_bounds("
                " CAST(:f AS timestamptz), CAST(:u AS timestamptz), :p)"
            ),
            {
                "f": valid_from + "+00:00",
                "u": (valid_until + "+00:00") if valid_until else None,
                "p": precision,
            },
        ).one()
    assert row.canon_start == expected.start
    assert row.canon_end == expected.end


def test_prompt_dates_render_the_utc_calendar_day() -> None:
    """A driver row in a non-UTC session zone must still print the UTC day."""
    from zoneinfo import ZoneInfo

    from rememberstack.spine.observation_adjudication import _date_text

    canonical_midnight_utc = datetime(2023, 5, 7, tzinfo=timezone.utc)
    as_new_york = canonical_midnight_utc.astimezone(ZoneInfo("America/New_York"))
    assert as_new_york.date().isoformat() == "2023-05-06"  # the trap
    assert _date_text(as_new_york) == "2023-05-07"
    assert (
        _date_text(datetime(2023, 5, 7, 12, 0)) == "2023-05-07"
    )  # naive is read as UTC
