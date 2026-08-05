"""The Cypher read surface over the published graph snapshot (design §3.5).

An agent writes Cypher against `memory_v1`'s graph projection and gets rows
back, with the one thing a snapshot answer cannot be read without: the instant
it projects. Every result carries grade `snapshot_graph` and the `built_at` of
the generation it ran against, because an aggregate over a snapshot is exactly
correct for that cut and says nothing about what has happened since.

The order of controls is the point. The pre-engine deny-scan (`cypher.py`)
refuses the file/network/extension family before the engine sees the text —
those are the constructs `read_only=True` does not stop. Mutations are not
scanned for: the snapshot is opened `read_only=True`, the engine refuses them
itself, and this executor maps that refusal to `cypher_not_allowed` so the
caller gets a stated rejection rather than a raw engine message. Everything
else runs under a timeout and the row/byte caps.

`confirm=true` is a narrow, explicit option: it checks that top-level `Entity`
and `RELATES` VALUES still exist live in PostgreSQL and drops rows whose ids do
not. It does not re-run the plan, re-ground an aggregate, or make any other
part of the result live, and the result stays `snapshot_graph`.

It does NOT confirm a scalar projection of an id: `RETURN e.id` comes back with
all three counts at zero, because knowing that a UUID column derives from
`Entity.id` needs the parsed Cypher AST, and guessing from column names or from
the value's shape would confirm `Document` ids, which §3.5 says are never
confirmed. Zero is the honest report, but a caller who projects ids and reads
`nominated = 0` as "all clear" is misreading it — project the node or the
relationship itself to have it checked.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time as datetime_time
from datetime import UTC
from decimal import Decimal
import hashlib
import json
import re
import time
from typing import Any
from typing import Final
from uuid import UUID
from uuid import uuid4

import psycopg

from rememberstack.spine.query_space.manifest import load_manifest
from rememberstack.surfaces.query_sandbox.audit import AuditTrail
from rememberstack.surfaces.query_sandbox.audit import KillSwitches
from rememberstack.surfaces.query_sandbox.cypher import RECURSIVE_HOPS_MAX
from rememberstack.surfaces.query_sandbox.cypher import validate_cypher
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.limits import LimitTier
from rememberstack.surfaces.query_sandbox.limits import TIER_LIMITS
from rememberstack.surfaces.query_sandbox.result import GraphConfirmation
from rememberstack.surfaces.query_sandbox.result import P2Snapshot
from rememberstack.surfaces.query_sandbox.result import QueryResult
from rememberstack.surfaces.query_sandbox.result import ResultColumn
from rememberstack.surfaces.query_sandbox.result import ResultLimits

#: §4.3: Cypher text is capped lower than SQL — the graph language is denser,
#: and a 32 KiB query is already far past anything an agent composes by hand.
CYPHER_TEXT_BYTES_MAX: Final = 32 * 1024

P2_FRESHNESS_WARNING_SECONDS: Final = 3600.0
P2_STALE_WARNING: Final = (
    "the published graph snapshot is more than 3600 seconds old;"
    " later changes are not represented"
)

NO_CONFIRMABLE_VALUES_WARNING: Final = (
    "confirm=true found no top-level Entity or RELATES value to check;"
    " scalar IDs and all other values remain snapshot-scoped"
)

#: Keys the engine uses for its own physical addressing. They are stable only
#: within one built generation, so publishing them would invite a caller to
#: store one and use it against the next snapshot, where it means something
#: else entirely. Matched case-insensitively: the pinned engine spells them
#: `_ID`/`_SRC`/`_DST`, and a case-sensitive list silently published all three.
_ENGINE_INTERNAL_KEYS: Final = frozenset({"_id", "_src", "_dst"})

# LadybugDB reports physical addresses as INTERNAL_ID wherever they are nested
# in the result type: scalar, collection, or struct field. Match a type token,
# not a caller-authored struct field named INTERNAL_ID.
_INTERNAL_ID_TYPE: Final = re.compile(
    r"(?:^|[\s,(])INTERNAL_ID(?:\[\])*(?=$|[,\)\]])", re.IGNORECASE
)

#: Where the engine records a value's label, in the spellings it uses.
_LABEL_KEYS: Final = ("_LABEL", "_label")

#: The pinned engine's refusal when a mutation reaches a `read_only=True`
#: database, verbatim and compared exactly. Mapped to `cypher_not_allowed` so the caller sees a
#: stated rejection rather than an opaque execution failure. A version bump
#: that changes this wording fails the pin test instead of silently
#: reclassifying writes as execution errors.
READ_ONLY_REFUSAL: Final = (
    "Connection exception: Cannot execute write operations in a read-only database!"
)

#: Which live relation each confirmable graph type is checked against.
_CONFIRM_SQL: Final = {
    "entity": (
        "SELECT entity_id::text FROM memory_v1.entities_current"
        " WHERE deployment_id = %(deployment)s"
        "   AND entity_id = ANY(%(ids)s::uuid[])"
    ),
    "relation": (
        "SELECT relation_id::text FROM memory_v1.graph_edges_current"
        " WHERE deployment_id = %(deployment)s"
        "   AND relation_id = ANY(%(ids)s::uuid[])"
    ),
}


class CypherSandboxExecutor:
    """One deployment's Cypher read surface over its published snapshot.

    `reader` serves the snapshot; `connect`, when given, is what `confirm=true`
    uses to check live membership. Without a connection the option is refused
    rather than silently ignored, because a caller who asked for confirmation
    and did not get it would read the result as confirmed.
    """

    def __init__(
        self,
        *,
        deployment_id: Any,
        reader: Any,
        connect: Callable[[], psycopg.Connection] | None = None,
        audit: AuditTrail | None = None,
        kill_switches: KillSwitches | None = None,
        analytical_entitlement: bool = False,
    ) -> None:
        # The reader serves ONE deployment's snapshot. Taking it and the
        # deployment id as independent arguments let a mismatched pair serve
        # one deployment's graph while labelling the result as another's, which
        # no amount of care at the call site makes safe.
        served = getattr(reader, "deployment_id", None)
        if served is not None and UUID(str(served)) != UUID(str(deployment_id)):
            raise ValueError(
                "this reader serves a different deployment than the one asked for"
            )
        self._deployment_id = UUID(str(deployment_id))
        self._manifest_hash = str(load_manifest()["surface_manifest_hash"])
        self._reader = reader
        self._connect = connect
        self._audit = audit or AuditTrail.disabled()
        self._kills = kill_switches or KillSwitches()
        self._analytical_entitlement = analytical_entitlement

    def query_cypher(
        self,
        *,
        cypher: str,
        parameters: Mapping[str, object] | None = None,
        max_rows: int | None = None,
        tier: LimitTier = LimitTier.INTERACTIVE,
        principal: str = "agent",
        confirm: bool = False,
    ) -> QueryResult:
        """One read statement against the published snapshot."""
        return self._run(
            cypher=cypher,
            parameters=parameters or {},
            max_rows=max_rows,
            tier=tier,
            principal=principal,
            confirm=confirm,
            explain=False,
        )

    def explain_cypher(
        self,
        *,
        cypher: str,
        parameters: Mapping[str, object] | None = None,
        tier: LimitTier = LimitTier.INTERACTIVE,
        principal: str = "agent",
    ) -> QueryResult:
        """The engine's plan for a statement, without running it.

        The surface prepends `EXPLAIN` after the deny-scan has accepted the
        statement. This is the plan path only — ordinary `query_cypher`
        executes the statement directly and does not compile it twice.
        """
        return self._run(
            cypher=cypher,
            parameters=parameters or {},
            max_rows=None,
            tier=tier,
            principal=principal,
            confirm=False,
            explain=True,
        )

    # -- internals ---------------------------------------------------------

    def _run(
        self,
        *,
        cypher: str,
        parameters: Mapping[str, object],
        max_rows: int | None,
        tier: LimitTier,
        principal: str,
        confirm: bool,
        explain: bool,
    ) -> QueryResult:
        if tier is LimitTier.ANALYTICAL and not self._analytical_entitlement:
            tier = LimitTier.INTERACTIVE
        limits = TIER_LIMITS[tier]
        started = datetime.now(tz=UTC)
        clock = time.monotonic()
        request_id = uuid4()
        # `max_rows=0` asks for no rows; only an ABSENT bound takes the tier
        # default. Treating zero as unset answered a different question, and a
        # negative bound is not a smaller one — `min(-1, cap)` is -1, and
        # `rows[:-1]` keeps almost everything.
        requested_rows = limits.returned_rows_default if max_rows is None else max_rows
        row_cap = max(0, min(requested_rows, limits.returned_rows_hard))
        limits_model = ResultLimits(
            # The cap this request actually ran under, not the tier ceiling: a
            # disclosure that always names the ceiling tells the caller nothing
            # about the bound their rows were cut at.
            row_cap=row_cap,
            # §4.3 gives a default and a hard cap; a request that names neither
            # runs under the DEFAULT. Disclosing and enforcing the hard cap
            # would let an unremarkable query return eight times what the tier
            # says it returns.
            byte_cap=limits.returned_bytes_default,
            statement_timeout_ms=limits.statement_timeout_ms_default,
            analytical_tier=tier is LimitTier.ANALYTICAL,
        )
        statement_hash = ""
        snapshot: P2Snapshot | None = None
        engine_fault_class: str | None = None
        admitted = False
        try:
            self._check_request(cypher=cypher, parameters=parameters, limits=limits)
            if self._kills.blocked(
                deployment_id=self._deployment_id, principal=principal
            ):
                raise SandboxRejection(
                    code=QueryErrorCode.QUOTA_EXCEEDED,
                    message="the open query surface is disabled by the operator",
                )
            statement = validate_cypher(cypher)
            statement_hash = _statement_hash(statement.normalized_tokens, parameters)
            if confirm and self._connect is None:
                raise SandboxRejection(
                    code=QueryErrorCode.PG_UNAVAILABLE,
                    message=(
                        "confirmation needs a PostgreSQL connection and this"
                        " surface has none"
                    ),
                )
            admission = self._kills.admit(
                deployment_id=self._deployment_id,
                principal=principal,
                per_principal=limits.concurrent_per_principal,
                per_deployment=limits.concurrent_per_deployment,
                principal_seconds_per_minute=(
                    limits.principal_statement_seconds_per_minute
                ),
                deployment_seconds_per_minute=(
                    limits.deployment_statement_seconds_per_minute
                ),
            )
            if admission is not None:
                raise SandboxRejection(
                    code=(
                        QueryErrorCode.QUOTA_EXCEEDED
                        if "quota" in admission
                        else QueryErrorCode.CONCURRENCY_EXCEEDED
                    ),
                    message=admission,
                )
            admitted = True

            connection, snapshot = self._pinned_snapshot(started_at=started)
            try:
                text = f"EXPLAIN {statement.text}" if explain else statement.text
                result = self._execute(
                    connection=connection,
                    text=text,
                    parameters=parameters,
                    timeout_ms=limits.statement_timeout_ms_default,
                    row_cap=row_cap,
                    byte_cap=limits.returned_bytes_default,
                )
            finally:
                # `pinned()` leases a request-private connection because its
                # timeout is mutable. Never return that state to another query.
                connection.close()
            columns = tuple(
                ResultColumn(name=name, type=kind, nullable=True)
                for name, kind in zip(
                    result.column_names, result.column_types, strict=False
                )
            )
            rows, truncated, byte_truncated = _bounded(
                result.rows, row_cap=row_cap, byte_cap=limits.returned_bytes_default
            )
            confirmation: GraphConfirmation | None = None
            if confirm:
                rows, confirmation = self._confirm(rows=rows, columns=columns)

            encoded = sum(
                len(json.dumps(list(row), default=str).encode()) for row in rows
            )
            warnings: list[str] = []
            if snapshot.age_seconds > P2_FRESHNESS_WARNING_SECONDS:
                warnings.append(P2_STALE_WARNING)
            if confirmation is not None and confirmation.nominated == 0:
                warnings.append(NO_CONFIRMABLE_VALUES_WARNING)
            outcome = QueryResult(
                request_id=request_id,
                deployment_id=self._deployment_id,
                surface_manifest_hash=self._manifest_hash,
                query_hash=statement_hash,
                grade="snapshot_graph",
                query_language="cypher",
                # §4.4: a Cypher answer did not read the memory_v1 SQL schema,
                # and the engine exposes no authoritative structural parse
                # result for graph-reference metadata.
                query_space_schema=None,
                referenced_graph_types=None,
                referenced_graph_properties=None,
                execution_started_at=started,
                elapsed_ms=(time.monotonic() - clock) * 1000,
                columns=columns,
                rows=tuple(tuple(row) for row in rows),
                returned_row_count=len(rows),
                returned_byte_count=encoded,
                pg_snapshot_at=(
                    confirmation.pg_confirmed_at if confirmation is not None else None
                ),
                truncated=truncated or byte_truncated,
                truncation_reason=(
                    "byte_cap" if byte_truncated else ("row_cap" if truncated else None)
                ),
                empty_result=not rows,
                termination_reason="completed",
                warnings=tuple(warnings),
                limits=limits_model,
                p2_snapshot=snapshot,
                confirmation=confirmation,
            )
        except SandboxRejection as rejection:
            engine_fault_class = rejection.engine_fault_class
            outcome = self._failure(
                rejection,
                self._deployment_id,
                self._manifest_hash,
                request_id,
                started,
                clock,
                limits_model,
                query_hash=statement_hash,
                p2_snapshot=snapshot,
            )
        finally:
            if admitted:
                self._kills.record_spend(
                    deployment_id=self._deployment_id,
                    principal=principal,
                    seconds=time.monotonic() - clock,
                )
                self._kills.release(
                    deployment_id=self._deployment_id, principal=principal
                )
        self._audit.emit(
            outcome=outcome,
            principal=principal,
            engine_fault_class=engine_fault_class,
            graph_depth_cap=RECURSIVE_HOPS_MAX,
        )
        return outcome

    @staticmethod
    def _check_request(
        *, cypher: str, parameters: Mapping[str, object], limits: Any
    ) -> None:
        """The §4.3 request bounds, checked before anything is parsed."""
        if len(cypher.encode()) > CYPHER_TEXT_BYTES_MAX:
            raise SandboxRejection(
                code=QueryErrorCode.RESOURCE_LIMIT,
                message=f"Cypher text exceeds {CYPHER_TEXT_BYTES_MAX} bytes",
            )
        if len(parameters) > limits.parameters_max:
            raise SandboxRejection(
                code=QueryErrorCode.RESOURCE_LIMIT,
                message=f"at most {limits.parameters_max} bound parameters",
            )
        encoded = len(json.dumps(dict(parameters), default=str).encode())
        if encoded > limits.parameters_bytes:
            raise SandboxRejection(
                code=QueryErrorCode.RESOURCE_LIMIT,
                message="the bound parameters exceed their encoded byte cap",
            )

    def _pinned_snapshot(self, *, started_at: datetime) -> tuple[Any, P2Snapshot]:
        """The served connection and the provenance describing THAT generation.

        Read as one act, so a refresh between them cannot produce rows from one
        generation labelled with another's cut.
        """
        try:
            connection, snapshot_id, version, built_at = self._reader.pinned()
            try:
                snapshot = self._describe(
                    snapshot_id, version, built_at, started_at=started_at
                )
            except Exception:
                # The caller's closing block begins only after this method
                # returns. Release a lease whose provenance failed validation.
                connection.close()
                raise
            return connection, snapshot
        except SandboxRejection:
            raise
        except Exception as error:
            raise SandboxRejection(
                code=QueryErrorCode.P2_UNAVAILABLE,
                message="no published graph snapshot is available",
                engine_fault_class="p2_snapshot",
            ) from error

    @staticmethod
    def _describe(
        snapshot_id: Any,
        version: Any,
        built_at: datetime | None,
        *,
        started_at: datetime,
    ) -> P2Snapshot:
        if snapshot_id is None or version is None or built_at is None:
            raise SandboxRejection(
                code=QueryErrorCode.P2_UNAVAILABLE,
                message="the published graph snapshot has incomplete provenance",
                engine_fault_class="p2_snapshot",
            )
        return P2Snapshot(
            snapshot_id=snapshot_id,
            snapshot_version=str(version),
            built_at=built_at,
            age_seconds=max(0.0, (started_at - built_at).total_seconds()),
        )

    @staticmethod
    def _execute(
        *,
        connection: Any,
        text: str,
        parameters: Mapping[str, object],
        timeout_ms: int,
        row_cap: int,
        byte_cap: int,
    ) -> _EngineResult:
        """Run the statement read-only, bounded, with parameters bound."""
        try:
            connection.set_query_timeout(timeout_ms)
        except Exception as error:
            raise SandboxRejection(
                code=QueryErrorCode.EXECUTION_ERROR,
                message="the graph engine could not apply the statement timeout",
                engine_fault_class="ladybug_timeout_setup",
            ) from error
        try:
            answer = connection.execute(text, dict(parameters))
        except RuntimeError as error:
            message = str(error)
            if is_read_only_refusal(message):
                # A mutation reached the engine. `read_only=True` already
                # stopped it; map that to the stated refusal so the caller
                # does not see a raw connection exception as an execution
                # failure.
                raise SandboxRejection(
                    code=QueryErrorCode.CYPHER_NOT_ALLOWED,
                    message=(
                        "this surface reads the published graph and never changes it"
                    ),
                    engine_fault_class="ladybug_read_only",
                ) from error
            if "Parser exception" in message or "Binder exception" in message:
                # The pinned dialect does not implement this. It is NOT
                # rewritten into something the dialect does implement.
                raise SandboxRejection(
                    code=QueryErrorCode.CYPHER_PARSE_ERROR,
                    message="the pinned Cypher dialect does not accept this statement",
                    engine_fault_class="ladybug_parse",
                ) from error
            if "Interrupt" in message or "timeout" in message.lower():
                raise SandboxRejection(
                    code=QueryErrorCode.STATEMENT_TIMEOUT,
                    message="the statement exceeded its timeout",
                    engine_fault_class="ladybug_timeout",
                ) from error
            raise SandboxRejection(
                code=QueryErrorCode.EXECUTION_ERROR,
                message="the statement failed during execution",
                engine_fault_class="ladybug_runtime",
            ) from error
        except Exception as error:
            raise SandboxRejection(
                code=QueryErrorCode.EXECUTION_ERROR,
                message="the statement failed during execution",
                engine_fault_class="ladybug_runtime",
            ) from error
        names = tuple(answer.get_column_names())
        types = tuple(str(kind) for kind in answer.get_column_data_types())
        if any(_is_internal_id_type(kind) for kind in types):
            raise SandboxRejection(
                code=QueryErrorCode.CYPHER_NOT_ALLOWED,
                message="engine-internal identifiers are not public graph values",
            )
        rows: list[list[object]] = []
        spent = 0
        # STOP at the caps rather than draining and trimming afterwards.
        # Materialising every row first made the row and byte caps disclosure
        # numbers rather than bounds: `UNWIND RANGE(1, 100000) AS n RETURN n`
        # with max_rows=1 pulled a hundred thousand rows into memory to return
        # one. One row past the cap is kept so truncation can be reported
        # honestly.
        while answer.has_next() and len(rows) <= row_cap and spent <= byte_cap:
            row = [_public_value(value) for value in answer.get_next()]
            spent += len(json.dumps(row, default=str).encode())
            rows.append(row)
        return _EngineResult(column_names=names, column_types=types, rows=rows)

    def _confirm(
        self, *, rows: Sequence[Sequence[object]], columns: Sequence[ResultColumn]
    ) -> tuple[list[Sequence[object]], GraphConfirmation]:
        """Check live membership of top-level entity and relation ids (§3.5).

        Only `Entity` nodes and `RELATES` relationships are confirmable. A row
        carrying one that fails drops as a unit; every other projected value —
        `Document`, `MENTIONED_IN`, `DOC_CROSSREF`, aggregates, collections —
        passes through snapshot-scoped, which is what the disclosure says.
        """
        wanted: dict[str, set[str]] = {"entity": set(), "relation": set()}
        per_row: list[dict[str, set[str]]] = []
        for row in rows:
            found: dict[str, set[str]] = {"entity": set(), "relation": set()}
            for value, column in zip(row, columns, strict=False):
                kind, identifier = _confirmable(value, logical_type=column.type)
                if kind is not None and identifier is not None:
                    found[kind].add(identifier)
                    wanted[kind].add(identifier)
            per_row.append(found)

        nominated = len(wanted["entity"]) + len(wanted["relation"])
        # `requested` counts the confirmable IDS that were asked about, not the
        # rows they arrived in: §3.5 describes unique confirmable-ID counts,
        # and counting rows made `requested` and `nominated` disagree for any
        # row projecting more than one.
        requested = nominated
        if nominated == 0:
            # PostgreSQL is still consulted, so a caller who asked for
            # confirmation learns whether it could have happened. Returning
            # zeros without touching the database would report the same thing
            # whether the database was reachable or not.
            confirmed_at = self._confirmation_instant()
            return list(rows), GraphConfirmation(
                requested=0,
                nominated=0,
                confirmed=0,
                dropped_stale=0,
                pg_confirmed_at=confirmed_at,
            )
        assert self._connect is not None
        live: dict[str, set[str]] = {"entity": set(), "relation": set()}
        try:
            with self._connect() as connection:
                # One snapshot for the whole confirmation. Under READ COMMITTED
                # each statement takes a new one, so a commit between the
                # entity check and the relation check could keep a row whose
                # two halves were never simultaneously live at the instant this
                # result claims to have checked.
                connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
                confirmed_at = connection.execute(
                    "SELECT transaction_timestamp()"
                ).fetchone()
                for kind, identifiers in wanted.items():
                    if not identifiers:
                        continue
                    live[kind] = {
                        str(found[0])
                        for found in connection.execute(
                            _CONFIRM_SQL[kind].encode(),
                            {
                                "deployment": str(self._deployment_id),
                                "ids": sorted(identifiers),
                            },
                        ).fetchall()
                    }
                connection.commit()
        except psycopg.Error as error:
            raise SandboxRejection(
                code=QueryErrorCode.PG_UNAVAILABLE,
                message="live membership could not be checked",
                engine_fault_class="postgresql_confirmation",
            ) from error

        kept: list[Sequence[object]] = []
        for row, found in zip(rows, per_row, strict=False):
            if all(found[kind] <= live[kind] for kind in found):
                kept.append(row)
        confirmed = sum(len(live[kind]) for kind in live)
        return kept, GraphConfirmation(
            requested=requested,
            nominated=nominated,
            confirmed=confirmed,
            dropped_stale=nominated - confirmed,
            pg_confirmed_at=confirmed_at[0] if confirmed_at else None,
        )

    def _confirmation_instant(self) -> datetime | None:
        """Reach PostgreSQL even when there was nothing to confirm.

        A caller who asked for confirmation and got zeros should be able to
        tell "nothing in this result was confirmable" from "the database was
        not reachable", and only actually asking distinguishes them.
        """
        assert self._connect is not None
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT statement_timestamp()").fetchone()
        except psycopg.Error as error:
            raise SandboxRejection(
                code=QueryErrorCode.PG_UNAVAILABLE,
                message="live membership could not be checked",
                engine_fault_class="postgresql_confirmation",
            ) from error
        return row[0] if row else None

    @staticmethod
    def _failure(
        rejection: SandboxRejection,
        deployment_id: Any,
        manifest_hash: str,
        request_id: Any,
        started: datetime,
        clock: float,
        limits_model: ResultLimits,
        *,
        query_hash: str = "",
        p2_snapshot: P2Snapshot | None = None,
    ) -> QueryResult:
        return QueryResult(
            request_id=request_id,
            deployment_id=deployment_id,
            surface_manifest_hash=manifest_hash,
            query_hash=query_hash,
            grade="snapshot_graph",
            query_language="cypher",
            # §4.4: a Cypher answer did not read the memory_v1 SQL schema, and
            # naming it would tell a caller their rows came from views they
            # never queried.
            query_space_schema=None,
            execution_started_at=started,
            elapsed_ms=(time.monotonic() - clock) * 1000,
            termination_reason=(
                "rejected"
                if rejection.code
                in (
                    QueryErrorCode.CYPHER_NOT_ALLOWED,
                    QueryErrorCode.CYPHER_PARSE_ERROR,
                    QueryErrorCode.RESOURCE_LIMIT,
                    QueryErrorCode.QUOTA_EXCEEDED,
                    QueryErrorCode.CONCURRENCY_EXCEEDED,
                )
                else "failed"
            ),
            error_code=rejection.code,
            error_message=rejection.message,
            warnings=(
                (P2_STALE_WARNING,)
                if p2_snapshot is not None
                and p2_snapshot.age_seconds > P2_FRESHNESS_WARNING_SECONDS
                else ()
            ),
            limits=limits_model,
            p2_snapshot=p2_snapshot,
        )


@dataclass(frozen=True)
class _EngineResult:
    column_names: tuple[str, ...]
    column_types: tuple[str, ...]
    rows: list[list[object]]


def is_read_only_refusal(message: str) -> bool:
    """Whether `message` is the pinned engine's read-only write refusal.

    One place. The pin test asserts the live engine still produces
    `READ_ONLY_REFUSAL` for a known mutation; every mapping path goes through
    this check so a wording change fails loudly instead of reclassifying
    writes as execution errors.
    """
    # EXACT match, not a substring. A caller can put any text in an error they
    # raise themselves — `RETURN error('Connection exception: Cannot execute
    # write operations in a read-only database!')` — and the engine prefixes
    # that with "Runtime exception: ". A substring test read the forgery as a
    # refusal, which would tell a caller their own error was the surface
    # declining to run a write.
    return message.strip() == READ_ONLY_REFUSAL


def _public_value(value: object) -> object:
    """One engine value as something a caller can keep.

    Structural values keep their labels and exposed properties and lose the
    engine's physical offsets, which are stable only inside one built
    generation — publishing one would invite a caller to store it and use it
    against the next snapshot, where it addresses something else.
    """
    if isinstance(value, dict):
        return {
            key: _public_value(inner)
            for key, inner in value.items()
            if key.lower() not in _ENGINE_INTERNAL_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_public_value(inner) for inner in value]
    return value


def _confirmable(value: object, *, logical_type: str) -> tuple[str | None, str | None]:
    """The confirmable kind and id of one projected value, if it has one."""
    if not isinstance(value, dict):
        return None, None
    engine_kind = logical_type.strip().upper()
    label = next((value[key] for key in _LABEL_KEYS if key in value), None)
    if engine_kind == "NODE" and label == "Entity":
        return "entity", _identifier(value.get("id"))
    if engine_kind == "REL" and label == "RELATES":
        return "relation", _identifier(value.get("relation_id"))
    return None, None


def _is_internal_id_type(logical_type: str) -> bool:
    """Whether an engine result column is a physical graph address."""
    return _INTERNAL_ID_TYPE.search(logical_type.strip()) is not None


def _identifier(value: object) -> str | None:
    """One id as text, whichever Python type the engine handed it back as."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        try:
            return str(UUID(value))
        except ValueError:
            return None
    return None


