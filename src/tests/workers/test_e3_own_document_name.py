"""D134: E3 never mints or resolves an entity from a document's own inserted name.

A claim whose text carries the document's name in place of a self-reference
records that name's span. The one reference whose surface is exactly the
text at the span is skipped; everything else resolves as before.
"""

from typing import Any
from uuid import uuid4

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.model import ClaimForNormalization
from rememberstack.model.relations import EntityRef
from rememberstack.spine.entity_eligibility import own_document_name_slot
from tests.workers.e3_test_doubles import _claim
from tests.workers.e3_test_doubles import _handler
from tests.workers.e3_test_doubles import _payload
from tests.workers.e3_test_doubles import RecordingFacts
from tests.workers.e3_test_doubles import RecordingResolver

_NAME = "Audit_2025.pdf"
_SELF_CLAIM = f"The report {_NAME} summarizes the 2025 audit findings by Acme."


def _claim_with(*, claim_text: str, own_name: str | None) -> ClaimForNormalization:
    """A claim whose recorded span covers the first occurrence of ``own_name``."""
    update: dict[str, object] = {"claim_text": claim_text}
    if own_name is not None:
        start = claim_text.index(own_name)
        update["own_document_name_start"] = start
        update["own_document_name_end"] = start + len(own_name)
    return _claim().model_copy(update=update)


def _normalize(
    *, claim: ClaimForNormalization, payload: dict[str, Any]
) -> tuple[RecordingResolver, RecordingFacts]:
    resolver = RecordingResolver()
    facts = RecordingFacts(predicates={"authored": None, "audited": None})
    handler = _handler(
        provider=FakeModelProvider(generate_payload=_payload(payload)),
        resolver=resolver,
        facts=facts,
    )
    handler._normalize_claim(
        deployment_id=uuid4(),
        claim=claim,
        version_ids=(uuid4(),),
        meter=NoopCostMeter(),
    )
    return resolver, facts


def test_claim_exposes_the_text_at_its_span() -> None:
    claim = _claim_with(claim_text=_SELF_CLAIM, own_name=_NAME)
    assert claim.own_document_name() == _NAME
    assert _claim().own_document_name() is None


def test_observation_about_the_document_is_not_minted() -> None:
    """The subject at the span is the document: no resolve, no staged output."""
    resolver, facts = _normalize(
        claim=_claim_with(claim_text=_SELF_CLAIM, own_name=_NAME),
        payload={
            "observations": [
                {
                    "subject": {"name": _NAME},
                    "statement": "summarizes the 2025 audit findings",
                }
            ]
        },
    )
    assert resolver.calls == []
    assert facts.applications.staged == []


def test_relation_with_the_document_as_object_is_dropped() -> None:
    """Only the own-name assertion is dropped; the other one still resolves."""
    resolver, facts = _normalize(
        claim=_claim_with(claim_text=_SELF_CLAIM, own_name=_NAME),
        payload={
            "relations": [
                {
                    "subject": {"name": "Acme"},
                    "predicate": "authored",
                    "object": {"name": "Audit 2025", "surface": _NAME},
                }
            ],
            "observations": [
                {"subject": {"name": "Acme"}, "statement": "found audit issues"}
            ],
        },
    )
    assert [ref.name for ref in resolver.calls] == ["Acme"]
    assert [row["kind"] for row in facts.applications.staged] == ["observation"]


def test_own_name_context_reference_is_omitted() -> None:
    """A context reference at the span is left out; the assertion still stages."""
    resolver, facts = _normalize(
        claim=_claim_with(claim_text=_SELF_CLAIM, own_name=_NAME),
        payload={
            "observations": [
                {
                    "subject": {"name": "Acme"},
                    "statement": "had 2025 audit findings",
                    "context_refs": [{"name": _NAME}, {"name": "EU region"}],
                }
            ]
        },
    )
    assert [ref.name for ref in resolver.calls] == ["Acme", "EU region"]
    (staged,) = facts.applications.staged
    assert [binding[0] for binding in staged["context_bindings"]] == [1]


def test_person_named_like_the_title_resolves_in_an_unmarked_claim() -> None:
    """Without a span nothing is skipped: Alice the person still resolves."""
    resolver, facts = _normalize(
        claim=_claim_with(claim_text="Alice approved the budget.", own_name=None),
        payload={
            "observations": [
                {"subject": {"name": "Alice"}, "statement": "approved the budget"}
            ]
        },
    )
    assert [ref.name for ref in resolver.calls] == ["Alice"]
    assert len(facts.applications.staged) == 1


