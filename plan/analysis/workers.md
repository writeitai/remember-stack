# Workers — the Complete Inventory: Behavior Contracts and Execution Classes

Every worker the designed system needs, what each one does, and **how each executes**:
deterministic code, a programmatic LLM pipeline, or an agent harness. Compiled from all
`current` designs (`overall_design.md`, `e0_files_design.md`, `e2_e3_claims_relations_design.md`,
`observations_design.md`, `registries_design.md`, `k_layers_design.md`, `p2_graph_design.md`,
`postgres_schema_design.md`) and the decision log. This is analysis, not binding design — but the
classification rule in §1 is distilled from standing decisions, so departures from it should be
argued, not drifted into. Model choices and cadences here are starting points to measure, not
committed constants (CLAUDE.md).

> **Reading this cold.** A **worker** is a schedulable unit of processing with its own
> idempotency record and trigger — a Cloud Run job fired by Cloud Tasks (plane E), a debounce
> window (plane K), or a schedule (plane P). It is distinct from a long-running *service* (the
> retrieval API — §9). The three execution classes used throughout:
>
> - **Deterministic** — pure computation: same inputs → same outputs, no language-model calls in
>   the control flow. May invoke *non-generative* model inference (an embedding model, OCR) —
>   that inference is versioned like everything else, but the worker makes no judgments; replay
>   means re-running the code.
> - **Programmatic LLM** ("LM-based") — a fixed-shape pipeline that makes one or a few
>   *structured* LLM calls per item: schema-constrained output, prompt rendered from the
>   registries (D15), no tools, no multi-step autonomy. Every call is versioned
>   (`pipeline_component_versions`), transcripted (the D33 ledger discipline), and **replayed
>   from storage on rebuild, never re-called** (D7).
> - **Agent harness** — a coding-agent runtime (Claude Code / Codex / OpenCode) running a model
>   in a tool-use loop (file reads, retrieval calls, SQL reads, git) until it decides it is
>   done. Expensive and low-cadence by nature; in this system every harness worker has a
>   **declared write surface** it may not exceed (e.g. "plan decisions only", "this one page").

## 1. The classification rule (derived from standing decisions)

The design already contains the decision rule; this inventory just applies it uniformly:

1. **If the transformation is mechanical, it is deterministic.** "Intelligence chooses;
   machinery routes" (D45): routing, staleness, blocking, validation, projection, commits are
   SQL and code. The query path is zero-LLM (D9). The graph writer is dumb (D44).
2. **If it needs semantic judgment per item at volume, it is a programmatic LLM inside a
   cheap-first cascade** — deterministic gates first, small model next, frontier model only for
   the ambiguous residue, so **spend scales with ambiguity, not volume** (D4, D17). Never a
   harness: a tool-loop per claim at millions of documents is cost and latency with no
   compensating judgment gain, and its decisions would be exactly the unrecorded improvisation
   D45 rejects.
3. **An agent harness only where the working surface is open-ended** — a git repo of pages, the
   corpus tree, cross-page coherence — **and the cadence is low** (per compile cycle or slower).
   In this design that surface is exactly plane K plus the review/audit seats. **No harness ever
   sits on a per-document, per-claim, or query path** — this is an invariant worth preserving,
   not an accident of the current inventory.
4. **Producer/checker separation across model families.** Judges, reviewers, and the reflection
   pass must not be the model that produced the work (D24 review-outside-the-proposing-context;
   D32 layer 4 "self-grading is optimistic"; k_layers §7 "fresh eyes"). Requirements fix
   Codex/OpenCode as the K **producer** agents (planner, writers) — so the **checker** seats
   (reflection, reviewer agent, sampled judges) default to the Claude family.

## 2. The shared worker contract (D12) — inherited by everything below

Stated once here; every per-worker spec in §4–§8 assumes it:

- **Runtime**: GCP Cloud Run job, triggered via Cloud Tasks (plane E: completion of the previous
  stage enqueues the next for that document), rate-limited.
- **Idempotency**: `INSERT … ON CONFLICT DO NOTHING` into `processing_state` on
  `(deployment, target_kind, target_id, stage, component_version)`; a succeeded row makes the
  attempt a no-op. Target IDs are content-derived, so this *is* the content-hash + version key.
