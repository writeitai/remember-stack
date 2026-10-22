"""D142 nominate–confirm–continue execution for ranked P1 search.

A ranked search asks an index for candidates in score order and keeps only
the ones that pass every authority rule. Doing both in one SQL statement left
the plan to PostgreSQL's cost model, which on real corpora chose to score every
row (issue #504). Here the two halves are separate statements inside one
read-only ``REPEATABLE READ`` transaction (one snapshot):

1. one **nomination cursor** over a join-free statement whose only ordering is
   the index's distance expression. Planner controls apply to that statement
   alone and ``EXPLAIN`` must show the expected index before anything is
   fetched (``plan_rejected`` otherwise);
2. **disjoint fetches** of a doubling batch from that one cursor (one BM25
   scoring generation, one continuing HNSW scan), each batch **confirmed** by
   primary key with the caller's unchanged eligibility predicates;
3. a **stop** with an honest reason (:class:`P1Termination`).

BM25 ordering. pg_textsearch 1.5.1 orders its index scan by an internal score
that can use a document's raw length, while its standalone operator — the
canonical score every result carries — uses the length after one-byte
quantization (``decode_fieldnorm(encode_fieldnorm(len))``). Quantization only
rounds a length *down*, by at most 1/9 of it, and BM25's per-term weight is
decreasing in length, so for every row ``index ≤ canonical ≤ index / (1 - 1/9)``
(up to float rounding, :func:`score_epsilon`). The stream is non-increasing in
the index score, so every unfetched row has a canonical score of at most
:func:`bm25_stop_margin` times the canonical score of the last fetched row. The
loop stops only once the ``k``-th confirmed canonical score is strictly above
that bound — no unfetched row can reach the result or tie with its last member.
"""

from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
import json
from time import monotonic
from typing import Any
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from rememberstack.ports.p1_index import merge_accounts  # noqa: F401 -- re-export
from rememberstack.ports.p1_index import P1EntityScopeTooBroadError  # noqa: F401
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.ports.p1_index import P1SearchAccount
from rememberstack.ports.p1_index import P1SearchResult
from rememberstack.ports.p1_index import P1SearchUnavailableError
from rememberstack.ports.p1_index import P1Termination

BM25_LENGTH_QUANTIZATION_ERROR = 1.0 / 9.0
"""Largest relative amount pg_textsearch's length encoding rounds down (1.5.1).

Measured from ``FIELDNORM_DECODE_TABLE``: the worst floor error over all
lengths is 0.111108…, just under 1/9. A test pins the bound to the table."""

BM25_BOUND_VERIFIED_REVISION = "fa142d049d08eac45a263f5abc250c8877ae076e"
"""The pg_textsearch revision whose scorers and length table the margin was
checked against. The scorer-bound gate fails when the image pins another one,
so an extension upgrade re-verifies the bound before it ships."""

FLOAT4_UNIT_ROUNDOFF = 2.0**-24
"""IEEE binary32 unit roundoff ``u``; both scorers compute each term in float4."""

TERM_OPERATIONS = 16
"""Upper bound on rounded float4 operations in one term's score, idf included
(pg_textsearch 1.5.1 computes about ten); per-term error is at most ``γ_16``."""


ANALYZED_TERMS_SQL = (
    "SELECT count(*) FROM ts_debug(CAST(:text_config AS regconfig), :query)"
    " WHERE array_length(lexemes, 1) > 0"
)
"""How many terms the text parser emits for a query, every occurrence counted.

``ts_debug`` reports each token the parser emits — compound tokens *and*
their parts (``a-b`` gives ``a-b``, ``a`` and ``b``) — so this counts at
least the terms any scorer can sum (review r2 P2-1: a length-based bound is
wrong for compound tokens)."""


def analyzed_terms(connection: Connection, *, query: str, text_config: str) -> int:
    """The parser's term count for ``query`` (an upper bound on scored terms)."""
    return max(
        1,
        int(
            connection.execute(
                text(ANALYZED_TERMS_SQL), {"text_config": text_config, "query": query}
            ).scalar_one()
        ),
    )


