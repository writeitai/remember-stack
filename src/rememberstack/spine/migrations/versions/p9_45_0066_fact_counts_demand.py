"""Prune unrequested fact evidence counts without changing visibility or values."""

from collections.abc import Sequence

from rememberstack.spine.migrations._helpers import apply_view_ddl

revision: str = "p9_45_0066"
down_revision: str | None = "p9_44_0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
    apply_view_ddl(sql=FACTS_DEMAND_COUNTS_DDL)


def downgrade() -> None:
    """Restore the preceding view shape without changing stored data."""
    apply_view_ddl(
        sql=FACTS_DEMAND_COUNTS_DDL.replace(
            "LEFT JOIN LATERAL (", "CROSS JOIN LATERAL (", 1
        ).replace(") AS counts ON true", ") AS counts", 1)
    )
