"""Create tenancy, pipeline, and registry structures."""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables
from rememberstack.spine.migrations._helpers import drop_types

revision: str = "p0_02_0002"
down_revision: str | None = "p0_02_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""-- ─────────────────────────────────────────────────────────────────────────
-- deployments — the tenancy root. One identity row for the independent deployment served by
-- this Postgres instance/schema (D16/D68/registries §1).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE deployments (
  deployment_id   uuid PRIMARY KEY,            -- stable instance identity; appears in every scoped FK
  slug            text NOT NULL UNIQUE,        -- short handle used in GCS bucket names: rememberstack-<slug>-raw etc.
  name            text NOT NULL,               -- human label ("Personal assistant", "Acme migration")
  description     text,                        -- what this deployment is for
  default_language text NOT NULL DEFAULT 'en', -- primary corpus language; gates the multilingual matching path (registries §5)
  raw_bucket      text NOT NULL,               -- gs:// raw bucket (immutable originals; mounted read-only OFF the navigation path, audit-logged — D37/D51)
  artifacts_bucket text NOT NULL,              -- gs:// artifacts bucket (markdown/pageindex, mount-readable) — D37
  corpusfs_bucket text NOT NULL,               -- gs:// P3 corpus-filesystem bucket (snapshots + latest) — D40
  knowledge_repo_uri text,                     -- plane-K git remote (git is truth for K; PG holds provenance only) — D1
  status          deployment_status NOT NULL DEFAULT 'active',
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE deployments IS
  'Identity root for the independent deployment served by this Postgres instance/schema (D16/D68). Entity spaces, registries, graphs and buckets are never shared across deployments; deployment_id is constant here and participates in every scoped FK as defense in depth.';

-- DATA BOUNDARY (D69): Alembic creates this table but inserts no deployment row. After schema
-- head, the library-owned bootstrap described below creates or verifies the one real row from
-- typed profile inputs before any deployment-scoped registry FK row is inserted.

-- ─────────────────────────────────────────────────────────────────────────
-- pipeline_component_versions — what every *_version string means (D1/D12).
-- Pin model+prompt+params so replay-on-rebuild (D7) is auditable and re-process batches can be
-- scoped by a version filter (e.g. re-embed where embedding_version < 'emb-2026-02').
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE pipeline_component_versions (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  component       pipeline_component NOT NULL, -- which producer (see the pipeline_component enum, §1 — every value listed there can appear)
  version         text NOT NULL,               -- the string stamped on artifacts (e.g. 'extractor-2026-03a')
  model_name      text,                        -- LLM/embedding model id, if this component calls a model (e.g. 'claude-opus-4-8')
  prompt_hash     text,                        -- sha256 of the rendered prompt template (registry-derived prompts change ⇒ new version) — D15
  embedding_dim   int,                         -- embedder only: vector dimension (re-embedding is the hardest migration — questions.md Q3)
  params          jsonb NOT NULL DEFAULT '{}', -- decoding params, routing table, thresholds snapshot, etc.
  notes           text,
  configured_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, component, version)
);
COMMENT ON TABLE pipeline_component_versions IS
  'Catalog resolving every *_version string to its model/prompt/params, so replay-on-rebuild (D7) and version-scoped reprocessing are auditable (D1, D12). Every pipeline_component enum value can be catalogued here.';

