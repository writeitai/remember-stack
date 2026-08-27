"""Typed live-graph reads over PostgreSQL 19 SQL/PGQ and bounded helpers.

One-hop neighborhoods execute static SQL/PGQ. Deeper neighborhoods and
shortest entity or document paths execute deployment-first
PostgreSQL helpers. Hydration shares one read-only repeatable-read transaction,
so every answer is one MVCC cut and one temporal instant without snapshots.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from datetime import UTC
from threading import BoundedSemaphore
from time import monotonic
from typing import cast
from uuid import UUID

from sqlalchemy import bindparam
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from rememberstack.model import AsOfTemporalScope
from rememberstack.model import current_temporal_scope
from rememberstack.model import Envelope
from rememberstack.model import FactSupport
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import GraphEdge
from rememberstack.model import GraphNode
from rememberstack.model import GraphPath
from rememberstack.model import Negative
from rememberstack.model import NegativeKind
from rememberstack.model import Truncation
from rememberstack.spine.postgres_graph_sql import HISTORY_NEIGHBORHOOD_GUARD
from rememberstack.spine.postgres_graph_sql import HISTORY_NEIGHBORHOOD_PGQ

DEFAULT_NEIGHBORHOOD_CAP = 500
MAX_NEIGHBORHOOD_DEPTH = 4
MAX_PATH_DEPTH = 6
MAX_CITATION_DEPTH = 6
DEFAULT_EXPANSION_BUDGET = 2_000
DEFAULT_FRONTIER_BUDGET = 1_000
DEFAULT_TIME_BUDGET_MS = 1_000
DEFAULT_GRAPH_WORK_MEM_KIB = 16_384
DEFAULT_GRAPH_CONCURRENCY = 2
DEFAULT_GRAPH_POOL_WAIT_SECONDS = 1.0


class GraphBusyError(RuntimeError):
    """The bounded graph-expansion slots were unavailable in time."""


class GraphHydrationError(RuntimeError):
    """A traversal row no longer matches the authority rows in its MVCC cut."""


def _deadline_timeout_ms(*, default_ms: int, deadline: float | None) -> int:
    """Clamp one PostgreSQL timeout to a caller's remaining wall-clock budget."""
    if deadline is None:
        return default_ms
    remaining_ms = int((deadline - monotonic()) * 1_000)
    if remaining_ms <= 0:
        raise TimeoutError("live graph operation deadline expired")
    return min(default_ms, remaining_ms)


