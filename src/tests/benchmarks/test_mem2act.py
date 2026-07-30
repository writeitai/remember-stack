"""Pure Mem2Act adapter tests."""

from __future__ import annotations

from benchmarks.mem2act.prompts import build_reader_prompt
from benchmarks.mem2act.prompts import parse_tool_call_json
from benchmarks.mem2act.runner import load_manifest
from benchmarks.mem2act.runner import load_session_map
from benchmarks.mem2act.runner import score_prediction
from benchmarks.mem2act.runner import summarize_scores
from benchmarks.mem2act.runner import validate_manifest
from benchmarks.mem2act.score import arguments_match
from benchmarks.mem2act.score import score_tool_call


def test_manifests_validate() -> None:
    session_map = load_session_map()
    for tier in ("smoke", "development", "publication"):
        manifest = load_manifest(tier=tier)  # type: ignore[arg-type]
        validate_manifest(manifest)
        assert all(item_id in session_map for item_id in manifest.item_ids)


def test_arguments_match_normalizes_strings_and_requires_gold_keys() -> None:
    gold = {"location": "New York", "days": 7}
    assert arguments_match(
        gold=gold, predicted={"location": " new york ", "days": 7, "extra": 1}
    )
    assert not arguments_match(gold=gold, predicted={"location": "New York"})
    assert not arguments_match(gold=gold, predicted={"location": "Boston", "days": 7})


def test_score_tool_call_conjunction() -> None:
    name_ok, args_ok, item_ok = score_tool_call(
        gold_name="GetWeatherForecast",
        gold_arguments={"location": "New York"},
        predicted_name="GetWeatherForecast",
        predicted_arguments={"location": "new york"},
    )
    assert name_ok and args_ok and item_ok
    name_ok, args_ok, item_ok = score_tool_call(
        gold_name="GetWeatherForecast",
        gold_arguments={"location": "New York"},
        predicted_name="Search",
        predicted_arguments={"location": "New York"},
    )
    assert not name_ok and args_ok and not item_ok


def test_parse_tool_call_json_accepts_fences() -> None:
    name, args, err = parse_tool_call_json(
        '```json\n{"name": "Search", "arguments": {"q": "x"}}\n```'
    )
    assert err is None
    assert name == "Search"
    assert args == {"q": "x"}


def test_build_reader_prompt_marks_empty_history() -> None:
    prompt = build_reader_prompt(
        query="book a flight", tool_schema={"name": "search_flights"}
    )
    assert "none provided" in prompt
    assert "book a flight" in prompt


def test_score_prediction_and_summary() -> None:
    qa = {
        "qa_id": "qa_test",
        "tool_call": {"name": "Search", "arguments": {"q": "hi"}},
        "complexity_metadata": {"level": "L1"},
    }
    ok = score_prediction(
        qa=qa, predicted_name="Search", predicted_arguments={"q": "HI"}
    )
    bad = score_prediction(
        qa=qa, predicted_name=None, predicted_arguments=None, failure="json_error"
    )
    summary = summarize_scores([ok, bad])
    assert summary["n"] == 2
    assert summary["ok"] == 1
    assert summary["accuracy"] == 0.5
    assert summary["by_level"]["L1"]["n"] == 2
