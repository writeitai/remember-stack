"""Index the lineage order the document inventory pages through.

revision: p9_25_0046
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_25_0046"
down_revision: str | None = "p9_24_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# `GET /documents` pages through a deployment's live lineages newest-first.
# Without this the planner has to read every live lineage and sort them on
# each page, so a fifty-row page costs a scan of the whole corpus — fine at a
# few thousand documents, and the wrong shape entirely at the millions this
# system targets.
#
# The key is `first_seen_at`, which is stamped when a lineage is created and
# never changes. That immutability is what makes it safe to page against: an
# ordering derived from the newest version's ingest time moves whenever a
# document is re-ingested or a version is tombstoned, which silently drops or
# repeats rows across page boundaries.
#
# `doc_id` breaks ties, so the order is total: a bulk import gives many
# lineages one `first_seen_at`, and without a tie-break two of them can swap
# places between pages.
_UP = """
CREATE INDEX ix_documents_inventory_order
  ON documents (deployment_id, first_seen_at DESC, doc_id DESC)
  WHERE deleted_at IS NULL;
"""

_DOWN = """
DROP INDEX ix_documents_inventory_order;
"""


def upgrade() -> None:
    """Add the covering order for the document inventory listing."""
    op.execute(_UP)


def downgrade() -> None:
    """Remove the inventory listing order."""
    op.execute(_DOWN)
