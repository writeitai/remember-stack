"""D142 LoCoMo gate: the predeclared paired non-inferiority decision rule."""

from benchmarks.p1_ranked.locomo_gate import gate
import pytest


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
