"""Add D79 immutable structure generations and append-only skeleton checks."""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables
from rememberstack.spine.migrations._helpers import drop_types

revision: str = "p1_04_0019"
down_revision: str | None = "p1_03_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""CREATE TYPE skeleton_check_outcome AS ENUM (
  'not_run_short',
  'coherent',
  'incoherent_repeated_boilerplate',
  'incoherent_heading_sequence',
  'incoherent_junk_titles',
  'incoherent_over_fragmented',
  'provider_error',
  'invalid_response'
);
CREATE TYPE structure_route_tag AS ENUM (
  'parser',
  'parser_demoted_check',
  'fallback_density',
  'fallback_leaf',
  'fallback_after_check',
  'synthetic_after_check',
  'legacy'
);

CREATE TABLE document_skeleton_checks (
  check_id          uuid PRIMARY KEY,
  processing_id     uuid NOT NULL,             -- LOGICAL FK → processing_state; cost-attribution attempt
  deployment_id     uuid NOT NULL REFERENCES deployments,
  doc_id            uuid NOT NULL,
  version_id        uuid NOT NULL,
  representation_id uuid NOT NULL,
  candidate_skeleton_hash text NOT NULL,
  stats_version     text NOT NULL,
  stats             jsonb NOT NULL,
  sampled_input_hash text NOT NULL,
  check_outcome     skeleton_check_outcome NOT NULL,
  checker_component_version text NOT NULL,
  checker_model     text NOT NULL,
  checker_model_hash text NOT NULL,
  checker_prompt_hash text NOT NULL,
  checker_schema_hash text NOT NULL,
  provider_failure  jsonb,                     -- metadata envelope only; completion text is forbidden
  tokens_in         integer,
  tokens_out        integer,
  cost_usd          numeric(18,8),
  latency_ms        integer,
  checked_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (deployment_id, doc_id) REFERENCES documents (deployment_id, doc_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, version_id) REFERENCES document_versions (deployment_id, version_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, representation_id) REFERENCES document_representations (deployment_id, representation_id) ON DELETE CASCADE,
  CHECK (jsonb_typeof(stats) = 'object'),
  CHECK (provider_failure IS NULL OR jsonb_typeof(provider_failure) = 'object'),
  CHECK ((tokens_in IS NULL) = (tokens_out IS NULL)),
  CHECK ((tokens_in IS NULL) = (cost_usd IS NULL)),
  CHECK ((tokens_in IS NULL) = (latency_ms IS NULL))
);
COMMENT ON TABLE document_skeleton_checks IS
  'D79/D52 append-only per-document skeleton checks. Every call/non-run is auditable; provider_failure is metadata-only and never contains completion text.';
CREATE INDEX ix_skeleton_checks_representation
  ON document_skeleton_checks (representation_id, checked_at DESC);

CREATE TABLE document_structure_generations (
  structure_generation_id uuid PRIMARY KEY,
  deployment_id     uuid NOT NULL REFERENCES deployments,
  doc_id            uuid NOT NULL,
  version_id        uuid NOT NULL,
  representation_id uuid NOT NULL,
  skeleton_version  text NOT NULL,
  skeleton_hash     text NOT NULL,
  skeleton_producer_family text NOT NULL,      -- deterministic parser/synthetic = N/A (D53)
  skeleton_check_version text,                 -- Wave 1 checker slot
  roles_version     text,                      -- Wave 1 role-pass slot; null on demoted pre-role candidates
  summary_version   text,                      -- Wave 2 slot; null in Wave 1
  placement_version text,                      -- Wave 2 slot; null in Wave 1
  selecting_check_id uuid REFERENCES document_skeleton_checks,
  route_tag         structure_route_tag NOT NULL,
  candidate_skeleton_hash text NOT NULL,
  stats_version     text NOT NULL,
  stats             jsonb NOT NULL,
  pageindex_uri     text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (representation_id, structure_generation_id),
  FOREIGN KEY (deployment_id, doc_id) REFERENCES documents (deployment_id, doc_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, version_id) REFERENCES document_versions (deployment_id, version_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, representation_id) REFERENCES document_representations (deployment_id, representation_id) ON DELETE CASCADE,
  CHECK (jsonb_typeof(stats) = 'object')
);
COMMENT ON TABLE document_structure_generations IS
  'D79 immutable five-slot provenance: skeleton, skeleton_check, roles, summaries, placement. A representation points to the current generation; old trees and sidecars remain addressable.';
CREATE INDEX ix_structure_generations_representation
  ON document_structure_generations (representation_id, created_at DESC);

