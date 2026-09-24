"""``remember setup``: write each coding harness's MCP entry (D108, D136 §6).

Configures Cursor, Claude Code, Claude Desktop, Codex and Antigravity. For
each harness it picks one of four entry shapes:

- **remote** — the MCP endpoint URL only; the harness signs in with OAuth
  when the server asks, so no secret is written anywhere;
- **remote with a key header** — the URL plus ``Authorization: Bearer`` that
  *references* ``REMEMBER_API_KEY`` in the harness's own variable syntax; the
  literal key is never written;
- **stdio bridge** — ``<launcher> mcp`` with ``REMEMBER_MCP_URL``; the key is
  read at run time from ``REMEMBER_API_KEY`` or the credential file;
- **stdio engine** — ``<launcher> mcp`` with ``REMEMBER_API_URL`` for a
  self-hosted engine.

A remote shape is used only where the harness is known to take it; anything
uncertain falls back to a stdio entry, which works everywhere.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from dataclasses import field
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from typing import Final
from typing import Literal

from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class _SetupSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    appdata: Path | None = None
    xdg_config_home: Path | None = None
    #: Set by CI systems; a non-empty value other than ``0``/``false`` means
    #: headless (no browser sign-in).
    ci: str | None = None


#: The variable a remote key-header entry and the stdio bridge read the key from.
KEY_VARIABLE: Final = "REMEMBER_API_KEY"
SERVER_NAME: Final = "remember"
_PROBE_TIMEOUT_SECONDS: Final = 15.0
_CLAUDE_TIMEOUT_SECONDS: Final = 60.0

CURSOR_RULE_CONTENT = """---
description: Use Remember bitemporal memory for codebase facts, architecture, and past decisions
globs: *
alwaysApply: false
---
# Remember Memory Integration

Before making architectural decisions, refactoring core subsystems, or answering
questions about past codebase designs, consult Remember bitemporal memory via the
available MCP tools (`facts_context`, `combined_context`, `claims_and_sources_context`, `resolve_entity`, `query_sql`).

## Retrieval Discipline
1. **Resolve entities first:** Use `resolve_entity` to obtain canonical entity IDs for people, projects, modules, or concepts.
2. **Query facts first:** Use `facts_context` (with `time.mode="history"` for historical context or achievements) as the primary authority for adjudicated truth.
3. **Fall back to claims only when needed:** Use `claims_and_sources_context` if facts are missing or verbatim source text is required.
4. **Use `query_sql`** to run sandboxed SQL against `facts_current` or `graph_edges_current`.

## Temporal Semantics
- `valid_from` / `valid_until`: When the fact was true in the real world. Granularity is given by `valid_precision` (`instant`, `day`, `month`, `quarter`, `year`, `open`, `unknown`). `open` indicates an ongoing state with a known start date and no recorded end date.
- `asserted_at`: Strictly when the source made the statement (message sent / page published). Unresolved relative phrases in claim text (e.g. "last week", "yesterday") are relative to `asserted_at`. Never confuse speech time (`asserted_at`) with event validity (`valid_from`/`valid_until`).
- Check past decisions and bitemporal validity before asserting assumptions.
- Never guess historical rationale when it is recorded in Remember.
"""

ANTIGRAVITY_SKILL_CONTENT = """---
name: remember
description: Open bitemporal memory infrastructure for AI agents. Use when looking up past decisions, system architecture, factual evidence, or attested codebase knowledge.
---

# Remember Bitemporal Memory Skill

You have access to Remember, an open bitemporal memory infrastructure for AI agents.
Use the Remember MCP tools (`facts_context`, `combined_context`, `claims_and_sources_context`, `resolve_entity`, `query_sql`, `describe_query_space`)
to query past system decisions, architectural records, and entity-relationship knowledge graphs.

## Preferred Retrieval Flow
1. **Entity resolution first (`resolve_entity`)**: When an inquiry involves a named person, organization, module, file, or concept, resolve it first with `resolve_entity` to obtain the canonical `entity_id`.
2. **Fact layer first (`facts_context`)**: Query `facts_context` (anchored by `entity_ids` when available, or by semantic text query) as the primary authority for established facts, biography, attributes, relationships, and history.
   - Use `time.mode="history"` for biography, achievements, and "has ever" questions so historical and completed facts remain visible.
   - Use `time.mode="current"` or `"at"` for what holds at an instant, and `"overlap"` for a requested interval.
