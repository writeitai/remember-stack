"""Rejection-aware retry proofs for fact adjudication (Fix B).

A deterministic translator rejection at temperature 0 must not burn every
worker attempt with identical bytes: ``drain`` retries once, in-delivery,
against the same prepared attempt, with a structural note appended outside
the inputs. The retry receipt carries a distinct call key so the ledger
never drops a billed call.
"""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderCallUsage
from rememberstack.spine.fact_adjudication import _FACT_PROMPT
from rememberstack.spine.fact_adjudication import canonical_json
from rememberstack.spine.fact_adjudication import FactAdjudicationSettings
from rememberstack.spine.fact_adjudication import FactAdjudicator
from rememberstack.spine.fact_adjudication import project_concise_inputs
from rememberstack.spine.fact_applications import ApplicationInputChanged
from rememberstack.spine.settings import load_database_settings
from tests.fact_application_support import WriterCase


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Use the explicitly configured integration database at the real schema head."""
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


class _RecordingMeter:
    """Keep every provider receipt instead of persisting it."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self.records: list[tuple[str, str, str]] = []

    def record(
        self,
        *,
        call_key: str,
        tier: str | None,
        usage: ProviderCallUsage,
        outcome: str = "ok",
    ) -> None:
        """Append one test-only call record."""
        self.records.append((call_key, tier or "", outcome))


def _setup(
    engine: Engine, router: object
) -> tuple[WriterCase, FactAdjudicator, str, str, UUID]:
    """Stage two assertions and return case, adjudicator, names, and day-12 app."""
    case = WriterCase(engine=engine)
    case.first()
    _, day12_app = case.stage(day=12)
    adjudicator = FactAdjudicator(
        engine=engine,
        model_provider=FakeModelProvider(generate_router=router),
        settings=FactAdjudicationSettings(),
    )
    prepared = adjudicator.prepare(
        deployment_id=case.dep, subject_entity_id=case.subject
    )
    assert prepared is not None
    _, mapping = project_concise_inputs(snapshot=prepared.inputs)
    fact_name = sorted(mapping.facts)[0]
    return case, adjudicator, fact_name, mapping.incoming_assertion, day12_app


def test_reject_then_accept_records_two_receipts(database_engine: Engine) -> None:
    """One deterministic rejection retries in-delivery; both calls are metered."""
    calls: list[str] = []

    def router(prompt: str, _type_name: str) -> dict[str, object]:
        """Answer badly once (undeclared N-target), then accept."""
        calls.append(prompt)
        if len(calls) == 1:
            return {
                "target": "N9",
                "new_facts": [],
                "confidence": 0.9,
                "rationale": "test",
            }
        return {"target": calls_fact[0], "confidence": 0.9, "rationale": "test"}

    calls_fact: list[str] = []
    case, adjudicator, fact_name, _, _ = _setup(database_engine, router)
    calls_fact.append(fact_name)
    meter = _RecordingMeter()
    facts = adjudicator.drain(
        deployment_id=case.dep,
        subject_entity_id=case.subject,
        meter=meter,
        call_key="test",
    )
    assert len(facts) == 1
    assert len(calls) == 2
    first, second = calls
    assert second.startswith(first) and len(second) > len(first)
    assert "N9" not in second
    assert len(meter.records) == 2
    base, retry = meter.records
    assert ":translator-retry1" not in base[0]
    assert retry[0] == base[0] + ":translator-retry1"
    assert all(record[2] == "ok" for record in meter.records)


def test_double_rejection_raises_with_both_receipts(database_engine: Engine) -> None:
    """A second identical rejection raises; the retry was still metered."""
    calls: list[str] = []

    def router(prompt: str, _type_name: str) -> dict[str, object]:
        """Always answer with an undeclared N-target."""
        calls.append(prompt)
        return {"target": "N9", "new_facts": [], "confidence": 0.9, "rationale": "test"}

    case, adjudicator, _, _, _ = _setup(database_engine, router)
    meter = _RecordingMeter()
    with pytest.raises(ApplicationInputChanged):
        adjudicator.drain(
            deployment_id=case.dep,
            subject_entity_id=case.subject,
            meter=meter,
            call_key="test",
        )
    assert len(calls) == 2
    assert len(meter.records) == 2
    assert meter.records[1][0] == meter.records[0][0] + ":translator-retry1"


def test_first_answer_accepted_has_no_note(database_engine: Engine) -> None:
    """An accepted first answer is byte-identical to today's prompt; one receipt."""

    def router(prompt: str, _type_name: str) -> dict[str, object]:
        """Target the stored fact immediately."""
        calls.append(prompt)
        return {"target": calls_fact[0], "confidence": 0.9, "rationale": "test"}

    calls: list[str] = []
    calls_fact: list[str] = []
    case, adjudicator, fact_name, _, _ = _setup(database_engine, router)
    calls_fact.append(fact_name)
    prepared = adjudicator.prepare(
        deployment_id=case.dep, subject_entity_id=case.subject
    )
    assert prepared is not None
    presentation, _ = project_concise_inputs(snapshot=prepared.inputs)
    expected = _FACT_PROMPT.format(inputs=canonical_json(presentation))
    meter = _RecordingMeter()
    adjudicator.drain(
        deployment_id=case.dep,
        subject_entity_id=case.subject,
        meter=meter,
        call_key="test",
    )
    assert calls == [expected]
    assert len(meter.records) == 1
    assert meter.records[0][2] == "ok"


def test_provider_error_never_retries(database_engine: Engine) -> None:
    """A transport failure raises at once; no second call, no note."""
    calls: list[str] = []

    def router(prompt: str, _type_name: str) -> dict[str, object]:
        """Fail the only call like a dropped stream."""
        calls.append(prompt)
        raise ProviderCallError("test drop")

    case, adjudicator, _, _, _ = _setup(database_engine, router)
    meter = _RecordingMeter()
    with pytest.raises(ProviderCallError):
        adjudicator.drain(
            deployment_id=case.dep,
            subject_entity_id=case.subject,
            meter=meter,
            call_key="test",
        )
    assert len(calls) == 1
    assert meter.records == []


def test_decision_published_once_after_retry(database_engine: Engine) -> None:
    """The retry publishes through the same CAS identity; no duplicate row."""
    seen: list[str] = []

    def router(prompt: str, _type_name: str) -> dict[str, object]:
        """Answer badly once, then accept."""
        seen.append(prompt)
        if len(seen) == 1:
            return {
                "target": "N9",
                "new_facts": [],
                "confidence": 0.9,
                "rationale": "test",
            }
        return {"target": seen_fact[0], "confidence": 0.9, "rationale": "test"}

    seen_fact: list[str] = []
    case, adjudicator, fact_name, _, day12_app = _setup(database_engine, router)
    seen_fact.append(fact_name)
    facts = adjudicator.drain(
        deployment_id=case.dep,
        subject_entity_id=case.subject,
        meter=_RecordingMeter(),
        call_key="test",
    )
    assert len(facts) == 1
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT applied_at IS NOT NULL AS applied, decision IS NULL"
                    " AS consumed FROM fact_applications"
                    " WHERE deployment_id=:dep AND application_id=:id"
                ),
                {"dep": case.dep, "id": day12_app},
            )
            .mappings()
            .one()
        )
        # One publication through one CAS identity: applied once, and the
        # decision payload consumed (apply clears it; the result endures).
        assert row["applied"] is True
        assert row["consumed"] is True
