"""Indexes for D84 chunk-level extract barrier and neighbour windows.

revision: p9_07_0028
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "p9_07_0028"
down_revision: str | None = "p9_06_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Speed barrier/neighbour lookups by representation packing generation."""
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chunks_representation_chunker_ordinal
        ON chunks (representation_id, chunker_version, ordinal)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_procstate_chunk_extract
        ON processing_state (deployment_id, stage, target_kind, component_version, status)
        WHERE stage = 'extract_claims' AND target_kind = 'chunk'
        """
    )


def downgrade() -> None:
    """Drop D84 helper indexes."""
    op.execute("DROP INDEX IF EXISTS ix_procstate_chunk_extract")
    op.execute("DROP INDEX IF EXISTS ix_chunks_representation_chunker_ordinal")
