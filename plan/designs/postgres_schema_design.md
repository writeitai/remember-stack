# Postgres Schema Design — the Plane-E Spine

This document specifies the **complete Postgres schema** for `rememberstack`: every table, column,
primary key, foreign key, index, enum, and the partitioning / deletion / versioning rules that
tie them together. Postgres is the **single source of truth for plane E** (evidence) and the
**only home of validity/invalidation state** (D6). PostgreSQL-native P1 vectors
and indexes are rebuildable derived state, not authority (D94); LadybugDB and the GCS
corpus filesystem are rebuildable projections, while the K-plane git repo is
an independently-backed source of truth whose *provenance and triggers* live here.

It is the binding companion to `overall_design.md` (§3 core data model, §9 lists this doc),
`postgres_p1_search_projection_design.md` (D94 PostgreSQL-native search schema),
`registries_design.md` (D15–D24), `e0_files_design.md` (D36–D40),
`e2_e3_claims_relations_design.md` (D31–D35), `p2_graph_design.md` (D6–D11),
`concepts.md` (the claims/relations/evidence/bi-temporality explainer) and `decisions.md`
(D1–D69). Where a table or column exists *because of* a decision, the decision is cited inline.

> **Amended 2026-08-26 (D95–D97).** Entity identity is untyped:
> `entities.type`, `entities.type_confidence`, `mentions.emitted_type`,
> `mentions.type_confidence`, and `predicate_signatures` are absent. There is
> no hats table or domain/range write gate. The dormant `entity_types` seed
> table remains only as non-authoritative registry inventory; no resolver,
> writer, projection, or query contract consumes it. Authority:
> [`entity_identity_and_retrieval_design.md`](entity_identity_and_retrieval_design.md).

> **Reading this as a stranger (CLAUDE.md Rule 1).** You do not need to have been in the design
> conversation. Each module opens with what it stores and *why it has the shape it has*; each
> column carries a description stating what it holds, its units, and any non-obvious constraint.
> Jargon is defined where first used or cited to a companion doc: **blocking** and **bi-temporal**
> in `concepts.md` §6/§5, **supersession** in `concepts.md` §4. The few statistical/tool terms
> this doc uses (**Wilson interval**, **Splink waterfall**, **novelty gate**) are glossed at first
> use below.

> **This is the full-scope schema (CLAUDE.md Rule 2).** It is sized and shaped for the millions-
> of-documents target, not an MVP. Numbers (partition cadence, retry budgets, golden-set sizes,
> threshold defaults) are **starting points to measure**, labelled as such — never committed
> constants. The one deliberately-excluded mechanism is the graph incremental-sync outbox
> (`graph_events`), a documented *non-goal* under D7 (rebuild-first); its absence is a scope
> boundary, not a deferral. §15 records it as a non-goal.

---

## 0. Conventions

These rules apply to every table unless a module overrides them with a stated reason.

- **Migrations & DDL ordering.** Structural schema shape is owned by **Alembic**
  (requirements_v3 §Code). The DDL in this document is the source; the migration emits each inline
  `--` column description as a
  `COMMENT ON COLUMN` and each `COMMENT ON TABLE` shown here. **All `CREATE TYPE` enum statements
  (§1) are created first, before any table** — they are presented in §1 (immediately below) for
  exactly this reason. Extensions (below) are created before that. Alembic creates no D68
  deployment identity row and no deployment-scoped registry data: after `upgrade head`, D69's
  library-owned typed bootstrap operation creates or verifies those rows (§2).
- **Identifiers.** All surrogate primary keys are **`uuid`**, generated as **UUIDv7**
  (time-ordered) by the application/worker, *not* the database — Cloud Run workers write
  concurrently and a central sequence would be a hotspot, while UUIDv7's leading timestamp gives
  index/heap locality on append-only tables and lets a reader derive the row's creation month from
  the id alone (used for partition pruning, §12). UUIDv7 also satisfies "`entity_id` is **never
  reused**" (D17/§4) for free. IDs are worker-minted so the row's identity is known before the
  INSERT (needed for idempotency and for deterministic-id dedup, §9).
- **Time vocabulary.** Every timestamp is **`timestamptz`** (UTC). Distinct *kinds* of time recur
  and must not be conflated (`concepts.md` §5):
  - **valid-time** — when a fact held *in the world*. On **relations** this is the revisable,
    adjudicated window `valid_from`/`valid_until`. On **claims** it is the **immutable,
    source-asserted** interval `claim_valid_from`/`claim_valid_until` (+ `claim_valid_precision`,
    `claim_valid_kind`) — *the window the source attributed to the proposition* (D41). Claim
    valid-time is *evidence* (immutable, many-valued per fact); relation valid-time is *current
    belief* (revisable, one per fact). They never write to each other.
  - **assertion-time** — `asserted_at` (claims only): when the **source** asserted the claim (≈
    the version's `source_modified_at`/`published_at`, D55). This is the assertion *event*, **not** the fact's world-time — "in
    2024 a report said FY2023 revenue was \$5M" has `asserted_at`=2024 but `claim_valid_*`=FY2023.
  - **transaction-time** — when the *system* learned/un-learned it: `ingested_at` (claims, relations)
    and `invalidated_at` (relations only). Never revised on claims; revisable on relations
    (supersession, D3).
  The relation pair (valid + transaction) = "bi-temporal". A relation answers "true in the world at
  T?" and "believed by us at T?"; a claim answers "asserted when / ingested when / asserted to hold
  over what world-interval?" — all three immutable.
- **Tenancy (`deployment_id`) and cross-deployment isolation (D68).** The system runs as **N
  independent deployments**, one per problem domain (personal assistant, agency, a manufacturer's
  migration, a legal engine), and each deployment has its own Postgres instance or isolated schema.
  There is no shared operational database that routes rows for several deployments; entity spaces
  are **never shared across deployments** (`registries_design.md` §1, D16, D50). Every
  deployment-scoped table still carries `deployment_id`, which is constant inside that
  database/schema. The column is retained as a stable deployment identity and structural defense in
  depth, not as a cross-deployment routing key. Every deployment-scoped parent table therefore
  carries a `UNIQUE (deployment_id, <pk>)` key, and every deployment-scoped foreign key is
  **composite** — `FOREIGN KEY (deployment_id, x_id) REFERENCES parent (deployment_id, x_id)` — so
  an accidental mismatched identifier remains unrepresentable even though UUIDs are globally
  unique. **The one documented exception is a
  *self-referential* FK** — a row pointing at another row of the *same* table (a section's parent
  section; a merge or adjudication supersession chain: `merge_events.reversed_by`,
  `relation_adjudications.superseded_by`). Both rows are by construction in the same deployment (same
  document, same registry), so these remain single-column for brevity; they cannot cross deployments
  because the worker only ever links rows it created within one deployment.
- **Foreign keys at scale (D23/D94).** Eight large append-only E-plane tables are partitioned and use
  **logical foreign keys** — referential integrity is enforced by idempotent workers and verified
  by a periodic **auditor query**, not by a DB-level `FOREIGN KEY`. Six use monthly RANGE
  partitions (`mentions`, `resolution_decisions`, `chunks`, `chunk_claims`,
  `claim_extraction_decisions`, `testimony_currency_events`); two use static HASH partitions
  (`relation_evidence`, `observation_evidence`). Reasons: (1) Postgres requires a FK to a
  partitioned table to reference a unique constraint that includes the partition key, which would
  force that key into every child and join; (2) D23's "btree-only, cap write-amplification" mandate
  on these hot tables. **Non-partitioned tables use real composite FK constraints
  when the referenced parent exposes a compatible physical key.** The D94
  exception is `claims`: it is non-partitioned for global BM25/HNSW but keeps
  logical provenance FKs because its required `chunks` parent remains
  partitioned by `(chunk_id, created_at)`. A logical-only FK is tagged
  `-- LOGICAL FK → table(col)`. The auditor checks for orphans and any worker-owned logical
  uniqueness not enforced by a primary key; both evidence joins enforce evidence-once directly
  through their partition-compatible primary keys (§§9, 9.A).
- **Versioning (D1, D12).** Every non-deterministic output records the version of the component
  that produced it (`*_version` columns), resolving to a row in `pipeline_component_versions` (§2)
  that pins model + prompt hash + params. This is what makes "rebuild = replay stored state, never
  re-call the model" (D7/D33) auditable and what scopes a re-processing batch.
- **Enums** (§1) are small, stable, system-owned vocabularies; `ALTER TYPE ... ADD VALUE` extends
  them. The user-governed predicate vocabulary is a **registry table** (§3), not
  an enum (D5), so deployments may insert predicates with descriptions and parent links.
  `entity_types` is retained as dormant inventory and is not an identity vocabulary (D96).
- **JSONB** is used only for open-ended, not-queried-by-key payloads (resolver feature vectors,
  tier configs, validation reports, DLQ payloads). Anything filtered or joined is a column.
- **Naming.** snake_case; tables plural; `_id` = identity reference; `_ref` = opaque key into
  another store; `_uri` = GCS object URI; `_at` = timestamptz; `_version` =
  component version string.
- **Exact source documents stay outside authority tables (D37/D94).** The
  authority schema stores compact, query-critical metadata and the section
  index; exact bodies/Markdown remain in GCS. The one private `chunk_search`
  sidecar stores the normalized current chunk body and embedding required by
  pgvector/pg_textsearch. Claims, facts, and entities index their existing text
  rows and carry one current derived embedding in place. The authority schema
  stores **offsets + URIs**, plus the small generated artifacts that must be *replayed* on rebuild
  (context prefixes, claim text, decision ledgers) — derived metadata, not source bodies.

Required Postgres extensions (created before all types and tables):

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;      -- gen_random_uuid fallback; digests
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- T1 fuzzy blocking: trigram GIN on names (D17)
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch; -- T2 phonetic: daitch_mokotoff() (D17, NOT soundex)
CREATE EXTENSION IF NOT EXISTS unaccent;      -- accent-fold names before trigram/phonetic (registries §5)
CREATE EXTENSION IF NOT EXISTS btree_gist;    -- composite GiST: relations bi-temporal EXCLUDE constraint (§9)
CREATE EXTENSION IF NOT EXISTS pg_partman;    -- monthly RANGE partition automation (D23, §12)
CREATE EXTENSION IF NOT EXISTS vector;        -- D94 PostgreSQL-native semantic vectors/HNSW
CREATE EXTENSION IF NOT EXISTS pg_textsearch; -- D94 chunk/claim BM25; also required in shared_preload_libraries
```

PostgreSQL **18**, kept on the current patched 18.x minor, is the binding
baseline. The selected pg_textsearch release must support PostgreSQL 18.
`vectorscale` is absent from the baseline. Pgvectorscale remains an unchosen
open proposal; promoting it would require a later binding decision. Composite-FK
column-list `ON DELETE SET NULL` requires 15+; this is
relied on for `document_crossrefs`, §6).

---

## 1. Enum types (created first)

System-owned, stable vocabularies, created before any table. Extended with
`ALTER TYPE ... ADD VALUE`. (The user-governed predicate vocabulary is a registry
*table*, §3, not an enum. The dormant `entity_types` table is not consulted by identity.)

```sql
CREATE TYPE deployment_status      AS ENUM ('active','suspended','archived');

-- Every non-deterministic producer that stamps a *_version resolving to pipeline_component_versions
-- has a value here (so a version string can always resolve to a catalog row, D1/D12):
CREATE TYPE pipeline_component     AS ENUM (
  'ingester','converter','blockizer','structurer','crossreferencer','chunker','context_prefixer',  -- context_prefixer: legacy LLM location; D80 uses embedding_input_policy + embedder generations
  'embedding_input_policy',
  'extractor','grounder','resolver','normalizer','adjudicator','embedder','fact_labeler',
  'profile_summarizer','community_detector','snapshot_builder','knowledge_planner',
  'knowledge_writer','knowledge_reflector','knowledge_linter','judge','forgetter');
CREATE TYPE processing_target      AS ENUM ('document','document_section','chunk','claim','relation','observation','entity','snapshot','knowledge_artifact','document_version','knowledge_dispatch');
CREATE TYPE pipeline_stage         AS ENUM ('ingest','convert','structure','crossref','chunk','embed_chunk','extract_claims','embed_claim','ground_claims','resolve_entities','normalize_relations','adjudicate_supersession','adjudicate_observations','embed_relation','label_relation','embed_observation','label_observation','refresh_profile','build_snapshot','detect_communities','compile_knowledge','reflect_knowledge','lint_knowledge','reconcile','dispatch_knowledge','hard_forget');
CREATE TYPE processing_status      AS ENUM ('pending','running','succeeded','failed','dead_letter','skipped');
-- D67: only plane-E routes use operational lanes. K/P (and other scheduled aggregate) jobs
-- represent their single unlaned route with SQL NULL, never a synthetic third enum value.
CREATE TYPE processing_lane        AS ENUM ('steady','backfill');
CREATE TYPE processing_defer_reason AS ENUM ('scheduled','retry_backoff','budget');
CREATE TYPE forget_manifest_status AS ENUM ('preparing','accepted','complete');

CREATE TYPE ontology_tier          AS ENUM ('core','extension','other','deprecated');
CREATE TYPE ontology_status        AS ENUM ('active','deprecated');
CREATE TYPE scope_interest_kind    AS ENUM ('entity_type','predicate','metadata','keyword');

CREATE TYPE entity_status          AS ENUM ('active','merged','retired');
CREATE TYPE alias_provenance       AS ENUM ('source','llm_canonical');
CREATE TYPE resolution_tier        AS ENUM ('T0','T1','T2','T3','T4_small','T4_frontier','human');
CREATE TYPE decision_actor         AS ENUM ('auto','human');

CREATE TYPE review_item_kind       AS ENUM ('merge_cluster','split_cluster','type_conflict','generic_identifier','contradiction','support_withdrawn');
CREATE TYPE review_status          AS ENUM ('pending','accepted','rejected','deferred','auto_resolved');
-- Covers all review_item_kinds (D24), not just merges: pick_a/pick_b/both_stand for contradictions,
-- downweight/keep_signal for generic_identifier, retype for type_conflict.
CREATE TYPE review_verdict         AS ENUM ('merge','not_merge','split','retype','downweight','keep_signal','pick_a','pick_b','both_stand','uncertain','restore_support','invalidate_fact');
-- restore_support / invalidate_fact are the two terminal verdicts of the support_withdrawn kind
-- (D54): restore = old claim regains currency ('review_restored' event) + the case is planted as
-- a D35 canary; invalidate = the fact's invalidated_at is set with a recorded adjudication.
-- 'uncertain' leaves the fact standing with its support:withdrawn marker (visibly unresolved).
CREATE TYPE golden_label           AS ENUM ('match','no_match');
CREATE TYPE golden_hardness        AS ENUM ('hard_positive','hard_negative','easy');
CREATE TYPE eval_suite             AS ENUM ('resolution','selection','grounding','retrieval','contradiction','lifecycle','operational');
CREATE TYPE selection_outcome      AS ENUM ('keep','rewrite','drop','kept_flagged');

CREATE TYPE document_status        AS ENUM ('ingesting','converting','structuring','ready','failed','deleted');
CREATE TYPE document_origin        AS ENUM ('external','system_generated');  -- D42, stamped at E0 ingest per lineage
-- D55 lineage semantics: snapshot = every version is independent dated testimony forever;
-- living = the current version is the source's standing statement (currency follows it, D54):
CREATE TYPE versioning_mode        AS ENUM ('snapshot','living');
-- D54 testimony-currency transitions (append-only ledger; bookkeeping, never validity):
CREATE TYPE currency_reason        AS ENUM ('reextracted','version_superseded','version_deleted','review_restored');
-- 'review_restored' = the support_withdrawn triage's verdict A (became_current=true): a reviewer
-- judged the old claim correct and the new extractor regressed — the old claim stands as the
-- chunk's current transcription until a fixed extractor re-derives it (lifecycle §4).
CREATE TYPE section_role           AS ENUM ('body','abstract','introduction','results','methods','discussion','conclusion','references','appendix','table','figure_caption','nav','boilerplate','legal');
CREATE TYPE crossref_kind          AS ENUM ('cites','links_to','attaches','replies_to');

CREATE TYPE claim_temporal_class   AS ENUM ('static','dynamic','atemporal');
-- D41 source-asserted validity on claims (immutable; never a relation-style revisable window):
CREATE TYPE claim_valid_precision  AS ENUM ('unknown','instant','day','month','quarter','year','open');
CREATE TYPE claim_valid_kind       AS ENUM ('proposition_validity','event_time','measurement_period','effective_period');
CREATE TYPE grounding_audit_status AS ENUM ('unaudited','sampled_pass','sampled_fail','escalated');
-- The ledger records DROPs, low-confidence keeps (flags), and decontextualization EDITs.
-- Plain keeps are NOT persisted (they ARE the claims row); see §8.
CREATE TYPE extraction_decision_type AS ENUM ('selection_drop','selection_keep_flagged','decontext_edit');
CREATE TYPE selection_drop_reason  AS ENUM ('opinion','advice','hypothetical','generic','question','intro','conclusion','no_info','ambiguous','references_boilerplate');

CREATE TYPE evidence_stance        AS ENUM ('supports','contradicts');
CREATE TYPE relation_status        AS ENUM ('active','invalidated');  -- generated mirror of invalidated_at; retirement (zero-evidence GC, §13) = setting invalidated_at
CREATE TYPE adjudication_outcome   AS ENUM ('add','noop','supersede','contradict','same_as_merge_proposal','retracted_source_removal');
-- 'retracted_source_removal' = D54/D55 source-acted closure: a living lineage's removal, OR
-- any deletion (version / lineage / source-observed), withdrew a fact's sole current support →
-- adjudicated closed per shape (states: valid_until cap; measurements: invalidated_at — the
-- D43 no-cap rule); the adjudication row is the audit record. (support_withdrawn is
-- exclusively the RE-EXTRACTION zero-support flag, D54 — never removal, never deletion.)
CREATE TYPE adjudication_method    AS ENUM ('novelty_gate','exact','fuzzy','embedding','small_model','frontier_llm');

CREATE TYPE projection_plane       AS ENUM ('P1_search','P2_graph','P3_corpusfs');
CREATE TYPE snapshot_status        AS ENUM ('building','validating','published','superseded','failed');
CREATE TYPE community_algorithm    AS ENUM ('leiden','louvain');  -- external detection pass (D11)

CREATE TYPE knowledge_layer        AS ENUM ('K1','K2','K3');  -- K1/K2 are shipped scope labels; legacy K3 is inert compatibility only (D73)
CREATE TYPE knowledge_page_kind    AS ENUM ('compiled','authored');  -- D46: machine-owned body vs human/agent-owned body
-- 'quarantined' = a compiled body was human-edited directly; excluded from recompile until the
-- diff is triaged — into the curation sidecar, or by adopting the page as authored
-- (plan_action 'convert_kind'; D46 quarantine rule):
CREATE TYPE knowledge_artifact_status AS ENUM ('active','stale','quarantined','tombstoned');
CREATE TYPE knowledge_evidence_role AS ENUM ('supports','contradicts','cites');
-- D45 routing rules — the closed, mechanically-evaluable kind set (k_layers_design.md §5):
CREATE TYPE knowledge_rule_kind    AS ENUM ('entity','entity_subtree','predicate_beat','community','doc_set','scope_interests','manual');
CREATE TYPE rule_key_kind          AS ENUM ('entity','predicate','community','doc_source');
-- convert_kind = D46 adoption (compiled→authored, via quarantine triage) / handover
-- (authored→compiled — the one action that NEVER auto-applies; requires author confirmation):
CREATE TYPE plan_action            AS ENUM ('create_page','split_page','merge_pages','move_page','retire_page','adjust_rule','convert_kind');
CREATE TYPE plan_trigger           AS ENUM ('orphan_evidence','size_overflow','community_change','reflection','writer_suggestion','human');
CREATE TYPE plan_decision_status   AS ENUM ('proposed','applied','rejected');
CREATE TYPE subscription_status    AS ENUM ('active','paused','retired');  -- dispatch consumers (k_layers §5)
-- 'authored_review' = cited/watched evidence changed under an AUTHORED page → review flag for
-- the page's author (human or agent), never an auto-recompile (D46):
CREATE TYPE knowledge_trigger      AS ENUM ('evidence_changed','community_changed','debounce_timer','manual','tombstone','authored_review');
CREATE TYPE refresh_status         AS ENUM ('pending','running','done','failed');

-- D50/D87 closed assured-operation registry (§11.A):
CREATE TYPE assured_operation_name AS ENUM
  ('resolve_entity','testimony_context','fact_context','answer_context');
CREATE TYPE assured_result_contract AS ENUM ('envelope','context_bundle_v1');
CREATE TYPE assured_output_grain AS ENUM ('fact','evidence');
CREATE TYPE assured_answer_intent AS ENUM
  ('identity','testimony','facts','combined_context');
```

`novelty_gate` (in `adjudication_method`) is the deterministic short-circuit at the front of the
supersession cascade: if a new claim's `(subject, predicate, object)` fact already exists with a
compatible window and adds nothing new, adjudication is skipped entirely (no LLM call) — the
cheap-first rung of D4. The other methods are self-evident (exact / fuzzy / embedding / small
model / frontier LLM).

---

## 2. Tenancy & pipeline infrastructure

The substrate every other module sits on: the deployment registry, the component-version catalog
that gives every `*_version` string meaning, the idempotency/dead-letter ledger that makes workers
re-runnable (D12), D67's normalized queue route/due/retry state, and the cost ledger that meters
LLM/embedding spend per stage and lane (`overall_design.md` §8).

```sql
-- ─────────────────────────────────────────────────────────────────────────
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

-- D94 current-only P1 control state. This is not a search-row mirror or a
-- generation history: exactly one row describes each target/channel now served.
CREATE TABLE p1_search_channels (
  deployment_id uuid NOT NULL REFERENCES deployments ON DELETE CASCADE,
  target text NOT NULL CHECK (target IN ('chunks','claims','relations','observations','entities')),
  channel text NOT NULL CHECK (channel IN ('semantic','bm25')),
  embedding_model text,
  embedding_dimension integer,
  embedding_input_policy_version text,
  text_config text,
  ready boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, target, channel),
  CHECK (
    (channel = 'semantic'
      AND embedding_model IS NOT NULL
      AND embedding_dimension = 1536
      AND embedding_input_policy_version IS NOT NULL
      AND text_config IS NULL)
    OR
    (channel = 'bm25'
      AND target IN ('chunks','claims')
      AND embedding_model IS NULL
      AND embedding_dimension IS NULL
      AND embedding_input_policy_version IS NULL
      AND text_config = 'simple')
  )
);
COMMENT ON TABLE p1_search_channels IS
  'D94 current target/channel configuration and readiness. One row only; no historical generations. Incompatible maintenance sets ready=false, rebuilds/verifies derived state, then overwrites this row with the new current configuration and ready=true.';

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
-- forget_manifests — D74's local materialization of one portable, content-free hard-forget
-- intent. The external ForgetManifestPort remains the restore-surviving intent source; this row
-- supplies admission, exact worker input, irreversible ingest guards, and verification progress.
-- Attempts/retry/DLQ remain in processing_state, never here.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE forget_manifests (
  forget_id            uuid PRIMARY KEY,
  deployment_id        uuid NOT NULL REFERENCES deployments,
  doc_id                uuid NOT NULL,               -- hard-forget v1 is lineage-scoped
  schema_version        smallint NOT NULL,
  manifest_hash         text,                        -- set with manifest before portable append
  manifest              jsonb,                       -- stable IDs/hashes/keys only; no source prose
  source_identity_hash  text,                        -- irreversible stable-source ingest guard
  content_hashes        text[] NOT NULL DEFAULT '{}',-- irreversible raw-content ingest guards
  status                forget_manifest_status NOT NULL DEFAULT 'preparing',
  prepared_at           timestamptz NOT NULL DEFAULT now(),
  accepted_at           timestamptz,
  completed_at          timestamptz,
  last_verified_at      timestamptz,                 -- latest all-store readiness re-honor
  UNIQUE (deployment_id, doc_id),
  UNIQUE (deployment_id, forget_id),
  CHECK ((status = 'preparing') OR
         (manifest_hash IS NOT NULL AND manifest IS NOT NULL AND accepted_at IS NOT NULL)),
  CHECK ((status = 'complete') = (completed_at IS NOT NULL))
);
COMMENT ON TABLE forget_manifests IS
  'D74 hard-forget manifest materialization: preparing is the temporary admission barrier; accepted means the portable append is durable; complete means one purge succeeded, never that readiness may skip re-honoring external stores.';
