"""Capture native read-only retrieval SQL and bounded plans without provider calls.

This is an operator diagnostic, not a benchmark answerer or a production query
strategy. Supply a private database URL through the named environment variable.
Cases contain operation, query, deployment_id and optional entity_ids/k/candidate_k.
One stored vector is reused explicitly to isolate database work from embedding.
"""

import argparse
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from datetime import UTC
from decimal import Decimal
import hashlib
import inspect
import json
from pathlib import Path
import re
import time
from typing import Any
from unittest.mock import patch
from uuid import UUID

from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import ExceptionContext
from sqlalchemy.engine import ExecutionContext

from rememberstack.adapters import p1_ranked
from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.adapters.postgres_p1 import PostgresP1Index
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import GeneratedResponse
from rememberstack.model import ModelRequest
from rememberstack.model import ProviderCallUsage
from rememberstack.model import StructuredResponseModel
from rememberstack.surfaces.query_engine import QueryEngine


class DiagnosticSettings(BaseSettings):
    """Read the private diagnostic URL without printing it in an artifact."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_DIAGNOSTIC_")
    database_url: SecretStr


def _dump(*, path: Path, value: object) -> None:
    """Write a human-readable, JSON-safe diagnostic artifact atomically."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def _clean(value: Any) -> Any:
    """Remove large vector literals while retaining plan structure and counts."""
    if isinstance(value, str):
        return re.sub(r"\[-?\d[\d.eE+ ,\-]{500,}\]", "[VECTOR_REDACTED]", value)
    if isinstance(value, Mapping):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_clean(item) for item in value]
    return value


def _error(error: BaseException) -> dict[str, object]:
    """Report diagnostic failure identity without connection URLs or secrets."""
    original = getattr(error, "orig", error)
    diagnostic = getattr(original, "diag", None)
    return {
        "type": type(error).__name__,
        "sqlstate": getattr(original, "sqlstate", None),
        "message": getattr(diagnostic, "message_primary", None),
    }


