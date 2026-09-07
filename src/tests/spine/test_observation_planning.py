"""D113 whole-plan proofs: seed/evidence, A-B-A reentry, support conservation and bounded work."""

from datetime import datetime
from datetime import timezone
import json
from uuid import UUID

import pytest

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core.fact_temporal import occurrence_union
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.observation_application import ObservationAdmissionHead
from rememberstack.model.observation_application import ObservationApplicationCandidate
from rememberstack.model.observation_application import ObservationApplicationInputs
from rememberstack.model.observation_application import ObservationApplicationPlan
from rememberstack.model.observation_application import (
    ObservationApplicationPreparation,
)
from rememberstack.model.observation_application import ObservationCurrentSupport
from rememberstack.model.observation_application import StagedObservation
from rememberstack.model.temporal_write import TemporalOperationKind
from rememberstack.spine.observation_adjudication import ObservationSettings
from rememberstack.spine.observation_identity import ObservationIdentityLadder
from rememberstack.spine.observation_planning import ObservationPlanBuilder
from rememberstack.spine.observation_planning import ObservationPlanningConflict
from tests.spine.test_observation_identity import _answer
from tests.spine.test_observation_identity import _assertion
from tests.spine.test_observation_identity import _candidate
from tests.spine.test_observation_identity import _support


def _prepared(
    *,
    source: StagedObservation,
    candidates: tuple[ObservationApplicationCandidate, ...] = (),
    support: tuple[ObservationCurrentSupport, ...] = (),
) -> ObservationApplicationPreparation:
    """Make a frozen planning input; database tests separately prove how this authority is prepared."""
    return ObservationApplicationPreparation(
        head=ObservationAdmissionHead(
            batch_id=UUID(int=2000),
            assertion_id=source.assertion_id,
            ordinal=1,
            expected_inputs=1,
            adjudicator_version="obs-proof",
            canonical_subject_entity_id=source.subject_entity_id,
        ),
        preparation_id=UUID(int=2001),
        input_fingerprint="a" * 64,
        inputs=ObservationApplicationInputs(
            deployment_id=UUID(int=2002),
            assertion=source,
            candidates=candidates,
            current_support=support,
            blocks=(),
            policy_fingerprint="b" * 64,
        ),
        new_observation_id=UUID(int=2003),
        recorded_at=datetime(2031, 9, 7, tzinfo=timezone.utc),
    )


def _build(
    *,
    prepared: ObservationApplicationPreparation,
    provider: FakeModelProvider,
    dependent_limit: int = 64,
) -> ObservationApplicationPlan:
    """Build the actual complete dependent plan with deterministic test providers, never SQL."""
    settings = ObservationSettings(
        novelty_floor=-1, dependent_assertion_limit=dependent_limit
    )
    return ObservationPlanBuilder(
        ladder=ObservationIdentityLadder(model_provider=provider, settings=settings),
        settings=settings,
    ).build(prepared=prepared, meter=NoopCostMeter())


def _a_b_a() -> ObservationApplicationPreparation:
    """Retain original A testimony from 2019 and 2024 before the late B@2022 assertion."""
    first = _assertion(
        number=2, statement="CEO is A", year=2019, shape=FactTemporalKind.STATE
    )
    later = _assertion(
        number=3,
        statement="A became CEO again",
        year=2024,
        shape=FactTemporalKind.STATE,
    )
    candidate = _candidate(assertion=first)
    windows = (first.testimony.window, later.testimony.window)
    candidate = candidate.model_copy(
        update={
            "state": candidate.state.model_copy(
                update={"revision": 2, "occurrence": occurrence_union(claims=windows)}
            ),
            "evidence_windows": windows,
            "testimony": (first.testimony, later.testimony),
        }
    )
    source = _assertion(
        number=4, statement="CEO is B", year=2022, shape=FactTemporalKind.STATE
    )
    return _prepared(
        source=source,
        candidates=(candidate,),
        support=(
            _support(assertion=first, candidate=candidate),
            _support(assertion=later, candidate=candidate),
        ),
    )


def _succession_router(prompt: str, type_name: str) -> dict[str, object]:
    """Use visible participant IDs to select the changing CEO state, including recursive reentry."""
    del type_name
    data = json.loads("{" + prompt.split("\n{", 1)[1])
    statement = data["assertion"]["statement"]
    prior = "CEO is A" if statement == "CEO is B" else "CEO is B"
    candidate = next(item for item in data["candidates"] if item["statement"] == prior)
    return _answer(
        target=UUID(candidate["observation_id"]), outcome="incoming_succeeds"
    )


