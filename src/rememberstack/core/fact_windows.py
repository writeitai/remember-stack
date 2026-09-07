"""Construction and query matching for one chosen fact window (D114)."""

from datetime import datetime

from rememberstack.core.temporal import canonical_endpoint
from rememberstack.model.claims import ClaimValidPrecision
from rememberstack.model.fact_windows import FactWindow
from rememberstack.model.fact_windows import TemporalMatch


def fact_window_from_raw(
    *,
    valid_from: datetime | None,
    valid_until: datetime | None,
    precision: ClaimValidPrecision,
) -> FactWindow:
    """Align known raw boundaries once, preserving either unknown side.

    For a complete instant claim, its inclusive identical endpoints become
    a nonempty microsecond interval. Already-canonical values instead go
    straight into FactWindow; calling this again would advance their ends.
    """
    return FactWindow(
        valid_from=(
            canonical_endpoint(value=valid_from, precision=precision, is_end=False)
            if valid_from is not None
            else None
        ),
        valid_until=(
            canonical_endpoint(value=valid_until, precision=precision, is_end=True)
            if valid_until is not None
            else None
        ),
        valid_precision=precision,
    )


def fact_match_at(*, window: FactWindow, at: datetime) -> TemporalMatch | None:
    """Containment at an instant, disclosing uncertainty in incomplete windows."""
    if window.valid_from is not None and at < window.valid_from:
        return None
    if window.valid_until is not None and at >= window.valid_until:
        return None
    return TemporalMatch.CONFIRMED if window.is_complete else TemporalMatch.POSSIBLE


def fact_match_overlap(
    *, window: FactWindow, start: datetime, end: datetime
) -> TemporalMatch | None:
    """Match a half-open query interval without interpreting an unknown as infinity."""
    if end <= start:
        raise ValueError("query interval must be nonempty")
    if window.valid_from is not None and window.valid_from >= end:
        return None
    if window.valid_until is not None and window.valid_until <= start:
        return None
    return TemporalMatch.CONFIRMED if window.is_complete else TemporalMatch.POSSIBLE


def fact_match_history(
    *, window: FactWindow, evaluated_at: datetime
) -> TemporalMatch | None:
    """Known history up to evaluation, distinct from containment at that instant."""
    if window.valid_from is None:
        return TemporalMatch.POSSIBLE
    if window.valid_from > evaluated_at:
        return None
    return TemporalMatch.CONFIRMED if window.is_complete else TemporalMatch.POSSIBLE
