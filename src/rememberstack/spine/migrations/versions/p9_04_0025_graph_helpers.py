"""Create the bounded graph helpers ``graph_neighborhood`` and ``graph_path``.

Design §3.4 lists both as public SQL functions and §4.3 gives their bounds.
Which edges they walk depends on what was asked. With no instants supplied
they read `memory_v1.graph_edges_current`, so a traversal and a direct read of
the current graph can never disagree about what is current; a helper that
walked relations the current view adjudicates away would contradict the very
view it is a shortcut for. With `valid_at` or `believed_at` supplied they read
`memory_v1.graph_edges_visible_history` under both clocks, each applied as a
half-open interval per D41 — a relation that stopped holding exactly at the
asked instant no longer holds at it.

Every bound is clamped inside the function, so it holds however the function is
called, and each traversal is ordered deterministically before it is cut off —
an unordered LIMIT would return a different subgraph on every run and make the
bound meaningless rather than merely tight.

Traversal is undirected. A relation is an assertion about two entities, and an
agent asking "what is connected to this person" means both the relations where
they are the subject and the ones where they are the object; a direction-only
walk would silently answer half the question. The edge rows keep their real
subject and object, so a caller who cares about direction can still see it.
"""

from alembic import op

revision: str = "p9_04_0025"
down_revision: str | None = "p9_03_0024"
branch_labels = None
depends_on = None

# §4.3, analytical hard caps: the function clamps to these, and the executor
# clamps a request to the tier's own (lower) cap before it ever gets here.
_NEIGHBORHOOD_DEPTH_MAX = 4
_NEIGHBORHOOD_EDGES_MAX = 500
_PATH_DEPTH_MAX = 6
_PATH_PATHS_MAX = 10
_PATH_EDGES_MAX = 500

_EDGE_COLUMNS = """
  relation_id uuid,
  subject_entity_id uuid,
  object_entity_id uuid,
  predicate text,
  fact_label text,
  valid_from timestamptz,
  valid_until timestamptz,
  ingested_at timestamptz,
  invalidated_at timestamptz,
  contradiction_group uuid,
  confidence real,
  evidence_count bigint,
  contradict_count bigint,
  support_state text
"""

# The edge source is chosen by the question, not by convenience. With no
# instants the walk reads `graph_edges_current`, so a traversal and a direct
# read of the current graph never disagree about what is current — a helper
# that quietly walked adjudicated-away relations would contradict the view it
# is supposed to be a shortcut for. With either instant supplied the walk reads
# the history view under both D41 clocks, half-open.
_EDGE_SOURCE = """
    SELECT
      c.deployment_id, c.relation_id, c.subject_entity_id, c.object_entity_id,
      c.predicate, c.fact_label, c.valid_from, c.valid_until, c.ingested_at,
      NULL::timestamptz AS invalidated_at, c.contradiction_group, c.confidence,
      c.evidence_count, c.contradict_count, c.support_state
    FROM memory_v1.graph_edges_current AS c
    WHERE {function}.valid_at IS NULL
      AND {function}.believed_at IS NULL
      AND ({function}.predicates IS NULL
           OR c.predicate = ANY({function}.predicates))
    UNION ALL
    SELECT
      h.deployment_id, h.relation_id, h.subject_entity_id, h.object_entity_id,
      h.predicate, h.fact_label, h.valid_from, h.valid_until, h.ingested_at,
      h.invalidated_at, h.contradiction_group, h.confidence,
      h.evidence_count_current, h.contradict_count_current,
      h.support_state_current
    FROM memory_v1.graph_edges_visible_history AS h, bounds AS b
    WHERE ({function}.valid_at IS NOT NULL
           OR {function}.believed_at IS NOT NULL)
      AND h.ingested_at <= b.as_of_believed
      AND (h.invalidated_at IS NULL OR h.invalidated_at > b.as_of_believed)
      AND h.valid_from <= b.as_of_valid
      AND (h.valid_until IS NULL OR h.valid_until > b.as_of_valid)
      AND ({function}.predicates IS NULL
           OR h.predicate = ANY({function}.predicates))
"""

