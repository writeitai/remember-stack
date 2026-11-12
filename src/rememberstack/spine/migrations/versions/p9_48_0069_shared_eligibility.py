"""D153 constrained identity provenance and shared fact source authority."""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from rememberstack.spine.migrations._helpers import apply_view_ddl

revision: str = "p9_48_0069"
down_revision: str | None = "p9_47_0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

D153_HELPER_VIEWS: tuple[str, ...] = (
    "v_memory_claim_carried_periodised",
    "v_memory_fact_source_qualified",
)

ENTITIES_CONSTRAINED_PROVENANCE_DDL = r"""
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
      -- Only the bridge expression index supplies this fixed byte order.
      -- UUID-key ordering can select a deployment-wide scan under skew.
      -- OFFSET prevents EXISTS simplification from dropping ORDER BY/LIMIT.
      ORDER BY document.doc_id::text COLLATE pg_catalog."C"
      LIMIT 1 OFFSET 0
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
      CROSS JOIN LATERAL (
        SELECT latest.decision_id
        FROM resolution_decisions AS latest
        WHERE latest.deployment_id = resolved.deployment_id
          AND latest.mention_id = resolved.mention_id
          AND latest.superseded_by IS NULL
        ORDER BY latest.decided_at DESC, latest.decision_id DESC
        LIMIT 1
      ) AS latest
      CROSS JOIN LATERAL (
        SELECT mention.deployment_id, mention.doc_id, mention.chunk_id
        FROM mentions AS mention
        WHERE mention.deployment_id = resolved.deployment_id
          AND mention.mention_id = resolved.mention_id
          AND latest.decision_id = resolved.decision_id
        OFFSET 0
      ) AS mention
      CROSS JOIN LATERAL (
        SELECT chunk.deployment_id, chunk.doc_id, chunk.version_id,
               chunk.representation_id
        FROM chunks AS chunk
        WHERE chunk.deployment_id = mention.deployment_id
          AND chunk.chunk_id = mention.chunk_id
          AND chunk.doc_id = mention.doc_id
        OFFSET 0
      ) AS chunk
      CROSS JOIN LATERAL (
        SELECT version.version_id
        FROM memory_v1.document_versions_visible AS version
        WHERE version.deployment_id = chunk.deployment_id
          AND version.version_id = chunk.version_id
          AND version.doc_id = chunk.doc_id
        OFFSET 0
      ) AS version
      CROSS JOIN LATERAL (
        SELECT representation.representation_id
        FROM document_representations AS representation
        WHERE representation.deployment_id = chunk.deployment_id
          AND representation.version_id = version.version_id
          AND representation.representation_id = chunk.representation_id
          AND representation.status = 'ready'
        OFFSET 0
      ) AS representation
    )
  );

COMMENT ON VIEW memory_v1.entities_current IS
  'One row per externally visible survivor entity, keyed by (deployment_id, entity_id). Membership requires SURVIVING PROVENANCE, which is an explicit association to at least one live document lineage: a mention of this survivor in any non-tombstoned version of a live lineage, or a live document-entity bridge. An entity whose every source has been forgotten is therefore absent rather than orphaned, and merged entities are absent because a merge redirects to a survivor instead of rewriting history. MEMBERSHIP AND THE COUNTS ANSWER DIFFERENT QUESTIONS, and the difference is deliberate: the two counts are exact over CURRENT content only — they equal this entity''s rows in mentions_live and entity_document_mentions — so an entity whose only mention sits in a superseded version of a live lineage is published here with both counts at zero and has no row in entity_document_mentions at all. A zero count is not an absence of provenance. graph_degree is a deprecated compatibility scalar fixed at zero after D98 because current adjacency is computed from live PostgreSQL relations; profile_summary is orientation text, never evidence; and the clocks are registry maintenance instants that carry no world-validity meaning. After D96, entity_type and type_confidence are vacated (always NULL); identity is the entity_id.';
"""

