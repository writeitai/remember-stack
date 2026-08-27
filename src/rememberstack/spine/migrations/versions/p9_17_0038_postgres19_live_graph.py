"""Replace P2 snapshots with PostgreSQL 19 live property graphs (D98).

revision: p9_17_0038

The property graphs are catalog metadata over private live relational views;
public result hydration remains on the evidence-rich ``memory_v1`` authority
views.  The graph views copy no tenant rows and therefore need neither a
generation nor a publication pointer.  Variable-length traversal remains in
bounded SQL helpers because PostgreSQL 19 deliberately ships fixed graph
patterns before quantified and shortest-path patterns.

The development-only downgrade restores legacy graph objects but does not
widen ``memory_v1.document_crossrefs_live`` back to unresolved or tombstoned
targets. D98 is a clean cut with disposable prerelease storage; retaining the
stricter public privacy view is intentional even when schema objects downgrade.
"""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.graph_catalog import GRAPH_HELPER_CONTRACT_VERSION
from rememberstack.spine.migrations._helpers import _split_sql
from rememberstack.spine.migrations._helpers import apply_ddl

revision: str = "p9_17_0038"
down_revision: str | None = "p9_16_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GRAPH_SOURCES = r"""
CREATE SCHEMA IF NOT EXISTS rememberstack_graph_internal;
REVOKE ALL ON SCHEMA rememberstack_graph_internal FROM PUBLIC;

CREATE VIEW rememberstack_graph_internal.entities_live AS
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
  );

CREATE VIEW rememberstack_graph_internal.documents_live AS
SELECT d.deployment_id, d.doc_id, d.title, d.source_uri, v.published_at
FROM documents AS d
LEFT JOIN document_versions AS v
  ON v.deployment_id = d.deployment_id
 AND v.version_id = d.current_version_id
WHERE d.deleted_at IS NULL;

CREATE VIEW rememberstack_graph_internal.relations_history AS
SELECT r.deployment_id, r.relation_id,
       subject.survivor_entity_id AS subject_entity_id,
       object.survivor_entity_id AS object_entity_id,
       r.predicate, r.valid_from, r.valid_until,
       r.ingested_at, r.invalidated_at
FROM relations AS r
JOIN v_memory_entity_survivor AS subject
  ON subject.deployment_id = r.deployment_id
 AND subject.entity_id = r.subject_entity_id
JOIN v_memory_entity_survivor AS object
  ON object.deployment_id = r.deployment_id
 AND object.entity_id = r.object_entity_id
JOIN rememberstack_graph_internal.entities_live AS subject_entity
  ON subject_entity.deployment_id = r.deployment_id
 AND subject_entity.entity_id = subject.survivor_entity_id
JOIN rememberstack_graph_internal.entities_live AS object_entity
  ON object_entity.deployment_id = r.deployment_id
 AND object_entity.entity_id = object.survivor_entity_id
WHERE EXISTS (
  SELECT 1
  FROM relation_evidence AS evidence
  JOIN documents AS evidence_document
    ON evidence_document.deployment_id = evidence.deployment_id
   AND evidence_document.doc_id = evidence.doc_id
   AND evidence_document.deleted_at IS NULL
  WHERE evidence.deployment_id = r.deployment_id
    AND evidence.relation_id = r.relation_id
);

CREATE VIEW rememberstack_graph_internal.relations_current AS
SELECT h.*
FROM rememberstack_graph_internal.relations_history AS h
CROSS JOIN (SELECT statement_timestamp() AS evaluated_at) AS clock
WHERE h.ingested_at <= clock.evaluated_at
  AND h.invalidated_at IS NULL
  AND (h.valid_from IS NULL OR h.valid_from <= clock.evaluated_at)
  AND (h.valid_until IS NULL OR h.valid_until > clock.evaluated_at);

CREATE VIEW rememberstack_graph_internal.crossrefs_live AS
SELECT x.deployment_id, x.crossref_id, x.from_doc_id, x.to_doc_id,
       x.kind::text AS kind, x.context, x.created_at
FROM document_crossrefs AS x
JOIN documents AS source
  ON source.deployment_id = x.deployment_id
 AND source.doc_id = x.from_doc_id
 AND source.deleted_at IS NULL
JOIN documents AS target
  ON target.deployment_id = x.deployment_id
 AND target.doc_id = x.to_doc_id
 AND target.deleted_at IS NULL
WHERE x.resolved AND x.to_doc_id IS NOT NULL;
"""

_CURRENT_GRAPH = r"""
CREATE PROPERTY GRAPH memory_v1.memory_current
  VERTEX TABLES (
    rememberstack_graph_internal.entities_live AS entity
      KEY (deployment_id, entity_id)
      LABEL entity PROPERTIES
        (deployment_id, entity_id, canonical_name, profile_summary),
    rememberstack_graph_internal.documents_live AS document
      KEY (deployment_id, doc_id)
      LABEL document PROPERTIES
        (deployment_id, doc_id, title, source_uri, published_at)
  )
  EDGE TABLES (
    rememberstack_graph_internal.relations_current AS relates
      KEY (deployment_id, relation_id)
      SOURCE KEY (deployment_id, subject_entity_id)
        REFERENCES entity (deployment_id, entity_id)
      DESTINATION KEY (deployment_id, object_entity_id)
        REFERENCES entity (deployment_id, entity_id)
      LABEL relates PROPERTIES
        (deployment_id, relation_id, predicate),
    memory_v1.entity_document_mentions AS mentioned_in
      KEY (deployment_id, entity_id, doc_id)
      SOURCE KEY (deployment_id, entity_id)
        REFERENCES entity (deployment_id, entity_id)
      DESTINATION KEY (deployment_id, doc_id)
        REFERENCES document (deployment_id, doc_id)
      LABEL mentioned_in PROPERTIES
        (deployment_id, mention_count, first_mentioned_at, last_mentioned_at),
    rememberstack_graph_internal.crossrefs_live AS document_crossref
      KEY (deployment_id, crossref_id)
      SOURCE KEY (deployment_id, from_doc_id)
        REFERENCES document (deployment_id, doc_id)
      DESTINATION KEY (deployment_id, to_doc_id)
        REFERENCES document (deployment_id, doc_id)
      LABEL document_crossref PROPERTIES
        (deployment_id, crossref_id, kind, context, created_at)
  )
"""

_HISTORY_GRAPH = r"""
CREATE PROPERTY GRAPH memory_v1.memory_history
  VERTEX TABLES (
    rememberstack_graph_internal.entities_live AS entity
      KEY (deployment_id, entity_id)
      LABEL entity PROPERTIES
        (deployment_id, entity_id, canonical_name, profile_summary)
  )
  EDGE TABLES (
    rememberstack_graph_internal.relations_history AS relates
      KEY (deployment_id, relation_id)
      SOURCE KEY (deployment_id, subject_entity_id)
        REFERENCES entity (deployment_id, entity_id)
      DESTINATION KEY (deployment_id, object_entity_id)
        REFERENCES entity (deployment_id, entity_id)
      LABEL relates PROPERTIES
        (deployment_id, relation_id, predicate, valid_from,
         valid_until, ingested_at, invalidated_at)
  )
