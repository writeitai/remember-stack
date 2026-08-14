"""Register RANGE families with pg_partman and create projection views."""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import apply_ddl

revision: str = "p0_02_0006"
down_revision: str | None = "p0_02_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RANGE_PARENTS = (
    ("mentions", "created_at"),
    ("resolution_decisions", "decided_at"),
    ("chunks", "created_at"),
    ("chunk_claims", "created_at"),
    ("claim_extraction_decisions", "decided_at"),
    ("testimony_currency_events", "occurred_at"),
)
_VIEWS = (
    "v_graph_survivor",
    "v_graph_entities",
    "v_graph_documents",
    "v_graph_relates",
    "v_graph_mentioned_in",
    "v_graph_crossref",
    "v_graph_is_document",
)
_VIEW_DDL = r"""-- Resolve every entity id to its final merge SURVIVOR. A merge is a REDIRECT, not a rewrite
-- (entities.merged_into; entity_id never reused) and relations are NOT re-pointed in PG — so endpoints
-- MUST be redirected here, or the rebuild silently drops every edge touching a merged entity. Cycle-safe
-- (merged_into acyclicity is not schema-enforced); the rebuild's validation gate (below) aborts the
-- snapshot if any retained endpoint fails to resolve to exactly one emitted survivor.
CREATE VIEW v_graph_survivor AS
WITH RECURSIVE chain(entity_id, cur, depth) AS (
  SELECT entity_id, entity_id, 0 FROM entities
  UNION ALL
  SELECT c.entity_id, e.merged_into, c.depth + 1
  FROM chain c JOIN entities e ON e.entity_id = c.cur
  WHERE e.merged_into IS NOT NULL AND c.depth < 64          -- cycle / runaway guard
)
SELECT entity_id,
       (SELECT cur FROM chain x WHERE x.entity_id = chain.entity_id ORDER BY depth DESC LIMIT 1) AS survivor
FROM chain GROUP BY entity_id;   -- survivor = the terminal (merged_into IS NULL) node of each chain

-- Nodes: survivors only; cast timestamps. Graph-derived metrics (pagerank/graph_degree) are NOT loaded —
-- they are computed POST-load (D11); reprojecting a stored value is circular. entity_id stays native UUID
-- (PK verified in LadybugDB source/tests; STRING fallback = entity_id::text, applied uniformly to the PK
-- AND every endpoint).
CREATE VIEW v_graph_entities AS
SELECT entity_id AS id, type, canonical_name AS name, normalized_name,
       profile_summary AS summary, (created_at AT TIME ZONE 'UTC') AS created_at
FROM   entities WHERE status = 'active';                    -- merged/retired entities are not nodes

CREATE VIEW v_graph_documents AS
SELECT d.doc_id AS id, d.title, d.source_uri,
       (dv.published_at AT TIME ZONE 'UTC')::date AS published_at  -- the CURRENT version's date (D55); NULL when unset
FROM   documents d
LEFT JOIN document_versions dv
       ON dv.deployment_id = d.deployment_id AND dv.version_id = d.current_version_id
WHERE  d.deleted_at IS NULL;   -- lineages project; a lineage mid-ingest (no current version yet) projects with NULL date (F2)

-- Edges: endpoints are the FIRST TWO columns (FROM, TO), survivor-redirected and guarded so both
-- endpoints exist as emitted nodes (else COPY-REL throws). Keep EVERY invalidated edge by default for
-- transaction-time as-of (D69): there is no invalidation-age filter and a closed valid-time fact is
-- unaffected. Endpoint joins are the retention boundary. Parallel edges with distinct relation_id are
-- PRESERVED (no blind DISTINCT — same-(s,p,o) collapse is E3's job, D43).
CREATE VIEW v_graph_relates AS
SELECT s1.survivor AS "from", s2.survivor AS "to",
       r.relation_id, r.predicate, r.fact_label AS fact,
       r.evidence_count::bigint AS evidence_count, r.contradict_count::bigint AS contradict_count,
       r.confidence::float8 AS confidence, r.contradiction_group,
       (r.valid_from AT TIME ZONE 'UTC') AS valid_from, (r.valid_until AT TIME ZONE 'UTC') AS valid_until,
       (r.ingested_at AT TIME ZONE 'UTC') AS ingested_at, (r.invalidated_at AT TIME ZONE 'UTC') AS invalidated_at
FROM   relations r
JOIN   v_graph_survivor s1 ON s1.entity_id = r.subject_entity_id
JOIN   v_graph_survivor s2 ON s2.entity_id = r.object_entity_id
JOIN   entities e1 ON e1.entity_id = s1.survivor AND e1.status = 'active'   -- endpoint emitted as a node
JOIN   entities e2 ON e2.entity_id = s2.survivor AND e2.status = 'active';
-- relations.status (GENERATED) is DROPPED — liveness is derived in Cypher (invalidated_at IS NULL), D6.

CREATE VIEW v_graph_mentioned_in AS                          -- aggregate: no (entity,doc) base table
SELECT s.survivor AS "from", m.doc_id AS "to",
       COUNT(*)::bigint AS mention_count, (MIN(m.created_at) AT TIME ZONE 'UTC') AS first_seen
FROM   mentions m
JOIN   resolution_decisions rd ON rd.mention_id = m.mention_id AND rd.superseded_by IS NULL   -- live verdict
JOIN   v_graph_survivor s ON s.entity_id = rd.entity_id
JOIN   entities e ON e.entity_id = s.survivor AND e.status = 'active'
WHERE  EXISTS (SELECT 1 FROM documents d WHERE d.doc_id = m.doc_id AND d.deleted_at IS NULL)
GROUP  BY s.survivor, m.doc_id;

CREATE VIEW v_graph_crossref AS
SELECT from_doc_id AS "from", to_doc_id AS "to", kind::text AS kind, context
FROM   document_crossrefs WHERE to_doc_id IS NOT NULL;       -- nullable = cited-but-not-ingested → no edge

CREATE VIEW v_graph_is_document AS                           -- bridge: Document-typed Entity ↔ its E0 doc
SELECT s.survivor AS "from", d.doc_id AS "to"
FROM   documents d
JOIN   v_graph_survivor s ON s.entity_id = d.document_entity_id
JOIN   entities e ON e.entity_id = s.survivor AND e.status = 'active'
WHERE  d.document_entity_id IS NOT NULL AND d.deleted_at IS NULL;
"""


