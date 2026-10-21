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
import hashlib
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
from remember.models import HistoryReadTime
from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.model.client import DocumentSearchFilters
from rememberstack.ports.p1_index import P1SearchAccount
from rememberstack.ports.p1_index import search_account
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.query_engine import QueryEngine

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


def _add_metadata(engine: Engine) -> None:
    """Give every version D134 metadata; document filters match on it.

    The D140 seed writes none, so without this a document filter matches
    nothing and the filtered stratum measures an empty result.
    """
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO document_metadata (deployment_id, version_id, doc_id,"
                " family, metadata_mapping_version)"
                " SELECT deployment_id, version_id, doc_id, 'markdown', 'bench'"
                " FROM document_versions WHERE deployment_id = :d"
                " ON CONFLICT DO NOTHING"
            ),
            {"d": d140.DEPLOYMENT},
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


def _queries(count: int, *, seed: int = 142) -> list[str]:
    """Two-word queries from the corpus's own Zipf word distribution.

    Drawn from the generator's distribution, not from the corpus rows, so the
    query set is identical at every corpus size (review r1 P2-4).
    """
    rng = random.Random(seed)
    return [
        " ".join(f"w{int((rng.random() ** 3) * d140.VOCABULARY)}" for _ in range(2))
        for _ in range(count)
    ]


def _percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50_ms": 1000 * statistics.median(ordered),
        "p95_ms": 1000 * ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
    }


class _RecordingIndex:
    """Delegate to the adapter, keeping the last lexical chunk search's account."""

    def __init__(self, inner: PostgresP1Index) -> None:
        self._inner = inner
        self.last: P1SearchAccount | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def search_chunks_lexical(self, **arguments: Any) -> Any:
        result = self._inner.search_chunks_lexical(**arguments)
        self.last = search_account(result)
        return result


class _NoEmbedder:
    """Lexical-only sweep: any embedding request is a harness bug."""

    def embed(self, **_: object) -> Any:
        raise AssertionError("the lexical sweep must not embed")


def _eligible_current(engine: Engine, doc_ids: tuple[UUID, ...] | None) -> int:
    """Current-scope eligible chunks of a stratum (the filter's real size)."""
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM memory_v1.chunks_live AS c"
                    " WHERE c.deployment_id = :d"
                    " AND (CAST(:docs AS uuid[]) IS NULL"
                    "      OR c.doc_id = ANY(CAST(:docs AS uuid[])))"
                ),
                {
                    "d": d140.DEPLOYMENT,
                    "docs": None
                    if doc_ids is None
                    else [str(item) for item in doc_ids],
                },
            ).scalar_one()
        )


def _stratum(
    engine: Engine,
    queries: list[str],
    *,
    doc_ids: tuple[UUID, ...] | None = None,
    time_scope: Any = None,
) -> dict[str, Any]:
    """Run the public ``search_chunks`` operation (BM25) for every query."""
    pool = BoundedPostgresReadPool(
        engine=engine, max_concurrency=2, pool_wait_seconds=30.0
    )
    index = _RecordingIndex(
        PostgresP1Index(engine=engine, embedding_model=d140.MODEL, read_pool=pool)
    )
    surface = QueryEngine(
        engine=engine,
        search_index=index,  # type: ignore[arg-type]
        model_provider=_NoEmbedder(),  # type: ignore[arg-type]
        embedding_model=d140.MODEL,
        fact_read_pool=pool,
    )
    documents = None if doc_ids is None else DocumentSearchFilters(doc_ids=doc_ids)
    latencies: list[float] = []
    outcomes: dict[str, int] = {}
    terminations: dict[str, int] = {}
    fetched: list[int] = []
    rounds: list[int] = []
    for query in queries:
        started = time.perf_counter()
        envelope = surface.search_chunks(
            deployment_id=d140.DEPLOYMENT,
            query=query,
            k=K,
            channel="bm25",
            documents=documents,
            time=time_scope,
        )
        latencies.append(time.perf_counter() - started)
        if len(envelope.chunks) >= K:
            outcome = "k_returned"
        elif envelope.negative is not None:
            outcome = envelope.negative.kind.value
        else:
            outcome = "fewer_than_k"
        if envelope.truncation is not None and envelope.truncation.reason:
            outcome += f":{envelope.truncation.reason}"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if index.last is not None:
            name = index.last.termination.value
            terminations[name] = terminations.get(name, 0) + 1
            fetched.append(index.last.fetched)
            rounds.append(index.last.fetch_rounds)
    truncated = sum(
        count
        for name, count in terminations.items()
        if name not in {"k_filled", "exhausted"}
    )
    return {
        **_percentiles(latencies),
        "queries": len(queries),
        "eligible_current_chunks": _eligible_current(engine, doc_ids),
        "public_outcomes": outcomes,
        "returned_k_rate": outcomes.get("k_returned", 0) / len(queries),
        "terminations": terminations,
        "truncation_rate": truncated / len(queries),
        "mean_fetched": statistics.mean(fetched) if fetched else None,
        "max_fetched": max(fetched) if fetched else None,
        "mean_fetch_rounds": statistics.mean(rounds) if rounds else None,
    }


