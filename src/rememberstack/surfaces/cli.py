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
from rememberstack.surfaces.remote_mcp import RemoteRecipeMcpServer
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
        if args.command == "query":
            return _run_query(args)
        if args.command == "ingest":
            return _run_ingest(args)
        if args.command == "connectors":
            return _run_connectors(args)
        if args.command == "mcp":
            return _run_mcp()
        if args.command == "eval":
            return _run_eval(args)
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


def _run_query(args: argparse.Namespace) -> int:
    """Run a query command through the typed remote SDK."""
    with MemoryClient.from_settings() as client:
        if args.query_command == "list":
            for descriptor in client.recipes():
                print(descriptor.model_dump_json())
            return 0
        if args.query_command == "run":
            try:
                arguments = dict(_split_arg(pair) for pair in args.arg)
            except ValueError as error:
                print(f"error: {error}", file=sys.stderr)
                return 2
            print(
                client.run_recipe(
                    name=args.recipe, arguments=arguments
                ).model_dump_json()
            )
            return 0
        return _run_open_query(client=client, args=args)


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
        with MemoryClient.from_settings() as client:
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
    with MemoryClient.from_settings() as client:
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


def _run_mcp() -> int:
    """Expose the remote deployment recipe registry over MCP stdio."""
    with MemoryClient.from_settings() as client:
        return serve_mcp_stdio(server=RemoteRecipeMcpServer(client=client))


