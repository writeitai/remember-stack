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
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
from pydantic import JsonValue
from pydantic import SecretStr

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
    from rememberstack.surfaces.credentials import CredentialFile

_MERGE_VERDICTS = ("merge", "not_merge")
_TRIAGE_VERDICTS = ("restore_support", "invalidate_fact", "uncertain")


def main(argv: list[str] | None = None) -> int:
    """The ``remember`` entry point; returns the process exit code."""
    _warn_if_revocation_outstanding()
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

        from rememberstack.spine.settings import load_database_settings
        from rememberstack.spine.surface_cost import open_surface_scope
        from rememberstack.spine.surface_cost import SurfaceCostKind

        review_queue_builder = import_module(
            "rememberstack.profiles.selfhost"
        ).build_selfhost_review_queue
    except ModuleNotFoundError:
        print(
            "error: review commands require the 'rememberstack[server]' extra",
            file=sys.stderr,
        )
        return 1

    engine = create_engine(load_database_settings().sqlalchemy_url())
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
        except ReviewDecisionError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        with open_surface_scope(surface=SurfaceCostKind.OPERATION):
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
            "error: ops commands require the 'rememberstack[server]' extra",
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
    from rememberstack.surfaces.credentials import CredentialError
    from rememberstack.surfaces.credentials import load_pending_revocations

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
    from rememberstack.surfaces.credentials import TokenHostSettings
    from rememberstack.surfaces.device_login import normalize_token_host

    settings = TokenHostSettings.model_validate({})
    host = explicit or settings.token_host or stored_host
    if host is None or not host.strip():
        raise ValueError("--token-host or REMEMBERSTACK_TOKEN_HOST is required")
    return normalize_token_host(token_host=host)


def _run_login(args: argparse.Namespace) -> int:
    """Device-grant login; writes the owner-only credential file.

    Held under the credential lock end to end, so two concurrent logins cannot
    each mint a replacement and overwrite the other's file — which would leave
    one live credential with nothing on disk naming it.
    """
    from rememberstack.surfaces.credentials import credential_lock
    from rememberstack.surfaces.credentials import CredentialError
    from rememberstack.surfaces.credentials import DurabilityUnconfirmed

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
    from rememberstack.surfaces.credentials import append_pending_revocation
    from rememberstack.surfaces.credentials import assert_revocation_capacity
    from rememberstack.surfaces.credentials import CliClientEnv
    from rememberstack.surfaces.credentials import credential_origin
    from rememberstack.surfaces.credentials import CredentialError
    from rememberstack.surfaces.credentials import drop_pending_revocation
    from rememberstack.surfaces.credentials import DurabilityUnconfirmed
    from rememberstack.surfaces.credentials import load_credentials
    from rememberstack.surfaces.credentials import PendingRevocation
    from rememberstack.surfaces.credentials import write_credentials
    from rememberstack.surfaces.device_login import authorize_device
    from rememberstack.surfaces.device_login import credential_from_token
    from rememberstack.surfaces.device_login import DeviceGrantError
    from rememberstack.surfaces.device_login import poll_device_token

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
            granted = authorize_device(client=client)
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
            try:
                try:
                    credential = credential_from_token(
                        token=token, api_url=api_url, token_host=token_host
                    )
                except DeviceGrantError:
                    # The poll already journalled the minted bearer. Retire it
                    # now when possible; if the host cannot confirm that, the
                    # journal keeps the only secret needed for a later retry.
                    _retry_pending_revocation()
                    raise
                if existing is not None:
                    # Written before the file is overwritten, because
                    # overwriting it destroys the only copy of the
                    # predecessor's secret. A crash after this point leaves a
                    # record of what still needs revoking; a crash before it
                    # leaves the old credential intact and in use.
                    append_pending_revocation(
                        pending=PendingRevocation(
                            version=1,
                            token_host=existing.token_host,
                            access_token=existing.access_token,
                            token_id=existing.token_id,
                        )
                    )
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
                # The journal entry stays: whatever went wrong, the credential
                # exists at the token host and the next login or logout will
                # retire it. Nothing here has to succeed for that to hold.
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
    ):
        # OSError included deliberately: a disk that cannot be written to
        # during login is a failed login, not a crash. The credential has
        # already been withdrawn or recorded by the time we get here.
        print("error: login failed", file=sys.stderr)
        return 1
    else:
        # The predecessor is revoked only now, with the replacement already on
        # disk. Revoking first would mean a login interrupted at the browser
        # step — a closed tab, an expired user code — leaves the machine with no
        # credential at all, having destroyed the working one to make room.
        if existing is not None:
            _retry_pending_revocation()
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
    from rememberstack.surfaces.device_login import revoke_self

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
    from rememberstack.surfaces.credentials import confirm_credentials_durable
    from rememberstack.surfaces.credentials import credential_origin
    from rememberstack.surfaces.credentials import CredentialError
    from rememberstack.surfaces.credentials import drop_pending_revocation
    from rememberstack.surfaces.credentials import DurabilityUnconfirmed
    from rememberstack.surfaces.credentials import load_credentials
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.device_login import normalize_token_host
    from rememberstack.surfaces.device_login import revoke_self

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
    current_identity = (
        (credential_origin(token_host=current.token_host), current.token_id)
        if current is not None
        else None
    )
    for pending in journal.entries:
        if current_identity is not None and pending.identity == current_identity:
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
                status = revoke_self(
                    client=client, access_token=pending.access_token.get_secret_value()
                )
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
    from rememberstack.surfaces.credentials import credential_lock
    from rememberstack.surfaces.credentials import CredentialError
    from rememberstack.surfaces.credentials import DurabilityUnconfirmed

    try:
        with credential_lock():
            _retry_pending_revocation()
            return _logout_existing(token_host=args.token_host, allow_stored_host=True)
    except (CredentialError, DurabilityUnconfirmed, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


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
    if not _revoke_confirmed(status=status):
        # 404 used to count as success here and does not: it can mean the route
        # is absent rather than the credential retired, and unlinking on it
        # discards the only copy of a secret that may still authenticate.
        print(
            f"error: revoke not confirmed (HTTP {status or 'no response'}); file kept",
            file=sys.stderr,
        )
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
