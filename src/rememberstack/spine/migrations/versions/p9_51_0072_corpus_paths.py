"""D155: record every possibly written document folder before object IO."""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_51_0072"
down_revision: str | None = "p9_50_0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE documents ADD COLUMN corpus_paths text[]")
    op.execute("""COMMENT ON COLUMN documents.corpus_paths IS
      'D155 possibly written corpus folder paths, committed before object IO and reduced after cleanup/navigation. NULL means none recorded; reconcile repairs missing state. Not a paid-work identity.'""")


def downgrade() -> None:
    op.execute("ALTER TABLE documents DROP COLUMN corpus_paths")
