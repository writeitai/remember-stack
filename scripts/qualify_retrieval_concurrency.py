"""Run eight native reads on isolated retained data with a frozen stored vector.

No ingestion or provider network call is allowed. All readers share the native
bounded pool; each operation owns its repeatable-read snapshot and deadline.
The receipt records observed completion and result parity, not a throughput SLA.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import UTC
import hashlib
import json
from pathlib import Path
from threading import Barrier
from time import monotonic
from typing import cast
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import SecretStr
from scripts.diagnose_retrieval_sql import FrozenProvider
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy import URL
from sqlalchemy.pool import QueuePool

from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.adapters.postgres_p1 import PostgresP1Index
from rememberstack.surfaces.query_engine import QueryEngine


class Database(BaseModel):
    """Explicit private target; credential values never enter the receipt."""

    host: str
    port: int
    username: str
    password: SecretStr
    database: str

    def url(self) -> URL:
        """Construct the in-process connection URL without shell interpolation."""
        return URL.create(
            "postgresql+psycopg",
            username=self.username,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.database,
        )


class Case(BaseModel):
    """An unchanged retained-store diagnostic case and normal retrieval sizes."""

    name: str
    operation: Literal["facts_context", "claims_and_sources_context"]
    deployment_id: UUID
    query: str
    entity_ids: tuple[UUID, ...]
    k: int = 50
    candidate_k: int = 200


def qualify(*, private: Path, cases: Path, output: Path) -> None:
    """Record one- and eight-reader native outputs under the original 120s bound."""
    if output.exists():
        raise ValueError("refusing to overwrite an earlier qualification")
    db = Database.model_validate(json.loads(private.read_text())["databases"]["target"])
    engine = create_engine(
        db.url(),
        pool_size=8,
        max_overflow=0,
        hide_parameters=True,
        connect_args={"application_name": "d153-eight-reader"},
    )
    output.mkdir(parents=True)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT observation_id,embedding::text,embedding_model FROM observations WHERE embedding IS NOT NULL AND vector_norm(embedding)>0 ORDER BY observation_id LIMIT 1"
            )
        ).one()
    vector = tuple(json.loads(row[1]))
    evaluated_at = datetime.now(UTC)
    pool = BoundedPostgresReadPool(
        engine=engine, max_concurrency=8, pool_wait_seconds=5
    )
    index = PostgresP1Index(engine=engine, embedding_model=row[2], read_pool=pool)
    receipt: dict[str, object] = {
        "observed_at": evaluated_at.isoformat(),
        "operation_seconds": 120,
        "max_concurrency": 8,
        "provider_calls": 0,
        "embedding_mode": "stored observation vector; not a question embedding",
        "vector_source": str(row[0]),
        "vector_sha256": hashlib.sha256(row[1].encode()).hexdigest(),
    }

    def read(*, case: Case, number: int, barrier: Barrier | None) -> dict[str, object]:
        """Run one real operation and preserve the complete envelope independently."""
        provider = FrozenProvider(vector=vector)
        query = QueryEngine(
            engine=engine,
            search_index=index,
            model_provider=provider,
            embedding_model=row[2],
            fact_read_pool=pool,
            database_budget_seconds=120,
        )
        if barrier is not None:
            barrier.wait(timeout=30)
        started = monotonic()
        result = getattr(query, case.operation)(
            deployment_id=case.deployment_id,
            query=case.query,
            entity_ids=case.entity_ids,
            k=case.k,
            candidate_k=case.candidate_k,
            evaluated_at=evaluated_at,
        )
        elapsed = monotonic() - started
        payload = result.model_dump(mode="json")
        (output / f"{case.name}-{number}.json").write_text(
            json.dumps(payload, indent=2)
        )
        # Pin the operation instant and compare all substantive returned rows;
        # per-request timing/freshness metadata is independently preserved above.
        content = {
            key: payload[key]
            for key in (
                "facts",
                "evidence",
                "chunks",
                "sources",
                "negative",
                "truncation",
            )
        }
        return {
            "reader": number,
            "seconds": elapsed,
            "sha256": hashlib.sha256(
                json.dumps(content, sort_keys=True).encode()
            ).hexdigest(),
            "negative": content["negative"],
            "truncation": content["truncation"],
        }

    try:
        for raw in json.loads(cases.read_text()):
            case = Case.model_validate(raw)
            baseline = read(case=case, number=0, barrier=None)
            barrier = Barrier(8)
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [
                    executor.submit(read, case=case, number=number, barrier=barrier)
                    for number in range(1, 9)
                ]
                readers = [future.result(timeout=180) for future in futures]
            all_equal = all(
                reader["sha256"] == baseline["sha256"] for reader in readers
            )
            receipt[case.name] = {
                "baseline": baseline,
                "readers": readers,
                "all_equal": all_equal,
            }
            (output / "receipt.json").write_text(json.dumps(receipt, indent=2))
            if not all_equal or baseline["negative"] is not None:
                raise RuntimeError(
                    "known nonempty qualification case has a boundary or unequal results"
                )
            print(
                json.dumps(
                    {
                        "case": case.name,
                        "baseline_seconds": baseline["seconds"],
                        "eight_seconds": [r["seconds"] for r in readers],
                        "all_equal": all_equal,
                    }
                ),
                flush=True,
            )
        receipt["checked_out_connections_after_reads"] = cast(
            QueuePool, engine.pool
        ).checkedout()
        with engine.connect() as connection:
            receipt["open_snapshot_transactions_after_reads"] = connection.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND application_name='d153-eight-reader' AND xact_start IS NOT NULL"
                )
            ).scalar_one()
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2))
        if (
            receipt["checked_out_connections_after_reads"] != 0
            or receipt["open_snapshot_transactions_after_reads"] != 0
        ):
            raise RuntimeError(
                "qualification left an admitted connection or snapshot open"
            )
    finally:
        engine.dispose()


def main() -> None:
    """Require explicit isolated private settings, cases and immutable output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    qualify(private=args.private, cases=args.cases, output=args.output)


if __name__ == "__main__":
    main()