MAX_ROUNDING_FRACTION = 0.25
"""Largest ``m·u`` the margin is proved for (see :func:`score_epsilon`)."""


def score_epsilon(terms: int) -> float:
    """Relative rounding bound ``ε`` on one computed BM25 score of ``terms`` terms.

    Each scorer sums ``n`` positive terms, each computed in float4 with at
    most ``TERM_OPERATIONS`` roundings (the standalone operator then adds the
    float4 terms in float8, a smaller error, and casts the exact float8 sum to
    double precision). With ``m = n + TERM_OPERATIONS`` and ``x = m·u``, a
    computed score ``C'`` and its real value ``C`` satisfy
    ``C(1−γ) ≤ C' ≤ C(1+γ)``, ``γ = x/(1−x)`` (Higham, positive recursive
    summation). With ``ε = 2x``:

    - ``C' ≤ C(1+γ) ≤ C(1+ε)`` because ``x/(1−x) ≤ 2x`` for ``x ≤ 1/2``;
    - ``C ≤ C'/(1−γ) = C'(1−x)/(1−2x) ≤ C'(1+ε)`` because
      ``(1−x) ≤ (1−2x)(1+2x) = 1−4x²`` exactly when ``x ≤ 1/4``.

    So the proof needs ``x ≤ 1/4``; larger queries are refused. A fixed slack
    cannot hold for every query: 40,000 terms in a single long document reach
    a canonical/index ratio of 1.12533 against a fixed-``1e-4`` margin of
    1.12523 (review r1).
    """
    m = max(1, terms) + TERM_OPERATIONS
    if m * FLOAT4_UNIT_ROUNDOFF > MAX_ROUNDING_FRACTION:
        raise ValueError("query too long for a float4 BM25 error bound")
    return 2.0 * m * FLOAT4_UNIT_ROUNDOFF


def bm25_stop_margin(terms: int) -> float:
    """Upper bound on (unfetched canonical score) / (last fetched canonical score).

    With computed scores primed and real ones bare, ``e`` the length
    quantization bound and the stream ordered by the computed index score:
    ``C'(u) ≤ C(u)(1+ε) ≤ I(u)(1+ε)/(1−e) ≤ I'(u)(1+ε)²/(1−e)
    ≤ I'(last)(1+ε)²/(1−e) ≤ I(last)(1+ε)³/(1−e) ≤ C(last)(1+ε)³/(1−e)
    ≤ C'(last)(1+ε)⁴/(1−e)`` — the real index score never exceeds the real
    canonical one (quantized length ≤ raw length), and each prime is removed
    or added with one factor of :func:`score_epsilon`.
    """
    return (1.0 + score_epsilon(terms)) ** 4 / (1.0 - BM25_LENGTH_QUANTIZATION_ERROR)


INITIAL_FETCH_FACTOR = 4
"""The first fetch reads ``4·k`` candidates; each later fetch doubles (D142 §8.3)."""

MIN_WORK_BUDGET = 20_000
WORK_BUDGET_PER_K = 200
"""Candidate work budget ``max(200·k, 20,000)`` — a starting point (D142 §12)."""

EXACT_TIER_THRESHOLD = 5_000
"""Entity tiers at or below this many items are scored exactly (D142 §8.4)."""

FILTER_PUSHDOWN_LIMIT = 20_000
"""Most enumerated eligible rows pushed into an index scan as a filter (§8.3).

Measured at 3M chunks (BM25, k=20, 20 queries): a 1% filter (6,000 rows)
took p50 618 ms / p95 827 ms with no early stop under the pushdown against
3.9 s / 4.5 s and 4 of 20 work-budget stops in the plain loop; a 10% filter
(60,000 rows) took 4.7 s under the pushdown against 1.7 s in the loop.
Above the limit the plain loop runs."""

