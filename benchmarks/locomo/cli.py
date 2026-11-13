"""Command line for the deliberately staged full-system LoCoMo harness."""

from __future__ import annotations

import argparse
from decimal import Decimal
from decimal import InvalidOperation
import math
from pathlib import Path
import sys
from typing import cast
from typing import Literal

from benchmarks.locomo.ablation import AblationProfile
from benchmarks.locomo.ablation import review_retrieval_ablation
from benchmarks.locomo.ablation import run_retrieval_ablation
from benchmarks.locomo.image_preparation import prepare_images_command
from benchmarks.locomo.protocol import DEFAULT_PROTOCOL_KEY
from benchmarks.locomo.protocol import protocol_for_key
from benchmarks.locomo.protocol import PROTOCOL_NAME
from benchmarks.locomo.protocol import PROTOCOL_REGISTRY
from benchmarks.locomo.retrieval import RetrievalInfrastructureError
from benchmarks.locomo.reuse import prepare_reused_run
from benchmarks.locomo.runner import answer_sample
from benchmarks.locomo.runner import BenchmarkRunError
from benchmarks.locomo.runner import ExecutionGuardError
from benchmarks.locomo.runner import ingest_sample
from benchmarks.locomo.runner import judge_sample
from benchmarks.locomo.runner import prepare_run
from benchmarks.locomo.runner import run_api_timeout_seconds
from benchmarks.locomo.runner import run_protocol
from benchmarks.locomo.runner import summarize_run
from benchmarks.locomo.runner import summarize_runs
from remember.client import MemoryClient
from remember.errors import MemoryApiError
from remember.retrieval import ContextRetrievalBudget
from rememberstack.adapters import build_generation_recorder
from rememberstack.adapters import GenerationRecorder
from rememberstack.adapters import LangfuseRecorderSettings
from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import OpenRouterSettings
from rememberstack.ports import ModelProviderPort


