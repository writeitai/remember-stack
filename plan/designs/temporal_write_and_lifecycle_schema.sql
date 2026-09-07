-- WITHDRAWN BY D114: historical design appendix, not normative or executable upgrade guidance.
-- Replacement authority: mutable_fact_windows_design.md, especially sections 4, 7, 8 and 10.
-- This appendix is no longer incorporated by postgres_schema_design.md.
-- Original DDL follows only to preserve the reasoning behind the superseded design.

-- D110 temporal write/lifecycle schema amendment.
-- Binding with temporal_write_and_lifecycle_design.md; not an automatic data converter.
-- Baseline: D107 T.0b structural head on main. Keep serving/legacy writes fenced
-- throughout expand, conversion and final validation. Migration entry points own
-- one-time DDL; temporal conversion owns resumable row work.
-- STEP A: commit enum additions before workers insert records using new values.
ALTER TYPE public.processing_target ADD VALUE IF NOT EXISTS 'temporal_correction';
ALTER TYPE public.processing_target ADD VALUE IF NOT EXISTS 'temporal_event';
ALTER TYPE public.pipeline_stage ADD VALUE IF NOT EXISTS 'correct_temporal';
ALTER TYPE public.pipeline_stage ADD VALUE IF NOT EXISTS 'refresh_temporal';
ALTER TYPE public.pipeline_component ADD VALUE IF NOT EXISTS 'temporal_adjudicator';
ALTER TYPE public.pipeline_component ADD VALUE IF NOT EXISTS 'temporal_refresher';
ALTER TYPE public.adjudication_outcome ADD VALUE IF NOT EXISTS 'temporal_correct';
ALTER TYPE public.adjudication_outcome ADD VALUE IF NOT EXISTS 'temporal_compensate';
ALTER TYPE public.adjudication_outcome ADD VALUE IF NOT EXISTS 'temporal_uncertain';

ALTER TYPE public.adjudication_outcome ADD VALUE IF NOT EXISTS 'migrate';

ALTER TYPE public.adjudication_method ADD VALUE IF NOT EXISTS 'migration';

-- Commit STEP A before conversion uses any newly added enum value.
-- STEP B: expand schema and receipts. No claim that DEFAULT unknown converts data.

CREATE TYPE public.fact_temporal_kind AS ENUM ('state', 'occurrence', 'unknown');
CREATE TYPE public.fact_temporal_basis AS ENUM
  ('world_time', 'verdict', 'source_removed', 'legacy', 'unknown', 'erased');

ALTER TABLE public.relations
  ADD COLUMN temporal_kind public.fact_temporal_kind NOT NULL DEFAULT 'unknown',
  ADD COLUMN valid_from_basis public.fact_temporal_basis NOT NULL DEFAULT 'unknown',
  ADD COLUMN valid_until_basis public.fact_temporal_basis NOT NULL DEFAULT 'unknown',
  ADD COLUMN seed_claim_id uuid,
  ADD COLUMN occurs_from timestamptz,
  ADD COLUMN occurs_until timestamptz,
  ADD COLUMN occurs_precision public.claim_valid_precision,
  ADD COLUMN temporal_revision bigint NOT NULL DEFAULT 0,
  ADD CONSTRAINT ck_rel_temporal_revision CHECK (temporal_revision >= 0);

ALTER TABLE public.observations
  ADD COLUMN temporal_kind public.fact_temporal_kind NOT NULL DEFAULT 'unknown',
  ADD COLUMN valid_from_basis public.fact_temporal_basis NOT NULL DEFAULT 'unknown',
  ADD COLUMN valid_until_basis public.fact_temporal_basis NOT NULL DEFAULT 'unknown',
  ADD COLUMN seed_claim_id uuid,
  ADD COLUMN occurs_from timestamptz,
  ADD COLUMN occurs_until timestamptz,
  ADD COLUMN occurs_precision public.claim_valid_precision,
  ADD COLUMN temporal_revision bigint NOT NULL DEFAULT 0,
  ADD CONSTRAINT ck_obs_temporal_revision CHECK (temporal_revision >= 0);

COMMENT ON COLUMN public.relations.seed_claim_id IS
  'Logical deployment-scoped claim reference: recorded creator, NULL for unrecoverable legacy provenance or hard forget; never minimum evidence claim.';
COMMENT ON COLUMN public.observations.seed_claim_id IS
  'Logical deployment-scoped claim reference: recorded creator, NULL for unrecoverable legacy provenance or hard forget.';
COMMENT ON COLUMN public.relations.temporal_revision IS
  'Monotonic revision for the fact temporal decision and evidence-derived bounds; changed only by guarded catalog writes, used for correction CAS and cache fencing.';
COMMENT ON COLUMN public.observations.temporal_revision IS
  'Monotonic revision for the fact temporal decision and evidence-derived bounds; changed only by guarded catalog writes, used for correction CAS and cache fencing.';

ALTER TABLE public.relations
  ADD CONSTRAINT ck_rel_state_nonempty CHECK
    (temporal_kind <> 'state' OR valid_from IS NULL OR valid_until IS NULL
      OR valid_until > valid_from) NOT VALID,
  ADD CONSTRAINT ck_rel_occurrence_uncapped CHECK
    (temporal_kind <> 'occurrence' OR valid_until IS NULL) NOT VALID,
  ADD CONSTRAINT ck_rel_occurs_nonempty CHECK
    (occurs_from IS NULL OR occurs_until IS NULL OR occurs_until > occurs_from)
    NOT VALID,
  ADD CONSTRAINT ck_rel_occurs_precision CHECK
    ((occurs_precision IS NULL AND occurs_from IS NULL AND occurs_until IS NULL)
      OR (occurs_precision IS NOT NULL AND occurs_precision <> 'unknown'
          AND occurs_from IS NOT NULL)) NOT VALID;

