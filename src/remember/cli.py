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
import math
from pathlib import Path
import sys
from typing import get_args
from typing import NoReturn
from uuid import UUID

import httpx
from pydantic import AliasChoices
from pydantic import Field
from pydantic import JsonValue
from pydantic import TypeAdapter
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from remember import __version__
from remember.cli_output import CommandFailure
from remember.cli_output import diagnostic_secrets
from remember.cli_output import failure
from remember.cli_output import report_error
from remember.cli_output import usage_failure
from remember.client import MemoryApiError
from remember.client import MemoryClient
from remember.connection import Connection
from remember.credentials import CredentialError
from remember.credentials import DurabilityUnconfirmed
from remember.errors import _PipelineReadinessTimeout
from remember.errors import PipelineDeadLettered
from remember.errors import redact_diagnostic
from remember.errors import StoredKeyRefused
from remember.issuer import DEFAULT_ISSUER
from remember.issuer import DeviceGrantError
from remember.issuer import fetch_issuer_metadata
from remember.issuer import IssuerError
from remember.issuer import same_origin
from remember.mcp_tools import map_error
from remember.models import ConnectorCreate
from remember.models import DocumentSearchFilters
from remember.models import DocumentTagFilter
from remember.models import EffectivePeriodInput
from remember.models import ReadinessRequirements
from remember.models import ReadTime
from remember.models import ReferenceInput
from remember.models import ReferenceKind


class _InternalOpsSettings(BaseSettings):
    """Whether ``remember ops`` is enabled.

    The engine image sets it, so ``docker compose exec api remember ops`` works
    as-is. A client install leaves it unset: ``ops`` needs the server
    dependencies and the deployment's database, which only the engine has.
    """

    model_config = SettingsConfigDict(extra="ignore")

    internal_ops: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "REMEMBERSTACK_INTERNAL_OPS", "REMEMBER_INTERNAL_OPS"
        ),
    )


class _ClientRequestSettings(BaseSettings):
    """The CLI request timeout, overridable by REMEMBER_TIMEOUT or a flag."""

    model_config = SettingsConfigDict(env_prefix="REMEMBER_", extra="ignore")

    timeout: float = Field(default=300.0, gt=0, allow_inf_nan=False)


def main(argv: list[str] | None = None) -> int:
    """The ``remember`` entry point; returns the process exit code."""
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    options = (
        effective_argv[: effective_argv.index("--")]
        if "--" in effective_argv
        else effective_argv
    )
    machine = "--json" in options
    parser: argparse.ArgumentParser | None = None
    args: argparse.Namespace | None = None
    try:
        _settle_journal()
        internal_ops = _InternalOpsSettings.model_validate({}).internal_ops
        command_index = next(
            (index for index, arg in enumerate(effective_argv) if arg != "--json"),
            len(effective_argv),
        )
        subcmd = next(iter(effective_argv[command_index:]), None)
        if subcmd == "ops" and not internal_ops:
            raise failure(
                code="command_unavailable",
                detail="'remember ops' runs inside the engine container",
                action=(
                    "Run 'docker compose exec api remember ops inspect --deployment <id>'"
                    " there, or use 'remember operations list|run' from a client."
                ),
            )
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
            # Skip output flags before looking for an explicit query command.
            query_index = command_index + 1
            while effective_argv[query_index : query_index + 1] == ["--json"]:
                query_index += 1
            has_subcmd = (
                query_index < len(effective_argv)
                and effective_argv[query_index] in known_query_subcmds
            )
            has_help = any(arg in ("-h", "--help") for arg in options[query_index:])
            if not has_subcmd and not has_help:
                effective_argv.insert(query_index, "text")

        parser = _build_parser(include_internal_ops=internal_ops, machine=machine)
        args = parser.parse_args(effective_argv)
        args.json = machine

        handlers = {
            "setup": _run_setup,
            "doctor": _run_doctor,
            "whoami": _run_whoami,
            "switch": _run_switch,
            "ops": _run_ops,
            "operations": _run_operations,
            "query": _run_query,
            "ingest": _run_ingest,
            "readiness": _run_readiness,
            "deployment": _run_deployment,
            "processing-layers": _run_processing_layers,
            "resolve": _run_resolve,
            "search": _run_search,
            "lookup": _run_lookup,
            "graph": _run_graph,
            "relation": _run_relation,
            "documents": _run_documents,
            "forget": _run_forget,
            "tags": _run_tags,
            "connectors": _run_connectors,
            "mcp": _run_mcp,
            "login": _run_login,
            "logout": _run_logout,
            "mount": _run_mount,
            "unmount": _run_unmount,
        }
        handler = handlers.get(args.command)
        if handler is not None:
            return handler(args)
    except _ParseFailure as error:
        diagnostic = usage_failure(str(error))
        report_error(error=diagnostic.error, machine=machine)
        if not machine:
            raise SystemExit(diagnostic.exit_code) from None
        return diagnostic.exit_code
    except CommandFailure as error:
        report_error(
            error=error.error, machine=machine, key=getattr(args, "api_key", None)
        )
        return error.exit_code
    except KeyboardInterrupt:
        error = failure(
            code="interrupted",
            detail="Command interrupted.",
            action="Check the operation's outcome before rerunning it.",
            exit_code=130,
        )
        report_error(error=error.error, machine=machine)
        return error.exit_code
    except (
        MemoryApiError,
        CredentialError,
        DurabilityUnconfirmed,
        httpx.InvalidURL,
        httpx.RequestError,
        ValueError,
        OSError,
    ) as error:
        diagnostic = _command_error(error)
        report_error(
            error=diagnostic.error, machine=machine, key=getattr(args, "api_key", None)
        )
        return diagnostic.exit_code
    except Exception:  # noqa: BLE001 — CLI process boundary
        report_error(
            error=failure(
                code="internal_error",
                detail="An unexpected command failure occurred.",
                action="Report the command and Remember version, omitting credentials.",
            ).error,
            machine=machine,
        )
        return 1
    if parser is not None and not machine:
        parser.print_help()
    report_error(error=usage_failure("A command is required.").error, machine=machine)
    return 2


def _command_error(error: Exception) -> CommandFailure:
    """Convert client and local errors to an actionable command failure."""
    if isinstance(error, StoredKeyRefused):
        return failure(
            code="stored_key_refused",
            detail=error.detail,
            action="Pass the destination's key explicitly with REMEMBER_API_KEY or --api-key.",
            exit_code=2,
        )
    if isinstance(error, CredentialError):
        return failure(
            code="credentials_unusable",
            detail=str(error),
            action="Check the credential file or run `remember login`.",
        )
    if isinstance(error, DurabilityUnconfirmed):
        return failure(
            code="durability_unconfirmed",
            detail=str(error),
            action="Check the credential directory and disk, then inspect the current sign-in state.",
        )
    if isinstance(error, DeviceGrantError):
        return failure(
            code="device_grant_failed",
            detail=error.detail,
            action="Run `remember login` again and approve the request in the browser.",
        )
    if isinstance(error, IssuerError):
        return failure(
            code="issuer_error",
            detail=error.detail,
            action="Check the issuer URL and network and retry.",
        )
    if isinstance(error, MemoryApiError):
        return CommandFailure(error=map_error(error))
    if isinstance(error, httpx.RequestError):
        return CommandFailure(
            error=map_error(ConnectionError("Network request failed."))
        )
    if isinstance(error, ValidationError):
        fields = ", ".join(
            ".".join(str(part) for part in item["loc"])
            for item in error.errors(include_input=False, include_context=False)
        )
        return failure(
            code="invalid_configuration",
            detail=f"Invalid environment configuration or input ({fields}).",
            action="Check the named settings or arguments and rerun.",
        )
    if isinstance(error, (ValueError, httpx.InvalidURL)):
        return failure(
            code="invalid_arguments",
            detail=str(error),
            action="Check the command arguments and connection settings and rerun.",
        )
    return failure(
        code="local_io_error",
        detail=str(error),
        action="Check the local files, permissions and available disk space.",
    )


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

    return run_setup(args)


