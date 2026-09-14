"""PostgreSQL proofs for handle translation, retries, forget, and date clearing."""

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core.concise_adjudication import project_concise_inputs
from rememberstack.core.concise_adjudication import translate_prompt_decision
from rememberstack.model.concise_adjudication import PromptFactDecision
from rememberstack.spine.fact_adjudication import FactAdjudicationSettings
from rememberstack.spine.fact_adjudication import FactAdjudicator
from rememberstack.spine.fact_applications import ApplicationInputChanged
from rememberstack.spine.settings import load_database_settings
from tests.fact_application_support import WriterCase


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Use this lane's disposable database; skip when it is not configured."""
    try:
        url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for writer acceptance")
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config=config, revision="head")
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


def _writer(engine: Engine, answer) -> FactAdjudicator:
    """An adjudicator whose provider returns attempt-local handle answers."""
    return FactAdjudicator(
        engine=engine,
        model_provider=FakeModelProvider(generate_router=answer),
        settings=FactAdjudicationSettings(),
    )


def _payload(prompt: str) -> dict[str, Any]:
    """Decode the compact JSON the model actually sees."""
    return json.loads(prompt.split("INPUT JSON:\n", 1)[1])


def test_drain_attaches_with_f1_and_hides_store_ids(database_engine: Engine) -> None:
    """The model names F1; the writer still attaches the real fact."""
    case = WriterCase(engine=database_engine)
    _, _, fact = case.first()
    case.stage(day=12)
    seen: dict[str, object] = {}

    def answer(prompt: str, type_name: str) -> dict[str, object]:
        assert type_name == "PromptFactDecision"
        payload = _payload(prompt)
        seen["payload"] = payload
        assert payload["facts"][0]["handle"] == "F1"
        assert str(fact) not in prompt
        assert str(case.dep) not in prompt
        return {"target": "F1", "confidence": 0.92, "rationale": "Same Riverside win."}

    drained = _writer(database_engine, answer).drain(
        deployment_id=case.dep,
        subject_entity_id=case.subject,
        meter=NoopCostMeter(),
        call_key="test",
    )
    assert drained == (fact,)
    with database_engine.connect() as connection:
        count = connection.execute(
            text("SELECT evidence_count FROM observations WHERE observation_id=:id"),
            {"id": fact},
        ).scalar_one()
    assert count == 2
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert "membership_hash" not in payload
    fact_row = payload["facts"][0]
    assert fact_row.get("canonical_subject", fact_row["subject"]) == payload["subject"]


def test_unknown_handle_retries_same_attempt(database_engine: Engine) -> None:
    """F99 fails closed; the next drain reuses the attempt and can succeed."""
    case = WriterCase(engine=database_engine)
    _, _, fact = case.first()
    case.stage(day=12)
    calls = {"n": 0}

    def answer(prompt: str, type_name: str) -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"target": "F99", "confidence": 0.9, "rationale": "Invented name."}
        return {"target": "F1", "confidence": 0.9, "rationale": "Same win."}

    writer = _writer(database_engine, answer)
    with pytest.raises(ApplicationInputChanged, match="unknown fact handle"):
        writer.drain(
            deployment_id=case.dep,
            subject_entity_id=case.subject,
            meter=NoopCostMeter(),
            call_key="test",
        )
    drained = writer.drain(
        deployment_id=case.dep,
        subject_entity_id=case.subject,
        meter=NoopCostMeter(),
        call_key="test",
    )
    assert drained == (fact,)
    assert calls["n"] == 2


def test_date_clearing_uses_supplied_claim_handle(database_engine: Engine) -> None:
    """An explicit unknown window through C-names clears chosen dates."""
    case = WriterCase(engine=database_engine)
    _, _, fact = case.first()
    case.stage(day=12)

    def answer(prompt: str, type_name: str) -> dict[str, object]:
        payload = _payload(prompt)
        incoming = next(row for row in payload["assertions"] if row["incoming"])
        return {
            "target": "F1",
            "window": {
                "window": {"valid_precision": "unknown"},
                "supporting_claims": [incoming["claim"]],
            },
            "confidence": 0.9,
            "rationale": "The date was never a world date for this win.",
        }

    _writer(database_engine, answer).drain(
        deployment_id=case.dep,
        subject_entity_id=case.subject,
        meter=NoopCostMeter(),
        call_key="test",
    )
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT valid_from,valid_until,valid_precision FROM observations WHERE observation_id=:id"
            ),
            {"id": fact},
        ).one()
    assert tuple(row) == (None, None, "unknown")