ALTER TABLE public.observations
  ADD CONSTRAINT ck_obs_state_nonempty CHECK
    (temporal_kind <> 'state' OR valid_from IS NULL OR valid_until IS NULL
      OR valid_until > valid_from) NOT VALID,
  ADD CONSTRAINT ck_obs_occurrence_uncapped CHECK
    (temporal_kind <> 'occurrence' OR valid_until IS NULL) NOT VALID,
  ADD CONSTRAINT ck_obs_occurs_nonempty CHECK
    (occurs_from IS NULL OR occurs_until IS NULL OR occurs_until > occurs_from)
    NOT VALID,
  ADD CONSTRAINT ck_obs_occurs_precision CHECK
    ((occurs_precision IS NULL AND occurs_from IS NULL AND occurs_until IS NULL)
      OR (occurs_precision IS NOT NULL AND occurs_precision <> 'unknown'
          AND occurs_from IS NOT NULL)) NOT VALID;

-- Normalization, admission and exact generation membership.
CREATE TABLE public.normalize_claim_receipts (
  receipt_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES public.deployments,
  claim_id uuid NOT NULL,
  doc_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  outcome text NOT NULL CHECK (outcome IN ('accepted', 'empty', 'soft_drop')),
  input_digest text NOT NULL,
  output_digest text NOT NULL,
  normalization_output jsonb NOT NULL,
  relation_count integer NOT NULL CHECK (relation_count >= 0),
  observation_count integer NOT NULL CHECK (observation_count >= 0),
  published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (deployment_id, claim_id, normalizer_version),
  UNIQUE (deployment_id, receipt_id, normalizer_version),
  FOREIGN KEY (deployment_id, claim_id)
    REFERENCES public.claims (deployment_id, claim_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, doc_id)
    REFERENCES public.documents (deployment_id, doc_id) ON DELETE CASCADE,
  CHECK (jsonb_typeof(normalization_output) = 'object'),
  CHECK (normalization_output ?& ARRAY['relations', 'observations']),
  CHECK (jsonb_typeof(normalization_output -> 'relations') = 'array'),
  CHECK (jsonb_typeof(normalization_output -> 'observations') = 'array'),
  CHECK (relation_count = jsonb_array_length(normalization_output -> 'relations')),
  CHECK (observation_count = jsonb_array_length(normalization_output -> 'observations')),
  CHECK ((outcome = 'accepted') = (relation_count + observation_count > 0))
);

CREATE TABLE public.normalize_relation_assertions (
  assertion_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES public.deployments,
  receipt_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  subject_entity_id uuid NOT NULL,
  predicate text NOT NULL,
  object_entity_id uuid NOT NULL,
  shape_kind text NOT NULL CHECK (shape_kind IN ('state', 'occurrence', 'unknown')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (deployment_id, assertion_id, normalizer_version),
  UNIQUE (deployment_id, assertion_id),
  UNIQUE (receipt_id, subject_entity_id, predicate, object_entity_id),
  FOREIGN KEY (deployment_id, receipt_id, normalizer_version)
    REFERENCES public.normalize_claim_receipts
      (deployment_id, receipt_id, normalizer_version) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, subject_entity_id)
    REFERENCES public.entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, object_entity_id)
    REFERENCES public.entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, predicate)
    REFERENCES public.predicates (deployment_id, predicate) ON UPDATE CASCADE
);
CREATE INDEX ix_rel_assertion_block ON public.normalize_relation_assertions
  (deployment_id, subject_entity_id, predicate, assertion_id);
CREATE INDEX ix_normalize_receipt_doc ON public.normalize_claim_receipts
  (deployment_id, doc_id);

CREATE TABLE public.relation_flush_version_state (
  deployment_id uuid NOT NULL REFERENCES public.deployments,
  version_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  representation_id uuid NOT NULL,
  doc_id uuid NOT NULL,
  chunker_version text NOT NULL,
  extractor_version text NOT NULL,
  adjudicator_version text NOT NULL,
  content_hash text NOT NULL,
  lane public.processing_lane NOT NULL,
  fanout_status text NOT NULL
    CHECK (fanout_status IN ('materialized', 'empty_complete', 'barrier_complete')),
  expected_units integer NOT NULL CHECK (expected_units >= 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  completed_at timestamptz,
  PRIMARY KEY (deployment_id, version_id, normalizer_version),
  UNIQUE (deployment_id, version_id, normalizer_version, adjudicator_version),
  FOREIGN KEY (deployment_id, doc_id, version_id)
    REFERENCES public.document_versions (deployment_id, doc_id, version_id)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, version_id, representation_id)
    REFERENCES public.document_representations
      (deployment_id, version_id, representation_id) ON DELETE CASCADE,
  CHECK ((fanout_status = 'materialized') = (completed_at IS NULL)),
  CHECK (fanout_status <> 'empty_complete' OR expected_units = 0)
);

