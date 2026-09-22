"""Command line for the deliberately staged full-system LoCoMo harness."""

from __future__ import annotations

import argparse
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
import sys
from typing import cast
from typing import Literal

from benchmarks.locomo.ablation import AblationProfile
from benchmarks.locomo.ablation import review_retrieval_ablation
from benchmarks.locomo.ablation import run_retrieval_ablation
from benchmarks.locomo.model import ProviderKey
from benchmarks.locomo.protocol import API_TIMEOUT_SECONDS
from benchmarks.locomo.protocol import DEFAULT_PROTOCOL_KEY
from benchmarks.locomo.protocol import PROTOCOL_REGISTRY
from benchmarks.locomo.retrieval import RetrievalInfrastructureError
from benchmarks.locomo.runner import answer_sample
from benchmarks.locomo.runner import BenchmarkRunError
from benchmarks.locomo.runner import ingest_sample
from benchmarks.locomo.runner import judge_sample
from benchmarks.locomo.runner import prepare_run
from benchmarks.locomo.runner import run_protocol
from benchmarks.locomo.runner import summarize_run
from benchmarks.locomo.runner import summarize_runs
from rememberstack.adapters import build_generation_recorder
from rememberstack.adapters import CodexSubscriptionModelProvider
from rememberstack.adapters import GenerationRecorder
from rememberstack.adapters import LangfuseRecorderSettings
from rememberstack.adapters import ModelRoutedProvider
from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters import VertexModelProvider
from rememberstack.adapters import VertexSettings
from rememberstack.ports import ModelProviderPort
from rememberstack.surfaces.sdk import MemoryApiError
from rememberstack.surfaces.sdk import MemoryClient


def main(argv: list[str] | None = None) -> int:
    """Run one local or explicitly acknowledged remote benchmark stage."""
    parser = _parser()
    args = parser.parse_args(argv)
    recorder: GenerationRecorder | None = None
    try:
        recorder = build_generation_recorder(
            settings=LangfuseRecorderSettings.model_validate({})
        )
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
                    provider=_provider(
                        run_dir=args.run, stage="ingest", recorder=recorder
                    ),
                )
            for record in records:
                print(record.model_dump_json())
            return 0
        if args.command == "answer":
            provider = _provider(run_dir=args.run, stage="answer", recorder=recorder)
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
            answer_provider = (
                None
                if profile.startswith("codex-")
                else _seat_provider(provider_key="openrouter", recorder=recorder)
            )
            judge_provider = _seat_provider(
                provider_key="openrouter", recorder=recorder
            )
            if profile in {"codex-p3-mcp", "mcp", "mcp-p3"}:
                with MemoryClient(timeout=API_TIMEOUT_SECONDS) as client:
                    summary = run_retrieval_ablation(
                        run_dir=args.run,
                        sample_id=args.sample,
                        profile=profile,
                        output=args.output,
                        p3_root=args.p3_root,
                        max_questions=args.max_questions,
                        max_agent_calls=args.max_agent_calls,
                        max_judge_calls=args.max_judge_calls,
                        max_evaluator_cost_usd=args.max_evaluator_cost_usd,
                        execute=args.execute,
                        answer_provider=answer_provider,
                        judge_provider=judge_provider,
                        client=client,
                    )
            else:
                summary = run_retrieval_ablation(
                    run_dir=args.run,
                    sample_id=args.sample,
                    profile=profile,
                    output=args.output,
                    p3_root=args.p3_root,
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
    """Compose only the provider needed by this frozen protocol stage.

    Ingest uses OpenRouter for the deployment embedding preflight and routes
    its chat probe to the answer provider. Answer and judge select their
    independently pinned seats. Stage-local composition lets a
    Codex-subscription evaluation run without an OpenRouter key once its store
    has already been ingested.
    """
    protocol = run_protocol(run_dir=run_dir)
    codex_audit_path = run_dir / f"codex-runtime-{stage}.jsonl"
    if stage == "ingest":
        openrouter = _seat_provider(provider_key="openrouter", recorder=recorder)
        if protocol.answer_agent_provider == "openrouter":
            return openrouter
        answer_provider = _seat_provider(
            provider_key=protocol.answer_agent_provider,
            codex_audit_path=codex_audit_path,
            codex_audit_stage=stage,
            recorder=recorder,
        )
        return ModelRoutedProvider(
            routes={protocol.answer_agent_model: answer_provider}, default=openrouter
        )
    provider_key = (
        protocol.answer_agent_provider if stage == "answer" else protocol.judge_provider
    )
    return _seat_provider(
        provider_key=provider_key,
        codex_audit_path=codex_audit_path,
        codex_audit_stage=stage,
        recorder=recorder,
        chat_provider_only=(protocol.chat_provider_only if stage == "answer" else None),
    )


def _seat_provider(
    *,
    provider_key: ProviderKey,
    codex_audit_path: Path | None = None,
    codex_audit_stage: str = "generation",
    recorder: GenerationRecorder | None = None,
    chat_provider_only: tuple[str, ...] | None = None,
) -> ModelProviderPort:
    """Build one configured provider without reading unrelated credentials."""
    if provider_key == "openrouter":
        overrides: dict[str, object] = {}
        if chat_provider_only:
            overrides["chat_provider_only"] = list(chat_provider_only)
        return OpenRouterModelProvider(
            settings=OpenRouterSettings.model_validate(overrides), recorder=recorder
        )
    if provider_key == "vertex":
        return VertexModelProvider(
            settings=VertexSettings.model_validate({}), recorder=recorder
        )
    return CodexSubscriptionModelProvider(
        audit_path=codex_audit_path, audit_stage=codex_audit_stage
    )


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
            "RS-LoCoMo-Full-v38: prepare is local; ingest/answer/judge require "
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

    ablation = commands.add_parser(
        "retrieval-ablation",
        help="run one answer-and-judge retrieval-access ablation arm",
    )
    _run_and_sample(ablation)
    ablation.add_argument(
        "--profile",
        choices=("codex-p3", "codex-p3-mcp", "mcp", "mcp-p3"),
        required=True,
    )
    ablation.add_argument("--output", type=Path, required=True)
    ablation.add_argument(
        "--p3-root",
        type=Path,
        help="ordinary local P3 directory; required only by profiles containing p3",
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
