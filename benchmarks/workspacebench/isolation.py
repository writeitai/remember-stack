"""Disposable Codex home and process isolation. Does not read ``auth.json``."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Final

from benchmarks.workspacebench.env import sanitized_subprocess_env
from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import sha256_file
from benchmarks.workspacebench.protocol import AUTH_CACHE_BASENAME
from benchmarks.workspacebench.protocol import CODEX_CLI_VERSION
from benchmarks.workspacebench.protocol import CODEX_CREDENTIALS_STORE
from benchmarks.workspacebench.protocol import CODEX_ISOLATION
from benchmarks.workspacebench.protocol import CODEX_SDK_VERSION

# Fixed trusted POSIX exec boundary. The env process may inherit the parent
# environment; ``-i`` then execs the pinned binary with only allowlisted vars.
TRUSTED_ENV_BIN: Final = Path("/usr/bin/env")
_ENV_NAME: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_KEY_MARKERS: Final = (
    "token",
    "authorization",
    "password",
    "secret",
    "api_key",
    "apikey",
    "openai_api_key",
    "bearer",
)

# Minimal config for a disposable $CODEX_HOME. No MCP servers, hooks, plugins,
# skills, or global instructions. ChatGPT credentials stay in the OS keyring.
DISPOSABLE_CODEX_CONFIG_TOML = """\
cli_auth_credentials_store = "keyring"
mcp_oauth_credentials_store = "keyring"
web_search = false

[shell_environment_policy]
inherit = "core"
ignore_default_excludes = false
experimental_use_profile = false

