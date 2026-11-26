"""Profile retained retrieval cases without ingestion or network model calls.

Run inside the pinned application image with source at /app, an isolated
PostgreSQL socket at /pgsocket, archived diagnostics at /old, and a writable
report directory at /report. This is experimental instrumentation, not a
serving implementation or public benchmark command.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from datetime import UTC
from decimal import Decimal
from functools import wraps
import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.engine import URL

from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.adapters.postgres_p1 import PostgresP1Index
from rememberstack.model import EmbeddingResponse
from rememberstack.model import ProviderCallUsage
from rememberstack.model.assured_operations import CurrentFactTime
from rememberstack.model.assured_operations import HistoryFactTime
from rememberstack.surfaces import query_engine as module
from rememberstack.surfaces.graph_queries import GraphQueries

ROOT = Path("/report")
DEPLOYMENT = UUID("3b71a8ea-5fb5-4836-9833-0440ab04c583")
EVALUATED_AT = datetime(2026, 11, 7, 0, 30, 16, tzinfo=UTC)
ACTIVE = ContextVar("profile_case", default="setup")


@dataclass
class Frame:
    """Track nested timing without attributing child time to its parent twice."""

    name: str
    children: float = 0


STACK: ContextVar[tuple[Frame, ...]] = ContextVar("profile_stack", default=())
RECORDS: list[dict[str, Any]] = []
STATEMENTS: dict[str, str] = {}


def record(*, value: dict[str, Any]) -> None:
    """Write private per-case timing metadata without SQL parameters or keys."""
    row = {"case": ACTIVE.get(), **value}
    RECORDS.append(row)
    with (ROOT / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(row, default=str) + "\n")


def instrument(*, owner: Any, attribute: str, phase: str) -> None:
    """Wrap an existing callable, preserving its arguments, result and failures."""
    original = getattr(owner, attribute)

    @wraps(original)
    def timed(*args: Any, **kwargs: Any) -> Any:
        """Measure inclusive/exclusive wall time of one nested component."""
        parents = STACK.get()
        frame = Frame(name=phase)
        token = STACK.set((*parents, frame))
        started = perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            elapsed = perf_counter() - started
            STACK.reset(token)
            if parents:
                parents[-1].children += elapsed
            record(
                value={
                    "event": "phase",
                    "phase": phase,
                    "seconds": elapsed,
                    "exclusive_seconds": max(0, elapsed - frame.children),
                }
            )

    setattr(owner, attribute, timed)


def load_cases() -> tuple[list[dict[str, Any]], dict[str, tuple[float, ...]]]:
    """Recover immutable operation inputs and their archived query vectors."""
    old = Path("/old/post-fix")
    cases = json.loads((old / "audit.json").read_text())["current"]["boundaries"]
    by_case = {f"{c['item_id']}:{c['tool_index']}": c for c in cases}
    vectors: dict[str, tuple[float, ...]] = {}
    active = ""
    for line in (old / "context-replays.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row["phase"] == "case_start":
            active = row["case"]
        if row["phase"] != "sql_start" or active not in by_case:
            continue
        saved = json.loads((old / f"statement-{row['seq']:04d}.json").read_text())
        parameters = saved["parameters"]
        vector = (
            parameters.get("query_vector") if isinstance(parameters, dict) else None
        )
        if vector is not None:
            arguments = by_case[active]["arguments"]
            query = arguments.get("query", arguments.get("name"))
            vectors[query] = tuple(
                json.loads(vector) if isinstance(vector, str) else vector
            )
    return cases, vectors


class SavedEmbedder:
    """Return archived vectors and prohibit all generative/network model calls."""

    def __init__(self, *, vectors: dict[str, tuple[float, ...]]) -> None:
        """Keep only the explicit finite fixture's saved embedding inputs."""
        self.vectors = vectors

    def embed(self, *, request: Any) -> EmbeddingResponse:
        """Record a cache miss with zero provider usage; reject unknown inputs."""
        assert len(request.texts) == 1
        vector = self.vectors[request.texts[0]]
        assert len(vector) == request.dimensions
        record(value={"event": "saved_embedding", "network_calls": 0})
        return EmbeddingResponse(
            vectors=(vector,),
            usage=ProviderCallUsage(
                model_name=request.model,
                tokens_in=0,
                tokens_out=0,
                cost_usd=Decimal(0),
                latency_ms=0,
            ),
        )


