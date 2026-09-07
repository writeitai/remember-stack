"""Persistent serving/intake fence for in-place conversion of fact date meaning."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

FACT_WINDOW_GENERATION = "mutable-fact-window-1"


def require_fact_windows_ready(
    *, connection: Connection, deployment_id: UUID | None = None
) -> None:
    """Refuse a partial or unsupported interpretation, including after restart."""
    generations = (
        connection.execute(
            text("""SELECT fact_window_generation FROM deployments
        WHERE CAST(:deployment_id AS uuid) IS NULL OR deployment_id=:deployment_id"""),
            {"deployment_id": deployment_id},
        )
        .scalars()
        .all()
    )
    if not generations or any(value != FACT_WINDOW_GENERATION for value in generations):
        raise RuntimeError(
            "fact window conversion is incomplete; serving and intake remain closed"
        )