CARRIED_CLAIMS_DDL = r"""
CREATE OR REPLACE VIEW v_memory_claim_carried_periodised (
  deployment_id,
  claim_id,
  doc_id,
  source_kind,
  source_handle,
  asserted_at,
  claim_valid_from,
  claim_valid_until,
  claim_valid_precision,
  claim_valid_kind
) AS
SELECT
  c.deployment_id,
  c.claim_id,
  c.doc_id,
  dl.source_kind,
  dl.source_kind || ':' || coalesce(dl.source_ref, dl.doc_id::text),
  c.asserted_at,
  c.claim_valid_from,
  c.claim_valid_until,
  c.claim_valid_precision::text,
  c.claim_valid_kind::text
FROM claims AS c
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = c.deployment_id
 AND dl.doc_id = c.doc_id
WHERE c.is_current_testimony
  AND EXISTS (
    SELECT 1 FROM document_version_scope AS mode_row
    WHERE mode_row.deployment_id = c.deployment_id
      AND mode_row.doc_id = c.doc_id
      AND mode_row.periodised
  )
  AND NOT EXISTS (
    SELECT 1 FROM memory_v1.claims_visible_history AS origin
    WHERE origin.deployment_id = c.deployment_id
      AND origin.claim_id = c.claim_id
  )
  AND EXISTS (
    SELECT 1
    FROM chunk_claims AS occurrence
    JOIN chunks AS ch
      ON ch.deployment_id = occurrence.deployment_id
     AND ch.chunk_id = occurrence.chunk_id
     AND ch.doc_id = c.doc_id
    JOIN document_versions AS v
      ON v.deployment_id = ch.deployment_id
     AND v.version_id = ch.version_id
     AND v.current_representation_id = ch.representation_id
     AND v.deleted_at IS NULL
    JOIN document_representations AS representation
      ON representation.deployment_id = ch.deployment_id
     AND representation.representation_id = ch.representation_id
     AND representation.status = 'ready'
    WHERE occurrence.deployment_id = c.deployment_id
      AND occurrence.claim_id = c.claim_id
  );
COMMENT ON VIEW v_memory_claim_carried_periodised IS
  'D140 §3.4: current-testimony claims of periodised lineages whose origin version is no longer visible but which a non-deleted version still carries in its current reading (a D56 reuse occurrence). D135 keeps such a claim current; this view lets the fact authority views count and show it through that occurrence. Claims whose origin is visible are absent (claims_visible_history already has them). Not part of memory_v1 and never granted to a query role.';
"""

FACT_CLAIM_CURRENT_DDL = r"""
CREATE OR REPLACE VIEW v_memory_fact_claim_live (
  deployment_id,
  fact_kind,
  fact_id,
  claim_id,
  stance,
  doc_id,
  source_kind,
  source_handle,
  asserted_at,
  claim_valid_from,
  claim_valid_until,
  claim_valid_precision,
  claim_valid_kind,
  linked_at
) AS
SELECT
  evidence.deployment_id,
  'relation',
  evidence.relation_id,
  evidence.claim_id,
  evidence.stance::text,
  claim.doc_id,
  claim.source_kind,
  claim.source_handle,
  claim.asserted_at,
  claim.claim_valid_from,
  claim.claim_valid_until,
  claim.claim_valid_precision,
  claim.claim_valid_kind,
  evidence.created_at
FROM relation_evidence AS evidence
JOIN (SELECT deployment_id, claim_id, doc_id, source_kind, source_handle, asserted_at, claim_valid_from, claim_valid_until, claim_valid_precision, claim_valid_kind FROM memory_v1.claims_live UNION ALL SELECT deployment_id, claim_id, doc_id, source_kind, source_handle, asserted_at, claim_valid_from, claim_valid_until, claim_valid_precision, claim_valid_kind FROM v_memory_claim_carried_periodised) AS claim
  ON claim.deployment_id = evidence.deployment_id
 AND claim.claim_id = evidence.claim_id
 AND claim.doc_id = evidence.doc_id
UNION ALL
SELECT
  evidence.deployment_id,
  'observation',
  evidence.observation_id,
  evidence.claim_id,
  evidence.stance::text,
  claim.doc_id,
  claim.source_kind,
  claim.source_handle,
  claim.asserted_at,
  claim.claim_valid_from,
  claim.claim_valid_until,
  claim.claim_valid_precision,
  claim.claim_valid_kind,
  evidence.created_at
FROM observation_evidence AS evidence
JOIN (SELECT deployment_id, claim_id, doc_id, source_kind, source_handle, asserted_at, claim_valid_from, claim_valid_until, claim_valid_precision, claim_valid_kind FROM memory_v1.claims_live UNION ALL SELECT deployment_id, claim_id, doc_id, source_kind, source_handle, asserted_at, claim_valid_from, claim_valid_until, claim_valid_precision, claim_valid_kind FROM v_memory_claim_carried_periodised) AS claim
  ON claim.deployment_id = evidence.deployment_id
 AND claim.claim_id = evidence.claim_id
 AND claim.doc_id = evidence.doc_id;
"""