CREATE TABLE public.relation_flush_block_units (
  unit_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL,
  version_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  adjudicator_version text NOT NULL,
  subject_entity_id uuid NOT NULL,
  predicate text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (deployment_id, version_id, normalizer_version, subject_entity_id, predicate),
  UNIQUE (deployment_id, unit_id, normalizer_version, adjudicator_version),
  FOREIGN KEY (deployment_id, version_id, normalizer_version, adjudicator_version)
    REFERENCES public.relation_flush_version_state
      (deployment_id, version_id, normalizer_version, adjudicator_version)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, subject_entity_id)
    REFERENCES public.entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, predicate)
    REFERENCES public.predicates (deployment_id, predicate) ON UPDATE CASCADE
);
CREATE INDEX ix_rel_flush_units_block ON public.relation_flush_block_units
  (deployment_id, subject_entity_id, predicate, adjudicator_version);

CREATE TABLE public.relation_flush_inputs (
  deployment_id uuid NOT NULL,
  unit_id uuid NOT NULL,
  assertion_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  adjudicator_version text NOT NULL,
  applied_at timestamptz,
  PRIMARY KEY (unit_id, assertion_id),
  FOREIGN KEY (deployment_id, unit_id, normalizer_version, adjudicator_version)
    REFERENCES public.relation_flush_block_units
      (deployment_id, unit_id, normalizer_version, adjudicator_version)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, assertion_id, normalizer_version)
    REFERENCES public.normalize_relation_assertions
      (deployment_id, assertion_id, normalizer_version) ON DELETE CASCADE
);
CREATE INDEX ix_rel_flush_unapplied ON public.relation_flush_inputs
  (deployment_id, unit_id, assertion_id) WHERE applied_at IS NULL;
CREATE INDEX ix_rel_flush_input_assertion ON public.relation_flush_inputs
  (deployment_id, assertion_id, adjudicator_version);

CREATE TABLE public.relation_apply_batches (
  batch_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES public.deployments,
  subject_entity_id uuid NOT NULL,
  predicate text NOT NULL,
  adjudicator_version text NOT NULL,
  expected_inputs bigint NOT NULL CHECK (expected_inputs > 0),
  admitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  completed_at timestamptz,
  UNIQUE (deployment_id, batch_id, adjudicator_version),
  FOREIGN KEY (deployment_id, subject_entity_id)
    REFERENCES public.entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, predicate)
    REFERENCES public.predicates (deployment_id, predicate) ON UPDATE CASCADE
);

CREATE UNIQUE INDEX uq_rel_active_batch
  ON public.relation_apply_batches
    (deployment_id, subject_entity_id, predicate)
  WHERE completed_at IS NULL;

CREATE TABLE public.relation_apply_batch_inputs (
  deployment_id uuid NOT NULL,
  batch_id uuid NOT NULL,
  adjudicator_version text NOT NULL,
  ordinal bigint NOT NULL CHECK (ordinal > 0),
  preparation_id uuid,
  prepared_fingerprint text,
  prepared_snapshot jsonb,
  prepared_output jsonb,
  CHECK (num_nonnulls(preparation_id, prepared_fingerprint, prepared_snapshot) IN (0, 3)),
  CHECK (prepared_output IS NULL OR prepared_snapshot IS NOT NULL),
  assertion_id uuid NOT NULL,
  PRIMARY KEY (batch_id, ordinal),
  UNIQUE (deployment_id, assertion_id, adjudicator_version),
  UNIQUE (deployment_id, batch_id, ordinal, assertion_id, adjudicator_version),
  FOREIGN KEY (deployment_id, batch_id, adjudicator_version)
    REFERENCES public.relation_apply_batches
      (deployment_id, batch_id, adjudicator_version) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, assertion_id)
    REFERENCES public.normalize_relation_assertions
      (deployment_id, assertion_id) ON DELETE CASCADE
);

CREATE TABLE public.relation_application_receipts (
  deployment_id uuid NOT NULL,
  assertion_id uuid NOT NULL,
  adjudicator_version text NOT NULL,
  batch_id uuid NOT NULL,
  ordinal bigint NOT NULL,
  identity_outcome text NOT NULL CHECK (identity_outcome IN ('new', 'evidence')),
  target_count bigint NOT NULL CHECK (target_count > 0),
  target_digest text NOT NULL CHECK (target_digest ~ '^[0-9a-f]{64}$'),
  CHECK (identity_outcome <> 'new' OR target_count = 1),
  input_digest text NOT NULL,
  completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (deployment_id, assertion_id, adjudicator_version),
  FOREIGN KEY (deployment_id, batch_id, ordinal, assertion_id, adjudicator_version)
    REFERENCES public.relation_apply_batch_inputs
      (deployment_id, batch_id, ordinal, assertion_id, adjudicator_version)
    ON DELETE CASCADE
);
COMMENT ON COLUMN public.relation_application_receipts.target_digest IS
  'D112 SHA-256 of the complete sorted canonical UUID JSON target array; verify with target_count before replay or completion.';

CREATE TABLE public.relation_application_targets (
  deployment_id uuid NOT NULL,
  assertion_id uuid NOT NULL,
  adjudicator_version text NOT NULL,
  relation_id uuid NOT NULL,
  PRIMARY KEY (deployment_id, assertion_id, adjudicator_version, relation_id),
  FOREIGN KEY (deployment_id, assertion_id, adjudicator_version)
    REFERENCES public.relation_application_receipts
      (deployment_id, assertion_id, adjudicator_version) ON DELETE CASCADE
);
COMMENT ON COLUMN public.relation_application_targets.relation_id IS
  'Historical logical support target (D112); validate under locks at apply. A retained receipt cannot resurrect a forgotten fact.';
CREATE INDEX ix_rel_application_target_fact ON public.relation_application_targets
  (deployment_id, relation_id);

