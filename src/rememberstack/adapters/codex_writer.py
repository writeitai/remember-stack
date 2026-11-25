"""Sandboxed stock-Codex adapter for one declared-output Plane-K session."""

import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import KnowledgeAgentSessionRequest
from rememberstack.model import KnowledgeAgentSessionResult


class CodexAgentAdapterSettings(BaseSettings):
    """The executable binding shared by Plane-K stock-harness seats."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_K_CODEX_")

    executable: str = Field(default="codex", min_length=1)


class CodexWriterAdapterSettings(BaseSettings):
    """Backward-compatible writer-specific executable settings."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_K_WRITER_CODEX_")

    executable: str = Field(default="codex", min_length=1)


class CodexCLIAgentAdapter:
    """Run Codex in an ephemeral non-git workspace and accept declared files only."""

    def __init__(
        self, *, settings: CodexAgentAdapterSettings | CodexWriterAdapterSettings
    ) -> None:
        """Bind the adapter to its settings-owned executable."""
        self._settings = settings

    def run_session(
        self, *, request: KnowledgeAgentSessionRequest
    ) -> KnowledgeAgentSessionResult:
        """Run one sandboxed session and return raw outputs without accepting them."""
        with TemporaryDirectory(prefix="rememberstack-k-writer-") as temporary:
            workspace = Path(temporary)
            for relative_path, content in request.input_files.items():
                target = _workspace_path(
                    workspace=workspace, relative_path=relative_path
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            for relative_path in request.sandbox.accepted_output_paths:
                _workspace_path(
                    workspace=workspace, relative_path=relative_path
                ).parent.mkdir(parents=True, exist_ok=True)
            writer_workdir = workspace / "output"
            last_message = workspace / ".session" / "last-message.txt"
            last_message.parent.mkdir(parents=True, exist_ok=True)
            command = _codex_command(
                executable=self._settings.executable,
                request=request,
                writer_workdir=writer_workdir,
                last_message=last_message,
            )
            try:
                completed = subprocess.run(  # noqa: S603 -- fixed argv, no shell
                    command,
                    input=request.prompt,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=request.timeout_seconds,
                )
            except (  # error-contract: raised later by the knowledge driver
                subprocess.TimeoutExpired
            ) as error:
                stdout = _timeout_text(value=error.stdout)
                stderr = _timeout_text(value=error.stderr)
                return KnowledgeAgentSessionResult(
                    session_id=request.session_id,
                    exit_code=None,
                    timed_out=True,
                    output_files=_declared_outputs(
                        workspace=workspace, paths=request.sandbox.accepted_output_paths
                    ),
                    transcript=_process_transcript(
                        stdout=stdout, stderr=stderr, exit_code=None, timed_out=True
                    ),
                    tokens=_token_usage(stdout=stdout),
                )
            return KnowledgeAgentSessionResult(
                session_id=request.session_id,
                exit_code=completed.returncode,
                output_files=_declared_outputs(
                    workspace=workspace, paths=request.sandbox.accepted_output_paths
                ),
                transcript=_process_transcript(
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    exit_code=completed.returncode,
                    timed_out=False,
                ),
                tokens=_token_usage(stdout=completed.stdout),
            )


def _codex_command(
    *,
    executable: str,
    request: KnowledgeAgentSessionRequest,
    writer_workdir: Path,
    last_message: Path,
) -> list[str]:
    """Build the fixed stock-harness command with no inherited tools or web search."""
    return [
        executable,
        "exec",
        "--strict-config",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-c",
        "sandbox_workspace_write.network_access=false",
        "-c",
        "shell_environment_policy.inherit=none",
        "-c",
        "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        "-c",
        "sandbox_workspace_write.exclude_slash_tmp=true",
        "--model",
        request.model,
        "--json",
        "--color",
        "never",
        "--output-last-message",
        str(last_message),
        "--cd",
        str(writer_workdir),
        "-",
    ]


def _declared_outputs(*, workspace: Path, paths: tuple[str, ...]) -> dict[str, str]:
    """Read only contract-declared UTF-8 files and discard every other writer edit."""
    outputs: dict[str, str] = {}
    for relative_path in paths:
        try:
            target = _workspace_path(workspace=workspace, relative_path=relative_path)
            if target.is_file():
                outputs[relative_path] = target.read_text(encoding="utf-8")
        except (
            OSError,
            UnicodeError,
            ValueError,
        ):  # error-contract: raised later by the compiler as missing declared output
            # The compiler rejects the missing declared output after it archives
            # the process transcript; hostile links and unreadable bytes cannot
            # make a completed session disappear from the ledger.
            continue
    return outputs


def _workspace_path(*, workspace: Path, relative_path: str) -> Path:
    """Resolve a validated relative path without following a workspace escape."""
    target = workspace.joinpath(*relative_path.split("/"))
    if target.is_symlink() or not target.resolve().is_relative_to(workspace.resolve()):
        raise ValueError(f"writer workspace path escapes root: {relative_path}")
    return target


def _timeout_text(*, value: str | bytes | None) -> str:
    """Normalize subprocess timeout fragments without discarding captured bytes."""
    if value is None:
        return ""
    return (
        value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    )


def _process_transcript(
    *, stdout: str, stderr: str, exit_code: int | None, timed_out: bool
) -> str:
    """Retain complete process streams and terminal status as one immutable ledger object."""
    return json.dumps(
        {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
        },
        sort_keys=True,
    )


def _token_usage(*, stdout: str) -> int | None:
    """Sum typed Codex turn usage without treating its prose as trusted output."""
    total = 0
    found = False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if (
            not isinstance(input_tokens, int)
            or input_tokens < 0
            or not isinstance(output_tokens, int)
            or output_tokens < 0
        ):
            continue
        total += input_tokens + output_tokens
        found = True
    return total if found else None


CodexCLIWriterAdapter = CodexCLIAgentAdapter