_NEIGHBORHOOD_DDL = f"""
CREATE FUNCTION memory_v1.graph_neighborhood(
  start_entity_id uuid,
  max_depth integer DEFAULT 2,
  predicates text[] DEFAULT NULL,
  valid_at timestamptz DEFAULT NULL,
  believed_at timestamptz DEFAULT NULL,
  max_edges integer DEFAULT 100
)
RETURNS TABLE (
  path_id bigint,
  hop integer,
  path_position integer,
  from_entity_id uuid,
  to_entity_id uuid,
{_EDGE_COLUMNS},
  applied_valid_at timestamptz,
  applied_believed_at timestamptz
)
LANGUAGE sql
STABLE
PARALLEL SAFE
SECURITY INVOKER
SET search_path = memory_v1, pg_catalog
AS $$
  WITH RECURSIVE bounds AS (
    SELECT
      least(greatest(coalesce(max_depth, 2), 1), {_NEIGHBORHOOD_DEPTH_MAX})
        AS depth_cap,
      least(greatest(coalesce(max_edges, 100), 1), {_NEIGHBORHOOD_EDGES_MAX})
        AS edge_cap,
      coalesce(valid_at, now()) AS as_of_valid,
      coalesce(believed_at, now()) AS as_of_believed
  ),
  edges AS (\n@@EDGE_SOURCE@@\n  ),
  walk AS (
    SELECT
      e.relation_id,
      1 AS hop,
      graph_neighborhood.start_entity_id AS from_entity_id,
      CASE
        WHEN e.subject_entity_id = graph_neighborhood.start_entity_id
        THEN e.object_entity_id ELSE e.subject_entity_id
      END AS to_entity_id,
      ARRAY[e.relation_id] AS seen
    FROM edges AS e
    WHERE graph_neighborhood.start_entity_id
      IN (e.subject_entity_id, e.object_entity_id)
    UNION ALL
    SELECT
      e.relation_id,
      w.hop + 1,
      w.to_entity_id,
      CASE
        WHEN e.subject_entity_id = w.to_entity_id
        THEN e.object_entity_id ELSE e.subject_entity_id
      END,
      w.seen || e.relation_id
    FROM walk AS w
    JOIN edges AS e
      ON w.to_entity_id IN (e.subject_entity_id, e.object_entity_id)
    CROSS JOIN bounds AS b
    -- A relation is walked at most once per branch: without this the walk
    -- oscillates across the same edge forever and the depth bound is the only
    -- thing that ends it, at exponential cost.
    WHERE NOT (e.relation_id = ANY(w.seen))
      AND w.hop < b.depth_cap
      -- The start is not its own neighbour. Every relation incident to it is
      -- already a hop-1 row, so walking back to it can only repeat what the
      -- caller has, one hop further away than it really is.
      AND CASE
            WHEN e.subject_entity_id = w.to_entity_id
            THEN e.object_entity_id ELSE e.subject_entity_id
          END <> graph_neighborhood.start_entity_id
  ),
  bounded AS (
    SELECT w.*, row_number() OVER (
      ORDER BY w.hop, w.relation_id, w.to_entity_id
    ) AS path_id
    FROM walk AS w
    ORDER BY w.hop, w.relation_id, w.to_entity_id
    LIMIT (SELECT edge_cap FROM bounds)
  )
  SELECT
    bounded.path_id,
    bounded.hop,
    bounded.hop AS path_position,
    bounded.from_entity_id,
    bounded.to_entity_id,
    e.relation_id,
    e.subject_entity_id,
    e.object_entity_id,
    e.predicate,
    e.fact_label,
    e.valid_from,
    e.valid_until,
    e.ingested_at,
    e.invalidated_at,
    e.contradiction_group,
    e.confidence,
    e.evidence_count,
    e.contradict_count,
    e.support_state,
    b.as_of_valid,
    b.as_of_believed
  FROM bounded
  JOIN edges AS e ON e.relation_id = bounded.relation_id
  CROSS JOIN bounds AS b
  ORDER BY bounded.path_id
$$;
"""

