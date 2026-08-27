"""Static PostgreSQL 19 SQL/PGQ statements used by the typed graph API.

PostgreSQL 19 supports fixed graph patterns but not quantified or shortest
paths, so only the one- and two-hop neighborhood shapes live here. A separate
bounded relational guard runs first in the same repeatable-read transaction.
Application code executes ``GRAPH_TABLE`` only when that guard admits the
request; refusal therefore cannot rely on planner short-circuit behavior.
"""

from typing import Final


def _replace_exact(*, statement: str, old: str, new: str, count: int = 1) -> str:
    """Replace a fixed SQL marker or fail import before semantic drift ships."""
    observed = statement.count(old)
    if observed != count:
        raise RuntimeError(
            f"SQL/PGQ template marker expected {count} occurrence(s), observed "
            f"{observed}: {old!r}"
        )
    return statement.replace(old, new)


# This guard deliberately selects only identifiers and counts. Each endpoint
# lookup starts with deployment_id and the current frontier head. LIMIT budget
# + 1 on each fixed level makes refusal bounded while still proving that the
# canonical expansion cap would be crossed. It never evaluates GRAPH_TABLE.
CURRENT_NEIGHBORHOOD_GUARD: Final = r"""
WITH
bounds AS (
  SELECT CAST(:deployment_id AS uuid) AS deployment_id,
         CAST(:anchor_id AS uuid) AS anchor_id,
         least(greatest(CAST(:max_depth AS integer), 1), 2) AS depth_cap,
         least(greatest(CAST(:expansion_budget AS integer), 1), 2000) AS budget,
         least(greatest(CAST(:frontier_budget AS integer), 1), 1000)
           AS frontier_cap
),
first_edges AS MATERIALIZED (
  SELECT edge.relation_id,
         CASE WHEN edge.subject_entity_id = b.anchor_id
              THEN edge.object_entity_id ELSE edge.subject_entity_id END
           AS neighbor_id,
         row_number() OVER (ORDER BY edge.relation_id) AS scan_ordinal
  FROM bounds AS b
  CROSS JOIN LATERAL (
    SELECT candidate.*
    FROM (
      SELECT c.relation_id, c.subject_entity_id, c.object_entity_id
      FROM rememberstack_graph_internal.relations_current AS c
      WHERE c.deployment_id = b.deployment_id
        AND c.subject_entity_id = b.anchor_id
        AND (CAST(:predicates AS text[]) IS NULL
             OR c.predicate = ANY(CAST(:predicates AS text[])))
      UNION ALL
      SELECT c.relation_id, c.subject_entity_id, c.object_entity_id
      FROM rememberstack_graph_internal.relations_current AS c
      WHERE c.deployment_id = b.deployment_id
        AND c.object_entity_id = b.anchor_id
        AND c.subject_entity_id <> b.anchor_id
        AND (CAST(:predicates AS text[]) IS NULL
             OR c.predicate = ANY(CAST(:predicates AS text[])))
    ) AS candidate
    ORDER BY candidate.relation_id
    LIMIT b.budget + 1
  ) AS edge
),
first_stats AS (
  SELECT count(e.relation_id)::bigint AS examined,
         count(e.relation_id) FILTER (
           WHERE e.scan_ordinal <= b.budget AND e.neighbor_id <> b.anchor_id
         )::bigint
           AS candidates
  FROM bounds AS b
  LEFT JOIN first_edges AS e ON true
  GROUP BY b.anchor_id, b.budget
),
first_frontier AS MATERIALIZED (
  SELECT DISTINCT ON (e.neighbor_id) e.neighbor_id, e.relation_id
  FROM bounds AS b
  JOIN first_edges AS e
    ON e.scan_ordinal <= b.budget AND e.neighbor_id <> b.anchor_id
  CROSS JOIN first_stats AS s
  WHERE s.examined <= b.budget AND s.candidates <= b.frontier_cap
  ORDER BY e.neighbor_id, e.relation_id
),
second_limit AS (
  SELECT greatest(b.budget - least(s.examined, b.budget), 0) + 1 AS scan_cap
  FROM bounds AS b
  CROSS JOIN first_stats AS s
),
seen_after_first AS (
  SELECT array_prepend(
           b.anchor_id,
           coalesce(
             array_agg(f.neighbor_id) FILTER (WHERE f.neighbor_id IS NOT NULL),
             ARRAY[]::uuid[]
           )
         ) AS entity_ids
  FROM bounds AS b
  LEFT JOIN first_frontier AS f ON true
  GROUP BY b.anchor_id
),
second_edge_candidates AS MATERIALIZED (
  SELECT edge.relation_id,
         CASE WHEN edge.subject_entity_id = f.neighbor_id
              THEN edge.object_entity_id ELSE edge.subject_entity_id END
           AS neighbor_id
  FROM bounds AS b
  JOIN first_frontier AS f ON b.depth_cap >= 2
  CROSS JOIN LATERAL (
    SELECT candidate.*
    FROM (
      SELECT c.relation_id, c.subject_entity_id, c.object_entity_id
      FROM rememberstack_graph_internal.relations_current AS c
      WHERE c.deployment_id = b.deployment_id
        AND c.subject_entity_id = f.neighbor_id
        AND (CAST(:predicates AS text[]) IS NULL
             OR c.predicate = ANY(CAST(:predicates AS text[])))
      UNION ALL
      SELECT c.relation_id, c.subject_entity_id, c.object_entity_id
      FROM rememberstack_graph_internal.relations_current AS c
      WHERE c.deployment_id = b.deployment_id
        AND c.object_entity_id = f.neighbor_id
        AND c.subject_entity_id <> f.neighbor_id
        AND (CAST(:predicates AS text[]) IS NULL
             OR c.predicate = ANY(CAST(:predicates AS text[])))
    ) AS candidate
    ORDER BY candidate.relation_id
    LIMIT (SELECT scan_cap FROM second_limit)
  ) AS edge
  LIMIT (SELECT scan_cap FROM second_limit)
),
second_edges AS MATERIALIZED (
  SELECT candidate.*,
         row_number() OVER () AS scan_ordinal
  FROM second_edge_candidates AS candidate
),
second_stats AS (
  SELECT count(e.relation_id)::bigint AS examined,
         count(e.relation_id) FILTER (
           WHERE e.scan_ordinal < cap.scan_cap
             AND NOT (e.neighbor_id = ANY(seen.entity_ids))
         )::bigint AS candidates
  FROM seen_after_first AS seen
  CROSS JOIN second_limit AS cap
  LEFT JOIN second_edges AS e ON true
  GROUP BY seen.entity_ids, cap.scan_cap
),
decision AS (
  SELECT b.*,
         first.examined AS first_examined,
         first.candidates AS first_candidates,
         second.examined AS second_examined,
         second.candidates AS second_candidates
  FROM bounds AS b
  CROSS JOIN first_stats AS first
  CROSS JOIN second_stats AS second
)
SELECT NOT (
         first_candidates > frontier_cap
         OR first_examined > budget
         OR second_candidates > frontier_cap
         OR first_examined + second_examined > budget
       ) AS admitted,
       (
         first_candidates > frontier_cap
         OR first_examined > budget
         OR second_candidates > frontier_cap
         OR first_examined + second_examined > budget
       ) AS truncated,
       CASE
         WHEN first_candidates > frontier_cap THEN 'frontier_budget'
         WHEN first_examined > budget THEN 'expansion_budget'
         WHEN second_candidates > frontier_cap THEN 'frontier_budget'
         WHEN first_examined + second_examined > budget THEN 'expansion_budget'
         ELSE NULL
       END AS truncation_reason,
       least(first_examined + second_examined, budget)::bigint AS examined_edges,
       depth_cap AS effective_depth,
       budget AS effective_expansion_budget
FROM decision
"""