ALTER TABLE document_representations
  ADD COLUMN current_structure_generation_id uuid;

ALTER TABLE document_sections
  ADD COLUMN structure_generation_id uuid,
  ADD COLUMN heading_level smallint,
  ADD COLUMN normalized_title text NOT NULL DEFAULT '',
  ADD CHECK (heading_level IS NULL OR heading_level BETWEEN 1 AND 6);
"""

_BACKFILL = r"""INSERT INTO document_structure_generations (
  structure_generation_id, deployment_id, doc_id, version_id, representation_id,
  skeleton_version, skeleton_hash, skeleton_producer_family,
  skeleton_check_version, roles_version, summary_version, placement_version,
  selecting_check_id, route_tag, candidate_skeleton_hash, stats_version, stats,
  pageindex_uri
)
SELECT
  gen_random_uuid(), r.deployment_id, v.doc_id, r.version_id, r.representation_id,
  coalesce(r.structurer_version, 'legacy-e0-structure'),
  encode(digest(r.representation_id::text || '-legacy-skeleton', 'sha256'), 'hex'),
  'legacy-unknown', NULL, coalesce(r.structurer_version, 'legacy-e0-role'), NULL, NULL,
  NULL, 'legacy',
  encode(digest(r.representation_id::text || '-legacy-skeleton', 'sha256'), 'hex'),
  'legacy', jsonb_build_object(
    'stats_version', 'legacy',
    'section_count', (SELECT greatest(count(*) - 1, 0)
                      FROM document_sections s
                      WHERE s.representation_id = r.representation_id)
  ),
  r.pageindex_uri
FROM document_representations r
JOIN document_versions v ON v.version_id = r.version_id
WHERE EXISTS (
  SELECT 1 FROM document_sections s
  WHERE s.representation_id = r.representation_id
);

UPDATE document_sections s
SET structure_generation_id = g.structure_generation_id
FROM document_structure_generations g
WHERE g.representation_id = s.representation_id
  AND g.route_tag = 'legacy';

UPDATE document_representations r
SET current_structure_generation_id = g.structure_generation_id
FROM document_structure_generations g
WHERE g.representation_id = r.representation_id
  AND g.route_tag = 'legacy';
"""

_CONSTRAINTS = r"""ALTER TABLE document_sections
  ALTER COLUMN structure_generation_id SET NOT NULL;
ALTER TABLE document_sections
  DROP CONSTRAINT document_sections_version_id_node_path_key;
ALTER TABLE document_sections
  ADD CONSTRAINT uq_sections_generation_path
  UNIQUE (structure_generation_id, node_path);
ALTER TABLE document_sections
  ADD CONSTRAINT fk_sections_structure_generation
  FOREIGN KEY (structure_generation_id)
  REFERENCES document_structure_generations (structure_generation_id)
  ON DELETE CASCADE;
ALTER TABLE document_representations
  ADD CONSTRAINT fk_docreps_current_structure_generation
  FOREIGN KEY (representation_id, current_structure_generation_id)
  REFERENCES document_structure_generations
    (representation_id, structure_generation_id);
"""


def upgrade() -> None:
    """Add generations/checks and wrap every existing section tree as legacy."""
    apply_ddl(sql=_DDL)
    op.execute(_BACKFILL)
    apply_ddl(sql=_CONSTRAINTS)


def downgrade() -> None:
    """Keep current trees, then remove the D79 provenance surface."""
    op.execute(
        """
        DELETE FROM document_sections s
        USING document_representations r
        WHERE s.representation_id = r.representation_id
          AND s.structure_generation_id <> r.current_structure_generation_id
        """
    )
    op.execute(
        "ALTER TABLE document_representations"
        " DROP CONSTRAINT fk_docreps_current_structure_generation"
    )
    op.execute(
        "ALTER TABLE document_sections DROP CONSTRAINT fk_sections_structure_generation"
    )
    op.execute(
        "ALTER TABLE document_sections DROP CONSTRAINT uq_sections_generation_path"
    )
    op.execute(
        "ALTER TABLE document_sections"
        " ADD CONSTRAINT document_sections_version_id_node_path_key"
        " UNIQUE (version_id, node_path)"
    )
    op.execute(
        "ALTER TABLE document_sections DROP COLUMN normalized_title,"
        " DROP COLUMN heading_level, DROP COLUMN structure_generation_id"
    )
    op.execute(
        "ALTER TABLE document_representations"
        " DROP COLUMN current_structure_generation_id"
    )
    drop_tables(
        table_names=("document_structure_generations", "document_skeleton_checks")
    )
    drop_types(type_names=("structure_route_tag", "skeleton_check_outcome"))