class GraphQueries:
    """Live entity and document traversal bound to one deployment."""

    def __init__(
        self,
        *,
        engine: Engine,
        deployment_id: UUID,
        work_mem_kib: int = DEFAULT_GRAPH_WORK_MEM_KIB,
        max_concurrency: int = DEFAULT_GRAPH_CONCURRENCY,
        pool_wait_seconds: float = DEFAULT_GRAPH_POOL_WAIT_SECONDS,
    ) -> None:
        """Bind the authoritative PostgreSQL pool and deployment scope."""
        if work_mem_kib <= 0:
            raise ValueError("graph work_mem_kib must be positive")
        if max_concurrency <= 0:
            raise ValueError("graph max_concurrency must be positive")
        if pool_wait_seconds <= 0:
            raise ValueError("graph pool_wait_seconds must be positive")
        self._engine = engine
        self._deployment_id = deployment_id
        self._work_mem_kib = work_mem_kib
        self._pool_wait_seconds = pool_wait_seconds
        self._slots = BoundedSemaphore(value=max_concurrency)

    def neighborhood(
        self,
        *,
        entity_id: UUID,
        hops: int = 2,
        predicates: tuple[str, ...] = (),
        valid_at: datetime | None = None,
        believed_at: datetime | None = None,
        limit: int = DEFAULT_NEIGHBORHOOD_CAP,
        continuation: str | None = None,
        include_paths: bool = False,
        _deadline: float | None = None,
        _connection: Connection | None = None,
    ) -> Envelope:
        """Return minimum-hop live neighbors and optionally their paths."""
        _validate_clocks(valid_at=valid_at, believed_at=believed_at)
        _validate_predicates(predicates=predicates)
        if not 1 <= hops <= MAX_NEIGHBORHOOD_DEPTH:
            raise ValueError(
                f"neighborhood hops must be between 1 and {MAX_NEIGHBORHOOD_DEPTH}"
            )
        if not 1 <= limit <= DEFAULT_NEIGHBORHOOD_CAP:
            raise ValueError(
                f"neighborhood limit must be between 1 and {DEFAULT_NEIGHBORHOOD_CAP}"
            )
        offset = _decode_continuation(continuation)
        if offset is None:
            return self._boundary(
                explanation="the continuation cursor is not a live-graph cursor",
                workaround="restart the traversal without a continuation cursor",
            )
        transaction = (
            self._transaction(deadline=_deadline)
            if _connection is None
            else self._shared_transaction(connection=_connection, deadline=_deadline)
        )
        with transaction as connection:
            evaluation = _evaluation_instant(connection=connection)
            if not _entity_exists(
                connection=connection,
                deployment_id=self._deployment_id,
                entity_id=entity_id,
            ):
                return self._unknown(identifier=entity_id, kind="entity", at=evaluation)
            applied_valid = valid_at or evaluation
            applied_believed = believed_at or evaluation
            fetch = min(offset + limit + 1, DEFAULT_NEIGHBORHOOD_CAP)
            parameters: dict[str, object] = {
                "deployment_id": self._deployment_id,
                "max_depth": hops,
                "predicates": list(predicates) or None,
                "valid_at": applied_valid,
                "believed_at": applied_believed,
                "max_results": limit if hops == 1 else fetch,
                "expansion_budget": DEFAULT_EXPANSION_BUDGET,
                "frontier_budget": DEFAULT_FRONTIER_BUDGET,
                "time_budget_ms": DEFAULT_TIME_BUDGET_MS,
            }
            if hops == 1:
                parameters.update({"anchor_id": entity_id, "result_offset": offset})
                raw = _shallow_neighborhood_rows(
                    connection=connection, parameters=parameters
                )
            else:
                parameters.update({"entity_id": entity_id})
                raw = _rows(
                    connection=connection,
                    statement=_NEIGHBORHOOD_HELPER,
                    parameters=parameters,
                )
            data, status = _split_status(rows=raw)
            selected = data[:limit] if hops == 1 else data[offset : offset + limit]
            more = len(data) > limit if hops == 1 else len(data) > offset + limit
            if include_paths:
                paths = _hydrate_entity_paths(
                    connection=connection,
                    deployment_id=self._deployment_id,
                    rows=selected,
                    valid_at=applied_valid,
                    believed_at=applied_believed,
                )
                nodes = tuple(path.nodes[-1] for path in paths)
            else:
                paths = ()
                nodes = _hydrate_neighbor_nodes(
                    connection=connection,
                    deployment_id=self._deployment_id,
                    rows=selected,
                )
        truncated = bool(status["truncated"]) or more
        if not nodes and offset == 0 and not truncated:
            return self._empty(
                explanation=(
                    f"entity {entity_id} exists but no neighbor within {hops} hop(s) "
                    "satisfies the requested filters"
                ),
                valid_at=applied_valid,
                believed_at=applied_believed,
                at=evaluation,
            )
        return _envelope(
            grain=Grain.FACT,
            temporal_scope=_temporal_scope(
                valid_at=applied_valid,
                believed_at=applied_believed,
                evaluated_at=evaluation,
            ),
            nodes=nodes,
            paths=paths,
            edges=_unique_edges(paths=paths),
            freshness=Freshness(pg_live_ts=evaluation),
            truncation=Truncation(
                truncated=truncated,
                returned=len(nodes),
                estimated_total=max(
                    int(status["returned_paths"]), offset + len(nodes) + int(more)
                ),
                total_is_exact=not truncated,
                continuation=(
                    _encode_continuation(offset + len(nodes)) if more else None
                ),
                reason=(
                    str(status["truncation_reason"])
                    if status["truncation_reason"] is not None
                    else ("result_budget" if more else None)
                ),
            ),
        )

    def path(
        self,
        *,
        from_entity_id: UUID,
        to_entity_id: UUID,
        max_hops: int = 4,
        valid_at: datetime | None = None,
        believed_at: datetime | None = None,
        predicates: tuple[str, ...] = (),
    ) -> Envelope:
        """Return bounded equal-length shortest paths between two entities."""
        _validate_clocks(valid_at=valid_at, believed_at=believed_at)
        _validate_predicates(predicates=predicates)
        if not 1 <= max_hops <= MAX_PATH_DEPTH:
            raise ValueError(f"path max_hops must be between 1 and {MAX_PATH_DEPTH}")
        with self._transaction() as connection:
            evaluation = _evaluation_instant(connection=connection)
            for identifier in (from_entity_id, to_entity_id):
                if not _entity_exists(
                    connection=connection,
                    deployment_id=self._deployment_id,
                    entity_id=identifier,
                ):
                    return self._unknown(
                        identifier=identifier, kind="entity", at=evaluation
                    )
            applied_valid = valid_at or evaluation
            applied_believed = believed_at or evaluation
            raw = _rows(
                connection=connection,
                statement=_PATH_HELPER,
                parameters={
                    "deployment_id": self._deployment_id,
                    "from_entity_id": from_entity_id,
                    "to_entity_id": to_entity_id,
                    "max_depth": max_hops,
                    "predicates": list(predicates) or None,
                    "valid_at": applied_valid,
                    "believed_at": applied_believed,
                    "max_paths": 10,
                    "expansion_budget": DEFAULT_EXPANSION_BUDGET,
                    "frontier_budget": DEFAULT_FRONTIER_BUDGET,
                    "time_budget_ms": DEFAULT_TIME_BUDGET_MS,
                },
            )
            data, status = _split_status(rows=raw)
            paths = _hydrate_entity_paths(
                connection=connection,
                deployment_id=self._deployment_id,
                rows=data,
                valid_at=applied_valid,
                believed_at=applied_believed,
            )
        if not paths and not bool(status["truncated"]):
            return self._empty(
                explanation=(
                    f"no path from {from_entity_id} to {to_entity_id} exists within "
                    f"{max_hops} hop(s) under the requested clocks"
                ),
                valid_at=applied_valid,
                believed_at=applied_believed,
                at=evaluation,
            )
        return _envelope(
            grain=Grain.FACT,
            temporal_scope=_temporal_scope(
                valid_at=applied_valid,
                believed_at=applied_believed,
                evaluated_at=evaluation,
            ),
            nodes=_unique_nodes(paths=paths),
            paths=paths,
            edges=_unique_edges(paths=paths),
            freshness=Freshness(pg_live_ts=evaluation),
            truncation=_status_truncation(status=status, returned=len(paths)),
        )

    def citation_path(
        self, *, from_doc_id: UUID, to_doc_id: UUID, max_hops: int = 6
    ) -> Envelope:
        """Return directed citation chains from one live document to another."""
        if not 1 <= max_hops <= MAX_CITATION_DEPTH:
            raise ValueError(
                f"citation max_hops must be between 1 and {MAX_CITATION_DEPTH}"
            )
        with self._transaction() as connection:
            evaluation = _evaluation_instant(connection=connection)
            for identifier in (from_doc_id, to_doc_id):
                if not _document_exists(
                    connection=connection,
                    deployment_id=self._deployment_id,
                    doc_id=identifier,
                ):
                    return self._unknown(
                        identifier=identifier, kind="document", at=evaluation
                    )
            raw = _rows(
                connection=connection,
                statement=_CITATION_HELPER,
                parameters={
                    "deployment_id": self._deployment_id,
                    "from_doc_id": from_doc_id,
                    "to_doc_id": to_doc_id,
                    "max_depth": max_hops,
                    "max_paths": 10,
                    "expansion_budget": DEFAULT_EXPANSION_BUDGET,
                    "frontier_budget": DEFAULT_FRONTIER_BUDGET,
                    "time_budget_ms": DEFAULT_TIME_BUDGET_MS,
                },
            )
            data, status = _split_status(rows=raw)
            paths = _hydrate_citation_paths(
                connection=connection, deployment_id=self._deployment_id, rows=data
            )
        if not paths and not bool(status["truncated"]):
            return self._empty(
                explanation=(
                    f"no directed citation path from {from_doc_id} to {to_doc_id} "
                    f"exists within {max_hops} hop(s)"
                ),
                valid_at=evaluation,
                believed_at=evaluation,
                at=evaluation,
            )
        return _envelope(
            grain=Grain.FACT,
            temporal_scope=current_temporal_scope(evaluated_at=evaluation),
            nodes=_unique_nodes(paths=paths),
            paths=paths,
            edges=_unique_edges(paths=paths),
            freshness=Freshness(pg_live_ts=evaluation),
            truncation=_status_truncation(status=status, returned=len(paths)),
        )

    @contextmanager
    def _transaction(self, *, deadline: float | None = None) -> Iterator[Connection]:
        """Open a bounded read-only repeatable-read graph transaction."""
        pool_wait_seconds = self._pool_wait_seconds
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("live graph operation deadline expired")
            pool_wait_seconds = min(pool_wait_seconds, remaining)
        if not self._slots.acquire(timeout=pool_wait_seconds):
            raise GraphBusyError("live graph concurrency limit reached")
        try:
            try:
                with self._engine.connect().execution_options(
                    isolation_level="REPEATABLE READ"
                ) as connection:
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    statement_timeout_ms = _deadline_timeout_ms(
                        default_ms=5_000, deadline=deadline
                    )
                    transaction_timeout_ms = _deadline_timeout_ms(
                        default_ms=6_000, deadline=deadline
                    )
                    connection.exec_driver_sql(
                        f"SET LOCAL statement_timeout = '{statement_timeout_ms}ms'"
                    )
                    connection.exec_driver_sql(
                        f"SET LOCAL transaction_timeout = '{transaction_timeout_ms}ms'"
                    )
                    quoted_role = str(
                        connection.execute(
                            text(
                                "SELECT quote_ident("
                                "'rememberstack_graph_' || current_database())"
                            )
                        ).scalar_one()
                    )
                    connection.exec_driver_sql("SET LOCAL lock_timeout = '500ms'")
                    connection.exec_driver_sql(
                        "SET LOCAL idle_in_transaction_session_timeout = '5s'"
                    )
                    connection.exec_driver_sql("SET LOCAL temp_file_limit = '65536kB'")
                    connection.exec_driver_sql(
                        "SET LOCAL max_parallel_workers_per_gather = 0"
                    )
                    connection.exec_driver_sql("SET LOCAL enable_seqscan = off")
                    connection.exec_driver_sql(
                        "SET LOCAL search_path = memory_v1, pg_catalog"
                    )
                    connection.exec_driver_sql(
                        f"SET LOCAL work_mem = '{self._work_mem_kib}kB'"
                    )
                    connection.exec_driver_sql(f"SET LOCAL ROLE {quoted_role}")
                    try:
                        yield connection
                    finally:
                        connection.rollback()
            except SQLAlchemyTimeoutError as error:
                raise GraphBusyError("live graph connection pool timed out") from error
        finally:
            self._slots.release()

    @contextmanager
    def _shared_transaction(
        self, *, connection: Connection, deadline: float | None
    ) -> Iterator[Connection]:
        """Apply graph admission and limits inside a caller-owned MVCC snapshot."""
        pool_wait_seconds = self._pool_wait_seconds
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("live graph operation deadline expired")
            pool_wait_seconds = min(pool_wait_seconds, remaining)
        if not self._slots.acquire(timeout=pool_wait_seconds):
            raise GraphBusyError("live graph concurrency limit reached")
        try:
            isolation = connection.exec_driver_sql(
                "SHOW transaction_isolation"
            ).scalar_one()
            read_only = connection.exec_driver_sql(
                "SHOW transaction_read_only"
            ).scalar_one()
            if isolation != "repeatable read" or read_only != "on":
                raise RuntimeError(
                    "shared graph reads require one read-only repeatable-read transaction"
                )
            statement_timeout_ms = _deadline_timeout_ms(
                default_ms=5_000, deadline=deadline
            )
            transaction_timeout_ms = _deadline_timeout_ms(
                default_ms=6_000, deadline=deadline
            )
            connection.exec_driver_sql(
                f"SET LOCAL statement_timeout = '{statement_timeout_ms}ms'"
            )
            connection.exec_driver_sql(
                f"SET LOCAL transaction_timeout = '{transaction_timeout_ms}ms'"
            )
            connection.exec_driver_sql("SET LOCAL lock_timeout = '500ms'")
            connection.exec_driver_sql(
                "SET LOCAL idle_in_transaction_session_timeout = '5s'"
            )
            connection.exec_driver_sql("SET LOCAL temp_file_limit = '65536kB'")
            connection.exec_driver_sql("SET LOCAL max_parallel_workers_per_gather = 0")
            connection.exec_driver_sql("SET LOCAL enable_seqscan = off")
            connection.exec_driver_sql("SET LOCAL enable_nestloop = DEFAULT")
            connection.exec_driver_sql("SET LOCAL join_collapse_limit = DEFAULT")
            connection.exec_driver_sql("SET LOCAL from_collapse_limit = DEFAULT")
            connection.exec_driver_sql(f"SET LOCAL work_mem = '{self._work_mem_kib}kB'")
            yield connection
        finally:
            self._slots.release()

    def _unknown(self, *, identifier: UUID, kind: str, at: datetime) -> Envelope:
        """Return the typed negative for an absent live graph endpoint."""
        return _envelope(
            grain=Grain.FACT,
            freshness=Freshness(pg_live_ts=at),
            negative=Negative(
                kind=NegativeKind.UNKNOWN_ENTITY,
                explanation=f"{kind} {identifier} is not present in the live graph",
                workaround=(
                    "resolve the identifier first, or verify that its surviving "
                    "authority row is visible"
                ),
            ),
        )

    def _boundary(self, *, explanation: str, workaround: str) -> Envelope:
        """Return a typed graph admission boundary."""
        at = datetime.now(UTC)
        return _envelope(
            grain=Grain.FACT,
            freshness=Freshness(pg_live_ts=at),
            negative=Negative(
                kind=NegativeKind.BOUNDARY,
                explanation=explanation,
                workaround=workaround,
            ),
        )

    def _empty(
        self,
        *,
        explanation: str,
        valid_at: datetime,
        believed_at: datetime,
        at: datetime,
    ) -> Envelope:
        """Return a typed known-empty traversal result."""
        return _envelope(
            grain=Grain.FACT,
            temporal_scope=_temporal_scope(
                valid_at=valid_at, believed_at=believed_at, evaluated_at=at
            ),
            freshness=Freshness(pg_live_ts=at),
            negative=Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation=explanation,
                workaround="widen the hop bound or relax the graph predicates",
            ),
        )


