"""Create ``memory_v1.facts_as_of``, the bounded bitemporal SRF (design §3.4).

The grammar already admits ``facts_as_of`` as a public set-returning function,
so until it exists a legal statement fails for a reason the caller cannot act
on. It is pure SQL: unlike the semantic functions it needs no projection, so it
is a real PostgreSQL function rather than an executor-resolved bridge, and the
planner sees straight through it.

Both clocks are applied as half-open intervals, which is the D41 rule for
facts: a fact holds at ``valid_at`` when ``valid_from <= valid_at`` and either
it never stopped holding or it stopped strictly after that instant, and the
system believed it at ``believed_at`` under the same rule on the belief axis.
The evidence and contradiction counts, and the support state, are the current
ones the view already computes under D54 — hence ``identity_regime`` of
``current``: the row is the current system's reading of an earlier instant,
not a reconstruction of what the counts were then.

The row bound is clamped in the function, not trusted from the caller, so the
§4.3 hard cap holds however the function is invoked, and the ordering is
deterministic so that a bound means the same thing on every run.
"""

from alembic import op

revision: str = "p9_03_0024"
down_revision: str | None = "p9_02_0023"
branch_labels = None
depends_on = None

_MAX_ROWS_HARD_CAP = 1000
_MAX_ROWS_DEFAULT = 200

_FUNCTION_DDL = f"""
CREATE FUNCTION memory_v1.facts_as_of(
  valid_at timestamptz,
  believed_at timestamptz,
  max_rows integer DEFAULT {_MAX_ROWS_DEFAULT}
)
RETURNS TABLE (
  deployment_id uuid,
  fact_kind text,
  fact_id uuid,
  subject_entity_id uuid,
  predicate text,
  object_entity_id uuid,
  statement text,
  fact_label text,
  valid_from timestamptz,
  valid_until timestamptz,
  ingested_at timestamptz,
  invalidated_at timestamptz,
  contradiction_group uuid,
  confidence real,
  evidence_count_current bigint,
  contradict_count_current bigint,
  support_state_current text,
  applied_valid_at timestamptz,
  applied_believed_at timestamptz,
  identity_regime text
)
LANGUAGE sql
STABLE
PARALLEL SAFE
SECURITY INVOKER
SET search_path = memory_v1, pg_catalog
AS $$
  SELECT
    f.deployment_id,
    f.fact_kind,
    f.fact_id,
    f.subject_entity_id,
    f.predicate,
    f.object_entity_id,
    f.statement,
    f.fact_label,
    f.valid_from,
    f.valid_until,
    f.ingested_at,
    f.invalidated_at,
    f.contradiction_group,
    f.confidence,
    f.evidence_count_current,
    f.contradict_count_current,
    f.support_state_current,
    facts_as_of.valid_at,
    facts_as_of.believed_at,
    'current'::text
  FROM memory_v1.facts_visible_history AS f
  WHERE f.ingested_at <= facts_as_of.believed_at
    AND (f.invalidated_at IS NULL OR f.invalidated_at > facts_as_of.believed_at)
    AND f.valid_from <= facts_as_of.valid_at
    AND (f.valid_until IS NULL OR f.valid_until > facts_as_of.valid_at)
  ORDER BY f.fact_kind, f.fact_id
  -- Zero means zero. Clamping an explicit 0 up to 1 would answer a question
  -- the caller did not ask, which §4.3 forbids: clamp, or reject, but never
  -- change what was asked into something else.
  LIMIT least(
    greatest(coalesce(facts_as_of.max_rows, {_MAX_ROWS_DEFAULT}), 0),
    {_MAX_ROWS_HARD_CAP}
  )
$$;
"""

_COMMENT = (
    "Facts that held at valid_at and were believed at believed_at, both clocks"
    " applied as half-open intervals (D41). Counts and support state are the"
    " current ones (D54), so identity_regime is 'current'. max_rows is clamped"
    f" to {_MAX_ROWS_HARD_CAP}."
)


def upgrade() -> None:
    """Create the function and grant it to this database's query role."""
    op.execute(_FUNCTION_DDL)
    op.execute(
        "COMMENT ON FUNCTION memory_v1.facts_as_of(timestamptz, timestamptz, integer)"
        f" IS {_quote(_COMMENT)}"
    )
    op.execute(
        "ALTER FUNCTION memory_v1.facts_as_of(timestamptz, timestamptz, integer)"
        " OWNER TO rememberstack_view_owner"
    )
    # EXECUTE was withdrawn from PUBLIC by the role migration, so the query
    # role is granted explicitly — by the same per-database name that migration
    # derived, since the login is per deployment.
    op.execute(
        """
        DO $do$
        DECLARE
          query_role text := 'rememberstack_query_' || current_database();
        BEGIN
          EXECUTE format(
            'GRANT EXECUTE ON FUNCTION'
            ' memory_v1.facts_as_of(timestamptz, timestamptz, integer) TO %I',
            query_role
          );
        END
        $do$;
        """
    )


def downgrade() -> None:
    """Drop the function; the grant goes with it."""
    op.execute(
        "DROP FUNCTION IF EXISTS"
        " memory_v1.facts_as_of(timestamptz, timestamptz, integer)"
    )


def _quote(value: str) -> str:
    """One SQL string literal."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"
