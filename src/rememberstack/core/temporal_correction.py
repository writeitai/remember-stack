"""Canonical candidate construction and conservative admission of ordinary corrections."""

from collections import defaultdict
from datetime import datetime
import json
from typing import Literal
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from rememberstack.core.fact_temporal import correct_window
from rememberstack.core.fact_temporal import TemporalMutation
from rememberstack.core.temporal import canonical_bounds
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.temporal_correction import CorrectionEndpointCandidate
from rememberstack.model.temporal_correction import CorrectionFactContext
from rememberstack.model.temporal_correction import CorrectionTestimony
from rememberstack.model.temporal_correction import TemporalCorrectionInputs
from rememberstack.model.temporal_correction import TemporalCorrectionVerdict


def correction_candidates(
    *, target: CorrectionFactContext, testimony: tuple[CorrectionTestimony, ...]
) -> tuple[CorrectionEndpointCandidate, ...]:
    """Group canonical world endpoints from current linked support, without counting duplicate lineages."""
    groups: dict[
        tuple[Literal["from", "until"], datetime], list[CorrectionTestimony]
    ] = defaultdict(list)
    for witness in testimony:
        if not witness.is_current or witness.stance != "supports":
            continue
        if witness.claim_id != witness.window.claim_id:
            raise ValueError(
                "correction witness and temporal source must name the same claim"
            )
        bounds = canonical_bounds(
            valid_from=witness.window.valid_from,
            valid_until=witness.window.valid_until,
            precision=witness.window.precision,
        )
        if bounds.start is not None:
            groups[("from", bounds.start)].append(witness)
        if target.state.kind is FactTemporalKind.STATE and bounds.end is not None:
            groups[("until", bounds.end)].append(witness)
    candidates: list[CorrectionEndpointCandidate] = []
    for (endpoint, value), sources in sorted(groups.items()):
        claims = tuple(sorted({source.claim_id for source in sources}))
        documents = tuple(sorted({source.doc_id for source in sources}))
        identity = json.dumps(
            [
                "rememberstack:temporal-endpoint:1",
                target.fact.plane.value,
                str(target.fact.fact_id),
                endpoint,
                value.isoformat(),
                [str(claim) for claim in claims],
            ],
            separators=(",", ":"),
            ensure_ascii=False,
        )
        candidates.append(
            CorrectionEndpointCandidate(
                candidate_id=uuid5(NAMESPACE_URL, identity),
                endpoint=endpoint,
                value=value,
                claim_ids=claims,
                document_ids=documents,
            )
        )
    return tuple(candidates)


def admit_correction(
    *,
    inputs: TemporalCorrectionInputs,
    verdict: TemporalCorrectionVerdict,
    operation_id: UUID,
    escalation_floor: float,
    supersede_margin: float,
) -> TemporalMutation:
    """Reject ungrounded or incompletely assessed changes before the journal revalidates them."""
    state = inputs.target.state

    def unchanged(*, result: TemporalResult, reason: str) -> TemporalMutation:
        """Return a completed non-application without replacing state or endpoint owners."""
        return TemporalMutation(state=state, result=result, reason=reason)

    if not 0 <= escalation_floor <= 1 or not 0 <= supersede_margin <= 1:
        raise ValueError(
            "correction confidence thresholds must be between zero and one"
        )
    if verdict.outcome == "uncertain":
        return unchanged(result=TemporalResult.UNCERTAIN, reason="review_uncertain")
    selected_ids = tuple(
        identity
        for identity in (verdict.start_candidate_id, verdict.end_candidate_id)
        if identity is not None
    )
    if verdict.outcome == "noop":
        return unchanged(
            result=TemporalResult.REFUSED if selected_ids else TemporalResult.NOOP,
            reason="noop_cannot_select_endpoints"
            if selected_ids
            else "review_no_change",
        )
    if not inputs.required_context_complete:
        return unchanged(
            result=TemporalResult.UNCERTAIN,
            reason="required_correction_context_omitted",
        )
    if verdict.confidence < max(escalation_floor, supersede_margin):
        return unchanged(
            result=TemporalResult.UNCERTAIN,
            reason="correction_confidence_below_application_margin",
        )
    if not selected_ids:
        return unchanged(
            result=TemporalResult.REFUSED, reason="correction_has_no_selected_endpoint"
        )
    actual = correction_candidates(target=inputs.target, testimony=inputs.testimony)
    if actual != inputs.candidates:
        return unchanged(
            result=TemporalResult.REFUSED,
            reason="candidate_authority_differs_from_linked_support",
        )
    candidates = {candidate.candidate_id: candidate for candidate in actual}
    selected: list[CorrectionEndpointCandidate] = []
    for identity, endpoint in (
        (verdict.start_candidate_id, "from"),
        (verdict.end_candidate_id, "until"),
    ):
        if identity is None:
            continue
        candidate = candidates.get(identity)
        if candidate is None or candidate.endpoint != endpoint:
            return unchanged(
                result=TemporalResult.REFUSED,
                reason="unknown_or_wrong_endpoint_candidate",
            )
        if not set(candidate.claim_ids).intersection(verdict.supporting_claim_ids):
            return unchanged(
                result=TemporalResult.REFUSED,
                reason="selected_endpoint_has_no_named_support",
            )
        selected.append(candidate)
    support = {
        witness.claim_id
        for witness in inputs.testimony
        if witness.is_current and witness.stance == "supports"
    }
    contrary = {
        witness.claim_id
        for witness in inputs.testimony
        if witness.stance == "contradicts"
    }
    if (
        not set(verdict.supporting_claim_ids) <= support
        or not set(verdict.contrary_claim_ids) <= contrary
    ):
        return unchanged(
            result=TemporalResult.REFUSED,
            reason="review_invents_or_misattributes_claim_support",
        )
    return correct_window(
        state=state,
        start=next(
            (candidate.value for candidate in selected if candidate.endpoint == "from"),
            None,
        ),
        end=next(
            (
                candidate.value
                for candidate in selected
                if candidate.endpoint == "until"
            ),
            None,
        ),
        operation_id=operation_id,
        neighbours=tuple(neighbour.state for neighbour in inputs.neighbours),
    )
