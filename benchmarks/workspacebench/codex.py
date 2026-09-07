"""Task-scoped Codex subscription runner. Does not read ``auth.json``."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import UTC
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from typing import Any
from typing import cast
from typing import Protocol

from benchmarks.workspacebench.consumption import MEMORY_CONSUMPTION_INSTRUCTION
from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.isolation import codex_child_env
from benchmarks.workspacebench.isolation import CodexIsolation
from benchmarks.workspacebench.isolation import CodexIsolationError
from benchmarks.workspacebench.isolation import inspect_bundled_codex_cli
from benchmarks.workspacebench.isolation import isolated_command_argv
from benchmarks.workspacebench.isolation import KEYRING_LOGIN_HELP
from benchmarks.workspacebench.isolation import prepare_disposable_codex_home
from benchmarks.workspacebench.isolation import resolve_bundled_codex_binary
from benchmarks.workspacebench.isolation import TRUSTED_ENV_BIN
from benchmarks.workspacebench.mcp import remember_launcher
from benchmarks.workspacebench.models import AccountAttestation
from benchmarks.workspacebench.models import ArmConfiguration
from benchmarks.workspacebench.protocol import AUTH_CACHE_BASENAME
from benchmarks.workspacebench.protocol import CODEX_CLI_VERSION
from benchmarks.workspacebench.protocol import CODEX_CLIENT_NAME
from benchmarks.workspacebench.protocol import CODEX_CLIENT_TITLE
from benchmarks.workspacebench.protocol import CODEX_MODEL
from benchmarks.workspacebench.protocol import CODEX_REASONING_EFFORT
from benchmarks.workspacebench.protocol import CODEX_SERVICE_NAME
from benchmarks.workspacebench.protocol import CONSUMPTION_INSTRUCTION_VERSION
from benchmarks.workspacebench.protocol import MCP_READ_ONLY_ARGS
from benchmarks.workspacebench.protocol import MCP_SERVER_NAME
from benchmarks.workspacebench.protocol import MCP_STARTUP_TIMEOUT_SEC
from benchmarks.workspacebench.protocol import MCP_TOOL_TIMEOUT_SEC


class CodexAccountError(WorkspaceBenchError):
    """Codex is missing an eligible ChatGPT subscription login."""


@dataclass(frozen=True)
class CodexRuntimeEvent:
    """One SDK thread item, with request-side fields retained."""

    item_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CodexTaskTurn:
    """Facts returned by one ephemeral Workspace-Bench Codex session."""

    status: str
    error_message: str | None
    final_response: str | None
    tokens_in: int | None
    tokens_out: int | None
    item_types: tuple[str, ...]
    events: tuple[CodexRuntimeEvent, ...]
    timed_out: bool = False


class CodexTurnRunner(Protocol):
    """Execute one toolful Codex task without exposing SDK types."""

    def __call__(self, *, request: "CodexTaskRequest") -> CodexTaskTurn:
        """Return the isolated task's response, usage, and runtime events."""
        ...


class AccountReader(Protocol):
    """Ask the official runtime for the active account type only."""

    def __call__(self) -> str | None:
        """Return ``chatgpt`` or another account type, never secret material."""
        ...


@dataclass(frozen=True)
class CodexTaskRequest:
    """Inputs for one arm session. Contains no credentials."""

    prompt: str
    workspace: Path
    model: str
    reasoning_effort: str
    arm: ArmConfiguration
    timeout_seconds: float
    grace_seconds: float


def attest_chatgpt_account(
    *, reader: AccountReader | None = None, now: Callable[[], datetime] | None = None
) -> AccountAttestation:
    """Require account type ``chatgpt`` without inspecting the credential cache."""
    account_type = (reader or _read_account_type)()
    if account_type != "chatgpt":
        raise CodexAccountError(
            "Codex must be logged in with ChatGPT; run `codex login`. "
            "API-key logins are rejected. " + KEYRING_LOGIN_HELP
        )
    clock = now or (lambda: datetime.now(tz=UTC))
    return AccountAttestation(account_type="chatgpt", attested_at=clock())


