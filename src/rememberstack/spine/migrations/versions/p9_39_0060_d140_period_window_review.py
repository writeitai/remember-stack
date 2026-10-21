"""D140 WP-ET.7: in-force periods as an adjudication input, and window review.

Three schema objects:

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
- ``fact_window_review_attempts``: one claimed review per fact and input
  fingerprint, with the model's completed answer once it exists, so a retry
  after a failed apply or a duplicate job reuses the answer instead of paying
  for another call. A row is deleted when its answer is applied; hard forget
  deletes the rows of the facts it touches.

All are additive. The downgrade drops the table and columns; PostgreSQL cannot
remove an enum value in place, so the stage value stays.
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
  'D140 §8.2: fingerprint of the periodised evidence claims and their in-force periods when this window was last adjudicated; NULL when that evidence had no periodised claim. A differing current fingerprint queues a window review.';
COMMENT ON COLUMN observations.period_basis IS
  'D140 §8.2: fingerprint of the periodised evidence claims and their in-force periods when this window was last adjudicated; NULL when that evidence had no periodised claim. A differing current fingerprint queues a window review.';
CREATE TABLE fact_window_review_attempts (
  deployment_id uuid NOT NULL REFERENCES deployments ON DELETE CASCADE,
  fact_kind     text NOT NULL CHECK (fact_kind IN ('relation', 'observation')),
  fact_id       uuid NOT NULL,
  input_digest  text NOT NULL,
  claimed_at    timestamptz NOT NULL DEFAULT now(),
  answer        jsonb,
  answered_at   timestamptz,
  PRIMARY KEY (deployment_id, fact_kind, fact_id, input_digest),
  CHECK ((answer IS NULL) = (answered_at IS NULL))
);
COMMENT ON TABLE fact_window_review_attempts IS
  'D140 §8.2: one claimed fact window review per fact and input fingerprint, with the completed model answer once it exists; reused by retries and duplicate jobs, deleted when applied, and deleted for the touched facts by hard forget.';
"""

_DOWNGRADE = """
DROP TABLE fact_window_review_attempts;
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
    """Drop the attempts and fingerprints; the enum value stays in place."""
    op.execute(_DOWNGRADE)