-- ─────────────────────────────────────────────────────────────────────────
-- processing_state — idempotency + status + dead-letter, the D12 spine.
-- One row per (target, stage, component_version). A worker INSERTs (ON CONFLICT DO NOTHING) this
-- row before work; it is a no-op if a row for the SAME (deployment_id, target_kind, target_id,
-- stage, component_version) already has status='succeeded'. Because target ids are content-derived
-- and stable (a document re-upload resolves to the same doc_id via documents' UNIQUE(deployment,
-- content_hash); sub-document targets are deterministic), the (target,stage,version) tuple IS the
-- content+version idempotency key D12 calls for. content_hash is carried for diagnostics/replay.
-- Lane is intentionally not part of that key: it routes one logical unit of work. A duplicate
-- steady enqueue may promote pending/failed backfill work (never the reverse); an explicit
-- dead-letter replay may also reroute it. Promotion clears only defer_reason='budget' and resets
-- that row's not_before to now(); scheduled and retry_backoff waits are preserved. The dead-letter
-- queue is status='dead_letter' rows.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE processing_state (
  processing_id   uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  target_kind     processing_target NOT NULL,  -- document | document_section | chunk | claim | relation | observation | entity | snapshot | knowledge_artifact
  target_id       uuid NOT NULL,               -- LOGICAL FK → the target table's PK (kind tells you which)
  stage           pipeline_stage NOT NULL,     -- the processing stage (see pipeline_stage enum, §1)
  component_version text NOT NULL,             -- LOGICAL FK → pipeline_component_versions(version); the version this attempt ran
  content_hash    text NOT NULL,               -- sha256 carried for diagnostics/replay; = doc raw-bytes hash, or parent-hash+salt for sub-document targets
  lane            processing_lane,             -- steady | backfill for plane E; NULL for K/P scheduled jobs (D67); stage/lane pairing enforced at the spine enqueue path (catalog UNLANED_STAGES), not by a stage-enumerating CHECK
  status          processing_status NOT NULL DEFAULT 'pending',
  not_before      timestamptz NOT NULL DEFAULT now(), -- canonical earliest claim time; never hidden in payload (D67)
  defer_reason    processing_defer_reason,      -- scheduled | retry_backoff | budget; constrained to the corresponding status below
  attempts        smallint NOT NULL DEFAULT 0, -- handler executions actually begun; delivery attempts and budget parking do not increment it
  max_attempts    smallint NOT NULL DEFAULT 3, -- total handler-attempt limit; starting point = initial + D12's two retries
  last_error      text,                        -- full traceback + cause chain for the most recent handler failure; failures never disappear
  payload         jsonb,                       -- open-ended handler input for DLQ inspection/replay; never route, due, retry, budget, or DLQ state
  enqueued_at     timestamptz NOT NULL DEFAULT now(),
  started_at      timestamptz,
  finished_at     timestamptz,
  UNIQUE (deployment_id, target_kind, target_id, stage, component_version),
  UNIQUE (deployment_id, processing_id),       -- supports tenancy-safe cost_ledger FK
  CHECK (attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts),
  CHECK (status <> 'failed' OR attempts < max_attempts),
  CHECK (
    (status = 'failed' AND defer_reason = 'retry_backoff') OR
    (status = 'pending' AND (defer_reason IS NULL OR defer_reason IN ('scheduled','budget'))) OR
    (status NOT IN ('pending','failed') AND defer_reason IS NULL)
  )
);
COMMENT ON TABLE processing_state IS
  'Per-(target,stage,version) idempotency and work-truth ledger (D12/D67). Route is deployment+stage+lane; not_before/defer_reason govern scheduling, retry backoff, and no-attempt budget parking. The DLQ is status=dead_letter rows; delivery-provider metadata is never authoritative.';
CREATE INDEX ix_procstate_dlq      ON processing_state (deployment_id, stage) WHERE status = 'dead_letter';
CREATE INDEX ix_procstate_due      ON processing_state (deployment_id, stage, lane, not_before, enqueued_at, processing_id) WHERE status IN ('pending','failed');
CREATE INDEX ix_procstate_target   ON processing_state (target_kind, target_id);

-- Transactional initial wake for the self-host shell (D67). PostgreSQL delivers NOTIFY only if
-- the INSERT commits; future-scheduled rows are discovered by the due-work fallback poll. The
-- delivery port never INSERTs processing_state. Explicit retry/replay/janitor announcements call
-- the same pg_notify operation through spine after their state transition commits.
CREATE FUNCTION notify_due_processing_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status IN ('pending','failed') AND NEW.not_before <= now() THEN
    PERFORM pg_notify('queue_wake', NEW.processing_id::text);
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER tr_processing_state_initial_wake
AFTER INSERT ON processing_state
FOR EACH ROW EXECUTE FUNCTION notify_due_processing_insert();

