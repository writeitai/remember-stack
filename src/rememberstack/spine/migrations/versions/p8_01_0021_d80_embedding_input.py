"""D80 embedding-input policy stamps on chunks."""

from collections.abc import Sequence

from alembic import op

revision: str = "p8_01_0021"
down_revision: str | None = "p5_07_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add location-facts / policy / hash columns for conventional embed input."""
    op.execute(
        """
        DO $$ BEGIN
          ALTER TYPE pipeline_component ADD VALUE 'embedding_input_policy';
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    for column_sql in (
        "location_facts_json jsonb",
        "location_header text",
        "embedding_text_hash text",
        "embedding_input_policy_version text",
        "policy_generation text",
    ):
        name = column_sql.split()[0]
        op.execute(
            f"ALTER TABLE chunks ADD COLUMN IF NOT EXISTS {column_sql}"
        )
        del name
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chunks_policy_embed
          ON chunks (deployment_id, policy_generation, embedding_version)
        """
    )


def downgrade() -> None:
    """Drop D80 stamp columns (enum value remains)."""
    op.execute("DROP INDEX IF EXISTS ix_chunks_policy_embed")
    op.execute(
        """
        ALTER TABLE chunks
          DROP COLUMN IF EXISTS location_facts_json,
          DROP COLUMN IF EXISTS location_header,
          DROP COLUMN IF EXISTS embedding_text_hash,
          DROP COLUMN IF EXISTS embedding_input_policy_version,
          DROP COLUMN IF EXISTS policy_generation
        """
    )