"""

_DROP_OLD_GRAPH_STATE = r"""
DROP FUNCTION IF EXISTS memory_v1.graph_neighborhood(
  uuid, integer, text[], timestamptz, timestamptz, integer
);
DROP FUNCTION IF EXISTS memory_v1.graph_path(
  uuid, uuid, integer, text[], timestamptz, timestamptz, integer, integer
);

DROP TABLE IF EXISTS entity_graph_metrics;
DROP TABLE IF EXISTS communities;
DELETE FROM projection_snapshots WHERE plane::text <> 'P3_corpusfs';

CREATE TYPE projection_plane_live AS ENUM ('P3_corpusfs');
ALTER TABLE projection_snapshots
  ALTER COLUMN plane TYPE projection_plane_live
  USING plane::text::projection_plane_live;
DROP TYPE projection_plane;
ALTER TYPE projection_plane_live RENAME TO projection_plane;
DROP TYPE IF EXISTS community_algorithm;

COMMENT ON TABLE projection_snapshots IS
  'Immutable P3 CorpusFS snapshots only. P1 search and the PostgreSQL graph are live database state and have no generation rows.';
"""

_DROP_LEGACY_GRAPH_EXPORTS = r"""
DROP VIEW IF EXISTS v_graph_is_document;
DROP VIEW IF EXISTS v_graph_crossref;
DROP VIEW IF EXISTS v_graph_mentioned_in;
DROP VIEW IF EXISTS v_graph_relates;
DROP VIEW IF EXISTS v_graph_documents;
DROP VIEW IF EXISTS v_graph_entities;
"""

_ALIGN_PUBLIC_CROSSREF_VIEW = r"""
CREATE OR REPLACE VIEW memory_v1.document_crossrefs_live (
  deployment_id,
  crossref_id,
  from_doc_id,
  to_doc_id,
  kind,
  context,
  created_at
) AS
SELECT
  x.deployment_id,
  x.crossref_id,
  source.doc_id,
  target.doc_id,
  x.kind::text,
  left(x.context, 500),
  x.created_at
FROM document_crossrefs AS x
JOIN memory_v1.documents_live AS source
  ON source.deployment_id = x.deployment_id
 AND source.doc_id = x.from_doc_id
JOIN memory_v1.documents_live AS target
  ON target.deployment_id = x.deployment_id
 AND target.doc_id = x.to_doc_id
WHERE x.resolved AND x.to_doc_id IS NOT NULL;

COMMENT ON VIEW memory_v1.document_crossrefs_live IS
  'One row per resolved cross-reference whose BOTH endpoint lineages are live, keyed by (deployment_id, crossref_id) and joined to documents_live on (deployment_id, from_doc_id) and (deployment_id, to_doc_id). An unresolved reference, a reference whose target was never ingested, or one whose source or target lineage has been forgotten is absent rather than half-resolved, so this relation never reveals that a document once existed. The raw citation text is deliberately not exposed, because it is retained even after a target is forgotten; the bounded context is truncated to 500 characters. The creation clock is a processing instant, and the view carries no counts and asserts no facts.';
"""

_DROP_P2_VOCABULARY = r"""
DELETE FROM cost_ledger
WHERE stage::text IN ('build_snapshot', 'detect_communities');
DELETE FROM processing_state
WHERE stage::text IN ('build_snapshot', 'detect_communities');
DELETE FROM pipeline_component_versions
WHERE component::text = 'community_detector';

UPDATE entities SET graph_degree = 0 WHERE graph_degree <> 0;
COMMENT ON COLUMN entities.graph_degree IS
  'Reserved compatibility scalar fixed at zero after D98; blast-radius degree is computed from current PostgreSQL adjacency.';

DELETE FROM knowledge_page_rules WHERE rule_kind::text = 'community';
DELETE FROM knowledge_rule_keys WHERE key_kind::text = 'community';
UPDATE knowledge_plan_decisions
SET trigger = 'reflection'
WHERE trigger::text = 'community_change';
UPDATE knowledge_plan_runs
SET trigger = 'reflection'
WHERE trigger::text = 'community_change';
UPDATE knowledge_refresh_queue
SET trigger = 'evidence_changed'
WHERE trigger::text = 'community_changed';

CREATE TYPE pipeline_stage_live AS ENUM (
  'ingest','convert','structure','crossref','chunk','embed_chunk',
  'extract_claims','embed_claim','ground_claims','resolve_entities',
  'normalize_relations','adjudicate_supersession','adjudicate_observations',
  'embed_relation','label_relation','embed_observation','label_observation',
  'refresh_profile','compile_knowledge','reflect_knowledge','lint_knowledge',
  'reconcile','dispatch_knowledge','hard_forget'
);
DROP VIEW v_cost_receipts;
DROP INDEX ix_procstate_chunk_extract;
DROP INDEX ix_procstate_claim_normalize;
DROP INDEX ix_procstate_entity_obs_flush;
ALTER TABLE processing_state ALTER COLUMN stage TYPE pipeline_stage_live
  USING stage::text::pipeline_stage_live;
ALTER TABLE cost_ledger ALTER COLUMN stage TYPE pipeline_stage_live
  USING stage::text::pipeline_stage_live;
DROP TYPE pipeline_stage;
ALTER TYPE pipeline_stage_live RENAME TO pipeline_stage;
CREATE INDEX ix_procstate_chunk_extract
  ON processing_state (
    deployment_id, stage, target_kind, component_version, status
  ) WHERE stage = 'extract_claims' AND target_kind = 'chunk';
CREATE INDEX ix_procstate_claim_normalize
  ON processing_state (
    deployment_id, stage, target_kind, component_version, status
  ) WHERE stage = 'normalize_relations' AND target_kind = 'claim';
CREATE INDEX ix_procstate_entity_obs_flush
  ON processing_state (
    deployment_id, stage, target_kind, component_version, status
  ) WHERE stage = 'adjudicate_observations' AND target_kind = 'entity';
CREATE VIEW v_cost_receipts AS
SELECT
  'worker'::text AS source, cost_id, deployment_id,
  processing_id AS work_id, stage::text AS stage, lane::text AS lane, attempt,
  NULL::text AS surface, call_key, outcome::text AS outcome, model_name,
  tokens_in, tokens_out, cost_usd, latency_ms, occurred_at
FROM cost_ledger
UNION ALL
SELECT
  'surface'::text AS source, cost_id, deployment_id, request_id AS work_id,
  NULL::text AS stage, NULL::text AS lane, NULL::smallint AS attempt,
  surface::text AS surface, call_site || ':' || ordinal::text AS call_key,
  outcome::text AS outcome, model_name, tokens_in, tokens_out, cost_usd,
  latency_ms, occurred_at
FROM surface_cost_ledger;

