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

TEMPORAL_FACT_GENERATION = "temporal-facts-d107-d111-1"

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
  ) WHERE (temporal_kind = 'state' AND valid_from IS NOT NULL
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
        AND c.generation = 'temporal-facts-d107-d111-1'
        AND c.state = 'complete'
        AND c.expected_relations = (
          SELECT count(*) FROM public.relations r WHERE r.deployment_id = d.deployment_id)
        AND c.expected_observations = (
          SELECT count(*) FROM public.observations o WHERE o.deployment_id = d.deployment_id)
        AND c.expected_relations + c.expected_observations = (
          SELECT count(*) FROM public.temporal_conversion_rows cr
          WHERE cr.deployment_id = d.deployment_id AND cr.conversion_id = c.conversion_id)
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
    for plane in ("relation", "observation"):
        op.execute(_receipt_guard(plane=plane))
    for statement in _split_sql(sql=TEMPORAL_FINALIZE_DDL):
        op.execute(statement)
    op.execute("""
        INSERT INTO public.temporal_fact_generations (
          deployment_id, generation, conversion_id, verified_at
        )
        SELECT deployment_id, generation, conversion_id, clock_timestamp()
        FROM public.temporal_conversion_runs
        WHERE generation = 'temporal-facts-d107-d111-1' AND state = 'complete'
        ON CONFLICT (deployment_id) DO UPDATE SET
          generation = EXCLUDED.generation,
          conversion_id = EXCLUDED.conversion_id,
          verified_at = EXCLUDED.verified_at
    """)


def _receipt_guard(*, plane: str) -> str:
    """Freeze exact converted state and operation/support attestation checks at D."""
    return f"""
    DO $guard$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM public.{plane}s fact WHERE NOT EXISTS (
          SELECT 1 FROM public.temporal_conversion_rows shadow
          JOIN public.temporal_conversion_runs campaign USING (deployment_id, conversion_id)
          JOIN public.temporal_operations effect ON effect.deployment_id = shadow.deployment_id
            AND effect.operation_id = shadow.operation_id
          JOIN public.temporal_operation_support support ON support.deployment_id = effect.deployment_id
            AND support.operation_id = effect.operation_id
          JOIN public.{plane}_adjudications narrative ON narrative.deployment_id = effect.deployment_id
            AND narrative.temporal_operation_id = effect.operation_id
          WHERE shadow.deployment_id = fact.deployment_id AND shadow.fact_kind = '{plane}'
            AND shadow.fact_id = fact.{plane}_id AND shadow.state = 'verified'
            AND campaign.generation = 'temporal-facts-d107-d111-1' AND campaign.state = 'complete'
            AND effect.{plane}_id = fact.{plane}_id
            AND effect.operation_kind = 'migration' AND effect.result = 'applied'
            AND shadow.expected_revision = 0 AND effect.expected_revision = 0
            AND effect.resulting_revision = 1 AND fact.temporal_revision = 1
            AND effect.input_fingerprint = shadow.input_fingerprint
            AND effect.policy_generation = 'recorded-legacy-authority-2'
            AND narrative.outcome = 'migrate' AND narrative.method = 'migration'
            AND narrative.{plane}_id = fact.{plane}_id
            AND narrative.features -> 'temporal_effect' -> 'after' = shadow.converted_state
            AND support.footprint_complete AND support.expected_block_count > 0
            AND support.expected_block_count = (SELECT count(*) FROM public.temporal_operation_blocks item
              WHERE item.deployment_id = effect.deployment_id AND item.operation_id = effect.operation_id)
            AND support.expected_claim_count = (SELECT count(DISTINCT item.claim_id) FROM public.temporal_operation_evidence item
              WHERE item.deployment_id = effect.deployment_id AND item.operation_id = effect.operation_id)
            AND support.expected_semantic_dependency_count = (SELECT count(*) FROM public.temporal_operation_dependencies item
              WHERE item.deployment_id = effect.deployment_id AND item.operation_id = effect.operation_id
                AND item.required_for_semantics)
            AND shadow.converted_state ?& ARRAY['kind','verdict','occurrence','seed_claim_id',
              'ingested_at','invalidated_at','revision','from_operation_id','until_operation_id','contradiction_group']
            AND shadow.converted_state -> 'verdict' ?& ARRAY['start','end','start_basis','end_basis']
            AND shadow.converted_state -> 'occurrence' ?& ARRAY['start','end','precision']
            AND fact.temporal_kind::text = shadow.converted_state ->> 'kind'
            AND fact.temporal_revision = (shadow.converted_state ->> 'revision')::bigint
            AND fact.valid_from IS NOT DISTINCT FROM (shadow.converted_state #>> '{{verdict,start}}')::timestamptz
            AND fact.valid_until IS NOT DISTINCT FROM (shadow.converted_state #>> '{{verdict,end}}')::timestamptz
            AND fact.valid_from_basis::text = shadow.converted_state #>> '{{verdict,start_basis}}'
            AND fact.valid_until_basis::text = shadow.converted_state #>> '{{verdict,end_basis}}'
            AND fact.occurs_from IS NOT DISTINCT FROM (shadow.converted_state #>> '{{occurrence,start}}')::timestamptz
            AND fact.occurs_until IS NOT DISTINCT FROM (shadow.converted_state #>> '{{occurrence,end}}')::timestamptz
            AND fact.occurs_precision::text IS NOT DISTINCT FROM shadow.converted_state #>> '{{occurrence,precision}}'
            AND fact.seed_claim_id IS NOT DISTINCT FROM (shadow.converted_state ->> 'seed_claim_id')::uuid
            AND fact.ingested_at = (shadow.converted_state ->> 'ingested_at')::timestamptz
            AND fact.invalidated_at IS NOT DISTINCT FROM (shadow.converted_state ->> 'invalidated_at')::timestamptz
            AND fact.from_operation_id IS NOT DISTINCT FROM (shadow.converted_state ->> 'from_operation_id')::uuid
            AND fact.until_operation_id IS NOT DISTINCT FROM (shadow.converted_state ->> 'until_operation_id')::uuid
            AND fact.contradiction_group IS NOT DISTINCT FROM (shadow.converted_state ->> 'contradiction_group')::uuid
            AND effect.new_valid_from IS NOT DISTINCT FROM fact.valid_from
            AND effect.new_valid_until IS NOT DISTINCT FROM fact.valid_until
            AND effect.new_from_basis = fact.valid_from_basis AND effect.new_until_basis = fact.valid_until_basis
            AND effect.new_invalidated_at IS NOT DISTINCT FROM fact.invalidated_at
        )
      ) THEN
        RAISE EXCEPTION 'D107 conversion state or migration support changed before finalization';
      END IF;
    END $guard$;
    """


def downgrade() -> None:
    """Return to the fenced conversion milestone without erasing converted rows."""
    op.execute("DELETE FROM public.temporal_fact_generations")
    op.execute("ALTER TABLE public.relations DROP CONSTRAINT ex_rel_state_world_window")