CREATE INDEX ix_forget_source_guard
  ON forget_manifests (deployment_id, source_identity_hash)
  WHERE source_identity_hash IS NOT NULL;
CREATE INDEX ix_forget_content_guard ON forget_manifests USING gin (content_hashes);

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
  target_kind     processing_target NOT NULL,  -- hard_forget reuses document; no deletion-only target kind
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

-- A self-host worker has one configured route. Use `lane = $3` for steady/backfill and the
-- equivalent `lane IS NULL` form for an unlaned K/P route; both use ix_procstate_due.
-- The claim transaction locks the selected row and runs the budget pre-flight before finalizing a
-- transition. Exhaustion leaves it pending with defer_reason='budget' and not_before at the window
-- roll. Otherwise the transaction clears defer_reason, changes status to running, and increments
-- attempts exactly once immediately before the handler begins.
SELECT processing_id
FROM processing_state
WHERE deployment_id = $1
  AND stage = $2
  AND lane = $3
  AND status IN ('pending','failed')
  AND not_before <= now()
  AND attempts < max_attempts
ORDER BY not_before, enqueued_at, processing_id
LIMIT $4
FOR UPDATE SKIP LOCKED;

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
```

### Post-head deployment bootstrap (D69)

The cold-start order is exact:

```text
fresh database -> Alembic upgrade head -> bootstrap_deployment(typed profile inputs)
               -> one deployments row -> 8 core roots -> 16 predicates -> 116 signatures -> commit
```

`bootstrap_deployment(DeploymentBootstrapInput) -> DeploymentBootstrapResult` is a library
operation owned by WP-0.3. It runs only after structural head exists and uses one database
transaction. The deployment row is the FK precondition for every registry row; all eight type rows
are the FK precondition for the concrete signatures; all sixteen predicate rows are their other FK
precondition. A failure at any step rolls back the deployment and every core row inserted by that
attempt.

`DeploymentBootstrapInput` maps profile-provided values to columns without hidden inputs:

| Typed input field | `deployments` column | Rule |
|---|---|---|
| `deployment_id` | `deployment_id` | Required D68 stable identity and bootstrap idempotency key |
| `slug` | `slug` | Required, compared exactly on retry |
| `name` | `name` | Required, compared exactly on retry |
| `description` | `description` | Optional; `NULL` is a complete value and is compared exactly |
| `default_language` | `default_language` | Required typed profile value; no environment-only fallback |
| `raw_bucket` | `raw_bucket` | Required profile-owned URI/value |
| `artifacts_bucket` | `artifacts_bucket` | Required profile-owned URI/value |
| `corpusfs_bucket` | `corpusfs_bucket` | Required profile-owned URI/value |
| `knowledge_repo_uri` | `knowledge_repo_uri` | Optional; `NULL` is a complete value and is compared exactly |

`status`, `created_at`, and `updated_at` are omitted so the documented database defaults own them.
No magic UUID, empty bucket sentinel, nullable tenancy, global template row, trigger, migration
argument, or environment-only input participates.

`DeploymentBootstrapResult` is also fixed: it returns `deployment_id`, `deployment_created`
(whether this transaction inserted the deployment row), `entity_types_count = 8`,
`predicates_count = 16`, and `predicate_signatures_count = 0`. Counts describe the verified core
after the operation, so a successful identical retry returns the same counts with
`deployment_created = false`; it never fabricates a success over partial state.

The operation uses compare-or-insert behavior, never a value-overwriting upsert:

| Row kind | Idempotency key | Identical retry | Conflict |
|---|---|---|---|
| deployment | `deployment_id` | Verify every mapped profile column; return existing row | Any mapped value differs, or `slug` belongs to another ID: typed deployment conflict |
| dormant entity-type seed | `(deployment_id, type)` | Verify every manifest field | Missing/extra core key or any field differs: typed manifest conflict; the row is still not identity authority |
| predicate | `(deployment_id, predicate)` | Verify every definition field; verify `usage_count >= 0` and preserve it | Missing/extra core key, invalid counter, or any definition field differs: typed manifest conflict |

On an empty deployment the operation inserts in the displayed order. On retry it verifies the
complete deployment/core state and performs no mutation. Detection of an extra row is limited to an
extra row claiming `tier='core'` for this deployment; extension rows and pack activation are separate
and do not conflict with the universal manifest. The dormant entity-type seed and predicate
values originate in `registries_design.md` §4; its former signatures are not installed or enforced.
`usage_count = 0` is the exact insert value, but
the counter is runtime-maintained thereafter; retry never resets it.

---

## 3. Predicate registry and dormant entity-type inventory (D5, D96)

The **governed predicate vocabulary** names relation edges and retains the D5
`other:` escape. It does not constrain endpoint classes: `works_for(Alice, Me)`
is legal regardless of how either referent would once have been classified.
The `entity_types` table remains in the physical schema only as dormant seed
inventory for existing pack/bootstrap structures. No extraction prompt,
resolver, relation writer, projection, or query reads it as authority. There
is no `predicate_signatures` table and no domain/range gate (D96).

```sql
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
-- entity_types — dormant non-authoritative seed inventory retained after D96.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE entity_types (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  type            text NOT NULL,               -- dormant seed key; never copied onto entities or mentions
  parent_type     text,                        -- retained seed hierarchy; not consulted by resolution or relation writes
  description     text NOT NULL,               -- inventory description; not rendered into extraction prompts
  examples        text[] NOT NULL DEFAULT '{}',-- dormant inventory examples
  schema_org_ref  text,                        -- dormant inventory metadata
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
  'Dormant entity-type seed inventory retained after D96 for bootstrap and pack structure only. No entity, mention, resolver, relation writer, projection, or query uses these rows as identity or as a domain/range gate.';
CREATE INDEX ix_entity_types_parent ON entity_types (deployment_id, parent_type);

-- ─────────────────────────────────────────────────────────────────────────
-- predicates — the governed relation vocabulary (D5). related_to is the core parent.
-- The other:<freetext> escape (D5) is materialized here too: when the normalizer encounters an
-- other:<value>, it UPSERTs a row with tier='other' (so relations.predicate's FK holds AND the
-- promotion funnel is countable via usage_count). No predicate has an endpoint type signature.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE predicates (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  predicate       text NOT NULL,               -- 'works_for',... ; 'other:<freetext>' rows live here as tier='other'
  parent_predicate text,                        -- optional predicate-side extend-never-fork anchor (default parent 'related_to')
  description     text NOT NULL,               -- meaning rendered into normalization prompts
  examples        text[] NOT NULL DEFAULT '{}',
  synonyms        text[] NOT NULL DEFAULT '{}',-- surface variants the normalizer maps onto this predicate (works_at/employed_by → works_for) — D5
  schema_org_ref  text,
  tier            ontology_tier NOT NULL,      -- core | extension | other | deprecated
  pack_id         text REFERENCES extension_packs,
  scope_id        uuid,
  usage_count     bigint NOT NULL DEFAULT 0,   -- cached count of relations using this predicate; ranks tier='other' values for promotion (D5 funnel)
  is_change_prone boolean NOT NULL DEFAULT false, -- employment/affiliation/location change over time ⇒ supersession-relevant
  exclude_from_graph_distance boolean NOT NULL DEFAULT false, -- causal/promiscuous predicates excluded from graph-distance reranking (registries §4)
  status          ontology_status NOT NULL DEFAULT 'active',
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, predicate),
  FOREIGN KEY (deployment_id, parent_predicate) REFERENCES predicates (deployment_id, predicate),
  FOREIGN KEY (deployment_id, scope_id) REFERENCES scopes (deployment_id, scope_id) ON DELETE SET NULL (scope_id)
);
COMMENT ON TABLE predicates IS
  'Governed predicate vocabulary (D5). Extraction is constrained to these names with an other:<freetext> escape, which the normalizer upserts as tier=other rows (FK holds; usage_count makes the promotion funnel queryable). Endpoint entity classes are not constrained.';
CREATE INDEX ix_predicates_other ON predicates (deployment_id, usage_count DESC) WHERE tier = 'other'; -- promotion-candidate ranking

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
  'Per-scope interest list (D16): predicate, metadata, and keyword interests define the scope''s PROJECT_GRAPH_CYPHER view and K2 compilation selection. The legacy entity_type enum value is rejected after D96; scopes do not filter entities by class.';
```

The deployment bootstrap creates or verifies eight dormant entity-type seed
rows and sixteen predicate rows after Alembic reaches head. It creates zero
signature rows. Only predicates participate in extraction and relation writes.

---

## 4. Entity registry & resolution (D17–D24)

The identity authority. The **transcript/verdict** epistemics of D2/D3 apply (`entity_registry.md`
§4): **mentions are evidence** (immutable), **entities are verdicts** (re-adjudicable),
**resolution is replayable**. The governing asymmetry is **under-merging degrades gradually;
over-merging poisons catastrophically**, so everything is recall-conservative and reversible.
"Blocking" (cheaply narrowing the candidate-match set so you avoid an all-pairs comparison) is
defined in `concepts.md` §6.

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- entities — the canonical registry. entity_id is NEVER reused; a merge is a redirect
-- (merged_into), never a rewrite (Wikidata model). UNIQUE(deployment_id, entity_id) is the
-- composite-FK target that keeps every entity reference inside one deployment (§0).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE entities (
  entity_id       uuid PRIMARY KEY,            -- canonical identity; never reused (D17); flows downstream to P1/Ladybug
  deployment_id   uuid NOT NULL REFERENCES deployments,
  canonical_name  text NOT NULL,               -- preferred display/blocking name; mirrored as an alias row (invariant below)
  normalized_name text NOT NULL,               -- unaccent+lower(canonical_name)
  status          entity_status NOT NULL DEFAULT 'active', -- active | merged | retired
  merged_into     uuid,                        -- redirect target when status=merged; follow the chain to the survivor (D21)
  profile_summary text,                        -- short registry-maintained blurb; improves future LLM adjudication (Graphiti lesson)
  embedding vector(1536),                      -- D94 disposable vector for the canonical profile input
  embedding_model text,                        -- configured model that produced embedding
  embedding_input_policy_version text,         -- exact profile-input rendering policy
  embedding_text_hash text,                    -- hash of the exact profile input
  mention_count   integer NOT NULL DEFAULT 0,  -- cached |mentions|; half of blast_radius (registries §6) and a health metric
  graph_degree    integer NOT NULL DEFAULT 0,  -- cached relation degree from the LATEST PUBLISHED P2 snapshot (§9); other half of blast_radius
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, entity_id),           -- composite-FK target (tenancy isolation, §0)
  FOREIGN KEY (deployment_id, merged_into) REFERENCES entities (deployment_id, entity_id), -- same-deployment redirect only
  CHECK ((status = 'merged') = (merged_into IS NOT NULL)), -- merged iff it redirects; an active/retired entity must NOT redirect
  CHECK (num_nonnulls(embedding, embedding_model, embedding_input_policy_version, embedding_text_hash) IN (0, 4))
);
COMMENT ON TABLE entities IS
  'Canonical entity registry (D17/D21/D95/D96). entity_id identifies the real-world referent and is never reused; merges are redirects via merged_into, not rewrites. profile_summary caches observation prose for resolution evidence, while mention_count and graph_degree cache blast-radius inputs.';
CREATE INDEX ix_entities_redirect ON entities (merged_into) WHERE merged_into IS NOT NULL;
-- entities is searchable by name but the PRIMARY blocking index lives on aliases (below). D68
-- gives each deployment its own instance/schema, so the blocking GIN contains only the match key:
CREATE INDEX ix_entities_name_trgm ON entities USING gin (normalized_name gin_trgm_ops);
CREATE INDEX ix_entities_embedding_hnsw ON entities
  USING hnsw (embedding vector_cosine_ops);

-- ─────────────────────────────────────────────────────────────────────────
-- aliases — surface forms per entity, the BLOCKING TARGET (D23). Includes the LLM-emitted
-- canonical form (provenance=llm_canonical) on which T0 exact-match runs. INVARIANT: each entity's
-- canonical_name exists as an alias row, so the cascade scans aliases only. Deliberately NOT
-- partitioned (≤10⁷, D23) so its GIN trigram/phonetic indexes can live on it.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE aliases (
  alias_id        uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  entity_id       uuid NOT NULL,               -- composite FK below (same-deployment)
  alias_text      text NOT NULL,               -- surface form as seen / as canonicalized
  normalized_lemma text NOT NULL,              -- unaccent+lower (and LLM nominative form for inflected langs, registries §5); the indexed match key
  provenance      alias_provenance NOT NULL,   -- source (observed in a document) | llm_canonical (extractor-emitted nominative form)
  confidence      real,                        -- confidence this surface really names this entity
  first_seen      timestamptz NOT NULL DEFAULT now(),
  last_seen       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, entity_id, normalized_lemma, provenance),
  FOREIGN KEY (deployment_id, entity_id) REFERENCES entities (deployment_id, entity_id) ON DELETE CASCADE
);
COMMENT ON TABLE aliases IS
  'Surface forms per entity and the blocking target for resolution (D17/D23). T0 exact-matches the llm_canonical lemma; T1 trigram-blocks and T2 phonetic-blocks on normalized_lemma. Not partitioned so its GIN indexes are usable.';
-- The two alias blocking indexes (D17/D23). D68 gives each deployment its own instance/schema, so
-- deployment_id is constant and the GIN keys contain only the values used for trigram/phonetic
-- matching. The btree exact-match index below keeps deployment_id as structural defense in depth.
CREATE INDEX ix_aliases_lemma_trgm  ON aliases USING gin (normalized_lemma gin_trgm_ops);
CREATE INDEX ix_aliases_lemma_dm    ON aliases USING gin (daitch_mokotoff(normalized_lemma));
CREATE INDEX ix_aliases_lemma_exact ON aliases (deployment_id, normalized_lemma);  -- T0 exact match
CREATE INDEX ix_aliases_entity      ON aliases (entity_id);

-- ─────────────────────────────────────────────────────────────────────────
-- generic_identifier_guard — the Senzing "promiscuous signal" guard (D21/registries §6).
-- Keyed by the normalized string (not a single alias row): the property "links to MANY distinct
-- entities ⇒ generic not identifying" is about the string across the registry.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE generic_identifier_guard (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  normalized_lemma text NOT NULL,              -- the suspect surface string
  distinct_entity_count integer NOT NULL,      -- how many distinct entities it currently links — the tell
  is_downweighted boolean NOT NULL DEFAULT true, -- stop trusting it as a blocking/match signal
  reason          text,                        -- 'role-address' | 'placeholder' | 'common-name' | ...
  evaluated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, normalized_lemma)
);
COMMENT ON TABLE generic_identifier_guard IS
  'Surfaces that link too many entities to be identifying (D21). Down-weighted so they stop driving merges; the merges they already caused are re-evaluated — enumerated via merge_events.trigger_lemmas (below).';

-- ─────────────────────────────────────────────────────────────────────────
-- resolution_exclusions — negative/"these are NOT the same" edges (D21).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE resolution_exclusions (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  entity_id_low   uuid NOT NULL,               -- least(a,b) — canonical ordering keeps the pair unique
  entity_id_high  uuid NOT NULL,               -- greatest(a,b)
  reason          text,                        -- why they are known-distinct (evidence / reviewer note)
  created_by      decision_actor NOT NULL,     -- auto | human
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, entity_id_low, entity_id_high),
  CHECK (entity_id_low < entity_id_high),
  FOREIGN KEY (deployment_id, entity_id_low)  REFERENCES entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, entity_id_high) REFERENCES entities (deployment_id, entity_id)
);
COMMENT ON TABLE resolution_exclusions IS
  'Adjudicated non-match constraints (D21): block re-proposing a merge the clusterer or a human ruled out (two J. Smiths, father/son). Consulted by the cascade and clustering.';

-- ─────────────────────────────────────────────────────────────────────────
-- resolver_versions — per-version tier config + one global threshold set (D17/D22/D96).
-- Also the home of the review-routing band boundaries (auto-accept ceiling / hub-merge floor, D24).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE resolver_versions (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  resolver_version text NOT NULL,              -- e.g. 'resolver-2026-03a'
  tier_config     jsonb NOT NULL,              -- T0–T4 enable/order, blocking floors, escalation bands, review band boundaries + hub-merge blast-radius cutoff (D24)
  thresholds jsonb NOT NULL,                   -- one global accept/reject band set, golden-set measured; starting points, not constants
  configured_at   timestamptz NOT NULL DEFAULT now(),
  notes           text,
  PRIMARY KEY (deployment_id, resolver_version)
);
COMMENT ON TABLE resolver_versions IS
  'Versioned global resolution thresholds plus tier config and review-routing bands (D17/D22/D24/D96). Block-loose and decide-tight; thresholds are golden-set-measured starting points to be re-measured, never committed constants.';

-- ─────────────────────────────────────────────────────────────────────────
-- mentions — the immutable transcript: every entity mention as extracted (D17). ~10⁸ rows ⇒
-- monthly RANGE partition by created_at; btree-only; logical FKs (D23). Queried by id/claim_id/
-- doc_id, never fuzzy-scanned (the fuzzy index lives on aliases). Partition pruning for id lookups:
-- §12.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE mentions (
  mention_id      uuid NOT NULL,               -- PK component (with created_at)
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  surface_form    text NOT NULL,               -- the mention exactly as it appeared
  normalized_lemma text NOT NULL,              -- unaccent+lower of surface_form
  canonical_name_form text,                    -- LLM-emitted nominative/canonical form at extraction (registries §5) — feeds T0 + becomes an llm_canonical alias
  context         text,                        -- short surrounding snippet for adjudication/audit (not the document body)
  language        text,                        -- mention language (per-deployment multilingual path, registries §5)
  claim_id        uuid,                        -- LOGICAL FK → claims; the claim this mention occurs in
  chunk_id        uuid,                        -- LOGICAL FK → chunks
  doc_id          uuid NOT NULL,               -- LOGICAL FK → documents
  char_start      integer,                     -- mention offset into the document markdown
  char_end        integer,
  created_at      timestamptz NOT NULL DEFAULT now(),  -- partition key (ingest month)
  PRIMARY KEY (mention_id, created_at)
) PARTITION BY RANGE (created_at);
COMMENT ON TABLE mentions IS
  'Immutable transcript of entity mentions (D17). Evidence for resolution verdicts; never edited. Monthly-partitioned, btree-only, logical FKs (D23). canonical_name_form is the LLM nominative form feeding T0 (registries §5).';
CREATE INDEX ix_mentions_claim ON mentions (claim_id);
CREATE INDEX ix_mentions_doc   ON mentions (deployment_id, doc_id);

-- ─────────────────────────────────────────────────────────────────────────
-- resolution_decisions — append-only verdict (D17). A better resolver SUPERSEDES (superseded_by),
-- never overwrites. ~10⁸ rows ⇒ monthly partition by decided_at; logical FKs (D23).
-- method ∈ {T0,T3,T4_small,T4_frontier,human}: T1/T2 are BLOCKING (candidate generation), never a
-- decision (D17 block-loose/decide-tight) — enforced by the CHECK below; which blocking tier
-- surfaced a candidate is recorded inside features.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE resolution_decisions (
  decision_id     uuid NOT NULL,
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  mention_id      uuid NOT NULL,               -- LOGICAL FK → mentions
  entity_id       uuid NOT NULL,               -- LOGICAL FK → entities; the resolved canonical id
  method          resolution_tier NOT NULL,    -- T0 | T3 | T4_small | T4_frontier | human (NOT T1/T2 — see CHECK)
  confidence      real NOT NULL,               -- tier confidence; bands in resolver_versions.thresholds
  is_new_entity   boolean NOT NULL DEFAULT false, -- true if this decision minted a new entity (no confident match)
  features        jsonb,                       -- evidence used (trigram/phonetic/cosine scores incl. the surfacing blocking tier, LLM rationale)
  resolver_version text NOT NULL,              -- LOGICAL FK → resolver_versions; pins the thresholds in force
  decided_by      decision_actor NOT NULL DEFAULT 'auto',
  decided_at      timestamptz NOT NULL DEFAULT now(),  -- partition key
  superseded_by   uuid,                        -- LOGICAL FK → resolution_decisions; set when a later decision replaces this one
  PRIMARY KEY (decision_id, decided_at),
  CHECK (method NOT IN ('T1','T2'))            -- T1/T2 are candidate generation, never a verdict (D17)
) PARTITION BY RANGE (decided_at);
COMMENT ON TABLE resolution_decisions IS
  'Append-only resolution verdicts (D17/D21). Replaced by superseded_by, never overwritten — re-adjudicable. Monthly-partitioned, logical FKs (D23). method excludes the blocking tiers T1/T2 (block-loose/decide-tight); features keeps the per-tier evidence for audit.';
CREATE INDEX ix_resdec_mention ON resolution_decisions (mention_id);
CREATE INDEX ix_resdec_entity  ON resolution_decisions (deployment_id, entity_id);
CREATE INDEX ix_resdec_live    ON resolution_decisions (mention_id) WHERE superseded_by IS NULL;

-- ─────────────────────────────────────────────────────────────────────────
-- merge_events — append-only reversibility record (D21). Snapshots pre-merge membership so
-- un-merge replays it. trigger_lemmas makes the generic-identifier-guard re-evaluation queryable
-- (registries §6: "the merges a downweighted signal caused are re-evaluated"). Not huge ⇒ real
-- composite FKs, no partition.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE merge_events (
  merge_id        uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  survivor_id     uuid NOT NULL,               -- the entity that absorbed the other
  absorbed_id     uuid NOT NULL,               -- the entity redirected into survivor (keeps its id, status=merged)
  trigger_lemmas  text[] NOT NULL DEFAULT '{}',-- the blocking lemma(s) that drove this merge — enumerated for guard re-evaluation (D21, registries §6)
  evidence        jsonb,                       -- why the merge fired (scores, reviewer note)
  blast_radius    integer,                     -- combined mention_count+degree at merge time (registries §6) — never auto-merge above threshold
  pre_merge_membership_snapshot jsonb NOT NULL,-- which mentions belonged to which entity BEFORE the merge — replay to un-merge (D21)
  decided_by      decision_actor NOT NULL DEFAULT 'auto', -- hub merges never auto (registries §6/§8)
  decided_at      timestamptz NOT NULL DEFAULT now(),
  reversed_by     uuid REFERENCES merge_events,-- the un-merge event that undid this one, if any
  FOREIGN KEY (deployment_id, survivor_id) REFERENCES entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, absorbed_id) REFERENCES entities (deployment_id, entity_id)
);
COMMENT ON TABLE merge_events IS
  'Append-only merge log enabling un-merge (D21) — the capability no OSS ER system ships. pre_merge_membership_snapshot is the "before" picture replayed to reverse; trigger_lemmas lets the generic-identifier guard re-evaluate affected merges; P2 rebuild re-points the graph for free.';
CREATE INDEX ix_merge_survivor ON merge_events (survivor_id);
CREATE INDEX ix_merge_absorbed ON merge_events (absorbed_id);
CREATE INDEX ix_merge_trigger  ON merge_events USING gin (trigger_lemmas); -- guard re-evaluation by lemma
```

---

## 5. Review queue, golden set & evaluation (D22, D24, O6)

Resolution quality is a *measured pipeline property*. Three first-class assets: the **cluster-
review queue** (D24 — review clusters, not pairs; route only the middle impact band to humans), the
**golden EVAL set** (D22 — unbiased, human-adjudicated, held separate from any future training
set), and the **eval-run history** (per-tier metrics with Wilson confidence intervals).