CREATE TYPE pipeline_component_live AS ENUM (
  'ingester','converter','blockizer','structurer','crossreferencer','chunker',
  'context_prefixer','extractor','grounder','resolver','normalizer',
  'adjudicator','embedder','fact_labeler','profile_summarizer',
  'knowledge_planner','knowledge_writer','knowledge_reflector',
  'knowledge_linter','judge','forgetter','embedding_input_policy',
  'snapshot_builder'
);
ALTER TABLE pipeline_component_versions ALTER COLUMN component
  TYPE pipeline_component_live USING component::text::pipeline_component_live;
DROP TYPE pipeline_component;
ALTER TYPE pipeline_component_live RENAME TO pipeline_component;

COMMENT ON TABLE entities IS
  'Canonical entity registry. Entity ids are never reused and merges remain redirects. mention_count is cached orientation; graph_degree is a D98 compatibility scalar fixed at zero because current adjacency is computed at the consumer.';

"""

_NEIGHBORHOOD_HELPER = r"""
CREATE FUNCTION memory_v1.graph_neighborhood(
  deployment_id uuid,
  start_entity_id uuid,
  max_depth integer DEFAULT 2,
  predicates text[] DEFAULT NULL,
  valid_at timestamptz DEFAULT NULL,
  believed_at timestamptz DEFAULT NULL,
  max_results integer DEFAULT 100,
  expansion_budget integer DEFAULT 2000,
  frontier_budget integer DEFAULT 1000,
  time_budget_ms integer DEFAULT 1000
)
RETURNS TABLE (
  row_kind text,
  hops integer,
  relation_ids uuid[],
  node_ids uuid[],
  truncated boolean,
  truncation_reason text,
  examined_edges bigint,
  returned_paths bigint,
  effective_depth integer,
  effective_expansion_budget integer,
  effective_frontier_budget integer,
  effective_result_budget integer,
  effective_time_budget_ms integer,
  applied_valid_at timestamptz,
  applied_believed_at timestamptz
)
LANGUAGE plpgsql
STABLE
PARALLEL UNSAFE
SECURITY INVOKER
SET search_path = memory_v1, pg_catalog
AS $function$
#variable_conflict use_column
DECLARE
  depth_cap integer := least(greatest(coalesce(max_depth, 2), 1), 4);
  expansion_cap integer := least(greatest(coalesce(expansion_budget, 2000), 1), 2000);
  frontier_cap integer := least(greatest(coalesce(frontier_budget, 1000), 1), 1000);
  result_cap integer := least(greatest(coalesce(max_results, 100), 1), 500);
  time_cap integer := least(greatest(coalesce(time_budget_ms, 1000), 1), 5000);
  started_at timestamptz := clock_timestamp();
  clock_valid timestamptz := coalesce(valid_at, statement_timestamp());
  clock_believed timestamptz := coalesce(believed_at, statement_timestamp());
  frontier jsonb[] := ARRAY[
    jsonb_build_object(
      'head', start_entity_id::text,
      'nodes', to_jsonb(ARRAY[start_entity_id]),
      'edges', '[]'::jsonb
    )
  ];
  next_frontier jsonb[] := ARRAY[]::jsonb[];
  result_buffer jsonb[] := ARRAY[]::jsonb[];
  seen_vertices uuid[] := ARRAY[start_entity_id];
  current_path jsonb;
  candidate jsonb;
  path_nodes uuid[];
  path_edges uuid[];
  head_id uuid;
  neighbor_id uuid;
  edge_record record;
  unpacked_path_ordinal bigint;
  level integer;
  examined bigint := 0;
  was_truncated boolean := coalesce(max_depth, 2) > 4;
  reason text := CASE WHEN coalesce(max_depth, 2) > 4 THEN 'depth_budget' END;
