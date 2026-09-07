"""Typed durable artifacts for the experimental Workspace-Bench protocol."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from typing import Annotated
from typing import Any
from typing import Literal
from typing import TypeAlias
from urllib.parse import urlparse

from pydantic import AfterValidator
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

NonEmpty = Annotated[str, Field(min_length=1)]
ArmName = Literal["native", "memory"]
FailureClass = Literal[
    "none",
    "preflight",
    "account",
    "timeout",
    "protocol_violation",
    "missing_output",
    "invalid_output",
    "credential_isolation",
    "upstream",
    "mcp_discovery",
    "receipt_mismatch",
    "not_executed",
    "partial_artifact",
]


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return value


UTCDateTime: TypeAlias = Annotated[
    datetime, Field(strict=True), AfterValidator(_require_utc)
]


class FrozenModel(BaseModel):
    """Strict immutable base for every durable benchmark boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ProtocolCoordinates(FrozenModel):
    """Pinned protocol identity recorded on every receipt.

    ``codex_cli_sha256`` is the observed digest of the pinned bundled CLI
    binary. It is platform-specific (macOS and Linux wheels differ).
    """

    schema_version: Literal["WorkspaceBenchProtocolCoordinates/v1"] = (
        "WorkspaceBenchProtocolCoordinates/v1"
    )
    protocol_name: NonEmpty
    protocol_fingerprint: NonEmpty
    experimental_label: NonEmpty
    adapter_version: NonEmpty
    upstream_commit: NonEmpty
    overlay_manifest_sha256: NonEmpty
    workspace_digest: NonEmpty
    role_workspace_digest: NonEmpty | None = None
    staged_workspace_digest: NonEmpty | None = None
    data_manifest_sha256: NonEmpty | None = None
    task_prompt_sha256: NonEmpty | None = None
    task_metadata_sha256: NonEmpty | None = None
    office_skill_digest: NonEmpty | None = None
    mcp_catalog_sha256: NonEmpty | None = None
    canonical_api_origin: NonEmpty | None = None
    local_api_origin: NonEmpty | None = None
    receipt_deployment_id: NonEmpty | None = None
    receipt_revision: NonEmpty | None = None
    receipt_sha256: NonEmpty | None = None
    arm_execution_fingerprint: NonEmpty | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    grace_seconds: float | None = Field(default=None, gt=0)
    task_id: NonEmpty
    model: NonEmpty
    reasoning_effort: NonEmpty
    temperature: None = None
    client_name: NonEmpty
    client_title: NonEmpty
    codex_execution_backend: Literal["sdk"]
    codex_sdk_version: NonEmpty
    codex_cli_version: NonEmpty
    codex_cli_sha256: NonEmpty | None = None
    codex_isolation: Literal["disposable_codex_home_keyring"]
    codex_credentials_store: Literal["keyring"]
    consumption_instruction_version: NonEmpty
    consumption_instruction_sha256: NonEmpty
    ignore_user_codex_config: Literal[True] = True
    sdk_timeout_partial_events: NonEmpty | None = None


class ReadinessPin(FrozenModel):
    """Required pipeline/projection coordinates from a sealed deployment."""

    pipeline: bool
    p1: bool
    live_graph: bool
    p3: bool


class McpAccessBinding(FrozenModel):
    """Typed MCP access: direct HTTPS or an SSH local forward.

    ``canonical_target_origin`` is the sealed deployment API origin from the
    receipt. ``local_access_origin`` is what the local ``remember mcp`` process
    actually dials. They are equal for a direct endpoint and different for a
    localhost SSH forward.
    """

    schema_version: Literal["WorkspaceBenchMcpAccessBinding/v1"] = (
        "WorkspaceBenchMcpAccessBinding/v1"
    )
    mode: Literal["direct", "ssh_local_forward"]
    local_access_origin: NonEmpty
    canonical_target_origin: NonEmpty


