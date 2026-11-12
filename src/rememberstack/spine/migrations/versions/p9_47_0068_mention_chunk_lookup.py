"""Build the D153 mention-to-chunk access path on existing data, online."""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Connection
from sqlalchemy import text
from sqlalchemy.engine import RowMapping

revision: str = "p9_47_0068"
down_revision: str | None = "p9_46_0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARENT = "ix_mentions_deployment_chunk"


def _index(*, connection: Connection, name: str) -> RowMapping | None:
    """Read an exact public object, including every relevant index property."""
    return (
        connection.execute(
            text("""
        SELECT c.oid, c.relkind, i.indrelid, i.indisvalid, i.indisunique,
               i.indisexclusion, i.indnkeyatts, i.indnatts, am.amname,
               i.indoption::text AS options, i.indcollation::text AS collations,
               ARRAY(SELECT opclass.opcdefault AND opclass.opcintype='uuid'::regtype
                     FROM unnest(i.indclass::oid[]) key(class_oid)
                     JOIN pg_opclass opclass ON opclass.oid=key.class_oid) AS default_uuid_ops,
               pg_get_expr(i.indpred, i.indrelid) AS predicate,
               pg_get_expr(i.indexprs, i.indrelid) AS expressions,
               pg_get_indexdef(c.oid, 1, true) AS first_key,
               pg_get_indexdef(c.oid, 2, true) AS second_key
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        LEFT JOIN pg_index AS i ON i.indexrelid = c.oid
        LEFT JOIN pg_am AS am ON am.oid = c.relam
        WHERE n.nspname = 'public' AND c.relname = :name
        """),
            {"name": name},
        )
        .mappings()
        .one_or_none()
    )


def _check(*, row: RowMapping, table_oid: int, partitioned: bool) -> None:
    """Refuse a colliding object instead of adopting a different access path."""
    if not (
        row["relkind"] == ("I" if partitioned else "i")
        and row["indrelid"] == table_oid
        and not row["indisunique"]
        and not row["indisexclusion"]
        and row["indnkeyatts"] == row["indnatts"] == 2
        and row["amname"] == "btree"
        and row["options"] == "0 0"
        and row["collations"] == "0 0"
        and row["default_uuid_ops"] == [True, True]
        and row["predicate"] is None
        and row["expressions"] is None
        and row["first_key"] == "deployment_id"
        and row["second_key"] == "chunk_id"
    ):
        raise RuntimeError("D153 mention index name collides with a different object")


def _leaves(*, connection: Connection) -> Sequence[RowMapping]:
    """Enumerate the engine's direct monthly partitions, including default."""
    rows = (
        connection.execute(
            text("""
        SELECT leaf.oid, ns.nspname, leaf.relname, leaf.relkind
        FROM pg_inherits AS inheritance
        JOIN pg_class AS leaf ON leaf.oid = inheritance.inhrelid
        JOIN pg_namespace AS ns ON ns.oid = leaf.relnamespace
        WHERE inheritance.inhparent = 'public.mentions'::regclass
        ORDER BY leaf.oid
    """)
        )
        .mappings()
        .all()
    )
    if any(row["relkind"] != "r" for row in rows):
        raise RuntimeError("D153 requires the native direct monthly mention partitions")
    if any(row["nspname"] != "public" for row in rows):
        raise RuntimeError("D153 requires native mention partitions in public")
    return rows


def _attached(*, connection: Connection, table_oid: int) -> str | None:
    """Find an already attached leaf, including one inherited by a new partition."""
    return connection.execute(
        text("""
        SELECT leaf.relname
        FROM pg_inherits AS inheritance
        JOIN pg_class AS parent ON parent.oid = inheritance.inhparent
        JOIN pg_namespace AS ns ON ns.oid = parent.relnamespace
        JOIN pg_class AS leaf ON leaf.oid = inheritance.inhrelid
        JOIN pg_index AS idx ON idx.indexrelid = leaf.oid
        WHERE ns.nspname = 'public' AND parent.relname = :parent
          AND idx.indrelid = :table_oid
        """),
        {"parent": _PARENT, "table_oid": table_oid},
    ).scalar_one_or_none()


def upgrade() -> None:
    """Build leaves concurrently, attach atomically and verify native validity."""
    if op.get_context().as_sql:
        raise RuntimeError(
            "D153 concurrent partition index requires an online migration"
        )
    connection = op.get_bind()
    quote = connection.dialect.identifier_preparer.quote
    table_oid = int(
        connection.execute(text("SELECT 'public.mentions'::regclass::oid")).scalar_one()
    )
    # The native runner holds its SESSION migration lock across this block.
    # Every committed object is either validated on rerun or an own-named
    # invalid leaf which can be dropped/rebuilt without adopting foreign DDL.
    with op.get_context().autocommit_block():
        parent = _index(connection=connection, name=_PARENT)
        if parent is None:
            connection.exec_driver_sql(
                f"CREATE INDEX {_PARENT} ON ONLY public.mentions (deployment_id, chunk_id)"
            )
        else:
            _check(row=parent, table_oid=table_oid, partitioned=True)
        for leaf in _leaves(connection=connection):
            attached = _attached(connection=connection, table_oid=int(leaf["oid"]))
            name = attached or f"ix_mentions_dc_{leaf['oid']}"
            existing = _index(connection=connection, name=name)
            if existing is not None:
                _check(row=existing, table_oid=int(leaf["oid"]), partitioned=False)
                if existing["indisvalid"]:
                    continue
                if attached is not None:
                    raise RuntimeError(
                        "D153 attached leaf is invalid; inspect native catalog"
                    )
                connection.exec_driver_sql(
                    f"DROP INDEX CONCURRENTLY public.{quote(name)}"
                )
            connection.exec_driver_sql(
                f"CREATE INDEX CONCURRENTLY {quote(name)}"
                f" ON {quote(leaf['nspname'])}.{quote(leaf['relname'])}"
                " (deployment_id, chunk_id)"
            )
    # Prevent partition attachment/creation during final enumeration. Ordinary
    # row writers remain allowed; this lock covers metadata, never a table scan.
    connection.exec_driver_sql(
        "LOCK TABLE ONLY public.mentions IN SHARE UPDATE EXCLUSIVE MODE"
    )
    for leaf in _leaves(connection=connection):
        attached = _attached(connection=connection, table_oid=int(leaf["oid"]))
        name = attached or f"ix_mentions_dc_{leaf['oid']}"
        existing = _index(connection=connection, name=name)
        if existing is None:
            raise RuntimeError("D153 partition appeared during build; rerun migration")
        _check(row=existing, table_oid=int(leaf["oid"]), partitioned=False)
        if not existing["indisvalid"]:
            raise RuntimeError("D153 leaf index is not valid")
        if attached is None:
            connection.exec_driver_sql(
                f"ALTER INDEX public.{_PARENT} ATTACH PARTITION public.{quote(name)}"
            )
    parent = _index(connection=connection, name=_PARENT)
    if parent is None or not parent["indisvalid"]:
        raise RuntimeError("D153 parent index is not valid after leaf attachment")


def downgrade() -> None:
    """Remove the parent and its attached leaf indexes through native DDL."""
    op.execute(f"DROP INDEX public.{_PARENT}")
