"""Create projection, knowledge, and retrieval structures."""

from collections.abc import Sequence

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables

revision: str = "p0_02_0005"
down_revision: str | None = "p0_02_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""-- ─────────────────────────────────────────────────────────────────────────
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
  pagerank        double precision,            -- salience prior (retrieval rank + K3 filter)
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
  'Per-entity graph analytics written back from each P2 rebuild (D11): PageRank salience, degree (blast-radius), k-core, community, WCC. Read by retrieval ranking, K3 filtering, ER health checks. GC''d when its snapshot is superseded. entities.graph_degree is refreshed only from the published is_latest snapshot.';
CREATE INDEX ix_egm_entity   ON entity_graph_metrics (entity_id);
CREATE INDEX ix_egm_snapshot ON entity_graph_metrics (snapshot_id);

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
  layer           knowledge_layer NOT NULL,    -- K1 | K2 | K3 — content tier (D47), one mechanism
  page_kind       knowledge_page_kind NOT NULL, -- compiled | authored (D46)
  scope_id        uuid,                        -- non-null for K2 scope artifacts (composite FK below)
  parent_artifact_id uuid,                     -- tree/DAG position (composite FK below)
  git_path        text NOT NULL,               -- path of the markdown file in the K repo
  curation_path   text,                        -- compiled pages: the human curation sidecar file (D46)
  kind            text,                        -- 'summary' | 'profile' | 'belief' | 'decision_log' | 'model_page' | ...
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
  decided_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (deployment_id, scope_id) REFERENCES scopes (deployment_id, scope_id) ON DELETE CASCADE
);
COMMENT ON TABLE knowledge_plan_decisions IS
  'Append-only planner transcript (D45): every create/split/merge/move/retire/rule change with trigger + rationale. Reviewable, revertible structure — the opposite of emergent session behavior. Blast-radius-gated auto-apply (D24 pattern).';
CREATE INDEX ix_kplan_proposed ON knowledge_plan_decisions (deployment_id, decided_at) WHERE status = 'proposed';

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
  debounce_seconds integer NOT NULL,           -- per-subscription batch window (starting point, measure — k_layers §11 spike 8)
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
  cited_count     int NOT NULL,                -- evidence the writer used (→ knowledge_artifact_evidence)
  uncited_count   int NOT NULL,                -- offered but not used (auditable coverage gap)
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
-- knowledge_artifact_evidence — the CITATIONS: page ⇄ evidence links (D45/D46; K3 requirement +
-- deletion cascade). A BINDING output contract, not self-reported provenance: on a compiled page
-- the driver REPLACES these rows from the writer's returned citations each compile; on an
-- authored page they are synced from the page's frontmatter (`cites:`). Evidence-change
-- staleness, authored review flags, and deletion reach are reverse lookups through this table.
-- A single link targets EXACTLY ONE of claim/relation/doc (the others NULL) — so a surrogate PK
-- + a num_nonnulls CHECK + a NULL-tolerant unique index, NOT an all-columns PK (PK columns
-- cannot be NULL).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE knowledge_artifact_evidence (
  evidence_link_id uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  artifact_id     uuid NOT NULL,               -- composite FK below, ON DELETE CASCADE
  claim_id        uuid,                        -- LOGICAL FK → claims (partitioned)
  relation_id     uuid,                        -- composite FK below, ON DELETE CASCADE (real, relations is not partitioned)
  doc_id          uuid,                        -- LOGICAL FK → documents
  role            knowledge_evidence_role NOT NULL, -- supports | contradicts | cites (K3 links supporting AND contradicting evidence)
  CHECK (num_nonnulls(claim_id, relation_id, doc_id) = 1),  -- exactly one target per link
  FOREIGN KEY (deployment_id, artifact_id) REFERENCES knowledge_artifacts (deployment_id, artifact_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, relation_id) REFERENCES relations (deployment_id, relation_id) ON DELETE CASCADE
);
COMMENT ON TABLE knowledge_artifact_evidence IS
  'Citations (D45/D46): the ONE claim/relation/document each link rests on, role supports|contradicts|cites. Binding writer output on compiled pages (replaced per compile); frontmatter-synced on authored pages. Drives exact incremental refresh (D12), authored review flags (D46), and the deletion cascade. Exactly-one-target enforced by CHECK; surrogate PK because the targets are nullable alternatives.';
-- NULL-tolerant dedup (one link per (artifact, target, role)); NULLS NOT DISTINCT treats the two
-- NULL targets as equal so the populated one is the discriminator:
CREATE UNIQUE INDEX ux_kae_link ON knowledge_artifact_evidence (artifact_id, role, claim_id, relation_id, doc_id) NULLS NOT DISTINCT;
CREATE INDEX ix_kae_claim    ON knowledge_artifact_evidence (claim_id)    WHERE claim_id IS NOT NULL;
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
-- ─────────────────────────────────────────────────────────────────────────
-- retrieval_recipes — the closed assured-operation descriptor registry (D50/D83).
-- Surfaces render only the canonical active version of the three assured names. The CHECK is
-- the mechanical half of the grain linter (D41/D49); saved patterns use saved_queries instead.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE retrieval_recipes (
  recipe_id       uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  name            text NOT NULL,               -- e.g. 'relation_hybrid_rrf', 'claims_as_of', 'identity_as_of'
  description     text NOT NULL,               -- rendered into the MCP tool description (D50)
  parameters      jsonb NOT NULL,              -- typed parameter schema (JSON-Schema form)
  chain           jsonb NOT NULL,              -- the typed primitive composition: ordered ops + fixed settings (channel sets, RRF constants, rerank weights)
  output_grain    recipe_output_grain NOT NULL, -- fact | evidence | compiled | composite (the D49 envelope grain)
  answer_intent   recipe_answer_intent NOT NULL, -- current_facts | assertion_history | orientation | audit | change_feed
  version         integer NOT NULL DEFAULT 1,  -- recall@k measured per (name, version) — regressions attributable (D22)
  status          ontology_status NOT NULL DEFAULT 'active',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, name, version),
  CHECK (answer_intent <> 'current_facts' OR output_grain = 'fact')  -- the D41 bar, mechanical
);
COMMENT ON TABLE retrieval_recipes IS
  'D50/D83: closed registry for the three assured operation descriptors rendered by API, CLI, and MCP. Bootstrap atomically reconciles the canonical rows; reusable query patterns belong in saved_queries. The CHECK enforces the D41 grain bar mechanically (current_facts ⇒ fact grain), with chain-level validation in the registration linter.';
"""
_TABLES = (
    "projection_snapshots",
    "communities",
    "entity_graph_metrics",
    "knowledge_artifacts",
    "knowledge_plan_decisions",
    "knowledge_subscriptions",
    "knowledge_page_rules",
    "knowledge_rule_keys",
    "knowledge_page_watches",
    "knowledge_dispatches",
    "knowledge_compilations",
    "knowledge_artifact_evidence",
    "knowledge_refresh_queue",
    "retrieval_recipes",
)


def upgrade() -> None:
    """Apply create projection, knowledge, and retrieval structures."""
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Revert create projection, knowledge, and retrieval structures."""
    drop_tables(table_names=reversed(_TABLES))
