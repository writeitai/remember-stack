"""Preserve tri-state identity uncertainty and exclusion provenance (D99).

revision: p9_20_0041
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_20_0041"
down_revision: str | None = "p9_19_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Classify legacy binary exclusions and add explicit support authority."""
    op.execute(
        "CREATE TYPE resolution_exclusion_basis AS ENUM "
        "('supported_different', 'human', 'legacy_binary')"
    )
    op.execute(
        """
        ALTER TABLE resolution_exclusions
          ADD COLUMN basis resolution_exclusion_basis,
          ADD COLUMN is_effective boolean,
          ADD COLUMN source_decision_id uuid,
          ADD COLUMN source_resolver_version text,
          ADD COLUMN retired_at timestamptz,
          ADD COLUMN retired_by_decision_id uuid
        """
    )
    op.execute(
        """
        UPDATE resolution_exclusions
        SET basis = CASE
              WHEN created_by = 'human' THEN 'human'::resolution_exclusion_basis
              ELSE 'legacy_binary'::resolution_exclusion_basis
            END,
            is_effective = created_by = 'human'
        """
    )
    op.execute(
        """
        ALTER TABLE resolution_exclusions
          ALTER COLUMN basis SET NOT NULL,
          ALTER COLUMN is_effective SET NOT NULL,
          ADD CONSTRAINT ck_resolution_exclusions_basis_actor CHECK (
            (basis = 'human' AND created_by = 'human')
            OR (basis <> 'human' AND created_by = 'auto')
          ),
          ADD CONSTRAINT ck_resolution_exclusions_supported_source CHECK (
            basis <> 'supported_different'
            OR (source_decision_id IS NOT NULL
                AND source_resolver_version IS NOT NULL)
          ),
          ADD CONSTRAINT ck_resolution_exclusions_legacy_inactive CHECK (
            basis <> 'legacy_binary' OR NOT is_effective
          ),
          ADD CONSTRAINT ck_resolution_exclusions_effective_retirement CHECK (
            (is_effective AND retired_at IS NULL)
            OR (NOT is_effective
                AND (basis = 'legacy_binary' OR retired_at IS NOT NULL))
          ),
          ADD CONSTRAINT ck_resolution_exclusions_retirement CHECK (
            (retired_at IS NULL AND retired_by_decision_id IS NULL)
            OR (retired_at IS NOT NULL
                AND retired_by_decision_id IS NOT NULL
                AND NOT is_effective)
          )
        """
    )
    op.execute(
        """
        COMMENT ON TABLE resolution_exclusions IS
          'Supported-difference cannot-links (D21/D99). Clustering reads only '
          'effective human or supported_different rows; legacy binary automatic '
          'rows remain ineffective audit evidence.'
        """
    )


def downgrade() -> None:
    """Restore the preceding binary exclusion shape."""
    op.execute(
        """
        ALTER TABLE resolution_exclusions
          DROP CONSTRAINT ck_resolution_exclusions_retirement,
          DROP CONSTRAINT ck_resolution_exclusions_effective_retirement,
          DROP CONSTRAINT ck_resolution_exclusions_legacy_inactive,
          DROP CONSTRAINT ck_resolution_exclusions_supported_source,
          DROP CONSTRAINT ck_resolution_exclusions_basis_actor,
          DROP COLUMN retired_by_decision_id,
          DROP COLUMN retired_at,
          DROP COLUMN source_resolver_version,
          DROP COLUMN source_decision_id,
          DROP COLUMN is_effective,
          DROP COLUMN basis
        """
    )
    op.execute("DROP TYPE resolution_exclusion_basis")
