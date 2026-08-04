"""The sandbox executor: gated SQL → PostgreSQL → `QueryResult/v1` (§3.1, §4).

The executor never sees raw agent SQL — only `validate_sql`'s accepted,
rewritten output. It runs each request in one `READ ONLY` transaction on a
connection bound to the deployment's database and query role, applies the
tier's caps as `SET LOCAL` session settings, and maps every failure to the
public taxonomy. Telemetry is fire-and-forget (never synchronous writes on
the query path — operator performance directive, 2026-08-04).
"""

from collections.abc import Callable
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime
import json
import time
from typing import Final
from uuid import UUID
from uuid import uuid4

import psycopg
from psycopg import sql as pgsql

from rememberstack.spine.query_space.canonical import surface_manifest_hash
from rememberstack.spine.query_space.manifest import build_hash_members
from rememberstack.surfaces.query_sandbox.audit import AuditTrail
from rememberstack.surfaces.query_sandbox.audit import KillSwitches
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.grammar import validate_sql
from rememberstack.surfaces.query_sandbox.grammar import ValidatedQuery
from rememberstack.surfaces.query_sandbox.limits import clamp_rows
from rememberstack.surfaces.query_sandbox.limits import LimitTier
from rememberstack.surfaces.query_sandbox.limits import TIER_LIMITS
from rememberstack.surfaces.query_sandbox.result import QueryResult
from rememberstack.surfaces.query_sandbox.result import ResultColumn
from rememberstack.surfaces.query_sandbox.result import ResultLimits

# The only relations whose reference permits a non-null `evaluated_at` (§4.4:
# every referenced relation must be current/as-of fact/graph surface).
_EVALUATED_AT_RELATIONS: Final = frozenset(
    {"facts_current", "graph_edges_current", "contradiction_members_current"}
)

_TYPE_NAMES: Final = {
    16: "boolean",
    20: "bigint",
    21: "smallint",
    23: "integer",
    25: "text",
    114: "json",
    701: "double precision",
    1043: "character varying",
    1082: "date",
    1114: "timestamp without time zone",
    1184: "timestamp with time zone",
    1186: "interval",
    1700: "numeric",
    2950: "uuid",
    3802: "jsonb",
}


