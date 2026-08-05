"""Make every ``memory_v1`` association bind its complete coordinates.

Batch A correctly made the public query space fail closed on ordinary
tombstones, but several logical associations authenticated only the identifier
someone expected to be sufficient.  Because the large transcript/evidence
tables deliberately use logical foreign keys, inconsistent rows can exist and
must be omitted rather than borrowing a live lineage from a sibling row.

This correction does three structural things:

* one cycle-safe ``v_graph_survivor`` becomes the merge-resolution authority;
  the query-space helper is only its deployment-labelled adapter;
* one private fact-catalog helper owns fact membership, so facts and their
  evidence bridge cannot disagree about whether a fact exists;
* every corrected content/testimony/identity join binds all applicable
  lineage, version, representation, and section coordinates.

The original ``p9_01_0022`` migration remains immutable.  These
``CREATE OR REPLACE`` statements are also consumed by the offline manifest
builder, so the corrected definitions and all authorization helpers roll the
surface hash without requiring a running PostgreSQL server.
"""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import _split_sql
from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations.versions.p4_01_0011_survivor_view_rewrite import (
    _REWRITTEN as _PRIOR_GRAPH_SURVIVOR_DDL,
)
from rememberstack.spine.migrations.versions.p9_01_0022_memory_v1_query_space import (
    MEMORY_V1_AUTHORED_DDL,
)

revision: str = "p9_04_0025"
down_revision: str | None = "p9_03_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEW_OWNER = "rememberstack_view_owner"
_QUERY_ROLE_PREFIX = "rememberstack_query"

