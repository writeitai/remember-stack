"""Canonical bounds for D41 claim windows (D107 §5, WP-T.0).

A claim's stored world-time window is *inclusive* and carries a precision:
``day`` / ``month`` / ``quarter`` / ``year`` store the resolved unit's first
and last calendar day, ``instant`` stores one timestamp in both ends, ``open``
stores a start and no end, ``unknown`` stores nothing. Comparing those raw
values directly treats a day as a zero-width point (an intraday as-of window
misses it) and lets adjacent units touch without overlapping.

``canonical_bounds`` turns a stored window into one **half-open** interval
``[start, end)`` whose ends are aligned to the precision unit in UTC, so one
overlap predicate — ``a.start < b.end AND b.start < a.end`` with a ``None``
end as +∞ — is correct for every precision. Storage is unchanged; only
comparisons canonicalise. The SQL twins ``claim_canonical_start`` /
``claim_canonical_end`` (migration ``p9_26_0047``) implement the same table
and MUST stay equivalent; ``test_temporal.py`` pins both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Final

_MICROSECOND: Final = timedelta(microseconds=1)

_BOUNDED_PRECISIONS: Final = frozenset({"day", "month", "quarter", "year"})


@dataclass(frozen=True)
class CanonicalBounds:
    """One half-open world-time interval, or no interval at all.

    ``start`` is inclusive; ``end`` is exclusive and ``None`` means unbounded.
    Both are ``None`` when the claim carries no usable window (``unknown``).
    """

    start: datetime | None
    end: datetime | None

    @property
    def is_known(self) -> bool:
        """True when the claim carries a window at all."""
        return self.start is not None

    def overlaps(self, other: CanonicalBounds) -> bool:
        """Half-open overlap; an unknown side never overlaps anything."""
        if self.start is None or other.start is None:
            return False
        if self.end is not None and self.end <= other.start:
            return False
        if other.end is not None and other.end <= self.start:
            return False
        return True


def canonical_bounds(
    *, valid_from: datetime | None, valid_until: datetime | None, precision: str
) -> CanonicalBounds:
    """Canonicalise one stored D41 window by its precision.

    - ``day`` / ``month`` / ``quarter`` / ``year``: ``[trunc(unit, from),
      trunc(unit, until) + unit)`` — a year stored 2022-01-01…2022-12-31
      becomes ``[2022-01-01, 2023-01-01)``; a day whose stored start is noon
      still becomes the whole calendar day.
    - ``instant``: ``[t, t + 1 µs)`` — a non-empty point.
    - ``open``: ``[from, None)``.
    - ``unknown`` (or a missing start): no interval.

    Timestamps are interpreted in UTC (D41 bounds are timezone-aware); naive
    inputs are treated as UTC rather than rejected, mirroring the SQL twin.
    """
    if precision == "unknown" or valid_from is None:
        return CanonicalBounds(start=None, end=None)
    start = _utc(valid_from)
    if precision == "instant":
        return CanonicalBounds(start=start, end=start + _MICROSECOND)
    if precision == "open":
        return CanonicalBounds(start=start, end=None)
    if precision in _BOUNDED_PRECISIONS:
        end_source = _utc(valid_until) if valid_until is not None else start
        return CanonicalBounds(
            start=_truncate(start, precision),
            end=_advance(_truncate(end_source, precision), precision),
        )
    raise ValueError(f"unknown claim_valid_precision {precision!r}")


def point_request(*, at: datetime) -> CanonicalBounds:
    """The canonical form of an inclusive point-in-time request ``(t, t)``."""
    start = _utc(at)
    return CanonicalBounds(start=start, end=start + _MICROSECOND)


def inclusive_request(*, from_: datetime, to: datetime) -> CanonicalBounds:
    """The canonical form of an inclusive caller window ``[from, to]``.

    ``to`` is inclusive for the caller, so the exclusive end is one microsecond
    later; ``from == to`` is therefore a point query, never an empty one.
    """
    return CanonicalBounds(start=_utc(from_), end=_utc(to) + _MICROSECOND)


def _utc(value: datetime) -> datetime:
    """Normalise to an aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _truncate(value: datetime, precision: str) -> datetime:
    """Truncate to the first instant of the precision unit, in UTC."""
    if precision == "day":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if precision == "month":
        return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if precision == "quarter":
        first_month = ((value.month - 1) // 3) * 3 + 1
        return value.replace(
            month=first_month, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    if precision == "year":
        return value.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"not a bounded precision: {precision!r}")


def _advance(value: datetime, precision: str) -> datetime:
    """Add exactly one precision unit to an already-truncated instant."""
    if precision == "day":
        return value + timedelta(days=1)
    if precision == "month":
        return _add_months(value, 1)
    if precision == "quarter":
        return _add_months(value, 3)
    if precision == "year":
        return value.replace(year=value.year + 1)
    raise ValueError(f"not a bounded precision: {precision!r}")


def _add_months(value: datetime, months: int) -> datetime:
    """Advance a first-of-month instant by whole months."""
    total = value.month - 1 + months
    return value.replace(year=value.year + total // 12, month=total % 12 + 1)
