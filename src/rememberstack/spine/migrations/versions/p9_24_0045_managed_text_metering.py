"""Durable managed doc-text receipt outbox and pre-dispatch admission state.

revision: p9_24_0045
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_24_0045"
down_revision: str | None = "p9_23_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UP = """
CREATE SEQUENCE managed_version_commit_sequence_seq AS bigint START WITH 1;

CREATE TABLE managed_ingest_measurements (
  measurement_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES deployments(deployment_id) ON DELETE RESTRICT,
  doc_id uuid NOT NULL,
  version_id uuid NOT NULL,
  ingest_attempt_id text NOT NULL CHECK (char_length(ingest_attempt_id) BETWEEN 1 AND 128),
  org_id uuid NOT NULL,
  project_id uuid NOT NULL,
  opaque_lineage_id text NOT NULL CHECK (char_length(opaque_lineage_id) BETWEEN 1 AND 128),
  opaque_source_version_id text NOT NULL CHECK (char_length(opaque_source_version_id) BETWEEN 1 AND 128),
  normalized_character_count bigint NOT NULL CHECK (normalized_character_count >= 0),
  canonical_source_bytes bigint NOT NULL CHECK (canonical_source_bytes >= 0),
  document_version_disposition text NOT NULL
    CHECK (document_version_disposition IN ('new_version', 'no_op')),
  classifier_version text NOT NULL CHECK (char_length(classifier_version) BETWEEN 1 AND 128),
  measurement_algorithm_version text NOT NULL
    CHECK (char_length(measurement_algorithm_version) BETWEEN 1 AND 128),
  processing_profile_id text NOT NULL
    CHECK (char_length(processing_profile_id) BETWEEN 1 AND 128),
  measured_at timestamptz NOT NULL,
  convert_component_version text NOT NULL,
  lane processing_lane NOT NULL,
  staged_content bytea,
  delivery_state text NOT NULL DEFAULT 'pending'
    CHECK (delivery_state IN ('pending', 'parked', 'quarantined', 'accepted')),
  decision_reason text CHECK (decision_reason IS NULL OR char_length(decision_reason) <= 64),
  processing_hold_id uuid,
  storage_growth_hold_id uuid,
  delivery_attempts integer NOT NULL DEFAULT 0 CHECK (delivery_attempts >= 0),
  last_attempt_at timestamptz,
  next_attempt_at timestamptz,
  accepted_at timestamptz,
  outcome_check_attempts integer NOT NULL DEFAULT 0 CHECK (outcome_check_attempts >= 0),
  outcome_last_checked_at timestamptz,
  outcome_next_attempt_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (deployment_id, opaque_lineage_id, opaque_source_version_id),
  FOREIGN KEY (deployment_id, doc_id, version_id)
    REFERENCES document_versions(deployment_id, doc_id, version_id) ON DELETE RESTRICT,
  CHECK (
    (delivery_state = 'accepted' AND accepted_at IS NOT NULL) OR
    (delivery_state <> 'accepted' AND accepted_at IS NULL)
  ),
  CHECK (
    (document_version_disposition = 'new_version') OR
    (processing_hold_id IS NULL AND storage_growth_hold_id IS NULL)
  ),
  CHECK (
    (document_version_disposition = 'new_version' AND
      ((delivery_state = 'accepted' AND staged_content IS NULL) OR
       (delivery_state <> 'accepted' AND staged_content IS NOT NULL))) OR
    (document_version_disposition = 'no_op' AND staged_content IS NULL)
  )
);
CREATE INDEX ix_managed_ingest_measurements_delivery
  ON managed_ingest_measurements (delivery_state, next_attempt_at, created_at)
  WHERE delivery_state IN ('pending', 'parked');
CREATE INDEX ix_managed_ingest_measurements_terminal
  ON managed_ingest_measurements (deployment_id, accepted_at)
  WHERE delivery_state = 'accepted' AND document_version_disposition = 'new_version';

CREATE TABLE managed_ingest_outcomes (
  measurement_id uuid PRIMARY KEY
    REFERENCES managed_ingest_measurements(measurement_id) ON DELETE CASCADE,
  document_version_id text,
  outcome text NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'no_op')),
  completed_at timestamptz NOT NULL,
  reason_code text CHECK (reason_code IS NULL OR char_length(reason_code) <= 64),
  profile_complete boolean NOT NULL,
  version_commit_sequence bigint UNIQUE,
  derived_normalized_character_count bigint
    CHECK (derived_normalized_character_count IS NULL OR derived_normalized_character_count >= 0),
  provider_cost_evidence_id text,
  delivery_state text NOT NULL DEFAULT 'pending'
    CHECK (delivery_state IN ('pending', 'accepted')),
  delivery_attempts integer NOT NULL DEFAULT 0 CHECK (delivery_attempts >= 0),
  last_attempt_at timestamptz,
  next_attempt_at timestamptz,
  accepted_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  CHECK (
    (outcome <> 'succeeded') OR
    (document_version_id IS NOT NULL AND profile_complete AND version_commit_sequence IS NOT NULL)
  ),
  CHECK (
    (delivery_state = 'accepted' AND accepted_at IS NOT NULL) OR
    (delivery_state <> 'accepted' AND accepted_at IS NULL)
  )
);
CREATE INDEX ix_managed_ingest_outcomes_delivery
  ON managed_ingest_outcomes (delivery_state, next_attempt_at, created_at)
  WHERE delivery_state <> 'accepted';

COMMENT ON TABLE managed_ingest_measurements IS
  'Content-free v2 managed ingest receipt outbox. New versions remain status=ingesting and have no convert work until both CP holds are accepted.';
COMMENT ON TABLE managed_ingest_outcomes IS
  'Content-free terminal v2 receipt outbox replayed until CP acknowledgement.';
"""

_DOWN = """
DROP TABLE managed_ingest_outcomes;
DROP TABLE managed_ingest_measurements;
DROP SEQUENCE managed_version_commit_sequence_seq;
"""


def upgrade() -> None:
    """Create immutable receipt payloads with mutable delivery projections."""
    op.execute(_UP)


def downgrade() -> None:
    """Remove the unshipped local managed-receipt state."""
    op.execute(_DOWN)
