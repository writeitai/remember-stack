"""Command line for the experimental Workspace-Bench cloud/local smoke."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from benchmarks.workspacebench.codex import memory_arm_configuration
from benchmarks.workspacebench.codex import native_arm_configuration
from benchmarks.workspacebench.errors import LiveGateError
from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import atomic_write_json
from benchmarks.workspacebench.judge import official_judge_command
from benchmarks.workspacebench.mcp import remember_launcher
from benchmarks.workspacebench.pair import parse_arm_order
from benchmarks.workspacebench.pair import run_pair
from benchmarks.workspacebench.preflight import run_preflight
from benchmarks.workspacebench.protocol import DEFAULT_ARM_ORDER
from benchmarks.workspacebench.protocol import DEFAULT_GRACE_SEC
from benchmarks.workspacebench.protocol import DEFAULT_TASK_ID
from benchmarks.workspacebench.protocol import DEFAULT_TIMEOUT_SEC
from benchmarks.workspacebench.report import reconstruct_from_output_dir
from benchmarks.workspacebench.report import write_paired_report
from benchmarks.workspacebench.runner import run_agent
from benchmarks.workspacebench.workspace import load_json_object


def main(argv: list[str] | None = None) -> int:
    """Run preflight, one arm, or a paired native/memory attempt."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            report = run_preflight(
                upstream=args.upstream,
                workspace=args.workspace,
                output=args.output,
                task_id=args.task_id,
                task_dir=args.task_dir,
                receipt_path=args.receipt,
                api_origin=args.api_url,
                access_mode=args.access_mode,
                canonical_origin=args.canonical_origin,
                arm=args.arm,
                skip_account=args.skip_account,
                skip_mcp=args.skip_mcp,
                skip_office=args.skip_office,
            )
            atomic_write_json(path=args.output / "preflight.json", value=report)
            print(report.model_dump_json())
            return 0 if report.ok else 1
        if args.command == "run-agent":
            if args.execute:
                raise LiveGateError(
                    "run-agent --execute is not a supported live entry; "
                    "use run-pair --execute so receipt, account, stdio MCP, "
                    "and live canary gates run first. Programmatic run_agent "
                    "with an injected turn runner remains available for tests."
                )
            prompt = args.prompt
            if not prompt and args.task_dir is not None:
                metadata = load_json_object(args.task_dir / "metadata.json")
                prompt = str(metadata.get("task") or metadata.get("prompt") or "")
            if not prompt:
                raise WorkspaceBenchError(
                    "task prompt is empty; pass --prompt or --task-dir"
                )
            arm = (
                native_arm_configuration()
                if args.arm == "native"
                else memory_arm_configuration(
                    remember_bin=remember_launcher(),
                    api_origin=args.api_url,
                    enabled_tools=tuple(args.enabled_tools or ()),
                )
            )
            metadata = None
            if args.task_dir is not None:
                metadata = load_json_object(args.task_dir / "metadata.json")
            result = run_agent(
                workspace=args.workspace,
                output=args.output,
                arm=arm,
                task_id=args.task_id,
                task_prompt=prompt,
                execute=args.execute,
                timeout_seconds=args.timeout_seconds,
                grace_seconds=args.grace_seconds,
                task_metadata=metadata,
                source_task_dir=args.task_dir,
                case_dir=args.output,
            )
            print(result.model_dump_json())
            return 0 if result.failure_class == "none" else 1
        if args.command == "run-pair":
            prompt = args.prompt
            preflight, pair, native, memory = run_pair(
                upstream=args.upstream,
                workspace=args.workspace,
                output=args.output,
                task_id=args.task_id,
                task_dir=args.task_dir,
                receipt_path=args.receipt,
                api_origin=args.api_url,
                access_mode=args.access_mode,
                canonical_origin=args.canonical_origin,
                task_prompt=prompt,
                execute=args.execute,
                arm_order=parse_arm_order(args.arm_order)
                if args.arm_order
                else DEFAULT_ARM_ORDER,
                timeout_seconds=args.timeout_seconds,
                grace_seconds=args.grace_seconds,
                skip_account=args.skip_account,
                skip_mcp=args.skip_mcp,
                skip_office=args.skip_office,
            )
            report = reconstruct_from_output_dir(args.output)
            write_paired_report(output=args.output, report=report)
            if args.eval_yaml is not None:
                eval_root = args.upstream / "evaluation"
                for name, case in (
                    ("native", args.output / "native"),
                    ("memory", args.output / "memory"),
                ):
                    command = official_judge_command(
                        eval_root=eval_root, task_dir=case, eval_yaml=args.eval_yaml
                    )
                    print(f"# {name} official judge (not executed):")
                    print(" ".join(command))
            print(preflight.model_dump_json())
            print(pair.model_dump_json())
            print(native.model_dump_json())
            print(memory.model_dump_json())
            print(report.model_dump_json())
            return 0 if preflight.ok else 1
    except (LiveGateError, WorkspaceBenchError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    parser.print_help()
    return 2


def _absolute_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError(f"must be an absolute path: {value}")
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.workspacebench",
        description=(
            "Experimental Workspace-Bench Codex-subscription cloud/local adapter. "
            "preflight is no-spend; live ChatGPT-subscription tasks use "
            "run-pair --execute. run-agent --execute is rejected; run-pair is "
            "the only supported live entry. --execute also authorizes the live "
            "credential-isolation canary turn."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    preflight = commands.add_parser(
        "preflight", help="verify pins, receipt, account, and MCP without spend"
    )
    _shared_paths(preflight)
    preflight.add_argument("--arm", choices=("native", "memory"), default="memory")
    _account_mcp_flags(preflight)
    _access_flags(preflight)

    agent = commands.add_parser(
        "run-agent",
        help="dry-run one arm against an already prepared workspace (no --execute)",
    )
    agent.add_argument("--workspace", type=_absolute_path, required=True)
    agent.add_argument("--output", type=_absolute_path, required=True)
    agent.add_argument("--task-id", default=DEFAULT_TASK_ID)
    agent.add_argument("--task-dir", type=_absolute_path)
    agent.add_argument("--arm", choices=("native", "memory"), required=True)
    agent.add_argument("--prompt")
    agent.add_argument("--api-url")
    agent.add_argument("--enabled-tools", nargs="*", default=None)
    agent.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SEC)
    agent.add_argument("--grace-seconds", type=float, default=DEFAULT_GRACE_SEC)
    agent.add_argument(
        "--execute",
        action="store_true",
        help="rejected: live execution is only supported through run-pair --execute",
    )

    pair = commands.add_parser(
        "run-pair", help="preflight once and run native plus memory arms"
    )
    _shared_paths(pair)
    pair.add_argument("--prompt")
    pair.add_argument(
        "--arm-order",
        default=",".join(DEFAULT_ARM_ORDER),
        help="native,memory or memory,native",
    )
    pair.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SEC)
    pair.add_argument("--grace-seconds", type=float, default=DEFAULT_GRACE_SEC)
    pair.add_argument("--eval-yaml", type=_absolute_path)
    pair.add_argument(
        "--execute",
        action="store_true",
        help=(
            "authorize live Codex ChatGPT-subscription task sessions and the "
            "live credential-isolation canary turn"
        ),
    )
    _account_mcp_flags(pair)
    _access_flags(pair)
    return parser