def memory_arm_configuration(
    *,
    remember_bin: Sequence[str] | str | None = None,
    api_origin: str | None,
    enabled_tools: Sequence[str],
) -> ArmConfiguration:
    """Exact memory-arm MCP allowlist and versioned instruction pin.

    ``mcp_command`` is the executable only. The rest of the launcher prefix
    (``-m remember``) plus ``mcp --read-only`` live in ``mcp_args`` so Codex
    does not drop ``-m remember``.
    """
    prefix = remember_bin if remember_bin is not None else remember_launcher()
    if isinstance(prefix, str):
        command = (prefix,)
        args = list(MCP_READ_ONLY_ARGS)
    else:
        launcher = tuple(prefix)
        if not launcher:
            raise WorkspaceBenchError("memory-arm MCP launcher is empty")
        command = (launcher[0],)
        args = [*launcher[1:], *MCP_READ_ONLY_ARGS]
    if api_origin:
        args.extend(["--api-url", api_origin])
    return ArmConfiguration(
        arm="memory",
        mcp_enabled=True,
        mcp_server_name=MCP_SERVER_NAME,
        mcp_command=command,
        mcp_args=tuple(args),
        enabled_tools=tuple(enabled_tools),
        consumption_instruction_version=CONSUMPTION_INSTRUCTION_VERSION,
    )


def effective_mcp_launch(*, arm: ArmConfiguration) -> tuple[str, tuple[str, ...]]:
    """Return the exact Codex MCP executable and args, including launcher prefix."""
    if not arm.mcp_command:
        return "remember", tuple(arm.mcp_args)
    return arm.mcp_command[0], (*arm.mcp_command[1:], *arm.mcp_args)


def native_arm_configuration() -> ArmConfiguration:
    """Native arm: workspace-write Codex, no Remember MCP."""
    return ArmConfiguration(arm="native", mcp_enabled=False, enabled_tools=())


def compose_prompt(*, task_prompt: str, arm: ArmConfiguration) -> str:
    """Keep the upstream task prompt verbatim; add the instruction only in memory."""
    if arm.arm == "memory":
        return f"{MEMORY_CONSUMPTION_INSTRUCTION.rstrip()}\n\n{task_prompt}"
    return task_prompt


def codex_config_overrides(*, arm: ArmConfiguration) -> tuple[str, ...]:
    """Per-process ``--config`` keys. Never includes tokens or auth-cache paths.

    ``codex-cli`` 0.147.0 ``app-server`` does not accept ``--ignore-user-config``
    (bundled app-server help/launch rejects it with exit 2). User config is
    isolated by a disposable ``CODEX_HOME`` plus these overrides. Native arms
    add no MCP servers; memory arms add only the required Remember server.
    """
    overrides = [
        'cli_auth_credentials_store="keyring"',
        'mcp_oauth_credentials_store="keyring"',
        'shell_environment_policy.inherit="core"',
        "shell_environment_policy.ignore_default_excludes=false",
        "shell_environment_policy.experimental_use_profile=false",
        "sandbox_workspace_write.network_access=false",
        "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        "sandbox_workspace_write.exclude_slash_tmp=true",
        "web_search=false",
    ]
    if arm.mcp_enabled:
        if arm.arm != "memory":
            raise WorkspaceBenchError(
                "only the memory arm may register a Codex MCP server"
            )
        command, args = effective_mcp_launch(arm=arm)
        overrides.extend(
            [
                f"mcp_servers.{MCP_SERVER_NAME}.command={_toml_str(command)}",
                f"mcp_servers.{MCP_SERVER_NAME}.args={_toml_array(args)}",
                f"mcp_servers.{MCP_SERVER_NAME}.enabled=true",
                f"mcp_servers.{MCP_SERVER_NAME}.required=true",
                (
                    f"mcp_servers.{MCP_SERVER_NAME}.enabled_tools="
                    f"{_toml_array(arm.enabled_tools)}"
                ),
                (
                    f"mcp_servers.{MCP_SERVER_NAME}.startup_timeout_sec="
                    f"{MCP_STARTUP_TIMEOUT_SEC}"
                ),
                (
                    f"mcp_servers.{MCP_SERVER_NAME}.tool_timeout_sec="
                    f"{MCP_TOOL_TIMEOUT_SEC}"
                ),
            ]
        )
    return tuple(overrides)