BEGIN
  IF (valid_at IS NULL) <> (believed_at IS NULL) THEN
    RAISE EXCEPTION 'a bitemporal traversal takes both clocks or neither'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  <<levels>>
  FOR level IN 1..depth_cap LOOP
    next_frontier := ARRAY[]::jsonb[];
    unpacked_path_ordinal := NULL;
    -- Plan one indexed adjacency statement per BFS level, not one copy of the
    -- survivor/provenance views per frontier node.
    FOR edge_record IN
      SELECT path.current_path, path.path_ordinal, head.head_id, edge.*
      FROM unnest(frontier) WITH ORDINALITY
        AS path(current_path, path_ordinal)
      CROSS JOIN LATERAL (
        SELECT (path.current_path ->> 'head')::uuid AS head_id
      ) AS head
      CROSS JOIN LATERAL (
        SELECT candidate.*
        FROM (
          SELECT h.relation_id, h.subject_entity_id, h.object_entity_id
          FROM rememberstack_graph_internal.relations_history AS h
          WHERE h.deployment_id = graph_neighborhood.deployment_id
            AND h.subject_entity_id = head.head_id
            AND (
              (valid_at IS NULL
               AND h.ingested_at <= clock_believed
               AND h.invalidated_at IS NULL)
              OR
              (valid_at IS NOT NULL
               AND (h.ingested_at IS NULL OR h.ingested_at <= clock_believed)
               AND (h.invalidated_at IS NULL
                    OR h.invalidated_at > clock_believed))
            )
            AND (h.valid_from IS NULL OR h.valid_from <= clock_valid)
            AND (h.valid_until IS NULL OR h.valid_until > clock_valid)
            AND (predicates IS NULL OR h.predicate = ANY(predicates))
          UNION ALL
          SELECT h.relation_id, h.subject_entity_id, h.object_entity_id
          FROM rememberstack_graph_internal.relations_history AS h
          WHERE h.deployment_id = graph_neighborhood.deployment_id
            AND h.object_entity_id = head.head_id
            AND h.subject_entity_id <> head.head_id
            AND (
              (valid_at IS NULL
               AND h.ingested_at <= clock_believed
               AND h.invalidated_at IS NULL)
              OR
              (valid_at IS NOT NULL
               AND (h.ingested_at IS NULL OR h.ingested_at <= clock_believed)
               AND (h.invalidated_at IS NULL
                    OR h.invalidated_at > clock_believed))
            )
            AND (h.valid_from IS NULL OR h.valid_from <= clock_valid)
            AND (h.valid_until IS NULL OR h.valid_until > clock_valid)
            AND (predicates IS NULL OR h.predicate = ANY(predicates))
        ) AS candidate
        ORDER BY candidate.relation_id
      ) AS edge
      ORDER BY path.path_ordinal, edge.relation_id
      LIMIT greatest(expansion_cap - examined + 1, 1)
    LOOP
      head_id := edge_record.head_id;
      IF unpacked_path_ordinal IS DISTINCT FROM edge_record.path_ordinal THEN
        current_path := edge_record.current_path;
        SELECT coalesce(array_agg(value::uuid), ARRAY[]::uuid[])
          INTO path_nodes
          FROM jsonb_array_elements_text(current_path -> 'nodes') AS value;
        SELECT coalesce(array_agg(value::uuid), ARRAY[]::uuid[])
          INTO path_edges
          FROM jsonb_array_elements_text(current_path -> 'edges') AS value;
        unpacked_path_ordinal := edge_record.path_ordinal;
      END IF;
      IF examined >= expansion_cap THEN
        was_truncated := true;
        reason := 'expansion_budget';
        EXIT;
      END IF;
      examined := examined + 1;
      IF extract(epoch FROM clock_timestamp() - started_at) * 1000 > time_cap THEN
        was_truncated := true;
        reason := 'time_budget';
        EXIT;
      END IF;
      neighbor_id := CASE
        WHEN edge_record.subject_entity_id = head_id
        THEN edge_record.object_entity_id
        ELSE edge_record.subject_entity_id
      END;
      IF neighbor_id = ANY(path_nodes) OR neighbor_id = ANY(seen_vertices) THEN
        CONTINUE;
      END IF;
      candidate := jsonb_build_object(
        'head', neighbor_id::text,
        'nodes', to_jsonb(path_nodes || neighbor_id),
        'edges', to_jsonb(path_edges || edge_record.relation_id)
      );
      next_frontier := next_frontier || candidate;
      IF cardinality(next_frontier) > frontier_cap THEN
        next_frontier := next_frontier[1:frontier_cap];
        was_truncated := true;
        reason := 'frontier_budget';
        EXIT;
      END IF;
    END LOOP;

    SELECT coalesce(array_agg(item ORDER BY item -> 'edges'), ARRAY[]::jsonb[])
      INTO frontier
      FROM (
        SELECT DISTINCT ON ((candidate_item ->> 'head')::uuid) candidate_item AS item
        FROM unnest(next_frontier) AS candidate_item
        ORDER BY (candidate_item ->> 'head')::uuid, candidate_item -> 'edges'
      ) AS selected;
    IF cardinality(frontier) = 0 THEN
      EXIT;
    END IF;
    result_buffer := result_buffer || frontier;
    SELECT seen_vertices || coalesce(
      array_agg((item ->> 'head')::uuid), ARRAY[]::uuid[]
    ) INTO seen_vertices FROM unnest(frontier) AS item;
    IF was_truncated
       AND reason IN ('expansion_budget', 'frontier_budget', 'time_budget') THEN
      EXIT levels;
    END IF;
    IF cardinality(result_buffer) > result_cap THEN
      was_truncated := true;
      reason := 'result_budget';
      EXIT;
    END IF;
  END LOOP;

  returned_paths := least(cardinality(result_buffer), result_cap);
  FOR candidate IN
    SELECT item
    FROM unnest(result_buffer) AS item
    ORDER BY jsonb_array_length(item -> 'edges'), item -> 'edges', item -> 'nodes'
    LIMIT result_cap
  LOOP
    row_kind := 'data';
    hops := jsonb_array_length(candidate -> 'edges');
    SELECT array_agg(value::uuid) INTO relation_ids
      FROM jsonb_array_elements_text(candidate -> 'edges') AS value;
    SELECT array_agg(value::uuid) INTO node_ids
      FROM jsonb_array_elements_text(candidate -> 'nodes') AS value;
    truncated := was_truncated;
    truncation_reason := reason;
    examined_edges := examined;
    effective_depth := depth_cap;
    effective_expansion_budget := expansion_cap;
    effective_frontier_budget := frontier_cap;
    effective_result_budget := result_cap;
    effective_time_budget_ms := time_cap;
    applied_valid_at := clock_valid;
    applied_believed_at := clock_believed;
    RETURN NEXT;
  END LOOP;

  row_kind := 'status';
  hops := NULL;
  relation_ids := NULL;
  node_ids := NULL;
  truncated := was_truncated;
  truncation_reason := reason;
  examined_edges := examined;
  returned_paths := least(cardinality(result_buffer), result_cap);
  effective_depth := depth_cap;
  effective_expansion_budget := expansion_cap;
  effective_frontier_budget := frontier_cap;
  effective_result_budget := result_cap;
  effective_time_budget_ms := time_cap;
  applied_valid_at := clock_valid;
  applied_believed_at := clock_believed;
  RETURN NEXT;
END
$function$;
"""

_PATH_HELPER = r"""
CREATE FUNCTION memory_v1.graph_path(
  deployment_id uuid,
  from_entity_id uuid,
  to_entity_id uuid,
  max_depth integer DEFAULT 4,
  predicates text[] DEFAULT NULL,
  valid_at timestamptz DEFAULT NULL,
  believed_at timestamptz DEFAULT NULL,
  max_paths integer DEFAULT 3,
  expansion_budget integer DEFAULT 2000,
  frontier_budget integer DEFAULT 1000,
  time_budget_ms integer DEFAULT 1000
)
RETURNS TABLE (
  row_kind text,
  hops integer,
  relation_ids uuid[],
  node_ids uuid[],
  truncated boolean,
  truncation_reason text,
  examined_edges bigint,
  returned_paths bigint,
  effective_depth integer,
  effective_expansion_budget integer,
  effective_frontier_budget integer,
  effective_result_budget integer,
  effective_time_budget_ms integer,
  applied_valid_at timestamptz,
  applied_believed_at timestamptz
)
LANGUAGE plpgsql
STABLE
PARALLEL UNSAFE
SECURITY INVOKER
SET search_path = memory_v1, pg_catalog
AS $function$
#variable_conflict use_column
DECLARE
  depth_cap integer := least(greatest(coalesce(max_depth, 4), 1), 6);
  expansion_cap integer := least(greatest(coalesce(expansion_budget, 2000), 1), 2000);
  frontier_cap integer := least(greatest(coalesce(frontier_budget, 1000), 1), 1000);
  result_cap integer := least(greatest(coalesce(max_paths, 3), 1), 10);
  time_cap integer := least(greatest(coalesce(time_budget_ms, 1000), 1), 5000);
  started_at timestamptz := clock_timestamp();
  clock_valid timestamptz := coalesce(valid_at, statement_timestamp());
  clock_believed timestamptz := coalesce(believed_at, statement_timestamp());
  frontier jsonb[] := ARRAY[
    jsonb_build_object(
      'head', from_entity_id::text,
      'nodes', to_jsonb(ARRAY[from_entity_id]),
      'edges', '[]'::jsonb
    )
  ];
  next_frontier jsonb[] := ARRAY[]::jsonb[];
  found_paths jsonb[] := ARRAY[]::jsonb[];
  current_path jsonb;
  candidate jsonb;
  path_nodes uuid[];
  path_edges uuid[];
  head_id uuid;
  neighbor_id uuid;
  edge_record record;
  unpacked_path_ordinal bigint;
  level integer;
  examined bigint := 0;
  was_truncated boolean := coalesce(max_depth, 4) > 6;
  reason text := CASE WHEN coalesce(max_depth, 4) > 6 THEN 'depth_budget' END;