class QuerySandboxExecutor:
    """One deployment's sandboxed SQL surface.

    `connect` must yield a psycopg connection already authenticated as the
    deployment-scoped query role on the deployment's database — the executor
    never chooses a deployment; the connection *is* the tenancy boundary
    (design §4.2 as amended, no RLS).
    """

    def __init__(
        self,
        *,
        deployment_id: UUID,
        connect: Callable[[], psycopg.Connection],
        audit: AuditTrail | None = None,
        kill_switches: KillSwitches | None = None,
    ) -> None:
        self._deployment_id = deployment_id
        self._connect = connect
        self._audit = audit or AuditTrail.disabled()
        self._kills = kill_switches or KillSwitches()
        self._manifest_hash = surface_manifest_hash(build_hash_members())

    # -- public entry points (§3.1) ------------------------------------------

    def query_sql(
        self,
        *,
        sql: str,
        parameters: Sequence[object] = (),
        max_rows: int | None = None,
        tier: LimitTier = LimitTier.INTERACTIVE,
        principal: str = "agent",
    ) -> QueryResult:
        """One sandboxed statement; `QueryResult/v1` in every outcome."""
        return self._run(
            sql=sql,
            parameters=parameters,
            max_rows=max_rows,
            tier=tier,
            principal=principal,
            explain=False,
        )

    def explain_sql(
        self,
        *,
        sql: str,
        parameters: Sequence[object] = (),
        tier: LimitTier = LimitTier.INTERACTIVE,
        principal: str = "agent",
    ) -> QueryResult:
        """`EXPLAIN (FORMAT JSON)` without execution; the same gates apply."""
        return self._run(
            sql=sql,
            parameters=parameters,
            max_rows=None,
            tier=tier,
            principal=principal,
            explain=True,
        )

    # -- the request pipeline -------------------------------------------------

    def _run(
        self,
        *,
        sql: str,
        parameters: Sequence[object],
        max_rows: int | None,
        tier: LimitTier,
        principal: str,
        explain: bool,
    ) -> QueryResult:
        request_id = uuid4()
        started = datetime.now().astimezone()
        clock = time.monotonic()
        limits = TIER_LIMITS[tier]
        row_cap = clamp_rows(tier=limits, requested=max_rows)
        result_limits = ResultLimits(
            row_cap=row_cap,
            byte_cap=limits.returned_bytes_default,
            statement_timeout_ms=limits.statement_timeout_ms_default,
            analytical_tier=tier is LimitTier.ANALYTICAL,
        )

        def failed(code: QueryErrorCode, message: str) -> QueryResult:
            outcome = QueryResult(
                request_id=request_id,
                deployment_id=self._deployment_id,
                surface_manifest_hash=self._manifest_hash,
                query_hash="",
                limits=result_limits,
                execution_started_at=started,
                elapsed_ms=(time.monotonic() - clock) * 1000,
                termination_reason="rejected"
                if code.value.endswith(("_not_allowed", "_error", "_statements"))
                or code
                in (
                    QueryErrorCode.INVALID_PARAMETER,
                    QueryErrorCode.UNBOUNDED_RECURSION,
                    QueryErrorCode.SCHEMA_VERSION_MISMATCH,
                    QueryErrorCode.QUOTA_EXCEEDED,
                    QueryErrorCode.CONCURRENCY_EXCEEDED,
                )
                else "failed",
                error_code=code,
                error_message=message,
            )
            self._audit.emit(outcome=outcome, principal=principal)
            return outcome

        if len(sql.encode()) > limits.sql_text_bytes:
            return failed(QueryErrorCode.INVALID_PARAMETER, "SQL text exceeds the cap")
        if self._kills.blocked(deployment_id=self._deployment_id, principal=principal):
            return failed(
                QueryErrorCode.QUOTA_EXCEEDED,
                "the open SQL surface is disabled by the operator",
            )

        try:
            validated = validate_sql(sql)
        except SandboxRejection as rejection:
            return failed(rejection.code, rejection.message)

        if len(parameters) != validated.parameter_count:
            return failed(
                QueryErrorCode.INVALID_PARAMETER,
                f"statement binds ${validated.parameter_count} parameter(s),"
                f" {len(parameters)} provided",
            )
        if len(parameters) > limits.parameters_max:
            return failed(QueryErrorCode.INVALID_PARAMETER, "too many parameters")

        admission = self._kills.admit(
            deployment_id=self._deployment_id,
            principal=principal,
            per_principal=limits.concurrent_per_principal,
            per_deployment=limits.concurrent_per_deployment,
        )
        if admission is not None:
            return failed(QueryErrorCode.CONCURRENCY_EXCEEDED, admission)

        try:
            return self._execute(
                validated=validated,
                parameters=parameters,
                limits_model=result_limits,
                row_cap=row_cap,
                byte_cap=limits.returned_bytes_default,
                tier=tier,
                explain=explain,
                request_id=request_id,
                started=started,
                clock=clock,
                principal=principal,
            )
        finally:
            self._kills.release(deployment_id=self._deployment_id, principal=principal)

    def _execute(
        self,
        *,
        validated: ValidatedQuery,
        parameters: Sequence[object],
        limits_model: ResultLimits,
        row_cap: int,
        byte_cap: int,
        tier: LimitTier,
        explain: bool,
        request_id: UUID,
        started: datetime,
        clock: float,
        principal: str,
    ) -> QueryResult:
        limits = TIER_LIMITS[tier]
        statement = (
            f"EXPLAIN (FORMAT JSON) {validated.sql}" if explain else validated.sql
        )
        try:
            with self._transaction(limits_ms=limits) as cursor:
                pg_snapshot_at = self._snapshot_instant(cursor)
                # psycopg's type stub demands a LiteralString; the executor's
                # input is machine-generated by the gate (never caller text), so
                # bytes — an equally accepted Query form — keeps it type-safe.
                cursor.execute(statement.encode(), list(parameters) or None)
                columns = tuple(
                    ResultColumn(
                        name=d.name,
                        sql_type=_TYPE_NAMES.get(d.type_code, str(d.type_code)),
                        nullable=True,
                    )
                    for d in (cursor.description or ())
                )
                raw = cursor.fetchmany(row_cap + 1)
        except psycopg.errors.QueryCanceled:
            return self._failure(
                QueryErrorCode.STATEMENT_TIMEOUT,
                "the statement exceeded its timeout",
                request_id,
                started,
                clock,
                limits_model,
                principal,
            )
        except psycopg.errors.LockNotAvailable:
            return self._failure(
                QueryErrorCode.LOCK_TIMEOUT,
                "a required lock was not available in time",
                request_id,
                started,
                clock,
                limits_model,
                principal,
            )
        except (
            psycopg.errors.ConfigurationLimitExceeded,
            psycopg.errors.DiskFull,
            psycopg.errors.OutOfMemory,
            psycopg.errors.TooManyConnections,
        ):
            return self._failure(
                QueryErrorCode.RESOURCE_LIMIT,
                "the statement exceeded a resource cap",
                request_id,
                started,
                clock,
                limits_model,
                principal,
            )
        except psycopg.OperationalError:
            return self._failure(
                QueryErrorCode.PG_UNAVAILABLE,
                "the deployment database is unavailable",
                request_id,
                started,
                clock,
                limits_model,
                principal,
            )
        except psycopg.Error:
            return self._failure(
                QueryErrorCode.EXECUTION_ERROR,
                "the statement failed during execution",
                request_id,
                started,
                clock,
                limits_model,
                principal,
            )

        truncated = len(raw) > row_cap
        rows = raw[:row_cap]
        encoded_bytes = 0
        kept: list[tuple[object, ...]] = []
        byte_truncated = False
        for row in rows:
            encoded_bytes += len(json.dumps(row, default=str).encode())
            if encoded_bytes > byte_cap:
                byte_truncated = True
                break
            kept.append(tuple(row))

        evaluated_at = None
        if validated.referenced_views and set(validated.referenced_views) <= (
            _EVALUATED_AT_RELATIONS
        ):
            evaluated_at = pg_snapshot_at

        outcome = QueryResult(
            request_id=request_id,
            deployment_id=self._deployment_id,
            surface_manifest_hash=self._manifest_hash,
            query_hash=validated.query_hash,
            referenced_views=validated.referenced_views,
            referenced_functions=validated.referenced_functions,
            columns=columns,
            rows=tuple(kept),
            returned_row_count=len(kept),
            returned_byte_count=encoded_bytes if not byte_truncated else byte_cap,
            limits=limits_model,
            truncated=truncated or byte_truncated,
            truncation_reason=(
                "row_cap" if truncated else ("byte_cap" if byte_truncated else None)
            ),
            ordered_result=validated.ordered_result,
            empty_result=not kept,
            execution_started_at=started,
            evaluated_at=evaluated_at,
            pg_snapshot_at=pg_snapshot_at,
            elapsed_ms=(time.monotonic() - clock) * 1000,
        )
        self._audit.emit(outcome=outcome, principal=principal)
        return outcome

    def _failure(
        self,
        code: QueryErrorCode,
        message: str,
        request_id: UUID,
        started: datetime,
        clock: float,
        limits_model: ResultLimits,
        principal: str,
    ) -> QueryResult:
        outcome = QueryResult(
            request_id=request_id,
            deployment_id=self._deployment_id,
            surface_manifest_hash=self._manifest_hash,
            query_hash="",
            limits=limits_model,
            execution_started_at=started,
            elapsed_ms=(time.monotonic() - clock) * 1000,
            termination_reason="failed",
            error_code=code,
            error_message=message,
        )
        self._audit.emit(outcome=outcome, principal=principal)
        return outcome

    @contextmanager
    def _transaction(self, *, limits_ms):  # noqa: ANN001, ANN202
        connection = self._connect()
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        pgsql.SQL(
                            "SET LOCAL search_path = memory_v1, pg_catalog;"
                            " SET LOCAL transaction_read_only = on;"
                            " SET LOCAL statement_timeout = {};"
                            " SET LOCAL lock_timeout = {};"
                            " SET LOCAL idle_in_transaction_session_timeout = {};"
                            " SET LOCAL work_mem = {};"
                            " SET LOCAL temp_file_limit = {}"
                        ).format(
                            pgsql.Literal(limits_ms.statement_timeout_ms_default),
                            pgsql.Literal(limits_ms.lock_timeout_ms),
                            pgsql.Literal(limits_ms.idle_transaction_ms),
                            pgsql.Literal(f"{limits_ms.work_mem_kib}kB"),
                            pgsql.Literal(f"{limits_ms.temp_file_kib}kB"),
                        )
                    )
                    yield cursor
        finally:
            connection.close()

    @staticmethod
    def _snapshot_instant(cursor: psycopg.Cursor) -> datetime:
        cursor.execute("SELECT transaction_timestamp()")
        row = cursor.fetchone()
        assert row is not None
        return row[0]
