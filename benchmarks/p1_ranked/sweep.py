"""D142 scaling sweep (WP-RS.8): how ranked BM25 chunk search scales with the corpus.

For each corpus size the sweep reseeds the D140 benchmark corpus (dated
lineages, several versions each), rewrites chunk text as Zipf-skewed words of
20-300 words (the D140 seed's own text is degenerate for BM25: every word is
``w0``), gives every version D134 metadata, and makes a share of lineages
repeat their text across versions (the "superseded editions" stratum of
versioned corpora such as law). ``--measure-only`` measures a corpus already
in the database instead.

What it measures, through the public ``QueryEngine.search_chunks`` (BM25,
k = 20) with a fixed query set drawn from the generator's distribution:

- strata: ``current``, ``history``, 0.1%/1%/10% document filters and 4,990 /
  5,010 eligible rows (both sides of the exact tier), and D156 tag filters
  on 1% and 90% of lineages (``tags_1pct`` / ``tags_90pct``);
- with ``--claims``, the same tag strata (and the unfiltered one) for BM25
  claim search over one claim per served chunk, reused across versions;
- per stratum: p50/p95 latency, the public outcome of every query (k
  returned, fewer than k, ``known_empty`` or a boundary with its reason), the
  eligible current-chunk count, and the adapter's account per query
  (termination, fetched candidates, fetch rounds);
- the production nomination statement's plan with its real ``LIMIT`` (the
  work budget), by ``EXPLAIN`` without ``ANALYZE``, for five queries;
- corpus and query hashes.

**Not measured:** index-internal execution work (posting traversal, scoring
passes, buffers) of the cursor as it runs; only the rows it hands out are.
The synthetic corpus has no vectors, so hydration ignores the embedding
generation, and only the BM25 chunk channel is swept.

Run the same command in a ``main`` checkout and in this branch against
separate databases, then compare the JSON files:

    REMEMBERSTACK_DATABASE_URL=... uv run python -m benchmarks.p1_ranked.sweep \\
        --sizes 30000,300000,3000000 --queries 40 --out sweep_branch.json

Synthetic data; timings and completeness only, not a correctness test.
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
from remember.models import DocumentTagFilter
from remember.models import HistoryReadTime
from rememberstack.adapters import p1_ranked
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
        # chunks, claims and occurrences are not reached by the deployments
        # cascade; a reseed would otherwise duplicate them
        connection.execute(
            text("TRUNCATE TABLE deployments, chunks, chunk_claims, claims CASCADE")
        )


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


TAG_SHARES: dict[str, float] = {"bench:1pct": 0.01, "bench:90pct": 0.9}
"""D156 qualification tags and the share of lineages each is assigned to."""


def _add_tags(engine: Engine, *, ordered: list[UUID]) -> None:
    """Register the qualification tags and assign each to its share of lineages.

    Lineages are taken in the same hashed order as the ``doc_ids`` strata, so
    the 1% tag and the 1% ``doc_ids`` filter select the same lineages.
    """
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM tags WHERE deployment_id = :d"), {"d": d140.DEPLOYMENT}
        )
        for name, share in TAG_SHARES.items():
            tag_id = connection.execute(
                text(
                    "INSERT INTO tags (deployment_id, tag_id, name, description)"
                    " VALUES (:d, gen_random_uuid(), :name, 'qualification')"
                    " RETURNING tag_id"
                ),
                {"d": d140.DEPLOYMENT, "name": name},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO document_tags (deployment_id, doc_id, tag_id)"
                    " SELECT :d, doc_id, :tag FROM unnest(CAST(:docs AS uuid[]))"
                    " AS doc_id"
                ),
                {
                    "d": d140.DEPLOYMENT,
                    "tag": tag_id,
                    "docs": [
                        str(doc_id)
                        for doc_id in ordered[: max(1, round(len(ordered) * share))]
                    ],
                },
            )
        connection.execute(text("ANALYZE document_tags"))


def _seed_claims(engine: Engine) -> int:
    """One current claim per chunk of each served version, reused by every version.

    The claim's text is its served chunk's text and it has one occurrence
    (``chunk_claims``) per version at the same ordinal, as D56 reuse leaves an
    unchanged chunk's claim. The claim BM25 index is rebuilt after the load.
    """
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX IF EXISTS ix_claims_current_bm25"))
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        created = connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version)"
                " SELECT md5('claim' || chunk.chunk_id::text)::uuid, chunk.deployment_id,"
                " chunk.doc_id, chunk.chunk_id, search.search_text, search.search_text,"
                " 0, 10, true, true, 'bench'"
                " FROM documents AS document"
                " JOIN chunks AS chunk ON chunk.deployment_id = document.deployment_id"
                "  AND chunk.version_id = document.current_version_id"
                " JOIN chunk_search AS search ON search.chunk_id = chunk.chunk_id"
                " WHERE document.deployment_id = :d"
            ),
            {"d": d140.DEPLOYMENT},
        ).rowcount
        connection.execute(
            text(
                "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
                " evidence_spans)"
                " SELECT chunk.deployment_id, chunk.chunk_id, claim.claim_id,"
                ' \'[{"char_start": 0, "char_end": 10}]\'::jsonb'
                " FROM claims AS claim"
                " JOIN chunks AS origin ON origin.chunk_id = claim.chunk_id"
                " JOIN chunks AS chunk ON chunk.deployment_id = origin.deployment_id"
                "  AND chunk.doc_id = origin.doc_id AND chunk.ordinal = origin.ordinal"
                " WHERE claim.deployment_id = :d"
            ),
            {"d": d140.DEPLOYMENT},
        )
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('maintenance_work_mem', :m, true)"),
            {"m": d140.MAINTENANCE_WORK_MEM[0]},
        )
        connection.execute(
            text(
                "CREATE INDEX ix_claims_current_bm25 ON claims USING bm25"
                " (claim_text) WITH (text_config='simple') WHERE is_current_testimony"
            )
        )
        connection.execute(text("ANALYZE claims"))
        connection.execute(text("ANALYZE chunk_claims"))
    return int(created or 0)


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

    def search_claims_lexical(self, **arguments: Any) -> Any:
        result = self._inner.search_claims_lexical(**arguments)
        self.last = search_account(result)
        return result


class _NoEmbedder:
    """Lexical-only sweep: any embedding request is a harness bug."""

    def embed(self, **_: object) -> Any:
        raise AssertionError("the lexical sweep must not embed")


def _eligible_current(
    engine: Engine, doc_ids: tuple[UUID, ...] | None, tag: str | None = None
) -> int:
    """Current-scope eligible chunks of a stratum (the filter's real size)."""
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM memory_v1.chunks_live AS c"
                    " WHERE c.deployment_id = :d"
                    " AND (CAST(:docs AS uuid[]) IS NULL"
                    "      OR c.doc_id = ANY(CAST(:docs AS uuid[])))"
                    " AND (CAST(:tag AS text) IS NULL OR EXISTS ("
                    "      SELECT 1 FROM document_tags AS assigned"
                    "      JOIN tags ON tags.tag_id = assigned.tag_id"
                    "      WHERE assigned.doc_id = c.doc_id AND tags.name = :tag))"
                ),
                {
                    "d": d140.DEPLOYMENT,
                    "docs": None
                    if doc_ids is None
                    else [str(item) for item in doc_ids],
                    "tag": tag,
                },
            ).scalar_one()
        )