def _history_statement(*, statement: str) -> str:
    """Derive an as-of statement with both half-open clocks on every edge scan."""
    history = _replace_exact(
        statement=statement,
        old="rememberstack_graph_internal.relations_current",
        new="rememberstack_graph_internal.relations_history",
        count=4,
    )
    return _replace_exact(
        statement=history,
        old=("AND (CAST(:predicates AS text[]) IS NULL\n             OR c.predicate"),
        new=(
            "AND (c.ingested_at IS NULL OR c.ingested_at <= CAST(:believed_at AS timestamptz))\n"
            "        AND (c.invalidated_at IS NULL OR c.invalidated_at > CAST(:believed_at AS timestamptz))\n"
            "        AND (c.valid_from IS NULL OR c.valid_from <= CAST(:valid_at AS timestamptz))\n"
            "        AND (c.valid_until IS NULL OR c.valid_until > CAST(:valid_at AS timestamptz))\n"
            "        AND (CAST(:predicates AS text[]) IS NULL\n"
            "             OR c.predicate"
        ),
        count=4,
    )


HISTORY_NEIGHBORHOOD_GUARD: Final = _history_statement(
    statement=CURRENT_NEIGHBORHOOD_GUARD
)


# The current statement is the fixed source template and direct PG19 proof
# surface. The caller has already admitted the request with the guard above.
CURRENT_NEIGHBORHOOD_PGQ: Final = r"""
WITH
bounds AS (
  SELECT CAST(:deployment_id AS uuid) AS deployment_id,
         CAST(:anchor_id AS uuid) AS anchor_id,
         least(greatest(CAST(:max_depth AS integer), 1), 2) AS depth_cap,
         least(greatest(CAST(:expansion_budget AS integer), 1), 2000) AS budget,
         least(greatest(CAST(:max_results AS integer), 1), 500) AS result_cap,
         greatest(CAST(:result_offset AS integer), 0) AS result_offset,
         CAST(:guard_examined_edges AS bigint) AS examined_edges
),
one_hop AS (
  SELECT 1 AS hops, ARRAY[g.relation_id] AS relation_ids,
         ARRAY[b.anchor_id, g.neighbor_id] AS node_ids
  FROM bounds AS b,
       GRAPH_TABLE (
         memory_v1.memory_current
         MATCH (x IS entity)-[r IS relates]-(y IS entity)
         WHERE x.deployment_id = b.deployment_id
           AND x.entity_id = b.anchor_id
           AND y.entity_id <> x.entity_id
           AND (CAST(:predicates AS text[]) IS NULL
                OR r.predicate = ANY(CAST(:predicates AS text[])))
         COLUMNS (r.relation_id AS relation_id, y.entity_id AS neighbor_id)
       ) AS g
  WHERE b.depth_cap >= 1
),
two_hop AS (
  SELECT 2 AS hops, ARRAY[g.relation_id_1, g.relation_id_2] AS relation_ids,
         ARRAY[b.anchor_id, g.middle_id, g.neighbor_id] AS node_ids
  FROM bounds AS b,
       GRAPH_TABLE (
         memory_v1.memory_current
         MATCH (x IS entity)-[r1 IS relates]-(m IS entity)
                           -[r2 IS relates]-(y IS entity)
         WHERE b.depth_cap >= 2
           AND x.deployment_id = b.deployment_id
           AND x.entity_id = b.anchor_id
           AND y.entity_id <> x.entity_id
           AND m.entity_id <> x.entity_id
           AND y.entity_id <> m.entity_id
           AND r1.relation_id <> r2.relation_id
           AND (CAST(:predicates AS text[]) IS NULL OR (
             r1.predicate = ANY(CAST(:predicates AS text[]))
             AND r2.predicate = ANY(CAST(:predicates AS text[]))
           ))
         COLUMNS (
           r1.relation_id AS relation_id_1,
           r2.relation_id AS relation_id_2,
           m.entity_id AS middle_id,
           y.entity_id AS neighbor_id
         )
       ) AS g
),
ranked AS (
  SELECT p.*,
         row_number() OVER (
           PARTITION BY p.node_ids[array_length(p.node_ids, 1)]
           ORDER BY p.hops, p.relation_ids
         ) AS representative
  FROM (SELECT * FROM one_hop UNION ALL SELECT * FROM two_hop) AS p
),
data_rows AS (
  SELECT r.hops, r.relation_ids, r.node_ids
  FROM ranked AS r
  WHERE r.representative = 1
  ORDER BY r.hops, r.relation_ids, r.node_ids
  LIMIT (SELECT result_cap + 1 FROM bounds)
  OFFSET (SELECT result_offset FROM bounds)
)
SELECT 'data'::text AS row_kind, d.hops, d.relation_ids, d.node_ids,
       false AS truncated, NULL::text AS truncation_reason,
       b.examined_edges, count(*) OVER ()::bigint AS returned_paths,
       b.depth_cap AS effective_depth, b.budget AS effective_expansion_budget
FROM bounds AS b
JOIN data_rows AS d ON true
UNION ALL
SELECT 'status', NULL, NULL, NULL,
       (SELECT count(*) FROM ranked WHERE representative = 1)
         > b.result_offset + b.result_cap,
       CASE WHEN (SELECT count(*) FROM ranked WHERE representative = 1)
                      > b.result_offset + b.result_cap
            THEN 'result_budget' ELSE NULL END,
       b.examined_edges,
       least((SELECT count(*) FROM data_rows), b.result_cap),
       b.depth_cap, b.budget
FROM bounds AS b
ORDER BY row_kind, hops NULLS LAST, relation_ids NULLS LAST
"""


