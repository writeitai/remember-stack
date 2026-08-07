"""Unit tests for BEAM official scorer helpers (no live OpenRouter)."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.rs_harness_beam.official_score import _kendall_tau_b
from benchmarks.rs_harness_beam.official_score import load_beam_rubrics
from benchmarks.rs_harness_beam.official_score import match_rubric
from benchmarks.rs_harness_beam.official_score import mean_nugget_score
from benchmarks.rs_harness_beam.official_score import NuggetJudgement
from benchmarks.rs_harness_beam.official_score import OfficialScoreError
from benchmarks.rs_harness_beam.official_score import parse_judge_json
import pytest


def test_parse_judge_json_accepts_fenced_and_raw() -> None:
    """Judge responses may be fenced or bare JSON objects."""
    raw = parse_judge_json('{"score": 1.0, "reason": "ok"}')
    assert raw["score"] == 1.0
    fenced = parse_judge_json('```json\n{"score": 0.5, "reason": "partial"}\n```')
    assert fenced["score"] == 0.5


def test_mean_nugget_score_averages() -> None:
    """Ability score is the mean of nugget scores."""
    judgements = (
        NuggetJudgement(rubric_item="a", score=1.0, reason=""),
        NuggetJudgement(rubric_item="b", score=0.0, reason=""),
        NuggetJudgement(rubric_item="c", score=0.5, reason=""),
    )
    assert mean_nugget_score(judgements=judgements) == pytest.approx(0.5)


def test_match_rubric_loads_smoke_fixture() -> None:
    """Smoke 100K conversation 1 rubrics match committed BEAM probes."""
    rubrics = load_beam_rubrics()
    items = match_rubric(
        ability="information_extraction",
        question="When does my first sprint end?",
        rubrics_by_ability=rubrics,
    )
    assert any("March 29" in item for item in items)


def test_match_rubric_missing_raises() -> None:
    """Unknown questions must not silently fall back."""
    rubrics = load_beam_rubrics()
    with pytest.raises(OfficialScoreError):
        match_rubric(
            ability="abstention",
            question="This question is not in BEAM",
            rubrics_by_ability=rubrics,
        )


def test_kendall_tau_b_identical_is_one() -> None:
    """Perfect order agreement → τ-b = 1."""
    assert _kendall_tau_b([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_kendall_tau_b_handles_ties() -> None:
    """Identical rankings with ties must still yield τ-b = 1 (SciPy τ-b)."""
    assert _kendall_tau_b([1.0, 1.0, 2.0], [1.0, 1.0, 2.0]) == pytest.approx(1.0)
    assert _kendall_tau_b([1.0, 1.0, 2.0, 2.0], [1.0, 1.0, 2.0, 2.0]) == pytest.approx(
        1.0
    )


def test_fixture_file_is_valid_json() -> None:
    """Committed probing_questions fixture stays parseable."""
    path = (
        Path(__file__).resolve().parents[3]
        / "benchmarks"
        / "rs_harness_beam"
        / "fixtures"
        / "beam_smoke_100k_1"
        / "probing_questions.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "abstention" in data
    assert data["abstention"][0]["rubric"]