-- ─────────────────────────────────────────────────────────────────────────
-- cost_ledger — per-invocation cost/latency metering for enforced per-layer budgets (§8 overall).
-- A succeeded-but-ack-lost call must not be re-billed on retry, while D31 and other handlers may
-- make several calls in one attempt. A ledger row is therefore anchored to one processing row,
-- handler attempt, and deterministic stage-local call_key. A batched call (a D58 window) is
-- billed as one row on the claiming processing row — a batch never crosses a document or a lane,
-- so lane budgets and document-level accounting stay exact without splitting. Enforcement reads
-- the deduplicated total. The spine cost-write method accepts processing_id + call_key + measured cost;
-- while the processing row is locked/running it copies stage, lane, and attempts from that row.
-- Callers and delivery envelopes cannot supply or override those three attribution fields.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE cost_ledger (
  cost_id         uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  processing_id   uuid NOT NULL,               -- owning processing_state row; composite FK below
  stage           pipeline_stage NOT NULL,     -- which layer/stage incurred the spend
  lane            processing_lane,             -- copied from processing_state when the billed call begins; NULL for unlaned K/P work (D67); pairing enforced upstream (spine copies from the validated row)
  target_kind     processing_target,           -- optional: what was being processed
  target_id       uuid,                        -- LOGICAL FK → target
  component_version text,                       -- LOGICAL FK → pipeline_component_versions(version)
  attempt         smallint NOT NULL,           -- processing_state.attempts value when this call began; starts at 1
  call_key        text NOT NULL,               -- deterministic key within the attempt, e.g. selection | decontextualize | adjudicate:<candidate_id>
  model_name      text,                        -- model billed
  tier            text,                        -- cascade rung that fired (e.g. 'T4-small','T4-frontier','selection','decontext') — cost scales with ambiguity (D4/D17)
  tokens_in       bigint,                      -- prompt tokens (incl. cached-prefix accounting where applicable)
  tokens_out      bigint,                      -- completion tokens
  cost_usd        numeric(12,6),               -- billed cost in USD
  latency_ms      integer,                     -- wall-clock of the call
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (deployment_id, processing_id) REFERENCES processing_state (deployment_id, processing_id),
  UNIQUE (deployment_id, processing_id, attempt, call_key), -- multiple calls per attempt; one row per logical call
  CHECK (attempt >= 1)
);
COMMENT ON TABLE cost_ledger IS
  'Append-only LLM/embedding call attribution for D67 budgets. Idempotent per (processing_id,attempt,call_key), so multi-call handlers are complete and acknowledged-late retries cannot double-count. A batched call (D58) bills one row on the claiming processing row; batches never cross a document or lane, so lane and document accounting stay exact. Nullable diagnostic target fields do not weaken deduplication.';
CREATE INDEX ix_cost_budget_window ON cost_ledger (deployment_id, stage, lane, occurred_at);
-- ─────────────────────────────────────────────────────────────────────────
-- extension_packs — system-shipped sets of types+predicates a deployment enables as a unit.
-- "Extensions are not second-class" (registries §4): full entity status, governance tier only.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE extension_packs (
  pack_id         text PRIMARY KEY,            -- stable pack handle: 'work' | 'legal' | 'systems' | ...
  name            text NOT NULL,
  description     text NOT NULL,               -- what the pack is for and which deployments want it
  is_system       boolean NOT NULL DEFAULT true -- shipped with the system vs deployment-defined
);
COMMENT ON TABLE extension_packs IS
  'Predefined bundles of extension types/predicates (registries §4) — e.g. the Work pack (Task/Decision/Goal). Enabling a pack inserts its registry rows for a deployment.';

CREATE TABLE deployment_extension_packs (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  pack_id         text NOT NULL REFERENCES extension_packs,
  enabled_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, pack_id)
);
COMMENT ON TABLE deployment_extension_packs IS 'Which packs each deployment has enabled (registries §1: core + chosen packs + own scopes).';