- **Failure**: bounded retries (default 2, per-stage tunable) → `status='dead_letter'` with the
  enqueue payload preserved. Failures never disappear; a failed worker leaves the previous
  consistent state serving (no partial outputs — single-transaction writes where an outcome is
  multi-row, e.g. cap + insert + adjudication reason).
- **Versioning**: every non-deterministic producer stamps a `*_version` resolving to
  `pipeline_component_versions` (model, prompt hash, params). A version bump reprocesses exactly
  the version-filtered scope.
- **Cost**: every model call writes `cost_ledger` (deduplicated per attempt); per-layer budgets
  are enforced, not advisory.
- **Replay, not recall**: on any rebuild, LLM stages replay their stored outputs (decision
  ledgers, adjudication transcripts, compile transcripts) — a model is re-called only on a
  version change.

## 3. Summary table

| # | Worker | Plane | Trigger | Class | Model budget |
|---|---|---|---|---|---|
| 1 | `ingest` | E0 | upload/API | deterministic | — |
| 2 | `convert` | E0 | chain | deterministic (+ OCR inference where routed) | — |
| 3 | `structure` | E0 | chain | programmatic LLM | mid |
| 4 | `crossref` | E0 | chain | deterministic + optional LLM rung | small |
| 5 | `chunk` | E1 | chain | deterministic | — |
| 6 | `context_prefix` | E1 | chain | programmatic LLM *(exists — F8 resolved by D63)* | small, prompt-cached |
| 7 | `embed_chunk` | E1 | chain | deterministic (embedding inference) | embedder |
| 8 | `extract_claims` | E2 | chain | programmatic LLM (2 calls) | mid — the volume cost center |
| 9 | `ground_claims` | E2 | in-pipeline | deterministic (layers 1–2) | — |
| 10 | `resolve_entities` | E3 | chain | cascade: deterministic T0–T2 / embedding T3 / LLM T4 | small→frontier residue |
| 11 | `normalize_relations` | E3 | chain | deterministic registry mapping + LLM rung | small |
| 12 | `adjudicate_supersession` | E3 | chain | cascade: deterministic gates + LLM residue | small→frontier residue |
| 13 | `adjudicate_observations` | E3 | chain | same cascade, entity-blocked | small→frontier residue |
| 14 | `label_and_embed_facts` | E3/P1 | on fact create/change | micro-LLM + embedding inference | small |
| 15 | `profile_refresher` | registry | debounced | micro-LLM, batched | small |
| 16 | `predicate_promotion` | registry | periodic | deterministic ranking + LLM proposal + gated apply | mid, tiny volume |
| 17 | `registry_health_monitors` | registry | periodic | deterministic | — |
| 18 | reviewer (queue consumer) | registry+K | queued items | human CLI **or** reviewer-agent harness | Claude family |
| 19 | `unmerge_applier` | registry | verdict | deterministic | — |
| 20 | `p2_build_snapshot` | P2 | schedule | deterministic | — |
| 21 | `graph_analytics` | P2 | post-publish | deterministic (+ optional micro-LLM community labels) | small |
| 22 | `p1_batch_rebuild` + compaction | P1 | drills/migrations/schedule | deterministic (embedding inference) | embedder |
| 23 | `p3_build` | P3 | schedule | deterministic | — |
| 24 | `k_driver` | K | debounce window | deterministic | — |
| 25 | `k_planner` | K | structural triggers | **harness — Codex/OpenCode** (imposed) | frontier |
| 26 | `k_writer` (per page) | K | stale set | **harness — Codex/OpenCode** (imposed); bundle or agent mode | tiered by page |
| 27 | `k_reflection` | K | periodic | **harness — Claude family** (checker seat) | frontier |
| 28 | `k_linter` | K | periodic | LLM QA pass (low-cadence harness; batched alternative) | mid |
| 29 | deletion cascade | cross | delete request | deterministic | — |
| 30 | hard-forget | cross | legal request | deterministic | — |
| 31 | eval harness + canary CI | cross | per version bump / schedule | deterministic runner + LLM judges | judge ≠ producer family |
| 32 | budget guard + snapshot/analytics GC | cross | continuous/post-publish | deterministic | — |

