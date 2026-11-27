"""Paired, provider-free D163 qualification against physically cloned PG18 stores.

Run "capture" with each release on PYTHONPATH, then "compare" on either database.
The same case JSON fixes the vector, scope, anchors, requested IDs and result cap.
Use "latency" on the target for uninstrumented timings and separate actual plans.
See the D163 qualification instructions for fixture preparation.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections import defaultdict
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
from math import ceil
from pathlib import Path
import re
from statistics import median
from time import monotonic
from typing import Any
from typing import cast
from typing import Literal
from typing import LiteralString
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID

import psycopg
from psycopg import sql
from pydantic import BaseModel
from pydantic import Field
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import psycopg as pg_dialect
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.sql.elements import TextClause

from benchmarks.rs_harness_beam.answer_agent import _OPEN_QUERY_MAX_ROWS
from benchmarks.rs_harness_beam.answer_agent import _OPEN_QUERY_SQL
from remember.retrieval import DEFAULT_CONTEXT_K
from remember.retrieval import DEFAULT_RETRIEVAL_DATABASE_BUDGET_SECONDS
from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.adapters.postgres_p1 import FACT_INPUT_POLICY
from rememberstack.adapters.postgres_p1 import PostgresP1Index
from rememberstack.core.text_scope import fact_in_scope
from rememberstack.core.text_scope import TextScope
from rememberstack.model import Envelope
from rememberstack.model.assured_operations import CurrentFactTime
from rememberstack.model.assured_operations import FactTime
from rememberstack.model.envelope import NegativeKind
from rememberstack.spine.query_space import load_manifest
from rememberstack.surfaces.query_engine import _CONFIRM_FACTS_CONTEXT_BY_KIND
from rememberstack.surfaces.query_engine import QueryEngine
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor

BASELINE_HEAD = "p9_55_0076"
TARGET_HEAD = "p9_58_0079"
MANIFEST_BY_HEAD = {
    BASELINE_HEAD: "8e526b54aca2bbac954a0b008156ff1ccb7bf498a9e1b384bd450ccd81b6301d",
    TARGET_HEAD: "dca2b2bb58e3e7a0e1552dac817f704b41668d10ebe47200ffb1969fd644123b",
}
FactKind = Literal["relation", "observation"]
KINDS: tuple[FactKind, ...] = ("relation", "observation")
BROAD_VIEWS = (
    "claims_visible_history",
    "claims_live",
    "facts_current",
    "fact_claim_evidence_live",
    "evidence_lineage",
)
LATENCY_TARGETS = {
    "hosted": (1.0, 5.0),
    "300k": (2.0, 10.0),
    "3m": (2.0, 10.0),
    "120-leaves": (5.0, None),  # native operation deadline applies
}
LATENCY_BATCH_SIZES = {"observation": 804, "relation": 535}


class Case(BaseModel):
    deployment_id: UUID
    embedding_model: str
    vector: tuple[float, ...]
    evaluated_at: datetime
    believed_at: datetime | None = None
    time: FactTime = Field(default_factory=CurrentFactTime)
    predicate: str | None = None
    entity_ids: tuple[str, ...] = ()
    ranking_entity_ids: tuple[str, ...] = ()
    candidate_keys: tuple[tuple[str, str], ...] = ()
    batch_sizes: dict[str, int] = Field(default_factory=dict)
    k: int = DEFAULT_CONTEXT_K


@dataclass(frozen=True)
class Statement:
    sql: TextClause
    parameters: dict[str, Any]


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class _TracedCursor:
    def __init__(self, cursor: Any, batches: list[list[Any]]) -> None:
        self._cursor = cursor
        self._batches = batches

    def fetchmany(self, size: int) -> Any:
        rows = self._cursor.fetchmany(size)
        self._batches.append([tuple(row) for row in rows])
        return rows

    def close(self) -> None:
        self._cursor.close()


class Trace:
    """Capture the real SQL before client ID maps and before scoped SQL limits."""

    def __init__(self) -> None:
        self.raw: dict[str, list[list[Any]]] = defaultdict(list)
        self.gates: list[tuple[FactKind, Statement, list[Any]]] = []
        self.scoped: Statement | None = None
        self._execute = Connection.execute

    def execute(
        self,
        connection: Connection,
        statement: Any,
        parameters: Any = None,
        **kwargs: Any,
    ) -> Any:
        result = self._execute(connection, statement, parameters, **kwargs)
        if not isinstance(statement, TextClause) or not isinstance(parameters, Mapping):
            return result
        query = str(statement)
        if (
            kwargs.get("execution_options", {}).get("stream_results")
            and "nomination_limit" in parameters
        ):
            kind = next((kind for kind in KINDS if f"FROM {kind}s" in query), None)
            if kind is not None:
                return _TracedCursor(result, self.raw[kind])
        if "batch_ids" in parameters and "AS item_id FROM eligible_facts" in query:
            gate_kind: FactKind = next(
                kind
                for kind in KINDS
                if f"SELECT indexed.{kind}_id::text AS item_id" in query
            )
            frozen = result.freeze()
            self.gates.append(
                (
                    gate_kind,
                    Statement(statement, dict(parameters)),
                    [tuple(row) for row in frozen.data],
                )
            )
            return frozen()
        if "branch_limit" in parameters and "1.0 - distance AS score" in query:
            fields = "item_id, qualifier, distance"
            if parameters.get("entity_ids"):
                fields += ", coverage"
            unlimited = re.sub(r"\bLIMIT :(?:branch_limit|limit)\b", "", query)
            unlimited = unlimited.replace(
                "item_id, qualifier, 1.0 - distance AS score", fields
            )
            self.scoped = Statement(text(unlimited), dict(parameters))
        return result


def _index(engine: Engine, case: Case) -> PostgresP1Index:
    return PostgresP1Index(
        engine=engine,
        embedding_model=case.embedding_model,
        read_pool=BoundedPostgresReadPool(
            engine=engine, max_concurrency=1, pool_wait_seconds=5
        ),
    )


def _fact_keys(connection: Connection, case: Case) -> tuple[tuple[str, str], ...]:
    if case.candidate_keys:
        return case.candidate_keys
    return tuple(
        (kind, str(row[0]))
        for kind in KINDS
        for row in connection.execute(
            text(
                f"SELECT {kind}_id FROM {kind}s WHERE deployment_id=:deployment "
                f"AND embedding IS NOT NULL AND vector_norm(embedding)>0 "
                f"AND embedding_model=:model AND embedding_input_policy_version=:policy "
                f"ORDER BY {kind}_id"
            ),
            {
                "deployment": case.deployment_id,
                "model": case.embedding_model,
                "policy": FACT_INPUT_POLICY,
            },
        )
    )


def _confirmation(
    case: Case,
    *,
    kind: FactKind,
    ids: list[str],
    scoped: bool = False,
    predicate: str | None = None,
) -> Statement:
    time = case.time.model_dump(by_alias=True)
    return Statement(
        _CONFIRM_FACTS_CONTEXT_BY_KIND[kind],
        {
            "deployment_id": case.deployment_id,
            "fact_ids": ids,
            "entity_ids": list(case.entity_ids) if scoped else [],
            "ranking_entity_ids": list(case.ranking_entity_ids) if scoped else [],
            "predicate": predicate,
            "time_mode": time["mode"],
            "evaluated_at": case.evaluated_at,
            "at": time.get("at"),
            "from": time.get("from"),
            "to": time.get("to"),
            **TextScope.of(time=case.time, evaluated_at=case.evaluated_at).parameters(),
        },
    )


def _run(connection: Connection, statement: Statement) -> list[Any]:
    return [
        tuple(row) for row in connection.execute(statement.sql, statement.parameters)
    ]


def _fixed_batch(
    statement: Statement,
    keys: tuple[tuple[str, str], ...],
    *,
    kind: FactKind,
    count: int,
) -> Statement:
    ids = [key for key_kind, key in keys if key_kind == kind][:count]
    if count < 1 or len(set(ids)) != count:
        raise AssertionError(f"fixture requires {count} distinct {kind} batch inputs")
    return Statement(statement.sql, {**statement.parameters, "batch_ids": ids})


def _arguments(case: Case, *, timeout: float) -> dict[str, Any]:
    return {
        "deployment_id": str(case.deployment_id),
        "vector": case.vector,
        "k": case.k,
        "kind": None,
        "time": case.time,
        "evaluated_at": case.evaluated_at,
        "deadline": monotonic() + timeout,
    }


@contextmanager
def _trace_sql() -> Iterator[Trace]:
    trace = Trace()
    with patch.object(
        Connection,
        "execute",
        lambda conn, *args, **kw: trace.execute(conn, *args, **kw),
    ):
        yield trace


def _record_nomination(
    index: PostgresP1Index, *, arguments: dict[str, Any], use_default: bool = False
) -> tuple[Trace, list[Any]]:
    nominate = index.nominate_facts_scored if use_default else index.search_facts_scored
    with _trace_sql() as trace:
        result = nominate(**arguments)
    if not result.account.termination.complete:
        raise AssertionError(result.account)
    return trace, [(row.qualifier, row.item_id, row.score) for row in result]


def capture(engine: Engine, case: Case, *, timeout: float) -> dict[str, Any]:
    index = _index(engine, case)
    query = _query_engine(engine, index, case, timeout=timeout)
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        connection.exec_driver_sql("SET LOCAL jit = off")
        connection.execute(
            text("SELECT set_config('statement_timeout', :value, true)"),
            {"value": str(int(timeout * 1000))},
        )
        keys = _fact_keys(connection, case)
        if not keys:
            raise AssertionError("fixture has no attested fact embeddings")
        with _trace_sql() as broad:
            top = _final_top_k(query, case)
        record: dict[str, Any] = {
            "raw": broad.raw,
            "ordered": {"final/broad": top},
            "cuts": {},
        }
        for batch, (kind, statement, rows) in enumerate(broad.gates):
            final = _run(
                connection,
                _confirmation(case, kind=kind, ids=statement.parameters["batch_ids"]),
            )
            if sorted(row[0] for row in rows) != sorted(str(row[2]) for row in final):
                raise AssertionError(
                    f"broad stage disagreement in {kind} batch {batch}"
                )
            record["cuts"][f"broad/{kind}/{batch}"] = [(kind, *row) for row in rows]
            record["cuts"][f"broad_final/{kind}/{batch}"] = final
        gates = {kind: statement for kind, statement, _ in broad.gates}
        for kind in KINDS:
            if kind not in case.batch_sizes:
                continue
            batch = _fixed_batch(
                gates[kind], keys, kind=kind, count=case.batch_sizes[kind]
            )
            rows = _run(connection, batch)
            final = _run(
                connection,
                _confirmation(case, kind=kind, ids=batch.parameters["batch_ids"]),
            )
            if sorted(row[0] for row in rows) != sorted(str(row[2]) for row in final):
                raise AssertionError(f"fixed batch stage disagreement in {kind}")
            record["cuts"][f"fixed_batch/{kind}"] = [(kind, *row) for row in rows]
            record["cuts"][f"fixed_final/{kind}"] = final
        scenarios: dict[str, dict[str, Any]] = {"keyed": {"candidate_keys": keys}}
        if case.entity_ids:
            scoped = {
                "entity_ids": case.entity_ids,
                "ranking_entity_ids": case.ranking_entity_ids,
            }
            scenarios["default"] = scoped
            if case.predicate is not None:
                scenarios["filtered"] = {
                    **scoped,
                    "equality_filters": {"predicate": case.predicate},
                }
        for name, options in scenarios.items():
            trace, top = _record_nomination(
                index,
                arguments={**_arguments(case, timeout=timeout), **options},
                use_default=name == "default",
            )
            assert trace.scoped is not None, name
            record["ordered"][name] = top
            nominated = _run(connection, trace.scoped)
            record["cuts"][name] = nominated
            if name == "keyed":
                continue
            predicate = case.predicate if name == "filtered" else None
            record["ordered"][f"final/{name}"] = _final_top_k(
                query, case, scoped=True, predicate=predicate
            )
            for kind in KINDS:
                expected = [(row[0], row[3]) for row in nominated if row[1] == kind]
                scoped_rows = _run(
                    connection,
                    _confirmation(
                        case,
                        kind=kind,
                        ids=[fact_id for fact_id, _ in expected],
                        scoped=True,
                        predicate=predicate,
                    ),
                )
                if sorted(expected) != sorted(
                    (str(row[2]), row[-1]) for row in scoped_rows
                ):
                    raise AssertionError(
                        f"{name} ID or coverage disagreement in {kind}"
                    )
                record["cuts"][f"{name}_final/{kind}"] = scoped_rows
        for kind in KINDS:
            ids = list(
                dict.fromkeys(fact for key_kind, fact in keys if key_kind == kind)
            )
            rows = _run(connection, _confirmation(case, kind=kind, ids=ids))
            keyed_ids = sorted(
                row[0] for row in record["cuts"]["keyed"] if row[1] == kind
            )
            if keyed_ids != sorted(str(row[2]) for row in rows):
                raise AssertionError(f"keyed stage disagreement in {kind}")
            record["cuts"][f"final/{kind}"] = rows
            # One eligible key repeated at two original ranks.
            repeat = [str(rows[0][2])] if rows else []
            record["cuts"][f"repeated/{kind}"] = _run(
                connection, _confirmation(case, kind=kind, ids=repeat * 2)
            )
            if case.believed_at is not None:
                pinned = fact_in_scope(
                    fact_kind=kind, fact_id="fact.fact_id", pinned_belief=True
                )
                scope = TextScope.of(
                    time=case.time,
                    evaluated_at=case.evaluated_at,
                    believed_at=case.believed_at,
                )
                record["cuts"][f"pinned/{kind}"] = _run(
                    connection,
                    Statement(
                        text(
                            f"SELECT fact_kind, fact_id FROM memory_v1.facts_visible_history AS fact "
                            f"WHERE deployment_id=:deployment_id AND fact_kind='{kind}' "
                            f"AND fact_id=ANY(CAST(:ids AS uuid[])) AND {pinned}"
                        ),
                        {
                            "deployment_id": case.deployment_id,
                            "ids": ids,
                            **scope.parameters(),
                        },
                    ),
                )
        return record


_DIFFERENCES = text("""
WITH baseline AS (SELECT value FROM jsonb_array_elements(CAST(:baseline AS jsonb))),
     target AS (SELECT value FROM jsonb_array_elements(CAST(:target AS jsonb)))
