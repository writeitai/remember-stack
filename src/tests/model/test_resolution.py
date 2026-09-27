"""Binary T4 response-schema proofs (D100)."""

from uuid import uuid4

from pydantic import ValidationError
import pytest

from rememberstack.model import T4Selection
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
