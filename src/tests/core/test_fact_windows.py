"""D114: partial dates stay unknown, and historical facts remain retrievable."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from pydantic import ValidationError
import pytest

from rememberstack.core.fact_windows import fact_match_at
from rememberstack.core.fact_windows import fact_match_history
from rememberstack.core.fact_windows import fact_match_overlap
from rememberstack.core.fact_windows import fact_window_from_raw
from rememberstack.model.claims import ClaimValidPrecision
from rememberstack.model.fact_windows import FactWindow
from rememberstack.model.fact_windows import GroundedFactWindow
from rememberstack.model.fact_windows import TemporalMatch


def _at(value: str) -> datetime:
    """An explicitly UTC fixture timestamp."""
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def test_partial_end_is_not_filled_from_start() -> None:
    """A start-only day does not silently become a one-day event or ongoing fact."""
    partial = fact_window_from_raw(
        valid_from=_at("2022-05-10T19:30"),
        valid_until=None,
        precision=ClaimValidPrecision.DAY,
    )
    assert partial.valid_from == _at("2022-05-10")
    assert partial.valid_until is None
    assert fact_match_at(window=partial, at=_at("2026-01-01")) is TemporalMatch.POSSIBLE
    assert fact_match_at(window=partial, at=_at("2021-01-01")) is None


def test_partial_start_stays_unknown_and_known_end_is_exclusive() -> None:
    """An end known only by year keeps its granularity and missing start."""
    partial = fact_window_from_raw(
        valid_from=None,
        valid_until=_at("2022-08-19T12:00"),
        precision=ClaimValidPrecision.YEAR,
    )
    assert partial.valid_from is None
    assert partial.valid_until == _at("2023-01-01")
    assert fact_match_at(window=partial, at=_at("2020-01-01")) is TemporalMatch.POSSIBLE
    assert fact_match_at(window=partial, at=_at("2023-01-01")) is None


def test_completed_win_is_history_but_does_not_hold_now() -> None:
    """A finite world window does not erase continued belief in an achievement."""
    win = fact_window_from_raw(
        valid_from=_at("2022-05-10T19:30"),
        valid_until=_at("2022-05-10T19:30"),
        precision=ClaimValidPrecision.DAY,
    )
    now = _at("2026-01-01")
    assert fact_match_at(window=win, at=now) is None
    assert fact_match_history(window=win, evaluated_at=now) is TemporalMatch.CONFIRMED
    assert (
        fact_match_overlap(window=win, start=_at("2022-01-01"), end=_at("2023-01-01"))
        is TemporalMatch.CONFIRMED
    )


def test_transition_belongs_to_successor_only() -> None:
    """The same half-open instant cannot confirm both adjacent CEO periods."""
    boundary = _at("2022-01-01")
    alice = FactWindow(
        valid_from=_at("2019-01-01"),
        valid_until=boundary,
        valid_precision=ClaimValidPrecision.YEAR,
    )
    bob = FactWindow(valid_from=boundary, valid_precision=ClaimValidPrecision.OPEN)
    assert fact_match_at(window=alice, at=boundary) is None
    assert fact_match_at(window=bob, at=boundary) is TemporalMatch.CONFIRMED


def test_unknown_additional_win_is_possible_not_a_confirmed_dated_count() -> None:
    """Unknown does not mean a win belongs to every requested year."""
    assert (
        fact_match_overlap(
            window=FactWindow(), start=_at("2022-01-01"), end=_at("2023-01-01")
        )
        is TemporalMatch.POSSIBLE
    )


def test_future_fact_is_excluded_from_history() -> None:
    """History is known past up to evaluation, not an unfiltered all-facts mode."""
    future = FactWindow(
        valid_from=_at("2030-01-01"), valid_precision=ClaimValidPrecision.OPEN
    )
    assert fact_match_history(window=future, evaluated_at=_at("2026-01-01")) is None


def test_already_canonical_fact_window_does_not_advance_again() -> None:
    """Validated stored endpoints preserve the exact previously chosen boundary."""
    until = _at("2023-01-01")
    window = FactWindow(
        valid_from=_at("2022-01-01"),
        valid_until=until,
        valid_precision=ClaimValidPrecision.YEAR,
    )
    assert FactWindow.model_validate_json(window.model_dump_json()).valid_until == until


def test_instant_claim_becomes_nonempty_once() -> None:
    """Exact instants remain queryable without a point-specific overlap rule."""
    instant = _at("2022-05-10T16:30")
    window = fact_window_from_raw(
        valid_from=instant, valid_until=instant, precision=ClaimValidPrecision.INSTANT
    )
    assert window.valid_until == instant + timedelta(microseconds=1)
    assert window.valid_until is not None
    assert fact_match_at(window=window, at=instant) is TemporalMatch.CONFIRMED
    assert fact_match_at(window=window, at=window.valid_until) is None


@pytest.mark.parametrize(
    ("start", "end", "precision"),
    [
        (None, None, "day"),
        (None, None, "open"),
        ("2022-01-01", None, "unknown"),
        (None, "2022-01-01", "unknown"),
        ("2022-01-01", "2023-01-01", "open"),
        ("2022-01-01", "2022-01-01", "day"),
        ("2023-01-01", "2022-01-01", "year"),
    ],
)
def test_invalid_shapes_rejected_without_inventing_dates(
    start: str | None, end: str | None, precision: str
) -> None:
    """Reject incoherent authority at the value boundary before any store write."""
    with pytest.raises(ValidationError):
        FactWindow(
            valid_from=_at(start) if start else None,
            valid_until=_at(end) if end else None,
            valid_precision=ClaimValidPrecision(precision),
        )


def test_replacement_requires_evidence_even_when_clearing_dates() -> None:
    """Explicitly clearing an incorrect date is still a grounded adjudication."""
    with pytest.raises(ValidationError):
        GroundedFactWindow(window=FactWindow(), supporting_claim_ids=())
