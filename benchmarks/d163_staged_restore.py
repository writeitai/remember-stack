"""Stage retained fact stores into a matched, disposable PostgreSQL 18 reference.

The reference must already have the source's Alembic revision, extensions,
partition bounds, and database-bound roles. Restore recreates its original
name after retaining the empty reference under a new name.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shlex
import subprocess
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.conninfo import make_conninfo

from rememberstack.spine.catalog_contract import EXPECTED_RANGE_PARENTS

_TABLES = """
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p') AND NOT c.relispartition
  AND NOT EXISTS (
    SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
      AND d.objid=c.oid AND d.deptype='e'
  )
ORDER BY c.relname
"""
_PARTMAN = """
SELECT c.relname FROM pg_class c JOIN pg_partitioned_table p ON p.partrelid=c.oid
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND p.partstrat='r' ORDER BY c.relname
"""
_PRIVILEGES = """
SELECT pg_get_userbyid(d.datdba), pg_get_userbyid(a.grantor),
       pg_get_userbyid(a.grantee), a.privilege_type, a.is_grantable
FROM pg_database d
CROSS JOIN LATERAL aclexplode(coalesce(d.datacl, acldefault('d',d.datdba))) a
WHERE d.datname=current_database() ORDER BY 1,2,3,4,5
"""


def _settings(connection: psycopg.Connection) -> None:
    connection.execute("SET LOCAL timezone TO 'UTC'")
    connection.execute("SET LOCAL datestyle TO 'ISO, YMD'")
    connection.execute("SET LOCAL intervalstyle TO 'postgres'")
    connection.execute("SET LOCAL extra_float_digits TO 3")


def _columns(connection: psycopg.Connection, table: str) -> tuple[list[str], list[str]]:
    return (
        [
            row[0]
            for row in connection.execute(
                """
            SELECT attname FROM pg_attribute WHERE attrelid=CAST(%s AS regclass)
              AND attnum>0 AND NOT attisdropped AND attgenerated=''
            ORDER BY attnum
            """,
                (f"public.{table}",),
            )
        ],
        [
            row[0]
            for row in connection.execute(
                """
            SELECT a.attname FROM pg_index i
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS key(attnum,ord)
            JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=key.attnum
            WHERE i.indrelid=CAST(%s AS regclass) AND i.indisprimary
            ORDER BY key.ord
            """,
                (f"public.{table}",),
            )
        ],
    )


def _copy_and_hash(
    source: psycopg.Connection,
    dest: psycopg.Connection,
    table: str,
    partitions: list[str],
    *,
    copy: bool,
) -> dict[str, Any]:
    columns, key = _columns(source, table)
    target_columns, target_key = _columns(dest, table)
    if target_columns != columns or (not copy and target_key != key):
        raise RuntimeError(f"{table} has a different writable column or key contract")
    relation = sql.Identifier("public", table)
    cols = sql.SQL(", ").join(sql.Identifier(col) for col in columns)
    order = (
        sql.SQL(", ").join(sql.Identifier(col) for col in key)
        if key
        else sql.SQL('row::text COLLATE "C"')
    )
    where = (
        sql.SQL("WHERE parent_table=ANY({})").format(sql.Literal(partitions))
        if table == "part_config"
        else sql.SQL("")
    )
    out = sql.SQL("COPY (SELECT {} FROM {} AS row {} ORDER BY {}) TO STDOUT").format(
        cols, relation, where, order
    )
    digest = sha256()
    with source.cursor() as reader, dest.cursor() as writer:
        if copy:
            if table == "part_config":
                writer.execute(sql.SQL("DELETE FROM public.part_config ") + where)
            statement = sql.SQL("COPY {} ({}) FROM STDIN").format(relation, cols)
            with reader.copy(out) as rows, writer.copy(statement) as load:
                for block in rows:
                    digest.update(block)
                    load.write(block)
        else:
            with reader.copy(out) as rows:
                for block in rows:
                    digest.update(block)
        expected_count = reader.rowcount
        actual = sha256()
        with writer.copy(out) as rows:
            for block in rows:
                actual.update(block)
        if digest.digest() != actual.digest() or expected_count != writer.rowcount:
            raise RuntimeError(f"{table}: key-ordered row count or hash differs")
    return {"rows": expected_count, "sha256": digest.hexdigest()}


def _sequences(source: psycopg.Connection, target: psycopg.Connection) -> None:
    for (name,) in source.execute(
        "SELECT sequencename FROM pg_sequences WHERE schemaname='public'"
    ):
        state = sql.SQL("SELECT last_value, is_called FROM {}").format(
            sql.Identifier("public", name)
        )
        value = source.execute(state).fetchone()
        assert value is not None
        target.execute(
            "SELECT setval(CAST(%s AS regclass), %s, %s)",
            (sql.Identifier("public", name).as_string(target), *value),
        )
        assert target.execute(state).fetchone() == value


def _verify_pointers(connection: psycopg.Connection) -> None:
    for query in (
        """SELECT EXISTS (
        SELECT 1 FROM documents d LEFT JOIN document_versions v
          ON (v.deployment_id,v.doc_id,v.version_id)=(d.deployment_id,d.doc_id,d.current_version_id)
        WHERE d.current_version_id IS NOT NULL AND v.version_id IS NULL)""",
        """SELECT EXISTS (
        SELECT 1 FROM document_versions v LEFT JOIN document_representations r
          ON (r.deployment_id,r.version_id,r.representation_id)=
             (v.deployment_id,v.version_id,v.current_representation_id)
        WHERE v.current_representation_id IS NOT NULL AND r.representation_id IS NULL)""",
    ):
        assert connection.execute(query).fetchone() == (False,)


def stage(args: argparse.Namespace) -> dict[str, Any]:
    database = str(conninfo_to_dict(args.reference)["dbname"])
    maintenance = make_conninfo(args.reference, dbname="postgres")
    dump, restore = shlex.split(args.pg_dump), shlex.split(args.pg_restore)
    cli_reference = args.cli_reference or args.reference
    cli_maintenance = make_conninfo(cli_reference, dbname="postgres")
    if (
        not subprocess.check_output([*dump, "--version"], text=True)
        .split()[-1]
        .startswith("18.")
    ):
        raise RuntimeError(
            "staged schema archive must be authored with PostgreSQL 18 pg_dump"
        )
    with (
        psycopg.connect(args.source) as source,
        psycopg.connect(args.reference) as reference,
    ):
        source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _settings(source)
        _settings(reference)
        identity = (
            "SELECT system_identifier, current_database() FROM pg_control_system()"
        )
        if (
            source.execute(identity).fetchone()
            == reference.execute(identity).fetchone()
        ):
            raise RuntimeError("source and reference must be different databases")
        revision = source.execute("SELECT version_num FROM alembic_version").fetchone()
        assert (
            reference.execute("SELECT version_num FROM alembic_version").fetchone()
            == revision
        )
        assert reference.info.server_version // 10000 == 18
        privileges = reference.execute(_PRIVILEGES).fetchall()
        # pg_inherits also contains partitioned indexes. Their leaf names can
        # incorporate OIDs from the local cluster, and they have no bounds.
        partition_layout = """SELECT parent.relname, child.relname, pg_get_expr(child.relpartbound, child.oid)
                              FROM pg_inherits i JOIN pg_class child ON child.oid=i.inhrelid
                              JOIN pg_class parent ON parent.oid=i.inhparent
                              WHERE child.relispartition AND child.relpartbound IS NOT NULL
                              ORDER BY 1,2"""
        assert (
            source.execute(partition_layout).fetchall()
            == reference.execute(partition_layout).fetchall()
        )
        tables = [row[0] for row in source.execute(_TABLES)]
        assert tables == [row[0] for row in reference.execute(_TABLES)]
        parents = [f"public.{row[0]}" for row in source.execute(_PARTMAN)]
        reference.close()
        with args.archive.open("xb") as output:
            subprocess.run(
                [*dump, "--schema-only", "--format=custom", "--dbname", cli_reference],
                stdout=output,
                check=True,
            )
        with psycopg.connect(maintenance, autocommit=True) as admin:
            admin.execute(
                sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                    sql.Identifier(database), sql.Identifier(database + "_ddl")
                )
            )
        with args.archive.open("rb") as archive:
            subprocess.run(
                [
                    *restore,
                    "--create",
                    "--section=pre-data",
                    "--exit-on-error",
                    "--dbname",
                    cli_maintenance,
                ],
                stdin=archive,
                check=True,
            )
        with psycopg.connect(args.reference) as target:
            _settings(target)
            for table in (*tables, "part_config"):
                _copy_and_hash(source, target, table, parents, copy=True)
            _sequences(source, target)
        with args.archive.open("rb") as archive:
            subprocess.run(
                [
                    *restore,
                    "--section=post-data",
                    "--exit-on-error",
                    "--dbname",
                    cli_reference,
                ],
                stdin=archive,
                check=True,
            )
        with psycopg.connect(args.reference) as target:
            _settings(target)
            _verify_pointers(target)
            assert target.execute(_PRIVILEGES).fetchall() == privileges
            verified = {
                table: _copy_and_hash(source, target, table, parents, copy=False)
                for table in (*tables, "part_config")
            }
        return {
            "source_pg": source.info.server_version,
            "source_revision": revision,
            "tables": verified,
        }


def preflight(dsn: str, query_dsn: str) -> dict[str, Any]:
    """Validate the prepared baseline's PG18, partman and public login contracts."""
    with psycopg.connect(dsn) as admin:
        assert admin.info.server_version // 10000 == 18
        assert admin.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "p9_55_0076",
        )
        registrations = admin.execute(
            "SELECT parent_table, control, partition_interval FROM public.part_config WHERE parent_table=ANY(%s)",
            ([f"public.{name}" for name in EXPECTED_RANGE_PARENTS],),
        ).fetchall()
        assert set(registrations) == {
            (f"public.{name}", control, "1 mon")
            for name, control in EXPECTED_RANGE_PARENTS.items()
        }
    with psycopg.connect(query_dsn) as reader:
        assert reader.info.user == "rememberstack_query_" + reader.info.dbname
        rows = reader.execute(
            "SELECT count(*) FROM memory_v1.facts_visible_history"
        ).fetchone()
    return {"partition_registrations": len(registrations), "visible_fact_rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    load = commands.add_parser("stage")
    load.add_argument("--source", required=True)
    load.add_argument("--reference", required=True)
    load.add_argument("--archive", required=True, type=Path)
    load.add_argument("--pg-dump", default="pg_dump")
    load.add_argument("--pg-restore", default="pg_restore")
    load.add_argument(
        "--cli-reference", help="libpq reference DSN as seen by pg_dump and pg_restore"
    )
    verify = commands.add_parser("preflight")
    verify.add_argument("--database", required=True)
    verify.add_argument("--query-dsn", required=True)
    args = parser.parse_args()
    result = (
        stage(args)
        if args.operation == "stage"
        else preflight(args.database, args.query_dsn)
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