> **Wilson interval** (used below) = a small-sample-robust error bar on a proportion like precision
> or recall, so a P/R figure measured on only ~200 golden pairs is not read as more precise than
> the sample supports (D22). **Splink waterfall** (used in the review payload) = the per-feature
> score breakdown a reviewer sees — how each signal (trigram, phonetic, embedding, shared context)
> raised or lowered the match score — which is what a human needs to adjudicate a cluster.

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- review_queue — the thin Postgres-backed CLUSTER review queue (D24). An action here appends to
-- resolution_decisions / merge_events (the verdict tables); the queue holds proposals + status.
-- Band boundaries (auto-accept ceiling / review band / hub-merge no-auto-accept floor) live,
-- versioned, in resolver_versions.tier_config (so routing thresholds are auditable per version).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE review_queue (
  review_id       uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  item_kind       review_item_kind NOT NULL,   -- merge_cluster | split_cluster | type_conflict | generic_identifier | contradiction
  candidate       jsonb NOT NULL,              -- the cluster: entity/mention ids + the Splink-style per-feature score waterfall + cluster card
  blast_radius    integer NOT NULL,            -- combined size/connectedness if wrong (registries §6)
  confidence      real NOT NULL,               -- model confidence in the proposal
  expected_impact real NOT NULL,               -- blast_radius × (1−confidence) — the routing/ranking score (D24)
  status          review_status NOT NULL DEFAULT 'pending', -- pending | accepted | rejected | deferred | auto_resolved
  verdict         review_verdict,              -- outcome appropriate to item_kind (merge/split/pick_a/downweight/retype/...) ; non-merge kinds use the matching enum value or verdict_note
  verdict_note    text,
  assigned_to     text,                        -- reviewer handle
  result_decision_id uuid,                     -- LOGICAL FK → the resolution_decisions / merge_events row the verdict produced
  created_at      timestamptz NOT NULL DEFAULT now(),
  resolved_at     timestamptz
);
COMMENT ON TABLE review_queue IS
  'Cluster-level human review queue (D24). Only the middle expected_impact band (boundaries in resolver_versions.tier_config) is routed to humans; hub merges never auto-accept. Verdicts append reversible, provenance-stamped rows to resolution_decisions/merge_events. verdict covers all item_kinds, not only merges.';
CREATE INDEX ix_review_pending ON review_queue (deployment_id, expected_impact DESC) WHERE status = 'pending';

