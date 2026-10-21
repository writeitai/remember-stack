"""D142 scaling sweep (WP-RS.8): does ranked-search work stay flat as the corpus grows?

For each corpus size the sweep reseeds the D140 benchmark corpus (dated
lineages, several versions each, Zipf text), optionally makes a share of
lineages repeat their text across versions (the "superseded editions" stratum
of versioned corpora such as law), and measures each ranked channel through
the public ``PostgresP1Index`` API:

- chunk text is rewritten as Zipf-skewed words of varying length (the D140
  seed's text is degenerate for BM25: every word is ``w0``);
- strata: ``current`` (one in-force version per lineage), a 1% document
  filter (a small, fixed eligible set), and ``history`` (every edition);
- per stratum: p50/p95 latency, returned-``k`` rate, termination counts and
  mean fetched candidates (D142 accounts; absent on ``main``, reported null),
  plus one ``EXPLAIN (ANALYZE, BUFFERS)`` buffer count of a nomination
  statement as a proxy for index-internal work.

Run the same command in a ``main`` checkout and in this branch against
separate databases, then compare the JSON files:

    REMEMBERSTACK_DATABASE_URL=... uv run python -m benchmarks.p1_ranked.sweep \\
        --sizes 30000,300000,3000000 --queries 60 --out sweep_branch.json

Synthetic data; timings only, not a correctness test.
"""

from __future__ import annotations

import argparse
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

from benchmarks.d140_scope import run as d140
from rememberstack.adapters import PostgresP1Index
from rememberstack.core.text_scope import TextScope
from rememberstack.model.assured_operations import HistoryFactTime
from rememberstack.model.client import DocumentSearchFilters
from rememberstack.spine.settings import load_database_settings

K = 20


def _reset(engine: Engine) -> None:
    with engine.begin() as connection:
        # isolated benchmark database only: empties every deployment
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))


def _rewrite_text(engine: Engine, *, min_words: int, max_words: int) -> None:
    """Give every chunk Zipf-skewed text of varying length.

    The D140 seed's words come from ``hashint8``, which returns a 32-bit hash,
    divided by 2**63: every word is ``w0`` and every chunk has the same length,
    so every BM25 score ties. Lengths here span the exact and quantized ranges
    of pg_textsearch's length encoding (0..39 exact, 3-bit mantissa above).
    """
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE chunk_search AS target SET search_text = ("
                "  SELECT string_agg('w' || floor(power(abs(hashint8extended("
                "      i::int8, hashtext(target.chunk_id::text)::int8))::float8"
                "      / 9223372036854775807, 3) * :vocabulary)::int, ' ')"
                "  FROM generate_series(1, :min_words"
                "      + abs(hashtext(target.chunk_id::text || 'n'))"
                "        % (:max_words - :min_words + 1)) AS i)"
                " WHERE target.deployment_id = :d"
            ),
            {
                "d": d140.DEPLOYMENT,
                "vocabulary": d140.VOCABULARY,
                "min_words": min_words,
                "max_words": max_words,
            },
        )


def _duplicate_versions(engine: Engine, *, share: float, versions: int) -> int:
    """Copy version 1's chunk text to every later version for ``share`` of lineages."""
    if share <= 0:
        return 0
    every = max(round(1 / share), 1)
    with engine.begin() as connection:
        result = connection.execute(
            text(
                "UPDATE chunk_search AS target SET search_text = source.search_text"
                " FROM chunks AS chunk, chunks AS first_chunk, chunk_search AS source,"
                "      document_versions AS version, document_versions AS first_version"
                " WHERE target.deployment_id = :d AND target.chunk_id = chunk.chunk_id"
                "   AND version.version_id = chunk.version_id AND version.version_no > 1"
                "   AND first_version.doc_id = version.doc_id"
                "   AND first_version.version_no = 1"
                "   AND first_chunk.version_id = first_version.version_id"
                "   AND first_chunk.ordinal = chunk.ordinal"
                "   AND source.chunk_id = first_chunk.chunk_id"
                "   AND ('x' || substr(md5(version.doc_id::text), 1, 8))::bit(32)::int"
                "       % :every = 0"
            ),
            {"d": d140.DEPLOYMENT, "every": every},
        )
        return int(result.rowcount or 0)


def _queries(engine: Engine, count: int) -> list[str]:
    rng = random.Random(142)
    with engine.connect() as connection:
        texts = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT search_text FROM chunk_search WHERE deployment_id = :d"
                    " ORDER BY chunk_id LIMIT 2000"
                ),
                {"d": d140.DEPLOYMENT},
            )
        ]
    queries = []
    for _ in range(count):
        words = rng.choice(texts).split()
        queries.append(" ".join(rng.sample(words, k=min(2, len(words)))))
    return queries


def _percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50_ms": 1000 * statistics.median(ordered),
        "p95_ms": 1000 * ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
    }