# These statements are the final authored definitions, in dependency order.
# ``source_definitions.py`` overlays them on the original migration when it
# builds the offline manifest.
MEMORY_V1_CORRECTION_DDL = r"""
CREATE OR REPLACE VIEW v_graph_survivor (entity_id, survivor) AS
WITH RECURSIVE chain(entity_id, cur) AS (
  SELECT entity_id, entity_id FROM entities
  UNION
  SELECT c.entity_id, e.merged_into
  FROM chain AS c
  JOIN entities AS e ON e.entity_id = c.cur
  WHERE e.merged_into IS NOT NULL
)
SELECT c.entity_id, c.cur
FROM chain AS c
JOIN entities AS terminal
  ON terminal.entity_id = c.cur
 AND terminal.merged_into IS NULL;

CREATE OR REPLACE VIEW v_memory_entity_survivor (
  deployment_id,
  entity_id,
  survivor_entity_id
) AS
SELECT source.deployment_id, resolved.entity_id, resolved.survivor
FROM v_graph_survivor AS resolved
JOIN entities AS source
  ON source.entity_id = resolved.entity_id
JOIN entities AS survivor
  ON survivor.entity_id = resolved.survivor
 AND survivor.deployment_id = source.deployment_id;
COMMENT ON VIEW v_memory_entity_survivor IS
  'Private deployment-labelled adapter over the single v_graph_survivor merge authority. A cyclic or dangling redirect reaches no terminal entity and therefore emits no row; valid chains have no guessed maximum depth. Not part of memory_v1 and never granted to a query role.';

CREATE OR REPLACE VIEW memory_v1.sections_live (
  deployment_id,
  section_id,
  doc_id,
  version_id,
  representation_id,
  structure_generation_id,
  parent_section_id,
  node_path,
  heading_level,
  title,
  normalized_title,
  role,
  ordinal,
  block_start,
  block_end,
  char_start,
  char_end,
  page_start,
  page_end,
  summary
) AS
SELECT
  s.deployment_id,
  s.section_id,
  s.doc_id,
  s.version_id,
  s.representation_id,
  s.structure_generation_id,
  parent.section_id,
  s.node_path,
  s.heading_level,
  s.title,
  s.normalized_title,
  s.role::text,
  s.ordinal,
  s.block_start,
  s.block_end,
  s.char_start,
  s.char_end,
  s.page_start,
  s.page_end,
  s.summary
FROM document_sections AS s
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = s.deployment_id
 AND dl.doc_id = s.doc_id
 AND dl.current_version_id = s.version_id
 AND dl.current_representation_id = s.representation_id
 AND dl.has_current_ready_content
JOIN document_representations AS r
  ON r.deployment_id = s.deployment_id
 AND r.version_id = s.version_id
 AND r.representation_id = s.representation_id
 AND r.current_structure_generation_id = s.structure_generation_id
LEFT JOIN document_sections AS parent
  ON parent.deployment_id = s.deployment_id
 AND parent.section_id = s.parent_section_id
 AND parent.doc_id = s.doc_id
 AND parent.version_id = s.version_id
 AND parent.representation_id = s.representation_id
 AND parent.structure_generation_id = s.structure_generation_id;

CREATE OR REPLACE VIEW memory_v1.chunks_live (
  deployment_id,
  chunk_id,
  doc_id,
  version_id,
  representation_id,
  section_id,
  ordinal,
  block_start,
  block_end,
  char_start,
  char_end,
  token_count,
  chunk_content_hash,
  extraction_input_hash,
  embedding_text_hash,
  location_facts,
  location_header,
  embedding_input_policy_version,
  policy_generation,
  embedder_generation,
  chunker_version,
  prefixer_version,
  created_at
) AS
SELECT
  c.deployment_id,
  c.chunk_id,
  c.doc_id,
  c.version_id,
  c.representation_id,
  sec.section_id,
  c.ordinal,
  c.block_start,
  c.block_end,
  c.char_start,
  c.char_end,
  c.token_count,
  c.chunk_content_hash,
  c.extraction_input_hash,
  c.embedding_text_hash,
  c.location_facts_json,
  c.location_header,
  c.embedding_input_policy_version,
  c.policy_generation,
  c.embedding_version,
  c.chunker_version,
  c.prefixer_version,
  c.created_at
FROM chunks AS c
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = c.deployment_id
 AND dl.doc_id = c.doc_id
 AND dl.current_version_id = c.version_id
 AND dl.current_representation_id = c.representation_id
 AND dl.has_current_ready_content
LEFT JOIN memory_v1.sections_live AS sec
  ON sec.deployment_id = c.deployment_id
 AND sec.section_id = c.section_id
 AND sec.doc_id = c.doc_id
 AND sec.version_id = c.version_id
 AND sec.representation_id = c.representation_id;

CREATE OR REPLACE VIEW memory_v1.claims_visible_history (
  deployment_id,
  claim_id,
  doc_id,
  version_id,
  representation_id,
  chunk_id,
  claim_text,
  source_span,
  char_start,
  char_end,
  added_context,
  temporal_class,
  is_attributed,
  audit_status,
  kept_flagged,
  extractor_version,
  asserted_at,
  claim_valid_from,
  claim_valid_until,
  claim_valid_precision,
  claim_valid_kind,
  ingested_at,
  source_kind,
  source_handle,
  is_current_testimony
) AS
SELECT
  c.deployment_id,
  c.claim_id,
  c.doc_id,
  ch.version_id,
  ch.representation_id,
  c.chunk_id,
  c.claim_text,
  c.source_span,
  c.char_start,
  c.char_end,
  c.added_context,
  c.temporal_class::text,
  c.is_attributed,
  c.audit_status::text,
  c.kept_flagged,
  c.extractor_version,
  c.asserted_at,
  c.claim_valid_from,
  c.claim_valid_until,
  c.claim_valid_precision::text,
  c.claim_valid_kind::text,
  c.ingested_at,
  dl.source_kind,
  dl.source_kind || ':' || coalesce(dl.source_ref, dl.doc_id::text),
  c.is_current_testimony
FROM claims AS c
JOIN chunks AS ch
  ON ch.deployment_id = c.deployment_id
 AND ch.chunk_id = c.chunk_id
 AND ch.doc_id = c.doc_id
JOIN memory_v1.document_versions_visible AS vv
  ON vv.deployment_id = ch.deployment_id
 AND vv.version_id = ch.version_id
 AND vv.doc_id = ch.doc_id
JOIN document_representations AS representation
  ON representation.deployment_id = ch.deployment_id
 AND representation.version_id = ch.version_id
 AND representation.representation_id = ch.representation_id
 AND representation.status = 'ready'
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = c.deployment_id
 AND dl.doc_id = c.doc_id;

CREATE OR REPLACE VIEW memory_v1.claim_occurrences_live (
  deployment_id,
  claim_id,
  chunk_id,
  derivation_kind,
  doc_id,
  version_id,
  representation_id,
  section_id,
  evidence_mode,
  source_locators,
  attached_at
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
  cc.created_at
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

CREATE OR REPLACE VIEW memory_v1.testimony_currency_events_visible (
  deployment_id,
  event_id,
  claim_id,
  doc_id,
  reconciliation_id,
  became_current,
  reason,
  from_extractor_version,
  from_version_id,
  occurred_at
) AS
SELECT
  e.deployment_id,
  e.event_id,
  e.claim_id,
  e.doc_id,
  e.reconciliation_id,
  e.became_current,
  e.reason::text,
  e.from_extractor_version,
  fv.version_id,
  e.occurred_at
FROM testimony_currency_events AS e
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = e.deployment_id
 AND dl.doc_id = e.doc_id
JOIN memory_v1.claims_visible_history AS ch
  ON ch.deployment_id = e.deployment_id
 AND ch.claim_id = e.claim_id
 AND ch.doc_id = e.doc_id
LEFT JOIN memory_v1.document_versions_visible AS fv
  ON fv.deployment_id = e.deployment_id
 AND fv.version_id = e.from_version_id
 AND fv.doc_id = e.doc_id;

CREATE OR REPLACE VIEW memory_v1.entities_current (
  deployment_id,
  entity_id,
  entity_type,
  canonical_name,
  normalized_name,
  type_confidence,
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
  e.type,
  e.canonical_name,
  e.normalized_name,
  e.type_confidence,
  e.profile_summary,
  live.mention_count,
  live.document_count,
  e.graph_degree::bigint,
  e.created_at,
  e.updated_at
FROM entities AS e
CROSS JOIN LATERAL (
  SELECT coalesce(sum(edm.mention_count), 0)::bigint AS mention_count,
         count(*)::bigint AS document_count
  FROM memory_v1.entity_document_mentions AS edm
  WHERE edm.deployment_id = e.deployment_id
    AND edm.entity_id = e.entity_id
) AS live
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

CREATE OR REPLACE VIEW memory_v1.identity_events_visible (
  deployment_id,
  object_kind,
  event_id,
  entity_id,
  related_entity_id,
  mention_id,
  outcome,
  method,
  confidence,
  decided_by,
  decided_at,
  is_superseded
) AS
SELECT
  rd.deployment_id,
  'resolution_decision',
  rd.decision_id,
  ec.entity_id,
  NULL::uuid,
  rd.mention_id,
  CASE WHEN rd.is_new_entity THEN 'new_entity' ELSE 'linked' END,
  rd.method::text,
  rd.confidence,
  rd.decided_by::text,
  rd.decided_at,
  (rd.superseded_by IS NOT NULL)
FROM resolution_decisions AS rd
JOIN v_memory_entity_survivor AS s
  ON s.deployment_id = rd.deployment_id
 AND s.entity_id = rd.entity_id
JOIN memory_v1.entities_current AS ec
  ON ec.deployment_id = s.deployment_id
 AND ec.entity_id = s.survivor_entity_id
JOIN memory_v1.mentions_live AS mention
  ON mention.deployment_id = rd.deployment_id
 AND mention.mention_id = rd.mention_id
UNION ALL
SELECT
  me.deployment_id,
  'merge_event',
  me.merge_id,
  ec.entity_id,
  me.absorbed_id,
  NULL::uuid,
  CASE WHEN me.reversed_by IS NOT NULL THEN 'unmerge' ELSE 'merge' END,
  'merge_event',
  NULL::real,
  me.decided_by::text,
  me.decided_at,
  (me.reversed_by IS NOT NULL)
FROM merge_events AS me
JOIN v_memory_entity_survivor AS s
  ON s.deployment_id = me.deployment_id
 AND s.entity_id = me.survivor_id
JOIN memory_v1.entities_current AS ec
  ON ec.deployment_id = s.deployment_id
 AND ec.entity_id = s.survivor_entity_id;

CREATE VIEW v_memory_fact_visible (
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
  confidence
) AS
SELECT
  r.deployment_id,
  'relation',
  r.relation_id,
  subject.entity_id,
  r.predicate,
  object.entity_id,
  NULL::text,
  r.fact_label,
  r.valid_from,
  r.valid_until,
  r.ingested_at,
  r.invalidated_at,
  r.contradiction_group,
  r.confidence
FROM relations AS r
JOIN v_memory_entity_survivor AS subject_survivor
  ON subject_survivor.deployment_id = r.deployment_id
 AND subject_survivor.entity_id = r.subject_entity_id
JOIN memory_v1.entities_current AS subject
  ON subject.deployment_id = subject_survivor.deployment_id
 AND subject.entity_id = subject_survivor.survivor_entity_id
JOIN v_memory_entity_survivor AS object_survivor
  ON object_survivor.deployment_id = r.deployment_id
 AND object_survivor.entity_id = r.object_entity_id
JOIN memory_v1.entities_current AS object
  ON object.deployment_id = object_survivor.deployment_id
 AND object.entity_id = object_survivor.survivor_entity_id
WHERE EXISTS (
  SELECT 1
  FROM relation_evidence AS evidence
  JOIN memory_v1.claims_visible_history AS claim
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
  subject.entity_id,
  NULL::text,
  NULL::uuid,
  o.statement,
  o.obs_label,
  o.valid_from,
  o.valid_until,
  o.ingested_at,
  o.invalidated_at,
  o.contradiction_group,
  o.confidence
FROM observations AS o
JOIN v_memory_entity_survivor AS subject_survivor
  ON subject_survivor.deployment_id = o.deployment_id
 AND subject_survivor.entity_id = o.subject_entity_id
JOIN memory_v1.entities_current AS subject
  ON subject.deployment_id = subject_survivor.deployment_id
 AND subject.entity_id = subject_survivor.survivor_entity_id
WHERE EXISTS (
  SELECT 1
  FROM observation_evidence AS evidence
  JOIN memory_v1.claims_visible_history AS claim
    ON claim.deployment_id = evidence.deployment_id
   AND claim.claim_id = evidence.claim_id
   AND claim.doc_id = evidence.doc_id
  WHERE evidence.deployment_id = o.deployment_id
    AND evidence.observation_id = o.observation_id
);
COMMENT ON VIEW v_memory_fact_visible IS
  'Private single authority for historically visible fact membership. A relation or observation appears only when every entity endpoint resolves to entities_current and at least one evidence association binds a fully visible historical claim coordinate. Both facts_visible_history and fact_claim_evidence_live consume this helper, so the fact catalog and its evidence bridge cannot disagree. Not part of memory_v1 and never granted to a query role.';

CREATE OR REPLACE VIEW v_memory_page_citation_visible (
  deployment_id,
  artifact_id,
  role,
  target_kind,
  target_id,
  claim_chunk_content_hash
) AS
SELECT
  e.deployment_id,
  e.artifact_id,
  e.role::text,
  CASE WHEN e.claim_lineage_id IS NOT NULL THEN 'claim' ELSE 'document' END,
  coalesce(e.claim_lineage_id, e.doc_id),
  e.claim_chunk_content_hash
FROM knowledge_artifact_evidence AS e
WHERE (e.claim_lineage_id IS NOT NULL OR e.relation_id IS NULL)
  AND EXISTS (
    SELECT 1
    FROM memory_v1.documents_live AS d
    WHERE d.deployment_id = e.deployment_id
      AND d.doc_id = coalesce(e.claim_lineage_id, e.doc_id)
  )
UNION ALL
SELECT
  e.deployment_id,
  e.artifact_id,
  e.role::text,
  'relation',
  e.relation_id,
  e.claim_chunk_content_hash
FROM knowledge_artifact_evidence AS e
WHERE e.claim_lineage_id IS NULL
  AND e.relation_id IS NOT NULL
  AND EXISTS (
    SELECT 1
    FROM v_memory_fact_visible AS fact
    WHERE fact.deployment_id = e.deployment_id
      AND fact.fact_kind = 'relation'
      AND fact.fact_id = e.relation_id
  );

CREATE OR REPLACE VIEW memory_v1.fact_claim_evidence_live (
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
JOIN v_memory_fact_visible AS fact
  ON fact.deployment_id = evidence.deployment_id
 AND fact.fact_kind = 'relation'
 AND fact.fact_id = evidence.relation_id
JOIN memory_v1.claims_live AS claim
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
JOIN v_memory_fact_visible AS fact
  ON fact.deployment_id = evidence.deployment_id
 AND fact.fact_kind = 'observation'
 AND fact.fact_id = evidence.observation_id
JOIN memory_v1.claims_live AS claim
  ON claim.deployment_id = evidence.deployment_id
 AND claim.claim_id = evidence.claim_id
 AND claim.doc_id = evidence.doc_id;

CREATE OR REPLACE VIEW memory_v1.facts_visible_history (
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
  support_state_current
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
    FROM review_queue AS q
    WHERE q.deployment_id = fact.deployment_id
      AND q.item_kind = 'support_withdrawn'
      AND q.status IN ('pending', 'deferred')
      AND q.candidate ->> 'fact_id' = fact.fact_id::text
  ) THEN 'withdrawn' ELSE 'current' END
FROM v_memory_fact_visible AS fact
CROSS JOIN LATERAL (
  SELECT
    count(*) FILTER (WHERE lineage.stance = 'supports')::bigint AS supports,
    count(*) FILTER (WHERE lineage.stance = 'contradicts')::bigint AS contradicts
  FROM memory_v1.evidence_lineage AS lineage
  WHERE lineage.deployment_id = fact.deployment_id
    AND lineage.fact_kind = fact.fact_kind
    AND lineage.fact_id = fact.fact_id
) AS counts;

CREATE OR REPLACE VIEW memory_v1.page_evidence_visible (
  deployment_id,
  artifact_id,
  role,
  target_kind,
  target_id,
  claim_chunk_content_hashes,
  link_count
) AS
WITH visible_links AS MATERIALIZED (
  SELECT * FROM v_memory_page_citation_visible
)
SELECT
  link.deployment_id,
  link.artifact_id,
  link.role,
  link.target_kind,
  link.target_id,
  array_agg(
    DISTINCT link.claim_chunk_content_hash
    ORDER BY link.claim_chunk_content_hash
  ) FILTER (WHERE link.claim_chunk_content_hash IS NOT NULL),
  count(*)::bigint
FROM visible_links AS link
JOIN knowledge_artifacts AS artifact
  ON artifact.deployment_id = link.deployment_id
 AND artifact.artifact_id = link.artifact_id
 AND artifact.status <> 'tombstoned'
GROUP BY
  link.deployment_id,
  link.artifact_id,
  link.role,
  link.target_kind,
  link.target_id;
COMMENT ON VIEW memory_v1.page_evidence_visible IS
  'One row per visible artifact-to-target citation, keyed by (deployment_id, artifact_id, role, target_kind, target_id) and joinable to pages_live on (deployment_id, artifact_id), documents_live on target_id for claim and document targets, and the fact relations on target_id for relation targets. EACH TARGET PASSES ITS OWN VISIBILITY GATE: a citation appears only while its cited lineage is live or its cited relation still has surviving provenance, so forgetting a source removes the link rather than leaving a reference to vanished content. The authoritative citation set is evaluated once and joined directly to non-tombstoned artifact status, which is exactly the membership rule pages_live applies, so a visible page always has at least one row here and a link never outlives the page that carries it. A claim citation is a stable coordinate on the asserting LINEAGE, and its chunk content hashes are exposed only as locators inside that already authorized lineage: the hash never authorizes a read and cannot be used to bypass the lineage gate. Because several chunk coordinates in one lineage collapse into one association, link_count reports exactly how many underlying links were collapsed. The view carries no clocks.';

CREATE OR REPLACE VIEW memory_v1.changes_visible (
  deployment_id,
  object_kind,
  event_id,
  object_id,
  occurred_at,
  label
) AS
WITH visible_facts AS MATERIALIZED (
  SELECT * FROM v_memory_fact_visible
)
SELECT h.deployment_id, 'relation_ingest', h.fact_id, h.fact_id, h.ingested_at,
       coalesce(h.fact_label, h.predicate)
FROM visible_facts AS h
WHERE h.fact_kind = 'relation'
UNION ALL
SELECT h.deployment_id, 'relation_invalidation', h.fact_id, h.fact_id,
       h.invalidated_at, coalesce(h.fact_label, h.predicate)
FROM visible_facts AS h
WHERE h.fact_kind = 'relation' AND h.invalidated_at IS NOT NULL
UNION ALL
SELECT ra.deployment_id, 'relation_supersession', ra.adjudication_id, h.fact_id,
       ra.decided_at, coalesce(h.fact_label, h.predicate)
FROM relation_adjudications AS ra
JOIN visible_facts AS h
  ON h.deployment_id = ra.deployment_id
 AND h.fact_kind = 'relation'
 AND h.fact_id = ra.relation_id
WHERE ra.outcome = 'supersede'
UNION ALL
SELECT h.deployment_id, 'observation_ingest', h.fact_id, h.fact_id, h.ingested_at,
       coalesce(h.fact_label, h.statement)
FROM visible_facts AS h
WHERE h.fact_kind = 'observation'
UNION ALL
SELECT h.deployment_id, 'observation_invalidation', h.fact_id, h.fact_id,
       h.invalidated_at, coalesce(h.fact_label, h.statement)
FROM visible_facts AS h
WHERE h.fact_kind = 'observation' AND h.invalidated_at IS NOT NULL
UNION ALL
SELECT oa.deployment_id, 'observation_supersession', oa.adjudication_id, h.fact_id,
       oa.decided_at, coalesce(h.fact_label, h.statement)
FROM observation_adjudications AS oa
JOIN visible_facts AS h
  ON h.deployment_id = oa.deployment_id
 AND h.fact_kind = 'observation'
 AND h.fact_id = oa.observation_id
WHERE oa.outcome = 'supersede'
UNION ALL
SELECT c.deployment_id, 'claim_ingest', c.claim_id, c.claim_id, c.ingested_at,
       left(c.claim_text, 120)
FROM memory_v1.claims_visible_history AS c
UNION ALL
SELECT kc.deployment_id, 'knowledge_page_compilation', kc.compilation_id,
       p.artifact_id, kc.compiled_at, p.git_path
FROM knowledge_compilations AS kc
JOIN memory_v1.pages_live AS p
  ON p.deployment_id = kc.deployment_id
 AND p.artifact_id = kc.artifact_id;
"""

