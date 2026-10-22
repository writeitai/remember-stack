"""D142 LoCoMo gate: the predeclared paired non-inferiority decision rule."""

from dataclasses import replace
import json
from pathlib import Path

from benchmarks.locomo import runner
from benchmarks.locomo.model import AnswerRecord
from benchmarks.locomo.model import BenchmarkFailure
from benchmarks.locomo.model import JudgeRecord
from benchmarks.locomo.runner import prepare_run
from benchmarks.p1_ranked.locomo_gate import gate
from benchmarks.p1_ranked.locomo_gate import GateInputError
from benchmarks.p1_ranked.locomo_gate import load_export
from benchmarks.p1_ranked.locomo_gate import require_comparable
import pytest

from tests.benchmarks.test_locomo_runner import _patch_prepared_inputs


def _runs(correct: dict[str, int], count: int = 3) -> list[dict[str, int]]:
    return [dict(correct) for _ in range(count)]


def test_identical_answers_pass_non_inferiority() -> None:
    answers = {f"q{index}": index % 2 for index in range(400)}
    report = gate(main_runs=_runs(answers), change_runs=_runs(answers))
    assert report["decision"] == "pass"
    assert report["mean_difference_pp"] == 0.0


def test_a_clear_regression_fails() -> None:
    main = {f"q{index}": 1 for index in range(400)}
    change = {f"q{index}": int(index % 10 != 0) for index in range(400)}  # -10 pp
    report = gate(main_runs=_runs(main), change_runs=_runs(change))
    assert report["decision"] == "fail"


def test_an_underpowered_small_drop_is_inconclusive_not_a_pass() -> None:
    main = {f"q{index}": 1 for index in range(40)}
    change = {f"q{index}": int(index != 0) for index in range(40)}  # -2.5 pp, n=40
    report = gate(main_runs=_runs(main), change_runs=_runs(change))
    assert report["decision"] in {"inconclusive", "fail"}
    assert report["decision"] != "pass"


def test_fewer_than_three_runs_per_side_is_refused() -> None:
    answers = {"q1": 1}
    with pytest.raises(ValueError, match="at least 3 runs"):
        gate(main_runs=_runs(answers, 2), change_runs=_runs(answers))


def _judged_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    label: str | None,
    old_surface: bool = False,
    answered: bool = True,
    judge_item: str | None = None,
) -> Path:
    """A prepared synthetic run, self-consistent, answered and judged.

    ``old_surface`` prepares it under another surface-manifest pin, so its
    recorded protocol fingerprint is genuinely recomputed for that pin (a
    ``main`` baseline), not merely edited in run.json.
    """
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / name
    with monkeypatch.context() as patch:
        if old_surface:
            original = runner.protocol_for_key
            patch.setattr(
                runner,
                "protocol_for_key",
                lambda key: replace(original(key), surface_manifest_hash="0" * 64),
            )
        prepare_run(
            dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir
        )
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    if label is not None:
        context = runner.load_recorded_run(run_dir=run_dir)
        question = context.questions[0]
        if answered:
            state["answers"] = {
                question.item_id: AnswerRecord(
                    item_id=question.item_id,
                    sample_id=question.sample_id,
                    category=question.category,
                    question=question.question,
                    gold_answer=question.answer or "",
                    gold_evidence=question.evidence,
                    retrieval_succeeded=False,
                    retrieval_latency_ms=0,
                    reader_called=False,
                    failure=BenchmarkFailure(kind="tool", message="fixture"),
                ).model_dump(mode="json")
            }
        state["judges"] = {
            question.item_id: JudgeRecord(
                item_id=judge_item or question.item_id,
                label=label,  # type: ignore[arg-type]
                model_called=False,
            ).model_dump(mode="json")
        }
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return run_dir


def test_a_main_baseline_loads_with_its_own_surface_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review r1 P2-1 / r2 P2-2: a genuinely valid old-pin run is rejected by
    the current-pin loader but loads against its own recorded identity."""
    main = _judged_run(
        tmp_path, monkeypatch, name="main", label="CORRECT", old_surface=True
    )
    change = _judged_run(tmp_path, monkeypatch, name="change", label="WRONG")
    with pytest.raises(runner.BenchmarkRunError):
        runner._load_run(run_dir=main)  # noqa: SLF001
    exports = [load_export(main), load_export(change)]
    require_comparable(exports)
    assert exports[0].labels == {"conv-test/qa/0000": 1}
    assert exports[1].labels == {"conv-test/qa/0000": 0}


def test_an_unfinished_run_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _judged_run(tmp_path, monkeypatch, name="unfinished", label=None)
    with pytest.raises(GateInputError, match="no answer"):
        load_export(run)


def test_a_judge_without_an_answer_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _judged_run(
        tmp_path, monkeypatch, name="unanswered", label="CORRECT", answered=False
    )
    with pytest.raises(GateInputError, match="judge without an answer"):
        load_export(run)


def test_a_mismatched_judge_id_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _judged_run(
        tmp_path, monkeypatch, name="mismatch", label="CORRECT", judge_item="other"
    )
    with pytest.raises(GateInputError, match="judge state changed"):
        load_export(run)


def test_an_edited_fingerprint_input_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _judged_run(tmp_path, monkeypatch, name="edited", label="CORRECT")
    configuration = json.loads((run / "run.json").read_text(encoding="utf-8"))
    configuration["surface_manifest_hash"] = "1" * 64  # fingerprint not recomputed
    (run / "run.json").write_text(json.dumps(configuration), encoding="utf-8")
    with pytest.raises(GateInputError, match="fingerprint changed"):
        load_export(run)


def test_runs_over_different_question_sets_are_refused() -> None:
    with pytest.raises(GateInputError, match="identical question set"):
        gate(main_runs=_runs({"q1": 1, "q2": 1}), change_runs=_runs({"q1": 1}))
