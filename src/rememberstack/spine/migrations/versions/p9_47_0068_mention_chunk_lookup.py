"""Build the D153 mention and ordered document-bridge indexes online."""

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
_DOCUMENT_INDEX = "ix_documents_entity"
_DOCUMENT_STAGING = "ix_documents_entity_d153"


def _index(*, connection: Connection, name: str) -> RowMapping | None:
    """Read an exact public object, including every relevant index property."""
    return (
        connection.execute(
            text("""
        SELECT c.oid, c.relkind, i.indrelid, i.indisvalid, i.indisunique,
               i.indisexclusion, i.indnkeyatts, i.indnatts, am.amname,
               i.indisready, i.indislive,
               i.indoption::text AS options, i.indcollation::text AS collations,
               ARRAY(SELECT opclass.opcdefault AND opclass.opcintype='uuid'::regtype
                     FROM unnest(i.indclass::oid[]) key(class_oid)
                     JOIN pg_opclass opclass ON opclass.oid=key.class_oid) AS default_uuid_ops,
               ARRAY(SELECT ns.nspname||'.'||opclass.opcname
                     FROM unnest(i.indclass::oid[]) WITH ORDINALITY key(class_oid,n)
                     JOIN pg_opclass opclass ON opclass.oid=key.class_oid
                     JOIN pg_namespace ns ON ns.oid=opclass.opcnamespace
                     ORDER BY key.n) AS opclasses,
               ARRAY(SELECT CASE WHEN key.collation_oid=0 THEN ''
                                  ELSE ns.nspname||'.'||coll.collname END
                     FROM unnest(i.indcollation::oid[]) WITH ORDINALITY key(collation_oid,n)
                     LEFT JOIN pg_collation coll ON coll.oid=key.collation_oid
                     LEFT JOIN pg_namespace ns ON ns.oid=coll.collnamespace
                     ORDER BY key.n) AS collation_names,
               pg_get_expr(i.indpred, i.indrelid) AS predicate,
               pg_get_expr(i.indexprs, i.indrelid) AS expressions,
               pg_get_indexdef(c.oid) AS definition,
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


def _document_shape(*, row: RowMapping, name: str, table_oid: int) -> bool:
    """Identify the ordered shape, refusing every non-native name collision."""
    prefix = f"CREATE INDEX {name} ON public.documents USING btree "
    predicate = " WHERE (document_entity_id IS NOT NULL)"
    old = prefix + "(document_entity_id)" + predicate
    ordered = (
        prefix
        + '(document_entity_id, deployment_id, ((doc_id)::text) COLLATE "C")'
        + predicate
    )
    is_ordered = row["definition"] == ordered
    if (
        row["relkind"] != "i"
        or row["indrelid"] != table_oid
        or row["definition"] not in (old, ordered)
        or row["opclasses"]
        != (
            ["pg_catalog.uuid_ops"] * (2 if is_ordered else 1)
            + (["pg_catalog.text_ops"] if is_ordered else [])
        )
        or row["collation_names"] != (["", "", "pg_catalog.C"] if is_ordered else [""])
    ):
        # Non-pretty pg_get_indexdef includes expression, collation, nondefault
        # opclasses, INCLUDE, uniqueness, storage options and tablespace. Exact
        # native DDL is intentional: an unexpected object is never adopted.
        raise RuntimeError("D153 document index name collides with a different object")
    return is_ordered


def _ready(*, row: RowMapping) -> bool:
    """Require a complete native index before removing any serving object."""
    return bool(row["indisvalid"] and row["indisready"] and row["indislive"])


def _replace_document_index(*, connection: Connection, ordered: bool) -> None:
    """Complete one exact native online replacement, also across interruptions."""
    table_oid = int(
        connection.exec_driver_sql(
            "SELECT 'public.documents'::regclass::oid"
        ).scalar_one()
    )
    current = _index(connection=connection, name=_DOCUMENT_INDEX)
    staging = _index(connection=connection, name=_DOCUMENT_STAGING)
    current_shape = (
        _document_shape(row=current, name=_DOCUMENT_INDEX, table_oid=table_oid)
        if current is not None
        else None
    )
    if staging is not None:
        if (
            _document_shape(row=staging, name=_DOCUMENT_STAGING, table_oid=table_oid)
            != ordered
        ):
            raise RuntimeError(
                "D153 document staging index has the wrong destination shape"
            )
    if current_shape == ordered:
        if current is None or not _ready(row=current) or staging is not None:
            raise RuntimeError(
                "D153 document destination index is incomplete or has a leftover staging object"
            )
        return
    if current is None or not _ready(row=current):
        if staging is None or not _ready(row=staging):
            raise RuntimeError("D153 document replacement has no validated index")
    elif staging is None or not _ready(row=staging):
        if staging is not None:
            connection.exec_driver_sql(
                f"DROP INDEX CONCURRENTLY public.{_DOCUMENT_STAGING}"
            )
        keys = (
            '(document_entity_id, deployment_id, ((doc_id::text) COLLATE pg_catalog."C"))'
            if ordered
            else "(document_entity_id)"
        )
        connection.exec_driver_sql(
            f"CREATE INDEX CONCURRENTLY {_DOCUMENT_STAGING} ON public.documents {keys}"
            " WHERE document_entity_id IS NOT NULL"
        )
    # Recheck the validated replacement immediately before every destructive
    # step. The native SESSION migration lock spans these autocommit phases.
    staging = _index(connection=connection, name=_DOCUMENT_STAGING)
    if (
        staging is None
        or _document_shape(row=staging, name=_DOCUMENT_STAGING, table_oid=table_oid)
        != ordered
        or not _ready(row=staging)
    ):
        raise RuntimeError("D153 document replacement index is not ready")
    current = _index(connection=connection, name=_DOCUMENT_INDEX)
    if current is not None:
        if (
            _document_shape(row=current, name=_DOCUMENT_INDEX, table_oid=table_oid)
            == ordered
        ):
            raise RuntimeError("D153 document source changed during replacement")
        connection.exec_driver_sql(f"DROP INDEX CONCURRENTLY public.{_DOCUMENT_INDEX}")
    staging = _index(connection=connection, name=_DOCUMENT_STAGING)
    if (
        staging is None
        or _document_shape(row=staging, name=_DOCUMENT_STAGING, table_oid=table_oid)
        != ordered
        or not _ready(row=staging)
    ):
        raise RuntimeError("D153 document replacement index changed before rename")
    connection.exec_driver_sql(
        f"ALTER INDEX public.{_DOCUMENT_STAGING} RENAME TO {_DOCUMENT_INDEX}"
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
        _replace_document_index(connection=connection, ordered=True)
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
    """Restore the exact old bridge shape and remove the mention index."""
    if op.get_context().as_sql:
        raise RuntimeError("D153 concurrent indexes require an online migration")
    with op.get_context().autocommit_block():
        _replace_document_index(connection=op.get_bind(), ordered=False)
    op.execute(f"DROP INDEX public.{_PARENT}")