def upgrade() -> None:
    """Register all monthly parents and create final no-filter projection views."""
    for parent, control in _RANGE_PARENTS:
        op.execute(
            "SELECT public.create_parent("
            f"p_parent_table := 'public.{parent}', "
            f"p_control := '{control}', "
            "p_interval := '1 month', "
            "p_type := 'range', "
            "p_premake := 4, "
            "p_default_table := true, "
            "p_automatic_maintenance := 'on', "
            "p_jobmon := false)"
        )
    apply_ddl(sql=_VIEW_DDL)


def downgrade() -> None:
    """Drop views and unregister only the six UGM pg_partman parents."""
    for view_name in reversed(_VIEWS):
        op.execute(f"DROP VIEW IF EXISTS {view_name}")
    op.execute(
        """
        DO $$
        DECLARE
          configured_parent text;
          configured_template text;
        BEGIN
          FOR configured_parent, configured_template IN
            SELECT parent_table, template_table
            FROM public.part_config
            WHERE parent_table = ANY (ARRAY[
              'public.mentions',
              'public.resolution_decisions',
              'public.chunks',
              'public.chunk_claims',
              'public.claim_extraction_decisions',
              'public.testimony_currency_events'
            ])
          LOOP
            DELETE FROM public.part_config
            WHERE parent_table = configured_parent;
            IF configured_template IS NOT NULL THEN
              EXECUTE 'DROP TABLE IF EXISTS ' || configured_template || ' CASCADE';
            END IF;
          END LOOP;
        END
        $$;
        """
    )
