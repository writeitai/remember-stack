"""D110 correction choices never acquire dates or authority from the model/source clock."""

from datetime import datetime
from datetime import timezone
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError
import pytest

from rememberstack.core.temporal_correction import admit_correction
from rememberstack.core.temporal_correction import correction_candidates
from rememberstack.model.claims import ClaimValidKind
from rememberstack.model.claims import ClaimValidPrecision
from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalBasis
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.fact_temporal import VerdictWindow
from rememberstack.model.temporal_correction import CorrectionFactContext
from rememberstack.model.temporal_correction import CorrectionTestimony
from rememberstack.model.temporal_correction import TemporalCorrectionInputs
from rememberstack.model.temporal_correction import TemporalCorrectionVerdict
from rememberstack.model.temporal_write import FactPlane
from rememberstack.model.temporal_write import TemporalFactRef


def _at(year: int) -> datetime:
    """Provide a fixed UTC instant independent of machine time."""
    return datetime(year, 1, 1, tzinfo=timezone.utc)


def _witness(
    *, current: bool = True, stance: Literal["supports", "contradicts"] = "supports"
) -> CorrectionTestimony:
    """A source written in 2040 describing a full world-time year in 2018."""
    claim_id = uuid4()
    return CorrectionTestimony(
        claim_id=claim_id,
        doc_id=uuid4(),
        text="The state already held during 2018.",
        asserted_at=_at(2040),
        stance=stance,
        is_current=current,
        window=ClaimTemporalWindow(
            claim_id=claim_id,
            kind=ClaimValidKind.EFFECTIVE_PERIOD,
            valid_from=_at(2018),
            valid_until=datetime(2018, 12, 31, tzinfo=timezone.utc),
            precision=ClaimValidPrecision.YEAR,
        ),
    )


def _inputs(
    *, kind: FactTemporalKind = FactTemporalKind.STATE
) -> TemporalCorrectionInputs:
    """A 2019-start fact ingested in 2030 with one linked current earlier witness."""
    context = CorrectionFactContext(
        fact=TemporalFactRef(plane=FactPlane.RELATION, fact_id=uuid4()),
        statement="A is CEO",
        state=FactTemporalState(
            kind=kind,
            verdict=VerdictWindow(
                start=_at(2019), start_basis=FactTemporalBasis.WORLD_TIME
            ),
            ingested_at=_at(2030),
            revision=5,
            from_operation_id=uuid4(),
        ),
    )
    testimony = (_witness(),)
    return TemporalCorrectionInputs(
        target=context,
        testimony=testimony,
        candidates=correction_candidates(target=context, testimony=testimony),
        neighbours=(),
        required_context_complete=True,
    )


def _verdict(
    *, inputs: TemporalCorrectionInputs, confidence: float = 1
) -> TemporalCorrectionVerdict:
    """Select the earlier endpoint and explicitly identify its linked supporting claim."""
    start = next(
        candidate for candidate in inputs.candidates if candidate.endpoint == "from"
    )
    return TemporalCorrectionVerdict(
        outcome="correct",
        start_candidate_id=start.candidate_id,
        supporting_claim_ids=start.claim_ids,
        confidence=confidence,
        rationale="The linked source dates the same state earlier.",
    )


def test_candidates_use_half_open_world_bounds_and_distinct_lineages() -> None:
    """Re-extraction adds a witness, not another independent source or source-clock boundary."""
    inputs = _inputs()
    original = inputs.testimony[0]
    second_id = uuid4()
    second = original.model_copy(
        update={
            "claim_id": second_id,
            "window": original.window.model_copy(update={"claim_id": second_id}),
        }
    )
    candidates = correction_candidates(
        target=inputs.target, testimony=(original, second)
    )
    assert [(candidate.endpoint, candidate.value) for candidate in candidates] == [
        ("from", _at(2018)),
        ("until", _at(2019)),
    ]
    assert all(
        len(candidate.claim_ids) == 2 and len(candidate.document_ids) == 1
        for candidate in candidates
    )
    assert (
        correction_candidates(target=inputs.target, testimony=(second, original))
        == candidates
    )


def test_withdrawn_and_contrary_testimony_cannot_supply_candidates() -> None:
    """Historical evidence remains context but cannot alone authorize a current date edit."""
    inputs = _inputs()
    assert (
        correction_candidates(
            target=inputs.target,
            testimony=(_witness(current=False), _witness(stance="contradicts")),
        )
        == ()
    )


def test_undated_source_does_not_offer_its_publication_time() -> None:
    """A source timestamp is never a fallback candidate for absent world-time."""
    inputs = _inputs()
    witness = inputs.testimony[0]
    undated = witness.model_copy(
        update={"window": ClaimTemporalWindow(claim_id=witness.claim_id)}
    )
    assert correction_candidates(target=inputs.target, testimony=(undated,)) == ()


