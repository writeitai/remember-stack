"""D96 hard type cut: drop entity type from identity and signatures.

revision: p9_14_0035

``entities.type`` and ``mentions.emitted_type`` stop being identity.
``predicate_signatures`` is dropped. Public and helper views keep the
vacated column *names* (``entity_type`` / ``type_confidence`` /
``emitted_type``) as always-NULL so dependents do not CASCADE-drop;
those columns must not be filtered on. The authority table columns
are gone.
"""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import drop_tables

revision: str = "p9_14_0035"
down_revision: str | None = "p9_13_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MEMORY_V1_TYPE_CUT_DDL = r"""
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

COMMENT ON VIEW memory_v1.entities_current IS
  'One row per externally visible survivor entity, keyed by (deployment_id, entity_id). Membership requires SURVIVING PROVENANCE, which is an explicit association to at least one live document lineage: a mention of this survivor in any non-tombstoned version of a live lineage, or a live document-entity bridge. An entity whose every source has been forgotten is therefore absent rather than orphaned, and merged entities are absent because a merge redirects to a survivor instead of rewriting history. MEMBERSHIP AND THE COUNTS ANSWER DIFFERENT QUESTIONS, and the difference is deliberate: the two counts are exact over CURRENT content only — they equal this entity''s rows in mentions_live and entity_document_mentions — so an entity whose only mention sits in a superseded version of a live lineage is published here with both counts at zero and has no row in entity_document_mentions at all. A zero count is not an absence of provenance. graph_degree is copied from the latest published graph snapshot and is therefore orientation that can lag live state; profile_summary is orientation text, never evidence; and the clocks are registry maintenance instants that carry no world-validity meaning. After D96, entity_type and type_confidence are vacated (always NULL); identity is the entity_id.';

CREATE OR REPLACE VIEW v_memory_mention_current_content (
  deployment_id,
  mention_id,
  doc_id,
  version_id,
  representation_id,
  chunk_id,
  section_id,
  claim_id,
  surface_form,
  normalized_lemma,
  canonical_name_form,
  emitted_type,
  type_confidence,
  language,
  char_start,
  char_end,
  created_at,
  survivor_entity_id,
  resolution_method,
  resolution_confidence,
  resolution_is_new_entity,
  resolved_at
) AS
SELECT
  m.deployment_id,
  m.mention_id,
  cl.doc_id,
  cl.version_id,
  cl.representation_id,
  cl.chunk_id,
  cl.section_id,
  mc.claim_id,
  m.surface_form,
  m.normalized_lemma,
  m.canonical_name_form,
  NULL::text,
  NULL::real,
  m.language,
  m.char_start,
  m.char_end,
  m.created_at,
  s.survivor_entity_id,
  live.method::text,
  live.confidence,
  live.is_new_entity,
  live.decided_at
FROM mentions AS m
JOIN memory_v1.chunks_live AS cl
  ON cl.deployment_id = m.deployment_id
 AND cl.chunk_id = m.chunk_id
 AND cl.doc_id = m.doc_id
LEFT JOIN memory_v1.claims_visible_history AS mc
  ON mc.deployment_id = m.deployment_id
 AND mc.claim_id = m.claim_id
 AND mc.doc_id = cl.doc_id
LEFT JOIN LATERAL (
  SELECT rd.entity_id, rd.method, rd.confidence, rd.is_new_entity, rd.decided_at
  FROM resolution_decisions AS rd
  WHERE rd.deployment_id = m.deployment_id
    AND rd.mention_id = m.mention_id
    AND rd.superseded_by IS NULL
  ORDER BY rd.decided_at DESC, rd.decision_id DESC
  LIMIT 1
) AS live ON true
LEFT JOIN v_memory_entity_survivor AS s
  ON s.deployment_id = m.deployment_id
 AND s.entity_id = live.entity_id;

COMMENT ON VIEW v_memory_mention_current_content IS
  'Private single definition of a mention in current content: exactly one row per mention whose chunk is a current-content chunk of the lineage the mention itself names, carrying the mention''s coordinates and the survivor of its one live, unsuperseded resolution decision. Both mentions_live and entity_document_mentions are projections of this relation, so a count and the transcript it counts cannot disagree. Not part of memory_v1 and never granted to a query role. emitted_type and type_confidence are vacated (always NULL) after D96.';

"""

_V_GRAPH_ENTITIES_TYPE_CUT = """
CREATE OR REPLACE VIEW v_graph_entities AS
SELECT entity_id AS id, NULL::text AS type, canonical_name AS name, normalized_name,
       profile_summary AS summary, (created_at AT TIME ZONE 'UTC') AS created_at
FROM   entities WHERE status = 'active';
"""

_V_GRAPH_ENTITIES_DOWNGRADE = """
CREATE OR REPLACE VIEW v_graph_entities AS
SELECT entity_id AS id, type, canonical_name AS name, normalized_name,
       profile_summary AS summary, (created_at AT TIME ZONE 'UTC') AS created_at
