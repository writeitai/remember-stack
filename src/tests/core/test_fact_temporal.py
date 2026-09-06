"""D107/D110 temporal authority, clock separation, and reversible write rules."""

from datetime import datetime
from datetime import timezone
from uuid import UUID
from uuid import uuid4

from pydantic import ValidationError
import pytest

from rememberstack.core.fact_temporal import cap_fact
from rememberstack.core.fact_temporal import compensate_window
from rememberstack.core.fact_temporal import correct_window
from rememberstack.core.fact_temporal import fact_membership
from rememberstack.core.fact_temporal import occurrence_union
from rememberstack.core.fact_temporal import seed_fact
from rememberstack.core.fact_temporal import withdraw_fact
from rememberstack.core.fact_temporal import world_membership
from rememberstack.model.claims import ClaimValidKind
from rememberstack.model.claims import ClaimValidPrecision
from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalBasis as Basis
from rememberstack.model.fact_temporal import FactTemporalKind as Kind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import ReversibleTemporalEffect
from rememberstack.model.fact_temporal import TemporalMembership as Membership
from rememberstack.model.fact_temporal import TemporalResult as Result
from rememberstack.model.fact_temporal import VerdictWindow


def _at(year: int, *, month: int = 1, day: int = 1) -> datetime:
    """An explicit UTC world-time instant, independent of the test runner clock."""
    return datetime(year, month, day, tzinfo=timezone.utc)


def _state(
    *,
    start: int | None = 2020,
    end: int | None = None,
    kind: Kind = Kind.STATE,
    operation_id: UUID | None = None,
) -> FactTemporalState:
    """A fact ingested in 2026, with dates and endpoint ownership supplied separately."""
    return FactTemporalState(
        kind=kind,
        verdict=VerdictWindow(
            start=_at(start) if start else None,
            end=_at(end) if end else None,
            start_basis=Basis.WORLD_TIME if start else Basis.UNKNOWN,
            end_basis=Basis.WORLD_TIME if end else Basis.UNKNOWN,
        ),
        ingested_at=_at(2026),
        revision=5,
        from_operation_id=operation_id,
        until_operation_id=operation_id,
    )


@pytest.mark.parametrize(
    ("claim_kind", "kind", "expected_end"),
    [
        (ClaimValidKind.EFFECTIVE_PERIOD, Kind.STATE, 2019),
        (ClaimValidKind.PROPOSITION_VALIDITY, Kind.STATE, 2019),
        (ClaimValidKind.MEASUREMENT_PERIOD, Kind.OCCURRENCE, None),
        (ClaimValidKind.EVENT_TIME, Kind.OCCURRENCE, None),
    ],
)
def test_seed_uses_world_period_and_keeps_occurrences_uncapped(
    claim_kind: ClaimValidKind, kind: Kind, expected_end: int | None
) -> None:
    """A historical spell imported today does not acquire today's world-time."""
    seed = ClaimTemporalWindow(
        claim_id=uuid4(),
        kind=claim_kind,
        valid_from=_at(2015),
        valid_until=_at(2018, month=12, day=31),
        precision=ClaimValidPrecision.YEAR,
    )
    state = seed_fact(seed=seed, shape=Kind.UNKNOWN, ingested_at=_at(2026))
    assert state.kind is kind
    assert state.seed_claim_id == seed.claim_id
    assert state.ingested_at == _at(2026)
    assert state.verdict.start == _at(2015)
    assert state.verdict.end == (_at(expected_end) if expected_end else None)
    assert state.occurrence.start == _at(2015)
    assert state.occurrence.end == _at(2019)
    assert state.occurrence.precision is ClaimValidPrecision.YEAR


@pytest.mark.parametrize("shape", tuple(Kind))
def test_missing_date_keeps_normalized_shape_without_fabricating_bounds(
    shape: Kind,
) -> None:
    """An undated occurrence remains an occurrence instead of becoming a state."""
    state = seed_fact(
        seed=ClaimTemporalWindow(claim_id=uuid4()), shape=shape, ingested_at=_at(2026)
    )
    assert state.kind is shape
    assert state.verdict == VerdictWindow()
    assert state.occurrence.start is None
    assert state.occurrence.precision is None


def test_occurrence_union_widens_without_reseeding_verdict() -> None:
    """Later historical evidence changes metadata, never the accepted start."""
    seed = ClaimTemporalWindow(
        claim_id=uuid4(),
        kind=ClaimValidKind.EVENT_TIME,
        valid_from=_at(2022, month=10),
        valid_until=_at(2022, month=10),
        precision=ClaimValidPrecision.DAY,
    )
    earlier = ClaimTemporalWindow(
        claim_id=uuid4(),
        kind=ClaimValidKind.EVENT_TIME,
        valid_from=_at(2022),
        valid_until=_at(2022),
        precision=ClaimValidPrecision.MONTH,
    )
    state = seed_fact(seed=seed, shape=Kind.UNKNOWN, ingested_at=_at(2026))
    union = occurrence_union(
        claims=(seed, earlier, ClaimTemporalWindow(claim_id=uuid4()))
    )
    assert union.start == _at(2022)
    assert union.end == _at(2022, month=10, day=2)
    assert union.precision is ClaimValidPrecision.MONTH
    assert state.verdict.start == _at(2022, month=10)