class FrozenProvider:
    """Return one labelled stored vector and forbid all generation/network work."""

    def __init__(self, *, vector: tuple[float, ...]) -> None:
        """Keep the diagnostic vector and an explicit local-call count."""
        self.vector = vector
        self.embedding_requests = 0

    def generate[ResponseT: StructuredResponseModel](
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        """Reject model generation because this diagnostic never answers questions."""
        raise AssertionError("generation is forbidden in this SQL diagnostic")

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Return local data with zero usage; no provider is invoked or metered."""
        self.embedding_requests += 1
        return EmbeddingResponse(
            vectors=tuple(self.vector for _ in request.texts),
            usage=ProviderCallUsage(
                model_name=request.model,
                tokens_in=0,
                tokens_out=0,
                cost_usd=Decimal(0),
                latency_ms=0,
            ),
        )


class SqlCapture:
    """Observe actual statements and retain native parameters for later EXPLAIN."""

    def __init__(self, *, engine: Engine, directory: Path) -> None:
        """Bind one dedicated diagnostic engine, never the live API engine."""
        self.engine = engine
        self.directory = directory
        self.rows: list[dict[str, Any]] = []
        self.native: list[tuple[str, Any]] = []

    def __enter__(self) -> "SqlCapture":
        """Install observers only for this sequential native call."""
        event.listen(self.engine, "before_cursor_execute", self._before)
        event.listen(self.engine, "after_cursor_execute", self._after)
        event.listen(self.engine, "handle_error", self._failed)
        return self

    def __exit__(self, *_: object) -> None:
        """Remove observers before independently executing the captured plans."""
        event.remove(self.engine, "before_cursor_execute", self._before)
        event.remove(self.engine, "after_cursor_execute", self._after)
        event.remove(self.engine, "handle_error", self._failed)
        _dump(path=self.directory / "statements.json", value=_clean(self.rows))

    def _before(
        self,
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        """Save SQL, safe parameters and stack labels before driver execution."""
        row = {
            "ordinal": len(self.rows),
            "statement": statement,
            "parameters": _clean(parameters),
            "server_side_cursor": bool(getattr(cursor, "name", None)),
            "stack": [
                frame.function
                for frame in inspect.stack(context=0)
                if "rememberstack" in frame.filename
            ],
            "started_monotonic": time.monotonic(),
        }
        self.rows.append(row)
        self.native.append((statement, parameters))
        # Checkpoint before a potentially slow or interrupted statement.
        _dump(path=self.directory / "statements.json", value=_clean(self.rows))

    def _after(
        self,
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        """Record driver execute time, excluding subsequent streaming fetches."""
        row = self.rows[-1]
        row["execute_seconds"] = time.monotonic() - row["started_monotonic"]
        row["driver_rowcount"] = cursor.rowcount

    def _failed(self, context: ExceptionContext) -> None:
        """Retain a failed statement's elapsed time and PostgreSQL error code."""
        row = self.rows[-1]
        row["execute_seconds"] = time.monotonic() - row["started_monotonic"]
        row["error"] = _error(
            context.sqlalchemy_exception or context.original_exception
        )


def _plans(
    *, engine: Engine, capture: SqlCapture, seconds: float, analyze_limit: int
) -> None:
    """Explain unique captured reads, with separately bounded actual execution."""
    seen: set[str] = set()
    analyzed = 0
    for row, (statement, parameters) in zip(capture.rows, capture.native, strict=True):
        upper = statement.lstrip().upper()
        if not upper.startswith(("SELECT", "WITH")) or upper.startswith(
            "SELECT SET_CONFIG"
        ):
            continue
        signature = hashlib.sha256(statement.encode()).hexdigest()
        if signature in seen:
            continue
        seen.add(signature)
        analyze = analyzed < analyze_limit and (
            "error" in row or row.get("execute_seconds", 0) > 0.15
        )
        analyzed += int(analyze)
        prefix = (
            "EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON) "
            if analyze
            else "EXPLAIN (SETTINGS, FORMAT JSON) "
        )
        output: dict[str, Any] = {
            "statement_ordinal": row["ordinal"],
            "analyze": analyze,
            "statement_timeout_seconds": seconds,
            "planner_controls": "normal; jit off; parallel workers 0",
            "note": "independent replay, not original-operation wall time",
        }
        started = time.monotonic()
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql(
                    f"SET LOCAL statement_timeout = {int(seconds * 1000)}"
                )
                connection.exec_driver_sql("SET LOCAL transaction_timeout = 0")
                connection.exec_driver_sql("SET LOCAL jit = off")
                connection.exec_driver_sql(
                    "SET LOCAL max_parallel_workers_per_gather = 0"
                )
                output["plan"] = connection.exec_driver_sql(
                    prefix + statement, parameters
                ).scalar_one()
        except Exception as error:
            output["error"] = _error(error)
        output["elapsed_seconds"] = time.monotonic() - started
        _dump(
            path=capture.directory / f"plan-{row['ordinal']:03d}.json",
            value=_clean(output),
        )


def _run_case(
    *,
    engine: Engine,
    index: PostgresP1Index,
    provider: FrozenProvider,
    pool: BoundedPostgresReadPool,
    case: dict[str, Any],
    output: Path,
    operation_seconds: float,
    plan_seconds: float,
    analyze_limit: int,
) -> None:
    """Run one native operation and record all continuation accounts and plans."""
    directory = output / case["name"]
    directory.mkdir(parents=True, exist_ok=True)
    calls: list[dict[str, Any]] = []
    original = p1_ranked.nominate_and_confirm

    def observed(*args: Any, **kwargs: Any) -> Any:
        """Measure each real continuation call without changing its behavior."""
        statement = kwargs["statement"]
        row: dict[str, Any] = {
            "k": kwargs["k"],
            "channel": statement.channel,
            "statement": statement.sql,
        }
        calls.append(row)
        started = time.monotonic()
        try:
            result = original(*args, **kwargs)
            row["account"] = asdict(result[1])
            return result
        finally:
            row["elapsed_seconds"] = time.monotonic() - started

    query_engine = QueryEngine(
        engine=engine,
        search_index=index,
        model_provider=provider,
        embedding_model=case["embedding_model"],
        fact_read_pool=pool,
        database_budget_seconds=operation_seconds,
    )
    summary: dict[str, Any] = {
        "case": case,
        "observed_at": datetime.now(UTC).isoformat(),
        "operation_budget_seconds": operation_seconds,
        "embedding_mode": "stored vector; NOT question embedding; no provider calls",
        "continuation_calls": calls,
    }
    started = time.monotonic()
    with SqlCapture(engine=engine, directory=directory) as capture:
        with patch.object(p1_ranked, "nominate_and_confirm", observed):
            try:
                operation = getattr(query_engine, case["operation"])
                envelope = operation(
                    deployment_id=UUID(case["deployment_id"]),
                    query=case["query"],
                    entity_ids=tuple(
                        UUID(value) for value in case.get("entity_ids", [])
                    ),
                    k=case.get("k", 50),
                    candidate_k=case.get("candidate_k", 200),
                )
                summary["result"] = {
                    "facts": len(envelope.facts),
                    "evidence": len(envelope.evidence),
                    "chunks": len(envelope.chunks),
                    "sources": len(envelope.sources),
                    "negative": envelope.negative,
                    "truncation": envelope.truncation,
                }
            except Exception as error:
                summary["error"] = _error(error)
    summary["elapsed_seconds"] = time.monotonic() - started
    _dump(path=directory / "summary.json", value=_clean(summary))
    print(json.dumps(_clean(summary), default=str), flush=True)
    _plans(
        engine=engine,
        capture=capture,
        seconds=plan_seconds,
        analyze_limit=analyze_limit,
    )


def main() -> None:
    """Run sequential, bounded diagnostics against an explicitly provided DB."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--operation-seconds", type=float, default=20)
    parser.add_argument("--plan-seconds", type=float, default=20)
    parser.add_argument("--analyze-limit", type=int, default=3)
    arguments = parser.parse_args()
    if (
        not 0 < arguments.operation_seconds <= 600
        or not 0 < arguments.plan_seconds <= 120
    ):
        parser.error(
            "operation/plan bounds must be positive and at most 600/120 seconds"
        )
    engine = create_engine(
        DiagnosticSettings().database_url.get_secret_value(),
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "options": "-c default_transaction_read_only=on -c application_name=locomo-sql-plan-audit"
        },
    )
    arguments.output.mkdir(parents=True, exist_ok=True)
    with engine.connect() as connection:
        if (
            connection.exec_driver_sql("SHOW transaction_read_only").scalar_one()
            != "on"
        ):
            raise RuntimeError("diagnostics require a read-only transaction")
        row = (
            connection.execute(
                text(
                    "SELECT observation_id, embedding::text AS vector, embedding_model "
                    "FROM observations WHERE embedding IS NOT NULL AND vector_norm(embedding)>0 "
                    "ORDER BY observation_id LIMIT 1"
                )
            )
            .mappings()
            .one()
        )
        vector = tuple(json.loads(row["vector"]))
        metadata = {
            "server_version": connection.exec_driver_sql(
                "SHOW server_version"
            ).scalar_one(),
            "extensions": [
                list(item)
                for item in connection.exec_driver_sql(
                    "SELECT extname,extversion FROM pg_extension"
                )
            ],
            "read_only": True,
            "dimensions": len(vector),
            "vector_source_observation_id": str(row["observation_id"]),
            "vector_sha256": hashlib.sha256(row["vector"].encode()).hexdigest(),
            "embedding_model": row["embedding_model"],
            "provider_calls": 0,
            "ingestion_or_schema_changes": False,
        }
    _dump(path=arguments.output / "metadata.json", value=metadata)
    provider = FrozenProvider(vector=vector)
    pool = BoundedPostgresReadPool(
        engine=engine, max_concurrency=1, pool_wait_seconds=5
    )
    index = PostgresP1Index(
        engine=engine, embedding_model=row["embedding_model"], read_pool=pool
    )
    try:
        for case in json.loads(arguments.cases.read_text()):
            case["embedding_model"] = row["embedding_model"]
            _run_case(
                engine=engine,
                index=index,
                provider=provider,
                pool=pool,
                case=case,
                output=arguments.output,
                operation_seconds=arguments.operation_seconds,
                plan_seconds=arguments.plan_seconds,
                analyze_limit=arguments.analyze_limit,
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