def app_server_launch_argv(
    *, binary: Path, arm: ArmConfiguration, env: Mapping[str, str]
) -> tuple[str, ...]:
    """Pinned ``codex-cli`` 0.147.0 app-server argv behind ``env -i``.

    Compatible with openai-codex stdio ``Popen``: the SDK execs this argv as
    the app-server process. ``/usr/bin/env -i`` inherits the SDK-merged parent
    environment, then execs the pinned binary with only ``env``.
    """
    launch = list(isolated_command_argv(env=env, binary=binary))
    for item in codex_config_overrides(arm=arm):
        launch.extend(["--config", item])
    launch.extend(["app-server", "--listen", "stdio://"])
    if launch[0] != str(TRUSTED_ENV_BIN) or launch[1] != "-i":
        raise CodexIsolationError("app-server argv must start with /usr/bin/env -i")
    if str(binary) not in launch:
        raise CodexIsolationError("app-server argv must include the pinned binary")
    if "--ignore-user-config" in launch:
        raise CodexIsolationError(
            "codex-cli 0.147.0 app-server rejects --ignore-user-config; "
            "user config is isolated with a disposable CODEX_HOME"
        )
    blob = "\0".join(launch)
    if AUTH_CACHE_BASENAME in blob:
        raise CodexIsolationError("app-server argv must not name auth.json")
    return tuple(launch)


def app_server_help_argv(
    *, binary: Path, arm: ArmConfiguration, env: Mapping[str, str]
) -> tuple[str, ...]:
    """Same generated overrides as a live launch, ending in ``app-server --help``."""
    live = app_server_launch_argv(binary=binary, arm=arm, env=env)
    if live[-3:] != ("app-server", "--listen", "stdio://"):
        raise CodexIsolationError(
            "app-server launch argv drifted from the expected tail"
        )
    return (*live[:-2], "--help")


def run_codex_task(*, request: CodexTaskRequest) -> CodexTaskTurn:
    """Live SDK session. Tests inject ``CodexTurnRunner`` instead of this."""
    try:
        from openai_codex import ApprovalMode
        from openai_codex import Codex
        from openai_codex import CodexConfig
        from openai_codex import Sandbox
        from openai_codex.generated.v2_all import ReasoningEffort
    except ImportError as error:
        raise WorkspaceBenchError(
            "Workspace-Bench Codex execution requires the benchmark extra: "
            "uv sync --extra benchmark"
        ) from error

    try:
        effort = ReasoningEffort(request.reasoning_effort)
    except ValueError as error:
        raise WorkspaceBenchError(
            f"Codex does not support reasoning effort {request.reasoning_effort!r}"
        ) from error

    inspect_bundled_codex_cli(force=True)
    binary = resolve_bundled_codex_binary()
    with tempfile.TemporaryDirectory(prefix="wb-codex-home-") as home:
        isolation = prepare_disposable_codex_home(Path(home))
        spawn_env = isolated_codex_env(isolation=isolation, arm=request.arm)
        launch = app_server_launch_argv(binary=binary, arm=request.arm, env=spawn_env)
        config = CodexConfig(
            launch_args_override=launch,
            cwd=str(request.workspace),
            client_name=CODEX_CLIENT_NAME,
            client_title=CODEX_CLIENT_TITLE,
            env=spawn_env,
        )
        with Codex(config) as codex:
            account = codex.account(refresh_token=False).account
            account_type = (
                None if account is None else account.model_dump(mode="json").get("type")
            )
            if account_type != "chatgpt":
                raise CodexAccountError(
                    "Codex must be logged in with ChatGPT; run `codex login`. "
                    + KEYRING_LOGIN_HELP
                )
            thread = codex.thread_start(
                approval_mode=ApprovalMode.deny_all,
                cwd=str(request.workspace),
                ephemeral=True,
                model=request.model,
                sandbox=Sandbox.workspace_write,
                service_name=CODEX_SERVICE_NAME,
            )
            result = thread.run(
                request.prompt, effort=effort, sandbox=Sandbox.workspace_write
            )
    total_usage = None if result.usage is None else result.usage.total
    roots = tuple(item.root for item in result.items)
    events = tuple(_runtime_event(item=root) for root in roots)
    return CodexTaskTurn(
        status=result.status.value,
        error_message=None if result.error is None else result.error.message,
        final_response=result.final_response,
        tokens_in=None if total_usage is None else total_usage.input_tokens,
        tokens_out=None if total_usage is None else total_usage.output_tokens,
        item_types=tuple(type(root).__name__ for root in roots),
        events=events,
    )


