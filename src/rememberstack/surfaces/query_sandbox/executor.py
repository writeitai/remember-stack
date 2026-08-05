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
import hashlib
import json
import time
from typing import Final
from uuid import UUID
from uuid import uuid4

import psycopg
from psycopg import sql as pgsql

from rememberstack.spine.query_space.canonical import surface_manifest_hash
from rememberstack.spine.query_space.manifest import build_hash_members
from rememberstack.spine.query_space.manifest import declared_views
from rememberstack.surfaces.query_sandbox.audit import AuditTrail
from rememberstack.surfaces.query_sandbox.audit import KillSwitches
from rememberstack.surfaces.query_sandbox.bridge import resolve_invocations
from rememberstack.surfaces.query_sandbox.bridge import substitute
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.grammar import PUBLIC_SRF_NAMES
from rememberstack.surfaces.query_sandbox.grammar import validate_sql
from rememberstack.surfaces.query_sandbox.grammar import ValidatedQuery
from rememberstack.surfaces.query_sandbox.limits import clamp_rows
from rememberstack.surfaces.query_sandbox.limits import LimitTier
from rememberstack.surfaces.query_sandbox.limits import TIER_LIMITS
from rememberstack.surfaces.query_sandbox.nomination import BridgeSettings
from rememberstack.surfaces.query_sandbox.result import QueryResult
from rememberstack.surfaces.query_sandbox.result import ResultColumn
from rememberstack.surfaces.query_sandbox.result import ResultLimits
from rememberstack.surfaces.query_sandbox.result import SemanticInvocation

# The only relations whose reference permits a non-null `evaluated_at` (§4.4:
# every referenced relation must be current/as-of fact/graph surface).
_EVALUATED_AT_RELATIONS: Final = frozenset(
    {"facts_current", "graph_edges_current", "contradiction_members_current"}
)
# As-of functions answer at an instant the caller names, which is equally a
# single applied instant; every other public function is not instant-scoped.
_AS_OF_FUNCTIONS: Final = frozenset({"facts_as_of"})

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


def _type_name(cursor: psycopg.Cursor, oid: int) -> str:
    """The SQL type name for a result column, asked of the server itself.

    A static OID table cannot know array or extension types; `format_type`
    is the same function the catalog gate uses, so both sides agree.
    """
    static = _TYPE_NAMES.get(oid)
    if static is not None:
        return static
    try:
        row = cursor.connection.execute(
            "SELECT pg_catalog.format_type(%s::oid, NULL)", (oid,)
        ).fetchone()
    except psycopg.Error:
        return str(oid)
    return str(row[0]) if row and row[0] else str(oid)


def _encoded_size(value: object) -> int:
    """The real wire size of one bound value.

    `str(value)` lies for binary payloads — a memoryview of a megabyte prints
    as a short repr — so buffers are measured as buffers.
    """
    if isinstance(value, memoryview):
        # `len()` counts ELEMENTS: a four-byte-format view of 256 KiB reports a
        # quarter of its size. `nbytes` is the wire size.
        return value.nbytes
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, str):
        return len(value.encode())
    return len(str(value).encode())


def _hash_with_parameter_types(base: str, parameters: Sequence[object]) -> str:
    """Fold the bound parameter TYPE vector into the statement hash (§4.4).

    Values never enter the hash — only their types, so the same text bound to
    an integer and to text are distinguishable without disclosing either.
    """
    if not parameters:
        return base
    vector = ",".join(_sql_type_family(value) for value in parameters)
    return hashlib.sha256(f"{base}|types={vector}".encode()).hexdigest()