def main(argv: list[str] | None = None) -> int:
    """Run one local or explicitly acknowledged remote benchmark stage."""
    parser = _parser()
    args = parser.parse_args(argv)
    recorder: GenerationRecorder | None = None
    try:
        recorder = build_generation_recorder(
            settings=LangfuseRecorderSettings.model_validate({})
        )
        if args.command == "prepare-images":
            summary = prepare_images_command(
                dataset_path=args.dataset,
                tier=args.tier,
                output=args.output,
                cache_dir=args.cache,
                execute=args.execute,
                max_conversions=args.max_conversions,
                max_cost_usd=args.max_cost_usd,
                workers=args.workers,
                allow_unavailable=args.allow_unavailable,
            )
            print(summary.model_dump_json())
            return 0
        if args.command == "prepare":
            configuration = prepare_run(
                dataset_path=args.dataset,
                tier=args.tier,
                output=args.output,
                protocol=args.protocol,
                images=args.images,
                retrieval_budget=ContextRetrievalBudget(
                    k=args.retrieval_k, candidate_k=args.retrieval_candidate_k
                ),
            )
            print(configuration.model_dump_json())
            return 0
        if args.command == "reuse-ingestion":
            with MemoryClient(
                timeout=protocol_for_key(
                    DEFAULT_PROTOCOL_KEY
                ).execution_profile.api_timeout_seconds
            ) as client:
                configuration = prepare_reused_run(
                    source_run=args.source_run,
                    output=args.output,
                    sample_id=args.sample,
                    execute=args.execute,
                    client=client,
                )
            print(configuration.model_dump_json())
            return 0
        if args.command == "ingest":
            with MemoryClient(
                timeout=run_api_timeout_seconds(run_dir=args.run)
            ) as client:
                records = ingest_sample(
                    run_dir=args.run,
                    sample_id=args.sample,
                    max_documents=args.max_documents,
                    max_evaluator_cost_usd=args.max_evaluator_cost_usd,
                    execute=args.execute,
                    isolated_deployment_confirmation=(args.confirm_isolated_deployment),
                    client=client,
                    provider=_provider(
                        run_dir=args.run, stage="ingest", recorder=recorder
                    ),
                )
            for record in records:
                print(record.model_dump_json())
            return 0
        if args.command == "answer":
            provider = _provider(run_dir=args.run, stage="answer", recorder=recorder)
            with MemoryClient(
                timeout=run_api_timeout_seconds(run_dir=args.run)
            ) as client:
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
                provider=_provider(run_dir=args.run, stage="judge", recorder=recorder),
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
        if args.command == "retrieval-ablation":
            profile = cast(AblationProfile, args.profile)
            if args.api_timeout_seconds is not None and profile != "full":
                raise ExecutionGuardError(
                    "--api-timeout-seconds requires --profile full"
                )
            full_policy = (
                protocol_for_key(
                    DEFAULT_PROTOCOL_KEY
                ).execution_profile.chat_provider_only
                if profile == "full"
                else None
            )
            answer_provider = (
                None
                if profile.startswith("codex-")
                else _seat_provider(
                    chat_provider_only=(
                        tuple(args.answer_provider)
                        if args.answer_provider is not None
                        else full_policy
                    ),
                    recorder=recorder,
                )
                if args.answer_provider is not None or profile == "full"
                else _provider(run_dir=args.run, stage="answer", recorder=recorder)
            )
            judge_provider = (
                _seat_provider(chat_provider_only=full_policy, recorder=recorder)
                if profile == "full"
                else _provider(run_dir=args.run, stage="judge", recorder=recorder)
            )
            if profile in {"codex-p3-mcp", "mcp", "mcp-p3", "full"}:
                with MemoryClient(
                    timeout=(
                        args.api_timeout_seconds
                        if args.api_timeout_seconds is not None
                        else run_api_timeout_seconds(run_dir=args.run)
                    )
                ) as client:
                    summary = run_retrieval_ablation(
                        run_dir=args.run,
                        sample_id=args.sample,
                        profile=profile,
                        output=args.output,
                        p3_root=args.p3_root,
                        answer_prompt=args.answer_prompt,
                        answer_model=args.answer_model,
                        answer_workers=args.answer_workers,
                        judge_model=args.judge_model,
                        use_current_judge_prompt=args.use_current_judge_prompt,
                        item_ids=None if args.item is None else tuple(args.item),
                        answer_provider_only=None
                        if args.answer_provider is None
                        else tuple(args.answer_provider),
                        max_questions=args.max_questions,
                        max_agent_calls=args.max_agent_calls,
                        max_judge_calls=args.max_judge_calls,
                        max_evaluator_cost_usd=args.max_evaluator_cost_usd,
                        execute=args.execute,
                        answer_provider=answer_provider,
                        judge_provider=judge_provider,
                        client=client,
                        api_timeout_seconds=args.api_timeout_seconds,
                    )
            else:
                summary = run_retrieval_ablation(
                    run_dir=args.run,
                    sample_id=args.sample,
                    profile=profile,
                    output=args.output,
                    p3_root=args.p3_root,
                    answer_prompt=args.answer_prompt,
                    answer_model=args.answer_model,
                    answer_workers=args.answer_workers,
                    judge_model=args.judge_model,
                    use_current_judge_prompt=args.use_current_judge_prompt,
                    item_ids=None if args.item is None else tuple(args.item),
                    answer_provider_only=None
                    if args.answer_provider is None
                    else tuple(args.answer_provider),
                    max_questions=args.max_questions,
                    max_agent_calls=args.max_agent_calls,
                    max_judge_calls=args.max_judge_calls,
                    max_evaluator_cost_usd=args.max_evaluator_cost_usd,
                    execute=args.execute,
                    answer_provider=None,
                    judge_provider=judge_provider,
                    client=None,
                )
            print(summary.model_dump_json())
            return 0
        if args.command == "retrieval-ablation-review":
            summary = review_retrieval_ablation(
                output=args.output, status=args.status, note=args.note
            )
            print(summary.model_dump_json())
            return 0
    except (
        BenchmarkRunError,
        MemoryApiError,
        OSError,
        RetrievalInfrastructureError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        if recorder is not None:
            recorder.flush()
    parser.print_help()
    return 2


ProviderStage = Literal["ingest", "answer", "judge"]


def _provider(
    *, run_dir: Path, stage: ProviderStage, recorder: GenerationRecorder | None = None
) -> ModelProviderPort:
    """Compose the current run's explicit OpenRouter/Flex evaluation binding."""
    protocol = run_protocol(run_dir=run_dir)
    profile = protocol.execution_profile
    return _seat_provider(
        chat_provider_only=profile.chat_provider_only, recorder=recorder
    )


def _seat_provider(
    *,
    recorder: GenerationRecorder | None = None,
    chat_provider_only: tuple[str, ...] | None = None,
) -> ModelProviderPort:
    """Build the current OpenRouter binding with an explicit optional shortlist."""
    return OpenRouterModelProvider(
        settings=OpenRouterSettings.model_validate(
            {
                "chat_provider_only": list(chat_provider_only),
                "chat_provider_order": (
                    list(chat_provider_only) if len(chat_provider_only) > 1 else None
                ),
            }
            if chat_provider_only is not None
            else {}
        ),
        recorder=recorder,
    )


def _positive_seconds(value: str) -> float:
    """Parse a finite positive request timeout before constructing any client."""
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive and finite")
    return parsed


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
            f"{PROTOCOL_NAME}: prepare is local; ingest/answer/judge require "
            "explicit execution acknowledgements"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    images = commands.add_parser(
        "prepare-images",
        help="plan or explicitly run metered OCR/Luna source-image preparation",
    )
    images.add_argument("--dataset", type=Path, required=True)
    images.add_argument(
        "--tier", choices=("smoke", "development", "publication"), required=True
    )
    images.add_argument("--cache", type=Path, required=True)
    images.add_argument("--output", type=Path, required=True)
    images.add_argument("--execute", action="store_true")
    images.add_argument(
        "--max-conversions",
        type=int,
        required=True,
        help="cache-absolute converter-attempt ceiling, including failures",
    )
    images.add_argument(
        "--max-cost-usd",
        type=_positive_decimal,
        required=True,
        help="cache-absolute recorded-spend admission stop; admitted calls finish",
    )
    images.add_argument(
        "--workers",
        type=int,
        default=2,
        help="concurrent source images, each with OCR and vision lanes (1-16)",
    )
    images.add_argument(
        "--allow-unavailable",
        action="store_true",
        help="explicitly record unavailable originals with coverage markers; provider failures still stop",
    )

    prepare = commands.add_parser(
        "prepare", help="validate and render a local run (no API/model calls)"
    )
    prepare.add_argument("--dataset", type=Path, required=True)
    prepare.add_argument(
        "--tier", choices=("smoke", "development", "publication"), required=True
    )
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument(
        "--images",
        type=Path,
        help="frozen prepare-images bundle; required when source turns contain image URLs",
    )
    prepare.add_argument(
        "--protocol", choices=tuple(PROTOCOL_REGISTRY), default=DEFAULT_PROTOCOL_KEY
    )

    prepare.add_argument(
        "--retrieval-k",
        type=int,
        default=50,
        help="confirmed results per type in context tools (1-100; default 50)",
    )
    prepare.add_argument(
        "--retrieval-candidate-k",
        type=int,
        help="internal candidates per search channel (1-400; default max(4*k, 60))",
    )

    reuse = commands.add_parser(
        "reuse-ingestion",
        help="derive a current serving execution from compatible recorded ingestion",
    )
    reuse.add_argument("--source-run", type=Path, required=True)
    reuse.add_argument("--output", type=Path, required=True)
    reuse.add_argument("--sample", required=True)
    reuse.add_argument("--execute", action="store_true")

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
        help="live P3 directory whose ready folder identities must match per-version publication markers",
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

    ablation = commands.add_parser(
        "retrieval-ablation",
        help="run one answer-and-judge retrieval-access ablation arm",
    )
    _run_and_sample(ablation)
    ablation.add_argument(
        "--profile",
        choices=("codex-p3", "codex-p3-mcp", "mcp", "mcp-p3", "full"),
        required=True,
    )
    ablation.add_argument("--output", type=Path, required=True)
    ablation.add_argument(
        "--p3-root",
        type=Path,
        help="ordinary local P3 directory; required by full and profiles containing p3",
    )
    ablation.add_argument(
        "--answer-prompt",
        type=Path,
        help="UTF-8 answer template for the full profile; frozen into the output",
    )
    ablation.add_argument(
        "--answer-model",
        help="explicit answer model for the full profile; frozen into experiment identity",
    )
    ablation.add_argument(
        "--judge-model",
        help="explicit judge model for the full profile; frozen into experiment identity",
    )
    ablation.add_argument(
        "--use-current-judge-prompt",
        action="store_true",
        help="explicitly re-score a compatible source with the current judge prompt; recorded in identity",
    )
    ablation.add_argument(
        "--item",
        action="append",
        help="selected sample item ID; repeat for a development subset (default all items)",
    )
    ablation.add_argument(
        "--answer-workers",
        type=int,
        default=1,
        help="concurrent full-profile questions (1-16, default 1); recorded in identity",
    )
    ablation.add_argument(
        "--api-timeout-seconds",
        type=_positive_seconds,
        help="explicit full-profile API request timeout; frozen into experiment identity; default source timeout",
    )
    ablation.add_argument(
        "--answer-provider",
        action="append",
        help="ordered full-profile answer-provider allowlist; repeat to allow multiple endpoints",
    )
    ablation.add_argument(
        "--max-questions",
        type=int,
        required=True,
        help="run-absolute authorization; must cover every selected sample item",
    )
    ablation.add_argument(
        "--max-agent-calls",
        type=int,
        required=True,
        help="run-absolute answer-model ceiling; allow headroom for interrupted calls",
    )
    ablation.add_argument(
        "--max-judge-calls",
        type=int,
        required=True,
        help="run-absolute judge ceiling; allow headroom for interrupted calls",
    )
    ablation.add_argument(
        "--max-evaluator-cost-usd",
        type=_positive_decimal,
        required=True,
        help="run-absolute reported-spend stop; provider caps remain the hard boundary",
    )
    ablation.add_argument("--execute", action="store_true")

    review = commands.add_parser(
        "retrieval-ablation-review",
        help="record the manual native-Codex action-audit verdict",
    )
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--status", choices=("clean", "invalid"), required=True)
    review.add_argument("--note")
    return parser


def _run_and_sample(parser: argparse.ArgumentParser) -> None:
    """Add the common prepared-run and isolated-sample arguments."""
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--sample", required=True)