_NEIGHBORHOOD_HELPER = """
SELECT * FROM memory_v1.graph_neighborhood(
  :deployment_id, :entity_id, :max_depth, :predicates,
  :valid_at, :believed_at, :max_results, :expansion_budget,
  :frontier_budget, :time_budget_ms
)
"""

_PATH_HELPER = """
SELECT * FROM memory_v1.graph_path(
  :deployment_id, :from_entity_id, :to_entity_id, :max_depth, :predicates,
  :valid_at, :believed_at, :max_paths, :expansion_budget,
  :frontier_budget, :time_budget_ms
)
"""

_CITATION_HELPER = """
SELECT * FROM memory_v1.graph_citation_path(
  :deployment_id, :from_doc_id, :to_doc_id, :max_depth, :max_paths,
  :expansion_budget, :frontier_budget, :time_budget_ms
)
"""

_ENTITY_EXISTS = text(
    """
    SELECT EXISTS (
      SELECT 1 FROM memory_v1.entities_current
      WHERE deployment_id = :deployment_id AND entity_id = :entity_id
    )
    """
)

_DOCUMENT_EXISTS = text(
    """
    SELECT EXISTS (
      SELECT 1 FROM memory_v1.documents_live
      WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    )
    """
)

_ENTITY_ROWS = text(
    """
    SELECT entity_id, canonical_name
    FROM memory_v1.entities_current
    WHERE deployment_id = :deployment_id AND entity_id IN :entity_ids
    """
).bindparams(bindparam("entity_ids", expanding=True))