AUTHORIZATION_HELPER_VIEWS: tuple[str, ...] = (
    "v_graph_survivor",
    "v_memory_entity_survivor",
    "v_memory_fact_visible",
    "v_memory_mention_current_content",
    "v_memory_page_citation_visible",
)

_REPLACED_INITIAL_VIEWS: tuple[str, ...] = (
    "v_memory_entity_survivor",
    "memory_v1.sections_live",
    "memory_v1.chunks_live",
    "memory_v1.claims_visible_history",
    "memory_v1.claim_occurrences_live",
    "memory_v1.testimony_currency_events_visible",
    "memory_v1.entities_current",
    "memory_v1.identity_events_visible",
    "v_memory_page_citation_visible",
    "memory_v1.fact_claim_evidence_live",
    "memory_v1.facts_visible_history",
    "memory_v1.page_evidence_visible",
    "memory_v1.changes_visible",
)

_REPLACED_VIEW_COMMENTS: tuple[str, ...] = (
    "v_memory_entity_survivor",
    "memory_v1.page_evidence_visible",
)


def _query_role_name(*, database: str) -> str:
    """Return the deployment login name already created by Batch B."""
    return f"{_QUERY_ROLE_PREFIX}_{database}"


def _prior_view_definitions() -> dict[str, str]:
    """Extract the initial authored definitions needed by downgrade."""
    definitions: dict[str, str] = {}
    for block in MEMORY_V1_AUTHORED_DDL:
        for statement in _split_sql(sql=block):
            if not statement.startswith("CREATE VIEW "):
                continue
            qualified_name = statement.split(maxsplit=3)[2]
            if qualified_name in _REPLACED_INITIAL_VIEWS:
                definitions[qualified_name] = statement.replace(
                    "CREATE VIEW ", "CREATE OR REPLACE VIEW ", 1
                )
    missing = set(_REPLACED_INITIAL_VIEWS) - set(definitions)
    if missing:
        raise RuntimeError(f"missing prior view definitions: {sorted(missing)}")
    return definitions


