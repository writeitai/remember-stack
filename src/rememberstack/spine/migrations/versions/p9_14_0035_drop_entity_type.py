"""D96 hard type cut: drop entity type from identity and signatures.

revision: p9_14_0035

``entities.type`` and ``mentions.emitted_type`` stop being identity.
``predicate_signatures`` is dropped. ``memory_v1.entities_current`` keeps
the ``entity_type`` / ``type_confidence`` *column names* as always-NULL
so query-space dependents do not CASCADE-drop; those columns are vacated
and must not be filtered on. The authority table columns are gone.
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

"""

_COMMENT_ENTITIES_CURRENT = (
    "COMMENT ON VIEW memory_v1.entities_current IS "
    "'One row per survivor entity with surviving provenance. entity_type and "
    "type_confidence are vacated (always NULL) after D96; identity is the entity_id.';"
)


def upgrade() -> None:
    """Vacate type on the public view, then drop authority type columns."""
    # Replace the view *before* DROP COLUMN so dependents no longer read e.type.
    # apply_view_ddl's CREATE VIEW regex does not match CREATE OR REPLACE.
    op.execute(MEMORY_V1_TYPE_CUT_DDL)
    op.execute(_COMMENT_ENTITIES_CURRENT)
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
    op.execute(
        """
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
        """
    )
