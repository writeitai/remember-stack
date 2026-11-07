"""The managed meter measures any cost class, in the convert stage.

revision: p9_46_0067

The measurement outbox becomes the generic v3 receipt: a ``cost_class``, an
integer ``native_quantity`` in that class's ``unit`` and the ``format_family``,
in place of a text-only character count. Measurement moves from ingest into
the convert stage, so:

- uploads write their original to the raw store at ingest, and the outbox
  no longer stages bytes (``staged_content`` is dropped);
- unchanged bytes create no version and are never measured, so ``no_op``
  measurements and outcomes are deleted and the disposition column dropped;
- a version has at most one measurement;
- convert work waiting for the control plane's holds parks with the new
  ``admission`` defer reason, which no claim takes until approval releases it.

Refuses to run while any measurement still holds staged bytes: those
originals exist nowhere else. Drain the outbox with the previous release
first. The downgrade restores the text-only shape while the outbox holds
only delivered text receipts and no work is parked for admission.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_46_0067"
down_revision: str | None = "p9_45_0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UP = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM managed_ingest_measurements WHERE staged_content IS NOT NULL
  ) THEN
    RAISE EXCEPTION
      'managed_ingest_measurements holds staged originals; drain the meter '
      'outbox with the previous release before upgrading';
  END IF;
END $$;

DELETE FROM managed_ingest_measurements
WHERE document_version_disposition = 'no_op';

DROP INDEX IF EXISTS ix_managed_ingest_unaccepted_version;
DROP INDEX IF EXISTS ix_managed_ingest_measurements_terminal;
ALTER TABLE managed_ingest_measurements DROP COLUMN staged_content;
ALTER TABLE managed_ingest_measurements DROP COLUMN document_version_disposition;
ALTER TABLE managed_ingest_measurements
  RENAME COLUMN normalized_character_count TO native_quantity;
ALTER TABLE managed_ingest_measurements
  ADD COLUMN cost_class text NOT NULL DEFAULT 'doc-text',
  ADD COLUMN unit text NOT NULL DEFAULT 'normalized_character',
  ADD COLUMN format_family text NOT NULL DEFAULT 'text'
    CHECK (char_length(format_family) BETWEEN 1 AND 64);
ALTER TABLE managed_ingest_measurements
  ALTER COLUMN cost_class DROP DEFAULT,
  ALTER COLUMN unit DROP DEFAULT,
  ALTER COLUMN format_family DROP DEFAULT,
  ADD CONSTRAINT managed_ingest_measurements_class_unit CHECK (
    (cost_class = 'doc-text' AND unit = 'normalized_character') OR
    (cost_class = 'doc-scan' AND unit = 'page' AND native_quantity >= 1) OR
    (cost_class = 'image' AND unit = 'image' AND native_quantity >= 1) OR
    (cost_class IN ('audio', 'video') AND unit = 'millisecond'
      AND native_quantity >= 1)
  ),
  ADD CONSTRAINT managed_ingest_measurements_one_per_version
    UNIQUE (deployment_id, version_id);
CREATE INDEX ix_managed_ingest_measurements_terminal
  ON managed_ingest_measurements (deployment_id, accepted_at)
  WHERE delivery_state = 'accepted';

ALTER TABLE managed_ingest_outcomes
  DROP COLUMN derived_normalized_character_count,
  DROP COLUMN provider_cost_evidence_id;
DO $$
DECLARE
  constraint_name text;
BEGIN
  SELECT con.conname INTO constraint_name
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
  WHERE rel.relname = 'managed_ingest_outcomes'
    AND con.contype = 'c'
    AND pg_get_constraintdef(con.oid) LIKE '%no_op%';
  EXECUTE format(
    'ALTER TABLE managed_ingest_outcomes DROP CONSTRAINT %I', constraint_name
  );
END $$;
ALTER TABLE managed_ingest_outcomes
  ADD CONSTRAINT managed_ingest_outcomes_outcome
  CHECK (outcome IN ('succeeded', 'failed'));

COMMENT ON TABLE managed_ingest_measurements IS
  'Content-free v3 managed meter receipt outbox: one measurement per new source version, taken in the convert stage before provider work. Its convert work stays parked (defer_reason admission) until both control-plane holds are accepted.';
COMMENT ON TABLE managed_ingest_outcomes IS
  'Content-free terminal v3 receipt outbox replayed until CP acknowledgement.';

ALTER TYPE processing_defer_reason ADD VALUE IF NOT EXISTS 'admission';
ALTER TABLE processing_state
  DROP CONSTRAINT processing_state_defer_reason_check;
ALTER TABLE processing_state ADD CONSTRAINT processing_state_defer_reason_check
CHECK (
  (status = 'failed' AND defer_reason = 'retry_backoff') OR
  (status = 'pending' AND (
    defer_reason IS NULL
    OR defer_reason::text IN ('scheduled', 'budget', 'no_route', 'admission')
  )) OR
  (status NOT IN ('pending', 'failed') AND defer_reason IS NULL)
);
"""


