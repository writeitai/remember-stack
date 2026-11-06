"""Canonical protocol fingerprint over pins, overlays, and instruction text."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
import hashlib
import json
from typing import Any

from benchmarks.workspacebench.consumption import consumption_instruction_sha256
from benchmarks.workspacebench.models import ArmConfiguration
from benchmarks.workspacebench.models import CloudDeploymentReceipt
from benchmarks.workspacebench.models import McpAccessBinding
from benchmarks.workspacebench.models import McpDiscovery
from benchmarks.workspacebench.overlay import overlay_manifest_sha256
from benchmarks.workspacebench.protocol import ADAPTER_VERSION
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
from benchmarks.workspacebench.protocol import DEFAULT_TIMEOUT_SEC
from benchmarks.workspacebench.protocol import EXPECTED_UPSTREAM_FILE_SHA256
from benchmarks.workspacebench.protocol import MCP_STARTUP_TIMEOUT_SEC
from benchmarks.workspacebench.protocol import MCP_TOOL_TIMEOUT_SEC
from benchmarks.workspacebench.protocol import PROTOCOL_NAME
from benchmarks.workspacebench.protocol import TARGET_OUTPUT_DIR
from benchmarks.workspacebench.protocol import UPSTREAM_COMMIT


def protocol_fingerprint(
    *,
    workspace_digest: str,
    task_id: str,
    role_workspace_digest: str | None = None,
    staged_workspace_digest: str | None = None,
    data_manifest_sha256: str | None = None,
    task_prompt_sha256: str | None = None,
    task_metadata_sha256: str | None = None,
    office_skill_digest: str | None = None,
    overlay_sha256: str | None = None,
    upstream_file_sha256: Mapping[str, str] | None = None,
    canonical_api_origin: str | None = None,
    local_api_origin: str | None = None,
    receipt: CloudDeploymentReceipt | None = None,
    access: McpAccessBinding | None = None,
    mcp: McpDiscovery | None = None,
    mcp_catalog_sha256: str | None = None,
    timeout_seconds: float | None = None,
    grace_seconds: float | None = None,
    codex_cli_sha256: str | None = None,
) -> str:
    """Stable SHA-256 over the shared experimental protocol coordinates.

    Computed only after the relevant pins are known. Arm-specific MCP allowlists
    and consumption application belong in ``arm_execution_fingerprint``.

    ``codex_cli_sha256`` is the observed digest of the pinned bundled CLI. It is
    platform-specific: macOS and Linux wheels of the same ``codex-cli`` version
    produce different fingerprints.
    """
    catalog = mcp_catalog_sha256
    if mcp is not None:
        catalog = mcp.catalog_sha256
    payload = {
        "adapter_version": ADAPTER_VERSION,
        "canonical_api_origin": canonical_api_origin,
        "client_name": CODEX_CLIENT_NAME,
        "client_title": CODEX_CLIENT_TITLE,
        "codex_cli_version": CODEX_CLI_VERSION,
        "codex_cli_sha256": codex_cli_sha256,
        "codex_credentials_store": CODEX_CREDENTIALS_STORE,
        "codex_execution_backend": CODEX_EXECUTION_BACKEND,
        "codex_isolation": CODEX_ISOLATION,
        "codex_sdk_version": CODEX_SDK_VERSION,
        "consumption_instruction_sha256": consumption_instruction_sha256(),
        "consumption_instruction_version": CONSUMPTION_INSTRUCTION_VERSION,
        "data_manifest_sha256": data_manifest_sha256,
        "grace_seconds": DEFAULT_GRACE_SEC if grace_seconds is None else grace_seconds,
        "ignore_user_codex_config": True,
        "local_api_origin": local_api_origin,
        "mcp_access": None if access is None else access.model_dump(mode="json"),
        "mcp_catalog_sha256": catalog,
        "mcp_startup_timeout_sec": MCP_STARTUP_TIMEOUT_SEC,
        "mcp_tool_timeout_sec": MCP_TOOL_TIMEOUT_SEC,
        "model": CODEX_MODEL,
        "office_skill_digest": office_skill_digest,
        "overlay_manifest_sha256": overlay_sha256 or overlay_manifest_sha256(),
        "protocol_name": PROTOCOL_NAME,
        "reasoning_effort": CODEX_REASONING_EFFORT,
        "receipt": None if receipt is None else receipt.model_dump(mode="json"),
        "role_workspace_digest": role_workspace_digest or workspace_digest,
        "staged_workspace_digest": staged_workspace_digest,
        "target_output_dir": TARGET_OUTPUT_DIR,
        "task_id": task_id,
        "task_metadata_sha256": task_metadata_sha256,
        "task_prompt_sha256": task_prompt_sha256,
        "temperature": None,
        "timeout_seconds": (
            DEFAULT_TIMEOUT_SEC if timeout_seconds is None else timeout_seconds
        ),
        "upstream_commit": UPSTREAM_COMMIT,
        "upstream_file_sha256": dict(
            upstream_file_sha256
            if upstream_file_sha256 is not None
            else EXPECTED_UPSTREAM_FILE_SHA256
        ),
        "workspace_digest": workspace_digest,
    }
    return _sha256_payload(payload)


def arm_execution_fingerprint(
    *,
    base_protocol_fingerprint: str,
    arm: ArmConfiguration,
    mcp: McpDiscovery | None = None,
    enabled_tools: Sequence[str] | None = None,
) -> str:
    """Treatment fingerprint. Native and memory may differ; the base stays bound."""
    catalog = None if mcp is None else mcp.catalog_sha256
    payload = {
        "arm": arm.arm,
        "base_protocol_fingerprint": base_protocol_fingerprint,
        "consumption_instruction_version": arm.consumption_instruction_version,
        "enabled_tools": list(
            enabled_tools if enabled_tools is not None else arm.enabled_tools
        ),
        "mcp_catalog_sha256": catalog,
        "mcp_command": list(arm.mcp_command),
        "mcp_args": list(arm.mcp_args),
        "mcp_enabled": arm.mcp_enabled,
        "mcp_server_name": arm.mcp_server_name,
    }
    return _sha256_payload(payload)


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()
