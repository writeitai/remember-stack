"""Stable normalized observation assertion identities; canonical redirects are separate."""

from datetime import datetime
from datetime import timezone
import json
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from rememberstack.core.fact_temporal import fact_kind
from rememberstack.core.temporal import canonical_bounds
from rememberstack.core.temporal import CanonicalBounds
from rememberstack.model.fact_temporal import FactTemporalBasis
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.observation_application import ObservationApplicationCandidate
from rememberstack.model.observation_application import ObservationCurrentSupport
from rememberstack.model.observation_application import ObservationResplitInputs
from rememberstack.model.observation_application import StagedObservation


def observation_assertion_id(
    *,
    deployment_id: UUID,
    receipt_id: UUID,
    normalized_subject_entity_id: UUID,
    statement: str,
) -> UUID:
    """Apply D113's exact UTF-8 JSON identity without changing text or normalized subject."""
    encoded = json.dumps(
        [
            "rememberstack:observation-assertion:1",
            str(deployment_id),
            str(receipt_id),
            str(normalized_subject_entity_id),
            statement,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return uuid5(NAMESPACE_URL, encoded)


def observation_kind(*, assertion: StagedObservation) -> FactTemporalKind:
    """Explicit claim kind outranks the retained normalizer shape judgment."""
    return fact_kind(
        claim_kind=assertion.testimony.window.kind, shape=assertion.shape_kind
    )


def observation_bounds(*, assertion: StagedObservation) -> CanonicalBounds:
    """Canonicalize raw claim endpoints once, without publication-time fallback."""
    window = assertion.testimony.window
    return canonical_bounds(
        valid_from=window.valid_from,
        valid_until=window.valid_until,
        precision=window.precision,
    )


def observation_permits_evidence(
    *, assertion: StagedObservation, candidate: ObservationApplicationCandidate
) -> bool:
    """Semantic identity cannot override kind, datedness, disjoint periods or erased authority."""
    state = candidate.state
    if (
        state.invalidated_at is not None
        or observation_kind(assertion=assertion) is not state.kind
    ):
        return False
    if FactTemporalBasis.ERASED in (state.verdict.start_basis, state.verdict.end_basis):
        return False
    incoming = observation_bounds(assertion=assertion)
    window = state.verdict if state.kind is FactTemporalKind.STATE else state.occurrence
    existing = CanonicalBounds(start=window.start, end=window.end)
    return (
        incoming.overlaps(existing)
        if incoming.is_known and existing.is_known
        else not incoming.is_known and not existing.is_known
    )


def deterministic_observation_target(
    *,
    assertion: StagedObservation,
    candidates: tuple[ObservationApplicationCandidate, ...],
) -> UUID | None:
    """Only one identical compatible state proves identity without semantic selection."""
    matches = tuple(
        candidate.observation_id
        for candidate in candidates
        if candidate.state.kind is FactTemporalKind.STATE
        and candidate.statement == assertion.statement
        and observation_permits_evidence(assertion=assertion, candidate=candidate)
    )
    return matches[0] if len(matches) == 1 else None


def nominate_observation(
    *, assertion: StagedObservation, candidate: ObservationApplicationCandidate
) -> bool:
    """Keep same-kind date disputes and dated ending events, including finite-ended states."""
    if candidate.state.invalidated_at is not None:
        return False
    incoming_kind = observation_kind(assertion=assertion)
    if incoming_kind is candidate.state.kind:
        return True
    start = observation_bounds(assertion=assertion).start
    return (
        incoming_kind is FactTemporalKind.OCCURRENCE
        and candidate.state.kind is FactTemporalKind.STATE
        and start is not None
        and (candidate.state.verdict.end is None or start < candidate.state.verdict.end)
    )


def observation_resplit_inputs(
    *,
    candidate: ObservationApplicationCandidate,
    boundary: datetime,
    current_support: tuple[ObservationCurrentSupport, ...],
) -> ObservationResplitInputs:
    """Select the complete displaced state assertions by world time, preserving source identity.

    The caller evaluates the chronological cap guard separately. A legacy
    blocker refuses that cap; its text must never be guessed from the fact's
    display statement. Multiple generations remain independently owned support.
    """
    if boundary.tzinfo is None or boundary.utcoffset() is None:
        raise ValueError("a re-split boundary requires an explicit timezone")
    if candidate.state.kind is not FactTemporalKind.STATE:
        raise ValueError("only a state can require a cap-driven re-split")
    support = tuple(
        item
        for item in current_support
        if item.current_observation_id == candidate.observation_id
    )
    keys = {(item.assertion.assertion_id, item.adjudicator_version) for item in support}
    if len(keys) != len(support):
        raise ValueError("duplicate observation current-support application")
    windows = {item.claim_id: item for item in candidate.evidence_windows}
    if len(windows) != len(candidate.evidence_windows):
        raise ValueError(
            "observation evidence aggregation contains duplicate claim windows"
        )
    linked_claims = {item.assertion.testimony.claim_id for item in support}
    legacy_claims = set(candidate.legacy_claim_ids)
    if set(windows) != linked_claims | legacy_claims:
        raise ValueError(
            "observation evidence has incomplete current or legacy attribution"
        )
    for item in support:
        if (
            item.assertion.testimony.window
            != windows[item.assertion.testimony.claim_id]
        ):
            raise ValueError(
                "observation support and attached testimony windows disagree"
            )
    displaced = tuple(
        item
        for item in support
        if observation_kind(assertion=item.assertion) is FactTemporalKind.STATE
        and (start := observation_bounds(assertion=item.assertion).start) is not None
        and start >= boundary
    )
    blocking = []
    for claim_id in legacy_claims:
        window = windows[claim_id]
        start = canonical_bounds(
            valid_from=window.valid_from,
            valid_until=window.valid_until,
            precision=window.precision,
        ).start
        if start is not None and start >= boundary:
            blocking.append(claim_id)
    return ObservationResplitInputs(
        applications=tuple(sorted(displaced, key=_resplit_source_order)),
        blocking_legacy_claim_ids=tuple(sorted(blocking)),
    )


def observation_support_remains(
    *,
    candidate: ObservationApplicationCandidate,
    moving: ObservationCurrentSupport,
    current_support: tuple[ObservationCurrentSupport, ...],
) -> bool:
    """Moving one assertion cannot delete the same claim's legacy or other application support."""
    if moving.current_observation_id != candidate.observation_id:
        raise ValueError("support move does not own the proposed previous observation")
    claim_id = moving.assertion.testimony.claim_id
    key = moving.assertion.assertion_id, moving.adjudicator_version
    return claim_id in candidate.legacy_claim_ids or any(
        item.current_observation_id == candidate.observation_id
        and item.assertion.testimony.claim_id == claim_id
        and (item.assertion.assertion_id, item.adjudicator_version) != key
        for item in current_support
    )


def _resplit_source_order(
    item: ObservationCurrentSupport,
) -> tuple[bool, datetime, UUID, bytes, UUID, str]:
    """Order full reentry by the retained source tuple, with a stable semantic-generation tie."""
    assertion = item.assertion
    return (
        assertion.testimony.asserted_at is None,
        assertion.testimony.asserted_at or datetime.max.replace(tzinfo=timezone.utc),
        assertion.testimony.claim_id,
        assertion.statement.encode("utf-8"),
        assertion.assertion_id,
        item.adjudicator_version,
    )