BEGIN
  IF (valid_at IS NULL) <> (believed_at IS NULL) THEN
    RAISE EXCEPTION 'a bitemporal traversal takes both clocks or neither'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  <<levels>>
  FOR level IN 1..depth_cap LOOP
    next_frontier := ARRAY[]::jsonb[];
    found_paths := ARRAY[]::jsonb[];
    unpacked_path_ordinal := NULL;
    -- Equal-length alternatives share one planned adjacency statement while
    -- path ordinality preserves the canonical deterministic expansion order.
    FOR edge_record IN
      SELECT path.current_path, path.path_ordinal, head.head_id, edge.*
      FROM unnest(frontier) WITH ORDINALITY
        AS path(current_path, path_ordinal)
      CROSS JOIN LATERAL (
        SELECT (path.current_path ->> 'head')::uuid AS head_id
      ) AS head
      CROSS JOIN LATERAL (
        SELECT candidate.*
        FROM (
          SELECT h.relation_id, h.subject_entity_id, h.object_entity_id
          FROM rememberstack_graph_internal.relations_history AS h
          WHERE h.deployment_id = graph_path.deployment_id
            AND h.subject_entity_id = head.head_id
            AND (
              (valid_at IS NULL
               AND h.ingested_at <= clock_believed
               AND h.invalidated_at IS NULL)
              OR
              (valid_at IS NOT NULL
               AND (h.ingested_at IS NULL OR h.ingested_at <= clock_believed)
               AND (h.invalidated_at IS NULL
                    OR h.invalidated_at > clock_believed))
            )
            AND (h.valid_from IS NULL OR h.valid_from <= clock_valid)
            AND (h.valid_until IS NULL OR h.valid_until > clock_valid)
            AND (predicates IS NULL OR h.predicate = ANY(predicates))
          UNION ALL
          SELECT h.relation_id, h.subject_entity_id, h.object_entity_id
          FROM rememberstack_graph_internal.relations_history AS h
          WHERE h.deployment_id = graph_path.deployment_id
            AND h.object_entity_id = head.head_id
            AND h.subject_entity_id <> head.head_id
            AND (
              (valid_at IS NULL
               AND h.ingested_at <= clock_believed
               AND h.invalidated_at IS NULL)
              OR
              (valid_at IS NOT NULL
               AND (h.ingested_at IS NULL OR h.ingested_at <= clock_believed)
               AND (h.invalidated_at IS NULL
                    OR h.invalidated_at > clock_believed))
            )
            AND (h.valid_from IS NULL OR h.valid_from <= clock_valid)
            AND (h.valid_until IS NULL OR h.valid_until > clock_valid)
            AND (predicates IS NULL OR h.predicate = ANY(predicates))
        ) AS candidate
        ORDER BY candidate.relation_id
      ) AS edge
      ORDER BY path.path_ordinal, edge.relation_id
      LIMIT greatest(expansion_cap - examined + 1, 1)
    LOOP
      head_id := edge_record.head_id;
      IF unpacked_path_ordinal IS DISTINCT FROM edge_record.path_ordinal THEN
        current_path := edge_record.current_path;
        SELECT coalesce(array_agg(value::uuid), ARRAY[]::uuid[])
          INTO path_nodes
          FROM jsonb_array_elements_text(current_path -> 'nodes') AS value;
        SELECT coalesce(array_agg(value::uuid), ARRAY[]::uuid[])
          INTO path_edges
          FROM jsonb_array_elements_text(current_path -> 'edges') AS value;
        unpacked_path_ordinal := edge_record.path_ordinal;
      END IF;
      IF examined >= expansion_cap THEN
        was_truncated := true;
        reason := 'expansion_budget';
        found_paths := ARRAY[]::jsonb[];
        EXIT levels;
      END IF;
      examined := examined + 1;
      IF extract(epoch FROM clock_timestamp() - started_at) * 1000 > time_cap THEN
        was_truncated := true;
        reason := 'time_budget';
        found_paths := ARRAY[]::jsonb[];
        EXIT levels;
      END IF;
      neighbor_id := CASE
        WHEN edge_record.subject_entity_id = head_id
        THEN edge_record.object_entity_id
        ELSE edge_record.subject_entity_id
      END;
      IF neighbor_id = ANY(path_nodes)
         OR edge_record.relation_id = ANY(path_edges) THEN
        CONTINUE;
      END IF;
      candidate := jsonb_build_object(
        'head', neighbor_id::text,
        'nodes', to_jsonb(path_nodes || neighbor_id),
        'edges', to_jsonb(path_edges || edge_record.relation_id)
      );
      IF neighbor_id = to_entity_id THEN
        found_paths := found_paths || candidate;
      ELSE
        next_frontier := next_frontier || candidate;
        IF cardinality(next_frontier) > frontier_cap THEN
          was_truncated := true;
          reason := 'frontier_budget';
          found_paths := ARRAY[]::jsonb[];
          EXIT levels;
        END IF;
      END IF;
    END LOOP;

    IF cardinality(found_paths) > 0 THEN
      EXIT;
    END IF;
    frontier := next_frontier;
    IF cardinality(frontier) = 0 THEN
      EXIT;
    END IF;
  END LOOP;

  IF NOT was_truncated AND cardinality(found_paths) > result_cap THEN
    was_truncated := true;
    reason := 'result_budget';
  END IF;
  returned_paths := CASE
    WHEN was_truncated AND reason IN ('expansion_budget', 'frontier_budget', 'time_budget')
    THEN 0
    ELSE least(cardinality(found_paths), result_cap)
  END;

  IF returned_paths > 0 THEN
    FOR candidate IN
      SELECT item
      FROM unnest(found_paths) AS item
      ORDER BY item -> 'edges', item -> 'nodes'
      LIMIT result_cap
    LOOP
      row_kind := 'data';
      hops := jsonb_array_length(candidate -> 'edges');
      SELECT array_agg(value::uuid) INTO relation_ids
        FROM jsonb_array_elements_text(candidate -> 'edges') AS value;
      SELECT array_agg(value::uuid) INTO node_ids
        FROM jsonb_array_elements_text(candidate -> 'nodes') AS value;
      truncated := was_truncated;
      truncation_reason := reason;
      examined_edges := examined;
      effective_depth := depth_cap;
      effective_expansion_budget := expansion_cap;
      effective_frontier_budget := frontier_cap;
      effective_result_budget := result_cap;
      effective_time_budget_ms := time_cap;
      applied_valid_at := clock_valid;
      applied_believed_at := clock_believed;
      RETURN NEXT;
    END LOOP;
  END IF;

  row_kind := 'status';
  hops := NULL;
  relation_ids := NULL;
  node_ids := NULL;
  truncated := was_truncated;
  truncation_reason := reason;
  examined_edges := examined;
  effective_depth := depth_cap;
  effective_expansion_budget := expansion_cap;
  effective_frontier_budget := frontier_cap;
  effective_result_budget := result_cap;
  effective_time_budget_ms := time_cap;
  applied_valid_at := clock_valid;
  applied_believed_at := clock_believed;
  RETURN NEXT;
