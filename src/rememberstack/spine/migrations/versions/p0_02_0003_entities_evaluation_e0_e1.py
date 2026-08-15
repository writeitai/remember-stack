"""Create entity, evaluation, E0, and E1 structures."""

from collections.abc import Sequence

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables

revision: str = "p0_02_0003"
down_revision: str | None = "p0_02_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""-- ─────────────────────────────────────────────────────────────────────────
-- entities — the canonical registry. entity_id is NEVER reused; a merge is a redirect
-- (merged_into), never a rewrite (Wikidata model). UNIQUE(deployment_id, entity_id) is the
-- composite-FK target that keeps every entity reference inside one deployment (§0).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE entities (
  entity_id       uuid PRIMARY KEY,            -- canonical identity; never reused (D17); flows downstream to P1/P2
  deployment_id   uuid NOT NULL REFERENCES deployments,
  type            text NOT NULL,               -- canonical type = majority/highest-confidence vote across mentions (registries §4)
  canonical_name  text NOT NULL,               -- preferred display/blocking name; mirrored as an alias row (invariant below)
  normalized_name text NOT NULL,               -- unaccent+lower(canonical_name)
  status          entity_status NOT NULL DEFAULT 'active', -- active | merged | retired
  merged_into     uuid,                        -- redirect target when status=merged; follow the chain to the survivor (D21)
  type_confidence real,                        -- confidence of the type vote; low + cross-mention disagreement ⇒ over-merge signal (registries §4)
  profile_summary text,                        -- short registry-maintained blurb; improves future LLM adjudication (Graphiti lesson)
  mention_count   integer NOT NULL DEFAULT 0,  -- cached |mentions|; half of blast_radius (registries §6) and a health metric
  graph_degree    integer NOT NULL DEFAULT 0,  -- cached relation degree from the LATEST PUBLISHED P2 snapshot (§9); other half of blast_radius
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, entity_id),           -- composite-FK target (tenancy isolation, §0)
  FOREIGN KEY (deployment_id, type) REFERENCES entity_types (deployment_id, type),
  FOREIGN KEY (deployment_id, merged_into) REFERENCES entities (deployment_id, entity_id), -- same-deployment redirect only
  CHECK ((status = 'merged') = (merged_into IS NOT NULL))  -- merged iff it redirects; an active/retired entity must NOT redirect
);
COMMENT ON TABLE entities IS
  'Canonical entity registry (D17/D21). entity_id never reused; merges are redirects via merged_into (un-mergeable), not rewrites. type is the cross-mention vote; mention_count+graph_degree cache the blast-radius inputs for review gating (registries §6/§8).';
CREATE INDEX ix_entities_type     ON entities (deployment_id, type);
CREATE INDEX ix_entities_redirect ON entities (merged_into) WHERE merged_into IS NOT NULL;
-- entities is searchable by name but the PRIMARY blocking index lives on aliases (below). D68
-- gives each deployment its own instance/schema, so the blocking GIN contains only the match key:
CREATE INDEX ix_entities_name_trgm ON entities USING gin (normalized_name gin_trgm_ops);

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
-- resolver_versions — per-version tier config + per-type thresholds (D17/D22).
-- Also the home of the review-routing band boundaries (auto-accept ceiling / hub-merge floor, D24).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE resolver_versions (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  resolver_version text NOT NULL,              -- e.g. 'resolver-2026-03a'
  tier_config     jsonb NOT NULL,              -- T0–T4 enable/order, blocking floors, escalation bands, review band boundaries + hub-merge blast-radius cutoff (D24)
  thresholds_by_type jsonb NOT NULL,           -- per-entity-type accept/reject bands (golden-set-measured, D22) — starting points, not constants
  configured_at   timestamptz NOT NULL DEFAULT now(),
  notes           text,
  PRIMARY KEY (deployment_id, resolver_version)
);
COMMENT ON TABLE resolver_versions IS
  'Versioned, per-type resolution thresholds + tier config + review-routing bands (D17/D22/D24). Block-loose/decide-tight; thresholds are golden-set-measured starting points to be re-measured, never committed constants.';

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
  emitted_type    text,                        -- entity type the extractor emitted for this mention (registry-constrained)
  type_confidence real,                        -- extractor confidence in emitted_type
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
  confidence      real NOT NULL,               -- tier confidence; bands per resolver_versions.thresholds_by_type
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
  entity_type     text NOT NULL,               -- the type stratum this pair tests
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
  'Human-adjudicated ER evaluation pairs (D22). Measures P/R and tunes per-type thresholds; never used for training. expected_blocking_tier supports blocking-stratified recall. Stored as surface+context so it survives re-resolution.';