ALTER TABLE public.relation_adjudications
  ADD COLUMN triggering_assertion_id uuid,
  ADD CONSTRAINT uq_rel_adjudication_deployment
    UNIQUE (deployment_id, adjudication_id);
COMMENT ON COLUMN public.relation_adjudications.triggering_assertion_id IS
  'Logical reference to normalize_relation_assertions; scrub with triggering_claim_id on hard forget.';

CREATE TABLE public.relation_application_adjudications (
  deployment_id uuid NOT NULL,
  assertion_id uuid NOT NULL,
  adjudicator_version text NOT NULL,
  adjudication_id uuid NOT NULL,
  PRIMARY KEY (deployment_id, assertion_id, adjudicator_version, adjudication_id),
  FOREIGN KEY (deployment_id, assertion_id, adjudicator_version)
    REFERENCES public.relation_application_receipts
      (deployment_id, assertion_id, adjudicator_version) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, adjudication_id)
    REFERENCES public.relation_adjudications (deployment_id, adjudication_id)
    ON DELETE CASCADE
);

-- Single temporal mutation history.
CREATE TABLE temporal_blocks (
    deployment_id uuid NOT NULL REFERENCES deployments (deployment_id),
    block_key text NOT NULL,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    last_sequence bigint NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    PRIMARY KEY (deployment_id, block_key)
);

