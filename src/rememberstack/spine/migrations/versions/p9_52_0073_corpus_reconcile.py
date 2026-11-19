"""D155: periodic corpus verification uses the existing delayed work ledger."""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_52_0073"
down_revision: str | None = "p9_51_0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE pipeline_stage ADD VALUE IF NOT EXISTS 'reconcile_corpus'"
        )


def downgrade() -> None:
    pass  # PostgreSQL enum labels cannot be removed without rebuilding the type.
