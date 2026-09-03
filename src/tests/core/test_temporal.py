"""D107 §5 canonical bounds: the pure function and its SQL twin agree."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest

from rememberstack.core.temporal import canonical_bounds
from rememberstack.core.temporal import CanonicalBounds
from rememberstack.core.temporal import inclusive_request
from rememberstack.core.temporal import point_request

_UTC = timezone.utc
_US = timedelta(microseconds=1)


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=_UTC)


@pytest.mark.parametrize(
    ("precision", "valid_from", "valid_until", "start", "end"),
    [
        # a year stored as its first and last day covers the whole year
        (
            "year",
            "2022-01-01T00:00",
            "2022-12-31T00:00",
            "2022-01-01T00:00",
            "2023-01-01T00:00",
        ),
        # a day whose stored start is noon is still the whole calendar day
        (
            "day",
            "2023-05-07T12:00",
            "2023-05-07T12:00",
            "2023-05-07T00:00",
            "2023-05-08T00:00",
        ),
        # month and quarter units, including a year rollover
        (
            "month",
            "2022-12-03T00:00",
            "2022-12-03T00:00",
            "2022-12-01T00:00",
            "2023-01-01T00:00",
        ),
        (
            "quarter",
            "2022-11-15T00:00",
            "2022-11-15T00:00",
            "2022-10-01T00:00",
            "2023-01-01T00:00",
        ),
        # a bounded span keeps its own start and end units
        (
            "year",
            "2015-01-01T00:00",
            "2018-12-31T00:00",
            "2015-01-01T00:00",
            "2019-01-01T00:00",
        ),
    ],
)
def test_bounded_precisions_align_both_ends(
    precision: str, valid_from: str, valid_until: str, start: str, end: str
) -> None:
    bounds = canonical_bounds(
        valid_from=_ts(valid_from), valid_until=_ts(valid_until), precision=precision
    )
    assert bounds == CanonicalBounds(start=_ts(start), end=_ts(end))


def test_instant_is_a_non_empty_point_that_overlaps_itself() -> None:
    t = _ts("2022-08-21T16:30")
    bounds = canonical_bounds(valid_from=t, valid_until=t, precision="instant")
    assert bounds == CanonicalBounds(start=t, end=t + _US)
    assert bounds.overlaps(bounds)


def test_open_is_unbounded_and_unknown_is_no_interval() -> None:
    start = _ts("2019-01-01T00:00")
    assert canonical_bounds(valid_from=start, valid_until=None, precision="open") == (
        CanonicalBounds(start=start, end=None)
    )
    unknown = canonical_bounds(valid_from=None, valid_until=None, precision="unknown")
    assert unknown == CanonicalBounds(start=None, end=None)
    assert not unknown.is_known
    assert not unknown.overlaps(
        canonical_bounds(valid_from=start, valid_until=None, precision="open")
    )


def test_adjacent_days_do_not_overlap_but_an_intraday_request_finds_the_day() -> None:
    day = canonical_bounds(
        valid_from=_ts("2023-05-07T00:00"),
        valid_until=_ts("2023-05-07T00:00"),
        precision="day",
    )
    next_day = canonical_bounds(
        valid_from=_ts("2023-05-08T00:00"),
        valid_until=_ts("2023-05-08T00:00"),
        precision="day",
    )
    assert not day.overlaps(next_day)
    intraday = inclusive_request(
        from_=_ts("2023-05-07T09:00"), to=_ts("2023-05-07T23:00")
    )
    assert day.overlaps(intraday)
    assert day.overlaps(point_request(at=_ts("2023-05-07T12:00")))
    # a day-precision claim and an instant inside that day overlap
    instant = canonical_bounds(
        valid_from=_ts("2023-05-07T15:00"),
        valid_until=_ts("2023-05-07T15:00"),
        precision="instant",
    )
    assert day.overlaps(instant)


def test_a_point_request_is_never_empty() -> None:
    t = _ts("2024-06-15T12:00")
    request = inclusive_request(from_=t, to=t)
    assert request == point_request(at=t)
    assert request.end is not None and request.end > request.start  # type: ignore[operator]


def test_naive_inputs_are_read_as_utc() -> None:
    bounds = canonical_bounds(
        valid_from=datetime(2022, 1, 1, 12, 0),
        valid_until=datetime(2022, 1, 1, 12, 0),
        precision="day",
    )
    assert bounds.start == _ts("2022-01-01T00:00")
    assert bounds.end == _ts("2022-01-02T00:00")
