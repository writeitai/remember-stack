"""Materialize graph entity provenance once per SQL/PGQ view expansion.

revision: p9_18_0039
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_18_0039"
down_revision: str | None = "p9_17_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_MATERIALIZED_ENTITY_VIEW = r"""
CREATE OR REPLACE VIEW rememberstack_graph_internal.entities_live AS
WITH provenance AS MATERIALIZED (
  SELECT mention.deployment_id, survivor.survivor_entity_id AS entity_id
  FROM mentions AS mention
  JOIN chunks AS chunk
    ON chunk.deployment_id = mention.deployment_id
   AND chunk.chunk_id = mention.chunk_id
  JOIN document_versions AS version
    ON version.deployment_id = chunk.deployment_id
   AND version.version_id = chunk.version_id
   AND version.doc_id = mention.doc_id
   AND version.deleted_at IS NULL
  JOIN documents AS document
    ON document.deployment_id = version.deployment_id
   AND document.doc_id = version.doc_id
   AND document.deleted_at IS NULL
  CROSS JOIN LATERAL (
    SELECT decision.entity_id
    FROM resolution_decisions AS decision
    WHERE decision.deployment_id = mention.deployment_id
      AND decision.mention_id = mention.mention_id
      AND decision.superseded_by IS NULL
    ORDER BY decision.decided_at DESC, decision.decision_id DESC
    LIMIT 1
  ) AS decided
  JOIN v_memory_entity_survivor AS survivor
    ON survivor.deployment_id = mention.deployment_id
   AND survivor.entity_id = decided.entity_id
  UNION ALL
  SELECT document.deployment_id, survivor.survivor_entity_id
  FROM documents AS document
  JOIN v_memory_entity_survivor AS survivor
    ON survivor.deployment_id = document.deployment_id
   AND survivor.entity_id = document.document_entity_id
  WHERE document.deleted_at IS NULL
)
SELECT e.deployment_id, e.entity_id, e.canonical_name, e.profile_summary
FROM entities AS e
WHERE e.status = 'active'
  AND EXISTS (
    SELECT 1
    FROM provenance
    WHERE provenance.deployment_id = e.deployment_id
      AND provenance.entity_id = e.entity_id
  )
"""


_CORRELATED_ENTITY_VIEW = r"""
CREATE OR REPLACE VIEW rememberstack_graph_internal.entities_live AS
SELECT e.deployment_id, e.entity_id, e.canonical_name, e.profile_summary
FROM entities AS e
WHERE e.status = 'active'
  AND EXISTS (
    SELECT 1
    FROM (
      SELECT mention.deployment_id, survivor.survivor_entity_id AS entity_id
      FROM mentions AS mention
      JOIN chunks AS chunk
        ON chunk.deployment_id = mention.deployment_id
       AND chunk.chunk_id = mention.chunk_id
      JOIN document_versions AS version
        ON version.deployment_id = chunk.deployment_id
       AND version.version_id = chunk.version_id
       AND version.doc_id = mention.doc_id
       AND version.deleted_at IS NULL
      JOIN documents AS document
        ON document.deployment_id = version.deployment_id
       AND document.doc_id = version.doc_id
       AND document.deleted_at IS NULL
      CROSS JOIN LATERAL (
        SELECT decision.entity_id
        FROM resolution_decisions AS decision
        WHERE decision.deployment_id = mention.deployment_id
          AND decision.mention_id = mention.mention_id
          AND decision.superseded_by IS NULL
        ORDER BY decision.decided_at DESC, decision.decision_id DESC
        LIMIT 1
      ) AS decided
      JOIN v_memory_entity_survivor AS survivor
        ON survivor.deployment_id = mention.deployment_id
       AND survivor.entity_id = decided.entity_id
      UNION ALL
      SELECT document.deployment_id, survivor.survivor_entity_id
      FROM documents AS document
      JOIN v_memory_entity_survivor AS survivor
        ON survivor.deployment_id = document.deployment_id
       AND survivor.entity_id = document.document_entity_id
      WHERE document.deleted_at IS NULL
    ) AS provenance
    WHERE provenance.deployment_id = e.deployment_id
      AND provenance.entity_id = e.entity_id
  )
"""


def upgrade() -> None:
    """Prevent repeated correlated provenance expansion in one-hop PGQ plans."""
    op.execute(_MATERIALIZED_ENTITY_VIEW)


def downgrade() -> None:
    """Restore the equivalent pre-optimization correlated view shape."""
    op.execute(_CORRELATED_ENTITY_VIEW)