_EDGE_ROWS = text(
    """
    SELECT relation_id, subject_entity_id, object_entity_id, predicate,
           fact_label, valid_from, valid_until, ingested_at, invalidated_at,
           evidence_count_current, support_state_current
    FROM memory_v1.graph_edges_visible_history
    WHERE deployment_id = :deployment_id
      AND relation_id IN :relation_ids
      AND (ingested_at IS NULL OR ingested_at <= :believed_at)
      AND (invalidated_at IS NULL OR invalidated_at > :believed_at)
      AND (valid_from IS NULL OR valid_from <= :valid_at)
      AND (valid_until IS NULL OR valid_until > :valid_at)
    """
).bindparams(bindparam("relation_ids", expanding=True))

_DOCUMENT_ROWS = text(
    """
    SELECT doc_id, title
    FROM memory_v1.documents_live
    WHERE deployment_id = :deployment_id AND doc_id IN :document_ids
    """
).bindparams(bindparam("document_ids", expanding=True))

_CROSSREF_ROWS = text(
    """
    SELECT crossref_id, from_doc_id, to_doc_id, kind, context
    FROM memory_v1.document_crossrefs_live
    WHERE deployment_id = :deployment_id AND crossref_id IN :crossref_ids
    """
).bindparams(bindparam("crossref_ids", expanding=True))