def _bounded(
    rows: Sequence[Sequence[object]], *, row_cap: int, byte_cap: int
) -> tuple[list[Sequence[object]], bool, bool]:
    """Rows within both the row and encoded-byte caps, and what was cut."""
    truncated = len(rows) > row_cap
    kept: list[Sequence[object]] = []
    spent = 0
    byte_truncated = False
    for row in rows[:row_cap]:
        size = len(json.dumps(list(row), default=str).encode())
        if spent + size > byte_cap:
            byte_truncated = True
            break
        spent += size
        kept.append(row)
    return kept, truncated, byte_truncated


def _statement_hash(
    normalized_tokens: Sequence[str], parameters: Mapping[str, object]
) -> str:
    """Hash normalized lexical identity plus canonical Ladybug type families."""
    identity = {
        "tokens": list(normalized_tokens),
        "parameters": [
            [name, _cypher_type_family(value)]
            for name, value in sorted(parameters.items())
        ],
    }
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _cypher_type_family(value: object) -> str:
    """The pinned engine logical family of one bound Python value."""
    if value is None:
        return "ANY"
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, bytes):
        return "BLOB"
    if isinstance(value, str):
        return "STRING"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, Decimal):
        return "DECIMAL"
    if isinstance(value, UUID):
        return "UUID"
    if isinstance(value, datetime):
        return "TIMESTAMP"
    if isinstance(value, date):
        return "DATE"
    if isinstance(value, datetime_time):
        return "TIME_TZ" if value.tzinfo is not None else "TIME"
    if isinstance(value, list):
        members = sorted({_cypher_type_family(member) for member in value})
        element = "ANY" if not members else "|".join(members)
        return f"LIST<{element}>"
    if isinstance(value, Mapping):
        fields = ",".join(
            f"{name}:{_cypher_type_family(member)}"
            for name, member in sorted(value.items(), key=lambda item: str(item[0]))
        )
        return f"STRUCT<{fields}>"
    return "UNSUPPORTED"
