"""Construction and query matching for one chosen fact window (D118)."""

from datetime import datetime

from rememberstack.core.temporal import _utc
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
    at = _utc(at)
    if window.valid_from is not None and at < window.valid_from:
        return None
    if window.valid_until is not None and at >= window.valid_until:
        return None
    return TemporalMatch.CONFIRMED if window.is_complete else TemporalMatch.POSSIBLE


def fact_match_overlap(
    *, window: FactWindow, start: datetime, end: datetime
) -> TemporalMatch | None:
    """Match existing inclusive query bounds against canonical half-open facts."""
    start, end = _utc(start), _utc(end)
    if end < start:
        raise ValueError("query interval end must not precede its start")
    if window.valid_from is not None and window.valid_from > end:
        return None
    if window.valid_until is not None and window.valid_until <= start:
        return None
    return TemporalMatch.CONFIRMED if window.is_complete else TemporalMatch.POSSIBLE


def fact_match_history(
    *, window: FactWindow, evaluated_at: datetime
) -> TemporalMatch | None:
    """Known history up to evaluation, distinct from containment at that instant."""
    evaluated_at = _utc(evaluated_at)
    if window.valid_from is None:
        return TemporalMatch.POSSIBLE
    if window.valid_from > evaluated_at:
        return None
    return TemporalMatch.CONFIRMED if window.is_complete else TemporalMatch.POSSIBLE


def _format_boundary(value: datetime, precision: ClaimValidPrecision) -> str:
    """Format a boundary timestamp matching its declared precision without inventing noise."""
    if precision == ClaimValidPrecision.YEAR:
        return str(value.year)
    if precision == ClaimValidPrecision.QUARTER:
        return f"{value.year} Q{(value.month - 1) // 3 + 1}"
    if precision == ClaimValidPrecision.MONTH:
        return value.strftime("%Y-%m")
    if precision in (ClaimValidPrecision.DAY, ClaimValidPrecision.OPEN):
        return value.date().isoformat()
    return value.isoformat().replace("+00:00", "Z")


def describe_fact_window(*, window: FactWindow) -> str:
    """Render dated historical prose whose meaning does not depend on today's clock."""
    from datetime import timedelta

    if window.valid_precision == ClaimValidPrecision.UNKNOWN:
        return "world date unknown"
    if window.valid_precision == ClaimValidPrecision.OPEN:
        assert window.valid_from is not None
        return f"since {_format_boundary(window.valid_from, window.valid_precision)}; no end recorded"
    start = (
        _format_boundary(window.valid_from, window.valid_precision)
        if window.valid_from is not None
        else None
    )
    end = (
        _format_boundary(
            window.valid_until - timedelta(microseconds=1), window.valid_precision
        )
        if window.valid_until is not None
        else None
    )
    if start is None:
        return (
            f"start unknown; through {end} ({window.valid_precision.value} precision)"
        )
    if end is None:
        return f"start {start}; end unknown ({window.valid_precision.value} precision)"
    period = start if start == end else f"{start} through {end}"
    return f"{period} ({window.valid_precision.value} precision)"


def format_fact_temporal_annotation(*, window: FactWindow) -> str:
    """Render canonical prompt serialization for dated facts: e.g. ' (valid: 2022-01-21)'.

    If the date is unknown, returns empty string so undated facts remain clean prose.
    """
    if window.valid_precision == ClaimValidPrecision.UNKNOWN:
        return ""
    from datetime import timedelta

    if window.valid_precision == ClaimValidPrecision.OPEN:
        assert window.valid_from is not None
        return f" (valid: since {_format_boundary(window.valid_from, window.valid_precision)}, ongoing)"
    start = (
        _format_boundary(window.valid_from, window.valid_precision)
        if window.valid_from is not None
        else None
    )
    end = (
        _format_boundary(
            window.valid_until - timedelta(microseconds=1), window.valid_precision
        )
        if window.valid_until is not None
        else None
    )
    if start is None:
        return f" (valid: through {end})"
    if end is None:
        return f" (valid: from {start})"
    period = start if start == end else f"{start} through {end}"
    return f" (valid: {period})"
