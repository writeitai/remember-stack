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
available MCP tools (`fact_context`, `answer_context`, `testimony_context`, `resolve_entity`, `query_sql`).

- Use `fact_context` or `answer_context` to retrieve attested facts and evidence.
- Use `query_sql` to run sandboxed SQL against `facts_current` or `graph_edges_current`.
- Check past decisions and bitemporal validity before asserting assumptions.
- Never guess historical rationale when it is recorded in Remember.
"""

ANTIGRAVITY_SKILL_CONTENT = """---
name: remember
description: Open bitemporal memory infrastructure for AI agents. Use when looking up past decisions, system architecture, factual evidence, or attested codebase knowledge.
---

# Remember Bitemporal Memory Skill

You have access to Remember, an open bitemporal memory infrastructure for AI agents.
Use the Remember MCP tools (`fact_context`, `answer_context`, `testimony_context`, `resolve_entity`, `query_sql`, `describe_query_space`)
to query past system decisions, architectural records, and entity-relationship knowledge graphs.

## Core Guidelines
1. Query attested facts using `fact_context` or `query_sql`.
2. Inspect bitemporal validity ranges (`valid_at`, `believed_at`) when examining changes.
3. Trust attested records over unverified guesswork.
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
    target_url: str | None = getattr(args, "url", None)
    target_token: str | None = getattr(args, "token", None)

    # Interactive choice if neither is specified on a TTY
    if target_env is None and not is_cloud and not is_self_hosted:
        if sys.stdin.isatty() and not dry_run:
            print("Choose your Remember backend:")
            print("  1) Remember Cloud [default]")
            print("  2) Self-Hosted Engine (http://localhost:8000)")
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

    from remember.credentials import load_credentials

    stored = load_credentials()

    env: dict[str, str] = {}
    if is_self_hosted:
        url = target_url or "http://localhost:8000"
        env["REMEMBER_DATA_PLANE_URL"] = url
        print("Backend: Self-Hosted Engine")
        print(f"  Data Plane URL: {url}")
    else:
        print("Backend: Remember Cloud")
        if target_url:
            env["REMEMBER_DATA_PLANE_URL"] = target_url
            print(f"  Data Plane URL (explicit override): {target_url}")
        else:
            dp_url = stored.active_data_plane_url if stored else None
            if dp_url:
                print(f"  Active Project Data Plane: {dp_url}")
                print(
                    "  Harness configuration: Using ambient credentials (dynamically follows `remember switch`)"
                )
            else:
                print(
                    "  [!] Notice: No Remember Cloud data-plane URL or credentials configured.\n"
                    "      Run 'remember login' to authenticate and bind your active project,\n"
                    "      or pass --url https://<project>.dp.remember.dev with --token.\n"
                    "      (Your AI coding agents will connect once 'remember login' completes.)"
                )

    if target_token or target_env == "self_hosted":
        # Securely persist token/endpoint to user's credential file (~/.config/remember/credentials.json, mode 0600)
        # NEVER leak bearer secrets into repository configuration files (D92/D108)!
        if (
            is_cloud
            and not target_url
            and not (stored and stored.active_data_plane_url)
        ):
            print(
                "error: When passing an explicit token with --token for Remember Cloud, "
                "you must also pass --url https://<project-id>.dp.remember.dev",
                file=sys.stderr,
            )
            return 1

        from pydantic import SecretStr

        from remember.credentials import CredentialFile
        from remember.credentials import ProjectCredentials
        from remember.credentials import write_credentials

        effective_url: str = (
            target_url
            or (
                stored.active_data_plane_url
                if stored and stored.active_data_plane_url
                else None
            )
            or "http://localhost:8000"
        )
        token_str: str = target_token or ""
        if not dry_run:
            is_local = (
                target_env == "self_hosted"
                or "localhost" in effective_url
                or "127.0.0.1" in effective_url
            )
            token_host = effective_url if is_local else "https://api.remember.dev"
            if stored is not None:
                updated_projects = (
                    dict(stored.projects) if stored.projects is not None else {}
                )
                active_id = (
                    "self_hosted"
                    if target_env == "self_hosted"
                    else (stored.active_project_id or "default")
                )
                old_p = updated_projects.get(active_id)
                old_token = (
                    old_p.data_plane_token.get_secret_value()
                    if (old_p and old_p.data_plane_token)
                    else (
                        stored.access_token.get_secret_value()
                        if stored.access_token
                        else None
                    )
                )
                from uuid import uuid4

                old_token_id = old_p.token_id if old_p else None
                # Only borrow stored.token_id if proven to describe the same active project and token
                if (
                    old_token_id is None
                    and stored.active_project_id == active_id
                    and stored.access_token
                    and old_token
                    and stored.access_token.get_secret_value() == old_token
                ):
                    old_token_id = stored.token_id
                old_host = (
                    (old_p.token_host if old_p else None)
                    or stored.token_host
                    or token_host
                )
                if (
                    old_token
                    and target_token
                    and old_token != target_token
                    and not is_local
                    and ("api.remember.dev" in old_host or "remember.dev" in old_host)
                ):
                    from remember.credentials import append_pending_revocation
                    from remember.credentials import PendingRevocation

                    append_pending_revocation(
                        pending=PendingRevocation(
                            version=1,
                            token_host=old_host,
                            access_token=SecretStr(old_token),
                            token_id=old_token_id or uuid4(),
                        )
                    )

                new_token_id = uuid4()
                proj_name = (
                    "self_hosted"
                    if target_env == "self_hosted"
                    else (old_p.name if old_p else "default")
                )
                updated_projects[active_id] = ProjectCredentials(
                    name=proj_name,
                    data_plane_url=effective_url,
                    data_plane_token=SecretStr(token_str),
                    token_host=token_host,
                    token_id=new_token_id,
                )

                new_stored = stored.model_copy(
                    update={
                        "projects": updated_projects,
                        "active_project_id": active_id,
                        "api_url": effective_url,
                        "token_host": token_host,
                        "access_token": SecretStr(token_str),
                        "token_id": new_token_id,
                    }
                )
                write_credentials(credential=new_stored)
            else:
                from uuid import uuid4

                new_token_id = uuid4()
                proj_id = "self_hosted" if target_env == "self_hosted" else "default"
                cred = CredentialFile(
                    version=1,
                    api_url=effective_url,
                    token_host=token_host,
                    access_token=SecretStr(token_str),
                    token_id=new_token_id,
                    active_project_id=proj_id,
                    projects={
                        proj_id: ProjectCredentials(
                            name=proj_id,
                            data_plane_url=effective_url,
                            data_plane_token=SecretStr(token_str),
                            token_host=token_host,
                            token_id=new_token_id,
                        )
                    },
                )
                write_credentials(credential=cred)
            if target_token:
                print(
                    "[✓] Stored access token securely in ~/.config/remember/credentials.json (mode 0600)"
                )
            elif target_env == "self_hosted":
                print(
                    f"[✓] Configured self-hosted endpoint ({effective_url}) in ~/.config/remember/credentials.json (mode 0600)"
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
