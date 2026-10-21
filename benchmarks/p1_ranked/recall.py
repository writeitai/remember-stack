"""D142 HNSW recall gate (WP-RS.9): index nomination against exact scoring.

For sampled query vectors (taken from stored chunk vectors with a small
perturbation, so neighbours are meaningful), compares the semantic chunk
search (HNSW nomination + confirmation) with an exact eligible top-k computed
by a full ordered scan. Reports recall@k per stratum and checks the design's
starting thresholds (mean recall@10 >= 0.95, no stratum below 0.90).

Run against a deployment whose chunks carry vectors (a copy of a real one, or
the D140 benchmark corpus seeded with ``--semantic-chunks``):

    REMEMBERSTACK_DATABASE_URL=... uv run python -m benchmarks.p1_ranked.recall \\
        --deployment <uuid> --model <embedding model> --samples 200 --out recall.json

Exit status 0 when the gate passes, 1 when it fails.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import PostgresP1Index
from rememberstack.model.client import DocumentSearchFilters
from rememberstack.spine.settings import load_database_settings

MEAN_THRESHOLD = 0.95
STRATUM_THRESHOLD = 0.90


def _sample_vectors(
    engine: Engine, *, deployment: UUID, samples: int, seed: int
) -> list[tuple[float, ...]]:
    rng = random.Random(seed)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT embedding::text FROM chunk_search"
                " WHERE deployment_id = :d AND embedding IS NOT NULL"
                " ORDER BY md5(chunk_id::text || :seed) LIMIT :n"
            ),
            {"d": deployment, "seed": str(seed), "n": samples},
        ).scalars()
        vectors = []
        for row in rows:
            values = [float(item) for item in row.strip("[]").split(",")]
            noisy = [value + rng.gauss(0, 0.01) for value in values]
            norm = sum(value * value for value in noisy) ** 0.5
            vectors.append(tuple(value / norm for value in noisy))
    return vectors


def _exact(
    engine: Engine,
    *,
    deployment: UUID,
    vector: tuple[float, ...],
    k: int,
    doc_ids: tuple[UUID, ...] | None,
) -> list[str]:
    restriction = (
        " AND published.doc_id = ANY(CAST(:docs AS uuid[]))" if doc_ids else ""
    )
    with engine.connect() as connection:
        connection.execute(text("SET LOCAL enable_indexscan = off"))
        rows = connection.execute(
            text(
                "SELECT indexed.chunk_id::text FROM chunk_search AS indexed"
                " JOIN memory_v1.chunks_all_versions_live AS published"
                "   ON published.deployment_id = indexed.deployment_id"
                "  AND published.chunk_id = indexed.chunk_id"
                " WHERE indexed.deployment_id = :d AND indexed.embedding IS NOT NULL"
                f"{restriction}"
                " ORDER BY indexed.embedding <=> CAST(:v AS vector), indexed.chunk_id"
                " LIMIT :k"
            ),
            {
                "d": deployment,
                "v": "[" + ",".join(repr(value) for value in vector) + "]",
                "k": k,
                "docs": [str(item) for item in doc_ids or ()],
            },
        ).scalars()
        result = list(rows)
        connection.rollback()
    return result


def run(
    engine: Engine, *, deployment: UUID, model: str, samples: int, k: int, seed: int
) -> dict[str, Any]:
    index = PostgresP1Index(engine=engine, embedding_model=model)
    vectors = _sample_vectors(engine, deployment=deployment, samples=samples, seed=seed)
    with engine.connect() as connection:
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
    strata: dict[str, tuple[UUID, ...] | None] = {
        "unfiltered": None,
        "document_filter": doc_ids,
    }
    report: dict[str, Any] = {"k": k, "samples": len(vectors), "strata": {}}
    for name, docs in strata.items():
        recalls = []
        for vector in vectors:
            exact = _exact(
                engine, deployment=deployment, vector=vector, k=k, doc_ids=docs
            )
            if not exact:
                continue
            found = index.search_chunks_scored(
                deployment_id=str(deployment),
                vector=vector,
                k=k,
                documents=DocumentSearchFilters(doc_ids=docs) if docs else None,
            )
            got = {item.item_id for item in found}
            recalls.append(len(got & set(exact)) / len(exact))
        report["strata"][name] = {
            "queries": len(recalls),
            "mean_recall": statistics.mean(recalls) if recalls else None,
            "min_recall": min(recalls) if recalls else None,
        }
    means = [
        item["mean_recall"]
        for item in report["strata"].values()
        if item["mean_recall"] is not None
    ]
    mean_recall = statistics.mean(means) if means else None
    report["mean_recall"] = mean_recall
    report["passed"] = bool(
        mean_recall is not None
        and mean_recall >= MEAN_THRESHOLD
        and all(value >= STRATUM_THRESHOLD for value in means)
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
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
