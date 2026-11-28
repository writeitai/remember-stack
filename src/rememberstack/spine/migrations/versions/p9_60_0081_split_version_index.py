"""Index split chunks without blocking writes to populated partitions (D162)."""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Connection
from sqlalchemy import text
from sqlalchemy.engine import RowMapping

revision: str = "p9_60_0081"
down_revision: str | None = "p9_59_0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_chunks_split_version"
_PREDICATE = "span_kind = 'split'::public.chunk_span_kind"


def _leaves(connection: Connection) -> Sequence[RowMapping]:
    return (
        connection.execute(
            text("""
        SELECT c.oid, c.relname, n.nspname, c.relkind
        FROM pg_inherits i
        JOIN pg_class c ON c.oid=i.inhrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE i.inhparent='public.chunks'::regclass
        ORDER BY c.oid
    """)
        )
        .mappings()
        .all()
    )


def _lookup(
    connection: Connection, *, name: str, table: str, partitioned: bool
) -> RowMapping | None:
    row = (
        connection.execute(
            text("""
        SELECT c.relkind, i.indisvalid, i.indisready, i.indislive,
               pg_get_indexdef(c.oid) AS definition
        FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_index i ON i.indexrelid=c.oid
        WHERE n.nspname='public' AND c.relname=:name
    """),
            {"name": name},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    only = "ONLY " if partitioned else ""
    exact = (
        f"CREATE INDEX {name} ON {only}public.{table} USING btree (version_id)"
        " WHERE (span_kind = 'split'::chunk_span_kind)"
    )
    if row["relkind"] != ("I" if partitioned else "i") or row["definition"] != exact:
        raise RuntimeError(f"D162 index name {name} belongs to a different object")
    return row


def _attached(connection: Connection, leaf_oid: int) -> str | None:
    return connection.execute(
        text("""
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid=i.inhrelid
        JOIN pg_index x ON x.indexrelid=c.oid
        WHERE i.inhparent='public.ix_chunks_split_version'::regclass
          AND x.indrelid=:leaf_oid
    """),
        {"leaf_oid": leaf_oid},
    ).scalar_one_or_none()


def upgrade() -> None:
    """Resume completed native steps and rebuild an invalid interrupted leaf."""
    context = op.get_context()
    if context.as_sql:
        raise RuntimeError("D162 split-version index requires an online migration")
    connection = op.get_bind()
    quote = connection.dialect.identifier_preparer.quote
    with context.autocommit_block():
        if _lookup(connection, name=_INDEX, table="chunks", partitioned=True) is None:
            connection.exec_driver_sql(
                f"CREATE INDEX {_INDEX} ON ONLY public.chunks (version_id)"
                f" WHERE {_PREDICATE}"
            )
        for leaf in _leaves(connection):
            if leaf["nspname"] != "public" or leaf["relkind"] != "r":
                raise RuntimeError(
                    "D162 requires native direct chunks partitions in public"
                )
            table = leaf["relname"]
            attached = _attached(connection, leaf["oid"])
            name = attached or f"ix_chunks_split_version_{leaf['oid']}"
            row = _lookup(connection, name=name, table=quote(table), partitioned=False)
            if row is not None:
                if row["indisvalid"] and row["indisready"] and row["indislive"]:
                    continue
                if attached is not None:
                    raise RuntimeError("D162 found an invalid attached leaf")
                connection.exec_driver_sql(
                    f"DROP INDEX CONCURRENTLY public.{quote(name)}"
                )
            connection.exec_driver_sql(
                f"CREATE INDEX CONCURRENTLY {quote(name)} ON public.{quote(table)}"
                f" (version_id) WHERE {_PREDICATE}"
            )
    # Freeze partition metadata for the short attach phase. Ordinary writes continue.
    connection.exec_driver_sql(
        "LOCK TABLE ONLY public.chunks IN SHARE UPDATE EXCLUSIVE MODE"
    )
    for leaf in _leaves(connection):
        attached = _attached(connection, leaf["oid"])
        name = attached or f"ix_chunks_split_version_{leaf['oid']}"
        row = _lookup(
            connection, name=name, table=quote(leaf["relname"]), partitioned=False
        )
        if row is None or not (
            row["indisvalid"] and row["indisready"] and row["indislive"]
        ):
            raise RuntimeError("D162 chunks partition changed during index build")
        if attached is None:
            connection.exec_driver_sql(
                f"ALTER INDEX public.{_INDEX} ATTACH PARTITION public.{quote(name)}"
            )
    parent = _lookup(connection, name=_INDEX, table="chunks", partitioned=True)
    if parent is None or not parent["indisvalid"]:
        raise RuntimeError("D162 partitioned split-version index is incomplete")


def downgrade() -> None:
    """Drop only the D162 index and its attached leaf indexes."""
    op.execute(f"DROP INDEX public.{_INDEX}")