3. **Sources fallback (`claims_and_sources_context`)**: Only fall back to `claims_and_sources_context` if `facts_context` lacks the answer, or if the inquiry specifically demands verbatim quotes, speaker dialogue details, or raw source context.
4. **Combined context (`combined_context`)**: Use when both adjudicated facts and source claims are needed side by side.

## Dates and Temporal Semantics
Do not collapse distinct temporal dimensions into a single generic date:

- **Facts carry `validity` with `valid_from`, `valid_until`, and `valid_precision`:**
  - `valid_from` / `valid_until`: Real-world event or state validity ("When did this happen or hold true in the world?"). Answer event-time questions using these bounds.
  - `valid_precision`: The granularity of the validity window (`instant`, `day`, `month`, `quarter`, `year`, `open`, or `unknown`).
  - `open`: Represents an ongoing state with a known start date and no recorded end date (still true/current).
  - `unknown`: No usable real-world date was given in the source. Undated facts are clean prose without temporal bracket annotations.
- **Evidence rows (claims) carry `asserted_at`:**
  - `asserted_at`: Strictly **when the source made this statement** (when the message was sent, conversation occurred, or page was published).
  - Unresolved relative phrases: If claim text still contains a relative phrase (*"last week"*, *"yesterday"*, *"two months ago"*), evaluate it relative to that row's `asserted_at`.
  - **Never confuse speech time (`asserted_at`) with real-world event validity (`valid_from` / `valid_until`).**
- **System transaction timestamps (`ingested_at`, `invalidated_at`):**
  - Record when the database learned or superseded the record. Never present system ingestion time as an event or conversation date.
"""


def resolve_launcher() -> tuple[str, list[str]]:
    """Locate launcher candidates and strictly normalize into canonical absolute paths (§4.3).

    Returns a tuple of (canonical_absolute_command, args).
    Raises RuntimeError if neither 'remember' nor 'uvx' can be resolved.
    """
    remember_candidate = shutil.which("remember")
    resolved_remember = (
        Path(remember_candidate).resolve().as_posix() if remember_candidate else None
    )

    uvx_candidate = shutil.which("uvx")
    resolved_uvx = Path(uvx_candidate).resolve().as_posix() if uvx_candidate else None

    # Detect if resolved_remember is ephemeral (inside .venv, virtualenvs, tmp, .cache, uv archives, etc.)
    ephemeral_markers = (
        ".venv",
        "virtualenvs",
        "tmp",
        "site-packages",
        ".cache",
        "archive-v0",
        "uv/archive",
        ".uv",
        "pipx",
    )
    is_ephemeral = resolved_remember is not None and any(
        marker in resolved_remember.lower() for marker in ephemeral_markers
    )

    # Prefer durable uvx launcher over an ephemeral venv or cached wheel binary
    if resolved_uvx and (resolved_remember is None or is_ephemeral):
        return (resolved_uvx, ["remember", "mcp"])
    if resolved_remember:
        return (resolved_remember, ["mcp"])
    if resolved_uvx:
        return (resolved_uvx, ["remember", "mcp"])

    raise RuntimeError(
        "neither 'remember' nor 'uvx' was found on PATH.\n"
        "Please install uv (https://astral.sh/uv) or install remember globally with:\n"
        "  uv tool install remember"
    )


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

Shape = Literal["remote", "remote_key_header", "stdio_bridge", "stdio_engine"]


@dataclass(frozen=True)
class Plan:
    """Where every harness entry of one run points."""

    #: The remote MCP endpoint (the issuer's, or a self-hoster's ``--mcp-url``).
    remote_url: str | None
    #: A remote entry must carry the key header (headless, or a keyed engine).
    key_header: bool
    stdio_shape: Literal["stdio_bridge", "stdio_engine"]
    #: The one variable a stdio entry pins (``REMEMBER_MCP_URL`` or ``REMEMBER_API_URL``).
    stdio_env: dict[str, str]
    launcher_cmd: str
    launcher_args: tuple[str, ...]
    #: The remote endpoint is the hosted one (OAuth sign-in is expected).
    hosted: bool = False


@dataclass(frozen=True)
class RemoteSupport:
    """What a harness is known to accept besides a stdio entry."""

    #: A remote Streamable HTTP entry, with OAuth sign-in when the server asks.
    url: bool = False
    #: A request header that references an environment variable.
    header_env: bool = False


@dataclass(frozen=True)
class Entry:
    """One harness entry, before rendering into the harness's format."""

    shape: Shape
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)

    @property
    def remote(self) -> bool:
        return self.shape in ("remote", "remote_key_header")