ENTITY_WORK_BUDGET = 50_000
"""Most scoped items an entity-coverage computation may read (D142 §8.4)."""

_PLANNER_CONTROLS = (
    ("enable_seqscan", "off"),
    ("enable_sort", "off"),
    ("enable_bitmapscan", "off"),
    # EXPLAIN cannot plan a DECLARE; with fraction 1.0 a cursor is planned
    # exactly like the attested SELECT (its LIMIT already bounds the rows),
    # so the attestation covers the plan the cursor runs (review r1 nit).
    ("cursor_tuple_fraction", "1"),
    # A prepared statement must not switch to a generic plan the attestation
    # never saw.
    ("plan_cache_mode", "force_custom_plan"),
)


class P1PlanRejectedError(P1SearchUnavailableError):
    """The nomination statement did not plan as the expected index scan."""


def work_budget(k: int) -> int:
    """The candidate work budget for one ranked search of ``k`` rows."""
    return max(WORK_BUDGET_PER_K * k, MIN_WORK_BUDGET)


@dataclass(frozen=True)
class NominationStatement:
    """One join-free statement that streams ``item_id`` in index order.

    BM25 statements select ``score`` (the canonical, larger-is-better standalone
    score) and order by the raw ``<@>`` expression; semantic statements select
    ``distance`` and order by it. ``count_sql`` counts the searchable rows the
    statement could ever return (used to prove HNSW exhaustion).
    """

    channel: Literal["semantic", "bm25"]
    sql: str
    parameters: Mapping[str, Any]
    expected_index: str
    count_sql: str | None = None
    query_terms: int = 1
    """BM25: the parser's term count for the query (:func:`analyzed_terms`)."""


@dataclass(frozen=True)
class Candidate:
    """One fetched and confirmed candidate."""

    item_id: str
    score: float
    sort_value: float
    qualifier: str | None = None


Confirm = Callable[[Connection, Sequence[str]], Mapping[str, str | None]]
"""Return the eligible subset of a batch, each with its qualifier (or None)."""


def attest_index_plan(
    connection: Connection, *, sql: str, parameters: Mapping[str, Any], index: str
) -> None:
    """Require ``EXPLAIN`` of the exact statement to scan ``index`` (§9)."""
    plan_json = connection.execute(
        text(f"EXPLAIN (FORMAT JSON) {sql}"), dict(parameters)
    ).scalar_one()
    plan = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
    nodes = list(_plan_nodes(plan[0]["Plan"]))
    scans = [node for node in nodes if "Scan" in str(node.get("Node Type", ""))]
    index_scans = [
        node
        for node in scans
        if node.get("Node Type") in {"Index Scan", "Index Only Scan"}
        and node.get("Index Name") == index
    ]
    if not index_scans or len(scans) != len(index_scans):
        kinds = sorted({str(node.get("Node Type")) for node in scans})
        raise P1PlanRejectedError(
            f"nomination statement did not plan as an index scan on {index}"
            f" (scans: {', '.join(kinds) or 'none'})"
        )
    if any(node.get("Node Type") in {"Sort", "Incremental Sort"} for node in nodes):
        raise P1PlanRejectedError(
            f"nomination statement on {index} needs a sort; the index order is not used"
        )


def clamp_deadline(connection: Connection, *, deadline: float | None) -> None:
    """Give the next statement only the time the operation has left (§8.6).

    Set transaction-locally before every attestation, FETCH and confirmation
    of the loop, so no statement starts with a stale, larger timeout. An
    expired deadline leaves one millisecond: the statement is cancelled with
    SQLSTATE 57014, which public boundaries answer as ``p1_deadline``.
    """
    if deadline is None:
        return
    remaining_ms = int((deadline - monotonic()) * 1000)
    connection.execute(
        text("SELECT set_config('statement_timeout', :value, true)"),
        {"value": str(min(max(1, remaining_ms), 2**31 - 1))},
    )


