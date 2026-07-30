"""CLI for RS-STATE-Learning-v1 prepare / plan stages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from benchmarks.state_bench.protocol import DEFAULT_RECIPE_NAME
from benchmarks.state_bench.protocol import DEFAULT_TOP_K
from benchmarks.state_bench.protocol import DOMAINS
from benchmarks.state_bench.runner import BenchmarkRunError
from benchmarks.state_bench.runner import plan_matrix
from benchmarks.state_bench.runner import preflight_episode_estimate
from benchmarks.state_bench.runner import prepare_run
from benchmarks.state_bench.trajectories import TrajectoryError


def main(argv: list[str] | None = None) -> int:
    """Run one local prepare or matrix-planning stage."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            configuration = prepare_run(
                output=Path(args.output),
                tier=args.tier,
                arm=args.arm,
                sub_protocol=args.sub_protocol,
                state_bench_root=Path(args.state_bench_root),
                agent_model_name=args.agent_model_name,
                agent_model_reasoning_level=args.agent_model_reasoning_level,
                num_runs=args.num_runs,
                top_k=args.top_k,
                recipe_name=args.recipe_name,
                max_evaluator_cost_usd=args.max_evaluator_cost_usd,
                domains=tuple(args.domain) if args.domain else None,
                allow_dirty=args.allow_dirty,
            )
            print(configuration.model_dump_json())
            return 0
        if args.command == "plan-matrix":
            plan = plan_matrix(
                tier=args.tier,
                arms=tuple(args.arm),
                sub_protocols=tuple(args.sub_protocol),
                domains=tuple(args.domain) if args.domain else None,
                num_runs=args.num_runs,
                num_workers=args.num_workers,
            )
            estimate = preflight_episode_estimate(plan=plan)
            payload = {"plan": plan.model_dump(mode="json"), "preflight": estimate}
            text = json.dumps(payload, indent=2)
            if args.output:
                Path(args.output).write_text(text + "\n", encoding="utf-8")
            print(text)
            return 0
    except (BenchmarkRunError, TrajectoryError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    parser.print_help()
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.state_bench",
        description="RememberStack STATE-Bench Agent Learning Track adapter",
    )
    sub = parser.add_subparsers(dest="command")

    prepare = sub.add_parser("prepare", help="local, provider-free run preparation")
    prepare.add_argument("--output", required=True)
    prepare.add_argument(
        "--tier", choices=("smoke", "development", "publication"), required=True
    )
    prepare.add_argument(
        "--arm",
        choices=(
            "empty",
            "full_context",
            "bm25",
            "dense",
            "mem0",
            "graphiti",
            "rememberstack",
        ),
        required=True,
    )
    prepare.add_argument(
        "--sub-protocol", choices=("shared", "native"), default="shared"
    )
    prepare.add_argument(
        "--state-bench-root",
        required=True,
        help="path to pinned microsoft/STATE-Bench checkout",
    )
    prepare.add_argument("--agent-model-name", required=True)
    prepare.add_argument("--agent-model-reasoning-level", default=None)
    prepare.add_argument("--num-runs", type=int, default=None)
    prepare.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    prepare.add_argument("--recipe-name", default=DEFAULT_RECIPE_NAME)
    prepare.add_argument("--max-evaluator-cost-usd", type=float, default=50.0)
    prepare.add_argument(
        "--domain",
        action="append",
        choices=list(DOMAINS),
        help="repeatable; default all three domains (RS/BM25 require exactly one)",
    )
    prepare.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow prepare on a dirty adapter worktree (dev only; not for scored runs)",
    )

    matrix = sub.add_parser(
        "plan-matrix",
        help="build parallel arm×sub-protocol×domain cells + episode preflight",
    )
    matrix.add_argument(
        "--tier", choices=("smoke", "development", "publication"), required=True
    )
    matrix.add_argument(
        "--arm",
        action="append",
        required=True,
        choices=(
            "empty",
            "full_context",
            "bm25",
            "dense",
            "mem0",
            "graphiti",
            "rememberstack",
        ),
    )
    matrix.add_argument(
        "--sub-protocol", action="append", required=True, choices=("shared", "native")
    )
    matrix.add_argument("--domain", action="append", choices=list(DOMAINS))
    matrix.add_argument("--num-runs", type=int, default=None)
    matrix.add_argument("--num-workers", type=int, default=8)
    matrix.add_argument("--output", default=None)

    return parser
