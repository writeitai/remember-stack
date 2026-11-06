"""Prune unrequested fact evidence counts without changing visibility or values."""

from collections.abc import Sequence

from rememberstack.spine.migrations._helpers import _split_sql
from rememberstack.spine.migrations._helpers import apply_view_ddl

revision: str = "p9_45_0066"
down_revision: str | None = "p9_44_0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SURVIVOR_DEMAND_DDL = r"""
CREATE OR REPLACE VIEW v_graph_survivor (entity_id, survivor) AS
SELECT source.entity_id, terminal.cur
FROM entities AS source
CROSS JOIN LATERAL (
  WITH RECURSIVE chain(cur, merged_into) AS (
    SELECT source.entity_id, source.merged_into
    UNION
    SELECT next.entity_id, next.merged_into
    FROM chain
    JOIN entities AS next ON next.entity_id = chain.merged_into
  )
  SELECT cur FROM chain WHERE merged_into IS NULL
) AS terminal;
"""

ENTITIES_BOUNDED_PROVENANCE_DDL = r"""
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
  AND e.merged_into IS NULL
  AND EXISTS (
    WITH RECURSIVE members(entity_id) AS (
      SELECT e.entity_id
      UNION
      SELECT redirect.entity_id
      FROM entities AS redirect
      JOIN members ON redirect.merged_into = members.entity_id
      WHERE redirect.deployment_id = e.deployment_id
    )
    SELECT 1
    FROM members
    WHERE EXISTS (
      SELECT 1 FROM documents AS document
      WHERE document.deployment_id = e.deployment_id
        AND document.document_entity_id = members.entity_id
        AND document.deleted_at IS NULL
    ) OR EXISTS (
      SELECT 1
      FROM LATERAL (
        SELECT decision.deployment_id, decision.mention_id, decision.decision_id
        FROM resolution_decisions AS decision
        WHERE decision.deployment_id = e.deployment_id
          AND decision.entity_id = members.entity_id
          AND decision.superseded_by IS NULL
        OFFSET 0
      ) AS resolved
      JOIN mentions AS mention
        ON mention.deployment_id = resolved.deployment_id
       AND mention.mention_id = resolved.mention_id
      JOIN chunks AS chunk
        ON chunk.deployment_id = mention.deployment_id
       AND chunk.chunk_id = mention.chunk_id
       AND chunk.doc_id = mention.doc_id
      JOIN memory_v1.document_versions_visible AS version
        ON version.deployment_id = chunk.deployment_id
       AND version.version_id = chunk.version_id
       AND version.doc_id = chunk.doc_id
      JOIN document_representations AS representation
        ON representation.deployment_id = chunk.deployment_id
       AND representation.version_id = chunk.version_id
       AND representation.representation_id = chunk.representation_id
       AND representation.status = 'ready'
      WHERE resolved.decision_id = (
          SELECT latest.decision_id
          FROM resolution_decisions AS latest
          WHERE latest.deployment_id = resolved.deployment_id
            AND latest.mention_id = resolved.mention_id
            AND latest.superseded_by IS NULL
          ORDER BY latest.decided_at DESC, latest.decision_id DESC
          LIMIT 1
        )
    )
  );

COMMENT ON VIEW memory_v1.entities_current IS
  'One row per externally visible survivor entity, keyed by (deployment_id, entity_id). Membership requires SURVIVING PROVENANCE, which is an explicit association to at least one live document lineage: a mention of this survivor in any non-tombstoned version of a live lineage, or a live document-entity bridge. An entity whose every source has been forgotten is therefore absent rather than orphaned, and merged entities are absent because a merge redirects to a survivor instead of rewriting history. MEMBERSHIP AND THE COUNTS ANSWER DIFFERENT QUESTIONS, and the difference is deliberate: the two counts are exact over CURRENT content only — they equal this entity''s rows in mentions_live and entity_document_mentions — so an entity whose only mention sits in a superseded version of a live lineage is published here with both counts at zero and has no row in entity_document_mentions at all. A zero count is not an absence of provenance. graph_degree is a deprecated compatibility scalar fixed at zero after D98 because current adjacency is computed from live PostgreSQL relations; profile_summary is orientation text, never evidence; and the clocks are registry maintenance instants that carry no world-validity meaning. After D96, entity_type and type_confidence are vacated (always NULL); identity is the entity_id.';