Dispatch delivery (K subscriptions) is a duty of the driver (#24), not a separate worker.
Subscriber workflows, K authoring agents, and the review CLI are **outside the worker set** (§9).

## 4. Plane E — the per-document chain

### 4.1 `ingest` — deterministic

Stores raw bytes to `gs://…-raw/<doc_id>/<content_hash>/`, computes `content_hash`
(sha256 — the canonical byte identity), upserts the `documents` row
(`UNIQUE(deployment, content_hash)` makes re-upload a no-op — the only surviving "dedup", as
idempotency, D25), enqueues `convert`. May never mutate a raw object (immutable, D1).

### 4.2 `convert` — deterministic router (+ OCR model inference where routed)

Routes by input type per deployment config (D38). **PDF route superseded by D139:**
every admitted PDF page goes through OCR, including pages with a text layer;
the historical digital-versus-scanned split does not apply. Office/html/email
route to markitdown; text passes through.
Emits `document.md` + `conversion.json` whose **page/char offsets are load-bearing** (D32
grounding spans, chunk positions). OCR is model inference inside a deterministic control flow —
the worker routes and validates; it judges nothing. `converter_version` bump re-converts the
affected batch and rebuilds downstream (D7).

### 4.3 `structure` — programmatic LLM

Runs PageIndex (imposed constraint) and emits the section tree with roles (closed enum), spans,
per-section summaries, and the **placement hint** for P3 (D39). Output contract is
unconditional: every document gets `document_sections` rows — a short document gets a synthetic
root section without running the expensive tool (an implementation-routing choice, not a
contract gap). Dual output: `pageindex.json` sidecar (mountable artifact) + Postgres rows
(queryable index). Versioned (`structurer_*`); replayed from storage on rebuild. Mid-tier model;
the placement hint is advisory (P3 reconciles), so structure quality gates E1/E2 but placement
quality gates nothing.

### 4.4 `crossref` — deterministic first, one optional LLM rung

Extracts citations and document links: markdown/HTML links, DOI/citation-string patterns, email
reply/attachment headers — all deterministic parsers. A small-model rung only for fuzzy
citation-to-document matching where string rules fail; `to_doc_id` stays NULL for
cited-but-not-ingested targets (no edge, per `v_graph_crossref`).

### 4.5 `chunk` — deterministic

semchunk (imposed) within PageIndex section boundaries — never split mid-section, one chunk ≈
one topic. Pure function of markdown + sections.

### 4.6 `context_prefix` — programmatic LLM *(existence resolved by D63)*

One small-model call per chunk producing the "where this sits" sentence
(contextual-retrieval style), sharing one cached per-document prefix. This worker exists only
under a non-contextual embedding model choice — and **D63 made that choice**: the shipped
default embedder (`qwen3-embedding-8b`) is conventional, so the worker **exists** in the
default configuration. A deployment switching its embedder port to a contextual model
(voyage-context-class) deletes this stage for that deployment — port config, not redesign
(`e1_chunks_design.md` §5).

### 4.7 `embed_chunk` — deterministic (embedding inference)

Embeds chunks and writes P1/Lance inline (P1 stays a projection: the batch path of §6.3 can
rebuild everything). Embedding calls are versioned inference, not judgment. Also embeds claims
as E2 lands them (same family, same discipline).

### 4.8 `extract_claims` — programmatic LLM, two calls; the volume cost center

The Claimify-staged extractor (D31) over the context bundle (header, section path + summary, E1
prefix, ±1 neighbours, entity hints):

- **Call 1 — Selection** (optionally self-consistency-voted): KEEP/REWRITE/DROP per proposition;
  keep-biased with never-drop classes and `kept_flagged` (D35).
- **Call 2 — fused**: decontextualize + decompose + in-call grounding self-verdict (D32 layer 3)
  + coreference (D19) + canonical name form per mention (registries §5) + entity type (registry
  enum) + temporal class + the D41 asserted-validity interval + the relation-vs-observation
  routing signal.

Everything it decides is transcripted: drops/flags/edits → `claim_extraction_decisions`. This is
**three LLM calls per chunk over the whole corpus counting the E1 prefix** (F8) — strictly
volume-proportional spend, so the model tier is a measured choice on the golden set, mid-tier at
most; a frontier model here is the single most expensive mistake available in the system, and a
harness is categorically wrong (rule 2, §1).

### 4.9 `ground_claims` — deterministic (the in-pipeline layers)

Layers 1–2 of D32 acceptance, code not model: the `source_span` must be a real in-bounds slice
of the chunk (anchor); every `added_context[]` substring must verbatim-exist in the bundle
element it is attributed to (window membership). Rejections are recorded; the model cannot talk
its way past either check. (Layer 3 rides inside call 2 above; layer 4 is the sampled judge,
§8.3.)

### 4.10 `resolve_entities` — cheap-first cascade (deterministic core, LLM residue)

The T0–T4 cascade (D17): T0 exact on the LLM-emitted canonical form, T1 `pg_trgm`, T2
Daitch-Mokotoff — deterministic Postgres, candidate generation never auto-rejects (near-misses
escalate); T3 embedding similarity (Lance); T4 programmatic LLM small→frontier for the ambiguous
band. Around the cascade, all deterministic: **local pocket re-clustering** (gather
connected-component blob → HAC distance-cut split — order-independent placement, no transitive
closure), the **black-hole guard** (oversized blob → raise bar, re-split), the
**generic-identifier guard** (one alias linking many entities → downweight + re-evaluate), and
**blast-radius routing** (`expected_impact = blast_radius × (1 − confidence)` middle band → the
review queue; hub merges never auto-accept). All decisions append-only
(`resolution_decisions`), all merges snapshot-backed and reversible (`merge_events`).

### 4.11 `normalize_relations` — deterministic registry mapping + one LLM rung

Maps eligible claims to `(subject, predicate, object)` against the predicate registry:
constrained predicate from the extraction call + `synonyms[]` mapping + domain/range validation
(D18 `edge_type_map`) + the `other:<freetext>` UPSERT (tier='other', FK holds, usage counted) —
all deterministic. A small-model rung only where the claim→predicate mapping is genuinely
ambiguous. A relation failing domain/range is dropped, not quarantined — it is re-derivable from
its immutable claim if the entity is retyped (registries §4).

### 4.12 `adjudicate_supersession` (relations) — cascade, transcripted

Novelty gate (deterministic short-circuit: same `(s,p,o)`, compatible window → evidence row, no
LLM) → `(entity_id, predicate)` blocking (SQL) → deterministic compare → small model → frontier
for survivors (D4). Write-time outcomes in one transaction: `supersede` closes windows,
`contradict` groups (both live), `same_as` proposes merges (never auto-applies). Every
non-trivial outcome writes an adjudication transcript row (replayable, D7).

### 4.13 `adjudicate_observations` — the same engine, entity-blocked

Blocks on the resolved entity (exact, exhaustive — the no-recall-hole property), semantic
narrowing via P1 **orders candidates only, never filters membership**, then the same
cheap-first cascade under the **no-cap rule** (a fixed-period figure is never valid-time-capped;
same-period conflicts coexist) and the **binding fail-safe contract** (obs design §3.4):
`supersede` only above an explicit margin against a positively-matched prior, always with an
`observation_adjudications` reason row; anything less → coexist. Claims batch per entity (one
block fetch per document-entity). The two zero-LLM exits (first mention; exact re-assertion →
evidence collapse) absorb the bulk of volume. *(Open finding F5 — an ungoverned `property_hint`
narrowing key for hub entities — would change this worker's ordering input, not its class.)*

### 4.14 `label_and_embed_facts` — micro-LLM + embedding inference

One-sentence canonical fact labels for relations ("Alice Novak works at Acme as VP of
Engineering"), regenerated only when adjudication materially changes the relation; observation
labels reuse `statement` where possible (zero-LLM). Embeddings batched into P1. Small model;
this is the `fact_labeler` component.

## 5. Registry substrate & governance workers

### 5.1 `profile_refresher` — micro-LLM, batched

Maintains `entities.profile_summary` + the profile embedding (Lance,
`profile_embedding_ref`), debounced on evidence change for the entity. Small model, batched.
**These fields are cascade inputs, not cosmetics** (schema §4): the profile embedding is what
T3 matches mention embeddings against, and the summary is T4 adjudication context (the
Graphiti lesson) — so the refresher is quality-relevant to entity resolution. Staleness
degrades inside the cascade's existing safety envelope (per-type golden-tuned thresholds,
escalate-don't-auto-reject, blast-radius review), costing match quality, never an unreviewed
merge. The refresher writes only these two fields — never names, aliases, types, or status
(identity belongs to resolution). Binding spec: `registries_design.md` ("Entity profiles").

### 5.2 `predicate_promotion` — deterministic ranking + LLM proposal + gated apply

Periodic (D5 funnel): rank `tier='other'` predicates by `usage_count` (SQL), have a programmatic
LLM draft the disposition per candidate (map to existing predicate vs promote with a tight
signature vs leave) with examples pulled from evidence, then a **review-gated deterministic
apply** (registry row inserts/retypes; P2 rebuild makes retyping retroactively clean). Splitting
a heavily-used predicate is the one expensive operation (registries §7) and always routes to
review. *(Open: the workflow owner — registry SYNTHESIS G5.)*

### 5.3 `registry_health_monitors` — deterministic

Scheduled SQL over the registry: cluster-size distribution (emerging giant = over-merge),
singleton rate per type (under-merge), unresolved-mention rate, alias-per-entity growth,
cross-mention core-type disagreement (over-merge tell), tiny disconnected graph components (ER
misses). Findings become review items — SELECTs, not machinery.

### 5.4 The reviewer — human CLI, or a reviewer-agent harness

The D24 review queue (clusters not pairs, evidence waterfall, 3-way verdicts, blast-radius-gated
band) is consumed by a human via the thin CLI **or**, in agent-operated deployments, by a
designated **reviewer agent**: an agent harness (Claude family — outside the Codex/OpenCode
proposing context, rule 4 §1) with read access to the evidence panels and a write surface of
review verdicts only. Same seat consumes the K plan-decision review band (k_layers §7).
Everything it decides appends reversible, provenance-stamped records; a wrong verdict costs a
revert, not a loss — which is what makes an agent in this seat safe to run, with the human as
after-the-fact auditor.

### 5.5 `unmerge_applier` — deterministic

Applies an un-merge verdict: replay `pre_merge_membership_snapshot`, remove the redirect,
enqueue re-adjudication of relation windows that were closed under the merged identity
(registries §11 spike 3 — the silent-supersession ripple), and let the next P2 rebuild re-point
edges for free.

## 6. Plane P — projection workers (scheduled; rebuild-first, D7)

### 6.1 `p2_build_snapshot` — deterministic ("the worker is dumb")

Per cycle: read the `v_graph_*` views (all casts, merge-redirects, retention filtering live in
the views, D44) via the Parquet hop (committed transport) or ATTACH-direct (pending
throughput spike), COPY into a fresh LadybugDB (nodes before rels), run the **validation gate**
(every retained endpoint resolves to exactly one emitted survivor; per-table counts match —
failure **aborts**, previous snapshot keeps serving), checkpoint, upload immutable snapshot,
flip `latest`, record `projection_snapshots`.

### 6.2 `graph_analytics` — deterministic (+ one optional micro-LLM)

After publish: PageRank/K-Core/WCC natively on the snapshot; Leiden/Louvain as an **external
pass** (igraph/graspologic over the same Parquet export — LadybugDB ships no community
detection); write back `communities` + `entity_graph_metrics` to Postgres (never reprojected
into the graph — circular); `entities.graph_degree` refreshed **only** from the published
`is_latest` snapshot (the auto-merge gate must never read an unvalidated projection); GC
superseded snapshots' analytics rows. Optional micro-LLM: a topic label per community (K1
hint) — small model, cosmetic.

### 6.3 `p1_batch_rebuild` + compaction — deterministic (embedding inference)

The exercised batch path behind P1's inline writes: full rebuild from Postgres for drills and
embedding migrations (re-embed scoped by version filter — the hardest migration in the system,
`questions.md` #3), plus the Lance compaction schedule. No judgment anywhere.

### 6.4 `p3_build` — deterministic

Materializes the corpus filesystem snapshot: tree from placement hints reconciled with
entity/relation structure, generated leaf stubs (frontmatter + artifact pointer), `_index.md` /
`llms.txt` at each level assembled from **already-stored** LLM-derived inputs (section
summaries, K page summaries, placement hints) — the build itself makes zero LLM calls; all
understanding was paid for upstream. Snapshot + validation + pointer swap, same as P2. *(Open
finding F6 — the stable-path contract for entity/document leaves — constrains the tree layout,
not the worker's class.)*

## 7. Plane K — the compile system (D45–D47, D73)

### 7.1 `k_driver` — deterministic; the repo's only automated committer

One worker, many duties, all mechanical (k_layers §3, §5–§6): pull + authored-frontmatter sync +
quarantine detection (`content_hash` mismatch → proposed sidecar entry, page excluded); routing
(consume `knowledge_refresh_queue` → `knowledge_rule_keys` lookup + citation reverse-lookup;
re-materialize derived rule keys); staleness = the `inputs_hash` test; apply auto-band plan
decisions, queue the rest; schedule writers in DAG order (shared model page → children → parents
→ root, once); render the deterministic **fact-sheet band**; validate writer output (citations
resolve, exclusions honored, links resolve); one commit per cycle, two-phase against Postgres;
coalesce + deliver **dispatches** (per-subscription debounce, delta payload, at-least-once via
Cloud Tasks); raise the declaration lint and `authored_review` flags. May never generate
content or override curation. Failure semantics: a failed writer leaves the previous consistent
page serving; there is no partial-page state.

### 7.2 `k_planner` — agent harness (Codex/OpenCode; imposed constraint)

Owns structure, never content: consumes orphan aggregates, size overflows, community changes,
writer suggestions, reflection findings; emits append-only `knowledge_plan_decisions`
(create/split/merge/move/retire/adjust-rule/convert_kind) with rationale. Its **entire write
surface is plan-decision rows** — the driver applies them. Low-blast-radius decisions
auto-apply; above the band → the reviewer seat (§5.4); authored→compiled handover never
auto-applies. Frontier-class model: planner quality is the named load-bearing judgment of the
plane (k_layers §11 residual 1), and its volume is tiny.

### 7.3 `k_writer` — agent harness (Codex/OpenCode; imposed constraint), one page per invocation

Compiles the LLM band of one compiled page from: the rule-matched evidence bundle (relations +
observations in full — bounded; claims capped: residue + top-K evidence per leading fact),
child page summaries, the scope's shared model page, and the curation sidecar (enforceable
subset enforced mechanically). Two operating modes, both design content: **bundle mode** — the
pre-hydrated evidence, no tools (the completeness floor); **agent mode** — a full session with
retrieval tools over the memory for high-stakes scopes (the rule is a floor, not a ceiling).
Output contract: markdown body + **citations** (binding; validated) + a 2–3-sentence page
summary + optional suggestions. May never touch another file or leave inputs uncited.
Fact-sheet-only pages skip the writer entirely (zero LLM). Model tier is configured per page
kind/scope — shared-model and other high-stakes pages high, leaf entity pages mid.

### 7.4 `k_reflection` — agent harness, Claude family (the checker seat)

Periodic; reads across the compiled tree + health metrics (orphan volume, staleness
distribution, page sizes, uncited-candidate rates, navigation dead-ends) and proposes
structural changes — as `knowledge_plan_decisions` proposals only, never edits. Explicitly a
**different agent/model than the planner** (k_layers §7): if the producer seats are
Codex/OpenCode, this seat is Claude.

### 7.5 `k_linter` — LLM quality-assurance pass (demoted from load-bearing)

Checks prose, not staleness (staleness is mechanical now): cross-page contradictions, broken
narrative, tone drift; files findings as review items or recompile requests. The cross-page
surface plus low cadence makes a low-frequency **harness reading the repo** the natural shape;
a batched programmatic variant (structured checks over rendered page bundles) is the documented
alternative if the harness cost proves unjustified for QA-only output. Either way its write
surface is findings, never pages.

## 8. Cross-cutting workers

### 8.1 Deletion cascade — deterministic

Document removal: delete raw + artifacts objects, cascade Postgres rows
(documents → sections → chunks → claims → evidence; relations whose evidence empties get
retired), emit `tombstone` events into the K refresh queue (compiled pages recompile without
the evidence; authored pages get review flags; empty-rule pages become retire proposals). P1/P2/P3
heal on their next rebuild — nothing to delete in a projection.

### 8.2 Hard-forget (GDPR) — deterministic, checklist-shaped, with verification

Everything in 8.1 **plus** erasing derived text: K git-history rewrite (`git filter-repo`
scoped by the citation index to exactly the pages that ever cited the source), P1 vector purge
(row deletes + compaction, or rebuild), the #24 design's active P2/P3 snapshot treatment, durable
deletion state that restore/import must reapply, and a final verification sweep that the content
hash appears nowhere. Physical backup schedules, rotation, and expiry are operator/cloud duties
under D60. Authored pages that cited the forgotten source get **redaction flags** — the system
never rewrites an author's words, even to forget; that duty is the author's.

### 8.3 Eval harness + canary CI — deterministic runner, LLM judges

One evaluation surface, several suites (`eval_suite`: resolution, selection, grounding,
retrieval, contradiction — plus K writer-completeness and citation-faithfulness, k_layers §7):
a deterministic runner over golden sets and planted canaries (D22, D35), re-run per version
bump, failing CI on regression. Where a metric needs judgment (grounding layer-4 sampled
audits, contradiction precision/recall — the *acceptance criterion* for the observation
adjudicator, citation-faithfulness sampling), a **programmatic LLM judge in a different model
family than the producer** (rule 4, §1). Golden-set growth is the LLM-propose / human-verify
labeling loop (registries §11 spike 1).

### 8.4 Budget guard + GC — deterministic

Budget enforcement reads `cost_ledger` deduplicated totals and halts (not warns) per-layer
overruns; snapshot/analytics GC prunes superseded snapshots' derived rows and applies GCS
retention. Both are code.

## 9. At the boundary — not workers of this system

- **Retrieval API / CLI / MCP server** — long-running *services*, deterministic, **zero LLM on
  the core path** (D9); each instance runs a deterministic snapshot-swap sidecar (download
  `latest`, open READ_ONLY, hot-swap on pointer change). The optional cross-encoder rerank is
  flagged, off the default path, and is inference, not judgment.
- **The review CLI** — human tooling over the queue (§5.4), not a worker.
- **Subscriber workflows** (K dispatch consumers — e.g. an operating company's planning module)
  — invoked by the driver, owned entirely outside the system boundary (k_layers §5); they must
  be idempotent per `dispatch_id`, and that is all the system asks of them.
- **Authoring agents** — the agents that write authored pages commit through normal git flow;
  they are *authors* (users of the system), not system workers, even when a dispatch wakes them.

## 10. What compiling this inventory surfaced

1. **Small schema-enum gaps.** `pipeline_stage` has no values for observation adjudication /
   labeling, claim/observation embedding, or the K linter/reflection stages;
   `processing_target` lacks `observation`; `pipeline_component` could carry `reflector` /
   `linter` (or they ride `judge`). All additive `ALTER TYPE` fixes to
   `postgres_schema_design.md` §1 — worth landing before the schema freezes.
2. **The harness surface is exactly plane K + the review/audit seats — by design.** Every
   volume-shaped worker is deterministic or a programmatic cascade. "No harness on a
   per-document, per-claim, or query path" deserves recording as an explicit invariant
   (a candidate decision), not folklore.
3. **The producer/checker family split is currently implicit.** Requirements pin Codex/OpenCode
   to the K producer seats; nothing yet *binds* the checker seats (reflection, reviewer,
   judges) to a different family — it is stated in k_layers §7 for reflection only. Worth one
   line in a decision if accepted.
4. **Two workers are contingent on open decisions**: `context_prefix` exists only under a
   non-contextual embedding model (F8 — decide the embedder first; *since resolved by D63:
   conventional default → the worker exists*), and the observation adjudicator's hub-entity
   ordering input changes if F5 (`property_hint`) is accepted. Neither changes any
   classification.
5. **The three load-bearing LLM workers** (extractor, the adjudicator pair, K writers) are
   exactly the three with append-only transcript tables (`claim_extraction_decisions`,
   `*_adjudications`, `knowledge_compilations`) — the D33 discipline held everywhere it
   matters. Any *new* worker that gains an LLM call must gain a ledger with it.

## References

Designs: `overall_design.md` (§4–§8), `e0_files_design.md`, `e2_e3_claims_relations_design.md`,
`observations_design.md` (§3), `registries_design.md` (§3, §6–§10), `k_layers_design.md`
(§3, §5–§7), `p2_graph_design.md` (§5–§7), `postgres_schema_design.md` (§1–§2, §5, §10–§11).
Decisions: D4, D7, D9, D12, D17, D19, D22, D24, D25, D31–D35, D41, D43–D47. Review:
`design_review_2026_07.md` (F5, F6, F8). Requirements: `requirements_v3.md` (imposed
constraints; operational properties).
