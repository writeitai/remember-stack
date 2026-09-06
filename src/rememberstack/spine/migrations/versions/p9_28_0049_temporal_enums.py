"""Commit D107/D110 temporal work and adjudication vocabulary before use.

revision: p9_28_0049
"""

from alembic import op

from rememberstack.spine.migrations._helpers import _split_sql

revision: str = "p9_28_0049"
down_revision: str | None = "p9_27_0048"
branch_labels = None
depends_on = None

_DDL = r"""
ALTER TYPE public.processing_target ADD VALUE IF NOT EXISTS 'temporal_correction';
ALTER TYPE public.processing_target ADD VALUE IF NOT EXISTS 'temporal_event';
ALTER TYPE public.pipeline_stage ADD VALUE IF NOT EXISTS 'correct_temporal';
ALTER TYPE public.pipeline_stage ADD VALUE IF NOT EXISTS 'refresh_temporal';
ALTER TYPE public.pipeline_component ADD VALUE IF NOT EXISTS 'temporal_adjudicator';
ALTER TYPE public.pipeline_component ADD VALUE IF NOT EXISTS 'temporal_refresher';
ALTER TYPE public.adjudication_outcome ADD VALUE IF NOT EXISTS 'temporal_correct';
ALTER TYPE public.adjudication_outcome ADD VALUE IF NOT EXISTS 'temporal_compensate';
ALTER TYPE public.adjudication_outcome ADD VALUE IF NOT EXISTS 'temporal_uncertain';

ALTER TYPE public.adjudication_outcome ADD VALUE IF NOT EXISTS 'migrate';

ALTER TYPE public.adjudication_method ADD VALUE IF NOT EXISTS 'migration';
"""


def upgrade() -> None:
    """Commit enum additions before a later transaction uses their new labels."""
    with op.get_context().autocommit_block():
        for statement in _split_sql(sql=_DDL):
            op.execute(statement)


def downgrade() -> None:
    """Retain unused enum labels; PostgreSQL cannot safely remove individual labels."""