-- ─────────────────────────────────────────────────────────────────────────
-- scopes — K2 special-purpose lenses over the one shared entity space (D16).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE scopes (
  scope_id        uuid NOT NULL,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  slug            text NOT NULL,               -- 'people' | 'product-atlas' | ...
  name            text NOT NULL,
  description     text,
  git_path        text,                        -- directory in the K-plane repo this scope compiles into (K2)
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (scope_id),
  UNIQUE (deployment_id, scope_id),            -- composite-FK target (tenancy isolation, §0)
  UNIQUE (deployment_id, slug)
);
COMMENT ON TABLE scopes IS
  'K2 scopes (D16): perspectives over shared evidence. "Scopes multiply; truth doesn''t" — a scope is registry rows + a view definition + a git directory, never a new database.';

-- ─────────────────────────────────────────────────────────────────────────
-- entity_types — the typed-entity vocabulary (8-type core + pack/scope extensions, D18).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE entity_types (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  type            text NOT NULL,               -- 'Person','Organization',... ; extension subtypes like 'ResearchPaper'
  parent_type     text,                        -- extend-never-fork: every extension declares a core parent (D15); NULL only for the 8 core roots
  description     text NOT NULL,               -- plain-language meaning, rendered into extraction prompts (D15)
  examples        text[] NOT NULL DEFAULT '{}',-- few-shot examples rendered into prompts
  schema_org_ref  text,                        -- schema.org anchor (D18); spot-checked before freezing
  tier            ontology_tier NOT NULL,      -- core | extension | other | deprecated (the three speeds, D15)
  pack_id         text REFERENCES extension_packs, -- non-null if from an extension pack
  scope_id        uuid,                        -- non-null if defined by a single K2 scope
  status          ontology_status NOT NULL DEFAULT 'active',
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, type),
  FOREIGN KEY (deployment_id, parent_type) REFERENCES entity_types (deployment_id, type),
  FOREIGN KEY (deployment_id, scope_id)    REFERENCES scopes (deployment_id, scope_id) ON DELETE SET NULL (scope_id)
);
COMMENT ON TABLE entity_types IS
  'Governed entity-type registry (D15/D18). 8 schema.org-aligned core roots + pack/scope extensions, each anchored to a core parent (extend-never-fork) so blocking and cross-scope queries always fall back to the core level.';
CREATE INDEX ix_entity_types_parent ON entity_types (deployment_id, parent_type);

-- ─────────────────────────────────────────────────────────────────────────
-- predicates — the governed relation vocabulary (D5/D18). related_to is the core parent.
-- The other:<freetext> escape (D5) is materialized here too: when the normalizer encounters an
-- other:<value>, it UPSERTs a row with tier='other' (so relations.predicate's FK holds AND the
-- promotion funnel is countable via usage_count). Domain/range is NOT enforced for tier='other'
-- rows until a periodic job promotes them (registries §4/§7).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE predicates (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  predicate       text NOT NULL,               -- 'works_for',... ; 'other:<freetext>' rows live here as tier='other'
  parent_predicate text,                        -- optional predicate-side extend-never-fork anchor (default parent 'related_to', D18)
  description     text NOT NULL,               -- meaning rendered into normalization prompts
  examples        text[] NOT NULL DEFAULT '{}',
  synonyms        text[] NOT NULL DEFAULT '{}',-- surface variants the normalizer maps onto this predicate (works_at/employed_by → works_for) — D5
  schema_org_ref  text,
  tier            ontology_tier NOT NULL,      -- core | extension | other | deprecated
  pack_id         text REFERENCES extension_packs,
  scope_id        uuid,
  usage_count     bigint NOT NULL DEFAULT 0,   -- cached count of relations using this predicate; ranks tier='other' values for promotion (D5 funnel)
  is_change_prone boolean NOT NULL DEFAULT false, -- employment/affiliation/location change over time ⇒ supersession-relevant (D18)
  exclude_from_graph_distance boolean NOT NULL DEFAULT false, -- causal/promiscuous predicates excluded from graph-distance reranking (registries §4)
  status          ontology_status NOT NULL DEFAULT 'active',
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, predicate),
  FOREIGN KEY (deployment_id, parent_predicate) REFERENCES predicates (deployment_id, predicate),
  FOREIGN KEY (deployment_id, scope_id) REFERENCES scopes (deployment_id, scope_id) ON DELETE SET NULL (scope_id)
);
COMMENT ON TABLE predicates IS
  'Governed predicate vocabulary (D5/D18). Extraction is constrained to these names with an other:<freetext> escape, which the normalizer upserts as tier=other rows (FK holds; usage_count makes the promotion funnel queryable). related_to is the permissive core parent.';
