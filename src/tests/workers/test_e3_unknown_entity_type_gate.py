"""D86: unknown entity type gate — retry then drop (unit, fake provider)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

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
from rememberstack.workers.e3 import _MAX_INNER_NORMALIZE_ATTEMPTS
from rememberstack.workers.e3 import E3Settings
from rememberstack.workers.e3 import NormalizeRelationsHandler


class RecordingCostMeter:
    """Capture call_keys for billing assertions."""

    def __init__(self) -> None:
        """Start with an empty record list."""
        self.records: list[tuple[str, str | None]] = []

    def record(
        self,
        *,
        call_key: str,
        tier: str | None,
        usage: ProviderCallUsage,
        outcome: str = "ok",
    ) -> None:
        """Append one metered call."""
        del usage, outcome
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
        deployment_id=uuid4(),
        doc_id=uuid4(),
        chunk_id=uuid4(),
        claim_text="The caching process stores hot keys.",
        is_attributed=False,
        extractor_version="e2-test",
    )


def _payload(data: dict[str, Any]) -> dict[str, object]:
    """Widen nested canned dicts for FakeModelProvider typing."""
    return data


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
        return _payload(illegal if n["i"] == 1 else legal)

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
    assert out is not None
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
    provider = FakeModelProvider(generate_payload=_payload(illegal))
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
    assert out is not None
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
    provider = FakeModelProvider(generate_payload=_payload(illegal))
    resolver = RecordingResolver()
    facts = RecordingFacts()
    handler = _handler(provider=provider, resolver=resolver, facts=facts)
    observations: dict = {}
    created: list[str] = []
    handler._normalize_claim(
        created_relations=created,
        observations_by_entity=observations,
        staged_observations=None,
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
    provider = FakeModelProvider(generate_payload=_payload(illegal))
    resolver = RecordingResolver()
    facts = RecordingFacts()
    handler = _handler(provider=provider, resolver=resolver, facts=facts)
    handler._normalize_claim(
        created_relations=[],
        observations_by_entity={},
        staged_observations=None,
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
    provider = FakeModelProvider(generate_payload=_payload(mixed))
    resolver = RecordingResolver()
    handler = _handler(provider=provider, resolver=resolver, facts=RecordingFacts())
    observations: dict = {}
    handler._normalize_claim(
        created_relations=[],
        observations_by_entity=observations,
        staged_observations=None,
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


def test_generate_soft_poison_returns_none_and_meters() -> None:
    """Generate content poison: None return + a1:failure (not raised)."""
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
    out = handler._generate_normalize_response(
        claim=claim,
        base_prompt="BASE",
        allowed_types=frozenset({"Concept"}),
        types_csv="Concept",
        meter=meter,
    )
    assert out is None
    assert meter.records == [
        (f"normalize:{claim.claim_id}:a1:failure", "normalize_failed_response")
    ]


def test_normalize_claim_soft_skip_does_not_resolve() -> None:
    """Soft generate poison returns soft_skipped and never calls resolve."""
    usage = ProviderCallUsage(
        model_name="m", tokens_in=1, tokens_out=0, cost_usd=Decimal(0), latency_ms=0
    )

    class RaisingProvider:
        def generate(self, *, request: object, response_type: object) -> object:
            del request, response_type
            raise ProviderInvalidResponseError("schema fail", usage=usage)

    resolver = RecordingResolver()
    handler = NormalizeRelationsHandler(
        claim_catalog=None,  # type: ignore[arg-type]
        chunk_catalog=None,  # type: ignore[arg-type]
        registry=None,  # type: ignore[arg-type]
        resolver=resolver,  # type: ignore[arg-type]
        facts=RecordingFacts(),  # type: ignore[arg-type]
        observation_adjudicator=None,  # type: ignore[arg-type]
        model_provider=RaisingProvider(),  # type: ignore[arg-type]
        settings=E3Settings(normalize_model="test-model"),
        chunker_version="test",
    )
    soft = handler._normalize_claim(
        created_relations=[],
        observations_by_entity={},
        staged_observations=None,
        deployment_id=uuid4(),
        claim=_claim(),
        predicates={},
        prompt_lines="",
        signatures={},
        type_parents={"Concept": None},
        allowed_types=frozenset({"Concept"}),
        meter=NoopCostMeter(),
    )
    assert soft is True
    assert resolver.calls == []


def test_resolver_invalid_response_is_not_soft() -> None:
    """ProviderInvalidResponseError from resolve re-raises (not claim-soft)."""
    legal = {
        "observations": [
            {"subject": {"name": "cache", "type": "Concept"}, "statement": "legal"}
        ]
    }
    usage = ProviderCallUsage(
        model_name="m", tokens_in=2, tokens_out=0, cost_usd=Decimal(0), latency_ms=0
    )

    class RaisingResolver:
        def resolve(self, **kwargs: object) -> ResolvedEntity:
            del kwargs
            raise ProviderInvalidResponseError("t4 schema fail", usage=usage)

    handler = _handler(
        provider=FakeModelProvider(generate_payload=_payload(legal)),
        resolver=RaisingResolver(),
        facts=RecordingFacts(),
    )
    with pytest.raises(ProviderInvalidResponseError):
        handler._normalize_claim(
            created_relations=[],
            observations_by_entity={},
            staged_observations=None,
            deployment_id=uuid4(),
            claim=_claim(),
            predicates={},
            prompt_lines="",
            signatures={},
            type_parents={"Concept": None},
            allowed_types=frozenset({"Concept"}),
            meter=NoopCostMeter(),
        )


def test_systemic_provider_error_not_metered_in_generate() -> None:
    """Escaping ProviderCallError must not write aN:failure (Worker bills it)."""
    from rememberstack.model import ProviderCallError

    usage = ProviderCallUsage(
        model_name="m", tokens_in=3, tokens_out=0, cost_usd=Decimal(0), latency_ms=1
    )

    class RaisingProvider:
        def generate(self, *, request: object, response_type: object) -> object:
            del request, response_type
            raise ProviderCallError("transport", usage=usage)

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
    with pytest.raises(ProviderCallError):
        handler._generate_normalize_response(
            claim=_claim(),
            base_prompt="BASE",
            allowed_types=frozenset({"Concept"}),
            types_csv="Concept",
            meter=meter,
        )
    assert meter.records == []


def test_mint_refuses_unregistered_type_before_insert() -> None:
    """CascadeResolver._mint raises typed error when entity_types lookup misses."""
    from rememberstack.spine.resolver import CascadeResolver

    class _Result:
        def one_or_none(self) -> None:
            return None

    class _Conn:
        def execute(self, statement: object, params: object = None) -> _Result:
            del statement, params
            return _Result()

    resolver = CascadeResolver.__new__(CascadeResolver)
    resolver._last_rejection = None
    with pytest.raises(UnregisteredEntityTypeError, match="Process"):
        CascadeResolver._mint(
            resolver,
            connection=_Conn(),  # type: ignore[arg-type]
            deployment_id=uuid4(),
            reference=EntityRef(name="caching", type="Process"),
            claim=_claim(),
            lemma="caching",
            considered=(),
            meter=None,
            call_key="test",
        )


def test_entity_type_fk_violation_classifier() -> None:
    """Only entity_types-related IntegrityError maps to the FK alarm."""
    from rememberstack.workers.e3 import _is_entity_type_fk_violation

    entity_fk = IntegrityError(
        "INSERT",
        {},
        Exception(
            'insert or update on table "entities" violates foreign key '
            'constraint "entities_deployment_id_type_fkey" on entity_types'
        ),
    )
    other = IntegrityError("INSERT", {}, Exception("unique violation on relations"))
    assert _is_entity_type_fk_violation(error=entity_fk)
    assert not _is_entity_type_fk_violation(error=other)
