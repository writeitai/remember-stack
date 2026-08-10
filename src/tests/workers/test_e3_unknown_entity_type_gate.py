"""D86: unknown entity type gate — retry then drop (unit, fake provider)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.model import ClaimForNormalization
from rememberstack.model import EntityRef
from rememberstack.model import NormalizationResponse
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.model import ResolvedEntity
from rememberstack.spine.resolver import UnregisteredEntityTypeError
from rememberstack.workers.e3 import _illegal_types_in_response
from rememberstack.workers.e3 import _is_claim_soft_failure
from rememberstack.workers.e3 import _MAX_INNER_NORMALIZE_ATTEMPTS
from rememberstack.workers.e3 import E3Settings
from rememberstack.workers.e3 import NormalizeRelationsHandler


class RecordingCostMeter:
    """Capture call_keys for billing assertions."""

    def __init__(self) -> None:
        """Start with an empty record list."""
        self.records: list[tuple[str, str | None]] = []

    def record(
        self, *, call_key: str, tier: str | None, usage: ProviderCallUsage
    ) -> None:
        """Append one metered call."""
        del usage
        self.records.append((call_key, tier))


class RecordingResolver:
    """Resolver that records resolve calls and never mints illegal types."""

    def __init__(self) -> None:
        """Empty call log."""
        self.calls: list[EntityRef] = []

    def resolve(
        self,
        *,
        deployment_id: object,
        reference: EntityRef,
        claim: ClaimForNormalization,
        meter: object = None,
        call_key: str = "resolve",
    ) -> ResolvedEntity:
        """Record the reference and return a synthetic entity id."""
        del deployment_id, claim, meter, call_key
        self.calls.append(reference)
        return ResolvedEntity(
            entity_id=uuid4(), created=True, entity_type=reference.type
        )


class RecordingFacts:
    """Minimal fact catalog for normalize unit paths that touch predicates."""

    def __init__(self, *, predicates: dict[str, str | None] | None = None) -> None:
        """Bind optional predicate map."""
        self.predicates = predicates or {"related_to": None}
        self.other_ensured: list[str] = []
        self.upserts: list[dict[str, Any]] = []

    def ensure_other_predicate(self, *, deployment_id: object, predicate: str) -> None:
        """Record other: registration."""
        del deployment_id
        self.other_ensured.append(predicate)

    def upsert_relation(
        self,
        *,
        deployment_id: object,
        subject_entity_id: object,
        predicate: str,
        object_entity_id: object,
        claim_id: object,
        doc_id: object,
        normalizer_version: str,
    ) -> Any:
        """Record upsert and return a created stub."""
        del deployment_id, subject_entity_id, object_entity_id, claim_id, doc_id
        self.upserts.append(
            {"predicate": predicate, "normalizer_version": normalizer_version}
        )

        class _Upserted:
            created = True
            relation_id = uuid4()

        return _Upserted()


def _claim() -> ClaimForNormalization:
    """One claim stub for unit normalize."""
    return ClaimForNormalization(
        claim_id=uuid4(),
        doc_id=uuid4(),
        chunk_id=uuid4(),
        claim_text="The caching process stores hot keys.",
        is_attributed=False,
    )


def _handler(
    *,
    provider: FakeModelProvider,
    resolver: object | None = None,
    facts: object | None = None,
) -> NormalizeRelationsHandler:
    """Handler with generate path wired; catalogs optional for drop tests."""
    return NormalizeRelationsHandler(
        claim_catalog=None,  # type: ignore[arg-type]
        chunk_catalog=None,  # type: ignore[arg-type]
        registry=None,  # type: ignore[arg-type]
        resolver=resolver,  # type: ignore[arg-type]
        facts=facts,  # type: ignore[arg-type]
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
        del prompt, type_name
        n["i"] += 1
        return illegal if n["i"] == 1 else legal

    provider = FakeModelProvider(generate_router=router)
    handler = _handler(provider=provider)
    meter = RecordingCostMeter()
    claim = _claim()
    out = handler._generate_normalize_response(
        claim=claim,
        base_prompt="BASE",
        allowed_types=frozenset({"Concept", "Person"}),
        types_csv="Concept, Person",
        meter=meter,
    )
    assert out.observations[0].subject.type == "Concept"
    assert len(provider.generated_prompts) == 2
    assert "TYPE GATE RETRY" in provider.generated_prompts[1]
    assert _MAX_INNER_NORMALIZE_ATTEMPTS == 2
    assert meter.records == [
        (f"normalize:{claim.claim_id}:a1", "normalize"),
        (f"normalize:{claim.claim_id}:a2", "normalize"),
    ]


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
    meter = RecordingCostMeter()
    claim = _claim()
    out = handler._generate_normalize_response(
        claim=claim,
        base_prompt="BASE",
        allowed_types=frozenset({"Concept"}),
        types_csv="Concept",
        meter=meter,
    )
    assert out.observations[0].subject.type == "Process"
    assert len(provider.generated_prompts) == 2
    illegal_set = _illegal_types_in_response(
        response=out, allowed_types=frozenset({"Concept"})
    )
    assert "Process" in illegal_set
    assert [key for key, _ in meter.records] == [
        f"normalize:{claim.claim_id}:a1",
        f"normalize:{claim.claim_id}:a2",
    ]


def test_normalize_claim_drops_illegal_observation_without_resolve() -> None:
    """Persistent Process observation: two generates, zero resolve calls."""
    illegal = {
        "observations": [
            {
                "subject": {"name": "caching process", "type": "Process"},
                "statement": "stores hot keys",
            }
        ]
    }
    provider = FakeModelProvider(generate_payload=illegal)
    resolver = RecordingResolver()
    facts = RecordingFacts()
    handler = _handler(provider=provider, resolver=resolver, facts=facts)
    observations: dict = {}
    created: list[str] = []
    handler._normalize_claim(
        created_relations=created,
        observations_by_entity=observations,
        deployment_id=uuid4(),
        claim=_claim(),
        predicates={"related_to": None},
        prompt_lines="related_to",
        signatures={},
        type_parents={"Concept": None, "Person": None},
        allowed_types=frozenset({"Concept", "Person"}),
        meter=NoopCostMeter(),
    )
    assert resolver.calls == []
    assert observations == {}
    assert created == []
    assert len(provider.generated_prompts) == 2


def test_normalize_claim_drops_illegal_relation_before_other_predicate() -> None:
    """Illegal relation endpoints drop before ensure_other_predicate / resolve."""
    illegal = {
        "relations": [
            {
                "subject": {"name": "Acme", "type": "Process"},
                "predicate": "other:sponsors",
                "object": {"name": "Bob", "type": "Person"},
            }
        ]
    }
    provider = FakeModelProvider(generate_payload=illegal)
    resolver = RecordingResolver()
    facts = RecordingFacts()
    handler = _handler(provider=provider, resolver=resolver, facts=facts)
    handler._normalize_claim(
        created_relations=[],
        observations_by_entity={},
        deployment_id=uuid4(),
        claim=_claim(),
        predicates={"related_to": None},
        prompt_lines="related_to",
        signatures={},
        type_parents={"Person": None, "Organization": None},
        allowed_types=frozenset({"Person", "Organization"}),
        meter=NoopCostMeter(),
    )
    assert facts.other_ensured == []
    assert resolver.calls == []
    assert facts.upserts == []


def test_normalize_claim_keeps_legal_sibling_observation() -> None:
    """Mixed final response: legal observation resolves; illegal drops."""
    mixed = {
        "observations": [
            {"subject": {"name": "caching", "type": "Process"}, "statement": "illegal"},
            {"subject": {"name": "cache", "type": "Concept"}, "statement": "legal"},
        ]
    }
    provider = FakeModelProvider(generate_payload=mixed)
    resolver = RecordingResolver()
    handler = _handler(provider=provider, resolver=resolver, facts=RecordingFacts())
    observations: dict = {}
    handler._normalize_claim(
        created_relations=[],
        observations_by_entity=observations,
        deployment_id=uuid4(),
        claim=_claim(),
        predicates={},
        prompt_lines="",
        signatures={},
        type_parents={"Concept": None},
        allowed_types=frozenset({"Concept"}),
        meter=NoopCostMeter(),
    )
    assert len(resolver.calls) == 1
    assert resolver.calls[0].type == "Concept"
    assert len(observations) == 1
    only = next(iter(observations.values()))
    assert only[0].statement == "legal"


def test_soft_failure_is_only_invalid_response() -> None:
    """Systemic errors are not claim-soft; content poison is."""
    usage = ProviderCallUsage(
        model_name="m", tokens_in=1, tokens_out=0, cost_usd=Decimal(0), latency_ms=0
    )
    assert _is_claim_soft_failure(
        exception=ProviderInvalidResponseError("bad json", usage=usage)
    )
    assert not _is_claim_soft_failure(exception=RuntimeError("db down"))
    assert not _is_claim_soft_failure(
        exception=UnregisteredEntityTypeError("Process not registered")
    )


def test_generate_failure_records_attempt_failure_key() -> None:
    """Usage-bearing invalid response meters normalize:{id}:a1:failure."""
    usage = ProviderCallUsage(
        model_name="m", tokens_in=3, tokens_out=0, cost_usd=Decimal(0), latency_ms=1
    )

    class RaisingProvider:
        def generate(self, *, request: object, response_type: object) -> object:
            del request, response_type
            raise ProviderInvalidResponseError("schema fail", usage=usage)

    handler = NormalizeRelationsHandler(
        claim_catalog=None,  # type: ignore[arg-type]
        chunk_catalog=None,  # type: ignore[arg-type]
        registry=None,  # type: ignore[arg-type]
        resolver=None,  # type: ignore[arg-type]
        facts=None,  # type: ignore[arg-type]
        observation_adjudicator=None,  # type: ignore[arg-type]
        model_provider=RaisingProvider(),  # type: ignore[arg-type]
        settings=E3Settings(normalize_model="test-model"),
        chunker_version="test",
    )
    meter = RecordingCostMeter()
    claim = _claim()
    with pytest.raises(ProviderInvalidResponseError):
        handler._generate_normalize_response(
            claim=claim,
            base_prompt="BASE",
            allowed_types=frozenset({"Concept"}),
            types_csv="Concept",
            meter=meter,
        )
    assert meter.records == [
        (f"normalize:{claim.claim_id}:a1:failure", "normalize_failed")
    ]


def test_unregistered_entity_type_error_message() -> None:
    """Typed mint refusal carries the illegal type (unit surface)."""
    error = UnregisteredEntityTypeError(
        "entity type 'Process' is not registered for deployment x"
    )
    assert "Process" in str(error)
