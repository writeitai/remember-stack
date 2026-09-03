"""Canonical half-open bounds for D41 claim windows (D107 §5, WP-T.0a).

Two IMMUTABLE functions turn a stored claim window — inclusive ends plus a
precision — into the half-open interval its precision means, so that every
comparison in the engine (the as-of candidate scan, the D106 temporal rung)
reads the same bounds: a day is the whole calendar day, a year is the whole
year, an instant is a non-empty point, an open window has no end. The Python
twin is ``rememberstack.core.temporal.canonical_bounds``; both are pinned by
tests to stay equivalent. Claim storage and its CHECK constraints are
unchanged.

revision: p9_26_0047
"""

from alembic import op

revision: str = "p9_26_0047"
down_revision: str | None = "p9_25_0046"
branch_labels = None
depends_on = None

_FUNCTIONS_DDL = r"""
CREATE FUNCTION claim_canonical_start(
  valid_from timestamptz,
  valid_precision claim_valid_precision
) RETURNS timestamptz
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN valid_from IS NULL OR valid_precision = 'unknown' THEN NULL
    WHEN valid_precision = 'day'     THEN date_trunc('day',     valid_from, 'UTC')
    WHEN valid_precision = 'month'   THEN date_trunc('month',   valid_from, 'UTC')
    WHEN valid_precision = 'quarter' THEN date_trunc('quarter', valid_from, 'UTC')
    WHEN valid_precision = 'year'    THEN date_trunc('year',    valid_from, 'UTC')
    ELSE valid_from
  END
$$;
COMMENT ON FUNCTION claim_canonical_start(timestamptz, claim_valid_precision) IS
  'D107 §5: the inclusive start of a claim window aligned to its precision unit in UTC; NULL for unknown precision.';

CREATE FUNCTION claim_canonical_end(
  valid_from timestamptz,
  valid_until timestamptz,
  valid_precision claim_valid_precision
) RETURNS timestamptz
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN valid_from IS NULL OR valid_precision = 'unknown' THEN NULL
    WHEN valid_precision = 'open'    THEN NULL
    WHEN valid_precision = 'instant' THEN valid_from + interval '1 microsecond'
    WHEN valid_precision = 'day'     THEN date_trunc('day',     coalesce(valid_until, valid_from), 'UTC') + interval '1 day'
    WHEN valid_precision = 'month'   THEN date_trunc('month',   coalesce(valid_until, valid_from), 'UTC') + interval '1 month'
    WHEN valid_precision = 'quarter' THEN date_trunc('quarter', coalesce(valid_until, valid_from), 'UTC') + interval '3 months'
    WHEN valid_precision = 'year'    THEN date_trunc('year',    coalesce(valid_until, valid_from), 'UTC') + interval '1 year'
  END
$$;
COMMENT ON FUNCTION claim_canonical_end(timestamptz, timestamptz, claim_valid_precision) IS
  'D107 §5: the EXCLUSIVE end of a claim window aligned to its precision unit in UTC; NULL for open or unknown.';

-- The as-of candidate scan filters on the canonical ends; an expression
-- index keeps it indexed exactly as the raw-column partial index did.
CREATE INDEX ix_claims_canonical_window
  ON claims (
    deployment_id,
    claim_canonical_start(claim_valid_from, claim_valid_precision),
    claim_canonical_end(claim_valid_from, claim_valid_until, claim_valid_precision)
  )
  WHERE claim_valid_precision <> 'unknown';
"""

_FUNCTIONS_DROP = r"""
DROP INDEX IF EXISTS ix_claims_canonical_window;
DROP FUNCTION IF EXISTS claim_canonical_end(timestamptz, timestamptz, claim_valid_precision);
DROP FUNCTION IF EXISTS claim_canonical_start(timestamptz, claim_valid_precision);
"""


def upgrade() -> None:
    """Create the canonical-bounds functions and their expression index."""
    op.execute(_FUNCTIONS_DDL)


def downgrade() -> None:
    """Drop the index and functions; claim rows are untouched."""
    op.execute(_FUNCTIONS_DROP)
