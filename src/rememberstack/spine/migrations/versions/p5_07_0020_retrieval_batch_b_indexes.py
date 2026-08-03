"""Add the authoritative Batch B entity/time retrieval indexes."""

from collections.abc import Sequence

from alembic import op

revision: str = "p5_07_0020"
down_revision: str | None = "p1_04_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Index stamped validity windows and live entity-resolution joins."""
    op.execute(
        "CREATE INDEX ix_claims_valid_window ON claims "
        "(deployment_id, claim_valid_from, claim_valid_until) "
        "WHERE claim_valid_precision <> 'unknown'"
    )
    op.execute(
        "CREATE INDEX ix_resdec_entity_live ON resolution_decisions "
        "(deployment_id, entity_id, mention_id) WHERE superseded_by IS NULL"
    )


def downgrade() -> None:
    """Remove only the two Batch B indexes."""
    op.execute("DROP INDEX IF EXISTS ix_resdec_entity_live")
    op.execute("DROP INDEX IF EXISTS ix_claims_valid_window")
