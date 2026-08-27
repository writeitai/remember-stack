"""Static PostgreSQL 19 SQL/PGQ statements used by the typed graph API.

PostgreSQL 19 supports fixed graph patterns but not quantified or shortest
paths, so the one-hop neighborhood shape lives here. A separate
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


# This guard selects only identifiers and counts. Both endpoint lookups start
# with deployment_id and the anchor; budget + 1 proves refusal without PGQ.
CURRENT_NEIGHBORHOOD_GUARD: Final = r"""
WITH
bounds AS (
  SELECT CAST(:deployment_id AS uuid) AS deployment_id,
         CAST(:anchor_id AS uuid) AS anchor_id,
         least(greatest(CAST(:expansion_budget AS integer), 1), 2000) AS budget,
         least(greatest(CAST(:frontier_budget AS integer), 1), 1000)
           AS frontier_cap
),
edges AS MATERIALIZED (
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
      FROM public.v_memory_entity_survivor AS anchor
      JOIN public.relations AS c
        ON c.deployment_id = anchor.deployment_id
       AND c.subject_entity_id = anchor.entity_id
      WHERE anchor.deployment_id = b.deployment_id
        AND anchor.survivor_entity_id = b.anchor_id
        AND c.ingested_at <= statement_timestamp()
        AND c.invalidated_at IS NULL
        AND (c.valid_from IS NULL OR c.valid_from <= statement_timestamp())
        AND (c.valid_until IS NULL OR c.valid_until > statement_timestamp())
        AND (CAST(:predicates AS text[]) IS NULL
             OR c.predicate = ANY(CAST(:predicates AS text[])))
      UNION ALL
      SELECT c.relation_id, c.subject_entity_id, c.object_entity_id
      FROM public.v_memory_entity_survivor AS anchor
      JOIN public.relations AS c
        ON c.deployment_id = anchor.deployment_id
       AND c.object_entity_id = anchor.entity_id
      WHERE anchor.deployment_id = b.deployment_id
        AND anchor.survivor_entity_id = b.anchor_id
        AND c.subject_entity_id <> anchor.entity_id
        AND c.ingested_at <= statement_timestamp()
        AND c.invalidated_at IS NULL
        AND (c.valid_from IS NULL OR c.valid_from <= statement_timestamp())
        AND (c.valid_until IS NULL OR c.valid_until > statement_timestamp())
        AND (CAST(:predicates AS text[]) IS NULL
             OR c.predicate = ANY(CAST(:predicates AS text[])))
    ) AS candidate
    ORDER BY candidate.relation_id
    LIMIT b.budget + 1
  ) AS edge
),
stats AS (
  SELECT count(e.relation_id)::bigint AS examined,
         count(e.relation_id) FILTER (
           WHERE e.scan_ordinal <= b.budget AND e.neighbor_id <> b.anchor_id
         )::bigint
           AS candidates
  FROM bounds AS b
  LEFT JOIN edges AS e ON true
  GROUP BY b.anchor_id, b.budget
)
SELECT NOT (s.candidates > b.frontier_cap OR s.examined > b.budget) AS admitted,
       (s.candidates > b.frontier_cap OR s.examined > b.budget) AS truncated,
       CASE
         WHEN s.candidates > b.frontier_cap THEN 'frontier_budget'
         WHEN s.examined > b.budget THEN 'expansion_budget'
         ELSE NULL
       END AS truncation_reason,
       least(s.examined, b.budget)::bigint AS examined_edges,
       1 AS effective_depth,
       b.budget AS effective_expansion_budget
FROM bounds AS b
CROSS JOIN stats AS s
"""


def _history_statement(*, statement: str) -> str:
    """Derive an as-of statement with both half-open clocks on every edge scan."""
    return _replace_exact(
        statement=statement,
        old=(
            "AND c.ingested_at <= statement_timestamp()\n"
            "        AND c.invalidated_at IS NULL\n"
            "        AND (c.valid_from IS NULL OR c.valid_from <= statement_timestamp())\n"
            "        AND (c.valid_until IS NULL OR c.valid_until > statement_timestamp())"
        ),
        new=(
            "AND (c.ingested_at IS NULL OR c.ingested_at <= CAST(:believed_at AS timestamptz))\n"
            "        AND (c.invalidated_at IS NULL OR c.invalidated_at > CAST(:believed_at AS timestamptz))\n"
            "        AND (c.valid_from IS NULL OR c.valid_from <= CAST(:valid_at AS timestamptz))\n"
            "        AND (c.valid_until IS NULL OR c.valid_until > CAST(:valid_at AS timestamptz))"
        ),
        count=2,
    )


HISTORY_NEIGHBORHOOD_GUARD: Final = _history_statement(
    statement=CURRENT_NEIGHBORHOOD_GUARD
)


# PostgreSQL 19 Beta 3 produces an unbounded two-hop view-backed plan, so the
# request path uses PGQ here and the bounded frontier helper for depth two up.
CURRENT_NEIGHBORHOOD_PGQ: Final = r"""
WITH bounds AS (
  SELECT CAST(:deployment_id AS uuid) AS deployment_id,
         CAST(:anchor_id AS uuid) AS anchor_id
)
SELECT 1 AS hops, ARRAY[g.relation_id] AS relation_ids,
       ARRAY[b.anchor_id, g.neighbor_id] AS node_ids
FROM bounds AS b,
     GRAPH_TABLE (
       memory_v1.memory_current
       MATCH (x IS entity)-[r IS relates]-(y IS entity)
       WHERE x.deployment_id = b.deployment_id
         AND r.deployment_id = b.deployment_id
         AND y.deployment_id = b.deployment_id
         AND x.entity_id = b.anchor_id
         AND y.entity_id <> x.entity_id
         AND (CAST(:predicates AS text[]) IS NULL
              OR r.predicate = ANY(CAST(:predicates AS text[])))
       COLUMNS (r.relation_id AS relation_id, y.entity_id AS neighbor_id)
     ) AS g
"""


HISTORY_NEIGHBORHOOD_PGQ: Final = _replace_exact(
    statement=_replace_exact(
        statement=CURRENT_NEIGHBORHOOD_PGQ,
        old="memory_v1.memory_current",
        new="memory_v1.memory_history",
    ),
    old="AND (CAST(:predicates AS text[]) IS NULL\n              OR r.predicate",
    new=(
        "AND (r.ingested_at IS NULL OR r.ingested_at <= CAST(:believed_at AS timestamptz))\n"
        "         AND (r.invalidated_at IS NULL OR r.invalidated_at > CAST(:believed_at AS timestamptz))\n"
        "         AND (r.valid_from IS NULL OR r.valid_from <= CAST(:valid_at AS timestamptz))\n"
        "         AND (r.valid_until IS NULL OR r.valid_until > CAST(:valid_at AS timestamptz))\n"
        "         AND (CAST(:predicates AS text[]) IS NULL\n"
        "              OR r.predicate"
    ),
)
