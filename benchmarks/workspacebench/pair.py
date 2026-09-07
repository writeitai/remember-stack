"""Paired native/memory execution from one pristine role workspace."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
import shutil
import tempfile
from typing import Any
from uuid import uuid4

from benchmarks.workspacebench.canary import LiveCanaryRunner
from benchmarks.workspacebench.canary import require_live_canary
from benchmarks.workspacebench.canary import run_live_canary
from benchmarks.workspacebench.canary import write_fake_secret
from benchmarks.workspacebench.codex import AccountReader
from benchmarks.workspacebench.codex import CodexTurnRunner
from benchmarks.workspacebench.codex import memory_arm_configuration
from benchmarks.workspacebench.codex import native_arm_configuration
from benchmarks.workspacebench.errors import LiveGateError
from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.fingerprint import arm_execution_fingerprint
from benchmarks.workspacebench.hashing import atomic_write_json
from benchmarks.workspacebench.hashing import require_absolute_path
from benchmarks.workspacebench.hashing import require_path_topology
from benchmarks.workspacebench.hashing import tree_digest
from benchmarks.workspacebench.isolation import inspect_bundled_codex_cli
from benchmarks.workspacebench.mcp import remember_launcher
from benchmarks.workspacebench.mcp import StdioRunner
from benchmarks.workspacebench.models import ArmConfiguration
from benchmarks.workspacebench.models import ArmName
from benchmarks.workspacebench.models import CanaryResult
from benchmarks.workspacebench.models import PairedRunManifest
from benchmarks.workspacebench.models import PreflightReport
from benchmarks.workspacebench.models import TaskResult
from benchmarks.workspacebench.office import stage_office_skills
from benchmarks.workspacebench.preflight import build_protocol_coordinates
from benchmarks.workspacebench.preflight import run_preflight
from benchmarks.workspacebench.protocol import DEFAULT_ARM_ORDER
from benchmarks.workspacebench.protocol import DEFAULT_GRACE_SEC
from benchmarks.workspacebench.protocol import DEFAULT_TASK_ID
from benchmarks.workspacebench.protocol import DEFAULT_TIMEOUT_SEC
from benchmarks.workspacebench.protocol import PROTOCOL_EXPERIMENTAL_LABEL
from benchmarks.workspacebench.runner import run_agent
from benchmarks.workspacebench.task_contract import load_upstream_task_contract
from benchmarks.workspacebench.task_contract import metadata_sha256
from benchmarks.workspacebench.task_contract import task_prompt_sha256
from benchmarks.workspacebench.task_contract import TaskContract
from benchmarks.workspacebench.upstream import GitCommand
from benchmarks.workspacebench.workspace import clone_pristine_workspace
from benchmarks.workspacebench.workspace import load_json_object
from benchmarks.workspacebench.workspace import stage_manifest_inputs


def parse_arm_order(value: str) -> tuple[ArmName, ArmName]:
    """Parse ``native,memory`` or ``memory,native``."""
    parts = tuple(part.strip() for part in value.split(",") if part.strip())
    if parts == ("native", "memory"):
        return ("native", "memory")
    if parts == ("memory", "native"):
        return ("memory", "native")
    raise WorkspaceBenchError("arm order must be native,memory or memory,native")


def run_pair(
    *,
    upstream: Path,
    workspace: Path,
    output: Path,
    task_id: str = DEFAULT_TASK_ID,
    task_dir: Path | None = None,
    receipt_path: Path | None = None,
    api_origin: str | None = None,
    access_mode: str = "direct",
    canonical_origin: str | None = None,
    task_prompt: str | None = None,
    execute: bool,
    arm_order: tuple[ArmName, ArmName] = DEFAULT_ARM_ORDER,
    timeout_seconds: float = DEFAULT_TIMEOUT_SEC,
    grace_seconds: float = DEFAULT_GRACE_SEC,
    account_reader: AccountReader | None = None,
    git: GitCommand | None = None,
    native_turn_runner: CodexTurnRunner | None = None,
    memory_turn_runner: CodexTurnRunner | None = None,
    stdio_runner: StdioRunner | None = None,
    live_canary_runner: LiveCanaryRunner | CodexTurnRunner | None = None,
    task_contract: TaskContract | None = None,
    remember_bin: Sequence[str] | str | None = None,
    skip_account: bool = False,
    skip_mcp: bool = False,
    skip_office: bool = False,
    mcp_env: Mapping[str, str] | None = None,
) -> tuple[PreflightReport, PairedRunManifest, TaskResult, TaskResult]:
    """Preflight once, clone the role workspace twice, run both arms."""
    require_absolute_path(upstream, label="upstream checkout")
    require_absolute_path(workspace, label="workspace")
    require_absolute_path(output, label="output")
    require_path_topology(
        {
            "upstream": upstream,
            "workspace": workspace,
            "task-dir": task_dir,
            "output": output,
        }
    )
    if execute and (skip_account or skip_mcp):
        raise LiveGateError("live run-pair --execute cannot skip account or MCP checks")
    if execute and skip_office:
        raise LiveGateError("live run-pair --execute cannot skip office-skill staging")
    if execute and task_dir is None:
        raise LiveGateError("live run-pair --execute requires --task-dir")
    output.mkdir(parents=True, exist_ok=True)
    preflight = run_preflight(
        upstream=upstream,
        workspace=workspace,
        output=output / "preflight",
        task_id=task_id,
        task_dir=task_dir,
        receipt_path=receipt_path,
        api_origin=api_origin,
        access_mode=access_mode,
        canonical_origin=canonical_origin,
        arm="memory",
        account_reader=account_reader,
        git=git,
        stdio_runner=stdio_runner,
        remember_bin=remember_bin,
        skip_account=skip_account,
        skip_mcp=skip_mcp,
        skip_office=skip_office,
        mcp_env=mcp_env,
    )
    atomic_write_json(path=output / "preflight.json", value=preflight)
    if not preflight.ok:
        raise WorkspaceBenchError("preflight failed: " + "; ".join(preflight.failures))
    if execute:
        _require_live_gates(preflight)
    metadata = _load_task_metadata(task_dir=task_dir)
    if task_dir is not None and preflight.protocol.task_metadata_sha256:
        observed_metadata_hash = metadata_sha256(metadata)
        if observed_metadata_hash != preflight.protocol.task_metadata_sha256:
            raise WorkspaceBenchError(
                "task metadata changed between preflight and staging"
            )
    if task_prompt is not None:
        prompt = task_prompt
        prompt_hash = task_prompt_sha256(task_prompt)
    else:
        prompt = str(metadata.get("task") or metadata.get("prompt") or "")
        prompt_hash = preflight.protocol.task_prompt_sha256
    if not prompt:
        raise WorkspaceBenchError("task prompt is empty; pass --prompt or --task-dir")
    contract = task_contract or load_upstream_task_contract(upstream=upstream)
    native_ws = output / "native" / "workspace"
    memory_ws = output / "memory" / "workspace"
    clone_pristine_workspace(source=workspace, destination=native_ws)
    clone_pristine_workspace(source=workspace, destination=memory_ws)
    if task_dir is not None:
        stage_manifest_inputs(
            source_task_dir=task_dir,
            destination=native_ws,
            metadata=metadata,
            contract=contract,
        )
        stage_manifest_inputs(
            source_task_dir=task_dir,
            destination=memory_ws,
            metadata=metadata,
            contract=contract,
        )
    if not skip_office:
        stage_office_skills(upstream=upstream, workspace=native_ws)
        stage_office_skills(upstream=upstream, workspace=memory_ws)
    native_digest = tree_digest(native_ws)
    memory_digest = tree_digest(memory_ws)
    if native_digest != memory_digest:
        raise WorkspaceBenchError("paired clones are not byte-identical after staging")
    native_arm = native_arm_configuration()
    enabled = preflight.mcp.enabled_tools if preflight.mcp is not None else ()
    local_origin = (
        None
        if preflight.mcp is None or preflight.mcp.access is None
        else preflight.mcp.access.local_access_origin
    )
    if local_origin is None and preflight.mcp is not None:
        local_origin = preflight.mcp.api_origin
    memory_arm = memory_arm_configuration(
        remember_bin=remember_bin or remember_launcher(),
        api_origin=local_origin,
        enabled_tools=enabled,
    )
    live_canary = None
    if execute:
        live_canary = _run_execute_canary(
            memory_arm=memory_arm, runner=live_canary_runner
        )
        require_live_canary(live_canary)
    protocol = build_protocol_coordinates(
        workspace_digest=native_digest,
        task_id=task_id,
        role_workspace_digest=preflight.workspace_digest,
        staged_workspace_digest=native_digest,
        data_manifest_sha256_value=preflight.data_manifest_sha256,
        task_prompt_sha256_value=prompt_hash,
        task_metadata_sha256_value=preflight.protocol.task_metadata_sha256,
        office_skill_digest=preflight.protocol.office_skill_digest,
        mcp_catalog_sha256=(
            None if preflight.mcp is None else preflight.mcp.catalog_sha256
        ),
        canonical_api_origin=preflight.protocol.canonical_api_origin,
        local_api_origin=preflight.protocol.local_api_origin,
        receipt=preflight.receipt,
        access=None if preflight.mcp is None else preflight.mcp.access,
        upstream_file_sha256=preflight.upstream.file_sha256,
        timeout_seconds=timeout_seconds,
        grace_seconds=grace_seconds,
        codex_cli_sha256=_preflight_cli_sha256(preflight),
    )
    native_fp = arm_execution_fingerprint(
        base_protocol_fingerprint=protocol.protocol_fingerprint, arm=native_arm
    )
    memory_fp = arm_execution_fingerprint(
        base_protocol_fingerprint=protocol.protocol_fingerprint,
        arm=memory_arm,
        mcp=preflight.mcp,
    )
    results: dict[ArmName, TaskResult] = {}
    for name in arm_order:
        if name == "native":
            results["native"] = run_agent(
                workspace=native_ws,
                output=output / "native",
                arm=native_arm,
                task_id=task_id,
                task_prompt=prompt,
                execute=execute,
                timeout_seconds=timeout_seconds,
                grace_seconds=grace_seconds,
                turn_runner=native_turn_runner,
                task_metadata=metadata,
                source_task_dir=task_dir,
                protocol=protocol.model_copy(
                    update={"arm_execution_fingerprint": native_fp}
                ),
                account=preflight.account,
                contract=contract,
                case_dir=output / "native",
            )
        else:
            results["memory"] = run_agent(
                workspace=memory_ws,
                output=output / "memory",
                arm=memory_arm,
                task_id=task_id,
                task_prompt=prompt,
                execute=execute,
                timeout_seconds=timeout_seconds,
                grace_seconds=grace_seconds,
                turn_runner=memory_turn_runner,
                task_metadata=metadata,
                source_task_dir=task_dir,
                protocol=protocol.model_copy(
                    update={"arm_execution_fingerprint": memory_fp}
                ),
                account=preflight.account,
                contract=contract,
                case_dir=output / "memory",
            )
    pair = PairedRunManifest(
        pair_id=uuid4().hex,
        task_id=task_id,
        arm_order=arm_order,
        role_workspace_digest=preflight.workspace_digest,
        native_workspace_digest=native_digest,
        memory_workspace_digest=memory_digest,
        native_result_relpath="native/result.json",
        memory_result_relpath="memory/result.json",
        experimental_label=PROTOCOL_EXPERIMENTAL_LABEL,
        protocol_fingerprint=protocol.protocol_fingerprint,
        native_arm_fingerprint=native_fp,
        memory_arm_fingerprint=memory_fp,
        live_canary=live_canary,
    )
    atomic_write_json(path=output / "pair.json", value=pair)
    return preflight, pair, results["native"], results["memory"]


def _require_live_gates(preflight: PreflightReport) -> None:
    if preflight.account is None:
        raise LiveGateError(
            "live run-pair --execute requires ChatGPT account attestation"
        )
    if preflight.mcp is None or not preflight.mcp.discovered_over_stdio:
        raise LiveGateError(
            "live run-pair --execute requires MCP discovered over real stdio "
            "of remember mcp --read-only"
        )
    expected = _preflight_cli_sha256(preflight)
    if expected is None:
        raise LiveGateError(
            "live run-pair --execute requires a Codex runtime pin "
            "(observed bundled CLI SHA-256)"
        )
    if preflight.protocol.codex_cli_sha256 != expected:
        raise LiveGateError(
            "live run-pair --execute requires the observed CLI SHA-256 "
            "on the protocol fingerprint"
        )
    observed = inspect_bundled_codex_cli(force=True)
    if observed.cli_sha256 != expected:
        raise LiveGateError(
            "bundled Codex CLI SHA-256 changed between preflight and execution"
        )


def _preflight_cli_sha256(preflight: PreflightReport) -> str | None:
    if preflight.codex_runtime is None:
        return None
    return preflight.codex_runtime.cli_sha256


def _run_execute_canary(
    *, memory_arm: ArmConfiguration, runner: LiveCanaryRunner | CodexTurnRunner | None
) -> CanaryResult:
    canary_root = Path(tempfile.mkdtemp(prefix="wb-canary-"))
    try:
        workspace = canary_root / "workspace"
        workspace.mkdir()
        fake_secret_path = write_fake_secret(directory=canary_root / "secret")
        canary_runner = runner
        if canary_runner is None:
            from benchmarks.workspacebench.runner import supervised_turn_runner

            canary_runner = supervised_turn_runner()
        return run_live_canary(
            workspace=workspace,
            artifact_root=workspace,
            fake_secret_path=fake_secret_path,
            runner=canary_runner,
            arm=memory_arm,
        )
    finally:
        shutil.rmtree(canary_root, ignore_errors=True)


def _load_task_metadata(*, task_dir: Path | None) -> dict[str, Any]:
    if task_dir is None:
        return {}
    require_absolute_path(task_dir, label="task directory")
    return load_json_object(task_dir / "metadata.json")