def upgrade() -> None:
    """Generalize the outbox and add the admission parking reason."""
    op.execute(_UP)


_DOWN = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM managed_ingest_measurements WHERE cost_class <> 'doc-text'
  ) OR EXISTS (
    SELECT 1 FROM managed_ingest_measurements WHERE delivery_state <> 'accepted'
  ) OR EXISTS (
    SELECT 1 FROM processing_state WHERE defer_reason::text = 'admission'
  ) THEN
    RAISE EXCEPTION
      'p9_46_0067 downgrade refused: non-text or undelivered measurements or '
      'admission-parked work exist';
  END IF;
END $$;

ALTER TABLE processing_state
  DROP CONSTRAINT processing_state_defer_reason_check;
ALTER TABLE processing_state ADD CONSTRAINT processing_state_defer_reason_check
CHECK (
  (status = 'failed' AND defer_reason = 'retry_backoff') OR
  (status = 'pending' AND (
    defer_reason IS NULL
    OR defer_reason::text IN ('scheduled', 'budget', 'no_route')
  )) OR
  (status NOT IN ('pending', 'failed') AND defer_reason IS NULL)
);

ALTER TABLE managed_ingest_outcomes DROP CONSTRAINT managed_ingest_outcomes_outcome;
ALTER TABLE managed_ingest_outcomes
  ADD CONSTRAINT managed_ingest_outcomes_outcome
  CHECK (outcome IN ('succeeded', 'failed', 'no_op')),
  ADD COLUMN derived_normalized_character_count bigint
    CHECK (derived_normalized_character_count IS NULL
           OR derived_normalized_character_count >= 0),
  ADD COLUMN provider_cost_evidence_id text;

DROP INDEX ix_managed_ingest_measurements_terminal;
ALTER TABLE managed_ingest_measurements
  DROP CONSTRAINT managed_ingest_measurements_one_per_version,
  DROP CONSTRAINT managed_ingest_measurements_class_unit,
  DROP COLUMN cost_class,
  DROP COLUMN unit,
  DROP COLUMN format_family;
ALTER TABLE managed_ingest_measurements
  RENAME COLUMN native_quantity TO normalized_character_count;
ALTER TABLE managed_ingest_measurements
  ADD COLUMN document_version_disposition text NOT NULL DEFAULT 'new_version'
    CHECK (document_version_disposition IN ('new_version', 'no_op')),
  ADD COLUMN staged_content bytea;
ALTER TABLE managed_ingest_measurements
  ALTER COLUMN document_version_disposition DROP DEFAULT;
CREATE INDEX ix_managed_ingest_measurements_terminal
  ON managed_ingest_measurements (deployment_id, accepted_at)
  WHERE delivery_state = 'accepted' AND document_version_disposition = 'new_version';
CREATE INDEX ix_managed_ingest_unaccepted_version
  ON managed_ingest_measurements (deployment_id, version_id)
  WHERE document_version_disposition = 'new_version' AND accepted_at IS NULL;
"""


def downgrade() -> None:
    """Restore the text-only outbox while it holds only delivered text receipts.

    The ``admission`` enum value stays (PostgreSQL cannot drop one); nothing
    uses it once the constraint excludes it.
    """
    op.execute(_DOWN)