def _stratum(
    engine: Engine,
    queries: list[str],
    *,
    doc_ids: tuple[UUID, ...] | None = None,
    tag: str | None = None,
    grain: str = "chunks",
    time_scope: Any = None,
) -> dict[str, Any]:
    """Run the public ``search_chunks`` (or ``search_claims``) BM25 operation."""
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
    # The synthetic corpus carries no vectors, so its chunk_search rows have
    # no embedding generation; without this the generation-pinned hydration
    # drops every candidate and no public result ever hydrates (review r2
    # P2-4). Lexical search does not depend on the generation.
    surface._policy_generation = None  # type: ignore[assignment]  # noqa: SLF001
    surface._embedder_generation = None  # type: ignore[assignment]  # noqa: SLF001
    documents = (
        DocumentSearchFilters(tags=DocumentTagFilter(all=(tag,)))
        if tag is not None
        else None
        if doc_ids is None
        else DocumentSearchFilters(doc_ids=doc_ids)
    )
    search = surface.search_claims if grain == "claims" else surface.search_chunks
    latencies: list[float] = []
    outcomes: dict[str, int] = {}
    terminations: dict[str, int] = {}
    fetched: list[int] = []
    rounds: list[int] = []
    per_query: list[dict[str, Any]] = []
    for query in queries:
        started = time.perf_counter()
        envelope = search(
            deployment_id=d140.DEPLOYMENT,
            query=query,
            k=K,
            channel="bm25",
            documents=documents,
            time=time_scope,
        )
        latencies.append(time.perf_counter() - started)
        returned = len(envelope.evidence if grain == "claims" else envelope.chunks)
        if returned >= K:
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
        per_query.append(
            {
                "query": query,
                "ms": round(1000 * latencies[-1], 1),
                "returned": returned,
                "outcome": outcome,
                "fetched": index.last.fetched if index.last else None,
                "rounds": index.last.fetch_rounds if index.last else None,
                "termination": index.last.termination.value if index.last else None,
            }
        )
    truncated = sum(
        count
        for name, count in terminations.items()
        if name not in {"k_filled", "exhausted"}
    )
    return {
        **_percentiles(latencies),
        "queries": len(queries),
        "eligible_current_chunks": _eligible_current(engine, doc_ids, tag),
        "public_outcomes": outcomes,
        "returned_k_rate": outcomes.get("k_returned", 0) / len(queries),
        "terminations": terminations,
        "truncation_rate": truncated / len(queries),
        "mean_fetched": statistics.mean(fetched) if fetched else None,
        "max_fetched": max(fetched) if fetched else None,
        "mean_fetch_rounds": statistics.mean(rounds) if rounds else None,
        "per_query": per_query,
    }


