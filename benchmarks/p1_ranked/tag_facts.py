"""D156 §4.6 qualification for fact search under a tag filter.

Seeds the D140 benchmark corpus (``--lineages`` lineages, two versions of two
chunks each), D134 metadata, the sweep's qualification tags (1% and 90% of
lineages), one current claim per served chunk, and one open relation per
lineage supported by its first claim, with live mentions of both endpoints
and a random unit vector. Then it runs ``PostgresP1Index.search_facts_scored``
(k = 20, relations) for ``--queries`` random query vectors per stratum:
unfiltered, ``tags all [bench:1pct]`` and ``tags all [bench:90pct]``, and
reports p50/p95 latency, terminations and returned counts.

No public operation searches facts with a document filter yet (the context
operations take it in D156 step 3); this measures the P1 statement they use.

    REMEMBERSTACK_DATABASE_URL=... uv run python -m benchmarks.p1_ranked.tag_facts \\
        --lineages 60000 --queries 40 --out tag_facts.json

Synthetic data; timings and completeness only, not a correctness test.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from benchmarks.d140_scope import run as d140
from benchmarks.p1_ranked import sweep
from remember.models import DocumentSearchFilters
from remember.models import DocumentTagFilter
from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.adapters.postgres_p1 import FACT_INPUT_POLICY
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.ports.p1_index import search_account
from rememberstack.spine.settings import load_database_settings
from rememberstack.spine.tags import resolve_filter_tags_on

K = 20


def _seed_facts(engine: Engine) -> int:
    """One relation per lineage, supported by its served first chunk's claim."""
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text(
                "CREATE TEMP TABLE bench_facts ON COMMIT DROP AS"
                " SELECT claim.claim_id, claim.doc_id, claim.chunk_id,"
                " md5('rel' || claim.claim_id::text)::uuid AS relation_id,"
                " md5('subj' || claim.claim_id::text)::uuid AS subject_id,"
                " md5('obj' || claim.claim_id::text)::uuid AS object_id"
                " FROM claims AS claim JOIN chunks AS chunk"
                "   ON chunk.chunk_id = claim.chunk_id AND chunk.ordinal = 0"
                " WHERE claim.deployment_id = :d"
            ),
            {"d": d140.DEPLOYMENT},
        )
        for statement in (
            "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
            " normalized_name) SELECT entity_id, :d, 'Bench ' || entity_id,"
            " 'bench ' || entity_id FROM (SELECT subject_id AS entity_id FROM"
            " bench_facts UNION ALL SELECT object_id FROM bench_facts) AS e",
            "INSERT INTO mentions (mention_id, deployment_id, surface_form,"
            " normalized_lemma, chunk_id, claim_id, doc_id, created_at)"
            " SELECT md5('m' || entity_id::text)::uuid, :d, 'bench', 'bench',"
            " chunk_id, claim_id, doc_id, now() FROM (SELECT subject_id AS"
            " entity_id, chunk_id, claim_id, doc_id FROM bench_facts UNION ALL"
            " SELECT object_id, chunk_id, claim_id, doc_id FROM bench_facts) AS e",
            "INSERT INTO resolution_decisions (decision_id, deployment_id,"
            " mention_id, entity_id, method, confidence, resolver_version,"
            " decided_at) SELECT md5('r' || entity_id::text)::uuid, :d,"
            " md5('m' || entity_id::text)::uuid, entity_id, 'T0', 1.0, 'bench',"
            " now() FROM (SELECT subject_id AS entity_id FROM bench_facts UNION"
            " ALL SELECT object_id FROM bench_facts) AS e",
            "INSERT INTO relations (relation_id, deployment_id, subject_entity_id,"
            " predicate, object_entity_id, normalizer_version, fact_label,"
            " ingested_at, valid_from, valid_precision, window_claim_ids,"
            " embedding_model, embedding_input_policy_version, embedding_text_hash,"
            " embedding)"
            " SELECT relation_id, :d, subject_id, 'works_for', object_id, 'bench',"
            " 'bench fact ' || relation_id, '2001-01-01', '2001-01-01', 'open',"
            " ARRAY[claim_id], :model, :policy, 'bench',"
            " (SELECT CAST(array_agg(random() - 0.5) AS vector)"
            "  FROM generate_series(1, :dimensions) WHERE relation_id IS NOT NULL)"
            " FROM bench_facts",
            "INSERT INTO relation_evidence (deployment_id, relation_id, claim_id,"
            " doc_id, stance, normalizer_version) SELECT :d, relation_id,"
            " claim_id, doc_id, 'supports', 'bench' FROM bench_facts",
        ):
            connection.execute(
                text(statement),
                {
                    "d": d140.DEPLOYMENT,
                    "model": d140.MODEL,
                    "policy": FACT_INPUT_POLICY,
                    "dimensions": P1_VECTOR_DIMENSIONS,
                },
            )
        created = connection.execute(text("SELECT count(*) FROM bench_facts"))
        count = int(created.scalar_one())
    with engine.begin() as connection:
        for table in (
            "entities",
            "mentions",
            "resolution_decisions",
            "relations",
            "relation_evidence",
        ):
            connection.execute(text(f"ANALYZE {table}"))
    return count


