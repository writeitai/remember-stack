"""No-spend preflight: pins, receipts, account, MCP stdio, structural canary."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
import json
from pathlib import Path

from benchmarks.workspacebench.canary import evaluate_canary
from benchmarks.workspacebench.canary import write_fake_secret
from benchmarks.workspacebench.codex import AccountReader
from benchmarks.workspacebench.codex import attest_chatgpt_account
from benchmarks.workspacebench.codex import memory_arm_configuration
from benchmarks.workspacebench.codex import native_arm_configuration
from benchmarks.workspacebench.consumption import consumption_instruction_sha256
from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.fingerprint import protocol_fingerprint
from benchmarks.workspacebench.hashing import require_absolute_path
from benchmarks.workspacebench.hashing import require_path_topology
from benchmarks.workspacebench.hashing import sha256_bytes
from benchmarks.workspacebench.hashing import tree_digest
from benchmarks.workspacebench.isolation import inspect_bundled_codex_cli
from benchmarks.workspacebench.mcp import access_binding_from_args
from benchmarks.workspacebench.mcp import discover_mcp
from benchmarks.workspacebench.mcp import remember_launcher
from benchmarks.workspacebench.mcp import StdioRunner
from benchmarks.workspacebench.models import ArmName
from benchmarks.workspacebench.models import CloudDeploymentReceipt
from benchmarks.workspacebench.models import CodexRuntimePin
from benchmarks.workspacebench.models import McpAccessBinding
from benchmarks.workspacebench.models import PreflightReport
from benchmarks.workspacebench.models import ProtocolCoordinates
from benchmarks.workspacebench.office import inspect_office
from benchmarks.workspacebench.overlay import overlay_manifest_sha256
from benchmarks.workspacebench.protocol import ADAPTER_VERSION
from benchmarks.workspacebench.protocol import AUTOMATIC_LIVE_GATES
from benchmarks.workspacebench.protocol import CODEX_CLI_VERSION
from benchmarks.workspacebench.protocol import CODEX_CLIENT_NAME
from benchmarks.workspacebench.protocol import CODEX_CLIENT_TITLE
from benchmarks.workspacebench.protocol import CODEX_CREDENTIALS_STORE
from benchmarks.workspacebench.protocol import CODEX_EXECUTION_BACKEND
from benchmarks.workspacebench.protocol import CODEX_ISOLATION
from benchmarks.workspacebench.protocol import CODEX_MODEL
from benchmarks.workspacebench.protocol import CODEX_REASONING_EFFORT
from benchmarks.workspacebench.protocol import CODEX_SDK_VERSION
from benchmarks.workspacebench.protocol import CONSUMPTION_INSTRUCTION_VERSION
from benchmarks.workspacebench.protocol import DEFAULT_GRACE_SEC
from benchmarks.workspacebench.protocol import DEFAULT_TASK_ID
from benchmarks.workspacebench.protocol import DEFAULT_TIMEOUT_SEC
from benchmarks.workspacebench.protocol import LIVE_GATES
from benchmarks.workspacebench.protocol import PROTOCOL_EXPERIMENTAL_LABEL
from benchmarks.workspacebench.protocol import PROTOCOL_NAME
from benchmarks.workspacebench.protocol import SDK_TIMEOUT_PARTIAL_EVENTS
from benchmarks.workspacebench.protocol import UPSTREAM_COMMIT
from benchmarks.workspacebench.receipts import load_cloud_receipt
from benchmarks.workspacebench.receipts import validate_receipt_against_workspace
from benchmarks.workspacebench.task_contract import metadata_sha256
from benchmarks.workspacebench.task_contract import required_task_id
from benchmarks.workspacebench.task_contract import task_prompt_sha256
from benchmarks.workspacebench.task_contract import validate_data_manifest
from benchmarks.workspacebench.upstream import GitCommand
from benchmarks.workspacebench.upstream import inspect_upstream
from benchmarks.workspacebench.workspace import data_manifest_sha256
from benchmarks.workspacebench.workspace import load_json_object


def build_protocol_coordinates(
    *,
    workspace_digest: str,
    task_id: str,
    role_workspace_digest: str | None = None,
    staged_workspace_digest: str | None = None,
    data_manifest_sha256_value: str | None = None,
    task_prompt_sha256_value: str | None = None,
    task_metadata_sha256_value: str | None = None,
    office_skill_digest: str | None = None,
    mcp_catalog_sha256: str | None = None,
    canonical_api_origin: str | None = None,
    local_api_origin: str | None = None,
    receipt: CloudDeploymentReceipt | None = None,
    access: McpAccessBinding | None = None,
    upstream_file_sha256: Mapping[str, str] | None = None,
    arm_execution_fingerprint: str | None = None,
    timeout_seconds: float | None = None,
    grace_seconds: float | None = None,
    codex_cli_sha256: str | None = None,
) -> ProtocolCoordinates:
    """Assemble the pinned protocol identity once coordinates are known."""
    timeout = DEFAULT_TIMEOUT_SEC if timeout_seconds is None else timeout_seconds
    grace = DEFAULT_GRACE_SEC if grace_seconds is None else grace_seconds
    fingerprint = protocol_fingerprint(
        workspace_digest=workspace_digest,
        task_id=task_id,
        role_workspace_digest=role_workspace_digest,
        staged_workspace_digest=staged_workspace_digest,
        data_manifest_sha256=data_manifest_sha256_value,
        task_prompt_sha256=task_prompt_sha256_value,
        task_metadata_sha256=task_metadata_sha256_value,
        office_skill_digest=office_skill_digest,
        overlay_sha256=overlay_manifest_sha256(),
        upstream_file_sha256=upstream_file_sha256,
        canonical_api_origin=canonical_api_origin,
        local_api_origin=local_api_origin,
        receipt=receipt,
        access=access,
        mcp_catalog_sha256=mcp_catalog_sha256,
        timeout_seconds=timeout,
        grace_seconds=grace,
        codex_cli_sha256=codex_cli_sha256,
    )
    receipt_hash = None
    if receipt is not None:
        receipt_hash = sha256_bytes(
            json.dumps(
                receipt.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        )
    return ProtocolCoordinates(
        protocol_name=PROTOCOL_NAME,
        protocol_fingerprint=fingerprint,
        experimental_label=PROTOCOL_EXPERIMENTAL_LABEL,
        adapter_version=ADAPTER_VERSION,
        upstream_commit=UPSTREAM_COMMIT,
        overlay_manifest_sha256=overlay_manifest_sha256(),
        workspace_digest=workspace_digest,
        role_workspace_digest=role_workspace_digest,
        staged_workspace_digest=staged_workspace_digest,
        data_manifest_sha256=data_manifest_sha256_value,
        task_prompt_sha256=task_prompt_sha256_value,
        task_metadata_sha256=task_metadata_sha256_value,
        office_skill_digest=office_skill_digest,
        mcp_catalog_sha256=mcp_catalog_sha256,
        canonical_api_origin=canonical_api_origin,
        local_api_origin=local_api_origin,
        receipt_deployment_id=None if receipt is None else receipt.deployment_id,
        receipt_revision=None if receipt is None else receipt.rememberstack_revision,
        receipt_sha256=receipt_hash,
        arm_execution_fingerprint=arm_execution_fingerprint,
        timeout_seconds=timeout,
        grace_seconds=grace,
        task_id=task_id,
        model=CODEX_MODEL,
        reasoning_effort=CODEX_REASONING_EFFORT,
        client_name=CODEX_CLIENT_NAME,
        client_title=CODEX_CLIENT_TITLE,
        codex_execution_backend=CODEX_EXECUTION_BACKEND,
        codex_sdk_version=CODEX_SDK_VERSION,
        codex_cli_version=CODEX_CLI_VERSION,
        codex_cli_sha256=codex_cli_sha256,
        codex_isolation=CODEX_ISOLATION,
        codex_credentials_store=CODEX_CREDENTIALS_STORE,
        consumption_instruction_version=CONSUMPTION_INSTRUCTION_VERSION,
        consumption_instruction_sha256=consumption_instruction_sha256(),
        sdk_timeout_partial_events=SDK_TIMEOUT_PARTIAL_EVENTS,
    )


def run_preflight(
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
    arm: ArmName = "memory",
    account_reader: AccountReader | None = None,
    git: GitCommand | None = None,
    stdio_runner: StdioRunner | None = None,
    remember_bin: Sequence[str] | str | None = None,
    skip_account: bool = False,
    skip_mcp: bool = False,
    skip_office: bool = False,
    mcp_env: Mapping[str, str] | None = None,
) -> PreflightReport:
    """Validate pins without downloading datasets or calling a model.

    Dry preflight is no-spend. The canary recorded here is structural: it does
    not authorize a Codex subscription turn.
    """
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
    failures: list[str] = []
    output.mkdir(parents=True, exist_ok=True)
    upstream_inspection = None
    receipt: CloudDeploymentReceipt | None = None
    account = None
    mcp = None
    office = None
    access: McpAccessBinding | None = None
    manifest_hash = None
    metadata_hash = None
    prompt_hash = None
    try:
        upstream_inspection = inspect_upstream(path=upstream, git=git)
    except WorkspaceBenchError as error:
        failures.append(str(error))
    workspace_digest = tree_digest(workspace)
    if task_dir is not None:
        require_absolute_path(task_dir, label="task directory")
        metadata = load_json_object(task_dir / "metadata.json")
        try:
            observed_id = required_task_id(metadata)
        except WorkspaceBenchError as error:
            failures.append(str(error))
            observed_id = ""
        if observed_id and observed_id != task_id:
            failures.append(
                f"task directory id {observed_id!r} does not match requested {task_id!r}"
            )
        try:
            validate_data_manifest(
                metadata=metadata, source_task_dir=task_dir, destination=None
            )
        except WorkspaceBenchError as error:
            failures.append(str(error))
        manifest_hash = data_manifest_sha256(metadata)
        metadata_hash = metadata_sha256(metadata)
        prompt = str(metadata.get("task") or metadata.get("prompt") or "")
        if prompt:
            prompt_hash = task_prompt_sha256(prompt)
    if receipt_path is not None:
        try:
            receipt = load_cloud_receipt(receipt_path)
            origin = api_origin or receipt.api_origin
            access = access_binding_from_args(
                api_origin=origin,
                receipt_origin=receipt.api_origin,
                access_mode=access_mode,
                canonical_origin=canonical_origin,
            )
            validate_receipt_against_workspace(
                receipt=receipt,
                workspace_digest=workspace_digest,
                api_origin=origin,
                access=access,
            )
        except WorkspaceBenchError as error:
            failures.append(str(error))
    elif arm == "memory":
        failures.append("memory arm preflight requires a cloud deployment receipt")
    runtime_pin = None
    try:
        inspected = inspect_bundled_codex_cli()
        runtime_pin = CodexRuntimePin(
            sdk_version=inspected.sdk_version,
            cli_version=inspected.cli_version,
            cli_sha256=inspected.cli_sha256,
            isolation=CODEX_ISOLATION,
            credentials_store=CODEX_CREDENTIALS_STORE,
        )
    except WorkspaceBenchError as error:
        failures.append(str(error))
    if not skip_account:
        try:
            account = attest_chatgpt_account(reader=account_reader)
        except WorkspaceBenchError as error:
            failures.append(str(error))
    if arm == "memory" and not skip_mcp:
        if api_origin is None or receipt is None or access is None:
            failures.append(
                "memory arm MCP discovery requires --api-url, a receipt, and an "
                "access binding; credentials come from remember login"
            )
        else:
            try:
                mcp = discover_mcp(
                    api_origin=access.local_access_origin,
                    receipt_origin=receipt.api_origin,
                    access=access,
                    remember_bin=remember_bin or remember_launcher(),
                    stdio_runner=stdio_runner,
                    cwd=output,
                    env=mcp_env,
                )
            except WorkspaceBenchError as error:
                failures.append(str(error))
    if not skip_office:
        try:
            office = inspect_office(upstream=upstream, skip_executables=skip_office)
        except WorkspaceBenchError as error:
            failures.append(str(error))
    protocol = build_protocol_coordinates(
        workspace_digest=workspace_digest,
        task_id=task_id,
        role_workspace_digest=workspace_digest,
        data_manifest_sha256_value=manifest_hash,
        task_prompt_sha256_value=prompt_hash,
        task_metadata_sha256_value=metadata_hash,
        office_skill_digest=None if office is None else office.digest,
        mcp_catalog_sha256=None if mcp is None else mcp.catalog_sha256,
        canonical_api_origin=None if access is None else access.canonical_target_origin,
        local_api_origin=None if access is None else access.local_access_origin,
        receipt=receipt,
        access=access,
        upstream_file_sha256=(
            None if upstream_inspection is None else upstream_inspection.file_sha256
        ),
        codex_cli_sha256=None if runtime_pin is None else runtime_pin.cli_sha256,
    )
    canary_dir = output / "canary"
    fake_path = write_fake_secret(directory=canary_dir)
    arm_config = (
        memory_arm_configuration(
            remember_bin=remember_bin or remember_launcher(),
            api_origin=None if access is None else access.local_access_origin,
            enabled_tools=mcp.enabled_tools if mcp is not None else (),
        )
        if arm == "memory"
        else native_arm_configuration()
    )
    canary = evaluate_canary(
        artifact_root=output,
        arm=arm_config,
        events=(),
        fake_secret_path=fake_path,
        kind="structural",
        live_turn=False,
    )
    if not canary.passed:
        failures.append(canary.detail)
    if api_origin is not None and "token=" in api_origin.lower():
        failures.append("api origin must not embed credentials")
    report = PreflightReport(
        task_id=task_id,
        upstream=upstream_inspection
        if upstream_inspection is not None
        else _empty_upstream(path=upstream),
        workspace_digest=workspace_digest,
        data_manifest_sha256=manifest_hash,
        receipt=receipt,
        account=account,
        mcp=mcp,
        canary=canary,
        office=office,
        codex_runtime=runtime_pin,
        protocol=protocol,
        live_gates=LIVE_GATES,
        automatic_live_gates=AUTOMATIC_LIVE_GATES,
        ok=not failures,
        failures=tuple(failures),
    )
    return report


def _empty_upstream(*, path: Path):
    from benchmarks.workspacebench.models import UpstreamInspection

    return UpstreamInspection(
        path=str(path), commit="unknown", dirty=True, file_sha256={}
    )
