"""D113 identity inference proofs: source clocks, event identity and grounded selection."""

from datetime import datetime
from datetime import timezone
from uuid import UUID

import pytest

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core.fact_temporal import cap_fact
from rememberstack.core.fact_temporal import seed_fact
from rememberstack.core.observation_temporal import observation_bounds
from rememberstack.core.observation_temporal import observation_resplit_inputs
from rememberstack.core.observation_temporal import observation_support_remains
from rememberstack.model import ClaimValidKind
from rememberstack.model import ClaimValidPrecision
from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalBasis
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.model_provider import ProviderCallError
from rememberstack.model.model_provider import ProviderInvalidResponseError
from rememberstack.model.observation_application import ObservationApplicationCandidate
from rememberstack.model.observation_application import ObservationApplicationOutput
from rememberstack.model.observation_application import ObservationCurrentSupport
from rememberstack.model.observation_application import ObservationTestimony
from rememberstack.model.observation_application import StagedObservation
from rememberstack.model.temporal_write import TemporalEvidenceRef
from rememberstack.spine.observation_adjudication import ObservationSettings
from rememberstack.spine.observation_identity import ObservationIdentityLadder


def _assertion(
    *,
    number: int = 1,
    statement: str = "won the tournament",
    year: int | None = 2024,
    shape: FactTemporalKind = FactTemporalKind.OCCURRENCE,
    claim_kind: ClaimValidKind | None = None,
) -> StagedObservation:
    """Create raw D41 testimony whose publication clock is deliberately much later."""
    claim = UUID(int=number)
    return StagedObservation(
        assertion_id=UUID(int=number + 100),
        receipt_id=UUID(int=number + 200),
        normalizer_version="identity-proof",
        normalized_subject_entity_id=UUID(int=1000),
        subject_entity_id=UUID(int=1000),
        statement=statement,
        shape_kind=shape,
        testimony=ObservationTestimony(
            claim_id=claim,
            doc_id=UUID(int=number + 300),
            text=statement,
            asserted_at=datetime(2030, 9, 1, 19, 30, tzinfo=timezone.utc),
            window=ClaimTemporalWindow(
                claim_id=claim,
                kind=claim_kind,
                valid_from=datetime(year, 1, 1, tzinfo=timezone.utc) if year else None,
                precision=ClaimValidPrecision.OPEN
                if year
                else ClaimValidPrecision.UNKNOWN,
            ),
            evidence=TemporalEvidenceRef(
                claim_id=claim, role="support", was_current=True, fingerprint="a" * 64
            ),
        ),
    )


def _candidate(*, assertion: StagedObservation) -> ObservationApplicationCandidate:
    """Seed the comparison fact through the actual pure temporal authority."""
    return ObservationApplicationCandidate(
        observation_id=UUID(int=assertion.testimony.claim_id.int + 400),
        statement=assertion.statement,
        normalizer_version=assertion.normalizer_version,
        state=seed_fact(
            seed=assertion.testimony.window,
            shape=assertion.shape_kind,
            ingested_at=datetime(2031, 1, 1, tzinfo=timezone.utc),
        ),
        operation_id=UUID(int=assertion.testimony.claim_id.int + 500),
        testimony=(assertion.testimony,),
        omitted_testimony=0,
        evidence_windows=(assertion.testimony.window,),
        legacy_claim_ids=(),
    )


def _infer(
    *,
    assertion: StagedObservation,
    candidates: tuple[ObservationApplicationCandidate, ...],
    provider: FakeModelProvider,
    settings: ObservationSettings | None = None,
) -> ObservationApplicationOutput:
    """Run the real inference ladder with no engine or SQL session available."""
    return ObservationIdentityLadder(
        model_provider=provider,
        settings=settings or ObservationSettings(novelty_floor=-1),
    ).infer(
        assertion=assertion,
        candidates=candidates,
        meter=NoopCostMeter(),
        call_key="identity-proof",
    )


def _answer(
    *, target: UUID, outcome: str = "evidence", confidence: float = 0.95
) -> dict[str, object]:
    """Return one named semantic selection, without any authority to invent timestamps."""
    return {
        "decisions": [{"observation_id": str(target), "outcome": outcome}],
        "confidence": confidence,
        "rationale": "same individually identified occurrence",
    }


def test_unique_state_shortcut_uses_no_provider_calls() -> None:
    """Repeated identical compatible states consume established identity without inference."""
    incoming = _assertion(shape=FactTemporalKind.STATE)
    existing = _candidate(assertion=_assertion(number=2, shape=FactTemporalKind.STATE))
    provider = FakeModelProvider()
    result = _infer(assertion=incoming, candidates=(existing,), provider=provider)
    assert result.method == "exact"
    assert result.verdict.decisions[0].observation_id == existing.observation_id
    assert provider.generated_requests == []
    assert provider.embedded_texts == []


