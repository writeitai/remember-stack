"""D159: a settled source archive no longer names the cycle it waited for.

``documents.archived_cycle_id`` names the sync cycle a source archive waits
for; the finalizer now clears it at the archive's first settlement, so a
later sweep knows the episode's full cascade has run. Source archives an
earlier release already settled (their cycle finalized, or finalized and
since reopened by a D143 layer backfill, and none of their claims current)
are marked settled here. Downgrade restores the column comment and
leaves the values: the earlier release reads the column only while the
cycle is unfinalized.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_56_0077"
down_revision: str | None = "p9_55_0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Mark settled the source archives an earlier release settled."""
    op.execute(
        """
        UPDATE documents d SET archived_cycle_id = NULL
        WHERE d.archived_cycle_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM connector_sync_cycles y
              WHERE y.cycle_id = d.archived_cycle_id
                -- finalized, or finalized once and reopened by a D143 layer
                -- backfill (which clears finalized_at)
                AND (y.finalized_at IS NOT NULL OR y.layers_reopened_at IS NOT NULL)
          )
          AND NOT EXISTS (
              SELECT 1 FROM claims cl
              WHERE cl.deployment_id = d.deployment_id AND cl.doc_id = d.doc_id
                AND cl.is_current_testimony
          )
        """
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
