"""Validate converted fact authority and publish the D107/D110 schema generation.

A direct upgrade to head cannot silently skip conversion on a populated store.
The caller first commits p9_29_0050, resumes conversion, then invokes this revision.

revision: p9_30_0051
"""

from alembic import op

from rememberstack.spine.migrations._helpers import _split_sql

revision: str = "p9_30_0051"
down_revision: str | None = "p9_29_0050"
branch_labels = None
depends_on = None

TEMPORAL_FACT_GENERATION = "temporal-facts-d107-d110-1"

TEMPORAL_FINALIZE_DDL = r"""
-- STEP D: a separate Alembic upgrade invocation, ONLY after resumable conversion.
-- Its revision verifies completed conversion/generation before any final DDL;
-- constraints and the D revision marker commit together. Empty stores must have
-- their explicit zero-row conversion too. Never infer conversion from defaults.
ALTER TABLE public.relations
  ADD CONSTRAINT ex_rel_state_world_window EXCLUDE USING gist (
    deployment_id WITH =,
    subject_entity_id WITH =,
    predicate WITH =,
    object_entity_id WITH =,
    tstzrange(valid_from, valid_until, '[)') WITH &&
  ) WHERE (temporal_kind = 'state'
           AND valid_from_basis <> 'erased' AND valid_until_basis <> 'erased'
           AND invalidated_at IS NULL AND contradiction_group IS NULL);

ALTER TABLE public.relations VALIDATE CONSTRAINT ck_rel_state_nonempty;
ALTER TABLE public.relations VALIDATE CONSTRAINT ck_rel_occurrence_uncapped;
ALTER TABLE public.relations VALIDATE CONSTRAINT ck_rel_occurs_nonempty;
ALTER TABLE public.relations VALIDATE CONSTRAINT ck_rel_occurs_precision;
ALTER TABLE public.observations VALIDATE CONSTRAINT ck_obs_state_nonempty;
ALTER TABLE public.observations VALIDATE CONSTRAINT ck_obs_occurrence_uncapped;
ALTER TABLE public.observations VALIDATE CONSTRAINT ck_obs_occurs_nonempty;
ALTER TABLE public.observations VALIDATE CONSTRAINT ck_obs_occurs_precision;
"""

_CONVERSION_GUARD = r"""
DO $guard$
BEGIN
  IF EXISTS (
    SELECT 1 FROM public.deployments d
    WHERE NOT EXISTS (
      SELECT 1 FROM public.temporal_conversion_runs c
      WHERE c.deployment_id = d.deployment_id
        AND c.generation = 'temporal-facts-d107-d110-1'
        AND c.state = 'complete'
        AND c.expected_relations = (
          SELECT count(*) FROM public.relations r WHERE r.deployment_id = d.deployment_id)
        AND c.expected_observations = (
          SELECT count(*) FROM public.observations o WHERE o.deployment_id = d.deployment_id)
        AND NOT EXISTS (
          SELECT 1 FROM public.temporal_conversion_rows cr
          WHERE cr.deployment_id = d.deployment_id AND cr.conversion_id = c.conversion_id
            AND cr.state <> 'verified')
        AND NOT EXISTS (
          SELECT 1 FROM public.relations r
          WHERE r.deployment_id = d.deployment_id AND NOT EXISTS (
            SELECT 1 FROM public.temporal_conversion_rows cr
            JOIN public.temporal_operations effect ON effect.operation_id = cr.operation_id
              AND effect.deployment_id = cr.deployment_id
            WHERE cr.deployment_id = d.deployment_id AND cr.conversion_id = c.conversion_id
              AND cr.fact_kind = 'relation' AND cr.fact_id = r.relation_id
              AND cr.state = 'verified' AND effect.relation_id = r.relation_id
              AND effect.resulting_revision = r.temporal_revision))
        AND NOT EXISTS (
          SELECT 1 FROM public.observations o
          WHERE o.deployment_id = d.deployment_id AND NOT EXISTS (
            SELECT 1 FROM public.temporal_conversion_rows cr
            JOIN public.temporal_operations effect ON effect.operation_id = cr.operation_id
              AND effect.deployment_id = cr.deployment_id
            WHERE cr.deployment_id = d.deployment_id AND cr.conversion_id = c.conversion_id
              AND cr.fact_kind = 'observation' AND cr.fact_id = o.observation_id
              AND cr.state = 'verified' AND effect.observation_id = o.observation_id
              AND effect.resulting_revision = o.temporal_revision))
    )
  ) THEN
    RAISE EXCEPTION 'D107 conversion is incomplete; commit p9_29_0050 and resume conversion before upgrading to head';
  END IF;
END $guard$;
"""


def upgrade() -> None:
    """Reject unconverted data, validate state constraints, then certify generation."""
    op.execute(_CONVERSION_GUARD)
    for statement in _split_sql(sql=TEMPORAL_FINALIZE_DDL):
        op.execute(statement)
    op.execute("""
        INSERT INTO public.temporal_fact_generations (
          deployment_id, generation, conversion_id, verified_at
        )
        SELECT deployment_id, generation, conversion_id, clock_timestamp()
        FROM public.temporal_conversion_runs
        WHERE generation = 'temporal-facts-d107-d110-1' AND state = 'complete'
        ON CONFLICT (deployment_id) DO UPDATE SET
          generation = EXCLUDED.generation,
          conversion_id = EXCLUDED.conversion_id,
          verified_at = EXCLUDED.verified_at
    """)


def downgrade() -> None:
    """Return to the fenced conversion milestone without erasing converted rows."""
    op.execute("DELETE FROM public.temporal_fact_generations")
    op.execute("ALTER TABLE public.relations DROP CONSTRAINT ex_rel_state_world_window")