def _sql_type_family(value: object) -> str:
    """The canonical SQL type a bound value adapts to.

    Python class names are the wrong vector: `bytes` and `memoryview` both
    bind as `bytea` and must hash alike, while the width PostgreSQL picks for
    an integer is a wire detail, not a different query.
    """
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "bytea"
    if isinstance(value, str):
        return "text"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "double precision"
    if isinstance(value, (list, tuple)):
        # An array of integers and an array of text are different types.
        element = _sql_type_family(value[0]) if value else "unknown"
        return f"{element}[]"
    if value is None:
        return "unknown"
    from datetime import date
    from datetime import datetime as _datetime
    from datetime import time as _time
    from decimal import Decimal
    from uuid import UUID as _UUID

    if isinstance(value, _datetime):
        # A naive value carries no zone and is a different SQL type.
        return "timestamptz" if value.tzinfo is not None else "timestamp"
    if isinstance(value, date):
        return "date"
    if isinstance(value, _time):
        return "time"
    if isinstance(value, Decimal):
        return "numeric"
    if isinstance(value, _UUID):
        return "uuid"
    if isinstance(value, dict):
        return "jsonb"
    return type(value).__name__.lower()


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
        analytical_entitlement: bool = False,
        search: object | None = None,
        embed: Callable[..., tuple[float, ...]] | None = None,
    ) -> None:
        # The public functions need a projection to nominate from and a way to
        # embed a query; without them the statement still parses, and a call
        # fails with the store-phase code rather than a confusing SQL error.
        self._search = search
        self._embed = embed
        self._deployment_id = deployment_id
        # §4.3: the analytical tier requires an operator entitlement and its
        # own pool. A caller asking for it without one runs interactive.
        self._analytical_entitlement = analytical_entitlement
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
        if tier is LimitTier.ANALYTICAL and not self._analytical_entitlement:
            tier = LimitTier.INTERACTIVE
        limits = TIER_LIMITS[tier]
        row_cap = clamp_rows(tier=limits, requested=max_rows)
        result_limits = ResultLimits(
            row_cap=row_cap,
            byte_cap=limits.returned_bytes_default,
            statement_timeout_ms=limits.statement_timeout_ms_default,
            analytical_tier=tier is LimitTier.ANALYTICAL,
        )

        known_hash = ""

        def failed(code: QueryErrorCode, message: str) -> QueryResult:
            outcome = QueryResult(
                request_id=request_id,
                deployment_id=self._deployment_id,
                surface_manifest_hash=self._manifest_hash,
                query_hash=known_hash,
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
        known_hash = _hash_with_parameter_types(validated.query_hash, parameters)

        if len(parameters) != validated.parameter_count:
            return failed(
                QueryErrorCode.INVALID_PARAMETER,
                f"statement binds ${validated.parameter_count} parameter(s),"
                f" {len(parameters)} provided",
            )
        if len(parameters) > limits.parameters_max:
            return failed(QueryErrorCode.INVALID_PARAMETER, "too many parameters")
        encoded_parameter_bytes = sum(_encoded_size(value) for value in parameters)
        if encoded_parameter_bytes > limits.parameters_bytes:
            return failed(
                QueryErrorCode.INVALID_PARAMETER,
                "the encoded parameters exceed the tier's byte cap",
            )

        admission = self._kills.admit(
            deployment_id=self._deployment_id,
            principal=principal,
            per_principal=limits.concurrent_per_principal,
            per_deployment=limits.concurrent_per_deployment,
            principal_seconds_per_minute=limits.principal_statement_seconds_per_minute,
            deployment_seconds_per_minute=(
                limits.deployment_statement_seconds_per_minute
            ),
        )
        if admission is not None:
            code = (
                QueryErrorCode.QUOTA_EXCEEDED
                if "quota" in admission
                else QueryErrorCode.CONCURRENCY_EXCEEDED
            )
            return failed(code, admission)

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
            self._kills.record_spend(
                deployment_id=self._deployment_id,
                principal=principal,
                seconds=time.monotonic() - clock,
            )
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
        semantic_invocations: tuple[SemanticInvocation, ...] = ()
        bridge_parameters: dict[str, object] = {}
        executable = validated.sql
        if validated.srf_bindings and not explain:
            if self._search is None or self._embed is None:
                return self._failure(
                    QueryErrorCode.LANCE_UNAVAILABLE,
                    "the projection is not configured for this deployment",
                    request_id,
                    started,
                    clock,
                    limits_model,
                    principal,
                )
            try:
                with self._transaction(limits_ms=limits) as bridge_cursor:
                    resolutions = resolve_invocations(
                        bindings=validated.srf_bindings,
                        parameters=parameters,
                        connection=bridge_cursor.connection,
                        search=self._search,
                        embed=self._embed,
                        settings=BridgeSettings(deployment_id=self._deployment_id),
                    )
            except SandboxRejection as rejection:
                return self._failure(
                    rejection.code,
                    rejection.message,
                    request_id,
                    started,
                    clock,
                    limits_model,
                    principal,
                    query_hash=_hash_with_parameter_types(
                        validated.query_hash, parameters
                    ),
                )
            executable, bridge_parameters = substitute(validated.sql, resolutions)
            semantic_invocations = tuple(
                resolution.invocation for resolution in resolutions
            )

        statement = f"EXPLAIN (FORMAT JSON) {executable}" if explain else executable
        try:
            with self._transaction(limits_ms=limits) as cursor:
                pg_snapshot_at = self._snapshot_instant(cursor)
                # psycopg's type stub demands a LiteralString; the executor's
                # input is machine-generated by the gate (never caller text), so
                # bytes — an equally accepted Query form — keeps it type-safe.
                bound: dict[str, object] = {
                    f"p{index}": value
                    for index, value in enumerate(parameters, start=1)
                }
                # The bridge's confirmed rows travel as parameters too, so no
                # confirmed value is ever parsed as part of the statement.
                bound.update(bridge_parameters)
                cursor.execute(statement.encode(), bound or None)
                columns = tuple(
                    ResultColumn(
                        name=d.name,
                        type=_type_name(cursor, d.type_code),
                        nullable=True,  # PostgreSQL does not report result
                        # nullability for computed columns; the contract says
                        # so rather than guessing per expression.
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
                query_hash=_hash_with_parameter_types(validated.query_hash, parameters),
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
                query_hash=_hash_with_parameter_types(validated.query_hash, parameters),
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
                query_hash=_hash_with_parameter_types(validated.query_hash, parameters),
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
                query_hash=_hash_with_parameter_types(validated.query_hash, parameters),
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
                query_hash=_hash_with_parameter_types(validated.query_hash, parameters),
            )

        truncated = len(raw) > row_cap
        rows = raw[:row_cap]
        encoded_bytes = 0
        kept: list[tuple[object, ...]] = []
        byte_truncated = False
        for row in rows:
            row_bytes = len(json.dumps(row, default=str).encode())
            if encoded_bytes + row_bytes > byte_cap:
                byte_truncated = True
                break
            encoded_bytes += row_bytes
            kept.append(tuple(row))

        # §4.4: one applied instant may be reported only when EVERY referenced
        # relation and function answers at one — a mixed statement has no
        # single instant to name.
        # Only the public functions decide the instant: an ordinary scalar
        # like `count` says nothing about when the rows were true.
        referenced = set(validated.referenced_views) | (
            set(validated.referenced_functions) & PUBLIC_SRF_NAMES
        )
        evaluated_at = None
        if referenced and referenced <= (_EVALUATED_AT_RELATIONS | _AS_OF_FUNCTIONS):
            evaluated_at = pg_snapshot_at
        grain_tags = tuple(
            sorted(
                {
                    view.grain_tag
                    for view in declared_views()
                    if view.name in set(validated.referenced_views)
                }
            )
        )

        outcome = QueryResult(
            request_id=request_id,
            deployment_id=self._deployment_id,
            surface_manifest_hash=self._manifest_hash,
            query_hash=_hash_with_parameter_types(validated.query_hash, parameters),
            referenced_views=validated.referenced_views,
            referenced_functions=validated.referenced_functions,
            source_grain_tags=grain_tags,
            columns=columns,
            rows=tuple(kept),
            returned_row_count=len(kept),
            returned_byte_count=encoded_bytes,
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
            semantic_invocations=semantic_invocations,
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
        query_hash: str = "",
    ) -> QueryResult:
        outcome = QueryResult(
            request_id=request_id,
            deployment_id=self._deployment_id,
            surface_manifest_hash=self._manifest_hash,
            query_hash=query_hash,
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
            # A pooled connection may carry state from an earlier request —
            # prepared statements, cursors, temp objects, LISTEN registrations,
            # session GUCs. Discard all of it before this request begins.
            # DISCARD ALL cannot run inside a transaction block, so it runs in
            # autocommit before the request transaction opens.
            previous_autocommit = connection.autocommit
            connection.autocommit = True
            connection.execute("DISCARD ALL")
            connection.autocommit = previous_autocommit
            with connection.transaction():
                with connection.cursor() as cursor:
                    # Only GUCs a non-superuser may set belong here: the
                    # deployment role is deliberately unprivileged, so
                    # temp_file_limit (superuser-only) is pinned on the role
                    # itself by migration p9_02_0023 instead. Parallel query is
                    # off so one statement cannot fan out across workers.
                    cursor.execute(
                        pgsql.SQL(
                            "SET LOCAL search_path = memory_v1, pg_catalog;"
                            " SET LOCAL transaction_read_only = on;"
                            " SET LOCAL statement_timeout = {};"
                            " SET LOCAL lock_timeout = {};"
                            " SET LOCAL idle_in_transaction_session_timeout = {};"
                            " SET LOCAL work_mem = {};"
                            " SET LOCAL max_parallel_workers_per_gather = 0"
                        ).format(
                            pgsql.Literal(limits_ms.statement_timeout_ms_default),
                            pgsql.Literal(limits_ms.lock_timeout_ms),
                            pgsql.Literal(limits_ms.idle_transaction_ms),
                            pgsql.Literal(f"{limits_ms.work_mem_kib}kB"),
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
