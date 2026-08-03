"""The `graph` primitive (retrieval §3, p2 §4/§6): traversal over the snapshot.

Three typed, zero-LLM operations over the published P2 snapshot:

- **neighborhood(entity, hops, predicates?)** — everything within N hops,
  distance-ranked, with an EXPLICIT truncation marker whenever a hub
  exceeds the page cap (S18: never a silent top-k).
- **path(a, b, max_hops)** — how two entities connect, shortest-first. A
  path is a COMPOUND result: it revalidates as a unit, so a dropped edge
  drops the whole path rather than silently yielding a shorter, false
  connection (S17/S21).
- **citation_path(from_doc, to_doc, max_hops)** — the document graph
  (`DOC_CROSSREF`), for "which documents ultimately cite X" (S22).

Both entity operations accept the temporal parameters and echo what they
applied. As-of traversal uses the engine's **inline recursive-pattern
predicate** — evaluated per edge DURING the neighbor scan, verified live in
the WP-4.1 spike battery — never the post-hoc `all(r IN rels(p) …)` form.
Projected graphs are not an option: they feed the algorithm extensions only
and cannot be `MATCH`-traversed (D44).

Three engine constraints shape every query here (recorded with canaries in
`plan/analysis/p2_spike_battery.md`): recursive bounds cap at 30 hops; NULL
parameters cannot participate in typed comparisons, so temporal conjuncts
are composed conditionally; and a plain variable-length match enumerates
paths combinatorially, so `SHORTEST` is load-bearing for reachability.
"""

from collections.abc import Callable
from datetime import datetime
from datetime import UTC
from typing import cast
from typing import Final
from uuid import UUID

import ladybug

from rememberstack.model import Envelope
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import GraphEdge
from rememberstack.model import GraphNode
from rememberstack.model import GraphPath
from rememberstack.model import Negative
from rememberstack.model import NegativeKind
from rememberstack.model import Truncation

MAX_ENGINE_HOPS: Final = 30
"""The engine's recursive upper bound (WP-4.1 spike d2). Requests above it
are clamped and disclosed, never silently honored or failed."""

DEFAULT_NEIGHBORHOOD_CAP: Final = 500
"""WP-5.6's measured neighbor page cap (under 64 KiB at the S49 hub)."""

COUNT_CAP: Final = 10_000
"""How far the total-count probe walks before reporting an inexact total —
a hub must never turn an honest count into an unbounded scan."""

_TRANSIENT_MARKERS: Final = ("Overflow", "INT128", "out of range")
"""Substrings of the intermittent engine faults a single retry clears."""


class _TransientEngineError(Exception):
    """A transient engine fault a retry did not clear (→ typed boundary)."""


