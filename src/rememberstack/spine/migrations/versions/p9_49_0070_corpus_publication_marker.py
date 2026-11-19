"""D155: nullable success marker for the pure reader export's desired digest.

No publication wiring or new durable coordination state is installed here.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_49_0070"
down_revision: str | None = "p9_48_0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Existing lineages start unpublished; no backfill or suffix allocation."""
    op.execute("ALTER TABLE documents ADD COLUMN corpus_published_revision text")
    op.execute("""COMMENT ON COLUMN documents.corpus_published_revision IS
      'D155 deterministic desired export digest after successful corpus publication; NULL while unpublished or updating. Not a revision counter or paid-work identity.'""")


def downgrade() -> None:
    """Remove the derived success marker."""
    op.execute("ALTER TABLE documents DROP COLUMN corpus_published_revision")
