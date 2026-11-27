# Hosted fact eligibility capture, 2026-11-27

The [investigation](evidence_driven_fact_eligibility_20261127.md) uses these
read-only PostgreSQL 18.6 captures from the v0.22.4 store at
`p9_55_0076`. This receipt includes redacted plan excerpts and the paired
SQL. The excerpts preserve measured work and timings while omitting plan
expressions that contain deployment IDs and candidate UUID arrays.

| Original file | Capture (UTC) | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `live-plans.out` | 18:35:28 | 16,268,570 | `90df055ba4f69a619a6bede66f07e3eacc81323c0b03ac98972f27c4d7687cfb` |
| `eligibility.sql` | 18:35 query input | 17,858 | `299b319cb2a9d7bd509227cd4e680c309613831ea396baed6b42bb23c6f56df7` |
| `candidate-driven-plans.out` | 18:36:54 | 33,487,415 | `09097508f25f5eb87d1708526c6ea1320491bbd22dabdb14f14eb868ccc60288` |
| `candidate-driven.sql` | 18:36 query input | 28,494 | `c45438253b340315468b5ddae8a31eae6b51ae658fceacbfa8f206a753a3b47a` |
| `native-replay.out` | 18:38:34 | 861 | `9e99af6442787c052e1b73ec8261d0197e057f9fdfad278cf5cf2460f6cbe1db` |

## Redacted plan excerpts

All four `EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, FORMAT JSON)` captures
record `effective_cache_size = '3GB'` and `jit = off` under normal join
planning. The statements ran in read-only repeatable-read transactions,
with a 60 s statement timeout and 2 s lock timeout. Shared reads and temp
writes were zero on all four. The paired executor times include both
branches and the difference query. The branch times below come from their
materialized CTE roots.

| Statement | Kind | Output rows | Planning ms | Execution ms | Shared hits |
| --- | --- | ---: | ---: | ---: | ---: |
| Live confirmation | observation | 786 | 197.503 | 15683.395 | 8,155,986 |
| Live confirmation | relation | 518 | 142.717 | 12177.758 | 5,509,219 |
| Paired difference | observation | 0 | 394.357 | 16892.583 | 8,226,600 |
| Paired difference | relation | 0 | 287.996 | 14298.528 | 6,087,104 |

These rows come from the executing supports-only subplans in the paired
statement. Leaf loops sum `Actual Loops` across children in each
partitioned scan. Both `EXCEPT` nodes returned zero rows for each kind.

| Kind | CTE branch | Eligible rows | Total ms | Shared hits | Gate calls | Evidence leaf loops | `chunk_claims` leaf loops |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| observation | `baseline` | 786 | 16400.040 | 8,155,984 | 786 | 1,232,750 | 3,093,280 |
| observation | `candidate_driven` | 786 | 433.877 | 70,616 | 786 | 786 | 3,930 |
| relation | `baseline` | 518 | 11348.544 | 5,509,219 | 518 | 757,668 | 1,884,710 |
| relation | `candidate_driven` | 518 | 2918.937 | 577,885 | 518 | 518 | 2,590 |

The redacted relation plan shows how the runtime order changed. The
`Append` nodes collect rows from partition leaves. Loop counts at the leaf
level are in the table above. The same baseline order appears in the
standalone relation confirmation.

```text
CTE baseline (518 rows, 1 loop)
  Subquery Scan indexed (535 loops)
    support SubPlan: Nested Loop (518 loops, 4,945,320 shared hits)
      Nested Loop (1,462.68 rows/loop, 518 loops)
        chunks joined to document_version_scope (364.34 chunk rows/loop)
        Append chunk_claims (4.01 rows/loop, 188,730 loops)
          leaf index conditions use chunk_id
      Append relation_evidence (757,668 loops)
        leaf index conditions use relation_id and claim_id

CTE candidate_driven (518 rows, 1 loop)
  Subquery Scan indexed (535 loops)
    support SubPlan: Nested Loop (518 loops, 13,986 shared hits)
      Append relation_evidence (1 row/loop, 518 loops)
      Append chunk_claims by claim_id (1 row/loop, 518 loops)
      Append chunks by chunk_id (1 row/loop, 518 loops)
      version and document_version_scope by occurrence coordinates
```

Both relation branches still execute the subject and object
`Recursive Union` under `CTE fact_sources` 535 and 518,415 times,
respectively. The paired prototype measured the time gate only.

## Native replay excerpt