def _rows(
    *, connection: Connection, statement: str, parameters: dict[str, object]
) -> list[RowMapping]:
    """Execute one static graph statement and materialize mappings."""
    return list(connection.execute(text(statement), parameters).mappings())


def _shallow_neighborhood_rows(
    *, connection: Connection, parameters: dict[str, object]
) -> list[RowMapping]:
    """Run the relational guard, and execute PGQ only after explicit admission."""
    guard_rows = _rows(
        connection=connection,
        statement=HISTORY_NEIGHBORHOOD_GUARD,
        parameters=parameters,
    )
    if len(guard_rows) != 1:
        raise RuntimeError("shallow graph guard did not return exactly one row")
    guard = guard_rows[0]
    admitted = bool(guard["admitted"])
    truncated = bool(guard["truncated"])
    reason = guard["truncation_reason"]
    if admitted == truncated or truncated != (reason is not None):
        raise RuntimeError("shallow graph guard returned an inconsistent decision")
    if not admitted:
        return [
            cast(
                RowMapping,
                {
                    "row_kind": "status",
                    "hops": None,
                    "relation_ids": None,
                    "node_ids": None,
                    "truncated": True,
                    "truncation_reason": reason,
                    "examined_edges": guard["examined_edges"],
                    "returned_paths": 0,
                    "effective_depth": guard["effective_depth"],
                    "effective_expansion_budget": guard["effective_expansion_budget"],
                },
            )
        ]
    paths = _rows(
        connection=connection, statement=HISTORY_NEIGHBORHOOD_PGQ, parameters=parameters
    )
    representatives: dict[UUID, RowMapping] = {}
    for row in paths:
        node_ids = tuple(cast(list[UUID], row["node_ids"]))
        relation_ids = tuple(cast(list[UUID], row["relation_ids"]))
        endpoint = node_ids[-1]
        candidate_key = (int(row["hops"]), relation_ids)
        previous = representatives.get(endpoint)
        if previous is None:
            representatives[endpoint] = row
            continue
        previous_key = (
            int(previous["hops"]),
            tuple(cast(list[UUID], previous["relation_ids"])),
        )
        if candidate_key < previous_key:
            representatives[endpoint] = row

    ordered = sorted(
        representatives.values(),
        key=lambda row: (
            int(row["hops"]),
            tuple(cast(list[UUID], row["relation_ids"])),
            tuple(cast(list[UUID], row["node_ids"])),
        ),
    )
    result_cap = min(max(cast(int, parameters["max_results"]), 1), 500)
    result_offset = max(cast(int, parameters["result_offset"]), 0)
    page = ordered[result_offset : result_offset + result_cap + 1]
    result_truncated = len(ordered) > result_offset + result_cap
    returned_paths = min(len(page), result_cap)
    reason = "result_budget" if result_truncated else None
    data = [
        cast(
            RowMapping,
            {
                "row_kind": "data",
                "hops": row["hops"],
                "relation_ids": row["relation_ids"],
                "node_ids": row["node_ids"],
                "truncated": result_truncated,
                "truncation_reason": reason,
                "examined_edges": guard["examined_edges"],
                "returned_paths": returned_paths,
                "effective_depth": guard["effective_depth"],
                "effective_expansion_budget": guard["effective_expansion_budget"],
            },
        )
        for row in page
    ]
    return data + [
        cast(
            RowMapping,
            {
                "row_kind": "status",
                "hops": None,
                "relation_ids": None,
                "node_ids": None,
                "truncated": result_truncated,
                "truncation_reason": reason,
                "examined_edges": guard["examined_edges"],
                "returned_paths": returned_paths,
                "effective_depth": guard["effective_depth"],
                "effective_expansion_budget": guard["effective_expansion_budget"],
            },
        )
    ]


