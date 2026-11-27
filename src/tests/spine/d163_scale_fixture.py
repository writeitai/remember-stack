"""Expand a disposable D163 test corpus, keeping its candidate evidence fixed."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from benchmarks.d163_qualification import Case
from benchmarks.d163_qualification import KINDS
from benchmarks.d163_qualification import TARGET_HEAD
import psycopg
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Connection

from rememberstack.profiles.selfhost import _provision_query_role_password
from rememberstack.spine.settings import load_database_settings


def _columns(connection: Connection, table: str) -> list[str]:
    return list(
        connection.execute(
            text(
                "SELECT attname FROM pg_attribute WHERE attrelid=CAST(:table AS regclass) "
                "AND attnum>0 AND NOT attisdropped AND attgenerated='' ORDER BY attnum"
            ),
            {"table": table},
        ).scalars()
    )


def _expand_facts(connection: Connection, *, kind: str, size: int) -> None:
    table = f"{kind}s"
    identifier = f"{kind}_id"
    count = connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
    if count >= size:
        return
    labels = "obs_label" if kind == "observation" else "fact_label"
    # Use an eligible prototype; clones keep its evidence and distinct keys.
    seed = connection.execute(
        text(f"SELECT {identifier} FROM {table} WHERE {labels}='multi_source'")
    ).scalar_one()
    columns = _columns(connection, table)
    projection = [
        "md5(:prefix || n)::uuid" if field == identifier else f"seed.{field}"
        for field in columns
    ]
    bindings = {
        "seed": seed,
        "prefix": f"d163-test-{kind}-",
        "first": count + 1,
        "last": size,
    }
    connection.execute(
        text(
            f"INSERT INTO {table} ({', '.join(columns)}) SELECT {', '.join(projection)} "
            f"FROM {table} seed CROSS JOIN generate_series(CAST(:first AS integer), CAST(:last AS integer)) AS series(n) "
            f"WHERE seed.{identifier}=:seed"
        ),
        bindings,
    )
    evidence_table = f"{kind}_evidence"
    columns = _columns(connection, evidence_table)
    projection = [
        "md5(:prefix || n)::uuid" if field == identifier else f"seed.{field}"
        for field in columns
    ]
    connection.execute(
        text(
            f"INSERT INTO {evidence_table} ({', '.join(columns)}) "
            f"SELECT {', '.join(projection)} FROM {evidence_table} seed "
            f"CROSS JOIN generate_series(CAST(:first AS integer), CAST(:last AS integer)) AS series(n) WHERE seed.{identifier}=:seed"
        ),
        bindings,
    )


def _expand_chunks(connection: Connection, *, size: int) -> None:
    """Grow one retained document without changing any candidate's evidence."""
    count = connection.exec_driver_sql("SELECT count(*) FROM chunks").scalar_one()
    if count >= size:
        return
    seed = connection.exec_driver_sql(
        "SELECT c.chunk_id FROM claims c WHERE c.claim_text='old rule' LIMIT 1"
    ).scalar_one()
    columns = _columns(connection, "chunks")
    projection = {
        "chunk_id": "md5('d163-noise-chunk-' || n)::uuid",
        "ordinal": "100000 + n",
    }
    connection.execute(
        text(
            f"INSERT INTO chunks ({', '.join(columns)}) "
            f"SELECT {', '.join(projection.get(col, f'seed.{col}') for col in columns)} "
            "FROM chunks seed CROSS JOIN generate_series(CAST(:first AS integer), CAST(:last AS integer)) AS series(n) "
            "WHERE seed.chunk_id=:seed"
        ),
        {"seed": seed, "first": count + 1, "last": size},
    )


def _expand_leaves(connection: Connection, *, leaves: int) -> None:
    for table in ("chunks", "chunk_claims"):
        count = connection.execute(
            text(
                "SELECT count(*) FROM pg_inherits WHERE inhparent=CAST(:name AS regclass)"
            ),
            {"name": table},
        ).scalar_one()
        for offset in range(leaves - count):
            year, month = 1900 + offset // 12, 1 + offset % 12
            end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
            connection.exec_driver_sql(
                f"CREATE TABLE {table}_d163_{year}_{month:02d} PARTITION OF {table} "
                f"FOR VALUES FROM ('{year}-{month:02d}-01') TO ('{end_year}-{end_month:02d}-01')"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--chunks", type=int, required=True)
    parser.add_argument("--leaves", type=int, default=10)
    parser.add_argument("--observations", type=int, default=2625)
    parser.add_argument("--relations", type=int, default=535)
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    case = Case.model_validate_json(args.case.read_text())
    engine = create_engine(load_database_settings().sqlalchemy_url())
    assert engine.url.database is not None and engine.url.database.startswith("d163_")
    with engine.begin() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            == TARGET_HEAD
        )
        assert (
            connection.execute(
                text("SELECT slug FROM deployments WHERE deployment_id=:deployment"),
                {"deployment": case.deployment_id},
            ).scalar_one()
            == "d163-oracle"
        )
        for kind in KINDS:
            _expand_facts(connection, kind=kind, size=getattr(args, f"{kind}s"))
        _expand_leaves(connection, leaves=args.leaves)
        _expand_chunks(connection, size=args.chunks)
        if args.analyze:
            connection.exec_driver_sql("ANALYZE")
        print(
            connection.exec_driver_sql(
                "SELECT (SELECT count(*) FROM chunks), (SELECT count(*) FROM relations), "
                "(SELECT count(*) FROM observations)"
            ).one()
        )
    with engine.connect() as connection:
        raw = cast("psycopg.Connection", connection.connection.dbapi_connection)
        _provision_query_role_password(connection=raw, engine=engine)
        raw.commit()
    engine.dispose()


if __name__ == "__main__":
    main()