HISTORY_NEIGHBORHOOD_PGQ: Final = _replace_exact(
    statement=_replace_exact(
        statement=_replace_exact(
            statement=CURRENT_NEIGHBORHOOD_PGQ,
            old="memory_v1.memory_current",
            new="memory_v1.memory_history",
            count=2,
        ),
        old=(
            "AND (CAST(:predicates AS text[]) IS NULL\n                OR r.predicate"
        ),
        new=(
            "AND (r.ingested_at IS NULL OR r.ingested_at <= CAST(:believed_at AS timestamptz))\n"
            "           AND (r.invalidated_at IS NULL OR r.invalidated_at > CAST(:believed_at AS timestamptz))\n"
            "           AND (r.valid_from IS NULL OR r.valid_from <= CAST(:valid_at AS timestamptz))\n"
            "           AND (r.valid_until IS NULL OR r.valid_until > CAST(:valid_at AS timestamptz))\n"
            "           AND (CAST(:predicates AS text[]) IS NULL\n"
            "                OR r.predicate"
        ),
    ),
    old=("AND (CAST(:predicates AS text[]) IS NULL OR (\n             r1.predicate"),
    new=(
        "AND (r1.ingested_at IS NULL OR r1.ingested_at <= CAST(:believed_at AS timestamptz))\n"
        "           AND (r1.invalidated_at IS NULL OR r1.invalidated_at > CAST(:believed_at AS timestamptz))\n"
        "           AND (r1.valid_from IS NULL OR r1.valid_from <= CAST(:valid_at AS timestamptz))\n"
        "           AND (r1.valid_until IS NULL OR r1.valid_until > CAST(:valid_at AS timestamptz))\n"
        "           AND (r2.ingested_at IS NULL OR r2.ingested_at <= CAST(:believed_at AS timestamptz))\n"
        "           AND (r2.invalidated_at IS NULL OR r2.invalidated_at > CAST(:believed_at AS timestamptz))\n"
        "           AND (r2.valid_from IS NULL OR r2.valid_from <= CAST(:valid_at AS timestamptz))\n"
        "           AND (r2.valid_until IS NULL OR r2.valid_until > CAST(:valid_at AS timestamptz))\n"
        "           AND (CAST(:predicates AS text[]) IS NULL OR (\n"
        "             r1.predicate"
    ),
)