"""

FACTS_DEMAND_COUNTS_DDL = r"""
CREATE OR REPLACE VIEW memory_v1.facts_visible_history (
  deployment_id, -- The deployment that owns the fact.
  fact_kind, -- Which fact layer this row belongs to, either relation or observation.
  fact_id, -- Stable identity of the adjudicated fact.
  subject_entity_id, -- Survivor identity of the subject entity, with merge redirects resolved.
  predicate, -- The governed predicate of a relation, null for an observation.
  object_entity_id, -- Survivor identity of the object entity of a relation, null for an observation.
  statement, -- The canonical statement of an observation, null for a relation.
  fact_label, -- Human-readable sentence for the fact, null when no label has been generated.
  valid_from, -- Canonical inclusive world-time start; NULL means unknown.
  valid_until, -- Canonical exclusive world-time end; NULL means unknown unless valid_precision is open.
  ingested_at, -- Raw transaction-time start: when the system first believed the fact.
  invalidated_at, -- Raw transaction-time end: when the system learned the fact was superseded, null while it is still believed.
  contradiction_group, -- Shared identifier of an unadjudicated contradiction, null when the fact is in no contradiction group.
  confidence, -- Aggregate confidence over the fact's evidence, null when never scored.
  evidence_count_current, -- LIVE count of distinct current-testimony lineages supporting the fact, read now and never a historical reconstruction.
  contradict_count_current, -- LIVE count of distinct current-testimony lineages contradicting the fact, read now and never a historical reconstruction.
  support_state_current, -- LIVE support state, exactly current or withdrawn, derived now from the open review queue and never a stored column.
  valid_precision -- Chosen world-date precision; unknown and partial boundaries are distinct from explicitly open.
) AS
SELECT
  fact.deployment_id,
  fact.fact_kind,
  fact.fact_id,
  fact.subject_entity_id,
  fact.predicate,
  fact.object_entity_id,
  fact.statement,
  fact.fact_label,
  fact.valid_from,
  fact.valid_until,
  fact.ingested_at,
  fact.invalidated_at,
  fact.contradiction_group,
  fact.confidence,
  counts.supports,
  counts.contradicts,
  CASE WHEN EXISTS (
    SELECT 1
    FROM review_queue AS queue
    WHERE queue.deployment_id = fact.deployment_id
      AND queue.item_kind = 'support_withdrawn'
      AND queue.status IN ('pending', 'deferred')
      AND queue.candidate ->> 'fact_kind' = fact.fact_kind
      AND queue.candidate ->> 'fact_id' = fact.fact_id::text
  ) THEN 'withdrawn' ELSE 'current' END,
  fact.valid_precision
FROM (
  SELECT base.*, coalesce(relation.valid_precision, observation.valid_precision)::text AS valid_precision
  FROM v_memory_fact_visible AS base
  LEFT JOIN relations AS relation ON base.fact_kind='relation'
    AND relation.deployment_id=base.deployment_id AND relation.relation_id=base.fact_id
  LEFT JOIN observations AS observation ON base.fact_kind='observation'
    AND observation.deployment_id=base.deployment_id AND observation.observation_id=base.fact_id
) AS fact
LEFT JOIN LATERAL (
  SELECT
    count(*) FILTER (WHERE lineage.stance = 'supports')::bigint AS supports,
    count(*) FILTER (WHERE lineage.stance = 'contradicts')::bigint AS contradicts
  FROM v_memory_evidence_lineage_live AS lineage
  WHERE lineage.deployment_id = fact.deployment_id
    AND lineage.fact_kind = fact.fact_kind
    AND lineage.fact_id = fact.fact_id
) AS counts ON true;
COMMENT ON VIEW memory_v1.facts_visible_history IS 'Historically visible adjudicated facts with one canonical world window and separate system timestamps. Membership requires surviving provenance. Unknown dates and partial windows are possible temporal matches, not proof of being current; open precision explicitly means ongoing. Completed windows remain believed history while invalidated_at is NULL. Evidence counts and support state are current testimony, not reconstructed historical counts.';
"""


def upgrade() -> None:
    """Let ID-only reads skip the always-one-row evidence-count aggregate."""
    apply_view_ddl(sql=SURVIVOR_DEMAND_DDL)
    apply_view_ddl(sql=ENTITIES_BOUNDED_PROVENANCE_DDL)
    apply_view_ddl(sql=FACTS_DEMAND_COUNTS_DDL)


def downgrade() -> None:
    """Restore the preceding view shape without changing stored data."""
    from rememberstack.spine.migrations.versions.p9_04_0025_coordinate_binding import (
        MEMORY_V1_CORRECTION_DDL,
    )
    from rememberstack.spine.migrations.versions.p9_42_0063_entity_counts_demand import (
        ENTITIES_DEMAND_COUNTS_DDL,
    )

    prior_survivor = next(
        statement
        for statement in _split_sql(sql=MEMORY_V1_CORRECTION_DDL)
        if statement.startswith("CREATE OR REPLACE VIEW v_graph_survivor (")
    )
    apply_view_ddl(sql=prior_survivor)
    apply_view_ddl(sql=ENTITIES_DEMAND_COUNTS_DDL)
    apply_view_ddl(
        sql=FACTS_DEMAND_COUNTS_DDL.replace(
            "LEFT JOIN LATERAL (", "CROSS JOIN LATERAL (", 1
        ).replace(") AS counts ON true", ") AS counts", 1)
    )
