"""Store complete occurrence evidence spans for coherent multi-span claims (D119).

Populated claim stores are refused: old rows cannot satisfy the complete
occurrence-span contract, and this migration never invents multi-span
evidence or wipes a live store.

revision: p9_31_0052
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from rememberstack.spine.migrations._helpers import apply_view_ddl

revision: str = "p9_31_0052"
down_revision: str | None = "p9_30_0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLAIM_OCCURRENCES_LIVE_DDL = r"""
CREATE VIEW memory_v1.claim_occurrences_live (
  deployment_id,       -- The deployment that owns the occurrence.
  claim_id,            -- The claim carried by this chunk occurrence.
  chunk_id,            -- The current-content chunk that carries the claim.
  derivation_kind,     -- How this occurrence was derived from the source, such as passthrough, asr, or ocr; null when the reading recorded no label.
  doc_id,              -- The live lineage carrying the occurrence.
  version_id,          -- The lineage's current version carrying the occurrence.
  representation_id,   -- The current ready reading carrying the occurrence.
  section_id,          -- The section containing the carrying chunk, null when the chunk has no section in the current structure generation.
  evidence_mode,       -- How mediated this occurrence is, such as source_expression or model_observation; null when the reading recorded no mode.
  source_locators,     -- The resolved source locator set for this occurrence, null when the reading resolved none.
  attached_at,         -- When this occurrence was first recorded, which is a processing instant rather than a world-time clock.
  evidence_spans       -- The complete ordered list of supporting body ranges for this occurrence, origin first, as {char_start, char_end} objects in the carrying representation.
) AS
SELECT DISTINCT ON (cc.deployment_id, cc.claim_id, cc.chunk_id, cc.derivation_kind)
  cc.deployment_id,
  cc.claim_id,
  cc.chunk_id,
  cc.derivation_kind,
  cl.doc_id,
  cl.version_id,
  cl.representation_id,
  cl.section_id,
  cc.evidence_mode,
  cc.source_locators,
  cc.created_at,
  cc.evidence_spans
FROM chunk_claims AS cc
JOIN memory_v1.chunks_live AS cl
  ON cl.deployment_id = cc.deployment_id
 AND cl.chunk_id = cc.chunk_id
JOIN memory_v1.claims_visible_history AS ch
  ON ch.deployment_id = cc.deployment_id
 AND ch.claim_id = cc.claim_id
 AND ch.doc_id = cl.doc_id
ORDER BY cc.deployment_id, cc.claim_id, cc.chunk_id, cc.derivation_kind,
         cc.created_at, cc.evidence_mode;
COMMENT ON VIEW memory_v1.claim_occurrences_live IS
  'One row per current claim occurrence, keyed by (deployment_id, claim_id, chunk_id, derivation_kind) with null derivation kinds treated as equal, and joined to claims_visible_history on (deployment_id, claim_id) and to chunks_live on (deployment_id, chunk_id). It is the explicit association answering which current chunk, version, representation, and section carry a claim, and evidence_spans is the complete body support for that occurrence. Repeated attachments collapse to the earliest, so attached_at is the first time the occurrence was recorded. Occurrences in superseded versions, non-ready readings, and forgotten lineages are absent. Claim char_start/char_end remain the immutable origin; they are not the complete evidence list. The view carries no counts and no validity clocks.';
"""


def upgrade() -> None:
    """Refuse populated claim stores, then require occurrence evidence spans."""
    connection = op.get_bind()
    op.execute("LOCK TABLE claims, chunk_claims IN ACCESS EXCLUSIVE MODE")
    if connection.execute(text("SELECT EXISTS(SELECT 1 FROM claims)")).scalar_one():
        raise RuntimeError(
            "D119 does not convert a store that already holds claims; "
            "recreate the deployment and ingest its sources again"
        )
    if connection.execute(
        text("SELECT EXISTS(SELECT 1 FROM chunk_claims)")
    ).scalar_one():
        raise RuntimeError(
            "D119 does not convert a store that already holds claim occurrences; "
            "recreate the deployment and ingest its sources again"
        )
    op.execute("ALTER TABLE chunk_claims ADD COLUMN evidence_spans jsonb NOT NULL")
    op.execute(
        """
        CREATE FUNCTION chunk_claims_evidence_spans_ok(payload jsonb)
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog, pg_temp
        AS $$
          SELECT CASE
            WHEN jsonb_typeof(payload) IS DISTINCT FROM 'array' THEN false
            WHEN jsonb_array_length(payload) NOT BETWEEN 1 AND 8 THEN false
            WHEN EXISTS (
              SELECT 1
              FROM jsonb_array_elements(payload) AS elem
              WHERE CASE
                WHEN jsonb_typeof(elem) IS DISTINCT FROM 'object' THEN true
                WHEN NOT (elem ? 'char_start') OR NOT (elem ? 'char_end') THEN true
                WHEN jsonb_typeof(elem->'char_start') IS DISTINCT FROM 'number' THEN true
                WHEN jsonb_typeof(elem->'char_end') IS DISTINCT FROM 'number' THEN true
                WHEN (elem->>'char_start') ~ '\\.' OR (elem->>'char_end') ~ '\\.' THEN true
                WHEN (elem->>'char_start')::bigint < 0 THEN true
                WHEN (elem->>'char_end')::bigint <= (elem->>'char_start')::bigint THEN true
                ELSE false
              END
            ) THEN false
            ELSE true
          END
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION chunk_claims_evidence_spans_ok(jsonb) FROM PUBLIC"
    )
    op.execute(
        """
        ALTER TABLE chunk_claims
          ADD CONSTRAINT chunk_claims_evidence_spans_shape
          CHECK (chunk_claims_evidence_spans_ok(evidence_spans))
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN chunk_claims.evidence_spans IS
          'Complete supporting body ranges for this occurrence, origin first: JSON array of {char_start, char_end} in the owning chunk''s representation (D119).'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE chunk_claims IS
          'Claim occurrences per version-chunk (F4) + occurrence-grain provenance (D65) + complete evidence spans (D119): fresh extraction AND reuse both link here, so one immutable claim attaches to every version-chunk that carries it, each attachment carrying remapped body ranges, derivation labels, and locators. claims.char_start/char_end remain the immutable origin only. Monthly-partitioned; logical FKs (D23).'
        """
    )
    op.execute("DROP VIEW memory_v1.claim_occurrences_live")
    apply_view_ddl(sql=CLAIM_OCCURRENCES_LIVE_DDL)
    op.execute(
        "ALTER VIEW memory_v1.claim_occurrences_live OWNER TO rememberstack_view_owner"
    )
    op.execute(
        """
        DO $do$
        DECLARE
          query_role text := 'rememberstack_query_' || current_database();
        BEGIN
          EXECUTE format(
            'GRANT SELECT ON memory_v1.claim_occurrences_live TO %I',
            query_role
          );
        END
        $do$;
        """
    )


def downgrade() -> None:
    """Refuse a lossy drop of occurrence evidence spans."""
    raise RuntimeError(
        "D119 downgrade requires an explicitly reviewed restore/conversion plan"
    )