def _run_eval(args: argparse.Namespace) -> int:
    """Offline open-query noninferiority gate and paid-run estimate (no models)."""
    from rememberstack.eval.open_query_noninferiority import (  # noqa: PLC0415
        estimate_paid_run,
    )
    from rememberstack.eval.open_query_noninferiority import (  # noqa: PLC0415
        evaluate_noninferiority,
    )
    from rememberstack.eval.open_query_noninferiority import (  # noqa: PLC0415
        load_arm_metrics,
    )

    if args.eval_command != "open-query-gate":
        print(f"error: unknown eval command {args.eval_command!r}", file=sys.stderr)
        return 2
    if args.estimate or args.metrics is None:
        plan = estimate_paid_run(
            cases=args.cases,
            arms=args.arms,
            calls_per_case=args.calls_per_case,
            unit_cost=args.unit_cost,
        )
        print(json.dumps(plan, indent=2, default=str))
        if args.metrics is None and not args.estimate:
            print(
                "note: pass --metrics <file.json> to evaluate offline gates;"
                " this command never starts a paid run.",
                file=sys.stderr,
            )
        return 0
    try:
        metrics = load_arm_metrics(path=args.metrics)
        report = evaluate_noninferiority(metrics=metrics)
    except (OSError, ValueError, json.JSONDecodeError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("passed") else 1


def query_list(*, client: httpx.Client) -> int:
    """Print recipes from an injected client (the parity-testable CLI seam)."""
    try:
        for descriptor in MemoryClient(client=client).recipes():
            print(descriptor.model_dump_json())
    except MemoryApiError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def query_run(*, client: httpx.Client, name: str, arg_pairs: list[str]) -> int:
    """Run one recipe through an injected client and print its envelope."""
    try:
        arguments = dict(_split_arg(pair) for pair in arg_pairs)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    try:
        envelope = MemoryClient(client=client).run_recipe(
            name=name, arguments=arguments
        )
    except MemoryApiError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(envelope.model_dump_json())
    return 0


def _split_arg(pair: str) -> tuple[str, str]:
    """Split one ``key=value`` argument, or raise a clear error."""
    key, separator, value = pair.partition("=")
    if not separator or not key:
        raise ValueError(f"argument {pair!r} is not key=value")
    return key, value


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

    query = commands.add_parser(
        "query", help="query deployment recipes and the open query space"
    )
    query_commands = query.add_subparsers(dest="query_command", required=True)
    query_commands.add_parser("list", help="list the remote recipe tools")
    run = query_commands.add_parser("run", help="run one recipe by name")
    run.add_argument("recipe", help="the recipe name (see `remember query list`)")
    run.add_argument(
        "--arg", action="append", default=[], metavar="KEY=VALUE", help="repeatable"
    )
    sql = query_commands.add_parser("sql", help="run one sandboxed SQL statement")
    sql.add_argument("statement", help="SQL text")
    sql.add_argument("--parameters", help="JSON array of positional bound parameters")
    sql.add_argument("--max-rows", type=int)
    explain_sql = query_commands.add_parser(
        "explain-sql", help="EXPLAIN one SQL statement without executing it"
    )
    explain_sql.add_argument("statement", help="SQL text")
    explain_sql.add_argument(
        "--parameters", help="JSON array of positional bound parameters"
    )
    cypher = query_commands.add_parser(
        "cypher", help="run one read-only Cypher statement"
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
        "explain-cypher", help="plan one Cypher statement without executing it"
    )
    explain_cypher.add_argument("statement", help="Cypher text")
    explain_cypher.add_argument("--parameters", help="JSON object of named parameters")
    space = query_commands.add_parser(
        "space", help="describe the open query space (manifest discovery)"
    )
    space.add_argument("--pattern", help="optional fnmatch filter over view names")
    space.add_argument(
        "--include-examples",
        action="store_true",
        help="include shipped examples.* names",
    )
    search_space = query_commands.add_parser(
        "search-space", help="search checked-in manifest text"
    )
    search_space.add_argument("query", help="free-text search over the manifest")
    search_space.add_argument("--k", type=int, default=10)
    list_saved = query_commands.add_parser(
        "list-saved", help="list saved-query registry metadata"
    )
    list_saved.add_argument("--namespace")
    list_saved.add_argument("--status")
    describe_saved = query_commands.add_parser(
        "describe-saved", help="describe one saved-query version"
    )
    describe_saved.add_argument("namespace")
    describe_saved.add_argument("name")
    describe_saved.add_argument("--version", type=int)
    run_saved = query_commands.add_parser(
        "run-saved", help="run one active saved query"
    )
    run_saved.add_argument("namespace")
    run_saved.add_argument("name")
    run_saved.add_argument("--version", type=int)
    run_saved.add_argument(
        "--parameters", help="JSON array of positional bound parameters"
    )
    run_saved.add_argument("--max-rows", type=int)

    eval_cmd = commands.add_parser(
        "eval", help="offline evaluation helpers (never starts a paid benchmark run)"
    )
    eval_commands = eval_cmd.add_subparsers(dest="eval_command", required=True)
    gate = eval_commands.add_parser(
        "open-query-gate",
        help="offline §8 noninferiority gate or paid-run cost estimate (no model calls)",
    )
    gate.add_argument(
        "--metrics",
        type=Path,
        help="JSON file of already-collected same-condition arm metrics",
    )
    gate.add_argument(
        "--estimate",
        action="store_true",
        help="print the paid-run estimate/plan without evaluating metrics",
    )
    gate.add_argument("--cases", type=int, default=0, help="estimated case count")
    gate.add_argument("--arms", type=int, default=2, help="arm count (legacy + open)")
    gate.add_argument(
        "--calls-per-case", type=int, default=1, help="mean model calls per case"
    )
    gate.add_argument(
        "--unit-cost",
        type=float,
        default=0.0,
        help="operator-supplied unit cost per model call (currency units)",
    )

    ingest = commands.add_parser("ingest", help="push a file through E0")
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
    connector_commands.add_parser("list", help="list connectors")
    add = connector_commands.add_parser("add", help="add connector configuration")
    add.add_argument("kind")
    add.add_argument("--name", required=True)
    add.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    add.add_argument("--credential-ref")
    pause = connector_commands.add_parser("pause", help="pause a connector")
    pause.add_argument("connector_id", type=UUID)
    status = connector_commands.add_parser("status", help="show connector status")
    status.add_argument("connector_id", type=UUID)

    commands.add_parser("mcp", help="serve remote recipes over MCP stdio")
    return parser


if __name__ == "__main__":  # pragma: no cover - exercised via the entry point
    raise SystemExit(main())
