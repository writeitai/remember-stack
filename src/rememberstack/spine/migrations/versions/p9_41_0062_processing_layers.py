"""D143: processing layers that a deployment can switch off.

Two tables:

- ``deployment_processing_layers``: one row per deployment once ``setup`` has
  written a non-default layer configuration. No row means both the claims and
  the facts layer are enabled, so every existing deployment keeps today's
  behaviour without a data migration. The check keeps facts impossible without
  claims.
- ``processing_layer_gaps``: one row per live version and layer that has not
  entered the layer yet. Boundaries write it; the lineage-ordered advance
  stamps ``entered_at``; a living lineage's advance stamps ``discharged_at`` on
  superseded versions; reaching the layer's frontier deletes it. Coverage is
  derived from it and never stored. Like ``obs_flush_version_state`` it carries
  no foreign key: deletion and hard forget delete the rows explicitly.

It also adds ``connector_sync_cycles.layers_reopened_at``: entering a layer
for a version whose connector cycle already finalized reopens that cycle, so
its zero-support closure is evaluated again once the resumed work is done; the
stamp gives the repeated finalization its own reconciliation id.

All of it is additive; the downgrade drops it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_41_0062"
down_revision: str | None = "p9_40_0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE = """
CREATE TABLE deployment_processing_layers (
  deployment_id  uuid PRIMARY KEY REFERENCES deployments ON DELETE CASCADE,
  claims_enabled boolean NOT NULL,
  facts_enabled  boolean NOT NULL,
  changed_at     timestamptz NOT NULL,
  CHECK (claims_enabled OR NOT facts_enabled)
);
COMMENT ON TABLE deployment_processing_layers IS
  'D143: the deployment''s enabled processing layers, written only by setup under the layer fence. No row means claims and facts are both enabled; once written the row stays.';

CREATE TABLE processing_layer_gaps (
  deployment_id uuid NOT NULL,
  version_id    uuid NOT NULL,
  doc_id        uuid NOT NULL,
  layer         text NOT NULL CHECK (layer IN ('claims', 'facts')),
  lane          processing_lane NOT NULL,
  recorded_at   timestamptz NOT NULL DEFAULT now(),
  entered_at    timestamptz,
  discharged_at timestamptz,
  PRIMARY KEY (deployment_id, version_id, layer),
  CHECK (entered_at IS NULL OR discharged_at IS NULL)
);
COMMENT ON TABLE processing_layer_gaps IS
  'D143: a live version that has not entered a processing layer. Boundaries insert it, advance stamps entered_at (or discharged_at for superseded living versions), reaching the layer frontier deletes it; deletion and hard forget delete it explicitly. A layer is backfilling while it is enabled and an open gap of a live version exists.';
CREATE INDEX ix_layer_gaps_open
  ON processing_layer_gaps (deployment_id, layer, doc_id)
  WHERE discharged_at IS NULL;

ALTER TABLE connector_sync_cycles ADD COLUMN layers_reopened_at timestamptz;
COMMENT ON COLUMN connector_sync_cycles.layers_reopened_at IS
  'D143: when a layer entry last reopened this finalized cycle (finalized_at cleared) so its closure runs again after the resumed work; NULL when never reopened.';
"""

_DOWNGRADE = """
ALTER TABLE connector_sync_cycles DROP COLUMN layers_reopened_at;
DROP TABLE processing_layer_gaps;
DROP TABLE deployment_processing_layers;
"""


def upgrade() -> None:
    """Create the layer configuration and gap tables."""
    op.execute(_UPGRADE)


def downgrade() -> None:
    """Drop both tables."""
    op.execute(_DOWNGRADE)