CREATE INDEX ix_predicates_other ON predicates (deployment_id, usage_count DESC) WHERE tier = 'other'; -- promotion-candidate ranking

-- ─────────────────────────────────────────────────────────────────────────
-- predicate_signatures — the domain/range gate (Graphiti edge_type_map shape, D18).
-- A child table because a predicate may allow several (subject_type,object_type) pairs. ENFORCEMENT
-- is by the NORMALIZER at E3 write time (application-enforced, not a per-insert DB trigger — at 10⁸
-- relations a trigger walking the type hierarchy on every write is too costly): the normalizer
-- resolves the subject/object entity types, walks each up its parent chain (extend-never-fork), and
-- accepts the relation only if some signature matches at any ancestor level. A relation that fails
-- is DROPPED — and is re-derivable from its immutable claim if the entity is later retyped, so no
-- quarantine table is needed (registries §4). (An optional BEFORE INSERT trigger may be added as a
-- belt-and-braces backstop in low-throughput deployments.)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE predicate_signatures (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  predicate       text NOT NULL,               -- the predicate this signature constrains
  subject_type    text NOT NULL,               -- allowed subject entity_type (matched at this level OR any descendant via the normalizer's parent walk)
  object_type     text NOT NULL,               -- allowed object entity_type
  PRIMARY KEY (deployment_id, predicate, subject_type, object_type),
  FOREIGN KEY (deployment_id, predicate)    REFERENCES predicates (deployment_id, predicate) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, subject_type) REFERENCES entity_types (deployment_id, type),
  FOREIGN KEY (deployment_id, object_type)  REFERENCES entity_types (deployment_id, type)
);
COMMENT ON TABLE predicate_signatures IS
  'Allowed (subject_type → object_type) pairs per predicate — the one structural ontology gate (D18), enforced by the normalizer (parent-chain walk) at E3 write time. Subtypes inherit a parent''s signatures; a relation matching none is dropped (re-derivable from its claim).';

-- ─────────────────────────────────────────────────────────────────────────
-- scope_interests — registry-declared scope views + extraction interests (D16).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE scope_interests (
  interest_id     uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  scope_id        uuid NOT NULL,
  interest_type   scope_interest_kind NOT NULL, -- entity_type | predicate | metadata | keyword
  value           text NOT NULL,                -- the type name / predicate / metadata key / keyword
  UNIQUE (scope_id, interest_type, value),
  FOREIGN KEY (deployment_id, scope_id) REFERENCES scopes (deployment_id, scope_id) ON DELETE CASCADE
);
COMMENT ON TABLE scope_interests IS
  'Per-scope interest list (D16/D98): the predicate/metadata footprint that selects live PostgreSQL graph/fact rows and K2 compilation inputs. A query/compile-time selection over fully-extracted facts — never a promotion trigger (D28 withdrawn).';
"""
_TABLES = (
    "deployments",
    "pipeline_component_versions",
    "processing_state",
    "cost_ledger",
    "extension_packs",
    "deployment_extension_packs",
    "scopes",
    "entity_types",
    "predicates",
    "predicate_signatures",
    "scope_interests",
)
_TYPES = ()


def upgrade() -> None:
    """Apply create tenancy, pipeline, and registry structures."""
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Revert create tenancy, pipeline, and registry structures."""
    drop_tables(table_names=reversed(_TABLES))
    op.execute("DROP FUNCTION IF EXISTS notify_due_processing_insert()")
    drop_types(type_names=reversed(_TYPES))