class CloudDeploymentReceipt(FrozenModel):
    """Operator-attested cloud handoff. Contains no bearer token."""

    schema_version: Literal["WorkspaceBenchCloudReceipt/v1"] = (
        "WorkspaceBenchCloudReceipt/v1"
    )
    workspace_digest: NonEmpty
    rememberstack_revision: NonEmpty
    converter_router_configuration: NonEmpty
    component_generations: dict[str, str]
    version_ids: tuple[NonEmpty, ...] = Field(min_length=1)
    readiness_requirements: ReadinessPin
    api_origin: NonEmpty
    deployment_id: NonEmpty
    sealed: Literal[True]
    attested_by: NonEmpty
    attested_at: UTCDateTime

    @field_validator("api_origin")
    @classmethod
    def _origin_has_no_userinfo(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.username or parsed.password:
            raise ValueError("api_origin must not contain credentials")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("api_origin must be an http(s) origin")
        return value.rstrip("/")

    @field_validator("component_generations")
    @classmethod
    def _generations_nonempty(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("component_generations must not be empty")
        for key, item in value.items():
            if not key or not item:
                raise ValueError(
                    "component generation names and values must be nonempty"
                )
            lowered = key.lower()
            if lowered in {
                "authorization",
                "token",
                "access_token",
                "refresh_token",
                "password",
                "secret",
                "api_key",
                "apikey",
                "bearer",
            }:
                raise ValueError(f"credential-shaped component generation key: {key}")
        return value

    @model_validator(mode="after")
    def _read_plane_and_no_secrets(self) -> CloudDeploymentReceipt:
        pin = self.readiness_requirements
        if not pin.pipeline:
            raise ValueError("readiness_requirements.pipeline must be true")
        if not pin.p1 or not pin.live_graph:
            raise ValueError(
                "readiness_requirements must assert pipeline, p1, and live_graph "
                "so the four assured read operations (resolve_entity, "
                "claims_and_sources_context, facts_context, combined_context) "
                "are supported; p3/CorpusFS is optional and an arbitrary one of "
                "p1/live_graph/p3 is not enough"
            )
        blob = self.model_dump_json().lower()
        for token in ("bearer ", "authorization", "access_token", "api_key"):
            if token in blob:
                raise ValueError("cloud receipt must not contain credential material")
        return self


class ArmConfiguration(FrozenModel):
    """Exact treatment for one arm. Memory adds MCP tools and the instruction."""

    schema_version: Literal["WorkspaceBenchArmConfiguration/v1"] = (
        "WorkspaceBenchArmConfiguration/v1"
    )
    arm: ArmName
    mcp_enabled: bool
    mcp_server_name: NonEmpty | None = None
    mcp_command: tuple[NonEmpty, ...] = ()
    mcp_args: tuple[str, ...] = ()
    enabled_tools: tuple[NonEmpty, ...] = ()
    consumption_instruction_version: NonEmpty | None = None
    sandbox: Literal["workspace-write"] = "workspace-write"
    approval_mode: Literal["deny_all"] = "deny_all"
    command_network_access: Literal[False] = False
    ignore_user_codex_config: Literal[True] = True


class AccountAttestation(FrozenModel):
    """ChatGPT seat proof without secret material or auth-cache paths."""

    schema_version: Literal["WorkspaceBenchAccountAttestation/v1"] = (
        "WorkspaceBenchAccountAttestation/v1"
    )
    account_type: Literal["chatgpt"]
    attested_at: UTCDateTime


class TokenUsage(FrozenModel):
    """Subscription token counts. ``cost_usd=0`` is not a free-seat claim."""

    schema_version: Literal["WorkspaceBenchTokenUsage/v1"] = (
        "WorkspaceBenchTokenUsage/v1"
    )
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    cost_usd: Decimal
    model: NonEmpty


class Timing(FrozenModel):
    """Wall-clock timing for one supervised task session."""

    schema_version: Literal["WorkspaceBenchTiming/v1"] = "WorkspaceBenchTiming/v1"
    started_at: UTCDateTime
    finished_at: UTCDateTime | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)
    timeout_seconds: float = Field(gt=0)
    timed_out: bool = False
    grace_seconds: float = Field(gt=0)


class OutputFile(FrozenModel):
    """One collected artifact rooted under the official output directory."""

    relative_path: NonEmpty
    size_bytes: int = Field(ge=0)
    mime_type: NonEmpty
    sha256: NonEmpty


class OutputManifest(FrozenModel):
    """Expected outputs plus explicit missing/invalid paths."""

    schema_version: Literal["WorkspaceBenchOutputManifest/v1"] = (
        "WorkspaceBenchOutputManifest/v1"
    )
    output_root: NonEmpty
    files: tuple[OutputFile, ...] = ()
    missing: tuple[NonEmpty, ...] = ()
    rejected: tuple[NonEmpty, ...] = ()


class McpCallRecord(FrozenModel):
    """One Remember MCP call with redacted retrieval diagnostics."""

    server: NonEmpty
    tool: NonEmpty
    arguments: Any = None
    duration_ms: int | None = Field(default=None, ge=0)
    is_error: bool = False
    returned_context_bytes: int = Field(default=0, ge=0)
    attributable_paths: tuple[str, ...] = ()
    zero_result: bool = False


class TraceManifest(FrozenModel):
    """Pointers to raw and sanitized traces plus classified event counts."""

    schema_version: Literal["WorkspaceBenchTraceManifest/v1"] = (
        "WorkspaceBenchTraceManifest/v1"
    )
    raw_relpath: NonEmpty
    sanitized_relpath: NonEmpty
    item_types: tuple[str, ...] = ()
    mcp_calls: tuple[McpCallRecord, ...] = ()
    command_count: int = Field(default=0, ge=0)
    file_change_count: int = Field(default=0, ge=0)


class MemoryDiagnostics(FrozenModel):
    """Memory-arm retrieval diagnostics. Native arm leaves this unset."""

    schema_version: Literal["WorkspaceBenchMemoryDiagnostics/v1"] = (
        "WorkspaceBenchMemoryDiagnostics/v1"
    )
    query_count: int = Field(ge=0)
    zero_result_count: int = Field(ge=0)
    latencies_ms: tuple[int, ...] = ()
    returned_context_bytes: int = Field(ge=0)
    attributable_paths: tuple[str, ...] = ()


class TaskResult(FrozenModel):
    """Durable envelope for one arm attempt. A retry is a new attempt id."""

    schema_version: Literal["WorkspaceBenchTaskResult/v1"] = (
        "WorkspaceBenchTaskResult/v1"
    )
    attempt_id: NonEmpty
    task_id: NonEmpty
    arm: ArmName
    protocol: ProtocolCoordinates
    workspace_digest: NonEmpty
    account: AccountAttestation | None = None
    arm_configuration: ArmConfiguration
    usage: TokenUsage
    timing: Timing
    failure_class: FailureClass
    failure_detail: str | None = None
    outputs: OutputManifest
    traces: TraceManifest
    memory: MemoryDiagnostics | None = None
    protocol_violations: tuple[NonEmpty, ...] = ()
    experimental_label: NonEmpty
    arm_execution_fingerprint: NonEmpty | None = None


class CanaryResult(FrozenModel):
    """Credential-isolation canary outcome. Never stores the live auth cache."""

    schema_version: Literal["WorkspaceBenchCanaryResult/v1"] = (
        "WorkspaceBenchCanaryResult/v1"
    )
    passed: bool
    fake_secret_present_in_artifacts: bool
    credential_path_readable_by_command: bool
    scanned_artifact_relpaths: tuple[str, ...]
    detail: NonEmpty
    kind: Literal["structural", "live"] = "structural"
    live_turn: bool = False
    observed_denied_outside_read: bool = False


class PairedRunManifest(FrozenModel):
    """Binds two pristine workspace clones and their result envelopes."""

    schema_version: Literal["WorkspaceBenchPairedRun/v1"] = "WorkspaceBenchPairedRun/v1"
    pair_id: NonEmpty
    task_id: NonEmpty
    arm_order: tuple[ArmName, ArmName]
    role_workspace_digest: NonEmpty | None = None
    native_workspace_digest: NonEmpty
    memory_workspace_digest: NonEmpty
    native_result_relpath: NonEmpty
    memory_result_relpath: NonEmpty
    experimental: Literal[True] = True
    experimental_label: NonEmpty
    protocol_fingerprint: NonEmpty
    native_arm_fingerprint: NonEmpty | None = None
    memory_arm_fingerprint: NonEmpty | None = None
    live_canary: CanaryResult | None = None

    @field_validator("arm_order")
    @classmethod
    def _two_distinct_arms(
        cls, value: tuple[ArmName, ArmName]
    ) -> tuple[ArmName, ArmName]:
        if set(value) != {"native", "memory"}:
            raise ValueError("arm_order must contain native and memory once each")
        return value


class ToolDescriptorRecord(FrozenModel):
    """One MCP tools/list descriptor captured during preflight."""

    name: NonEmpty
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class McpDiscovery(FrozenModel):
    """Bound MCP catalog observed before a memory-arm run."""

    schema_version: Literal["WorkspaceBenchMcpDiscovery/v1"] = (
        "WorkspaceBenchMcpDiscovery/v1"
    )
    api_origin: NonEmpty
    assured_operations: tuple[NonEmpty, ...]
    open_query_tools: tuple[NonEmpty, ...]
    listed_tools: tuple[ToolDescriptorRecord, ...]
    enabled_tools: tuple[NonEmpty, ...]
    write_tools_present: Literal[False] = False
    discovered_over_stdio: bool = False
    catalog_sha256: NonEmpty | None = None
    access: McpAccessBinding | None = None
    negotiated_protocol_version: NonEmpty | None = None


class UpstreamInspection(FrozenModel):
    """Git identity of the external Workspace-Bench checkout."""

    schema_version: Literal["WorkspaceBenchUpstreamInspection/v1"] = (
        "WorkspaceBenchUpstreamInspection/v1"
    )
    path: NonEmpty
    commit: NonEmpty
    dirty: bool
    file_sha256: dict[str, str]


class CodexRuntimePin(FrozenModel):
    """Observed bundled Codex CLI pin. No binary path, home path, or secrets."""

    schema_version: Literal["WorkspaceBenchCodexRuntimePin/v1"] = (
        "WorkspaceBenchCodexRuntimePin/v1"
    )
    sdk_version: NonEmpty
    cli_version: NonEmpty
    cli_sha256: NonEmpty
    isolation: Literal["disposable_codex_home_keyring"]
    credentials_store: Literal["keyring"]


class OfficeSkillPin(FrozenModel):
    """Workspace-local office skill staging from the pinned upstream tree."""

    schema_version: Literal["WorkspaceBenchOfficeSkillPin/v1"] = (
        "WorkspaceBenchOfficeSkillPin/v1"
    )
    source_relpath: NonEmpty
    workspace_relpath: NonEmpty
    digest: NonEmpty
    executables: tuple[NonEmpty, ...] = ()
    skill_names: tuple[NonEmpty, ...] = ()


class PreflightReport(FrozenModel):
    """No-spend preflight receipt. Does not trigger ingest or model turns."""

    schema_version: Literal["WorkspaceBenchPreflightReport/v1"] = (
        "WorkspaceBenchPreflightReport/v1"
    )
    task_id: NonEmpty
    upstream: UpstreamInspection
    workspace_digest: NonEmpty
    data_manifest_sha256: NonEmpty | None = None
    receipt: CloudDeploymentReceipt | None = None
    account: AccountAttestation | None = None
    mcp: McpDiscovery | None = None
    canary: CanaryResult | None = None
    office: OfficeSkillPin | None = None
    codex_runtime: CodexRuntimePin | None = None
    protocol: ProtocolCoordinates
    live_gates: tuple[NonEmpty, ...]
    automatic_live_gates: tuple[NonEmpty, ...] = ()
    ok: bool
    failures: tuple[NonEmpty, ...] = ()


class PairedReport(FrozenModel):
    """Deterministic reconstruction from the two arm receipts."""

    schema_version: Literal["WorkspaceBenchPairedReport/v1"] = (
        "WorkspaceBenchPairedReport/v1"
    )
    pair: PairedRunManifest
    native: TaskResult
    memory: TaskResult
    experimental_label: NonEmpty
    quality_inference: Literal["not_inferred_from_single_task"] = (
        "not_inferred_from_single_task"
    )
