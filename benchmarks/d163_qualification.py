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
from collections.abc import Mapping
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

from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.adapters.postgres_p1 import FACT_INPUT_POLICY
from rememberstack.adapters.postgres_p1 import PostgresP1Index
from rememberstack.core.text_scope import fact_in_scope
from rememberstack.core.text_scope import TextScope
from rememberstack.model import Envelope
from rememberstack.model.assured_operations import CurrentFactTime
from rememberstack.model.assured_operations import FactTime
from rememberstack.spine.query_space import load_manifest
from rememberstack.surfaces.query_engine import _CONFIRM_FACTS_CONTEXT_BY_KIND
from rememberstack.surfaces.query_engine import QueryEngine
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor

BASELINE_HEAD = "p9_55_0076"
TARGET_HEAD = "p9_58_0079"
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
    k: int = 50


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
            kind = next(kind for kind in KINDS if f"FROM {kind}s" in query)
            return _TracedCursor(result, self.raw[kind])
        if "batch_ids" in parameters:
            gate_kind: FactKind = next(
                kind
                for kind in KINDS
                if "AS item_id FROM eligible_facts" in query
                and f"SELECT indexed.{kind}_id::text AS item_id" in query
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
    case: Case, *, kind: FactKind, ids: list[str], scoped: bool = False
) -> Statement:
    time = case.time.model_dump(by_alias=True)
    return Statement(
        _CONFIRM_FACTS_CONTEXT_BY_KIND[kind],
        {
            "deployment_id": case.deployment_id,
            "fact_ids": ids,
            "entity_ids": list(case.entity_ids) if scoped else [],
            "ranking_entity_ids": list(case.ranking_entity_ids) if scoped else [],
            "predicate": None,
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


def _record_nomination(
    index: PostgresP1Index, *, arguments: dict[str, Any], use_default: bool = False
) -> tuple[Trace, list[Any]]:
    trace = Trace()
    nominate = index.nominate_facts_scored if use_default else index.search_facts_scored
    with patch.object(
        Connection,
        "execute",
        lambda conn, *args, **kw: trace.execute(conn, *args, **kw),
    ):
        result = nominate(**arguments)
    if not result.account.termination.complete:
        raise AssertionError(result.account)
    return trace, [(row.qualifier, row.item_id, row.score) for row in result]


def capture(engine: Engine, case: Case, *, timeout: float) -> dict[str, Any]:
    index = _index(engine, case)
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
        broad, top = _record_nomination(
            index, arguments=_arguments(case, timeout=timeout)
        )
        record: dict[str, Any] = {
            "raw": broad.raw,
            "ordered": {"broad": top},
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
            count = case.batch_sizes[kind]
            ids = [key for key_kind, key in keys if key_kind == kind][:count]
            assert len(ids) == count, (kind, count)
            original = gates[kind]
            batch = Statement(original.sql, {**original.parameters, "batch_ids": ids})
            rows = _run(connection, batch)
            final = _run(connection, _confirmation(case, kind=kind, ids=ids))
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
            scenarios.update(
                default=scoped,
                filtered={
                    **scoped,
                    "equality_filters": {"predicate": case.predicate}
                    if case.predicate
                    else {},
                },
            )
        for name, options in scenarios.items():
            trace, top = _record_nomination(
                index,
                arguments={**_arguments(case, timeout=timeout), **options},
                use_default=name == "default",
            )
            assert trace.scoped is not None, name
            record["ordered"][name] = top
            record["cuts"][name] = _run(connection, trace.scoped)
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
            if case.entity_ids:
                scoped_ids = [
                    row[0] for row in record["cuts"]["default"] if row[1] == kind
                ]
                scoped_rows = _run(
                    connection,
                    _confirmation(case, kind=kind, ids=scoped_ids, scoped=True),
                )
                if sorted(
                    (row[0], row[3])
                    for row in record["cuts"]["default"]
                    if row[1] == kind
                ) != sorted((str(row[2]), row[-1]) for row in scoped_rows):
                    raise AssertionError(
                        f"scoped ID or coverage disagreement in {kind}"
                    )
                record["cuts"][f"scoped_final/{kind}"] = scoped_rows
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


def compare(
    connection: Connection, baseline: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
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
        "ordered_raw": {
            kind: [len(batch) for batch in batches]
            for kind, batches in baseline["raw"].items()
        },
        "top_k": {name: len(rows) for name, rows in baseline["ordered"].items()},
    }


def _sandbox_queries(case: Case, *, dsn: str, samples: int) -> dict[str, Any]:
    """Run broad SQL through its actual login and interactive limits."""
    sandbox = QuerySandboxExecutor(
        deployment_id=case.deployment_id,
        connect=lambda: psycopg.connect(dsn, options="-c jit=off"),
    )
    queries = {
        **{view: f"SELECT * FROM {view}" for view in BROAD_VIEWS},
        "beam_predicates": "SELECT predicate, count(*) AS n FROM facts_current "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 25",
        "beam_sample": "SELECT fact_kind, fact_id::text, predicate, fact_label AS label "
        "FROM facts_current ORDER BY predicate, fact_id LIMIT 40",
    }
    invalid = sandbox.query_sql(
        sql=queries["beam_sample"].replace("fact_label AS label", "label")
    )
    if invalid.error_code != "execution_error":
        raise AssertionError("the original BEAM label query should fail")

    report: dict[str, Any] = {"beam_undefined_label": str(invalid.error_code)}
    for name, query in queries.items():
        result = sandbox.query_sql(sql=query)
        times = []
        for _ in range(samples):
            if (
                result.error_code is not None
                or result.termination_reason != "completed"
            ):
                break
            start = monotonic()
            result = sandbox.query_sql(sql=query)
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
    engine: Engine, case: Case, *, timeout: float, samples: int, explain: bool
) -> dict[str, Any]:
    """Use shipped budgets for native calls; time executable SQL with planning."""
    index = _index(engine, case)
    trace, _ = _record_nomination(index, arguments=_arguments(case, timeout=timeout))
    statements = {kind: statement for kind, statement, _ in reversed(trace.gates)}
    report: dict[str, Any] = {}
    with engine.connect() as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        connection.exec_driver_sql("SET LOCAL jit = off")
        connection.exec_driver_sql("SET LOCAL max_parallel_workers_per_gather = 0")
        connection.execute(
            text("SELECT set_config('statement_timeout', :value, true)"),
            {"value": str(int(timeout * 1000))},
        )
        keys = _fact_keys(connection, case)
        for kind, statement in statements.items():
            if kind in case.batch_sizes:
                ids = [key for key_kind, key in keys if key_kind == kind][
                    : case.batch_sizes[kind]
                ]
                assert len(ids) == case.batch_sizes[kind]
                statement.parameters["batch_ids"] = ids
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
            if response.truncation and (reason := response.truncation.reason):
                incomplete[reason] += 1
            return len(response.facts)

        measured = _measured(measure, samples=samples)
        measured["incomplete"] = dict(incomplete)
        return measured

    report["native/broad"] = native(())
    if case.entity_ids:
        report["native/scoped"] = native(case.entity_ids)
    return report


def _query_engine(engine: Engine, index: PostgresP1Index, case: Case) -> QueryEngine:
    provider = MagicMock()
    provider.embed.return_value = MagicMock(vectors=(case.vector,), usage=None)
    return QueryEngine(
        engine=engine,
        search_index=index,
        model_provider=provider,
        embedding_model=case.embedding_model,
        fact_read_pool=index._read_pool,
    )


def _native_read(
    query: QueryEngine, case: Case, *, entities: tuple[str, ...]
) -> Envelope:
    return query.facts_context(
        deployment_id=case.deployment_id,
        query="fixed stored vector",
        entity_ids=tuple(UUID(entity) for entity in entities),
        k=50,
        candidate_k=200,
        time=case.time,
        evaluated_at=case.evaluated_at,
    )


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
            assert (baseline["schema_head"], target["schema_head"]) == (
                BASELINE_HEAD,
                TARGET_HEAD,
            )
            assert baseline["case"] == target["case"], (
                "the two releases must use identical inputs"
            )
            with engine.connect() as connection:
                result = compare(connection, baseline, target)
            failed = not (
                result["raw_batches_match"] and result["ordered_top_k_match"]
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
                if not args.query_dsn or args.samples < 1:
                    parser.error("sandbox requires --query-dsn and at least one sample")
                result = {
                    "sandbox": _sandbox_queries(
                        case, dsn=args.query_dsn, samples=args.samples
                    )
                }
            else:
                if args.samples < 40:
                    parser.error("latency requires at least 40 warm samples")
                result = latency(
                    engine,
                    case,
                    timeout=args.timeout,
                    samples=args.samples,
                    explain=args.plans,
                )
                if args.query_dsn:
                    result["sandbox"] = _sandbox_queries(
                        case, dsn=args.query_dsn, samples=args.samples
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
            if "sandbox" in result:
                failed |= any(
                    state["error"] is not None
                    for state in result["sandbox"].values()
                    if isinstance(state, dict)
                )
            result["case"] = case.model_dump(mode="json")
            result["schema_head"] = head
            result["server_version_num"] = int(server)
            result["surface_manifest_hash"] = load_manifest()["surface_manifest_hash"]
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
