"""Binary T4 response-schema proofs (D100)."""

from uuid import uuid4

from pydantic import ValidationError
import pytest

from rememberstack.model import T4Selection


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