CREATE TABLE temporal_discrepancies (
    discrepancy_id uuid PRIMARY KEY,
    deployment_id uuid NOT NULL REFERENCES deployments (deployment_id),
    relation_id uuid,
    observation_id uuid,
    input_fingerprint text NOT NULL,
    policy_generation text NOT NULL,
    state text NOT NULL CHECK (state IN
        ('ready', 'prepared', 'complete', 'retryable', 'superseded')),
    reason_code text NOT NULL,
    preparation_id uuid,
    prepared_fingerprint text,
    prepared_snapshot jsonb,
    prepared_at timestamptz,
    prepared_output jsonb,
    created_at timestamptz NOT NULL,
    UNIQUE (deployment_id, discrepancy_id),
    CHECK (num_nonnulls(relation_id, observation_id) = 1),
    CHECK (num_nonnulls(preparation_id, prepared_fingerprint, prepared_snapshot) IN (0, 3)),
    CHECK (prepared_output IS NULL OR prepared_snapshot IS NOT NULL),
    FOREIGN KEY (deployment_id, relation_id)
        REFERENCES relations (deployment_id, relation_id) ON DELETE CASCADE,
    FOREIGN KEY (deployment_id, observation_id)
        REFERENCES observations (deployment_id, observation_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX ux_temporal_discrepancy_relation
    ON temporal_discrepancies
        (deployment_id, relation_id, input_fingerprint, policy_generation)
    WHERE relation_id IS NOT NULL;
CREATE UNIQUE INDEX ux_temporal_discrepancy_observation
    ON temporal_discrepancies
        (deployment_id, observation_id, input_fingerprint, policy_generation)
    WHERE observation_id IS NOT NULL;

CREATE TABLE temporal_operations (
    operation_id uuid PRIMARY KEY,
    deployment_id uuid NOT NULL REFERENCES deployments (deployment_id),
    relation_id uuid,
    observation_id uuid,
    discrepancy_id uuid,
    operation_kind text NOT NULL CHECK (operation_kind IN
        ('seed', 'evidence', 'correction', 'compensation', 'cap',
         'source_removal', 'migration', 'identity', 'forget_recompute')),
    result text NOT NULL CHECK (result IN
        ('applied', 'noop', 'uncertain', 'refused', 'stale')),
    expected_revision bigint NOT NULL CHECK (expected_revision >= 0),
    resulting_revision bigint NOT NULL CHECK (resulting_revision >= 0),
    old_valid_from timestamptz,
    old_valid_until timestamptz,
    old_from_basis public.fact_temporal_basis NOT NULL,
    old_until_basis public.fact_temporal_basis NOT NULL,
    new_valid_from timestamptz,
    new_valid_until timestamptz,
    new_from_basis public.fact_temporal_basis NOT NULL,
    new_until_basis public.fact_temporal_basis NOT NULL,
    old_invalidated_at timestamptz,
    new_invalidated_at timestamptz,
    old_from_operation_id uuid,
    old_until_operation_id uuid,
    changed_from boolean NOT NULL DEFAULT false,
    changed_until boolean NOT NULL DEFAULT false,
    reverses_operation_id uuid,
    input_fingerprint text NOT NULL,
    identity_generation text NOT NULL,
    policy_generation text NOT NULL,
    reason_code text NOT NULL,
    recorded_at timestamptz NOT NULL,
    UNIQUE (deployment_id, operation_id),
    CHECK (num_nonnulls(relation_id, observation_id) = 1),
    CHECK (changed_from = (ROW(old_valid_from, old_from_basis)
      IS DISTINCT FROM ROW(new_valid_from, new_from_basis))),
    CHECK (changed_until = (ROW(old_valid_until, old_until_basis)
      IS DISTINCT FROM ROW(new_valid_until, new_until_basis))),
    CHECK ((operation_kind = 'compensation') = (reverses_operation_id IS NOT NULL)),
    CHECK (reverses_operation_id IS DISTINCT FROM operation_id),
    CHECK (result = 'applied' OR (
        resulting_revision = expected_revision
        AND old_valid_from IS NOT DISTINCT FROM new_valid_from
        AND old_valid_until IS NOT DISTINCT FROM new_valid_until
        AND old_from_basis = new_from_basis
        AND old_until_basis = new_until_basis
        AND old_invalidated_at IS NOT DISTINCT FROM new_invalidated_at
        AND NOT changed_from AND NOT changed_until)),
    CHECK (result <> 'applied' OR resulting_revision = expected_revision + 1),
    -- Fact targets are historical logical references, validated by ordinary apply.
    FOREIGN KEY (deployment_id, discrepancy_id)
        REFERENCES temporal_discrepancies (deployment_id, discrepancy_id)
        ON DELETE SET NULL (discrepancy_id),
    FOREIGN KEY (deployment_id, reverses_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    FOREIGN KEY (deployment_id, old_from_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    FOREIGN KEY (deployment_id, old_until_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id)
);
CREATE INDEX ix_temporal_operations_relation
    ON temporal_operations (deployment_id, relation_id, resulting_revision);
CREATE INDEX ix_temporal_operations_observation
    ON temporal_operations (deployment_id, observation_id, resulting_revision);

CREATE TABLE temporal_operation_blocks (
    deployment_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    block_key text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    writes_block boolean NOT NULL,
    expected_block_revision bigint NOT NULL CHECK (expected_block_revision >= 0),
    resulting_block_revision bigint NOT NULL CHECK (resulting_block_revision >= 0),
    CHECK (resulting_block_revision = expected_block_revision
      + CASE WHEN writes_block THEN 1 ELSE 0 END),
    PRIMARY KEY (deployment_id, operation_id, block_key),
    UNIQUE (deployment_id, block_key, sequence),
    FOREIGN KEY (deployment_id, operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    FOREIGN KEY (deployment_id, block_key)
        REFERENCES temporal_blocks (deployment_id, block_key)
);

CREATE TABLE temporal_operation_dependencies (
    deployment_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    predecessor_operation_id uuid NOT NULL,
    PRIMARY KEY (deployment_id, operation_id, predecessor_operation_id),
    CHECK (operation_id <> predecessor_operation_id),
    FOREIGN KEY (deployment_id, operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    FOREIGN KEY (deployment_id, predecessor_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id)
);

CREATE TABLE temporal_operation_evidence (
    deployment_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    evidence_role text NOT NULL CHECK (evidence_role IN
        ('support', 'contrary', 'historical', 'candidate_from', 'candidate_until')),
    was_current boolean NOT NULL,
    evidence_fingerprint text NOT NULL,
    PRIMARY KEY (deployment_id, operation_id, claim_id, evidence_role),
    FOREIGN KEY (deployment_id, operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    FOREIGN KEY (deployment_id, claim_id)
        REFERENCES claims (deployment_id, claim_id)
);
CREATE INDEX ix_temporal_evidence_claim
    ON temporal_operation_evidence (deployment_id, claim_id, operation_id);

ALTER TABLE relations
    ADD COLUMN from_operation_id uuid,
    ADD COLUMN until_operation_id uuid,
    ADD FOREIGN KEY (deployment_id, from_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    ADD FOREIGN KEY (deployment_id, until_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id);
ALTER TABLE observations
    ADD COLUMN from_operation_id uuid,
    ADD COLUMN until_operation_id uuid,
    ADD FOREIGN KEY (deployment_id, from_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    ADD FOREIGN KEY (deployment_id, until_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id);

ALTER TABLE relation_adjudications
    ADD COLUMN temporal_operation_id uuid,
    ADD FOREIGN KEY (deployment_id, temporal_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id);
ALTER TABLE observation_adjudications
    ADD COLUMN temporal_operation_id uuid,
    ADD FOREIGN KEY (deployment_id, temporal_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id);
CREATE UNIQUE INDEX ux_relation_adjudication_temporal_operation
    ON relation_adjudications (deployment_id, temporal_operation_id)
    WHERE temporal_operation_id IS NOT NULL;
CREATE UNIQUE INDEX ux_observation_adjudication_temporal_operation
    ON observation_adjudications (deployment_id, temporal_operation_id)
    WHERE temporal_operation_id IS NOT NULL;

-- Cache certification and work identities (processing_state is the only scheduler).
CREATE TABLE temporal_sources (
  deployment_id uuid NOT NULL REFERENCES deployments,
  source_kind text NOT NULL CHECK (source_kind IN (
    'relation','observation','entity','predicate','document','doc_source',
    'scope','rule_owner','page_publication','structural','broad_rule'
  )),
  source_id uuid NOT NULL,
  source_key text NOT NULL,
  UNIQUE (deployment_id, source_kind, source_key),
  revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
  next_boundary_at timestamptz,
  PRIMARY KEY (deployment_id, source_kind, source_id)
);
CREATE INDEX ix_temporal_sources_next
  ON temporal_sources (deployment_id, next_boundary_at, source_kind, source_id)
  WHERE next_boundary_at IS NOT NULL;

-- Each fact is a leaf; memberships attach its two future boundaries to
-- deterministic routing keys. No recursive source-key graph is introduced.
CREATE TABLE temporal_source_members (
  deployment_id uuid NOT NULL,
  source_kind text NOT NULL,
  source_id uuid NOT NULL,
  fact_kind text NOT NULL CHECK (fact_kind IN ('relation','observation')),
  fact_id uuid NOT NULL,
  fact_revision bigint NOT NULL CHECK (fact_revision >= 0),
  activation_at timestamptz,
  expiry_at timestamptz,
  PRIMARY KEY (deployment_id, source_kind, source_id, fact_kind, fact_id),
  FOREIGN KEY (deployment_id, source_kind, source_id)
    REFERENCES temporal_sources (deployment_id, source_kind, source_id)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, fact_kind, fact_id)
    REFERENCES temporal_sources (deployment_id, source_kind, source_id)
    ON DELETE CASCADE
);
CREATE INDEX ix_temporal_members_activation
  ON temporal_source_members
    (deployment_id, source_kind, source_id, activation_at, fact_kind, fact_id)
  WHERE activation_at IS NOT NULL;
CREATE INDEX ix_temporal_members_expiry
  ON temporal_source_members
    (deployment_id, source_kind, source_id, expiry_at, fact_kind, fact_id)
  WHERE expiry_at IS NOT NULL;
CREATE INDEX ix_temporal_members_fact
  ON temporal_source_members (deployment_id, fact_kind, fact_id);

CREATE TABLE temporal_artifact_certificates (
  certificate_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES deployments,
  entity_id uuid,
  artifact_id uuid,
  generation text NOT NULL,
  input_hash text,
  evaluated_at timestamptz,
  fresh_until timestamptz,
  publication_revision bigint NOT NULL DEFAULT 0
    CHECK (publication_revision >= 0),
  CHECK (num_nonnulls(entity_id, artifact_id) = 1),
  CHECK ((input_hash IS NULL) = (evaluated_at IS NULL)),
  CHECK (evaluated_at IS NOT NULL OR fresh_until IS NULL),
  CHECK (fresh_until IS NULL OR fresh_until > evaluated_at),
  UNIQUE (deployment_id, certificate_id),
  FOREIGN KEY (deployment_id, entity_id)
    REFERENCES entities (deployment_id, entity_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, artifact_id)
    REFERENCES knowledge_artifacts (deployment_id, artifact_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_temporal_profile_certificate
  ON temporal_artifact_certificates (deployment_id, entity_id)
  WHERE entity_id IS NOT NULL;
CREATE UNIQUE INDEX uq_temporal_page_certificate
  ON temporal_artifact_certificates (deployment_id, artifact_id)
  WHERE artifact_id IS NOT NULL;
CREATE INDEX ix_temporal_certificate_expiry
  ON temporal_artifact_certificates (deployment_id, fresh_until, certificate_id)
  WHERE fresh_until IS NOT NULL;

CREATE TABLE temporal_artifact_dependencies (
  deployment_id uuid NOT NULL,
  certificate_id uuid NOT NULL,
  source_kind text NOT NULL,
  source_id uuid NOT NULL,
  observed_revision bigint NOT NULL CHECK (observed_revision >= 0),
  PRIMARY KEY (deployment_id, certificate_id, source_kind, source_id),
  FOREIGN KEY (deployment_id, certificate_id)
    REFERENCES temporal_artifact_certificates (deployment_id, certificate_id)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, source_kind, source_id)
    REFERENCES temporal_sources (deployment_id, source_kind, source_id)
    ON DELETE RESTRICT
);
CREATE INDEX ix_temporal_dependencies_source
  ON temporal_artifact_dependencies
    (deployment_id, source_kind, source_id, certificate_id);

CREATE TABLE fact_expiry_schedule (
  event_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES deployments,
  source_kind text,
  source_id uuid,
  source_revision bigint,
  certificate_id uuid,
  certificate_revision bigint,
  event_kind text NOT NULL CHECK (event_kind IN (
    'activation','expiry','invalidation','artifact_refresh','routing_resume'
  )),
  boundary_at timestamptz NOT NULL,
  generator_version text NOT NULL,
  continuation_sequence bigint NOT NULL DEFAULT 0 CHECK (continuation_sequence >= 0),
  cursor_kind text,
  cursor_id uuid,
  CHECK (
    (source_kind IS NOT NULL AND source_id IS NOT NULL
     AND source_revision IS NOT NULL AND source_revision >= 0
     AND certificate_id IS NULL AND certificate_revision IS NULL)
    OR
    (source_kind IS NULL AND source_id IS NULL AND source_revision IS NULL
     AND certificate_id IS NOT NULL AND certificate_revision IS NOT NULL
     AND certificate_revision >= 0)
  ),
  CHECK ((cursor_kind IS NULL) = (cursor_id IS NULL)),
  UNIQUE (deployment_id, event_id),
  FOREIGN KEY (deployment_id, source_kind, source_id)
    REFERENCES temporal_sources (deployment_id, source_kind, source_id)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, certificate_id)
    REFERENCES temporal_artifact_certificates (deployment_id, certificate_id)
    ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_temporal_source_event
  ON fact_expiry_schedule (
    deployment_id, source_kind, source_id, source_revision,
    event_kind, boundary_at, generator_version, continuation_sequence
  ) WHERE source_kind IS NOT NULL;
CREATE UNIQUE INDEX uq_temporal_artifact_event
  ON fact_expiry_schedule (
    deployment_id, certificate_id, certificate_revision,
    event_kind, boundary_at, generator_version
  ) WHERE certificate_id IS NOT NULL;
CREATE INDEX ix_temporal_event_source
  ON fact_expiry_schedule (deployment_id, source_kind, source_id)
  WHERE source_kind IS NOT NULL;
CREATE INDEX ix_temporal_event_artifact
  ON fact_expiry_schedule (deployment_id, certificate_id)
  WHERE certificate_id IS NOT NULL;
CREATE INDEX ix_temporal_event_boundary
  ON fact_expiry_schedule (deployment_id, boundary_at, event_id);

-- Sanitized forget roots, support attestation, and covered-history boundaries.
ALTER TABLE relations
  ADD CHECK (valid_from_basis::text <> 'erased' OR valid_from IS NULL),
  ADD CHECK (valid_until_basis::text <> 'erased' OR valid_until IS NULL);
ALTER TABLE observations
  ADD CHECK (valid_from_basis::text <> 'erased' OR valid_from IS NULL),
  ADD CHECK (valid_until_basis::text <> 'erased' OR valid_until IS NULL);

ALTER TABLE temporal_operations
  ADD CHECK (old_from_basis <> 'erased' OR old_valid_from IS NULL),
  ADD CHECK (old_until_basis <> 'erased' OR old_valid_until IS NULL),
  ADD CHECK (new_from_basis <> 'erased' OR new_valid_from IS NULL),
  ADD CHECK (new_until_basis <> 'erased' OR new_valid_until IS NULL);

CREATE TABLE temporal_operation_support (
  deployment_id uuid NOT NULL,
  operation_id uuid NOT NULL,
  support_state text NOT NULL CHECK
    (support_state IN ('complete','erased','unproven')),
  footprint_complete boolean NOT NULL DEFAULT false,
  expected_block_count integer NOT NULL DEFAULT 0 CHECK (expected_block_count >= 0),
  expected_claim_count integer NOT NULL CHECK (expected_claim_count >= 0),
  expected_semantic_dependency_count integer NOT NULL
    CHECK (expected_semantic_dependency_count >= 0),
  support_fingerprint text NOT NULL,
  PRIMARY KEY (deployment_id, operation_id),
  FOREIGN KEY (deployment_id, operation_id)
    REFERENCES temporal_operations (deployment_id, operation_id)
);
ALTER TABLE temporal_operation_dependencies
  ADD COLUMN required_for_semantics boolean NOT NULL DEFAULT true;

CREATE TABLE temporal_forget_checkpoints (
  checkpoint_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL,
  forget_id uuid NOT NULL,
  checkpoint_format integer NOT NULL DEFAULT 1 CHECK (checkpoint_format = 1),
  state text NOT NULL DEFAULT 'preparing'
    CHECK (state IN ('preparing','verified','superseded')),
  created_at timestamptz NOT NULL,
  verified_at timestamptz,
  superseded_by uuid,
  inventory_hash text NOT NULL,
  policy_generation text NOT NULL,
  CHECK ((state = 'preparing') = (verified_at IS NULL)),
  CHECK ((state = 'superseded') = (superseded_by IS NOT NULL)),
  CHECK (superseded_by IS DISTINCT FROM checkpoint_id),
  UNIQUE (deployment_id, checkpoint_id),
  UNIQUE (deployment_id, forget_id),
  FOREIGN KEY (deployment_id, forget_id)
    REFERENCES forget_manifests (deployment_id, forget_id),
  FOREIGN KEY (deployment_id, superseded_by)
    REFERENCES temporal_forget_checkpoints (deployment_id, checkpoint_id)
);

CREATE TABLE temporal_checkpoint_blocks (
  deployment_id uuid NOT NULL,
  checkpoint_id uuid NOT NULL,
  block_key text NOT NULL,
  covered_through_sequence bigint NOT NULL CHECK (covered_through_sequence >= 0),
  covered_through_revision bigint NOT NULL CHECK (covered_through_revision >= 0),
  resume_sequence bigint NOT NULL CHECK (resume_sequence >= 0),
  resume_revision bigint NOT NULL CHECK (resume_revision >= 0),
  CHECK (resume_sequence >= covered_through_sequence),
  CHECK (resume_revision >= covered_through_revision),
  PRIMARY KEY (deployment_id, checkpoint_id, block_key),
  FOREIGN KEY (deployment_id, checkpoint_id)
    REFERENCES temporal_forget_checkpoints (deployment_id, checkpoint_id),
  FOREIGN KEY (deployment_id, block_key)
    REFERENCES temporal_blocks (deployment_id, block_key)
);
CREATE INDEX ix_temporal_checkpoint_block
  ON temporal_checkpoint_blocks (deployment_id, block_key, resume_sequence);

ALTER TABLE temporal_operations
  ADD COLUMN replay_class text NOT NULL DEFAULT 'ordinary'
    CHECK (replay_class IN ('ordinary','checkpoint_root','covered')),
  ADD COLUMN covered_by_checkpoint_id uuid,
  ADD CHECK ((replay_class = 'covered') =
    (covered_by_checkpoint_id IS NOT NULL)),
  ADD FOREIGN KEY (deployment_id, covered_by_checkpoint_id)
    REFERENCES temporal_forget_checkpoints (deployment_id, checkpoint_id);

CREATE TABLE temporal_checkpoint_facts (
  deployment_id uuid NOT NULL,
  checkpoint_id uuid NOT NULL,
  fact_kind text NOT NULL CHECK (fact_kind IN ('relation','observation')),
  fact_id uuid NOT NULL,
  root_operation_id uuid NOT NULL,
  temporal_kind text NOT NULL CHECK (temporal_kind IN ('state','occurrence','unknown')),
  occurs_from timestamptz,
  occurs_until timestamptz,
  occurs_precision public.claim_valid_precision,
  seed_claim_id uuid,
  ingested_at timestamptz NOT NULL,
  temporal_revision bigint NOT NULL CHECK (temporal_revision >= 0),
  CHECK (occurs_from IS NULL OR occurs_until IS NULL OR occurs_until > occurs_from),
  CHECK ((occurs_precision IS NULL AND occurs_from IS NULL AND occurs_until IS NULL)
    OR (occurs_precision IS NOT NULL AND occurs_precision <> 'unknown'
        AND occurs_from IS NOT NULL)),
  PRIMARY KEY (deployment_id, checkpoint_id, fact_kind, fact_id),
  UNIQUE (deployment_id, checkpoint_id, root_operation_id),
  FOREIGN KEY (deployment_id, checkpoint_id)
    REFERENCES temporal_forget_checkpoints (deployment_id, checkpoint_id),
  FOREIGN KEY (deployment_id, root_operation_id)
    REFERENCES temporal_operations (deployment_id, operation_id),
  FOREIGN KEY (deployment_id, seed_claim_id)
    REFERENCES claims (deployment_id, claim_id)
);
CREATE INDEX ix_temporal_checkpoint_fact
  ON temporal_checkpoint_facts (deployment_id, fact_kind, fact_id);
CREATE INDEX ix_temporal_checkpoint_seed
  ON temporal_checkpoint_facts (deployment_id, seed_claim_id)
  WHERE seed_claim_id IS NOT NULL;

CREATE TABLE temporal_checkpoint_components (
  deployment_id uuid NOT NULL,
  checkpoint_id uuid NOT NULL,
  root_operation_id uuid NOT NULL,
  component text NOT NULL CHECK (component IN ('from','until','kind')),
  supporting_operation_id uuid NOT NULL,
  value_fingerprint text NOT NULL,
  PRIMARY KEY (deployment_id, checkpoint_id, root_operation_id, component),
  FOREIGN KEY (deployment_id, checkpoint_id, root_operation_id)
    REFERENCES temporal_checkpoint_facts
      (deployment_id, checkpoint_id, root_operation_id),
  FOREIGN KEY (deployment_id, supporting_operation_id)
    REFERENCES temporal_operation_support (deployment_id, operation_id)
);

-- Conversion is a pinned, fenced campaign; these are domain progress records,
-- never a second work queue. The existing work ledger owns execution/retries.
CREATE TABLE temporal_conversion_runs (
  conversion_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES deployments,
  generation text NOT NULL,
  input_generation text NOT NULL,
  policy_fingerprint text NOT NULL,
  state text NOT NULL CHECK (state IN ('preparing','converting','verifying','complete','failed')),
  expected_relations bigint NOT NULL CHECK (expected_relations >= 0),
  expected_observations bigint NOT NULL CHECK (expected_observations >= 0),
  captured_at timestamptz NOT NULL,
  completed_at timestamptz,
  CHECK ((state = 'complete') = (completed_at IS NOT NULL)),
  UNIQUE (deployment_id, generation),
  UNIQUE (deployment_id, conversion_id)
);
CREATE TABLE temporal_conversion_rows (
  deployment_id uuid NOT NULL,
  conversion_id uuid NOT NULL,
  fact_kind text NOT NULL CHECK (fact_kind IN ('relation','observation')),
  fact_id uuid NOT NULL,
  expected_revision bigint NOT NULL CHECK (expected_revision >= 0),
  input_fingerprint text NOT NULL,
  converted_state jsonb NOT NULL CHECK (jsonb_typeof(converted_state) = 'object'),
  state text NOT NULL CHECK (state IN ('prepared','applied','verified')),
  operation_id uuid,
  CHECK ((state = 'prepared') = (operation_id IS NULL)),
  PRIMARY KEY (deployment_id, conversion_id, fact_kind, fact_id),
  FOREIGN KEY (deployment_id, conversion_id)
    REFERENCES temporal_conversion_runs (deployment_id, conversion_id),
  FOREIGN KEY (deployment_id, operation_id)
    REFERENCES temporal_operations (deployment_id, operation_id)
);
CREATE INDEX ix_temporal_conversion_progress ON temporal_conversion_rows
  (deployment_id, conversion_id, state, fact_kind, fact_id);
CREATE TABLE temporal_fact_generations (
  deployment_id uuid PRIMARY KEY REFERENCES deployments,
  generation text NOT NULL,
  conversion_id uuid NOT NULL,
  verified_at timestamptz NOT NULL,
  FOREIGN KEY (deployment_id, conversion_id)
    REFERENCES temporal_conversion_runs (deployment_id, conversion_id)
);

-- STEP C: with intake/serving fenced and all legacy writers drained, drop
-- the old all-kind exclusion BEFORE applying any converted occurrence row.
-- This is one Alembic revision: its version marker and DDL commit atomically.
-- The orchestrator upgrades explicitly TO this revision, commits, then resumes
-- data conversion. It does not rerun C on retries or upgrade directly to head.
-- A missing exclusion without this recorded revision is schema drift, not success.
DO $ddl$
DECLARE
  exclusion_name text;
  exclusion_count integer;
BEGIN
  -- Resolve the one authoritative pre-D107 exclusion, failing on schema drift.
  SELECT count(*), min(conname)
    INTO exclusion_count, exclusion_name
  FROM pg_constraint
  WHERE conrelid = 'public.relations'::regclass AND contype = 'x';
  IF exclusion_count <> 1 THEN
    RAISE EXCEPTION 'Expected one legacy relation exclusion; found %', exclusion_count;
  END IF;
  EXECUTE format('ALTER TABLE public.relations DROP CONSTRAINT %I', exclusion_name);
END
$ddl$;
-- Run resumable conversion using the campaign/shadow stores above. Do not
-- continue to D until every expected fact has its validated migration effect.

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