CREATE INDEX ix_golden_type ON golden_pairs (deployment_id, entity_type);

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
  suite           eval_suite NOT NULL,         -- resolution | selection | grounding | retrieval | contradiction
  component_version text NOT NULL,             -- LOGICAL FK → pipeline_component_versions / resolver_versions; what was measured
  metrics         jsonb NOT NULL,              -- per-tier/per-type P/R with Wilson CIs; recall@k per recipe; rerank weights; per-fact false-drop
  passed          boolean,                     -- did the canary regression pass for this version?
  ran_at          timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE eval_runs IS
  'Evaluation history (D22/O6). Per-tier/per-type metrics with Wilson intervals for resolution; recall@k + rerank tuning for retrieval; per-fact false-drop for selection. A canary regression re-runs per version.';
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
-- document_versions — append-only observed snapshots of a lineage (D55). One row per
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
  source_version_ref text,                     -- connector revision/etag/generation, if the source has one
  sync_cycle_id   uuid,                        -- LOGICAL FK → connector_sync_cycles (created below): which cycle observed this version (retract barrier)
  source_modified_at timestamptz,              -- when the SOURCE says this snapshot was authored/modified → derived claims' asserted_at
  published_at    timestamptz,                 -- document's own date (resolves "last year"); world-time origin
  language        text,                        -- detected primary language (per version — it can change)
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
-- ─────────────────────────────────────────────────────────────────────────
-- chunks — semchunk units, section-aware (never split mid-section, D39). Body = markdown_uri sliced
-- by [char_start,char_end]; current searchable text/vector live in chunk_search (D94). Large
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
  context_prefix  text,                        -- generated "where this sits" sentence (E1); replayed on rebuild — derived metadata, not body
  prefixer_version text,                       -- LOGICAL FK → pipeline_component_versions (context_prefixer)
  chunker_version text,                        -- LOGICAL FK → pipeline_component_versions (semchunk config)
  embedding_ref   text,                        -- embed-stage completion/typed-skip marker; not a vector-store locator
  embedding_version text,                       -- LOGICAL FK → pipeline_component_versions (embedder); scopes re-embedding batches
  created_at      timestamptz NOT NULL DEFAULT now(),  -- partition key
  PRIMARY KEY (chunk_id, created_at)
) PARTITION BY RANGE (created_at);
COMMENT ON TABLE chunks IS
  'E1 retrieval units (semchunk, section-aware), one row per (version, position). PostgreSQL chunk_search stores current searchable text and derived vectors; chunks stores offsets, section link, replayable location metadata, version stamps, and D56 reuse keys. Per-version chunk rows double as the occurrence record. Monthly-partitioned, logical FKs (D23/D94).';
CREATE INDEX ix_chunks_doc     ON chunks (deployment_id, doc_id);
CREATE INDEX ix_chunks_version ON chunks (version_id);
CREATE INDEX ix_chunks_reuse   ON chunks (deployment_id, doc_id, extraction_input_hash);  -- the D56 reuse lookup
CREATE INDEX ix_chunks_section ON chunks (section_id);

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
"""
_TABLES = (
    "entities",
    "aliases",
    "generic_identifier_guard",
    "resolution_exclusions",
    "resolver_versions",
    "mentions",
    "resolution_decisions",
    "merge_events",
    "review_queue",
    "golden_pairs",
    "golden_claim_labels",
    "eval_runs",
    "canary_cases",
    "content_objects",
    "documents",
    "document_versions",
    "document_representations",
    "connector_sync_cycles",
    "document_sections",
    "document_crossrefs",
    "chunks",
    "chunk_claims",
)


def upgrade() -> None:
    """Apply create entity, evaluation, e0, and e1 structures."""
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Revert create entity, evaluation, e0, and e1 structures."""
    drop_tables(table_names=reversed(_TABLES))