def _prior_view_comments() -> dict[str, str]:
    """Extract comments overwritten by the correction for exact downgrade."""
    comments: dict[str, str] = {}
    for block in MEMORY_V1_AUTHORED_DDL:
        for statement in _split_sql(sql=block):
            comment_offset = statement.find("COMMENT ON VIEW ")
            if comment_offset < 0:
                continue
            comment = statement[comment_offset:]
            qualified_name = comment.split(maxsplit=4)[3]
            if qualified_name in _REPLACED_VIEW_COMMENTS:
                comments[qualified_name] = comment
    missing = set(_REPLACED_VIEW_COMMENTS) - set(comments)
    if missing:
        raise RuntimeError(f"missing prior view comments: {sorted(missing)}")
    return comments


def upgrade() -> None:
    """Install the complete coordinate-binding correction and lock its grants."""
    apply_ddl(sql=MEMORY_V1_CORRECTION_DDL)
    database = str(
        op.get_bind().exec_driver_sql("SELECT current_database()").scalar_one()
    )
    query_role = _query_role_name(database=database)
    for helper in AUTHORIZATION_HELPER_VIEWS:
        op.execute(f"REVOKE ALL ON public.{helper} FROM PUBLIC")
        op.execute(f"REVOKE ALL ON public.{helper} FROM {query_role}")
        op.execute(f"ALTER VIEW public.{helper} OWNER TO {_VIEW_OWNER}")


def downgrade() -> None:
    """Restore the pre-correction definitions and remove the new fact helper."""
    definitions = _prior_view_definitions()
    for qualified_name in _REPLACED_INITIAL_VIEWS:
        op.execute(definitions[qualified_name])
    for statement in _prior_view_comments().values():
        op.execute(statement)
    op.execute(_PRIOR_GRAPH_SURVIVOR_DDL)
    op.execute("ALTER VIEW public.v_graph_survivor OWNER TO CURRENT_USER")
    op.execute("DROP VIEW v_memory_fact_visible")