def _stratum(
    index: PostgresP1Index, queries: list[str], **arguments: Any
) -> dict[str, Any]:
    latencies: list[float] = []
    filled = 0
    terminations: dict[str, int] = {}
    fetched: list[int] = []
    for query in queries:
        started = time.perf_counter()
        result = index.search_chunks_lexical_scored(
            deployment_id=str(d140.DEPLOYMENT), query=query, k=K, **arguments
        )
        latencies.append(time.perf_counter() - started)
        filled += int(len(result) >= K)
        account = getattr(result, "account", None)
        if account is not None:
            name = str(account.termination.value)
            terminations[name] = terminations.get(name, 0) + 1
            fetched.append(int(account.fetched))
    return {
        **_percentiles(latencies),
        "returned_k_rate": filled / len(queries),
        "terminations": terminations or None,
        "mean_fetched": statistics.mean(fetched) if fetched else None,
        "truncation_rate": (
            sum(
                v for k, v in terminations.items() if k not in {"k_filled", "exhausted"}
            )
            / len(queries)
            if terminations
            else None
        ),
    }


def _nomination_buffers(engine: Engine, query: str) -> int | None:
    """Shared-buffer hits+reads of one join-free BM25 nomination (index work proxy)."""
    with engine.connect() as connection:
        plan = connection.execute(
            text(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)"
                " SELECT chunk_id FROM chunk_search WHERE deployment_id = :d"
                " ORDER BY search_text <@> to_bm25query(:q, 'ix_chunk_search_bm25')"
                " LIMIT :k"
            ),
            {"d": d140.DEPLOYMENT, "q": query, "k": 4 * K},
        ).scalar_one()
        connection.rollback()
    root = (json.loads(plan) if isinstance(plan, str) else plan)[0]["Plan"]
    return int(root.get("Shared Hit Blocks", 0)) + int(
        root.get("Shared Read Blocks", 0)
    )


def measure(engine: Engine, *, queries: int, lineages: int) -> dict[str, Any]:
    index = PostgresP1Index(engine=engine, embedding_model=d140.MODEL)
    index.configure_channels(deployment_id=d140.DEPLOYMENT)
    sample = _queries(engine, queries)
    with engine.connect() as connection:
        doc_ids = [
            UUID(str(row[0]))
            for row in connection.execute(
                text(
                    "SELECT doc_id FROM documents WHERE deployment_id = :d"
                    " ORDER BY doc_id LIMIT :n"
                ),
                {"d": d140.DEPLOYMENT, "n": max(1, lineages // 100)},
            )
        ]
    return {
        "current": _stratum(index, sample),
        "document_filter_1pct": _stratum(
            index, sample, documents=DocumentSearchFilters(doc_ids=tuple(doc_ids))
        ),
        "history": _stratum(
            index,
            sample,
            time=TextScope.of(time=HistoryFactTime(), evaluated_at=datetime.now(UTC)),
        ),
        "nomination_buffers": _nomination_buffers(engine, sample[0]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--sizes", default="30000,300000,3000000")
    parser.add_argument("--versions", type=int, default=5)
    parser.add_argument("--chunks", type=int, default=10)
    parser.add_argument("--periodised", type=float, default=0.5)
    parser.add_argument("--duplicate-share", type=float, default=0.5)
    parser.add_argument("--queries", type=int, default=60)
    parser.add_argument("--min-words", type=int, default=20)
    parser.add_argument("--max-words", type=int, default=300)
    parser.add_argument("--maintenance-work-mem", default="1GB")
    parser.add_argument("--out", default="p1_ranked_sweep.json")
    args = parser.parse_args()
    engine = create_engine(
        load_database_settings().database_url.get_secret_value(), pool_size=2
    )
    d140.MAINTENANCE_WORK_MEM[0] = args.maintenance_work_mem
    report: dict[str, Any] = {"arguments": vars(args), "sizes": {}}
    for size in (int(item) for item in args.sizes.split(",")):
        lineages = max(1, size // (args.versions * args.chunks))
        _reset(engine)
        timings = d140.seed(
            engine=engine,
            lineages=lineages,
            versions=args.versions,
            chunks=args.chunks,
            periodised=args.periodised,
            semantic_chunks=0,
        )
        _rewrite_text(engine, min_words=args.min_words, max_words=args.max_words)
        duplicated = _duplicate_versions(
            engine, share=args.duplicate_share, versions=args.versions
        )
        with engine.begin() as connection:
            connection.execute(text("REINDEX INDEX ix_chunk_search_bm25"))
            connection.execute(text("ANALYZE chunk_search"))
        report["sizes"][str(size)] = {
            "seed": timings,
            "duplicated_chunks": duplicated,
            "latency": measure(engine, queries=args.queries, lineages=lineages),
        }
        print(json.dumps({size: report["sizes"][str(size)]}, indent=2), flush=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)


if __name__ == "__main__":
    main()
