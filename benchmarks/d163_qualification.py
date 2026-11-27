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
from collections import deque
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
import json
from math import ceil
from pathlib import Path
import re
from statistics import median
from time import monotonic
from time import sleep
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

from benchmarks.d163_query_login import bind_query_login
from benchmarks.rs_harness_beam.answer_agent import _OPEN_QUERY_MAX_ROWS
from benchmarks.rs_harness_beam.answer_agent import _OPEN_QUERY_SQL
from remember.retrieval import DEFAULT_CONTEXT_K
from remember.retrieval import DEFAULT_RETRIEVAL_DATABASE_BUDGET_SECONDS
from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.adapters.p1_ranked import ENTITY_WORK_BUDGET
from rememberstack.adapters.postgres_p1 import _fact_candidates_sql
from rememberstack.adapters.postgres_p1 import _SURVIVOR_SCOPE_CTE
from rememberstack.adapters.postgres_p1 import FACT_INPUT_POLICY
from rememberstack.adapters.postgres_p1 import PostgresP1Index
from rememberstack.core.text_scope import fact_in_scope
from rememberstack.core.text_scope import TextScope
from rememberstack.model import Envelope
from rememberstack.model.assured_operations import CurrentFactTime
from rememberstack.model.assured_operations import FactTime
from rememberstack.model.envelope import NegativeKind
from rememberstack.spine.query_space import load_manifest
from rememberstack.spine.retrieval_eligibility import fact_eligibility_ctes
from rememberstack.surfaces.query_engine import _CONFIRM_FACTS_CONTEXT_BY_KIND
from rememberstack.surfaces.query_engine import QueryEngine
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.executor import _database_failure_result
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.grammar import validate_sql
from rememberstack.surfaces.query_sandbox.limits import INTERACTIVE_LIMITS
from rememberstack.surfaces.query_sandbox.result import QueryResult

