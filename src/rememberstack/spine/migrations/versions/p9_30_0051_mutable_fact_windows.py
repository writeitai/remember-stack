"""D118 single mutable world window and guarded fact application stores.

Stores that already hold claims are not converted: their fact dates carry the
old meaning and are recreated by re-ingestion (design §8). Experimental draft
schema heads are not ancestors of this migration and have no automatic
downgrade path.
"""

from alembic import op
from sqlalchemy import text

from rememberstack.spine.migrations._helpers import _split_sql
from rememberstack.spine.migrations._helpers import apply_view_ddl

revision: str = "p9_30_0051"
down_revision: str | None = "p9_29_0050"
branch_labels = None
depends_on = None

_STORAGE_DDL = r"""
CREATE TABLE public.normalization_outputs (
  deployment_id uuid NOT NULL,
  claim_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  output jsonb NOT NULL CHECK (jsonb_typeof(output) = 'object'),
  accepted_outputs jsonb NOT NULL CHECK (jsonb_typeof(accepted_outputs) = 'array'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, claim_id, normalizer_version),
  FOREIGN KEY (deployment_id, claim_id)
    REFERENCES public.claims (deployment_id, claim_id) ON DELETE CASCADE
);

COMMENT ON TABLE public.normalization_outputs IS 'D118 immutable original normalization response and accepted original ordinals per claim and generation.';

CREATE SEQUENCE public.fact_application_admission_sequence;
CREATE TABLE public.fact_applications (
  application_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL,
  claim_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  output_kind text NOT NULL CHECK (output_kind IN ('relation', 'observation')),
  output_ordinal integer NOT NULL CHECK (output_ordinal >= 0),
  adjudicator_version text NOT NULL,
  subject_entity_id uuid NOT NULL,
  object_entity_id uuid,
  admission_sequence bigint UNIQUE,
  attempt_id uuid,
  input_hash text,
  input_claim_ids uuid[] NOT NULL DEFAULT '{}',
  prepared jsonb,
  decision jsonb,
  applied_at timestamptz,
  result jsonb,
  support_relation_id uuid,
  support_observation_id uuid,
  support_stance public.evidence_stance NOT NULL DEFAULT 'supports',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, application_id),
  UNIQUE (deployment_id, claim_id, normalizer_version, output_kind,
          output_ordinal, adjudicator_version),
  FOREIGN KEY (deployment_id, claim_id, normalizer_version)
    REFERENCES public.normalization_outputs
      (deployment_id, claim_id, normalizer_version) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, subject_entity_id)
    REFERENCES public.entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, object_entity_id)
    REFERENCES public.entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, support_relation_id)
    REFERENCES public.relations (deployment_id, relation_id)
    ON DELETE SET NULL (support_relation_id),
  FOREIGN KEY (deployment_id, support_observation_id)
    REFERENCES public.observations (deployment_id, observation_id)
    ON DELETE SET NULL (support_observation_id),
  CHECK ((output_kind = 'relation' AND object_entity_id IS NOT NULL)
      OR (output_kind = 'observation' AND object_entity_id IS NULL)),
  CHECK (num_nonnulls(support_relation_id, support_observation_id) <= 1),
  CHECK ((output_kind = 'relation' AND support_observation_id IS NULL)
      OR (output_kind = 'observation' AND support_relation_id IS NULL)),
  CHECK (num_nonnulls(attempt_id, input_hash, prepared) IN (0, 3)),
  CHECK (decision IS NULL OR prepared IS NOT NULL),
  CHECK ((applied_at IS NULL) = (result IS NULL)),
  CHECK (applied_at IS NULL OR (prepared IS NULL AND decision IS NULL)),
  CHECK (applied_at IS NOT NULL OR
         num_nonnulls(support_relation_id, support_observation_id) = 0)
);
COMMENT ON TABLE public.fact_applications IS 'D118 ordered ordinary assertion application, unlocked inference attempt, atomic original receipt and mutable support pointer; source-owned and erasable.';
CREATE INDEX ix_fact_applications_pending
  ON public.fact_applications (deployment_id, subject_entity_id, admission_sequence)
  WHERE applied_at IS NULL;
CREATE INDEX ix_fact_applications_inputs
  ON public.fact_applications USING gin (input_claim_ids);
CREATE INDEX ix_fact_applications_relation_support
  ON public.fact_applications (deployment_id, support_relation_id, claim_id)
  WHERE support_relation_id IS NOT NULL;
CREATE INDEX ix_fact_applications_observation_support
  ON public.fact_applications (deployment_id, support_observation_id, claim_id)
  WHERE support_observation_id IS NOT NULL;

-- No pending staging rows are permitted at this maintenance boundary.
ALTER TABLE public.normalize_observation_staging
  ADD COLUMN application_id uuid NOT NULL,
  DROP CONSTRAINT normalize_observation_staging_pkey,
  ADD PRIMARY KEY (deployment_id, version_id, application_id),
  ADD FOREIGN KEY (deployment_id, application_id)
    REFERENCES public.fact_applications (deployment_id, application_id)
    ON DELETE CASCADE;
ALTER TABLE public.normalize_observation_staging ALTER COLUMN statement DROP NOT NULL;
ALTER TABLE public.relations
  ADD COLUMN valid_precision public.claim_valid_precision,
  ADD COLUMN window_claim_ids uuid[];
ALTER TABLE public.observations
  ADD COLUMN valid_precision public.claim_valid_precision,
  ADD COLUMN window_claim_ids uuid[];
CREATE INDEX ix_relations_window_claims ON public.relations USING gin (window_claim_ids);
CREATE INDEX ix_observations_window_claims ON public.observations USING gin (window_claim_ids);

-- Drop the original relation exclusion by its catalog identity in the migration;
-- it must not be replaced by any date/type-based uniqueness constraint.
-- Install this exact CHECK on BOTH fact tables:
-- CHECK (
--   (valid_precision = 'unknown' AND valid_from IS NULL AND valid_until IS NULL
--      AND cardinality(window_claim_ids) = 0)
--   OR
--   (valid_precision = 'open' AND valid_from IS NOT NULL AND valid_until IS NULL
--      AND cardinality(window_claim_ids) > 0)
--   OR
--   (valid_precision IN ('instant','day','month','quarter','year')
--      AND num_nonnulls(valid_from, valid_until) > 0
--      AND (valid_from IS NULL OR valid_until IS NULL OR valid_from < valid_until)
--      AND cardinality(window_claim_ids) > 0)
-- );

ALTER TABLE public.relation_adjudications
  ADD COLUMN consumed_claim_ids uuid[] NOT NULL DEFAULT '{}';
ALTER TABLE public.observation_adjudications
  ADD COLUMN consumed_claim_ids uuid[] NOT NULL DEFAULT '{}';
CREATE INDEX ix_relation_adjudications_inputs
  ON public.relation_adjudications USING gin (consumed_claim_ids);
CREATE INDEX ix_observation_adjudications_inputs
  ON public.observation_adjudications USING gin (consumed_claim_ids);

-- Generic mutable-fact update, not a dedicated temporal operation category.
ALTER TYPE public.adjudication_outcome ADD VALUE IF NOT EXISTS 'update';

REVOKE ALL ON public.normalization_outputs, public.fact_applications FROM PUBLIC;
REVOKE ALL ON SEQUENCE public.fact_application_admission_sequence FROM PUBLIC;
-- No published query-role grants on these internal stores or their sequence.

"""

