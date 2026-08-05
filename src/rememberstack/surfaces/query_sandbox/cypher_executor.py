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
from datetime import datetime
from datetime import UTC
import hashlib
import json
import time
from typing import Any
from typing import Final
from uuid import UUID
from uuid import uuid4

import psycopg

from rememberstack.spine.query_space.manifest import load_manifest
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
        self._deployment_id = deployment_id
        self._manifest_hash = str(load_manifest()["surface_manifest_hash"])
        self._reader = reader
        self._connect = connect
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

        try:
            self._check_request(cypher=cypher, parameters=parameters, limits=limits)
            statement = validate_cypher(cypher)
            if confirm and self._connect is None:
                raise SandboxRejection(
                    code=QueryErrorCode.PG_UNAVAILABLE,
                    message=(
                        "confirmation needs a PostgreSQL connection and this"
                        " surface has none"
                    ),
                )
            connection, snapshot = self._pinned_snapshot()
            text = f"EXPLAIN {statement.text}" if explain else statement.text
            result = self._execute(
                connection=connection,
                text=text,
                parameters=parameters,
                timeout_ms=limits.statement_timeout_ms_default,
                row_cap=row_cap,
                byte_cap=limits.returned_bytes_default,
            )
        except SandboxRejection as rejection:
            return self._failure(
                rejection,
                self._deployment_id,
                self._manifest_hash,
                request_id,
                started,
                clock,
                limits_model,
                principal,
            )

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
            try:
                rows, confirmation = self._confirm(rows=rows, columns=columns)
            except SandboxRejection as rejection:
                return self._failure(
                    rejection,
                    self._deployment_id,
                    self._manifest_hash,
                    request_id,
                    started,
                    clock,
                    limits_model,
                    principal,
                )

        encoded = sum(len(json.dumps(list(row), default=str).encode()) for row in rows)
        outcome = QueryResult(
            request_id=request_id,
            deployment_id=self._deployment_id,
            surface_manifest_hash=self._manifest_hash,
            query_hash=_statement_hash(statement.text, parameters),
            grade="snapshot_graph",
            query_language="cypher",
            # §4.4: a Cypher answer did not read the memory_v1 SQL schema, and
            # naming it would tell a caller their rows came from views they
            # never queried. Graph-reference metadata is unavailable rather
            # than known-empty: the engine exposes no structural parse result,
            # and a text guesser is not an authority.
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
            warnings=(
                (NO_CONFIRMABLE_VALUES_WARNING,)
                if confirmation is not None and confirmation.nominated == 0
                else ()
            ),
            limits=limits_model,
            p2_snapshot=snapshot,
            confirmation=confirmation,
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

    def _pinned_snapshot(self) -> tuple[Any, P2Snapshot | None]:
        """The served connection and the provenance describing THAT generation.

        Read as one act, so a refresh between them cannot produce rows from one
        generation labelled with another's cut.
        """
        try:
            if hasattr(self._reader, "pinned"):
                connection, snapshot_id, version, built_at = self._reader.pinned()
                return connection, self._describe(snapshot_id, version, built_at)
            # A reader without the paired accessor still serves, but its
            # provenance cannot be pinned to the connection; say nothing rather
            # than say something that might describe a different generation.
            return self._reader.connection(), self._provenance()
        except SandboxRejection:
            raise
        except Exception as error:
            raise SandboxRejection(
                code=QueryErrorCode.P2_UNAVAILABLE,
                message="no published graph snapshot is available",
            ) from error

    @staticmethod
    def _describe(
        snapshot_id: Any, version: Any, built_at: datetime | None
    ) -> P2Snapshot | None:
        if snapshot_id is None or version is None or built_at is None:
            return None
        return P2Snapshot(
            snapshot_id=snapshot_id,
            snapshot_version=str(version),
            built_at=built_at,
            age_seconds=(datetime.now(tz=UTC) - built_at).total_seconds(),
        )

    def _provenance(self) -> P2Snapshot | None:
        """What the snapshot is, and how old the cut it projects is."""
        version = getattr(self._reader, "version", None)
        built_at = getattr(self._reader, "built_at", None)
        snapshot_id = getattr(self._reader, "snapshot_id", None)
        if version is None or built_at is None or snapshot_id is None:
            return None
        return P2Snapshot(
            snapshot_id=snapshot_id,
            snapshot_version=str(version),
            built_at=built_at,
            age_seconds=(datetime.now(tz=UTC) - built_at).total_seconds(),
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
        except Exception:  # an engine build without the knob still runs
            pass
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
                ) from error
            if "Parser exception" in message or "Binder exception" in message:
                # The pinned dialect does not implement this. It is NOT
                # rewritten into something the dialect does implement.
                raise SandboxRejection(
                    code=QueryErrorCode.CYPHER_PARSE_ERROR,
                    message="the pinned Cypher dialect does not accept this statement",
                ) from error
            if "Interrupt" in message or "timeout" in message.lower():
                raise SandboxRejection(
                    code=QueryErrorCode.STATEMENT_TIMEOUT,
                    message="the statement exceeded its timeout",
                ) from error
            raise SandboxRejection(
                code=QueryErrorCode.EXECUTION_ERROR,
                message="the statement failed during execution",
            ) from error
        except Exception as error:
            raise SandboxRejection(
                code=QueryErrorCode.EXECUTION_ERROR,
                message="the statement failed during execution",
            ) from error
        names = tuple(answer.get_column_names())
        types = tuple(str(kind) for kind in answer.get_column_data_types())
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
            for value in row:
                kind, identifier = _confirmable(value)
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
        principal: str,
    ) -> QueryResult:
        return QueryResult(
            request_id=request_id,
            deployment_id=deployment_id,
            surface_manifest_hash=manifest_hash,
            query_hash="",
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
                )
                else "failed"
            ),
            error_code=rejection.code,
            error_message=rejection.message,
            limits=limits_model,
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


def _confirmable(value: object) -> tuple[str | None, str | None]:
    """The confirmable kind and id of one projected value, if it has one."""
    if not isinstance(value, dict):
        return None, None
    label = next((value[key] for key in _LABEL_KEYS if key in value), None)
    if label == "Entity":
        return "entity", _identifier(value.get("id"))
    if label == "RELATES":
        return "relation", _identifier(value.get("relation_id"))
    return None, None


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


def _statement_hash(text: str, parameters: Mapping[str, object]) -> str:
    """A stable identity for the statement and the shape of its parameters."""
    shape = sorted((name, type(value).__name__) for name, value in parameters.items())
    return hashlib.sha256(f"{text}|params={shape}".encode()).hexdigest()