def _nomination_plan(engine: Engine, query: str) -> dict[str, Any]:
    """The production nomination statement's plan, with its real LIMIT.

    Explained with the production cursor's own LIMIT (the work budget) and
    the loop's planner controls, without ANALYZE: executing it to that LIMIT
    would measure more rows than any cursor consumes. The rows a search
    actually consumed are its account (``fetched``, ``rounds``), kept per
    query in the stratum results (review r2 P2-4).
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
        for name, value in p1_ranked._PLANNER_CONTROLS:  # noqa: SLF001
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": name, "value": value},
            )
        started = time.perf_counter()
        plan = connection.execute(
            text(f"EXPLAIN (FORMAT JSON) {nomination.sql} LIMIT :nomination_limit"),
            {**nomination.parameters, "nomination_limit": p1_ranked.work_budget(K)},
        ).scalar_one()
        round_trip_ms = 1000 * (time.perf_counter() - started)
        connection.rollback()
    document = (json.loads(plan) if isinstance(plan, str) else plan)[0]
    nodes = [document["Plan"], *document["Plan"].get("Plans", ())]
    return {
        "query": query,
        "limit": p1_ranked.work_budget(K),
        "explain_round_trip_ms": round_trip_ms,
        "index": next(
            (node.get("Index Name") for node in nodes if node.get("Index Name")), None
        ),
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


def _hashed_lineages(engine: Engine) -> list[UUID]:
    """Every lineage in a fixed hashed order (the filter strata take prefixes)."""
    with engine.connect() as connection:
        return [
            UUID(str(row[0]))
            for row in connection.execute(
                text(
                    "SELECT doc_id FROM documents WHERE deployment_id = :d"
                    " ORDER BY md5(doc_id::text)"
                ),
                {"d": d140.DEPLOYMENT},
            )
        ]


def measure(
    engine: Engine, *, queries: int, lineages: int, claims: bool = False
) -> dict[str, Any]:
    """Every stratum through the public operation, plus corpus/query identity."""
    PostgresP1Index(engine=engine, embedding_model=d140.MODEL).configure_channels(
        deployment_id=d140.DEPLOYMENT
    )
    sample = _queries(queries)
    ordered = _hashed_lineages(engine)
    strata: dict[str, dict[str, Any]] = {
        "current": {},
        "history": {"time_scope": HistoryReadTime()},
        "docs_0.1pct": {"doc_ids": tuple(ordered[: max(1, lineages // 1000)])},
        "docs_1pct": {"doc_ids": tuple(ordered[: max(1, lineages // 100)])},
        "docs_10pct": {"doc_ids": tuple(ordered[: max(1, lineages // 10)])},
        # 10 current chunks per lineage: both sides of the 5,000-row tier
        "docs_4990_rows": {"doc_ids": tuple(ordered[:499])},
        "docs_5010_rows": {"doc_ids": tuple(ordered[:501])},
        "tags_1pct": {"tag": "bench:1pct"},
        "tags_90pct": {"tag": "bench:90pct"},
    }
    if claims:
        strata.update(
            {
                "claims_current": {"grain": "claims"},
                "claims_tags_1pct": {"grain": "claims", "tag": "bench:1pct"},
                "claims_tags_90pct": {"grain": "claims", "tag": "bench:90pct"},
            }
        )
    report: dict[str, Any] = {
        "query_sha256": hashlib.sha256("\n".join(sample).encode()).hexdigest(),
        "corpus_md5": _corpus_hash(engine),
        "strata": {},
    }
    for name, arguments in strata.items():
        report["strata"][name] = _stratum(engine, sample, **arguments)
    report["nomination_plans"] = [
        _nomination_plan(engine, query) for query in sample[:5]
    ]
    report["tag_eligibility_plans"] = {
        tag: _tag_eligibility_plan(engine, tag=tag) for tag in TAG_SHARES
    }
    return report


def _tag_eligibility_plan(engine: Engine, *, tag: str) -> dict[str, Any]:
    """``EXPLAIN (ANALYZE, BUFFERS)`` of the D156 tag filter's eligible-set probe.

    The probe is the chunk path's enumeration (``_enumerate_eligible``): the
    live versions whose lineage carries the tag, each one's chunks read
    through the scope join, up to ``FILTER_PUSHDOWN_LIMIT + 1`` rows. Reported:
    execution time, rows, shared buffers and whether the plan starts from the
    tag index.
    """
    from rememberstack.spine.tags import resolve_filter_tags_on

    index = PostgresP1Index(engine=engine, embedding_model=d140.MODEL)
    with engine.connect() as connection:
        documents = resolve_filter_tags_on(
            connection=connection,
            deployment_id=d140.DEPLOYMENT,
            filters=DocumentSearchFilters(tags=DocumentTagFilter(all=(tag,))),
        )
        query = index._testimony_query(  # noqa: SLF001 -- the real statement
            target="chunks",
            channel="bm25",
            deployment_id=str(d140.DEPLOYMENT),
            current_only=True,
            equality_filters=None,
            entity_ids=(),
            documents=documents,
            time=None,
            query_text="w1",
        )
        assert query.eligible_sql is not None
        statement = (
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)"
            f" {query.eligible_sql} LIMIT :tier_limit"
        )
        plan = connection.execute(
            text(statement),
            {**query.parameters, "tier_limit": p1_ranked.FILTER_PUSHDOWN_LIMIT + 1},
        ).scalar_one()
        connection.rollback()
    document = (json.loads(plan) if isinstance(plan, str) else plan)[0]

    def walk(node: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            node,
            *(item for child in node.get("Plans", ()) for item in walk(child)),
        ]

    nodes = walk(document["Plan"])
    return {
        "execution_ms": document["Execution Time"],
        "planning_ms": document["Planning Time"],
        "rows": document["Plan"]["Actual Rows"],
        "shared_hit_blocks": document["Plan"].get("Shared Hit Blocks"),
        "shared_read_blocks": document["Plan"].get("Shared Read Blocks"),
        "indexes": sorted(
            {node["Index Name"] for node in nodes if node.get("Index Name")}
        ),
        "plan": document["Plan"],
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
    parser.add_argument(
        "--claims",
        action="store_true",
        help="also seed one claim per served chunk and measure claim strata",
    )
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
            "latency": measure(
                engine, queries=args.queries, lineages=lineages, claims=args.claims
            ),
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
        _add_tags(engine, ordered=_hashed_lineages(engine))
        claims = _seed_claims(engine) if args.claims else 0
        report["sizes"][str(size)] = {
            "seed": timings,
            "duplicated_chunks": duplicated,
            "claims": claims,
            "latency": measure(
                engine, queries=args.queries, lineages=lineages, claims=args.claims
            ),
        }
        print(json.dumps({size: report["sizes"][str(size)]}, indent=2), flush=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)


if __name__ == "__main__":
    main()