def test_support_move_uses_typed_names(database_engine: Engine) -> None:
    """A split names the older assertion by A-handle and expected F-handle."""
    case = WriterCase(engine=database_engine)
    _, _, original = case.first()
    _, later_app = case.stage(day=14)
    case.decide(decision={"target": {"fact_id": str(original)}})
    case.apply(app=later_app)
    case.stage(day=12)

    def answer(prompt: str, type_name: str) -> dict[str, object]:
        payload = _payload(prompt)
        later_claim = max(
            payload["claims"], key=lambda row: str(row.get("source_said_at") or "")
        )
        later = next(
            row
            for row in payload["assertions"]
            if row.get("claim") == later_claim["handle"] and not row["incoming"]
        )
        return {
            "target": "middle",
            "new_facts": [
                {"handle": "middle", "assertion": payload["incoming_assertion"]},
                {"handle": "later", "assertion": later["handle"]},
            ],
            "support_moves": [
                {
                    "assertion": later["handle"],
                    "expected_fact": later["assigned_to"],
                    "target": "later",
                }
            ],
            "confidence": 0.9,
            "rationale": "A later report was a different final.",
        }

    _writer(database_engine, answer).drain(
        deployment_id=case.dep,
        subject_entity_id=case.subject,
        meter=NoopCostMeter(),
        call_key="test",
    )
    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT observation_id,evidence_count FROM observations WHERE deployment_id=:dep"
            ),
            {"dep": case.dep},
        ).all()
    assert len(rows) == 3
    assert all(row[1] == 1 for row in rows)


def test_deleted_source_cannot_publish_translated_decision(
    database_engine: Engine,
) -> None:
    """Source erasure still wins after handle translation."""
    case = WriterCase(engine=database_engine)
    case.first()
    claim, _app = case.stage(day=12)
    prepared = case.writer.prepare(
        deployment_id=case.dep, subject_entity_id=case.subject
    )
    assert prepared is not None and prepared.decision is None
    _presentation, mapping = project_concise_inputs(snapshot=prepared.inputs)
    decision = translate_prompt_decision(
        response=PromptFactDecision.model_validate(
            {"target": "F1", "confidence": 0.9, "rationale": "Same win."}
        ),
        mapping=mapping,
    )
    with database_engine.begin() as connection:
        connection.execute(text("DELETE FROM claims WHERE claim_id=:id"), {"id": claim})
    assert not case.catalog.publish_decision(
        deployment_id=case.dep, prepared=prepared, decision=decision
    )


def test_unhydrated_window_witness_is_named_but_not_citable(
    database_engine: Engine,
) -> None:
    """A window witness absent from the claims payload cannot authorize a replacement."""
    case = WriterCase(engine=database_engine)
    _, _, fact = case.first()
    case.stage(day=12)
    ghost = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """UPDATE observations SET window_claim_ids = array_append(window_claim_ids, :ghost)
                WHERE observation_id=:id"""
            ),
            {"id": fact, "ghost": ghost},
        )
    prepared = case.writer.prepare(
        deployment_id=case.dep, subject_entity_id=case.subject
    )
    assert prepared is not None
    presentation, mapping = project_concise_inputs(snapshot=prepared.inputs)
    fact_row = presentation["facts"][0]
    assert fact_row["window_claims_not_supplied"] == ["W1"]
    assert str(ghost) not in json.dumps(presentation)
    with pytest.raises(ValueError, match="not a claim name"):
        translate_prompt_decision(
            response=PromptFactDecision.model_validate(
                {
                    "target": "F1",
                    "window": {
                        "window": {"valid_precision": "unknown"},
                        "supporting_claims": ["W1"],
                    },
                    "confidence": 0.9,
                    "rationale": "Cite a witness that was not supplied.",
                }
            ),
            mapping=mapping,
        )