def _plan_nodes(node: Mapping[str, Any]) -> Any:
    yield node
    for child in node.get("Plans", ()) or ():
        yield from _plan_nodes(child)


def nominate_and_confirm(
    connection: Connection,
    *,
    statement: NominationStatement,
    k: int,
    confirm: Confirm,
    deadline: float | None = None,
    budget: int | None = None,
) -> tuple[list[Candidate], P1SearchAccount]:
    """Run one ranked search; return the ``k`` best eligible rows and the account."""
    limit = budget if budget is not None else work_budget(k)
    limits = {
        "k": float(k),
        "work_budget": float(limit),
        "initial_fetch": float(INITIAL_FETCH_FACTOR * k),
    }
    if k <= 0:
        return [], P1SearchAccount(termination=P1Termination.K_FILLED, limits=limits)
    if statement.channel == "semantic":
        max_scan = max(P1_HNSW_MIN_SCAN_TUPLES, 4 * limit)
        connection.exec_driver_sql("SET LOCAL hnsw.iterative_scan = 'strict_order'")
        connection.execute(
            text("SELECT set_config('hnsw.max_scan_tuples', :value, true)"),
            {"value": str(max_scan)},
        )
        connection.execute(
            text("SELECT set_config('hnsw.ef_search', :value, true)"),
            {"value": str(HNSW_EF_SEARCH)},
        )
        limits["hnsw_max_scan_tuples"] = float(max_scan)
        limits["hnsw_ef_search"] = float(HNSW_EF_SEARCH)
    previous = {
        name: str(
            connection.execute(
                text("SELECT current_setting(:name)"), {"name": name}
            ).scalar_one()
        )
        for name, _value in _PLANNER_CONTROLS
    }
    for name, value in _PLANNER_CONTROLS:
        connection.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": name, "value": value},
        )
    # Both extensions use their index only for ORDER BY … LIMIT; the limit is
    # the work budget, the most this search may ever fetch from the cursor.
    sql = f"{statement.sql} LIMIT :nomination_limit"
    parameters = {**statement.parameters, "nomination_limit": limit}
    clamp_deadline(connection, deadline=deadline)

    def restore() -> None:
        # The caller's own values, not the defaults (review r1 nit).
        for name, value in previous.items():
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": name, "value": value},
            )

    try:
        attest_index_plan(
            connection, sql=sql, parameters=parameters, index=statement.expected_index
        )
        # Per-statement option: Connection.execution_options() would change the
        # connection itself and turn every later statement into a cursor.
        stream = connection.execute(
            text(sql), parameters, execution_options={"stream_results": True}
        )
    except DBAPIError:
        # A cancelled EXPLAIN/DECLARE aborts the transaction; restoring there
        # would replace the original error (review r2 P1-3). The rollback
        # that follows discards these transaction-local settings anyway.
        raise
    except BaseException:
        restore()  # e.g. plan rejected: the transaction is healthy
        raise
    restore()

    confirmed: list[Candidate] = []
    fetched = rejected = rounds = 0
    seen: set[str] = set()
    batch_size = INITIAL_FETCH_FACTOR * k
    last_sort: float | None = None
    termination: P1Termination | None = None
    try:
        while termination is None:
            if deadline is not None and monotonic() >= deadline:
                termination = P1Termination.DEADLINE
                break
            size = min(batch_size, limit - fetched)
            if size <= 0:
                termination = P1Termination.WORK_BUDGET
                break
            clamp_deadline(connection, deadline=deadline)  # each FETCH (r2 P1-2)
            rows = stream.fetchmany(size)
            rounds += 1
            fetched += len(rows)
            batch: list[tuple[str, float, float]] = []
            for row in rows:
                item_id = str(row._mapping["item_id"])
                if statement.channel == "semantic":
                    distance = float(row._mapping["distance"])
                    score, sort_value = 1.0 - distance, distance
                else:
                    score = float(row._mapping["score"])
                    sort_value = -score
                    if score <= 0.0:  # positive matches only (§8.1)
                        rejected += 1
                        continue
                if item_id in seen:
                    continue
                seen.add(item_id)
                batch.append((item_id, score, sort_value))
            if rows:
                last = rows[-1]._mapping
                last_sort = (
                    float(last["distance"])
                    if statement.channel == "semantic"
                    else -float(last["score"])
                )
            if batch:
                clamp_deadline(connection, deadline=deadline)
                eligible = confirm(connection, [item for item, _, _ in batch])
                for item_id, score, sort_value in batch:
                    if item_id in eligible:
                        confirmed.append(
                            Candidate(
                                item_id=item_id,
                                score=score,
                                sort_value=sort_value,
                                qualifier=eligible[item_id],
                            )
                        )
                    else:
                        rejected += 1
            if len(rows) < size:
                termination = _drained(connection, statement=statement, fetched=fetched)
                break
            if _filled(
                statement=statement, confirmed=confirmed, k=k, last_sort=last_sort
            ):
                termination = P1Termination.K_FILLED
                break
            batch_size *= 2
    finally:
        stream.close()
    confirmed.sort(key=lambda item: (item.sort_value, item.item_id))
    account = P1SearchAccount(
        termination=termination,
        fetched=fetched,
        confirmed=len(confirmed),
        rejected=rejected,
        fetch_rounds=rounds,
        limits=limits,
    )
    return confirmed[:k], account


