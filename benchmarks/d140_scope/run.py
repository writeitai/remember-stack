"""D140 §3.2 scale check: the time-scope probe's cost in ranked chunk search.

Seeds a synthetic corpus straight into PostgreSQL (no ingest pipeline, no
model calls): ``--lineages`` documents, each with ``--versions`` versions of
``--chunks`` chunks. A ``--periodised`` fraction of the lineages declares one
yearly effective period per version (version *j* in force from 2000+*j*); the
rest are undeclared. Chunk text is synthetic, Zipf-skewed words from a fixed
vocabulary, so BM25 queries have realistic selectivity. Optionally the first
``--semantic-chunks`` chunks also get random unit vectors and an HNSW index.

It then measures, with the real ``PostgresP1Index`` statements:

- ``main_baseline``: the exact pre-D140 statement from ``main`` (served
  version only, through ``memory_v1.chunks_live``);
- ``current`` (the default every read uses), ``at`` a past instant, and
  ``history``: the D140 scope probe, one primary-key lookup per candidate.

Each mode runs the same ``--queries`` two-word queries after one warm-up pass
and reports p50/p95/max. Finally it times ``refresh_document_version_scope``
for one lineage with ``--rewrite-versions`` versions (design target: under
100 ms at 1,000 versions).

Usage (an empty database migrated to head, as a superuser so the bulk load can
skip per-row triggers):

    REMEMBERSTACK_DATABASE_URL=... uv run python -m benchmarks.d140_scope.run \\
        --lineages 1000000 --versions 5 --chunks 10 --periodised 0.5

Synthetic data and timings only; nothing here is a correctness test.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
from datetime import UTC
import json
import random
import statistics
import time
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from remember.models import AtReadTime
from remember.models import HistoryReadTime
from rememberstack.adapters import PostgresP1Index
from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.text_scope import TextScope
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings

DEPLOYMENT = UUID("d1400000-0000-4000-8000-00000000be0c")
MODEL = "d140-bench-model"
VOCABULARY = 50_000
WORDS_PER_CHUNK = 24
BATCH_LINEAGES = 20_000
MAINTENANCE_WORK_MEM = ["256MB"]
"""Index-build memory; set from ``--maintenance-work-mem``."""


def _word(expression: str) -> str:
    """A Zipf-skewed vocabulary word for a hash input expression."""
    return (
        "'w' || floor(power(abs(hashint8("
        f"{expression}))::float8 / 9223372036854775807, 3) * {VOCABULARY})::int"
    )


def seed(
    *,
    engine: Engine,
    lineages: int,
    versions: int,
    chunks: int,
    periodised: float,
    semantic_chunks: int,
) -> dict[str, float]:
    """Load the corpus; returns phase timings in seconds."""
    timings: dict[str, float] = {}
    started = time.monotonic()
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=DEPLOYMENT,
            slug="d140-bench",
            name="D140 scope benchmark",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    every = max(round(1 / periodised), 1) if periodised > 0 else 0
    words = " || ' ' || ".join(
        _word(f"c.n * {WORDS_PER_CHUNK} + {index}") for index in range(WORDS_PER_CHUNK)
    )
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX IF EXISTS ix_chunk_search_bm25"))
    for first in range(0, lineages, BATCH_LINEAGES):
        last = min(first + BATCH_LINEAGES, lineages)
        parameters = {
            "d": DEPLOYMENT,
            "first": first,
            "last": last - 1,
            "versions": versions,
            "chunks": chunks,
            "every": every,
        }
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            connection.execute(text("SET LOCAL synchronous_commit = off"))
            for statement in (
                """
                INSERT INTO documents (doc_id, deployment_id, source_kind,
                  source_ref, title, versioning_mode, current_version_id)
                SELECT md5('d' || i)::uuid, :d, 'upload', 'bench/' || i,
                       'Bench ' || i, 'snapshot', md5('v' || i || '.' || :versions)::uuid
                FROM generate_series(CAST(:first AS int), CAST(:last AS int)) AS i
                """,
                """
                INSERT INTO document_versions (version_id, deployment_id, doc_id,
                  content_hash, version_no, status, current_representation_id)
                SELECT md5('v' || i || '.' || j)::uuid, :d, md5('d' || i)::uuid,
                       'h' || i || '.' || j, j, 'ready',
                       md5('r' || i || '.' || j)::uuid
                FROM generate_series(CAST(:first AS int), CAST(:last AS int)) AS i,
                     generate_series(1, CAST(:versions AS int)) AS j
                """,
                """
                INSERT INTO document_representations (representation_id,
                  deployment_id, version_id, route, status)
                SELECT md5('r' || i || '.' || j)::uuid, :d,
                       md5('v' || i || '.' || j)::uuid, 'passthrough', 'ready'
                FROM generate_series(CAST(:first AS int), CAST(:last AS int)) AS i,
                     generate_series(1, CAST(:versions AS int)) AS j
                """,
                """
                INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,
                  representation_id, ordinal, block_start, block_end,
                  chunk_content_hash, extraction_input_hash, char_start,
                  char_end, created_at)
                SELECT md5('c' || i || '.' || j || '.' || k)::uuid, :d,
                       md5('d' || i)::uuid, md5('v' || i || '.' || j)::uuid,
                       md5('r' || i || '.' || j)::uuid, k, k, k,
                       'c', 'e', k * 200, k * 200 + 199, now()
                FROM generate_series(CAST(:first AS int), CAST(:last AS int)) AS i,
                     generate_series(1, CAST(:versions AS int)) AS j,
                     generate_series(0, CAST(:chunks AS int) - 1) AS k
                """,
                f"""
                INSERT INTO chunk_search (deployment_id, chunk_id, search_text)
                SELECT :d, md5('c' || c.i || '.' || c.j || '.' || c.k)::uuid,
                       {words}
                FROM (
                  SELECT i, j, k,
                         (i::bigint * :versions + j) * :chunks + k AS n
                  FROM generate_series(CAST(:first AS int), CAST(:last AS int)) AS i,
                       generate_series(1, CAST(:versions AS int)) AS j,
                       generate_series(0, CAST(:chunks AS int) - 1) AS k
                ) AS c
                """,  # noqa: S608 -- fixed generated expression
                """
                INSERT INTO document_effective_periods (period_id, deployment_id,
                  doc_id, version_id, effective_from, declared_by)
                SELECT md5('p' || i || '.' || j)::uuid, :d, md5('d' || i)::uuid,
                       md5('v' || i || '.' || j)::uuid,
                       make_timestamptz(2000 + j, 1, 1, 0, 0, 0, 'UTC'), 'ingest'
                FROM generate_series(CAST(:first AS int), CAST(:last AS int)) AS i,
                     generate_series(1, CAST(:versions AS int)) AS j
                WHERE CAST(:every AS int) > 0 AND i % greatest(CAST(:every AS int), 1) = 0
                """,
                """
                INSERT INTO document_effective_time_events (deployment_id,
                  doc_id, event_at, event)
                SELECT :d, md5('d' || i)::uuid, now(), 'declared'
                FROM generate_series(CAST(:first AS int), CAST(:last AS int)) AS i
                WHERE CAST(:every AS int) > 0 AND i % greatest(CAST(:every AS int), 1) = 0
                """,
            ):
                connection.execute(text(statement), parameters)
            connection.execute(
                text(
                    "SELECT refresh_document_version_scope(:d, md5('d' || i)::uuid)"
                    " FROM generate_series(CAST(:first AS int), CAST(:last AS int)) AS i"
                ),
                parameters,
            )
        print(f"seeded lineages {first}..{last - 1}", flush=True)
    timings["load_seconds"] = time.monotonic() - started
    started = time.monotonic()
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('maintenance_work_mem', :m, true)"),
            {"m": MAINTENANCE_WORK_MEM[0]},
        )
        connection.execute(
            text(
                "CREATE INDEX ix_chunk_search_bm25 ON chunk_search"
                " USING bm25 (search_text) WITH (text_config='simple')"
            )
        )
    timings["bm25_index_seconds"] = time.monotonic() - started
    if semantic_chunks:
        started = time.monotonic()
        _seed_vectors(engine=engine, count=semantic_chunks)
        timings["semantic_seconds"] = time.monotonic() - started
    with engine.begin() as connection:
        for table in (
            "documents",
            "document_versions",
            "document_representations",
            "chunks",
            "chunk_search",
            "document_version_scope",
            "document_effective_periods",
        ):
            connection.execute(text(f"ANALYZE {table}"))
    return timings


def _seed_vectors(*, engine: Engine, count: int) -> None:
    """Random unit vectors for the first ``count`` chunks, then HNSW."""
    rng = random.Random(140)
    with engine.connect() as connection:
        ids = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT chunk_id FROM chunk_search WHERE deployment_id = :d"
                    " ORDER BY chunk_id LIMIT :n"
                ),
                {"d": DEPLOYMENT, "n": count},
            )
        ]
    for start in range(0, len(ids), 2_000):
        rows = []
        for chunk_id in ids[start : start + 2_000]:
            vector = [rng.gauss(0, 1) for _ in range(1_536)]
            norm = sum(value * value for value in vector) ** 0.5
            rows.append(
                {
                    "c": chunk_id,
                    "v": "["
                    + ",".join(f"{value / norm:.5f}" for value in vector)
                    + "]",
                }
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE chunk_search SET embedding = CAST(:v AS vector),"
                    " embedding_model = :m, embedding_input_policy_version = :p,"
                    " embedding_text_hash = 'bench'"
                    " WHERE deployment_id = :d AND chunk_id = :c"
                ),
                [
                    {
                        **row,
                        "d": DEPLOYMENT,
                        "m": MODEL,
                        "p": EMBEDDING_INPUT_POLICY_VERSION,
                    }
                    for row in rows
                ],
            )
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('maintenance_work_mem', :m, true)"),
            {"m": MAINTENANCE_WORK_MEM[0]},
        )
        connection.execute(text("REINDEX INDEX ix_chunk_search_embedding_hnsw"))


_MAIN_FROM = """
    FROM chunk_search AS indexed
    JOIN memory_v1.chunks_live AS published
      ON published.deployment_id = indexed.deployment_id
     AND published.chunk_id = indexed.chunk_id
    JOIN memory_v1.documents_live AS document
      ON document.deployment_id = published.deployment_id
     AND document.doc_id = published.doc_id
    LEFT JOIN memory_v1.sections_live AS section
      ON section.deployment_id = published.deployment_id
     AND section.section_id = published.section_id
    WHERE indexed.deployment_id = :deployment_id
