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

from rememberstack.spine.migrations._helpers import _split_sql
from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations.versions.p9_01_0022_memory_v1_query_space import (
    MEMORY_V1_AUTHORED_DDL,
)

revision: str = "p9_05_0026"
down_revision: str | None = "p9_04_0025"
branch_labels = None
depends_on = None

# §4.3, analytical hard caps: the function clamps to these, and the executor
# clamps a request to the tier's own (lower) cap before it ever gets here.
_NEIGHBORHOOD_DEPTH_MAX = 4
_NEIGHBORHOOD_EDGES_MAX = 500
_PATH_DEPTH_MAX = 6
_PATH_PATHS_MAX = 10
_PATH_EDGES_MAX = 500

# Endpoint membership is a semijoin: the graph edge publishes no entity
# columns. Spelling it as two ordinary joins makes PostgreSQL expand the full
# entities_current definition twice inside the already-expanded fact view,
# producing a multi-thousand-node plan after Batch A's coordinate correction.
# EXISTS preserves exactly the same membership rule without that plan blow-up.
GRAPH_EDGE_VIEW_DDL = r"""
CREATE OR REPLACE VIEW memory_v1.graph_edges_current AS
SELECT
  f.deployment_id,
  f.fact_id AS relation_id,
  f.subject_entity_id,
  f.object_entity_id,
  f.predicate,
  f.fact_label,
  f.valid_from,
  f.valid_until,
  f.ingested_at,
  f.contradiction_group,
  f.confidence,
  f.evidence_count,
  f.contradict_count,
  f.support_state,
  f.evaluated_at
FROM memory_v1.facts_current AS f
WHERE f.fact_kind = 'relation'
  AND EXISTS (
    SELECT 1
    FROM memory_v1.entities_current AS subject
    WHERE subject.deployment_id = f.deployment_id
      AND subject.entity_id = f.subject_entity_id
  )
  AND EXISTS (
    SELECT 1
    FROM memory_v1.entities_current AS object
    WHERE object.deployment_id = f.deployment_id
      AND object.entity_id = f.object_entity_id
  );

CREATE OR REPLACE VIEW memory_v1.graph_edges_visible_history AS
SELECT
  h.deployment_id,
  h.fact_id AS relation_id,
  h.subject_entity_id,
  h.object_entity_id,
  h.predicate,
  h.fact_label,
  h.valid_from,
  h.valid_until,
  h.ingested_at,
  h.invalidated_at,
  h.contradiction_group,
  h.confidence,
  h.evidence_count_current,
  h.contradict_count_current,
  h.support_state_current
FROM memory_v1.facts_visible_history AS h
WHERE h.fact_kind = 'relation'
  AND EXISTS (
    SELECT 1
    FROM memory_v1.entities_current AS subject
    WHERE subject.deployment_id = h.deployment_id
      AND subject.entity_id = h.subject_entity_id
  )
  AND EXISTS (
    SELECT 1
    FROM memory_v1.entities_current AS object
    WHERE object.deployment_id = h.deployment_id
      AND object.entity_id = h.object_entity_id
  );
"""

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
  evidence_count_current bigint,
  contradict_count_current bigint,
  support_state_current text
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
      c.evidence_count AS evidence_count_current,
      c.contradict_count AS contradict_count_current,
      c.support_state AS support_state_current
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
      -- A null endpoint is an OPEN interval on both clocks. Treating a null
      -- start as "began after every instant" hid relations from every as-of
      -- question, which is the opposite of what an open interval means.
      AND (h.ingested_at IS NULL OR h.ingested_at <= b.as_of_believed)
      AND (h.invalidated_at IS NULL OR h.invalidated_at > b.as_of_believed)
      AND (h.valid_from IS NULL OR h.valid_from <= b.as_of_valid)
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
LANGUAGE plpgsql
STABLE
PARALLEL UNSAFE
SECURITY INVOKER
SET search_path = memory_v1, pg_catalog
SET join_collapse_limit = 1
SET from_collapse_limit = 1
AS $$
#variable_conflict use_column
BEGIN
  IF (graph_neighborhood.valid_at IS NULL)
     <> (graph_neighborhood.believed_at IS NULL) THEN
    RAISE EXCEPTION
      'a bitemporal traversal takes both clocks or neither'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  RETURN QUERY
  WITH RECURSIVE bounds AS (
    SELECT
      least(greatest(coalesce(max_depth, 2), 1), {_NEIGHBORHOOD_DEPTH_MAX})
        AS depth_cap,
      least(greatest(coalesce(max_edges, 100), 1), {_NEIGHBORHOOD_EDGES_MAX})
        AS edge_cap,
      -- Both instants or neither. Supplying one and letting the other default
      -- to now() answers a bitemporal question nobody asked: "as the world was
      -- then, as we believe it now" is a third thing, and returning it under
      -- the caller's one-clock request would misreport what it means.
      -- `statement_timestamp()`, not `now()`. `now()` is transaction start,
      -- while `graph_edges_current` evaluates at statement time, so a row
      -- selected by one instant was being labelled with an earlier one.
      coalesce(valid_at, statement_timestamp()) AS as_of_valid,
      coalesce(believed_at, statement_timestamp()) AS as_of_believed
  ),
  edges AS MATERIALIZED (\n@@EDGE_SOURCE@@\n  ),
  walk AS (
    SELECT
      e.relation_id,
      1 AS hop,
      graph_neighborhood.start_entity_id AS from_entity_id,
      CASE
        WHEN e.subject_entity_id = graph_neighborhood.start_entity_id
        THEN e.object_entity_id ELSE e.subject_entity_id
      END AS to_entity_id,
      ARRAY[e.relation_id] AS seen_relations,
      ARRAY[
        graph_neighborhood.start_entity_id,
        CASE
          WHEN e.subject_entity_id = graph_neighborhood.start_entity_id
          THEN e.object_entity_id ELSE e.subject_entity_id
        END
      ] AS seen_nodes
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
      w.seen_relations || e.relation_id,
      w.seen_nodes || CASE
        WHEN e.subject_entity_id = w.to_entity_id
        THEN e.object_entity_id ELSE e.subject_entity_id
      END
    FROM walk AS w
    JOIN edges AS e
      ON w.to_entity_id IN (e.subject_entity_id, e.object_entity_id)
    CROSS JOIN bounds AS b
    -- A relation is walked at most once per branch: without this the walk
    -- oscillates across the same edge forever and the depth bound is the only
    -- thing that ends it, at exponential cost.
    WHERE NOT (e.relation_id = ANY(w.seen_relations))
      AND w.hop < b.depth_cap
      -- Simple branches only: distinct parallel edges do not make revisiting
      -- an entity a new route, and cycles must not multiply the neighborhood.
      AND NOT (
        CASE
          WHEN e.subject_entity_id = w.to_entity_id
          THEN e.object_entity_id ELSE e.subject_entity_id
        END = ANY(w.seen_nodes)
      )
  ),
  cap_status AS MATERIALIZED (
    SELECT
      (SELECT count(*) FROM walk) > b.edge_cap
      OR EXISTS (
        SELECT 1
        FROM walk AS w
        JOIN edges AS e
          ON w.to_entity_id IN (e.subject_entity_id, e.object_entity_id)
        WHERE w.hop = b.depth_cap
          AND NOT (e.relation_id = ANY(w.seen_relations))
          AND NOT (
            CASE
              WHEN e.subject_entity_id = w.to_entity_id
              THEN e.object_entity_id ELSE e.subject_entity_id
            END = ANY(w.seen_nodes)
          )
      ) AS reached
    FROM bounds AS b
  ),
  bounded AS (
    SELECT w.*, row_number() OVER (
      ORDER BY w.hop, w.relation_id, w.to_entity_id
    ) AS path_id
    FROM walk AS w
    ORDER BY w.hop, w.relation_id, w.to_entity_id
    LIMIT (SELECT edge_cap FROM bounds)
  ),
  result_rows AS MATERIALIZED (
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
      e.evidence_count_current,
      e.contradict_count_current,
      e.support_state_current,
      b.as_of_valid AS applied_valid_at,
      b.as_of_believed AS applied_believed_at
    FROM bounded
    JOIN edges AS e ON e.relation_id = bounded.relation_id
    CROSS JOIN bounds AS b
  ),
  cap_state AS MATERIALIZED (
    SELECT set_config(
      'rememberstack.graph_cap_reached',
      (
        coalesce(
          current_setting('rememberstack.graph_cap_reached', true), 'false'
        )::boolean
        OR cap_status.reached
      )::text,
      true
    ) AS marker
    FROM cap_status
  ),
  marked_rows AS MATERIALIZED (
    -- The left join deliberately produces one private marker row even when a
    -- tight budget leaves no public row. The outer filter removes it, while
    -- the executor can still read the transaction-local cap flag.
    SELECT result_rows AS row_value, cap_state.marker
    FROM cap_state
    LEFT JOIN result_rows ON true
  )
  SELECT (mr.row_value).*
  FROM marked_rows AS mr
  WHERE (mr.row_value).path_id IS NOT NULL
  ORDER BY (mr.row_value).path_id;