-- ─────────────────────────────────────────────────────────────────────────
-- golden_pairs — the unbiased ER eval set (D22). Human-adjudicated (the cascade/LLM may propose,
-- only humans label — breaks circularity). expected_blocking_tier records the stratum so blocking
-- recall is measurable per tier (the "blocking-stratified" intent).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE golden_pairs (
  pair_id         uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  surface_a       text NOT NULL,               -- mention/alias A (stored as text so the set survives re-resolution)
  surface_b       text NOT NULL,
  context_a       text,                        -- disambiguating context for A
  context_b       text,
  label           golden_label NOT NULL,       -- match | no_match — the ground truth
  hardness        golden_hardness NOT NULL,    -- hard_positive | hard_negative | easy
  expected_blocking_tier resolution_tier,      -- which tier should surface this pair (exact/trigram/phonetic/embedding) — for per-stratum recall (D22)
  is_synthetic    boolean NOT NULL DEFAULT false, -- planted father/son/inflection/married-name case
  adjudicated_by  text NOT NULL,               -- human adjudicator (circularity guard, D22)
  created_at      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE golden_pairs IS
  'Human-adjudicated ER evaluation pairs (D22/D95). Measures one global precision/recall curve plus blocking-stratum and deciding-tier diagnostics and is never used for training. Same-lemma non-matches are first-class rows; surfaces and contexts survive re-resolution.';

-- ─────────────────────────────────────────────────────────────────────────
-- golden_claim_labels — the E2 Selection verifiability golden set (D22/D25/D35).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE golden_claim_labels (
  label_id        uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  proposition     text NOT NULL,               -- the candidate proposition under test
  context         text,                        -- the bundle context it was judged in
  expected_outcome selection_outcome NOT NULL, -- keep | rewrite | drop | kept_flagged (D31/D35)
  protected_class text,                        -- never-drop class if any: 'quantity'|'date'|'named_entity_predicate'|'change_of_state' (D35)
  adjudicated_by  text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE golden_claim_labels IS
  'Human-labelled Selection cases (D22/D35): the verifiability golden set + planted never-drop canaries that fail CI if Selection drops them. Tunes per-fact false-drop, not a corpus average.';

-- ─────────────────────────────────────────────────────────────────────────
-- eval_runs — metrics history per resolver/extractor version, all suites (D22, O6 both halves).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE eval_runs (
  eval_run_id     uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  suite           eval_suite NOT NULL,         -- includes lifecycle correctness + operational scale batteries
  component_version text NOT NULL,             -- LOGICAL FK → pipeline_component_versions / resolver_versions; what was measured
  metrics         jsonb NOT NULL,              -- global P/R plus per-tier diagnostics; recall@k per recipe; rerank weights; per-fact false-drop
  passed          boolean,                     -- did the canary regression pass for this version?
  ran_at          timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE eval_runs IS
  'Evaluation history (D22/O6). Global precision/recall plus per-tier diagnostics for resolution; recall@k and rerank tuning for retrieval; per-fact false-drop for selection. A canary regression re-runs per version.';
CREATE INDEX ix_eval_suite_ver ON eval_runs (deployment_id, suite, ran_at);

-- ─────────────────────────────────────────────────────────────────────────
-- canary_cases — known-tricky regressions re-run per resolver/extractor version (registries §10).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE canary_cases (
  canary_id       uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  suite           eval_suite NOT NULL,
  description     text NOT NULL,               -- what tricky behavior this guards (e.g. 'inflected Czech surname must merge')
  input           jsonb NOT NULL,              -- the case input
  expected        jsonb NOT NULL,              -- the required outcome
  created_at      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE canary_cases IS 'Regression canaries (registries §10): tricky cases re-run per version; a regression blocks the version from shipping.';
```

---

## 6. E0 — document lineages, versions, sections, cross-references (D36–D40, D55)

Bodies live in GCS (raw + artifacts buckets); Postgres holds **identity, versions, processing
state, artifact URIs, hashes, costs, and the section index** (D37) — never the body. Each E0
sub-worker (ingest → convert → structure → crossref, D36) is **separately versioned** and
idempotent on `content_hash + its own version`.

**Three identities (D55).** A **content object** = immutable bytes (`content_hash` — the
idempotency/dedup key, stored and converted once even across lineages); a **document lineage**
(`doc_id`) = the logical document over time, identified by connector-native
`(source_kind, source_ref)` (a Drive file ID, a message ID — renames/moves are metadata over a
stable ref); a **document version** = one observed immutable snapshot of a lineage pointing at
one content object. Everything durable (P3 paths, K citations, crossrefs, GCS path prefixes)
anchors on the lineage; per-snapshot state lives on the version. Full design:
`evidence_lifecycle_design.md` §2.

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- content_objects — immutable bytes, deduplicated (D55/D56). One row per distinct byte content
-- per deployment; two lineages carrying identical bytes (the same PDF in two Drive folders)
-- share one object — stored once; converted once PER TOOLCHAIN (D65): one byte object can own
-- several representation generations (document_representations below) — a new ASR/VLM re-reads
-- the same bytes into a new immutable representation beside the old one, never over it.
-- NEVER dedup across deployments (D37/D16).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE content_objects (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  content_hash    text NOT NULL,               -- sha256 of raw bytes — THE idempotency key (D12)
  mime            text NOT NULL,               -- detected MIME, drives the conversion router (D38)
  byte_size       bigint,
  raw_uri         text NOT NULL,               -- gs://…-raw/<doc_id-of-first-observer>/<content_hash>/original.<ext> (D51 raw mount)
  first_seen_at   timestamptz NOT NULL DEFAULT now(),
  purged_at       timestamptz,                 -- hard-forget: bytes erased when no live version references this object (§13)
  PRIMARY KEY (deployment_id, content_hash)
);
COMMENT ON TABLE content_objects IS
  'Deduplicated immutable bytes (D55/D56): one row per distinct content per deployment; versions reference these, so identical bytes across lineages are stored and converted once. content_hash idempotency (D12) lives here.';

-- ─────────────────────────────────────────────────────────────────────────
-- documents — one row per DOCUMENT LINEAGE (D55): the logical document over time, identified
-- by connector-native (source_kind, source_ref). Stable anchor for P3 paths, K citations,
-- crossrefs, GCS path prefixes. Per-snapshot state lives on document_versions. A hard-delete
-- SOFT-TOMBSTONES the lineage (deleted_at set) rather than removing it (§13) — auditors can
-- tell "forgotten" from "never existed".
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE documents (
  doc_id          uuid PRIMARY KEY,            -- stable lineage identity (used in GCS path prefixes)
  deployment_id   uuid NOT NULL REFERENCES deployments,
  source_kind     text NOT NULL,               -- connector kind: google_drive | upload | email | url | … (identity rules per kind: lifecycle spike 4)
  source_ref      text,                        -- connector-native stable ID (Drive file ID, message ID); NULL only for kinds without one (one-shot uploads)
  source_uri      text,                        -- original location, if any
  versioning_mode versioning_mode NOT NULL DEFAULT 'snapshot', -- D55: snapshot (fail-safe) | living (currency follows the current version, D54)
  origin          document_origin NOT NULL DEFAULT 'external', -- D42: external | system_generated — stamped at ingest, per lineage
  current_version_id uuid,                     -- → document_versions; the lineage's current snapshot (real FK added after that table)
  document_entity_id uuid,                      -- OPTIONAL bridge to the Document-typed entity (see note below); composite FK
  title           text,                        -- best-effort current title (the human name lives in P3, not the canonical path)
  first_seen_at   timestamptz NOT NULL DEFAULT now(),
  last_observed_at timestamptz,                -- last connector observation (watch loop heartbeat)
  deleted_at      timestamptz,                 -- lineage tombstone for hard-delete/forget (§13)
  UNIQUE (deployment_id, source_kind, source_ref),  -- lineage identity (D55)
  UNIQUE (deployment_id, doc_id),               -- composite-FK target (tenancy isolation, §0)
  FOREIGN KEY (deployment_id, document_entity_id) REFERENCES entities (deployment_id, entity_id) ON DELETE SET NULL (document_entity_id)
);
COMMENT ON TABLE documents IS
  'Document LINEAGES (D55): the logical document over time, connector-native identity. Snapshot state lives on document_versions; bytes on content_objects; bodies in GCS. versioning_mode drives testimony currency (D54); origin is the D42 stamp. A forget soft-tombstones the lineage.';
CREATE INDEX ix_documents_live     ON documents (deployment_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_documents_entity   ON documents (document_entity_id) WHERE document_entity_id IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────
-- document_versions — semantically append-only observed snapshots of a lineage (D55).
-- source_version_ref is the sole mutable cursor: it may advance on identical-byte revision
-- churn so polling converges; it never changes snapshot time or derived meaning. One row per
-- (lineage, content) observation the connector chose to ingest (debounced — rapid edits
-- coalesce; unchanged revision/etag or bytes never create a row). Carries everything that is
-- true OF A SNAPSHOT: artifact URIs, conversion/structure provenance, processing status.
-- source_modified_at feeds derived claims' asserted_at (testimony is dated by when the source
-- said it — D41/D55).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE document_versions (
  version_id      uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  doc_id          uuid NOT NULL,               -- composite FK below → documents (the lineage)
  content_hash    text NOT NULL,               -- → content_objects (composite FK below)
  version_no      integer NOT NULL,            -- 1..n within the lineage
  source_version_ref text,                     -- connector revision/etag/generation; may advance on an identical-byte no-op so polling converges, but is not semantic snapshot metadata
  sync_cycle_id   uuid,                        -- LOGICAL FK → connector_sync_cycles (created below): which cycle observed this version (retract barrier)
  source_modified_at timestamptz,              -- immutable after version creation: when the SOURCE says this snapshot was authored/modified → deterministic E2 header + derived claims' asserted_at
  published_at    timestamptz,                 -- document's own date (resolves "last year"); world-time origin
  language        text,                        -- detected primary language (per version — it can change)
  source_shape    text,                        -- D80 typed filter grain: document | message_atom | thread | channel_export | connector-defined extension
  current_representation_id uuid,              -- → document_representations (D65): the LIVE reading of this snapshot; swapped only after the new representation's conversion→E1→E2 chain completes (real FK added after that table)
  status          document_status NOT NULL DEFAULT 'ingesting', -- ingesting | converting | structuring | ready | failed | deleted
  error           text,
  ingested_at     timestamptz NOT NULL DEFAULT now(),  -- system-time origin for everything derived from this version
  superseded_at   timestamptz,                 -- set when a newer version becomes current (lineage pointer moved)
  deleted_at      timestamptz,                 -- version tombstone (delete-a-version, §13)
  UNIQUE (deployment_id, doc_id, content_hash),
  UNIQUE (deployment_id, doc_id, version_no),
  UNIQUE (deployment_id, version_id),           -- composite-FK target
  UNIQUE (deployment_id, doc_id, version_id),   -- composite-FK target for the CURRENT pointer (a lineage can only point at ITS OWN version)
  FOREIGN KEY (deployment_id, doc_id) REFERENCES documents (deployment_id, doc_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, content_hash) REFERENCES content_objects (deployment_id, content_hash)
);
COMMENT ON TABLE document_versions IS
  'Append-only snapshots of a lineage (D55). source_modified_at dates the testimony (→ claims.asserted_at). Artifacts + conversion provenance live on document_representations (D65) — a version can own several immutable readings; current_representation_id names the live one. The lineage''s current_version_id points here; superseding never deletes. Chunks/sections/claims derive from ONE (version, representation) and denormalize doc_id.';
CREATE INDEX ix_docversions_doc     ON document_versions (doc_id, version_no DESC);
CREATE INDEX ix_docversions_status  ON document_versions (deployment_id, status) WHERE status <> 'ready';
CREATE INDEX ix_docversions_hash    ON document_versions (deployment_id, content_hash);
CREATE INDEX ix_docversions_shape   ON document_versions (deployment_id, source_shape)
  WHERE source_shape IS NOT NULL;

ALTER TABLE documents ADD FOREIGN KEY (deployment_id, doc_id, current_version_id)
  REFERENCES document_versions (deployment_id, doc_id, version_id);
  -- the current-snapshot pointer, moved transactionally with currency (D54). The THREE-column FK
  -- (incl. doc_id) makes cross-lineage pointers unrepresentable — a lineage can only point at its
  -- own version (Codex review F6).

-- ─────────────────────────────────────────────────────────────────────────
-- document_representations — one conversion run's IMMUTABLE output (D65): the identified
-- "reading" of a version's bytes. A version owns 1..n representations over its life (the 2026
-- ASR's transcript and the 2027 ASR's transcript of the same recording are two rows, both
-- kept); document_versions.current_representation_id names the live one and is swapped ONLY
-- on completion of the new representation's conversion→E1→E2 chain (no window where old
-- testimony is retired and new hasn't landed — the D54 completion rule). Artifact paths carry
-- the representation dimension (…/<doc_id>/<content_hash>/<representation_id>/…), so a
-- re-conversion can never overwrite the coordinate system historical claims' spans and
-- locators resolve against. Rows are NEVER updated after status='ready'; a re-run of the same
-- (content, route, versions) replays this row's stored output (D7) — the model is not
-- re-called. Old representations are deleted only by the version/lineage deletion cascade.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE document_representations (
  representation_id uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  version_id      uuid NOT NULL,               -- composite FK below → document_versions: a representation reads ONE snapshot
  -- route + component identity (what produced this reading — the reuse key with content_hash):
  route           text NOT NULL,               -- router route taken (digital_pdf | ocr | markitdown | asr_diarized | video_asr_keyframes | image_description | …, D38/D65)
  converter_name  text,
  converter_version text,                      -- LOGICAL FK → pipeline_component_versions; a bump creates a NEW representation (never mutates this one)
  blockizer_version text,                      -- LOGICAL FK → pipeline_component_versions; blocks = f(document.md, blockizer_version) (D57)
  structurer_name text,
  structurer_version text,
  structurer_model text,
  structurer_prompt_version text,
  -- GCS artifact URIs (bodies live there, not in PG — D37); all under …/<content_hash>/<representation_id>/:
  markdown_uri    text,                        -- document.md (clean Markdown — the immutable coordinate system, D57)
  pageindex_uri   text,                        -- pageindex.json
  conversion_uri  text,                        -- conversion.json (source map + route manifest: component graph, execution context (D61), coverage, gaps/warnings, range→derivation labels — D65)
  blocks_uri      text,                        -- blocks.json (the blockizer's block sequence — identity substrate, D57)
  meta_uri        text,                        -- meta.json
  -- output identity (from the manifest — replay/verification, D7/D65):
  markdown_hash   text,                        -- sha256 of document.md
  manifest_hash   text,                        -- sha256 of the manifest (covers source map + derived-asset hashes)
  pageindex_hash  text,
  placement_version text,
  section_index_version text,
  crossref_version text,
  status          text NOT NULL DEFAULT 'converting',  -- converting | structuring | ready | failed
  error           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, representation_id),    -- composite-FK target
  UNIQUE (deployment_id, version_id, representation_id), -- composite-FK target for the CURRENT pointer
  FOREIGN KEY (deployment_id, version_id) REFERENCES document_versions (deployment_id, version_id) ON DELETE CASCADE
);
COMMENT ON TABLE document_representations IS
  'Immutable conversion outputs (D65): one row per (version, toolchain) reading. The extraction basis (D54-refined) is (representation_id, blockizer_version, structurer_version, extractor_version). Never updated after ready; never overwritten by re-conversion; replayed, not regenerated, on re-runs (D7).';
CREATE INDEX ix_docreps_version ON document_representations (version_id, created_at DESC);

ALTER TABLE document_versions ADD FOREIGN KEY (deployment_id, version_id, current_representation_id)
  REFERENCES document_representations (deployment_id, version_id, representation_id);
  -- the current-reading pointer (D65): three-column FK — a version can only point at its OWN
  -- representation; swapped transactionally with the currency flip on chain completion (D54).

-- ─────────────────────────────────────────────────────────────────────────
-- connector_sync_cycles — the D55 retract-timing barrier (Codex review F8). A watched
-- connector's poll cycle is explicit state: living-mode retraction evaluation (D55 — all
-- removals retract; the 'review' softener was removed, lifecycle §2) runs ONLY as a
-- cycle-finalization job after every lineage observed in the cycle has completed
-- extraction — so an intra-cycle section MOVE resolves as a support swap, never
-- retract-then-reassert. Lineages still extracting at finalization defer their retraction checks
-- to the next finalization (grace, recorded).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE connector_sync_cycles (
  cycle_id        uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  source_kind     text NOT NULL,               -- which connector (google_drive, …)
  started_at      timestamptz NOT NULL DEFAULT now(),
  observed_lineages integer,                   -- how many lineages this cycle touched
  completed_at    timestamptz,                 -- all observations ingested
  finalized_at    timestamptz                  -- retraction evaluation ran (only after completed_at)
);
COMMENT ON TABLE connector_sync_cycles IS
  'D55 retract-timing barrier: living-mode retraction evaluates only at cycle finalization, after every lineage the cycle observed finished extraction — an intra-cycle move is a support swap, never a retract flicker. document_versions.sync_cycle_id stamps membership. FINALIZATION CONTRACT: the connector worker sets completed_at when the poll pass ends; an async finalization job runs when every stamped lineage''s extraction is done (or a timeout elapses), sets finalized_at, and evaluates retractions; lineages still extracting defer to the NEXT finalization — the deferral is visible as (completed_at set, finalized_at null) plus the lineage''s processing_state.';
```

> **Document ↔ entity bridge (D18, Codex review).** D18 makes `Document ⊂ CreativeWork` a core
> *entity* type with predicates `authored: Person → Document` and `about: Document → any`, so
> documents participate in relations as entities. The corpus's *ingested files* and the *registry's
> Document entities* are distinct but linkable: `documents.document_entity_id` points an ingested
> file at its registry entity **when one exists**. Policy: an ingested document gets a Document
> entity when it is referenced as the subject/object of a relation (e.g. "Alice authored this
> report") or by a deployment-configured default; a Document entity may also exist for a
> *cited-but-not-ingested* paper (created from a `document_crossrefs` row with no `to_doc_id`),
> which has a registry entity but no `documents` row. So the bridge is nullable in both directions
> and neither side is mandatory.

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- document_sections — the queryable PageIndex section index (D39). Every document gets rows here
-- unconditionally (a short doc gets one synthetic root section). Summaries are kept as context,
-- never facts. parent_section_id cascades on delete so a hard-delete removes the whole subtree (§13).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE document_sections (
  section_id      uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  doc_id          uuid NOT NULL,               -- composite FK below, ON DELETE CASCADE (the lineage — denormalized for routing)
  version_id      uuid NOT NULL,               -- composite FK below → document_versions: structure derives from ONE snapshot (D55)
  representation_id uuid NOT NULL,             -- LOGICAL FK → document_representations (D65): the reading whose document.md these spans index — offsets are meaningless without it
  parent_section_id uuid REFERENCES document_sections ON DELETE CASCADE, -- tree structure; NULL for root; cascades the subtree
  node_path       text NOT NULL,               -- materialized path, e.g. '0.2.1' — cheap ancestor/subtree queries
  block_start     integer NOT NULL,            -- first block ordinal of the section (D57: sections are BLOCK RANGES on the deterministic grid)
  block_end       integer NOT NULL,            -- last block ordinal (inclusive); char spans below are derived from the blocks
  title           text,
  role            section_role NOT NULL,       -- body|abstract|introduction|...|references|nav|boilerplate|legal (D39)
  char_start      integer NOT NULL,            -- section span start, char offset into document.md
  char_end        integer NOT NULL,            -- section span end
  page_start      integer,                     -- source page span (from conversion blocks), if paginated
  page_end        integer,
  ordinal         integer NOT NULL,            -- order among siblings
  summary         text,                        -- per-section summary (D39): context for E1 prefixes/navigation/Selection-explainability; NOT a fact source
  placement_path  text,                        -- per-section placement hint for P3 (advisory; D39/D40)
  structurer_version text,                     -- LOGICAL FK → pipeline_component_versions; matches documents.structurer_version
  UNIQUE (version_id, node_path),
  FOREIGN KEY (deployment_id, doc_id) REFERENCES documents (deployment_id, doc_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, version_id) REFERENCES document_versions (deployment_id, version_id) ON DELETE CASCADE
);
COMMENT ON TABLE document_sections IS
  'Per-document section tree (D39): path/role/span/summary/placement per section. Drives section-aware chunking (E1), the E2 role signal (Selection drops references/boilerplate at proposition grain), and P3 placement. Summaries are context, never facts.';
CREATE INDEX ix_sections_doc    ON document_sections (doc_id);
CREATE INDEX ix_sections_role   ON document_sections (deployment_id, role);
CREATE INDEX ix_sections_parent ON document_sections (parent_section_id);

-- ─────────────────────────────────────────────────────────────────────────
-- document_crossrefs — citations / inter-document links (the crossref sub-worker, D36). to_doc_id
-- is NULL until/unless the cited target resolves to an ingested document. ON DELETE SET NULL uses
-- the PG15+ column-list form so only to_doc_id is cleared (deployment_id stays). Projected to graph
-- CITES edges (p2 §2).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE document_crossrefs (
  crossref_id     uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  from_doc_id     uuid NOT NULL,               -- composite FK below, ON DELETE CASCADE
  to_doc_id       uuid,                        -- composite FK below, ON DELETE SET NULL(to_doc_id); NULL if cited doc not (yet) ingested
  kind            crossref_kind NOT NULL,      -- cites | links_to | attaches | replies_to
  raw_citation    text,                        -- the citation text as found; RETAINED even when resolved, so a forgotten target can be re-resolved (§13)
  context         text,                        -- surrounding context of the reference
  resolved        boolean NOT NULL DEFAULT false, -- whether to_doc_id was matched
  crossref_version text,                       -- LOGICAL FK → pipeline_component_versions (crossreferencer); a bump re-extracts (D36/D7)
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (deployment_id, from_doc_id) REFERENCES documents (deployment_id, doc_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, to_doc_id)   REFERENCES documents (deployment_id, doc_id) ON DELETE SET NULL (to_doc_id)
);
COMMENT ON TABLE document_crossrefs IS
  'Cross-document references from the E0 crossref sub-worker (D36), versioned by crossref_version (D7 replay). Projected to graph CITES edges; raw_citation is retained even after resolution so a forgotten/re-ingested target can be re-resolved.';
CREATE INDEX ix_crossrefs_from ON document_crossrefs (from_doc_id);
CREATE INDEX ix_crossrefs_to   ON document_crossrefs (to_doc_id) WHERE to_doc_id IS NOT NULL;
```

---

## 7. E1 — chunks

Retrieval-sized units that preserve context and trace back to position. Chunk
**search text and embedding live in the private PostgreSQL `chunk_search`
sidecar**; authority
tables hold metadata, offsets, the section link, and the **generated
context prefix** (LLM-derived, so it is *replayed* on rebuild, not regenerated — D7 — and therefore
stored as derived metadata, not body text).

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- chunks — semchunk units, section-aware (never split mid-section, D39). Body = markdown_uri sliced
-- by [char_start,char_end] (NOT stored in the authority row, D37). P1 is keyed by chunk_id. Large
-- (tens of millions) ⇒ monthly partition by created_at; logical FKs (D23). Pruning: §12.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE chunks (
  chunk_id        uuid NOT NULL,
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  doc_id          uuid NOT NULL,               -- LOGICAL FK → documents (the lineage — denormalized for routing/counting)
  version_id      uuid NOT NULL,               -- LOGICAL FK → document_versions: a chunk belongs to ONE snapshot (D55)
  representation_id uuid NOT NULL,             -- LOGICAL FK → document_representations (D65): the reading whose block grid + document.md offsets this chunk is cut from — the basis coordinate on every occurrence
  section_id      uuid,                        -- LOGICAL FK → document_sections; section (role/path signal for E2)
  ordinal         integer NOT NULL,            -- position within the document
  block_start     integer NOT NULL,            -- first block ordinal packed into this chunk (D57/D58: a chunk = a run of whole blocks)
  block_end       integer NOT NULL,            -- last block ordinal (inclusive)
  chunk_content_hash text NOT NULL,            -- hash of the chunk's ORDERED BLOCK HASHES (D58) — embedding-reuse + occurrence identity
  extraction_input_hash text NOT NULL,         -- hash of STABLE components only: own block hashes + neighbor block hashes + stable header facts (deterministic document metadata fed to the E2 bundle: title, source_kind, source_modified_at/published_at, language) + extractor_version + structurer_version (D56/D57/D58 — NO LLM output in the key; prefixes/summaries/section paths are carried forward, not keyed; a structurer bump is a re-extraction boundary)
  char_start      integer NOT NULL,            -- chunk span start, offset into document.md
  char_end        integer NOT NULL,            -- chunk span end
  token_count     integer,                     -- token length (sizing/budget)
  -- D80 embedding-input stamps (see e1_embedding_input_policy.md). Full embedding text is NOT
  -- stored here (D37); chunk_search holds normalized body + one current vector.
  location_facts_json jsonb,                   -- typed location-facts snapshot (schema version inside JSON); null until prepare
  location_header text,                        -- optional bounded deterministic header only (not body); null in body_only mode
  embedding_text_hash text,                    -- hash of canonical embedding text under the policy
  embedding_input_policy_version text,         -- LOGICAL FK → pipeline_component_versions (embedding_input_policy)
  context_prefix  text,                        -- LEGACY LLM location prose (pre-D80); retained for migration/replay; not written on D80 path
  prefixer_version text,                       -- LEGACY context_prefixer component version
  chunker_version text,                        -- LOGICAL FK → pipeline_component_versions (semchunk config)
  embedding_version text,                      -- configured embedder/model attestation for the current chunk vector
  policy_generation text,                      -- configured embedding_input_policy_version
  created_at      timestamptz NOT NULL DEFAULT now(),  -- partition key
  PRIMARY KEY (chunk_id, created_at)
) PARTITION BY RANGE (created_at);
COMMENT ON TABLE chunks IS
  'E1 retrieval units (semchunk, section-aware), one row per (version, position). Normalized search text + current embedding live in private chunk_search (D94); the authority row stores offsets, section link, D80 location-facts/header/hash/policy stamps, version stamps, and D56 extraction reuse keys. Passage vector reuse is embedding_text_hash + policy + embedder generation (D80), not content hash alone. Monthly-partitioned, logical FKs (D23).';
CREATE INDEX ix_chunks_doc     ON chunks (deployment_id, doc_id);
CREATE INDEX ix_chunks_version ON chunks (version_id);
CREATE INDEX ix_chunks_reuse   ON chunks (deployment_id, doc_id, extraction_input_hash);  -- the D56 reuse lookup
CREATE INDEX ix_chunks_section ON chunks (section_id);

-- D94 chunk_search is the ONLY P1 sidecar. The reference profile pins
-- Qwen3-Embedding-8B and pgvector HNSW to 1,536 float32 dimensions.
-- pg_textsearch creates the BM25 index on search_text. No entity/time/lineage
-- filter fields are copied here; ranked queries join chunks and their authority
-- associations in the same statement.
CREATE TABLE chunk_search (
  deployment_id uuid NOT NULL,
  chunk_id uuid NOT NULL,
  search_text text NOT NULL,
  embedding vector(1536),                      -- D94 reference profile dimension
  embedding_model text,
  embedding_input_policy_version text,
  embedding_text_hash text,
  PRIMARY KEY (deployment_id, chunk_id)
);
COMMENT ON TABLE chunk_search IS
  'D94 disposable chunk retrieval state: whitespace-normalized canonical source slice, one current vector, and minimum attestation. No copied entity arrays, temporal state, eligibility, lineage, summaries, neighboring text, or location header.';
CREATE INDEX ix_chunk_search_embedding_hnsw ON chunk_search
  USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;
CREATE INDEX ix_chunk_search_bm25 ON chunk_search
  USING bm25 (search_text) WITH (text_config='simple');
-- chunks is range-partitioned and its primary key includes created_at, so this
-- sidecar cannot use a truthful two-column physical FK. The E1/lifecycle
-- transaction owns the upsert/delete and the D23 auditor rejects orphans.

-- ─────────────────────────────────────────────────────────────────────────
-- chunk_claims — the claim OCCURRENCE map (D56; Codex review F4) and, since D65, the
-- OCCURRENCE-GRAIN PROVENANCE home. claims.chunk_id names the ORIGIN chunk (immutable
-- provenance); when a new version's chunk REUSES prior claims, the link is recorded here —
-- one row per (chunk, claim) attachment, making "which versions carried this claim" an exact
-- join (never an ambiguous chunk_content_hash match — duplicate identical chunks within a
-- version stay distinguishable). The derivation labels + locator set live HERE, not on
-- claims, because they are occurrence facts: the same claim text re-derived by a new ASR
-- generation keeps its text but gets new timestamps, speaker labels, and model family — the
-- claim is immutable, its occurrence provenance varies per representation (reached via the
-- chunk's representation_id). claims_as_of over living documents, currency transitions,
-- K (lineage, chunk)-grain citations, envelope evidence provenance (retrieval §5), and
-- modality-aware audits (which raw target to judge) read THIS table. Written by the E1/E2
-- workers on both fresh extraction and reuse; append-only; monthly-partitioned like chunks.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE chunk_claims (
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  chunk_id        uuid NOT NULL,               -- LOGICAL FK → chunks (a specific version's chunk row; representation via chunks.representation_id)
  claim_id        uuid NOT NULL,               -- LOGICAL FK → claims
  derivation_kind text,                        -- D65 disclosure, resolved from the manifest's labeled ranges: asr | acoustic_events | vlm_description | ocr | shot_notes | passthrough | …
  evidence_mode   text,                        -- D65: source_expression | model_observation | model_interpretation (most-mediated wins on range-crossing spans)
  source_locators jsonb,                       -- D65: resolved locator set for THIS occurrence (SourceLocator[], media_design §4) — the span→source-map intersection, cached
  created_at      timestamptz NOT NULL DEFAULT now(),  -- partition key
  PRIMARY KEY (chunk_id, claim_id, created_at)
) PARTITION BY RANGE (created_at);
COMMENT ON TABLE chunk_claims IS
  'Claim occurrences per version-chunk (F4) + occurrence-grain provenance (D65): fresh extraction AND reuse both link here, so one immutable claim attaches to every version-chunk that carries it, each attachment carrying its resolved derivation labels + locators. The exact occurrence record behind claims_as_of on living documents, the (lineage, chunk)-grain K citation keys, and envelope evidence provenance. Monthly-partitioned; logical FKs (D23).';
CREATE INDEX ix_chunkclaims_claim ON chunk_claims (claim_id);
```

---

## 8. E2 — claims, the extraction decision ledger, grounding audits (D31–D35)

A **claim** is an atomic, standalone, verifiable assertion (Claimify-staged extraction). Claims are
**immutable, append-only** — they record *what a source said* and are never superseded
(supersession is on *relations*, D3). The model stores both the standalone `claim_text` and the
verbatim `source_span` + offsets + the `added_context` substrings (D32), so grounding is
**provenance + entailment**, not verbatim-substring matching.

> **Reconciliation note (claim "type").** `requirements_v3` (an earlier conception) said claims are
> "typed (fact / opinion / prediction)". The current binding design (D31/D34/D59 Claimify Selection)
> **drops** *unattributed* opinions / advice / hypotheticals at Selection rather than storing them as
> typed claims — attributed stances are kept as ordinary claims (D59; `is_attributed` marks them) and
> normalize to holder-anchored observations — so the *stored* claim space stays "verifiable
> proposition", and dropped material lives only in the **drop ledger**
> (`claim_extraction_decisions`), not as a `claims.claim_type` value. We therefore carry no fact/opinion/prediction column; we carry
> `temporal_class` (the "temporally classified" half) and the grounding/recall-envelope fields the
> binding design names. Intentional simplification (CLAUDE.md), recorded so a reader does not think
> the column was forgotten.

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- claims — immutable verifiable propositions (D31/D32). THREE immutable time axes (concepts §5, D41):
-- asserted_at = assertion-EVENT time (when the source spoke, ≈ published_at); claim_valid_from/until
-- (+ precision/kind) = the world-time interval the SOURCE asserted the proposition held (valid-time
-- as EVIDENCE, not current belief); ingested_at = when WE extracted it (transaction-time). None ever
-- change, and claim validity is never superseded (D3) — adjudicated validity lives only on relations.
-- A row in claims is an ACCEPTED claim: the deterministic grounding gate (anchor + window
-- membership, D32 layers 1-2) MUST pass, enforced by the CHECK — a claim that fails the gate is
-- never produced (it becomes a grounding_rejected ledger entry, #161; never silently discarded),
-- so the flags exist for audit and are always true here. Large (~5×10⁷) but
-- non-partitioned under D94 so current-testimony BM25 uses one global corpus.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE claims (
  claim_id        uuid NOT NULL,
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  doc_id          uuid NOT NULL,               -- LOGICAL FK → documents (provenance always attached — requirements_v3)
  chunk_id        uuid NOT NULL,               -- LOGICAL FK → chunks (the target chunk of the context bundle)
  section_id      uuid,                        -- LOGICAL FK → document_sections (denormalized role/path; explains a Selection decision)
  claim_text      text NOT NULL,               -- the STANDALONE assertion — what retrieval/E3/reasoning use (decontextualized, D32)
  source_span     text NOT NULL,               -- the verbatim slice of the chunk the claim derives from (provenance/audit, D32)
  char_start      integer NOT NULL,            -- source_span offset into document.md (anchor check: a real in-bounds slice, D32 layer 1)
  char_end        integer NOT NULL,
  added_context   jsonb NOT NULL DEFAULT '[]', -- [{text, source_kind: header|neighbour|prefix|hint, source_ref}] — each substring decontextualization ADDED (D32 layer 2)
  temporal_class  claim_temporal_class,        -- static | dynamic | atemporal — the "temporally classified" requirement (see reconciliation note)
  is_attributed   boolean NOT NULL DEFAULT false, -- preserves a "X said Y" attribution (entailment rule: entails "X said Y", not "Y" — D32)
  -- grounding verdicts (D32). Deterministic layers 1-2 are an ACCEPTANCE GATE (must be true here);
  -- the LLM layers 3-4 are advisory/sampled and may be false on a kept-but-borderline claim:
  anchor_ok       boolean NOT NULL,            -- layer 1: source_span is a real in-bounds slice of the chunk (deterministic)
  window_membership_ok boolean NOT NULL,       -- layer 2: every added_context substring verbatim-exists in its declared bundle source (deterministic; rejects fabrication)
  entailment_self_verdict boolean,             -- layer 3: in-call self-assertion the bundle entails the claim (~free, optimistic)
  audit_status    grounding_audit_status NOT NULL DEFAULT 'unaudited', -- layer 4: unaudited | sampled_pass | sampled_fail | escalated (sampled, not per-claim)
  kept_flagged    boolean NOT NULL DEFAULT false, -- D35 low-confidence Selection outcome: kept but marked-for-review (mirrors a selection_keep_flagged ledger row — see invariant below)
  is_current_testimony boolean NOT NULL DEFAULT true, -- D54 CACHE of testimony currency (the ledger below is truth): false once a newer extraction generation covers this chunk, or (living mode) the chunk left the current version. Bookkeeping, NEVER validity — no adjudication, claims stay immutable in every D3 sense
  asserted_at     timestamptz,                 -- ASSERTION-EVENT time: when the source asserted this (≈ the version's source_modified_at/published_at, D55) — immutable; NOT the fact's world-time (that is claim_valid_*, D41)
  -- D41 source-asserted world-validity INTERVAL — immutable evidence about WHEN (not current belief).
  -- Overlap of these intervals across sources is EXPECTED (it is evidence), so there is deliberately
  -- NO uniqueness/EXCLUDE, NO invalidated_at, NO status here — the opposite of relations (§9):
  claim_valid_from timestamptz,                -- world-time start the SOURCE attributed (NULL = unbounded-before/unknown); immutable
  claim_valid_until timestamptz,               -- world-time end (NULL = open-per-source OR unknown; disambiguated by claim_valid_precision)
  claim_valid_precision claim_valid_precision NOT NULL DEFAULT 'unknown', -- unknown|instant|day|month|quarter|year|open — "FY2023" stores a normalized [start,end] without lying about granularity
  claim_valid_kind claim_valid_kind,           -- which world-interval this is: proposition_validity|event_time|measurement_period|effective_period (so a measurement period is never conflated with an event date or with asserted_at)
  extractor_version text NOT NULL,             -- LOGICAL FK → pipeline_component_versions (extractor); replay-on-rebuild key (D33)
  embedding vector(1536),                      -- D94 disposable vector for claim_text
  embedding_model text,                        -- configured model that produced embedding
  embedding_input_policy_version text,         -- exact claim-input rendering policy
  embedding_text_hash text,                    -- hash of claim_text embedding input
  ingested_at     timestamptz NOT NULL DEFAULT now(),  -- transaction-time; immutable
  PRIMARY KEY (claim_id),
  UNIQUE (deployment_id, claim_id),             -- composite identity target; provenance FKs remain logical as documented in §0
  CHECK (char_end >= char_start),
  CHECK (anchor_ok AND window_membership_ok),  -- a claims row is an ACCEPTED claim; the deterministic grounding gate passed (D32)
  -- D41 precision/bounds coherence (claim validity carries no status/invalidated_at — it is immutable):
  CHECK (claim_valid_until IS NULL OR claim_valid_from IS NULL OR claim_valid_until >= claim_valid_from),
  CHECK (claim_valid_precision <> 'unknown' OR (claim_valid_from IS NULL AND claim_valid_until IS NULL)),
  CHECK (claim_valid_precision <> 'open'    OR (claim_valid_from IS NOT NULL AND claim_valid_until IS NULL)),
  CHECK (claim_valid_precision <> 'instant' OR (claim_valid_from IS NOT NULL AND claim_valid_until = claim_valid_from)),
  -- a bounded precision must actually carry both bounds (else it silently degrades to unknown/open):
  CHECK (claim_valid_precision NOT IN ('day','month','quarter','year') OR (claim_valid_from IS NOT NULL AND claim_valid_until IS NOT NULL)),
  CHECK (num_nonnulls(embedding, embedding_model, embedding_input_policy_version, embedding_text_hash) IN (0, 4))
);
COMMENT ON TABLE claims IS
  'E2 immutable verifiable propositions (D31/D32). Stores standalone claim_text + verbatim source_span + offsets + added_context for provenance-and-entailment grounding. Three immutable time axes (D41): asserted_at (assertion event), claim_valid_from/until (+precision/kind = source-asserted world-interval — evidence, not belief), ingested_at (system); never superseded (supersession is on relations, D3). A row here passed the deterministic grounding gate. Non-partitioned under D94 so current-testimony HNSW/BM25 indexes have one global corpus.';
CREATE INDEX ix_claims_doc      ON claims (deployment_id, doc_id);
CREATE INDEX ix_claims_chunk    ON claims (chunk_id);
CREATE INDEX ix_claims_flagged  ON claims (deployment_id) WHERE kept_flagged = true;        -- review surface (D35)
CREATE INDEX ix_claims_current  ON claims (deployment_id, doc_id) WHERE is_current_testimony; -- the D54 hot filter (counts; default claim search)
CREATE INDEX ix_claims_audit    ON claims (deployment_id) WHERE audit_status = 'sampled_fail'; -- grounding regressions
-- D41 claim validity remains on claims; ranked retrieval filters it through the
-- normalized row in the same statement as the HNSW/BM25 candidate scan. Retrieval Batch B
-- supersedes the original no-default-index stance: `claims_as_of(from,to)` first filters in the
-- authoritative Postgres spine, then optionally ranks only that bounded set semantically. Stamped
-- claims are the minority, so the default is the partial btree below. This evidence-grain recipe
-- answers "what did sources assert held over T" and is barred from answering "currently true";
-- fact-as-of stays relations-only (D10).
CREATE INDEX ix_claims_valid_window ON claims
  (deployment_id, claim_valid_from, claim_valid_until)
  WHERE claim_valid_precision <> 'unknown';
CREATE INDEX ix_claims_current_embedding_hnsw ON claims
  USING hnsw (embedding vector_cosine_ops)
  WHERE is_current_testimony;
CREATE INDEX ix_claims_current_bm25 ON claims
  USING bm25 (claim_text) WITH (text_config='simple')
  WHERE is_current_testimony;
-- Partial BM25 queries name ix_claims_current_bm25 explicitly through
-- to_bm25query(); implicit index discovery does not select partial indexes.

-- ─────────────────────────────────────────────────────────────────────────
-- claim_extraction_decisions — the append-only, version-stamped extraction transcript (D33). It
-- records every Selection DROP (with reason), every low-confidence KEEP-FLAG, every
-- decontextualization EDIT, and (issue #161, 2026-07-27) the Claimify-stage losses:
-- CLAIMIFY_OMITTED (a kept span the model returned no claim for) and GROUNDING_REJECTED (a
-- returned claim a D32 gate rejected — gate named in edit_detail; these are ledgered, not
-- discarded). Plain keeps that land are NOT recorded (they ARE the claims row); a plain keep
-- that dies now leaves a loss row, so the table sizes for drops+flags+edits+losses, still not
-- every keep. Rebuild reads stored claims + these decisions and never re-calls the model (D7).
-- Large ⇒ monthly partition by decided_at.
-- INVARIANT: a kept_flagged claim is the pair (claims row with kept_flagged=true) + (a
-- selection_keep_flagged decision here); the ledger is the replay source from which
-- claims.kept_flagged is reconstituted on rebuild.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE claim_extraction_decisions (
  decision_id     uuid NOT NULL,
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  doc_id          uuid NOT NULL,               -- LOGICAL FK → documents
  chunk_id        uuid NOT NULL,               -- LOGICAL FK → chunks
  claim_id        uuid,                        -- LOGICAL FK → claims; set for decontext edits + selection_keep_flagged; NULL for selection_drop (no claim produced)
  decision_type   extraction_decision_type NOT NULL, -- selection_drop | selection_keep_flagged | decontext_edit | claimify_omitted | grounding_rejected (#161)
  source_span     text,                        -- the proposition/sentence the decision was about
  reason          selection_drop_reason,       -- for drops: opinion|advice|hypothetical|generic|question|intro|conclusion|no_info|ambiguous|references_boilerplate (D31)
  edit_detail     jsonb,                        -- for decontext edits: what was resolved/added and from which bundle source
  protected_class text,                        -- never-drop class checked/applied (quantity|date|named_entity_predicate|change_of_state) — D35
  extractor_version text NOT NULL,             -- LOGICAL FK → pipeline_component_versions; the version that made this decision
  decided_at      timestamptz NOT NULL DEFAULT now(),  -- partition key
  PRIMARY KEY (decision_id, decided_at)
) PARTITION BY RANGE (decided_at);
COMMENT ON TABLE claim_extraction_decisions IS
  'Append-only Selection-drop + keep-flag + decontextualization-edit ledger (D33); plain keeps are not recorded. Makes aggressive Selection auditable and recoverable: a better prompt re-examines only the drops; rebuild replays from here without re-calling the model (D7). Monthly-partitioned.';
CREATE INDEX ix_cxd_chunk ON claim_extraction_decisions (chunk_id);
CREATE INDEX ix_cxd_drops ON claim_extraction_decisions (deployment_id, reason) WHERE decision_type = 'selection_drop';

-- ─────────────────────────────────────────────────────────────────────────
-- grounding_audits — the sampled independent entailment audit (D32 layer 4). Not per-claim
-- (self-grading is optimistic; a separate judge re-checks a sample; only a borderline band
-- escalates). claims.audit_status caches the latest result.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE grounding_audits (
  audit_id        uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  claim_id        uuid NOT NULL,               -- LOGICAL FK → claims
  verdict         grounding_audit_status NOT NULL, -- sampled_pass | sampled_fail | escalated
  judge_version   text NOT NULL,               -- LOGICAL FK → pipeline_component_versions (independent judge)
  rationale       text,
  sampled_at      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE grounding_audits IS
  'Sampled independent entailment audits of claims (D32 layer 4). Offline, not per-claim; feeds claims.audit_status and the grounding eval suite.';
CREATE INDEX ix_grounding_claim ON grounding_audits (claim_id);

-- ─────────────────────────────────────────────────────────────────────────
-- testimony_currency_events — the D54 currency ledger (append-only; the D33 pattern: this is
-- truth, claims.is_current_testimony is cache). A transition is BOOKKEEPING, never validity:
-- no adjudication, no invalidated_at, nothing about the claim changes. Timestamped events keep
-- transaction-time reconstructions exact (fact-as-of-T still sees old generations).
-- Written by the reconciliation step of the lifecycle flow (evidence_lifecycle_design §5),
-- which runs only on COMPLETED basis changes and then recounts affected facts.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE testimony_currency_events (
  event_id        uuid NOT NULL,
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  claim_id        uuid NOT NULL,               -- LOGICAL FK → claims
  doc_id          uuid NOT NULL,               -- LOGICAL FK → documents (the lineage; the recount scope)
  reconciliation_id uuid NOT NULL,             -- identifies ONE reconciliation run = one completed basis change per lineage (a new version's extraction completing, or a version-bump re-extraction completing). Minted when the run starts and stored in processing_state, so a RETRIED run reuses it — its re-emitted events hit the UNIQUE below as no-ops
  became_current  boolean NOT NULL,            -- false = lost currency; true = regained (un-delete, mode change)
  reason          currency_reason NOT NULL,    -- reextracted | version_superseded | version_deleted
  from_extractor_version text,                 -- the superseded generation (reason=reextracted)
  from_version_id uuid,                        -- the superseded/deleted document version (reason=version_*)
  occurred_at     timestamptz NOT NULL DEFAULT now(),  -- partition key
  PRIMARY KEY (event_id, occurred_at),
  UNIQUE (claim_id, reconciliation_id, reason, became_current, occurred_at)  -- a retried reconciliation re-emits as a no-op, never a duplicate (F11)
) PARTITION BY RANGE (occurred_at);
COMMENT ON TABLE testimony_currency_events IS
  'Append-only D54 testimony-currency transitions. Truth for claims.is_current_testimony (cache); replayable (D7). Reasons: reextracted (a newer extraction generation covers the chunk), version_superseded (living-mode lineage moved past the claim''s version), version_deleted. Never validity, never supersession — D3 untouched.';
CREATE INDEX ix_currency_claim ON testimony_currency_events (claim_id);
CREATE INDEX ix_currency_doc   ON testimony_currency_events (deployment_id, doc_id, occurred_at);
```

---

## 9. E3 — relations, evidence, supersession adjudications (D2–D4, D8)

A **relation** is a distinct fact `(subject_entity, predicate, object_entity)` — its identity is the
*fact itself*. Relations are the unit of **supersession** and **contradiction** and carry the
**bi-temporal** windows. **Evidence** is the many-to-many join from relations back to the claims
that support or contradict them — where corpus redundancy collapses into `evidence_count` (a free
confidence/salience signal, D2). **One claim may yield several relations, and one relation may be
evidenced by many claims** (`concepts.md` §2): the same `claim_id` therefore legitimately appears in
several `relation_evidence` rows for *different* `relation_id`s; uniqueness is per `(relation_id,
claim_id)` only.

Objects are **always entities** (entity→entity). Attribute / single-entity / literal facts ("Acme's
headcount is 600") yield **no relation**; under **D43** they become **observations** (§9.A) — a
separate, untyped, entity-anchored layer with the same bi-temporal validity, so non-relational facts
get first-class temporal validity and supersession too. Time is bi-temporal edge metadata, **never** a
predicate or Date-entity (D18).

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- relations — distinct bi-temporal facts (D2/D3). The (entity_id,predicate) blocking key for
-- supersession is the composite index below; it is small (distinct facts, not assertions) —
-- what makes supersession affordable at scale (concepts §6). The canonical fact LABEL and its
-- one current derived embedding live on this row (D94). Not partitioned.
--
-- "Live belief" = invalidated_at IS NULL (transaction-time), regardless of valid_until: a
-- believed-historical fact ("Alice worked at Acme 2020-2022", valid_until set, invalidated_at NULL)
-- is still currently believed. status is a GENERATED mirror of invalidated_at so validity has
-- exactly one authoritative home (D6) and cannot drift.
--
-- Uniqueness: a GiST EXCLUSION constraint forbids two BELIEVED, non-contradictory relations with
-- the same (s,p,o) AND OVERLAPPING valid-time windows. This is more correct than a partial unique
-- index: it permits re-occurring facts with non-overlapping windows (Alice worked at Acme twice)
-- and permits contradictions (carved out via contradiction_group), while forbidding duplicate
-- overlapping beliefs. Evidence-collapse (D2) finds the believed relation whose window covers the
-- new claim's time.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE relations (
  relation_id     uuid PRIMARY KEY,            -- the fact's identity; provenance handle in graph/P1 projections
  deployment_id   uuid NOT NULL REFERENCES deployments,
  subject_entity_id uuid NOT NULL,             -- canonical subject (composite FK below; only canonical entities enter relations/graph — p2 §2)
  predicate       text NOT NULL,               -- governed predicate; composite FK below
  object_entity_id uuid NOT NULL,              -- canonical object (entity→entity only; literals stay in claims — D2)
  -- bi-temporality (concepts §5): two clocks, different questions.
  valid_from      timestamptz,                 -- VALID-time start: when the fact began holding in the world (NULL = unknown/always)
  valid_until     timestamptz,                 -- VALID-time end: closed by supersession when the fact stops holding ("Alice left Acme")
  ingested_at     timestamptz NOT NULL DEFAULT now(), -- TRANSACTION-time: when the system first believed this fact
  invalidated_at  timestamptz,                 -- TRANSACTION-time: when the system learned it was superseded (NULL = still believed)
  evidence_count  integer NOT NULL DEFAULT 0,  -- cached count of DISTINCT DOCUMENT LINEAGES with current-testimony supporting claims (D54 — invariant under re-extraction/version churn/intra-doc repetition); confidence/salience signal (D2 refined)
  contradict_count integer NOT NULL DEFAULT 0, -- cached count of distinct current-testimony lineages contradicting (same D54 rule, stance=contradicts)
  confidence      real,                        -- aggregate confidence over evidence (not an extraction-time guess — concepts §3)
  contradiction_group uuid,                    -- shared id when two live relations contradict and can't be adjudicated — retrieval shows both sides (concepts §4)
  status          relation_status GENERATED ALWAYS AS  -- DERIVED mirror of invalidated_at (single validity home, D6): active iff invalidated_at IS NULL, else invalidated
                    (CASE WHEN invalidated_at IS NOT NULL THEN 'invalidated'::relation_status ELSE 'active'::relation_status END) STORED,
  -- fact label (D94): the human-readable semantic input; regenerated only on material adjudication change.
  fact_label      text,                        -- "Alice Novak works at Acme as VP of Engineering"
  fact_label_version text,                     -- LOGICAL FK → pipeline_component_versions (fact_labeler)
  embedding vector(1536),                      -- D94 disposable vector for fact_label
  embedding_model text,                        -- configured model that produced embedding
  embedding_input_policy_version text,         -- exact fact-label rendering policy
  embedding_text_hash text,                    -- hash of exact embedded fact_label
  normalizer_version text NOT NULL,            -- LOGICAL FK → pipeline_component_versions (normalizer); replay-on-rebuild
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, relation_id),          -- composite-FK target (tenancy isolation, §0)
  FOREIGN KEY (deployment_id, predicate) REFERENCES predicates (deployment_id, predicate) ON UPDATE CASCADE,
  FOREIGN KEY (deployment_id, subject_entity_id) REFERENCES entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, object_entity_id)  REFERENCES entities (deployment_id, entity_id),
  CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from),
  CHECK (invalidated_at IS NULL OR invalidated_at >= ingested_at),  -- can't un-learn before learning
  CHECK (num_nonnulls(embedding, embedding_model, embedding_input_policy_version, embedding_text_hash) IN (0, 4) AND (embedding IS NULL OR fact_label IS NOT NULL)),
  -- At most one BELIEVED, non-contradictory relation per (s,p,o) with overlapping world-time:
  EXCLUDE USING gist (
    deployment_id WITH =, subject_entity_id WITH =, predicate WITH =, object_entity_id WITH =,
    tstzrange(valid_from, valid_until) WITH &&
  ) WHERE (invalidated_at IS NULL AND contradiction_group IS NULL)
);
COMMENT ON TABLE relations IS
  'E3 distinct bi-temporal facts (D2/D3). Identity = (subject,predicate,object) + validity interval; the unit of supersession/contradiction. evidence_count caches corpus redundancy as a confidence signal (D2). status is a generated mirror of invalidated_at (validity has one home, D6). The GiST EXCLUDE forbids overlapping duplicate beliefs while allowing re-occurring facts and carved-out contradictions. fact_label is authority metadata; its one current derived embedding is stored on this row (D94).';
-- The supersession blocking key (D4) — small, distinct facts; THE index that makes supersession
-- detection affordable (concepts §6):
CREATE INDEX ix_relations_block_subj ON relations (deployment_id, subject_entity_id, predicate, object_entity_id);
CREATE INDEX ix_relations_block_obj  ON relations (deployment_id, object_entity_id, predicate);  -- reverse blocking ("who works_at acme?")
CREATE INDEX ix_relations_predicate  ON relations (deployment_id, predicate);
CREATE INDEX ix_relations_contradiction ON relations (contradiction_group) WHERE contradiction_group IS NOT NULL;
CREATE INDEX ix_relations_live       ON relations (deployment_id, subject_entity_id) WHERE invalidated_at IS NULL;
CREATE INDEX ix_relations_embedding_hnsw ON relations
  USING hnsw (embedding vector_cosine_ops);
```

> **Contradiction insert protocol (concepts §4; resolves the §17 spike on the EXCLUDE WHERE
> clause).** When the adjudicator cannot resolve a conflict between two same-`(s,p,o)` facts with
> overlapping windows (murky dates), both stay live with a shared `contradiction_group`. Because the
> EXCLUDE constraint ignores rows where `contradiction_group IS NOT NULL`, the second open row must
> be inserted **with its `contradiction_group` already set** (assigned in the same transaction that
> detects the conflict and stamps the group onto the existing row too). The constraint therefore
> never sees two live `contradiction_group IS NULL` rows for the same overlapping `(s,p,o)`.

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- relation_evidence — the many-to-many join claims ⇄ relations (D2). "Where corpus redundancy goes
-- to die": 200 documents asserting the same fact = one relation + 200 rows here. ~10⁸ rows.
--
-- Partitioned by HASH(relation_id), NOT by ingest month — because every hot access is by
-- relation_id (hydration) and the evidence-once invariant is on (relation_id, claim_id). With the
-- partition key = relation_id: relation hydration prunes to ONE partition, AND a real
-- PRIMARY KEY (relation_id, claim_id) enforces "a claim evidences a relation at most once" in-DB
-- (so relations.evidence_count cannot be inflated by a retry — a re-link is an ON CONFLICT no-op).
-- This is D23's evidence-join policy. Hash partitions are STATIC (64 migration-created children;
-- a measured starting point), so no pg_partman rolling-window is needed. The claim_id reverse lookup
-- ("which relations does this claim evidence")
-- scans all partitions but is the cold path. FKs remain logical (D23 btree-only/write-amplification).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE relation_evidence (
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  relation_id     uuid NOT NULL,               -- LOGICAL FK → relations; HASH partition key
  claim_id        uuid NOT NULL,               -- LOGICAL FK → claims; the asserting claim (immutable evidence). One claim may evidence MANY relations.
  doc_id          uuid NOT NULL,               -- LOGICAL FK → documents (the claim's LINEAGE, denormalized write-once) — makes the D54 recount a single-table scan per fact (F7)
  stance          evidence_stance NOT NULL,    -- supports | contradicts (concepts §3/§4)
  normalizer_version text NOT NULL,            -- LOGICAL FK → pipeline_component_versions; which normalizer linked them
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (relation_id, claim_id)          -- evidence-once, DB-enforced (partition key relation_id is included); re-link via ON CONFLICT DO NOTHING is a no-op
) PARTITION BY HASH (relation_id);
COMMENT ON TABLE relation_evidence IS
  'Many-to-many evidence links (D2). Corpus redundancy collapses here into relations.evidence_count. Partitioned by HASH(relation_id) so relation hydration prunes to one partition and PRIMARY KEY (relation_id, claim_id) enforces evidence-once in-DB (D23; §17 item 1 resolved). claim_id reverse lookup scans all partitions (cold path). Logical FKs (D23).';
-- 64 static hash children created by the migration — required, or inserts fail (F5); mirrors observation_evidence:
DO $$ BEGIN
  FOR i IN 0..63 LOOP
    EXECUTE format('CREATE TABLE relation_evidence_p%s PARTITION OF relation_evidence '
                   'FOR VALUES WITH (MODULUS 64, REMAINDER %s);', i, i);
  END LOOP;
END $$;
CREATE INDEX ix_relevidence_claim ON relation_evidence (claim_id);  -- reverse lookup: relations a claim evidences (all-partition scan)

-- ─────────────────────────────────────────────────────────────────────────
-- relation_adjudications — append-only supersession/contradiction transcript (D3/D4). Records WHY a
-- relation's window closed, a contradiction was flagged, or a merge proposed — by which cascade
-- rung, with what confidence/evidence. Makes the non-deterministic adjudication replayable on
-- rebuild (D7) and answers "why did valid_until close on 2026-01-15?". Real composite FK; the
-- deletion GC retires (not deletes) relations referenced here so the audit trail survives (§13).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE relation_adjudications (
  adjudication_id uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  relation_id     uuid NOT NULL,               -- the relation acted upon (composite FK below)
  related_relation_id uuid,                    -- the other relation in a supersede/contradict pair, if any (composite FK below)
  outcome         adjudication_outcome NOT NULL, -- add | noop | supersede | contradict | same_as_merge_proposal (D4 write-time outcomes)
  method          adjudication_method NOT NULL,  -- novelty_gate | exact | fuzzy | embedding | small_model | frontier_llm (cheap-first cascade, D4)
  confidence      real,
  triggering_claim_id uuid,                     -- LOGICAL FK → claims; the new claim that triggered adjudication
  features        jsonb,                        -- scores/rationale the decision used (audit); scrubbed on hard-forget (§13)
  adjudicator_version text NOT NULL,            -- LOGICAL FK → pipeline_component_versions
  decided_by      decision_actor NOT NULL DEFAULT 'auto',
  decided_at      timestamptz NOT NULL DEFAULT now(),
  superseded_by   uuid REFERENCES relation_adjudications, -- a later adjudication that overrode this one
  FOREIGN KEY (deployment_id, relation_id)         REFERENCES relations (deployment_id, relation_id),
  FOREIGN KEY (deployment_id, related_relation_id) REFERENCES relations (deployment_id, relation_id)
);
COMMENT ON TABLE relation_adjudications IS
  'Append-only supersession/contradiction decision log (D3/D4). Explains every window closure / contradiction flag / merge proposal, by cascade rung + evidence; replayed on P2 rebuild and used for "what did we believe at T / why" audits.';
CREATE INDEX ix_adjud_relation ON relation_adjudications (relation_id);
CREATE INDEX ix_adjud_live     ON relation_adjudications (relation_id) WHERE superseded_by IS NULL;
```

## 9.A E3 — observations: non-graph facts with temporal validity (D43)

An **observation** is a believed fact about **one entity** whose object is a *value or statement*, not
another entity — "Acme's headcount is 600", "Acme's FY2023 revenue was \$5M". It is the non-graph
sibling of a relation: same bi-temporal windows, same evidence-collapse, but **anchored to a resolved
entity** and **not typed by any governed attribute vocabulary**. Design + worked examples:
`observations_design.md` (D43).

The contrast with `relations` is deliberate and is the whole point of D43:

- A relation's supersession slot is the exact `(subject, predicate, object)` key; an observation has **no
  typed slot** — the supersession blocking key is just the **resolved entity**, and the
  same-thing/supersede-vs-coexist decision is made by the **adjudicator** (entity-block → semantic-narrow
  → cheap-first cascade, D4), not by a schema constraint. So there is **no `value_domain`/`cardinality`
  registry and no typed EXCLUDE arm here** and **no DB-level uniqueness/overlap guard.**
- **"Never silently resolve" is a binding adjudicator contract, not a schema invariant — and the design
  is honest that this is policy, not a DDL guarantee.** The binding rules (verdict-critical, eval-gated):
  (a) a `supersede` (capping a prior `valid_until`) is allowed **only** on a *positively matched* prior
  with adjudicator margin above an explicit threshold, and **every** cap writes an
  `observation_adjudications` reason row; (b) below threshold, or on any incomplete comparison, the
  outcome **must** be `coexist`/`new`, never `supersede`; (c) conflicting values for the **same property
  and same period** (both matched *semantically* from `statement` — no typed column) always coexist
  under a shared `contradiction_group`. A contradiction precision/recall **eval gate**
  on the golden set is an acceptance criterion for shipping the adjudicator (the E2/E3 eval harness,
  `questions.md`). Net: a wrong call degrades to *coexist* (both surfaced) — never a silent overwrite.
- **Recall, precisely.** The entity block makes **all** of an entity's live observations *available*
  (exact key — no clustering can hide one). For a hub entity, semantic similarity only **orders** which
  to compare first; because `supersede` requires a *positive* match (above), a prior that top-k ranking
  skips results at worst in a **duplicate coexisting observation**, never a wrong supersede. So the only
  residual cost of imperfect narrowing is a redundant row to reconcile later — the safe direction.
- Observations **never project to the graph** (D18 — a value is not a node); their
  natural rows carry current semantic search state over `statement`/label and
  remain entity-anchored timelines.

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- observations — non-graph facts about ONE entity (D43): entity-anchored, bi-temporal, UNTYPED. The
-- supersession blocking key is subject_entity_id alone (exact, exhaustive per entity); the same-slot /
-- supersede-vs-coexist judgment is the adjudicator's (D4 cascade), fail-safe to coexist. No governed
-- attribute vocabulary, no value_domain/cardinality, NO structured value/period columns, no typed
-- EXCLUDE — the value AND any reporting period live in `statement` and are matched SEMANTICALLY, exactly
-- like the property (consistent with the untyped design). status is a GENERATED mirror of invalidated_at
-- (one validity home, D6). The observation label is authority metadata and its one current embedding lives on this row (D94).
-- THE NO-CAP RULE (D43): only a CHANGING EFFECTIVE STATE (headcount/balance/status) is capped on
-- valid-time when superseded; a MEASUREMENT / FIXED-PERIOD figure ("FY2023 revenue") is NEVER capped —
-- it doesn't stop being true at period-end, stays open, and conflicting same-period figures coexist.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE observations (
  observation_id  uuid PRIMARY KEY,            -- the observation's identity; provenance handle in P1
  deployment_id   uuid NOT NULL REFERENCES deployments,
  subject_entity_id uuid NOT NULL,            -- the ANCHOR + supersession blocking key (a resolved canonical entity); composite FK below
  statement       text NOT NULL,              -- canonical NL form of the observed fact ("Acme's headcount is 600", "Acme's FY2023 revenue was $5M"); natural semantic-search text (D94). The VALUE and any reporting period live HERE — there is no structured value/period column (D43 lean); the adjudicator reads them semantically, like the property.
  -- bi-temporality (concepts §5): two clocks, WORLD-VALIDITY OF THE BELIEF.
  valid_from      timestamptz,                 -- VALID-time start: when the belief began holding in the world (NULL = unknown/always); seeded from the claim's asserted validity (D41)
  valid_until     timestamptz,                 -- VALID-time end. NO-CAP RULE (D43): capped ONLY when a CHANGING EFFECTIVE STATE (headcount/balance/status) is superseded by a later value. A MEASUREMENT / FIXED-PERIOD figure ("FY2023 revenue") is NEVER capped here — it doesn't stop being true at period-end; it stays open and conflicting same-period figures coexist. The adjudicator decides state-vs-measurement from `statement` (semantic), not a typed column. (observations_design.md §3)
  ingested_at     timestamptz NOT NULL DEFAULT now(), -- TRANSACTION-time: when the system first believed it
  invalidated_at  timestamptz,                 -- TRANSACTION-time: when learned wrong (NULL = still believed). NOT used to "end" a fact — that's valid_until.
  evidence_count  integer NOT NULL DEFAULT 0,  -- cached count of DISTINCT current-testimony LINEAGES supporting (D54 — mirrors relations)
  contradict_count integer NOT NULL DEFAULT 0, -- cached count of distinct current-testimony lineages contradicting (D54). NB: conflicting OBSERVATIONS are tracked via contradiction_group, a different concept.
  confidence      real,                        -- aggregate confidence over evidence
  contradiction_group uuid,                    -- shared id when two live observations conflict and both must stand (concepts §4)
  status          relation_status GENERATED ALWAYS AS  -- DERIVED mirror of invalidated_at (single validity home, D6)
                    (CASE WHEN invalidated_at IS NOT NULL THEN 'invalidated'::relation_status ELSE 'active'::relation_status END) STORED,
  obs_label       text,                        -- human-readable semantic input (often = statement); semantic blocking + retrieval
  obs_label_version text,                      -- LOGICAL FK → pipeline_component_versions (labeler)
  embedding vector(1536),                      -- D94 disposable vector for current observation search text
  embedding_model text,                        -- configured model that produced embedding
  embedding_input_policy_version text,         -- exact observation-input rendering policy
  embedding_text_hash text,                    -- hash of exact embedded obs_label/statement
  normalizer_version text NOT NULL,            -- LOGICAL FK → pipeline_component_versions; replay-on-rebuild
  adjudicator_version text,                    -- LOGICAL FK → pipeline_component_versions (supersession/contradiction adjudicator, D4)
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, observation_id),       -- composite-FK target (tenancy isolation, §0)
  FOREIGN KEY (deployment_id, subject_entity_id) REFERENCES entities (deployment_id, entity_id),
  CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from),
  CHECK (invalidated_at IS NULL OR invalidated_at >= ingested_at), -- can't un-learn before learning
  CHECK (num_nonnulls(embedding, embedding_model, embedding_input_policy_version, embedding_text_hash) IN (0, 4))
  -- NOTE: intentionally NO EXCLUDE / uniqueness constraint — there is no typed slot to key one on;
  -- supersession + evidence-collapse are adjudicated (entity-block + semantic + cascade), and
  -- "both-stand" is the safe default.
);
COMMENT ON TABLE observations IS
  'D43 non-graph fact layer: a believed value/statement about ONE entity (entity-anchored, bi-temporal, UNTYPED). Sibling of relations; never projects to the graph (D18). The value AND any reporting period live in `statement` (no structured value/period columns); the adjudicator matches same-entity + same-property + same-period + value-compatibility SEMANTICALLY. Supersession is adjudicated by entity-blocking + the D4 cascade (no typed slot, no EXCLUDE); "never silently resolve" is a binding adjudicator contract (supersede only on a positively-matched prior above margin, with a persisted reason; else coexist) + an eval gate, NOT a schema invariant. NO-CAP RULE: only a changing effective state is capped on valid-time; a measurement/fixed-period figure is never capped and conflicting same-period figures coexist. status is a generated mirror of invalidated_at (one validity home, D6); the label is authority metadata and its one current derived embedding is stored on this row (D94).';
-- The supersession blocking key (D4): all live observations for an entity (exact + exhaustive per entity):
CREATE INDEX ix_observations_block ON observations (deployment_id, subject_entity_id) WHERE invalidated_at IS NULL;
CREATE INDEX ix_observations_entity ON observations (deployment_id, subject_entity_id);  -- full history incl. capped/invalidated
CREATE INDEX ix_observations_contradiction ON observations (contradiction_group) WHERE contradiction_group IS NOT NULL;
CREATE INDEX ix_observations_embedding_hnsw ON observations
  USING hnsw (embedding vector_cosine_ops);

-- ─────────────────────────────────────────────────────────────────────────
-- observation_evidence — many-to-many join claims ⇄ observations (D2), mirroring relation_evidence.
-- Corpus redundancy collapses here into observations.evidence_count. HASH(observation_id). Like
-- relation_evidence (§9), FKs are LOGICAL (D23 — btree-only at 10^8 scale); the integrity guarantee
-- here is the PRIMARY KEY (evidence-once), NOT referential FKs. (Evidence-collapse of the same value
-- into ONE observation is adjudicated upstream — best-effort — not enforced by this table, which only
-- dedups a given (observation, claim) pair.)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE observation_evidence (
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  observation_id  uuid NOT NULL,               -- LOGICAL FK → observations; HASH partition key
  claim_id        uuid NOT NULL,               -- LOGICAL FK → claims; the asserting claim (immutable evidence). One claim may evidence many observations.
  stance          evidence_stance NOT NULL,    -- supports | contradicts (concepts §3/§4)
  normalizer_version text NOT NULL,            -- LOGICAL FK → pipeline_component_versions
  doc_id          uuid NOT NULL,               -- LOGICAL FK → documents (the claim's lineage, write-once) — D54 recount without cross-partition claim joins (F7)
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (observation_id, claim_id)       -- evidence-once, DB-enforced; re-link via ON CONFLICT DO NOTHING is a no-op
) PARTITION BY HASH (observation_id);
-- 64 static hash children created by the migration, same as relation_evidence (§9):
DO $$ BEGIN
  FOR i IN 0..63 LOOP
    EXECUTE format('CREATE TABLE observation_evidence_p%s PARTITION OF observation_evidence '
                   'FOR VALUES WITH (MODULUS 64, REMAINDER %s);', i, i);
  END LOOP;
END $$;
COMMENT ON TABLE observation_evidence IS
  'Many-to-many evidence links claims ⇄ observations (D2/D43), mirroring relation_evidence. Corpus redundancy collapses into observations.evidence_count. HASH(observation_id), 64 static partitions; PRIMARY KEY (observation_id, claim_id) enforces evidence-once. Logical FKs (D23).';
CREATE INDEX ix_obsevidence_claim ON observation_evidence (claim_id);  -- reverse lookup (all-partition scan, cold path)

-- ─────────────────────────────────────────────────────────────────────────
-- observation_adjudications — append-only supersession/contradiction transcript (D3/D4), mirroring
-- relation_adjudications. Records WHY an observation's window closed / a contradiction was flagged, by
-- cascade rung + confidence; makes the non-deterministic adjudication replayable on rebuild (D7).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE observation_adjudications (
  adjudication_id uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  observation_id  uuid NOT NULL,               -- the observation acted upon (composite FK below)
  related_observation_id uuid,                 -- the other observation in a supersede/contradict pair, if any (composite FK below)
  outcome         adjudication_outcome NOT NULL, -- add | noop | supersede | contradict | same_as_merge_proposal (D4)
  method          adjudication_method NOT NULL,  -- novelty_gate | exact | fuzzy | embedding | small_model | frontier_llm (cheap-first cascade, D4)
  confidence      real,
  triggering_claim_id uuid,                     -- LOGICAL FK → claims; the new claim that triggered adjudication
  features        jsonb,                        -- scores/rationale (audit); scrubbed on hard-forget (§13)
  adjudicator_version text NOT NULL,            -- LOGICAL FK → pipeline_component_versions
  decided_by      decision_actor NOT NULL DEFAULT 'auto',
  decided_at      timestamptz NOT NULL DEFAULT now(),
  superseded_by   uuid REFERENCES observation_adjudications, -- a later adjudication that overrode this one
  FOREIGN KEY (deployment_id, observation_id)         REFERENCES observations (deployment_id, observation_id),
  FOREIGN KEY (deployment_id, related_observation_id) REFERENCES observations (deployment_id, observation_id)
);
COMMENT ON TABLE observation_adjudications IS
  'Append-only supersession/contradiction decision log for observations (D3/D4/D43), mirroring relation_adjudications. Explains every window closure / contradiction flag by cascade rung + evidence; replayed on rebuild and used for "what did we believe at T / why" audits.';
CREATE INDEX ix_obsadjud_observation ON observation_adjudications (observation_id);
CREATE INDEX ix_obsadjud_live        ON observation_adjudications (observation_id) WHERE superseded_by IS NULL;
```

---

## 10. Graph analytics writeback (D11) & projection snapshots (D7, D40)

P1/P2/P3 are **derived projections**; two things flow *back into* Postgres because the rest of the
system reads them: (1) **community detection + centrality** (D11 — Louvain/Leiden run externally;
PageRank/K-Core/WCC natively), serving K1 refresh triggers and salience priors; and (2) a
**snapshot registry** for P1/P2/P3 (D7/D40) — observability, validation gating, reader
coordination. Per-entity analytics are **regenerated each rebuild** and **garbage-collected** when
their snapshot is superseded — without GC these tables grow by ~entities-per-cycle forever at the
6-hourly cadence, which contradicts their disposable intent.

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- projection_snapshots — registry of P1/P2/P3 rebuilds (D7/D40). Immutable versioned snapshots;
-- a validation gate must pass before is_latest flips (failure ⇒ previous snapshot keeps serving).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE projection_snapshots (
  snapshot_id     uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  plane           projection_plane NOT NULL,   -- P1_search | P2_graph | P3_corpusfs
  version         text NOT NULL,               -- monotonic/timestamped snapshot version (also the GCS path segment)
  gcs_uri         text NOT NULL,               -- gs://…/snapshots/<version>/
  status          snapshot_status NOT NULL DEFAULT 'building', -- building | validating | published | superseded | failed
  is_latest       boolean NOT NULL DEFAULT false, -- the pointer readers follow; exactly one per (deployment,plane)
  row_counts      jsonb,                       -- per-table counts validated against Postgres (D7 validation gate)
  validation      jsonb,                       -- validation report (pass/fail per check)
  built_from_watermark timestamptz,            -- max ingested_at included — bounds projection staleness (freshness SLA = cadence, D7)
  built_at        timestamptz NOT NULL DEFAULT now(),
  published_at    timestamptz,
  UNIQUE (deployment_id, plane, version),
  UNIQUE (deployment_id, snapshot_id)           -- composite-FK target (tenancy isolation, §0)
);
COMMENT ON TABLE projection_snapshots IS
  'Registry of immutable P1/P2/P3 snapshots (D7/D40). Validation gates is_latest; old snapshots are free point-in-time debugging artifacts. Mirrors the GCS latest pointer for operators/workers.';
CREATE UNIQUE INDEX ux_snapshot_latest ON projection_snapshots (deployment_id, plane) WHERE is_latest;

-- ─────────────────────────────────────────────────────────────────────────
-- communities — detected entity communities per P2 snapshot (D11). Recomputed each rebuild.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE communities (
  community_id    uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  snapshot_id     uuid NOT NULL,               -- which (P2_graph) rebuild produced this partition (composite FK below)
  label           text,                        -- optional human/LLM topic label (K1 hint)
  size            integer NOT NULL,            -- member count; an emerging giant community can signal over-merge (health metric, registries §10)
  algorithm       community_algorithm NOT NULL,-- leiden | louvain (external pass) — D11
  detected_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, community_id),         -- composite-FK target
  FOREIGN KEY (deployment_id, snapshot_id) REFERENCES projection_snapshots (deployment_id, snapshot_id) ON DELETE CASCADE
);
COMMENT ON TABLE communities IS
  'Externally-detected communities per P2 graph snapshot (D11). Feed K1 refresh triggers ("claims in community C changed") and salience; recomputed each rebuild and GC''d with their snapshot (graph stays a projection). FK references must be a plane=P2_graph snapshot (invariant; the writer only inserts P2 snapshots here).';
CREATE INDEX ix_communities_snapshot ON communities (snapshot_id);

-- ─────────────────────────────────────────────────────────────────────────
-- entity_graph_metrics — per-entity centrality + community membership per snapshot (D11). PageRank
-- = salience prior; degree feeds entities.graph_degree (blast-radius) — refreshed ONLY from the
-- currently-published is_latest P2 snapshot, after the validation gate passes, so the auto-merge
-- gate is never computed from a stale/unvalidated projection. component_id is a synthetic
-- per-snapshot WCC grouping label (NOT an FK).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE entity_graph_metrics (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  entity_id       uuid NOT NULL,               -- composite FK below
  snapshot_id     uuid NOT NULL,               -- composite FK below (a P2_graph snapshot)
  community_id    uuid,                        -- this entity's community in this snapshot (composite FK below)
  pagerank        double precision,            -- salience prior for retrieval rank and K topic prioritization
  degree          integer,                     -- relation degree — copied into entities.graph_degree from the latest published snapshot only
  k_core          integer,                     -- k-core number (hub-ness)
  component_id    uuid,                        -- synthetic per-snapshot weakly-connected-component label (NOT an FK; scoped to snapshot_id)
  computed_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, entity_id, snapshot_id),
  FOREIGN KEY (deployment_id, entity_id)    REFERENCES entities (deployment_id, entity_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, snapshot_id)  REFERENCES projection_snapshots (deployment_id, snapshot_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, community_id) REFERENCES communities (deployment_id, community_id) ON DELETE SET NULL (community_id)
);
COMMENT ON TABLE entity_graph_metrics IS
  'Per-entity graph analytics written back from each P2 rebuild (D11): PageRank salience, degree (blast-radius), k-core, community, WCC. Read by retrieval ranking, K topic prioritization, and ER health checks. GC''d when its snapshot is superseded. entities.graph_degree is refreshed only from the published is_latest snapshot.';
CREATE INDEX ix_egm_entity   ON entity_graph_metrics (entity_id);
CREATE INDEX ix_egm_snapshot ON entity_graph_metrics (snapshot_id);
```

**Retention/GC (D7 disposability).** On a successful publish (the `is_latest` flip), the rebuild's
finalize step **deletes `communities` and `entity_graph_metrics` rows of superseded snapshots**
(keeping the latest, or latest-N for debugging) — the `ON DELETE CASCADE` from
`projection_snapshots` does this when a superseded snapshot row is pruned, or the finalize step
deletes them directly. The lightweight `projection_snapshots` registry rows and the GCS snapshot
objects are retained as point-in-time debugging artifacts independently of this per-entity GC.

---

## 10.A P2 projection views — the LadybugDB COPY contract (D7, D43, D44)

The P2 graph is a dumb projection (D6) rebuilt from Postgres (D7). To keep that projection **mechanical
and auditable**, the entire Postgres→LadybugDB boundary is a set of read-only **views** — one per graph
node/rel table — that pre-cast, pre-filter, survivor-redirect, and pre-aggregate, so the LadybugDB side is
a trivial `COPY <T> FROM SQL_QUERY('pg', 'SELECT * FROM v_graph_<t>')` (or the Parquet equivalent — same
views). Full analysis + LadybugDB-side DDL: `plan/analysis/ladybug_translation_research/SYNTHESIS.md`;
decision **D44**. The three transforms (timestamptz→naive-UTC, enum→text, drop graph-irrelevant columns)
and two correctness rules (merge-redirect, keep-retracted) live here, **not** in the graph worker.

The graph consumes a thin slice: `entities`→`Entity`, `documents`→`Document`, `relations`→`RELATES`,
`mentions⋈resolution_decisions`→`MENTIONED_IN`, `document_crossrefs`→`DOC_CROSSREF`,
`documents.document_entity_id`→`IS_DOCUMENT`. **Observations and claims never project** (D43/D18 — a value
is not a graph node). One snapshot = one deployment (D16: separate Postgres per deployment), so the views
take no `deployment_id` parameter.

```sql
-- Resolve every entity id to its final merge SURVIVOR. A merge is a REDIRECT, not a rewrite
-- (entities.merged_into; entity_id never reused) and relations are NOT re-pointed in PG — so endpoints
-- MUST be redirected here, or the rebuild silently drops every edge touching a merged entity. Cycle-safe
-- (merged_into acyclicity is not schema-enforced); the rebuild's validation gate (below) aborts the
-- snapshot if any retained endpoint fails to resolve to exactly one emitted survivor.
CREATE VIEW v_graph_survivor AS
WITH RECURSIVE chain(entity_id, cur, depth) AS (
  SELECT entity_id, entity_id, 0 FROM entities
  UNION ALL
  SELECT c.entity_id, e.merged_into, c.depth + 1
  FROM chain c JOIN entities e ON e.entity_id = c.cur
  WHERE e.merged_into IS NOT NULL AND c.depth < 64          -- cycle / runaway guard
)
SELECT entity_id,
       (SELECT cur FROM chain x WHERE x.entity_id = chain.entity_id ORDER BY depth DESC LIMIT 1) AS survivor
FROM chain GROUP BY entity_id;   -- survivor = the terminal (merged_into IS NULL) node of each chain

-- Nodes: survivors only; cast timestamps. Graph-derived metrics (pagerank/graph_degree) are NOT loaded —
-- they are computed POST-load (D11); reprojecting a stored value is circular. entity_id stays native UUID
-- (PK verified in LadybugDB source/tests; STRING fallback = entity_id::text, applied uniformly to the PK
-- AND every endpoint).
CREATE VIEW v_graph_entities AS
SELECT entity_id AS id, type, canonical_name AS name, normalized_name,
       profile_summary AS summary, (created_at AT TIME ZONE 'UTC') AS created_at
FROM   entities WHERE status = 'active';                    -- merged/retired entities are not nodes

CREATE VIEW v_graph_documents AS
SELECT d.doc_id AS id, d.title, d.source_uri,
       (dv.published_at AT TIME ZONE 'UTC')::date AS published_at  -- the CURRENT version's date (D55); NULL when unset
FROM   documents d
LEFT JOIN document_versions dv
       ON dv.deployment_id = d.deployment_id AND dv.version_id = d.current_version_id
WHERE  d.deleted_at IS NULL;   -- lineages project; a lineage mid-ingest (no current version yet) projects with NULL date (F2)

-- Edges: endpoints are the FIRST TWO columns (FROM, TO), survivor-redirected and guarded so both
-- endpoints exist as emitted nodes (else COPY-REL throws). Keep EVERY invalidated edge by default for
-- transaction-time as-of (D69): there is no invalidation-age filter and a closed valid-time fact is
-- unaffected. Endpoint joins are the retention boundary. Parallel edges with distinct relation_id are
-- PRESERVED (no blind DISTINCT — same-(s,p,o) collapse is E3's job, D43).
CREATE VIEW v_graph_relates AS
SELECT s1.survivor AS "from", s2.survivor AS "to",
       r.relation_id, r.predicate, r.fact_label AS fact,
       r.evidence_count::bigint AS evidence_count, r.contradict_count::bigint AS contradict_count,
       r.confidence::float8 AS confidence, r.contradiction_group,
       (r.valid_from AT TIME ZONE 'UTC') AS valid_from, (r.valid_until AT TIME ZONE 'UTC') AS valid_until,
       (r.ingested_at AT TIME ZONE 'UTC') AS ingested_at, (r.invalidated_at AT TIME ZONE 'UTC') AS invalidated_at
FROM   relations r
JOIN   v_graph_survivor s1 ON s1.entity_id = r.subject_entity_id
JOIN   v_graph_survivor s2 ON s2.entity_id = r.object_entity_id
JOIN   entities e1 ON e1.entity_id = s1.survivor AND e1.status = 'active'   -- endpoint emitted as a node
JOIN   entities e2 ON e2.entity_id = s2.survivor AND e2.status = 'active';
-- relations.status (GENERATED) is DROPPED — liveness is derived in Cypher (invalidated_at IS NULL), D6.

CREATE VIEW v_graph_mentioned_in AS                          -- aggregate: no (entity,doc) base table
SELECT s.survivor AS "from", m.doc_id AS "to",
       COUNT(*)::bigint AS mention_count, (MIN(m.created_at) AT TIME ZONE 'UTC') AS first_seen
FROM   mentions m
JOIN   resolution_decisions rd ON rd.mention_id = m.mention_id AND rd.superseded_by IS NULL   -- live verdict
JOIN   v_graph_survivor s ON s.entity_id = rd.entity_id
JOIN   entities e ON e.entity_id = s.survivor AND e.status = 'active'
WHERE  EXISTS (SELECT 1 FROM documents d WHERE d.doc_id = m.doc_id AND d.deleted_at IS NULL)
GROUP  BY s.survivor, m.doc_id;

CREATE VIEW v_graph_crossref AS
SELECT from_doc_id AS "from", to_doc_id AS "to", kind::text AS kind, context
FROM   document_crossrefs WHERE to_doc_id IS NOT NULL;       -- nullable = cited-but-not-ingested → no edge

CREATE VIEW v_graph_is_document AS                           -- bridge: Document-typed Entity ↔ its E0 doc
SELECT s.survivor AS "from", d.doc_id AS "to"
FROM   documents d
JOIN   v_graph_survivor s ON s.entity_id = d.document_entity_id
JOIN   entities e ON e.entity_id = s.survivor AND e.status = 'active'
WHERE  d.document_entity_id IS NOT NULL AND d.deleted_at IS NULL;
```

**Rebuild gate (D44).** Before the snapshot pointer-swap (D7), the worker asserts: (1) every retained edge
endpoint resolved to exactly one emitted survivor (no merge cycle, no dangling endpoint); (2) per-table
graph row-count vs. view row-count match. A failure **aborts** the snapshot rather than publishing a
corrupt graph. `COPY <Node|Rel> FROM SQL_QUERY('pg', …)` is verified; the committed transport stays the
**Parquet hop** (D7) until cross-DB attach throughput at 10⁷–10⁸ is measured — both transports consume
these same views.

---

## 11. K plane — the compile control plane (D1, D12, D45–D47, D73)

The K plane is **markdown whose source of truth is the git repo**, backed up independently (D1;
irreducibly the human-authored content — D46). Postgres holds the **control plane** of the D45
compile system: what pages exist (`knowledge_artifacts`), what each page is *about* (the routing
rules + their inverted key index), what each page *rests on* (the citations), why structure
changed (the planner's append-only decisions), what each compile did (the compile transcript),
who else is *listening* (subscriptions, page watches, and the dispatch transcript — the trigger
surface, k_layers §5), and the debounced trigger queue (D12). Everything the driver computes —
staleness, deletion reach, orphan evidence, dispatch batches — is SQL over these tables; git
holds only content. Binding design: `k_layers_design.md`.

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- knowledge_artifacts — the PG handle on a K-plane git file (D1, D45–D47). page_kind is the
-- D46 ownership contract: 'compiled' bodies are machine-owned (regenerated when stale; human
-- input only via the curation sidecar); 'authored' bodies are human/agent-owned (never
-- machine-written; evidence changes raise review flags, not recompiles). parent_artifact_id is
-- the tree the driver compiles in dependency order (children before parents — parents consume
-- child page_summary values, never re-read child files). inputs_hash is the D45 staleness key.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_artifacts (
  artifact_id     uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  layer           knowledge_layer NOT NULL,    -- built-in configuration uses K1/K2; K3 is an inert legacy enum label (D73)
  page_kind       knowledge_page_kind NOT NULL, -- compiled | authored (D46)
  scope_id        uuid,                        -- non-null for K2 scope artifacts (composite FK below)
  parent_artifact_id uuid,                     -- tree/DAG position (composite FK below)
  git_path        text NOT NULL,               -- path of the markdown file in the K repo
  curation_path   text,                        -- compiled pages: the human curation sidecar file (D46)
  kind            text,                        -- 'summary' | 'profile' | 'principle' | 'decision_log' | 'model_page' | ...
  page_summary    text,                        -- writer-emitted 2–3 sentence abstract; what PARENT compiles consume
  content_hash    text,                        -- hash of the git file at last compile/sync (drift + quarantine detection, D46)
  inputs_hash     text,                        -- D45 staleness key: hash(candidate evidence IDs + validity fingerprints,
                                               --   curation sidecar, child summaries, shared model page, writer prompt/model version)
  writer_version  text,                        -- LOGICAL FK → pipeline_component_versions (knowledge_writer); NULL on authored pages
  last_compiled_at timestamptz,
  status          knowledge_artifact_status NOT NULL DEFAULT 'active', -- active | stale | quarantined | tombstoned
  UNIQUE (deployment_id, git_path),
  UNIQUE (deployment_id, artifact_id),          -- composite-FK target
  FOREIGN KEY (deployment_id, scope_id) REFERENCES scopes (deployment_id, scope_id) ON DELETE SET NULL (scope_id),
  FOREIGN KEY (deployment_id, parent_artifact_id) REFERENCES knowledge_artifacts (deployment_id, artifact_id),
  CHECK (page_kind = 'compiled' OR writer_version IS NULL)  -- authored bodies are never machine-written (D46)
);
COMMENT ON TABLE knowledge_artifacts IS
  'PG handle + compile state per K-plane git file (D45–D47). page_kind = the D46 ownership contract (compiled: machine-owned, regenerated; authored: human-owned, review-flagged). inputs_hash = the D45 mechanical staleness key (stale iff recomputed hash differs). parent_artifact_id = the compile DAG. Git holds content; PG holds control.';
CREATE INDEX ix_kartifacts_scope  ON knowledge_artifacts (scope_id);
CREATE INDEX ix_kartifacts_parent ON knowledge_artifacts (parent_artifact_id) WHERE parent_artifact_id IS NOT NULL;
CREATE INDEX ix_kartifacts_stale  ON knowledge_artifacts (deployment_id) WHERE status = 'stale';

-- ─────────────────────────────────────────────────────────────────────────
-- knowledge_plan_runs — one terminal row per stock-harness planner or reflection session.
-- The transcript is archived before its decisions are parsed, so malformed output and failed
-- sessions remain inspectable under D52 just like writer failures. Reflection is a separate
-- run_kind because D53 requires it to use a different model family from the planner.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_plan_runs (
  run_id                 uuid PRIMARY KEY,
  deployment_id          uuid NOT NULL REFERENCES deployments,
  scope_id               uuid,
  run_kind               text NOT NULL CHECK (run_kind IN ('planner','reflection')),
  trigger                plan_trigger NOT NULL,
  component_version      text NOT NULL,       -- LOGICAL FK → the planner/reflection component version
  input_hash             text NOT NULL,       -- canonical structural snapshot consumed by the run
  session_transcript_uri text NOT NULL,       -- immutable complete harness transcript
  status                 text NOT NULL CHECK (status IN ('succeeded','failed')),
  failure                text,                -- full traceback on terminal failure, never str(error)-only
  tokens                 integer CHECK (tokens IS NULL OR tokens >= 0),
  cost_usd               numeric CHECK (cost_usd IS NULL OR cost_usd >= 0),
  completed_at           timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (deployment_id, scope_id) REFERENCES scopes (deployment_id, scope_id) ON DELETE CASCADE,
  CHECK ((status = 'failed') = (failure IS NOT NULL))
);
CREATE INDEX ix_kplan_runs_deployment ON knowledge_plan_runs (deployment_id, completed_at DESC);

-- ─────────────────────────────────────────────────────────────────────────
-- knowledge_plan_decisions — the planner's append-only STRUCTURE transcript (D45; the D33
-- ledger discipline applied to structure). Low-blast-radius decisions auto-apply; restructures
-- above the band queue as 'proposed' for the deployment's accountable reviewer — a human or a
-- designated reviewer agent (the D24 pattern; k_layers §7). Exception: convert_kind in the
-- authored→compiled direction NEVER auto-applies (author confirmation).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_plan_decisions (
  decision_id     uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  scope_id        uuid,                        -- composite FK below
  action          plan_action NOT NULL,        -- create_page | split_page | merge_pages | move_page | retire_page | adjust_rule | convert_kind
  payload         jsonb NOT NULL,              -- paths, rule diffs, rationale text
  trigger         plan_trigger NOT NULL,       -- orphan_evidence | size_overflow | community_change | reflection | writer_suggestion | human
  planner_version text NOT NULL,               -- LOGICAL FK → pipeline_component_versions (knowledge_planner)
  status          plan_decision_status NOT NULL DEFAULT 'proposed', -- proposed | applied | rejected
  plan_run_id     uuid REFERENCES knowledge_plan_runs (run_id), -- NULL only for explicit human/quarantine decisions
  confidence      numeric CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  blast_radius    integer CHECK (blast_radius IS NULL OR blast_radius >= 0), -- deterministic affected-page/candidate count
  expected_impact numeric CHECK (expected_impact IS NULL OR expected_impact >= 0), -- blast_radius × (1 - confidence)
  reviewed_by     text,
  reviewed_at     timestamptz,
  application_commit text,                     -- git revision first reflecting an applied decision; NULL while pending
  decided_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (deployment_id, scope_id) REFERENCES scopes (deployment_id, scope_id) ON DELETE CASCADE,
  CHECK ((reviewed_by IS NULL) = (reviewed_at IS NULL))
);
COMMENT ON TABLE knowledge_plan_decisions IS
  'Append-only planner transcript (D45): every create/split/merge/move/retire/rule change with trigger + rationale. Reviewable, revertible structure — the opposite of emergent session behavior. Blast-radius-gated auto-apply (D24 pattern).';
CREATE INDEX ix_kplan_proposed ON knowledge_plan_decisions (deployment_id, decided_at) WHERE status = 'proposed';

-- ─────────────────────────────────────────────────────────────────────────
-- knowledge_quarantines — a direct edit to a compiled body is retained verbatim as proposed
-- curation and the page is excluded from compilation. Triage has exactly three durable exits:
-- copy the proposal into the git sidecar and recompile, adopt the page as authored, or reject
-- the edit. The proposal remains in this ledger after resolution, so regeneration cannot erase
-- the author's work or the fact that the ownership boundary was crossed.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_quarantines (
  quarantine_id          uuid PRIMARY KEY,
  decision_id            uuid NOT NULL UNIQUE REFERENCES knowledge_plan_decisions (decision_id),
  deployment_id          uuid NOT NULL REFERENCES deployments,
  artifact_id            uuid NOT NULL,
  recorded_content_hash  text NOT NULL,
  detected_content_hash  text NOT NULL,
  proposed_sidecar_entry text NOT NULL,
  status                 text NOT NULL DEFAULT 'proposed'
    CHECK (status IN ('proposed','curation_accepted','adopted','rejected')),
  resolution_note        text,
  curation_content_hash  text,
  detected_at            timestamptz NOT NULL DEFAULT now(),
  resolved_at            timestamptz,
  FOREIGN KEY (deployment_id, artifact_id)
    REFERENCES knowledge_artifacts (deployment_id, artifact_id) ON DELETE CASCADE,
  CHECK ((status = 'proposed') = (resolved_at IS NULL)),
  CHECK (status <> 'curation_accepted' OR curation_content_hash IS NOT NULL)
);
CREATE UNIQUE INDEX ux_kquarantine_open_artifact
  ON knowledge_quarantines (artifact_id) WHERE status = 'proposed';

-- ─────────────────────────────────────────────────────────────────────────
-- knowledge_subscriptions — the DISPATCH consumers (the K trigger surface, k_layers §5; the
-- E→K signal channel D42 deferred, now designed). Binds match criteria — an owned routing rule
-- (below) and/or page watches — to a workflow endpoint. Dispatch is DEBOUNCED per subscription
-- and delivered with the D12 worker discipline (Cloud Tasks, retries, DLQ, idempotent
-- consumers); the payload carries the delta (matched evidence + citation/validity changes),
-- never a bare ping. The memory system only notifies + serves context — it never runs the
-- subscriber's logic (subscribers are operating agents outside the system boundary).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_subscriptions (
  subscription_id uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  scope_id        uuid,                        -- optional owning scope (composite FK below)
  name            text NOT NULL,               -- e.g. 'planning-module-replan'
  workflow_endpoint text NOT NULL,             -- the agentic workflow invoked on dispatch (Cloud Tasks target)
  debounce_seconds integer NOT NULL,           -- per-subscription batch window (starting point, measure — k_layers §11 spike 7)
  status          subscription_status NOT NULL DEFAULT 'active', -- active | paused | retired
  created_by      text,                        -- registering agent/human
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, name),
  FOREIGN KEY (deployment_id, scope_id) REFERENCES scopes (deployment_id, scope_id) ON DELETE CASCADE
);
COMMENT ON TABLE knowledge_subscriptions IS
  'Dispatch consumers of the K trigger surface (k_layers §5). Match criteria = owned routing rules and/or page watches; consequence = debounced workflow invocation carrying the evidence delta. The E→K signal channel D42 deferred.';

-- ─────────────────────────────────────────────────────────────────────────
-- knowledge_page_rules — the ROUTING RULES (D45): the recorded answer to "what evidence
-- belongs to this OWNER". Mechanical: each rule_kind has ONE fixed SQL evaluation
-- (k_layers_design.md §5); an LLM chooses the rule, SQL evaluates it — no LLM on the routing
-- path (the D9 rule applied to routing). An owner may hold several rules (union). The owner is
-- EXACTLY ONE of a page or a subscription, and the rule's CONSEQUENCE derives from it:
-- compiled page → stale/recompile; authored page → an 'authored_review' flag (a watch rule,
-- D46); subscription → dispatch (k_layers §5). Page-owned rules require a plan decision;
-- subscription-owned rules are accounted by the subscription's created_by.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_page_rules (
  rule_id         uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  artifact_id     uuid,                        -- the page this rule feeds (XOR subscription_id; composite FK below)
  subscription_id uuid REFERENCES knowledge_subscriptions (subscription_id) ON DELETE CASCADE,
  rule_kind       knowledge_rule_kind NOT NULL,
  params          jsonb NOT NULL,              -- e.g. {"entity_id": …, "predicates": ["works_for"], "layers": ["relations","observations","claims"]}
  status          ontology_status NOT NULL DEFAULT 'active',
  plan_decision_id uuid REFERENCES knowledge_plan_decisions (decision_id),  -- who created it and why (page-owned rules)
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (num_nonnulls(artifact_id, subscription_id) = 1),          -- exactly one owner
  CHECK ((artifact_id IS NOT NULL) = (plan_decision_id IS NOT NULL)), -- plan-decided iff page-owned
  FOREIGN KEY (deployment_id, artifact_id) REFERENCES knowledge_artifacts (deployment_id, artifact_id) ON DELETE CASCADE
);
COMMENT ON TABLE knowledge_page_rules IS
  'D45 routing rules, owned by a page XOR a subscription (the trigger surface, k_layers §5). Closed kind set; params per kind; union across an owner''s rules. manual = the editorial escape hatch. Evidence matching NO rule in a scope = orphan → a planner trigger.';

-- ─────────────────────────────────────────────────────────────────────────
-- knowledge_rule_keys — the routing INVERTED INDEX (D45): rule match keys materialized so that
-- routing a batch of new evidence is one indexed lookup (the D4 block-first philosophy — exact
-- keys narrow; nothing expensive runs corpus-wide). Derived-membership rules (entity_subtree
-- via the part_of closure; community via the D11 writeback) get their keys RE-MATERIALIZED by
-- the driver when their inputs change — both arrive as ordinary evidence events.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_rule_keys (
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments (tenancy-leading lookup index below)
  rule_id         uuid NOT NULL REFERENCES knowledge_page_rules (rule_id) ON DELETE CASCADE,
  key_kind        rule_key_kind NOT NULL,      -- entity | predicate | community | doc_source
  key_value       text NOT NULL,               -- uuid-as-text for entity/community; predicate name; doc source
  PRIMARY KEY (rule_id, key_kind, key_value)
);
CREATE INDEX ix_krule_keys_lookup ON knowledge_rule_keys (deployment_id, key_kind, key_value);
COMMENT ON TABLE knowledge_rule_keys IS
  'Inverted index over rule match keys (D45): new evidence → its E-plane labels (entities, predicate, community, doc source) → the rules (page- or subscription-owned) it affects, in one lookup. The key set of a derived-membership rule is re-materialized when its inputs change.';

-- ─────────────────────────────────────────────────────────────────────────
-- knowledge_page_watches — PAGE-LEVEL watch targets (k_layers §5): subscribe to another page's
-- recompiles instead of re-declaring its rules (the paired-workbench ergonomics — a gap
-- analysis watches the compiled to-be page it judges, and stays subscribed as the planner
-- adjusts that page's rules). Watcher is EXACTLY ONE of an authored page (consequence: an
-- 'authored_review' flag) or a subscription (consequence: dispatch). Same edge type as the
-- compile DAG's parent→child dependency, different consequence.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_page_watches (
  watch_id        uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL,
  watcher_artifact_id uuid,                    -- an authored page … (XOR subscription_id)
  subscription_id uuid REFERENCES knowledge_subscriptions (subscription_id) ON DELETE CASCADE,
  watched_artifact_id uuid NOT NULL,           -- the page whose recompiles are watched
  CHECK (num_nonnulls(watcher_artifact_id, subscription_id) = 1),
  FOREIGN KEY (deployment_id, watcher_artifact_id) REFERENCES knowledge_artifacts (deployment_id, artifact_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, watched_artifact_id) REFERENCES knowledge_artifacts (deployment_id, artifact_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX ux_kwatch ON knowledge_page_watches (watcher_artifact_id, subscription_id, watched_artifact_id) NULLS NOT DISTINCT;
CREATE INDEX ix_kwatch_watched ON knowledge_page_watches (watched_artifact_id);
COMMENT ON TABLE knowledge_page_watches IS
  'Page-level watches (k_layers §5): a watcher (authored page XOR subscription) subscribes to a watched page''s recompiles. Consequence derives from the watcher: flag or dispatch. Synced from authored frontmatter (watch: page:<path>) or registered with a subscription.';

-- ─────────────────────────────────────────────────────────────────────────
-- knowledge_dispatches — the append-only DISPATCH transcript (k_layers §5). The driver
-- coalesces a subscription's matches over its debounce window into ONE row whose payload
-- carries the delta (matched evidence IDs, citation/validity changes, affected page refs);
-- delivery is at-least-once (Cloud Tasks, D12 retries/DLQ) — subscriber workflows must be
-- idempotent per dispatch_id.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_dispatches (
  dispatch_id     uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  subscription_id uuid NOT NULL REFERENCES knowledge_subscriptions (subscription_id) ON DELETE CASCADE,
  payload         jsonb NOT NULL,              -- {matched_evidence_ids, deltas, page_refs} — the delta, never a bare ping
  status          refresh_status NOT NULL DEFAULT 'pending', -- pending | running | done | failed
  enqueued_at     timestamptz NOT NULL DEFAULT now(),
  delivered_at    timestamptz
);
CREATE INDEX ix_kdispatch_pending ON knowledge_dispatches (deployment_id, status) WHERE status = 'pending';
COMMENT ON TABLE knowledge_dispatches IS
  'Append-only dispatch transcript (k_layers §5): one debounce-coalesced row per subscription window, delta-carrying payload, at-least-once delivery with idempotent consumers (keyed by dispatch_id). Makes the E→K trigger surface auditable like every other non-deterministic boundary (D33 discipline).';

-- ─────────────────────────────────────────────────────────────────────────
-- knowledge_compilations — the append-only COMPILE transcript (D45; D33 for content).
-- uncited_count is the K-plane analogue of the Selection-drop ledger: rule-matched evidence the
-- writer chose not to cite is counted, so "why isn''t fact X on this page?" has an answer.
-- git_commit is two-phase: the row is written before the push, the sha stamped after; startup
-- reconciles repo HEAD against the newest committed rows.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_compilations (
  compilation_id  uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  artifact_id     uuid NOT NULL,               -- composite FK below
  inputs_hash     text NOT NULL,               -- the candidate snapshot this compile consumed (D45 idempotency)
  candidate_count int NOT NULL,                -- rule-matched evidence offered to the writer
  cited_count     int NOT NULL,                -- offered fact/claim-coordinate candidates covered by accepted citations
  uncited_count   int NOT NULL,                -- offered but not used (auditable coverage gap)
  claims_cut_count int NOT NULL DEFAULT 0 CHECK (claims_cut_count >= 0), -- D54 claim coordinates omitted by the settings-bound cap
  suggestions     jsonb NOT NULL DEFAULT '[]'::jsonb, -- typed, inert planner suggestions returned by the writer
  evidence_added  int NOT NULL DEFAULT 0,      -- citation-set delta vs the previous compile
  evidence_removed int NOT NULL DEFAULT 0,
  evidence_invalidated int NOT NULL DEFAULT 0,
  writer_version  text NOT NULL,               -- LOGICAL FK → pipeline_component_versions (knowledge_writer)
  tokens          integer, cost_usd numeric,   -- cost metering (requirements: per-layer budgets)
  session_transcript_uri text,                 -- archived writer-session transcript (GCS) — the residual read-audit log for stock-harness writers (k_layers §7); NULL when a session left no transcript
  git_commit      text,
  compiled_at     timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (deployment_id, artifact_id) REFERENCES knowledge_artifacts (deployment_id, artifact_id) ON DELETE CASCADE
);
CREATE INDEX ix_kcompilations_artifact ON knowledge_compilations (artifact_id, compiled_at DESC);
COMMENT ON TABLE knowledge_compilations IS
  'Append-only compile transcript per page (D45): inputs snapshot, candidate/cited/uncited counts, citation deltas, versions, cost, commit. Makes compiles idempotent (inputs_hash), auditable, and replayable-from-storage like every non-deterministic stage (D7/D33).';

-- ─────────────────────────────────────────────────────────────────────────
-- knowledge_artifact_evidence — the CITATIONS: page ⇄ evidence links (D45/D46; authored grounding +
-- deletion cascade). A BINDING output contract, not self-reported provenance: on a compiled page
-- the driver REPLACES these rows from the writer's returned citations each compile; on an
-- authored page they are synced from the page's frontmatter (`cites:`). Evidence-change
-- staleness, authored review flags, and deletion reach are reverse lookups through this table.
-- A single link targets EXACTLY ONE of stable claim coordinate/relation/doc (the others NULL)
-- — so a surrogate PK + pair/exactly-one CHECKs + a NULL-tolerant unique index, NOT an
-- all-columns PK (PK columns cannot be NULL). D54 forbids raw extraction-generation claim IDs
-- here: re-extracting unchanged testimony must not churn a page's binding citations.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_artifact_evidence (
  evidence_link_id uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  artifact_id     uuid NOT NULL,               -- composite FK below, ON DELETE CASCADE
  claim_lineage_id uuid,                       -- document lineage half of the D54-stable claim coordinate
  claim_chunk_content_hash text,               -- chunk-content half; required iff claim_lineage_id is present
  relation_id     uuid,                        -- composite FK below, ON DELETE CASCADE (real, relations is not partitioned)
  doc_id          uuid,                        -- LOGICAL FK → documents
  role            knowledge_evidence_role NOT NULL, -- supports | contradicts | cites; generic provenance roles for any page
  CHECK ((claim_lineage_id IS NULL) = (claim_chunk_content_hash IS NULL)),
  CHECK (num_nonnulls(claim_lineage_id, relation_id, doc_id) = 1), -- claim pair counts as one target
  FOREIGN KEY (deployment_id, artifact_id) REFERENCES knowledge_artifacts (deployment_id, artifact_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, relation_id) REFERENCES relations (deployment_id, relation_id) ON DELETE CASCADE
);
COMMENT ON TABLE knowledge_artifact_evidence IS
  'Citations (D45/D46/D54): the ONE stable claim coordinate/relation/document each link rests on, role supports|contradicts|cites. Binding writer output on compiled pages (replaced per compile); frontmatter-synced on authored pages. Drives exact incremental refresh (D12), authored review flags (D46), and the deletion cascade. Exactly-one-target enforced by CHECK; surrogate PK because the targets are nullable alternatives.';
-- NULL-tolerant dedup (one link per (artifact, target, role)); NULLS NOT DISTINCT treats the two
-- NULL targets as equal so the populated one is the discriminator:
CREATE UNIQUE INDEX ux_kae_link ON knowledge_artifact_evidence (artifact_id, role, claim_lineage_id, claim_chunk_content_hash, relation_id, doc_id) NULLS NOT DISTINCT;
CREATE INDEX ix_kae_claim_coordinate ON knowledge_artifact_evidence (claim_lineage_id, claim_chunk_content_hash) WHERE claim_lineage_id IS NOT NULL;
CREATE INDEX ix_kae_relation ON knowledge_artifact_evidence (relation_id) WHERE relation_id IS NOT NULL;
CREATE INDEX ix_kae_doc      ON knowledge_artifact_evidence (doc_id)      WHERE doc_id IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────
-- knowledge_refresh_queue — the debounced trigger queue (D12) the D45 driver consumes at cycle
-- start. Evidence-change events carry the changed IDs; the driver ROUTES them to pages via
-- knowledge_rule_keys + the citation reverse lookup — artifact_id is therefore NULL on evidence
-- batches (routing is mechanical, no longer "decide which" by an LLM at processing time) and
-- set only on targeted triggers (authored_review, tombstone, manual). not_before is the plain
-- debounce delay — the hot-file rationale is gone (D45: the root index is just the last DAG
-- target, compiled once per cycle). This is domain-trigger aggregation, not D61 task delivery:
-- once the driver materializes a K job, its unlaned processing_state row and that row's
-- not_before are authoritative under D67.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_refresh_queue (
  refresh_id      uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  artifact_id     uuid,                        -- composite FK below (nullable); NULL on evidence batches — routing via rule keys (D45)
  scope_id        uuid,                        -- composite FK below
  trigger         knowledge_trigger NOT NULL,  -- evidence_changed | community_changed | debounce_timer | manual | tombstone | authored_review
  payload         jsonb,                       -- e.g. {changed_relation_ids:[…]} | {changed_claim_ids:[…]} | {community_id:…} | {deleted_doc_id:…}
  not_before      timestamptz,                 -- debounce delay — don't process before this
  status          refresh_status NOT NULL DEFAULT 'pending', -- pending | running | done | failed
  enqueued_at     timestamptz NOT NULL DEFAULT now(),
  processed_at    timestamptz,
  FOREIGN KEY (deployment_id, artifact_id) REFERENCES knowledge_artifacts (deployment_id, artifact_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, scope_id)    REFERENCES scopes (deployment_id, scope_id) ON DELETE CASCADE
);
COMMENT ON TABLE knowledge_refresh_queue IS
  'Debounced domain-trigger queue for the K compile driver (D12/D45), not D61 delivery state. Evidence batches route mechanically; authored_review surfaces D46 flags; this not_before coalesces triggers, while a materialized K job is delivered only from its unlaned processing_state row (D67).';
CREATE INDEX ix_krefresh_runnable ON knowledge_refresh_queue (deployment_id, status, not_before) WHERE status = 'pending';
```

---

## 11.A Closed assured-operation registry (D50/D87)

The four platform operations are named, versioned compositions of zero-LLM query
authorities and remain **registry rows, not independent code**
(`retrieval_design.md` §4). The registry is closed: customer behavior belongs in
the saved-query registry, and inserting a row cannot mint a fifth MCP tool. D87
replaces the pre-release `retrieval_recipes` target table outright; no
compatibility view or old-name rows remain. Bootstrap owns the rows and exposes
exactly one active canonical version of each enum member per deployment.

The result-contract field keeps D49 honest: three operations return one complete
`Envelope`; `answer_context` returns `ContextBundle/v1` and therefore has no
single output grain. `result_schema` stores the exact closed wire schema; for
`fact_context` it requires D87's discriminated `temporal_scope` union. The
database constrains the four name/contract/intent/grain tuples, while the
registration linter proves the canonical result schema and execution-plan
properties—for example, `fact_context` uses validity-filtered
relation/observation authorities and `answer_context` contains only the two
canonical child operations. `resolve_entity` retains its v1 fact grain: the
identity registry is an authority, not source testimony.

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- assured_operations — the four frozen platform plans as registry data (D50/D87).
-- Surfaces render the one active canonical version of each name.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE assured_operations (
  operation_id    uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  name            assured_operation_name NOT NULL,
  description     text NOT NULL,               -- rendered into the MCP tool description (D50)
  parameters      jsonb NOT NULL,              -- typed parameter schema (JSON-Schema form)
  result_schema   jsonb NOT NULL,              -- exact closed wire schema; includes fact_context temporal_scope
  execution_plan  jsonb NOT NULL,              -- closed union: primitive_chain, or answer_context's exact two-child operation_bundle
  result_contract assured_result_contract NOT NULL,
  output_grain    assured_output_grain,        -- null only for ContextBundle/v1
  answer_intent   assured_answer_intent NOT NULL,
  version         integer NOT NULL DEFAULT 1,  -- recall@k measured per (name, version) — regressions attributable (D22)
  status          ontology_status NOT NULL DEFAULT 'active',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, name, version),
  CHECK (
    (name = 'resolve_entity' AND result_contract = 'envelope'
      AND output_grain = 'fact' AND answer_intent = 'identity') OR
    (name = 'testimony_context' AND result_contract = 'envelope'
      AND output_grain = 'evidence' AND answer_intent = 'testimony') OR
    (name = 'fact_context' AND result_contract = 'envelope'
      AND output_grain = 'fact' AND answer_intent = 'facts') OR
    (name = 'answer_context' AND result_contract = 'context_bundle_v1'
      AND output_grain IS NULL AND answer_intent = 'combined_context')
  )
);
COMMENT ON TABLE assured_operations IS
  'D50/D87: exactly four platform-owned assured operations. MCP renders the active canonical versions; customer patterns live in saved_queries. Name/contract/intent/grain tuples are mechanically closed and the registration linter validates each execution plan.';
CREATE UNIQUE INDEX uq_assured_operation_active
  ON assured_operations (deployment_id, name) WHERE status = 'active';
```

---

## 12. Partitioning & partition pruning (D23)

Exactly **eight** append-only E-plane tables are partitioned for scale. Six use monthly RANGE
children managed by `pg_partman`; two evidence joins use 64 static HASH children created by the
schema migration. Monthly RANGE caps btree size, makes detaching an old hot month a partition
operation, and aligns with projection archival (p2 §8). HASH partitioning puts every evidence row
for one fact in one child and makes the evidence-once primary key enforceable. This estate is
binding. A load test (registries §11 spike 4, sized against ungated volume per D25) may justify a
documented revision to the monthly cadence or the measured HASH starting count of 64.

| Parent | Strategy and key | Primary key | Child management | FK policy |
|---|---|---|---|---|
| `mentions` | monthly RANGE (`created_at`) | (`mention_id`, `created_at`) | `pg_partman` | logical |
| `resolution_decisions` | monthly RANGE (`decided_at`) | (`decision_id`, `decided_at`) | `pg_partman` | logical |
| `chunks` | monthly RANGE (`created_at`) | (`chunk_id`, `created_at`) | `pg_partman` | logical |
| `chunk_claims` | monthly RANGE (`created_at`) | (`chunk_id`, `claim_id`, `created_at`) | `pg_partman` | logical |
| `claim_extraction_decisions` | monthly RANGE (`decided_at`) | (`decision_id`, `decided_at`) | `pg_partman` | logical |
| `testimony_currency_events` | monthly RANGE (`occurred_at`) | (`event_id`, `occurred_at`) | `pg_partman` | logical |
| `relation_evidence` | HASH (`relation_id`) | (`relation_id`, `claim_id`) | 64 static migration-created children | logical |
| `observation_evidence` | HASH (`observation_id`) | (`observation_id`, `claim_id`) | 64 static migration-created children | logical |

`pg_partman` creates and maintains only the six monthly RANGE families. It does not manage either
HASH family; migrations create all remainders `0..63` before inserts can reach those parents.
`entities` and `aliases` are deliberately **not** partitioned (≤10⁷, the blocking targets whose
single-column GIN trigram/phonetic indexes span the deployment, D23/D68). `relations`,
`observations`, and their adjudication tables are not partitioned: distinct facts and their
decision transcripts are far smaller than assertion-grain evidence.

**Partition pruning on ID lookups.** Hot queries over the remaining partitioned
families often select by id/parent (`mention_id → resolution_decisions`,
`doc_id → chunks`, `relation_id → relation_evidence`), which do *not* mention the
partition key, so a naive query scans every monthly partition's local index. The mitigation, applied
by the data-access layer: **UUIDv7 ids embed their creation timestamp**, and a child row's creation
time is closely correlated with its parent's ingest time, so the application derives a time bound
from the id (or from the parent's `ingested_at`) and adds it as a predicate (e.g.
`AND ingested_at BETWEEN $lo AND $hi`), pruning to 1–2 partitions. This works for the
ingest-time-correlated RANGE families (`mentions`, `chunks`, `chunk_claims`,
`claim_extraction_decisions`, `testimony_currency_events`, and — for the first-resolution pass —
`resolution_decisions`).

**`relation_evidence` is the exception, and is the reason it is hash-partitioned:** evidence for a
popular fact accrues over the fact's whole life, so an id-derived time bound would *not* prune a
`relation_id → evidence` lookup. Partitioning by `HASH(relation_id)` instead prunes relation
hydration to one partition *and* makes the real `PRIMARY KEY (relation_id, claim_id)` evidence-once
guarantee enforceable in-DB (a partitioned PK must include the partition key, which `relation_id` now
is). This is the D23 contract. The `claim_id` reverse lookup still fans across hash partitions, but
it is the cold path. `observation_evidence` applies the same policy to `observation_id`.

**Partition-key consequences in the DDL:** a partitioned table's PRIMARY KEY/UNIQUE must include the
partition key, so all six RANGE parents include their time key and the two HASH parents include
their fact identifier. The application treats each UUIDv7 alone as row identity (globally unique by
construction); the wider RANGE keys are the Postgres mechanical requirement. The evidence joins'
pair keys are also the intended evidence-once identity.

---

## 13. Deletion / forget cascade (D37, requirements_v3)

Removing an input propagates through every derived layer, and **hard delete** of the original bytes
must be supported (GDPR forget, D37). Deletion is executed by a **deletion worker in batches**
(the large tables are partitioned and logical-FK, so there is no single giant `ON DELETE CASCADE`
transaction); the real composite FKs on the smaller tables are the integrity backstop. Two modes:

### 13.1 Normal delete (remove a document; retain audit history)

1. **K tombstone first.** Before touching evidence, enqueue a `knowledge_refresh_queue` row with
   `trigger='tombstone'` carrying the doc/claim ids (found via `knowledge_artifact_evidence`), so
   the K driver recompiles affected **compiled** pages without the removed evidence and raises
   `authored_review` flags on **authored** pages that cited it (D45/D46; the tombstone signal,
   D37). Doing this first ensures the links are still present to discover.
2. **GCS**: purge the document's raw + artifacts objects.
3. **`documents` + `document_versions`** (D55 grains): deleting the **lineage** soft-tombstones
   it (`deleted_at`) and every version (`status='deleted'`, artifact URIs nulled, `deleted_at`);
   deleting **one version** tombstones only that version row (its claims' currency ends,
   `version_deleted` — the lineage continues) and purges its content object if no live version
   references it. Rows are **kept** (the `deleted` enum value and `ix_documents_live` stay
   meaningful, the logical-FK auditor distinguishes "forgotten" from "never existed", and
   crossref targets resolve sanely). The worker then clears the affected `document_sections`
   (cascade) and sets dependent `document_crossrefs.to_doc_id = NULL, resolved = false` while
   **retaining `raw_citation`** so the link can be re-resolved if the target is re-ingested.
4. **`chunks`** (logical FK): rows are **retained** (they are the occurrence record, D56/F4) but
   their P1 rows are deleted by the lifecycle transaction/repair path and are
   immediately ineligible through the authority join. The auditor ignores rows
   for documents with `deleted_at` set.
5. **`claims` are NOT deleted on a normal delete** (Codex review F12 — this is the fix that keeps
   "normal delete retains audit history" true): normal deletion **ends the claims' testimony
   currency** (`version_deleted` events; `is_current_testimony=false`) so they stop counting,
   stop surfacing in default search, and stop supporting current belief — while the assertion
   history (`claims_as_of`, audit opt-ins, adjudication transcripts) survives. Only **hard
   forget** (§13.2) scrubs claim texts/spans and the dependent `mentions`,
   `claim_extraction_decisions`, and `grounding_audits` content — S55's forgotten≡never-existed
   applies to hard forget alone. `relation_evidence`/`observation_evidence` rows are likewise
   retained as historical links (their claims are non-current, so counts exclude them).
6. **`relations`**: **not** deleted with one document's claims — a relation is a *shared* fact. The
   worker recomputes `evidence_count`/`contradict_count` (the D54 rule: `COUNT(DISTINCT doc_id)` over
   evidence rows whose claims are current testimony, per stance — the write-once `doc_id` on evidence
   rows makes this a single-table scan per fact; so duplicates
   cannot inflate it). A relation whose **current** support drops to zero via deletion is
   **closed** per the D54 source-acted rule (states: `valid_until` cap; measurements:
   `invalidated_at`; recorded `retracted_source_removal` adjudication — the `support_withdrawn`
   flag is exclusively the re-extraction path, never deletion). Where closure sets
   `invalidated_at`, the generated `status` becomes `invalidated` and the projection stops
   emitting it — it is **not physically deleted**, because `relation_adjudications` and
   `knowledge_artifact_evidence` reference it and the audit trail is retained. A
   `relation_adjudications` row records the retirement reason so it is distinguishable from a
   supersession in audit.
7. **`entities`**: not deleted; a separate GC **retires** (`status='retired'`) entities with zero
   surviving mentions. They are **not physically deleted**, because `merge_events`,
   `resolution_exclusions`, `relation_adjudications`, and `entity_graph_metrics` reference them and
   D21 mandates retaining merge history for un-merge. Retired entities are not emitted on rebuild.
8. **P1/P2/P3**: cascade "for free" — projections don't materialize removed/retired data on the next
   rebuild (D40).

### 13.2 Hard forget (GDPR — erase the bytes *and* the derived text)

**D74 / `hard_forget_design.md` is authoritative for orchestration, portable manifest shape,
active-store adapter hooks, and restore non-resurrection.** This section owns only the PostgreSQL
part. Normal delete already purges the GCS bytes and the document row's text-bearing URIs. A full
forget additionally **scrubs or deletes every source-bearing payload** that normal delete retains
for audit. The implementation owns one explicit column/table inventory, and the deterministic S55
test fails when a new source-bearing field is not classified. At minimum it covers:

- lineage/version/representation source refs, titles, URIs, errors, hashes that are not retained as
  content-free guards, sections, cross-reference citation text, representation metadata, and assets;
- chunks/occurrences, claims and their text/spans/added context, mentions and aliases exclusive to
  the lineage, extraction decisions, grounding/resolution decisions, review payloads, locators,
  audit rationales/features, and source-exclusive relation/observation evidence;
- source-exclusive observation values and entity names/profiles, while facts/entities with
  independent live support retain only that independently supported state;
- K refresh/compile/plan payloads and transcript URIs tied to the affected evidence (the object
  payloads themselves are purged through D74); and
- eval/golden/canary payloads derived from the forgotten lineage.

Because audit rows reference `relations`/`entities` via real composite FKs, a forget that physically
deletes a relation/entity must first delete or anonymize the referencing audit rows (or use the
retire-don't-delete handle from §13.1 and scrub every free-text field in place). The worker performs
this explicit pass after the normal currency/counting cascade. `forget_manifests` retains only the
portable manifest and irreversible ingest guards; the ordinary `processing_state` row records
execution and failure. Any `preparing` or accepted-incomplete row closes public/ordinary-work
admission until full cross-store verification succeeds; the authorized forget coordinator remains
able to call its internal purge services.

The logical-FK **auditor** (run periodically) catches orphans and worker-owned logical-uniqueness
violations, and ignores rows belonging to documents with `deleted_at` set (a delete in flight).
The evidence joins' pair primary keys enforce evidence-once directly, so the auditor does not
duplicate that check.

---

## 14. How a write flows through the schema (worked example)

From `concepts.md`'s running example — *Doc C (Jan 2026): "Alice Novak left Acme to found Beacon
Labs."*

1. **E0** `documents` row (`ingesting`→`ready`), `document_sections` rows; `processing_state` tracks
   each sub-worker; `cost_ledger` logs the OCR/structure calls (idempotent per
   `(processing_id, attempt, call_key)`).
2. **E1** `chunks` rows with offsets + `location_header`; normalized search
   text and one current vector land in `chunk_search`, keyed by deployment and
   `chunk_id`.
3. **E2 Selection** keeps the two verifiable propositions (any drop/edit → `claim_extraction_decisions`).
   Kept propositions → `claims` (`c3`, `c4`) with `source_span`, offsets, `added_context`,
   `anchor_ok`/`window_membership_ok` true (the CHECK); mentions of "Alice Novak", "Acme", "Beacon
   Labs" → `mentions` with `canonical_name_form`.
4. **Resolution** (T0–T4) writes `resolution_decisions` (method ∈ {T0,T3,T4_*,human}) linking each
   mention to an `entities` row via the `aliases` blocking indexes; "Beacon Labs" is new
   (`is_new_entity`).
5. **E3 normalize**: `c4` → `(alice, founded, beacon_labs)` — new `relations` row (the
   governed predicate is valid; endpoint classes are not a write gate), one `relation_evidence(supports)` row keyed
   `(relation_id, claim_id)` (a re-link is an `ON CONFLICT` no-op). `c3` triggers **supersession** on `(alice, works_for, acme)`: the
   blocking index `ix_relations_block_subj` finds it, the cascade adjudicates `supersede`, a
   `relation_adjudications` row records why, and the relation's `valid_until`/`invalidated_at` close
   (the GiST EXCLUDE permits this — the closed window no longer overlaps a live one; `c1`,`c2` claims
   untouched, D3). `status` flips to `invalidated` automatically (generated column).
6. **Projections**: next P2 rebuild re-points edges; `entity_graph_metrics`/`communities` write back
   (old snapshot's rows GC'd); `projection_snapshots` records the new version and flips `is_latest`
   after validation. `knowledge_refresh_queue` gets an `evidence_changed` event with the changed
   relation/claim IDs; the K driver routes it (rule keys + citation reverse lookup, D45) — Alice's
   and Acme's compiled pages go stale, and any authored page citing the employment fact gets an
   `authored_review` flag (D46).

---

## 15. Non-goals (scope boundaries, not deferrals)

- **No `graph_events` outbox / incremental-sync state.** P2 is rebuild-first (D7); incremental is a
  *documented alternative* (`p2_graph_design.md` §5), not built.
- **No value/salience-gate state** (`gate_decisions` / `document_extraction_state` /
  `salience_gate_versions`) — D25–D30 withdrawn. Junk-control is in-call E2 Selection
  (`claim_extraction_decisions`) + D2 redundancy-collapse + content-hash idempotency.
- **No `external_ids` table.** Resolution is registry-self-contained (D20); future internal/domain
  authoritative IDs would attach as *aliases*, never as the canonical `entity_id`.
- **Vectors are private derived PostgreSQL state (D94).** Claims, relations,
  observations, and entities carry one current vector on their natural row;
  chunks use the sole `chunk_search` sidecar. No opaque external-store
  references or generalized P1 mirror tables remain. HNSW/BM25 columns and
  indexes stay outside `memory_v1` and never become truth.
- **No exact document bodies in Postgres** (D37). Exact bodies remain in GCS;
  PostgreSQL holds offsets + URIs plus one normalized current chunk search copy
  in `chunk_search` for BM25 and hydration.
- **No fact/opinion/prediction claim type** — unattributed opinions are dropped at Selection;
  attributed stances are kept as ordinary claims → stance observations (D59); nothing is stored as a
  typed claim (§8 reconciliation note).
- **Non-relational facts now get structured validity + supersession (D43 — no longer a non-goal).** A
  fact that yields no relation ("Acme's headcount is 600"; "revenue \$5M for FY2023") becomes an
  **observation** (§9.A) carrying bi-temporal validity: a changing value supersedes (interval-capped),
  and incompatible **same-period** values ("\$5M" vs "\$7M" for FY2023) both stand under a shared
  `contradiction_group` — surfaced, never silently resolved. Adjudicated by entity-blocking + the D4
  cascade, fail-safe to coexist; **no governed attribute typing.** (This was previously a non-goal
  awaiting an "E3 proposition-fact layer"; the observation layer *is* that layer, kept deliberately
  untyped — see `observations_design.md`.)
- **No multi-interval / recurring / un-datable claim validity (D41).** The single
  `claim_valid_from`/`until` interval does not model recurrence ("every Q4") or anchor-events that
  can't be dated at extraction ("as of the merger"); D31 decomposition absorbs most multi-span
  sentences into atomic single-interval claims. The documented upgrade is an expressivity child table
  (btree-indexed, D23-restamped), built only on measured demand (§17).

---

## 16. Decision → table map

| Decision | Realized by |
|---|---|
| D1 split source of truth; versioned processing | `pipeline_component_versions`; K-plane provenance §11; `*_version` columns |
| D2 claims/relations distinct, M:N evidence | `claims`, `relations`, `relation_evidence` (+ `evidence_count`) |
| D3 supersession at relation level, bi-temporal | `relations` 4 temporal columns + GiST EXCLUDE; `relation_adjudications`; claims immutable (incl. their D41 asserted-validity interval) |
| D4 supersession blocking + cheap-first cascade | `ix_relations_block_subj/obj`; `relation_adjudications.method` (incl. `novelty_gate`) |
| D5 governed predicate registry + `other:` | `predicates` (`synonyms`, `tier='other'` upsert, `usage_count` funnel) |
| D6 graph is a projection; validity has one home | adjudicated validity only on `relations`; generated `status`; claim-validity is evidence, not a second home (D41); analytics writeback §10 |
| D7 rebuild-first; immutable snapshots | `projection_snapshots`; replay via `*_version` + decision ledgers; snapshot GC |
| D94 PostgreSQL-native P1 | one `chunk_search` sidecar; one current embedding on natural claim/relation/observation/entity rows; current-only `p1_search_channels` readiness; normalized joins for filters; no opaque cross-store refs or generalized mirror tables |
| D9 search/rerank; evidence-count + graph-distance | `relations.evidence_count`; `entity_graph_metrics.pagerank/degree` |
| D11 communities external → write back to PG | `communities`, `entity_graph_metrics` (+ GC) |
| D12 idempotency, retries, DLQ, debounced K triggers | `processing_state` identity/status/`attempts`/`max_attempts`; `cost_ledger`; `knowledge_refresh_queue` (retry ownership refined by D67) |
| D5/D15/D96 predicate vocabulary; no entity-class gate | `predicates`, `extension_packs`; dormant `entity_types` inventory is not identity authority; `predicate_signatures` is absent |
| D16 one graph, scope views | `scopes`, `scope_interests` |
| D17 T0–T4 cascade, block-loose/decide-tight | single-column blocking GIN indexes on `entities.normalized_name` and `aliases.normalized_lemma` (D68); `resolution_decisions.method` (CHECK excludes T1/T2); `resolver_versions` |
| D19 coref in-call | `mentions.canonical_name_form` (no coref model/table) |
| D20 no external authority | non-goal §15 |
| D21 clustering, reversibility, generic-id guard | `merge_events` (+ `trigger_lemmas`), `resolution_exclusions`, `generic_identifier_guard`, `superseded_by` |
| D22 golden set + eval | `golden_pairs` (+ `expected_blocking_tier`), `golden_claim_labels`, `eval_runs`, `canary_cases` |
| D23/D94 partition the big tables; btree-only; GIN on registry targets | §12's eight parents (6 monthly RANGE via `pg_partman`, 2 static HASH-64); claims are non-partitioned for one global current-testimony BM25/HNSW corpus; single-column `ix_entities_name_trgm`, `ix_aliases_lemma_trgm`, `ix_aliases_lemma_dm` |
| D24 cluster review queue | `review_queue` (band boundaries in `resolver_versions.tier_config`) |
| D25 no value gate | non-goal §15 |
| D31/D32 Claimify staged extraction + grounding | `claims` (`source_span`, `added_context`, grounding flags + gate CHECK), `grounding_audits` |
| D33 extraction decision ledger | `claim_extraction_decisions` |
| D35 Selection recall envelope | `claims.kept_flagged`, `selection_drop_reason`, `protected_class`, `golden_claim_labels` |
| D36/D37 E0 sub-workers (incl. crossref version), storage split | `documents` (URIs + all four sub-worker versions), `document_crossrefs.crossref_version` |
| D39 PageIndex sections + placement | `document_sections` (path/role/span/summary/placement) |
| D40 P3 corpus filesystem | `projection_snapshots (plane='P3_corpusfs')` + `document(_sections).placement*` |
| D41 claim-grain source-asserted validity | `claims.claim_valid_from/until/precision/kind` (immutable); `claims_as_of` recipe (evidence-only); same-statement normalized filter |
| D45 planned K compilation (planner/writer/driver; mechanical routing; manifest staleness) | `knowledge_page_rules`, `knowledge_rule_keys`, `knowledge_plan_decisions`, `knowledge_compilations`; `knowledge_artifacts.inputs_hash/page_summary/parent_artifact_id` |
| D46 compiled vs authored pages; ownership + quarantine | `knowledge_artifacts.page_kind/curation_path/content_hash` (+ `status='quarantined'`); citations binding in `knowledge_artifact_evidence`; authored review flags via `knowledge_refresh_queue ('authored_review')` |
| D47/D73 one K mechanism, N scopes; no shipped K3 tier | `knowledge_artifacts.layer` (K1/K2 used by built-in configuration; legacy K3 label inert); scope model page = an artifact all writer runs in that scope depend on; authored K2 pages hold principles and stances |
| E→K trigger surface (k_layers §5; the signal channel D42 deferred) | `knowledge_subscriptions`, `knowledge_page_watches`, `knowledge_dispatches`; rules owned by page XOR subscription; D42 `origin` guards re-ingested plans |
| D48 propose/dispose hydration | no tables — an API-layer contract over existing spine reads (by-ID re-verification; drop counts in the envelope) |
| D49 response envelope (grain / contradictions / freshness / negatives) | wire contract, not schema; the K freshness block reads `knowledge_artifacts` + `knowledge_compilations`; horizons read `projection_snapshots` |
| D50/D87 zero-LLM primitives; four closed assured operations | `assured_operations` (§11.A) + `assured_operation_name`/`assured_result_contract`/grain/intent enums; the canonical tuple CHECK |
| D51 filesystem-first mounts + consumption skill | `deployments.raw_bucket`/`content_objects.raw_uri` comments (raw mounted off-path, D37 refined); the skill is a shipped versioned artifact, not schema |
| D54 testimony currency + counting rule | `testimony_currency_events` (partitioned ledger + reconciliation idempotency key) + `claims.is_current_testimony` (cache); `evidence_count`/`contradict_count` redefined (distinct current lineages — write-once `doc_id` on evidence rows makes the recount single-table); `review_item_kind = 'support_withdrawn'` |
| D55 document lineages + versions | `documents` (lineage: `source_kind/source_ref`, `versioning_mode`, three-column current-version FK), `document_versions` (append-only; `source_modified_at` → `asserted_at`; `sync_cycle_id`), `content_objects`; `connector_sync_cycles` (the retract barrier); `adjudication_outcome='retracted_source_removal'` (living removal retracts — no review softener) |
| D56 content-addressed reuse | `chunks.chunk_content_hash` + `chunks.extraction_input_hash` (+ `ix_chunks_reuse`); `chunk_claims` — the exact claim-occurrence map (fresh + reused attachments) |
| D57 block substrate + blockizer; sections on the grid | `document_representations.blocks_uri` + `blockizer_version`; `document_sections.block_start/end`; `pipeline_component = 'blockizer'`; blocks live in `blocks.json` (sidecar), never as rows |
| D65 media: immutable representations + occurrence provenance | `document_representations` (immutable conversion outputs; route + component graph + output hashes; representation-addressed artifact paths) + `document_versions.current_representation_id` (swap-on-completion); `representation_id` on `document_sections`/`chunks` (the basis coordinate); `chunk_claims.derivation_kind`/`evidence_mode`/`source_locators` (occurrence-grain disclosure + locators, media_design §4–§6) |
| D58 chunk packing + multi-granularity retrieval | `chunks.block_start/end` + `chunk_content_hash` (= ordered block hashes); role filter joins chunk/section authority; no-overlap invariant is worker discipline, not DDL |
| D67 normalized queue route, due time, parking, retry/DLQ, and lane costs | `processing_lane` / `processing_defer_reason`; `processing_state.lane/not_before/defer_reason/attempts/max_attempts`; transactional `tr_processing_state_initial_wake`; `ix_procstate_due`; `cost_ledger.processing_id/attempt/call_key/lane` + per-call UNIQUE; `ix_cost_budget_window`; `payload` explicitly non-authoritative |
| D68 schema-/database-per-deployment | §0 tenancy contract; one deployment identity row; composite scoped keys retained as defense in depth; single-column `ix_entities_name_trgm`, `ix_aliases_lemma_trgm`, `ix_aliases_lemma_dm`; no `btree_gin` |
| D69 unbounded graph-edge retention + post-head deployment bootstrap | §10.A `v_graph_relates` (endpoint-bounded, no invalidation-age filter); §2 typed input map, sequence, transaction/idempotency/conflict contract; §3 bootstrap-owned universal core cross-link |

---

## 17. Open spikes / recommended decision revisits (measure before locking)

Per CLAUDE.md, numbers are starting points. Items that may move the schema or a decision:

1. ~~**`relation_evidence` partitioning — amend D23.**~~ **RESOLVED (D23).** D23 now records
   `HASH(relation_id)`, PRIMARY KEY (`relation_id`, `claim_id`), and 64 static migration-created
   children. Relation hydration prunes to one partition and evidence-once is DB-enforced. The
   partition count remains a measured starting point in the registries §11 corpus-slice load test.
2. ~~**GIN blocking index shape — reconcile tenancy and D23.**~~ **RESOLVED (D68, D23,
   `registries_design.md` §§1 and 9).** Each deployment has its own Postgres instance/schema;
   `deployment_id` is constant and remains structural defense in depth. The three blocking GIN
   indexes are single-column, and `btree_gin` is not required. The rejected shared-database
   alternative and its deployment-leading composite indexes are recorded in D68 rather than left
   as an implementer choice.
3. **Embedding dimension** (questions Q3): pins `pipeline_component_versions.embedding_dim` and the
   re-embedding batch path — the hardest thing to change later.
4. **Logical-FK auditor cost**: confirm the periodic orphan and worker-owned logical-uniqueness
   checks over partitioned tables stay cheap enough to run often; else reconsider selective real
   FKs. The evidence joins' pair duplicates are not part of this spike because their primary keys
   enforce evidence-once in the database.
5. **K-provenance granularity**: whether `knowledge_artifact_evidence` at claim grain is affordable,
   or should coarsen to relation/community grain for the largest K1 summaries.
6. **Un-merge ↔ supersession ripple** (registries §11 spike 3): verify replaying
   `merge_events.pre_merge_membership_snapshot` correctly re-adjudicates relation windows closed
   under a merged identity (`relation_adjudications.superseded_by` supports it; the procedure needs a
   test).
7. ~~**P3/D40 wording reconciliation.**~~ **RESOLVED** — the cross-link-only model is adopted
   everywhere (D40 refinement note; `requirements_v3.md` §Plane P, `overall_design.md` §5,
   README updated; `questions.md` #25 closed). This schema already followed the binding design;
   no schema change was needed.
8. **Snapshot retention depth** (latest-only vs latest-N for `communities`/`entity_graph_metrics`):
   pick the debugging-history depth against storage cost.
9. **Claim asserted-validity (D41) — measure before locking.** (a) Precision/recall of the extracted
   `claim_valid_*` interval on a golden slice + a per-fact canary for window false-extraction (D35).
   (b) Fiscal-calendar expansion ("FY2023" ≠ calendar 2023 for off-calendar years) — `precision` + the
   grounded source substring keep a wrong expansion auditable, but exact resolution is an
   extraction-quality spike. (c) Whether the optional PG partial btree on `claim_valid_*` is ever
   needed beyond the admitted query plan; add it only for a demonstrated filter path. (d) If
   recurrence / un-datable anchor-events prove load-bearing, the expressivity child table (btree-only,
   D23-restamped) is the documented upgrade — a named alternative, not a deferral.
10. **Invalidated relation retention in P2 (D69).** The executable default is unbounded by age:
    `v_graph_relates` retains every relation whose survivor-redirected endpoints are emitted active
    nodes. Measure snapshot size, rebuild duration, and transaction-time query demand at target scale.
    Only a subsequent binding P2 design revision may introduce a finite hot-snapshot horizon and its
    truthful fallback contract; this spike supplies evidence, not a hidden Phase-0 value.

## References

Designs: `overall_design.md` (§3 data model, §9 index), `registries_design.md` (D15–D24),
`e0_files_design.md` (D36–D40), `e2_e3_claims_relations_design.md` (D31–D35), `p2_graph_design.md`
(D6–D11), `k_layers_design.md` (D45–D47). Explainer: `concepts.md`. Decisions: `decisions.md`
(D1–D69). Requirements: `requirements/requirements_v3.md`. Open items: `questions.md`.