def main() -> None:
    """Run five observed passes of unchanged context operations on the copy."""
    ROOT.mkdir(exist_ok=True)
    assert not (ROOT / "events.jsonl").exists(), "Use a fresh report directory"
    cases, vectors = load_cases()
    engine = create_engine(
        URL.create(
            drivername="postgresql+psycopg",
            username="rememberstack",
            database="rememberstack",
            query={"host": "/pgsocket"},
        ),
        pool_size=4,
        max_overflow=0,
    )

    @event.listens_for(engine, "before_cursor_execute")
    def before(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        """Record statement identity and the active component before execution."""
        context.profile_started = perf_counter()
        context.profile_phase = (
            STACK.get()[-1].name if STACK.get() else "request_control"
        )
        context.profile_hash = hashlib.sha256(statement.encode()).hexdigest()
        STATEMENTS[context.profile_hash] = statement

    @event.listens_for(engine, "after_cursor_execute")
    def after(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        """Measure cursor execution; decoding and model construction stay outside."""
        record(
            value={
                "event": "sql",
                "phase": context.profile_phase,
                "seconds": perf_counter() - context.profile_started,
                "sql_sha256": context.profile_hash,
                "control": statement.lstrip().upper().startswith("SET "),
                "rows": cursor.rowcount,
            }
        )

    for attribute, phase in (
        ("_embed", "embedding"),
        ("layer_admission", "layer_admission"),
        ("_nominate_facts_context", "fact_nomination"),
        ("_nominate_claims_and_sources_claims", "claim_nomination"),
        ("_nominate_claims_and_sources_chunks", "chunk_nomination"),
        ("_confirm_claims", "claim_eligibility_and_content"),
        ("_confirm_chunks", "chunk_eligibility_and_content"),
        ("_enrich_facts_context_facts", "fact_enrichment"),
        ("_touched_pending", "pending_probe"),
        ("_scoped_freshness", "freshness"),
    ):
        instrument(owner=module.QueryEngine, attribute=attribute, phase=phase)
    for attribute, phase in (
        ("_confirm_facts_context", "fact_confirmation"),
        ("_facts_context_evidence", "fact_evidence"),
    ):
        instrument(owner=module, attribute=attribute, phase=phase)
    instrument(owner=PostgresP1Index, attribute="chunk_texts", phase="chunk_body")
    pool = BoundedPostgresReadPool(
        engine=engine, max_concurrency=4, pool_wait_seconds=1
    )
    model = "qwen/qwen3-embedding-8b"
    query = module.QueryEngine(
        engine=engine,
        search_index=PostgresP1Index(
            engine=engine, embedding_model=model, read_pool=pool
        ),
        model_provider=SavedEmbedder(vectors=vectors),
        embedding_model=model,
        fact_read_pool=pool,
        database_budget_seconds=120,
    )
    graph = GraphQueries(engine=engine, deployment_id=DEPLOYMENT)
    results = []
    for repetition in range(5):
        for case in cases:
            case_id = f"{case['item_id']}:{case['tool_index']}"
            ACTIVE.set(f"{repetition}:{case_id}")
            arguments = dict(case["arguments"])
            if "entity_ids" in arguments:
                arguments["entity_ids"] = tuple(
                    UUID(item) for item in arguments["entity_ids"]
                )
            if "time" in arguments:
                arguments["time"] = (
                    HistoryFactTime()
                    if arguments["time"]["mode"] == "history"
                    else CurrentFactTime()
                )
            started = perf_counter()
            if case["tool"] == "resolve_entity":
                result = query.resolve(deployment_id=DEPLOYMENT, **arguments)
            elif case["tool"] == "facts_context":
                result = query.default_facts_context(
                    deployment_id=DEPLOYMENT,
                    graph_queries=graph,
                    evaluated_at=EVALUATED_AT,
                    **arguments,
                )
            else:
                result = query.claims_and_sources_context(
                    deployment_id=DEPLOYMENT, evaluated_at=EVALUATED_AT, **arguments
                )
            seconds = perf_counter() - started
            serialization_start = perf_counter()
            payload = result.model_dump_json()
            serialization_seconds = perf_counter() - serialization_start
            row = {
                "case": ACTIVE.get(),
                "source_case": case_id,
                "tool": case["tool"],
                "repetition": repetition,
                "seconds": seconds,
                "serialization_seconds": serialization_seconds,
                "json_bytes": len(payload.encode()),
                "facts": len(result.facts),
                "claims": len(result.evidence),
                "chunks": len(result.chunks),
                "negative": None
                if result.negative is None
                else result.negative.model_dump(mode="json"),
            }
            results.append(row)
            (ROOT / "cases.json").write_text(json.dumps(results, indent=2) + "\n")
            print(json.dumps(row), flush=True)
    (ROOT / "sql-statements.json").write_text(json.dumps(STATEMENTS, indent=2) + "\n")
    engine.dispose()


if __name__ == "__main__":
    main()