def test_first_mention_plan_seeds_world_time_with_no_inference() -> None:
    """One source produces a revision-one seed intent; source publication never supplies the boundary."""
    provider = FakeModelProvider()
    prepared = _prepared(source=_assertion())
    plan = _build(prepared=prepared, provider=provider)
    assert plan.original_observation_id == prepared.new_observation_id
    assert plan.identity_outcome == "new"
    assert len(plan.new_facts) == len(plan.steps) == 1
    effect = plan.steps[0].effect
    assert effect.kind is TemporalOperationKind.SEED
    assert effect.after.revision == 1
    assert effect.after.verdict.start == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert effect.after.verdict.end is None
    assert provider.generated_requests == []
    assert (
        ObservationApplicationPlan.model_validate_json(plan.model_dump_json()) == plan
    )


def test_evidence_plan_preserves_verdict_and_seed_while_expanding_occurrence() -> None:
    """Attaching older compatible testimony never silently changes a state's adjudicated start."""
    old = _assertion(
        number=2, statement="CEO is A", year=2020, shape=FactTemporalKind.STATE
    )
    candidate = _candidate(assertion=old)
    source = _assertion(statement="CEO is A", year=2019, shape=FactTemporalKind.STATE)
    plan = _build(
        prepared=_prepared(source=source, candidates=(candidate,)),
        provider=FakeModelProvider(),
    )
    assert plan.identity_outcome == "evidence"
    assert plan.new_facts == ()
    effect = plan.steps[0].effect
    assert effect.after.verdict == candidate.state.verdict
    assert effect.after.seed_claim_id == old.testimony.claim_id
    assert effect.after.occurrence.start == datetime(2019, 1, 1, tzinfo=timezone.utc)


def test_complete_late_middle_plan_creates_a_b_a_and_moves_original_support() -> None:
    """The returned atomic group includes both caps and reentry, never a blind appended A slice."""
    prepared = _a_b_a()
    provider = FakeModelProvider(generate_router=_succession_router)
    plan = _build(prepared=prepared, provider=provider)
    assert len(provider.generated_requests) == 2
    assert [row.statement for row in plan.new_facts] == [
        "CEO is B",
        "A became CEO again",
    ]
    caps = [
        step for step in plan.steps if step.effect.kind is TemporalOperationKind.CAP
    ]
    assert [step.effect.after.verdict.end for step in caps] == [
        datetime(2022, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 1, tzinfo=timezone.utc),
    ]
    moves = [step for step in plan.steps if step.support_move is not None]
    assert len(moves) == 1
    move = moves[0].support_move
    assert move is not None
    assert (
        move.assertion_id == prepared.inputs.current_support[1].assertion.assertion_id
    )
    assert move.previous_observation_id == prepared.inputs.candidates[0].observation_id
    assert move.destination_observation_id == plan.new_facts[1].observation_id
    assert move.causal_cap_operation_id == caps[0].effect.operation_id
    assert (
        move.previous_support_owner_operation_id
        in moves[0].effect.semantic_predecessors
    )
    assert move.causal_cap_operation_id in moves[0].effect.semantic_predecessors
    assert moves[0].effect.decision.features["support_move"] == move.model_dump(
        mode="json", by_alias=True
    )
    removals = [step for step in plan.steps if step.remove_claim_id is not None]
    assert len(removals) == 1
    assert (
        removals[0].remove_claim_id
        == prepared.inputs.current_support[1].assertion.testimony.claim_id
    )
    assert removals[0].effect.after.occurrence == occurrence_union(
        claims=(prepared.inputs.candidates[0].evidence_windows[0],)
    )
    assert plan.original_observation_id == plan.new_facts[0].observation_id
    # Every target's before tuple matches the preceding virtual effect exactly.
    states = {item.observation_id: item.state for item in prepared.inputs.candidates}
    for step in plan.steps:
        if step.effect.fact.fact_id in states:
            assert step.effect.before == states[step.effect.fact.fact_id]
        states[step.effect.fact.fact_id] = step.effect.after
    assert (
        ObservationApplicationPlan.model_validate_json(plan.model_dump_json()) == plan
    )


