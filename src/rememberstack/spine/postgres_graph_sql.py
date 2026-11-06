"""Static one-hop graph statements used by the typed graph API (D98).

The one-hop neighborhood is plain relational SQL over the live graph's edge
views. A separate bounded relational guard runs first in the same
repeatable-read transaction. Application code executes the one-hop read only
when that guard admits the request; refusal therefore cannot rely on planner
short-circuit behavior.

Every edge scan also applies the D140 §8.1 evidence gate
(``rememberstack_graph_internal.relation_evidence_in_scope``) in the same
statement, so an edge without in-scope supporting text is never a candidate:
it cannot consume the expansion budget or a result slot.
"""

from typing import Final


def _replace_exact(*, statement: str, old: str, new: str, count: int = 1) -> str:
    """Replace a fixed SQL marker or fail import before semantic drift ships."""
    observed = statement.count(old)
    if observed != count:
        raise RuntimeError(
            f"graph SQL template marker expected {count} occurrence(s), observed "
            f"{observed}: {old!r}"
        )
    return statement.replace(old, new)


# This guard selects only identifiers and counts. Both endpoint lookups start
# with deployment_id and the anchor; budget + 1 proves refusal before the read.
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
    SELECT gated.* FROM (
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
        AND c.valid_from <= statement_timestamp()
        AND (c.valid_precision='open' OR c.valid_until > statement_timestamp())
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
        AND c.valid_from <= statement_timestamp()
        AND (c.valid_precision='open' OR c.valid_until > statement_timestamp())
        AND (CAST(:predicates AS text[]) IS NULL
             OR c.predicate = ANY(CAST(:predicates AS text[])))
    ) AS candidate
    ORDER BY candidate.relation_id
    OFFSET 0
    ) AS gated
    -- D140 §8.1, lazily over the ordered candidates: an edge without
    -- in-scope supporting text never counts toward the budget
    WHERE rememberstack_graph_internal.relation_evidence_in_scope(
          b.deployment_id, gated.relation_id,
          statement_timestamp(), statement_timestamp(), statement_timestamp())
    ORDER BY gated.relation_id
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


_GATE_NOW = "statement_timestamp(), statement_timestamp(), statement_timestamp())"
_GATE_HISTORY = (
    "CAST(:valid_at AS timestamptz), CAST(:believed_at AS timestamptz),"
    " statement_timestamp())"
)


def _history_statement(*, statement: str) -> str:
    """Derive an as-of statement with both half-open clocks on every edge scan."""
    statement = _replace_exact(statement=statement, old=_GATE_NOW, new=_GATE_HISTORY)
    return _replace_exact(
        statement=statement,
        old=(
            "AND c.ingested_at <= statement_timestamp()\n"
            "        AND c.invalidated_at IS NULL\n"
            "        AND c.valid_from <= statement_timestamp()\n"
            "        AND (c.valid_precision='open' OR c.valid_until > statement_timestamp())"
        ),
        new=(
            "AND (c.ingested_at IS NULL OR c.ingested_at <= CAST(:believed_at AS timestamptz))\n"
            "        AND (c.invalidated_at IS NULL OR c.invalidated_at > CAST(:believed_at AS timestamptz))\n"
            "        AND c.valid_from <= CAST(:valid_at AS timestamptz)\n"
            "        AND (c.valid_precision='open' OR c.valid_until > CAST(:valid_at AS timestamptz))"
        ),
        count=2,
    )


HISTORY_NEIGHBORHOOD_GUARD: Final = _history_statement(
    statement=CURRENT_NEIGHBORHOOD_GUARD
)


# The one-hop read joins the live edge views directly. Both endpoints of every
# edge row are already live entity vertices (the views join entities_live), so
# no vertex join is needed. One branch per edge direction keeps each scan
# anchored on an indexed endpoint; a self-loop is never a neighbor. The D140
# evidence gate runs above an OFFSET 0 fence, as in the guard. Depth two and
# above use the bounded frontier helpers.
_ONE_HOP = r"""
WITH bounds AS (
  SELECT CAST(:deployment_id AS uuid) AS deployment_id,
         CAST(:anchor_id AS uuid) AS anchor_id
)
SELECT 1 AS hops, ARRAY[edge.relation_id] AS relation_ids,
       ARRAY[b.anchor_id, edge.neighbor_id] AS node_ids
FROM bounds AS b
CROSS JOIN LATERAL (
  SELECT r.relation_id, r.object_entity_id AS neighbor_id
  FROM {edges} AS r
  WHERE r.deployment_id = b.deployment_id
    AND r.subject_entity_id = b.anchor_id
    AND r.object_entity_id <> b.anchor_id{clock}
    AND (CAST(:predicates AS text[]) IS NULL
         OR r.predicate = ANY(CAST(:predicates AS text[])))
  UNION ALL
  SELECT r.relation_id, r.subject_entity_id AS neighbor_id
  FROM {edges} AS r
  WHERE r.deployment_id = b.deployment_id
    AND r.object_entity_id = b.anchor_id
    AND r.subject_entity_id <> b.anchor_id{clock}
    AND (CAST(:predicates AS text[]) IS NULL
         OR r.predicate = ANY(CAST(:predicates AS text[])))
  -- fence: the gate must run on the anchored edges only, never be pushed
  -- down to every relation row of the deployment inside the edge view
  OFFSET 0
) AS edge
WHERE rememberstack_graph_internal.relation_evidence_in_scope(
      b.deployment_id, edge.relation_id,
      {gate}
"""

_HISTORY_CLOCK = """
    AND (r.ingested_at IS NULL OR r.ingested_at <= CAST(:believed_at AS timestamptz))
    AND (r.invalidated_at IS NULL OR r.invalidated_at > CAST(:believed_at AS timestamptz))
    AND r.valid_from <= CAST(:valid_at AS timestamptz)
    AND (r.valid_precision='open' OR r.valid_until > CAST(:valid_at AS timestamptz))"""


CURRENT_NEIGHBORHOOD_ONE_HOP: Final = _ONE_HOP.format(
    edges="rememberstack_graph_internal.relations_current", clock="", gate=_GATE_NOW
)


HISTORY_NEIGHBORHOOD_ONE_HOP: Final = _ONE_HOP.format(
    edges="rememberstack_graph_internal.relations_history",
    clock=_HISTORY_CLOCK,
    gate=_GATE_HISTORY,
)
