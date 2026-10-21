"""D142 HNSW recall gate (WP-RS.9): index nomination against exact scoring.

For sampled query vectors (stored chunk vectors with a small perturbation, so
neighbours are meaningful), compares the semantic chunk search (HNSW
nomination + confirmation) with an exact top-k over **the same eligible
set**: the oracle reuses the adapter's own eligibility relation (D140
selection at the pinned instant, model/policy attestation, document
metadata), and both run in one read-only snapshot. The small-set exact tier
is disabled for the run so every stratum exercises the HNSW loop; the
report records which path each search took. Starting thresholds (design
§9): mean recall@10 >= 0.95, no stratum below 0.90. A required stratum with
no measurable query makes the gate inconclusive, never a pass.

Run against a deployment whose chunks carry vectors (a copy of a real one, or
the D140 benchmark corpus seeded with ``--semantic-chunks``):

    REMEMBERSTACK_DATABASE_URL=... uv run python -m benchmarks.p1_ranked.recall \\
        --deployment <uuid> --model <embedding model> --samples 200 --out recall.json

Exit status 0 pass, 1 fail, 2 inconclusive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import p1_ranked
from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.model.client import DocumentSearchFilters
from rememberstack.ports.p1_index import search_account
from rememberstack.spine.settings import load_database_settings

MEAN_THRESHOLD = 0.95
STRATUM_THRESHOLD = 0.90
REQUIRED_STRATA = ("unfiltered", "document_filter")


def _sample_vectors(
    pool: BoundedPostgresReadPool, *, deployment: UUID, samples: int, seed: int
) -> tuple[list[tuple[float, ...]], list[str]]:
    rng = random.Random(seed)
    with pool.connect(deadline=None) as connection:
        rows = connection.execute(
            text(
                "SELECT chunk_id::text, embedding::text FROM chunk_search"
                " WHERE deployment_id = :d AND embedding IS NOT NULL"
                " ORDER BY md5(chunk_id::text || :seed) LIMIT :n"
            ),
            {"d": deployment, "seed": str(seed), "n": samples},
        ).all()
    vectors, sources = [], []
    for chunk_id, literal in rows:
        values = [float(item) for item in literal.strip("[]").split(",")]
        noisy = [value + rng.gauss(0, 0.01) for value in values]
        norm = sum(value * value for value in noisy) ** 0.5
        vectors.append(tuple(value / norm for value in noisy))
        sources.append(str(chunk_id))
    return vectors, sources


def _exact(
    index: PostgresP1Index,
    pool: BoundedPostgresReadPool,
    *,
    deployment: UUID,
    vector: tuple[float, ...],
    k: int,
    documents: DocumentSearchFilters | None,
) -> list[str]:
    """Exact top-k over the adapter's own eligibility relation."""
    query = index._testimony_query(  # noqa: SLF001 -- the oracle must share it
        target="chunks",
        channel="semantic",
        deployment_id=str(deployment),
        current_only=False,
        equality_filters=None,
        entity_ids=(),
        documents=documents,
        time=None,
        vector=vector,
        model=index._embedding_model,  # noqa: SLF001
        policy=index._chunk_input_policy,  # noqa: SLF001
    )
    with pool.connect(deadline=None) as connection:
        connection.execute(text("SELECT set_config('enable_indexscan', 'off', true)"))
        rows = connection.execute(
            text(
                f"SELECT indexed.chunk_id::text {query.relation} {query.where}"
                " ORDER BY indexed.embedding <=> CAST(:query_vector AS vector),"
                " indexed.chunk_id LIMIT :k"
            ),
            {**query.parameters, "k": k},
        ).scalars()
        result = list(rows)
        connection.execute(text("SELECT set_config('enable_indexscan', 'on', true)"))
    return result


def run(
    engine: Engine, *, deployment: UUID, model: str, samples: int, k: int, seed: int
) -> dict[str, Any]:
    """Measure recall per stratum inside one snapshot; decide the gate."""
    pool = BoundedPostgresReadPool(
        engine=engine, max_concurrency=1, pool_wait_seconds=30.0
    )
    index = PostgresP1Index(engine=engine, embedding_model=model, read_pool=pool)
    threshold = p1_ranked.EXACT_TIER_THRESHOLD
    p1_ranked.EXACT_TIER_THRESHOLD = 0  # force the HNSW loop in every stratum
    report: dict[str, Any] = {"k": k, "seed": seed, "strata": {}}
    try:
        with pool.snapshot(deadline=monotonic() + 3600.0):
            vectors, sources = _sample_vectors(
                pool, deployment=deployment, samples=samples, seed=seed
            )
            report["samples"] = len(vectors)
            report["sample_sha256"] = hashlib.sha256(
                "\n".join(sources).encode()
            ).hexdigest()
            with pool.connect(deadline=None) as connection:
                doc_ids = tuple(
                    UUID(str(row[0]))
                    for row in connection.execute(
                        text(
                            "SELECT doc_id FROM documents WHERE deployment_id = :d"
                            " ORDER BY md5(doc_id::text) LIMIT 50"
                        ),
                        {"d": deployment},
                    )
                )
            strata: dict[str, DocumentSearchFilters | None] = {
                "unfiltered": None,
                "document_filter": DocumentSearchFilters(doc_ids=doc_ids)
                if doc_ids
                else None,
            }
            for name, documents in strata.items():
                if name != "unfiltered" and documents is None:
                    report["strata"][name] = {"queries": 0, "mean_recall": None}
                    continue
                recalls: list[float] = []
                paths: dict[str, int] = {}
                for vector in vectors:
                    exact = _exact(
                        index,
                        pool,
                        deployment=deployment,
                        vector=vector,
                        k=k,
                        documents=documents,
                    )
                    if not exact:
                        continue
                    found = index.search_chunks_scored(
                        deployment_id=str(deployment),
                        vector=vector,
                        k=k,
                        documents=documents,
                    )
                    account = search_account(found)
                    path = "hnsw_loop" if account.fetch_rounds else "exact_tier"
                    paths[path] = paths.get(path, 0) + 1
                    got = {item.item_id for item in found}
                    recalls.append(len(got & set(exact)) / len(exact))
                report["strata"][name] = {
                    "queries": len(recalls),
                    "paths": paths,
                    "mean_recall": statistics.mean(recalls) if recalls else None,
                    "min_recall": min(recalls) if recalls else None,
                }
    finally:
        p1_ranked.EXACT_TIER_THRESHOLD = threshold
    means = [report["strata"][name]["mean_recall"] for name in REQUIRED_STRATA]
    if any(value is None for value in means):
        report["decision"] = "inconclusive"
    elif statistics.mean(means) >= MEAN_THRESHOLD and all(
        value >= STRATUM_THRESHOLD for value in means
    ):
        report["decision"] = "pass"
    else:
        report["decision"] = "fail"
    report["mean_recall"] = (
        None if None in means else statistics.mean(means)  # type: ignore[arg-type]
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--deployment", type=UUID, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=142)
    parser.add_argument("--out", default="p1_recall.json")
    args = parser.parse_args()
    engine = create_engine(load_database_settings().database_url.get_secret_value())
    report = run(
        engine,
        deployment=args.deployment,
        model=args.model,
        samples=args.samples,
        k=args.k,
        seed=args.seed,
    )
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))
    sys.exit({"pass": 0, "fail": 1, "inconclusive": 2}[report["decision"]])


if __name__ == "__main__":
    main()
