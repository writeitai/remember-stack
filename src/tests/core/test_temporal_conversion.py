"""Legacy conversion preserves provenance and admits only recorded date authority."""

from datetime import datetime
from datetime import timezone
from uuid import uuid4

import pytest

from rememberstack.core.temporal_conversion import convert_legacy_fact
from rememberstack.model.claims import ClaimValidKind
from rememberstack.model.claims import ClaimValidPrecision
from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalBasis
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import VerdictWindow


def _at(*, year: int) -> datetime:
    """Use UTC year boundaries that cannot depend on wall-clock execution time."""
    return datetime(year, 1, 1, tzinfo=timezone.utc)


def _claim(
    *, year: int = 2020, kind: ClaimValidKind | None = ClaimValidKind.EFFECTIVE_PERIOD
) -> ClaimTemporalWindow:
    """One retained source window, independent of its ingestion or publication time."""
    return ClaimTemporalWindow(
        claim_id=uuid4(),
        kind=kind,
        valid_from=_at(year=year),
        precision=ClaimValidPrecision.OPEN,
    )


def _legacy(
    *, end: int | None = None, invalidated: int | None = None
) -> FactTemporalState:
    """The preconversion row has default temporal metadata but retains both old clocks."""
    return FactTemporalState(
        kind=FactTemporalKind.UNKNOWN,
        verdict=VerdictWindow(start=_at(year=2021), end=_at(year=end) if end else None),
        ingested_at=_at(year=2022),
        invalidated_at=_at(year=invalidated) if invalidated else None,
    )


def test_unrecorded_creator_is_not_replaced_by_earliest_evidence() -> None:
    """An earlier source expands occurrence evidence without rewriting a legacy verdict start."""
    claims = (_claim(year=2015), _claim(year=2020))
    legacy = _legacy()
    result = convert_legacy_fact(
        legacy=legacy,
        evidence=iter(claims),
        recorded_seed=None,
        successor_world_start=None,
        recorded_withdrawal_at=None,
        operation_id=uuid4(),
    )
    assert result.state.seed_claim_id is None
    assert result.state.verdict.start == _at(year=2021)
    assert result.state.verdict.start_basis is FactTemporalBasis.LEGACY
    assert result.state.occurrence.start == _at(year=2015)
    assert result.state.kind is FactTemporalKind.STATE
    assert result.state.ingested_at == legacy.ingested_at


def test_recorded_seed_alone_supplies_initial_verdict_and_other_evidence_supplies_union() -> (
    None
):
    """An exact add adjudication can recover a seed; a broad evidence union is a separate clock."""
    seed, other = _claim(year=2020), _claim(year=2015)
    result = convert_legacy_fact(
        legacy=_legacy(),
        evidence=(other, seed),
        recorded_seed=seed,
        successor_world_start=None,
        recorded_withdrawal_at=None,
        operation_id=uuid4(),
    )
    assert result.state.seed_claim_id == seed.claim_id
    assert result.state.verdict.start == _at(year=2020)
    assert result.state.verdict.start_basis is FactTemporalBasis.WORLD_TIME
    assert result.state.occurrence.start == _at(year=2015)


@pytest.mark.parametrize(
    "kinds", [(), (None,), (ClaimValidKind.EVENT_TIME, ClaimValidKind.EFFECTIVE_PERIOD)]
)
def test_missing_or_disagreeing_shape_remains_unknown(
    kinds: tuple[ClaimValidKind | None, ...],
) -> None:
    """Conversion never invents a state/occurrence majority over missing or mixed evidence."""
    result = convert_legacy_fact(
        legacy=_legacy(),
        evidence=(_claim(kind=kind) for kind in kinds),
        recorded_seed=None,
        successor_world_start=None,
        recorded_withdrawal_at=None,
        operation_id=uuid4(),
    )
    assert result.state.kind is FactTemporalKind.UNKNOWN
    assert "legacy_shape_unknown" in result.diagnostics


@pytest.mark.parametrize("successor_year", [None, 2019, 2020])
def test_missing_or_chronologically_invalid_successor_erases_legacy_cap(
    successor_year: int | None,
) -> None:
    """A legacy source-clock cap cannot survive as world-time by default."""
    seed = _claim()
    result = convert_legacy_fact(
        legacy=_legacy(end=2025),
        evidence=(seed,),
        recorded_seed=seed,
        successor_world_start=_at(year=successor_year) if successor_year else None,
        recorded_withdrawal_at=None,
        operation_id=uuid4(),
    )
    assert result.state.verdict.end is None
    assert result.state.verdict.end_basis is FactTemporalBasis.UNKNOWN
    assert "legacy_unknown_boundary" in result.diagnostics


