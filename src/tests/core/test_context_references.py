"""Provider-free D123 context-reference cap and original-ordinal tests."""

from rememberstack.core.context_references import attempted_context_refs
from rememberstack.core.context_references import MAX_CONTEXT_REFS
from rememberstack.model.relations import EntityRef
from rememberstack.model.relations import NormalizationResponse
from rememberstack.model.relations import ObservationCandidate


def test_overflow_keeps_original_ordinals_and_does_not_reject_the_assertion() -> None:
    """Auxiliary extras are truncated after the fourth original slot."""
    refs = tuple(EntityRef(name=f"Entity {index}") for index in range(6))
    attempted, truncated = attempted_context_refs(refs=refs)
    assert truncated is True
    assert [ordinal for ordinal, _ in attempted] == list(range(MAX_CONTEXT_REFS))
    assert [ref.name for _, ref in attempted] == [
        f"Entity {index}" for index in range(4)
    ]
    response = NormalizationResponse(
        observations=(
            ObservationCandidate(
                subject=EntityRef(name="Joanna"),
                statement="Joanna said Nate won Tournament A",
                context_refs=refs,
            ),
        )
    )
    assert response.observations[0].subject.name == "Joanna"
    assert len(response.observations[0].context_refs) == 6


def test_equal_names_keep_distinct_original_ordinals() -> None:
    """Equal names are not identity before resolution."""
    refs = (
        EntityRef(name="The Open"),
        EntityRef(name="Nate"),
        EntityRef(name="The Open"),
        EntityRef(name="Tournament B"),
    )
    attempted, truncated = attempted_context_refs(refs=refs)
    assert truncated is False
    assert [(ordinal, ref.name) for ordinal, ref in attempted] == [
        (0, "The Open"),
        (1, "Nate"),
        (2, "The Open"),
        (3, "Tournament B"),
    ]
    empty, empty_truncated = attempted_context_refs(refs=())
    assert empty == ()
    assert empty_truncated is False
