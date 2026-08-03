"""D80 connector packaging fields for location facts (document_versions)."""

from collections.abc import Sequence

from alembic import op

revision: str = "p8_01_0022"
down_revision: str | None = "p8_01_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add optional packaging/message coordinates used by embedding-input policy."""
    for column_sql in (
        "source_shape text NOT NULL DEFAULT 'document'",
        "channel_ref text",
        "thread_ref text",
        "author_ref text",
        "message_ts text",
    ):
        op.execute(
            f"ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS {column_sql}"
        )


def downgrade() -> None:
    """Drop packaging columns added for D80 connector metadata."""
    for column in (
        "message_ts",
        "author_ref",
        "thread_ref",
        "channel_ref",
        "source_shape",
    ):
        op.execute(f"ALTER TABLE document_versions DROP COLUMN IF EXISTS {column}")