END
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
LANGUAGE plpgsql
STABLE
PARALLEL UNSAFE
SECURITY INVOKER
SET search_path = memory_v1, pg_catalog
SET join_collapse_limit = 1
SET from_collapse_limit = 1
AS $$
#variable_conflict use_column
BEGIN
  IF (graph_path.valid_at IS NULL) <> (graph_path.believed_at IS NULL) THEN
    RAISE EXCEPTION
      'a bitemporal traversal takes both clocks or neither'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  RETURN QUERY
  WITH RECURSIVE bounds AS (
    SELECT
      least(greatest(coalesce(max_depth, 4), 1), {_PATH_DEPTH_MAX}) AS depth_cap,
      least(greatest(coalesce(max_paths, 3), 1), {_PATH_PATHS_MAX}) AS path_cap,
      least(greatest(coalesce(max_edges, 100), 1), {_PATH_EDGES_MAX}) AS edge_cap,
      -- Both instants or neither. Supplying one and letting the other default
      -- to now() answers a bitemporal question nobody asked: "as the world was
      -- then, as we believe it now" is a third thing, and returning it under
      -- the caller's one-clock request would misreport what it means.
      -- `statement_timestamp()`, not `now()`. `now()` is transaction start,
      -- while `graph_edges_current` evaluates at statement time, so a row
      -- selected by one instant was being labelled with an earlier one.
      coalesce(valid_at, statement_timestamp()) AS as_of_valid,
      coalesce(believed_at, statement_timestamp()) AS as_of_believed
  ),
  edges AS MATERIALIZED (\n@@EDGE_SOURCE@@\n  ),
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
  cap_status AS MATERIALIZED (
    SELECT
      EXISTS (
        SELECT 1
        FROM walk AS w
        JOIN edges AS e ON w.head IN (e.subject_entity_id, e.object_entity_id)
        WHERE w.length = b.depth_cap
          AND w.head <> graph_path.to_entity_id
          AND NOT (
            CASE
              WHEN e.subject_entity_id = w.head
              THEN e.object_entity_id ELSE e.subject_entity_id
            END = ANY(w.nodes)
          )
      )
      OR (SELECT count(*) FROM reached) > b.path_cap
      OR EXISTS (
        SELECT 1
        FROM reached AS r
        WHERE r.path_id <= b.path_cap
          AND r.edges_through > b.edge_cap
      ) AS reached
    FROM bounds AS b
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
  ),
  result_rows AS MATERIALIZED (
    SELECT
      s.path_id,
      s.length AS path_length,
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
      e.evidence_count_current,
      e.contradict_count_current,
      e.support_state_current,
      b.as_of_valid AS applied_valid_at,
      b.as_of_believed AS applied_believed_at
    FROM steps AS s
    JOIN edges AS e ON e.relation_id = s.relation_id
    CROSS JOIN bounds AS b
  ),
  cap_state AS MATERIALIZED (
    SELECT set_config(
      'rememberstack.graph_cap_reached',
      (
        coalesce(
          current_setting('rememberstack.graph_cap_reached', true), 'false'
        )::boolean
        OR cap_status.reached
      )::text,
      true
    ) AS marker
    FROM cap_status
  ),
  marked_rows AS MATERIALIZED (
    SELECT result_rows AS row_value, cap_state.marker
    FROM cap_state
    LEFT JOIN result_rows ON true
  )
  SELECT (mr.row_value).*
  FROM marked_rows AS mr
  WHERE (mr.row_value).path_id IS NOT NULL
  ORDER BY (mr.row_value).path_id, (mr.row_value).path_position;
