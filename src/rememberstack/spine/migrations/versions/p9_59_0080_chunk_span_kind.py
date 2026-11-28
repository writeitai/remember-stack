"""Add the immutable D162 span kind, with a metadata-only default."""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p9_59_0080"
down_revision: str | None = "p9_58_0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Old rows and old workers both use whole through the column default."""
    op.execute("CREATE TYPE public.chunk_span_kind AS ENUM ('whole', 'split')")
    op.execute(
        "ALTER TABLE public.chunks ADD COLUMN span_kind public.chunk_span_kind"
        " NOT NULL DEFAULT 'whole'"
    )


def downgrade() -> None:
    """Drop the column only if doing so preserves every version's reuse state."""
    op.execute("LOCK TABLE public.chunks IN ACCESS EXCLUSIVE MODE")
    if (
        op.get_bind()
        .execute(
            text("SELECT EXISTS(SELECT 1 FROM public.chunks WHERE span_kind='split')")
        )
        .scalar_one()
    ):
        raise RuntimeError("D162 downgrade requires a database without split chunks")
    op.execute("ALTER TABLE public.chunks DROP COLUMN span_kind")
    op.execute("DROP TYPE public.chunk_span_kind")
