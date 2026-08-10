"""D86: unknown entity type gate — retry then drop (unit, fake provider)."""

from __future__ import annotations

from uuid import uuid4

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.model import ClaimForNormalization
from rememberstack.model import NormalizationResponse
from rememberstack.workers.e3 import _illegal_types_in_response
from rememberstack.workers.e3 import _MAX_INNER_NORMALIZE_ATTEMPTS
from rememberstack.workers.e3 import E3Settings
from rememberstack.workers.e3 import NormalizeRelationsHandler


def _claim() -> ClaimForNormalization:
    """One claim stub for unit normalize."""
    return ClaimForNormalization(
        claim_id=uuid4(),
        doc_id=uuid4(),
        chunk_id=uuid4(),
        claim_text="The caching process stores hot keys.",
        is_attributed=False,
    )


def _handler(*, provider: FakeModelProvider) -> NormalizeRelationsHandler:
    """Handler with only generate path wired (catalogs unused)."""
    return NormalizeRelationsHandler(
        claim_catalog=None,  # type: ignore[arg-type]
        chunk_catalog=None,  # type: ignore[arg-type]
        registry=None,  # type: ignore[arg-type]
        resolver=None,  # type: ignore[arg-type]
        facts=None,  # type: ignore[arg-type]
        observation_adjudicator=None,  # type: ignore[arg-type]
        model_provider=provider,
        settings=E3Settings(normalize_model="test-model"),
        chunker_version="test",
    )


def test_illegal_types_detects_observation_process() -> None:
    """Observation subject Process is illegal against core-like set."""
    response = NormalizationResponse.model_validate(
        {
            "relations": [],
            "observations": [
                {
                    "subject": {"name": "caching process", "type": "Process"},
                    "statement": "caching process stores hot keys",
                }
            ],
        }
    )
    illegal = _illegal_types_in_response(
        response=response, allowed_types=frozenset({"Person", "Concept", "Product"})
    )
    assert illegal == frozenset({"Process"})


def test_generate_retries_then_returns_legal_response() -> None:
    """First response illegal → second legal; two prompts; final is legal."""
    illegal = {
        "observations": [
            {
                "subject": {"name": "caching process", "type": "Process"},
                "statement": "stores hot keys",
            }
        ]
    }
    legal = {
        "observations": [
            {
                "subject": {"name": "caching process", "type": "Concept"},
                "statement": "stores hot keys",
            }
        ]
    }
    n = {"i": 0}

    def router(prompt: str, type_name: str) -> dict[str, object]:
        n["i"] += 1
        return illegal if n["i"] == 1 else legal

    provider = FakeModelProvider(generate_router=router)
    handler = _handler(provider=provider)
    out = handler._generate_normalize_response(
        claim=_claim(),
        base_prompt="BASE",
        allowed_types=frozenset({"Concept", "Person"}),
        types_csv="Concept, Person",
        meter=NoopCostMeter(),
    )
    assert out.observations[0].subject.type == "Concept"
    assert len(provider.generated_prompts) == 2
    assert "TYPE GATE RETRY" in provider.generated_prompts[1]
    assert _MAX_INNER_NORMALIZE_ATTEMPTS == 2


def test_generate_exhausts_retry_returns_last_illegal() -> None:
    """Two illegal responses → return last; caller drops assertions."""
    illegal = {
        "observations": [
            {
                "subject": {"name": "caching process", "type": "Process"},
                "statement": "stores hot keys",
            }
        ]
    }
    provider = FakeModelProvider(generate_payload=illegal)
    handler = _handler(provider=provider)
    out = handler._generate_normalize_response(
        claim=_claim(),
        base_prompt="BASE",
        allowed_types=frozenset({"Concept"}),
        types_csv="Concept",
        meter=NoopCostMeter(),
    )
    assert out.observations[0].subject.type == "Process"
    assert len(provider.generated_prompts) == 2
    illegal_set = _illegal_types_in_response(
        response=out, allowed_types=frozenset({"Concept"})
    )
    assert "Process" in illegal_set