class GraphQueries:
    """Snapshot traversal: neighborhoods, paths, as-of, distance ranking."""

    def __init__(self, *, reader: object) -> None:
        """Bind to a snapshot reader (the WP-4.2 `GraphSnapshotReader`)."""
        self._reader = reader

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
    ) -> Envelope:
        """Everything within `hops` of the entity, distance-ranked (S18/S19).

        Ranking is graph distance first (nearer is more relevant — the D9
        graph-distance rerank in its native form), then name, then a stable
        id tiebreak, so pages are deterministic. The applied `valid_at`
        defaults to NOW so "current" means currently-valid (S18) and is
        always echoed; historical reads pass an explicit instant.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        try:
            return self._neighborhood(
                entity_id=entity_id,
                hops=hops,
                predicates=predicates,
                valid_at=valid_at,
                believed_at=believed_at,
                limit=limit,
                continuation=continuation,
                include_paths=include_paths,
            )
        except _TransientEngineError as error:
            return self._transient(error=error)

    def _neighborhood(
        self,
        *,
        entity_id: UUID,
        hops: int,
        predicates: tuple[str, ...],
        valid_at: datetime | None,
        believed_at: datetime | None,
        limit: int,
        continuation: str | None,
        include_paths: bool,
    ) -> Envelope:
        """The neighborhood body (a transient engine fault surfaces above)."""
        connection = self._connection()
        if connection is None:
            return self._no_snapshot()
        offset = self._decode_continuation(continuation)
        if offset is None:
            return self._stale_continuation()
        applied_valid_at = valid_at or datetime.now(tz=UTC)
        if not self._entity_exists(connection, entity_id=entity_id):
            return self._unknown_entity(entity_id=entity_id)
        clamped = min(max(hops, 1), MAX_ENGINE_HOPS)
        predicate_filter = " AND r.predicate IN $predicates" if predicates else ""
        guard = _temporal_predicate(valid_at=applied_valid_at, believed_at=believed_at)
        # SHORTEST is load-bearing, not an optimization: a plain
        # variable-length match ENUMERATES every path, which explodes
        # combinatorially on a cyclic graph (a 30-hop undirected walk never
        # returns). SHORTEST gives one result per reachable node — exactly
        # what a distance-ranked neighborhood is — in BFS time.
        pattern = (
            f"MATCH {'p = ' if include_paths else ''}(a:Entity {{id: $entity_id}})"
            f" -[r:RELATES* SHORTEST 1..{clamped}"
            f" (r, n | WHERE {guard}{predicate_filter})]-"
            f" (b:Entity)"
        )
        parameters: dict[str, object] = {"entity_id": entity_id}
        if predicates:
            parameters["predicates"] = list(predicates)
        _bind_temporal(parameters, valid_at=applied_valid_at, believed_at=believed_at)
        total, exact = self._count_reachable(
            connection, pattern=pattern, parameters=parameters
        )
        projection = "b.id, b.name, b.type, length(r) AS hops"
        if include_paths:
            projection += ", nodes(p) AS path_nodes, rels(p) AS path_edges"
        rows = _rows(
            connection,
            f"{pattern} RETURN {projection}"
            " ORDER BY hops, b.name, b.id SKIP $offset LIMIT $fetch",  # noqa: S608
            {**parameters, "offset": offset, "fetch": limit + 1},
            fresh=self._reconnect,
        )
        page_rows = rows[:limit]
        nodes = tuple(
            GraphNode(
                entity_id=cast("UUID", row[0]),
                name=cast("str", row[1]),
                type=cast("str", row[2]),
                hops=cast("int", row[3]),
            )
            for row in page_rows
        )
        paths: tuple[GraphPath, ...] = ()
        edges: tuple[GraphEdge, ...] = ()
        if include_paths:
            ranked_paths = tuple(
                _path_from_row([row[3], row[4], row[5]]) for row in page_rows
            )
            edges_by_id: dict[UUID, GraphEdge] = {}
            for path in ranked_paths:
                for edge in path.edges:
                    edges_by_id.setdefault(edge.relation_id, edge)
            edges = tuple(edges_by_id.values())[:limit]
            selected_edge_ids = {edge.relation_id for edge in edges}
            paths = tuple(
                path
                for path in ranked_paths
                if all(edge.relation_id in selected_edge_ids for edge in path.edges)
            )
        if not nodes and offset == 0:
            return self._empty(
                explanation=(
                    f"entity {entity_id} exists but no neighbor within"
                    f" {clamped} hop(s) satisfies the requested filters"
                ),
                valid_at=applied_valid_at,
                believed_at=believed_at,
            )
        # The bounded count probe is metadata, never the paging boundary.
        # Fetching one extra row lets a 10^5-edge hub continue beyond
        # COUNT_CAP without turning the total-count probe into an unbounded scan.
        more = len(rows) > limit
        estimated_total = (
            total if exact else max(total, offset + len(nodes) + int(more))
        )
        return Envelope(
            grain=Grain.FACT,
            as_of_valid_at=applied_valid_at,
            as_of_believed_at=believed_at,
            nodes=nodes,
            paths=paths,
            edges=edges,
            freshness=self._freshness(),
            truncation=Truncation(
                truncated=more or hops > MAX_ENGINE_HOPS,
                returned=len(nodes),
                estimated_total=estimated_total,
                total_is_exact=exact,
                continuation=(
                    self._encode_continuation(offset + len(nodes)) if more else None
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
    ) -> Envelope:
        """How two entities connect, shortest-first (S17/S21).

        The path returns as a unit with every traversed edge — each edge
        carrying its STORED direction, never the traversal's, so a fact
        read backwards is still reported as the fact it is.
        """
        try:
            return self._path(
                from_entity_id=from_entity_id,
                to_entity_id=to_entity_id,
                max_hops=max_hops,
                valid_at=valid_at,
                believed_at=believed_at,
            )
        except _TransientEngineError as error:
            return self._transient(error=error)

    def _path(
        self,
        *,
        from_entity_id: UUID,
        to_entity_id: UUID,
        max_hops: int,
        valid_at: datetime | None,
        believed_at: datetime | None,
    ) -> Envelope:
        """The path body (a transient engine fault surfaces above)."""
        connection = self._connection()
        if connection is None:
            return self._no_snapshot()
        applied_valid_at = valid_at or datetime.now(tz=UTC)
        for endpoint in (from_entity_id, to_entity_id):
            if not self._entity_exists(connection, entity_id=endpoint):
                return self._unknown_entity(entity_id=endpoint)
        clamped = min(max(max_hops, 1), MAX_ENGINE_HOPS)
        guard = _temporal_predicate(valid_at=applied_valid_at, believed_at=believed_at)
        query = f"""
            MATCH p = (a:Entity {{id: $from_id}})
                      -[r:RELATES* SHORTEST 1..{clamped}
                        (r, n | WHERE {guard})]-
                      (b:Entity {{id: $to_id}})
            RETURN length(p) AS hops, nodes(p) AS path_nodes, rels(p) AS path_edges
            """  # noqa: S608 — `clamped` is a validated int
        # NB: the engine does not support list comprehensions over path
        # elements (`[x IN nodes(p) | …]` → "Variable x is not in scope") —
        # `nodes(p)`/`rels(p)` return full property maps, read below
        parameters: dict[str, object] = {
            "from_id": from_entity_id,
            "to_id": to_entity_id,
        }
        _bind_temporal(parameters, valid_at=applied_valid_at, believed_at=believed_at)
        rows = _rows(connection, query, parameters, fresh=self._reconnect)
        if not rows:
            return self._empty(
                explanation=(
                    f"both entities exist, but no path of {clamped} hop(s) or"
                    " fewer connects them under the applied temporal filters"
                ),
                valid_at=applied_valid_at,
                believed_at=believed_at,
            )
        paths = tuple(_path_from_row(row) for row in rows)
        return Envelope(
            grain=Grain.FACT,
            as_of_valid_at=applied_valid_at,
            as_of_believed_at=believed_at,
            paths=paths,
            edges=tuple(edge for path in paths for edge in path.edges),
            nodes=tuple(node for path in paths for node in path.nodes),
            freshness=self._freshness(),
            truncation=Truncation(
                truncated=max_hops > MAX_ENGINE_HOPS,
                returned=len(paths),
                estimated_total=len(paths),
            ),
        )

    def citation_path(
        self, *, from_doc_id: UUID, to_doc_id: UUID, max_hops: int = 6
    ) -> Envelope:
        """Which documents ultimately cite which (S22): the document graph.

        `DOC_CROSSREF` carries no validity window (structural metadata,
        not a bi-temporal fact), so this traversal takes no temporal
        parameters — the honest shape rather than a decorative one.
        """
        try:
            return self._citation_path(
                from_doc_id=from_doc_id, to_doc_id=to_doc_id, max_hops=max_hops
            )
        except _TransientEngineError as error:
            return self._transient(error=error)

    def _citation_path(
        self, *, from_doc_id: UUID, to_doc_id: UUID, max_hops: int
    ) -> Envelope:
        """The citation-path body (a transient engine fault surfaces above)."""
        connection = self._connection()
        if connection is None:
            return self._no_snapshot()
        clamped = min(max(max_hops, 1), MAX_ENGINE_HOPS)
        for endpoint in (from_doc_id, to_doc_id):
            if not self._document_exists(connection, doc_id=endpoint):
                return self._unknown_entity(entity_id=endpoint, kind="document")
        query = f"""
            MATCH p = (a:Document {{id: $from_id}})
                      -[r:DOC_CROSSREF* SHORTEST 1..{clamped}]->
                      (b:Document {{id: $to_id}})
            RETURN length(p) AS hops, nodes(p) AS path_nodes, rels(p) AS path_edges
            """  # noqa: S608 — `clamped` is a validated int
        rows = _rows(
            connection,
            query,
            {"from_id": from_doc_id, "to_id": to_doc_id},
            fresh=self._reconnect,
        )
        if not rows:
            return self._empty(
                explanation=(
                    f"both documents exist, but no citation chain of"
                    f" {clamped} hop(s) or fewer connects them"
                ),
                valid_at=None,
                believed_at=None,
            )
        paths = tuple(_citation_path_from_row(row) for row in rows)
        return Envelope(
            grain=Grain.FACT,
            paths=paths,
            nodes=tuple(node for path in paths for node in path.nodes),
            edges=tuple(edge for path in paths for edge in path.edges),
            freshness=self._freshness(),
            truncation=Truncation(
                truncated=max_hops > MAX_ENGINE_HOPS,
                returned=len(paths),
                estimated_total=len(paths),
            ),
        )

    def _count_reachable(
        self,
        connection: ladybug.Connection,
        *,
        pattern: str,
        parameters: dict[str, object],
    ) -> tuple[int, bool]:
        """How many members the neighborhood holds, bounded by COUNT_CAP.

        An honest `estimated_total` needs a real count — but a hub must not
        turn it into an unbounded scan, so the probe stops at the cap and
        says so (`total_is_exact=False`).
        """
        rows = _rows(
            connection,
            f"{pattern} RETURN b.id LIMIT $count_cap",  # noqa: S608
            {**parameters, "count_cap": COUNT_CAP},
            fresh=self._reconnect,
        )
        return len(rows), len(rows) < COUNT_CAP

    def _entity_exists(
        self, connection: ladybug.Connection, *, entity_id: UUID
    ) -> bool:
        """Whether the graph knows this entity (unknown_entity vs empty)."""
        rows = _rows(
            connection,
            "MATCH (e:Entity {id: $entity_id}) RETURN e.id LIMIT 1",
            {"entity_id": entity_id},
            fresh=self._reconnect,
        )
        return bool(rows)

    def _document_exists(self, connection: ladybug.Connection, *, doc_id: UUID) -> bool:
        """Whether the graph knows this document lineage."""
        rows = _rows(
            connection,
            "MATCH (d:Document {id: $doc_id}) RETURN d.id LIMIT 1",
            {"doc_id": doc_id},
            fresh=self._reconnect,
        )
        return bool(rows)

    def _encode_continuation(self, offset: int) -> str:
        """A snapshot-BOUND cursor: pages from a swapped snapshot are refused.

        A raw offset would silently skip or duplicate members when the
        reader hot-swaps between pages; binding the snapshot version makes
        that visible instead (Codex review).
        """
        return f"{getattr(self._reader, 'version', '')}:{offset}"

    def _decode_continuation(self, continuation: str | None) -> int | None:
        """The offset a cursor names, or None when it belongs elsewhere."""
        if continuation is None:
            return 0
        version, _, raw_offset = continuation.rpartition(":")
        if version != str(getattr(self._reader, "version", "")):
            return None
        try:
            return max(int(raw_offset), 0)
        except ValueError:
            return None

    def _connection(self) -> ladybug.Connection | None:
        """The snapshot connection, or None when nothing is published yet."""
        try:
            return cast("ladybug.Connection", self._reader.connection())  # type: ignore[attr-defined]
        except RuntimeError:
            return None

    def _reconnect(self) -> ladybug.Connection:
        """A FRESH connection to the same snapshot, for a transient-fault retry.

        A reader that can mint one (the real `GraphSnapshotReader`) gives a
        clean per-connection scan state; a stub without the method falls
        back to the cached connection, so the retry still runs.
        """
        fresh = getattr(self._reader, "fresh_connection", None)
        if callable(fresh):
            return cast("ladybug.Connection", fresh())
        return cast("ladybug.Connection", self._reader.connection())  # type: ignore[attr-defined]

    def _freshness(self) -> Freshness:
        """Stamp WHICH snapshot answered and WHEN it published (S42)."""
        return Freshness(
            pg_live_ts=datetime.now(tz=UTC),
            p2_snapshot_version=getattr(self._reader, "version", None),
            p2_snapshot_ts=getattr(self._reader, "published_at", None),
        )

    def _no_snapshot(self) -> Envelope:
        """A typed boundary: the graph plane has never published (S39)."""
        return Envelope(
            grain=Grain.FACT,
            freshness=Freshness(pg_live_ts=datetime.now(tz=UTC)),
            negative=Negative(
                kind=NegativeKind.BOUNDARY,
                explanation="no P2 graph snapshot has been published yet",
                workaround=(
                    "run the graph rebuild worker, or use lookup/search on the"
                    " live spine which needs no projection"
                ),
            ),
        )

    def _stale_continuation(self) -> Envelope:
        """The cursor belongs to a superseded snapshot (S18 honesty)."""
        return Envelope(
            grain=Grain.FACT,
            freshness=self._freshness(),
            negative=Negative(
                kind=NegativeKind.BOUNDARY,
                explanation=(
                    "the continuation cursor belongs to a superseded graph"
                    " snapshot; paging across a snapshot swap would skip or"
                    " duplicate members"
                ),
                workaround="restart the traversal to page over the current snapshot",
            ),
        )

    def _unknown_entity(self, *, entity_id: UUID, kind: str = "entity") -> Envelope:
        """The endpoint is not in the graph at all (S29's first branch)."""
        return Envelope(
            grain=Grain.FACT,
            freshness=self._freshness(),
            negative=Negative(
                kind=NegativeKind.UNKNOWN_ENTITY,
                explanation=f"{kind} {entity_id} is not present in the graph snapshot",
                workaround=(
                    "resolve the name first, or check whether the entity was"
                    " merged into a survivor or arrived after this snapshot"
                ),
            ),
        )

    def _transient(self, *, error: _TransientEngineError) -> Envelope:
        """A retryable engine fault, surfaced honestly (never a raw crash).

        The embedded engine intermittently overflows an internal counter on
        a SHORTEST traversal under memory pressure; one retry already ran.
        The caller sees a typed boundary with "retry" as the workaround
        rather than an INT128 RuntimeError.
        """
        return Envelope(
            grain=Grain.FACT,
            freshness=self._freshness(),
            negative=Negative(
                kind=NegativeKind.BOUNDARY,
                explanation=(
                    "the graph engine hit a transient internal fault on this"
                    f" traversal ({error}); one retry did not clear it"
                ),
                workaround="retry the query, or narrow the hop bound",
            ),
        )

    def _empty(
        self,
        *,
        explanation: str,
        valid_at: datetime | None,
        believed_at: datetime | None,
    ) -> Envelope:
        """A typed known_empty: the traversal ran and found nothing (S29)."""
        return Envelope(
            grain=Grain.FACT,
            as_of_valid_at=valid_at,
            as_of_believed_at=believed_at,
            freshness=self._freshness(),
            negative=Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation=explanation,
                workaround="widen the hop bound, relax predicates, or drop the as-of",
            ),
        )


