"""The ``remember`` CLI: a dependency-light client plus optional local admin commands.

Query, ingest, connector management, and MCP all talk to the deployment HTTP
API. ``remember review``, ``remember budget``, and ``remember ops`` import the server extra
and connect to the spine.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from importlib import import_module
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
from pydantic import JsonValue

from rememberstack import __version__
from rememberstack.model.adjudication import ReviewDecisionError
from rememberstack.model.client import ConnectorCreate
from rememberstack.surfaces.remote_mcp import RemoteOperationMcpServer
from rememberstack.surfaces.remote_mcp import serve_mcp_stdio
from rememberstack.surfaces.sdk import MemoryApiError
from rememberstack.surfaces.sdk import MemoryClient

if TYPE_CHECKING:
    from rememberstack.spine.review import ReviewQueue
    from rememberstack.spine.work_ledger import WorkLedger

_MERGE_VERDICTS = ("merge", "not_merge")
_TRIAGE_VERDICTS = ("restore_support", "invalidate_fact", "uncertain")


def main(argv: list[str] | None = None) -> int:
    """The ``remember`` entry point; returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "review":
            return _run_review(args)
        if args.command == "budget":
            return _run_budget(args)
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
    except MemoryApiError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    parser.print_help()
    return 2


def _run_review(args: argparse.Namespace) -> int:
    """Compose the optional local ReviewQueue over the spine."""
    try:
        from sqlalchemy import create_engine

        from rememberstack.spine.review import ReviewQueue
        from rememberstack.spine.settings import load_database_settings
    except ModuleNotFoundError:
        print(
            "error: review commands require the 'rememberstack[server]' extra",
            file=sys.stderr,
        )
        return 1

    engine = create_engine(load_database_settings().sqlalchemy_url())
    try:
        queue = ReviewQueue(engine=engine)
        if args.review_command == "list":
            return _list(queue=queue, deployment_id=args.deployment)
        return _decide(
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
    """Compose the local WorkLedger and print configured budget state."""
    try:
        from sqlalchemy import create_engine

        from rememberstack.spine.settings import load_database_settings
        from rememberstack.spine.work_ledger import WorkLedger
        from rememberstack.spine.work_ledger import WorkLedgerSettings
    except ModuleNotFoundError:
        print(
            "error: budget commands require the 'rememberstack[server]' extra",
            file=sys.stderr,
        )
        return 1

    engine = create_engine(load_database_settings().sqlalchemy_url())
    try:
        ledger = WorkLedger(engine=engine, settings=WorkLedgerSettings())
        return _inspect_budgets(ledger=ledger, deployment_id=args.deployment)
    finally:
        engine.dispose()


def _run_ops(args: argparse.Namespace) -> int:
    """Compose bounded local inspection, one-row replay, or an existing rebuild."""
    try:
        from rememberstack.model import ProcessingLane
        from rememberstack.model import WorkLedgerError

        operations_type = import_module(
            "rememberstack.profiles.selfhost_operations"
        ).SelfHostOperations
    except ModuleNotFoundError:
        print(
            "error: ops commands require the 'rememberstack[server]' extra",
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
            plane=args.plane,
            deployment_id=args.deployment,
            snapshot_root=args.snapshot_root,
            workdir=args.workdir,
            version=args.version,
        )
        print(json.dumps(result, default=str, sort_keys=True))
        return 0
    except (WorkLedgerError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        operations.close()


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
            "error: ops commands require the 'rememberstack[server]' extra",
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
        if command == "cypher":
            print(
                json.dumps(
                    client.query_cypher(
                        cypher=args.statement,
                        parameters=_json_object(args.parameters),
                        max_rows=args.max_rows,
                        confirm=bool(args.confirm),
                    ),
                    default=str,
                )
            )
            return 0
        if command == "explain-cypher":
            print(
                json.dumps(
                    client.explain_cypher(
                        cypher=args.statement, parameters=_json_object(args.parameters)
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


def _json_object(raw: str | None) -> dict[str, object]:
    """Parse an optional JSON object of named Cypher parameters."""
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("parameters must be a JSON object")
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
    from rememberstack.surfaces.credentials import authorization_header
    from rememberstack.surfaces.credentials import CliClientEnv
    from rememberstack.surfaces.credentials import CredentialError
    from rememberstack.surfaces.credentials import load_credentials

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
    if (
        flag_url is None
        and flag_token is None
        and env.api_url is None
        and env.api_authorization is None
        and stored is None
    ):
        return MemoryClient.from_settings()
    api_url = (
        flag_url or env.api_url or (stored.api_url if stored is not None else None)
    )
    raw_token = flag_token
    if raw_token is None and env.api_authorization is not None:
        raw_token = env.api_authorization.get_secret_value()
    if raw_token is None and stored is not None:
        raw_token = stored.access_token.get_secret_value()
    return MemoryClient(
        base_url=api_url,
        authorization=authorization_header(token=raw_token) if raw_token else None,
    )


def _resolved_token_host(
    *, explicit: str | None, stored_host: str | None = None
) -> str:
    """Require an explicit token host; never derive one from the query API URL."""
    from rememberstack.surfaces.credentials import TokenHostSettings
    from rememberstack.surfaces.device_login import normalize_token_host

    settings = TokenHostSettings.model_validate({})
    host = explicit or settings.token_host or stored_host
    if host is None or not host.strip():
        raise ValueError("--token-host or REMEMBERSTACK_TOKEN_HOST is required")
    return normalize_token_host(token_host=host)


def _run_login(args: argparse.Namespace) -> int:
    """Device-grant login; writes the owner-only credential file."""
    from rememberstack.surfaces.credentials import CliClientEnv
    from rememberstack.surfaces.credentials import CredentialError
    from rememberstack.surfaces.credentials import load_credentials
    from rememberstack.surfaces.credentials import write_credentials
    from rememberstack.surfaces.device_login import authorize_device
    from rememberstack.surfaces.device_login import credential_from_token
    from rememberstack.surfaces.device_login import DeviceGrantError
    from rememberstack.surfaces.device_login import poll_device_token

    env = CliClientEnv.model_validate({})
    api_url = args.api_url or env.api_url or "http://127.0.0.1:8000"
    try:
        token_host = _resolved_token_host(explicit=args.token_host)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    existing = None
    try:
        existing = load_credentials()
    except CredentialError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if existing is not None:
        revoked = _logout_existing(
            token_host=existing.token_host, allow_stored_host=True
        )
        if revoked != 0:
            print(
                "error: existing credential could not be revoked; file kept",
                file=sys.stderr,
            )
            return revoked
    try:
        with httpx.Client(
            base_url=token_host, timeout=30.0, follow_redirects=False
        ) as client:
            granted = authorize_device(client=client)
            print(f"verification_uri: {granted.verification_uri}")
            print(f"verification_uri_complete: {granted.verification_uri_complete}")
            print(f"user_code: {granted.user_code}")
            token = poll_device_token(
                client=client,
                device_code=granted.device_code.get_secret_value(),
                interval=granted.interval,
                expires_in=granted.expires_in,
            )
            credential = credential_from_token(
                token=token, api_url=api_url, token_host=token_host
            )
            write_credentials(credential=credential)
    except KeyboardInterrupt:
        return 130
    except DeviceGrantError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except (httpx.HTTPError, CredentialError, ValueError):
        print("error: login failed", file=sys.stderr)
        return 1
    else:
        print(f"token_prefix: {credential.token_prefix}")
        print(f"deployment_id: {credential.deployment_id}")
        print(f"api_url: {credential.api_url}")
        return 0


def _run_logout(args: argparse.Namespace) -> int:
    """Revoke the stored bearer, then unlink the file."""
    return _logout_existing(token_host=args.token_host, allow_stored_host=True)


def _logout_existing(*, token_host: str | None, allow_stored_host: bool) -> int:
    """Revoke-then-unlink using the stored bearer. Keep the file on 5xx."""
    from rememberstack.surfaces.credentials import CredentialError
    from rememberstack.surfaces.credentials import load_credentials
    from rememberstack.surfaces.credentials import unlink_credentials
    from rememberstack.surfaces.device_login import revoke_self

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
    with httpx.Client(base_url=host, timeout=30.0, follow_redirects=False) as client:
        status = revoke_self(
            client=client, access_token=stored.access_token.get_secret_value()
        )
    if status == 0 or status >= 500:
        print("error: token host did not confirm revoke; file kept", file=sys.stderr)
        return 1
    if status < 200 or (status >= 300 and status not in {401, 404}):
        print(f"error: revoke returned HTTP {status}; file kept", file=sys.stderr)
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


def _list(*, queue: ReviewQueue, deployment_id: UUID) -> int:
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


def _inspect_budgets(*, ledger: WorkLedger, deployment_id: UUID) -> int:
    """Print one current-window JSON record per configured deployment budget."""
    for status in ledger.budget_status(deployment_id=deployment_id):
        print(status.model_dump_json())
    return 0


def _decide(
    *,
    queue: ReviewQueue,
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
    except ReviewDecisionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the client-first command grammar."""
    parser = argparse.ArgumentParser(
        prog="remember", description="RememberStack command-line interface"
    )
    parser.add_argument(
        "--version", action="version", version=f"RememberStack {__version__}"
    )
    commands = parser.add_subparsers(dest="command")
    client_flags = argparse.ArgumentParser(add_help=False)
    client_flags.add_argument("--api-url", default=None)
    client_flags.add_argument("--token", default=None)

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

    ops = commands.add_parser(
        "ops", help="inspect durable state, replay one DLQ row, or rebuild"
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
    rebuild = ops_commands.add_parser(
        "rebuild", help="invoke the existing P2 or P3 full-rebuild path"
    )
    rebuild.add_argument("--plane", choices=("p2", "p3"), required=True)
    rebuild.add_argument("--deployment", type=UUID, required=True)
    rebuild.add_argument("--snapshot-root", type=Path, required=True)
    rebuild.add_argument("--workdir", type=Path, required=True)
    rebuild.add_argument("--version", required=True)

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

    query = commands.add_parser("query", help="query the open retrieval space")
    query_commands = query.add_subparsers(dest="query_command", required=True)
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
    cypher = query_commands.add_parser(
        "cypher", parents=[client_flags], help="run one read-only Cypher statement"
    )
    cypher.add_argument("statement", help="Cypher text")
    cypher.add_argument("--parameters", help="JSON object of named parameters")
    cypher.add_argument("--max-rows", type=int)
    cypher.add_argument(
        "--confirm",
        action="store_true",
        help="confirm projected Entity/RELATES ids against live PostgreSQL",
    )
    explain_cypher = query_commands.add_parser(
        "explain-cypher",
        parents=[client_flags],
        help="plan one Cypher statement without executing it",
    )
    explain_cypher.add_argument("statement", help="Cypher text")
    explain_cypher.add_argument("--parameters", help="JSON object of named parameters")
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
    logout = commands.add_parser("logout", help="revoke the stored bearer and unlink")
    logout.add_argument("--token-host", default=None)
    return parser


if __name__ == "__main__":  # pragma: no cover - exercised via the entry point
    raise SystemExit(main())