def test_identical_occurrence_text_requires_semantic_identity_and_both_clocks() -> None:
    """Even identical same-day event wording can mean distinct events, never an exact collapse."""
    incoming = _assertion()
    existing = _candidate(assertion=_assertion(number=2))
    provider = FakeModelProvider(
        generate_payload=_answer(target=existing.observation_id, outcome="coexist")
    )
    result = _infer(assertion=incoming, candidates=(existing,), provider=provider)
    assert result.method == "small_model"
    assert result.verdict.decisions[0].outcome == "coexist"
    prompt = provider.generated_prompts[0]
    assert "2030-09-01T19:30:00Z" in prompt
    assert "2024-01-01T00:00:00Z" in prompt
    assert "Identical text is NOT proof" in prompt
    assert provider.generated_requests[0].temperature == 0


def test_multiple_state_matches_require_one_grounded_target() -> None:
    """Observation state matching never inherits the relation multi-target support rule."""
    incoming = _assertion(shape=FactTemporalKind.STATE)
    candidates = tuple(
        _candidate(assertion=_assertion(number=n, shape=FactTemporalKind.STATE))
        for n in (2, 3)
    )
    provider = FakeModelProvider(
        generate_payload=_answer(target=candidates[1].observation_id)
    )
    result = _infer(assertion=incoming, candidates=candidates, provider=provider)
    assert result.method == "small_model"
    assert result.verdict.decisions[0].observation_id == candidates[1].observation_id


@pytest.mark.parametrize(
    "invalid", ["unshown", "duplicate", "two_targets", "mixed_datedness", "erased"]
)
def test_ungrounded_selection_escalates_then_coexists(invalid: str) -> None:
    """Neither high confidence nor a frontier model can bypass identity authority guards."""
    incoming = _assertion(year=None if invalid == "mixed_datedness" else 2024)
    candidates = tuple(_candidate(assertion=_assertion(number=n)) for n in (2, 3))
    if invalid == "erased":
        candidate = candidates[0]
        state = candidate.state.model_copy(
            update={
                "verdict": candidate.state.verdict.model_copy(
                    update={"start": None, "start_basis": FactTemporalBasis.ERASED}
                )
            }
        )
        candidates = (candidate.model_copy(update={"state": state}), candidates[1])
    payload = _answer(
        target=UUID(int=9999) if invalid == "unshown" else candidates[0].observation_id
    )
    if invalid in ("duplicate", "two_targets"):
        target = candidates[0] if invalid == "duplicate" else candidates[1]
        payload["decisions"] = [
            {
                "observation_id": str(candidates[0].observation_id),
                "outcome": "evidence",
            },
            {"observation_id": str(target.observation_id), "outcome": "evidence"},
        ]
    provider = FakeModelProvider(generate_payload=payload)
    result = _infer(assertion=incoming, candidates=candidates, provider=provider)
    assert result.disposition == "refused"
    assert result.verdict.decisions == ()
    assert result.rejected_verdict is not None
    assert len(provider.generated_requests) == 2


def test_low_confidence_frontier_does_not_attach_evidence() -> None:
    """Escalation is not acceptance: uncertain identity creates conservative coexistence."""
    candidate = _candidate(assertion=_assertion(number=2))
    provider = FakeModelProvider(
        generate_payload=_answer(target=candidate.observation_id, confidence=0.6)
    )
    result = _infer(assertion=_assertion(), candidates=(candidate,), provider=provider)
    assert result.disposition == "uncertain"
    assert result.verdict.decisions == ()
    assert len(provider.generated_requests) == 2


def test_cap_requires_supersession_margin_even_above_escalation_floor() -> None:
    """A .77 answer cannot cap at the configured .8 margin after either model rung."""
    candidate = _candidate(
        assertion=_assertion(
            number=2, statement="was CEO", year=2020, shape=FactTemporalKind.STATE
        )
    )
    provider = FakeModelProvider(
        generate_payload=_answer(
            target=candidate.observation_id,
            outcome="incoming_succeeds",
            confidence=0.77,
        )
    )
    result = _infer(
        assertion=_assertion(statement="resigned as CEO"),
        candidates=(candidate,),
        provider=provider,
    )
    assert result.disposition == "uncertain"
    assert result.verdict.decisions == ()
    assert len(provider.generated_requests) == 2


