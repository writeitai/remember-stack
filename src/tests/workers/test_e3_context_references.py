"""E3 context-reference resolution uses canonical ids, not casefolded names."""

from uuid import uuid4

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from tests.workers.e3_test_doubles import _claim
from tests.workers.e3_test_doubles import _handler
from tests.workers.e3_test_doubles import _payload
from tests.workers.e3_test_doubles import RecordingFacts
from tests.workers.e3_test_doubles import RecordingResolver


def _run(*, payload: dict[str, object], resolver: RecordingResolver) -> RecordingFacts:
    """Normalize one canned observation through the real E3 handler."""
    facts = RecordingFacts()
    handler = _handler(
        provider=FakeModelProvider(generate_payload=_payload(payload)),
        resolver=resolver,
        facts=facts,
    )
    handler._normalize_claim(
        deployment_id=uuid4(),
        claim=_claim(),
        version_ids=(uuid4(),),
        meter=NoopCostMeter(),
    )
    return facts


def test_equal_names_stay_distinct_until_resolution() -> None:
    """Two 'The Open' references can resolve to different entities."""
    payload = {
        "relations": [],
        "observations": [
            {
                "subject": {"name": "Joanna"},
                "statement": "Joanna said The Open then The Open",
                "context_refs": [{"name": "The Open"}, {"name": "The Open"}],
            }
        ],
    }
    facts = _run(payload=payload, resolver=RecordingResolver())
    bindings = facts.applications.staged[0]["context_bindings"]
    assert [ordinal for ordinal, _entity, _decision in bindings] == [0, 1]
    assert bindings[0][1] != bindings[1][1]


def test_aliases_of_one_entity_keep_the_first_ordinal() -> None:
    """Resolved identity is canonical; a second alias does not abort staging."""
    tournament = uuid4()
    payload = {
        "relations": [],
        "observations": [
            {
                "subject": {"name": "Joanna"},
                "statement": "Joanna said Nate won the Riverside Cup",
                "context_refs": [
                    {"name": "the cup"},
                    {"name": "Riverside Cup"},
                    {"name": "Nate"},
                ],
            }
        ],
    }
    facts = _run(
        payload=payload,
        resolver=RecordingResolver(
            identities={"the cup": tournament, "Riverside Cup": tournament}
        ),
    )
    staged = facts.applications.staged[0]
    bindings = staged["context_bindings"]
    assert [ordinal for ordinal, entity, _decision in bindings] == [0, 2]
    assert bindings[0][1] == tournament
    assert bindings[1][1] != tournament


def test_canonical_subject_is_excluded_after_resolution() -> None:
    """A context name that resolves to the subject is dropped after resolve."""
    joanna = uuid4()
    payload = {
        "relations": [],
        "observations": [
            {
                "subject": {"name": "Joanna Smith"},
                "statement": "Joanna Smith said Joanna won",
                "context_refs": [{"name": "Joanna"}],
            }
        ],
    }
    facts = _run(
        payload=payload,
        resolver=RecordingResolver(
            identities={"Joanna Smith": joanna, "Joanna": joanna}
        ),
    )
    assert facts.applications.staged[0]["context_bindings"] == ()
    assert facts.applications.staged[0]["subject_entity_id"] == joanna


def test_overflow_still_stages_the_assertion() -> None:
    """Auxiliary extras are truncated; the assertion is still published."""
    payload = {
        "relations": [],
        "observations": [
            {
                "subject": {"name": "Joanna"},
                "statement": "Joanna listed extra referents",
                "context_refs": [{"name": f"Entity {index}"} for index in range(6)],
            }
        ],
    }
    facts = _run(payload=payload, resolver=RecordingResolver())
    published = facts.applications.published
    assert published is not None
    frozen, accepted = published
    assert accepted == (("observation", 0),)
    assert len(frozen.observations[0].context_refs) == 6
    bindings = facts.applications.staged[0]["context_bindings"]
    assert [ordinal for ordinal, _entity, _decision in bindings] == [0, 1, 2, 3]
