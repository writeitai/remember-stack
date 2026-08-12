"""D90 entity-grain observation flush: membership + version state.

revision: p9_10_0031
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "p9_10_0031"
down_revision: str | None = "p9_09_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Membership units and version fan-out state for D90 entity flush."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS obs_flush_entity_units (
          unit_id uuid PRIMARY KEY,
          deployment_id uuid NOT NULL,
          version_id uuid NOT NULL,
          representation_id uuid NOT NULL,
          normalizer_version text NOT NULL,
          chunker_version text NOT NULL,
          extractor_version text NOT NULL,
          subject_entity_id uuid NOT NULL,
          doc_id uuid,
          content_hash text NOT NULL,
          min_asserted_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (
            deployment_id, version_id, normalizer_version, subject_entity_id
          )
        )
        """
    )
    op.execute(
        """
        COMMENT ON TABLE obs_flush_entity_units IS
          'D90: version-scoped entity flush unit; unit_id is processing_state.target_id '
          'for target_kind=entity adjudicate_observations jobs.';
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_obs_flush_units_version
        ON obs_flush_entity_units (
          deployment_id, version_id, normalizer_version
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_obs_flush_units_entity
        ON obs_flush_entity_units (deployment_id, subject_entity_id)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS obs_flush_version_state (
          deployment_id uuid NOT NULL,
          version_id uuid NOT NULL,
          normalizer_version text NOT NULL,
          representation_id uuid NOT NULL,
          chunker_version text NOT NULL,
          extractor_version text NOT NULL,
          content_hash text NOT NULL,
          fanout_status text NOT NULL,
          completed_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (deployment_id, version_id, normalizer_version),
          CHECK (
            fanout_status IN (
              'materialized', 'empty_complete', 'barrier_complete'
            )
          )
        )
        """
    )
    op.execute(
        """
        COMMENT ON TABLE obs_flush_version_state IS
          'D90: durable empty/materialized/barrier state for obs flush; never use '
          'document_version processing rows at entity-fanout component version.';
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_procstate_entity_obs_flush
        ON processing_state (
          deployment_id, stage, target_kind, component_version, status
        )
        WHERE stage = 'adjudicate_observations' AND target_kind = 'entity'
        """
    )


def downgrade() -> None:
    """Drop D90 helper objects."""
    op.execute("DROP INDEX IF EXISTS ix_procstate_entity_obs_flush")
    op.execute("DROP TABLE IF EXISTS obs_flush_version_state")
    op.execute("DROP INDEX IF EXISTS ix_obs_flush_units_entity")
    op.execute("DROP INDEX IF EXISTS ix_obs_flush_units_version")
    op.execute("DROP TABLE IF EXISTS obs_flush_entity_units")
