-- WITHDRAWN BY D118: historical design appendix, not normative or executable upgrade guidance.
-- Replacement authority: mutable_fact_windows_design.md, especially sections 4, 7, 8 and 10.
-- This appendix is no longer incorporated by postgres_schema_design.md.
-- Original DDL follows only to preserve the reasoning behind the superseded design.

-- D113 normative amendment after the D110 temporal schema and existing D90 tables.
-- Applies during the stopped/drained temporal conversion. Legacy metadata is
-- explicitly unpinned; current writers never borrow these default markers.

ALTER TABLE public.obs_flush_version_state
  ADD COLUMN adjudicator_version text NOT NULL DEFAULT 'legacy-unpinned:pre-d113',
  ADD COLUMN flush_version text NOT NULL DEFAULT 'legacy-unpinned:pre-d113',
  ADD COLUMN lane public.processing_lane,
  ADD COLUMN expected_units bigint CHECK (expected_units >= 0),
  DROP CONSTRAINT obs_flush_version_state_pkey,
  ADD PRIMARY KEY (deployment_id, version_id, normalizer_version, adjudicator_version, flush_version),
  ADD CHECK (
    (adjudicator_version = 'legacy-unpinned:pre-d113' AND flush_version = 'legacy-unpinned:pre-d113')
    OR (adjudicator_version <> 'legacy-unpinned:pre-d113' AND flush_version <> 'legacy-unpinned:pre-d113'
        AND lane IS NOT NULL AND expected_units IS NOT NULL)
  );
ALTER TABLE public.obs_flush_version_state
  ALTER COLUMN adjudicator_version DROP DEFAULT,
  ALTER COLUMN flush_version DROP DEFAULT;

ALTER TABLE public.obs_flush_entity_units
  ADD COLUMN adjudicator_version text NOT NULL DEFAULT 'legacy-unpinned:pre-d113',
  ADD COLUMN flush_version text NOT NULL DEFAULT 'legacy-unpinned:pre-d113';
-- D90 has one natural UNIQUE, lacking semantic/flush generation. Resolve its
-- catalog name rather than depending on PostgreSQL's identifier truncation.
DO $d113$ DECLARE old_key text; BEGIN
  SELECT conname INTO STRICT old_key FROM pg_constraint
    WHERE conrelid='public.obs_flush_entity_units'::regclass AND contype='u'
      AND conkey=(SELECT array_agg(a.attnum ORDER BY wanted.ordinality)
        FROM unnest(ARRAY['deployment_id','version_id','normalizer_version','subject_entity_id'])
          WITH ORDINALITY AS wanted(name, ordinality)
        JOIN pg_attribute a ON a.attrelid='public.obs_flush_entity_units'::regclass
          AND a.attname=wanted.name);
  EXECUTE format('ALTER TABLE public.obs_flush_entity_units DROP CONSTRAINT %I', old_key);
END $d113$;
ALTER TABLE public.obs_flush_entity_units
  ADD UNIQUE (deployment_id, version_id, normalizer_version, adjudicator_version, flush_version, subject_entity_id),
  ADD UNIQUE (deployment_id, unit_id),
  ADD FOREIGN KEY (deployment_id, version_id, normalizer_version, adjudicator_version, flush_version)
    REFERENCES public.obs_flush_version_state
      (deployment_id, version_id, normalizer_version, adjudicator_version, flush_version)
    ON DELETE CASCADE,
  ALTER COLUMN adjudicator_version DROP DEFAULT,
  ALTER COLUMN flush_version DROP DEFAULT;

CREATE TABLE public.observation_apply_batches (
  deployment_id uuid NOT NULL REFERENCES public.deployments,
  batch_id uuid NOT NULL,
  canonical_subject_entity_id uuid NOT NULL,
  adjudicator_version text NOT NULL,
  expected_inputs bigint NOT NULL CHECK (expected_inputs > 0),
  admitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  completed_at timestamptz,
  retired_by_checkpoint_id uuid,
  CHECK (retired_by_checkpoint_id IS NULL OR completed_at IS NOT NULL),
  FOREIGN KEY (deployment_id, retired_by_checkpoint_id)
    REFERENCES public.temporal_forget_checkpoints (deployment_id, checkpoint_id),
  PRIMARY KEY (deployment_id, batch_id),
  UNIQUE (deployment_id, batch_id, adjudicator_version)
);
CREATE UNIQUE INDEX uq_obs_active_apply_batch ON public.observation_apply_batches
  (deployment_id, canonical_subject_entity_id) WHERE completed_at IS NULL;
COMMENT ON TABLE public.observation_apply_batches IS
  'D113 closed observation admission, one active canonical entity head across semantic generations; processing_state owns execution.';