def _temporal_predicate(
    *, valid_at: datetime | None, believed_at: datetime | None
) -> str:
    """The as-of guard, evaluated PER EDGE during the neighbor scan (D44).

    Composed CONDITIONALLY, never with NULL parameters: the engine infers a
    NULL parameter's type as BOOL and refuses to compare it with TIMESTAMP
    (`Cannot compare types TIMESTAMP and BOOL`) — found live here, pinned
    by a canary in the spike battery.

    Valid time filters the world-time window (defaulted to now by the
    callers, so "current" means currently-valid and is always echoed);
    system time filters what we believed then, and without it the read is
    current belief (`invalidated_at IS NULL`, D6). The NULL-column guards
    keep SQL three-valued semantics (spike f).
    """
    conjuncts: list[str] = []
    if valid_at is not None:
        conjuncts.append(
            "(r.valid_from IS NULL OR r.valid_from <= $valid_at)"
            " AND (r.valid_until IS NULL OR r.valid_until > $valid_at)"
        )
    if believed_at is not None:
        conjuncts.append(
            "r.ingested_at <= $believed_at"
            " AND (r.invalidated_at IS NULL OR r.invalidated_at > $believed_at)"
        )
    else:
        conjuncts.append("r.invalidated_at IS NULL")  # current belief
    return " AND ".join(f"({conjunct})" for conjunct in conjuncts)