def test_ambiguous_own_name_skips_nothing() -> None:
    """Two references share the name: neither is skipped, a person is never lost."""
    claim_text = "Alice wrote the report Alice."
    start = claim_text.rindex("Alice")
    claim = _claim().model_copy(
        update={
            "claim_text": claim_text,
            "own_document_name_start": start,
            "own_document_name_end": start + len("Alice"),
        }
    )
    resolver, facts = _normalize(
        claim=claim,
        payload={
            "relations": [
                {
                    "subject": {"name": "Alice"},
                    "predicate": "authored",
                    "object": {"name": "Alice"},
                }
            ]
        },
    )
    assert [ref.name for ref in resolver.calls] == ["Alice", "Alice"]
    assert len(facts.applications.staged) == 1


def test_slot_rule() -> None:
    refs = (EntityRef(name="Acme"), EntityRef(name="Audit", surface=_NAME))
    assert own_document_name_slot(refs=refs, own_document_name=_NAME) == (1, False)
    assert own_document_name_slot(refs=refs, own_document_name=None) == (None, False)
    assert own_document_name_slot(refs=refs, own_document_name="Other") == (None, False)
    twice = (EntityRef(name=_NAME), EntityRef(name="x", surface=_NAME))
    assert own_document_name_slot(refs=twice, own_document_name=_NAME) == (None, True)


def test_longer_surface_containing_the_name_is_skipped() -> None:
    """A surface "The report Audit_2025.pdf" still names the document."""
    resolver, facts = _normalize(
        claim=_claim_with(claim_text=_SELF_CLAIM, own_name=_NAME),
        payload={
            "relations": [
                {
                    "subject": {"name": "Acme"},
                    "predicate": "authored",
                    "object": {
                        "name": "2025 audit report",
                        "surface": f"The report {_NAME}",
                    },
                }
            ]
        },
    )
    assert resolver.calls == []
    assert facts.applications.staged == []


def test_name_only_match_is_skipped() -> None:
    """The canonical name is the document even when the surface is longer."""
    resolver, facts = _normalize(
        claim=_claim_with(claim_text=_SELF_CLAIM, own_name=_NAME),
        payload={
            "observations": [
                {
                    "subject": {"name": _NAME, "surface": "the report"},
                    "statement": "summarizes the 2025 audit findings",
                }
            ]
        },
    )
    assert resolver.calls == []
    assert facts.applications.staged == []


def test_two_containing_references_skip_none() -> None:
    """Ambiguity protection holds for containment matches too."""
    resolver, facts = _normalize(
        claim=_claim_with(claim_text=_SELF_CLAIM, own_name=_NAME),
        payload={
            "relations": [
                {
                    "subject": {"name": _NAME},
                    "predicate": "audited",
                    "object": {"name": "Audit", "surface": f"The report {_NAME}"},
                }
            ]
        },
    )
    assert [ref.name for ref in resolver.calls] == [_NAME, "Audit"]
    assert len(facts.applications.staged) == 1


def test_person_reference_without_the_name_is_untouched() -> None:
    """Only the matching reference is skipped; the person still resolves."""
    resolver, facts = _normalize(
        claim=_claim_with(
            claim_text=f"Alice wrote the report {_NAME}.", own_name=_NAME
        ),
        payload={
            "observations": [
                {
                    "subject": {"name": "Alice"},
                    "statement": "wrote a report",
                    "context_refs": [
                        {"name": "Audit", "surface": f"the report {_NAME}"}
                    ],
                }
            ]
        },
    )
    assert [ref.name for ref in resolver.calls] == ["Alice"]
    (staged,) = facts.applications.staged
    assert staged["context_bindings"] == ()


def test_slot_rule_needs_a_whole_name() -> None:
    """ "Audit_2025.pdf" inside "Audit_2025.pdfx" is not the document."""
    refs = (EntityRef(name="Audit_2025.pdfx"), EntityRef(name="Acme"))
    assert own_document_name_slot(refs=refs, own_document_name=_NAME) == (None, False)
    spaced = (EntityRef(name="acme"), EntityRef(name="x", surface="AUDIT_2025.PDF"))
    assert own_document_name_slot(refs=spaced, own_document_name=_NAME) == (1, False)