FACT_SOURCE_QUALIFIED_DDL = r"""
CREATE VIEW v_memory_fact_source_qualified (
  deployment_id,
  fact_kind,
  fact_id,
  subject_entity_id,
  predicate,
  object_entity_id,
  statement,
  fact_label,
  valid_from,
  valid_until,
  ingested_at,
  invalidated_at,
  contradiction_group,
  confidence,
  evidence_count_current,
  contradict_count_current,
  support_state_current,
  valid_precision
) AS
SELECT fact.deployment_id, fact.fact_kind, fact.fact_id,
       fact.subject_entity_id, fact.predicate, fact.object_entity_id,
       fact.statement, fact.fact_label, fact.valid_from, fact.valid_until,
       fact.ingested_at, fact.invalidated_at, fact.contradiction_group,
       fact.confidence, counts.supports, counts.contradicts,
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
SELECT
  r.deployment_id,
  'relation',
  r.relation_id,
  subject_survivor.survivor_entity_id,
  r.predicate,
  object_survivor.survivor_entity_id,
  NULL::text,
  r.fact_label,
  r.valid_from,
  r.valid_until,
  r.ingested_at,
  r.invalidated_at,
  r.contradiction_group,
  r.confidence,
  r.valid_precision::text
FROM relations AS r
JOIN v_memory_entity_survivor AS subject_survivor
  ON subject_survivor.deployment_id = r.deployment_id
 AND subject_survivor.entity_id = r.subject_entity_id
JOIN v_memory_entity_survivor AS object_survivor
  ON object_survivor.deployment_id = r.deployment_id
 AND object_survivor.entity_id = r.object_entity_id
WHERE EXISTS (
  SELECT 1
  FROM relation_evidence AS evidence
  JOIN (SELECT deployment_id, claim_id, doc_id FROM memory_v1.claims_visible_history UNION ALL SELECT deployment_id, claim_id, doc_id FROM v_memory_claim_carried_periodised) AS claim
    ON claim.deployment_id = evidence.deployment_id
   AND claim.claim_id = evidence.claim_id
   AND claim.doc_id = evidence.doc_id
  WHERE evidence.deployment_id = r.deployment_id
    AND evidence.relation_id = r.relation_id
)
UNION ALL
SELECT
  o.deployment_id,
  'observation',
  o.observation_id,
  subject_survivor.survivor_entity_id,
  NULL::text,
  NULL::uuid,
  o.statement,
  o.obs_label,
  o.valid_from,
  o.valid_until,
  o.ingested_at,
  o.invalidated_at,
  o.contradiction_group,
  o.confidence,
  o.valid_precision::text
FROM observations AS o
JOIN v_memory_entity_survivor AS subject_survivor
  ON subject_survivor.deployment_id = o.deployment_id
 AND subject_survivor.entity_id = o.subject_entity_id
WHERE EXISTS (
  SELECT 1
  FROM observation_evidence AS evidence
  JOIN (SELECT deployment_id, claim_id, doc_id FROM memory_v1.claims_visible_history UNION ALL SELECT deployment_id, claim_id, doc_id FROM v_memory_claim_carried_periodised) AS claim
    ON claim.deployment_id = evidence.deployment_id
   AND claim.claim_id = evidence.claim_id
   AND claim.doc_id = evidence.doc_id
  WHERE evidence.deployment_id = o.deployment_id
    AND evidence.observation_id = o.observation_id
)
) AS fact (deployment_id, fact_kind, fact_id, subject_entity_id, predicate, object_entity_id, statement, fact_label, valid_from, valid_until, ingested_at, invalidated_at, contradiction_group, confidence, valid_precision)
LEFT JOIN LATERAL (
  SELECT
    count(*) FILTER (WHERE lineage.stance = 'supports')::bigint AS supports,
    count(*) FILTER (WHERE lineage.stance = 'contradicts')::bigint AS contradicts
  FROM v_memory_evidence_lineage_live AS lineage
  WHERE lineage.deployment_id = fact.deployment_id
    AND lineage.fact_kind = fact.fact_kind
    AND lineage.fact_id = fact.fact_id
) AS counts ON true;
COMMENT ON VIEW v_memory_fact_source_qualified IS
  'Private D153 source and presentation authority. Preserves D140 historical or carried claim coordinates and D54 live lineage counts; entity endpoints still require entities_current. Never granted to a query role.';
"""

FACT_VISIBLE_DDL = r"""
CREATE OR REPLACE VIEW v_memory_fact_visible (
  deployment_id,
  fact_kind,
  fact_id,
  subject_entity_id,
  predicate,
  object_entity_id,
  statement,
  fact_label,
  valid_from,
  valid_until,
  ingested_at,
  invalidated_at,
  contradiction_group,
  confidence,
  evidence_count_current,
  contradict_count_current,
  support_state_current,
  valid_precision
) AS
SELECT fact.*
FROM v_memory_fact_source_qualified AS fact
WHERE EXISTS (
  SELECT 1 FROM memory_v1.entities_current AS subject
  WHERE subject.deployment_id = fact.deployment_id
    AND subject.entity_id = fact.subject_entity_id
) AND (fact.fact_kind = 'observation' OR EXISTS (
  SELECT 1 FROM memory_v1.entities_current AS object
  WHERE object.deployment_id = fact.deployment_id
    AND object.entity_id = fact.object_entity_id
));
"""

