"""Pure D107/D110 rules shared by temporal writers on both fact planes.

These functions apply already-authorized endpoint choices. They do not infer
identity, choose evidence, acquire locks, or replace journal/support validation.
All instants come from the caller; no wall clock can become a world boundary.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from typing import Literal
from uuid import UUID

from rememberstack.core.temporal import canonical_bounds
from rememberstack.model.claims import ClaimValidKind
from rememberstack.model.claims import ClaimValidPrecision
from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalBasis
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import OccurrenceWindow
from rememberstack.model.fact_temporal import ReversibleTemporalEffect
from rememberstack.model.fact_temporal import TemporalMembership
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.fact_temporal import VerdictWindow

_PRECISION_ORDER: Final = (
    ClaimValidPrecision.INSTANT,
    ClaimValidPrecision.DAY,
    ClaimValidPrecision.MONTH,
    ClaimValidPrecision.QUARTER,
    ClaimValidPrecision.YEAR,
    ClaimValidPrecision.OPEN,
)


@dataclass(frozen=True, kw_only=True)
class TemporalMutation:
    """A proposed journal outcome and its complete resulting temporal state."""

    state: FactTemporalState
    result: TemporalResult
    reason: str
    skipped_components: tuple[Literal["from", "until"], ...] = ()


def fact_kind(
    *, claim_kind: ClaimValidKind | None, shape: FactTemporalKind
) -> FactTemporalKind:
    """Prefer explicit D41 kind; use normalized wording shape only when undated."""
    if claim_kind in (
        ClaimValidKind.PROPOSITION_VALIDITY,
        ClaimValidKind.EFFECTIVE_PERIOD,
    ):
        return FactTemporalKind.STATE
    if claim_kind in (ClaimValidKind.EVENT_TIME, ClaimValidKind.MEASUREMENT_PERIOD):
        return FactTemporalKind.OCCURRENCE
    return shape


def occurrence_union(*, claims: Iterable[ClaimTemporalWindow]) -> OccurrenceWindow:
    """Reduce attached non-forgotten evidence without writing verdict authority."""
    start: datetime | None = None
    end: datetime | None = None
    precision: ClaimValidPrecision | None = None
    unbounded = False
    for claim in claims:
        bounds = canonical_bounds(
            valid_from=claim.valid_from,
            valid_until=claim.valid_until,
            precision=claim.precision,
        )
        if bounds.start is None:
            continue
        start = bounds.start if start is None else min(start, bounds.start)
        if bounds.end is None:
            unbounded = True
        else:
            end = bounds.end if end is None else max(end, bounds.end)
        if precision is None or _PRECISION_ORDER.index(
            claim.precision
        ) > _PRECISION_ORDER.index(precision):
            precision = claim.precision
    if start is None:
        return OccurrenceWindow()
    return OccurrenceWindow(
        start=start, end=None if unbounded else end, precision=precision
    )


def seed_fact(
    *, seed: ClaimTemporalWindow, shape: FactTemporalKind, ingested_at: datetime
) -> FactTemporalState:
    """Seed once from the creating claim; belief ingestion never supplies its dates."""
    kind = fact_kind(claim_kind=seed.kind, shape=shape)
    occurrence = occurrence_union(claims=(seed,))
    end = occurrence.end if kind is FactTemporalKind.STATE else None
    return FactTemporalState(
        kind=kind,
        verdict=VerdictWindow(
            start=occurrence.start,
            end=end,
            start_basis=(
                FactTemporalBasis.WORLD_TIME
                if occurrence.start is not None
                else FactTemporalBasis.UNKNOWN
            ),
            end_basis=(
                FactTemporalBasis.WORLD_TIME
                if end is not None
                else FactTemporalBasis.UNKNOWN
            ),
        ),
        occurrence=occurrence,
        seed_claim_id=seed.claim_id,
        ingested_at=ingested_at,
    )


def world_membership(*, window: VerdictWindow, at: datetime) -> TemporalMembership:
    """Apply half-open bounds before distinguishing erasure from ordinary unknown."""
    if window.start is not None and at < window.start:
        return TemporalMembership.OUTSIDE
    if window.end is not None and at >= window.end:
        return TemporalMembership.OUTSIDE
    if FactTemporalBasis.ERASED in (window.start_basis, window.end_basis):
        return TemporalMembership.UNCERTAIN
    return TemporalMembership.INSIDE


def fact_membership(
    *, state: FactTemporalState, world_at: datetime, believed_at: datetime
) -> TemporalMembership:
    """Keep world-time lookup independent of when the system acquired the fact."""
    if believed_at < state.ingested_at or (
        state.invalidated_at is not None and believed_at >= state.invalidated_at
    ):
        return TemporalMembership.OUTSIDE
    return world_membership(window=state.verdict, at=world_at)


def cap_allowed(*, state: FactTemporalState, boundary: datetime | None) -> bool:
    """A cap must shorten a state strictly inside its existing known endpoints."""
    return (
        state.kind is FactTemporalKind.STATE
        and boundary is not None
        and (state.verdict.start is None or boundary > state.verdict.start)
        and (state.verdict.end is None or boundary < state.verdict.end)
    )


def cap_fact(
    *, state: FactTemporalState, boundary: datetime | None, operation_id: UUID
) -> TemporalMutation:
    """Apply an adjudicated successor boundary; refusal leaves both ends intact."""
    if not cap_allowed(state=state, boundary=boundary):
        return TemporalMutation(
            state=state, result=TemporalResult.REFUSED, reason="cap_guard_refused"
        )
    verdict = VerdictWindow(
        start=state.verdict.start,
        end=boundary,
        start_basis=state.verdict.start_basis,
        end_basis=FactTemporalBasis.VERDICT,
    )
    return _changed(
        state=state, verdict=verdict, operation_id=operation_id, reason="successor_cap"
    )


def correct_window(
    *,
    state: FactTemporalState,
    start: datetime | None,
    end: datetime | None,
    operation_id: UUID,
    neighbours: tuple[FactTemporalState, ...],
) -> TemporalMutation:
    """Apply grounded monotonic choices; None means keep the existing component."""
    old = state.verdict
    if start is not None and old.start is not None and start > old.start:
        return _refused(state=state, reason="start_cannot_move_later")
    if end is not None and (
        state.kind is not FactTemporalKind.STATE
        or (old.end is not None and end > old.end)
    ):
        return _refused(state=state, reason="end_cannot_reopen_or_cap_nonstate")
    move_start = start is not None and start != old.start
    move_end = end is not None and end != old.end
    proposed = VerdictWindow(
        start=start if move_start else old.start,
        end=end if move_end else old.end,
        start_basis=FactTemporalBasis.VERDICT if move_start else old.start_basis,
        end_basis=FactTemporalBasis.VERDICT if move_end else old.end_basis,
    )
    if not _valid_window(state=state, window=proposed, neighbours=neighbours):
        return _refused(state=state, reason="invalid_combined_window")
    return _changed(
        state=state,
        verdict=proposed,
        operation_id=operation_id,
        reason="grounded_correction",
    )


def compensate_window(
    *,
    state: FactTemporalState,
    target: ReversibleTemporalEffect,
    operation_id: UUID,
    neighbours: tuple[FactTemporalState, ...],
) -> TemporalMutation:
    """Restore only still-owned changed components, preserving later independent caps.

    The journal caller must establish that target is an accepted correction or
    compensation with current independent support, never a checkpoint root.
    """
    restore_start = (
        target.changed_start and state.from_operation_id == target.operation_id
    )
    restore_end = target.changed_end and state.until_operation_id == target.operation_id
    skipped: list[Literal["from", "until"]] = []
    if target.changed_start and not restore_start:
        skipped.append("from")
    if target.changed_end and not restore_end:
        skipped.append("until")
    old = state.verdict
    proposed = VerdictWindow(
        start=target.before.start if restore_start else old.start,
        end=target.before.end if restore_end else old.end,
        start_basis=target.before.start_basis if restore_start else old.start_basis,
        end_basis=target.before.end_basis if restore_end else old.end_basis,
    )
    if not _valid_window(state=state, window=proposed, neighbours=neighbours):
        return _refused(state=state, reason="invalid_combined_compensation")
    result = _changed(
        state=state,
        verdict=proposed,
        operation_id=operation_id,
        reason="compensating_verdict",
    )
    return TemporalMutation(
        state=result.state,
        result=result.result,
        reason=result.reason,
        skipped_components=tuple(skipped),
    )


def withdraw_fact(
    *,
    state: FactTemporalState,
    boundary: datetime | None,
    reconciliation_at: datetime,
    operation_id: UUID,
) -> TemporalMutation:
    """D55 closes belief at its recorded instant even when a world cap is refused."""
    verdict = state.verdict
    permitted = cap_allowed(state=state, boundary=boundary)
    if permitted:
        verdict = VerdictWindow(
            start=verdict.start,
            end=boundary,
            start_basis=verdict.start_basis,
            end_basis=FactTemporalBasis.SOURCE_REMOVED,
        )
    invalidated_at = state.invalidated_at or reconciliation_at
    if verdict == state.verdict and invalidated_at == state.invalidated_at:
        return TemporalMutation(
            state=state, result=TemporalResult.NOOP, reason="already_withdrawn"
        )
    result = _changed(
        state=state,
        verdict=verdict,
        operation_id=operation_id,
        reason="source_withdrawal",
        invalidated_at=invalidated_at,
    )
    return TemporalMutation(
        state=result.state,
        result=result.result,
        reason="withdrawn_with_world_cap"
        if permitted
        else "withdrawn_without_world_cap",
    )


def _refused(*, state: FactTemporalState, reason: str) -> TemporalMutation:
    """Refusal preserves the exact existing tuple and its revision."""
    return TemporalMutation(state=state, result=TemporalResult.REFUSED, reason=reason)


def _changed(
    *,
    state: FactTemporalState,
    verdict: VerdictWindow,
    operation_id: UUID,
    reason: str,
    invalidated_at: datetime | None = None,
) -> TemporalMutation:
    """Transfer ownership only for changed value/basis pairs and advance once."""
    old = state.verdict
    invalidated_at = state.invalidated_at if invalidated_at is None else invalidated_at
    if verdict == old and invalidated_at == state.invalidated_at:
        return TemporalMutation(state=state, result=TemporalResult.NOOP, reason=reason)
    changed_start = (old.start, old.start_basis) != (verdict.start, verdict.start_basis)
    changed_end = (old.end, old.end_basis) != (verdict.end, verdict.end_basis)
    updated = FactTemporalState(
        **(
            state.model_dump()
            | {
                "verdict": verdict,
                "revision": state.revision + 1,
                "invalidated_at": invalidated_at,
                "from_operation_id": operation_id
                if changed_start
                else state.from_operation_id,
                "until_operation_id": operation_id
                if changed_end
                else state.until_operation_id,
            }
        )
    )
    return TemporalMutation(state=updated, result=TemporalResult.APPLIED, reason=reason)


def _valid_window(
    *,
    state: FactTemporalState,
    window: VerdictWindow,
    neighbours: tuple[FactTemporalState, ...],
) -> bool:
    """Check the combined proposal against local shape and eligible state slices."""
    if state.kind is FactTemporalKind.OCCURRENCE and window.end is not None:
        return False
    if state.kind is not FactTemporalKind.STATE:
        return True
    if (
        window.start is not None
        and window.end is not None
        and window.end <= window.start
    ):
        return False
    if not _ordinary_state(state=state, window=window):
        return True
    return not any(
        _ordinary_state(state=neighbour, window=neighbour.verdict)
        and _overlaps(left=window, right=neighbour.verdict)
        for neighbour in neighbours
    )


def _ordinary_state(*, state: FactTemporalState, window: VerdictWindow) -> bool:
    """Match the state exclusion's belief, contradiction, and erased-basis scope."""
    return (
        state.kind is FactTemporalKind.STATE
        and state.invalidated_at is None
        and state.contradiction_group is None
        and FactTemporalBasis.ERASED not in (window.start_basis, window.end_basis)
    )


def _overlaps(*, left: VerdictWindow, right: VerdictWindow) -> bool:
    """Half-open overlap with ordinary NULL endpoints interpreted as infinities."""
    if left.end is not None and right.start is not None and left.end <= right.start:
        return False
    return not (
        right.end is not None and left.start is not None and right.end <= left.start
    )