def select_entry(plan: Plan, support: RemoteSupport) -> Entry:
    """The D136 §6 selection rule for one harness."""
    if (
        plan.remote_url is not None
        and support.url
        and (support.header_env or not plan.key_header)
    ):
        shape: Shape = "remote_key_header" if plan.key_header else "remote"
        return Entry(shape=shape, url=plan.remote_url)
    return Entry(
        shape=plan.stdio_shape,
        command=plan.launcher_cmd,
        args=plan.launcher_args,
        env=dict(plan.stdio_env),
    )


def _json_entry(entry: Entry) -> dict[str, object]:
    """``mcpServers.remember`` for the JSON harnesses (Cursor's header syntax)."""
    if entry.url is not None:
        rendered: dict[str, object] = {"url": entry.url}
        if entry.shape == "remote_key_header":
            rendered["headers"] = {"Authorization": f"Bearer ${{env:{KEY_VARIABLE}}}"}
        return rendered
    rendered = {"command": entry.command, "args": list(entry.args)}
    if entry.env:
        rendered["env"] = dict(entry.env)
    return rendered


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def _cli_help_mentions(argv: list[str], needle: str) -> bool:
    """Whether ``argv`` (a ``--help`` call) runs and its output names ``needle``."""
    executable = shutil.which(argv[0])
    if executable is None:
        return False
    try:
        result = subprocess.run(
            [executable, *argv[1:]],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and needle in result.stdout + result.stderr


#: Cursor's ``mcp.json`` takes ``url`` entries (OAuth sign-in) and
#: ``${env:NAME}`` in header values. It has no CLI to probe.
CURSOR_SUPPORT: Final = RemoteSupport(url=True, header_env=True)
#: ``claude_desktop_config.json`` takes stdio servers only.
CLAUDE_DESKTOP_SUPPORT: Final = RemoteSupport()
#: Antigravity's remote-entry and header-variable support is not established.
ANTIGRAVITY_SUPPORT: Final = RemoteSupport()


def claude_code_support() -> RemoteSupport:
    """Claude Code takes ``--transport http`` entries and signs in with OAuth.

    Header variable references are not relied on: whether a CLI-added entry
    expands them is not established, so a headless run gets the stdio bridge.
    """
    return RemoteSupport(
        url=_cli_help_mentions(["claude", "mcp", "add", "--help"], "--transport")
    )


def codex_support() -> RemoteSupport:
    """A Codex whose ``mcp add`` knows ``--bearer-token-env-var`` reads ``url``
    entries, ``bearer_token_env_var`` and ``codex mcp login`` (OAuth)."""
    supported = _cli_help_mentions(
        ["codex", "mcp", "add", "--help"], "--bearer-token-env-var"
    )
    return RemoteSupport(url=supported, header_env=supported)


# ---------------------------------------------------------------------------
# Writing files
# ---------------------------------------------------------------------------


def _write_if_changed(path: Path, text: str) -> None:
    """Replace ``path`` atomically, keeping its mode; skip when unchanged."""
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _merged_json(path: Path, entry: Entry) -> str:
    """``path``'s JSON with ``mcpServers.remember`` replaced, everything else kept."""
    config: dict[str, object] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise RuntimeError(
                f"Existing {path} contains invalid JSON: {error}. "
                "Please fix or remove it before configuring Remember."
            ) from error
        if not isinstance(loaded, dict):
            raise RuntimeError(
                f"Existing {path} has invalid structure: expected JSON object at root, "
                f"got {type(loaded).__name__}."
            )
        config = loaded
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise RuntimeError(
            f"Existing {path} has invalid structure: 'mcpServers' must be a JSON "
            f"object, got {type(servers).__name__}."
        )
    servers[SERVER_NAME] = _json_entry(entry)
    return json.dumps(config, indent=2) + "\n"


def _describe(entry: Entry) -> str:
    return {
        "remote": "remote entry (sign-in through the browser)",
        "remote_key_header": f"remote entry with the key from ${KEY_VARIABLE}",
        "stdio_bridge": "stdio bridge (`remember mcp`)",
        "stdio_engine": "stdio engine entry (`remember mcp`)",
    }[entry.shape]


def _print_dry_run(path: object, rendered: str) -> None:
    print(f"[dry-run] Would write to {path}:")
    for line in rendered.rstrip("\n").splitlines():
        print(f"    {line}")


# ---------------------------------------------------------------------------
# Harnesses
# ---------------------------------------------------------------------------


def configure_cursor(*, cwd: Path, entry: Entry, dry_run: bool = False) -> bool:
    """``.cursor/mcp.json`` and the rule file ``.cursor/rules/remember.mdc``."""
    mcp_file = cwd / ".cursor" / "mcp.json"
    rule_file = cwd / ".cursor" / "rules" / "remember.mdc"
    merged = _merged_json(mcp_file, entry)
    if dry_run:
        _print_dry_run(
            mcp_file, json.dumps({SERVER_NAME: _json_entry(entry)}, indent=2)
        )
        print(f"[dry-run] Would write {rule_file}")
        return True
    _write_if_changed(mcp_file, merged)
    _write_if_changed(rule_file, CURSOR_RULE_CONTENT)
    print(f"[✓] Configured Cursor, {_describe(entry)}: {mcp_file} & {rule_file}")
    return True


def configure_antigravity(*, cwd: Path, entry: Entry, dry_run: bool = False) -> bool:
    """``.agents/mcp_config.json`` and the skill ``.agents/skills/remember/SKILL.md``."""
    mcp_file = cwd / ".agents" / "mcp_config.json"
    skill_file = cwd / ".agents" / "skills" / "remember" / "SKILL.md"
    merged = _merged_json(mcp_file, entry)
    if dry_run:
        _print_dry_run(
            mcp_file, json.dumps({SERVER_NAME: _json_entry(entry)}, indent=2)
        )
        print(f"[dry-run] Would write {skill_file}")
        return True
    _write_if_changed(mcp_file, merged)
    _write_if_changed(skill_file, ANTIGRAVITY_SKILL_CONTENT)
    print(f"[✓] Configured Antigravity, {_describe(entry)}: {mcp_file} & {skill_file}")
    return True


def get_claude_desktop_config_path() -> Path:
    """Resolve OS-specific path to claude_desktop_config.json."""
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    settings = _SetupSettings.model_validate({})
    if sys.platform == "win32":
        base = settings.appdata or (Path.home() / "AppData" / "Roaming")
        return base / "Claude" / "claude_desktop_config.json"
    base = settings.xdg_config_home or (Path.home() / ".config")
    return base / "Claude" / "claude_desktop_config.json"


def configure_claude_desktop(*, entry: Entry, dry_run: bool = False) -> bool:
    """Claude Desktop's ``claude_desktop_config.json`` (stdio entries only)."""
    config_path = get_claude_desktop_config_path()
    merged = _merged_json(config_path, entry)
    if dry_run:
        _print_dry_run(
            config_path, json.dumps({SERVER_NAME: _json_entry(entry)}, indent=2)
        )
        return True
    _write_if_changed(config_path, merged)
    print(f"[✓] Configured Claude Desktop, {_describe(entry)}: {config_path}")
    return True


def claude_code_command(entry: Entry) -> list[str]:
    """The ``claude mcp add`` call for ``entry``, in the project's local scope."""
    command = ["claude", "mcp", "add", "--scope", "local"]
    if entry.url is not None:
        # Header variables are never selected for Claude Code (see its probe).
        return [*command, "--transport", "http", SERVER_NAME, entry.url]
    command.append(SERVER_NAME)
    for name, value in entry.env.items():
        command.extend(["-e", f"{name}={value}"])
    return [*command, "--", entry.command or "", *entry.args]


def configure_claude_code(*, cwd: Path, entry: Entry, dry_run: bool = False) -> bool:
    """Register the entry with ``claude mcp add``, replacing any earlier one."""
    add = claude_code_command(entry)
    if dry_run:
        print(f"[dry-run] Would run in {cwd}: {shlex.join(add)}")
        return True
    executable = shutil.which("claude")
    if executable is None:
        print(
            "[-] Claude Code CLI not found on PATH. To configure manually, run in "
            f"{cwd}:\n    {shlex.join(add)}"
        )
        return False
    try:
        # `claude mcp add` refuses an existing name, so an earlier entry goes
        # first; a missing one makes `remove` fail, which is fine.
        subprocess.run(
            [executable, "mcp", "remove", "--scope", "local", SERVER_NAME],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_CLAUDE_TIMEOUT_SECONDS,
            check=False,
        )
        result = subprocess.run(
            [executable, *add[1:]],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_CLAUDE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        print(f"[!] Claude Code registration failed: {error}")
        return False
    if result.returncode != 0:
        print(
            f"[!] Claude Code registration returned exit code {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
        return False
    print(f"[✓] Configured Claude Code, {_describe(entry)}")
    return True


def codex_block(entry: Entry) -> str:
    """The ``[mcp_servers.remember]`` table(s) for ``entry``."""
    lines = [f"[mcp_servers.{SERVER_NAME}]"]
    if entry.url is not None:
        lines.append(f"url = {json.dumps(entry.url)}")
        if entry.shape == "remote_key_header":
            lines.append(f"bearer_token_env_var = {json.dumps(KEY_VARIABLE)}")
    else:
        lines.append(f"command = {json.dumps(entry.command)}")
        lines.append(f"args = {json.dumps(list(entry.args))}")
        if entry.env:
            lines.extend(["", f"[mcp_servers.{SERVER_NAME}.env]"])
            lines.extend(f"{k} = {json.dumps(v)}" for k, v in entry.env.items())
    return "\n".join(lines) + "\n"


#: An existing ``[mcp_servers.remember]`` table or one of its sub-tables, up to
#: the next table header.
_CODEX_TABLE = re.compile(
    rf"(?ms)^\[mcp_servers\.{SERVER_NAME}(?:\.[^\]]+)?\].*?(?=^\[|\Z)"
)


def configure_codex(
    *, cwd: Path, entry: Entry, hosted: bool = False, dry_run: bool = False
) -> bool:
    """``.codex/config.toml``: the ``remember`` tables replaced, the rest kept."""
    config_file = cwd / ".codex" / "config.toml"
    existing = config_file.read_text(encoding="utf-8") if config_file.is_file() else ""
    try:
        tomllib.loads(existing)
    except tomllib.TOMLDecodeError as error:
        raise RuntimeError(
            f"Existing {config_file} contains invalid TOML: {error}. "
            "Please fix or remove it before configuring Remember."
        ) from error
    block = codex_block(entry)
    kept = _CODEX_TABLE.sub("", existing).rstrip()
    content = f"{kept}\n\n{block}" if kept else block
    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise RuntimeError(
            f"Generated configuration for {config_file} contains invalid TOML: {error}."
        ) from error
    if dry_run:
        _print_dry_run(config_file, block)
        return True
    _write_if_changed(config_file, content)
    print(f"[✓] Configured Codex, {_describe(entry)}: {config_file}")
    print("    Codex loads a project's .codex/config.toml only when you trust it.")
    if entry.shape == "remote" and hosted:
        print(f"    Sign in once: run `codex mcp login {SERVER_NAME}` in {cwd}")
    return True


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

Harness = Literal["cursor", "agy", "codex", "claude_code", "claude_desktop"]

_LABELS: Final[dict[Harness, str]] = {
    "cursor": "Cursor",
    "agy": "Antigravity",
    "codex": "Codex",
    "claude_code": "Claude Code",
    "claude_desktop": "Claude Desktop",
}


def _claude_desktop_present() -> bool:
    config = get_claude_desktop_config_path()
    return (
        config.exists()
        or config.parent.is_dir()
        or (sys.platform == "darwin" and Path("/Applications/Claude.app").exists())
    )


def _harnesses(agent: str, target_dir: Path) -> list[Harness]:
    """The harnesses to configure for ``--agent``."""
    if agent == "cursor":
        return ["cursor"]
    if agent == "agy":
        return ["agy"]
    if agent == "codex":
        return ["codex"]
    claude: list[Harness] = []
    if shutil.which("claude"):
        claude.append("claude_code")
    if _claude_desktop_present():
        claude.append("claude_desktop")
    if agent == "claude":
        if not claude:
            raise RuntimeError(
                "Neither Claude Code CLI ('claude') nor Claude Desktop was detected.\n"
                "To install Claude Code CLI: npm install -g @anthropic-ai/claude-code\n"
                "To install Claude Desktop:  https://claude.ai/download"
            )
        return claude
    found: list[Harness] = []
    if (target_dir / ".cursor").is_dir():
        found.append("cursor")
    if (target_dir / ".agents").is_dir():
        found.append("agy")
    if (
        (target_dir / ".codex").is_dir()
        or shutil.which("codex")
        or (Path.home() / ".codex").is_dir()
    ):
        found.append("codex")
    found.extend(claude)
    return found or ["cursor", "agy"]


def _configure(
    harness: Harness, *, target_dir: Path, plan: Plan, dry_run: bool
) -> bool:
    """Probe one harness, select its entry and write it."""
    if harness == "cursor":
        entry = select_entry(plan, CURSOR_SUPPORT)
        return configure_cursor(cwd=target_dir, entry=entry, dry_run=dry_run)
    if harness == "agy":
        entry = select_entry(plan, ANTIGRAVITY_SUPPORT)
        return configure_antigravity(cwd=target_dir, entry=entry, dry_run=dry_run)
    if harness == "codex":
        entry = select_entry(plan, codex_support())
        return configure_codex(
            cwd=target_dir, entry=entry, hosted=plan.hosted, dry_run=dry_run
        )
    if harness == "claude_code":
        entry = select_entry(plan, claude_code_support())
        return configure_claude_code(cwd=target_dir, entry=entry, dry_run=dry_run)
    entry = select_entry(plan, CLAUDE_DESKTOP_SUPPORT)
    return configure_claude_desktop(entry=entry, dry_run=dry_run)


def _headless(args: argparse.Namespace) -> bool:
    if getattr(args, "headless", False):
        return True
    ci = (_SetupSettings.model_validate({}).ci or "").strip().lower()
    return ci not in ("", "0", "false")


def _choose_backend(args: argparse.Namespace, *, dry_run: bool) -> str:
    """``cloud`` or ``self_hosted``; an engine or listener URL means self-hosted."""
    target_env: str | None = getattr(args, "target_env", None)
    explicit_self_hosted = bool(
        getattr(args, "api_url", None) or getattr(args, "mcp_url", None)
    )
    if target_env == "cloud" and explicit_self_hosted:
        raise ValueError(
            "--api-url and --mcp-url are for a self-hosted engine; drop --cloud"
        )
    if target_env is not None:
        return target_env
    if explicit_self_hosted:
        return "self_hosted"
    if sys.stdin.isatty() and not dry_run:
        print("Choose your Remember backend:")
        print("  1) remember.dev [default]")
        print("  2) Self-hosted engine (http://127.0.0.1:8000)")
        try:
            choice = input("Select [1/2, default 1]: ").strip()
        except EOFError:
            choice = ""
        return "self_hosted" if choice == "2" else "cloud"
    return "cloud"


def _self_hosted_plan(
    args: argparse.Namespace, *, launcher: tuple[str, list[str]], dry_run: bool
) -> Plan | None:
    """Store the engine URL (and key) and plan stdio engine entries.

    Returns ``None`` after printing the reason when a hosted key is stored.
    """
    from pydantic import SecretStr

    from remember.connection import DEFAULT_API_URL
    from remember.connection import normalize_key
    from remember.credentials import credential_lock
    from remember.credentials import CredentialError
    from remember.credentials import load_credentials
    from remember.credentials import StoredCredentials
    from remember.credentials import write_credentials
    from remember.issuer import require_secure_url

    if getattr(args, "issuer", None):
        raise ValueError("--issuer is for remember.dev (--cloud)")
    url: str = getattr(args, "api_url", None) or DEFAULT_API_URL
    key: str | None = getattr(args, "api_key", None)
    mcp_url: str | None = getattr(args, "mcp_url", None)
    if mcp_url:
        mcp_url = str(require_secure_url(mcp_url.strip(), what="--mcp-url"))
    print("Backend: self-hosted engine")
    print(f"  Engine URL: {url}")
    refusal = (
        "error: a key from {issuer} is stored; run `remember logout` "
        "before configuring a self-hosted engine"
    )

    def stored_issuer() -> str | None:
        try:
            current = load_credentials()
        except CredentialError:
            return None
        return current.issuer if current is not None else None

    if dry_run:
        issuer = stored_issuer()
        if issuer:
            print(refusal.format(issuer=issuer), file=sys.stderr)
            return None
    else:
        # Read, check and write under the lock `remember login` holds, so a
        # concurrent login's key is never overwritten unrevoked. The key goes
        # to the owner-only credential file, never into a harness file.
        with credential_lock():
            issuer = stored_issuer()
            if issuer:
                print(refusal.format(issuer=issuer), file=sys.stderr)
                return None
            write_credentials(
                credentials=StoredCredentials(
                    version=2,
                    api_url=url,
                    key=SecretStr(normalize_key(key)) if key else None,
                )
            )
        print("[✓] Stored the engine URL (and key) in the owner-only credential file")
    if mcp_url and key:
        print(
            f"  Remote entries send ${KEY_VARIABLE}: set it in the agent's environment"
        )
    return Plan(
        remote_url=mcp_url,
        key_header=bool(key),
        stdio_shape="stdio_engine",
        stdio_env={"REMEMBER_API_URL": url},
        launcher_cmd=launcher[0],
        launcher_args=tuple(launcher[1]),
    )


def _hosted_plan(
    args: argparse.Namespace,
    *,
    launcher: tuple[str, list[str]],
    dry_run: bool,
    headless: bool,
) -> Plan:
    """Find the issuer's MCP endpoint, signing in first when there is no key."""
    import httpx

    from remember.connection import resolve_connection
    from remember.issuer import DEFAULT_ISSUER
    from remember.issuer import fetch_issuer_metadata
    from remember.issuer import normalize_issuer
    from remember.login import login

    if getattr(args, "api_key", None):
        raise ValueError(
            "--api-key is for --self-hosted; for remember.dev run `remember login`, "
            f"or set {KEY_VARIABLE} in the agent's environment"
        )
    connection = resolve_connection(issuer=getattr(args, "issuer", None))
    issuer = normalize_issuer(connection.issuer or DEFAULT_ISSUER)
    print(f"Backend: {issuer}")
    with httpx.Client(timeout=30.0, follow_redirects=False) as http:
        if connection.key is None:
            if headless:
                print(f"  No key stored; the agents read it from ${KEY_VARIABLE}.")
            elif dry_run:
                print("[dry-run] Would run `remember login` first (no key stored)")
            else:
                print("  Not signed in; running `remember login` first.")
                login(issuer=issuer, http=http)
        endpoint = fetch_issuer_metadata(issuer, http=http).endpoint(
            "remember_mcp_endpoint"
        )
    print(f"  MCP endpoint: {endpoint}")
    return Plan(
        remote_url=endpoint,
        key_header=headless,
        stdio_shape="stdio_bridge",
        stdio_env={"REMEMBER_MCP_URL": endpoint},
        launcher_cmd=launcher[0],
        launcher_args=tuple(launcher[1]),
        hosted=True,
    )


def run_setup(args: argparse.Namespace, *, cwd: Path | None = None) -> int:
    """Write the ``remember`` MCP entry of each requested or detected harness."""
    target_dir: Path = getattr(args, "cwd", None) or cwd or Path.cwd()
    dry_run: bool = getattr(args, "dry_run", False)
    agent: str = getattr(args, "agent", None) or "all"
    try:
        launcher = resolve_launcher()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    harnesses = _harnesses(agent, target_dir)
    if _choose_backend(args, dry_run=dry_run) == "self_hosted":
        plan = _self_hosted_plan(args, launcher=launcher, dry_run=dry_run)
        if plan is None:
            return 1
    else:
        headless = _headless(args)
        plan = _hosted_plan(args, launcher=launcher, dry_run=dry_run, headless=headless)

    print("Configuring AI coding harnesses for Remember:")
    print(f"  Launcher command: {launcher[0]} {' '.join(launcher[1])}")
    if dry_run:
        print("  Mode: DRY RUN (no files will be written)")
    print()

    failed: list[str] = []
    for harness in harnesses:
        try:
            ok = _configure(harness, target_dir=target_dir, plan=plan, dry_run=dry_run)
        except RuntimeError as error:
            print(f"error: {error}", file=sys.stderr)
            ok = False
        if not ok:
            failed.append(_LABELS[harness])
    if failed:
        print(f"error: failed to configure {', '.join(failed)}.", file=sys.stderr)
        return 1
    print(
        "\nTip: Run `uv tool install remember` to install the remember CLI "
        "permanently to your shell PATH."
    )
    return 0
