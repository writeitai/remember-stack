"""D142 WP-RS.0: record how a real deployment plans its ranked statements.

Runs ``EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`` for the ``main``-shaped
one-statement BM25 chunk search (ranking joined with the authority views) and
for D142's join-free nomination statement, for a few query strings, and writes
the plans with their scan types, timings and buffer counts.

**Run it only against a COPY of a deployment** (for example a restored backup
of a LoCoMo database), never against a database a benchmark or a user is
using: ``EXPLAIN ANALYZE`` executes the statements.

    REMEMBERSTACK_DATABASE_URL=<copy> uv run python -m benchmarks.p1_ranked.explain_real \\
        --deployment <uuid> --query "kayaking trip" --query "job interview" \\
        --out explain_real.json
"""

from __future__ import annotations

import argparse
import json
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.spine.settings import load_database_settings

_ONE_STATEMENT = """
SELECT indexed.chunk_id::text AS item_id,
       -(indexed.search_text <@> to_bm25query(:q, 'ix_chunk_search_bm25'))::double precision
FROM chunk_search AS indexed
JOIN memory_v1.chunks_all_versions_live AS published
  ON published.deployment_id = indexed.deployment_id
 AND published.chunk_id = indexed.chunk_id
JOIN memory_v1.documents_live AS document
  ON document.deployment_id = published.deployment_id
 AND document.doc_id = published.doc_id
WHERE indexed.deployment_id = :d
ORDER BY indexed.search_text <@> to_bm25query(:q, 'ix_chunk_search_bm25'),
         indexed.chunk_id
LIMIT :k
"""

_NOMINATION = """
SELECT chunk_id::text AS item_id,
       -(search_text <@> to_bm25query(:q, 'ix_chunk_search_bm25'))::double precision
FROM chunk_search
WHERE deployment_id = :d
ORDER BY search_text <@> to_bm25query(:q, 'ix_chunk_search_bm25')
LIMIT :k
"""


def _scans(node: dict[str, Any]) -> list[str]:
    found = []
    if "Scan" in str(node.get("Node Type", "")) or node.get("Node Type") == "Sort":
        found.append(
            f"{node.get('Node Type')}:{node.get('Index Name') or node.get('Relation Name') or ''}"
        )
    for child in node.get("Plans", ()) or ():
        found.extend(_scans(child))
    return found


def explain(engine: Engine, *, sql: str, parameters: dict[str, Any]) -> dict[str, Any]:
    with engine.connect() as connection:
        plan = connection.execute(
            text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"), parameters
        ).scalar_one()
        connection.rollback()
    document = (json.loads(plan) if isinstance(plan, str) else plan)[0]
    root = document["Plan"]
    return {
        "execution_ms": document.get("Execution Time"),
        "planning_ms": document.get("Planning Time"),
        "scans": _scans(root),
        "shared_blocks": int(root.get("Shared Hit Blocks", 0))
        + int(root.get("Shared Read Blocks", 0)),
        "plan": document,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--deployment", type=UUID, required=True)
    parser.add_argument("--query", action="append", required=True)
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument("--out", default="explain_real.json")
    args = parser.parse_args()
    engine = create_engine(load_database_settings().database_url.get_secret_value())
    report: dict[str, Any] = {"deployment": str(args.deployment), "queries": {}}
    with engine.connect() as connection:
        report["chunks"] = connection.execute(
            text("SELECT count(*) FROM chunk_search WHERE deployment_id = :d"),
            {"d": args.deployment},
        ).scalar_one()
    for query in args.query:
        parameters = {"q": query, "d": args.deployment, "k": args.k}
        report["queries"][query] = {
            "main_one_statement": explain(
                engine, sql=_ONE_STATEMENT, parameters=parameters
            ),
            "d142_nomination": explain(engine, sql=_NOMINATION, parameters=parameters),
        }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    summary = {
        query: {
            name: {
                key: value[key] for key in ("execution_ms", "scans", "shared_blocks")
            }
            for name, value in entry.items()
        }
        for query, entry in report["queries"].items()
    }
    print(json.dumps({"chunks": report["chunks"], "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
