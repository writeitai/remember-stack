"""Command line for the deliberately staged full-system LoCoMo harness."""

from __future__ import annotations

import argparse
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
import sys

from benchmarks.locomo.protocol import API_TIMEOUT_SECONDS
from benchmarks.locomo.protocol import DEFAULT_PROTOCOL_KEY
from benchmarks.locomo.protocol import PROTOCOL_REGISTRY
from benchmarks.locomo.runner import adopt_ingest_sample
from benchmarks.locomo.runner import answer_sample
from benchmarks.locomo.runner import BenchmarkRunError
from benchmarks.locomo.runner import ingest_sample
from benchmarks.locomo.runner import judge_sample
from benchmarks.locomo.runner import prepare_run
from benchmarks.locomo.runner import summarize_run
from benchmarks.locomo.runner import summarize_runs
from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import OpenRouterSettings
from rememberstack.surfaces.sdk import MemoryApiError
from rememberstack.surfaces.sdk import MemoryClient


def main(argv: list[str] | None = None) -> int:
    """Run one local or explicitly acknowledged remote benchmark stage."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            configuration = prepare_run(
                dataset_path=args.dataset,
                tier=args.tier,
                output=args.output,
                protocol=args.protocol,
            )
            print(configuration.model_dump_json())
            return 0
        if args.command == "ingest":
            with MemoryClient(timeout=API_TIMEOUT_SECONDS) as client:
                records = ingest_sample(
                    run_dir=args.run,
                    sample_id=args.sample,
                    max_documents=args.max_documents,
                    max_evaluator_cost_usd=args.max_evaluator_cost_usd,
                    execute=args.execute,
                    isolated_deployment_confirmation=(args.confirm_isolated_deployment),
                    client=client,
                    provider=_provider(),
                )
            for record in records:
                print(record.model_dump_json())
            return 0
        if args.command == "adopt-ingest":
            record = adopt_ingest_sample(
                target_run_dir=args.target_run,
                source_run_dir=args.source_run,
                sample_id=args.sample,
                receipt_path=args.receipt,
            )
            print(record.model_dump_json())
            return 0
        if args.command == "answer":
            provider = _provider()
            with MemoryClient(timeout=API_TIMEOUT_SECONDS) as client:
                records = answer_sample(
                    run_dir=args.run,
                    sample_id=args.sample,
                    max_questions=args.max_questions,
                    max_agent_calls=args.max_agent_calls,
                    max_evaluator_cost_usd=args.max_evaluator_cost_usd,
                    execute=args.execute,
                    p3_root=args.p3_root,
                    client=client,
                    provider=provider,
                )
            for record in records:
                print(record.model_dump_json())
            return 0
        if args.command == "judge":
            records = judge_sample(
                run_dir=args.run,
                sample_id=args.sample,
                max_judge_calls=args.max_judge_calls,
                max_evaluator_cost_usd=args.max_evaluator_cost_usd,
                execute=args.execute,
                provider=_provider(),
            )
            for record in records:
                print(record.model_dump_json())
            return 0
        if args.command == "summarize":
            summary = (
                summarize_run(run_dir=args.run[0])
                if len(args.run) == 1
                else summarize_runs(run_dirs=tuple(args.run))
            )
            print(summary.model_dump_json())
            return 0
    except (BenchmarkRunError, MemoryApiError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    parser.print_help()
    return 2


def _provider() -> OpenRouterModelProvider:
    """Compose the existing typed OpenRouter adapter from settings."""
    return OpenRouterModelProvider(settings=OpenRouterSettings.model_validate({}))


def _positive_decimal(value: str) -> Decimal:
    """Parse one strictly positive reported-spend stop threshold."""
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("must be a decimal number") from error
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive and finite")
    return parsed


def _parser() -> argparse.ArgumentParser:
    """Build the deliberately staged command surfaces."""
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.locomo",
        description=(
            "RS-LoCoMo-Full-v13: prepare is local; ingest/answer/judge require "
            "explicit execution acknowledgements"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare", help="validate and render a local run (no API/model calls)"
    )
    prepare.add_argument("--dataset", type=Path, required=True)
    prepare.add_argument(
        "--tier", choices=("smoke", "development", "publication"), required=True
    )
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument(
        "--protocol", choices=tuple(PROTOCOL_REGISTRY), default=DEFAULT_PROTOCOL_KEY
    )

    adopt = commands.add_parser(
        "adopt-ingest",
        help="reuse one receipt-backed completed v12 ingest in a prepared v13 run",
    )
    adopt.add_argument("--target-run", type=Path, required=True)
    adopt.add_argument("--source-run", type=Path, required=True)
    adopt.add_argument("--sample", required=True)
    adopt.add_argument("--receipt", type=Path, required=True)

    ingest = commands.add_parser(
        "ingest", help="upload one sample to a clean isolated deployment"
    )
    _run_and_sample(ingest)
    ingest.add_argument("--max-documents", type=int, required=True)
    ingest.add_argument(
        "--max-evaluator-cost-usd",
        type=_positive_decimal,
        required=True,
        help="run-absolute shared reported-spend stop threshold for preflight,"
        " answer, and judge calls",
    )
    ingest.add_argument("--execute", action="store_true")
    ingest.add_argument("--confirm-isolated-deployment")

    answer = commands.add_parser(
        "answer", help="run the bounded complete-retrieval answer agent for one sample"
    )
    _run_and_sample(answer)
    answer.add_argument(
        "--p3-root",
        type=Path,
        required=True,
        help="published P3 directory whose snapshot marker must match readiness",
    )
    answer.add_argument(
        "--max-questions",
        type=int,
        required=True,
        help="run-absolute authorization; must cover the prepared tier item count",
    )
    answer.add_argument(
        "--max-agent-calls",
        type=int,
        required=True,
        help="run-absolute ceiling over answer-agent model calls; operation calls"
        " have a separate per-question cap",
    )
    answer.add_argument(
        "--max-evaluator-cost-usd",
        type=_positive_decimal,
        required=True,
        help="run-absolute shared reported-spend stop threshold; use a provider"
        " account cap as the hard monetary boundary",
    )
    answer.add_argument("--execute", action="store_true")

    judge = commands.add_parser(
        "judge", help="call the frozen judge for one sample's answers"
    )
    _run_and_sample(judge)
    judge.add_argument(
        "--max-judge-calls",
        type=int,
        required=True,
        help="run-absolute ceiling over judge calls already recorded plus new calls",
    )
    judge.add_argument(
        "--max-evaluator-cost-usd",
        type=_positive_decimal,
        required=True,
        help="run-absolute shared reported-spend stop threshold; use a provider"
        " account cap as the hard monetary boundary",
    )
    judge.add_argument("--execute", action="store_true")

    summarize = commands.add_parser(
        "summarize", help="score the full manifest locally; missing means zero"
    )
    summarize.add_argument("--run", type=Path, action="append", required=True)
    return parser


def _run_and_sample(parser: argparse.ArgumentParser) -> None:
    """Add the common prepared-run and isolated-sample arguments."""
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--sample", required=True)
