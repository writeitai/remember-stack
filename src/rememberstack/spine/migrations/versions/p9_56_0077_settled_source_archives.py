"""D159: a settled source archive no longer names the cycle it waited for.

``documents.archived_cycle_id`` names the sync cycle a source archive waits
for; the finalizer now clears it at the archive's first settlement, so a
later sweep knows the episode's full cascade has run.

A source archive an earlier release already settled is marked settled here,
judged by what a first cascade leaves behind, not by poll history: a
currency event or a closure already carries the archive's reconciliation id,
or the lineage has no current claim and no open fact that only it evidences.
Any other marker stays and the finalizer settles the archive normally.
Downgrade restores the column comment and leaves the values: the earlier
release reads the column only while the cycle is unfinalized.
"""

from collections.abc import Sequence
from datetime import datetime
from datetime import UTC
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from alembic import op
from sqlalchemy import text

revision: str = "p9_56_0077"
down_revision: str | None = "p9_55_0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MARKED = text(
    """
    SELECT doc_id, archived_at FROM documents
    WHERE archived_cycle_id IS NOT NULL AND archived_at IS NOT NULL
    ORDER BY doc_id
    """
)

_SETTLED = text(
    """
    SELECT
      EXISTS (
          SELECT 1 FROM testimony_currency_events e
          WHERE e.reconciliation_id = :rid
      )
      OR EXISTS (
          SELECT 1 FROM relation_adjudications a
          WHERE a.features ->> 'reconciliation_id' = CAST(:rid AS text)
      )
      OR EXISTS (
          SELECT 1 FROM observation_adjudications a
          WHERE a.features ->> 'reconciliation_id' = CAST(:rid AS text)
      )
      OR (
          NOT EXISTS (
              SELECT 1 FROM claims cl
              WHERE cl.doc_id = :doc_id AND cl.is_current_testimony
          )
          AND NOT EXISTS (
              SELECT 1 FROM relation_evidence e
              JOIN relations r ON r.relation_id = e.relation_id
              WHERE e.doc_id = :doc_id AND r.invalidated_at IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM relation_evidence other
                    WHERE other.relation_id = r.relation_id
                      AND other.doc_id <> :doc_id
                )
          )
          AND NOT EXISTS (
              SELECT 1 FROM observation_evidence e
              JOIN observations o ON o.observation_id = e.observation_id
              WHERE e.doc_id = :doc_id AND o.invalidated_at IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM observation_evidence other
                    WHERE other.observation_id = o.observation_id
                      AND other.doc_id <> :doc_id
                )
          )
      )
    """
)


def archive_reconciliation_id(*, doc_id: UUID, archived_at: datetime) -> UUID:
    """The archive episode's reconciliation id, frozen with p9_55_0076's copy."""
    return uuid5(
        NAMESPACE_URL,
        f"rememberstack:archive:{doc_id}:{archived_at.astimezone(UTC).isoformat()}",
    )


def upgrade() -> None:
    """Mark settled the source archives an earlier release settled."""
    connection = op.get_bind()
    for row in connection.execute(_MARKED).mappings().all():
        rid = archive_reconciliation_id(
            doc_id=row["doc_id"], archived_at=row["archived_at"]
        )
        if connection.execute(
            _SETTLED, {"rid": rid, "doc_id": row["doc_id"]}
        ).scalar_one():
            connection.execute(
                text("UPDATE documents SET archived_cycle_id = NULL WHERE doc_id = :d"),
                {"d": row["doc_id"]},
            )
    op.execute(
        "COMMENT ON COLUMN documents.archived_cycle_id IS"
        " 'D159: the sync cycle a source archive waits for (the retract-timing"
        " barrier); NULL for a caller archive and once the archive has settled.'"
    )


def downgrade() -> None:
    """Restore the earlier comment; a cleared marker reads as settled to every release."""
    op.execute(
        "COMMENT ON COLUMN documents.archived_cycle_id IS"
        " 'D159 the sync cycle that observed a source archive; its cascade waits for"
        " that cycle''s finalization (the retract-timing barrier). NULL otherwise.'"
    )