def _run_doctor(args: argparse.Namespace) -> int:
    """Verify installation, credentials, network connectivity, and harnesses."""
    import shutil
    import time

    from remember.credentials import credentials_path
    from remember.credentials import load_credentials
    from remember.setup import get_claude_desktop_config_path

    checks: list[dict[str, object]] = []
    diagnostic: CommandFailure | None = None
    secrets = diagnostic_secrets(key=args.api_key)

    def check(*, name: str, detail: str, ok: bool = True) -> None:
        """Capture one doctor check and render its terminal form if requested."""
        detail = redact_diagnostic(detail, secrets=secrets)
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not args.json:
            print(detail)

    if not args.json:
        print(f"Remember Doctor (v{__version__})\n")

    # 1. Launcher resolution
    rem_cand = shutil.which("remember")
    uvx_cand = shutil.which("uvx")
    if rem_cand:
        resolved = Path(rem_cand).resolve().as_posix()
        check(name="launcher", detail=f"[✓] 'remember' binary found: {resolved}")
    elif uvx_cand:
        resolved = Path(uvx_cand).resolve().as_posix()
        check(
            name="launcher",
            detail=f"[✓] 'uvx' runner found: {resolved} (remember available via uvx)",
        )
    else:
        check(
            name="launcher",
            ok=False,
            detail="[!] Neither 'remember' nor 'uvx' found on PATH. Run 'uv tool install remember'.",
        )

    # 2. Stored credentials
    cred_file = credentials_path()
    try:
        stored = load_credentials()
    except CredentialError as err:
        check(name="credentials", ok=False, detail=f"[!] {err}")
        diagnostic = _command_error(err)
    else:
        if stored is None:
            check(
                name="credentials", detail=f"[-] No stored credentials at {cred_file}"
            )
        elif stored.issuer:
            expires = stored.expires_at.isoformat() if stored.expires_at else "unknown"
            check(
                name="credentials",
                detail=f"[✓] Signed in to {stored.issuer} (key {stored.key_id}, expires {expires})",
            )
        else:
            check(
                name="credentials",
                detail=f"[✓] Self-hosted engine stored: {stored.api_url}",
            )

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
        check(
            name="engine",
            detail=(
                f"[✓] Engine reachable and authenticated ({elapsed}ms, "
                f"build {info.build_revision or 'unknown'})"
            ),
        )
        for line in _tool_version_mismatches(info.tools):
            check(name="tool_version", ok=False, detail=f"[!] {line}")
    except (MemoryApiError, httpx.InvalidURL, ValueError) as err:
        diagnostic = _command_error(err)
        check(
            name="engine",
            ok=False,
            detail=f"[!] Engine check failed: {diagnostic.error.detail}",
        )

    # 4. Harness configurations & syntax validation
    if not args.json:
        print("\nCoding Agent Harnesses:")
    harness_files = (
        (
            "Cursor",
            Path.cwd() / ".cursor" / "mcp.json",
            "remember setup --agent cursor",
        ),
        (
            "Antigravity",
            Path.cwd() / ".agents" / "mcp_config.json",
            "remember setup --agent agy",
        ),
        (
            "Codex",
            Path.cwd() / ".codex" / "config.toml",
            "remember setup --agent codex",
        ),
        ("Claude Desktop", get_claude_desktop_config_path(), None),
    )
    for label, path, hint in harness_files:
        ok, line = _check_harness_file(label=label, path=path, hint=hint)
        check(name=label, ok=ok, detail=line)

    all_ok = all(item["ok"] for item in checks)
    if args.json:
        print(json.dumps({"version": __version__, "ok": all_ok, "checks": checks}))
    else:
        print()
    if diagnostic is not None:
        raise diagnostic
    if not all_ok:
        raise failure(
            code="doctor_failed",
            detail="One or more local or tool-version checks failed.",
            action="Review the doctor report and resolve the failed checks before rerunning.",
        )
    return 0


def _check_harness_file(
    *, label: str, path: Path, hint: str | None
) -> tuple[bool, str]:
    """One doctor line for a harness file's ``remember`` entry, and whether it passes."""
    import json
    import os
    import shutil
    import tomllib

    if not path.is_file():
        if hint is None:
            return True, f"[-] {label}: not detected at {path}"
        return True, f"[-] {label}: not configured in this directory (run '{hint}')"
    toml = path.suffix == ".toml"
    try:
        text = path.read_text(encoding="utf-8")
        data = tomllib.loads(text) if toml else json.loads(text)
    except (OSError, ValueError) as error:
        kind = "TOML" if toml else "JSON"
        return False, f"[!] {label}: malformed {kind} in {path}: {error}"
    servers = (
        data.get("mcp_servers" if toml else "mcpServers")
        if isinstance(data, dict)
        else None
    )
    entry = servers.get("remember") if isinstance(servers, dict) else None
    if not isinstance(entry, dict):
        return False, f"[!] {label}: missing 'remember' MCP server ({path})"
    url = entry.get("url")
    if isinstance(url, str):
        return True, f"[✓] {label}: remote entry for {url} ({path})"
    command = entry.get("command")
    executable = isinstance(command, str) and (
        bool(shutil.which(command))
        or (Path(command).is_file() and os.access(command, os.X_OK))
    )
    if executable:
        return True, f"[✓] {label}: configured and launcher verified ({path})"
    return (
        False,
        f"[!] {label}: launcher command '{command}' not found or not executable ({path})",
    )


def _run_whoami(args: argparse.Namespace) -> int:
    """Print the stored key's claims (never calls the engine)."""
    from remember.login import whoami

    with _issuer_http() as http:
        return whoami(http=http, machine=args.json)


def _run_switch(args: argparse.Namespace) -> int:
    """Set the stored default project after resolving it once."""
    from remember.login import switch

    with _issuer_http() as http:
        switch(project=args.project, http=http, machine=args.json)
    return 0


def _run_ops(args: argparse.Namespace) -> int:
    """Compose bounded local inspection, route release, or one-row replay."""
    if args.ops_command == "graph-catalog":
        return _run_graph_catalog_ensure()
    try:
        from rememberstack.model import ProcessingLane
        from rememberstack.model import WorkLedgerError

        operations_type = import_module(
            "rememberstack.profiles.selfhost_operations"
        ).SelfHostOperations
    except ModuleNotFoundError as error:
        raise _missing_ops_dependencies() from error

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
        if args.ops_command == "forget-reapply":
            reapplied = operations.forget_reapply(deployment_id=args.deployment)
            print(json.dumps({"reapplied": [str(item) for item in reapplied]}))
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
        raise AssertionError(f"unhandled ops command {args.ops_command!r}")
    except WorkLedgerError as error:
        raise failure(
            code="work_ledger_error",
            detail=str(error),
            action="Check the deployment and processing row with `remember ops inspect`.",
        ) from error
    finally:
        operations.close()


def _missing_ops_dependencies() -> CommandFailure:
    """Explain where to run an operator command whose server imports are missing."""
    return failure(
        code="command_unavailable",
        detail="Ops commands require server container environment dependencies.",
        action="Run the command inside the ghcr.io/writeitai/remember-stack engine container.",
    )


