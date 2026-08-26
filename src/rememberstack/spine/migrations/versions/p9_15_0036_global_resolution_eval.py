"""D95/D96 global resolution evaluation and threshold provenance.

revision: p9_15_0036

The golden set is no longer keyed by an entity class. Resolver versions carry
one global threshold set, matching the one global precision/recall curve. The
upgrade preserves the old default band and deliberately discards type-specific
overrides because entity types no longer participate in resolution.

The downgrade cannot reconstruct deleted type labels. It restores the old
shape with ``Unknown`` as an explicit synthetic stratum and wraps the global
thresholds under the old ``default`` key.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_15_0036"
down_revision: str | None = "p9_14_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install the untyped golden set and global resolver thresholds."""
    op.execute("DROP INDEX ix_golden_type")
    op.execute("ALTER TABLE golden_pairs DROP COLUMN entity_type")
    op.execute(
        "ALTER TABLE resolver_versions RENAME COLUMN thresholds_by_type TO thresholds"
    )
    op.execute(
        "UPDATE resolver_versions SET thresholds = CASE"
        " WHEN jsonb_typeof(thresholds) = 'object'"
        " AND thresholds ? 'default' THEN thresholds -> 'default'"
        " ELSE thresholds END"
    )
    op.execute(
        "COMMENT ON COLUMN resolver_versions.thresholds IS"
        " 'One global set of golden-set-measured resolution decision bands"
        " (D22/D96); versioned starting points, never unmeasured constants.'"
    )
    op.execute(
        "COMMENT ON TABLE resolver_versions IS"
        " 'Versioned global resolution thresholds plus tier config and"
        " review-routing bands (D17/D22/D24/D96). Block-loose and decide-tight;"
        " thresholds are golden-set-measured starting points to be re-measured,"
        " never committed constants.'"
    )
    op.execute(
        "COMMENT ON TABLE golden_pairs IS"
        " 'Human-adjudicated ER evaluation pairs (D22/D95). Measures one global"
        " precision/recall curve plus blocking-stratum and deciding-tier"
        " diagnostics and is never used for training. Same-lemma non-matches"
        " are first-class rows; surfaces and contexts survive re-resolution.'"
    )


def downgrade() -> None:
    """Restore the pre-I.3 type-keyed shapes with explicit synthetic defaults."""
    op.execute(
        "ALTER TABLE resolver_versions RENAME COLUMN thresholds TO thresholds_by_type"
    )
    op.execute(
        "UPDATE resolver_versions SET thresholds_by_type ="
        " jsonb_build_object('default', thresholds_by_type)"
    )
    op.execute(
        "COMMENT ON COLUMN resolver_versions.thresholds_by_type IS"
        " 'Per-entity-type accept/reject bands (golden-set-measured, D22);"
        " starting points, not constants.'"
    )
    op.execute(
        "COMMENT ON TABLE resolver_versions IS"
        " 'Versioned, per-type resolution thresholds plus tier config and"
        " review-routing bands (D17/D22/D24). Block-loose and decide-tight;"
        " thresholds are golden-set-measured starting points.'"
    )
    op.execute(
        "ALTER TABLE golden_pairs"
        " ADD COLUMN entity_type text NOT NULL DEFAULT 'Unknown'"
    )
    op.execute("ALTER TABLE golden_pairs ALTER COLUMN entity_type DROP DEFAULT")
    op.execute(
        "COMMENT ON COLUMN golden_pairs.entity_type IS"
        " 'Synthetic type stratum restored by the lossy p9_15 downgrade; values"
        " removed by the global evaluation cut cannot be reconstructed.'"
    )
    op.execute(
        "COMMENT ON TABLE golden_pairs IS"
        " 'Human-adjudicated ER evaluation pairs (D22). Measures precision and"
        " recall by type stratum and is never used for training.'"
    )
    op.execute(
        "CREATE INDEX ix_golden_type ON golden_pairs (deployment_id, entity_type)"
    )