FACTS_PUBLIC_DDL = r"""
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
  fact.evidence_count_current,
  fact.contradict_count_current,
  fact.support_state_current,
  fact.valid_precision
FROM v_memory_fact_visible AS fact;
COMMENT ON VIEW memory_v1.facts_visible_history IS 'Historically visible adjudicated facts with one canonical world window and separate system timestamps. Membership requires surviving provenance. Unknown dates and partial windows are possible temporal matches, not proof of being current; open precision explicitly means ongoing. Completed windows remain believed history while invalidated_at is NULL. Evidence counts and support state are current testimony, not reconstructed historical counts.';
"""


def upgrade() -> None:
    """Install literal head-equivalent source truth and private endpoint factoring."""
    for ddl in (
        ENTITIES_CONSTRAINED_PROVENANCE_DDL,
        CARRIED_CLAIMS_DDL,
        FACT_CLAIM_CURRENT_DDL,
        FACT_SOURCE_QUALIFIED_DDL,
        FACT_VISIBLE_DDL,
        FACTS_PUBLIC_DDL,
    ):
        apply_view_ddl(sql=ddl)
    database = str(
        op.get_bind().exec_driver_sql("SELECT current_database()").scalar_one()
    )
    quote = op.get_bind().dialect.identifier_preparer.quote
    for helper in D153_HELPER_VIEWS:
        op.execute(f"REVOKE ALL ON public.{helper} FROM PUBLIC")
        op.execute(
            f"REVOKE ALL ON public.{helper} FROM {quote('rememberstack_query_' + database)}"
        )
        op.execute(f"ALTER VIEW public.{helper} OWNER TO rememberstack_view_owner")


def downgrade() -> None:
    """Restore the prior private shape without dropping callers or their grants."""
    from rememberstack.spine.migrations.versions.p9_38_0059_d140_effective_time import (
        _fact_authority_views,
    )
    from rememberstack.spine.migrations.versions.p9_45_0066_fact_counts_demand import (
        ENTITIES_BOUNDED_PROVENANCE_DDL,
    )
    from rememberstack.spine.migrations.versions.p9_45_0066_fact_counts_demand import (
        FACTS_DEMAND_COUNTS_DDL,
    )

    connection = op.get_bind()
    quote = connection.dialect.identifier_preparer.quote
    # PostgreSQL cannot remove view columns in place. Keep dependent view OIDs,
    # comments, owners and grants by replacing their queries, not DROP CASCADE.
    dependents = (
        connection.execute(
            text("""
        SELECT DISTINCT namespace.nspname, relation.relname,
               pg_get_viewdef(relation.oid, false) AS definition
        FROM pg_depend dependency
        JOIN pg_rewrite rewrite ON rewrite.oid = dependency.objid
        JOIN pg_class relation ON relation.oid = rewrite.ev_class
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE dependency.refobjid = 'public.v_memory_fact_visible'::regclass
          AND relation.oid <> dependency.refobjid AND relation.relkind = 'v'
    """)
        )
        .mappings()
        .all()
    )
    op.execute(
        "ALTER VIEW public.v_memory_fact_visible RENAME TO v_memory_fact_visible_d153"
    )
    op.execute(_fact_authority_views()[1][1])
    op.execute("""COMMENT ON VIEW public.v_memory_fact_visible IS
      'Private single authority for historically visible fact membership. A relation or observation appears only when every entity endpoint resolves to entities_current and at least one evidence association binds a fully visible historical claim coordinate. Both facts_visible_history and fact_claim_evidence_live consume this helper, so the fact catalog and its evidence bridge cannot disagree. Not part of memory_v1 and never granted to a query role.'""")
    op.execute(
        "ALTER VIEW public.v_memory_fact_visible OWNER TO rememberstack_view_owner"
    )
    for dependent in dependents:
        if (dependent["nspname"], dependent["relname"]) == (
            "memory_v1",
            "facts_visible_history",
        ):
            continue
        op.execute(
            f"CREATE OR REPLACE VIEW {quote(dependent['nspname'])}.{quote(dependent['relname'])}"
            f" AS {dependent['definition']}"
        )
    apply_view_ddl(sql=FACTS_DEMAND_COUNTS_DDL)
    op.execute("DROP VIEW public.v_memory_fact_visible_d153")
    op.execute("DROP VIEW public.v_memory_fact_source_qualified")
    apply_view_ddl(sql=ENTITIES_BOUNDED_PROVENANCE_DDL)
