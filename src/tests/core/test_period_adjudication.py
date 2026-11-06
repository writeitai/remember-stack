"""D140 §8.2 (WP-ET.7) pieces that need no database."""

from datetime import datetime
from datetime import UTC

from pydantic import ValidationError
import pytest

from rememberstack.model.claims import ClaimValidPrecision
from rememberstack.spine.fact_adjudication import _FACT_PROMPT
from rememberstack.spine.fact_adjudication import _single_in_force_window
from rememberstack.spine.fact_adjudication import render_fact_prompt
from rememberstack.spine.fact_applications import canonical_json
from rememberstack.spine.fact_window_review import PromptWindowReview
from rememberstack.spine.fact_window_review import review_prompt

_START = datetime(2015, 1, 1, tzinfo=UTC)
_END = datetime(2024, 1, 1, tzinfo=UTC)


def test_undated_attempt_renders_the_unchanged_template() -> None:
    """No claim carries in-force periods: byte-for-byte the previous rendering."""
    presentation = {"claims": [{"handle": "C1", "source_world_precision": "day"}]}
    assert render_fact_prompt(presentation=presentation) == _FACT_PROMPT.format(
        inputs=canonical_json(presentation)
    )


def test_periodised_attempt_adds_the_in_force_section_before_output() -> None:
    """The guidance sits after WORLD DATES and before OUTPUT, once."""
    presentation = {"claims": [{"handle": "C1", "source_in_force": []}]}
    rendered = render_fact_prompt(presentation=presentation)
    assert rendered.count("IN-FORCE PERIODS") == 1
    assert (
        rendered.index("WORLD DATES")
        < rendered.index("IN-FORCE PERIODS")
        < rendered.index("\nOUTPUT\n")
    )
    without = rendered.replace(
        rendered[rendered.index("\nIN-FORCE") : rendered.index("\nOUTPUT\n")], ""
    )
    assert without == _FACT_PROMPT.format(inputs=canonical_json(presentation))


def test_one_closed_period_is_a_day_window_and_an_open_one_is_ongoing() -> None:
    """In-force bounds are already canonical: the end is not advanced."""
    closed = _single_in_force_window(
        {"source_in_force": [{"from": _START.isoformat(), "until": _END.isoformat()}]}
    )
    assert closed is not None
    assert (closed.valid_from, closed.valid_until, closed.valid_precision) == (
        _START,
        _END,
        ClaimValidPrecision.DAY,
    )
    ongoing = _single_in_force_window(
        {"source_in_force": [{"from": _START.isoformat(), "until": None}]}
    )
    assert ongoing is not None
    assert ongoing.valid_precision is ClaimValidPrecision.OPEN
    assert ongoing.valid_until is None


def test_no_single_period_window_for_none_or_several_periods() -> None:
    """Several periods (text that left and came back) are left to the model."""
    assert _single_in_force_window(None) is None
    assert _single_in_force_window({"source_in_force": []}) is None
    assert (
        _single_in_force_window(
            {
                "source_in_force": [
                    {"from": _START.isoformat(), "until": _END.isoformat()},
                    {"from": "2025-01-01T00:00:00+00:00", "until": None},
                ]
            }
        )
        is None
    )


def test_review_answer_shape_is_enforced() -> None:
    """Replace needs a grounded window; keep must not carry one."""
    with pytest.raises(ValidationError):
        PromptWindowReview(
            decision="replace", window=None, confidence=0.9, rationale="x"
        )
    kept = PromptWindowReview(
        decision="keep", window=None, confidence=0.9, rationale="x"
    )
    assert kept.window is None
    assert "IN-FORCE PERIODS" in review_prompt(presentation={"claims": []})