_PATH_DDL = f"""
CREATE FUNCTION memory_v1.graph_path(
  from_entity_id uuid,
  to_entity_id uuid,
  max_depth integer DEFAULT 4,
  predicates text[] DEFAULT NULL,
  valid_at timestamptz DEFAULT NULL,
  believed_at timestamptz DEFAULT NULL,
  max_paths integer DEFAULT 3,
  max_edges integer DEFAULT 100
)
RETURNS TABLE (
  path_id bigint,
  path_length integer,
  path_position integer,
  step_from_entity_id uuid,
  step_to_entity_id uuid,
{_EDGE_COLUMNS},
  applied_valid_at timestamptz,
  applied_believed_at timestamptz
)
LANGUAGE sql
STABLE
PARALLEL SAFE
SECURITY INVOKER
SET search_path = memory_v1, pg_catalog
AS $$
  WITH RECURSIVE bounds AS (
    SELECT
      least(greatest(coalesce(max_depth, 4), 1), {_PATH_DEPTH_MAX}) AS depth_cap,
      least(greatest(coalesce(max_paths, 3), 1), {_PATH_PATHS_MAX}) AS path_cap,
      least(greatest(coalesce(max_edges, 100), 1), {_PATH_EDGES_MAX}) AS edge_cap,
      coalesce(valid_at, now()) AS as_of_valid,
      coalesce(believed_at, now()) AS as_of_believed
  ),
  edges AS (\n@@EDGE_SOURCE@@\n  ),
  walk AS (
    SELECT
      CASE
        WHEN e.subject_entity_id = graph_path.from_entity_id
        THEN e.object_entity_id ELSE e.subject_entity_id
      END AS head,
      1 AS length,
      ARRAY[e.relation_id] AS relations,
      ARRAY[graph_path.from_entity_id] || ARRAY[
        CASE
          WHEN e.subject_entity_id = graph_path.from_entity_id
          THEN e.object_entity_id ELSE e.subject_entity_id
        END
      ] AS nodes
    FROM edges AS e
    WHERE graph_path.from_entity_id IN (e.subject_entity_id, e.object_entity_id)
    UNION ALL
    SELECT
      CASE
        WHEN e.subject_entity_id = w.head
        THEN e.object_entity_id ELSE e.subject_entity_id
      END,
      w.length + 1,
      w.relations || e.relation_id,
      w.nodes || CASE
        WHEN e.subject_entity_id = w.head
        THEN e.object_entity_id ELSE e.subject_entity_id
      END
    FROM walk AS w
    JOIN edges AS e ON w.head IN (e.subject_entity_id, e.object_entity_id)
    CROSS JOIN bounds AS b
    -- Simple paths only: an entity appears once, so the walk cannot loop and
    -- a returned path is a real route rather than a tour.
    WHERE w.length < b.depth_cap
      AND w.head <> graph_path.to_entity_id
      AND NOT (
        CASE
          WHEN e.subject_entity_id = w.head
          THEN e.object_entity_id ELSE e.subject_entity_id
        END = ANY(w.nodes)
      )
  ),
  reached AS (
    SELECT
      w.length,
      w.relations,
      w.nodes,
      row_number() OVER (ORDER BY w.length, w.relations) AS path_id,
      -- How many edges have been spent by the end of this path, in the same
      -- deterministic order the paths are returned in.
      sum(w.length) OVER (
        ORDER BY w.length, w.relations
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
      ) AS edges_through
    FROM walk AS w
    WHERE w.head = graph_path.to_entity_id
  ),
  arrived AS (
    -- The edge bound is spent on WHOLE paths. Cutting rows at the end would
    -- return a path whose reported length does not match the steps it came
    -- with — a route that does not connect, presented as one that does. A
    -- path that cannot fit within the bound is omitted entirely, which is a
    -- shorter answer rather than a wrong one.
    SELECT r.length, r.relations, r.nodes, r.path_id
    FROM reached AS r
    CROSS JOIN bounds AS b
    WHERE r.path_id <= b.path_cap
      AND r.edges_through <= b.edge_cap
  ),
  steps AS (
    SELECT
      a.path_id,
      a.length,
      step.position::integer AS path_position,
      step.relation_id,
      a.nodes[step.position] AS step_from_entity_id,
      a.nodes[step.position + 1] AS step_to_entity_id
    FROM arrived AS a
    CROSS JOIN LATERAL unnest(a.relations)
      WITH ORDINALITY AS step(relation_id, position)
  )
  SELECT
    s.path_id,
    s.length,
    s.path_position,
    s.step_from_entity_id,
    s.step_to_entity_id,
    e.relation_id,
    e.subject_entity_id,
    e.object_entity_id,
    e.predicate,
    e.fact_label,
    e.valid_from,
    e.valid_until,
    e.ingested_at,
    e.invalidated_at,
    e.contradiction_group,
    e.confidence,
    e.evidence_count,
    e.contradict_count,
    e.support_state,
    b.as_of_valid,
    b.as_of_believed
  FROM steps AS s
  JOIN edges AS e ON e.relation_id = s.relation_id
  CROSS JOIN bounds AS b
  ORDER BY s.path_id, s.path_position
$$;
"""