def _eligible_facts(engine: Engine, tag: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(DISTINCT evidence.relation_id)"
                    " FROM relation_evidence AS evidence"
                    " JOIN document_tags AS assigned ON assigned.doc_id = evidence.doc_id"
                    " JOIN tags ON tags.tag_id = assigned.tag_id"
                    " WHERE evidence.deployment_id = :d AND tags.name = :tag"
                ),
                {"d": d140.DEPLOYMENT, "tag": tag},
            ).scalar_one()
        )


def measure(engine: Engine, *, queries: int) -> dict[str, Any]:
    """Every stratum through the real P1 fact statement."""
    pool = BoundedPostgresReadPool(
        engine=engine, max_concurrency=2, pool_wait_seconds=30.0
    )
    index = PostgresP1Index(engine=engine, embedding_model=d140.MODEL, read_pool=pool)
    index.configure_channels(deployment_id=d140.DEPLOYMENT)
    rng = random.Random(156)
    vectors = [
        tuple(rng.random() - 0.5 for _ in range(P1_VECTOR_DIMENSIONS))
        for _ in range(queries)
    ]
    report: dict[str, Any] = {"strata": {}}
    for name, tag in (
        ("unfiltered", None),
        ("tags_1pct", "bench:1pct"),
        ("tags_90pct", "bench:90pct"),
    ):
        documents = None
        if tag is not None:
            with engine.connect() as connection:
                documents = resolve_filter_tags_on(
                    connection=connection,
                    deployment_id=d140.DEPLOYMENT,
                    filters=DocumentSearchFilters(tags=DocumentTagFilter(all=(tag,))),
                )
        latencies: list[float] = []
        terminations: dict[str, int] = {}
        returned: list[int] = []
        for vector in vectors:
            started = time.perf_counter()
            result = index.search_facts_scored(
                deployment_id=str(d140.DEPLOYMENT),
                vector=vector,
                k=K,
                kind="relation",
                documents=documents,
            )
            latencies.append(time.perf_counter() - started)
            termination = search_account(result).termination.value
            terminations[termination] = terminations.get(termination, 0) + 1
            returned.append(len(result))
        ordered = sorted(latencies)
        report["strata"][name] = {
            "p50_ms": 1000 * statistics.median(ordered),
            "p95_ms": 1000 * ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
            "max_ms": 1000 * ordered[-1],
            "terminations": terminations,
            "returned_k_rate": sum(1 for count in returned if count >= K)
            / len(returned),
            "eligible_facts": None if tag is None else _eligible_facts(engine, tag),
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--lineages", type=int, default=60_000)
    parser.add_argument("--queries", type=int, default=40)
    parser.add_argument("--maintenance-work-mem", default="512MB")
    parser.add_argument("--out", default="tag_facts.json")
    args = parser.parse_args()
    engine = create_engine(
        load_database_settings().database_url.get_secret_value(), pool_size=2
    )
    d140.MAINTENANCE_WORK_MEM[0] = args.maintenance_work_mem
    sweep._reset(engine)  # noqa: SLF001 -- the same isolated benchmark database
    timings = d140.seed(
        engine=engine,
        lineages=args.lineages,
        versions=2,
        chunks=2,
        periodised=0.0,
        semantic_chunks=0,
    )
    sweep._add_metadata(engine)  # noqa: SLF001
    sweep._add_tags(engine, ordered=sweep._hashed_lineages(engine))  # noqa: SLF001
    claims = sweep._seed_claims(engine)  # noqa: SLF001
    started = time.monotonic()
    facts = _seed_facts(engine)
    timings["facts_seconds"] = time.monotonic() - started
    report = {
        "arguments": vars(args),
        "seed": timings,
        "claims": claims,
        "facts": facts,
        **measure(engine, queries=args.queries),
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