END
$function$;
"""

_CITATION_PATH_HELPER = r"""
CREATE FUNCTION memory_v1.graph_citation_path(
  deployment_id uuid,
  from_doc_id uuid,
  to_doc_id uuid,
  max_depth integer DEFAULT 6,
  max_paths integer DEFAULT 3,
  expansion_budget integer DEFAULT 2000,
  frontier_budget integer DEFAULT 1000,
  time_budget_ms integer DEFAULT 1000
)
RETURNS TABLE (
  row_kind text,
  hops integer,
  crossref_ids uuid[],
  document_ids uuid[],
  truncated boolean,
  truncation_reason text,
  examined_edges bigint,
  returned_paths bigint,
  effective_depth integer,
  effective_expansion_budget integer,
  effective_frontier_budget integer,
  effective_result_budget integer,
  effective_time_budget_ms integer,
  evaluated_at timestamptz
)
LANGUAGE plpgsql
STABLE
PARALLEL UNSAFE
SECURITY INVOKER
SET search_path = memory_v1, pg_catalog
AS $function$
#variable_conflict use_column
DECLARE
  depth_cap integer := least(greatest(coalesce(max_depth, 6), 1), 6);
  expansion_cap integer := least(greatest(coalesce(expansion_budget, 2000), 1), 2000);
  frontier_cap integer := least(greatest(coalesce(frontier_budget, 1000), 1), 1000);
  result_cap integer := least(greatest(coalesce(max_paths, 3), 1), 10);
  time_cap integer := least(greatest(coalesce(time_budget_ms, 1000), 1), 5000);
  started_at timestamptz := clock_timestamp();
  operation_at timestamptz := statement_timestamp();
  frontier jsonb[] := ARRAY[
    jsonb_build_object(
      'head', from_doc_id::text,
      'nodes', to_jsonb(ARRAY[from_doc_id]),
      'edges', '[]'::jsonb
    )
  ];
  next_frontier jsonb[] := ARRAY[]::jsonb[];
  found_paths jsonb[] := ARRAY[]::jsonb[];
  current_path jsonb;
  candidate jsonb;
  path_nodes uuid[];
  path_edges uuid[];
  head_id uuid;
  edge_record record;
  level integer;
  examined bigint := 0;
  was_truncated boolean := coalesce(max_depth, 6) > 6;
  reason text := CASE WHEN coalesce(max_depth, 6) > 6 THEN 'depth_budget' END;
BEGIN
  <<levels>>
  FOR level IN 1..depth_cap LOOP
    next_frontier := ARRAY[]::jsonb[];
    found_paths := ARRAY[]::jsonb[];
    FOREACH current_path IN ARRAY frontier LOOP
      head_id := (current_path ->> 'head')::uuid;
      SELECT coalesce(array_agg(value::uuid), ARRAY[]::uuid[])
        INTO path_nodes
        FROM jsonb_array_elements_text(current_path -> 'nodes') AS value;
      SELECT coalesce(array_agg(value::uuid), ARRAY[]::uuid[])
        INTO path_edges
        FROM jsonb_array_elements_text(current_path -> 'edges') AS value;

      FOR edge_record IN
        SELECT x.crossref_id, x.to_doc_id
        FROM memory_v1.document_crossrefs_live AS x
        WHERE x.deployment_id = graph_citation_path.deployment_id
          AND x.from_doc_id = head_id
        ORDER BY x.crossref_id
        LIMIT greatest(expansion_cap - examined + 1, 1)
      LOOP
        IF examined >= expansion_cap THEN
          was_truncated := true;
          reason := 'expansion_budget';
          found_paths := ARRAY[]::jsonb[];
          EXIT levels;
        END IF;
        examined := examined + 1;
        IF extract(epoch FROM clock_timestamp() - started_at) * 1000 > time_cap THEN
          was_truncated := true;
          reason := 'time_budget';
          found_paths := ARRAY[]::jsonb[];
          EXIT levels;
        END IF;
        IF edge_record.to_doc_id = ANY(path_nodes)
           OR edge_record.crossref_id = ANY(path_edges) THEN
          CONTINUE;
        END IF;
        candidate := jsonb_build_object(
          'head', edge_record.to_doc_id::text,
          'nodes', to_jsonb(path_nodes || edge_record.to_doc_id),
          'edges', to_jsonb(path_edges || edge_record.crossref_id)
        );
        IF edge_record.to_doc_id = graph_citation_path.to_doc_id THEN
          found_paths := found_paths || candidate;
        ELSE
          next_frontier := next_frontier || candidate;
          IF cardinality(next_frontier) > frontier_cap THEN
            was_truncated := true;
            reason := 'frontier_budget';
            found_paths := ARRAY[]::jsonb[];
            EXIT levels;
          END IF;
        END IF;
      END LOOP;
    END LOOP;

    IF cardinality(found_paths) > 0 THEN
      EXIT;
    END IF;
    frontier := next_frontier;
    IF cardinality(frontier) = 0 THEN
      EXIT;
    END IF;
  END LOOP;

  IF NOT was_truncated AND cardinality(found_paths) > result_cap THEN
    was_truncated := true;
    reason := 'result_budget';
  END IF;
  returned_paths := CASE
    WHEN was_truncated AND reason IN ('expansion_budget', 'frontier_budget', 'time_budget')
    THEN 0
    ELSE least(cardinality(found_paths), result_cap)
  END;

  IF returned_paths > 0 THEN
    FOR candidate IN
      SELECT item
      FROM unnest(found_paths) AS item
      ORDER BY item -> 'edges', item -> 'nodes'
      LIMIT result_cap
    LOOP
      row_kind := 'data';
      hops := jsonb_array_length(candidate -> 'edges');
      SELECT array_agg(value::uuid) INTO crossref_ids
        FROM jsonb_array_elements_text(candidate -> 'edges') AS value;
      SELECT array_agg(value::uuid) INTO document_ids
        FROM jsonb_array_elements_text(candidate -> 'nodes') AS value;
      truncated := was_truncated;
      truncation_reason := reason;
      examined_edges := examined;
      effective_depth := depth_cap;
      effective_expansion_budget := expansion_cap;
      effective_frontier_budget := frontier_cap;
      effective_result_budget := result_cap;
      effective_time_budget_ms := time_cap;
      evaluated_at := operation_at;
      RETURN NEXT;
    END LOOP;
  END IF;

  row_kind := 'status';
  hops := NULL;
  crossref_ids := NULL;
  document_ids := NULL;
  truncated := was_truncated;
  truncation_reason := reason;
  examined_edges := examined;
  effective_depth := depth_cap;
  effective_expansion_budget := expansion_cap;
  effective_frontier_budget := frontier_cap;
  effective_result_budget := result_cap;
  effective_time_budget_ms := time_cap;
  evaluated_at := operation_at;
  RETURN NEXT;
END
$function$;
"""

_GRAPH_ROLE = r"""
DO $do$
DECLARE
  query_role text := 'rememberstack_query_' || current_database();
  graph_role text := 'rememberstack_graph_' || current_database();
  owner_role text := current_user;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = graph_role) THEN
    EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT', graph_role);
  END IF;
  EXECUTE format('ALTER ROLE %I NOLOGIN NOINHERIT', graph_role);
  EXECUTE format('REVOKE %I FROM %I', query_role, graph_role);
  EXECUTE format('GRANT %I TO %I', graph_role, owner_role);
