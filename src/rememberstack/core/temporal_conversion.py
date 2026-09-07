"""D107 §9 deterministic conversion of recorded legacy fact authority in place.

This module never chooses a creator, successor, or reconciliation event. The
catalog must establish those identities from retained adjudications under the
conversion fence. Evidence dates alone cannot establish verdict authority.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from rememberstack.core.fact_temporal import cap_allowed
from rememberstack.core.fact_temporal import fact_kind
from rememberstack.core.fact_temporal import occurrence_union
from rememberstack.core.fact_temporal import seed_fact
from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalBasis
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import VerdictWindow


@dataclass(frozen=True)
class TemporalConversionDecision:
    """Exact converted state and inspectable diagnostics for its migration verdict."""

    state: FactTemporalState
    diagnostics: tuple[str, ...]


def convert_legacy_fact(
    *,
    legacy: FactTemporalState,
    evidence: Iterable[ClaimTemporalWindow],
    recorded_seed: ClaimTemporalWindow | None,
    legacy_cap_cause: Literal["supersession", "source_removal", "unknown"],
    successor_world_start: datetime | None,
    recorded_withdrawal_at: datetime | None,
    operation_id: UUID,
) -> TemporalConversionDecision:
    """Convert one original tuple without inventing historical semantic decisions.

    Consume all attached non-forgotten evidence, including withdrawn testimony.
    The caller supplies only a recorded creator and a proven successor's
    canonical world start; neither minimum evidence dates nor source clocks
    qualify. Legacy cap cause is independent of later belief withdrawal.
    The returned state preserves the fact's belief ingestion instant
    and existing invalidation. Replaying the campaign uses its original shadow,
    rather than converting an already-converted state a second time.
    """
    if (
        legacy.revision != 0
        or legacy.seed_claim_id is not None
        or legacy.kind is not FactTemporalKind.UNKNOWN
        or legacy.from_operation_id is not None
        or legacy.until_operation_id is not None
    ):
        raise ValueError("legacy conversion requires the original unconverted tuple")
    kinds: set[FactTemporalKind] = set()
    found_seed = False

    def observed_evidence() -> Iterable[ClaimTemporalWindow]:
        """Accumulate constant-size shape evidence while streaming the occurrence union."""
        nonlocal found_seed
        for claim in evidence:
            kinds.add(fact_kind(claim_kind=claim.kind, shape=FactTemporalKind.UNKNOWN))
            if recorded_seed is not None and claim.claim_id == recorded_seed.claim_id:
                if claim != recorded_seed:
                    raise ValueError(
                        "recorded creator differs from its attached testimony"
                    )
                found_seed = True
            yield claim

    occurrence = occurrence_union(claims=observed_evidence())
    if recorded_seed is not None and not found_seed:
        raise ValueError("recorded creator is not retained attached evidence")
    agreed_kind = next(iter(kinds)) if len(kinds) == 1 else FactTemporalKind.UNKNOWN
    diagnostics: list[str] = []
    if recorded_seed is not None:
        seeded = seed_fact(
            seed=recorded_seed, shape=agreed_kind, ingested_at=legacy.ingested_at
        )
        kind = seeded.kind
        verdict = seeded.verdict
        seed_claim_id = recorded_seed.claim_id
    else:
        kind = agreed_kind
        verdict = VerdictWindow(
            start=legacy.verdict.start,
            start_basis=FactTemporalBasis.LEGACY
            if legacy.verdict.start is not None
            else FactTemporalBasis.UNKNOWN,
        )
        seed_claim_id = None
        diagnostics.append("legacy_creator_unrecoverable")
    if kind is FactTemporalKind.UNKNOWN:
        diagnostics.append("legacy_shape_unknown")
    if legacy.verdict.end is not None:
        # Legacy finite ends came from supersession/reconciliation. They are
        # not trusted just because the stored range happens to be well formed.
        seed_window = verdict
        verdict = VerdictWindow(start=verdict.start, start_basis=verdict.start_basis)
        if kind is FactTemporalKind.OCCURRENCE:
            diagnostics.append("legacy_occurrence_cap_removed")
        elif legacy_cap_cause == "source_removal":
            diagnostics.append("legacy_withdrawal_boundary_removed")
        elif legacy_cap_cause == "supersession" and successor_world_start is not None:
            uncapped = FactTemporalState(
                kind=kind, verdict=seed_window, ingested_at=legacy.ingested_at
            )
            if cap_allowed(state=uncapped, boundary=successor_world_start):
                verdict = VerdictWindow(
                    start=verdict.start,
                    start_basis=verdict.start_basis,
                    end=successor_world_start,
                    end_basis=FactTemporalBasis.VERDICT,
                )
            else:
                diagnostics.append("legacy_unknown_boundary")
        else:
            diagnostics.append("legacy_unknown_boundary")
    invalidated_at = legacy.invalidated_at or recorded_withdrawal_at
    if invalidated_at is not None and invalidated_at < legacy.ingested_at:
        raise ValueError("recorded belief withdrawal predates fact ingestion")
    if recorded_withdrawal_at is not None and legacy.invalidated_at is None:
        diagnostics.append("legacy_withdrawal_belief_restored")
    return TemporalConversionDecision(
        state=FactTemporalState(
            kind=kind,
            verdict=verdict,
            occurrence=occurrence,
            seed_claim_id=seed_claim_id,
            ingested_at=legacy.ingested_at,
            invalidated_at=invalidated_at,
            revision=legacy.revision + 1,
            contradiction_group=legacy.contradiction_group,
            from_operation_id=operation_id
            if (legacy.verdict.start, legacy.verdict.start_basis)
            != (verdict.start, verdict.start_basis)
            else legacy.from_operation_id,
            until_operation_id=operation_id
            if (legacy.verdict.end, legacy.verdict.end_basis)
            != (verdict.end, verdict.end_basis)
            else legacy.until_operation_id,
        ),
        diagnostics=tuple(diagnostics),
    )