def test_dependent_budget_discards_all_speculative_caps_and_moves() -> None:
    """Budget exhaustion preserves primary completion with a durable refusal, never half an A-B-A plan."""
    prepared = _a_b_a()
    provider = FakeModelProvider(generate_router=_succession_router)
    plan = _build(prepared=prepared, provider=provider, dependent_limit=0)
    assert len(provider.generated_requests) == 1
    assert len(plan.new_facts) == 1
    assert all(
        step.support_move is None and step.remove_claim_id is None
        for step in plan.steps
    )
    caps = [
        step.effect
        for step in plan.steps
        if step.effect.kind is TemporalOperationKind.CAP
    ]
    assert len(caps) == 1
    assert caps[0].result is TemporalResult.REFUSED
    assert caps[0].before == caps[0].after == prepared.inputs.candidates[0].state
    assert caps[0].reason == "dependent_assertion_budget_exhausted"


def test_legacy_support_refuses_cap_and_preserves_primary_new_identity() -> None:
    """An unrecoverable original assertion never receives reentry based on the fact display text."""
    prepared = _a_b_a()
    candidate = prepared.inputs.candidates[0]
    candidate = candidate.model_copy(
        update={"legacy_claim_ids": (candidate.evidence_windows[1].claim_id,)}
    )
    prepared = prepared.model_copy(
        update={
            "inputs": prepared.inputs.model_copy(update={"candidates": (candidate,)})
        }
    )
    provider = FakeModelProvider(generate_router=_succession_router)
    plan = _build(prepared=prepared, provider=provider)
    assert len(provider.generated_requests) == 1
    assert len(plan.new_facts) == 1
    refused = plan.steps[-1].effect
    assert refused.result is TemporalResult.REFUSED
    assert refused.reason == "legacy_assertion_unrecoverable"
    assert refused.after == candidate.state


def test_historical_creation_closes_belief_at_recorded_currency_instant() -> None:
    """Retained withdrawn source can establish historical identity without becoming a current belief."""
    source = _assertion()
    ended = datetime(2028, 2, 3, tzinfo=timezone.utc)
    source = source.model_copy(
        update={
            "testimony": source.testimony.model_copy(
                update={
                    "withdrawn_at": ended,
                    "withdrawal_reason": "version_deleted",
                    "evidence": source.testimony.evidence.model_copy(
                        update={"was_current": False}
                    ),
                }
            )
        }
    )
    plan = _build(prepared=_prepared(source=source), provider=FakeModelProvider())
    assert len(plan.steps) == 2
    close = plan.steps[-1].effect
    assert close.kind is TemporalOperationKind.SOURCE_REMOVAL
    assert close.after.invalidated_at == ended
    assert close.after.verdict == plan.steps[0].effect.after.verdict
    assert close.after.occurrence == plan.steps[0].effect.after.occurrence


def test_missing_withdrawal_clock_refuses_complete_plan_without_using_now() -> None:
    """A missing lifecycle witness cannot be replaced by preparation or publication time."""
    source = _assertion()
    source = source.model_copy(
        update={
            "testimony": source.testimony.model_copy(
                update={
                    "evidence": source.testimony.evidence.model_copy(
                        update={"was_current": False}
                    )
                }
            )
        }
    )
    with pytest.raises(
        ObservationPlanningConflict, match="recorded reconciliation instant"
    ):
        _build(prepared=_prepared(source=source), provider=FakeModelProvider())