END
$do$;
"""

_GRANTS = r"""
DO $do$
DECLARE
  query_role text := 'rememberstack_query_' || current_database();
  graph_role text := 'rememberstack_graph_' || current_database();
BEGIN
  EXECUTE format(
    'GRANT USAGE ON SCHEMA rememberstack_graph_internal TO %I', query_role
  );
  EXECUTE format(
    'GRANT SELECT ON ALL TABLES IN SCHEMA rememberstack_graph_internal TO %I',
    query_role
  );
  EXECUTE format(
    'GRANT SELECT ON PROPERTY GRAPH memory_v1.memory_current TO %I', query_role
  );
  EXECUTE format(
    'GRANT SELECT ON PROPERTY GRAPH memory_v1.memory_history TO %I', query_role
  );
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION memory_v1.graph_neighborhood(uuid, uuid, integer, text[], timestamptz, timestamptz, integer, integer, integer, integer) TO %I',
    query_role
  );
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION memory_v1.graph_path(uuid, uuid, uuid, integer, text[], timestamptz, timestamptz, integer, integer, integer, integer) TO %I',
    query_role
  );
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION memory_v1.graph_citation_path(uuid, uuid, uuid, integer, integer, integer, integer, integer) TO %I',
    query_role
  );
  EXECUTE format(
    'GRANT USAGE ON SCHEMA rememberstack_graph_internal TO %I', graph_role
  );
  EXECUTE format('GRANT USAGE ON SCHEMA memory_v1 TO %I', graph_role);
  EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', graph_role);
  EXECUTE format(
    'GRANT SELECT ON ALL TABLES IN SCHEMA rememberstack_graph_internal TO %I',
    graph_role
  );
  EXECUTE format(
    'GRANT SELECT ON memory_v1.entities_current, memory_v1.documents_live, memory_v1.graph_edges_visible_history, memory_v1.document_crossrefs_live, memory_v1.entity_document_mentions TO %I',
    graph_role
  );
  EXECUTE format(
    'GRANT SELECT (deployment_id, relation_id, subject_entity_id, object_entity_id, predicate, valid_from, valid_until, ingested_at, invalidated_at) ON public.relations TO %I',
    graph_role
  );
  EXECUTE format(
    'GRANT SELECT (deployment_id, entity_id, survivor_entity_id) ON public.v_memory_entity_survivor TO %I',
    graph_role
  );
  EXECUTE format(
    'GRANT SELECT ON PROPERTY GRAPH memory_v1.memory_current TO %I', graph_role
  );
  EXECUTE format(
    'GRANT SELECT ON PROPERTY GRAPH memory_v1.memory_history TO %I', graph_role
  );
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION memory_v1.graph_neighborhood(uuid, uuid, integer, text[], timestamptz, timestamptz, integer, integer, integer, integer) TO %I',
    graph_role
  );
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION memory_v1.graph_path(uuid, uuid, uuid, integer, text[], timestamptz, timestamptz, integer, integer, integer, integer) TO %I',
    graph_role
  );
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION memory_v1.graph_citation_path(uuid, uuid, uuid, integer, integer, integer, integer, integer) TO %I',
    graph_role
  );
END
$do$;
"""

_REVOKE_GRAPH_GUARD_GRANTS = r"""
DO $do$
DECLARE
  graph_role text := 'rememberstack_graph_' || current_database();
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = graph_role) THEN
    EXECUTE format(
      'REVOKE SELECT (deployment_id, relation_id, subject_entity_id, object_entity_id, predicate, valid_from, valid_until, ingested_at, invalidated_at) ON public.relations FROM %I',
      graph_role
    );
    EXECUTE format(
      'REVOKE SELECT (deployment_id, entity_id, survivor_entity_id) ON public.v_memory_entity_survivor FROM %I',
      graph_role
    );
    EXECUTE format('REVOKE USAGE ON SCHEMA public FROM %I', graph_role);
  END IF;
END
$do$;
"""

_HELPER_COMMENTS = f"""
COMMENT ON FUNCTION memory_v1.graph_neighborhood(
  uuid, uuid, integer, text[], timestamptz, timestamptz,
  integer, integer, integer, integer
) IS '{GRAPH_HELPER_CONTRACT_VERSION} graph_neighborhood';
COMMENT ON FUNCTION memory_v1.graph_path(
  uuid, uuid, uuid, integer, text[], timestamptz, timestamptz,
  integer, integer, integer, integer
) IS '{GRAPH_HELPER_CONTRACT_VERSION} graph_path';
COMMENT ON FUNCTION memory_v1.graph_citation_path(
  uuid, uuid, uuid, integer, integer, integer, integer, integer
) IS '{GRAPH_HELPER_CONTRACT_VERSION} graph_citation_path';
"""

# The NOLOGIN graph role's rolconfig is an audited defensive baseline. Product
# callers set and verify the same values transaction-locally before SET ROLE,
# because PostgreSQL does not apply ALTER ROLE ... SET merely on role switch.
_ROLE_LIMITS = r"""
DO $do$
DECLARE
  query_role text := 'rememberstack_query_' || current_database();
  graph_role text := 'rememberstack_graph_' || current_database();
BEGIN
  EXECUTE format('ALTER ROLE %I RESET ALL', query_role);
  EXECUTE format('ALTER ROLE %I SET statement_timeout = 60000', query_role);
  EXECUTE format('ALTER ROLE %I SET lock_timeout = 2000', query_role);
  EXECUTE format(
    'ALTER ROLE %I SET idle_in_transaction_session_timeout = 5000', query_role
  );
  EXECUTE format('ALTER ROLE %I SET temp_file_limit = ''65536kB''', query_role);
  EXECUTE format('ALTER ROLE %I SET default_transaction_read_only = on', query_role);
  EXECUTE format('ALTER ROLE %I SET search_path = memory_v1, pg_catalog', query_role);
  EXECUTE format('ALTER ROLE %I SET max_parallel_workers_per_gather = 0', query_role);
  EXECUTE format('ALTER ROLE %I RESET ALL', graph_role);
  EXECUTE format('ALTER ROLE %I SET statement_timeout = 5000', graph_role);
  EXECUTE format('ALTER ROLE %I SET lock_timeout = 500', graph_role);
  EXECUTE format(
    'ALTER ROLE %I SET idle_in_transaction_session_timeout = 5000', graph_role
  );
  EXECUTE format('ALTER ROLE %I SET transaction_timeout = 6000', graph_role);
  EXECUTE format('ALTER ROLE %I SET temp_file_limit = ''65536kB''', graph_role);
  EXECUTE format('ALTER ROLE %I SET work_mem = ''16384kB''', graph_role);
  EXECUTE format('ALTER ROLE %I SET default_transaction_read_only = on', graph_role);
  EXECUTE format('ALTER ROLE %I SET search_path = memory_v1, pg_catalog', graph_role);
  EXECUTE format('ALTER ROLE %I SET max_parallel_workers_per_gather = 0', graph_role);