[sandbox_workspace_write]
network_access = false
exclude_tmpdir_env_var = true
exclude_slash_tmp = true
"""

KEYRING_LOGIN_HELP = (
    "Workspace-Bench requires ChatGPT credentials in the OS keyring, not a "
    'file-backed auth.json cache. Set cli_auth_credentials_store = "keyring" '
    "in your Codex user config, run `codex login`, then `codex login status`. "
    "This adapter never reads, copies, parses, or logs auth.json; live runs "
    "use a disposable CODEX_HOME plus keyring auth."
)


@dataclass(frozen=True)
class CodexRuntimeInspection:
    """Pinned bundled CLI identity. Contains no credential material or home path."""

    sdk_version: str
    cli_version: str
    cli_sha256: str
    isolation: str
    credentials_store: str


@dataclass(frozen=True)
class CodexIsolation:
    """One disposable Codex home. The directory is not an auth-cache path."""

    home: Path
    config_toml: str


class CodexIsolationError(WorkspaceBenchError):
    """Codex runtime isolation or the pinned bundled CLI failed closed."""


_CACHED_RUNTIME_PIN: CodexRuntimeInspection | None = None


def prepare_disposable_codex_home(directory: Path) -> CodexIsolation:
    """Create a minimal CODEX_HOME with no user MCP, hooks, plugins, or auth.json."""
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / "config.toml"
    config_path.write_text(DISPOSABLE_CODEX_CONFIG_TOML, encoding="utf-8")
    auth_path = directory / AUTH_CACHE_BASENAME
    if auth_path.exists():
        raise CodexIsolationError(
            "disposable CODEX_HOME must not contain auth.json; "
            "refusing to use or copy a credential cache"
        )
    isolation = CodexIsolation(home=directory, config_toml=DISPOSABLE_CODEX_CONFIG_TOML)
    _reject_user_config_payloads(isolation=isolation)
    return isolation


def codex_child_env(
    *, isolation: CodexIsolation, include_remember_config: bool
) -> dict[str, str]:
    """Allowlisted child environment with CODEX_HOME forced to the disposable home.

    Ambient CODEX_HOME is dropped so a user profile cannot leak MCP servers,
    hooks, plugins, skills, or instructions into either arm. Remember config-dir
    discovery is kept only for the memory arm's required read-only MCP.
    """
    env = sanitized_subprocess_env()
    env.pop("CODEX_HOME", None)
    if not include_remember_config:
        env.pop("REMEMBER_CONFIG_DIR", None)
        env.pop("REMEMBERSTACK_CONFIG_DIR", None)
    env["CODEX_HOME"] = str(isolation.home)
    return env


def isolated_exec_argv(*, env: Mapping[str, str], binary: Path) -> tuple[str, ...]:
    """Return ``/usr/bin/env -i KEY=VALUE ... binary`` for a sanitized child.

    openai-codex 0.147.0 ``CodexClient.start`` copies ``os.environ`` and then
    updates ``CodexConfig.env`` before ``Popen``. This prefix is the child-side
    exec boundary: the trusted env process may inherit that merged parent
    environment, then ``-i`` execs ``binary`` with only ``env``. Do not put
    secrets or an auth-cache path in the returned argv. Does not mutate the
    parent process environment.
    """
    if not TRUSTED_ENV_BIN.is_file() or not os.access(TRUSTED_ENV_BIN, os.X_OK):
        raise CodexIsolationError(
            f"trusted env binary is missing or not executable: {TRUSTED_ENV_BIN}"
        )
    if not binary.is_file():
        raise CodexIsolationError(f"isolated exec target is not a file: {binary}")
    assignments: list[str] = []
    for key in sorted(env):
        assignments.append(_env_assignment(key=key, value=env[key]))
    argv = (str(TRUSTED_ENV_BIN), "-i", *assignments, str(binary))
    blob = "\0".join(argv)
    if AUTH_CACHE_BASENAME in blob:
        raise CodexIsolationError("isolation argv must not name auth.json")
    return argv


def isolated_command_argv(
    *, env: Mapping[str, str], binary: Path, args: Sequence[str] = ()
) -> tuple[str, ...]:
    """Isolation prefix plus arguments passed to the pinned binary."""
    return (*isolated_exec_argv(env=env, binary=binary), *args)


def inspect_bundled_codex_cli(*, force: bool = False) -> CodexRuntimeInspection:
    """Resolve the pinned bundled CLI, require version 0.147.0, and hash it."""
    global _CACHED_RUNTIME_PIN
    if _CACHED_RUNTIME_PIN is not None and not force:
        return _CACHED_RUNTIME_PIN
    try:
        from openai_codex.client import _default_codex_bin_resolver_ops
        from openai_codex.client import CodexConfig
        from openai_codex.client import resolve_codex_bin
    except ImportError as error:
        raise CodexIsolationError(
            "Workspace-Bench Codex execution requires the benchmark extra: "
            "uv sync --extra benchmark"
        ) from error
    binary = resolve_codex_bin(CodexConfig(), _default_codex_bin_resolver_ops())
    if not binary.is_file():
        raise CodexIsolationError(f"bundled Codex CLI is not a file: {binary}")
    version = _read_cli_version(binary=binary)
    if version != CODEX_CLI_VERSION:
        raise CodexIsolationError(
            f"bundled Codex CLI version {version!r} does not match pinned "
            f"{CODEX_CLI_VERSION!r}"
        )
    pin = CodexRuntimeInspection(
        sdk_version=CODEX_SDK_VERSION,
        cli_version=version,
        cli_sha256=sha256_file(binary),
        isolation=CODEX_ISOLATION,
        credentials_store=CODEX_CREDENTIALS_STORE,
    )
    _CACHED_RUNTIME_PIN = pin
    return pin


def resolve_bundled_codex_binary() -> Path:
    """Return the openai-codex-cli-bin 0.147.0 executable path."""
    try:
        from openai_codex.client import _default_codex_bin_resolver_ops
        from openai_codex.client import CodexConfig
        from openai_codex.client import resolve_codex_bin
    except ImportError as error:
        raise CodexIsolationError(
            "Workspace-Bench Codex execution requires the benchmark extra: "
            "uv sync --extra benchmark"
        ) from error
    binary = resolve_codex_bin(CodexConfig(), _default_codex_bin_resolver_ops())
    if not binary.is_file():
        raise CodexIsolationError(f"bundled Codex CLI is not a file: {binary}")
    return binary


def _read_cli_version(*, binary: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="wb-codex-version-") as home:
        isolation = prepare_disposable_codex_home(Path(home))
        env = codex_child_env(isolation=isolation, include_remember_config=False)
        argv = isolated_command_argv(env=env, binary=binary, args=("--version",))
        try:
            completed = subprocess.run(  # noqa: S603 -- trusted env prefix, pinned binary
                list(argv), check=False, capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CodexIsolationError(
                f"failed to invoke bundled Codex CLI --version: {error}"
            ) from error
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise CodexIsolationError(
            "bundled Codex CLI --version failed with "
            f"exit {completed.returncode}: {stderr or completed.stdout}"
        )
    line = (completed.stdout or "").strip().splitlines()[0] if completed.stdout else ""
    prefix = "codex-cli "
    if not line.startswith(prefix):
        raise CodexIsolationError(
            f"bundled Codex CLI --version returned unexpected output: {line!r}"
        )
    return line[len(prefix) :].strip()


def _env_assignment(*, key: str, value: str) -> str:
    if not _ENV_NAME.fullmatch(key):
        raise CodexIsolationError(
            f"refusing non-portable environment key {key!r} on isolation argv"
        )
    lowered = key.lower()
    if any(marker in lowered for marker in _SECRET_KEY_MARKERS):
        raise CodexIsolationError(f"isolation argv must not include credential {key}")
    if "\x00" in value:
        raise CodexIsolationError(f"environment value for {key} contains NUL")
    assignment = f"{key}={value}"
    if AUTH_CACHE_BASENAME in assignment:
        raise CodexIsolationError("isolation argv must not name auth.json")
    return assignment


def _reject_user_config_payloads(*, isolation: CodexIsolation) -> None:
    blob = isolation.config_toml.lower()
    if AUTH_CACHE_BASENAME in blob:
        raise CodexIsolationError("disposable Codex config must not name auth.json")
    if "[mcp_servers" in blob:
        raise CodexIsolationError(
            "disposable Codex config must not declare MCP servers; "
            "the memory arm adds the required Remember server via --config"
        )
    if (isolation.home / AUTH_CACHE_BASENAME).exists():
        raise CodexIsolationError("disposable CODEX_HOME must not contain auth.json")
    for relative in ("hooks", "plugins", "skills", "AGENTS.md"):
        if (isolation.home / relative).exists():
            raise CodexIsolationError(
                f"disposable CODEX_HOME must not contain user {relative}"
            )