```json
{
  "remember": "0.22.4",
  "transaction_read_only": "on",
  "instant_utc": "2026-11-27T18:38:34.573539+00:00",
  "wall_seconds": 30.536,
  "sql_seconds": 30.2135,
  "sql_statements": 154,
  "returned_facts": 50,
  "returned_evidence": 40,
  "negative": null,
  "stored_vector_uses": 1,
  "paid_queries": 0,
  "confirmation_statements": [
    {
      "kind": "relation",
      "input_ids": 535,
      "eligible_rows": 518,
      "seconds": 9.8666
    },
    {
      "kind": "observation",
      "input_ids": 804,
      "eligible_rows": 786,
      "seconds": 13.2241
    }
  ]
}
```

## Paired SQL

The captured statement compares the original and evidence-first gate for
each fact kind, under the same snapshot and with the same selected IDs.
The capture SQL below replaces the hosted deployment and role with psql
prompts and reflows long lines.

<details>
<summary>Paired observation and relation capture SQL</summary>

```sql
\pset pager off
\prompt 'Runtime role: ' runtime_role
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '2s';
SELECT transaction_timestamp() AS evaluated_at \gset
\prompt 'Deployment UUID: ' deployment_id
SELECT current_setting('transaction_read_only') AS read_only, :'evaluated_at' AS evaluated_at;
\echo BEGIN_STATS
SELECT parent.relname AS partition_root, pg_get_partkeydef(parent.oid), count(*) AS leaves,
       count(*) FILTER (WHERE leaf.relpages > 0) AS nonzero_relpages,
       sum(GREATEST(leaf.reltuples,0))::bigint AS estimated,
       sum(s.n_live_tup) AS n_live_tup, sum(s.n_mod_since_analyze) AS changed_since,
       count(s.last_analyze) AS manually_analyzed_leaves,
       count(s.last_autoanalyze) AS autoanalyzed_leaves,
       max(s.last_analyze), max(s.last_autoanalyze),
       bool_and(leaf.reloptions IS NULL) AS no_leaf_overrides
FROM pg_inherits h JOIN pg_class parent ON parent.oid=h.inhparent
JOIN pg_class leaf ON leaf.oid=h.inhrelid
JOIN pg_stat_user_tables s ON s.relid=leaf.oid
WHERE parent.relname IN ('relation_evidence','observation_evidence','resolution_decisions','chunk_claims','chunks','mentions')
GROUP BY 1,2 ORDER BY 1;
SELECT x.relname, count(*) FILTER (WHERE ix.indisvalid AND ix.indisready),count(*)
FROM pg_index ix JOIN pg_class x ON x.oid=ix.indrelid
WHERE x.relname ~ '^(observation_evidence|relation_evidence)($|_)'
GROUP BY x.relname
HAVING NOT bool_and(ix.indisvalid AND ix.indisready);
SELECT 'chunk_claims_by_chunk', count(*), count(*) FILTER (WHERE links > 0), round(avg(links) FILTER (WHERE links > 0),2), max(links)
FROM (SELECT c.chunk_id,count(cc.claim_id) AS links FROM chunks c LEFT JOIN chunk_claims cc
   ON cc.deployment_id=c.deployment_id AND cc.chunk_id=c.chunk_id
   WHERE c.deployment_id=:'deployment_id' GROUP BY c.chunk_id) chunk_links;
SELECT 'chunk_claims_by_claim', count(*), count(*) FILTER (WHERE anchors > 1), max(anchors)
FROM (SELECT claim_id,count(*) AS anchors FROM chunk_claims WHERE deployment_id=:'deployment_id' GROUP BY claim_id) claim_links;
SELECT 'nonempty_evidence_partitions',
 (SELECT count(DISTINCT tableoid) FROM observation_evidence WHERE deployment_id=:'deployment_id'),
 (SELECT count(DISTINCT tableoid) FROM relation_evidence WHERE deployment_id=:'deployment_id');
SELECT 'D153_document_bridge_rows', count(*) FROM documents WHERE deployment_id=:'deployment_id' AND document_entity_id IS NOT NULL;
SELECT s.relname,c.reltuples::bigint,s.n_live_tup,s.n_mod_since_analyze,s.last_analyze,s.last_autoanalyze
FROM pg_stat_user_tables s JOIN pg_class c ON c.oid=s.relid
WHERE s.relname IN ('chunk_claims','chunks','mentions','observations','relations','claims')
   OR s.relname ~ '^(observation_evidence|relation_evidence)_(p0|p1|p11|p27|p33|p61|p62)$'
ORDER BY s.relname;
\echo END_STATS
SET LOCAL ROLE :"runtime_role";


SELECT count(*) AS observation_count, array_agg(observation_id) AS observation_batch_ids
FROM (
 SELECT observation_id FROM observations WHERE deployment_id=:'deployment_id' AND
 embedding IS NOT NULL AND embedding_input_policy_version='fact-label-v2:chosen-window'
 ORDER BY embedding <=> (SELECT embedding FROM observations WHERE deployment_id=:'deployment_id'
     AND embedding IS NOT NULL AND vector_norm(embedding)>0 ORDER BY observation_id LIMIT 1)
 LIMIT 804
) nominated \gset
\echo BATCH_observation :observation_count

\echo BEGIN_PLAN_observation
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, FORMAT JSON)
    WITH baseline AS MATERIALIZED (
WITH
    fact_sources AS MATERIALIZED (
        SELECT source.deployment_id, source.fact_kind, source.fact_id, source.subject_entity_id,
    source.object_entity_id, source.predicate, source.valid_from, source.valid_until, source.ingested_at,
    source.invalidated_at, source.contradiction_group, source.confidence, source.valid_precision
        FROM (SELECT DISTINCT fact_kind,fact_id FROM (SELECT 'observation'::text AS fact_kind,
    unnest(CAST(:'observation_batch_ids' AS uuid[])) AS fact_id) input) requested
        CROSS JOIN LATERAL (
            SELECT fact.deployment_id, fact.fact_kind, fact.fact_id, fact.subject_entity_id, fact.object_entity_id,
    fact.predicate, fact.valid_from, fact.valid_until, fact.ingested_at, fact.invalidated_at,
    fact.contradiction_group, fact.confidence, fact.valid_precision
            FROM public.v_memory_fact_source_qualified AS fact
            WHERE fact.deployment_id = :'deployment_id'
              AND fact.fact_kind = requested.fact_kind
              AND fact.fact_id = requested.fact_id
            OFFSET 0
        ) source
    ), fact_endpoint_ids AS MATERIALIZED (
        SELECT subject_entity_id AS entity_id FROM fact_sources
        UNION SELECT object_entity_id FROM fact_sources WHERE object_entity_id IS NOT NULL
    ), fact_visible_endpoints AS MATERIALIZED (
        SELECT visible.entity_id FROM fact_endpoint_ids endpoint
        CROSS JOIN LATERAL (
            SELECT entity.entity_id FROM memory_v1.entities_current AS entity
            WHERE entity.deployment_id = :'deployment_id'
              AND entity.entity_id = endpoint.entity_id
            OFFSET 0
        ) visible
    ), fact_eligible_keys AS MATERIALIZED (
        SELECT source.fact_kind, source.fact_id FROM fact_sources source
        JOIN fact_visible_endpoints subject ON subject.entity_id = source.subject_entity_id
        WHERE source.fact_kind = 'observation' OR EXISTS (
            SELECT 1 FROM fact_visible_endpoints object
            WHERE object.entity_id = source.object_entity_id
        )
    ), eligible_facts AS NOT MATERIALIZED (SELECT source.* FROM fact_sources source JOIN fact_eligible_keys eligible
    ON eligible.fact_kind=source.fact_kind AND eligible.fact_id=source.fact_id)
     SELECT indexed.observation_id::text AS item_id
FROM eligible_facts AS fact CROSS JOIN LATERAL (
    SELECT indexed.* FROM observations indexed
    WHERE indexed.deployment_id=fact.deployment_id
    AND indexed.observation_id=fact.fact_id
    AND fact.fact_kind='observation' OFFSET 0
) indexed
WHERE indexed.deployment_id=:'deployment_id'

AND indexed.ingested_at <= :'evaluated_at' AND indexed.invalidated_at IS NULL
AND (indexed.valid_from IS NULL OR indexed.valid_from <= :'evaluated_at')
AND (indexed.valid_until IS NULL OR indexed.valid_until > :'evaluated_at')
 AND (EXISTS (SELECT 1 FROM public.observation_evidence gate_observation_evidence JOIN public.chunk_claims
    gate_observation_occurrence   ON gate_observation_occurrence.deployment_id =
    gate_observation_evidence.deployment_id  AND gate_observation_occurrence.claim_id =
    gate_observation_evidence.claim_id JOIN public.chunks gate_observation_chunk   ON
    gate_observation_chunk.deployment_id = gate_observation_occurrence.deployment_id  AND
    gate_observation_chunk.chunk_id = gate_observation_occurrence.chunk_id JOIN public.document_versions
    gate_observation_version   ON gate_observation_version.deployment_id = gate_observation_chunk.deployment_id  AND
    gate_observation_version.version_id = gate_observation_chunk.version_id  AND
    gate_observation_version.current_representation_id = gate_observation_chunk.representation_id JOIN
    public.document_version_scope gate_observation_scope   ON gate_observation_scope.deployment_id =
    gate_observation_chunk.deployment_id  AND gate_observation_scope.version_id = gate_observation_chunk.version_id
    WHERE gate_observation_evidence.deployment_id = :'deployment_id'   AND gate_observation_evidence.observation_id
    = indexed.observation_id   AND gate_observation_evidence.stance = 'supports' AND (NOT
    gate_observation_scope.periodised OR (gate_observation_scope.selectable AND gate_observation_scope.in_force &&
    tstzrange(CAST(:'evaluated_at' AS timestamptz), CAST(:'evaluated_at' AS timestamptz), '[]')))) OR (NOT EXISTS
    (SELECT 1 FROM public.observation_evidence gate_observation_any WHERE gate_observation_any.deployment_id =
    :'deployment_id' AND gate_observation_any.observation_id = indexed.observation_id AND
    gate_observation_any.stance = 'supports') AND EXISTS (SELECT 1 FROM public.observation_evidence
    gate_observation_all_evidence JOIN public.chunk_claims gate_observation_all_occurrence   ON
    gate_observation_all_occurrence.deployment_id = gate_observation_all_evidence.deployment_id  AND
    gate_observation_all_occurrence.claim_id = gate_observation_all_evidence.claim_id JOIN public.chunks
    gate_observation_all_chunk   ON gate_observation_all_chunk.deployment_id =
    gate_observation_all_occurrence.deployment_id  AND gate_observation_all_chunk.chunk_id =
    gate_observation_all_occurrence.chunk_id JOIN public.document_versions gate_observation_all_version   ON
    gate_observation_all_version.deployment_id = gate_observation_all_chunk.deployment_id  AND
    gate_observation_all_version.version_id = gate_observation_all_chunk.version_id  AND
    gate_observation_all_version.current_representation_id = gate_observation_all_chunk.representation_id JOIN
    public.document_version_scope gate_observation_all_scope   ON gate_observation_all_scope.deployment_id =
    gate_observation_all_chunk.deployment_id  AND gate_observation_all_scope.version_id =
    gate_observation_all_chunk.version_id WHERE gate_observation_all_evidence.deployment_id = :'deployment_id'   AND
    gate_observation_all_evidence.observation_id = indexed.observation_id AND (NOT
    gate_observation_all_scope.periodised OR (gate_observation_all_scope.selectable AND
    gate_observation_all_scope.in_force && tstzrange(CAST(:'evaluated_at' AS timestamptz), CAST(:'evaluated_at' AS
    timestamptz), '[]'))))))),
    candidate_driven AS MATERIALIZED (
WITH
    fact_sources AS MATERIALIZED (
        SELECT source.deployment_id, source.fact_kind, source.fact_id, source.subject_entity_id,
    source.object_entity_id, source.predicate, source.valid_from, source.valid_until, source.ingested_at,
    source.invalidated_at, source.contradiction_group, source.confidence, source.valid_precision
        FROM (SELECT DISTINCT fact_kind,fact_id FROM (SELECT 'observation'::text AS fact_kind,
    unnest(CAST(:'observation_batch_ids' AS uuid[])) AS fact_id) input) requested
        CROSS JOIN LATERAL (
            SELECT fact.deployment_id, fact.fact_kind, fact.fact_id, fact.subject_entity_id, fact.object_entity_id,
    fact.predicate, fact.valid_from, fact.valid_until, fact.ingested_at, fact.invalidated_at,
    fact.contradiction_group, fact.confidence, fact.valid_precision
            FROM public.v_memory_fact_source_qualified AS fact
            WHERE fact.deployment_id = :'deployment_id'
              AND fact.fact_kind = requested.fact_kind
              AND fact.fact_id = requested.fact_id
            OFFSET 0
        ) source
    ), fact_endpoint_ids AS MATERIALIZED (
        SELECT subject_entity_id AS entity_id FROM fact_sources
        UNION SELECT object_entity_id FROM fact_sources WHERE object_entity_id IS NOT NULL
    ), fact_visible_endpoints AS MATERIALIZED (
        SELECT visible.entity_id FROM fact_endpoint_ids endpoint
        CROSS JOIN LATERAL (
            SELECT entity.entity_id FROM memory_v1.entities_current AS entity
            WHERE entity.deployment_id = :'deployment_id'
              AND entity.entity_id = endpoint.entity_id
            OFFSET 0
        ) visible
    ), fact_eligible_keys AS MATERIALIZED (
        SELECT source.fact_kind, source.fact_id FROM fact_sources source
        JOIN fact_visible_endpoints subject ON subject.entity_id = source.subject_entity_id
        WHERE source.fact_kind = 'observation' OR EXISTS (
            SELECT 1 FROM fact_visible_endpoints object
            WHERE object.entity_id = source.object_entity_id
        )
    ), eligible_facts AS NOT MATERIALIZED (SELECT source.* FROM fact_sources source JOIN fact_eligible_keys eligible
    ON eligible.fact_kind=source.fact_kind AND eligible.fact_id=source.fact_id)
     SELECT indexed.observation_id::text AS item_id
FROM eligible_facts AS fact CROSS JOIN LATERAL (
    SELECT indexed.* FROM observations indexed
    WHERE indexed.deployment_id=fact.deployment_id
    AND indexed.observation_id=fact.fact_id
    AND fact.fact_kind='observation' OFFSET 0
) indexed
WHERE indexed.deployment_id=:'deployment_id'

AND indexed.ingested_at <= :'evaluated_at' AND indexed.invalidated_at IS NULL
AND (indexed.valid_from IS NULL OR indexed.valid_from <= :'evaluated_at')
AND (indexed.valid_until IS NULL OR indexed.valid_until > :'evaluated_at')
 AND (EXISTS (
              SELECT 1 FROM LATERAL (
                SELECT e.claim_id, e.deployment_id FROM public.observation_evidence e
                WHERE e.deployment_id = :'deployment_id' AND e.observation_id = indexed.observation_id
                AND e.stance = 'supports' OFFSET 0
              ) evidence
              CROSS JOIN LATERAL (
                SELECT cc.deployment_id, cc.chunk_id FROM public.chunk_claims cc
                WHERE cc.deployment_id = evidence.deployment_id AND cc.claim_id = evidence.claim_id
                OFFSET 0
              ) occurrence
              CROSS JOIN LATERAL (
                SELECT c.deployment_id, c.version_id, c.representation_id FROM public.chunks c
                WHERE c.deployment_id = occurrence.deployment_id AND c.chunk_id = occurrence.chunk_id
                OFFSET 0
              ) chunk
              CROSS JOIN LATERAL (
                SELECT 1 FROM public.document_versions version
                JOIN public.document_version_scope s
                  ON s.deployment_id = version.deployment_id AND s.version_id = version.version_id
                WHERE version.deployment_id = chunk.deployment_id AND version.version_id = chunk.version_id
                  AND version.current_representation_id = chunk.representation_id
                  AND (NOT s.periodised OR (s.selectable AND s.in_force &&
                   tstzrange(CAST(:'evaluated_at' AS timestamptz), CAST(:'evaluated_at' AS timestamptz), '[]')))
                OFFSET 0
              ) readable
            ) OR
         (NOT EXISTS (SELECT 1 FROM public.observation_evidence e
           WHERE e.deployment_id = :'deployment_id' AND e.observation_id = indexed.observation_id
             AND e.stance='supports')
          AND EXISTS (
              SELECT 1 FROM LATERAL (
                SELECT e.claim_id, e.deployment_id FROM public.observation_evidence e
                WHERE e.deployment_id = :'deployment_id' AND e.observation_id = indexed.observation_id
                 OFFSET 0
              ) evidence
              CROSS JOIN LATERAL (
                SELECT cc.deployment_id, cc.chunk_id FROM public.chunk_claims cc
                WHERE cc.deployment_id = evidence.deployment_id AND cc.claim_id = evidence.claim_id
                OFFSET 0
              ) occurrence
              CROSS JOIN LATERAL (
                SELECT c.deployment_id, c.version_id, c.representation_id FROM public.chunks c
                WHERE c.deployment_id = occurrence.deployment_id AND c.chunk_id = occurrence.chunk_id
                OFFSET 0
              ) chunk
              CROSS JOIN LATERAL (
                SELECT 1 FROM public.document_versions version
                JOIN public.document_version_scope s
                  ON s.deployment_id = version.deployment_id AND s.version_id = version.version_id
                WHERE version.deployment_id = chunk.deployment_id AND version.version_id = chunk.version_id
                  AND version.current_representation_id = chunk.representation_id
                  AND (NOT s.periodised OR (s.selectable AND s.in_force &&
                   tstzrange(CAST(:'evaluated_at' AS timestamptz), CAST(:'evaluated_at' AS timestamptz), '[]')))
                OFFSET 0
              ) readable
            ))))
    (SELECT * FROM baseline EXCEPT SELECT * FROM candidate_driven)
    UNION ALL
    (SELECT * FROM candidate_driven EXCEPT SELECT * FROM baseline);

\echo END_PLAN_observation

SELECT count(*) AS relation_count, array_agg(relation_id) AS relation_batch_ids
FROM (
 SELECT relation_id FROM relations WHERE deployment_id=:'deployment_id' AND
 embedding IS NOT NULL AND embedding_input_policy_version='fact-label-v2:chosen-window'
 ORDER BY embedding <=> (SELECT embedding FROM observations WHERE deployment_id=:'deployment_id'
     AND embedding IS NOT NULL AND vector_norm(embedding)>0 ORDER BY observation_id LIMIT 1)
 LIMIT 804
) nominated \gset
\echo BATCH_relation :relation_count

\echo BEGIN_PLAN_relation
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, FORMAT JSON)
    WITH baseline AS MATERIALIZED (
WITH
    fact_sources AS MATERIALIZED (
        SELECT source.deployment_id, source.fact_kind, source.fact_id, source.subject_entity_id,
    source.object_entity_id, source.predicate, source.valid_from, source.valid_until, source.ingested_at,
    source.invalidated_at, source.contradiction_group, source.confidence, source.valid_precision
        FROM (SELECT DISTINCT fact_kind,fact_id FROM (SELECT 'relation'::text AS fact_kind,
    unnest(CAST(:'relation_batch_ids' AS uuid[])) AS fact_id) input) requested
        CROSS JOIN LATERAL (
            SELECT fact.deployment_id, fact.fact_kind, fact.fact_id, fact.subject_entity_id, fact.object_entity_id,
    fact.predicate, fact.valid_from, fact.valid_until, fact.ingested_at, fact.invalidated_at,
    fact.contradiction_group, fact.confidence, fact.valid_precision
            FROM public.v_memory_fact_source_qualified AS fact
            WHERE fact.deployment_id = :'deployment_id'
              AND fact.fact_kind = requested.fact_kind
              AND fact.fact_id = requested.fact_id
            OFFSET 0
        ) source
    ), fact_endpoint_ids AS MATERIALIZED (
        SELECT subject_entity_id AS entity_id FROM fact_sources
        UNION SELECT object_entity_id FROM fact_sources WHERE object_entity_id IS NOT NULL
    ), fact_visible_endpoints AS MATERIALIZED (
        SELECT visible.entity_id FROM fact_endpoint_ids endpoint
        CROSS JOIN LATERAL (
            SELECT entity.entity_id FROM memory_v1.entities_current AS entity
            WHERE entity.deployment_id = :'deployment_id'
              AND entity.entity_id = endpoint.entity_id
            OFFSET 0
        ) visible
    ), fact_eligible_keys AS MATERIALIZED (
        SELECT source.fact_kind, source.fact_id FROM fact_sources source
        JOIN fact_visible_endpoints subject ON subject.entity_id = source.subject_entity_id
        WHERE source.fact_kind = 'observation' OR EXISTS (
            SELECT 1 FROM fact_visible_endpoints object
            WHERE object.entity_id = source.object_entity_id
        )
    ), eligible_facts AS NOT MATERIALIZED (SELECT source.* FROM fact_sources source JOIN fact_eligible_keys eligible
    ON eligible.fact_kind=source.fact_kind AND eligible.fact_id=source.fact_id)
     SELECT indexed.relation_id::text AS item_id
FROM eligible_facts AS fact CROSS JOIN LATERAL (
    SELECT indexed.* FROM relations indexed
    WHERE indexed.deployment_id=fact.deployment_id
    AND indexed.relation_id=fact.fact_id
    AND fact.fact_kind='relation' OFFSET 0
) indexed
WHERE indexed.deployment_id=:'deployment_id'

AND indexed.ingested_at <= :'evaluated_at' AND indexed.invalidated_at IS NULL
AND (indexed.valid_from IS NULL OR indexed.valid_from <= :'evaluated_at')
AND (indexed.valid_until IS NULL OR indexed.valid_until > :'evaluated_at')
 AND (EXISTS (SELECT 1 FROM public.relation_evidence gate_relation_evidence JOIN public.chunk_claims
    gate_relation_occurrence   ON gate_relation_occurrence.deployment_id = gate_relation_evidence.deployment_id  AND
    gate_relation_occurrence.claim_id = gate_relation_evidence.claim_id JOIN public.chunks gate_relation_chunk   ON
    gate_relation_chunk.deployment_id = gate_relation_occurrence.deployment_id  AND gate_relation_chunk.chunk_id =
    gate_relation_occurrence.chunk_id JOIN public.document_versions gate_relation_version   ON
    gate_relation_version.deployment_id = gate_relation_chunk.deployment_id  AND gate_relation_version.version_id =
    gate_relation_chunk.version_id  AND gate_relation_version.current_representation_id =
    gate_relation_chunk.representation_id JOIN public.document_version_scope gate_relation_scope   ON
    gate_relation_scope.deployment_id = gate_relation_chunk.deployment_id  AND gate_relation_scope.version_id =
    gate_relation_chunk.version_id WHERE gate_relation_evidence.deployment_id = :'deployment_id'   AND
    gate_relation_evidence.relation_id = indexed.relation_id   AND gate_relation_evidence.stance = 'supports' AND
    (NOT gate_relation_scope.periodised OR (gate_relation_scope.selectable AND gate_relation_scope.in_force &&
    tstzrange(CAST(:'evaluated_at' AS timestamptz), CAST(:'evaluated_at' AS timestamptz), '[]')))) OR (NOT EXISTS
    (SELECT 1 FROM public.relation_evidence gate_relation_any WHERE gate_relation_any.deployment_id =
    :'deployment_id' AND gate_relation_any.relation_id = indexed.relation_id AND gate_relation_any.stance =
    'supports') AND EXISTS (SELECT 1 FROM public.relation_evidence gate_relation_all_evidence JOIN
    public.chunk_claims gate_relation_all_occurrence   ON gate_relation_all_occurrence.deployment_id =
    gate_relation_all_evidence.deployment_id  AND gate_relation_all_occurrence.claim_id =
    gate_relation_all_evidence.claim_id JOIN public.chunks gate_relation_all_chunk   ON
    gate_relation_all_chunk.deployment_id = gate_relation_all_occurrence.deployment_id  AND
    gate_relation_all_chunk.chunk_id = gate_relation_all_occurrence.chunk_id JOIN public.document_versions
    gate_relation_all_version   ON gate_relation_all_version.deployment_id = gate_relation_all_chunk.deployment_id
    AND gate_relation_all_version.version_id = gate_relation_all_chunk.version_id  AND
    gate_relation_all_version.current_representation_id = gate_relation_all_chunk.representation_id JOIN
    public.document_version_scope gate_relation_all_scope   ON gate_relation_all_scope.deployment_id =
    gate_relation_all_chunk.deployment_id  AND gate_relation_all_scope.version_id =
    gate_relation_all_chunk.version_id WHERE gate_relation_all_evidence.deployment_id = :'deployment_id'   AND
    gate_relation_all_evidence.relation_id = indexed.relation_id AND (NOT gate_relation_all_scope.periodised OR
    (gate_relation_all_scope.selectable AND gate_relation_all_scope.in_force && tstzrange(CAST(:'evaluated_at' AS
    timestamptz), CAST(:'evaluated_at' AS timestamptz), '[]'))))))),
    candidate_driven AS MATERIALIZED (
WITH
    fact_sources AS MATERIALIZED (
        SELECT source.deployment_id, source.fact_kind, source.fact_id, source.subject_entity_id,
    source.object_entity_id, source.predicate, source.valid_from, source.valid_until, source.ingested_at,
    source.invalidated_at, source.contradiction_group, source.confidence, source.valid_precision
        FROM (SELECT DISTINCT fact_kind,fact_id FROM (SELECT 'relation'::text AS fact_kind,
    unnest(CAST(:'relation_batch_ids' AS uuid[])) AS fact_id) input) requested
        CROSS JOIN LATERAL (
            SELECT fact.deployment_id, fact.fact_kind, fact.fact_id, fact.subject_entity_id, fact.object_entity_id,
    fact.predicate, fact.valid_from, fact.valid_until, fact.ingested_at, fact.invalidated_at,
    fact.contradiction_group, fact.confidence, fact.valid_precision
            FROM public.v_memory_fact_source_qualified AS fact
            WHERE fact.deployment_id = :'deployment_id'
              AND fact.fact_kind = requested.fact_kind
              AND fact.fact_id = requested.fact_id
            OFFSET 0
        ) source
    ), fact_endpoint_ids AS MATERIALIZED (
        SELECT subject_entity_id AS entity_id FROM fact_sources
        UNION SELECT object_entity_id FROM fact_sources WHERE object_entity_id IS NOT NULL
    ), fact_visible_endpoints AS MATERIALIZED (
        SELECT visible.entity_id FROM fact_endpoint_ids endpoint
        CROSS JOIN LATERAL (
            SELECT entity.entity_id FROM memory_v1.entities_current AS entity
            WHERE entity.deployment_id = :'deployment_id'
              AND entity.entity_id = endpoint.entity_id
            OFFSET 0
        ) visible
    ), fact_eligible_keys AS MATERIALIZED (
        SELECT source.fact_kind, source.fact_id FROM fact_sources source
        JOIN fact_visible_endpoints subject ON subject.entity_id = source.subject_entity_id
        WHERE source.fact_kind = 'observation' OR EXISTS (
            SELECT 1 FROM fact_visible_endpoints object
            WHERE object.entity_id = source.object_entity_id
        )
    ), eligible_facts AS NOT MATERIALIZED (SELECT source.* FROM fact_sources source JOIN fact_eligible_keys eligible
    ON eligible.fact_kind=source.fact_kind AND eligible.fact_id=source.fact_id)
     SELECT indexed.relation_id::text AS item_id
FROM eligible_facts AS fact CROSS JOIN LATERAL (
    SELECT indexed.* FROM relations indexed
    WHERE indexed.deployment_id=fact.deployment_id
    AND indexed.relation_id=fact.fact_id
    AND fact.fact_kind='relation' OFFSET 0
) indexed
WHERE indexed.deployment_id=:'deployment_id'

AND indexed.ingested_at <= :'evaluated_at' AND indexed.invalidated_at IS NULL
AND (indexed.valid_from IS NULL OR indexed.valid_from <= :'evaluated_at')
AND (indexed.valid_until IS NULL OR indexed.valid_until > :'evaluated_at')
 AND (EXISTS (
              SELECT 1 FROM LATERAL (
                SELECT e.claim_id, e.deployment_id FROM public.relation_evidence e
                WHERE e.deployment_id = :'deployment_id' AND e.relation_id = indexed.relation_id
                AND e.stance = 'supports' OFFSET 0
              ) evidence
              CROSS JOIN LATERAL (
                SELECT cc.deployment_id, cc.chunk_id FROM public.chunk_claims cc
                WHERE cc.deployment_id = evidence.deployment_id AND cc.claim_id = evidence.claim_id
                OFFSET 0
              ) occurrence
              CROSS JOIN LATERAL (
                SELECT c.deployment_id, c.version_id, c.representation_id FROM public.chunks c
                WHERE c.deployment_id = occurrence.deployment_id AND c.chunk_id = occurrence.chunk_id
                OFFSET 0
              ) chunk
              CROSS JOIN LATERAL (
                SELECT 1 FROM public.document_versions version
                JOIN public.document_version_scope s
                  ON s.deployment_id = version.deployment_id AND s.version_id = version.version_id
                WHERE version.deployment_id = chunk.deployment_id AND version.version_id = chunk.version_id
                  AND version.current_representation_id = chunk.representation_id
                  AND (NOT s.periodised OR (s.selectable AND s.in_force &&
                   tstzrange(CAST(:'evaluated_at' AS timestamptz), CAST(:'evaluated_at' AS timestamptz), '[]')))
                OFFSET 0
              ) readable
            ) OR
         (NOT EXISTS (SELECT 1 FROM public.relation_evidence e
           WHERE e.deployment_id = :'deployment_id' AND e.relation_id = indexed.relation_id
             AND e.stance='supports')
          AND EXISTS (
              SELECT 1 FROM LATERAL (
                SELECT e.claim_id, e.deployment_id FROM public.relation_evidence e
                WHERE e.deployment_id = :'deployment_id' AND e.relation_id = indexed.relation_id
                 OFFSET 0
              ) evidence
              CROSS JOIN LATERAL (
                SELECT cc.deployment_id, cc.chunk_id FROM public.chunk_claims cc
                WHERE cc.deployment_id = evidence.deployment_id AND cc.claim_id = evidence.claim_id
                OFFSET 0
              ) occurrence
              CROSS JOIN LATERAL (
                SELECT c.deployment_id, c.version_id, c.representation_id FROM public.chunks c
                WHERE c.deployment_id = occurrence.deployment_id AND c.chunk_id = occurrence.chunk_id
                OFFSET 0
              ) chunk
              CROSS JOIN LATERAL (
                SELECT 1 FROM public.document_versions version
                JOIN public.document_version_scope s
                  ON s.deployment_id = version.deployment_id AND s.version_id = version.version_id
                WHERE version.deployment_id = chunk.deployment_id AND version.version_id = chunk.version_id
                  AND version.current_representation_id = chunk.representation_id
                  AND (NOT s.periodised OR (s.selectable AND s.in_force &&
                   tstzrange(CAST(:'evaluated_at' AS timestamptz), CAST(:'evaluated_at' AS timestamptz), '[]')))
                OFFSET 0
              ) readable
            ))))
    (SELECT * FROM baseline EXCEPT SELECT * FROM candidate_driven)
    UNION ALL
    (SELECT * FROM candidate_driven EXCEPT SELECT * FROM baseline);

\echo END_PLAN_relation
ROLLBACK;
```

</details>