def test_budget_exhaustion_after_first_reentry_rolls_back_the_entire_virtual_group() -> (
    None
):
    """A completed dependent answer and its B cap are both discarded when another reentry exceeds budget."""
    prepared = _a_b_a()
    candidate = prepared.inputs.candidates[0]
    extra = _assertion(
        number=5,
        statement="A became CEO again",
        year=2026,
        shape=FactTemporalKind.STATE,
    )
    windows = candidate.evidence_windows + (extra.testimony.window,)
    candidate = candidate.model_copy(
        update={
            "evidence_windows": windows,
            "testimony": candidate.testimony + (extra.testimony,),
            "state": candidate.state.model_copy(
                update={"occurrence": occurrence_union(claims=windows)}
            ),
        }
    )
    support = prepared.inputs.current_support + (
        _support(assertion=extra, candidate=candidate),
    )
    prepared = prepared.model_copy(
        update={
            "inputs": prepared.inputs.model_copy(
                update={"candidates": (candidate,), "current_support": support}
            )
        }
    )
    provider = FakeModelProvider(generate_router=_succession_router)
    plan = _build(prepared=prepared, provider=provider, dependent_limit=1)
    assert len(provider.generated_requests) == 2
    assert len(plan.new_facts) == 1
    assert plan.new_facts[0].statement == "CEO is B"
    assert all(
        step.support_move is None and step.remove_claim_id is None
        for step in plan.steps
    )
    assert not any(
        step.effect.kind is TemporalOperationKind.CAP
        and step.effect.result is TemporalResult.APPLIED
        for step in plan.steps
    )
    assert plan.steps[-1].effect.reason == "dependent_assertion_budget_exhausted"


def test_two_generations_move_independently_and_remove_old_claim_only_once() -> None:
    """Both original receipts survive; the second move removes the final old aggregation contribution."""
    prepared = _a_b_a()
    later = prepared.inputs.current_support[1]
    other = later.model_copy(
        update={"adjudicator_version": "older-observation-generation"}
    )
    prepared = prepared.model_copy(
        update={
            "inputs": prepared.inputs.model_copy(
                update={"current_support": prepared.inputs.current_support + (other,)}
            )
        }
    )
    plan = _build(
        prepared=prepared,
        provider=FakeModelProvider(generate_router=_succession_router),
    )
    moves = [step for step in plan.steps if step.support_move is not None]
    assert len(moves) == 2
    assert len(plan.new_facts) == 2
    assert {
        step.support_move.adjudicator_version for step in moves if step.support_move
    } == {"obs-proof", "older-observation-generation"}
    assert (
        len(
            {
                step.support_move.destination_observation_id
                for step in moves
                if step.support_move
            }
        )
        == 1
    )
    removals = [
        step for step in plan.steps if step.effect.reason == "support_relocated"
    ]
    assert len(removals) == 2
    assert removals[0].remove_claim_id is None
    assert removals[1].remove_claim_id == later.assertion.testimony.claim_id
    assert (
        removals[0].effect.after.occurrence
        == prepared.inputs.candidates[0].state.occurrence
    )
    assert moves[0].support_move is not None
    assert (
        moves[0].effect.decision.features["source_adjudicator_version"]
        == moves[0].support_move.adjudicator_version
    )


def test_disjoint_event_date_dispute_produces_two_uncapped_identities() -> None:
    """Semantic contradiction relates two event facts and never translates the conflict into a cap."""
    source = _assertion(number=1, statement="the final was in 2025", year=2025)
    old = _assertion(number=2, statement="the final was in 2024", year=2024)
    candidate = _candidate(assertion=old)
    provider = FakeModelProvider(
        generate_payload=_answer(target=candidate.observation_id, outcome="contradict")
    )
    plan = _build(
        prepared=_prepared(source=source, candidates=(candidate,)), provider=provider
    )
    final = {step.effect.fact.fact_id: step.effect.after for step in plan.steps}
    assert len(final) == 2
    assert len({state.contradiction_group for state in final.values()}) == 1
    assert all(state.verdict.end is None for state in final.values())
    assert all(step.effect.kind is not TemporalOperationKind.CAP for step in plan.steps)
    assert all(
        step.effect.decision.related_fact_id is not None
        for step in plan.steps
        if step.effect.decision.outcome == "contradict"
    )


def test_refused_chronological_cap_preserves_both_states_and_records_reason() -> None:
    """A purported successor before the known start cannot create an empty or backwards state."""
    old = _assertion(
        number=2, statement="CEO is A", year=2025, shape=FactTemporalKind.STATE
    )
    candidate = _candidate(assertion=old)
    source = _assertion(
        number=1, statement="CEO is B", year=2022, shape=FactTemporalKind.STATE
    )
    provider = FakeModelProvider(
        generate_payload=_answer(
            target=candidate.observation_id, outcome="incoming_succeeds"
        )
    )
    plan = _build(
        prepared=_prepared(source=source, candidates=(candidate,)), provider=provider
    )
    assert len(plan.new_facts) == 1
    cap = plan.steps[-1].effect
    assert cap.result is TemporalResult.REFUSED
    assert cap.reason == "cap_guard_refused"
    assert cap.before == cap.after == candidate.state
    assert cap.decision.related_fact_id == plan.original_observation_id


