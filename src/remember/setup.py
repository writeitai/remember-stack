"""The AI coding agent bootstrapper for `remember setup` (D108).

Configures persistent MCP connections and context rules for Cursor, Claude Code,
Claude Desktop, Codex, and Antigravity with zero secret leakage in git and
durable absolute launcher paths.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class _DesktopSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    appdata: Path | None = None
    xdg_config_home: Path | None = None


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


def configure_cursor(
    *,
    cwd: Path,
    launcher_cmd: str,
    launcher_args: list[str],
    env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> bool:
    """Configure Cursor (.cursor/mcp.json and .cursor/rules/remember.mdc)."""
    cursor_dir = cwd / ".cursor"
    mcp_file = cursor_dir / "mcp.json"
    rules_dir = cursor_dir / "rules"
    rule_file = rules_dir / "remember.mdc"

    mcp_config: dict[str, object] = {}
    if mcp_file.is_file():
        try:
            loaded = json.loads(mcp_file.read_text(encoding="utf-8"))
        except Exception as error:
            raise RuntimeError(
                f"Existing {mcp_file} contains invalid JSON: {error}. "
                "Please fix or remove it before configuring Remember."
            ) from error
        if not isinstance(loaded, dict):
            raise RuntimeError(
                f"Existing {mcp_file} has invalid structure: expected JSON object at root, got {type(loaded).__name__}."
            )
        mcp_config = loaded

    servers_raw = mcp_config.setdefault("mcpServers", {})
    if not isinstance(servers_raw, dict):
        raise RuntimeError(
            f"Existing {mcp_file} has invalid structure: 'mcpServers' must be a JSON object, got {type(servers_raw).__name__}."
        )

    servers: dict[str, object] = servers_raw
    server_entry: dict[str, object] = {"command": launcher_cmd, "args": launcher_args}
    if env:
        server_entry["env"] = env
    servers["remember"] = server_entry

    if dry_run:
        print(f"[dry-run] Would update {mcp_file}")
        print(f"[dry-run] Would write {rule_file}")
        return True

    cursor_dir.mkdir(parents=True, exist_ok=True)
    mcp_file.write_text(json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8")

    rules_dir.mkdir(parents=True, exist_ok=True)
    rule_file.write_text(CURSOR_RULE_CONTENT, encoding="utf-8")
    print(f"[✓] Configured Cursor: {mcp_file} & {rule_file}")
    return True


def configure_claude_code(
    *,
    launcher_cmd: str,
    launcher_args: list[str],
    env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> bool:
    """Register MCP server with Claude Code CLI (claude mcp add)."""
    cmd = ["claude", "mcp", "add", "remember"]
    if env:
        for k, v in env.items():
            cmd.extend(["-e", f"{k}={v}"])
    cmd.extend(["--", launcher_cmd, *launcher_args])

    if dry_run:
        print(f"[dry-run] Would execute: {' '.join(cmd)}")
        return True

    if not shutil.which("claude"):
        print(
            f"[-] Claude Code CLI not found on PATH. To configure manually, run:\n    {' '.join(cmd)}"
        )
        return False

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            print("[✓] Configured Claude Code CLI (claude mcp add remember)")
            return True
        print(
            f"[!] Claude Code registration returned exit code {res.returncode}: {res.stderr.strip() or res.stdout.strip()}"
        )
        return False
    except Exception as error:
        print(f"[!] Claude Code registration failed: {error}")
        return False


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
    settings = _DesktopSettings.model_validate({})
    if sys.platform == "win32":
        base = settings.appdata or (Path.home() / "AppData" / "Roaming")
        return base / "Claude" / "claude_desktop_config.json"
    # Linux / XDG
    base = settings.xdg_config_home or (Path.home() / ".config")
    return base / "Claude" / "claude_desktop_config.json"


def configure_claude_desktop(
    *,
    launcher_cmd: str,
    launcher_args: list[str],
    env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> bool:
    """Configure Claude Desktop app configuration."""
    config_path = get_claude_desktop_config_path()
    config: dict[str, object] = {}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as error:
            raise RuntimeError(
                f"Existing {config_path} contains invalid JSON: {error}. "
                "Please fix or remove it before configuring Remember."
            ) from error
        if not isinstance(loaded, dict):
            raise RuntimeError(
                f"Existing {config_path} has invalid structure: expected JSON object at root, got {type(loaded).__name__}."
            )
        config = loaded

    servers_raw = config.setdefault("mcpServers", {})
    if not isinstance(servers_raw, dict):
        raise RuntimeError(
            f"Existing {config_path} has invalid structure: 'mcpServers' must be a JSON object, got {type(servers_raw).__name__}."
        )

    servers: dict[str, object] = servers_raw
    server_entry: dict[str, object] = {"command": launcher_cmd, "args": launcher_args}
    if env:
        server_entry["env"] = env
    servers["remember"] = server_entry

    if dry_run:
        print(f"[dry-run] Would update {config_path}")
        return True

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"[✓] Configured Claude Desktop: {config_path}")
    return True


def configure_codex(
    *,
    cwd: Path,
    launcher_cmd: str,
    launcher_args: list[str],
    env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> bool:
    """Configure Codex MCP servers in .codex/config.toml."""
    import re
    import tomllib

    codex_dir = cwd / ".codex"
    config_file = codex_dir / "config.toml"

    existing_content = (
        config_file.read_text(encoding="utf-8") if config_file.is_file() else ""
    )
    if config_file.is_file():
        try:
            tomllib.loads(existing_content)
        except Exception as error:
            raise RuntimeError(
                f"Existing {config_file} contains invalid TOML: {error}. "
                "Please fix or remove it before configuring Remember."
            ) from error

    cmd_repr = json.dumps(launcher_cmd)
    args_repr = json.dumps(launcher_args)
    block_lines = [
        "",
        "[mcp_servers.remember]",
        f"command = {cmd_repr}",
        f"args = {args_repr}",
    ]
    if env:
        block_lines.append("")
        block_lines.append("[mcp_servers.remember.env]")
        for k, v in env.items():
            block_lines.append(f"{k} = {json.dumps(v)}")
    block = "\n".join(block_lines) + "\n"

    # Strip any existing [mcp_servers.remember] and [mcp_servers.remember.*] sections (cleans stale configs and leaked secrets)
    pattern = r"(?ms)^\[mcp_servers\.remember(?:\.[^\]]+)?\].*?(?=(?:^\[|\Z))"
    cleaned = re.sub(pattern, "", existing_content).rstrip()

    if cleaned:
        new_content = cleaned + "\n\n" + block.lstrip()
    else:
        new_content = block.lstrip()

    # Validate that resulting document parses cleanly
    try:
        tomllib.loads(new_content)
    except Exception as error:
        raise RuntimeError(
            f"Generated configuration for {config_file} contains invalid TOML: {error}."
        ) from error

    if dry_run:
        print(f"[dry-run] Would update {config_file} with [mcp_servers.remember]")
        return True

    codex_dir.mkdir(parents=True, exist_ok=True)
    config_file.write_text(new_content, encoding="utf-8")
    print(f"[✓] Configured Codex: {config_file}")
    print(
        "    Note: Project-local Codex MCP servers require the project directory to be trusted by Codex."
    )
    return True


def configure_antigravity(
    *,
    cwd: Path,
    launcher_cmd: str,
    launcher_args: list[str],
    env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> bool:
    """Configure Antigravity MCP servers in .agents/mcp_config.json."""
    agents_dir = cwd / ".agents"
    mcp_file = agents_dir / "mcp_config.json"
    skill_dir = agents_dir / "skills" / "remember"
    skill_file = skill_dir / "SKILL.md"

    config: dict[str, Any] = {"mcpServers": {}}
    if mcp_file.is_file():
        try:
            loaded = json.loads(mcp_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            raise RuntimeError(
                f"Antigravity config {mcp_file} exists but contains invalid JSON: {err}. Refusing to overwrite."
            ) from err
        if not isinstance(loaded, dict):
            raise RuntimeError(
                f"Antigravity config {mcp_file} has invalid structure: expected JSON object at root, got {type(loaded).__name__}."
            )
        config = loaded

    if "mcpServers" in config and not isinstance(config["mcpServers"], dict):
        raise RuntimeError(
            f"Antigravity config {mcp_file} has invalid structure: 'mcpServers' must be a JSON object, got {type(config['mcpServers']).__name__}."
        )

    servers = config.setdefault("mcpServers", {})
    entry: dict[str, Any] = {"command": launcher_cmd, "args": launcher_args}
    if env:
        entry["env"] = env
    servers["remember"] = entry

    if dry_run:
        print(f"[dry-run] Would update {mcp_file} and create {skill_file}")
        return True

    agents_dir.mkdir(parents=True, exist_ok=True)
    mcp_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(ANTIGRAVITY_SKILL_CONTENT, encoding="utf-8")
    print(f"[✓] Configured Antigravity: {mcp_file} & {skill_file}")
    return True


def run_setup(args: argparse.Namespace, *, cwd: Path | None = None) -> int:
    """Execute harness configuration according to parsed CLI args."""
    target_dir = getattr(args, "cwd", None) or cwd or Path.cwd()

    try:
        launcher_cmd, launcher_args = resolve_launcher()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    dry_run: bool = getattr(args, "dry_run", False)
    target_agent: str = getattr(args, "agent", None) or "all"
    target_env: str | None = getattr(args, "target_env", None)
    is_cloud: bool = target_env == "cloud" or getattr(args, "cloud", False)
    is_self_hosted: bool = target_env == "self_hosted" or getattr(
        args, "self_hosted", False
    )
    target_url: str | None = getattr(args, "api_url", None)
    target_key: str | None = getattr(args, "api_key", None)

    # Interactive choice if neither is specified on a TTY
    if target_env is None and not is_cloud and not is_self_hosted:
        if sys.stdin.isatty() and not dry_run:
            print("Choose your Remember backend:")
            print("  1) Remember Cloud [default]")
            print("  2) Self-Hosted Engine (http://127.0.0.1:8000)")
            try:
                choice = input("Select [1/2, default 1]: ").strip()
                if choice == "2":
                    is_self_hosted = True
                else:
                    is_cloud = True
            except (EOFError, KeyboardInterrupt):
                is_cloud = True
        else:
            is_cloud = True

    from pydantic import SecretStr

    from remember.connection import normalize_key
    from remember.credentials import credential_lock
    from remember.credentials import CredentialError
    from remember.credentials import load_credentials
    from remember.credentials import StoredCredentials
    from remember.credentials import write_credentials

    try:
        stored = load_credentials()
    except CredentialError:
        stored = None

    env: dict[str, str] = {}
    if is_self_hosted:
        url = target_url or "http://127.0.0.1:8000"
        env["REMEMBER_API_URL"] = url
        print("Backend: Self-Hosted Engine")
        print(f"  Engine URL: {url}")
        refusal = (
            "error: a key from {issuer} is stored; run `remember logout` "
            "before configuring a self-hosted engine"
        )
        if dry_run:
            if stored is not None and stored.issuer:
                print(refusal.format(issuer=stored.issuer), file=sys.stderr)
                return 1
        else:
            # Read, check and write under the lock `remember login` holds, so
            # a concurrent login's key is never overwritten unrevoked. The key
            # goes to the owner-only credential file, never into a harness
            # configuration file.
            with credential_lock():
                try:
                    current = load_credentials()
                except CredentialError:
                    current = None
                if current is not None and current.issuer:
                    print(refusal.format(issuer=current.issuer), file=sys.stderr)
                    return 1
                write_credentials(
                    credentials=StoredCredentials(
                        version=2,
                        api_url=url,
                        key=SecretStr(normalize_key(target_key))
                        if target_key
                        else None,
                    )
                )
            print(
                "[✓] Stored the engine URL (and key) in the owner-only credential file"
            )
    else:
        print("Backend: Remember Cloud")
        if target_key:
            print(
                "error: --api-key is for --self-hosted; for Remember Cloud run "
                "`remember login`, or set REMEMBER_API_KEY in the harness environment",
                file=sys.stderr,
            )
            return 1
        if target_url:
            env["REMEMBER_API_URL"] = target_url
            print(f"  Engine URL (explicit override): {target_url}")
        elif stored is None or not stored.issuer:
            print(
                "  [!] Not signed in. Run `remember login`; your agents connect "
                "once it completes."
            )

    print("Configuring AI coding harnesses for Remember:")
    print(f"  Launcher command: {launcher_cmd} {' '.join(launcher_args)}")
    if dry_run:
        print("  Mode: DRY RUN (no files will be written)")
    print()

    configured_any = False

    if target_agent == "cursor":
        ok = configure_cursor(
            cwd=target_dir,
            launcher_cmd=launcher_cmd,
            launcher_args=launcher_args,
            env=env if env else None,
            dry_run=dry_run,
        )
        if not ok:
            print("error: Failed to configure Cursor harness.", file=sys.stderr)
            return 1
        configured_any = True
    elif target_agent == "agy":
        ok = configure_antigravity(
            cwd=target_dir,
            launcher_cmd=launcher_cmd,
            launcher_args=launcher_args,
            env=env if env else None,
            dry_run=dry_run,
        )
        if not ok:
            print("error: Failed to configure Antigravity harness.", file=sys.stderr)
            return 1
        configured_any = True
    elif target_agent == "codex":
        ok = configure_codex(
            cwd=target_dir,
            launcher_cmd=launcher_cmd,
            launcher_args=launcher_args,
            env=env if env else None,
            dry_run=dry_run,
        )
        if not ok:
            print("error: Failed to configure OpenAI Codex harness.", file=sys.stderr)
            return 1
        configured_any = True
    elif target_agent == "claude":
        claude_cli_installed = bool(shutil.which("claude"))
        desktop_config = get_claude_desktop_config_path()
        desktop_installed = (
            desktop_config.exists()
            or desktop_config.parent.is_dir()
            or (sys.platform == "darwin" and Path("/Applications/Claude.app").exists())
        )

        if not claude_cli_installed and not desktop_installed:
            print(
                "error: Neither Claude Code CLI ('claude') nor Claude Desktop was detected.\n"
                "To install Claude Code CLI: npm install -g @anthropic-ai/claude-code\n"
                "To install Claude Desktop:  https://claude.ai/download",
                file=sys.stderr,
            )
            return 1

        code_failed = False
        if claude_cli_installed:
            ok_code = configure_claude_code(
                launcher_cmd=launcher_cmd,
                launcher_args=launcher_args,
                env=env if env else None,
                dry_run=dry_run,
            )
            if not ok_code:
                code_failed = True
            else:
                configured_any = True

        desktop_failed = False
        if desktop_installed:
            try:
                ok_desktop = configure_claude_desktop(
                    launcher_cmd=launcher_cmd,
                    launcher_args=launcher_args,
                    env=env if env else None,
                    dry_run=dry_run,
                )
                if not ok_desktop:
                    desktop_failed = True
                else:
                    configured_any = True
            except RuntimeError as exc:
                print(f"error: {exc}", file=sys.stderr)
                desktop_failed = True

        if code_failed or desktop_failed or not configured_any:
            failed_targets = []
            if code_failed:
                failed_targets.append("Claude Code CLI")
            if desktop_failed:
                failed_targets.append("Claude Desktop")
            print(
                f"error: Failed to configure Claude harness ({', '.join(failed_targets)} failed).",
                file=sys.stderr,
            )
            return 1
    else:
        # target_agent == "all": auto-detect existing harnesses
        if (target_dir / ".cursor").is_dir():
            try:
                configure_cursor(
                    cwd=target_dir,
                    launcher_cmd=launcher_cmd,
                    launcher_args=launcher_args,
                    env=env if env else None,
                    dry_run=dry_run,
                )
                configured_any = True
            except RuntimeError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1

        if (target_dir / ".agents").is_dir():
            try:
                configure_antigravity(
                    cwd=target_dir,
                    launcher_cmd=launcher_cmd,
                    launcher_args=launcher_args,
                    env=env if env else None,
                    dry_run=dry_run,
                )
                configured_any = True
            except RuntimeError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1

        if (
            (target_dir / ".codex").is_dir()
            or bool(shutil.which("codex"))
            or (Path.home() / ".codex").is_dir()
        ):
            try:
                configure_codex(
                    cwd=target_dir,
                    launcher_cmd=launcher_cmd,
                    launcher_args=launcher_args,
                    env=env if env else None,
                    dry_run=dry_run,
                )
                configured_any = True
            except RuntimeError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1

        if shutil.which("claude"):
            ok = configure_claude_code(
                launcher_cmd=launcher_cmd,
                launcher_args=launcher_args,
                env=env if env else None,
                dry_run=dry_run,
            )
            if ok:
                configured_any = True

        desktop_config = get_claude_desktop_config_path()
        if desktop_config.exists() or (
            sys.platform == "darwin" and Path("/Applications/Claude.app").exists()
        ):
            try:
                configure_claude_desktop(
                    launcher_cmd=launcher_cmd,
                    launcher_args=launcher_args,
                    env=env if env else None,
                    dry_run=dry_run,
                )
                configured_any = True
            except RuntimeError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1

        if not configured_any:
            # Default fallback to Cursor and Antigravity
            configure_cursor(
                cwd=target_dir,
                launcher_cmd=launcher_cmd,
                launcher_args=launcher_args,
                env=env if env else None,
                dry_run=dry_run,
            )
            configure_antigravity(
                cwd=target_dir,
                launcher_cmd=launcher_cmd,
                launcher_args=launcher_args,
                env=env if env else None,
                dry_run=dry_run,
            )

    print(
        "\nTip: Run `uv tool install remember` to install the remember CLI permanently to your shell PATH."
    )
    return 0
