"""Let identity-only reads skip unrequested current entity statistics.

The aggregate returns exactly one row, including zero counts, so changing its
join to LEFT preserves every published value and provenance condition. It lets
PostgreSQL remove that join when none of its count columns are requested.
"""

from collections.abc import Sequence

from rememberstack.spine.migrations._helpers import apply_view_ddl

revision: str = "p9_42_0063"
down_revision: str | None = "p9_41_0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENTITIES_DEMAND_COUNTS_DDL = r"""
CREATE OR REPLACE VIEW memory_v1.entities_current (
  deployment_id,
  entity_id,
  entity_type,          -- Vacated after D96: this compatibility output position always returns NULL because identity is the entity_id and no entity class is stored; use observation or profile fact text for kind-like retrieval.
  canonical_name,
  normalized_name,
  type_confidence,      -- Vacated after D96: this compatibility output position always returns NULL because the removed entity-class vote has no confidence value; identity decisions are recorded by resolution tier and confidence instead.
  profile_summary,
  live_mention_count,
  live_document_count,
  graph_degree,
  created_at,
  updated_at
) AS
SELECT
  e.deployment_id,
  e.entity_id,
  NULL::text,
  e.canonical_name,
  e.normalized_name,
  NULL::real,
  e.profile_summary,
  live.mention_count,
  live.document_count,
  e.graph_degree::bigint,
  e.created_at,
  e.updated_at
FROM entities AS e
LEFT JOIN LATERAL (
  SELECT coalesce(sum(edm.mention_count), 0)::bigint AS mention_count,
         count(*)::bigint AS document_count
  FROM memory_v1.entity_document_mentions AS edm
  WHERE edm.deployment_id = e.deployment_id
    AND edm.entity_id = e.entity_id
) AS live ON true
WHERE e.status = 'active'
  AND EXISTS (
    SELECT 1
    FROM (
      SELECT m.deployment_id, s.survivor_entity_id AS entity_id
      FROM mentions AS m
      JOIN chunks AS ch
        ON ch.deployment_id = m.deployment_id
       AND ch.chunk_id = m.chunk_id
       AND ch.doc_id = m.doc_id
      JOIN memory_v1.document_versions_visible AS vv
        ON vv.deployment_id = ch.deployment_id
       AND vv.version_id = ch.version_id
       AND vv.doc_id = ch.doc_id
      JOIN document_representations AS representation
        ON representation.deployment_id = ch.deployment_id
       AND representation.version_id = ch.version_id
       AND representation.representation_id = ch.representation_id
       AND representation.status = 'ready'
      CROSS JOIN LATERAL (
        SELECT rd.entity_id
        FROM resolution_decisions AS rd
        WHERE rd.deployment_id = m.deployment_id
          AND rd.mention_id = m.mention_id
          AND rd.superseded_by IS NULL
        ORDER BY rd.decided_at DESC, rd.decision_id DESC
        LIMIT 1
      ) AS decided
      JOIN v_memory_entity_survivor AS s
        ON s.deployment_id = m.deployment_id
       AND s.entity_id = decided.entity_id
      UNION ALL
      SELECT d.deployment_id, s.survivor_entity_id
      FROM documents AS d
      JOIN v_memory_entity_survivor AS s
        ON s.deployment_id = d.deployment_id
       AND s.entity_id = d.document_entity_id
      WHERE d.deleted_at IS NULL
    ) AS provenance
    WHERE provenance.deployment_id = e.deployment_id
      AND provenance.entity_id = e.entity_id
  );

COMMENT ON VIEW memory_v1.entities_current IS
  'One row per externally visible survivor entity, keyed by (deployment_id, entity_id). Membership requires SURVIVING PROVENANCE, which is an explicit association to at least one live document lineage: a mention of this survivor in any non-tombstoned version of a live lineage, or a live document-entity bridge. An entity whose every source has been forgotten is therefore absent rather than orphaned, and merged entities are absent because a merge redirects to a survivor instead of rewriting history. MEMBERSHIP AND THE COUNTS ANSWER DIFFERENT QUESTIONS, and the difference is deliberate: the two counts are exact over CURRENT content only — they equal this entity''s rows in mentions_live and entity_document_mentions — so an entity whose only mention sits in a superseded version of a live lineage is published here with both counts at zero and has no row in entity_document_mentions at all. A zero count is not an absence of provenance. graph_degree is a deprecated compatibility scalar fixed at zero after D98 because current adjacency is computed from live PostgreSQL relations; profile_summary is orientation text, never evidence; and the clocks are registry maintenance instants that carry no world-validity meaning. After D96, entity_type and type_confidence are vacated (always NULL); identity is the entity_id.';

"""


def upgrade() -> None:
    """Preserve entity membership/counts while pruning unused count work."""
    apply_view_ddl(sql=ENTITIES_DEMAND_COUNTS_DDL)


def downgrade() -> None:
    """Restore the preceding view definition without modifying stored data."""
    apply_view_ddl(
        sql=ENTITIES_DEMAND_COUNTS_DDL.replace(
            "LEFT JOIN LATERAL (", "CROSS JOIN LATERAL (", 1
        ).replace(") AS live ON true", ") AS live", 1)
    )
