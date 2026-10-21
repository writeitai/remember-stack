"""D140 WP-ET.7: in-force periods as an adjudication input, and window review.

Two schema objects:

- ``relations.period_basis`` and ``observations.period_basis``: a fingerprint
  of the in-force periods of the fact's evidence claims (the periods of the
  versions those claims occur in) at the moment the fact's window was last
  adjudicated. NULL means the window has never been adjudicated against any
  declared period. A fact whose current fingerprint differs from the stored one
  is reviewed again (D140 §8.2); the fingerprint is how that review is found and
  how a repeated trigger becomes a no-op.
- the ``review_fact_windows`` pipeline stage: one job per lineage whose
  periods or testimony changed (it finds the affected facts) and one job per
  affected fact (it asks the adjudicator whether the fact's window still fits).

Both are additive. The downgrade drops the columns; PostgreSQL cannot remove an
enum value in place, so the stage value stays.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_39_0060"
down_revision: str | None = "p9_38_0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE = """
ALTER TABLE relations ADD COLUMN period_basis text;
ALTER TABLE observations ADD COLUMN period_basis text;
COMMENT ON COLUMN relations.period_basis IS
  'D140 §8.2: fingerprint of the evidence claims'' in-force periods when this window was last adjudicated; NULL when never adjudicated against a declared period. A differing current fingerprint queues a window review.';
COMMENT ON COLUMN observations.period_basis IS
  'D140 §8.2: fingerprint of the evidence claims'' in-force periods when this window was last adjudicated; NULL when never adjudicated against a declared period. A differing current fingerprint queues a window review.';
"""

_DOWNGRADE = """
ALTER TABLE observations DROP COLUMN period_basis;
ALTER TABLE relations DROP COLUMN period_basis;
"""


def upgrade() -> None:
    """Add the stage value (outside the column DDL) and the two columns."""
    op.execute(
        "ALTER TYPE pipeline_stage ADD VALUE IF NOT EXISTS 'review_fact_windows'"
    )
    op.execute(_UPGRADE)


def downgrade() -> None:
    """Drop the fingerprints; the enum value cannot be removed in place."""
    op.execute(_DOWNGRADE)