def _run_graph_catalog_ensure() -> int:
    """Inspect and, when needed, replay the PostgreSQL live-graph metadata."""
    try:
        from sqlalchemy import create_engine

        from rememberstack.spine.graph_catalog import ensure_graph_catalog
        from rememberstack.spine.settings import load_database_settings
    except ModuleNotFoundError as error:
        raise _missing_ops_dependencies() from error

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
                },
                sort_keys=True,
            )
        )
        return 0
    except RuntimeError as error:
        raise failure(
            code="graph_catalog_error",
            detail="The graph catalog could not be ensured.",
            action="Check database access and migrations, then rerun graph catalog ensure.",
        ) from error
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
    except ModuleNotFoundError as error:
        raise _missing_ops_dependencies() from error

    engine = create_engine(load_database_settings().sqlalchemy_url())
    try:
        try:
            spine_id = spine_deployment_id(engine=engine)
        except CostExportConfigError as error:
            raise usage_failure(str(error)) from error
        if spine_id != deployment_id:
            raise usage_failure("--deployment does not match the spine deployment.")
        reader = SqlCostExportReader(engine=engine)
        page = reader.read_page(
            deployment_id=deployment_id, cursor=args.cursor, limit=args.limit
        )
    except CostExportCursorError as error:
        raise usage_failure(str(error)) from error
    finally:
        engine.dispose()
    print(page.model_dump_json())
    return 0


def _run_query(args: argparse.Namespace) -> int:
    """Run a query command through the typed remote SDK."""
    with _cli_memory_client(args) as client:
        return _run_open_query(client=client, args=args)


def _run_readiness(args: argparse.Namespace) -> int:
    """Inspect exact version readiness or wait using the SDK's polling rules."""
    if not math.isfinite(args.poll_interval) or args.poll_interval <= 0:
        raise usage_failure("--poll-interval must be a finite positive number.")
    with _cli_memory_client(args) as client:
        if args.wait:
            timeout = args.timeout if args.timeout is not None else 1800.0
            try:
                report = client.wait_for_readiness(
                    args.version_ids,
                    timeout=timeout,
                    poll_interval=args.poll_interval,
                    require_p3=args.require_p3,
                )
            except PipelineDeadLettered as error:
                raise failure(
                    code="pipeline_dead_lettered",
                    detail=str(error),
                    action="Ask the deployment operator to inspect the dead-lettered stage.",
                ) from error
            except _PipelineReadinessTimeout as error:
                print(error.report.model_dump_json())
                raise failure(
                    code="readiness_timeout",
                    detail=f"Versions did not become ready after {timeout:g} seconds.",
                    action="Inspect the last report for pending work before waiting again.",
                ) from error
        else:
            report = client.pipeline_readiness(
                version_ids=tuple(args.version_ids),
                require=ReadinessRequirements(
                    pipeline=True, p1=True, live_graph=True, p3=args.require_p3
                ),
            )
    print(report.model_dump_json())
    return 0


def _run_deployment(args: argparse.Namespace) -> int:
    with _cli_memory_client(args) as client:
        print(client.deployment_build_info().model_dump_json())
    return 0


def _run_processing_layers(args: argparse.Namespace) -> int:
    with _cli_memory_client(args) as client:
        print(client.processing_layers().model_dump_json())
    return 0


def _run_resolve(args: argparse.Namespace) -> int:
    with _cli_memory_client(args) as client:
        result = client.resolve(
            name=args.name, context_entity_ids=tuple(args.context_entity_id)
        )
    print(result.model_dump_json())
    return 0


def _run_search(args: argparse.Namespace) -> int:
    with _cli_memory_client(args) as client:
        search = (
            client.search_claims
            if args.search_command == "claims"
            else client.search_chunks
        )
        result = search(
            query=args.query,
            k=args.k,
            channel=args.channel,
            documents=args.documents,
            time=args.time,
        )
    print(result.model_dump_json())
    return 0


def _run_lookup(args: argparse.Namespace) -> int:
    with _cli_memory_client(args) as client:
        if args.lookup_command == "observations":
            result = client.lookup_observations(
                entity_id=args.entity_id,
                property_query=args.property_query,
                k=args.k,
                limit=args.limit,
                cursor=args.cursor,
            )
        else:
            result = client.lookup_relations(
                subject_entity_id=args.subject_entity_id,
                predicate=args.predicate,
                object_entity_id=args.object_entity_id,
                valid_at=args.valid_at,
                limit=args.limit,
                cursor=args.cursor,
            )
    print(result.model_dump_json())
    return 0


def _run_graph(args: argparse.Namespace) -> int:
    with _cli_memory_client(args) as client:
        if args.graph_command == "neighborhood":
            result = client.graph_neighborhood(
                entity_id=args.entity_id,
                hops=args.hops,
                predicates=tuple(args.predicate),
                valid_at=args.valid_at,
                believed_at=args.believed_at,
                limit=args.limit,
                cursor=args.cursor,
                include_paths=args.include_paths,
            )
        elif args.graph_command == "path":
            result = client.graph_path(
                from_entity_id=args.from_entity_id,
                to_entity_id=args.to_entity_id,
                max_hops=args.max_hops,
                predicates=tuple(args.predicate),
                valid_at=args.valid_at,
                believed_at=args.believed_at,
            )
        else:
            result = client.graph_citation_path(
                from_doc_id=args.from_doc_id,
                to_doc_id=args.to_doc_id,
                max_hops=args.max_hops,
            )
    print(result.model_dump_json())
    return 0


def _run_relation(args: argparse.Namespace) -> int:
    with _cli_memory_client(args) as client:
        if args.relation_command == "hydrate":
            result = client.hydrate_relation(relation_id=args.relation_id)
        else:
            result = client.transcript_relation(relation_id=args.relation_id)
    print(result.model_dump_json())
    return 0


def _run_operations(args: argparse.Namespace) -> int:
    """List or run the closed assured-operation catalog."""
    with _cli_memory_client(args) as client:
        if args.operation_command == "list":
            descriptors = client.list_operations()
            if args.json:
                print(
                    json.dumps([item.model_dump(mode="json") for item in descriptors])
                )
            else:
                for descriptor in descriptors:
                    print(descriptor.model_dump_json())
            return 0
        try:
            arguments = dict(_split_operation_arg(pair) for pair in args.arg)
        except ValueError as error:
            raise usage_failure(str(error)) from error
        print(
            client.run_operation(
                name=args.operation, arguments=arguments, layers=args.layers
            ).model_dump_json()
        )
        return 0


def _tag_documents(args: argparse.Namespace) -> DocumentSearchFilters | None:
    """The D156 ``documents`` filter of ``--tag``/``--any-tag``/``--not-tag``, if any."""
    if not (args.tag or args.any_tag or args.not_tag):
        return None
    return DocumentSearchFilters(
        tags=DocumentTagFilter(
            all=tuple(args.tag), any=tuple(args.any_tag), none=tuple(args.not_tag)
        )
    )


