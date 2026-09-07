"""Contextual identity accepts model decisions without hard text/date gates (D118)."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model.fact_application import AssertionKind
from tests.fact_application_support import WriterCase
from tests.spine.test_fact_application_writer import database_engine as database_engine


@pytest.mark.parametrize("kind", ["relation", "observation"])
@pytest.mark.parametrize("day", [10, 12])
@pytest.mark.parametrize("same_identity", [True, False])
def test_identity_is_contextual_even_with_identical_words_or_disjoint_dates(
    database_engine: Engine, kind: AssertionKind, day: int, same_identity: bool
) -> None:
    """Identical testimony can be two wins; differently dated testimony can be one corrected win."""
    case = WriterCase(engine=database_engine)
    _, _, original = case.first(kind=kind)
    _, app = case.stage(day=day, kind=kind)
    decision: dict[str, object] = (
        {"target": {"fact_id": str(original)}}
        if same_identity
        else {
            "target": {"new_handle": "other"},
            "new_facts": [{"handle": "other", "assertion_application_id": str(app)}],
        }
    )
    case.decide(decision=decision)
    result = case.apply(app=app)
    assert (result["fact_id"] == str(original)) is same_identity
    table = "relations" if kind == "relation" else "observations"
    with database_engine.connect() as connection:
        assert connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE deployment_id=:dep"),
            {"dep": case.dep},
        ).scalar_one() == (1 if same_identity else 2)
        # Attaching another claim does not implicitly rewrite the chosen date.
        assert (
            connection.execute(
                text(f"SELECT valid_from FROM {table} WHERE {kind}_id=:id"),
                {"id": original},
            )
            .scalar_one()
            .day
            == 10
        )


@pytest.mark.parametrize("kind", ["relation", "observation"])
def test_contradicting_claim_does_not_overwrite_fact_or_its_dates(
    database_engine: Engine, kind: AssertionKind
) -> None:
    """Contrary evidence remains attributable; the fact's current interpretation stays inspectable."""
    case = WriterCase(engine=database_engine)
    _, _, original = case.first(kind=kind)
    claim, app = case.stage(day=12, kind=kind)
    case.decide(
        decision={"target": {"fact_id": str(original)}, "stance": "contradicts"}
    )
    case.apply(app=app)
    table = "relations" if kind == "relation" else "observations"
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                f"SELECT valid_from,evidence_count,contradict_count FROM {table} WHERE {kind}_id=:id"
            ),
            {"id": original},
        ).one()
        assert row[0].day == 10 and row[1:] == (1, 1)
        assert (
            connection.execute(
                text(f"SELECT stance::text FROM {kind}_evidence WHERE claim_id=:claim"),
                {"claim": claim},
            ).scalar_one()
            == "contradicts"
        )


def test_legacy_contradiction_diagnostic_records_and_rejects_missed_conflicts(
    database_engine: Engine,
) -> None:
    """Retained pair-diagnostic callers still get real P/R and an append-only run."""
    from rememberstack.adapters.testing import FakeModelProvider
    from rememberstack.eval import run_contradiction_suite
    from rememberstack.eval import seed_contradiction_cases
    from rememberstack.spine import ObservationAdjudicator
    from rememberstack.spine import ObservationSettings

    case = WriterCase(engine=database_engine)
    seed_contradiction_cases(engine=database_engine, deployment_id=case.dep)

    def answer(prompt: str, type_name: str) -> dict[str, object]:
        """Canned independent positive/negative predictions for the five golden inputs."""
        assert type_name == "ObservationVerdict"
        conflict = "$7M" in prompt or "800" in prompt
        return {
            "outcome": "contradict" if conflict else "new",
            "confidence": 0.95,
            "rationale": "golden diagnostic prediction",
        }

    correct = ObservationAdjudicator(
        engine=database_engine,
        model_provider=FakeModelProvider(generate_router=answer),
        settings=ObservationSettings(),
    )
    report = run_contradiction_suite(
        engine=database_engine,
        deployment_id=case.dep,
        adjudicator=correct,
        component_version="legacy-diagnostic-test",
    )
    assert report["passed"] and report["precision"] == 1.0 and report["recall"] == 1.0
    blind = ObservationAdjudicator(
        engine=database_engine,
        model_provider=FakeModelProvider(
            generate_payloads={
                "ObservationVerdict": {"outcome": "new", "confidence": 0.95}
            }
        ),
        settings=ObservationSettings(),
    )
    assert not run_contradiction_suite(
        engine=database_engine,
        deployment_id=case.dep,
        adjudicator=blind,
        component_version="legacy-diagnostic-test",
    )["passed"]
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM eval_runs WHERE deployment_id=:dep AND suite='contradiction'"
                ),
                {"dep": case.dep},
            ).scalar_one()
            == 2
        )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM canary_cases WHERE deployment_id=:dep AND NOT (expected->>'contradiction')::boolean"
            ),
            {"dep": case.dep},
        )
    assert not run_contradiction_suite(
        engine=database_engine,
        deployment_id=case.dep,
        adjudicator=correct,
        component_version="legacy-diagnostic-test",
    )["passed"]
