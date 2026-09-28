"""Cloud deployment receipt load/validate. Never stores a bearer token."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import require_absolute_path
from benchmarks.workspacebench.mcp import origins_equal
from benchmarks.workspacebench.mcp import validate_access_binding
from benchmarks.workspacebench.models import CloudDeploymentReceipt
from benchmarks.workspacebench.models import McpAccessBinding


def load_cloud_receipt(path: Path) -> CloudDeploymentReceipt:
    """Parse an operator-attested receipt from disk."""
    require_absolute_path(path, label="receipt")
    try:
        return CloudDeploymentReceipt.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as error:
        raise WorkspaceBenchError(f"invalid cloud receipt {path}: {error}") from error


def validate_receipt_against_workspace(
    *,
    receipt: CloudDeploymentReceipt,
    workspace_digest: str,
    api_origin: str,
    access: McpAccessBinding | None = None,
) -> McpAccessBinding:
    """Refuse a receipt whose digest or origin does not match the local run.

    Direct access requires ``api_origin`` to equal the receipt canonical origin.
    An SSH local forward is allowed only when the typed binding's declared
    target equals that canonical origin.
    """
    if receipt.workspace_digest != workspace_digest:
        raise WorkspaceBenchError(
            "cloud receipt workspace digest does not match the local workspace"
        )
    if not receipt.sealed:
        raise WorkspaceBenchError("cloud receipt is not sealed")
    if not receipt.version_ids:
        raise WorkspaceBenchError("cloud receipt version_ids must be nonempty")
    if not receipt.component_generations:
        raise WorkspaceBenchError(
            "cloud receipt component_generations must be nonempty"
        )
    binding = access or McpAccessBinding(
        mode="direct",
        local_access_origin=api_origin.rstrip("/"),
        canonical_target_origin=receipt.api_origin,
    )
    try:
        validate_access_binding(access=binding, receipt_origin=receipt.api_origin)
    except WorkspaceBenchError as error:
        raise WorkspaceBenchError(
            f"cloud receipt origin {receipt.api_origin!r} does not match "
            f"configured access {binding.mode}:{binding.local_access_origin!r} "
            f"target {binding.canonical_target_origin!r}: {error}"
        ) from error
    if binding.mode == "direct" and not origins_equal(receipt.api_origin, api_origin):
        raise WorkspaceBenchError(
            f"cloud receipt origin {receipt.api_origin!r} does not match "
            f"configured origin {api_origin!r}"
        )
    return binding
