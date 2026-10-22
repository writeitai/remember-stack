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
from dataclasses import dataclass
import json
from pathlib import Path
import random
import statistics
import sys
from typing import Any

from benchmarks.locomo.runner import BenchmarkRunError
from benchmarks.locomo.runner import load_recorded_run

RESAMPLES = 10_000
SEED = 504
MARGIN_PP = 1.0
MIN_RUNS = 3


class GateInputError(ValueError):
    """The runs cannot be compared under the predeclared rule."""


@dataclass(frozen=True)
class RunExport:
    """One finished run's judge labels and the identity of its inputs.

    Loaded from the run's own immutable files and checked against the hashes
    the run recorded — not against the current code's protocol pins, so a
    ``main`` baseline keeps its own surface-manifest fingerprint (review r1
    P2-1).
    """

    run_dir: Path
    item_ids_sha256: str
    documents_sha256: str
    dataset_sha256: str
    labels: dict[str, int]


def load_export(run_dir: Path) -> RunExport:
    """Load one run against its own recorded identity; every question answered
    and judged.

    The runner's own integrity checks run (recomputed protocol fingerprint,
    manifest/item/document hashes, rendered documents, state/identity
    correspondence, judge ids), without the current code's protocol pins, so
    a ``main`` baseline keeps its own fingerprint (review r1 P2-1, r2 P2-2).
    """
    try:
        context = load_recorded_run(run_dir=run_dir)
    except BenchmarkRunError as error:
        raise GateInputError(f"{run_dir}: {error}") from error
    item_ids = tuple(question.item_id for question in context.questions)
    unanswered = [item for item in item_ids if item not in context.state.answers]
    unjudged = [item for item in item_ids if item not in context.state.judges]
    if unanswered or unjudged:
        raise GateInputError(
            f"{run_dir}: {len(unanswered)} questions have no answer and"
            f" {len(unjudged)} no judge record (unfinished)"
        )
    labels = {
        item_id: int(context.state.judges[item_id].label == "CORRECT")
        for item_id in item_ids
    }
    return RunExport(
        run_dir=run_dir,
        item_ids_sha256=context.configuration.item_ids_sha256,
        documents_sha256=context.configuration.documents_sha256,
        dataset_sha256=context.configuration.dataset_sha256,
        labels=labels,
    )


def require_comparable(exports: list[RunExport]) -> None:
    """Every run answers the identical questions over identical ingested data."""
    first = exports[0]
    for export in exports[1:]:
        for name in ("item_ids_sha256", "documents_sha256", "dataset_sha256"):
            if getattr(export, name) != getattr(first, name):
                raise GateInputError(
                    f"{export.run_dir} and {first.run_dir} differ in {name}"
                )


def correctness(run_dir: Path) -> dict[str, int]:
    """Per-question judge correctness (1/0) of one finished, checked run."""
    return load_export(run_dir).labels


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
    question_sets = {frozenset(run) for run in main_runs + change_runs}
    if len(question_sets) != 1:
        # The predeclared rule compares one identical question set; an
        # intersection would silently change the population (review r1 P2-1).
        raise GateInputError("the runs do not answer the identical question set")
    questions = sorted(next(iter(question_sets)))
    if not questions:
        raise GateInputError("the runs answer no questions")
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
    main_exports = [load_export(path) for path in args.main]
    change_exports = [load_export(path) for path in args.change]
    require_comparable(main_exports + change_exports)
    report = gate(
        main_runs=[export.labels for export in main_exports],
        change_runs=[export.labels for export in change_exports],
        margin_pp=args.margin_pp,
    )
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))
    sys.exit({"pass": 0, "fail": 1, "inconclusive": 2}[report["decision"]])


if __name__ == "__main__":
    main()