def _nomination_work(engine: Engine, query: str, *, fetched: int) -> dict[str, Any]:
    """EXPLAIN (ANALYZE, BUFFERS) of the production nomination statement.

    The statement the cursor runs (canonical projection, index order), read
    to ``fetched`` rows — the depth the measured search actually consumed —
    reporting planning time and shared buffers (index-internal work proxy).
    """
    index = PostgresP1Index(engine=engine, embedding_model=d140.MODEL)
    nomination = index._testimony_query(  # noqa: SLF001 -- the real statement
        target="chunks",
        channel="bm25",
        deployment_id=str(d140.DEPLOYMENT),
        current_only=False,
        equality_filters=None,
        entity_ids=(),
        documents=None,
        time=None,
        query_text=query,
    ).nomination
    with engine.connect() as connection:
        for name in ("enable_seqscan", "enable_bitmapscan", "enable_sort"):
            connection.execute(text(f"SET LOCAL {name} = off"))
        plan = connection.execute(
            text(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                f"{nomination.sql} LIMIT :nomination_limit"
            ),
            {**nomination.parameters, "nomination_limit": max(1, fetched)},
        ).scalar_one()
        connection.rollback()
    document = (json.loads(plan) if isinstance(plan, str) else plan)[0]
    root = document["Plan"]
    return {
        "query": query,
        "rows": fetched,
        "planning_ms": document.get("Planning Time"),
        "execution_ms": document.get("Execution Time"),
        "shared_blocks": int(root.get("Shared Hit Blocks", 0))
        + int(root.get("Shared Read Blocks", 0)),
    }


def _corpus_hash(engine: Engine) -> str:
    """A content hash of the indexed corpus (ids and search text)."""
    with engine.connect() as connection:
        return str(
            connection.execute(
                text(
                    "SELECT md5(string_agg(chunk_id::text || md5(search_text), ','"
                    " ORDER BY chunk_id)) FROM chunk_search WHERE deployment_id = :d"
                ),
                {"d": d140.DEPLOYMENT},
            ).scalar_one()
        )


def measure(engine: Engine, *, queries: int, lineages: int) -> dict[str, Any]:
    """Every stratum through the public operation, plus corpus/query identity."""
    sample = _queries(queries)
    with engine.connect() as connection:
        ordered = [
            UUID(str(row[0]))
            for row in connection.execute(
                text(
                    "SELECT doc_id FROM documents WHERE deployment_id = :d"
                    " ORDER BY md5(doc_id::text)"
                ),
                {"d": d140.DEPLOYMENT},
            )
        ]
    strata: dict[str, dict[str, Any]] = {
        "current": {},
        "history": {"time_scope": HistoryReadTime()},
        "docs_0.1pct": {"doc_ids": tuple(ordered[: max(1, lineages // 1000)])},
        "docs_1pct": {"doc_ids": tuple(ordered[: max(1, lineages // 100)])},
        "docs_10pct": {"doc_ids": tuple(ordered[: max(1, lineages // 10)])},
        # 10 current chunks per lineage: both sides of the 5,000-row tier
        "docs_4990_rows": {"doc_ids": tuple(ordered[:499])},
        "docs_5010_rows": {"doc_ids": tuple(ordered[:501])},
    }
    report: dict[str, Any] = {
        "query_sha256": hashlib.sha256("\n".join(sample).encode()).hexdigest(),
        "corpus_md5": _corpus_hash(engine),
        "strata": {},
    }
    for name, arguments in strata.items():
        report["strata"][name] = _stratum(engine, sample, **arguments)
    report["nomination_work"] = _nomination_work(
        engine, sample[0], fetched=int(report["strata"]["current"]["max_fetched"] or K)
    )
    return report


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
    parser.add_argument(
        "--measure-only",
        action="store_true",
        help="measure the corpus already in the database (one size, no reseed)",
    )
    args = parser.parse_args()
    engine = create_engine(
        load_database_settings().database_url.get_secret_value(), pool_size=2
    )
    d140.MAINTENANCE_WORK_MEM[0] = args.maintenance_work_mem
    report: dict[str, Any] = {"arguments": vars(args), "sizes": {}}
    if args.measure_only:
        with engine.connect() as connection:
            lineages = int(
                connection.execute(
                    text("SELECT count(*) FROM documents WHERE deployment_id = :d"),
                    {"d": d140.DEPLOYMENT},
                ).scalar_one()
            )
        report["sizes"]["existing"] = {
            "lineages": lineages,
            "latency": measure(engine, queries=args.queries, lineages=lineages),
        }
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)
        print(json.dumps(report, indent=2, default=str))
        return
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
        _add_metadata(engine)
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
