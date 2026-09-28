"""Credential-isolation canary. Never reads or copies ``~/.codex/auth.json``.

Codex ``workspace-write`` (codex-cli 0.147.0) restricts writes only: model
commands can read any file the operator can read, on macOS and Linux. The
canary therefore does not ask for a denied read. It checks what the harness
does guarantee: a fake secret placed in the environment of the process that
launches Codex never reaches the model's shell, events, outputs, or traces,
because the child runs behind ``env -i`` with a sanitized allowlist, a
disposable ``CODEX_HOME``, and keyring-held ChatGPT credentials.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from benchmarks.workspacebench.codex import CodexRuntimeEvent
from benchmarks.workspacebench.codex import CodexTaskRequest
from benchmarks.workspacebench.codex import CodexTaskTurn
from benchmarks.workspacebench.codex import CodexTurnRunner
from benchmarks.workspacebench.codex import config_blob_for_canary
from benchmarks.workspacebench.errors import LiveGateError
from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.models import ArmConfiguration
from benchmarks.workspacebench.models import CanaryResult
from benchmarks.workspacebench.protocol import AUTH_CACHE_BASENAME
from benchmarks.workspacebench.protocol import CANARY_SECRET_FILENAME
from benchmarks.workspacebench.protocol import CODEX_MODEL
from benchmarks.workspacebench.protocol import CODEX_REASONING_EFFORT
from benchmarks.workspacebench.protocol import DEFAULT_GRACE_SEC
from benchmarks.workspacebench.protocol import FAKE_CANARY_SECRET
from benchmarks.workspacebench.protocol import STRUCTURAL_CANARY_DETAIL
from benchmarks.workspacebench.traces import scan_for_secret

LIVE_CANARY_TIMEOUT_SEC = 60.0


class LiveCanaryRunner(Protocol):
    """Injectable disposable canary turn. Production uses the Codex runner."""

    def __call__(self, *, request: CodexTaskRequest) -> CodexTaskTurn:
        """Run one canary session against the fake secret outside the workspace."""
        ...


def write_fake_secret(*, directory: Path, secret: str = FAKE_CANARY_SECRET) -> Path:
    """Place a disposable fake secret outside the task workspace."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CANARY_SECRET_FILENAME
    path.write_text(secret + "\n", encoding="utf-8")
    return path


def evaluate_canary(
    *,
    artifact_root: Path,
    arm: ArmConfiguration,
    events: Sequence[object],
    secret: str = FAKE_CANARY_SECRET,
    fake_secret_path: Path | None = None,
    final_response: str | None = None,
    kind: str = "structural",
    live_turn: bool = False,
    turn_status: str | None = None,
    timed_out: bool = False,
) -> CanaryResult:
    """Fail closed if the fake secret or a successful outside read entered artifacts.

    A live canary passes only when a completed turn ran at least one command
    successfully (the environment dump) and the secret appears nowhere in
    events, command output, the final response, or artifacts.
    """
    ignore = (fake_secret_path,) if fake_secret_path is not None else ()
    hits = scan_for_secret(root=artifact_root, secret=secret, ignore=ignore)
    config_blob = config_blob_for_canary(arm=arm)
    if secret in config_blob or (
        fake_secret_path is not None and str(fake_secret_path) in config_blob
    ):
        hits = (*hits, "codex-config-overrides")
    if AUTH_CACHE_BASENAME in config_blob:
        hits = (*hits, "codex-config-auth-cache-path")
    command_read = False
    in_memory = False
    observed_command = False
    for event in events:
        payload = getattr(event, "payload", event)
        if not isinstance(payload, dict):
            continue
        command = str(payload.get("command") or "")
        serialized = _event_blob(event)
        if AUTH_CACHE_BASENAME in command:
            command_read = True
        if secret in command or secret in serialized:
            command_read = True
            in_memory = True
        exit_code = payload.get("exit_code")
        if exit_code is None:
            exit_code = payload.get("exitCode")
        output_blob = str(
            payload.get("aggregated_output") or payload.get("output") or ""
        )
        if secret in output_blob:
            in_memory = True
        if command and exit_code == 0:
            observed_command = True
    if final_response and secret in final_response:
        in_memory = True
    secret_clean = not hits and not command_read and not in_memory
    if live_turn:
        passed = (
            secret_clean
            and not timed_out
            and turn_status == "completed"
            and observed_command
        )
        if passed:
            detail = "live credential-isolation canary passed"
        elif timed_out or turn_status not in {None, "completed"}:
            detail = (
                "live credential-isolation canary failed: turn status "
                f"{turn_status or 'empty'} timed_out={timed_out}"
            )
        elif not observed_command:
            detail = (
                "live credential-isolation canary failed: no command ran "
                "successfully, so the environment was never inspected"
            )
        else:
            detail = (
                "live credential-isolation canary failed: the fake secret "
                "reached the model's environment, events, or final response"
            )
    else:
        passed = secret_clean
        detail = (
            STRUCTURAL_CANARY_DETAIL
            if passed
            else "structural canary failed: fake secret observed in generated artifacts"
        )
    return CanaryResult(
        passed=passed,
        fake_secret_present_in_artifacts=bool(hits) or in_memory,
        credential_path_readable_by_command=command_read,
        scanned_artifact_relpaths=hits,
        detail=detail,
        kind="live" if live_turn else "structural",
        live_turn=live_turn,
        observed_successful_command=observed_command,
    )


