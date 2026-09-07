-- D114 application storage contract; review target, not an automatic converter.
-- Apply only with serving/intake/workers stopped and legacy staging drained.
-- The implementation migration must also close its fact-generation readiness gate.

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

-- No pending legacy rows are permitted at this maintenance boundary.
ALTER TABLE public.normalize_observation_staging
  ADD COLUMN application_id uuid NOT NULL,
  DROP CONSTRAINT normalize_observation_staging_pkey,
  ADD PRIMARY KEY (deployment_id, version_id, application_id),
  ADD FOREIGN KEY (deployment_id, application_id)
    REFERENCES public.fact_applications (deployment_id, application_id)
    ON DELETE CASCADE;
ALTER TABLE public.relation_evidence
  ADD COLUMN legacy_support boolean NOT NULL DEFAULT true;
ALTER TABLE public.observation_evidence
  ADD COLUMN legacy_support boolean NOT NULL DEFAULT true;
-- New links explicitly set legacy_support=false; the default preserves old callers
-- until their participation is removed at the guarded generation cutover.

ALTER TABLE public.relations
  ADD COLUMN valid_precision public.claim_valid_precision NOT NULL DEFAULT 'unknown',
  ADD COLUMN window_claim_ids uuid[] NOT NULL DEFAULT '{}';
ALTER TABLE public.observations
  ADD COLUMN valid_precision public.claim_valid_precision NOT NULL DEFAULT 'unknown',
  ADD COLUMN window_claim_ids uuid[] NOT NULL DEFAULT '{}';
CREATE INDEX ix_relations_window_claims ON public.relations USING gin (window_claim_ids);
CREATE INDEX ix_observations_window_claims ON public.observations USING gin (window_claim_ids);

-- Drop the original relation exclusion by its catalog identity in the migration;
-- it must not be replaced by any date/type-based uniqueness constraint.
-- After grounded conversion, install this exact CHECK on BOTH fact tables:
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
-- Never expose the expand/convert interval through an open readiness gate.

-- Generic mutable-fact update, not a dedicated temporal operation category.
ALTER TYPE public.adjudication_outcome ADD VALUE IF NOT EXISTS 'update';

REVOKE ALL ON public.normalization_outputs, public.fact_applications FROM PUBLIC;
REVOKE ALL ON SEQUENCE public.fact_application_admission_sequence FROM PUBLIC;
-- No published query-role grants on these internal stores or their sequence.
