"""Add Claimify-stage loss ledger values to extraction_decision_type (#161).

Every kept Selection span must end in one of {accepted claim(s),
grounding_rejected row(s), claimify_omitted row}. The two new enum values
make the previously silent Claimify omissions and D32 gate rejections
auditable on the append-only D33 transcript. Additive only; no new indexes
on the partitioned claim_extraction_decisions table.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p1_03_0018"
down_revision: str | None = "p7_05_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Append the two loss-ledger values (additive; existing rows untouched)."""
    op.execute(
        "ALTER TYPE extraction_decision_type ADD VALUE IF NOT EXISTS 'claimify_omitted'"
    )
    op.execute(
        "ALTER TYPE extraction_decision_type"
        " ADD VALUE IF NOT EXISTS 'grounding_rejected'"
    )


def downgrade() -> None:
    """PostgreSQL cannot remove an enum value in place; additive no-op."""