SELECT (SELECT count(*) FROM (TABLE baseline EXCEPT ALL TABLE target) missing),
       (SELECT count(*) FROM (TABLE target EXCEPT ALL TABLE baseline) extra)
""")


def _require_runtime(record: Mapping[str, Any], *, head: str) -> None:
    if record.get("schema_head") != head:
        raise AssertionError(f"expected schema head {head}")
    if record.get("surface_manifest_hash") != MANIFEST_BY_HEAD[head]:
        raise AssertionError(f"runtime manifest does not match schema head {head}")


def compare(
    connection: Connection, baseline: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    _require_runtime(baseline, head=BASELINE_HEAD)
    _require_runtime(target, head=TARGET_HEAD)
    if baseline["case"] != target["case"]:
        raise AssertionError("the two releases must use identical inputs")
    if baseline["cuts"].keys() != target["cuts"].keys():
        raise AssertionError("different eligible cuts were captured")
    counts = {}
    for name, rows in baseline["cuts"].items():
        actual = target["cuts"][name]
        missing, extra = connection.execute(
            _DIFFERENCES, {"baseline": _json(rows), "target": _json(actual)}
        ).one()
        counts[name] = {
            "baseline": len(rows),
            "target": len(actual),
            "missing": missing,
            "extra": extra,
        }
    return {
        "cuts": counts,
        "raw_batches_match": baseline["raw"] == target["raw"],
        "ordered_top_k_match": baseline["ordered"] == target["ordered"],
        "beam_matches": {
            name: baseline["beam"][name] == target["beam"][name]
            and _beam_completed(name, baseline["beam"][name])
            for name, _ in _OPEN_QUERY_SQL
        },
        "ordered_raw": {
            kind: [len(batch) for batch in batches]
            for kind, batches in baseline["raw"].items()
        },
        "top_k": {name: len(rows) for name, rows in baseline["ordered"].items()},
    }


def _sandbox(case: Case, *, dsn: str) -> QuerySandboxExecutor:
    return QuerySandboxExecutor(
        deployment_id=case.deployment_id,
        connect=lambda: psycopg.connect(dsn, options="-c jit=off"),
    )


def _beam_queries(sandbox: QuerySandboxExecutor) -> dict[str, Any]:
    """Capture the ordered results and typed outcomes that the BEAM agent sees."""
    return {
        name: sandbox.query_sql(sql=query, max_rows=_OPEN_QUERY_MAX_ROWS).model_dump(
            mode="json",
            include={
                "columns",
                "rows",
                "empty_result",
                "ordered_result",
                "returned_row_count",
                "termination_reason",
                "truncated",
                "truncation_reason",
                "error_code",
                "error_message",
            },
        )
        for name, query in _OPEN_QUERY_SQL
    }


def _beam_completed(name: str, result: Mapping[str, Any]) -> bool:
    if name == "facts_sample":
        return (
            result["termination_reason"] == "failed"
            and result["error_code"] == QueryErrorCode.EXECUTION_ERROR
        )
    return result["termination_reason"] == "completed" and result["error_code"] is None


def _sandbox_queries(sandbox: QuerySandboxExecutor, *, samples: int) -> dict[str, Any]:
    """Time broad SQL and the corrected sample under interactive limits."""
    beam = dict(_OPEN_QUERY_SQL)
    queries = {
        **{view: f"SELECT * FROM {view}" for view in BROAD_VIEWS},
        "beam_predicates": beam["facts_predicates"],
        "beam_sample_corrected": beam["facts_sample"].replace(
            "predicate, label", "predicate, fact_label AS label"
        ),
    }
    report: dict[str, Any] = {}
    for name, query in queries.items():
        max_rows = _OPEN_QUERY_MAX_ROWS if name.startswith("beam_") else None
        result = sandbox.query_sql(sql=query, max_rows=max_rows)
        times = []
        for _ in range(samples):
            if (
                result.error_code is not None
                or result.termination_reason != "completed"
            ):
                break
            start = monotonic()
            result = sandbox.query_sql(sql=query, max_rows=max_rows)
            times.append(monotonic() - start)
        report[name] = {
            "samples": len(times),
            "termination": result.termination_reason,
            "error": result.error_code,
            "last_rows": len(result.rows),
            "truncation": result.truncation_reason,
            "last_elapsed_ms": result.elapsed_ms,
            **(_percentiles(times) if times else {}),
        }
    return report


def _percentiles(times: list[float]) -> dict[str, float]:
    return {"p50_s": median(times), "p95_s": sorted(times)[ceil(0.95 * len(times)) - 1]}


def _measured(run: Callable[[], Any], *, samples: int) -> dict[str, Any]:
    run()
    times = []
    last = None
    for _ in range(samples):
        start = monotonic()
        last = run()
        times.append(monotonic() - start)
    return {"samples": samples, **_percentiles(times), "last_rows": last}


def _plans(connection: Connection, statement: Statement) -> dict[str, Any]:
    driver = cast("psycopg.Connection", connection.connection.dbapi_connection)
    compiled = statement.sql.compile(
        dialect=pg_dialect.dialect(paramstyle="numeric_dollar")
    )
    assert compiled.positiontup
    values = [statement.parameters[name] for name in compiled.positiontup]
    with driver.cursor() as cursor:
        cursor.execute(
            sql.SQL("PREPARE d163_batch AS ")
            + sql.SQL(cast("LiteralString", str(compiled)))
        )
        explain = (
            sql.SQL("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE d163_batch (")
            + sql.SQL(", ").join(sql.Literal(value) for value in values)
            + sql.SQL(")")
        )
        result = {}
        try:
            for mode in ("force_custom_plan", "force_generic_plan"):
                connection.exec_driver_sql(f"SET LOCAL plan_cache_mode = '{mode}'")
                cursor.execute(explain)
                row = cursor.fetchone()
                assert row is not None
                result[mode] = row[0][0]
        finally:
            cursor.execute("DEALLOCATE d163_batch")
            connection.exec_driver_sql("RESET plan_cache_mode")
        return result


def latency(
    engine: Engine,
    case: Case,
    *,
    timeout: float,
    samples: int,
    explain: bool,
    stratum: str | None = None,
) -> dict[str, Any]:
    """Use shipped budgets for native calls; time executable SQL with planning."""
    if stratum is not None:
        if case.batch_sizes != LATENCY_BATCH_SIZES:
            raise AssertionError(
                f"{stratum} requires 804 observation and 535 relation batch inputs"
            )
        if not case.entity_ids:
            raise AssertionError(
                f"{stratum} requires anchors for the scoped native read"
            )
    index = _index(engine, case)
    trace, _ = _record_nomination(index, arguments=_arguments(case, timeout=timeout))
    statements: dict[FactKind, Statement] = {
        kind: statement for kind, statement, _ in reversed(trace.gates)
    }
    if stratum is not None and statements.keys() != LATENCY_BATCH_SIZES.keys():
        raise AssertionError(f"{stratum} requires traced gates for both fact kinds")
    with engine.connect() as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        connection.exec_driver_sql("SET LOCAL jit = off")
        connection.exec_driver_sql("SET LOCAL max_parallel_workers_per_gather = 0")
        connection.execute(
            text("SELECT set_config('statement_timeout', :value, true)"),
            {"value": str(int(timeout * 1000))},
        )
        keys = _fact_keys(connection, case)
        batches = {
            kind: _fixed_batch(statement, keys, kind=kind, count=case.batch_sizes[kind])
            if kind in case.batch_sizes
            else statement
            for kind, statement in statements.items()
        }
        report: dict[str, Any] = {}
        for kind, statement in batches.items():
            measured = _measured(
                lambda statement=statement: len(_run(connection, statement)),
                samples=samples,
            )
            measured["inputs"] = len(statement.parameters["batch_ids"])
            report[f"batch/{kind}"] = measured
            if explain:
                measured["plans"] = _plans(connection, statement)

    query = _query_engine(engine, index, case)

    def native(entities: tuple[str, ...]) -> dict[str, Any]:
        incomplete: Counter[str] = Counter()

        def measure() -> int:
            response = _native_read(query, case, entities=entities)
            if reason := _native_failure(response):
                incomplete[reason] += 1
            return len(response.facts)

        measured = _measured(measure, samples=samples)
        measured["incomplete"] = dict(incomplete)
        return measured

    report["native/broad"] = native(())
    if case.entity_ids:
        report["native/scoped"] = native(case.entity_ids)
    return report


def _query_engine(
    engine: Engine,
    index: PostgresP1Index,
    case: Case,
    *,
    timeout: float = DEFAULT_RETRIEVAL_DATABASE_BUDGET_SECONDS,
) -> QueryEngine:
    provider = MagicMock()
    provider.embed.return_value = MagicMock(vectors=(case.vector,), usage=None)
    return QueryEngine(
        engine=engine,
        search_index=index,
        model_provider=provider,
        embedding_model=case.embedding_model,
        fact_read_pool=index._read_pool,
        database_budget_seconds=timeout,
    )


def _native_read(
    query: QueryEngine,
    case: Case,
    *,
    entities: tuple[str, ...],
    ranking_entities: tuple[str, ...] | None = None,
    predicate: str | None = None,
    k: int = DEFAULT_CONTEXT_K,
) -> Envelope:
    return query.facts_context(
        deployment_id=case.deployment_id,
        query="fixed stored vector",
        entity_ids=tuple(UUID(entity) for entity in entities),
        k=k,
        predicate=predicate,
        time=case.time,
        evaluated_at=case.evaluated_at,
        _required_entity_ids=(
            tuple(UUID(entity) for entity in ranking_entities)
            if ranking_entities is not None
            else None
        ),
    )


def _native_failure(response: Envelope) -> str | None:
    if response.truncation and response.truncation.reason:
        return response.truncation.reason
    if response.negative and response.negative.kind != NegativeKind.KNOWN_EMPTY:
        return response.negative.kind.value
    return None


def _final_top_k(
    query: QueryEngine,
    case: Case,
    *,
    scoped: bool = False,
    predicate: str | None = None,
) -> list[tuple[str, str]]:
    response = _native_read(
        query,
        case,
        entities=case.entity_ids if scoped else (),
        ranking_entities=case.ranking_entity_ids if scoped else None,
        predicate=predicate,
        k=case.k,
    )
    if reason := _native_failure(response):
        raise AssertionError(f"native final confirmation did not complete: {reason}")
    return [(fact.kind, str(fact.fact_id)) for fact in response.facts]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("capture", "compare", "latency", "sandbox", "scope")
    )
    parser.add_argument("--database", required=True, help="SQLAlchemy PostgreSQL URL")
    parser.add_argument("--case", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--plans", action="store_true")
    parser.add_argument("--stratum", choices=LATENCY_TARGETS)
    parser.add_argument("--expect-scope-boundary", action="store_true")
    parser.add_argument(
        "--query-dsn", help="libpq DSN for the deployment's query login"
    )
    args = parser.parse_args()
    engine = create_engine(args.database)
    failed = False
    result: dict[str, Any]
    try:
        if args.operation == "compare":
            if args.baseline is None or args.target is None:
                parser.error("compare requires --baseline and --target")
            baseline = json.loads(args.baseline.read_text())
            target = json.loads(args.target.read_text())
            with engine.connect() as connection:
                result = compare(connection, baseline, target)
            failed = not (
                result["raw_batches_match"]
                and result["ordered_top_k_match"]
                and all(result["beam_matches"].values())
            ) or any(
                count["missing"] or count["extra"] for count in result["cuts"].values()
            )
        else:
            if args.case is None or args.output is None:
                parser.error("this operation requires --case and --output")
            case = Case.model_validate_json(args.case.read_text())
            with engine.connect() as connection:
                head = connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one()
                assert head in (BASELINE_HEAD, TARGET_HEAD), head
                server = connection.exec_driver_sql(
                    "SHOW server_version_num"
                ).scalar_one()
                assert int(server) // 10000 == 18, server
            identity = {
                "schema_head": head,
                "surface_manifest_hash": load_manifest()["surface_manifest_hash"],
            }
            _require_runtime(
                identity,
                head=TARGET_HEAD
                if args.operation == "latency" and args.stratum
                else head,
            )
            if args.operation in {"capture", "sandbox"} and not args.query_dsn:
                parser.error(f"{args.operation} requires --query-dsn")
            if args.operation == "capture":
                result = capture(engine, case, timeout=args.timeout)
            elif args.operation == "scope":
                if not case.entity_ids:
                    parser.error("scope requires entity_ids in the case")
                query = _query_engine(engine, _index(engine, case), case)
                start = monotonic()
                response = _native_read(query, case, entities=case.entity_ids)
                reason = response.truncation.reason if response.truncation else None
                expected_rows = 0 if args.expect_scope_boundary else 50
                result = {
                    "wall_s": monotonic() - start,
                    "rows": len(response.facts),
                    "reason": reason,
                }
                failed = len(response.facts) != expected_rows or (
                    (reason == "entity_scope_too_broad") != args.expect_scope_boundary
                )
            elif args.operation == "sandbox":
                if args.samples < 1:
                    parser.error("sandbox requires at least one sample")
                result = {}
            else:
                if args.samples < 40:
                    parser.error("latency requires at least 40 warm samples")
                result = latency(
                    engine,
                    case,
                    timeout=args.timeout,
                    samples=args.samples,
                    explain=args.plans,
                    stratum=args.stratum,
                )
                failed = any(
                    value["incomplete"]
                    for name, value in result.items()
                    if name.startswith("native/")
                )
                if args.stratum:
                    batch_limit, native_limit = LATENCY_TARGETS[args.stratum]
                    result["stratum"] = args.stratum
                    result["missed_targets"] = [
                        name
                        for name, value in result.items()
                        if (name.startswith("batch/") and value["p95_s"] > batch_limit)
                        or (
                            name.startswith("native/")
                            and native_limit is not None
                            and value["p95_s"] > native_limit
                        )
                    ]
                    failed |= bool(result["missed_targets"])
            if args.query_dsn and args.operation in {"capture", "sandbox", "latency"}:
                result["beam"] = _beam_queries(_sandbox(case, dsn=args.query_dsn))
                failed |= any(
                    not _beam_completed(name, outcome)
                    for name, outcome in result["beam"].items()
                )
            if args.query_dsn and args.operation in {"sandbox", "latency"}:
                sandbox = _sandbox_queries(
                    _sandbox(case, dsn=args.query_dsn), samples=args.samples
                )
                result["sandbox"] = sandbox
                failed |= any(state["error"] is not None for state in sandbox.values())
            result["case"] = case.model_dump(mode="json")
            result.update(identity)
            result["server_version_num"] = int(server)
        rendered = json.dumps(result, indent=2, default=str, sort_keys=True) + "\n"
        if args.output is None:
            print(rendered, end="")
        else:
            with args.output.open("x") as output:
                output.write(rendered)
        if failed:
            raise SystemExit("D163 qualification gate failed")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
