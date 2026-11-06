"""Binary T4 response-schema proofs (D100)."""

from uuid import uuid4

from pydantic import ValidationError
import pytest

from rememberstack.adapters.openrouter import _strict_json_schema
from rememberstack.model import T4Selection
from rememberstack.model.resolution import t4_selection_for_candidates
from rememberstack.spine.resolver import _T4_PROMPT


def test_t4_match_requires_candidate_id() -> None:
    """A match cannot leave candidate selection implicit."""
    with pytest.raises(ValidationError, match="match requires candidate_id"):
        T4Selection(decision="match", confidence=0.8)


def test_t4_new_forbids_candidate_id() -> None:
    """A new decision cannot also select an existing candidate."""
    with pytest.raises(ValidationError, match="new forbids candidate_id"):
        T4Selection(decision="new", candidate_id=uuid4(), confidence=0.8)


def test_t4_binary_shapes_validate() -> None:
    """Both and only the intended binary shapes validate."""
    candidate_id = uuid4()
    match = T4Selection(decision="match", candidate_id=candidate_id, confidence=0.7)
    new = T4Selection(decision="new", confidence=0.6)
    assert match.candidate_id == candidate_id
    assert new.candidate_id is None
    assert tuple(T4Selection.model_fields) == (
        "decision",
        "candidate_id",
        "confidence",
        "rationale",
    )


def test_t4_prompt_requires_all_four_fields() -> None:
    """The measured instruction names the existing T4Selection fields."""
    instruction = (
        "OUTPUT FORMAT\n"
        "Return one JSON object with all four fields: candidate_id, confidence, "
        'decision, and rationale. decision is "match" or "new". confidence is a '
        "number from 0 to 1. rationale is a short explanation or null. Include "
        "every field, even when its value is null.\n\n"
        "MENTION:"
    )
    assert instruction in _T4_PROMPT
    assert _T4_PROMPT.index("OUTPUT FORMAT") < _T4_PROMPT.index("MENTION:")


def test_t4_wire_schema_is_closed_over_the_actual_snapshot() -> None:
    """The provider sees supplied IDs and null, never an unrestricted UUID slot."""
    candidates = (uuid4(), uuid4())
    response_type = t4_selection_for_candidates(candidate_ids=candidates)
    schema = _strict_json_schema(response_type=response_type)
    assert schema["properties"]["candidate_id"]["enum"] == [
        *(str(value) for value in candidates),
        None,
    ]
    assert (
        response_type.model_validate(
            {"decision": "match", "candidate_id": str(candidates[1]), "confidence": 0.8}
        ).candidate_id
        == candidates[1]
    )
    assert (
        response_type.model_validate(
            {"decision": "new", "candidate_id": None, "confidence": 0.8}
        ).candidate_id
        is None
    )


def test_t4_snapshot_types_cannot_leak_candidates_between_calls() -> None:
    """Local validation rejects stale or invented IDs independently of the provider."""
    first, second = uuid4(), uuid4()
    first_type = t4_selection_for_candidates(candidate_ids=(first,))
    second_type = t4_selection_for_candidates(candidate_ids=(second,))
    for response_type, wrong in ((first_type, second), (second_type, first)):
        with pytest.raises(ValidationError, match="outside the supplied snapshot"):
            response_type.model_validate(
                {"decision": "match", "candidate_id": str(wrong), "confidence": 0.9}
            )
    with pytest.raises(ValidationError, match="new forbids candidate_id"):
        first_type.model_validate(
            {"decision": "new", "candidate_id": str(first), "confidence": 0.9}
        )