CREATE TABLE public.observation_applications (
  deployment_id uuid NOT NULL,
  assertion_id uuid NOT NULL,
  adjudicator_version text NOT NULL,
  receipt_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  normalized_subject_entity_id uuid NOT NULL,
  statement text NOT NULL,
  shape_kind public.fact_temporal_kind NOT NULL,
  batch_id uuid,
  ordinal bigint CHECK (ordinal > 0),
  preparation_id uuid,
  prepared_fingerprint text,
  prepared_snapshot jsonb,
  prepared_output jsonb,
  identity_outcome text CHECK (identity_outcome IN ('new', 'evidence')),
  original_observation_id uuid,
  committed_input_digest text,
  completed_at timestamptz,
  support_state text CHECK (support_state IN ('linked', 'erased')),
  current_observation_id uuid,
  support_owner_operation_id uuid,
  support_checkpoint_id uuid,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (deployment_id, assertion_id, adjudicator_version),
  UNIQUE (deployment_id, batch_id, ordinal),
  FOREIGN KEY (deployment_id, receipt_id, normalizer_version)
    REFERENCES public.normalize_claim_receipts (deployment_id, receipt_id, normalizer_version)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, batch_id, adjudicator_version)
    REFERENCES public.observation_apply_batches (deployment_id, batch_id, adjudicator_version),
  CHECK (num_nonnulls(batch_id, ordinal) IN (0, 2)),
  CHECK (num_nonnulls(preparation_id, prepared_fingerprint, prepared_snapshot) IN (0, 3)),
  CHECK (prepared_output IS NULL OR prepared_snapshot IS NOT NULL),
  CHECK (prepared_fingerprint IS NULL OR prepared_fingerprint ~ '^[0-9a-f]{64}$'),
  CHECK (committed_input_digest IS NULL OR committed_input_digest ~ '^[0-9a-f]{64}$'),
  CHECK (num_nonnulls(identity_outcome, original_observation_id, committed_input_digest,
    completed_at, support_state) IN (0, 5)),
  CHECK (CASE
    WHEN support_state IS NULL THEN current_observation_id IS NULL AND support_owner_operation_id IS NULL
    WHEN support_state = 'linked' THEN current_observation_id IS NOT NULL AND support_owner_operation_id IS NOT NULL
    WHEN support_state = 'erased' THEN current_observation_id IS NULL AND support_owner_operation_id IS NULL
    ELSE false END),
  CHECK (completed_at IS NULL OR batch_id IS NOT NULL),
  CHECK (support_state IS NOT NULL OR support_checkpoint_id IS NULL),
  CHECK (support_state <> 'erased' OR support_checkpoint_id IS NOT NULL)
);
CREATE INDEX ix_obs_application_original_fact ON public.observation_applications
  (deployment_id, original_observation_id);
CREATE INDEX ix_obs_application_current_fact ON public.observation_applications
  (deployment_id, current_observation_id);
CREATE INDEX ix_obs_application_support_owner ON public.observation_applications
  (deployment_id, support_owner_operation_id);
CREATE INDEX ix_obs_application_unapplied ON public.observation_applications
  (deployment_id, normalized_subject_entity_id, adjudicator_version)
  WHERE completed_at IS NULL;
COMMENT ON TABLE public.observation_applications IS
  'D113 immutable source assertion and original application result, with CAS preparation and separately owned current support location.';
COMMENT ON COLUMN public.observation_applications.original_observation_id IS
  'Immutable logical historical identity; absence without a sanitized checkpoint blocks replay and never permits resurrection.';
COMMENT ON COLUMN public.observation_applications.current_observation_id IS
  'Guarded current assertion support, relocated only by typed recorded lifecycle effects; distinct from the immutable original result.';
COMMENT ON COLUMN public.observation_applications.support_owner_operation_id IS
  'Logical establishing operation; initialization and relocation validate authority under the D110 writer, including sanitized checkpoint rules.';

ALTER TABLE public.normalize_observation_staging
  ADD COLUMN membership_id uuid NOT NULL DEFAULT gen_random_uuid(),
  ADD COLUMN adjudicator_version text NOT NULL DEFAULT 'legacy-unpinned:pre-d113',
  ADD COLUMN flush_version text NOT NULL DEFAULT 'legacy-unpinned:pre-d113',
  ADD COLUMN assertion_id uuid,
  ADD COLUMN applied_at timestamptz,
  DROP CONSTRAINT normalize_observation_staging_pkey,
  ADD PRIMARY KEY (deployment_id, membership_id),
  ADD UNIQUE (deployment_id, version_id, normalizer_version, adjudicator_version, flush_version, assertion_id),
  ADD FOREIGN KEY (deployment_id, assertion_id, adjudicator_version)
    REFERENCES public.observation_applications (deployment_id, assertion_id, adjudicator_version)
    ON DELETE CASCADE,
  ADD CHECK (
    (adjudicator_version = 'legacy-unpinned:pre-d113' AND flush_version = 'legacy-unpinned:pre-d113'
      AND assertion_id IS NULL AND applied_at IS NULL)
    OR (adjudicator_version <> 'legacy-unpinned:pre-d113' AND flush_version <> 'legacy-unpinned:pre-d113'
      AND assertion_id IS NOT NULL)
  );