def test_dated_ending_event_can_nominate_a_finite_ended_state() -> None:
    """The ladder considers an earlier resignation even if a state already has a later end."""
    prior = _assertion(
        number=2, statement="was CEO", year=2020, shape=FactTemporalKind.STATE
    )
    window = prior.testimony.window.model_copy(
        update={
            "valid_until": datetime(2028, 1, 1, tzinfo=timezone.utc),
            "precision": ClaimValidPrecision.YEAR,
        }
    )
    prior = prior.model_copy(
        update={"testimony": prior.testimony.model_copy(update={"window": window})}
    )
    candidate = _candidate(assertion=prior)
    provider = FakeModelProvider(
        generate_payload=_answer(
            target=candidate.observation_id, outcome="incoming_succeeds"
        )
    )
    result = _infer(
        assertion=_assertion(statement="resigned as CEO"),
        candidates=(candidate,),
        provider=provider,
    )
    assert result.disposition == "accepted"
    assert result.verdict.decisions[0].outcome == "incoming_succeeds"


def test_undated_ending_event_does_not_borrow_publication_date() -> None:
    """A source timestamp cannot make an undated event eligible to end a state."""
    candidate = _candidate(assertion=_assertion(number=2, shape=FactTemporalKind.STATE))
    provider = FakeModelProvider()
    result = _infer(
        assertion=_assertion(year=None), candidates=(candidate,), provider=provider
    )
    assert result.method == "novelty_gate"
    assert provider.generated_requests == []


def test_explicit_claim_kind_outranks_normalizer_shape() -> None:
    """An event_time claim cannot take the state-text shortcut despite its normalizer guess."""
    incoming = _assertion(
        shape=FactTemporalKind.STATE, claim_kind=ClaimValidKind.EVENT_TIME
    )
    candidate = _candidate(assertion=_assertion(number=2, shape=FactTemporalKind.STATE))
    provider = FakeModelProvider(
        generate_payload=_answer(target=candidate.observation_id)
    )
    result = _infer(assertion=incoming, candidates=(candidate,), provider=provider)
    assert result.disposition == "refused"
    assert result.verdict.decisions == ()


def test_bounded_nomination_discloses_omissions_and_rejects_unshown_target() -> None:
    """Top-k rank limits inference, and an omitted identity cannot be supplied by the model."""
    candidates = tuple(_candidate(assertion=_assertion(number=n)) for n in (2, 3, 4))
    provider = FakeModelProvider(
        generate_payload=_answer(target=candidates[-1].observation_id)
    )
    result = _infer(
        assertion=_assertion(),
        candidates=candidates,
        provider=provider,
        settings=ObservationSettings(novelty_floor=-1, hub_top_k=1),
    )
    assert result.nominated_observation_ids == (candidates[0].observation_id,)
    assert result.omitted_candidates == 2
    assert result.disposition == "refused"
    assert '"omitted_candidates": 2' in provider.generated_prompts[0]


def test_completed_invalid_output_escalates_but_operational_failure_propagates() -> (
    None
):
    """Malformed completed answers and transport failures have different work-ledger outcomes."""
    candidate = _candidate(assertion=_assertion(number=2))

    def invalid(prompt: str, type_name: str) -> dict[str, object]:
        """Simulate a provider's completed schema-invalid response."""
        del prompt, type_name
        raise ProviderInvalidResponseError("invalid structured response")

    provider = FakeModelProvider(generate_router=invalid)
    result = _infer(assertion=_assertion(), candidates=(candidate,), provider=provider)
    assert result.disposition == "uncertain"
    assert len(provider.generated_requests) == 2

    def failed(prompt: str, type_name: str) -> dict[str, object]:
        """Simulate an operational failure without a completed semantic answer."""
        del prompt, type_name
        raise ProviderCallError("transport failed")

    provider = FakeModelProvider(generate_router=failed)
    with pytest.raises(ProviderCallError, match="transport failed"):
        _infer(assertion=_assertion(), candidates=(candidate,), provider=provider)
    assert len(provider.generated_requests) == 1


def _support(
    *,
    assertion: StagedObservation,
    candidate: ObservationApplicationCandidate,
    generation: str = "obs-proof",
) -> ObservationCurrentSupport:
    """Retain immutable original identity separately from the presently owned evidence location."""
    return ObservationCurrentSupport(
        assertion=assertion,
        adjudicator_version=generation,
        original_observation_id=candidate.observation_id,
        current_observation_id=candidate.observation_id,
        support_owner_operation_id=candidate.operation_id,
        support_checkpoint_id=None,
    )


