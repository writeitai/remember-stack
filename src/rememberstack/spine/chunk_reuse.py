"""D162 lineage coordination and the shared immutable version predicate."""

from uuid import UUID

from sqlalchemy import Connection
from sqlalchemy import text


class ChunkReuseBlocked(Exception):
    """A split grid committed before the proposed reuse could publish."""


def version_is_reusable(version_sql: str) -> str:
    """SQL for an eligible version. Callers supply a static column or bind."""
    return (
        "NOT EXISTS (SELECT 1 FROM chunks split "
        f"WHERE split.version_id = {version_sql} AND split.span_kind = 'split')"
    )


def lock_chunk_lineage(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID, exclusive: bool
) -> None:
    """Order grid commits and reuse writes on the same existing lineage row."""
    lock = "UPDATE" if exclusive else "SHARE"
    present = connection.execute(
        text(f"""
            SELECT doc_id FROM documents
            WHERE deployment_id=:deployment_id AND doc_id=:doc_id AND deleted_at IS NULL
            FOR {lock}
        """),
        {"deployment_id": deployment_id, "doc_id": doc_id},
    ).scalar_one_or_none()
    if present is None:
        raise LookupError("chunk lineage disappeared before publication")


def require_reusable_chunks_on(
    *, connection: Connection, deployment_id: UUID, chunk_ids: tuple[UUID, UUID]
) -> None:
    """After the lineage lock, check both versions in a fresh statement."""
    eligible = connection.execute(
        text(f"""
            SELECT count(DISTINCT c.chunk_id) FROM chunks c
            WHERE c.deployment_id=:deployment_id AND c.chunk_id=ANY(:chunk_ids)
              AND {version_is_reusable("c.version_id")}
        """),
        {"deployment_id": deployment_id, "chunk_ids": list(chunk_ids)},
    ).scalar_one()
    if eligible != len(set(chunk_ids)):
        raise ChunkReuseBlocked