def _bind_temporal(
    parameters: dict[str, object],
    *,
    valid_at: datetime | None,
    believed_at: datetime | None,
) -> None:
    """Bind only the temporal parameters the predicate actually references."""
    if valid_at is not None:
        parameters["valid_at"] = _naive(valid_at)
    if believed_at is not None:
        parameters["believed_at"] = _naive(believed_at)


def _path_from_row(row: list[object]) -> GraphPath:
    """Assemble one compound entity path from its node and edge maps."""
    raw_nodes = cast("list[dict[str, object]]", row[1])
    raw_edges = cast("list[dict[str, object]]", row[2])
    nodes = tuple(
        GraphNode(
            entity_id=cast("UUID", node["id"]),
            name=cast("str", node["name"]),
            type=cast("str", node["type"]),
            hops=index,
        )
        for index, node in enumerate(raw_nodes)
    )
    edges = tuple(
        GraphEdge(
            relation_id=cast("UUID", edge["relation_id"]),
            # the STORED direction, not the traversal's: a backwards
            # crossing must never invert the fact (Codex review)
            subject_id=cast("UUID", edge["subject_id"]),
            object_id=cast("UUID", edge["object_id"]),
            predicate=cast("str", edge["predicate"]),
            fact=cast("str | None", edge.get("fact")),
            evidence_count=cast("int", edge["evidence_count"]),
            valid_from=_utc(edge.get("valid_from")),
            valid_until=_utc(edge.get("valid_until")),
            ingested_at=_utc(edge.get("ingested_at")),
            invalidated_at=_utc(edge.get("invalidated_at")),
        )
        for edge in raw_edges
    )
    return GraphPath(length=cast("int", row[0]), nodes=nodes, edges=edges)


