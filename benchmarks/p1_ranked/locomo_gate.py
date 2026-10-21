"""D142 LoCoMo answer-quality gate (WP-RS.9): paired non-inferiority test.

Predeclared rule (P1 search design §9): the sampling unit is the question;
each side contributes at least three LoCoMo runs on identical ingested data
and the same question set; a question's score per side is its mean judge
correctness over that side's runs; the estimator is the mean over questions of
the paired difference (change − main); a one-sided 95% bound comes from a
paired bootstrap over questions (10,000 resamples, fixed seed). With margin
``m`` (1.0 percentage point unless the owner fixes another before the run):

- **pass** when the lower bound is above −m;
- **fail** when the upper bound is below −m;
- otherwise **inconclusive** (an underpowered result never passes).

    uv run python -m benchmarks.p1_ranked.locomo_gate \\
        --main runs/main-1 runs/main-2 runs/main-3 \\
        --change runs/d142-1 runs/d142-2 runs/d142-3 --out locomo_gate.json

Exit status: 0 pass, 1 fail, 2 inconclusive. No model is called; it reads the
judge labels the runs already stored.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import statistics
import sys
from typing import Any

from benchmarks.locomo.runner import _load_run  # noqa: PLC2701 -- read-only reuse

RESAMPLES = 10_000
SEED = 504
MARGIN_PP = 1.0
MIN_RUNS = 3


def correctness(run_dir: Path) -> dict[str, int]:
    """Per-question judge correctness (1/0) of one finished run."""
    context = _load_run(run_dir=run_dir)
    result = {}
    for question in context.questions:
        judge = context.state.judges.get(question.item_id)
        result[str(question.item_id)] = int(
            judge is not None and judge.label == "CORRECT"
        )
    return result


def gate(
    *,
    main_runs: list[dict[str, int]],
    change_runs: list[dict[str, int]],
    margin_pp: float = MARGIN_PP,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict[str, Any]:
    """Apply the predeclared paired non-inferiority rule."""
    if len(main_runs) < MIN_RUNS or len(change_runs) < MIN_RUNS:
        raise ValueError(f"each side needs at least {MIN_RUNS} runs")
    questions = sorted(set.intersection(*(set(run) for run in main_runs + change_runs)))
    if not questions:
        raise ValueError("the runs share no questions")
    differences = [
        statistics.mean(run[q] for run in change_runs)
        - statistics.mean(run[q] for run in main_runs)
        for q in questions
    ]
    rng = random.Random(seed)
    size = len(differences)
    means = sorted(
        statistics.fmean(differences[rng.randrange(size)] for _ in range(size))
        for _ in range(resamples)
    )
    lower = means[int(0.05 * resamples)]
    upper = means[int(0.95 * resamples) - 1]
    margin = margin_pp / 100.0
    if lower > -margin:
        decision = "pass"
    elif upper < -margin:
        decision = "fail"
    else:
        decision = "inconclusive"
    return {
        "questions": size,
        "main_runs": len(main_runs),
        "change_runs": len(change_runs),
        "mean_difference_pp": 100.0 * statistics.fmean(differences),
        "one_sided_95_lower_pp": 100.0 * lower,
        "one_sided_95_upper_pp": 100.0 * upper,
        "margin_pp": margin_pp,
        "resamples": resamples,
        "seed": seed,
        "decision": decision,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--main", nargs="+", type=Path, required=True)
    parser.add_argument("--change", nargs="+", type=Path, required=True)
    parser.add_argument("--margin-pp", type=float, default=MARGIN_PP)
    parser.add_argument("--out", default="locomo_gate.json")
    args = parser.parse_args()
    report = gate(
        main_runs=[correctness(path) for path in args.main],
        change_runs=[correctness(path) for path in args.change],
        margin_pp=args.margin_pp,
    )
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))
    sys.exit({"pass": 0, "fail": 1, "inconclusive": 2}[report["decision"]])


if __name__ == "__main__":
    main()
