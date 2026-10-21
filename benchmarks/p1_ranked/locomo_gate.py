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

from pydantic import TypeAdapter

from benchmarks.locomo.dataset import item_ids_hash
from benchmarks.locomo.dataset import manifest_bytes_hash
from benchmarks.locomo.model import PreparedDocument
from benchmarks.locomo.model import QuestionManifest
from benchmarks.locomo.model import RunConfiguration
from benchmarks.locomo.model import RunState
from benchmarks.locomo.runner import _models_hash  # noqa: PLC2701 -- read-only reuse

RESAMPLES = 10_000
SEED = 504
MARGIN_PP = 1.0
MIN_RUNS = 3

_DOCUMENTS = TypeAdapter(tuple[PreparedDocument, ...])


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
    """Read and integrity-check one run; every question must be judged."""
    configuration = RunConfiguration.model_validate_json(
        (run_dir / "run.json").read_text(encoding="utf-8")
    )
    manifest = QuestionManifest.model_validate_json(
        (run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    documents = _DOCUMENTS.validate_json(
        (run_dir / "documents.json").read_text(encoding="utf-8")
    )
    state = RunState.model_validate_json(
        (run_dir / "state.json").read_text(encoding="utf-8")
    )
    if item_ids_hash(item_ids=manifest.item_ids) != manifest.item_ids_sha256:
        raise GateInputError(f"{run_dir}: manifest item hash does not match its ids")
    if manifest_bytes_hash(manifest=manifest) != configuration.manifest_sha256:
        raise GateInputError(f"{run_dir}: manifest differs from the recorded run")
    if _models_hash(values=documents) != configuration.documents_sha256:
        raise GateInputError(f"{run_dir}: documents differ from the recorded run")
    labels: dict[str, int] = {}
    missing = [item for item in manifest.item_ids if item not in state.judges]
    if missing:
        raise GateInputError(
            f"{run_dir}: {len(missing)} questions have no judge record (unfinished)"
        )
    for item_id in manifest.item_ids:
        labels[item_id] = int(state.judges[item_id].label == "CORRECT")
    return RunExport(
        run_dir=run_dir,
        item_ids_sha256=manifest.item_ids_sha256,
        documents_sha256=configuration.documents_sha256,
        dataset_sha256=configuration.dataset_sha256,
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