def _shared_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--upstream", type=_absolute_path, required=True)
    parser.add_argument(
        "--workspace",
        type=_absolute_path,
        required=True,
        help="complete pristine role workspace (not the task corpus)",
    )
    parser.add_argument("--output", type=_absolute_path, required=True)
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument(
        "--task-dir",
        type=_absolute_path,
        help="separate task corpus directory containing metadata.json and data_manifest files",
    )
    parser.add_argument("--receipt", type=_absolute_path)
    parser.add_argument("--api-url")


def _access_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--access-mode",
        choices=("direct", "ssh_local_forward"),
        default="direct",
        help=(
            "direct: --api-url must equal the receipt canonical origin. "
            "ssh_local_forward: --api-url is the local loopback endpoint and "
            "--canonical-origin must equal the receipt origin"
        ),
    )
    parser.add_argument(
        "--canonical-origin",
        help="receipt canonical deployment API origin; required for ssh_local_forward",
    )


def _account_mcp_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--skip-account",
        action="store_true",
        help="skip Codex account attestation (dry preflight and tests only; rejected by --execute)",
    )
    parser.add_argument(
        "--skip-mcp",
        action="store_true",
        help="skip remote MCP stdio discovery (dry preflight and tests only; rejected by --execute)",
    )
    parser.add_argument(
        "--skip-office",
        action="store_true",
        help="skip office-skill tree and host executable checks (tests only)",
    )
