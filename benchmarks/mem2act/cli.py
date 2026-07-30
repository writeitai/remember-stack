"""CLI for RS-Mem2Act-v1 prepare and offline score."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from benchmarks.mem2act.dataset import DatasetError
from benchmarks.mem2act.model import ScoreRecord
from benchmarks.mem2act.protocol import DEFAULT_RECIPE_NAME
from benchmarks.mem2act.protocol import DEFAULT_TOP_K
from benchmarks.mem2act.runner import BenchmarkRunError
from benchmarks.mem2act.runner import prepare_run
from benchmarks.mem2act.runner import summarize_scores


def main(argv: list[str] | None = None) -> int:
    """Run prepare or summarize scores."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            configuration = prepare_run(
                output=Path(args.output),
                tier=args.tier,
                arm=args.arm,
                dataset_root=Path(args.dataset_root),
                reader_model=args.reader_model,
                top_k=args.top_k,
                recipe_name=args.recipe_name,
                max_evaluator_cost_usd=args.max_evaluator_cost_usd,
            )
            print(configuration.model_dump_json())
            return 0
        if args.command == "summarize":
            path = Path(args.scores)
            rows = json.loads(path.read_text(encoding="utf-8"))
            records = [ScoreRecord.model_validate(row) for row in rows]
            print(json.dumps(summarize_scores(records), indent=2))
            return 0
    except (BenchmarkRunError, DatasetError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    parser.print_help()
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.mem2act")
    sub = parser.add_subparsers(dest="command")

    prepare = sub.add_parser("prepare", help="local prepare; no provider calls")
    prepare.add_argument("--output", required=True)
    prepare.add_argument(
        "--tier", choices=("smoke", "development", "publication"), required=True
    )
    prepare.add_argument(
        "--arm", choices=("empty", "rememberstack", "full_context"), required=True
    )
    prepare.add_argument(
        "--dataset-root",
        required=True,
        help="path to Mem2ActBench checkout at the pinned commit",
    )
    prepare.add_argument("--reader-model", required=True)
    prepare.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    prepare.add_argument("--recipe-name", default=DEFAULT_RECIPE_NAME)
    prepare.add_argument("--max-evaluator-cost-usd", type=float, default=25.0)

    summarize = sub.add_parser("summarize", help="aggregate a scores JSON list")
    summarize.add_argument("--scores", required=True)

    return parser