def test_occurrence_has_no_end_correction_candidate() -> None:
    """Occurrence metadata has an end, but its verdict cannot be capped by correction."""
    inputs = _inputs(kind=FactTemporalKind.OCCURRENCE)
    assert [candidate.endpoint for candidate in inputs.candidates] == ["from"]


@pytest.mark.parametrize(
    "confidence, expected",
    [(0.79, TemporalResult.UNCERTAIN), (0.8, TemporalResult.APPLIED)],
)
def test_application_margin_is_stricter_than_escalation_floor(
    confidence: float, expected: TemporalResult
) -> None:
    """Clearing escalation alone does not authorize a frontier correction."""
    inputs = _inputs()
    operation_id = uuid4()
    result = admit_correction(
        inputs=inputs,
        verdict=_verdict(inputs=inputs, confidence=confidence),
        operation_id=operation_id,
        escalation_floor=0.75,
        supersede_margin=0.8,
    )
    assert result.result is expected
    if expected is TemporalResult.APPLIED:
        assert result.state.verdict.start == _at(2018)
        assert result.state.from_operation_id == operation_id
        assert result.state.revision == 6 and result.state.ingested_at == _at(2030)
    else:
        assert result.state == inputs.target.state


def test_incomplete_required_context_preserves_all_authority() -> None:
    """High model confidence cannot overcome omitted necessary conflict context."""
    inputs = _inputs().model_copy(
        update={"required_context_complete": False, "omitted_testimony_count": 3}
    )
    result = admit_correction(
        inputs=inputs,
        verdict=_verdict(inputs=inputs),
        operation_id=uuid4(),
        escalation_floor=0.75,
        supersede_margin=0.8,
    )
    assert (
        result.result is TemporalResult.UNCERTAIN
        and result.state == inputs.target.state
    )


@pytest.mark.parametrize(
    "mutation",
    ["invented", "wrong_endpoint", "no_support", "foreign_support", "forged_candidate"],
)
def test_model_cannot_invent_or_misattribute_endpoint_authority(mutation: str) -> None:
    """Every selected endpoint and supporting identity is checked against reconstructed candidates."""
    inputs = _inputs()
    verdict = _verdict(inputs=inputs)
    if mutation == "invented":
        verdict = verdict.model_copy(update={"start_candidate_id": uuid4()})
    elif mutation == "wrong_endpoint":
        verdict = verdict.model_copy(
            update={"start_candidate_id": inputs.candidates[1].candidate_id}
        )
    elif mutation == "no_support":
        verdict = verdict.model_copy(update={"supporting_claim_ids": ()})
    elif mutation == "foreign_support":
        verdict = verdict.model_copy(
            update={"supporting_claim_ids": (*verdict.supporting_claim_ids, uuid4())}
        )
    else:
        inputs = inputs.model_copy(
            update={
                "candidates": (
                    inputs.candidates[0].model_copy(update={"value": _at(2040)}),
                    *inputs.candidates[1:],
                )
            }
        )
    result = admit_correction(
        inputs=inputs,
        verdict=verdict,
        operation_id=uuid4(),
        escalation_floor=0.75,
        supersede_margin=0.8,
    )
    assert (
        result.result is TemporalResult.REFUSED and result.state == inputs.target.state
    )


def test_correction_cannot_cross_a_neighbouring_state() -> None:
    """Grounded source dates still obey the whole proposed window's neighbor guard."""
    inputs = _inputs()
    neighbour = inputs.target.model_copy(
        update={
            "fact": TemporalFactRef(plane=FactPlane.RELATION, fact_id=uuid4()),
            "state": inputs.target.state.model_copy(
                update={
                    "verdict": VerdictWindow(
                        start=_at(2017),
                        end=_at(2019),
                        start_basis=FactTemporalBasis.WORLD_TIME,
                        end_basis=FactTemporalBasis.WORLD_TIME,
                    )
                }
            ),
        }
    )
    inputs = inputs.model_copy(update={"neighbours": (neighbour,)})
    result = admit_correction(
        inputs=inputs,
        verdict=_verdict(inputs=inputs),
        operation_id=uuid4(),
        escalation_floor=0.75,
        supersede_margin=0.8,
    )
    assert (
        result.result is TemporalResult.REFUSED and result.state == inputs.target.state
    )


def test_verdict_schema_rejects_arbitrary_timestamp_output() -> None:
    """The provider answer surface accepts candidate IDs instead of model-authored dates."""
    with pytest.raises(ValidationError, match="Extra inputs"):
        TemporalCorrectionVerdict.model_validate(
            {
                "outcome": "correct",
                "confidence": 1,
                "rationale": "guess",
                "valid_from": "2018-01-01T00:00:00Z",
            }
        )