def test_reextraction_plans_support_flag_without_closing_belief() -> None:
    """D54 processing uncertainty must not become a fabricated D55 source deletion."""
    source = _assertion()
    source = source.model_copy(
        update={
            "testimony": source.testimony.model_copy(
                update={
                    "withdrawn_at": datetime(2028, 2, 3, tzinfo=timezone.utc),
                    "withdrawal_reason": "reextracted",
                    "evidence": source.testimony.evidence.model_copy(
                        update={"was_current": False}
                    ),
                }
            )
        }
    )
    plan = _build(prepared=_prepared(source=source), provider=FakeModelProvider())
    marker = plan.steps[-1]
    assert marker.flag_support_withdrawn is True
    assert marker.effect.kind is TemporalOperationKind.EVIDENCE
    assert marker.effect.after.invalidated_at is None
    assert marker.effect.before.verdict == marker.effect.after.verdict
    assert marker.effect.reason == "reextraction_support_withdrawn"


@pytest.mark.parametrize(
    "damage",
    [
        "missing_owner",
        "different_destination",
        "orphan_seed",
        "future_dependency",
        "broken_chain",
    ],
)
def test_corrupt_complete_plan_cannot_be_deserialized_as_execution_authority(
    damage: str,
) -> None:
    """A saved answer with lost causal/evidence steps cannot authorize an incomplete transaction."""
    plan = _build(
        prepared=_a_b_a(),
        provider=FakeModelProvider(generate_router=_succession_router),
    )
    payload = plan.model_dump(mode="json")
    move = next(step for step in payload["steps"] if step["support_move"] is not None)
    if damage == "missing_owner":
        owner = move["support_move"]["previous_support_owner_operation_id"]
        move["effect"]["semantic_predecessors"] = [
            value for value in move["effect"]["semantic_predecessors"] if value != owner
        ]
    elif damage == "different_destination":
        move["support_move"]["destination_observation_id"] = str(UUID(int=99999))
    elif damage == "orphan_seed":
        payload["new_facts"] = payload["new_facts"][:1]
    elif damage == "future_dependency":
        payload["steps"][0]["effect"]["semantic_predecessors"].append(
            payload["steps"][-1]["effect"]["operation_id"]
        )
    else:
        final = payload["steps"][-1]["effect"]
        final["before"]["revision"] += 5
        final["after"]["revision"] += 5
    with pytest.raises(ValueError):
        ObservationApplicationPlan.model_validate_json(json.dumps(payload))


def test_complete_evidence_footprint_survives_bounded_model_testimony() -> None:
    """A cap planning scan consumes every legacy window even though the prompt shows bounded text."""
    testimony = tuple(
        _assertion(
            number=n, statement="CEO is A", year=2019, shape=FactTemporalKind.STATE
        ).testimony
        for n in range(2, 13)
    )
    first = _assertion(
        number=2, statement="CEO is A", year=2019, shape=FactTemporalKind.STATE
    )
    candidate = _candidate(assertion=first).model_copy(
        update={
            "evidence_windows": tuple(item.window for item in testimony),
            "evidence": tuple(
                item.evidence.model_copy(update={"role": "historical"})
                for item in testimony
            ),
            "testimony": testimony[:1],
            "omitted_testimony": 10,
            "legacy_claim_ids": tuple(item.claim_id for item in testimony),
        }
    )
    source = _assertion(
        number=1, statement="CEO is B", year=2022, shape=FactTemporalKind.STATE
    )
    provider = FakeModelProvider(
        generate_payload=_answer(
            target=candidate.observation_id, outcome="incoming_succeeds"
        )
    )
    plan = _build(
        prepared=_prepared(source=source, candidates=(candidate,)), provider=provider
    )
    cap = plan.steps[-1].effect
    assert cap.result is TemporalResult.APPLIED
    assert {item.claim_id for item in cap.evidence} == {source.testimony.claim_id} | {
        item.claim_id for item in testimony
    }
    prompt_data = json.loads("{" + provider.generated_prompts[0].split("\n{", 1)[1])
    assert len(prompt_data["candidates"][0]["testimony"]) == 1
    assert "evidence" not in prompt_data["candidates"][0]