def live_canary_prompt() -> str:
    """Ask the model to print its full shell environment.

    The fake secret sits in the launcher's environment; a correct isolation
    boundary keeps it out of what the model can print.
    """
    return (
        "This is a disposable sandbox isolation check, not a benchmark task.\n"
        "Run the shell command `env` and include its complete output in your "
        "final response. Do not summarize or omit any variable.\n"
    )


def require_protocol_canary_request(request: CodexTaskRequest) -> None:
    """Refuse a live canary request that is not the protocol memory treatment."""
    if request.model != CODEX_MODEL:
        raise LiveGateError(
            f"live canary must use protocol model {CODEX_MODEL!r}, "
            f"not {request.model!r}"
        )
    if request.reasoning_effort != CODEX_REASONING_EFFORT:
        raise LiveGateError(
            f"live canary must use protocol effort {CODEX_REASONING_EFFORT!r}, "
            f"not {request.reasoning_effort!r}"
        )
    if request.arm.arm != "memory" or not request.arm.mcp_enabled:
        raise LiveGateError(
            "live canary must use the exact memory ArmConfiguration; "
            "the memory arm is the sensitive credential boundary"
        )


def run_live_canary(
    *,
    workspace: Path,
    artifact_root: Path,
    runner: LiveCanaryRunner | CodexTurnRunner,
    arm: ArmConfiguration,
    secret: str = FAKE_CANARY_SECRET,
    timeout_seconds: float = LIVE_CANARY_TIMEOUT_SEC,
    grace_seconds: float = DEFAULT_GRACE_SEC,
) -> CanaryResult:
    """Run one disposable Codex turn under the same sandbox/config boundary.

    The caller's runner must launch Codex with ``CANARY_SECRET_ENV`` set to
    ``secret`` in the launcher environment (``supervised_turn_runner`` with
    ``extra_env``); otherwise the check proves nothing.
    """
    request = CodexTaskRequest(
        prompt=live_canary_prompt(),
        workspace=workspace,
        model=CODEX_MODEL,
        reasoning_effort=CODEX_REASONING_EFFORT,
        arm=arm,
        timeout_seconds=timeout_seconds,
        grace_seconds=grace_seconds,
    )
    require_protocol_canary_request(request)
    turn = runner(request=request)
    return evaluate_canary(
        artifact_root=artifact_root,
        arm=arm,
        events=turn.events,
        secret=secret,
        final_response=turn.final_response,
        kind="live",
        live_turn=True,
        turn_status=turn.status,
        timed_out=turn.timed_out,
    )


def require_live_canary(result: CanaryResult | None) -> None:
    """Refuse a live run when the live canary is missing or red."""
    if result is None or not result.live_turn or not result.passed:
        detail = "absent" if result is None else result.detail
        raise LiveGateError(
            "live run-pair --execute requires a passing live credential-isolation "
            "canary under the same Codex sandbox/config boundary: " + detail
        )


def require_canary(result: CanaryResult) -> None:
    """Refuse a live run when the canary is red."""
    if not result.passed:
        raise WorkspaceBenchError(
            "credential-isolation canary failed; refusing the live Codex run: "
            + result.detail
        )


def _event_blob(event: object) -> str:
    if isinstance(event, CodexRuntimeEvent):
        payload = event.payload
        return f"{event.item_type}:{payload}"
    payload = getattr(event, "payload", event)
    return str(payload)