_NEIGHBORHOOD_DDL = _NEIGHBORHOOD_DDL.replace(
    "@@EDGE_SOURCE@@", _EDGE_SOURCE.replace("{function}", "graph_neighborhood")
)
_PATH_DDL = _PATH_DDL.replace(
    "@@EDGE_SOURCE@@", _EDGE_SOURCE.replace("{function}", "graph_path")
)

_NEIGHBORHOOD_SIGNATURE = (
    "memory_v1.graph_neighborhood(uuid, integer, text[], timestamptz,"
    " timestamptz, integer)"
)
_PATH_SIGNATURE = (
    "memory_v1.graph_path(uuid, uuid, integer, text[], timestamptz,"
    " timestamptz, integer, integer)"
)

_GRANT = """
DO $do$
DECLARE
  query_role text := 'rememberstack_query_' || current_database();
BEGIN
  EXECUTE format('GRANT EXECUTE ON FUNCTION {signature} TO %I', query_role);
END
$do$;
"""


def upgrade() -> None:
    """Create both helpers, hand them to the view owner, and grant them."""
    for ddl, signature, comment in (
        (
            _NEIGHBORHOOD_DDL,
            _NEIGHBORHOOD_SIGNATURE,
            "Relations within max_depth hops of an entity, traversed"
            " undirected over both D41 clocks (each half-open, each defaulting"
            f" to now()). Depth is clamped to {_NEIGHBORHOOD_DEPTH_MAX} and"
            f" edges to {_NEIGHBORHOOD_EDGES_MAX}; a relation is walked at most"
            " once per branch.",
        ),
        (
            _PATH_DDL,
            _PATH_SIGNATURE,
            "Simple paths between two entities, traversed undirected over both"
            " D41 clocks (each half-open, each defaulting to now()). Depth is"
            f" clamped to {_PATH_DEPTH_MAX}, paths to {_PATH_PATHS_MAX}, and"
            f" edges to {_PATH_EDGES_MAX}; an entity appears at most once in a"
            " path.",
        ),
    ):
        op.execute(ddl)
        op.execute(f"COMMENT ON FUNCTION {signature} IS {_quote(comment)}")
        op.execute(f"ALTER FUNCTION {signature} OWNER TO rememberstack_view_owner")
        op.execute(_GRANT.replace("{signature}", signature))


def downgrade() -> None:
    """Drop both helpers; their grants go with them."""
    op.execute(f"DROP FUNCTION IF EXISTS {_PATH_SIGNATURE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_NEIGHBORHOOD_SIGNATURE}")


def _quote(value: str) -> str:
    """One SQL string literal."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"