def _citation_path_from_row(row: list[object]) -> GraphPath:
    """Assemble one document citation chain (no validity windows)."""
    raw_nodes = cast("list[dict[str, object]]", row[1])
    raw_edges = cast("list[dict[str, object]]", row[2])
    nodes = tuple(
        GraphNode(
            entity_id=cast("UUID", node["id"]),
            name=cast("str", node.get("title") or ""),
            type="Document",
            hops=index,
        )
        for index, node in enumerate(raw_nodes)
    )
    edges = tuple(
        GraphEdge(
            relation_id=cast("UUID", edge["from_doc_id"]),
            subject_id=cast("UUID", edge["from_doc_id"]),
            object_id=cast("UUID", edge["to_doc_id"]),
            predicate=cast("str", edge["kind"]),
            fact=cast("str | None", edge.get("context")),
            evidence_count=0,
            valid_from=None,
            valid_until=None,
            ingested_at=None,
            invalidated_at=None,
        )
        for edge in raw_edges
    )
    return GraphPath(length=cast("int", row[0]), nodes=nodes, edges=edges)


def _is_transient(error: RuntimeError) -> bool:
    """Whether an engine RuntimeError is the retryable overflow fault."""
    return any(marker in str(error) for marker in _TRANSIENT_MARKERS)


