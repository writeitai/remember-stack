"""Temporal admission rules for relation identity; no source clock supplies a boundary."""

from hashlib import sha256
import json
from uuid import UUID

from rememberstack.core.fact_temporal import fact_kind
from rememberstack.core.fact_temporal import occurrence_union
from rememberstack.core.temporal import CanonicalBounds
from rememberstack.model.fact_temporal import FactTemporalBasis
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import OccurrenceWindow
from rememberstack.model.relation_application import RelationApplicationCandidate
from rememberstack.model.relation_application import StagedRelation


def relation_kind(*, assertion: StagedRelation) -> FactTemporalKind:
    """Prefer immutable D41 kind over the normalizer's retained semantic judgment."""
    return fact_kind(
        claim_kind=assertion.testimony.window.kind, shape=assertion.shape_kind
    )


def assertion_bounds(*, assertion: StagedRelation) -> CanonicalBounds:
    """Resolve the assertion's raw D41 window once into half-open world time."""
    window = occurrence_union(claims=(assertion.testimony.window,))
    return CanonicalBounds(start=window.start, end=window.end)


def candidate_bounds(*, candidate: RelationApplicationCandidate) -> CanonicalBounds:
    """State identity uses verdict bounds; occurrence identity compares evidence metadata."""
    state = candidate.state
    window = state.verdict if state.kind is FactTemporalKind.STATE else state.occurrence
    return CanonicalBounds(start=window.start, end=window.end)


def permits_evidence(
    *, assertion: StagedRelation, candidate: RelationApplicationCandidate
) -> bool:
    """A semantic identity verdict cannot collapse disjoint, mixed-clock, or erased inputs."""
    if (
        assertion.object_entity_id != candidate.object_entity_id
        or relation_kind(assertion=assertion) is not candidate.state.kind
    ):
        return False
    if FactTemporalBasis.ERASED in (
        candidate.state.verdict.start_basis,
        candidate.state.verdict.end_basis,
    ):
        return False
    incoming, existing = (
        assertion_bounds(assertion=assertion),
        candidate_bounds(candidate=candidate),
    )
    return (
        incoming.overlaps(existing)
        if incoming.is_known and existing.is_known
        else not incoming.is_known and not existing.is_known
    )


def nominated_candidate(
    *,
    assertion: StagedRelation,
    candidate: RelationApplicationCandidate,
    is_change_prone: bool,
) -> bool:
    """Retain disjoint occurrences for date-dispute adjudication and eligible ending events."""
    incoming_kind = relation_kind(assertion=assertion)
    if candidate.state.invalidated_at is not None:
        return False
    if candidate.state.kind is incoming_kind:
        return candidate.object_entity_id == assertion.object_entity_id or (
            incoming_kind is FactTemporalKind.STATE and is_change_prone
        )
    if (
        incoming_kind is not FactTemporalKind.OCCURRENCE
        or candidate.state.kind is not FactTemporalKind.STATE
    ):
        return False
    if candidate.object_entity_id != assertion.object_entity_id:
        return False
    start = assertion_bounds(assertion=assertion).start
    return start is not None and (
        candidate.state.verdict.end is None or candidate.state.verdict.end > start
    )


def union_occurrence(
    *, left: OccurrenceWindow, right: OccurrenceWindow
) -> OccurrenceWindow:
    """Combine already-canonical evidence windows without aligning or extending them again."""
    if left.start is None:
        return right
    if right.start is None:
        return left
    order = ("instant", "day", "month", "quarter", "year", "open")
    precision = max(
        (left.precision, right.precision), key=lambda item: order.index(str(item))
    )
    return OccurrenceWindow(
        start=min(left.start, right.start),
        end=None if left.end is None or right.end is None else max(left.end, right.end),
        precision=precision,
    )


def deterministic_state_targets(
    *, assertion: StagedRelation, candidates: tuple[RelationApplicationCandidate, ...]
) -> tuple[UUID, ...]:
    """Return complete dated state support or the unique undated state shortcut."""
    matches = tuple(
        sorted(
            candidate.relation_id
            for candidate in candidates
            if candidate.state.kind is FactTemporalKind.STATE
            and candidate.state.invalidated_at is None
            and permits_evidence(assertion=assertion, candidate=candidate)
        )
    )
    if assertion_bounds(assertion=assertion).is_known or len(matches) == 1:
        return matches
    return ()


def relation_target_digest(*, targets: tuple[UUID, ...]) -> str:
    """Certify the distinct target set using D112's canonical UUID JSON encoding."""
    encoded = json.dumps(
        [str(value) for value in sorted(set(targets))], separators=(",", ":")
    )
    return sha256(encoded.encode("utf-8")).hexdigest()