FROM   entities WHERE status = 'active';
"""

_MENTION_HELPER_DOWNGRADE = r"""
CREATE OR REPLACE VIEW v_memory_mention_current_content (
  deployment_id,
  mention_id,
  doc_id,
  version_id,
  representation_id,
  chunk_id,
  section_id,
  claim_id,
  surface_form,
  normalized_lemma,
  canonical_name_form,
  emitted_type,
  type_confidence,
  language,
  char_start,
  char_end,
  created_at,
  survivor_entity_id,
  resolution_method,
  resolution_confidence,
  resolution_is_new_entity,
  resolved_at
) AS
SELECT
  m.deployment_id,
  m.mention_id,
  cl.doc_id,
  cl.version_id,
  cl.representation_id,
  cl.chunk_id,
  cl.section_id,
  mc.claim_id,
  m.surface_form,
  m.normalized_lemma,
  m.canonical_name_form,
  m.emitted_type,
  m.type_confidence,
  m.language,
  m.char_start,
  m.char_end,
  m.created_at,
  s.survivor_entity_id,
  live.method::text,
  live.confidence,
  live.is_new_entity,
  live.decided_at
FROM mentions AS m
JOIN memory_v1.chunks_live AS cl
  ON cl.deployment_id = m.deployment_id
 AND cl.chunk_id = m.chunk_id
 AND cl.doc_id = m.doc_id
LEFT JOIN memory_v1.claims_visible_history AS mc
  ON mc.deployment_id = m.deployment_id
 AND mc.claim_id = m.claim_id
 AND mc.doc_id = cl.doc_id
LEFT JOIN LATERAL (
  SELECT rd.entity_id, rd.method, rd.confidence, rd.is_new_entity, rd.decided_at
  FROM resolution_decisions AS rd
  WHERE rd.deployment_id = m.deployment_id
    AND rd.mention_id = m.mention_id
    AND rd.superseded_by IS NULL
  ORDER BY rd.decided_at DESC, rd.decision_id DESC
  LIMIT 1
) AS live ON true
LEFT JOIN v_memory_entity_survivor AS s
  ON s.deployment_id = m.deployment_id
 AND s.entity_id = live.entity_id;
"""

_ENTITIES_CURRENT_DOWNGRADE = r"""
CREATE OR REPLACE VIEW memory_v1.entities_current (
  deployment_id, entity_id, entity_type, canonical_name, normalized_name,
  type_confidence, profile_summary, live_mention_count, live_document_count,
  graph_degree, created_at, updated_at
) AS
SELECT e.deployment_id, e.entity_id, e.type, e.canonical_name, e.normalized_name,
       e.type_confidence, e.profile_summary, live.mention_count, live.document_count,
       e.graph_degree::bigint, e.created_at, e.updated_at
FROM entities AS e
CROSS JOIN LATERAL (
  SELECT coalesce(sum(edm.mention_count), 0)::bigint AS mention_count,
         count(*)::bigint AS document_count
  FROM memory_v1.entity_document_mentions AS edm
  WHERE edm.deployment_id = e.deployment_id AND edm.entity_id = e.entity_id
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
"""


def upgrade() -> None:
    """Vacate type on public/helper views, then drop authority type columns."""
    # Replace views *before* DROP COLUMN so dependents no longer read the
    # authority columns. apply_view_ddl's CREATE VIEW regex does not match
    # CREATE OR REPLACE.
    op.execute(MEMORY_V1_TYPE_CUT_DDL)
    op.execute(_V_GRAPH_ENTITIES_TYPE_CUT)
    op.execute(
        "ALTER TABLE entities DROP CONSTRAINT IF EXISTS entities_deployment_id_type_fkey"
    )
    op.execute("DROP INDEX IF EXISTS ix_entities_type")
    op.execute("ALTER TABLE entities DROP COLUMN IF EXISTS type")
    op.execute("ALTER TABLE entities DROP COLUMN IF EXISTS type_confidence")
    op.execute("ALTER TABLE mentions DROP COLUMN IF EXISTS emitted_type")
    op.execute("ALTER TABLE mentions DROP COLUMN IF EXISTS type_confidence")
    drop_tables(table_names=("predicate_signatures",))


def downgrade() -> None:
    """Restore nullable type columns (values are NULL; D96 does not invent types)."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS predicate_signatures (
          deployment_id   uuid NOT NULL REFERENCES deployments,
          predicate       text NOT NULL,
          subject_type    text NOT NULL,
          object_type     text NOT NULL,
          PRIMARY KEY (deployment_id, predicate, subject_type, object_type),
          FOREIGN KEY (deployment_id, predicate)
            REFERENCES predicates (deployment_id, predicate) ON DELETE CASCADE,
          FOREIGN KEY (deployment_id, subject_type)
            REFERENCES entity_types (deployment_id, type),
          FOREIGN KEY (deployment_id, object_type)
            REFERENCES entity_types (deployment_id, type)
        )
        """
    )
    op.execute("ALTER TABLE entities ADD COLUMN IF NOT EXISTS type text")
    op.execute("ALTER TABLE entities ADD COLUMN IF NOT EXISTS type_confidence real")
    op.execute("ALTER TABLE mentions ADD COLUMN IF NOT EXISTS emitted_type text")
    op.execute("ALTER TABLE mentions ADD COLUMN IF NOT EXISTS type_confidence real")
    op.execute(_V_GRAPH_ENTITIES_DOWNGRADE)
    op.execute(_MENTION_HELPER_DOWNGRADE)
    op.execute(_ENTITIES_CURRENT_DOWNGRADE)