_WINDOW_CHECK = """(
 (valid_precision='unknown' AND valid_from IS NULL AND valid_until IS NULL AND cardinality(window_claim_ids)=0)
 OR (valid_precision='open' AND valid_from IS NOT NULL AND valid_until IS NULL AND cardinality(window_claim_ids)>0)
 OR (valid_precision IN ('instant','day','month','quarter','year')
     AND num_nonnulls(valid_from,valid_until)>0
     AND (valid_from IS NULL OR valid_until IS NULL OR valid_from<valid_until)
     AND cardinality(window_claim_ids)>0)
)"""


FACT_WINDOWS_VIEW_DDL = r"""
CREATE OR REPLACE VIEW memory_v1.facts_visible_history (
  deployment_id, -- The deployment that owns the fact.
  fact_kind, -- Which fact layer this row belongs to, either relation or observation.
  fact_id, -- Stable identity of the adjudicated fact.
  subject_entity_id, -- Survivor identity of the subject entity, with merge redirects resolved.
  predicate, -- The governed predicate of a relation, null for an observation.
  object_entity_id, -- Survivor identity of the object entity of a relation, null for an observation.
  statement, -- The canonical statement of an observation, null for a relation.
  fact_label, -- Human-readable sentence for the fact, null when no label has been generated.
  valid_from, -- Canonical inclusive world-time start; NULL means unknown.
  valid_until, -- Canonical exclusive world-time end; NULL means unknown unless valid_precision is open.
  ingested_at, -- Raw transaction-time start: when the system first believed the fact.
  invalidated_at, -- Raw transaction-time end: when the system learned the fact was superseded, null while it is still believed.
  contradiction_group, -- Shared identifier of an unadjudicated contradiction, null when the fact is in no contradiction group.
  confidence, -- Aggregate confidence over the fact's evidence, null when never scored.
  evidence_count_current, -- LIVE count of distinct current-testimony lineages supporting the fact, read now and never a historical reconstruction.
  contradict_count_current, -- LIVE count of distinct current-testimony lineages contradicting the fact, read now and never a historical reconstruction.
  support_state_current, -- LIVE support state, exactly current or withdrawn, derived now from the open review queue and never a stored column.
  valid_precision -- Chosen world-date precision; unknown and partial boundaries are distinct from explicitly open.
) AS
SELECT
  fact.deployment_id,
  fact.fact_kind,
  fact.fact_id,
  fact.subject_entity_id,
  fact.predicate,
  fact.object_entity_id,
  fact.statement,
  fact.fact_label,
  fact.valid_from,
  fact.valid_until,
  fact.ingested_at,
  fact.invalidated_at,
  fact.contradiction_group,
  fact.confidence,
  counts.supports,
  counts.contradicts,
  CASE WHEN EXISTS (
    SELECT 1
    FROM review_queue AS queue
    WHERE queue.deployment_id = fact.deployment_id
      AND queue.item_kind = 'support_withdrawn'
      AND queue.status IN ('pending', 'deferred')
      AND queue.candidate ->> 'fact_kind' = fact.fact_kind
      AND queue.candidate ->> 'fact_id' = fact.fact_id::text
  ) THEN 'withdrawn' ELSE 'current' END,
  fact.valid_precision
FROM (
  SELECT base.*, coalesce(relation.valid_precision, observation.valid_precision)::text AS valid_precision
  FROM v_memory_fact_visible AS base
  LEFT JOIN relations AS relation ON base.fact_kind='relation'
    AND relation.deployment_id=base.deployment_id AND relation.relation_id=base.fact_id
  LEFT JOIN observations AS observation ON base.fact_kind='observation'
    AND observation.deployment_id=base.deployment_id AND observation.observation_id=base.fact_id
) AS fact
CROSS JOIN LATERAL (
  SELECT
    count(*) FILTER (WHERE lineage.stance = 'supports')::bigint AS supports,
    count(*) FILTER (WHERE lineage.stance = 'contradicts')::bigint AS contradicts
  FROM v_memory_evidence_lineage_live AS lineage
  WHERE lineage.deployment_id = fact.deployment_id
    AND lineage.fact_kind = fact.fact_kind
    AND lineage.fact_id = fact.fact_id
) AS counts;
COMMENT ON VIEW memory_v1.facts_visible_history IS 'Historically visible adjudicated facts with one canonical world window and separate system timestamps. Membership requires surviving provenance. Unknown dates and partial windows are possible temporal matches, not proof of being current; open precision explicitly means ongoing. Completed windows remain believed history while invalidated_at is NULL. Evidence counts and support state are current testimony, not reconstructed historical counts.';

CREATE OR REPLACE VIEW memory_v1.facts_current (
  deployment_id, -- The deployment that owns the fact.
  fact_kind, -- Which fact layer this row belongs to, either relation or observation.
  fact_id, -- Stable identity of the adjudicated fact.
  subject_entity_id, -- Survivor identity of the subject entity, with merge redirects resolved.
  predicate, -- The governed predicate of a relation, null for an observation.
  object_entity_id, -- Survivor identity of the object entity of a relation, null for an observation.
  statement, -- The canonical statement of an observation, null for a relation.
  fact_label, -- Human-readable sentence for the fact, null when no label has been generated.
  valid_from, -- Canonical inclusive world-time start; NULL means unknown.
  valid_until, -- Canonical exclusive world-time end; NULL means unknown unless valid_precision is open.
  ingested_at, -- Transaction-time start: when the system first believed the fact.
  contradiction_group, -- Shared identifier of an unadjudicated contradiction, null when the fact is in no contradiction group.
  confidence, -- Aggregate confidence over the fact's evidence, null when never scored.
  evidence_count, -- Exact count of distinct current-testimony lineages supporting the fact.
  contradict_count, -- Exact count of distinct current-testimony lineages contradicting the fact.
  support_state, -- Exactly current or withdrawn, derived at read time from the open review queue.
  evaluated_at, -- The single statement instant at which both clocks were applied, shared by every current relation referenced in the same statement.
  valid_precision -- Chosen world-date precision; unknown and partial boundaries are distinct from explicitly open.
) AS
SELECT
  h.deployment_id,
  h.fact_kind,
  h.fact_id,
  h.subject_entity_id,
  h.predicate,
  h.object_entity_id,
  h.statement,
  h.fact_label,
  h.valid_from,
  h.valid_until,
  h.ingested_at,
  h.contradiction_group,
  h.confidence,
  h.evidence_count_current,
  h.contradict_count_current,
  h.support_state_current,
  clock.evaluated_at,
  h.valid_precision
FROM (SELECT statement_timestamp() AS evaluated_at) AS clock
CROSS JOIN memory_v1.facts_visible_history AS h
WHERE h.ingested_at <= clock.evaluated_at
  AND h.invalidated_at IS NULL
  AND h.valid_from <= clock.evaluated_at
  AND (h.valid_precision='open' OR h.valid_until > clock.evaluated_at);
COMMENT ON VIEW memory_v1.facts_current IS 'Confirmed facts holding at the single evaluated_at instant and still believed. Unknown or partial windows are excluded from this strict current view; inspect facts_visible_history for possible matches and historical achievements. A NULL end alone never establishes ongoing validity. Counts are distinct current testimony lineages.';
"""