HNSW_EF_SEARCH = 200
"""HNSW candidate-list size for the semantic loop (design §9 recall gate).

Measured on a clustered 100k-vector corpus (1,536 dimensions, 500 clusters):
mean recall@10 0.887 at pgvector's default 40, 0.959 at 100, 0.999 at 200
and 400, unfiltered and with a 1,000-document filter alike."""

P1_HNSW_MIN_SCAN_TUPLES = 20_000
"""Floor for one iterative HNSW scan's visited-tuple bound (D94 reference)."""


def _filled(
    *,
    statement: NominationStatement,
    confirmed: Sequence[Candidate],
    k: int,
    last_sort: float | None,
) -> bool:
    """Whether no unfetched row can enter the top ``k`` or tie with its last row."""
    if len(confirmed) < k or last_sort is None:
        return False
    kth = sorted(item.sort_value for item in confirmed)[k - 1]
    if statement.channel == "semantic":
        # The stream is non-decreasing in distance: an unfetched row is at least
        # as far as the last fetched one, so it ranks strictly after the k-th
        # only when the last fetched distance is strictly larger.
        return last_sort > kth
    kth_score = -kth
    last_score = -last_sort
    return kth_score > bm25_stop_margin(statement.query_terms) * last_score


def _drained(
    connection: Connection, *, statement: NominationStatement, fetched: int
) -> P1Termination:
    """Classify a cursor that returned fewer rows than asked (§8.6)."""
    if statement.channel == "bm25":
        # pg_textsearch >= 1.5.0 grows its internal batch until matches are
        # exhausted, so a drained BM25 cursor streamed every matching row.
        return P1Termination.EXHAUSTED
    if statement.count_sql is None:
        return P1Termination.ANN_SCAN_BOUND
    searchable = int(
        connection.execute(
            text(statement.count_sql), dict(statement.parameters)
        ).scalar_one()
    )
    if searchable <= fetched:
        return P1Termination.EXHAUSTED
    return P1Termination.ANN_SCAN_BOUND


def as_result(
    candidates: Sequence[Candidate], *, account: P1SearchAccount, channel: str
) -> P1SearchResult:
    """Attach one-based ranks and the account."""
    return P1SearchResult(
        (
            P1Nomination(
                item_id=item.item_id,
                rank=rank,
                score=item.score,
                channel=channel,
                qualifier=item.qualifier,
            )
            for rank, item in enumerate(candidates, start=1)
        ),
        account=account,
    )