def test_open_evidence_dominates_union_end_and_precision() -> None:
    """Union remains open even if another source supplied a finite later span."""
    union = occurrence_union(
        claims=(
            ClaimTemporalWindow(
                claim_id=uuid4(),
                kind=ClaimValidKind.PROPOSITION_VALIDITY,
                valid_from=_at(2019),
                precision=ClaimValidPrecision.OPEN,
            ),
            ClaimTemporalWindow(
                claim_id=uuid4(),
                kind=ClaimValidKind.EFFECTIVE_PERIOD,
                valid_from=_at(2023),
                valid_until=_at(2024),
                precision=ClaimValidPrecision.YEAR,
            ),
        )
    )
    assert union.start == _at(2019)
    assert union.end is None
    assert union.precision is ClaimValidPrecision.OPEN


def test_historical_world_lookup_does_not_use_world_at_as_ingestion_cutoff() -> None:
    """The 2015 employer remains queryable after its archive is imported in 2026."""
    state = _state(start=2015, end=2019)
    assert (
        fact_membership(state=state, world_at=_at(2016), believed_at=_at(2026))
        is Membership.INSIDE
    )
    assert (
        fact_membership(state=state, world_at=_at(2016), believed_at=_at(2025))
        is Membership.OUTSIDE
    )


@pytest.mark.parametrize(
    ("at", "expected"),
    [(2026, Membership.OUTSIDE), (2030, Membership.INSIDE), (2031, Membership.OUTSIDE)],
)
def test_future_fact_activates_at_start_and_expires_at_exclusive_end(
    at: int, expected: Membership
) -> None:
    """Clock-only activation/expiry follows exactly the half-open interval."""
    assert (
        fact_membership(
            state=_state(start=2030, end=2031), world_at=_at(at), believed_at=_at(at)
        )
        is expected
    )


@pytest.mark.parametrize(
    ("window", "at", "expected"),
    [
        (VerdictWindow(start_basis=Basis.ERASED), 2026, Membership.UNCERTAIN),
        (
            VerdictWindow(
                end=_at(2025), start_basis=Basis.ERASED, end_basis=Basis.VERDICT
            ),
            2026,
            Membership.OUTSIDE,
        ),
        (
            VerdictWindow(
                start=_at(2030), start_basis=Basis.VERDICT, end_basis=Basis.ERASED
            ),
            2026,
            Membership.OUTSIDE,
        ),
        (
            VerdictWindow(
                start=_at(2030), start_basis=Basis.VERDICT, end_basis=Basis.ERASED
            ),
            2031,
            Membership.UNCERTAIN,
        ),
        (VerdictWindow(), 2026, Membership.INSIDE),
    ],
)
def test_erased_membership_preserves_known_exclusions_and_discloses_uncertainty(
    window: VerdictWindow, at: int, expected: Membership
) -> None:
    """Erasing an end cannot turn a fact into confidently current unbounded truth."""
    assert world_membership(window=window, at=_at(at)) is expected


@pytest.mark.parametrize("boundary", [2019, 2020, 2030, 2031, None])
def test_refused_cap_preserves_an_independent_finite_end(boundary: int | None) -> None:
    """The chronological guard never clears an end when a successor is unusable."""
    state = _state(start=2020, end=2030)
    result = cap_fact(
        state=state, boundary=_at(boundary) if boundary else None, operation_id=uuid4()
    )
    assert result.result is Result.REFUSED
    assert result.state is state


@pytest.mark.parametrize("start", [None, 2025])
def test_dated_resignation_caps_unknown_or_finite_tenure(start: int | None) -> None:
    """An ending occurrence may end an undated state or shorten a finite state."""
    result = cap_fact(
        state=_state(start=start, end=2030), boundary=_at(2027), operation_id=uuid4()
    )
    assert result.result is Result.APPLIED
    assert result.state.verdict.end == _at(2027)
    assert result.state.verdict.end_basis is Basis.VERDICT