def test_recorded_successor_replaces_old_source_clock_cap() -> None:
    """The world start of the recorded successor owns the converted state's end."""
    seed = _claim()
    operation_id = uuid4()
    result = convert_legacy_fact(
        legacy=_legacy(end=2025),
        evidence=(seed,),
        recorded_seed=seed,
        successor_world_start=_at(year=2024),
        recorded_withdrawal_at=None,
        operation_id=operation_id,
    )
    assert result.state.verdict.end == _at(year=2024)
    assert result.state.verdict.end_basis is FactTemporalBasis.VERDICT
    assert result.state.until_operation_id == operation_id


@pytest.mark.parametrize(
    "kind", [ClaimValidKind.EVENT_TIME, ClaimValidKind.MEASUREMENT_PERIOD]
)
def test_occurrence_loses_legacy_cap_and_preserves_belief_withdrawal(
    kind: ClaimValidKind,
) -> None:
    """Historical event testimony must not disappear when its source was withdrawn."""
    seed = _claim(kind=kind)
    legacy = _legacy(end=2025, invalidated=2026)
    result = convert_legacy_fact(
        legacy=legacy,
        evidence=(seed,),
        recorded_seed=seed,
        successor_world_start=_at(year=2024),
        recorded_withdrawal_at=_at(year=2025),
        operation_id=uuid4(),
    )
    assert result.state.kind is FactTemporalKind.OCCURRENCE
    assert result.state.verdict.end is None
    assert result.state.invalidated_at == legacy.invalidated_at
    assert result.state.occurrence.start == _at(year=2020)
    assert "legacy_occurrence_cap_removed" in result.diagnostics


def test_d55_source_cap_becomes_recorded_belief_closure() -> None:
    """The persisted reconciliation instant closes belief; the old source-clock cap is removed."""
    claim = _claim()
    result = convert_legacy_fact(
        legacy=_legacy(end=2025),
        evidence=(claim,),
        recorded_seed=None,
        successor_world_start=None,
        recorded_withdrawal_at=_at(year=2026),
        operation_id=uuid4(),
    )
    assert result.state.verdict.end is None
    assert result.state.invalidated_at == _at(year=2026)
    assert result.state.ingested_at == _at(year=2022)


def test_scrubbed_seed_cannot_be_reintroduced_from_old_snapshot() -> None:
    """A creator that is absent from retained attached evidence is not conversion authority."""
    with pytest.raises(ValueError, match="not retained attached evidence"):
        convert_legacy_fact(
            legacy=_legacy(),
            evidence=(_claim(),),
            recorded_seed=_claim(),
            successor_world_start=None,
            recorded_withdrawal_at=None,
            operation_id=uuid4(),
        )


def test_converted_state_is_not_silently_converted_again() -> None:
    """Resume must consume the campaign's original shadow, not redefine it from converted data."""
    claim = _claim()
    first = convert_legacy_fact(
        legacy=_legacy(),
        evidence=(claim,),
        recorded_seed=None,
        successor_world_start=None,
        recorded_withdrawal_at=None,
        operation_id=uuid4(),
    )
    with pytest.raises(ValueError, match="original unconverted tuple"):
        convert_legacy_fact(
            legacy=first.state,
            evidence=(claim,),
            recorded_seed=None,
            successor_world_start=None,
            recorded_withdrawal_at=None,
            operation_id=uuid4(),
        )


@pytest.mark.parametrize("legacy_end", [None, 2025])
def test_conversion_never_extends_a_bounded_seed_to_later_successor(
    legacy_end: int | None,
) -> None:
    """A successor cap must pass the guard against the recovered seed's actual finite window."""
    seed = ClaimTemporalWindow(
        claim_id=uuid4(),
        kind=ClaimValidKind.EFFECTIVE_PERIOD,
        valid_from=_at(year=2020),
        valid_until=_at(year=2020),
        precision=ClaimValidPrecision.YEAR,
    )
    result = convert_legacy_fact(
        legacy=_legacy(end=legacy_end),
        evidence=(seed,),
        recorded_seed=seed,
        successor_world_start=_at(year=2030),
        recorded_withdrawal_at=None,
        operation_id=uuid4(),
    )
    if legacy_end is None:
        assert result.state.verdict.end == _at(year=2021)
        assert result.state.verdict.end_basis is FactTemporalBasis.WORLD_TIME
    else:
        assert result.state.verdict.end is None
        assert "legacy_unknown_boundary" in result.diagnostics
