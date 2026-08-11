"""D88 claim-level normalize fan-out: observation staging + claim work index.

revision: p9_08_0029
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "p9_08_0029"
down_revision: str | None = "p9_07_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Stage observations until post-barrier ordered D43 flush; index claim normalize."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS normalize_observation_staging (
          deployment_id uuid NOT NULL,
          version_id uuid NOT NULL,
          claim_id uuid NOT NULL,
          subject_entity_id uuid NOT NULL,
          statement text NOT NULL,
          doc_id uuid NOT NULL,
          normalizer_version text NOT NULL,
          staged_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (
            deployment_id, version_id, claim_id, subject_entity_id,
            statement, normalizer_version
          )
        )
        """
    )
    op.execute(
        """
        COMMENT ON TABLE normalize_observation_staging IS
          'D88: claim-grain normalize stages observations until the version barrier '
          'fires an ordered D43 flush (asserted_at, claim_id). Not a public fact table.';
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_normalize_obs_staging_version
        ON normalize_observation_staging (deployment_id, version_id, normalizer_version)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_procstate_claim_normalize
        ON processing_state (
          deployment_id, stage, target_kind, component_version, status
        )
        WHERE stage = 'normalize_relations' AND target_kind = 'claim'
        """
    )


def downgrade() -> None:
    """Drop D88 helper objects."""
    op.execute("DROP INDEX IF EXISTS ix_procstate_claim_normalize")
    op.execute("DROP INDEX IF EXISTS ix_normalize_obs_staging_version")
    op.execute("DROP TABLE IF EXISTS normalize_observation_staging")