def test_compensation_preserves_later_cap_and_belief_invalidation() -> None:
    """Undo 2020→2019 after a separate 2025 cap; preserve end and withdrawal."""
    original = _state()
    correction_id, cap_id, reversal_id = uuid4(), uuid4(), uuid4()
    correction = correct_window(
        state=original,
        start=_at(2019),
        end=None,
        operation_id=correction_id,
        neighbours=(),
    )
    capped = cap_fact(state=correction.state, boundary=_at(2025), operation_id=cap_id)
    withdrawn = withdraw_fact(
        state=capped.state,
        boundary=None,
        reconciliation_at=_at(2026, month=2),
        operation_id=uuid4(),
    )
    restored = compensate_window(
        state=withdrawn.state,
        target=ReversibleTemporalEffect(
            operation_id=correction_id,
            before=original.verdict,
            after=correction.state.verdict,
        ),
        operation_id=reversal_id,
        neighbours=(),
    )
    assert restored.result is Result.APPLIED
    assert restored.state.verdict.start == _at(2020)
    assert restored.state.verdict.start_basis is Basis.WORLD_TIME
    assert restored.state.from_operation_id == reversal_id
    assert restored.state.verdict.end == _at(2025)
    assert restored.state.until_operation_id == cap_id
    assert restored.state.invalidated_at == _at(2026, month=2)


def test_compensation_skips_component_owned_by_another_operation() -> None:
    """A later correction cannot be silently undone by reversing its predecessor."""
    original = _state()
    first_id = uuid4()
    first = correct_window(
        state=original,
        start=_at(2019),
        end=_at(2030),
        operation_id=first_id,
        neighbours=(),
    )
    later = cap_fact(state=first.state, boundary=_at(2025), operation_id=uuid4())
    result = compensate_window(
        state=later.state,
        target=ReversibleTemporalEffect(
            operation_id=first_id, before=original.verdict, after=first.state.verdict
        ),
        operation_id=uuid4(),
        neighbours=(),
    )
    assert result.skipped_components == ("until",)
    assert result.state.verdict.start == _at(2020)
    assert result.state.verdict.end == _at(2025)


def test_invalid_combined_compensation_changes_neither_endpoint() -> None:
    """Restoring start 2020 after an independent 2019 cap would create an empty slice."""
    original = _state()
    first_id = uuid4()
    first = correct_window(
        state=original, start=_at(2018), end=None, operation_id=first_id, neighbours=()
    )
    later = cap_fact(state=first.state, boundary=_at(2019), operation_id=uuid4())
    result = compensate_window(
        state=later.state,
        target=ReversibleTemporalEffect(
            operation_id=first_id, before=original.verdict, after=first.state.verdict
        ),
        operation_id=uuid4(),
        neighbours=(),
    )
    assert result.result is Result.REFUSED
    assert result.state is later.state


@pytest.mark.parametrize(
    ("start", "end", "neighbours"),
    [
        (2021, None, ()),
        (None, 2031, ()),
        (2010, 2010, ()),
        (2015, None, (_state(start=2010, end=2020),)),
    ],
)
def test_correction_refuses_later_start_reopening_empty_or_crossed_slice(
    start: int | None, end: int | None, neighbours: tuple[FactTemporalState, ...]
) -> None:
    """The ordinary correction authority is narrower than arbitrary date editing."""
    state = _state(end=2030)
    result = correct_window(
        state=state,
        start=_at(start) if start else None,
        end=_at(end) if end else None,
        operation_id=uuid4(),
        neighbours=neighbours,
    )
    assert result.result is Result.REFUSED
    assert result.state is state


@pytest.mark.parametrize("kind", [Kind.OCCURRENCE, Kind.UNKNOWN])
def test_nonstate_cannot_acquire_a_cap(kind: Kind) -> None:
    """Events and unknown shape remain uncapped through ordinary correction."""
    state = _state(kind=kind)
    assert (
        cap_fact(state=state, boundary=_at(2027), operation_id=uuid4()).result
        is Result.REFUSED
    )
    assert (
        correct_window(
            state=state, start=None, end=_at(2027), operation_id=uuid4(), neighbours=()
        ).result
        is Result.REFUSED
    )


@pytest.mark.parametrize("boundary", [None, 2019, 2020, 2031])
def test_source_withdrawal_closes_belief_when_world_cap_is_refused(
    boundary: int | None,
) -> None:
    """A refused source timestamp cannot leave a zombie fact or clear its finite cap."""
    state = _state(end=2030)
    result = withdraw_fact(
        state=state,
        boundary=_at(boundary) if boundary else None,
        reconciliation_at=_at(2026, month=2),
        operation_id=uuid4(),
    )
    assert result.result is Result.APPLIED
    assert result.state.verdict == state.verdict
    assert result.state.invalidated_at == _at(2026, month=2)
    assert (
        fact_membership(state=result.state, world_at=_at(2027), believed_at=_at(2027))
        is Membership.OUTSIDE
    )


def test_erased_date_is_removed_and_empty_state_is_rejected() -> None:
    """Typed state enforces the same irreducible tuple constraints as PostgreSQL."""
    with pytest.raises(ValidationError, match="erased start must be NULL"):
        VerdictWindow(start=_at(2020), start_basis=Basis.ERASED)
    with pytest.raises(ValidationError, match="non-empty"):
        _state(start=2020, end=2020)