def _split_status(*, rows: list[RowMapping]) -> tuple[list[RowMapping], RowMapping]:
    """Separate data from the one mandatory terminal status row."""
    data = [row for row in rows if row["row_kind"] == "data"]
    statuses = [row for row in rows if row["row_kind"] == "status"]
    if len(statuses) != 1:
        raise RuntimeError("graph traversal did not return exactly one status row")
    status = statuses[0]
    reason = status["truncation_reason"]
    if bool(status["truncated"]) != (reason is not None):
        raise RuntimeError("graph traversal returned an inconsistent truncation status")
    if reason not in {
        None,
        "expansion_budget",
        "frontier_budget",
        "result_budget",
        "time_budget",
        "depth_budget",
    }:
        raise RuntimeError("graph traversal returned an unknown truncation reason")
    return data, status


def _evaluation_instant(*, connection: Connection) -> datetime:
    """Capture the operation clock once inside the transaction."""
    return cast(
        datetime, connection.execute(text("SELECT statement_timestamp()")).scalar_one()
    )


def _entity_exists(
    *, connection: Connection, deployment_id: UUID, entity_id: UUID
) -> bool:
    """Whether a survivor entity is visible in this transaction."""
    return bool(
        connection.execute(
            _ENTITY_EXISTS, {"deployment_id": deployment_id, "entity_id": entity_id}
        ).scalar_one()
    )


def _document_exists(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID
) -> bool:
    """Whether a live document is visible in this transaction."""
    return bool(
        connection.execute(
            _DOCUMENT_EXISTS, {"deployment_id": deployment_id, "doc_id": doc_id}
        ).scalar_one()
    )


