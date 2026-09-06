"""The ``remember`` CLI: a dependency-light client plus optional local admin commands.

Query, ingest, connector management, and MCP all talk to the deployment HTTP
API. ``remember review``, ``remember budget``, and ``remember ops`` import the server extra
and connect to the spine.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from importlib import import_module
import json
from pathlib import Path
import sys
from typing import Any
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from uuid import UUID

import httpx
from pydantic import JsonValue
from pydantic import SecretStr

from remember import __version__
from remember.client import MemoryApiError
from remember.client import MemoryClient
from remember.credentials import CredentialError
from remember.models import ConnectorCreate
from remember.remote_mcp import RemoteOperationMcpServer
from remember.remote_mcp import serve_mcp_stdio

if TYPE_CHECKING:
    from remember.credentials import CredentialFile

_MERGE_VERDICTS = ("merge", "not_merge")
_TRIAGE_VERDICTS = ("restore_support", "invalidate_fact", "uncertain")


def main(argv: list[str] | None = None) -> int:
    """The ``remember`` entry point; returns the process exit code."""
    from pydantic import ValidationError

    from remember.credentials import CliClientEnv

    parser: argparse.ArgumentParser | None = None
    try:
        _warn_if_revocation_outstanding()
        effective_argv = list(sys.argv[1:] if argv is None else argv)
        if effective_argv:
            subcmd = effective_argv[0]
            if subcmd == "ops":
                env = CliClientEnv.model_validate({})
                if not env.internal_ops:
                    print(
                        "error: 'remember ops' is confined to internal container environments. "
                        "For developer operations, use 'remember operations list|run'. See https://remember.dev/docs",
                        file=sys.stderr,
                    )
                    return 1
            if subcmd == "query":
                known_query_subcmds = {
                    "text",
                    "sql",
                    "explain-sql",
                    "space",
                    "search-space",
                    "list-saved",
                    "describe-saved",
                    "run-saved",
                }
                has_subcmd = any(
                    arg in known_query_subcmds for arg in effective_argv[1:]
                )
                has_help = any(arg in ("-h", "--help") for arg in effective_argv[1:])
                if not has_subcmd and not has_help:
                    effective_argv.insert(1, "text")

        env = CliClientEnv.model_validate({})
        has_server_subcmd = bool(effective_argv) and effective_argv[0] in (
            "review",
            "budget",
            "ops",
        )
        parser = _build_parser(
            include_internal_ops=env.internal_ops or has_server_subcmd
        )
        args = parser.parse_args(effective_argv)

        if args.command == "review":
            return _run_review(args)
        if args.command == "budget":
            return _run_budget(args)
        if args.command == "setup":
            return _run_setup(args)
        if args.command == "doctor":
            return _run_doctor(args)
        if args.command == "whoami":
            return _run_whoami(args)
        if args.command in ("balance", "billing"):
            return _run_balance(args)
        if args.command == "projects":
            return _run_projects(args)
        if args.command == "switch":
            return _run_switch(args)
        if args.command == "members":
            return _run_members(args)
        if args.command == "ops":
            return _run_ops(args)
        if args.command == "operations":
            return _run_operations(args)
        if args.command == "query":
            return _run_query(args)
        if args.command == "ingest":
            return _run_ingest(args)
        if args.command == "connectors":
            return _run_connectors(args)
        if args.command == "mcp":
            return _run_mcp(args)
        if args.command == "login":
            return _run_login(args)
        if args.command == "logout":
            return _run_logout(args)
    except ValidationError as error:
        errors = error.errors()
        if errors:
            first = errors[0]
            loc = ".".join(str(x) for x in first.get("loc", []))
            msg = first.get("msg", str(error))
            print(
                f"error: Invalid environment configuration ({loc}): {msg}",
                file=sys.stderr,
            )
        else:
            print(f"error: Invalid environment configuration: {error}", file=sys.stderr)
        return 1
    except (
        MemoryApiError,
        CredentialError,
        httpx.InvalidURL,
        httpx.RequestError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if parser is not None:
        parser.print_help()
    return 2


def _get_active_endpoint(args: argparse.Namespace) -> str:
    """Return the currently configured or stored data-plane endpoint URL."""
    from remember.credentials import CliClientEnv
    from remember.credentials import load_credentials

    try:
        stored = load_credentials()
    except CredentialError:
        stored = None

    env = CliClientEnv.model_validate({})
    api_url = (
        getattr(args, "api_url", None)
        or env.api_url
        or (stored.active_data_plane_url if stored else None)
    )
    return api_url or "http://localhost:8000"


def _self_hosted_notice(args: argparse.Namespace) -> str:
    """Format an informative notice explaining self-hosted engine boundaries."""
    url = _get_active_endpoint(args)
    return (
        f"Note: You are connected to a self-hosted engine ({url}). "
        "Projects, team members, and billing are cloud-managed services on remember.dev."
    )


def _is_local_host(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")
        or host.endswith(".local")
        or (parsed.port == 8000 and not host.endswith("remember.dev"))
    )


def _is_cloud_host(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host == "remember.dev" or host.endswith(".remember.dev")


def _is_self_hosted(args: argparse.Namespace) -> bool:
    """True when explicitly flagged or pointing at a self-hosted instance without cloud credentials."""
    if getattr(args, "self_hosted", False):
        return True
    from remember.credentials import CliClientEnv
    from remember.credentials import load_credentials

    try:
        stored = load_credentials()
    except CredentialError:
        stored = None

    env = CliClientEnv.model_validate({})
    explicit_target = getattr(args, "api_url", None) or env.api_url
    if explicit_target:
        return _is_local_host(explicit_target)

    if stored is not None:
        if stored.control_plane is not None:
            return False
        active_url = stored.active_data_plane_url or stored.api_url
        if _is_local_host(active_url):
            return True
        if _is_cloud_host(active_url) or _is_cloud_host(stored.token_host):
            return False
        if (
            active_url
            and stored.token_host
            and active_url.rstrip("/") == stored.token_host.rstrip("/")
        ):
            return True

    return False


def _run_setup(args: argparse.Namespace) -> int:
    """Bootstrap AI coding agent harnesses (D108)."""
    from remember.setup import run_setup

    try:
        return run_setup(args)
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _run_doctor(args: argparse.Namespace) -> int:
    """Verify installation, credentials, network connectivity, and harnesses."""
    import shutil
    import time

    from remember.credentials import CliClientEnv
    from remember.credentials import credentials_path
    from remember.credentials import load_credentials
    from remember.setup import get_claude_desktop_config_path

    print(f"Remember Doctor (v{__version__})\n")
    all_ok = True

    # 1. Launcher resolution
    rem_cand = shutil.which("remember")
    uvx_cand = shutil.which("uvx")
    if rem_cand:
        resolved = Path(rem_cand).resolve().as_posix()
        print(f"[✓] 'remember' binary found: {resolved}")
    elif uvx_cand:
        resolved = Path(uvx_cand).resolve().as_posix()
        print(f"[✓] 'uvx' runner found: {resolved} (remember available via uvx)")
    else:
        print(
            "[!] Neither 'remember' nor 'uvx' found on PATH. Run 'uv tool install remember'."
        )
        all_ok = False

    # 2. Stored credentials
    cred_file = credentials_path()
    stored = None
    try:
        stored = load_credentials()
    except Exception as err:
        print(f"[!] Credential file unreadable at {cred_file}: {err}")
        all_ok = False

    active_token: str | None = None
    active_proj: str | None = None
    if stored is not None:
        import os

        perms_ok = True
        if os.name == "posix" and cred_file.is_file():
            mode = cred_file.stat().st_mode & 0o777
            if mode != 0o600:
                print(f"[!] Permissions on {cred_file} are {oct(mode)} (expected 0600)")
                perms_ok = False
        if perms_ok:
            print(f"[✓] Configuration: {cred_file} (0600)")
        else:
            print(f"[!] Configuration: {cred_file} (insecure file mode)")

        if stored.control_plane and stored.control_plane.email:
            print(
                f"    Identity: {stored.control_plane.email} (org: {stored.control_plane.org_id})"
            )
        active_proj = stored.active_project_id or (
            str(stored.deployment_id) if stored.deployment_id else "default"
        )
        print(f"    Active Project: {active_proj}")
        active_token = stored.get_active_project_token()
        if stored.expires_at:
            print(f"    Expires: {stored.expires_at.isoformat()}")
    else:
        print(
            f"[-] No stored credentials at {cred_file}. Run 'remember login' for cloud access."
        )

    # 3. Data plane connectivity & authentication
    explicit_token = getattr(args, "token", None) or getattr(
        args, "api_authorization", None
    )
    api_url = getattr(args, "api_url", None)
    if not api_url:
        api_url = CliClientEnv.model_validate({}).api_url
    if not api_url and stored:
        api_url = stored.active_data_plane_url or stored.api_url
    if not api_url:
        api_url = "http://127.0.0.1:8000"

    # Strictly verify scheme and origin before attaching ambient data-plane token
    token_for_request: str | None = None
    if explicit_token:
        token_for_request = explicit_token
    elif active_token and stored:
        stored_url = stored.active_data_plane_url or stored.api_url
        if stored_url:
            target_parsed = urlparse(api_url)
            stored_parsed = urlparse(stored_url)
            if (
                target_parsed.scheme == stored_parsed.scheme
                and target_parsed.netloc == stored_parsed.netloc
            ):
                token_for_request = active_token

    print(f"Checking data plane at {api_url}...")
    headers = (
        {"Authorization": f"Bearer {token_for_request}"} if token_for_request else {}
    )
    try:
        start_t = time.perf_counter()
        with httpx.Client(base_url=api_url, timeout=5.0) as client:
            resp = client.get("/deployment", headers=headers)
            elapsed = int((time.perf_counter() - start_t) * 1000)
            if resp.status_code == 200:
                print(f"[✓] Data plane reachable ({elapsed}ms, HTTP 200 OK)")
                if token_for_request:
                    print(f"[✓] Authentication: Valid session (project: {active_proj})")
                elif active_token:
                    print(
                        "[-] Authentication: Ambient token omitted (target origin does not match stored project URL)"
                    )
            elif resp.status_code in (401, 403):
                print(
                    f"[!] Authentication failed: HTTP {resp.status_code} (token invalid or missing scope)"
                )
                all_ok = False
            else:
                resp_health = client.get("/healthz")
                if resp_health.status_code == 200:
                    print(
                        f"[✓] Data plane reachable ({elapsed}ms, unauthenticated healthz OK)"
                    )
                else:
                    print(f"[!] Data plane answered HTTP {resp.status_code}")
                    all_ok = False
    except Exception as err:
        print(f"[!] Data plane unreachable: {err}")
        if "127.0.0.1" in api_url or "localhost" in api_url:
            print(
                "    (Self-hosted engine is not running locally. Start it with docker compose up)"
            )
        all_ok = False

    # 4. Harness configurations & syntax validation
    import json
    import os
    import tomllib

    print("\nCoding Agent Harnesses:")

    def _is_executable(cmd: str | None) -> bool:
        if not cmd:
            return False
        return bool(shutil.which(cmd)) or (
            Path(cmd).is_file() and os.access(cmd, os.X_OK)
        )

    cursor_mcp = Path.cwd() / ".cursor" / "mcp.json"
    if cursor_mcp.is_file():
        try:
            cdata = json.loads(cursor_mcp.read_text(encoding="utf-8"))
            if "mcpServers" in cdata and "remember" in cdata["mcpServers"]:
                entry = cdata["mcpServers"]["remember"]
                cmd = entry.get("command") if isinstance(entry, dict) else None
                if _is_executable(cmd):
                    print(
                        f"[✓] Cursor: configured and launcher verified ({cursor_mcp})"
                    )
                else:
                    print(
                        f"[!] Cursor: launcher command '{cmd}' not found or not executable ({cursor_mcp})"
                    )
                    all_ok = False
            else:
                print(
                    f"[!] Cursor: valid JSON but missing 'remember' MCP server ({cursor_mcp})"
                )
                all_ok = False
        except Exception as err:
            print(f"[!] Cursor: malformed JSON in {cursor_mcp}: {err}")
            all_ok = False
    else:
        print(
            "[-] Cursor: not configured in this directory (run 'remember setup --agent cursor')"
        )

    agy_mcp = Path.cwd() / ".agents" / "mcp_config.json"
    if agy_mcp.is_file():
        try:
            adata = json.loads(agy_mcp.read_text(encoding="utf-8"))
            if "mcpServers" in adata and "remember" in adata["mcpServers"]:
                entry = adata["mcpServers"]["remember"]
                cmd = entry.get("command") if isinstance(entry, dict) else None
                if _is_executable(cmd):
                    print(
                        f"[✓] Antigravity: configured and launcher verified ({agy_mcp})"
                    )
                else:
                    print(
                        f"[!] Antigravity: launcher command '{cmd}' not found or not executable ({agy_mcp})"
                    )
                    all_ok = False
            else:
                print(
                    f"[!] Antigravity: valid JSON but missing 'remember' MCP server ({agy_mcp})"
                )
                all_ok = False
        except Exception as err:
            print(f"[!] Antigravity: malformed JSON in {agy_mcp}: {err}")
            all_ok = False
    else:
        print(
            "[-] Antigravity: not configured in this directory (run 'remember setup --agent agy')"
        )

    codex_cfg = Path.cwd() / ".codex" / "config.toml"
    if codex_cfg.is_file():
        try:
            tdata = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
            if "mcp_servers" in tdata and "remember" in tdata["mcp_servers"]:
                entry = tdata["mcp_servers"]["remember"]
                cmd = entry.get("command") if isinstance(entry, dict) else None
                if _is_executable(cmd):
                    print(f"[✓] Codex: configured and launcher verified ({codex_cfg})")
                else:
                    print(
                        f"[!] Codex: launcher command '{cmd}' not found or not executable ({codex_cfg})"
                    )
                    all_ok = False
            else:
                print(
                    f"[!] Codex: valid TOML but missing [mcp_servers.remember] ({codex_cfg})"
                )
                all_ok = False
        except Exception as err:
            print(f"[!] Codex: malformed TOML in {codex_cfg}: {err}")
            all_ok = False
    else:
        print("[-] Codex: not configured in this directory")

    claude_cfg = get_claude_desktop_config_path()
    if claude_cfg.is_file():
        try:
            cldata = json.loads(claude_cfg.read_text(encoding="utf-8"))
            if "mcpServers" in cldata and "remember" in cldata["mcpServers"]:
                entry = cldata["mcpServers"]["remember"]
                cmd = entry.get("command") if isinstance(entry, dict) else None
                if _is_executable(cmd):
                    print(
                        f"[✓] Claude Desktop: configured and launcher verified ({claude_cfg})"
                    )
                else:
                    print(
                        f"[!] Claude Desktop: launcher command '{cmd}' not found or not executable ({claude_cfg})"
                    )
                    all_ok = False
            else:
                print(f"[!] Claude Desktop: missing 'remember' server ({claude_cfg})")
                all_ok = False
        except Exception as err:
            print(f"[!] Claude Desktop: malformed JSON in {claude_cfg}: {err}")
            all_ok = False
    else:
        print(f"[-] Claude Desktop: not detected at {claude_cfg}")

    print()
    return 0 if all_ok else 1


def _run_whoami(args: argparse.Namespace) -> int:
    """Display authenticated identity, organization, and current project (D108)."""
    if _is_self_hosted(args):
        endpoint = _get_active_endpoint(args)
        print("Identity: self-hosted (local)")
        print(f"Endpoint: {endpoint}")
        print(_self_hosted_notice(args))
        return 0

    from remember.credentials import load_credentials

    stored = load_credentials()
    if stored is None:
        print(
            "Not logged in. Run 'remember login' to authenticate with Remember Cloud."
        )
        return 1

    if stored.control_plane is not None:
        print(f"User ID: {stored.control_plane.user_id or 'unknown'}")
        if stored.control_plane.email:
            print(f"Email: {stored.control_plane.email}")
        print(f"Organization ID: {stored.control_plane.org_id or 'unknown'}")
    elif stored.org_id is not None:
        print(f"Organization ID: {stored.org_id}")

    active_proj = stored.active_project_id or (
        str(stored.deployment_id) if stored.deployment_id else "default"
    )
    print(f"Active Project: {active_proj}")
    endpoint = (
        stored.active_data_plane_url or stored.api_url or "https://api.remember.dev"
    )
    print(f"Data Plane: {endpoint}")
    return 0


def _run_balance(args: argparse.Namespace) -> int:
    """Fetch current credit balance and subscription status (D108)."""
    if _is_self_hosted(args):
        print(
            f"error: Credit balance and subscription billing are cloud-managed services on remember.dev.\n{_self_hosted_notice(args)}",
            file=sys.stderr,
        )
        return 1

    from remember.credentials import load_credentials

    stored = load_credentials()
    if (
        stored is None
        or stored.control_plane is None
        or stored.control_plane.access_token is None
    ):
        print(
            "error: Balance inspection requires an organization control-plane credential.\n"
            "View real-time credit balance and manage subscriptions in the web console: https://remember.dev/app/billing",
            file=sys.stderr,
        )
        return 1

    control_plane_url = stored.control_plane.url or "https://api.remember.dev"
    token = stored.control_plane.access_token.get_secret_value()
    org_id = (
        stored.control_plane.org_id
        if stored.control_plane and stored.control_plane.org_id
        else stored.org_id
    )
    endpoint = f"/v1/orgs/{org_id}/billing/status" if org_id else "/v1/billing/balance"
    try:
        with httpx.Client(base_url=control_plane_url, timeout=10.0) as client:
            resp = client.get(endpoint, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                data = resp.json()
                balance = data.get("balance_credits", data.get("balance", "€0.00"))
                status = data.get("billing_state", data.get("status", "Active"))
                print(f"Current balance: €{balance} [{status}]")
                return 0
            else:
                print(
                    f"error: Control plane returned HTTP {resp.status_code}: {resp.text}",
                    file=sys.stderr,
                )
                return 1
    except Exception as exc:
        print(
            f"error: Failed to reach control plane at {control_plane_url}: {exc}",
            file=sys.stderr,
        )
        return 1


def _run_projects(args: argparse.Namespace) -> int:
    """Manage tenant projects in the active organization (D108)."""
    if _is_self_hosted(args):
        if args.projects_command == "list":
            print(f"{'PROJECT ID':<36} {'NAME':<20} {'STATUS':<10} {'ACTIVE'}")
            print(f"{'local':<36} {'self-hosted':<20} {'ready':<10} *")
            print()
            print(
                "Note: Self-hosted engine operates in a single local project namespace.\n"
                "Connect to Remember Cloud for multi-project tenant management: remember login"
            )
            return 0
        if args.projects_command == "create":
            print(
                f"error: Multi-tenant project provisioning is not supported on a self-hosted engine.\n{_self_hosted_notice(args)}",
                file=sys.stderr,
            )
            return 1
        print(
            f"error: Projects management is not supported in self-hosted mode.\n{_self_hosted_notice(args)}",
            file=sys.stderr,
        )
        return 1

    from remember.credentials import load_credentials

    stored = load_credentials()

    if args.projects_command == "list":
        if stored is None:
            print(
                "error: Not authenticated. Run 'remember login' to authenticate with Remember Cloud.",
                file=sys.stderr,
            )
            return 1
        print(f"{'PROJECT ID':<36} {'NAME':<20} {'STATUS':<10} {'ACTIVE'}")

        # D108 / D56: If control plane credentials exist, query live projects from control plane
        if stored.control_plane and stored.control_plane.access_token:
            cp_url = stored.control_plane.url or "https://api.remember.dev"
            token = stored.control_plane.access_token.get_secret_value()
            org_id = stored.control_plane.org_id or stored.org_id
            endpoints = [
                f"/v1/orgs/{org_id}/deployments" if org_id else "/v1/deployments",
                f"/v1/orgs/{org_id}/projects" if org_id else "/v1/projects",
                "/v1/projects",
            ]
            for endpoint in endpoints:
                try:
                    with httpx.Client(base_url=cp_url, timeout=5.0) as client:
                        resp = client.get(
                            endpoint, headers={"Authorization": f"Bearer {token}"}
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            items = (
                                data.get("deployments")
                                or data.get("projects")
                                or (data if isinstance(data, list) else [])
                            )
                            if items:
                                for item in items:
                                    pid = str(
                                        item.get("id")
                                        or item.get("deployment_id")
                                        or item.get("project_id")
                                        or ""
                                    )
                                    name = str(
                                        item.get("name") or item.get("label") or pid[:8]
                                    )
                                    state = str(
                                        item.get("state")
                                        or item.get("status")
                                        or "ready"
                                    )
                                    is_act = (
                                        "*" if pid == stored.active_project_id else " "
                                    )
                                    print(f"{pid:<36} {name:<20} {state:<10} {is_act}")
                                return 0
                except Exception:
                    pass

        if stored.projects:
            for pid, p in stored.projects.items():
                active_marker = "*" if pid == stored.active_project_id else " "
                print(f"{pid:<36} {p.name:<20} {'ready':<10} {active_marker}")
        elif stored.deployment_id:
            active_marker = "*"
            name = stored.label or "default"
            print(
                f"{str(stored.deployment_id):<36} {name:<20} {'ready':<10} {active_marker}"
            )
        if not (stored.control_plane and stored.control_plane.access_token):
            print()
            print(
                "Note: Showing locally cached projects. For live organization discovery, run: remember login --audience control"
            )
        return 0

    if args.projects_command == "create":
        name = args.name
        print(
            f"error: Project pods cannot be provisioned via the CLI data-plane session.\n"
            f"Administrative provisioning and pod sizing take place in the web console:\n"
            f"  https://remember.dev/app/projects\n\n"
            f"After creating project '{name}' in the console, authenticate and link it locally:\n"
            f"  remember login",
            file=sys.stderr,
        )
        return 1

    return 0


def _run_switch(args: argparse.Namespace) -> int:
    """Switch the default active project in local configuration (D108)."""
    if _is_self_hosted(args):
        print(
            f"error: Project switching is not applicable to a self-hosted engine.\n{_self_hosted_notice(args)}",
            file=sys.stderr,
        )
        return 1

    from remember.credentials import load_credentials
    from remember.credentials import write_credentials

    stored = load_credentials()
    if stored is None:
        print(
            "Not logged in. Run 'remember login' to authenticate with Remember Cloud."
        )
        return 1

    target = args.project
    found_id = None
    if stored.projects:
        for pid, p in stored.projects.items():
            if pid == target or p.name == target:
                found_id = pid
                break
    if found_id is None:
        if target == str(stored.deployment_id) or target == stored.label:
            found_id = str(stored.deployment_id)
    if found_id is None:
        print(
            f"error: project '{target}' not found. Run 'remember projects list' to view available projects.",
            file=sys.stderr,
        )
        return 1

    updates: dict[str, object] = {"active_project_id": found_id}
    if stored.projects and found_id in stored.projects:
        p = stored.projects[found_id]
        updates["api_url"] = p.data_plane_url
        updates["access_token"] = p.data_plane_token
        updates["label"] = p.name
        if p.deployment_id is not None:
            updates["deployment_id"] = p.deployment_id
        else:
            try:
                updates["deployment_id"] = UUID(found_id)
            except (ValueError, AttributeError):
                pass
        if p.token_host is not None:
            updates["token_host"] = p.token_host
        if p.token_id is not None:
            updates["token_id"] = p.token_id
        else:
            from uuid import uuid4

            updates["token_id"] = uuid4()
        updates["expires_at"] = p.expires_at

    new_stored = stored.model_copy(update=updates)
    write_credentials(credential=new_stored)
    print(f"[✓] Switched active project to '{target}' ({found_id})")
    return 0


def _run_members(args: argparse.Namespace) -> int:
    """Manage team organization seats (D108)."""
    if _is_self_hosted(args):
        print(
            f"error: Team member and seat administration are cloud-managed services on remember.dev.\n{_self_hosted_notice(args)}",
            file=sys.stderr,
        )
        return 1

    if args.members_command == "list":
        print(
            "error: Organization team members and access roles require web console administration.\n"
            "Manage team members in the web console: https://remember.dev/app/team",
            file=sys.stderr,
        )
        return 1

    if args.members_command == "invite":
        raw_role = getattr(args, "role", "member") or "member"
        role_name = raw_role.lower()
        print(
            f"error: Team seat invitations cannot be dispatched via the CLI data-plane session.\n"
            f"Please visit the web console to invite {args.email} ({role_name}):\n"
            f"  https://remember.dev/app/team",
            file=sys.stderr,
        )
        return 1

    return 0


_MERGE_VERDICTS = ("merge", "not_merge")
_TRIAGE_VERDICTS = ("restore_support", "invalidate_fact", "uncertain")


def _list_reviews(*, queue: Any, deployment_id: UUID) -> int:
    """Print one JSON record per open item in impact-ranked order."""
    for item in queue.list_open(deployment_id=deployment_id):
        print(
            json.dumps(
                {
                    "review_id": str(item.review_id),
                    "kind": item.item_kind,
                    "expected_impact": item.expected_impact,
                    "blast_radius": item.blast_radius,
                    "status": item.status,
                    "candidate": item.candidate,
                },
                default=str,
            )
        )
    return 0


def _decide_review(
    *,
    queue: Any,
    deployment_id: UUID,
    review_id: UUID,
    verdict: str,
    reviewer: str,
    note: str | None,
) -> int:
    """Apply one verdict; the verdict picks the decision path by its name."""
    try:
        if verdict in _MERGE_VERDICTS:
            events = queue.decide_merge(
                deployment_id=deployment_id,
                review_id=review_id,
                verdict=verdict,
                reviewer=reviewer,
                note=note,
            )
            print(
                json.dumps(
                    {"verdict": verdict, "merge_events": [str(e) for e in events]}
                )
            )
        else:
            queue.decide_support_withdrawn(
                deployment_id=deployment_id,
                review_id=review_id,
                verdict=verdict,
                reviewer=reviewer,
                note=note,
            )
            print(json.dumps({"verdict": verdict}))
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _run_review(args: argparse.Namespace) -> int:
    """Compose the optional local ReviewQueue over the spine or show retirement notice."""
    try:
        from sqlalchemy import create_engine

        from rememberstack.spine.settings import load_database_settings
        from rememberstack.spine.surface_cost import open_surface_scope
        from rememberstack.spine.surface_cost import SurfaceCostKind

        db_settings = load_database_settings()
        review_queue_builder = import_module(
            "rememberstack.profiles.selfhost"
        ).build_selfhost_review_queue
    except Exception:
        print(
            "error: 'remember review' is retired. The engine adjudicates contradictions "
            "autonomously without human review queues. See https://remember.dev/docs/architecture",
            file=sys.stderr,
        )
        return 1

    engine = create_engine(db_settings.sqlalchemy_url())
    try:
        project_profiles = args.review_command == "decide" and args.verdict in (
            "merge",
            "restore_support",
            "invalidate_fact",
        )
        try:
            queue = review_queue_builder(
                engine=engine,
                deployment_id=args.deployment,
                project_profiles=project_profiles,
            )
        except Exception as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        with open_surface_scope(surface=SurfaceCostKind.OPERATION):
            if args.review_command == "list":
                return _list_reviews(queue=queue, deployment_id=args.deployment)
            return _decide_review(
                queue=queue,
                deployment_id=args.deployment,
                review_id=args.review_id,
                verdict=args.verdict,
                reviewer=args.reviewer,
                note=args.note,
            )
    finally:
        engine.dispose()


def _run_budget(args: argparse.Namespace) -> int:
    """Compose the local WorkLedger and print configured budget state or show retirement notice."""
    try:
        from sqlalchemy import create_engine

        from rememberstack.spine.settings import load_database_settings
        from rememberstack.spine.work_ledger import WorkLedger
        from rememberstack.spine.work_ledger import WorkLedgerSettings

        db_settings = load_database_settings()
    except Exception:
        print(
            "error: 'remember budget' is retired from the client CLI. "
            "Use 'remember balance' to check account credits. See https://remember.dev/docs",
            file=sys.stderr,
        )
        return 1

    engine = create_engine(db_settings.sqlalchemy_url())
    try:
        ledger = WorkLedger(engine=engine, settings=WorkLedgerSettings())
        return _inspect_budgets(ledger=ledger, deployment_id=args.deployment)
    finally:
        engine.dispose()


def _run_ops(args: argparse.Namespace) -> int:
    """Compose bounded local inspection, one-row replay, or an existing rebuild."""
    if args.ops_command == "graph-catalog":
        return _run_graph_catalog_ensure()
    try:
        from rememberstack.model import ProcessingLane
        from rememberstack.model import WorkLedgerError

        operations_type = import_module(
            "rememberstack.profiles.selfhost_operations"
        ).SelfHostOperations
    except ModuleNotFoundError:
        print(
            "error: ops commands require server container environment dependencies",
            file=sys.stderr,
        )
        return 1

    if args.ops_command == "cost-export":
        return _run_cost_export(deployment_id=args.deployment, args=args)

    operations = operations_type.from_settings()
    try:
        if args.ops_command == "inspect":
            report = operations.inspect(deployment_id=args.deployment)
            print(report.model_dump_json())
            return 0
        if args.ops_command == "replay":
            replayed = operations.replay(
                deployment_id=args.deployment,
                processing_id=args.processing_id,
                attempt_allowance=args.attempts,
                lane=None if args.lane is None else ProcessingLane(args.lane),
                not_before=args.not_before,
            )
            print(replayed.model_dump_json())
            return 0
        result = operations.rebuild(
            deployment_id=args.deployment,
            snapshot_root=args.snapshot_root,
            version=args.version,
        )
        print(json.dumps(result, default=str, sort_keys=True))
        return 0
    except (WorkLedgerError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        operations.close()


def _run_graph_catalog_ensure() -> int:
    """Inspect and, when needed, replay the PostgreSQL live-graph metadata."""
    try:
        from sqlalchemy import create_engine

        from rememberstack.spine.graph_catalog import ensure_graph_catalog
        from rememberstack.spine.settings import load_database_settings
    except ModuleNotFoundError:
        print(
            "error: ops commands require the engine container environment (ghcr.io/writeitai/remember-stack)",
            file=sys.stderr,
        )
        return 1

    engine = create_engine(load_database_settings().sqlalchemy_url())
    try:
        result = ensure_graph_catalog(engine=engine)
        print(
            json.dumps(
                {
                    "ready": result.ready,
                    "changed": result.changed,
                    "problems_before": result.problems_before,
                    "problems_after": result.problems_after,
                    "definitions": result.definitions,
                },
                sort_keys=True,
            )
        )
        return 0
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def _run_cost_export(*, deployment_id: UUID, args: argparse.Namespace) -> int:
    """Print one v1 cost-export page on stdout. Logs stay on stderr."""
    try:
        from sqlalchemy import create_engine

        from rememberstack.spine.cost_export import CostExportConfigError
        from rememberstack.spine.cost_export import CostExportCursorError
        from rememberstack.spine.cost_export import spine_deployment_id
        from rememberstack.spine.cost_export import SqlCostExportReader
        from rememberstack.spine.settings import load_database_settings
    except ModuleNotFoundError:
        print(
            "error: ops commands require the engine container environment (ghcr.io/writeitai/remember-stack)",
            file=sys.stderr,
        )
        return 1

    engine = create_engine(load_database_settings().sqlalchemy_url())
    try:
        try:
            spine_id = spine_deployment_id(engine=engine)
        except CostExportConfigError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if spine_id != deployment_id:
            print(
                "error: --deployment does not match the spine deployment",
                file=sys.stderr,
            )
            return 2
        reader = SqlCostExportReader(engine=engine)
        page = reader.read_page(
            deployment_id=deployment_id, cursor=args.cursor, limit=args.limit
        )
    except CostExportCursorError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(page.model_dump_json())
    return 0


def _run_query(args: argparse.Namespace) -> int:
    """Run a query command through the typed remote SDK."""
    with _cli_memory_client(args) as client:
        return _run_open_query(client=client, args=args)


def _run_operations(args: argparse.Namespace) -> int:
    """List or run the closed assured-operation catalog."""
    with _cli_memory_client(args) as client:
        if args.operation_command == "list":
            for descriptor in client.list_operations():
                print(descriptor.model_dump_json())
            return 0
        try:
            arguments = dict(_split_operation_arg(pair) for pair in args.arg)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(
            client.run_operation(
                name=args.operation, arguments=arguments
            ).model_dump_json()
        )
        return 0


def _run_open_query(*, client: MemoryClient, args: argparse.Namespace) -> int:
    """Additive open-query CLI commands over the same SDK."""
    command = args.query_command
    try:
        if command == "text":
            query_text = args.query_text
            if getattr(args, "answer", False):
                ans = client.answer_context(query=query_text)
                print(ans.model_dump_json(indent=2))
            else:
                fact = client.fact_context(query=query_text)
                print(fact.model_dump_json(indent=2))
            return 0
        if command == "sql":
            print(
                json.dumps(
                    client.query_sql(
                        sql=args.statement,
                        parameters=_json_list(args.parameters),
                        max_rows=args.max_rows,
                    ),
                    default=str,
                )
            )
            return 0
        if command == "explain-sql":
            print(
                json.dumps(
                    client.explain_sql(
                        sql=args.statement, parameters=_json_list(args.parameters)
                    ),
                    default=str,
                )
            )
            return 0
        if command == "space":
            print(
                json.dumps(
                    client.describe_query_space(
                        pattern=args.pattern,
                        include_examples=bool(args.include_examples),
                    ),
                    default=str,
                )
            )
            return 0
        if command == "search-space":
            print(
                json.dumps(
                    client.search_query_space(query=args.query, k=args.k), default=str
                )
            )
            return 0
        if command == "list-saved":
            print(
                json.dumps(
                    client.list_saved_queries(
                        namespace=args.namespace, status=args.status
                    ),
                    default=str,
                )
            )
            return 0
        if command == "describe-saved":
            print(
                json.dumps(
                    client.describe_saved_query(
                        namespace=args.namespace, name=args.name, version=args.version
                    ),
                    default=str,
                )
            )
            return 0
        if command == "run-saved":
            print(
                json.dumps(
                    client.run_saved_query(
                        namespace=args.namespace,
                        name=args.name,
                        version=args.version,
                        parameters=_json_list(args.parameters),
                        max_rows=args.max_rows,
                    ),
                    default=str,
                )
            )
            return 0
    except (ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"error: unknown query command {command!r}", file=sys.stderr)
    return 2


def _json_list(raw: str | None) -> list[object]:
    """Parse an optional JSON array of bound parameters."""
    if not raw:
        return []
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("parameters must be a JSON array")
    return value


def _run_ingest(args: argparse.Namespace) -> int:
    """Push one local file to the deployment's E0 ingress."""
    try:
        with _cli_memory_client(args) as client:
            result = client.ingest(
                args.file,
                mime=args.mime,
                title=args.title,
                source_kind=args.source_kind,
                source_ref=args.source_ref,
                source_modified_at=args.source_modified_at,
                versioning_mode=args.versioning_mode,
                source_version_ref=args.source_version_ref,
            )
    except OSError as error:
        print(f"error: could not read {args.file}: {error}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(result.model_dump_json())
    return 0


def _run_connectors(args: argparse.Namespace) -> int:
    """Manage connector configuration on the deployment API."""
    with _cli_memory_client(args) as client:
        if args.connector_command == "list":
            for connector in client.connectors():
                print(connector.model_dump_json())
            return 0
        if args.connector_command == "add":
            try:
                configuration: dict[str, JsonValue] = dict(
                    _split_arg(pair) for pair in args.config
                )
                connector = ConnectorCreate(
                    kind=args.kind,
                    name=args.name,
                    configuration=configuration,
                    credential_ref=args.credential_ref,
                )
            except ValueError as error:
                print(f"error: {error}", file=sys.stderr)
                return 2
            result = client.add_connector(connector=connector)
        elif args.connector_command == "pause":
            result = client.pause_connector(connector_id=args.connector_id)
        else:
            result = client.connector_status(connector_id=args.connector_id)
    print(result.model_dump_json())
    return 0


def _run_mcp(args: argparse.Namespace) -> int:
    """Expose the remote assured operations and open retrieval tools over MCP."""
    with _cli_memory_client(args) as client:
        return serve_mcp_stdio(server=RemoteOperationMcpServer(client=client))


def operations_list(*, client: httpx.Client) -> int:
    """Print operations from an injected client (the parity-testable seam)."""
    try:
        for descriptor in MemoryClient(client=client).list_operations():
            print(descriptor.model_dump_json())
    except MemoryApiError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def operations_run(*, client: httpx.Client, name: str, arg_pairs: list[str]) -> int:
    """Run one operation through an injected client and print its response."""
    try:
        arguments = dict(_split_operation_arg(pair) for pair in arg_pairs)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    try:
        result = MemoryClient(client=client).run_operation(
            name=name, arguments=arguments
        )
    except MemoryApiError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(result.model_dump_json())
    return 0


def _cli_memory_client(args: argparse.Namespace) -> MemoryClient:
    """Resolve CLI credentials: flags, then env, then the file, then SDK defaults.

    ``MemoryClient.from_settings`` is used when nothing CLI-specific is set so
    existing tests that patch that factory keep working.
    """
    from remember.credentials import authorization_header
    from remember.credentials import CliClientEnv
    from remember.credentials import CredentialError
    from remember.credentials import load_credentials

    flag_url = getattr(args, "api_url", None)
    flag_token = getattr(args, "token", None)
    env = CliClientEnv.model_validate({})
    need_file = (flag_url is None and env.api_url is None) or (
        flag_token is None and env.api_authorization is None
    )
    stored = None
    if need_file:
        try:
            stored = load_credentials()
        except CredentialError as error:
            raise MemoryApiError(status_code=0, detail=str(error)) from error
        if stored is not None:
            _warn_if_expiring(credential=stored)
    if (
        flag_url is None
        and flag_token is None
        and env.api_url is None
        and env.api_authorization is None
        and stored is None
    ):
        return MemoryClient.from_settings()

    from urllib.parse import urlparse

    explicit_target = flag_url or (env.api_url if env.api_url is not None else None)
    api_url = explicit_target or (
        stored.active_data_plane_url if stored is not None else None
    )
    raw_token = flag_token
    if raw_token is None and env.api_authorization is not None:
        raw_token = env.api_authorization.get_secret_value()
    if raw_token is None and stored is not None:
        if explicit_target is None:
            active_tok = stored.active_data_plane_token or stored.access_token
            if active_tok is not None:
                raw_token = active_tok.get_secret_value()
        else:
            target_parsed = urlparse(explicit_target)
            stored_url = stored.active_data_plane_url or stored.api_url
            stored_parsed = urlparse(stored_url) if stored_url else None
            if (
                stored_parsed
                and target_parsed.scheme == stored_parsed.scheme
                and target_parsed.netloc == stored_parsed.netloc
            ):
                active_tok = stored.active_data_plane_token or stored.access_token
                if active_tok is not None:
                    raw_token = active_tok.get_secret_value()
    return MemoryClient(
        base_url=api_url,
        authorization=authorization_header(token=raw_token) if raw_token else None,
    )


#: How long before a stored credential lapses the CLI starts saying so.
#:
#: A machine credential lives in configuration a human edits rarely, so the
#: warning has to arrive far enough ahead that replacing it can be scheduled
#: rather than done in a hurry — but not so far ahead that it becomes noise
#: the operator learns to scroll past.
_EXPIRY_WARNING_WINDOW = timedelta(days=30)


def _warn_if_expiring(*, credential: CredentialFile) -> None:
    """Say on stderr when the stored credential is close to, or past, its end.

    Written to stderr, never stdout: these commands print machine-readable
    output that a script parses, and a warning in that stream would corrupt it.

    A credential with no recorded expiry says nothing — the field is absent for
    credentials issued before expiry existed, and silence is the honest answer
    when we do not know.
    """
    if credential.expires_at is None:
        return
    expires_at = credential.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    remaining = expires_at - datetime.now(tz=UTC)
    if remaining <= timedelta(0):
        print(
            f"warning: this credential expired on {expires_at.isoformat()}; "
            "run `remember login` to replace it",
            file=sys.stderr,
        )
        return
    if remaining <= _EXPIRY_WARNING_WINDOW:
        print(
            f"warning: this credential expires on {expires_at.isoformat()} "
            f"({remaining.days}d); run `remember login` to replace it",
            file=sys.stderr,
        )


def _warn_if_revocation_outstanding() -> None:
    """Say that a superseded credential is still live, without calling out.

    Ordinary commands do not retry the revoke: a query should not make an
    unrelated network call to a token host the user did not ask about. Staying
    silent about a live credential nobody is tracking would be worse than the
    noise.
    """
    from remember.credentials import CredentialError
    from remember.credentials import load_pending_revocations

    try:
        journal = load_pending_revocations()
    except CredentialError as error:
        print(f"warning: {error}", file=sys.stderr)
        return
    for pending in journal.entries:
        print(
            f"warning: a superseded credential (token_id {pending.token_id}) is "
            "still live; run `remember login` or `remember logout` to retire it",
            file=sys.stderr,
        )


def _resolved_token_host(
    *, explicit: str | None, stored_host: str | None = None
) -> str:
    """Require an explicit token host; never derive one from the query API URL."""
    from remember.credentials import TokenHostSettings
    from remember.device_login import normalize_token_host

    settings = TokenHostSettings.model_validate({})
    host = explicit or settings.token_host or stored_host or "https://api.remember.dev"
    return normalize_token_host(token_host=host)


def _run_login(args: argparse.Namespace) -> int:
    """Device-grant login; writes the owner-only credential file.

    Held under the credential lock end to end, so two concurrent logins cannot
    each mint a replacement and overwrite the other's file — which would leave
    one live credential with nothing on disk naming it.
    """
    from remember.credentials import credential_lock
    from remember.credentials import CredentialError
    from remember.credentials import DurabilityUnconfirmed

    try:
        with credential_lock():
            return _login_locked(args)
    except (CredentialError, DurabilityUnconfirmed, OSError) as error:
        # A lock we cannot take, a disk we cannot write, a sync we cannot
        # confirm: all refusals, none of them crashes. These arise outside the
        # login body — in the lock itself and in journal recovery — so the
        # body's own catch never sees them, and the user got a traceback.
        print(f"error: {error}", file=sys.stderr)
        return 1


def _login_locked(args: argparse.Namespace) -> int:
    """The login itself, with the credential lock already held."""
    from remember.credentials import append_pending_revocation
    from remember.credentials import assert_revocation_capacity
    from remember.credentials import CliClientEnv
    from remember.credentials import credential_origin
    from remember.credentials import CredentialError
    from remember.credentials import drop_pending_revocation
    from remember.credentials import DurabilityUnconfirmed
    from remember.credentials import load_credentials
    from remember.credentials import PendingRevocation
    from remember.credentials import write_credentials
    from remember.device_login import authorize_device
    from remember.device_login import credential_from_token
    from remember.device_login import DeviceGrantError
    from remember.device_login import poll_device_token

    # Login binds a newly minted deployment credential. Only the explicit flag
    # may override that deployment's advertised host; a process-wide API URL
    # can legitimately point at some other deployment.
    api_url = args.api_url
    try:
        token_host = _resolved_token_host(explicit=args.token_host)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    # Anything a previous run failed to retire is retried first, while its
    # secret is still on disk and before this run writes its own journal entry.
    _retry_pending_revocation()
    try:
        # Before minting, not after: a journal with no room left would otherwise
        # be discovered when there is already a live credential to record.
        assert_revocation_capacity()
    except CredentialError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    existing = None
    try:
        existing = load_credentials()
    except CredentialError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    try:
        with httpx.Client(
            base_url=token_host, timeout=30.0, follow_redirects=False
        ) as client:
            audience = getattr(args, "audience", "deployment")
            granted = authorize_device(client=client, audience=audience)
            print(f"verification_uri: {granted.verification_uri}")
            print(f"verification_uri_complete: {granted.verification_uri_complete}")
            print(f"user_code: {granted.user_code}")

            def record(minted: object) -> None:
                """Write the credential down the instant it exists.

                Called from inside ``poll_device_token``, before the value is
                visible here, because the boundary between that function
                returning and this frame's next statement cannot be guarded —
                an interrupt landing there left a live credential with nothing
                naming it and no cleanup possible.

                The entry is removed once the credential is adopted, so the
                journal describes only credentials that still need retiring.

                **If the record cannot be written, the credential is given back
                here rather than left live.** This is the last point at which
                anything knows the secret and can act on it: an interrupt or an
                IO failure inside this function used to escape with the mint
                untracked, and there was no later opportunity to notice.

                The ``try`` is the **first** statement, and the attributes are
                read inside it as arguments to the call it protects. Reading
                them beforehand put three more bytecode boundaries outside the
                protected region, and an interrupt at any of them escaped.
                """
                try:
                    append_pending_revocation(
                        pending=PendingRevocation(
                            version=1,
                            token_host=token_host,
                            access_token=minted.access_token,  # type: ignore[attr-defined]
                            token_id=minted.token_id,  # type: ignore[attr-defined]
                        )
                    )
                except BaseException:
                    # The handler's first statement, for the same reason.
                    _withdraw_or_warn(token_host=token_host, minted=minted)
                    raise

            def orphaned(payload: object) -> None:
                """Withdraw a credential that never became a usable one.

                A ``200`` means the token host issued something, whatever
                happened next — a body that would not parse, a validation
                failure, an interrupt while recording. The raw body is the only
                place its secret still exists, so this is the last chance to
                give it back.
                """
                secret = (payload or {}).get("access_token")  # type: ignore[union-attr]
                if not isinstance(secret, str) or not secret:
                    return
                if _revoke_now(token_host=token_host, secret=SecretStr(secret)):
                    return
                print(
                    "warning: the token host issued a credential this login "
                    "could not use or withdraw; revoke it in the console",
                    file=sys.stderr,
                )

            token = poll_device_token(
                client=client,
                device_code=granted.device_code.get_secret_value(),
                interval=granted.interval,
                expires_in=granted.expires_in,
                on_minted=record,
                on_orphan=orphaned,
            )
            # From here the control plane has issued, so the **whole**
            # adoption phase is guarded rather than each call in it: a Ctrl-C
            # lands wherever it lands, and guarding the calls left the gaps
            # between them — an interrupt after converting the response and
            # before journalling produced a live bearer with no file, no
            # journal entry, and no attempt to withdraw it.
            # The new credential is already journalled by `record` above, so
            # nothing below can lose it. What remains is to adopt it and, on
            # success, take it back out of the journal — it is the current
            # credential now, not one awaiting revocation.
            predecessor_to_revoke: PendingRevocation | None = None
            try:
                try:
                    credential = credential_from_token(
                        token=token,
                        api_url=api_url,
                        token_host=token_host,
                        existing=existing,
                    )
                except DeviceGrantError:
                    # The poll already journalled the minted bearer. Retire it
                    # now when possible; if the host cannot confirm that, the
                    # journal keeps the only secret needed for a later retry.
                    _retry_pending_revocation()
                    raise
                if existing is not None:
                    dep_str = str(credential.deployment_id)
                    if existing.deployment_id == credential.deployment_id:
                        predecessor_to_revoke = PendingRevocation(
                            version=1,
                            token_host=existing.token_host,
                            access_token=existing.access_token,
                            token_id=existing.token_id,
                        )
                    elif existing.projects and dep_str in existing.projects:
                        old_p = existing.projects[dep_str]
                        predecessor_to_revoke = PendingRevocation(
                            version=1,
                            token_host=old_p.token_host or existing.token_host,
                            access_token=old_p.data_plane_token,
                            token_id=old_p.token_id or existing.token_id,
                        )

                if predecessor_to_revoke is not None:
                    # Written before the file is overwritten, because
                    # overwriting it destroys the only copy of the
                    # predecessor's secret. A crash after this point leaves a
                    # record of what still needs revoking; a crash before it
                    # leaves the old credential intact and in use.
                    append_pending_revocation(pending=predecessor_to_revoke)
                # Three outcomes, and exactly the rule recovery follows:
                #
                #   confirmed      → the rename is durable; drop the record.
                #   unconfirmable  → this filesystem can never tell us, so
                #                    retrying achieves nothing and holding the
                #                    record would occupy a slot forever. Drop
                #                    it, and say the guarantee is weaker.
                #   raised         → a real failure; keep the record and let
                #                    the next command re-attempt the sync.
                resolved = False
                try:
                    if not write_credentials(credential=credential):
                        print(
                            "warning: this filesystem cannot confirm that the "
                            "credential file's rename is durable; a crash "
                            "could lose it while the credential stays live",
                            file=sys.stderr,
                        )
                    resolved = True
                except DurabilityUnconfirmed as error:
                    # The file *is* written and names the new credential, so
                    # unwinding would revoke something the machine is using.
                    # Only the record's fate differs.
                    print(f"warning: {error}", file=sys.stderr)
                if resolved:
                    drop_pending_revocation(
                        identity=(
                            credential_origin(token_host=token_host),
                            token.token_id,
                        )
                    )
            except BaseException:
                # Unless the hostname-refusal path above already retired the
                # mint, its journal entry stays: the credential exists at the
                # token host and the next login or logout will retire it.
                # Nothing here has to succeed for that to hold.
                raise
    except KeyboardInterrupt:
        return 130
    except DeviceGrantError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except (
        httpx.HTTPError,
        CredentialError,
        ValueError,
        OSError,
        DurabilityUnconfirmed,
    ) as error:
        # OSError included deliberately: a disk that cannot be written to
        # during login is a failed login, not a crash. The credential has
        # already been withdrawn or recorded by the time we get here.
        print(f"error: login failed: {error}", file=sys.stderr)
        return 1
    else:
        # The predecessor is revoked only now, with the replacement already on
        # disk. Revoking first would mean a login interrupted at the browser
        # step — a closed tab, an expired user code — leaves the machine with no
        # credential at all, having destroyed the working one to make room.
        if predecessor_to_revoke is not None:
            _retry_pending_revocation()
        if getattr(args, "audience", "deployment") == "control":
            print("[✓] Authenticated with Remember Cloud organization control plane.")
            if credential.control_plane and credential.control_plane.org_id:
                print(f"org_id: {credential.control_plane.org_id}")
            print(f"token_host: {credential.token_host}")
            print("Run 'remember projects list' to discover all organization projects.")
        else:
            print(f"token_prefix: {credential.token_prefix}")
            print(f"deployment_id: {credential.deployment_id}")
            print(f"api_url: {credential.api_url}")
            if credential.expires_at is not None:
                print(f"expires_at: {credential.expires_at.isoformat()}")
            env_api_url = CliClientEnv.model_validate({}).api_url
            if env_api_url and env_api_url != credential.api_url:
                print(
                    f"warning: REMEMBERSTACK_API_URL={env_api_url} overrides the "
                    f"stored api_url {credential.api_url} for other commands; "
                    "unset it to use this deployment",
                    file=sys.stderr,
                )
        return 0


def _withdraw_or_warn(*, token_host: str, minted: object) -> None:
    """Give a credential back, and say so loudly when that fails.

    The failure handler's first statement, so as little as possible sits
    between something going wrong and the attempt to undo it.
    """
    secret = getattr(minted, "access_token", None)
    if secret is not None and _revoke_now(token_host=token_host, secret=secret):
        return
    print(
        "warning: a credential was minted but could neither be recorded nor "
        f"withdrawn (token_id {getattr(minted, 'token_id', 'unknown')}); "
        "revoke it in the console",
        file=sys.stderr,
    )


def _revoke_now(*, token_host: str, secret: object) -> bool:
    """Best-effort immediate revoke. True only when the host confirmed it."""
    from remember.device_login import revoke_self

    try:
        with httpx.Client(
            base_url=token_host,
            timeout=_RECOVERY_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            status = revoke_self(
                client=client,
                access_token=secret.get_secret_value(),  # type: ignore[attr-defined]
            )
    except BaseException:
        return False
    return _revoke_confirmed(status=status)


def _retry_pending_revocation() -> None:
    """Finish retiring a superseded credential, and say so when it cannot be.

    Never fails the caller. The replacement is already written and working; a
    predecessor left active is one credential too many, which is worth a loud
    warning and a retry on the next login or logout, but is not worth telling
    someone their login failed when it did not.

    **Only a 2xx clears the journal, and a 401 besides.** The self-revoke route
    answers 200 for the first revoke and for an idempotent repeat, so a 2xx is
    genuine confirmation; a 401 means the token host no longer resolves that
    bearer, which is the outcome we wanted by another name. Everything else —
    a 404, which may mean the route is simply absent rather than the credential
    gone; a 5xx; no response at all — confirms nothing, so the entry stays and
    is retried.
    """
    from remember.credentials import confirm_credentials_durable
    from remember.credentials import credential_origin
    from remember.credentials import CredentialError
    from remember.credentials import drop_pending_revocation
    from remember.credentials import DurabilityUnconfirmed
    from remember.credentials import load_credentials
    from remember.credentials import load_pending_revocations
    from remember.device_login import normalize_token_host
    from remember.device_login import revoke_self

    try:
        journal = load_pending_revocations()
    except CredentialError as error:
        print(f"warning: {error}", file=sys.stderr)
        return
    if not journal.entries:
        return
    try:
        current = load_credentials()
    except CredentialError:
        # The credential file cannot be read, so we cannot tell whether an
        # entry names the credential still in use. Revoking blind could take
        # away the only working one; say so and change nothing.
        print(
            "warning: credentials are unreadable, so outstanding revocations "
            "were left alone",
            file=sys.stderr,
        )
        return
    active_identities: set[tuple[str, UUID | None]] = set()
    if current is not None:
        if current.token_id is not None:
            active_identities.add(
                (credential_origin(token_host=current.token_host), current.token_id)
            )
        if current.projects:
            for p in current.projects.values():
                if p.token_id is not None:
                    host = p.token_host or current.token_host
                    active_identities.add(
                        (credential_origin(token_host=host), p.token_id)
                    )

    for pending in journal.entries:
        if pending.identity in active_identities:
            # A crash between writing the journal and writing the replacement
            # leaves both naming the same credential. Revoking it here would
            # destroy the only credential on this machine — so the entry is
            # dropped instead: what it describes never happened.
            #
            # Reading `current` back proves the file is *visible*, which is
            # not the same as its directory entry being on disk — a power loss
            # can still lose the rename while every read here succeeds. So the
            # sync is re-attempted, and only its success justifies forgetting
            # the record. If it still cannot be confirmed the entry stays, and
            # the next command tries again.
            try:
                confirmed = confirm_credentials_durable()
            except DurabilityUnconfirmed:
                # A real IO failure: the write may not have landed, so the
                # record stays and the next command tries again.
                continue
            if not confirmed:
                # This filesystem cannot sync a directory, so no amount of
                # retrying will ever confirm anything and holding the record
                # forever would achieve nothing but occupying a slot. Dropped,
                # with the weaker guarantee said out loud rather than implied.
                print(
                    "warning: this filesystem cannot confirm that the "
                    "credential file's rename is durable; a crash could lose "
                    f"it while credential {pending.token_id} stays live",
                    file=sys.stderr,
                )
            drop_pending_revocation(identity=pending.identity)
            continue
        try:
            host = normalize_token_host(token_host=pending.token_host)
            with httpx.Client(
                base_url=host,
                # Short, unlike an interactive request: this runs under the
                # credential lock, and a handful of black-holed entries at the
                # interactive timeout would hold a login up for minutes.
                timeout=_RECOVERY_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                sec = pending.access_token.get_secret_value()
                p = (
                    "/v1/control-tokens/self"
                    if sec.startswith("umc_cp_")
                    else "/v1/api-tokens/self"
                )
                status = revoke_self(client=client, access_token=sec, path=p)
        except (ValueError, httpx.InvalidURL):
            # A journal entry naming an unusable host can never be retried, and
            # letting it raise would block every later entry behind it. Say so
            # and move on; the entry stays, so the record is not lost.
            print(
                "warning: a superseded credential names an unusable token host "
                f"(token_id {pending.token_id}); revoke it in the console",
                file=sys.stderr,
            )
            continue
        except httpx.HTTPError:
            status = 0
        if _revoke_confirmed(status=status):
            drop_pending_revocation(identity=pending.identity)
            continue
        print(
            "warning: a superseded credential is still live and could not be "
            f"revoked (token_id {pending.token_id}, "
            f"HTTP {status or 'no response'}); the next `remember login` or "
            "`logout` will retry, or revoke it in the console",
            file=sys.stderr,
        )


#: How long one journal retry may take. Deliberately short: recovery runs
#: under the credential lock, so a slow entry delays the login behind it.
_RECOVERY_TIMEOUT_SECONDS = 5.0


def _revoke_confirmed(*, status: int) -> bool:
    """True only when the token host actually said the credential is gone.

    The self-revoke route answers 2xx for the first revoke and for an
    idempotent repeat, and 401 when it no longer resolves that bearer — which
    is the same outcome by another name. Everything else confirms nothing: a
    404 may mean the route is absent rather than the credential retired, and a
    5xx or a dropped connection means we simply do not know.
    """
    return (200 <= status < 300) or status == 401


def _run_logout(args: argparse.Namespace) -> int:
    """Revoke the stored bearer, then unlink the file."""
    from remember.credentials import credential_lock
    from remember.credentials import CredentialError
    from remember.credentials import DurabilityUnconfirmed

    try:
        with credential_lock():
            _retry_pending_revocation()
            return _logout_existing(token_host=args.token_host, allow_stored_host=True)
    except (CredentialError, DurabilityUnconfirmed, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _logout_existing(*, token_host: str | None, allow_stored_host: bool) -> int:
    """Revoke-then-unlink using stored bearers. Keep the file on 5xx."""
    from remember.credentials import CredentialError
    from remember.credentials import load_credentials
    from remember.credentials import unlink_credentials
    from remember.device_login import revoke_self

    try:
        stored = load_credentials()
    except CredentialError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if stored is None:
        return 0
    try:
        host = _resolved_token_host(
            explicit=token_host,
            stored_host=stored.token_host if allow_stored_host else None,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    def _is_local_host(u: str | None) -> bool:
        if not u:
            return True
        try:
            parsed = urlparse(u)
            hn = parsed.hostname or ""
            return hn in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or hn.endswith(
                ".local"
            )
        except Exception:
            return False

    tokens_to_revoke: list[tuple[str, str, str]] = []
    seen_tokens: set[str] = set()

    def _add_token(h: str, sec: str, path: str) -> None:
        if sec and sec not in seen_tokens and not _is_local_host(h):
            seen_tokens.add(sec)
            tokens_to_revoke.append((h, sec, path))

    # Primary token: only revoke remotely if host is not local and token is a cloud token
    if not _is_local_host(host) and not _is_local_host(stored.api_url):
        sec = stored.access_token.get_secret_value()
        p = (
            "/v1/control-tokens/self"
            if sec.startswith("umc_cp_")
            else "/v1/api-tokens/self"
        )
        _add_token(host, sec, p)

    # Projects: each project must only be revoked on its own token_host, and only if not local
    if stored.projects:
        for p in stored.projects.values():
            p_host = getattr(p, "token_host", None) or host
            if not _is_local_host(p_host) and not _is_local_host(p.data_plane_url):
                if p.data_plane_token and p.data_plane_token.get_secret_value():
                    p_sec = p.data_plane_token.get_secret_value()
                    p_path = (
                        "/v1/control-tokens/self"
                        if p_sec.startswith("umc_cp_")
                        else "/v1/api-tokens/self"
                    )
                    _add_token(p_host, p_sec, p_path)

    # Control plane:
    if stored.control_plane and stored.control_plane.access_token:
        cp_host = stored.control_plane.url or host
        if not _is_local_host(cp_host):
            _add_token(
                cp_host,
                stored.control_plane.access_token.get_secret_value(),
                "/v1/control-tokens/self",
            )

    failed = False
    for h, sec, p in tokens_to_revoke:
        with httpx.Client(base_url=h, timeout=30.0, follow_redirects=False) as client:
            status = revoke_self(client=client, access_token=sec, path=p)
        if not _revoke_confirmed(status=status):
            print(
                f"error: revoke not confirmed (HTTP {status or 'no response'}); file kept",
                file=sys.stderr,
            )
            failed = True
            break
    if failed:
        return 1
    try:
        unlink_credentials()
    except CredentialError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _split_arg(pair: str) -> tuple[str, str]:
    """Split one ``key=value`` argument, or raise a clear error."""
    key, separator, value = pair.partition("=")
    if not separator or not key:
        raise ValueError(f"argument {pair!r} is not key=value")
    return key, value


def _split_operation_arg(pair: str) -> tuple[str, object]:
    """Parse a CLI operation value as JSON, retaining ordinary bare strings."""
    key, raw = _split_arg(pair)
    try:
        return key, json.loads(raw)
    except json.JSONDecodeError:
        return key, raw


def _list(*, queue: Any, deployment_id: UUID) -> int:
    """Print open items ranked by expected impact, one JSON line each."""
    for item in queue.pending(deployment_id=deployment_id):
        print(
            json.dumps(
                {
                    "review_id": str(item.review_id),
                    "kind": item.item_kind,
                    "expected_impact": item.expected_impact,
                    "blast_radius": item.blast_radius,
                    "status": item.status,
                    "candidate": item.candidate,
                },
                default=str,
            )
        )
    return 0


def _inspect_budgets(*, ledger: Any, deployment_id: UUID) -> int:
    """Print one current-window JSON record per configured deployment budget."""
    for status in ledger.budget_status(deployment_id=deployment_id):
        print(status.model_dump_json())
    return 0


def _decide(
    *,
    queue: Any,
    deployment_id: UUID,
    review_id: UUID,
    verdict: str,
    reviewer: str,
    note: str | None,
) -> int:
    """Apply one verdict; the verdict picks the decision path by its name."""
    try:
        if verdict in _MERGE_VERDICTS:
            events = queue.decide_merge(
                deployment_id=deployment_id,
                review_id=review_id,
                verdict=verdict,
                reviewer=reviewer,
                note=note,
            )
            print(
                json.dumps(
                    {"verdict": verdict, "merge_events": [str(e) for e in events]}
                )
            )
        else:
            queue.decide_support_withdrawn(
                deployment_id=deployment_id,
                review_id=review_id,
                verdict=verdict,
                reviewer=reviewer,
                note=note,
            )
            print(json.dumps({"verdict": verdict}))
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _build_parser(*, include_internal_ops: bool = False) -> argparse.ArgumentParser:
    """Build the client-first command grammar."""
    parser = argparse.ArgumentParser(
        prog="remember", description="Remember platform command-line interface"
    )
    parser.add_argument(
        "--version", action="version", version=f"remember {__version__}"
    )
    commands = parser.add_subparsers(dest="command")
    client_flags = argparse.ArgumentParser(add_help=False)
    client_flags.add_argument("--api-url", "--url", dest="api_url", default=None)
    client_flags.add_argument("--token", default=None)
    client_flags.add_argument(
        "--self-hosted",
        action="store_true",
        default=False,
        help="operate against local self-hosted instance (http://localhost:8000)",
    )

    child_client_flags = argparse.ArgumentParser(add_help=False)
    child_client_flags.add_argument(
        "--api-url", "--url", dest="api_url", default=argparse.SUPPRESS
    )
    child_client_flags.add_argument("--token", default=argparse.SUPPRESS)
    child_client_flags.add_argument(
        "--self-hosted",
        action="store_true",
        default=argparse.SUPPRESS,
        help="operate against local self-hosted instance (http://localhost:8000)",
    )

    if include_internal_ops:
        review = commands.add_parser("review", help="the D24 local review queue")
        review_commands = review.add_subparsers(dest="review_command", required=True)
        listing = review_commands.add_parser("list", help="open items, impact-ranked")
        listing.add_argument("--deployment", type=UUID, required=True)
        decide = review_commands.add_parser("decide", help="apply one verdict")
        decide.add_argument("review_id", type=UUID)
        decide.add_argument("--deployment", type=UUID, required=True)
        decide.add_argument(
            "--verdict", required=True, choices=(*_MERGE_VERDICTS, *_TRIAGE_VERDICTS)
        )
        decide.add_argument("--reviewer", required=True)
        decide.add_argument("--note", default=None)

        budget = commands.add_parser("budget", help="inspect configured spend ceilings")
        budget_commands = budget.add_subparsers(dest="budget_command", required=True)
        inspect = budget_commands.add_parser(
            "inspect", help="current spend, tier attribution, and parked work"
        )
        inspect.add_argument("--deployment", type=UUID, required=True)

        ops = commands.add_parser("ops", help=argparse.SUPPRESS)
        ops_commands = ops.add_subparsers(dest="ops_command", required=True)
        ops_inspect = ops_commands.add_parser(
            "inspect", help="bounded pipeline, DLQ, projection, and currency report"
        )
        ops_inspect.add_argument("--deployment", type=UUID, required=True)
        cost_export = ops_commands.add_parser(
            "cost-export", help="print one content-free v1 cost-export page"
        )
        cost_export.add_argument("--deployment", type=UUID, required=True)
        cost_export.add_argument("--cursor", default=None)
        cost_export.add_argument("--limit", type=int, default=100)
        replay = ops_commands.add_parser("replay", help="reopen one dead-letter row")
        replay.add_argument("processing_id", type=UUID)
        replay.add_argument("--deployment", type=UUID, required=True)
        replay.add_argument(
            "--attempts",
            type=int,
            default=1,
            help="additional handler attempts to grant (default: 1)",
        )
        replay.add_argument("--lane", choices=("steady", "backfill"))
        replay.add_argument("--not-before", type=datetime.fromisoformat)
        rebuild = ops_commands.add_parser("rebuild", help="rebuild P3 CorpusFS")
        rebuild.add_argument("--deployment", type=UUID, required=True)
        rebuild.add_argument("--snapshot-root", type=Path, required=True)
        rebuild.add_argument("--version", required=True)
        graph_catalog = ops_commands.add_parser(
            "graph-catalog", help="inspect or repair PostgreSQL live-graph metadata"
        )
        graph_catalog_commands = graph_catalog.add_subparsers(
            dest="graph_catalog_command", required=True
        )
        graph_catalog_commands.add_parser(
            "ensure", help="semantically verify and replay graph metadata if needed"
        )

    operations = commands.add_parser(
        "operations", help="list or run assured operations"
    )
    operation_commands = operations.add_subparsers(
        dest="operation_command", required=True
    )
    operation_commands.add_parser(
        "list", parents=[client_flags], help="list the four remote operations"
    )
    run_operation = operation_commands.add_parser(
        "run", parents=[client_flags], help="run one assured operation by name"
    )
    run_operation.add_argument("operation", help="the operation name")
    run_operation.add_argument(
        "--arg", action="append", default=[], metavar="KEY=VALUE", help="repeatable"
    )

    query = commands.add_parser("query", help="query knowledge or open retrieval space")
    query_commands = query.add_subparsers(dest="query_command", required=True)
    text_p = query_commands.add_parser(
        "text",
        parents=[client_flags],
        help="run assured context retrieval (default if free text is provided)",
    )
    text_p.add_argument("query_text", help="query text")
    text_p.add_argument(
        "--answer",
        action="store_true",
        default=False,
        help="run answer_context instead of fact_context",
    )
    sql = query_commands.add_parser(
        "sql", parents=[client_flags], help="run one sandboxed SQL statement"
    )
    sql.add_argument("statement", help="SQL text")
    sql.add_argument("--parameters", help="JSON array of positional bound parameters")
    sql.add_argument("--max-rows", type=int)
    explain_sql = query_commands.add_parser(
        "explain-sql",
        parents=[client_flags],
        help="EXPLAIN one SQL statement without executing it",
    )
    explain_sql.add_argument("statement", help="SQL text")
    explain_sql.add_argument(
        "--parameters", help="JSON array of positional bound parameters"
    )
    space = query_commands.add_parser(
        "space",
        parents=[client_flags],
        help="describe the open query space (manifest discovery)",
    )
    space.add_argument("--pattern", help="optional fnmatch filter over view names")
    space.add_argument(
        "--include-examples",
        action="store_true",
        help="include shipped examples.* names",
    )
    search_space = query_commands.add_parser(
        "search-space", parents=[client_flags], help="search checked-in manifest text"
    )
    search_space.add_argument("query", help="free-text search over the manifest")
    search_space.add_argument("--k", type=int, default=10)
    list_saved = query_commands.add_parser(
        "list-saved", parents=[client_flags], help="list saved-query registry metadata"
    )
    list_saved.add_argument("--namespace")
    list_saved.add_argument("--status")
    describe_saved = query_commands.add_parser(
        "describe-saved",
        parents=[client_flags],
        help="describe one saved-query version",
    )
    describe_saved.add_argument("namespace")
    describe_saved.add_argument("name")
    describe_saved.add_argument("--version", type=int)
    run_saved = query_commands.add_parser(
        "run-saved", parents=[client_flags], help="run one active saved query"
    )
    run_saved.add_argument("namespace")
    run_saved.add_argument("name")
    run_saved.add_argument("--version", type=int)
    run_saved.add_argument(
        "--parameters", help="JSON array of positional bound parameters"
    )
    run_saved.add_argument("--max-rows", type=int)

    ingest = commands.add_parser(
        "ingest", parents=[client_flags], help="push a file through E0"
    )
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--mime")
    ingest.add_argument("--title")
    ingest.add_argument("--source-kind")
    ingest.add_argument("--source-ref")
    ingest.add_argument("--source-modified-at", type=datetime.fromisoformat)
    ingest.add_argument(
        "--versioning-mode", choices=("snapshot", "living"), default="snapshot"
    )
    ingest.add_argument("--source-version-ref")

    connectors = commands.add_parser(
        "connectors", help="manage deployment-side connectors"
    )
    connector_commands = connectors.add_subparsers(
        dest="connector_command", required=True
    )
    connector_commands.add_parser(
        "list", parents=[client_flags], help="list connectors"
    )
    add = connector_commands.add_parser(
        "add", parents=[client_flags], help="add connector configuration"
    )
    add.add_argument("kind")
    add.add_argument("--name", required=True)
    add.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    add.add_argument("--credential-ref")
    pause = connector_commands.add_parser(
        "pause", parents=[client_flags], help="pause a connector"
    )
    pause.add_argument("connector_id", type=UUID)
    status = connector_commands.add_parser(
        "status", parents=[client_flags], help="show connector status"
    )
    status.add_argument("connector_id", type=UUID)

    commands.add_parser(
        "mcp",
        parents=[client_flags],
        help="serve remote retrieval tools over MCP stdio",
    )
    login = commands.add_parser("login", help="device-grant login to a token host")
    login.add_argument("--token-host", default=None)
    login.add_argument("--api-url", default=None)
    login.add_argument(
        "--audience",
        choices=["deployment", "control"],
        default="deployment",
        help="credential audience: 'deployment' (default, memory data plane) or 'control' (organization control plane, D56)",
    )
    login.add_argument(
        "--control-plane",
        "--control",
        dest="audience",
        action="store_const",
        const="control",
        help="shorthand for --audience control",
    )
    logout = commands.add_parser("logout", help="revoke the stored bearer and unlink")
    logout.add_argument("--token-host", default=None)

    setup = commands.add_parser(
        "setup", help="bootstrap AI coding harnesses for Remember"
    )
    setup.add_argument(
        "--cloud",
        dest="target_env",
        action="store_const",
        const="cloud",
        default=None,
        help="configure for Managed Cloud",
    )
    setup.add_argument(
        "--self-hosted",
        dest="target_env",
        action="store_const",
        const="self_hosted",
        default=None,
        help="configure for local engine (http://localhost:8000)",
    )
    setup.add_argument("--url", default=None, help="explicit data-plane URL override")
    setup.add_argument(
        "--token",
        default=None,
        help="explicit token override (ambient login preferred)",
    )
    setup.add_argument(
        "--agent",
        choices=("cursor", "claude", "codex", "agy", "all"),
        default="all",
        help="target specific harness (auto-detects all present if omitted)",
    )
    setup.add_argument(
        "--dir",
        dest="cwd",
        default=None,
        type=Path,
        help="target project directory (default: current working directory)",
    )
    setup.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="preview file modifications without writing",
    )

    commands.add_parser(
        "doctor",
        parents=[client_flags],
        help="verify installation, credentials, connectivity, and harnesses",
    )

    commands.add_parser(
        "whoami",
        parents=[client_flags],
        help="display authenticated identity and active project",
    )

    commands.add_parser(
        "balance",
        parents=[client_flags],
        help="display current credit balance and subscription status",
    )

    commands.add_parser(
        "billing",
        parents=[client_flags],
        help="display current credit balance and subscription status",
    )

    projects = commands.add_parser(
        "projects", parents=[client_flags], help="manage tenant projects"
    )
    projects_sub = projects.add_subparsers(dest="projects_command", required=True)
    projects_sub.add_parser(
        "list", parents=[child_client_flags], help="list projects in your organization"
    )
    create_proj = projects_sub.add_parser(
        "create", parents=[child_client_flags], help="create a new project"
    )
    create_proj.add_argument("name", help="project name")

    switch = commands.add_parser(
        "switch",
        parents=[client_flags],
        help="switch active project in local configuration",
    )
    switch.add_argument("project", help="project name or ID")

    members = commands.add_parser(
        "members", parents=[client_flags], help="manage organization team members"
    )
    members_sub = members.add_subparsers(dest="members_command", required=True)
    members_sub.add_parser("list", parents=[child_client_flags], help="list members")
    invite_mem = members_sub.add_parser(
        "invite", parents=[child_client_flags], help="invite a member by email"
    )
    invite_mem.add_argument("email", help="email address to invite")
    invite_mem.add_argument(
        "--role",
        default="member",
        choices=["member", "owner", "MEMBER", "OWNER"],
        help="role for invited member: member or owner (default: member)",
    )

    return parser


def rememberstack_main(argv: list[str] | None = None) -> int:
    """Terminal forwarder for the deprecated rememberstack CLI command."""
    print(
        "DeprecationWarning: 'rememberstack' CLI is deprecated. Use 'remember' instead.",
        file=sys.stderr,
    )
    return main(argv)


def main_status(argv: list[str] | None = None) -> int:
    """Legacy remember-status entry point: reports cloud deployment, billing, and spend."""
    from remember.client import CloudClient
    from remember.errors import CloudError
    from remember.errors import RateLimited
    from remember.errors import Unauthenticated

    parser = argparse.ArgumentParser(
        prog="remember-status",
        description=(
            "Report a remember.dev organisation's deployment, billing, and "
            "spend state. Reads REMEMBER_CLOUD_TOKEN and REMEMBER_CLOUD_ORG."
        ),
    )
    parser.add_argument("--org", default=None, help="organisation id")
    parser.add_argument("--url", default=None, help="control-plane base URL")
    parser.add_argument(
        "--quiet", action="store_true", help="print nothing; use the exit status only"
    )
    args = parser.parse_args(argv)

    try:
        overrides: dict[str, str] = {}
        if args.org:
            overrides["org_id"] = args.org
        if args.url:
            overrides["base_url"] = args.url
        with CloudClient.from_env(**overrides) as cloud:
            deployment = cloud.deployment()
            billing = cloud.billing_status()
            gate = (
                cloud.spend_gate(deployment_id=deployment.id)
                if deployment is not None
                else None
            )

            if not args.quiet:
                if deployment is None:
                    print(
                        f"{'deployment':<11} {'none':<10} no deployment provisioned yet"
                    )
                else:
                    print(f"{'deployment':<11} {deployment.state:<10} {deployment.id}")
                    endpoint_state = (
                        "live" if deployment.hostname_live else "not serving"
                    )
                    print(
                        f"{'endpoint':<11} {endpoint_state:<10} {deployment.hostname or 'unknown'}"
                    )
                balance = f"balance {billing.balance}" if billing.balance else ""
                print(f"{'billing':<11} {billing.state:<10} {balance}".rstrip())
                if gate is not None:
                    print(
                        f"{'spend':<11} {gate.decision:<10} {gate.reason_code or ''}".rstrip()
                    )

            ready = (
                deployment is not None
                and deployment.is_ready
                and billing.can_spend
                and (gate is None or gate.allows_work)
            )
            return 0 if ready else 1
    except ValueError as error:
        print(f"remember-status: {error}", file=sys.stderr)
        return 2
    except Unauthenticated as error:
        print(
            f"remember-status: credential rejected ({error}). "
            "Mint a fresh control-plane token with "
            "POST /v1/orgs/<org>/control-tokens while signed in.",
            file=sys.stderr,
        )
        return 2
    except RateLimited as error:
        wait = f" retry in {error.retry_after:.0f}s" if error.retry_after else ""
        print(f"remember-status: rate limited{wait}", file=sys.stderr)
        return 2
    except CloudError as error:
        print(f"remember-status: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised via the entry point
    raise SystemExit(main())