def _run_rows(
    connection: ladybug.Connection, query: str, parameters: dict[str, object]
) -> list[list[object]]:
    """Execute one Cypher statement on a connection and drain its rows."""
    result = connection.execute(query, parameters)
    assert isinstance(result, ladybug.QueryResult)
    rows: list[list[object]] = []
    while result.has_next():
        rows.append(cast("list[object]", result.get_next()))
    return rows


def _rows(
    connection: ladybug.Connection,
    query: str,
    parameters: dict[str, object],
    *,
    fresh: Callable[[], ladybug.Connection] | None = None,
) -> list[list[object]]:
    """Run one Cypher statement and materialize its rows, transient-safe.

    The embedded engine intermittently raises an internal
    ``Overflow exception: INT128 is out of range`` under memory pressure on
    a `SHORTEST` traversal — nondeterministic, and the identical query
    clears on retry (recorded as a canary-less finding in the spike
    report, since it does not reproduce deterministically). A retrieval
    primitive must never throw a raw engine overflow at an agent, so on
    that fault this retries once — on a FRESH connection when `fresh` is
    given, since a wedged per-connection scan state must not be able to
    break the read permanently — and, if it still fails, raises
    `_TransientEngineError` for the caller to turn into a typed boundary.
    """
    try:
        return _run_rows(connection, query, parameters)
    except RuntimeError as first:
        if not _is_transient(first):
            raise
    retry_connection = fresh() if fresh is not None else connection
    try:
        return _run_rows(retry_connection, query, parameters)
    except RuntimeError as second:
        if _is_transient(second):
            raise _TransientEngineError(str(second)) from second
        raise


def _naive(value: datetime | None) -> datetime | None:
    """The graph stores naive UTC; parameters must match (spike f)."""
    if value is None:
        return None
    return value.astimezone(UTC).replace(tzinfo=None)


def _utc(value: object) -> datetime | None:
    """Re-attach UTC to a naive graph timestamp for the envelope."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC)
    return None