def test_late_middle_state_plans_reentry_from_original_world_time() -> None:
    """A@2019 plus A@2024, then B@2022, requires A@2024 to reenter and end B."""
    oldest = _assertion(
        number=2, statement="CEO is A", year=2019, shape=FactTemporalKind.STATE
    )
    latest = _assertion(
        number=3,
        statement="A became chief executive again",
        year=2024,
        shape=FactTemporalKind.STATE,
    )
    # Publication order deliberately disagrees with the world-time order.
    latest = latest.model_copy(
        update={
            "testimony": latest.testimony.model_copy(
                update={"asserted_at": datetime(2020, 1, 1, tzinfo=timezone.utc)}
            )
        }
    )
    first = _candidate(assertion=oldest)
    first = first.model_copy(
        update={"evidence_windows": (oldest.testimony.window, latest.testimony.window)}
    )
    original_support = (
        _support(assertion=oldest, candidate=first),
        _support(assertion=latest, candidate=first),
    )
    middle = _assertion(
        number=4, statement="CEO is B", year=2022, shape=FactTemporalKind.STATE
    )
    boundary = observation_bounds(assertion=middle).start
    assert boundary is not None
    displaced = observation_resplit_inputs(
        candidate=first, boundary=boundary, current_support=original_support
    )
    assert displaced.blocking_legacy_claim_ids == ()
    assert tuple(item.assertion for item in displaced.applications) == (latest,)
    cap = cap_fact(state=first.state, boundary=boundary, operation_id=UUID(int=700))
    first = first.model_copy(update={"state": cap.state})
    second = _candidate(assertion=middle)
    provider = FakeModelProvider(
        generate_payload=_answer(
            target=second.observation_id, outcome="incoming_succeeds"
        )
    )
    result = _infer(
        assertion=displaced.applications[0].assertion,
        candidates=(first, second),
        provider=provider,
    )
    assert result.disposition == "accepted"
    last_start = observation_bounds(assertion=latest).start
    assert last_start is not None
    second_cap = cap_fact(
        state=second.state, boundary=last_start, operation_id=UUID(int=701)
    )
    assert first.state.verdict.end == datetime(2022, 1, 1, tzinfo=timezone.utc)
    assert second_cap.state.verdict.end == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert (
        displaced.applications[0].assertion.statement
        == "A became chief executive again"
    )
    assert not observation_support_remains(
        candidate=first, moving=original_support[1], current_support=original_support
    )


def test_qualifying_legacy_evidence_blocks_a_cap_without_guessed_reentry() -> None:
    """A recovered current assertion cannot erase an independent unattributed legacy baseline."""
    source = _assertion(number=2, year=2024, shape=FactTemporalKind.STATE)
    candidate = _candidate(assertion=source).model_copy(
        update={"legacy_claim_ids": (source.testimony.claim_id,)}
    )
    application = _support(assertion=source, candidate=candidate)
    result = observation_resplit_inputs(
        candidate=candidate,
        boundary=datetime(2022, 1, 1, tzinfo=timezone.utc),
        current_support=(application,),
    )
    assert result.blocking_legacy_claim_ids == (source.testimony.claim_id,)
    assert observation_support_remains(
        candidate=candidate, moving=application, current_support=(application,)
    )


def test_other_generation_support_prevents_premature_evidence_removal() -> None:
    """A semantic roll does not authorize deleting another generation's source attribution."""
    source = _assertion(number=2, year=2024, shape=FactTemporalKind.STATE)
    candidate = _candidate(assertion=source)
    first = _support(assertion=source, candidate=candidate)
    second = _support(assertion=source, candidate=candidate, generation="obs-next")
    result = observation_resplit_inputs(
        candidate=candidate,
        boundary=datetime(2022, 1, 1, tzinfo=timezone.utc),
        current_support=(second, first),
    )
    assert {item.adjudicator_version for item in result.applications} == {
        "obs-proof",
        "obs-next",
    }
    assert observation_support_remains(
        candidate=candidate, moving=first, current_support=(first, second)
    )


def test_undated_support_is_not_displaced_by_its_publication_clock() -> None:
    """Even a late source publication cannot turn unknown world time into a re-split start."""
    source = _assertion(number=2, year=None, shape=FactTemporalKind.STATE)
    candidate = _candidate(assertion=source)
    result = observation_resplit_inputs(
        candidate=candidate,
        boundary=datetime(2022, 1, 1, tzinfo=timezone.utc),
        current_support=(_support(assertion=source, candidate=candidate),),
    )
    assert result.applications == ()
    assert result.blocking_legacy_claim_ids == ()


def test_missing_current_support_is_refused_before_a_cap_can_strand_evidence() -> None:
    """An attached non-legacy claim without its retained application is corruption, not an empty re-split."""
    source = _assertion(number=2, year=2024, shape=FactTemporalKind.STATE)
    candidate = _candidate(assertion=source)
    with pytest.raises(ValueError, match="incomplete current or legacy attribution"):
        observation_resplit_inputs(
            candidate=candidate,
            boundary=datetime(2022, 1, 1, tzinfo=timezone.utc),
            current_support=(),
        )