BASELINE_HEAD = "p9_55_0076"
TARGET_HEAD = "p9_58_0079"
MANIFEST_BY_HEAD = {
    BASELINE_HEAD: "8e526b54aca2bbac954a0b008156ff1ccb7bf498a9e1b384bd450ccd81b6301d",
    TARGET_HEAD: "84e77c12d570364745cbf636d3a95d4289fa8eecdfe38f6eb2d1bfa4c72557b1",
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
LATENCY_SCALES = {
    "hosted": (812, 10),
    "300k": (300_000, 10),
    "3m": (3_000_000, 10),
    "120-leaves": (None, 121),  # vary the partition count independently
}
LATENCY_BATCH_SIZES = {"observation": 804, "relation": 535}
_BEAM_SAMPLE_CORRECTED = dict(_OPEN_QUERY_SQL)["facts_sample"].replace(
    "predicate, label", "predicate, fact_label AS label"
)


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
        self.contradiction_members: list[Any] = []
        self.confirmation_batches: list[list[dict[str, Any]]] = []
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
        if "fact_ids" in parameters and "ranking_entity_ids" in parameters:
            # Native final confirmation binds both arrays, even for broad reads.
            frozen = result.freeze()
            self.confirmation_batches.append([dict(row) for row in frozen().mappings()])
            return frozen()
        if "groups" in parameters and "fact.contradiction_group" in query:
            frozen = result.freeze()
            self.contradiction_members.extend(tuple(row) for row in frozen.data)
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

    def native_cuts(self, name: str) -> dict[str, list[Any]]:
        return {
            f"native_final/{name}": [
                (batch, row)
                for batch, rows in enumerate(self.confirmation_batches)
                for row in rows
            ],
            f"contradiction/{name}": self.contradiction_members,
        }


def _index(engine: Engine, case: Case) -> PostgresP1Index:
    return PostgresP1Index(
        engine=engine,
        embedding_model=case.embedding_model,
        read_pool=BoundedPostgresReadPool(
            engine=engine, max_concurrency=1, pool_wait_seconds=5
        ),
    )


def _attested_ids(
    connection: Connection,
    case: Case,
    *,
    kind: FactKind,
    ids: list[str] | None = None,
    limit: int | None = None,
) -> list[str]:
    keyed = f"AND {kind}_id = ANY(CAST(:ids AS uuid[]))" if ids is not None else ""
    return list(
        connection.execute(
            text(
                f"SELECT {kind}_id::text FROM {kind}s AS fact WHERE deployment_id=:deployment "
                f"AND embedding IS NOT NULL AND vector_norm(embedding)>0 "
                f"AND embedding_model=:model AND embedding_input_policy_version=:policy "
                f"{keyed} ORDER BY fact.{kind}_id LIMIT :limit"
            ),
            {
                "deployment": case.deployment_id,
                "model": case.embedding_model,
                "policy": FACT_INPUT_POLICY,
                "ids": ids,
                "limit": limit,
            },
        ).scalars()
    )


def _fact_keys(
    connection: Connection, case: Case, *, fixed_batches: bool = False
) -> tuple[tuple[str, str], ...]:
    if case.candidate_keys:
        return case.candidate_keys
    return tuple(
        (kind, fact_id)
        for kind in KINDS
        for fact_id in _attested_ids(
            connection,
            case,
            kind=kind,
            limit=case.batch_sizes.get(kind, 0) if fixed_batches else None,
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
        broad, top = _record_native(query, case)
        record: dict[str, Any] = {
            "raw": broad.raw,
            "ordered": {"final/broad": top},
            "cuts": broad.native_cuts("broad"),
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
            native_trace, final_top = _record_native(
                query, case, scoped=True, predicate=predicate
            )
            record["ordered"][f"final/{name}"] = final_top
            record["cuts"].update(native_trace.native_cuts(name))
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


def _sandbox_completed(state: Mapping[str, Any]) -> bool:
    return state["termination"] == "completed" and state["error"] is None


def _sandbox_comparable(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    fields = ("termination", "error", "truncation", "limits")
    if any(before[field] != after[field] for field in fields):
        return False
    if _sandbox_completed(before):
        return all(
            outcome["work"]["error"] is None and outcome["work"]["plan"] is not None
            for outcome in (before, after)
        )
    # A full diagnostic export can prove equality even if both requests time out.
    return before["termination"] == "failed" and before["error"] == (
        QueryErrorCode.STATEMENT_TIMEOUT
    )


def compare(
    connection: Connection, baseline: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    _require_runtime(baseline, head=BASELINE_HEAD)
    _require_runtime(target, head=TARGET_HEAD)
    if baseline["case"] != target["case"]:
        raise AssertionError("the two releases must use identical inputs")
    if not baseline.get("sandbox_settings") or not target.get("sandbox_settings"):
        raise AssertionError("recapture both releases with the bound query login")
    if baseline["sandbox_settings"] != target["sandbox_settings"]:
        raise AssertionError("the two releases must use identical sandbox JIT settings")
    if "contradiction/broad" not in baseline["cuts"]:
        raise AssertionError("recapture both releases with native contradiction cuts")
    beam_names = {name for name, _ in _OPEN_QUERY_SQL}
    for record in (baseline, target):
        required = {
            f"native_{name}" for name in record["ordered"] if name.startswith("final/")
        }
        if not required.issubset(record["cuts"]):
            raise AssertionError(
                "recapture both releases with native final confirmation cuts"
            )
        if any(
            f"sandbox/{view}" not in record["cuts"]
            or "work" not in record.get("sandbox", {}).get(view, {})
            for view in BROAD_VIEWS
        ):
            raise AssertionError("recapture both releases with all broad sandbox cuts")
        if beam_names != record.get("beam_ordered", {}).keys():
            raise AssertionError("recapture both releases with fixed-instant BEAM rows")
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
        "sandbox_completion_matches": {
            view: _sandbox_comparable(
                baseline["sandbox"][view], target["sandbox"][view]
            )
            for view in BROAD_VIEWS
        },
        "beam_matches": {
            name: baseline["beam_ordered"][name] == target["beam_ordered"][name]
            and _beam_completed(name, baseline["beam"][name])
            and _beam_completed(name, target["beam"][name])
            for name, _ in _OPEN_QUERY_SQL
        },
        "ordered_raw": {
            kind: [len(batch) for batch in batches]
            for kind, batches in baseline["raw"].items()
        },
        "top_k": {name: len(rows) for name, rows in baseline["ordered"].items()},
    }


def _sandbox_settings(reader: psycopg.Connection) -> dict[str, str]:
    return dict(
        reader.execute("""
            SELECT name, setting FROM pg_catalog.pg_settings
            WHERE name LIKE 'jit%'
        """).fetchall()
    )


class _SandboxSampler:
    """Pace a single principal using the same rolling window as admission."""

    def __init__(self, sandbox: QuerySandboxExecutor) -> None:
        self.sandbox = sandbox
        self._spend: deque[tuple[float, float]] = deque()

    def run(self, query: str, *, max_rows: int | None) -> tuple[QueryResult, float]:
        quota = min(
            INTERACTIVE_LIMITS.principal_statement_seconds_per_minute,
            INTERACTIVE_LIMITS.deployment_statement_seconds_per_minute,
        )
        while self._spend:
            age = monotonic() - self._spend[0][0]
            if age > 60:
                self._spend.popleft()
            elif sum(seconds for _, seconds in self._spend) < quota:
                break
            else:
                sleep(max(0.001, 60 - age))
        start = monotonic()
        result = self.sandbox.query_sql(sql=query, max_rows=max_rows)
        end = monotonic()
        elapsed = end - start
        if result.termination_reason != "rejected":
            # Enclosing wall time conservatively bounds the executor's charge.
            self._spend.append((end, elapsed))
        return result, elapsed


def _beam_queries(sampler: _SandboxSampler) -> dict[str, Any]:
    """Record interactive outcomes from the unchanged BEAM requests."""
    return {
        name: sampler.run(query, max_rows=_OPEN_QUERY_MAX_ROWS)[0].model_dump(
            mode="json",
            include={
                "columns",
                "rows",
                "empty_result",
                "elapsed_ms",
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


def _pinned_facts_current(cursor: psycopg.Cursor, evaluated_at: datetime) -> sql.SQL:
    if evaluated_at.utcoffset() is None:
        raise AssertionError("the paired read instant requires a timezone")
    cursor.execute("SELECT pg_get_viewdef('memory_v1.facts_current'::regclass)")
    definition = cursor.fetchone()
    assert definition is not None
    # Pin only the installed view's clock, including in its membership test.
    pinned, substitutions = re.subn(
        r"\b(?:pg_catalog\.)?statement_timestamp\(\)",
        sql.Literal(evaluated_at).as_string(cursor),
        definition[0].rstrip().removesuffix(";"),
    )
    if substitutions != 1:
        raise AssertionError("expected one statement clock in facts_current")
    return sql.SQL(cast("LiteralString", pinned))


def _beam_ordered_rows(
    sandbox: QuerySandboxExecutor, *, evaluated_at: datetime, timeout_ms: int
) -> dict[str, list[Any]]:
    rows = {}
    with sandbox._transaction(
        limits_ms=INTERACTIVE_LIMITS, statement_timeout_ms=timeout_ms
    ) as cursor:
        pinned = _pinned_facts_current(cursor, evaluated_at)
        for name, query in _OPEN_QUERY_SQL:
            if name == "facts_sample":
                query = _BEAM_SAMPLE_CORRECTED
            if name == "facts_predicates":
                # Make tied counts deterministic before the diagnostic limit.
                query = query.replace("ORDER BY 2 DESC", "ORDER BY 2 DESC, 1")
            statement = sql.SQL(cast("LiteralString", query))
            if name in {"facts_predicates", "facts_sample"}:
                statement = sql.SQL("WITH facts_current AS ({}) {}").format(
                    pinned, statement
                )
            cursor.execute(statement)
            rows[name] = cursor.fetchall()
    return rows


def _broad_view_rows(
    sandbox: QuerySandboxExecutor, *, evaluated_at: datetime, timeout_ms: int
) -> dict[str, list[str]]:
    """Export unfiltered rows before the response caps on the frozen fixture."""
    cuts = {}
    with sandbox._transaction(
        limits_ms=INTERACTIVE_LIMITS, statement_timeout_ms=timeout_ms
    ) as cursor:
        cursor.execute("SET LOCAL TIME ZONE 'UTC'")
        pinned = _pinned_facts_current(cursor, evaluated_at)
        for view in BROAD_VIEWS:
            source = (
                sql.SQL("({})").format(pinned)
                if view == "facts_current"
                else sql.Identifier("memory_v1", view)
            )
            # PostgreSQL preserves JSON numbers and nulls without a Python round trip.
            cursor.execute(
                sql.SQL("SELECT to_jsonb(view_row)::text FROM {} AS view_row").format(
                    source
                )
            )
            cuts[f"sandbox/{view}"] = [row[0] for row in cursor.fetchall()]
    return cuts


def _sandbox_work(sandbox: QuerySandboxExecutor, query: str) -> dict[str, Any]:
    """Measure the actual broad statement with the sandbox's transaction limits."""
    statement = validate_sql(query).sql
    try:
        with sandbox._transaction(
            limits_ms=INTERACTIVE_LIMITS,
            statement_timeout_ms=INTERACTIVE_LIMITS.statement_timeout_ms_default,
        ) as cursor:
            cursor.execute(
                f"EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON) {statement}".encode(),
                {},
            )
            row = cursor.fetchone()
            assert row is not None
            return {"plan": row[0][0], "error": None}
    except psycopg.Error as error:
        code, _ = _database_failure_result(error=error, uses_graph_helper=False)
        return {"plan": None, "error": code}


def _sandbox_queries(sampler: _SandboxSampler, *, samples: int) -> dict[str, Any]:
    """Time broad SQL and the corrected sample under interactive limits."""
    queries = {
        **{view: f"SELECT * FROM {view}" for view in BROAD_VIEWS},
        "beam_predicates": dict(_OPEN_QUERY_SQL)["facts_predicates"],
        "beam_sample_corrected": _BEAM_SAMPLE_CORRECTED,
    }
    report: dict[str, Any] = {}
    for name, query in queries.items():
        max_rows = _OPEN_QUERY_MAX_ROWS if name.startswith("beam_") else None
        result, _ = sampler.run(query, max_rows=max_rows)
        times = []
        for _ in range(samples):
            if (
                result.error_code is not None
                or result.termination_reason != "completed"
            ):
                break
            result, elapsed = sampler.run(query, max_rows=max_rows)
            if result.termination_reason != "rejected":
                times.append(elapsed)
        admission_failures = (
            {result.error_code.value: 1}
            if result.error_code is not None
            and result.error_code
            in {QueryErrorCode.QUOTA_EXCEEDED, QueryErrorCode.CONCURRENCY_EXCEEDED}
            else {}
        )
        report[name] = {
            "samples": len(times),
            "admission_failures": admission_failures,
            "termination": result.termination_reason,
            "error": result.error_code,
            "last_rows": len(result.rows),
            "truncation": result.truncation_reason,
            "limits": result.limits.model_dump(mode="json"),
            "last_elapsed_ms": result.elapsed_ms,
            **(
                {"work": _sandbox_work(sampler.sandbox, query)}
                if name in BROAD_VIEWS
                else {}
            ),
            **(_percentiles(times) if times else {}),
        }
    return report


def _percentiles(times: list[float]) -> dict[str, float]:
    return {"p50_s": median(times), "p95_s": sorted(times)[ceil(0.95 * len(times)) - 1]}


def _measured(run: Callable[[], tuple[int, float]], *, samples: int) -> dict[str, Any]:
    run()
    times = []
    last = None
    for _ in range(samples):
        last, elapsed = run()
        times.append(elapsed)
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


def _timed_batch(
    index: PostgresP1Index,
    statement: Statement,
    *,
    timeout: float,
    expected_rows: int | None,
) -> tuple[int, float]:
    with index._ranked_connection(deadline=monotonic() + timeout) as connection:
        start = monotonic()
        rows = _run(connection, statement)
        elapsed = monotonic() - start
    if expected_rows is not None and len(rows) != expected_rows:
        raise AssertionError("batch result count changed after workload validation")
    return len(rows), elapsed


def _latency_scale(
    connection: Connection, *, case: Case, stratum: str
) -> dict[str, Any]:
    expected_chunks, expected_leaves = LATENCY_SCALES[stratum]
    chunks = connection.execute(
        text("SELECT count(*) FROM public.chunks WHERE deployment_id=:deployment"),
        {"deployment": case.deployment_id},
    ).scalar_one()
    if expected_chunks is not None and chunks != expected_chunks:
        raise AssertionError(
            f"{stratum} requires {expected_chunks:,} chunks for the case deployment. "
            f"Found {chunks:,}."
        )
    leaf_counts = {}
    for table in ("chunks", "chunk_claims"):
        leaves, defaults, monthly = connection.execute(
            text("""
                WITH leaves AS (
                    SELECT pg_get_expr(child.relpartbound, child.oid) AS bound
                    FROM pg_partition_tree(CAST(:parent AS regclass)) AS part
                    JOIN pg_class AS child ON child.oid = part.relid
                    WHERE part.isleaf
                ), ranges AS (
                    SELECT bound, regexp_match(bound, :range_pattern) AS dates
                    FROM leaves
                )
                SELECT count(*), count(*) FILTER (WHERE bound = 'DEFAULT'),
                       count(*) FILTER (
                           WHERE (dates[2]::timestamptz AT TIME ZONE 'UTC') =
                                 (dates[1]::timestamptz AT TIME ZONE 'UTC') + interval '1 month'
                       )
                FROM ranges
            """),
            {
                "parent": f"public.{table}",
                "range_pattern": r"^FOR VALUES FROM \('([^']+)'\) TO \('([^']+)'\)$",
            },
        ).one()
        if (leaves, defaults, monthly) != (expected_leaves, 1, expected_leaves - 1):
            raise AssertionError(
                f"{stratum} requires {expected_leaves - 1} monthly {table} leaves "
                f"plus the default. Found {leaves} leaves, including {monthly} "
                f"monthly and {defaults} default."
            )
        leaf_counts[table] = leaves
    return {"chunks": chunks, "range_leaves": leaf_counts}


def _validate_batch(
    connection: Connection, case: Case, *, kind: FactKind, statement: Statement
) -> tuple[int, str]:
    ids = statement.parameters["batch_ids"]
    attested = _attested_ids(connection, case, kind=kind, ids=ids)
    if len(attested) != len(ids):
        raise AssertionError(
            f"{kind} batch requires {len(ids)} distinct model-attested facts "
            f"in the case deployment. Found {len(attested)}."
        )
    eligible = _run(connection, statement)
    control = connection.execute(
        text(
            f"SELECT {kind}_id::text FROM public.{kind}_evidence "
            f"WHERE deployment_id=:deployment AND {kind}_id=ANY(CAST(:eligible AS uuid[])) "
            f"AND stance='supports' ORDER BY {kind}_id LIMIT 1"
        ),
        {"deployment": case.deployment_id, "eligible": [row[0] for row in eligible]},
    ).scalar_one_or_none()
    if control is None:
        raise AssertionError(
            f"{kind} batch requires an eligible supporting-evidence control"
        )
    return len(eligible), control


def _batch_evidence_sha256(
    connection: Connection, case: Case, *, kind: FactKind, ids: list[str]
) -> str:
    """Fingerprint raw evidence and the claim occurrences it causes us to probe."""
    return connection.execute(
        text(f"""
            WITH links AS MATERIALIZED (
                SELECT {kind}_id AS fact_id, claim_id, doc_id, stance
                FROM public.{kind}_evidence
                WHERE deployment_id=:deployment AND {kind}_id=ANY(CAST(:ids AS uuid[]))
            ), evidence AS (
                SELECT jsonb_build_array(fact_id, claim_id, doc_id, stance) AS item
                FROM links
                UNION ALL
                SELECT jsonb_build_array(linked.claim_id, occurrence.chunk_id,
                                         occurrence.created_at AT TIME ZONE 'UTC')
                FROM (SELECT DISTINCT claim_id FROM links) AS linked
                CROSS JOIN LATERAL (
                    SELECT chunk_id, created_at FROM public.chunk_claims
                    WHERE deployment_id=:deployment AND claim_id=linked.claim_id OFFSET 0
                ) AS occurrence
            )
            SELECT encode(sha256(convert_to(
                coalesce(string_agg(item::text, E'\\n' ORDER BY item), ''), 'UTF8'
            )), 'hex') FROM evidence
        """),
        {"deployment": case.deployment_id, "ids": ids},
    ).scalar_one()


def _match_ten_leaf_reference(
    reference: Mapping[str, Any], *, case: Case, chunks: int, workload: dict[str, Any]
) -> None:
    _require_runtime(reference, head=TARGET_HEAD)
    if LATENCY_SCALES.get(reference.get("stratum", "")) != (
        chunks,
        10,
    ) or reference.get("scale") != {
        "chunks": chunks,
        "range_leaves": {"chunks": 10, "chunk_claims": 10},
    }:
        raise AssertionError(
            "120-leaves requires a ten-leaf reference with the same chunk count"
        )
    if reference.get("case") != case.model_dump(mode="json"):
        raise AssertionError(
            "120-leaves requires the same case as the ten-leaf reference"
        )
    for kind, batch in workload.items():
        matched = reference.get("batch_workload", {}).get(kind, {})
        if matched.get("candidate_ids") != batch["candidate_ids"]:
            raise AssertionError(
                f"{kind} candidate IDs differ from the ten-leaf reference"
            )
        if matched.get("evidence_sha256") != batch["evidence_sha256"]:
            raise AssertionError(f"{kind} evidence differs from the ten-leaf reference")


def latency(
    engine: Engine,
    case: Case,
    *,
    timeout: float,
    samples: int,
    explain: bool,
    stratum: str | None = None,
    ten_leaf_reference: Mapping[str, Any] | None = None,
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
        if stratum == "120-leaves" and ten_leaf_reference is None:
            raise AssertionError("120-leaves requires a ten-leaf latency report")
    index = _index(engine, case)
    trace, _ = _record_nomination(index, arguments=_arguments(case, timeout=timeout))
    statements: dict[FactKind, Statement] = {
        kind: statement for kind, statement, _ in reversed(trace.gates)
    }
    if stratum is not None and statements.keys() != LATENCY_BATCH_SIZES.keys():
        raise AssertionError(f"{stratum} requires traced gates for both fact kinds")
    batch_timeout = min(timeout, DEFAULT_RETRIEVAL_DATABASE_BUDGET_SECONDS)
    report: dict[str, Any] = {}
    controls: dict[FactKind, tuple[int, str]] = {}
    workload: dict[str, Any] = {}
    with index._ranked_connection(deadline=monotonic() + batch_timeout) as connection:
        if stratum is not None:
            report["scale"] = _latency_scale(connection, case=case, stratum=stratum)
        keys = _fact_keys(connection, case, fixed_batches=True)
        for kind, statement in statements.items():
            if kind in case.batch_sizes:
                statement = _fixed_batch(
                    statement, keys, kind=kind, count=case.batch_sizes[kind]
                )
                statements[kind] = statement
                controls[kind] = _validate_batch(
                    connection, case, kind=kind, statement=statement
                )
                if stratum is not None:
                    ids = statement.parameters["batch_ids"]
                    workload[kind] = {
                        "candidate_ids": ids,
                        "evidence_sha256": _batch_evidence_sha256(
                            connection, case, kind=kind, ids=ids
                        ),
                    }
        report["p1_settings"] = dict(
            connection.execute(
                text("""
                    SELECT name, setting FROM pg_catalog.pg_settings
                    WHERE name LIKE 'jit%'
                       OR name IN ('max_parallel_workers_per_gather',
                                   'max_parallel_workers', 'max_worker_processes')
                """)
            )
            .tuples()
            .all()
        )
    if stratum is not None:
        report["batch_workload"] = workload
    if stratum == "120-leaves":
        assert ten_leaf_reference is not None
        _match_ten_leaf_reference(
            ten_leaf_reference,
            case=case,
            chunks=report["scale"]["chunks"],
            workload=workload,
        )

    query = _query_engine(engine, index, case)
    scoped_control = None
    if stratum is not None:
        response = _native_read(query, case, entities=case.entity_ids)
        if _native_failure(response) or not response.facts:
            raise AssertionError(
                f"{stratum} requires an eligible fact in the scoped native read"
            )
        scoped_control = str(response.facts[0].fact_id)

    for kind, statement in statements.items():
        expected_rows, control = controls.get(kind, (None, None))
        measured = _measured(
            lambda statement=statement, expected_rows=expected_rows: _timed_batch(
                index, statement, timeout=batch_timeout, expected_rows=expected_rows
            ),
            samples=samples,
        )
        measured["inputs"] = len(statement.parameters["batch_ids"])
        if control is not None:
            measured["positive_control_fact_id"] = control
        report[f"batch/{kind}"] = measured
        if explain:
            with index._ranked_connection(
                deadline=monotonic() + batch_timeout
            ) as connection:
                measured["plans"] = _plans(connection, statement)

    def native(entities: tuple[str, ...]) -> dict[str, Any]:
        incomplete: Counter[str] = Counter()

        def measure() -> tuple[int, float]:
            start = monotonic()
            response = _native_read(query, case, entities=entities)
            if reason := _native_failure(response):
                incomplete[reason] += 1
            return len(response.facts), monotonic() - start

        measured = _measured(measure, samples=samples)
        measured["incomplete"] = dict(incomplete)
        return measured

    report["native/broad"] = native(())
    if case.entity_ids:
        report["native/scoped"] = native(case.entity_ids)
        if scoped_control is not None:
            report["native/scoped"]["positive_control_fact_id"] = scoped_control
    return report


def _scope_gate(engine: Engine, case: Case, *, expect_boundary: bool) -> dict[str, Any]:
    index = _index(engine, case)
    expected = ENTITY_WORK_BUDGET + int(expect_boundary)
    candidates = _fact_candidates_sql(
        required=("relations", "observations"), entity_scoped=True, keyed=False
    )
    parameters = {
        "deployment_id": case.deployment_id,
        "entity_ids": list(case.entity_ids),
    }
    with index._ranked_connection(
        deadline=monotonic() + DEFAULT_RETRIEVAL_DATABASE_BUDGET_SECONDS
    ) as connection:
        raw_rows = connection.execute(
            text(f"{_SURVIVOR_SCOPE_CTE} SELECT count(*) FROM ({candidates}) AS raw"),
            parameters,
        ).scalar_one()
        if raw_rows != expected:
            raise AssertionError(
                f"scope requires {expected:,} raw fact rows. Found {raw_rows:,}."
            )
        visible_endpoints = None
        if not expect_boundary:
            eligibility = fact_eligibility_ctes(
                candidates_sql=candidates, presentation="keys"
            )
            visible_endpoints = connection.execute(
                text(
                    f"{_SURVIVOR_SCOPE_CTE}, {eligibility} SELECT count(*) FROM fact_visible_endpoints"
                ),
                parameters,
            ).scalar_one()
            if visible_endpoints < ENTITY_WORK_BUDGET // 2:
                raise AssertionError(
                    f"scope requires at least {ENTITY_WORK_BUDGET // 2:,} distinct visible endpoints. "
                    f"Found {visible_endpoints:,}."
                )

    query = _query_engine(engine, index, case)
    start = monotonic()
    response = _native_read(query, case, entities=case.entity_ids)
    return {
        "wall_s": monotonic() - start,
        "raw_scope_rows": raw_rows,
        "distinct_visible_endpoints": visible_endpoints,
        "rows": len(response.facts),
        "reason": _native_failure(response),
    }


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


def _record_native(
    query: QueryEngine,
    case: Case,
    *,
    scoped: bool = False,
    predicate: str | None = None,
) -> tuple[Trace, list[tuple[str, str, dict[str, Any] | None]]]:
    with _trace_sql() as trace:
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
    confirmed = {
        (row["kind"], row["fact_id"])
        for batch in trace.confirmation_batches
        for row in batch
    }
    if any((fact.kind, fact.fact_id) not in confirmed for fact in response.facts):
        raise AssertionError(
            "native final facts are missing captured confirmation rows"
        )
    return trace, [
        (
            fact.kind,
            str(fact.fact_id),
            fact.contradiction.model_dump(mode="json") if fact.contradiction else None,
        )
        for fact in response.facts
    ]


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
    parser.add_argument(
        "--ten-leaf-reference", type=Path, help="matching ten-leaf latency report"
    )
    parser.add_argument("--expect-scope-boundary", action="store_true")
    parser.add_argument(
        "--query-dsn", help="libpq DSN for the verified database's query login"
    )
    args = parser.parse_args()
    partition_run = args.operation == "latency" and args.stratum == "120-leaves"
    if partition_run != (args.ten_leaf_reference is not None):
        parser.error("latency --stratum 120-leaves requires --ten-leaf-reference")
    engine = create_engine(args.database)
    failed = False
    result: dict[str, Any]
    with ExitStack() as stack:
        stack.callback(engine.dispose)
        if args.operation == "compare":
            if args.baseline is None or args.target is None:
                parser.error("compare requires --baseline and --target")
            baseline = json.loads(args.baseline.read_text())
            target = json.loads(args.target.read_text())
            connection = stack.enter_context(engine.connect())
            result = compare(connection, baseline, target)
            failed = not (
                result["raw_batches_match"]
                and result["ordered_top_k_match"]
                and all(result["beam_matches"].values())
                and all(result["sandbox_completion_matches"].values())
            ) or any(
                count["missing"] or count["extra"] for count in result["cuts"].values()
            )
        else:
            if args.case is None or args.output is None:
                parser.error("this operation requires --case and --output")
            if args.operation in {"capture", "sandbox"} and not args.query_dsn:
                parser.error(f"{args.operation} requires --query-dsn")
            case = Case.model_validate_json(args.case.read_text())
            connection = stack.enter_context(engine.connect()).execution_options(
                isolation_level="AUTOCOMMIT"
            )
            head = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            assert head in (BASELINE_HEAD, TARGET_HEAD), head
            server = connection.exec_driver_sql("SHOW server_version_num").scalar_one()
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
            sandbox = None
            sandbox_settings = None
            if args.query_dsn and args.operation in {"capture", "sandbox", "latency"}:
                owner = cast(
                    "psycopg.Connection", connection.connection.dbapi_connection
                )
                connect = bind_query_login(owner, args.query_dsn)
                with connect() as reader:
                    sandbox_settings = _sandbox_settings(reader)
                sandbox = QuerySandboxExecutor(
                    deployment_id=case.deployment_id, connect=connect
                )
            if args.operation == "capture":
                result = capture(engine, case, timeout=args.timeout)
            elif args.operation == "scope":
                if not case.entity_ids:
                    parser.error("scope requires entity_ids in the case")
                result = _scope_gate(
                    engine, case, expect_boundary=args.expect_scope_boundary
                )
                expected_rows, expected_reason = (
                    (0, "entity_scope_too_broad")
                    if args.expect_scope_boundary
                    else (DEFAULT_CONTEXT_K, None)
                )
                failed = (
                    result["rows"] != expected_rows
                    or result["reason"] != expected_reason
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
                    ten_leaf_reference=json.loads(args.ten_leaf_reference.read_text())
                    if args.ten_leaf_reference is not None
                    else None,
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
            if sandbox is not None:
                sampler = _SandboxSampler(sandbox)
                result["sandbox_settings"] = sandbox_settings
                result["beam"] = _beam_queries(sampler)
                failed |= any(
                    not _beam_completed(name, outcome)
                    for name, outcome in result["beam"].items()
                )
                if args.operation == "capture":
                    timeout_ms = int(args.timeout * 1000)
                    result["cuts"].update(
                        _broad_view_rows(
                            sandbox,
                            evaluated_at=case.evaluated_at,
                            timeout_ms=timeout_ms,
                        )
                    )
                    result["sandbox_oracle_timeout_ms"] = timeout_ms
                    result["beam_ordered"] = _beam_ordered_rows(
                        sandbox, evaluated_at=case.evaluated_at, timeout_ms=timeout_ms
                    )
                    result["sandbox"] = _sandbox_queries(sampler, samples=0)
                else:
                    checks = _sandbox_queries(sampler, samples=args.samples)
                    result["sandbox"] = checks
                    failed |= any(
                        not _sandbox_completed(state)
                        or (name in BROAD_VIEWS and state["work"]["error"] is not None)
                        for name, state in checks.items()
                    )
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


if __name__ == "__main__":
    main()