END
$do$;
"""

_RESTORE_OLD_ENUMS = r"""
CREATE TYPE projection_plane_legacy AS ENUM ('P1_search', 'P2_graph', 'P3_corpusfs');
ALTER TABLE projection_snapshots
  ALTER COLUMN plane TYPE projection_plane_legacy
  USING plane::text::projection_plane_legacy;
DROP TYPE projection_plane;
ALTER TYPE projection_plane_legacy RENAME TO projection_plane;
CREATE TYPE community_algorithm AS ENUM ('leiden', 'louvain');

CREATE TABLE communities (
  community_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES deployments,
  snapshot_id uuid NOT NULL,
  label text,
  size integer NOT NULL,
  algorithm community_algorithm NOT NULL,
  detected_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, community_id),
  FOREIGN KEY (deployment_id, snapshot_id)
    REFERENCES projection_snapshots (deployment_id, snapshot_id) ON DELETE CASCADE
);

CREATE TABLE entity_graph_metrics (
  deployment_id uuid NOT NULL REFERENCES deployments ON DELETE CASCADE,
  entity_id uuid NOT NULL,
  snapshot_id uuid NOT NULL,
  community_id uuid,
  pagerank double precision,
  degree integer,
  k_core integer,
  component_id uuid,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, entity_id, snapshot_id),
  FOREIGN KEY (deployment_id, entity_id)
    REFERENCES entities (deployment_id, entity_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, snapshot_id)
    REFERENCES projection_snapshots (deployment_id, snapshot_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, community_id)
    REFERENCES communities (deployment_id, community_id)
    ON DELETE SET NULL (community_id)
);
CREATE INDEX ix_communities_snapshot ON communities (snapshot_id);
CREATE INDEX ix_egm_entity ON entity_graph_metrics (entity_id);
CREATE INDEX ix_egm_snapshot ON entity_graph_metrics (snapshot_id);
"""

_RESTORE_P2_PIPELINE_VOCABULARY = r"""
ALTER TYPE pipeline_stage ADD VALUE IF NOT EXISTS 'build_snapshot';
ALTER TYPE pipeline_stage ADD VALUE IF NOT EXISTS 'detect_communities';
ALTER TYPE pipeline_component ADD VALUE IF NOT EXISTS 'community_detector';
ALTER TYPE pipeline_component ADD VALUE IF NOT EXISTS 'snapshot_builder';
"""


def upgrade() -> None:
    """Create live graph metadata and delete obsolete P2 aggregate state."""
    apply_ddl(sql=_DROP_OLD_GRAPH_STATE)
    apply_ddl(sql=_DROP_P2_VOCABULARY)
    apply_ddl(sql=_DROP_LEGACY_GRAPH_EXPORTS)
    apply_ddl(sql=_ALIGN_PUBLIC_CROSSREF_VIEW)
    apply_ddl(sql=_GRAPH_SOURCES)
    op.execute(_CURRENT_GRAPH)
    op.execute(_HISTORY_GRAPH)
    op.execute("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA memory_v1 FROM PUBLIC")
    op.execute(_NEIGHBORHOOD_HELPER)
    op.execute(_PATH_HELPER)
    op.execute(_CITATION_PATH_HELPER)
    apply_ddl(sql=_HELPER_COMMENTS)
    op.execute(_GRAPH_ROLE)
    op.execute(_ROLE_LIMITS)
    op.execute("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA memory_v1 FROM PUBLIC")
    op.execute(_GRANTS)


def downgrade() -> None:
    """Restore legacy graph objects, but not deleted state or unsafe crossrefs."""
    from rememberstack.spine.migrations.versions.p9_05_0026_graph_helpers import (
        _GRANT as legacy_grant,
    )
    from rememberstack.spine.migrations.versions.p9_05_0026_graph_helpers import (
        _NEIGHBORHOOD_DDL as legacy_neighborhood,
    )
    from rememberstack.spine.migrations.versions.p9_05_0026_graph_helpers import (
        _NEIGHBORHOOD_SIGNATURE as legacy_neighborhood_signature,
    )
    from rememberstack.spine.migrations.versions.p9_05_0026_graph_helpers import (
        _PATH_DDL as legacy_path,
    )
    from rememberstack.spine.migrations.versions.p9_05_0026_graph_helpers import (
        _PATH_SIGNATURE as legacy_path_signature,
    )
    from rememberstack.spine.migrations.versions.p9_14_0035_drop_entity_type import (
        _V_GRAPH_ENTITIES_TYPE_CUT as type_cut_graph_entities,
    )

    op.execute("DROP PROPERTY GRAPH IF EXISTS memory_v1.memory_history")
    op.execute("DROP PROPERTY GRAPH IF EXISTS memory_v1.memory_current")
    op.execute(_REVOKE_GRAPH_GUARD_GRANTS)
    op.execute(
        "DROP FUNCTION IF EXISTS memory_v1.graph_citation_path(uuid, uuid, uuid, integer, integer, integer, integer, integer)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS memory_v1.graph_path(uuid, uuid, uuid, integer, text[], timestamptz, timestamptz, integer, integer, integer, integer)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS memory_v1.graph_neighborhood(uuid, uuid, integer, text[], timestamptz, timestamptz, integer, integer, integer, integer)"
    )
    # Development branches briefly placed the private sources in memory_v1.
    # Removing those names makes iterative downgrade testing recoverable and is
    # a no-op for every clean installation of this revision.
    for legacy_source in (
        "graph_crossrefs_live_source",
        "graph_relations_current_source",
        "graph_relations_history_source",
        "graph_documents_live_source",
        "graph_entities_live_source",
    ):
        op.execute(f"DROP VIEW IF EXISTS memory_v1.{legacy_source} CASCADE")
    op.execute("DROP SCHEMA IF EXISTS rememberstack_graph_internal CASCADE")
    # Iterative branch databases may have reached this revision before D98
    # began deleting the six obsolete snapshot-export views.
    apply_ddl(sql=_DROP_LEGACY_GRAPH_EXPORTS)
    from rememberstack.spine.migrations.versions.p0_02_0006_partitions_views import (
        _VIEW_DDL as legacy_graph_views,
    )

    for statement in _split_sql(sql=legacy_graph_views):
        if "CREATE VIEW v_graph_survivor AS" in statement:
            continue
        if "CREATE VIEW v_graph_entities AS" in statement:
            op.execute(type_cut_graph_entities)
            continue
        op.execute(statement)
    apply_ddl(sql=_RESTORE_P2_PIPELINE_VOCABULARY)
    apply_ddl(sql=_RESTORE_OLD_ENUMS)
    op.execute(legacy_neighborhood)
    op.execute(legacy_path)
    op.execute(legacy_grant.format(signature=legacy_neighborhood_signature))
    op.execute(legacy_grant.format(signature=legacy_path_signature))