END
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

_GRAPH_EDGE_VIEWS = (
    "memory_v1.graph_edges_current",
    "memory_v1.graph_edges_visible_history",
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
    """Bound graph-edge plans, then create and grant the two public helpers."""
    apply_ddl(sql=GRAPH_EDGE_VIEW_DDL)
    # PostgreSQL grants EXECUTE on new functions to PUBLIC by default. Close
    # both the inherited ACL and this migration role's future default before
    # creating the public API functions, then grant only the routed query role.
    op.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA memory_v1 FROM PUBLIC")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA memory_v1"
        " REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )
    for ddl, signature, comment in (
        (
            _NEIGHBORHOOD_DDL,
            _NEIGHBORHOOD_SIGNATURE,
            "Relations within max_depth hops of an entity, traversed"
            " undirected over both half-open D41 clocks. Omit both clocks for"
            " statement_timestamp(), or supply both; supplying exactly one"
            " raises invalid_parameter_value. Depth is clamped to"
            f" {_NEIGHBORHOOD_DEPTH_MAX} and"
            f" edges to {_NEIGHBORHOOD_EDGES_MAX}; neither a relation nor an"
            " entity is revisited within one branch.",
        ),
        (
            _PATH_DDL,
            _PATH_SIGNATURE,
            "Simple paths between two entities, traversed undirected over both"
            " half-open D41 clocks. Omit both clocks for statement_timestamp(),"
            " or supply both; supplying exactly one raises"
            " invalid_parameter_value. Depth is clamped to"
            f" {_PATH_DEPTH_MAX}, paths to {_PATH_PATHS_MAX}, and"
            f" edges to {_PATH_EDGES_MAX}; an entity appears at most once in a"
            " path.",
        ),
    ):
        op.execute(ddl)
        op.execute(f"COMMENT ON FUNCTION {signature} IS {_quote(comment)}")
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"ALTER FUNCTION {signature} OWNER TO rememberstack_view_owner")
        op.execute(_GRANT.replace("{signature}", signature))


def downgrade() -> None:
    """Drop both helpers and restore the prior graph-edge view definitions."""
    op.execute(f"DROP FUNCTION IF EXISTS {_PATH_SIGNATURE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_NEIGHBORHOOD_SIGNATURE}")
    prior = _prior_graph_edge_views()
    for name in _GRAPH_EDGE_VIEWS:
        op.execute(prior[name])


def _prior_graph_edge_views() -> dict[str, str]:
    """Extract the immutable p9.01 graph-edge definitions for downgrade."""
    definitions: dict[str, str] = {}
    for block in MEMORY_V1_AUTHORED_DDL:
        for statement in _split_sql(sql=block):
            if not statement.startswith("CREATE VIEW "):
                continue
            name = statement.split(maxsplit=3)[2]
            if name in _GRAPH_EDGE_VIEWS:
                definitions[name] = statement.replace(
                    "CREATE VIEW ", "CREATE OR REPLACE VIEW ", 1
                )
    missing = set(_GRAPH_EDGE_VIEWS) - set(definitions)
    if missing:
        raise RuntimeError(f"missing prior graph-edge views: {sorted(missing)}")
    return definitions


def _quote(value: str) -> str:
    """One SQL string literal."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"
