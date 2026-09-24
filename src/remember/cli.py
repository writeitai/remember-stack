"""The ``remember`` CLI: a dependency-light client plus optional local admin commands.

Query, ingest, connector management, and MCP all talk to the deployment HTTP
API. ``remember ops`` imports the server extra and connects to the spine.
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
from uuid import UUID

import httpx
from pydantic import JsonValue
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from remember import __version__
from remember.client import MemoryApiError
from remember.client import MemoryClient
from remember.credentials import CredentialError
from remember.credentials import DurabilityUnconfirmed
from remember.errors import StoredKeyRefused
from remember.issuer import DEFAULT_ISSUER
from remember.models import ConnectorCreate
from remember.remote_mcp import RemoteOperationMcpServer
from remember.remote_mcp import serve_mcp_stdio


class _InternalOpsSettings(BaseSettings):
    """Whether ``remember ops`` is enabled (engine containers only)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_", extra="ignore")

    internal_ops: bool = False


def main(argv: list[str] | None = None) -> int:
    """The ``remember`` entry point; returns the process exit code."""
    from pydantic import ValidationError

    parser: argparse.ArgumentParser | None = None
    try:
        _settle_journal()
        effective_argv = list(sys.argv[1:] if argv is None else argv)
        internal_ops = _InternalOpsSettings.model_validate({}).internal_ops
        if effective_argv:
            subcmd = effective_argv[0]
            if subcmd == "ops" and not internal_ops:
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
                    "adjacent-chunks",
                }
                has_subcmd = any(
                    arg in known_query_subcmds for arg in effective_argv[1:]
                )
                has_help = any(arg in ("-h", "--help") for arg in effective_argv[1:])
                if not has_subcmd and not has_help:
                    effective_argv.insert(1, "text")

        parser = _build_parser(include_internal_ops=internal_ops)
        args = parser.parse_args(effective_argv)

        handlers = {
            "setup": _run_setup,
            "doctor": _run_doctor,
            "whoami": _run_whoami,
            "switch": _run_switch,
            "ops": _run_ops,
            "operations": _run_operations,
            "query": _run_query,
            "ingest": _run_ingest,
            "documents": _run_documents,
            "connectors": _run_connectors,
            "mcp": _run_mcp,
            "login": _run_login,
            "logout": _run_logout,
        }
        handler = handlers.get(args.command)
        if handler is not None:
            return handler(args)
    except KeyboardInterrupt:
        return 130
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
    except StoredKeyRefused as error:
        print(f"error: {error.detail}", file=sys.stderr)
        return 2
    except (
        MemoryApiError,
        CredentialError,
        DurabilityUnconfirmed,
        httpx.InvalidURL,
        httpx.RequestError,
        ValueError,
        OSError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if parser is not None:
        parser.print_help()
    return 2


def _issuer_http() -> httpx.Client:
    """The HTTP client for issuer calls: no automatic redirects (same-origin only)."""
    return httpx.Client(timeout=30.0, follow_redirects=False)


def _settle_journal() -> None:
    """Retry journalled revocations at CLI start (D136 §8.4).

    Costs nothing unless a previous login left a replaced key unrevoked.
    """
    from remember.credentials import credential_lock
    from remember.credentials import load_pending_revocations
    from remember.login import retry_journal

    try:
        if not load_pending_revocations().entries:
            return
        with credential_lock(), _issuer_http() as http:
            retry_journal(http=http)
    except (CredentialError, DurabilityUnconfirmed, OSError) as error:
        print(f"warning: {error}", file=sys.stderr)


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
    try:
        stored = load_credentials()
    except CredentialError as err:
        print(f"[!] {err}")
        stored = None
        all_ok = False
    if stored is None:
        print(f"[-] No stored credentials at {cred_file}")
    elif stored.issuer:
        expires = stored.expires_at.isoformat() if stored.expires_at else "unknown"
        print(
            f"[✓] Signed in to {stored.issuer} (key {stored.key_id}, expires {expires})"
        )
    else:
        print(f"[✓] Self-hosted engine stored: {stored.api_url}")

    # 3. Engine connectivity and authentication, resolved like every command
    try:
        with MemoryClient(
            api_key=getattr(args, "api_key", None),
            base_url=getattr(args, "api_url", None),
            project=getattr(args, "project", None),
            timeout=5.0,
        ) as client:
            start_t = time.perf_counter()
            info = client.deployment_build_info()
            elapsed = int((time.perf_counter() - start_t) * 1000)
        print(
            f"[✓] Engine reachable and authenticated ({elapsed}ms, "
            f"build {info.build_revision or 'unknown'})"
        )
    except (MemoryApiError, CredentialError, ValueError) as err:
        print(f"[!] Engine check failed: {err}")
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
    """Print the stored key's claims (never calls the engine)."""
    from remember.login import whoami

    with _issuer_http() as http:
        return whoami(http=http)


def _run_switch(args: argparse.Namespace) -> int:
    """Set the stored default project after resolving it once."""
    from remember.login import switch

    with _issuer_http() as http:
        switch(project=args.project, http=http)
    return 0


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
        if args.ops_command == "resume-no-route":
            released = operations.resume_no_route(deployment_id=args.deployment)
            print(json.dumps({"released": [str(item) for item in released]}))
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
            if getattr(args, "combined", False):
                ans = client.combined_context(query=query_text)
                print(ans.model_dump_json(indent=2))
            else:
                fact = client.facts_context(query=query_text)
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
        if command == "adjacent-chunks":
            res = client.adjacent_chunks(chunk_id=args.chunk_id, window=args.window)
            print(res.model_dump_json(indent=2))
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
    if result.parked == "no_route":
        print(
            f"warning: {args.file} was stored but is parked waiting for a"
            " conversion route for its file type (parked: no_route). An operator"
            " adds a route if needed, then runs `remember ops resume-no-route`.",
            file=sys.stderr,
        )
    return 0


def _run_documents(args: argparse.Namespace) -> int:
    """List the deployment's documents, or delete one from the live memory."""
    with _cli_memory_client(args) as client:
        if args.documents_command == "list":
            page = client.list_documents(
                limit=args.limit, cursor=args.cursor, status=args.status
            )
            print(page.model_dump_json())
            return 0
        deletion = client.delete_document(doc_id=args.doc_id)
    print(deletion.model_dump_json())
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
        return serve_mcp_stdio(
            server=RemoteOperationMcpServer(
                client=client, read_only=bool(args.read_only)
            )
        )


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
    """The memory client for a command: flags, then environment, then the file."""
    _warn_if_expiring()
    return MemoryClient(
        api_key=getattr(args, "api_key", None),
        base_url=getattr(args, "api_url", None),
        project=getattr(args, "project", None),
    )


#: How long before a stored key lapses the CLI starts saying so.
_EXPIRY_WARNING_WINDOW = timedelta(days=30)


def _warn_if_expiring() -> None:
    """Say on stderr when the stored key is close to, or past, its expiry."""
    from remember.credentials import load_credentials

    try:
        stored = load_credentials()
    except CredentialError:
        return
    if stored is None or stored.expires_at is None:
        return
    remaining = stored.expires_at - datetime.now(tz=UTC)
    if remaining <= timedelta(0):
        print(
            f"warning: the stored key expired on {stored.expires_at.isoformat()}; "
            "run `remember login` to replace it",
            file=sys.stderr,
        )
    elif remaining <= _EXPIRY_WARNING_WINDOW:
        print(
            f"warning: the stored key expires on {stored.expires_at.isoformat()} "
            f"({remaining.days}d); run `remember login` to replace it",
            file=sys.stderr,
        )


def _run_login(args: argparse.Namespace) -> int:
    """RFC 8628 device login against the issuer; stores one key."""
    from remember.connection import resolve_connection
    from remember.issuer import IssuerError
    from remember.login import login

    try:
        issuer = resolve_connection(issuer=args.issuer).issuer
    except (CredentialError, ValueError):
        # An unreadable file is what `remember login` replaces.
        from remember.connection import environment_issuer

        issuer = args.issuer or environment_issuer()
    issuer = issuer or DEFAULT_ISSUER
    try:
        with _issuer_http() as http:
            stored = login(issuer=issuer, http=http)
    except IssuerError as error:
        print(f"error: {error.detail}", file=sys.stderr)
        return 1
    expires = stored.expires_at.isoformat() if stored.expires_at else "unknown"
    print(f"Signed in to {stored.issuer}. Key {stored.key_id or ''} expires {expires}.")
    return 0


def _run_logout(args: argparse.Namespace) -> int:
    """Revoke the stored key at its issuer, then remove the file."""
    from remember.login import logout

    with _issuer_http() as http:
        return logout(http=http)


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
    client_flags.add_argument(
        "--api-url",
        default=None,
        help="engine URL (overrides REMEMBER_API_URL and the stored file)",
    )
    client_flags.add_argument(
        "--api-key",
        default=None,
        help="API key (overrides REMEMBER_API_KEY and the stored file)",
    )
    client_flags.add_argument(
        "--project",
        default=None,
        help="project id or name for a signed key (overrides REMEMBER_PROJECT)",
    )

    if include_internal_ops:
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
        resume = ops_commands.add_parser(
            "resume-no-route",
            help="release parked conversion covered by current routes",
        )
        resume.add_argument("--deployment", type=UUID, required=True)
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
        "--combined",
        action="store_true",
        default=False,
        help="run combined_context instead of facts_context",
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
    adjacent_chunks = query_commands.add_parser(
        "adjacent-chunks",
        parents=[client_flags],
        help="fetch surrounding source chunks within a window around a target chunk",
    )
    adjacent_chunks.add_argument("chunk_id", help="the target chunk UUID")
    adjacent_chunks.add_argument(
        "--window",
        type=int,
        default=1,
        help="window size in chunks before and after (1-2, default 1)",
    )

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

    documents = commands.add_parser(
        "documents", help="list documents or delete one from memory"
    )
    documents_commands = documents.add_subparsers(
        dest="documents_command", required=True
    )
    list_documents = documents_commands.add_parser(
        "list",
        parents=[client_flags],
        help="one page of documents, newest first, as JSON",
    )
    list_documents.add_argument(
        "--limit", type=int, default=50, help="documents per page (1-200, default 50)"
    )
    list_documents.add_argument(
        "--cursor", help="the cursor a previous page returned, to read the next"
    )
    list_documents.add_argument(
        "--status",
        choices=("ingesting", "converting", "structuring", "ready", "failed"),
        help="only documents whose newest version has this status",
    )
    delete_document = documents_commands.add_parser(
        "delete",
        parents=[client_flags],
        help=(
            "remove a document from memory: its claims stop counting and facts"
            " only it supported are closed"
        ),
    )
    delete_document.add_argument("doc_id", type=UUID)

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

    mcp = commands.add_parser(
        "mcp",
        parents=[client_flags],
        help="serve remote retrieval tools over MCP stdio",
    )
    mcp.add_argument(
        "--read-only",
        action="store_true",
        help="omit and refuse the ingest, pipeline-readiness and delete tools",
    )
    login = commands.add_parser(
        "login", help="sign in with the device grant and store one key"
    )
    login.add_argument(
        "--issuer",
        default=None,
        help=f"key issuer URL (default: REMEMBER_ISSUER, else {DEFAULT_ISSUER})",
    )
    commands.add_parser("logout", help="revoke the stored key and remove it")

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
        help="configure for a self-hosted engine (default http://127.0.0.1:8000)",
    )
    setup.add_argument(
        "--api-url", default=None, help="engine URL (self-hosted, or an override)"
    )
    setup.add_argument(
        "--api-key",
        default=None,
        help="the self-hosted engine's key, stored in the owner-only credential file",
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
        "whoami", help="show the stored key's issuer, projects, permissions and expiry"
    )

    switch = commands.add_parser(
        "switch", help="set the default project for the stored key"
    )
    switch.add_argument("project", help="project id or name")

    return parser


def rememberstack_main(argv: list[str] | None = None) -> int:
    """Terminal forwarder for the deprecated rememberstack CLI command."""
    print(
        "DeprecationWarning: 'rememberstack' CLI is deprecated. Use 'remember' instead.",
        file=sys.stderr,
    )
    return main(argv)


if __name__ == "__main__":  # pragma: no cover - exercised via the entry point
    raise SystemExit(main())