ALTER TABLE public.normalize_observation_staging
  ALTER COLUMN adjudicator_version DROP DEFAULT,
  ALTER COLUMN flush_version DROP DEFAULT;
CREATE INDEX ix_obs_staging_application ON public.normalize_observation_staging
  (deployment_id, assertion_id, adjudicator_version);
CREATE INDEX ix_obs_staging_unapplied ON public.normalize_observation_staging
  (deployment_id, subject_entity_id, adjudicator_version, flush_version)
  WHERE applied_at IS NULL;
COMMENT ON TABLE public.normalize_observation_staging IS
  'D113 retained version-to-assertion membership with exact semantic/flush pins; applied_at is certified receipt reuse, not deletion or inferred empty success.';

ALTER TABLE public.observation_adjudications
  ADD COLUMN triggering_assertion_id uuid,
  ADD UNIQUE (deployment_id, adjudication_id);
COMMENT ON COLUMN public.observation_adjudications.triggering_assertion_id IS
  'D113 logical normalized observation assertion handle; scrub with source-bearing adjudication fields under D74.';
CREATE TABLE public.observation_application_adjudications (
  deployment_id uuid NOT NULL,
  assertion_id uuid NOT NULL,
  adjudicator_version text NOT NULL,
  adjudication_id uuid NOT NULL,
  PRIMARY KEY (deployment_id, assertion_id, adjudicator_version, adjudication_id),
  FOREIGN KEY (deployment_id, assertion_id, adjudicator_version)
    REFERENCES public.observation_applications (deployment_id, assertion_id, adjudicator_version)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, adjudication_id)
    REFERENCES public.observation_adjudications (deployment_id, adjudication_id)
    ON DELETE CASCADE
);
COMMENT ON TABLE public.observation_application_adjudications IS
  'D113 whole observation assertion effect groups, including re-split support transitions; no separate scheduler or narrative authority.';


ALTER TABLE public.observation_evidence
  ADD COLUMN legacy_support boolean NOT NULL DEFAULT true;
ALTER TABLE public.observation_evidence ALTER COLUMN legacy_support SET DEFAULT false;
COMMENT ON COLUMN public.observation_evidence.legacy_support IS
  'D113 preserved pre-application assertion attribution baseline; upserts cannot clear true and no new application alone disproves legacy support.';

CREATE TABLE public.temporal_checkpoint_observation_support (
  deployment_id uuid NOT NULL,
  checkpoint_id uuid NOT NULL,
  assertion_id uuid NOT NULL,
  adjudicator_version text NOT NULL,
  support_state text NOT NULL CHECK (support_state IN ('linked', 'erased')),
  current_observation_id uuid,
  root_operation_id uuid,
  supporting_operation_id uuid,
  value_fingerprint text NOT NULL CHECK (value_fingerprint ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY (deployment_id, checkpoint_id, assertion_id, adjudicator_version),
  FOREIGN KEY (deployment_id, checkpoint_id)
    REFERENCES public.temporal_forget_checkpoints (deployment_id, checkpoint_id),
  FOREIGN KEY (deployment_id, assertion_id, adjudicator_version)
    REFERENCES public.observation_applications (deployment_id, assertion_id, adjudicator_version)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, checkpoint_id, root_operation_id)
    REFERENCES public.temporal_checkpoint_facts (deployment_id, checkpoint_id, root_operation_id),
  FOREIGN KEY (deployment_id, supporting_operation_id)
    REFERENCES public.temporal_operation_support (deployment_id, operation_id),
  CHECK ((support_state='linked' AND current_observation_id IS NOT NULL
      AND root_operation_id IS NOT NULL AND supporting_operation_id IS NOT NULL)
    OR (support_state='erased' AND current_observation_id IS NULL
      AND root_operation_id IS NULL AND supporting_operation_id IS NULL))
);
CREATE INDEX ix_temporal_checkpoint_obs_support ON public.temporal_checkpoint_observation_support
  (deployment_id, assertion_id, adjudicator_version);
COMMENT ON TABLE public.temporal_checkpoint_observation_support IS
  'D113 clean assertion-support assignment roots or explicit erased dispositions; per-assignment attestation survives repeated forget independently of fact endpoint support.';

ALTER TABLE public.observation_applications
  ADD FOREIGN KEY (deployment_id, support_checkpoint_id, assertion_id, adjudicator_version)
    REFERENCES public.temporal_checkpoint_observation_support
      (deployment_id, checkpoint_id, assertion_id, adjudicator_version);
COMMENT ON COLUMN public.observation_applications.support_checkpoint_id IS
  'Exact active assignment checkpoint witness; required for terminal erased support, never inferred from any older historical disposition.';