def _hydrate_entity_paths(
    *,
    connection: Connection,
    deployment_id: UUID,
    rows: list[RowMapping],
    valid_at: datetime,
    believed_at: datetime,
) -> tuple[GraphPath, ...]:
    """Hydrate complete entity paths from authority views in one snapshot."""
    node_ids = tuple(
        dict.fromkeys(UUID(str(value)) for row in rows for value in row["node_ids"])
    )
    relation_ids = tuple(
        dict.fromkeys(UUID(str(value)) for row in rows for value in row["relation_ids"])
    )
    if not node_ids or not relation_ids:
        if rows:
            raise GraphHydrationError("entity traversal returned an incomplete path")
        return ()
    nodes = {
        UUID(str(row["entity_id"])): row
        for row in connection.execute(
            _ENTITY_ROWS, {"deployment_id": deployment_id, "entity_ids": node_ids}
        ).mappings()
    }
    edges = {
        UUID(str(row["relation_id"])): row
        for row in connection.execute(
            _EDGE_ROWS,
            {
                "deployment_id": deployment_id,
                "relation_ids": relation_ids,
                "valid_at": valid_at,
                "believed_at": believed_at,
            },
        ).mappings()
    }
    paths: list[GraphPath] = []
    for row in rows:
        raw_nodes = tuple(UUID(str(value)) for value in row["node_ids"])
        raw_edges = tuple(UUID(str(value)) for value in row["relation_ids"])
        if any(identifier not in nodes for identifier in raw_nodes) or any(
            identifier not in edges for identifier in raw_edges
        ):
            raise GraphHydrationError(
                "entity traversal hydration did not match the authority rows"
            )
        path_nodes = tuple(
            GraphNode(
                entity_id=identifier,
                name=str(nodes[identifier]["canonical_name"]),
                hops=index,
            )
            for index, identifier in enumerate(raw_nodes)
        )
        path_edges = tuple(
            _graph_edge(row=edges[identifier]) for identifier in raw_edges
        )
        paths.append(
            GraphPath(length=len(path_edges), nodes=path_nodes, edges=path_edges)
        )
    return tuple(paths)


def _hydrate_neighbor_nodes(
    *, connection: Connection, deployment_id: UUID, rows: list[RowMapping]
) -> tuple[GraphNode, ...]:
    """Hydrate only terminal neighbors when the caller did not request paths."""
    terminal_hops: dict[UUID, int] = {}
    for row in rows:
        identifier = UUID(str(row["node_ids"][-1]))
        terminal_hops.setdefault(identifier, int(row["hops"]))
    terminal_ids = tuple(terminal_hops)
    if not terminal_ids:
        return ()
    authority = {
        UUID(str(row["entity_id"])): row
        for row in connection.execute(
            _ENTITY_ROWS, {"deployment_id": deployment_id, "entity_ids": terminal_ids}
        ).mappings()
    }
    if any(identifier not in authority for identifier in terminal_ids):
        raise GraphHydrationError(
            "entity traversal hydration did not match the authority rows"
        )
    return tuple(
        GraphNode(
            entity_id=identifier,
            name=str(authority[identifier]["canonical_name"]),
            hops=terminal_hops[identifier],
        )
        for identifier in terminal_ids
    )


def _hydrate_citation_paths(
    *, connection: Connection, deployment_id: UUID, rows: list[RowMapping]
) -> tuple[GraphPath, ...]:
    """Hydrate complete directed document paths from live authority views."""
    document_ids = tuple(
        dict.fromkeys(UUID(str(value)) for row in rows for value in row["document_ids"])
    )
    crossref_ids = tuple(
        dict.fromkeys(UUID(str(value)) for row in rows for value in row["crossref_ids"])
    )
    if not document_ids or not crossref_ids:
        if rows:
            raise GraphHydrationError("citation traversal returned an incomplete path")
        return ()
    documents = {
        UUID(str(row["doc_id"])): row
        for row in connection.execute(
            _DOCUMENT_ROWS,
            {"deployment_id": deployment_id, "document_ids": document_ids},
        ).mappings()
    }
    crossrefs = {
        UUID(str(row["crossref_id"])): row
        for row in connection.execute(
            _CROSSREF_ROWS,
            {"deployment_id": deployment_id, "crossref_ids": crossref_ids},
        ).mappings()
    }
    paths: list[GraphPath] = []
    for row in rows:
        raw_nodes = tuple(UUID(str(value)) for value in row["document_ids"])
        raw_edges = tuple(UUID(str(value)) for value in row["crossref_ids"])
        if any(identifier not in documents for identifier in raw_nodes) or any(
            identifier not in crossrefs for identifier in raw_edges
        ):
            raise GraphHydrationError(
                "citation traversal hydration did not match the authority rows"
            )
        nodes = tuple(
            GraphNode(
                entity_id=identifier,
                name=str(documents[identifier]["title"] or ""),
                hops=index,
            )
            for index, identifier in enumerate(raw_nodes)
        )
        edges = tuple(
            GraphEdge(
                relation_id=identifier,
                subject_id=UUID(str(crossrefs[identifier]["from_doc_id"])),
                object_id=UUID(str(crossrefs[identifier]["to_doc_id"])),
                predicate=str(crossrefs[identifier]["kind"]),
                fact=cast("str | None", crossrefs[identifier]["context"]),
                evidence_count=0,
                valid_from=None,
                valid_until=None,
                ingested_at=None,
                invalidated_at=None,
            )
            for identifier in raw_edges
        )
        paths.append(GraphPath(length=len(edges), nodes=nodes, edges=edges))
    return tuple(paths)