def _run_open_query(*, client: MemoryClient, args: argparse.Namespace) -> int:
    """Additive open-query CLI commands over the same SDK."""
    command = args.query_command
    try:
        if command == "text":
            query_text = args.query_text
            try:
                documents = _tag_documents(args)
            except ValidationError as error:
                problems = "; ".join(item["msg"] for item in error.errors())
                raise usage_failure(problems) from error
            if getattr(args, "combined", False):
                ans = client.combined_context(
                    query=query_text, layers=args.layers, documents=documents
                )
                print(ans.model_dump_json(indent=2))
            elif args.layers is not None:
                arguments: dict[str, object] = {"query": query_text}
                if documents is not None:
                    arguments["documents"] = documents.model_dump(
                        mode="json", exclude_defaults=True
                    )
                narrowed = client.run_operation(
                    name="facts_context", arguments=arguments, layers=args.layers
                )
                print(narrowed.model_dump_json(indent=2))
            else:
                fact = client.facts_context(query=query_text, documents=documents)
                print(fact.model_dump_json(indent=2))
            return 0
        if command == "sql":
            print(
                json.dumps(
                    client.query_sql(
                        sql=args.statement,
                        parameters=_json_list(args.parameters),
                        max_rows=args.max_rows,
                        layers=args.layers,
                    ),
                    default=str,
                )
            )
            return 0
        if command == "explain-sql":
            print(
                json.dumps(
                    client.explain_sql(
                        sql=args.statement,
                        parameters=_json_list(args.parameters),
                        layers=args.layers,
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
                        layers=args.layers,
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
        raise usage_failure(str(error)) from error
    raise usage_failure("Unknown query command.")


def _json_list(raw: str | None) -> list[object]:
    """Parse an optional JSON array of bound parameters."""
    if not raw:
        return []
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("parameters must be a JSON array")
    return value


def _run_ingest(args: argparse.Namespace) -> int:
    """Send one local file to the deployment."""
    try:
        with _cli_memory_client(args) as client:
            result = client.ingest(
                args.file,
                filename=args.filename,
                mime=args.mime,
                title=args.title,
                source_kind=args.source_kind,
                source_ref=args.source_ref,
                source_modified_at=args.source_modified_at,
                versioning_mode=args.versioning_mode,
                source_version_ref=args.source_version_ref,
                version_key=args.version_key,
                effective_from=args.effective_from,
                effective_until=args.effective_until,
                tags=args.tag,
                allow_forgotten=args.allow_forgotten,
            )
    except OSError as error:
        raise failure(
            code="file_unreadable",
            detail=f"Could not read {args.file}: {error}",
            action="Check that the file exists and is readable, then rerun.",
        ) from error
    except ValueError as error:
        raise usage_failure(str(error)) from error
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
    """Read and manage the deployment's documents and their versions."""
    if args.documents_command == "forget" and not (
        args.yes or (not args.json and _confirmed_forget(doc_id=args.doc_id))
    ):
        raise failure(
            code="confirmation_required",
            detail="Not confirmed; nothing was forgotten.",
            action="Get the user's confirmation, then rerun with --yes.",
        )
    if (
        args.documents_command == "search"
        and args.query is not None
        and args.cursor is not None
    ):
        raise usage_failure("Cursor pages filter-only searches; drop query or cursor.")
    tag_filter = None
    if args.documents_command == "search" and (
        args.tag or args.any_tag or args.not_tag
    ):
        try:
            tag_filter = DocumentTagFilter(
                all=tuple(args.tag), any=tuple(args.any_tag), none=tuple(args.not_tag)
            )
        except ValidationError as error:
            problems = "; ".join(item["msg"] for item in error.errors())
            raise usage_failure(problems) from error
    with _cli_memory_client(args) as client:
        if args.documents_command == "search":
            # Ranked searches take k; browse lists take limit + cursor (D154).
            page = client.search_documents(
                args.query,
                filters=DocumentSearchFilters(
                    family=tuple(args.family),
                    authors=tuple(args.author),
                    recipients=tuple(args.recipient),
                    created_from=args.created_from,
                    created_to=args.created_to,
                    modified_from=args.modified_from,
                    modified_to=args.modified_to,
                    language=args.language,
                    thread_ref=args.thread_ref,
                    doc_ids=tuple(args.doc_id),
                    tags=tag_filter,
                    include_archived=args.include_archived,
                ),
                versions=args.versions,
                **(
                    {"k": args.limit}
                    if args.query
                    else {"limit": args.limit, "cursor": args.cursor}
                ),
            )
            print(page.model_dump_json())
            if page.next_cursor is not None:
                print(f"# next_cursor={page.next_cursor}", file=sys.stderr)
            return 0
        if args.documents_command == "list":
            page = client.list_documents(
                limit=args.limit,
                cursor=args.cursor,
                status=args.status,
                include_archived=args.include_archived,
            )
            print(page.model_dump_json())
            if page.next_cursor is not None:
                print(f"# next_cursor={page.next_cursor}", file=sys.stderr)
            return 0
        if args.documents_command == "tag":
            if args.set is not None and (args.add or args.remove):
                raise usage_failure("--set excludes --add and --remove.")
            tags = client.update_document_tags(
                doc_id=args.doc_id,
                add=args.add,
                remove=args.remove,
                set=None if args.set is None else [tag for tag in args.set if tag],
            )
            print(tags.model_dump_json())
            return 0
        if args.documents_command == "forget":
            print(client.forget_document(doc_id=args.doc_id).model_dump_json())
            return 0
        if args.documents_command == "section-history":
            history = client.section_history(
                doc_id=args.doc_id,
                section_key=args.section_key,
                time=args.time,
                limit=args.limit,
                cursor=args.cursor,
            )
            print(history.model_dump_json())
            if history.next_cursor is not None:
                print(f"# next_cursor={history.next_cursor}", file=sys.stderr)
            return 0
        if args.documents_command == "references":
            if args.section_key is not None and args.doc_id is None:
                raise usage_failure("--section-key requires --doc-id.")
            references = client.document_references(
                doc_id=args.doc_id,
                chunk_id=args.chunk_id,
                section_key=args.section_key,
                direction=args.direction,
                kinds=args.kind,
                time=args.time,
                limit=args.limit,
                cursor=args.cursor,
            )
            print(references.model_dump_json())
            if references.next_cursor is not None:
                print(f"# next_cursor={references.next_cursor}", file=sys.stderr)
            return 0
        if args.documents_command == "reference-generations":
            print(
                client.reference_generations(
                    doc_id=args.doc_id, version_id=args.version_id
                ).model_dump_json()
            )
            return 0
        if args.documents_command == "set-references":
            print(
                client.set_references(
                    doc_id=args.doc_id,
                    version_id=args.version_id,
                    references=_read_references(args.file),
                ).model_dump_json()
            )
            return 0
        if args.documents_command == "set-effective-periods":
            print(
                client.set_effective_periods(
                    doc_id=args.doc_id, version_id=args.version_id, periods=args.periods
                ).model_dump_json()
            )
            return 0
        if args.documents_command == "clear-effective-periods":
            print(client.clear_effective_time(doc_id=args.doc_id).model_dump_json())
            return 0
        if args.documents_command == "archive":
            result = client.archive_document(doc_id=args.doc_id)
        else:
            result = client.unarchive_document(doc_id=args.doc_id)
    print(result.model_dump_json())
    return 0


def _read_references(path: Path) -> list[ReferenceInput]:
    """Read the complete supplied reference set, one JSON object per line."""
    references: list[ReferenceInput] = []
    try:
        with path.open("rb") as source:
            for number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    references.append(ReferenceInput.model_validate_json(line))
                except ValidationError as error:
                    raise usage_failure(
                        f"Invalid reference on line {number} of {path}."
                    ) from error
    except OSError as error:
        raise failure(
            code="file_unreadable",
            detail=f"Could not read {path}: {error}",
            action="Check that the file exists and is readable, then rerun.",
        ) from error
    return references


def _confirmed_forget(*, doc_id: UUID) -> bool:
    """Ask on the terminal before forgetting; no answer is a refusal."""
    sys.stderr.write(
        f"Forget document {doc_id}? It and everything derived only from it are"
        " erased for good; there is no undo. [y/N] "
    )
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def _run_forget(args: argparse.Namespace) -> int:
    """List the deployment's forget records or show one (D159)."""
    with _cli_memory_client(args) as client:
        if args.forget_command == "list":
            print(client.list_forgets().model_dump_json())
            return 0
        print(client.get_forget(forget_id=args.forget_id).model_dump_json())
    return 0


def _run_tags(args: argparse.Namespace) -> int:
    """List, create, update or delete the deployment's tags (D156)."""
    with _cli_memory_client(args) as client:
        if args.tags_command == "list":
            print(client.list_tags(query=args.query).model_dump_json())
            return 0
        if args.tags_command == "create":
            tag = client.create_tag(name=args.name, description=args.description)
        elif args.tags_command == "update":
            if args.name is None and args.description is None:
                raise usage_failure("Give --name, --description or both.")
            tag = client.update_tag(
                tag=args.tag, name=args.name, description=args.description
            )
        else:
            tag = client.delete_tag(tag=args.tag)
    print(tag.model_dump_json())
    return 0


def _run_connectors(args: argparse.Namespace) -> int:
    """Manage connector configuration on the deployment API."""
    with _cli_memory_client(args) as client:
        if args.connector_command == "list":
            connectors = client.connectors()
            if args.json:
                print(json.dumps([item.model_dump(mode="json") for item in connectors]))
            else:
                for connector in connectors:
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
                raise usage_failure(str(error)) from error
            result = client.add_connector(connector=connector)
        elif args.connector_command == "pause":
            result = client.pause_connector(connector_id=args.connector_id)
        else:
            result = client.connector_status(connector_id=args.connector_id)
    print(result.model_dump_json())
    return 0


def _tool_version_mismatches(served: dict[str, int]) -> list[str]:
    """Catalogue tools the engine serves at another version (hidden by `remember mcp`)."""
    from remember.mcp_tools import memory_tools

    lines: list[str] = []
    for definition in memory_tools():
        version = served.get(definition.name)
        if version is None or version == definition.tool_version:
            continue
        newer = "remember" if version > definition.tool_version else "the engine"
        lines.append(
            f"MCP tool {definition.name!r}: this remember has version"
            f" {definition.tool_version}, the engine serves {version}; `remember mcp`"
            f" leaves it out until you upgrade {newer}"
        )
    return lines


def _run_mcp(args: argparse.Namespace) -> int:
    """Serve memory tools over MCP: engine mode (stdio or HTTP) or bridge mode.

    Bridge mode is chosen by a remote MCP URL (``--remote-url``,
    ``REMEMBER_MCP_URL``, or the signed key's issuer's
    ``remember_mcp_endpoint`` when no engine URL is given); an explicit engine
    URL (``--api-url``, ``REMEMBER_API_URL``) always means engine mode.
    """
    from remember.connection import DEFAULT_API_URL
    from remember.connection import resolve_connection

    connection = resolve_connection(
        api_key=args.api_key,
        api_url=args.api_url,
        project=args.project,
        mcp_url=args.remote_url,
    )
    explicit_engine = connection.api_url_source in ("explicit", "environment")
    if connection.mcp_url and explicit_engine:
        raise usage_failure(
            "give either an engine URL (--api-url / REMEMBER_API_URL) or a remote"
            " MCP URL (--remote-url / REMEMBER_MCP_URL), not both"
        )
    if args.transport == "http":
        if connection.mcp_url:
            raise usage_failure(
                "--transport http serves an engine; an HTTP client should connect"
                f" to the remote MCP URL {connection.mcp_url} directly"
            )
        if args.api_key or args.project:
            raise usage_failure(
                "--transport http holds no key: each caller's Authorization"
                " header is forwarded to the engine. Drop --api-key/--project"
            )
        if connection.api_url is None and connection.claims is not None:
            raise usage_failure(
                "--transport http needs the engine URL: pass --api-url or set"
                " REMEMBER_API_URL"
            )
        from remember.mcp_http import serve_http

        return serve_http(
            bind=args.bind,
            engine_url=connection.api_url or DEFAULT_API_URL,
            read_only=bool(args.read_only),
            layers=args.layers,
        )
    remote_url = connection.mcp_url
    if remote_url is None and not explicit_engine and connection.claims is not None:
        with _issuer_http() as http:
            remote_url = fetch_issuer_metadata(
                connection.claims.iss, http=http
            ).remember_mcp_endpoint
    if remote_url is not None:
        if args.layers is not None:
            raise usage_failure(
                "--layers narrows an engine-mode session; a remote MCP server"
                " applies its own deployment's layers"
            )
        return _run_mcp_bridge(args, connection=connection, remote_url=remote_url)
    from remember.mcp_engine import EngineMcpServer
    from remember.mcp_engine import serve_stdio

    with _cli_memory_client(args) as client:
        return serve_stdio(
            server=EngineMcpServer(
                client=client,
                read_only=bool(args.read_only),
                path_ingest=True,
                layers=args.layers,
            )
        )


def _run_mcp_bridge(
    args: argparse.Namespace, *, connection: Connection, remote_url: str
) -> int:
    """Relay stdio to ``remote_url`` with the resolved key (D136 §5.3)."""
    from remember.mcp_bridge import McpBridge

    if args.project:
        raise usage_failure(
            "--project applies to engine mode; through a remote MCP endpoint the"
            " agent names the project per call (the `project` tool argument)"
        )
    if connection.key is None:
        raise failure(
            code="missing_credentials",
            detail="Bridge mode needs an API key.",
            action="Run `remember login`, or set REMEMBER_API_KEY.",
            exit_code=2,
        )
    if connection.key_source == "file":
        issuer = (
            connection.claims.iss
            if connection.claims is not None
            else (connection.stored.issuer if connection.stored else None)
        )
        advertised = None
        if issuer is not None:
            with _issuer_http() as http:
                advertised = fetch_issuer_metadata(
                    issuer, http=http
                ).remember_mcp_endpoint
        if advertised is None or not same_origin(remote_url, advertised):
            raise StoredKeyRefused(
                detail=(
                    f"the stored key is not sent to {remote_url}: it goes only to"
                    " the MCP endpoint its issuer advertises. Pass the key"
                    " explicitly (--api-key or REMEMBER_API_KEY) to use it there"
                )
            )
    _warn_if_expiring()
    bridge = McpBridge(
        url=remote_url,
        key=connection.key.get_secret_value(),
        read_only=bool(args.read_only),
    )
    return bridge.run()


def _layer_list(value: str) -> tuple[str, ...]:
    """Parse a comma-separated D143 ``--layers`` value (sources, claims, facts)."""
    layers = tuple(part.strip() for part in value.split(",") if part.strip())
    unknown = sorted(set(layers) - {"sources", "claims", "facts"})
    if not layers or unknown:
        raise argparse.ArgumentTypeError(
            "--layers takes a comma-separated subset of sources,claims,facts"
        )
    return layers


def operations_list(*, client: httpx.Client) -> int:
    """Print operations from an injected client (the parity-testable seam)."""
    try:
        for descriptor in MemoryClient(client=client).list_operations():
            print(descriptor.model_dump_json())
    except MemoryApiError as error:
        report_error(error=map_error(error), machine=False)
        return 1
    return 0


def operations_run(*, client: httpx.Client, name: str, arg_pairs: list[str]) -> int:
    """Run one operation through an injected client and print its response."""
    try:
        arguments = dict(_split_operation_arg(pair) for pair in arg_pairs)
    except ValueError as error:
        report_error(error=usage_failure(str(error)).error, machine=False)
        return 2
    try:
        result = MemoryClient(client=client).run_operation(
            name=name, arguments=arguments
        )
    except MemoryApiError as error:
        report_error(error=map_error(error), machine=False)
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
        timeout=_ClientRequestSettings.model_validate(
            {} if getattr(args, "timeout", None) is None else {"timeout": args.timeout}
        ).timeout,
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
    from remember.login import login

    try:
        issuer = resolve_connection(issuer=args.issuer).issuer
    except (CredentialError, ValueError):
        # An unreadable file is what `remember login` replaces.
        from remember.connection import environment_issuer

        issuer = args.issuer or environment_issuer()
    issuer = issuer or DEFAULT_ISSUER
    with _issuer_http() as http:
        stored = login(
            issuer=issuer, http=http, progress=sys.stderr if args.json else None
        )
    expires = stored.expires_at.isoformat() if stored.expires_at else None
    if args.json:
        print(
            json.dumps(
                {
                    "issuer": stored.issuer,
                    "key_id": stored.key_id,
                    "expires_at": expires,
                }
            )
        )
    else:
        print(
            f"Signed in to {stored.issuer}. Key {stored.key_id or ''} expires {expires or 'unknown'}."
        )
    return 0


def _run_logout(args: argparse.Namespace) -> int:
    """Revoke the stored key at its issuer, then remove the file."""
    from remember.login import logout

    with _issuer_http() as http:
        removed = logout(http=http)
    if args.json:
        print(json.dumps({"removed": removed}))
    return 0


def _run_mount(args: argparse.Namespace) -> int:
    """Mount the managed corpus through a one-time stdin ticket (D155)."""
    from remember.connection import resolve_connection
    from remember.mount import mount

    _warn_if_expiring()
    connection = resolve_connection(api_key=args.api_key, project=args.project)
    with _issuer_http() as http:
        code = mount(args.directory, connection=connection, http=http)
    if code:
        raise failure(
            code="mount_failed",
            detail=f"mountable exited with status {code}.",
            action="Check the mount diagnostics and local mountpoint before retrying.",
            exit_code=code,
        )
    return 0


def _run_unmount(args: argparse.Namespace) -> int:
    """Unmount locally without obtaining another ticket."""
    from remember.mount import unmount

    code = unmount(args.directory, machine=args.json)
    if code:
        raise failure(
            code="unmount_failed",
            detail=f"mountable exited with status {code}.",
            action="Check that the directory is a mountpoint and rerun unmount.",
            exit_code=code,
        )
    if args.json:
        print(json.dumps({"unmounted": True}))
    return 0


def _split_arg(pair: str) -> tuple[str, str]:
    """Split one ``key=value`` argument, or raise a clear error."""
    key, separator, value = pair.partition("=")
    if not separator or not key:
        raise ValueError("Expected an argument in key=value form.")
    return key, value


def _split_operation_arg(pair: str) -> tuple[str, object]:
    """Parse a CLI operation value as JSON, retaining ordinary bare strings."""
    key, raw = _split_arg(pair)
    try:
        return key, json.loads(raw)
    except json.JSONDecodeError:
        return key, raw


def _add_tag_flags(parser: argparse.ArgumentParser) -> None:
    """The D156 tag selector flags of every query command with a document filter."""
    for flag, help_text in (
        ("--tag", "the document has this registered tag (repeatable; all must hold)"),
        ("--any-tag", "the document has at least one of these tags (repeatable)"),
        ("--not-tag", "the document has none of these tags (repeatable)"),
    ):
        parser.add_argument(flag, action="append", default=[], help=help_text)


class _ParseFailure(ValueError):
    """A credential-safe command grammar diagnostic."""


class _CliParser(argparse.ArgumentParser):
    """Expose the output option at each command level and report parse failures safely."""

    def __init__(self, *, add_output_flag: bool = True, **kwargs: object) -> None:
        """Set up one level of the command grammar."""
        super().__init__(**kwargs)  # type: ignore[arg-type]
        if add_output_flag:
            self.add_argument(
                "--json",
                action="store_true",
                default=argparse.SUPPRESS,
                help="emit machine-readable results and errors",
            )

    def error(self, message: str) -> NoReturn:
        """Identify the affected argument without echoing the supplied value."""
        if message.startswith("the following arguments are required:"):
            raise _ParseFailure(f"{message}. See {self.prog} --help.")
        label, _, _ = message.partition(":")
        cause = (
            label + " is invalid"
            if label.startswith("argument ")
            else "Invalid arguments"
        )
        raise _ParseFailure(f"{cause} for {self.prog}. See {self.prog} --help.")


def _build_parser(
    *, include_internal_ops: bool = False, machine: bool = False
) -> argparse.ArgumentParser:
    """Build the client-first command grammar."""
    parser = _CliParser(
        prog="remember", description="Remember platform command-line interface"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=json.dumps({"version": __version__})
        if machine
        else f"remember {__version__}",
    )
    commands = parser.add_subparsers(dest="command")
    client_flags = _CliParser(add_help=False, add_output_flag=False)
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

    client_flags.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="HTTP request timeout in seconds (overrides REMEMBER_TIMEOUT; default 300)",
    )
    parse_read_time = TypeAdapter(ReadTime).validate_json

    if include_internal_ops:
        ops = commands.add_parser(
            "ops", help="operator commands against this deployment's database"
        )
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
        forget_reapply = ops_commands.add_parser(
            "forget-reapply",
            help="re-run every forget's sweeps after restoring a store from backup",
        )
        forget_reapply.add_argument("--deployment", type=UUID, required=True)
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
    run_operation.add_argument(
        "--layers",
        type=_layer_list,
        default=None,
        help="comma-separated processing layers to read (sources,claims,facts; D143)",
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
    text_p.add_argument(
        "--layers",
        type=_layer_list,
        default=None,
        help="comma-separated processing layers to read (sources,claims,facts; D143)",
    )
    _add_tag_flags(text_p)
    sql = query_commands.add_parser(
        "sql", parents=[client_flags], help="run one sandboxed SQL statement"
    )
    sql.add_argument("statement", help="SQL text")
    sql.add_argument("--parameters", help="JSON array of positional bound parameters")
    sql.add_argument("--max-rows", type=int)
    sql.add_argument(
        "--layers",
        type=_layer_list,
        default=None,
        help="comma-separated processing layers to read (sources,claims,facts; D143)",
    )
    explain_sql = query_commands.add_parser(
        "explain-sql",
        parents=[client_flags],
        help="EXPLAIN one SQL statement without executing it",
    )
    explain_sql.add_argument("statement", help="SQL text")
    explain_sql.add_argument(
        "--parameters", help="JSON array of positional bound parameters"
    )
    explain_sql.add_argument(
        "--layers",
        type=_layer_list,
        default=None,
        help="comma-separated processing layers to read (sources,claims,facts; D143)",
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
    run_saved.add_argument(
        "--layers",
        type=_layer_list,
        default=None,
        help="comma-separated processing layers to read (sources,claims,facts; D143)",
    )
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
        "ingest", parents=[client_flags], help="send a file to the memory"
    )
    ingest.add_argument("file", type=Path)
    ingest.add_argument(
        "--filename", help="name to store the file under (default: its own name)"
    )
    ingest.add_argument("--mime")
    ingest.add_argument("--title")
    ingest.add_argument("--source-kind")
    ingest.add_argument("--source-ref")
    ingest.add_argument("--source-modified-at", type=datetime.fromisoformat)
    ingest.add_argument(
        "--versioning-mode", choices=("snapshot", "living"), default="snapshot"
    )
    ingest.add_argument("--source-version-ref")
    ingest.add_argument(
        "--version-key",
        help="your immutable name for this version, unique within the document",
    )
    ingest.add_argument(
        "--effective-from",
        type=datetime.fromisoformat,
        help="UTC start (inclusive) of the period this version is in force",
    )
    ingest.add_argument(
        "--effective-until",
        type=datetime.fromisoformat,
        help="UTC end (exclusive) of that period; omit for until the next start",
    )
    ingest.add_argument(
        "--tag",
        action="append",
        default=[],
        help="a registered tag (name or id) to add to the document (repeatable)",
    )
    ingest.add_argument(
        "--allow-forgotten",
        action="store_true",
        help=(
            "add back content of a forgotten document (once its forget is no"
            " longer erasing); it returns under a new doc_id"
        ),
    )

    readiness = commands.add_parser(
        "readiness",
        parents=[client_flags],
        help="check whether ingested versions are ready to query",
        epilog="With --wait, --timeout also sets the polling deadline (default 1800 seconds).",
    )
    readiness.add_argument(
        "version_ids",
        nargs="+",
        type=UUID,
        metavar="VERSION_ID",
        help="version UUID from ingest",
    )
    readiness.add_argument(
        "--wait", action="store_true", help="poll until all versions are ready"
    )
    readiness.add_argument(
        "--poll-interval",
        type=float,
        default=15.0,
        metavar="SECONDS",
        help="seconds between checks with --wait (default 15)",
    )
    readiness.add_argument(
        "--require-p3",
        action="store_true",
        help="also require publication to the corpus",
    )

    commands.add_parser(
        "deployment",
        parents=[client_flags],
        help="show the deployment build and model bindings",
    )
    commands.add_parser(
        "processing-layers",
        parents=[client_flags],
        help="show which processing layers the deployment serves",
    )

    resolve = commands.add_parser(
        "resolve",
        parents=[client_flags],
        help="resolve a name to its matching entities",
    )
    resolve.add_argument("name", help="name to resolve")
    resolve.add_argument(
        "--context-entity-id",
        type=UUID,
        action="append",
        default=[],
        help="entity UUID to use as context (repeatable, up to 8)",
    )

    search = commands.add_parser("search", help="search claims or source chunks")
    search_commands = search.add_subparsers(dest="search_command", required=True)
    for name, help_text in (
        ("claims", "search claims"),
        ("chunks", "search source passages"),
    ):
        search_kind = search_commands.add_parser(
            name, parents=[client_flags], help=help_text
        )
        search_kind.add_argument("query", help="search text")
        search_kind.add_argument(
            "--k", type=int, default=10, help="maximum results (1-400, default 10)"
        )
        search_kind.add_argument(
            "--channel", choices=("semantic", "bm25"), default="semantic"
        )
        search_kind.add_argument(
            "--documents",
            type=DocumentSearchFilters.model_validate_json,
            metavar="JSON",
            help="document filters as a JSON object",
        )
        search_kind.add_argument(
            "--time",
            type=parse_read_time,
            metavar="JSON",
            help="time scope (default current)",
        )

    lookup = commands.add_parser("lookup", help="look up relations or observations")
    lookup_commands = lookup.add_subparsers(dest="lookup_command", required=True)
    relations = lookup_commands.add_parser(
        "relations",
        parents=[client_flags],
        help="page relations matching an optional pattern",
    )
    relations.add_argument("--subject-entity-id", type=UUID)
    relations.add_argument("--predicate")
    relations.add_argument("--object-entity-id", type=UUID)
    relations.add_argument(
        "--valid-at",
        type=datetime.fromisoformat,
        help="an ISO 8601 instant with a timezone",
    )
    relations.add_argument(
        "--limit", type=int, default=50, help="relations per page (1-200, default 50)"
    )
    relations.add_argument("--cursor", help="pass a previous page's next_cursor")
    observations = lookup_commands.add_parser(
        "observations",
        parents=[client_flags],
        help="browse or rank observations for one entity",
    )
    observations.add_argument("entity_id", type=UUID)
    observation_scope = observations.add_mutually_exclusive_group()
    observation_scope.add_argument(
        "--property-query", help="rank observations by this text"
    )
    observation_scope.add_argument(
        "--cursor", help="pass a previous browse page's next_cursor"
    )
    observations.add_argument(
        "--k", type=int, default=10, help="ranked results (1-400, default 10)"
    )
    observations.add_argument(
        "--limit",
        type=int,
        default=50,
        help="observations per browse page (1-200, default 50)",
    )

    graph = commands.add_parser("graph", help="traverse the entity or citation graph")
    graph_commands = graph.add_subparsers(dest="graph_command", required=True)
    neighborhood = graph_commands.add_parser(
        "neighborhood",
        parents=[client_flags],
        help="read a bounded neighborhood around one entity",
    )
    neighborhood.add_argument("entity_id", type=UUID)
    neighborhood.add_argument(
        "--hops", type=int, default=2, help="maximum hops (1-4, default 2)"
    )
    neighborhood.add_argument(
        "--limit", type=int, default=100, help="page size (1-500, default 100)"
    )
    neighborhood.add_argument("--cursor", help="pass a previous page's next_cursor")
    neighborhood.add_argument(
        "--include-paths",
        action="store_true",
        help="include paths to the returned neighbors",
    )
    graph_path = graph_commands.add_parser(
        "path", parents=[client_flags], help="find shortest paths between two entities"
    )
    graph_path.add_argument("from_entity_id", type=UUID)
    graph_path.add_argument("to_entity_id", type=UUID)
    graph_path.add_argument(
        "--max-hops", type=int, default=4, help="maximum hops (1-6, default 4)"
    )
    for traversal in (neighborhood, graph_path):
        traversal.add_argument(
            "--predicate",
            action="append",
            default=[],
            help="traverse only this predicate (repeatable)",
        )
        traversal.add_argument(
            "--valid-at",
            type=datetime.fromisoformat,
            help="when the facts held, as an ISO 8601 instant",
        )
        traversal.add_argument(
            "--believed-at",
            type=datetime.fromisoformat,
            help="when the engine believed the facts, as an ISO 8601 instant",
        )
    citation_path = graph_commands.add_parser(
        "citation-path",
        parents=[client_flags],
        help="follow directed citations between two documents",
    )
    citation_path.add_argument("from_doc_id", type=UUID)
    citation_path.add_argument("to_doc_id", type=UUID)
    citation_path.add_argument(
        "--max-hops", type=int, default=6, help="maximum hops (1-6, default 6)"
    )

    relation = commands.add_parser(
        "relation", help="read evidence or decisions for a relation"
    )
    relation_commands = relation.add_subparsers(dest="relation_command", required=True)
    hydrate = relation_commands.add_parser(
        "hydrate",
        parents=[client_flags],
        help="follow a relation's evidence to its source documents",
    )
    hydrate.add_argument("relation_id", type=UUID)
    transcript = relation_commands.add_parser(
        "transcript",
        parents=[client_flags],
        help="read a relation's decision transcript",
    )
    transcript.add_argument("relation_id", type=UUID)

    documents = commands.add_parser(
        "documents", help="read and manage documents and their versions"
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
    list_documents.add_argument(
        "--include-archived",
        action="store_true",
        help="also list archived documents, with archived_at",
    )
    search_documents = documents_commands.add_parser(
        "search",
        parents=[client_flags],
        help=(
            "find documents by name, metadata and content, as JSON (ranked with"
            " a query; newest first and paged without one)"
        ),
    )
    search_documents.add_argument(
        "query", nargs="?", help="words from the file name, title, path or text"
    )
    for flag, help_text in (
        ("--family", "keep this format family (repeatable)"),
        ("--author", "an author's name or address (repeatable; any matches)"),
        ("--recipient", "a recipient's name or address (repeatable; any matches)"),
    ):
        search_documents.add_argument(flag, action="append", default=[], help=help_text)
    for flag in ("--created-from", "--created-to", "--modified-from", "--modified-to"):
        search_documents.add_argument(
            flag,
            type=datetime.fromisoformat,
            help="an ISO 8601 instant with a timezone, inclusive",
        )
    search_documents.add_argument("--language")
    search_documents.add_argument(
        "--include-archived",
        action="store_true",
        help="also search archived documents (history, hidden by default)",
    )
    search_documents.add_argument("--thread-ref")
    search_documents.add_argument(
        "--doc-id", action="append", default=[], type=UUID, help="only this document"
    )
    _add_tag_flags(search_documents)
    search_documents.add_argument(
        "--versions",
        choices=("current", "all"),
        default="current",
        help="judge each document by its current version (default) or any version",
    )
    search_documents.add_argument(
        "--limit",
        type=int,
        default=50,
        help=(
            "browse page size without a query, or ranked top-k with a query"
            " (1-200, default 50)"
        ),
    )
    search_documents.add_argument(
        "--cursor", help="pass a previous page's next_cursor (only without a query)"
    )
    archive_document = documents_commands.add_parser(
        "archive",
        parents=[client_flags],
        help=(
            "archive a document: its claims stop counting and facts only it"
            " supported are closed; it stays readable as history"
        ),
    )
    archive_document.add_argument("doc_id", type=UUID)
    unarchive_document = documents_commands.add_parser(
        "unarchive",
        parents=[client_flags],
        help=(
            "undo an archive: the document's testimony is current again and the"
            " facts it closed reopen when supported"
        ),
    )
    unarchive_document.add_argument("doc_id", type=UUID)
    forget_document = documents_commands.add_parser(
        "forget",
        parents=[client_flags],
        help=(
            "erase a document and everything derived only from it, for good;"
            " prints the forget record"
        ),
    )
    forget_document.add_argument("doc_id", type=UUID)
    forget_document.add_argument(
        "--yes", action="store_true", help="do not ask for confirmation"
    )
    tag_document = documents_commands.add_parser(
        "tag",
        parents=[client_flags],
        help=(
            "add or remove a document's tags, or replace them with --set;"
            " prints the document's tags"
        ),
    )
    tag_document.add_argument("doc_id", type=UUID)
    tag_document.add_argument(
        "--add", action="append", default=[], help="a tag to add (repeatable)"
    )
    tag_document.add_argument(
        "--remove", action="append", default=[], help="a tag to remove (repeatable)"
    )
    tag_document.add_argument(
        "--set",
        action="append",
        help=(
            "the document's complete tag set (repeatable); excludes --add and"
            " --remove. --set '' clears the tags"
        ),
    )

    section_history = documents_commands.add_parser(
        "section-history",
        parents=[client_flags],
        help="follow a keyed section across a document's versions",
    )
    section_history.add_argument("doc_id", type=UUID)
    section_history.add_argument("section_key")
    section_history.add_argument(
        "--time",
        type=parse_read_time,
        metavar="JSON",
        help="time scope (default history)",
    )
    section_history.add_argument(
        "--limit", type=int, default=50, help="versions per page (1-200, default 50)"
    )
    section_history.add_argument("--cursor", help="pass a previous page's next_cursor")
    references = documents_commands.add_parser(
        "references",
        parents=[client_flags],
        help="read incoming or outgoing references for a document, section or chunk",
    )
    reference_source = references.add_mutually_exclusive_group(required=True)
    reference_source.add_argument("--doc-id", type=UUID)
    reference_source.add_argument("--chunk-id", type=UUID)
    references.add_argument(
        "--section-key",
        help="only this section and its descendants (requires --doc-id)",
    )
    references.add_argument(
        "--direction", choices=("incoming", "outgoing", "both"), default="both"
    )
    references.add_argument(
        "--kind",
        choices=get_args(ReferenceKind),
        action="append",
        help="reference kind to keep (repeatable)",
    )
    references.add_argument(
        "--time",
        type=parse_read_time,
        metavar="JSON",
        help="select source versions by time",
    )
    references.add_argument(
        "--limit", type=int, default=50, help="references per page (1-200, default 50)"
    )
    references.add_argument("--cursor", help="pass a previous page's next_cursor")
    reference_generations = documents_commands.add_parser(
        "reference-generations",
        parents=[client_flags],
        help="list a version's reference generations and their statuses",
    )
    reference_generations.add_argument("doc_id", type=UUID)
    reference_generations.add_argument("version_id", type=UUID)
    set_references = documents_commands.add_parser(
        "set-references",
        parents=[client_flags],
        help="replace a version's complete set of supplied references",
    )
    set_references.add_argument("doc_id", type=UUID)
    set_references.add_argument("version_id", type=UUID)
    set_references.add_argument(
        "file", type=Path, help="NDJSON file with one reference per line"
    )
    set_periods = documents_commands.add_parser(
        "set-effective-periods",
        parents=[client_flags],
        help="replace a version's complete set of effective periods",
    )
    set_periods.add_argument("doc_id", type=UUID)
    set_periods.add_argument("version_id", type=UUID)
    set_periods.add_argument(
        "--periods",
        required=True,
        type=TypeAdapter(tuple[EffectivePeriodInput, ...]).validate_json,
        metavar="JSON",
        help="array of objects with effective_from and optional effective_until",
    )
    clear_periods = documents_commands.add_parser(
        "clear-effective-periods",
        parents=[client_flags],
        help="retract all declared effective periods for a document",
    )
    clear_periods.add_argument("doc_id", type=UUID)

    forget = commands.add_parser("forget", help="follow document forgets")
    forget_commands = forget.add_subparsers(dest="forget_command", required=True)
    forget_commands.add_parser(
        "list", parents=[client_flags], help="every forget record, oldest first"
    )
    forget_status = forget_commands.add_parser(
        "status", parents=[client_flags], help="one forget record and its progress"
    )
    forget_status.add_argument("forget_id", type=UUID)

    tags = commands.add_parser("tags", help="list, create, update or delete tags")
    tags_commands = tags.add_subparsers(dest="tags_command", required=True)
    list_tags = tags_commands.add_parser(
        "list", parents=[client_flags], help="the registered tags, as JSON"
    )
    list_tags.add_argument(
        "--query", help="only tags whose name or description contains this"
    )
    create_tag = tags_commands.add_parser(
        "create", parents=[client_flags], help="register a tag before using it"
    )
    create_tag.add_argument("name", help="the tag name, such as project:autoboard")
    create_tag.add_argument(
        "--description",
        required=True,
        help="what the tag means and when to apply it, for agents",
    )
    update_tag = tags_commands.add_parser(
        "update",
        parents=[client_flags],
        help="rename or re-describe a tag; documents keep it",
    )
    update_tag.add_argument("tag", help="the tag's name or id")
    update_tag.add_argument("--name", help="the new name")
    update_tag.add_argument("--description", help="the new description")
    delete_tag = tags_commands.add_parser(
        "delete",
        parents=[client_flags],
        help="delete a tag and remove it from every document",
    )
    delete_tag.add_argument("tag", help="the tag's name or id")

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
        help="serve memory tools over MCP (stdio or HTTP), or bridge to a remote MCP URL",
    )
    mcp.add_argument(
        "--read-only",
        action="store_true",
        help="omit and refuse every tool that changes memory",
    )
    mcp.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio (default) or Streamable HTTP; http is engine mode only",
    )
    mcp.add_argument(
        "--bind",
        default="127.0.0.1:8765",
        help="loopback HOST:PORT for --transport http (default 127.0.0.1:8765)",
    )
    mcp.add_argument(
        "--layers",
        type=_layer_list,
        default=None,
        help=(
            "comma-separated processing layers this session may read"
            " (sources,claims,facts; default every layer the deployment serves);"
            " engine mode only (D143)"
        ),
    )
    mcp.add_argument(
        "--remote-url",
        default=None,
        help="relay stdio to this remote MCP URL (overrides REMEMBER_MCP_URL)",
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

    mount = commands.add_parser(
        "mount",
        help="mount a remember.dev project's corpus read-only (requires mountable)",
        description=(
            "Mount the remember.dev corpus read-only using the signed key's org "
            "and selected project. Requires memory:read on the project. Prints "
            "JSON events, including the session_id when mounted, and stays in the "
            "foreground until unmounted. Use 'remember unmount DIR' in another "
            "terminal to stop it. Install mountable with 'npm install --global "
            "--ignore-scripts mountable-cli' or 'uv tool install mountable'."
        ),
    )
    mount.add_argument("directory", metavar="DIR", help="existing mount directory")
    mount.add_argument(
        "--api-key", help="remember.dev key (overrides REMEMBER_API_KEY and stored key)"
    )
    mount.add_argument(
        "--project",
        help="project id or name (overrides REMEMBER_PROJECT and stored default)",
    )
    unmount = commands.add_parser(
        "unmount", help="unmount DIR using mountable; no new ticket or sign-in required"
    )
    unmount.add_argument("directory", metavar="DIR", help="directory to unmount")

    setup = commands.add_parser(
        "setup", help="bootstrap AI coding harnesses for Remember"
    )
    setup.add_argument(
        "--cloud",
        dest="target_env",
        action="store_const",
        const="cloud",
        default=None,
        help="configure for remember.dev (or the --issuer); signs in first if needed",
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
        "--api-url",
        default=None,
        help="the self-hosted engine URL (implies --self-hosted)",
    )
    setup.add_argument(
        "--mcp-url",
        default=None,
        help="a self-hosted `remember mcp --transport http` URL for agents that "
        "take remote entries (implies --self-hosted)",
    )
    setup.add_argument(
        "--issuer",
        default=None,
        help=f"key issuer URL with --cloud (default: REMEMBER_ISSUER, else {DEFAULT_ISSUER})",
    )
    setup.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="no browser sign-in: agents send the key from REMEMBER_API_KEY "
        "(default when CI is set)",
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
