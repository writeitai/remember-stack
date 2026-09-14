"""D122 frozen Selection results and D123 application context bindings.

A populated store is not converted. Existing claims and frozen extraction
outputs cannot satisfy the new Selection-result and context-binding
generations; recreate the deployment and ingest sources again.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables

revision: str = "p9_32_0053"
down_revision: str | None = "p9_31_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""
ALTER TABLE chunks ADD COLUMN claimify_input_hash text;
CREATE INDEX ix_chunks_claimify_reuse ON chunks (deployment_id,doc_id,claimify_input_hash);
CREATE TABLE selection_results (
  deployment_id uuid NOT NULL,
  chunk_id uuid NOT NULL,
  representation_id uuid NOT NULL,
  extractor_version text NOT NULL,
  selection_input_hash text NOT NULL,
  output jsonb NOT NULL CHECK (jsonb_typeof(output) = 'object'),
  cards jsonb NOT NULL CHECK (jsonb_typeof(cards) = 'array'),
  truncated boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, chunk_id, extractor_version),
  FOREIGN KEY (deployment_id) REFERENCES deployments (deployment_id)
);
COMMENT ON TABLE selection_results IS
  'D122 frozen Selection responses, including zero-proposition and zero-card results. Cards are source-owned and remapped per consuming chunk occurrence.';

CREATE INDEX ix_selection_results_reuse
  ON selection_results (deployment_id, selection_input_hash);
CREATE INDEX ix_selection_results_representation
  ON selection_results (deployment_id, representation_id, extractor_version);

CREATE TABLE application_context_bindings (
  deployment_id uuid NOT NULL,
  application_id uuid NOT NULL,
  ordinal integer NOT NULL CHECK (ordinal >= 0),
  entity_id uuid NOT NULL,
  resolver_decision_id uuid NOT NULL,
  PRIMARY KEY (deployment_id, application_id, ordinal),
  UNIQUE (deployment_id, application_id, entity_id),
  FOREIGN KEY (deployment_id, application_id)
    REFERENCES fact_applications (deployment_id, application_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, entity_id)
    REFERENCES entities (deployment_id, entity_id)
);
COMMENT ON TABLE application_context_bindings IS
  'D123 immutable source-owned application-to-entity context bindings with stable original ordinals. An application row is the complete (possibly empty) set.';

CREATE INDEX ix_application_context_entity
  ON application_context_bindings (deployment_id, entity_id, application_id);
"""


def upgrade() -> None:
    """Refuse a populated store, then add the Selection store and context junction."""
    connection = op.get_bind()
    if connection.execute(
        text("SELECT EXISTS(SELECT 1 FROM claims) OR EXISTS(SELECT 1 FROM chunks)")
    ).scalar_one():
        raise RuntimeError(
            "D122/D123 do not convert a store that already holds claims or chunks; "
            "recreate the deployment and ingest its sources again"
        )
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Drop additive tables."""
    op.execute("ALTER TABLE chunks DROP COLUMN claimify_input_hash")
    drop_tables(table_names=("application_context_bindings", "selection_results"))
