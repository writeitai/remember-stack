"""WP-I.1: E3 drops bare-head-noun endpoints before resolve."""

from uuid import uuid4

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.model import ClaimForNormalization
from rememberstack.workers.e3 import _NORMALIZE_PROMPT
from tests.workers.test_e3_unknown_entity_type_gate import _claim
from tests.workers.test_e3_unknown_entity_type_gate import _handler
from tests.workers.test_e3_unknown_entity_type_gate import _payload
from tests.workers.test_e3_unknown_entity_type_gate import RecordingFacts
from tests.workers.test_e3_unknown_entity_type_gate import RecordingResolver


def _claim_with(*, claim_text: str) -> ClaimForNormalization:
    """A claim stub whose text can ground EntityRef.surface."""
    base = _claim()
    return base.model_copy(update={"claim_text": claim_text})


def test_prompt_has_no_registry_types() -> None:
    """D96: extract prompt does not list entity types."""
    assert "REGISTRY TYPES" not in _NORMALIZE_PROMPT
    assert "Do not emit a type field" in _NORMALIZE_PROMPT


def test_prompt_forbids_bare_head_nouns() -> None:
    """The normalizer prompt states the Graphiti-style eligibility rule."""
    assert "Do NOT emit bare head nouns" in _NORMALIZE_PROMPT
    assert "FIFA 23" in _NORMALIZE_PROMPT


def test_normalize_drops_game_relation_without_resolve() -> None:
    """Legal types still drop when an endpoint is the noun ``game``."""
    payload = {
        "relations": [
            {
                "subject": {"name": "James", "type": "Person"},
                "predicate": "related_to",
                "object": {"name": "game", "type": "Product"},
            }
        ]
    }
    provider = FakeModelProvider(generate_payload=_payload(payload))
    resolver = RecordingResolver()
    facts = RecordingFacts(predicates={"related_to": None})
    handler = _handler(provider=provider, resolver=resolver, facts=facts)
    handler._normalize_claim(
        created_relations=[],
        observations_by_entity={},
        staged_observations=None,
        deployment_id=uuid4(),
        claim=_claim(),
        predicates={"related_to": None},
        prompt_lines="related_to",
        meter=NoopCostMeter(),
    )
    assert resolver.calls == []
    assert facts.upserts == []


def test_normalize_resolves_fifa_23() -> None:
    """A qualified product name is not treated as a bare noun."""
    payload = {
        "relations": [
            {
                "subject": {"name": "James", "type": "Person"},
                "predicate": "related_to",
                "object": {"name": "FIFA 23", "type": "Product"},
            }
        ]
    }
    provider = FakeModelProvider(generate_payload=_payload(payload))
    resolver = RecordingResolver()
    facts = RecordingFacts(predicates={"related_to": None})
    handler = _handler(provider=provider, resolver=resolver, facts=facts)
    created: list[str] = []
    handler._normalize_claim(
        created_relations=created,
        observations_by_entity={},
        staged_observations=None,
        deployment_id=uuid4(),
        claim=_claim_with(claim_text="James played FIFA 23 after dinner."),
        predicates={"related_to": None},
        prompt_lines="related_to",
        meter=NoopCostMeter(),
    )
    assert [ref.name for ref in resolver.calls] == ["James", "FIFA 23"]
    assert len(facts.upserts) == 1


def test_normalize_drops_game_observation_without_resolve() -> None:
    """Bare-noun observation subjects are dropped the same way as relations."""
    payload = {
        "observations": [
            {
                "subject": {"name": "game", "type": "Product"},
                "statement": "the game is fun",
            }
        ]
    }
    provider = FakeModelProvider(generate_payload=_payload(payload))
    resolver = RecordingResolver()
    facts = RecordingFacts(predicates={"related_to": None})
    handler = _handler(provider=provider, resolver=resolver, facts=facts)
    handler._normalize_claim(
        created_relations=[],
        observations_by_entity={},
        staged_observations=None,
        deployment_id=uuid4(),
        claim=_claim(),
        predicates={"related_to": None},
        prompt_lines="related_to",
        meter=NoopCostMeter(),
    )
    assert resolver.calls == []


def test_works_for_between_people_is_not_dropped() -> None:
    """D96: works_for is not gated on Organization; two people persist."""
    payload = {
        "relations": [
            {
                "subject": {"name": "Alice", "type": "Person"},
                "predicate": "works_for",
                "object": {"name": "Me", "type": "Person"},
            }
        ]
    }
    provider = FakeModelProvider(generate_payload=_payload(payload))
    resolver = RecordingResolver()
    facts = RecordingFacts(predicates={"works_for": None})
    handler = _handler(provider=provider, resolver=resolver, facts=facts)
    created: list[str] = []
    handler._normalize_claim(
        created_relations=created,
        observations_by_entity={},
        staged_observations=None,
        deployment_id=uuid4(),
        claim=_claim_with(claim_text="Alice works for me."),
        predicates={"works_for": None},
        prompt_lines="works_for",
        meter=NoopCostMeter(),
    )
    assert [ref.name for ref in resolver.calls] == ["Alice", "Me"]
    assert facts.upserts[0]["predicate"] == "works_for"


def test_normalize_passes_source_surface_to_resolve() -> None:
    """Claim spelling App rides EntityRef.surface into resolve."""
    payload = {
        "relations": [
            {
                "subject": {"name": "James", "type": "Person"},
                "predicate": "related_to",
                "object": {"name": "Application", "type": "Product", "surface": "App"},
            }
        ]
    }
    provider = FakeModelProvider(generate_payload=_payload(payload))
    resolver = RecordingResolver()
    facts = RecordingFacts(predicates={"related_to": None})
    handler = _handler(provider=provider, resolver=resolver, facts=facts)
    created: list[str] = []
    handler._normalize_claim(
        created_relations=created,
        observations_by_entity={},
        staged_observations=None,
        deployment_id=uuid4(),
        claim=_claim_with(claim_text="James opened the App after dinner."),
        predicates={"related_to": None},
        prompt_lines="related_to",
        meter=NoopCostMeter(),
    )
    assert resolver.calls[1].name == "Application"
    assert resolver.calls[1].surface == "App"
    assert resolver.calls[1].mention_surface() == "App"
    assert len(facts.upserts) == 1
