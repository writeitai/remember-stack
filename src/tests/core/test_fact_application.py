"""Ordinary adjudication can revise dates, but cannot invent evidence or move twice."""

import json
from uuid import UUID

from pydantic import ValidationError
import pytest

from rememberstack.core.fact_application import ApplicationScope
from rememberstack.core.fact_application import new_fact_id
from rememberstack.core.fact_application import validate_application_scope
from rememberstack.model.fact_application import FactApplicationDecision

_A = UUID(int=1)
_B = UUID(int=2)
_C = UUID(int=3)
_D = UUID(int=4)
_SCOPE = ApplicationScope(
    incoming_application_id=_A,
    fact_ids=frozenset({_B}),
    claim_ids=frozenset({_C}),
    assertion_targets={_A: None, _D: _B},
)


def _decision(**fields: object) -> FactApplicationDecision:
    """A same-event decision with overridable effects."""
    return FactApplicationDecision.model_validate_json(
        json.dumps(
            {
                "target": {"fact_id": str(_B)},
                "confidence": 0.95,
                "rationale": "The official correction names the same final.",
                **fields,
            }
        )
    )


def test_corrected_event_date_may_move_later_or_be_cleared() -> None:
    """No date-direction gate or permanent first-claim ownership blocks a correction."""
    for window in (
        {
            "valid_from": "2022-05-12T00:00:00Z",
            "valid_until": "2022-05-13T00:00:00Z",
            "valid_precision": "day",
        },
        {"valid_from": None, "valid_until": None, "valid_precision": "unknown"},
    ):
        decision = _decision(
            window={"window": window, "supporting_claim_ids": [str(_C)]}
        )
        validate_application_scope(decision=decision, scope=_SCOPE)


def test_window_cannot_cite_unsupplied_claim() -> None:
    """Syntactically valid model evidence does not establish admissibility."""
    decision = _decision(window={"window": {}, "supporting_claim_ids": [str(_D)]})
    with pytest.raises(ValueError, match="outside the supplied scope"):
        validate_application_scope(decision=decision, scope=_SCOPE)


def test_split_uses_original_assertion_and_checks_current_support() -> None:
    """An explicit A→B→A split can move one assertion, not its entire source claim."""
    decision = _decision(
        new_facts=[{"handle": "second-tenure", "assertion_application_id": str(_D)}],
        support_moves=[
            {
                "application_id": str(_D),
                "expected_fact_id": str(_B),
                "target": {"new_handle": "second-tenure"},
            }
        ],
    )
    validate_application_scope(decision=decision, scope=_SCOPE)
    stale = ApplicationScope(
        incoming_application_id=_A,
        fact_ids=_SCOPE.fact_ids,
        claim_ids=_SCOPE.claim_ids,
        assertion_targets={_A: None, _D: None},
    )
    with pytest.raises(ValueError, match="expected source"):
        validate_application_scope(decision=decision, scope=stale)
    assert new_fact_id(application_id=_A, handle="second-tenure") == new_fact_id(
        application_id=_A, handle="second-tenure"
    )
    assert new_fact_id(application_id=_A, handle="second-tenure") != new_fact_id(
        application_id=_D, handle="second-tenure"
    )


def test_same_target_cannot_receive_two_replacements() -> None:
    """Ambiguous ordering of two chosen windows is rejected at the answer boundary."""
    replacement = {"window": {}, "supporting_claim_ids": [str(_C)]}
    with pytest.raises(ValidationError, match="replace one window twice"):
        _decision(
            window=replacement,
            updates=[{"target": {"fact_id": str(_B)}, "window": replacement}],
        )


def test_new_fact_cannot_be_derived_from_invented_original_assertion() -> None:
    """A new handle is no permission to manufacture an unsupplied assertion."""
    decision = _decision(
        target={"new_handle": "new"},
        new_facts=[{"handle": "new", "assertion_application_id": str(_C)}],
    )
    with pytest.raises(ValueError, match="original assertion"):
        validate_application_scope(decision=decision, scope=_SCOPE)