FACTS_AS_OF_DDL = r"""

CREATE FUNCTION memory_v1.facts_as_of(
  valid_at timestamptz,
  believed_at timestamptz,
  max_rows integer DEFAULT 200
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
  identity_regime text,
  valid_precision text,
  temporal_match text
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
    'current'::text,
    f.valid_precision,
    'confirmed'::text
  FROM memory_v1.facts_visible_history AS f
  WHERE (f.ingested_at IS NULL OR f.ingested_at <= facts_as_of.believed_at)
    AND (f.invalidated_at IS NULL OR f.invalidated_at > facts_as_of.believed_at)
    AND f.valid_from <= facts_as_of.valid_at
    AND (f.valid_precision='open' OR f.valid_until > facts_as_of.valid_at)
  ORDER BY f.fact_kind, f.fact_id
  -- Zero means zero. Clamping an explicit 0 up to 1 would answer a question
  -- the caller did not ask, which §4.3 forbids: clamp, or reject, but never
  -- change what was asked into something else.
  LIMIT least(
    greatest(coalesce(facts_as_of.max_rows, 200), 0),
    1000
  )
$$;

"""


def upgrade() -> None:
    """Refuse a populated store, then expand and install the chosen-window shape."""
    connection = op.get_bind()
    # Keep the maintenance drain check and the schema cut in one locked interval.
    op.execute(
        "LOCK TABLE processing_state, normalize_observation_staging IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "ALTER TYPE processing_target ADD VALUE IF NOT EXISTS 'fact_application'"
    )
    # Existing fact dates carry the old meaning (a null end read as "still
    # true", endpoints often copied from source time). They are not converted.
    if connection.execute(text("SELECT EXISTS(SELECT 1 FROM claims)")).scalar_one():
        raise RuntimeError(
            "D118 does not convert a store that already holds claims; "
            "recreate the deployment and ingest its sources again"
        )
    pending = connection.execute(
        text(
            "SELECT EXISTS(SELECT 1 FROM processing_state WHERE status NOT IN ('succeeded','skipped','dead_letter')) OR EXISTS(SELECT 1 FROM normalize_observation_staging)"
        )
    ).scalar_one()
    if pending:
        raise RuntimeError(
            "D118 upgrade requires stopped serving/intake and drained old work/staging"
        )
    for statement in _split_sql(sql=_STORAGE_DDL):
        op.execute(statement)
    for table in ("relations", "observations"):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN valid_precision SET NOT NULL, ALTER COLUMN valid_precision SET DEFAULT 'unknown', ALTER COLUMN window_claim_ids SET NOT NULL, ALTER COLUMN window_claim_ids SET DEFAULT '{{}}'"
        )
        constraints = (
            connection.execute(
                text("""SELECT conname FROM pg_constraint
          WHERE conrelid=CAST(:table AS regclass)
            AND (contype='x' OR (contype='c' AND pg_get_constraintdef(oid) LIKE '%valid_until%' AND pg_get_constraintdef(oid) LIKE '%valid_from%'))
        """),
                {"table": f"public.{table}"},
            )
            .scalars()
            .all()
        )
        for name in constraints:
            op.execute(
                f'ALTER TABLE {table} DROP CONSTRAINT "{str(name).replace(chr(34), chr(34) * 2)}"'
            )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_chosen_window CHECK {_WINDOW_CHECK}"
        )
        # Reject noncanonical bounded endpoints. Do not advance an already exclusive end.
        op.execute(f"""ALTER TABLE {table} ADD CONSTRAINT {table}_window_alignment CHECK (
          valid_precision NOT IN ('day','month','quarter','year') OR
          ((valid_from IS NULL OR valid_from AT TIME ZONE 'UTC'=date_trunc(valid_precision::text,valid_from AT TIME ZONE 'UTC'))
           AND (valid_until IS NULL OR valid_until AT TIME ZONE 'UTC'=date_trunc(valid_precision::text,valid_until AT TIME ZONE 'UTC'))))""")

    apply_view_ddl(sql=FACT_WINDOWS_VIEW_DDL)
    op.execute("DROP FUNCTION memory_v1.facts_as_of(timestamptz,timestamptz,integer)")
    op.execute(FACTS_AS_OF_DDL)
    op.execute(
        "ALTER FUNCTION memory_v1.facts_as_of(timestamptz,timestamptz,integer) OWNER TO rememberstack_view_owner"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION memory_v1.facts_as_of(timestamptz,timestamptz,integer) FROM PUBLIC"
    )
    op.execute(
        """DO $$ BEGIN EXECUTE format('GRANT EXECUTE ON FUNCTION memory_v1.facts_as_of(timestamptz,timestamptz,integer) TO %I','rememberstack_query_' || current_database()); END $$"""
    )

    from rememberstack.spine.fact_graph_contract import rebuild_fact_graphs

    rebuild_fact_graphs(connection=connection)


def downgrade() -> None:
    """Refuse a lossy automatic return to source-time dates and triple uniqueness."""
    raise RuntimeError(
        "D118 downgrade requires an explicitly reviewed restore/conversion plan"
    )
