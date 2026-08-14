"""Add the PostgreSQL-native P1 search projection (D94).

revision: p9_13_0034
"""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables

revision: str = "p9_13_0034"
down_revision: str | None = "p9_12_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""ALTER TABLE claims
  ADD COLUMN embedding vector(1536),
  ADD COLUMN embedding_model text,
  ADD COLUMN embedding_input_policy_version text,
  ADD COLUMN embedding_text_hash text,
  ADD CONSTRAINT ck_claims_embedding_attestation CHECK (
    num_nonnulls(embedding, embedding_model, embedding_input_policy_version, embedding_text_hash)
    IN (0, 4)
  );

ALTER TABLE relations
  ADD COLUMN embedding vector(1536),
  ADD COLUMN embedding_model text,
  ADD COLUMN embedding_input_policy_version text,
  ADD COLUMN embedding_text_hash text,
  ADD CONSTRAINT ck_relations_embedding_attestation CHECK (
    num_nonnulls(embedding, embedding_model, embedding_input_policy_version, embedding_text_hash)
    IN (0, 4) AND (embedding IS NULL OR fact_label IS NOT NULL)
  );

ALTER TABLE observations
  ADD COLUMN embedding vector(1536),
  ADD COLUMN embedding_model text,
  ADD COLUMN embedding_input_policy_version text,
  ADD COLUMN embedding_text_hash text,
  ADD CONSTRAINT ck_observations_embedding_attestation CHECK (
    num_nonnulls(embedding, embedding_model, embedding_input_policy_version, embedding_text_hash)
    IN (0, 4)
  );

ALTER TABLE entities
  ADD COLUMN embedding vector(1536),
  ADD COLUMN embedding_model text,
  ADD COLUMN embedding_input_policy_version text,
  ADD COLUMN embedding_text_hash text,
  ADD CONSTRAINT ck_entities_embedding_attestation CHECK (
    num_nonnulls(embedding, embedding_model, embedding_input_policy_version, embedding_text_hash)
    IN (0, 4)
  );

COMMENT ON COLUMN claims.embedding IS
  'D94 disposable semantic vector for claim_text; never testimony or fact authority.';
COMMENT ON COLUMN relations.embedding IS
  'D94 disposable semantic vector for fact_label; never fact authority.';
COMMENT ON COLUMN observations.embedding IS
  'D94 disposable semantic vector for the current observation search text; never fact authority.';
COMMENT ON COLUMN entities.embedding IS
  'D94 disposable semantic vector for the canonical entity profile text; never identity authority.';

CREATE TABLE chunk_search (
  deployment_id uuid NOT NULL REFERENCES deployments ON DELETE CASCADE,
  chunk_id uuid NOT NULL,
  search_text text NOT NULL,
  embedding vector(1536),
  embedding_model text,
  embedding_input_policy_version text,
  embedding_text_hash text,
  PRIMARY KEY (deployment_id, chunk_id),
  CHECK (btrim(search_text) <> ''),
  CHECK (
    num_nonnulls(embedding, embedding_model, embedding_input_policy_version, embedding_text_hash)
    IN (0, 4)
  )
);
COMMENT ON TABLE chunk_search IS
  'D94 private P1 sidecar: one normalized searchable body and one disposable current embedding per admitted chunk. Authority remains chunks plus its document/version visibility joins.';

CREATE TABLE p1_search_channels (
  deployment_id uuid NOT NULL REFERENCES deployments ON DELETE CASCADE,
  target text NOT NULL,
  channel text NOT NULL,
  embedding_model text,
  embedding_dimension integer,
  embedding_input_policy_version text,
  text_config text,
  ready boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, target, channel),
  CHECK (target IN ('chunks', 'claims', 'relations', 'observations', 'entities')),
  CHECK (channel IN ('semantic', 'bm25')),
  CHECK (channel <> 'bm25' OR target IN ('chunks', 'claims')),
  CHECK (
    (channel = 'semantic'
      AND embedding_model IS NOT NULL
      AND embedding_dimension = 1536
      AND embedding_input_policy_version IS NOT NULL
      AND text_config IS NULL)
    OR
    (channel = 'bm25'
      AND embedding_model IS NULL
      AND embedding_dimension IS NULL
      AND embedding_input_policy_version IS NULL
      AND text_config = 'simple')
  )
);
COMMENT ON TABLE p1_search_channels IS
  'D94 current readiness/configuration authority, one row per deployment, target, and admitted channel; not generation history and never a search-record mirror.';

CREATE INDEX ix_chunk_search_embedding_hnsw
  ON chunk_search USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_claims_current_embedding_hnsw
  ON claims USING hnsw (embedding vector_cosine_ops)
  WHERE is_current_testimony;
CREATE INDEX ix_relations_embedding_hnsw
  ON relations USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_observations_embedding_hnsw
  ON observations USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_entities_embedding_hnsw
  ON entities USING hnsw (embedding vector_cosine_ops);

CREATE INDEX ix_chunk_search_bm25
  ON chunk_search USING bm25 (search_text) WITH (text_config='simple');
CREATE INDEX ix_claims_current_bm25
  ON claims USING bm25 (claim_text) WITH (text_config='simple')
  WHERE is_current_testimony;
"""

_DROP_NATURAL_INDEXES = (
    "ix_claims_current_bm25",
    "ix_entities_embedding_hnsw",
    "ix_observations_embedding_hnsw",
    "ix_relations_embedding_hnsw",
    "ix_claims_current_embedding_hnsw",
)
_EMBEDDED_TABLES = ("entities", "observations", "relations", "claims")
_TABLES = ("p1_search_channels", "chunk_search")


def upgrade() -> None:
    """Create D94 search state, readiness rows, and native search indexes."""
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Remove only the D94 projection introduced by this revision."""
    for index_name in _DROP_NATURAL_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
    for table_name in _EMBEDDED_TABLES:
        op.execute(
            f"""ALTER TABLE {table_name}
              DROP CONSTRAINT IF EXISTS ck_{table_name}_embedding_attestation,
              DROP COLUMN IF EXISTS embedding,
              DROP COLUMN IF EXISTS embedding_model,
              DROP COLUMN IF EXISTS embedding_input_policy_version,
              DROP COLUMN IF EXISTS embedding_text_hash"""
        )
    drop_tables(table_names=_TABLES)
