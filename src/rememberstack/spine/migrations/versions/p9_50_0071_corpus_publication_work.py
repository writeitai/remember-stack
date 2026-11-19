"""D155: ordinary unlaned publication work, without a new coordination table."""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_50_0071"
down_revision: str | None = "p9_49_0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the stage and fresh-event target to the existing ledger vocabulary."""
    op.execute("ALTER TYPE pipeline_stage ADD VALUE IF NOT EXISTS 'publish_corpus'")
    op.execute(
        "ALTER TYPE processing_target ADD VALUE IF NOT EXISTS 'corpus_publication'"
    )


def downgrade() -> None:
    """PostgreSQL cannot remove enum values in place; additive no-op."""
