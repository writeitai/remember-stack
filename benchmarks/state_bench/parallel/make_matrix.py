"""CLI: write a host-schedulable cell matrix JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from benchmarks.state_bench.runner import BenchmarkRunError
from benchmarks.state_bench.runner import plan_matrix
from benchmarks.state_bench.runner import preflight_episode_estimate


def main(argv: list[str] | None = None) -> int:
    """Write plan JSON for parallel cell runners."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tier", choices=("smoke", "development", "publication"), required=True
    )
    parser.add_argument(
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
    parser.add_argument(
        "--sub-protocol", action="append", required=True, choices=("shared", "native")
    )
    parser.add_argument(
        "--domain",
        action="append",
        choices=("travel", "customer_support", "shopping_assistant"),
    )
    parser.add_argument("--num-runs", type=int, default=None)
    parser.add_argument("--num-workers", type=int, default=8)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        plan = plan_matrix(
            tier=args.tier,
            arms=tuple(args.arm),
            sub_protocols=tuple(args.sub_protocol),
            domains=tuple(args.domain) if args.domain else None,
            num_runs=args.num_runs,
            num_workers=args.num_workers,
        )
        payload = {
            "plan": plan.model_dump(mode="json"),
            "preflight": preflight_episode_estimate(plan=plan),
        }
        Path(args.output).write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(payload["preflight"]))
        return 0
    except (BenchmarkRunError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