def _graph_edge(*, row: RowMapping) -> GraphEdge:
    """Convert one edge-view row without reversing its stored direction."""
    return GraphEdge(
        relation_id=UUID(str(row["relation_id"])),
        subject_id=UUID(str(row["subject_entity_id"])),
        object_id=UUID(str(row["object_entity_id"])),
        predicate=str(row["predicate"]),
        fact=cast("str | None", row["fact_label"]),
        evidence_count=int(row["evidence_count_current"]),
        valid_from=cast("datetime | None", row["valid_from"]),
        valid_until=cast("datetime | None", row["valid_until"]),
        ingested_at=cast("datetime | None", row["ingested_at"]),
        invalidated_at=cast("datetime | None", row["invalidated_at"]),
        support=FactSupport(str(row["support_state_current"])),
    )


def _unique_edges(*, paths: tuple[GraphPath, ...]) -> tuple[GraphEdge, ...]:
    """Return first-seen edges in deterministic path order."""
    unique: dict[UUID, GraphEdge] = {}
    for path in paths:
        for edge in path.edges:
            unique.setdefault(edge.relation_id, edge)
    return tuple(unique.values())


def _unique_nodes(*, paths: tuple[GraphPath, ...]) -> tuple[GraphNode, ...]:
    """Return first-seen nodes in deterministic path order."""
    unique: dict[UUID, GraphNode] = {}
    for path in paths:
        for node in path.nodes:
            unique.setdefault(node.entity_id, node)
    return tuple(unique.values())


def _status_truncation(*, status: RowMapping, returned: int) -> Truncation:
    """Map the helper terminal row to the envelope disclosure."""
    truncated = bool(status["truncated"])
    return Truncation(
        truncated=truncated,
        returned=returned,
        estimated_total=max(int(status["returned_paths"]), returned),
        total_is_exact=not truncated,
        reason=(
            str(status["truncation_reason"])
            if status["truncation_reason"] is not None
            else None
        ),
    )


def _validate_clocks(
    *, valid_at: datetime | None, believed_at: datetime | None
) -> None:
    """Require a complete bitemporal coordinate or neither clock."""
    if (valid_at is None) != (believed_at is None):
        raise ValueError("a bitemporal traversal takes both clocks or neither")


def _validate_predicates(*, predicates: tuple[str, ...]) -> None:
    """Bound predicate cardinality and item size outside the HTTP surface too."""
    if len(predicates) > 100:
        raise ValueError("a graph traversal accepts at most 100 predicates")
    if any(not predicate or len(predicate) > 200 for predicate in predicates):
        raise ValueError("graph predicates must contain between 1 and 200 characters")


def _temporal_scope(
    *, valid_at: datetime, believed_at: datetime, evaluated_at: datetime
) -> AsOfTemporalScope:
    """Disclose both clocks used by every live graph operation."""
    return AsOfTemporalScope(
        valid_at=valid_at, believed_at=believed_at, evaluated_at=evaluated_at
    )


def _encode_continuation(offset: int) -> str:
    """Encode a deployment-local live result offset."""
    return f"live-v1:{offset}"


def _decode_continuation(value: str | None) -> int | None:
    """Decode a live result offset or reject a foreign cursor."""
    if value is None:
        return 0
    prefix, separator, raw_offset = value.partition(":")
    if prefix != "live-v1" or not separator:
        return None
    try:
        return max(int(raw_offset), 0)
    except ValueError:
        return None


def _envelope(**values: object) -> Envelope:
    """Build a graph envelope with an explicit current scope by default."""
    values.setdefault("temporal_scope", current_temporal_scope())
    return Envelope.model_validate(values)