"""
MAIN_LEXICAL = text(
    """
    SELECT indexed.chunk_id::text AS item_id,
           -(indexed.search_text <@> to_bm25query(
               :query, 'ix_chunk_search_bm25'))::double precision AS score"""
    + _MAIN_FROM
    + """
    ORDER BY indexed.search_text <@> to_bm25query(:query, 'ix_chunk_search_bm25'),
             indexed.chunk_id
    LIMIT :limit
    """
)
"""``search_chunks_lexical_scored`` exactly as on main before D140 (served
version only, through ``memory_v1.chunks_live``): the baseline."""

MAIN_SEMANTIC = text(
    """
    SELECT indexed.chunk_id::text AS item_id,
           1.0 - (indexed.embedding <=> CAST(:query_vector AS vector)) AS score"""
    + _MAIN_FROM
    + """
      AND indexed.embedding IS NOT NULL
      AND indexed.embedding_model = :embedding_model
      AND indexed.embedding_input_policy_version = :input_policy
    ORDER BY indexed.embedding <=> CAST(:query_vector AS vector), indexed.chunk_id
    LIMIT :limit
    """
)
"""``search_chunks_scored`` exactly as on main before D140: the baseline."""


def _percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50_ms": round(statistics.median(ordered) * 1000, 2),
        "p95_ms": round(ordered[int(0.95 * (len(ordered) - 1))] * 1000, 2),
        "max_ms": round(ordered[-1] * 1000, 2),
    }


def measure(*, engine: Engine, queries: int, semantic: bool) -> dict[str, Any]:
    """Run every mode over the same query set."""
    rng = random.Random(1140)
    terms = [
        f"w{int((rng.random() ** 3) * VOCABULARY)} w{int((rng.random() ** 3) * VOCABULARY)}"
        for _ in range(queries)
    ]
    index = PostgresP1Index(engine=engine, embedding_model=MODEL)
    index.configure_channels(deployment_id=DEPLOYMENT)
    now = datetime.now(UTC)
    modes: dict[str, TextScope | None] = {
        "current": None,
        "at_2003": TextScope.of(
            time=AtReadTime(at=datetime(2003, 6, 1, tzinfo=UTC)), evaluated_at=now
        ),
        "history": TextScope.of(time=HistoryReadTime(), evaluated_at=now),
    }
    results: dict[str, Any] = {}

    def run(label: str, call: Callable[[str], object]) -> None:
        for term in terms[: max(queries // 10, 5)]:
            call(term)  # warm-up
        samples = []
        for term in terms:
            started = time.perf_counter()
            call(term)
            samples.append(time.perf_counter() - started)
        results[label] = _percentiles(samples)
        print(label, results[label], flush=True)

    def main_lexical(query: str) -> object:
        with engine.connect() as connection:
            return connection.execute(
                MAIN_LEXICAL, {"deployment_id": DEPLOYMENT, "query": query, "limit": 20}
            ).all()

    run("bm25_main_baseline", main_lexical)
    for name, scope in modes.items():
        run(
            f"bm25_{name}",
            lambda q, scope=scope: index.search_chunks_lexical_scored(
                deployment_id=str(DEPLOYMENT), query=q, k=20, time=scope
            ),
        )
    if semantic:
        rng_vectors = random.Random(7)
        vectors = []
        for _ in range(queries):
            vector = [rng_vectors.gauss(0, 1) for _ in range(1_536)]
            norm = sum(value * value for value in vector) ** 0.5
            vectors.append(tuple(value / norm for value in vector))
        cursor = iter(range(10**9))

        def semantic_call(scope: TextScope | None) -> Callable[[str], object]:
            def call(_: str) -> object:
                return index.search_chunks_scored(
                    deployment_id=str(DEPLOYMENT),
                    vector=vectors[next(cursor) % len(vectors)],
                    k=20,
                    policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
                    embedder_generation=MODEL,
                    time=scope,
                )

            return call

        def main_semantic(_: str) -> object:
            vector = vectors[next(cursor) % len(vectors)]
            with engine.connect() as connection:
                return connection.execute(
                    MAIN_SEMANTIC,
                    {
                        "deployment_id": DEPLOYMENT,
                        "query_vector": "[" + ",".join(map(str, vector)) + "]",
                        "embedding_model": MODEL,
                        "input_policy": EMBEDDING_INPUT_POLICY_VERSION,
                        "limit": 20,
                    },
                ).all()

        run("semantic_main_baseline", main_semantic)
        for name, scope in modes.items():
            run(f"semantic_{name}", semantic_call(scope))
    return results


def measure_rewrite(*, engine: Engine, versions: int) -> dict[str, float]:
    """Time one projection rewrite for a lineage with ``versions`` versions."""
    doc = "d14000ff-0000-4000-8000-000000000001"
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind, source_ref,"
                " title, versioning_mode) VALUES (:doc, :d, 'upload', 'bench/rewrite',"
                " 'Rewrite', 'snapshot') ON CONFLICT DO NOTHING"
            ),
            {"doc": doc, "d": DEPLOYMENT},
        )
        connection.execute(
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, status, current_representation_id)"
                " SELECT md5('rv' || j)::uuid, :d, :doc, 'rh' || j, j, 'ready',"
                " md5('rr' || j)::uuid FROM generate_series(1, CAST(:n AS int)) AS j"
                " ON CONFLICT DO NOTHING"
            ),
            {"doc": doc, "d": DEPLOYMENT, "n": versions},
        )
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, status)"
                " SELECT md5('rr' || j)::uuid, :d, md5('rv' || j)::uuid,"
                " 'passthrough', 'ready' FROM generate_series(1, CAST(:n AS int)) AS j"
                " ON CONFLICT DO NOTHING"
            ),
            {"d": DEPLOYMENT, "n": versions},
        )
        connection.execute(
            text(
                "INSERT INTO document_effective_periods (period_id, deployment_id,"
                " doc_id, version_id, effective_from, declared_by)"
                " SELECT md5('rp' || j)::uuid, :d, :doc, md5('rv' || j)::uuid,"
                " timestamptz '1000-01-01 UTC' + j * interval '1 year', 'ingest'"
                " FROM generate_series(1, CAST(:n AS int)) AS j"
                " ON CONFLICT DO NOTHING"
            ),
            {"doc": doc, "d": DEPLOYMENT, "n": versions},
        )
        connection.execute(
            text(
                "INSERT INTO document_effective_time_events (deployment_id, doc_id,"
                " event_at, event) VALUES (:d, :doc, now(), 'declared')"
                " ON CONFLICT DO NOTHING"
            ),
            {"doc": doc, "d": DEPLOYMENT},
        )
    samples = []
    for _ in range(20):
        with engine.begin() as connection:
            started = time.perf_counter()
            connection.execute(
                text("SELECT refresh_document_version_scope(:d, :doc)"),
                {"d": DEPLOYMENT, "doc": doc},
            )
            samples.append(time.perf_counter() - started)
    return {"versions": versions, **_percentiles(samples)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lineages", type=int, default=10_000)
    parser.add_argument("--versions", type=int, default=5)
    parser.add_argument("--chunks", type=int, default=10)
    parser.add_argument("--periodised", type=float, default=0.5)
    parser.add_argument("--semantic-chunks", type=int, default=0)
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--rewrite-versions", type=int, default=1_000)
    parser.add_argument("--maintenance-work-mem", default="256MB")
    parser.add_argument("--skip-seed", action="store_true")
    parser.add_argument("--out", default="d140_scope_benchmark.json")
    args = parser.parse_args()

    engine = create_engine(
        load_database_settings().database_url.get_secret_value(), pool_size=2
    )
    MAINTENANCE_WORK_MEM[0] = args.maintenance_work_mem
    report: dict[str, Any] = {"arguments": vars(args)}
    if not args.skip_seed:
        report["seed"] = seed(
            engine=engine,
            lineages=args.lineages,
            versions=args.versions,
            chunks=args.chunks,
            periodised=args.periodised,
            semantic_chunks=args.semantic_chunks,
        )
    with engine.connect() as connection:
        report["corpus"] = {
            name: connection.execute(text(f"SELECT count(*) FROM {name}")).scalar_one()  # noqa: S608
            for name in ("documents", "document_versions", "chunks", "chunk_search")
        }
        report["postgres"] = connection.execute(text("SELECT version()")).scalar_one()
    report["latency"] = measure(
        engine=engine, queries=args.queries, semantic=args.semantic_chunks > 0
    )
    report["projection_rewrite"] = measure_rewrite(
        engine=engine, versions=args.rewrite_versions
    )
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