def default_model() -> str:
    """Pinned Codex model for this experimental protocol."""
    return CODEX_MODEL


def default_reasoning_effort() -> str:
    """Pinned reasoning effort. Codex has no temperature control."""
    return CODEX_REASONING_EFFORT


def subscription_cost() -> Decimal:
    """Provider-reported marginal USD. Not a claim that the seat is free."""
    return Decimal(0)


def isolated_codex_env(
    *, isolation: CodexIsolation, arm: ArmConfiguration
) -> dict[str, str]:
    """Allowlisted env applied by ``env -i`` to the app-server and its children."""
    return codex_child_env(
        isolation=isolation, include_remember_config=arm.arm == "memory"
    )


def _read_account_type() -> str | None:
    try:
        from openai_codex import Codex
        from openai_codex import CodexConfig
    except ImportError as error:
        raise WorkspaceBenchError(
            "account attestation requires the benchmark extra: uv sync --extra benchmark"
        ) from error
    inspect_bundled_codex_cli(force=True)
    binary = resolve_bundled_codex_binary()
    arm = native_arm_configuration()
    with tempfile.TemporaryDirectory(prefix="wb-codex-account-") as home:
        isolation = prepare_disposable_codex_home(Path(home))
        spawn_env = isolated_codex_env(isolation=isolation, arm=arm)
        launch = app_server_launch_argv(binary=binary, arm=arm, env=spawn_env)
        with Codex(
            CodexConfig(
                launch_args_override=launch,
                client_name=CODEX_CLIENT_NAME,
                client_title=CODEX_CLIENT_TITLE,
                env=spawn_env,
            )
        ) as codex:
            account = codex.account(refresh_token=False).account
            if account is None:
                return None
            dumped = account.model_dump(mode="json")
            value = dumped.get("type")
            return value if isinstance(value, str) else None


def _runtime_event(*, item: object) -> CodexRuntimeEvent:
    item_type = type(item).__name__
    serializer = getattr(item, "model_dump", None)
    if not callable(serializer):
        return CodexRuntimeEvent(item_type=item_type, payload={})
    dumped = serializer(mode="json", by_alias=True, exclude_none=True)
    payload = dumped if isinstance(dumped, dict) else {"value": dumped}
    return CodexRuntimeEvent(
        item_type=item_type, payload=cast("dict[str, Any]", payload)
    )


def _toml_str(value: str) -> str:
    return json.dumps(value)


def _toml_array(values: Sequence[str]) -> str:
    return "[" + ", ".join(_toml_str(item) for item in values) + "]"


def events_as_jsonable(events: Sequence[CodexRuntimeEvent]) -> list[dict[str, object]]:
    """Serialize runtime events for JSONL traces."""
    return [
        {"item_type": event.item_type, "payload": dict(event.payload)}
        for event in events
    ]


def request_contains_secret(*, request: CodexTaskRequest, secret: str) -> bool:
    """True when a fake secret leaked into the session request."""
    blob = "\n".join(
        (
            request.prompt,
            str(request.workspace),
            json.dumps(request.arm.model_dump(mode="json"), sort_keys=True),
            "\n".join(codex_config_overrides(arm=request.arm)),
            CODEX_CLI_VERSION,
        )
    )
    return secret in blob


def config_blob_for_canary(*, arm: ArmConfiguration) -> str:
    """Serialized Codex overrides used by the credential canary."""
    return "\n".join(codex_config_overrides(arm=arm))
