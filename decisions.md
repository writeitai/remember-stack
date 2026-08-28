# Architecture Decision Log

Decisions made during requirements/design exploration (June 2026), with context and rationale.
Companion docs: `plan/requirements/requirements_v3.md` (what),
`plan/designs/p2_graph_design.md` (graph how), `plan/analysis/concepts.md` (data-model
explainer), `plan/analysis/ladybug_capabilities.md` (verified DB facts), `questions.md`
(open). Naming note: D1–D13 predate the E/K/P plane naming (D14) and keep their original
L-numbers as historical record.

---


## D1. Split source of truth: Postgres vs. the git repo

**Decision.** Postgres is authoritative for L0–L2 and L6 — everything deterministically
derivable. The L3–L5 git repo is *itself* a source of truth (backed up independently);
Postgres holds only its provenance and triggers.

**Context.** v1 required "the entire system rebuildable from Postgres" while also making
L3–L5 LLM-derived git-tracked layers. LLM output is non-deterministic — those layers are not
reproducible from Postgres unless model+prompt+inputs are pinned, and even then re-runs differ.
The two requirements contradicted each other.

**Consequences.** Rebuild guarantees apply to L0–L2+L6 only. The repo needs its own backup
discipline. Postgres records prompt/model/embedding versions per derived artifact so partial
reproducibility is still auditable.

**Refined by D46.** "Not reproducible" was over-scoped: LLM non-determinism blocks *byte*
reproducibility, not *semantic* reproducibility. Compiled K pages are semantically regenerable
from the spine plus their recorded compile inputs; the git repo's **irreducible** source-of-truth
core — what backups genuinely protect — is human-authored content (authored pages + curation
sidecars).

---

## D2. Claims and relations are distinct concepts (many-to-many)

**Decision.** L2 claims are atomic *natural-language assertions* (identity =
assertion-by-a-source; immutable, append-only). A separate normalization step maps eligible
claims onto **relation** records `(subject_entity, predicate, object_entity)` (identity = the
fact itself). Join table `relation_evidence(relation_id, claim_id, stance)` connects them.

**Context.** An earlier draft stamped `claim_id` directly on graph edges, silently assuming
claims ≅ triplets 1:1. They aren't: one claim can yield several facts; one fact can be asserted
by hundreds of documents; many claims (opinions, n-ary, single-entity attributes) yield none.

**Consequences.**
- Corpus redundancy collapses: N documents asserting the same fact = one relation with N
  evidence rows, not N parallel edges. `evidence_count` becomes a free confidence/salience
  signal (and a candidate filter for L5 core beliefs).
- Graph edge count scales with distinct facts, not corpus size.
- Full reasoning in `plan/analysis/concepts.md`.

**Refined by D54.** The evidence *rows* stay claim-grained (provenance, unchanged), but the
cached **count's denominator** is corrected: `evidence_count` ≡ distinct document *lineages*
with *current-testimony* support — not claim rows, which inflate under re-extraction, document
versioning, and within-document repetition. Rationale: `evidence_lifecycle_design.md` §4.

---

## D3. Supersession/contradiction adjudication operates at the relation level

**Decision.** "Alice left Acme" closes the validity window of the relation
`(alice, works_at, acme)` — one row update. Claims are never marked superseded; they remain
true as records of what sources asserted.

**Context.** Claim-level supersession would require finding and flagging every assertion that
ever implied the old fact (hundreds of records, inevitable misses → zombie facts in
retrieval). Mirrors how Graphiti invalidates edges, not episodes.

**Consequences.** Two clocks with different semantics: claim timestamps (asserted/ingested)
never change; relation windows (valid_from/valid_until + ingested_at/invalidated_at) are
revisable adjudications over evidence. Both time-travel questions ("was it true at T?" /
"what did we believe at T?") stay answerable.

**Refined by D41.** Claims additionally carry an *immutable* source-asserted validity interval
(testimony about temporal extent). It never becomes revisable and never introduces claim-level
supersession — the adjudicated window stays relation-only; this strengthens, not weakens, D3.

---

## D4. Supersession detection via entity-keyed blocking + cheap-first cascade

**Decision.** Candidate conflicts are found by blocking on `(entity_id, predicate)` over the
relations table (small — distinct facts only), then escalating: exact → fuzzy → embedding
similarity → small model → frontier LLM only for the residue. A novelty gate (similarity
thresholds) routes clear ADD/NOOP cases past the LLM entirely.

**Context.** O(N) vector-similarity scans per write are both unaffordable at millions of
claims and imprecise (they surface compatible-but-related statements, forcing wasted LLM
judgments). Convergent recommendation of both external reviews. Blocking requires a predicate
— which raw NL claims don't have — making the relations table (D2) the enabling index.

**Consequences.** Write-side LLM cost scales with ambiguity, not volume. Entity-resolution
quality becomes make-or-break (false negatives in resolution = missed supersessions) →
invest in the registry early. Coreference is a *guarantee* (no claim leaves E2 with a dangling
pronoun), not necessarily a discrete prior stage — its topology is set by D19. Tier thresholds
mentioned here are placeholders superseded by D17 (per-type, golden-set-tuned).

---

## D5. Predicate vocabulary is governed, not emergent

**Decision.** A Postgres predicate registry (name, description, synonyms, status). Extraction
is constrained to the registry with an `other:<freetext>` escape; a periodic job reviews and
promotes/maps frequent `other:` values. Start strict (high precision, smaller graph).

**Context.** Free-text predicates fragment ("works_at"/"employed_by"/"is employee of"),
silently breaking both `(entity_id, predicate)` blocking and graph queries.

**Consequences.** Ontology evolves by review, not accretion. Because live graph views read the
registry-backed PostgreSQL facts (D98), vocabulary cleanups apply on the next statement.
Loosening later is cheap; tightening a noisy
vocabulary later is not — hence strict-first.

---

## D6. The graph (L6) is a derived projection, never an authority

**Status:** graph-projection placement superseded by D98. PostgreSQL remains
the sole authority, and the live graph is now a query surface over normalized
authority views rather than copied data.

**Decision.** LadybugDB holds a read-optimized projection of Postgres facts. It makes no
decisions, stores no unique state, holds **no embeddings**, and can be deleted and rebuilt at
any time. Validity metadata has exactly one home: Postgres.

**Context.** The strongest finding from the external supersession review: replicated
invalidation state across vector/graph stores drifts (documented Mem0 desync bug class).
Deliberate divergence from Graphiti, which adjudicates inside the graph at write time — we
already paid for adjudication at L2; a second authority would only create disagreement.

**Consequences.** The graph writer is dumb and deterministic. Graph corruption is a
non-event (rebuild). All cross-store consistency questions reduce to "how stale is the
projection," bounded by rebuild cadence.

**Refined by D41.** Claim-grain asserted-validity is *evidence*, not a second validity home: it is
immutable and many-valued, lives in Postgres only, and the `claims_as_of` recipe is barred from
answering current-fact — so validity-as-current-fact still has exactly one home.

---

## D7. Rebuild-first sync; immutable GCS snapshots; read-only readers

**Status:** superseded by D98 for graph data. P3 keeps its independently
specified rebuild/publish lifecycle; there is no P2 generation.

**Decision.** The L6 worker periodically rebuilds the entire graph from a Postgres → Parquet
export (`COPY FROM` bulk load), validates, and publishes an immutable versioned snapshot to
GCS (write-then-pointer-swap). Readers download the `latest` snapshot, open READ_ONLY, and
hot-swap on updates. Incremental event application is a **deliberate non-goal** — rebuild-first
is the design; incremental is a documented alternative (`p2_graph_design.md` §5) we would adopt
only if sub-hour graph freshness ever became a hard requirement.

**Context.** LadybugDB's verified concurrency model is one READ_WRITE process XOR many
READ_ONLY processes — snapshot serving is the intended usage, not a workaround. Sizing at the
1M-doc target (distinct relations, few GB, minutes to bulk-load) makes full rebuilds cheap.

**Consequences.**
- Drift between Postgres and graph is impossible beyond one cycle — no reconciliation jobs.
- Entity merges (nightmare incrementally — re-pointing thousands of edges) are no-ops.
- "Rebuildable from Postgres" is exercised every cycle instead of rotting as a DR script.
- Old snapshots are free point-in-time debugging artifacts.
- Freshness SLA = rebuild cadence (start 6-hourly; tighten if missed).

---

## D8. Relation fact-label embeddings live in LanceDB, not in the graph

**Status:** placement superseded by D94. Fact-label embeddings remain outside
the LadybugDB snapshot and now live as current derived columns on natural
relation/observation rows.

**Decision.** Each relation gets a canonical fact label ("Alice Novak works at Acme as VP of
Engineering") embedded in a Lance `relations` table keyed by `relation_id`, with scalar
columns (subject_id, predicate, object_id, validity window, evidence_count) for filtered
hybrid search. No vectors in the LadybugDB snapshot.

**Context.** Challenged ("is Lance really the best place?") and then verified against the
vendored LadybugDB source + official docs. Findings (detail in
`plan/analysis/ladybug_capabilities.md`):

1. **Hard blocker**: LadybugDB's HNSW vector index and BM25 FTS index support **node-table
   properties only** — relationship properties cannot be indexed. In-graph fact search would
   require reifying every relation as a node, roughly doubling the graph and contorting
   traversals.
2. **Snapshot economics**: 5–15M fact embeddings at 1024–1536 dims fp32 ≈ 20–90 GB inside
   every snapshot (vs. a few GB without) plus a full HNSW build per rebuild cycle — destroys
   the rebuild-and-ship model (D7).
3. **Lance exists regardless** for L1 chunks and L2 claims; one vector estate, one embedding
   pipeline, one index-maintenance regime.
4. **The avoided join is cheap**: top-k (~100s) relation_ids from Lance → ID-keyed
   expansion/BFS in the snapshot.

**Consequences.** Division of labor: Lance = entry (semantic + BM25 + scalar-filtered
candidate generation); LadybugDB = structure (expansion, paths, distance reranking, as-of
traversal). Revisit only if D7 changes *and* the node-only limitation disappears upstream.
The Lance relations table is derived state, rebuilt with the same guarantees as the snapshot.
Fact labels add a small write-side LLM cost (one sentence per relation, only on material
adjudication changes).

---

## D9. Search architecture: Graphiti-inspired, zero LLM calls on the query path

> **D98 amendment (2026-08-27).** Any Ladybug/P2 snapshot, generation,
> Cypher, or community statement in this decision is superseded. Independent
> semantic/BM25 channels, RRF, and zero-LLM query execution survive; graph
> distance/expansion now uses the bounded live PostgreSQL graph contract.

**Status:** channel/RRF/no-LLM decision remains binding; Lance-specific
placement is superseded by D94.

**Decision.** Parallel retrieval channels (semantic over PostgreSQL P1 fact
labels + claims/chunks, pg_textsearch BM25 over claims/chunks, structured
scalar lookups, registry entity resolution) fused with **RRF**;
reranked by **graph distance from focal entities** (bounded live PostgreSQL
frontier traversal) and
**evidence count**; optional cross-encoder as a flagged final stage. Composable primitives
plus named **search recipes** (`relation_hybrid_rrf`, `relation_near_entity`,
`claims_verbatim`, …). Hard rule: no LLM calls in the core search path.

**Context.** Graphiti's search stack (edge-fact embeddings + BM25 + graph traversal, RRF
default, node-distance/episode-mentions/MMR/cross-encoder rerankers, canned recipes, no
query-time LLM — how Zep reaches ~300ms P95), adapted to our store layout: their edge-fact
embedding maps to P1 (D94); their episode-mentions reranker is our `evidence_count`,
free from D2.

**Consequences.** Query latency is bounded by retrieval+rerank, not generation. Agents pick
strategies instead of assembling plumbing. Center-node reranking requires focal-entity
resolution first — the registry is on the hot path.

**Implemented default-path clarification (2026-07-30; storage amended by D94).**
A channel named hybrid must contain independent nominations: the shipped
claims/chunks hybrid is semantic + BM25 over the same P1 text, never two copies
of one vector search. Context results preserve fact/evidence/source grains.
PostgreSQL maintains ordinary P1 index entries automatically; generation
backfills and explicit rebuild/reindex remain controlled maintenance. Design
and rationale:
[`plan/designs/retrieval_design.md` §§3–5](plan/designs/retrieval_design.md) and
[`plan/analysis/retrieval_default_path.md`](plan/analysis/retrieval_default_path.md).

> **Refined by D87 (2026-08-10).** The hybrid nomination mechanics remain
> binding, but `question_context` is no longer the ordinary public operation.
> `testimony_context` owns the evidence-only form; `fact_context` owns
> adjudicated fact retrieval; `answer_context` is their pure two-envelope
> composition. There is no compatibility alias.

---

## D10. As-of traversal via projected graphs

**Status:** superseded by D98. As-of traversal uses deployment-scoped,
work-bounded PostgreSQL traversal with both clocks filtered during expansion.

**Decision.** Bi-temporal filtering during graph traversal is implemented with
`PROJECT_GRAPH_CYPHER` relationship predicates over the four temporal columns (project the
graph down to edges valid at `$as_of`, then traverse), since LadybugDB has no native temporal
query semantics.

**Context.** Verified: projected graphs accept rel-level Cypher predicates; nothing else in
the engine understands time.

---

## D11. Community detection runs externally

**Status:** superseded by D98. The system does not compute or persist graph
communities, PageRank, k-core, or WCC. D98 also removes the K `community` scope
and `community_changed` trigger; K retains entity, subtree, predicate, document,
and manual routing.

**Decision.** LadybugDB's algo extension ships PageRank, K-Core, and connected components but
**no Louvain/Leiden** (verified in `src/extension/extension_entries.cpp`). Community detection
runs as an external pass (igraph/graspologic) over the same Parquet export that feeds the
rebuild; results (community assignments, centrality) are written back to **Postgres**, keeping
the graph a projection (D6). Communities then serve as L3 refresh triggers ("claims in
community C changed") and salience priors.

---

## D12. Trigger model: per-document chain ends at L2; aggregates are debounced

**Decision.** L0→L1→L2 chain per document (Cloud Tasks). L3–L6 are *aggregate* layers
triggered by windows/debounce ("N new claims or T minutes"), with the rolling-window-delay
worker for hot files (index.md). Cloud Tasks: max 2 retries + dead-letter into Postgres;
idempotent workers keyed by content hash + processing version.

**Context.** "Trigger next layer when previous finishes" (v1) maps cleanly only to
per-document layers. L3+ summarize *across* documents; per-doc triggering of a serial
git-editing layer was the design's scaling bottleneck.

**Refined by D45.** The hot-file rolling-window-delay worker is superseded: the K compile driver
is the repo's only automated committer and compiles in dependency order, so hot files (the root
`index.md`) are simply the last DAG target, compiled once per cycle. The debounce/window trigger
model itself is unchanged.

**Refined by D56.** The idempotency discipline (content hash + processing version) extends one
level down: E2 keys on the **`extraction_input_hash`** (chunk text + the full context-bundle
fingerprint + extractor version), so re-ingesting an edited document re-extracts only the
changed chunks; embeddings key on (chunk content hash, embedding version). Same principle,
finer grain.

**Refined by D67.** "Max 2 retries" means one initial handler execution plus at most two
application retries: `processing_state.attempts` counts handler starts and its starting-point
`max_attempts` is three. Cloud Tasks retries transport delivery only; provider headers never
become the application counter or DLQ authority. Normalized backoff and budget-parking state lives
on the Postgres row.

---

## D13. LadybugDB accepted as the L6 engine (P2 after D14)

**Status:** superseded by D98. Retained only as the historical engine-selection
record; LadybugDB is not a runtime dependency or fallback.

**Decision.** LadybugDB (maintained community successor of Kuzu after Kuzu Inc. was acquired
by Apple and open-source development stopped, October 2025) is the L6 base: embedded,
columnar, Cypher, native paths, Parquet/Arrow interop, read-only multi-process mode.

**Context.** Confirmed via web research and a survey of the vendored source tree
(`plan/analysis/ladybug_capabilities.md`). Risks accepted: young fork; vector/FTS/algo extension
implementations live in a separate repo (not vendored) — irrelevant to our usage since
P1 vector/BM25 search uses PostgreSQL-native derived columns/indexes plus the
sole `chunk_search` sidecar (D94), while the Ladybug features
we depend on (COPY FROM, paths, projected graphs, read-only mode) are core and verified in
source.

**Reaffirmed 2026-08-14.** PostgreSQL 18 recursive CTEs and Apache AGE were reconsidered after
D94. Direct SQL is the simpler future candidate only if the product deliberately removes full
public Cypher. AGE credibly preserves Cypher and PostgreSQL-native backup mechanics, but its
label tables remain a duplicate projection, graph work shares the authority/P1 resource and
fault boundary, and the inspected PG18 shortest-path API does not expose the arbitrary
per-edge predicate needed to prove temporal filtering during traversal. Ladybug therefore
remains binding until measured operational pain and the recorded parity/isolation gate justify
a replacement. Analysis: `plan/analysis/postgresql_p2_graph_analysis.md`. Open, unchosen
proposal: `design/proposals/postgresql_p2_graph.md`.

---

## D14. Naming: three planes (E/K/P) replace the L0–L6 ladder

> **Refined by D47 and D73.** The three-plane naming stands. D47 collapsed K into one
> mechanism; D73 withdrew the K3 tier, so current Plane K is K1 plus K2 purpose scopes. The
> mapping below records the historical transition from the L-number design.

**Decision.** The system is described as three planes, each with its own internal sequence,
because the plane — not the number — determines the operational rules (trigger model, source
of truth, mutability, rebuild semantics):

- **Plane E — Evidence** (per-document processing writing into global ledgers; Postgres is
  truth): **E0 files, E1 chunks, E2 claims, E3 relations**; plus the **entity and predicate
  registries** as explicit cross-cutting substrate (layers *transform*, registries
  *canonicalize*).
- **Plane K — Knowledge** (aggregate, LLM-compiled, debounced; git is truth): **K1 general,
  K2 special-purpose scopes, K3 core beliefs**.
- **Plane P — Projections** (derived, no authority): **P1 search state**
  (PostgreSQL-native indexes plus `chunk_search`, D94), **P2 graph**
  (LadybugDB snapshots).

Mapping: L0→E0, L1→E1, L2→E2, L3→K1, L4→K2, L5→K3, L6→P2. Relations (E3) and the search
indexes (P1) previously had no name at all. L-numbers survive as colloquial shorthand;
"(formerly LX)" annotations are kept for one doc generation.

**Context.** Accepted objection O1 (`plan/analysis/objections.md`). The ladder implied a
single cascade of same-kind layers; in reality P2 is a projection of E3, not a level above
K3, and relations — the most load-bearing artifact — had no slot. Every recurring design
confusion was a plane-boundary violation: `claim_id`-on-edges (E3 vs P2), "each layer
triggers the next" (E rules applied to K), "is the graph rebuildable" (P semantics asked of
K). The asymmetry that the graph projection had a layer number while the vector indexes
didn't was a symptom of the same conflation.

**Consequences.** `requirements_v3` and `overall_design` reframed around planes;
`l6_graph_design.md` renamed `p2_graph_design.md`; future per-layer designs named by plane
(e2_claims, k_layers, …). O2 (collapsing K1–K3), if later accepted, becomes a change local to
plane K. Decision texts D1–D13 keep their original L-naming as historical record; the mapping
above translates.

---

## D15. Ontology: universal core + anchored extensions, on the registries

**Decision.** Users define their own ontology per problem; the system ships a small
best-effort core. Both live in the existing registries (D5) — ontology is content, not new
machinery:

- **Universal core, borrowed not invented**: ~8 entity types and ~10–15 predicates aligned
  with schema.org naming — *familiar, schema.org-aligned names + registry-rendered
  descriptions/examples* (LLMs interpret labels by pretrained semantics, so meaningful names
  beat arbitrary ones; **no measured schema.org-vs-good-synonym delta is claimed**). Concrete
  seed core fixed in D18.
- **Extension rule — extend, never fork**: every user-defined type declares a core parent
  (`ResearchPaper ⊂ Document`); predicates may too. This keeps blocking, graph queries, and
  cross-scope retrieval working at the core level over any custom domain.
- **Domain/range constraints** on predicates (`works_at: Person → Organization`) —
  lightweight typed columns that mechanically reject a class of extraction hallucinations.
- **Prompts render from the registry** (types/predicates/descriptions/examples): defining a
  scope = editing rows, not prompt engineering; prompt-version tracking (D12) captures
  ontology changes.
- **Deliberately not OWL**: parent-links + domain/range replicate most benefits without
  permanent reasoner/tooling cost. User-supplied OWL can be imported into the registry.

**Context.** Multiple K2 scopes are domain ontologies in disguise; a fixed universal ontology
either bloats or strangles them. The `other:` escape (D5) becomes the discovery/promotion
funnel. (An external-authority resolution tier was considered and later dropped — see D20.)
Three speeds, one registry: core (slow, each element a commitment) → scope extensions (fast,
each an experiment) → `other:` (ungoverned, monitored). Analysis:
`plan/analysis/entity_registry.md`.

**Consequences.** Adding types/predicates = inserting rows. Retyping is visible
through the live PostgreSQL graph views without a graph rebuild (D98). Only splitting heavily-used types/predicates is expensive —
hence the small core. Seed lists and constraint tables go to `registries_design.md`.

---

## D16. One graph, many lenses: scopes never get their own graph

> **D98 amendment (2026-08-27).** Scopes share the live PostgreSQL graph and
> entity space. `PROJECT_GRAPH_CYPHER`, Ladybug/P2 rebuilds, and materialized
> filtered graph snapshots are removed; `scope_interests` is a query/compile-
> time predicate and metadata footprint over live graph/fact views.

**Decision.** Multiple K2 scopes (projects, team profiling, …) share one live
PostgreSQL graph and one entity space. Scopes get ontology extensions (D15)
and query/compile-time lenses declared through `scope_interests`; the lens
selects predicates and metadata from shared live graph/fact views. It never
creates another graph or materialized graph-data snapshot. A future
performance accelerator requires a separate measured decision and cannot
become truth or weaken deployment isolation.

**Context.** Separate per-domain graphs would re-fragment identity — the exact disease the
registry cures — and kill cross-scope queries ("which team members worked on projects
connected to X?"), which are the point of having a graph. Plane discipline: K2 scopes are
consumers of plane E, not owners; a scope owns its compiled markdown, never facts.

**Consequences.** New scope = git directory + registry rows (predicates/metadata
lens) + extraction interests; never a new database. Rule of thumb: **scopes multiply;
truth doesn't.** Scope-sharing applies *within* one deployment only — separate
deployments (assistant, agency, client projects, …) are fully
independent instances with separate entity spaces (`registries_design.md` §1, deployment
model).

**Refined by D50 (trust model).** The former access-isolation arm (filtered
snapshots plus API-level authorization) is withdrawn: content-level
authorization inside a deployment is a library non-goal — a deployment is one trust domain,
and data with a different trust boundary belongs in a **separate deployment** (this decision's
own last sentence, promoted to the isolation mechanism). D98 also removes the
filtered graph-snapshot performance arm.

---

> **D17–D30 provenance.** D17–D24 formalize the entity-registry research
> (`plan/analysis/registry_research/SYNTHESIS.md`, objection O5); **D25 records the rejection of the
> value-gate mechanism** (O3 premise accepted, gate-as-answer rejected —
> `plan/analysis/value_gate_research/SYNTHESIS.md` + `plan/analysis/claimify_research/SYNTHESIS.md`);
> **D26–D30 are withdrawn-in-place** (folded into D25). Both
> efforts read 12 systems at source + literature, with adversarial fact-checkers. Where a
> number is involved it is a **placeholder to be measured on a golden set / corpus slice**, not
> a committed constant — the spikes are listed in each SYNTHESIS §5.

## D17. Canonical resolution tier cascade (T0–T4), block-loose / decide-tight

**Decision.** One authoritative entity-resolution cascade, replacing the scattered/folklore
thresholds: **T0** exact match on the LLM-emitted canonical name form (§5/D19) → **T1** fuzzy
*blocking* (`pg_trgm` GIN, recall-first low floor — candidate generation, NOT a decision) →
**T2** phonetic (Daitch-Mokotoff, **not** Soundex) → **T3** embedding similarity (PostgreSQL P1, residue
only) → **T4** LLM adjudication (small→frontier) on the ambiguous middle band → human review for
high blast-radius. **Registry-self-contained — no 3rd-party external-authority tier (D20).** Each
tier's accept/reject bands are **per-type, golden-set-measured, versioned config** stamped with
`resolver_version`. No threshold ships without a per-type precision/recall curve.

**Context.** JW≥0.92 / cosine≥0.88 were folklore: JW 0.92 is Splink's per-field Bayes *evidence
level*, not an accept bar; benchmark spread (Magellan 98.4 clean vs 43.6 textual) proves no
global constant works. Graphiti independently arrived at the same block-loose/decide-tight shape.
(R2; refines D4.)

**Consequences.** LLM cost scales with ambiguity. Blocking sets a hard recall ceiling, so cheap
tiers *escalate* near-misses, never auto-reject. Feeds O6 (every threshold needs the golden set).

**Refined by D95.** T0 exact lemma **never** auto-merges: it only lists
distinct candidate ids. T3 (profile embedding) may accept repeats; T4
handles empty/conflict/many candidates. The same spelling may be two
`entity_id`s. Profile text is T3/T4 evidence, not the identity key.

## D18. Ontology seed core — 8 types + 14 predicates, schema.org-anchored, domain/range not OWL

**Decision.** Seed core: 8 entity types (`Person`, `Organization`, `Place`, `Document` (a root
anchored to schema.org `CreativeWork`), `Event`, `Concept`, `Project`, `Product`) + 14 predicates with
`subject_type`/`object_type` columns
(`works_for, member_of, affiliated_with, located_in, part_of, authored, created, about,
knows_about, knows, participated_in, works_on, founded, related_to`). `related_to` is the
predicate-side core parent for extend-never-fork (D15). Time is bi-temporal edge metadata, never a
predicate or Date-node. Enforce domain/range exactly as Graphiti's `edge_type_map[(src,tgt)→[rel]]`
— the only structural ontology gate any surveyed production system ships. Schema.org property
mappings get a spot-check before freezing.

**Context.** Concretizes D15. Graphiti's `edge_type_map` is the validated mechanism; Cognee loads
OWL but enforces no domain/range. The "familiar names help extraction" claim is true in spirit
(pretrained semantics) but no measured schema.org-vs-synonym delta is asserted. (R5.)

**Consequences.** Work-shaped concepts (Task/Decision/Goal) stay out of the core but ship as a
system-provided **extension pack**, enabled per deployment — full entity status without a core
commitment; `Decision` standing rides on bi-temporal relations, so reversals are ordinary
supersession (`registries_design.md` §4, extension packs).

**Scope clarification (D41).** "Time is never a predicate or Date-node" governs the **relation/graph**
representation of time. A claim's immutable asserted-validity interval (D41) is *claim metadata*, not
a relation object/predicate or a Date-node, so it is fully compatible — D18 is unchanged.

**Refined by D64.** The seed core is now 8 types + **16** predicates: `uses`
(Person | Organization → Product) and `reports_to` (Person → Person) promoted from the
registries §4 watchlist — the first watchlist graduations. Everything else here is unchanged.

**Refined by D69.** The eight roots, all required/behavior-bearing row values, and all concrete
signatures are fixed by the inline registry manifest. `Document.parent_type = NULL` and its
`schema_org_ref` is `https://schema.org/CreativeWork`; `CreativeWork` is not a registry row.

**Refined by D96.** Entity types and domain/range as a write gate are
**withdrawn**. Predicates remain (D5). Dual-role facts are two ids; no
Organization hat is required for “works for me.”

## D19. Coref is satisfied inside the E2 extraction call (no dedicated model)

**Decision.** Coref is the guarantee that no claim leaves E2 with a dangling pronoun — satisfied
**inside the E2 extraction call, for all languages** (the LLM reads the chunk/document and writes
claims with referents resolved). **No dedicated coref model or pre-pass** (CorPipe/CorefUD).
Rationale: the extraction LLM is already called, so coref — a per-mention understanding task —
rides that call at ~zero marginal cost; a separate model would be a separate pass, separate
infra, and (CorPipe) a CC BY-NC-SA licensing exposure, to do something the LLM already does.

**Context.** Same family of decisions as entity typing (`registries_design.md` §4): per-mention
understanding (typing, coref, name-canonicalization) is free with extraction; only *at-scale
matching against the registry* (fuzzy/phonetic/embedding) needs non-LLM tiers. 6/6 surveyed
systems do coref in-call. The earlier "dedicated coref beats LLM by ~13 CoNLL F1" finding
compared older/constrained LLMs, not a frontier model extracting with full context; Czech and
other inflected languages are well-served by frontier LLMs in-context. (R1, R3; refines D4.)

**Consequences.** Cross-*document* coref ("the CEO" referring to an entity introduced in another
document) remains an open recall gap — it is not solved by intra-document coref of any kind
(LLM or model). If a *future* deployment's language is genuinely poorly served by frontier LLMs
(a low-resource language — not Czech), a specialized model could be reconsidered as a
per-deployment alternative — a documented option, not part of the system.

## D20. No 3rd-party external-authority tier — resolution is registry-self-contained

**Decision.** Entity resolution does **not** depend on 3rd-party external registries (Wikidata,
OpenAlex, DOI, ORCID, LEI, …). Identity is resolved entirely from the system's own data via the
T0–T4 cascade (D17). The earlier "tier-0 authority" idea is **dropped from scope.**

**Context.** Two reasons. (1) **Coverage:** public registries only know publicly-notable entities
(listed companies, published researchers, papers) — near-zero coverage for the actual target
deployments, whose data is internal/private/domain-specific (a manufacturer's internal systems,
a personal assistant's contacts, statutes, internal projects). (2) **Dependency:** they put an
external, rate-limited, license-encumbered service on a core write path for little return. The
research (R4) recommended them as an *optional, never-gating accelerator*; for these deployments
that accelerator rarely fires, so the simplicity of dropping it wins.

**Consequences.** The cascade starts at T0 = exact match on the LLM-emitted canonical name form.
The genuinely valuable "authority" case is **internal/domain authoritative IDs** (a source
system's own keys, legal citations) — *not* 3rd-party registries; that is a **future
per-deployment connector** (a documented alternative, not part of the system), which would attach such IDs as aliases (never as the
canonical `entity_id`). No `external_ids` table ships now.

## D21. Clustering algorithm + incremental procedure + reversibility records

**Decision.** Decision clustering = **connected-components-to-gather** (with a black-hole guard:
raise threshold + repartition above component size T) → **HAC distance-cut inside each blob**
(never bare transitive closure; never Louvain/Leiden for ER — that's D11 community detection).
Write-path incremental = max-both assignment + **nDR n=1** (re-cluster only the 1-hop neighborhood;
order-independent; n=2 only when a hub is touched). Reversibility state lives **only in Postgres**:
`resolution_decisions` (append-only, `superseded_by`), `merge_events` (append-only, pre-merge
membership snapshot), `merged_into` redirect chain, optional negative/exclusion edges. A
generic-identifier guard (Senzing) down-weights + re-evaluates an alias that suddenly links many.
P2 rebuild (D7) re-points edges on merge/un-merge for free.

**Context.** No OSS system (Splink/dedupe/Zingg/Graphiti) ships un-merge — building it in Postgres
is correct, not over-engineering. dedupe uses exactly HAC `linkage(centroid)`+`fcluster(distance)`
+ a `max_components` guard. (R8.)

## D22. Golden-set + evaluation plan

**Decision.** Two **separate** assets: a **golden EVAL set** (unbiased, measures P/R, tunes
thresholds) and a **training set** (built only if a learned matcher is ever added; AL-sampled,
biased, never used to measure). The eval set: ~200 human-verified labeled pairs/type (~100 hard
positives incl. synthetic father/son/inflection/married-name + ~100 hard negatives; ~400/type for
auto-merge-critical types), blocking-stratified positive over-sampling, **Wilson** CIs, per-tier
metrics, and a canary regression harness re-run per `resolver_version`. **Break the circularity:**
the cascade/LLM may *propose* candidate pairs, but measurement labels must be **human-adjudicated**.
The eval plan also covers the **retrieval half of O6** (recall@k per search recipe, rerank-weight
tuning, contradiction-detection precision). A learned matcher + active-learning training loop are
a documented **optional extension** of the cascade (D17), kept strictly separate from the eval
set — the core design resolves with the deterministic + LLM tiers, not a learned matcher.

**Context.** Closes O6's ER half concretely; the same eval set also seeds E2 Selection's
claim-verifiability golden set (D25 — junk-control moved to in-call Selection, not a salience gate).
(R7, O6.)

## D23. Registry scale & schema

**Decision.** D94 amends the partition estate to exactly eight parents. Six large append-only tables use
monthly RANGE partitions managed by `pg_partman`: `mentions(created_at)`,
`resolution_decisions(decided_at)`, `chunks(created_at)`, `chunk_claims(created_at)`,
`claim_extraction_decisions(decided_at)`, and `testimony_currency_events(occurred_at)`.
`claims` becomes non-partitioned so its current-testimony HNSW/BM25 indexes
form one global retrieval corpus; pg_textsearch partition-local statistics
cannot provide one comparable cross-month BM25 rank. Two evidence joins use static HASH partitions:
`relation_evidence` by `relation_id` with PRIMARY KEY (`relation_id`, `claim_id`), and
`observation_evidence` by `observation_id` with PRIMARY KEY (`observation_id`, `claim_id`). Each
HASH parent has 64 migration-created children; 64 is a measured starting point, not a committed
constant. The hot partitioned tables remain btree-only to cap write amplification.

Do **not** partition `entities`/`aliases` (the blocking targets, ≤10⁷). Under D68's
schema-/database-per-deployment contract, the blocking GIN indexes are single-column:
`ix_entities_name_trgm` on `entities USING gin (normalized_name gin_trgm_ops)`,
`ix_aliases_lemma_trgm` on `aliases USING gin (normalized_lemma gin_trgm_ops)`, and
`ix_aliases_lemma_dm` on `aliases USING gin (daitch_mokotoff(normalized_lemma))`. The alias key is
`normalized_lemma`, not `normalized_name`. Keep the btree composite
`(subject_entity_id, predicate[, object])` on `relations`. Supersession + tiers T0–T2 run in
Postgres authority; embedding tier T3 uses the current derived vector on the
canonical entity/profile row (D94). The HNSW columns share the database but are
not exposed through the public authority surface. Load-test a representative corpus
slice before revising partition cadence, HASH child count, or index choices. Size row counts and
the load test against full, ungated extraction volume (D25).

**Context.** Monthly partitioning fits append-only rows whose transaction time correlates with
their access path. It does not fit `relation_evidence`: evidence for one relation accumulates over
that relation's lifetime, so month cannot prune the hot `relation_id → evidence` lookup and cannot
support the evidence-once primary key. HASH partitioning makes that lookup prune to one child and
lets Postgres enforce the pair uniqueness directly. The static observation-evidence family uses
the same access pattern. The large evidence tables are never fuzzy-scanned; fuzzy blocking stays
on the unpartitioned registry targets. (R9; `postgres_schema_design.md` §§9, 9.A, 12.)

## D24. Review tooling — build a thin Postgres-backed cluster-review queue

**Decision.** **Build** (don't adopt as system-of-record) a thin CLI cluster-review queue over
Postgres; no OSS tool offers cluster-queue + append-only reversible verdicts + provenance +
blast-radius gating. Review **clusters, not pairs** (pairwise is quadratic); route only the
`expected_impact = blast_radius × (1 − confidence)` middle band to humans; high-degree hub merges
never auto-accept. Borrow Splink's waterfall (evidence panel), Zingg's 3-way verdict (ergonomics),
OpenRefine's cluster-card-with-exclude (interaction). Every action appends a reversible,
provenance-stamped, redirect-preserving record (D21). The design is the CLI queue; a web UI /
Argilla is an optional addition if review volume ever justifies it, not part of the core design.
(R10.)

## D25. No pre-extraction value/salience gate — junk-control is in-call at E2 Selection + D2

**Decision.** There is **no E1.5 stage and no value/salience gate**. Plane E is `E0 → E1 → E2 → E3`;
every document that survives chunking is fully extracted. Junk-control moves to where junk is cheapest
and safest to identify: **E2 Selection** (Claimify proposition-level verifiability KEEP/REWRITE/DROP,
in-call, zero marginal LLM calls — the ablation-proven highest-leverage stage, element-coverage
macro-F1 83.7→54.4 when removed) and **D2** (corpus redundancy collapses into one relation +
`evidence_count`, so duplicate *facts* cost nothing in the graph). Exact-content-hash dedup remains as
the **D12/D7 idempotency** mechanism (a `content_hash` short-circuit at the worker boundary), never as a
value tier. The E0 **PageIndex section path/role is fed into the E2 call** so Selection can drop
references/boilerplate/intro/conclusion at proposition grain (the structural signal is *absorbed into*
extraction, not used as a binary pre-skip).

**Context.** O3's *premise* (most raw content is low-value; junk poisons downstream) is **accepted**;
its proposed *mechanism* (a pre-extraction gate) is **rejected**. The only "value" rung (a distilled
salience classifier) is unbuilt and golden-set-dependent; the novelty rung is a corpus-scale ANN at 10⁸
claims (the gate's own #1 self-defeat risk — it becomes a new fleet-scale stage); the honest cost lever
is ~1.5–2×, not 10×, and the 10× lived entirely in the DEFERRED tier whose two Postgres state tables +
transactional outbox + `SKIP LOCKED` queue + heartbeat reconciler + four promotion triggers are pure
complexity for that 1.5–2×. Claimify's Selection ablation makes the in-call verifiability filter the
highest-leverage junk control and it is free; D2 already neutralizes redundant-fact cost. The gate also
concentrated the system's highest-severity correctness risk (the zombie-fact / supersession-skip case —
silently withholding the only superseding evidence) and the circular never-defer-by-predicate problem;
extracting every section removes that failure mode at its root. (O3 premise; value_gate_research V1–V6;
claimify_research C4/C8.)

**Consequences.**
- Plane E reverts to `E0→E1→E2→E3` (`overall_design.md` §4). Paying E2 on everything is the ~1.5–2× the
  gate would have saved; Selection's in-call precision means that spend buys *clean* claims.
- **R9 / D23 re-stamp:** the three 10⁸ tables (`mentions` / `resolution_decisions` /
  `relation_evidence`) are sized against **full extraction** again (`f_full = 1`); the favorable gate
  shrink is withdrawn and R9's partition/index load-test plans against ungated volume.
- The E1.5 design doc is retired; `plan/designs/e2_e3_claims_relations_design.md` §4 records the
  non-goal (why there is no value gate) and what handles junk instead.
- The recall-conservative discipline (defer-don't-DROP) relocates one grain down, to E2 Selection (the
  claim-layer D35 proposal): conservative KEEP bias, never-drop lexical classes, `kept_flagged` (no hard
  delete), DROP ledger, per-fact canary CI.
- **Future option (documented, not built):** if a corpus slice ever shows extraction cost is dominated
  by structurally-skippable sections, a *trivial deterministic* section filter
  (`pageindex_node_type NOT IN {references, bibliography, nav, boilerplate, legal}` on E2 entry — no
  classifier, no ANN, no defer machinery) is the cheap add-back, gated on a measured break-even. This is
  explicitly **not** a smart gate.

## D26. *(withdrawn — folded into D25)*

Was "the gate is a nested cheap-first cascade" (T-dup → T-struct → T-novel → T-salience). Withdrawn
with the gate (D25). The cheap-first philosophy survives unchanged in D4 (supersession) and D17
(resolution). Exact-content-hash dedup survives as plain D12/D7 idempotency, not a value tier.

## D27. *(withdrawn — folded into D25)*

Was "defer decision is durable, versioned Postgres state." There is no defer decision; the
`gate_decisions` / `document_extraction_state` / `salience_gate_versions` tables are not built.

## D28. *(withdrawn — folded into D25)*

Was "lazy promotion triggers." No DEFERRED tier, so no promotion. K2 scope-interest (D16) remains a
query/compile-time selection over fully-extracted facts, never a promotion trigger.

## D29. *(withdrawn — folded into D25)*

Was "defer-don't-DROP recall envelope." The recall-conservative discipline relocates one grain down to
E2 Selection (the claim-layer D35 proposal): conservative KEEP bias, never-drop lexical classes,
`kept_flagged` (no hard delete), an append-only DROP ledger, per-fact canary CI — defer-don't-DROP at
the proposition grain, where junk is actually identifiable.

## D30. *(withdrawn — folded into D25)*

Was "gate cost & break-even discipline." No gate to cost. The break-even discipline survives as a
property of E2 spend (the claimify cost model) and of the documented trivial structural-skip add-back
(D25, future option).

---

> **D31–D35 provenance.** D31–D35 formalize the Claimify E2 research
> (`plan/analysis/claimify_research/SYNTHESIS.md`, the de-contextualization + claim-level-selection
> effort); the binding design is `plan/designs/e2_e3_claims_relations_design.md`. Numbers/thresholds
> are placeholders to be measured on a golden set / corpus slice (see that SYNTHESIS §4 spikes).

## D31. E2 is a Claimify-staged extractor over a context bundle (two calls)

**Decision.** Claim extraction runs over a **context bundle** (target chunk + document header +
PageIndex section path + the E1 context prefix + ±N same-section neighbour chunks + entity hints),
never a bare chunk. The model does three jobs: **Selection** (keep only specific, verifiable
propositions; drop opinion / advice / hypothetical / generic / intro-conclusion / lack-of-info; keep
only the verifiable span of a mixed sentence), **Disambiguation/decontextualization** (resolve
references from the bundle and *only* the bundle; add the minimum context; discard when there is no
confident reading; coref in-call per D19), and **Decomposition** (atomic, attribution-preserving
claims). It runs as **two calls** (Selection separate from a fused decontextualize + decompose + ground
call); a one-call collapse is permitted only after an ablation. The literal three-calls-per-sentence
loop is not used at scale.

**Context.** Refines D4 (cheap-first) and realizes D19 (coref in-call). Selection is split out because
it is the highest-leverage stage and carries the opposite instruction to decontextualization. Design +
worked example: `plan/designs/e2_e3_claims_relations_design.md`. (C1–C8.)

**Refined by D58 (batched extraction).** The two-call shape applies to a **batch window** (a
section's contiguous chunks in one call pair) exactly as to a single chunk — the window is the
extraction unit, the calls are still two; bookkeeping stays per-chunk (per-chunk
`processing_state` commits keyed by `extraction_input_hash`; the batch's calls billed to the
claiming row — refined 2026-07-18).
`e1_chunks_design.md` §6.

## D32. Claim grounding is layered and dual-field, not verbatim-substring

**Decision.** A claim stores both a standalone `claim_text` and a verbatim `source_span` + character
offsets, plus an `added_context[]` list naming each added substring's bundle source. Acceptance layers,
cheapest first: (1) deterministic **anchor** — the source span is a real slice of the chunk; (2)
deterministic **window-membership** — every content token in non-empty added text must occur in the
union of source-derived bundle elements, while only a closed set of functional scaffolding tokens may
be absent and the declared source tag remains advisory provenance (rejects fabrication, not
mislabeling); (3) an in-call **entailment self-verdict** (incl. the "*X said* Y entails *X said Y*,
not *Y*" rule); (4) a **sampled independent** entailment audit (never per-claim). Replaces the
verbatim-substring gate, which is incompatible with decontextualization. No external knowledge.

**Context.** A decontextualized claim is a rewrite, so it is never a verbatim substring; grounding must
be provenance + entailment, as every surveyed decompose-then-verify system does. (C6.)

**Amendment (2026-07-29, union grounding).** Layer 2 searches the TARGET CHUNK slice, deterministic
document header, both available same-section neighbours, and stored context prefix as one
source-derived membership union. `added_context.source_kind` is still persisted as a best-effort
pointer but cannot veto a verbatim union match. The #161 GLM-5.2 smoke loss ledger supplied the
production evidence: 371 of 411 grounding rejections were `added_context_unverified` mislabel
deaths; sampled correct decontextualizations added names present in TARGET CHUNK turn lines but
tagged them `header` (258) or `prefix` (99), and only 27 claims from 19 documents survived.
**Section summaries remain outside the union (the stored prefix, though LLM-derived, is a designed union member per D79's accepted second-order channel); D79 consumption
rules are unchanged.**

**Amendment (2026-07-29, token-tolerant union grounding).** Layer 2 checks an addition at token
grain instead of requiring its whole connective phrase to occur verbatim. It tokenizes Unicode words
and punctuation and splits possessives (`Caroline's` → `caroline` + `'s`). Every token must either
occur case-insensitively at a word boundary somewhere in the same source-derived union or belong to
this closed functional allowlist:

- attribution scaffolding: `said`, `says`, `saying`, `asked`, `asks`, `told`, `tells`, `mentioned`,
  `mentions`, `wrote`, `writes`, `according`;
- pure function words: `that`, `the`, `a`, `an`, `of`, `to`, `in`, `on`, `at`, `and`, `or`, `is`,
  `was`, `were`, `be`, `been`, `she`, `he`, `they`, `her`, `his`, `their`, `it`, `its`, `this`,
  `these`, `those`, `with`, `for`, `as`, `by`, `from`;
- punctuation: `,`, `.`, `:`, `;`, straight or curly single/double quote tokens, and `'s`.

Empty or whitespace-only additions are no-ops. **Numeric tokens are never allowlisted**: a number
such as `2022` must occur in the union or the addition is rejected, preserving #158's rule that a
computed date cannot enter claim text through `added_context`. Proper names and every other content
noun, verb, or adjective likewise always require a union match; `Melanie` therefore passes beside
the colon in a `Melanie:` speaker label, while an absent `Paris`, `pride`, or `parade` does not.
The preserved invariant is that **every content token of every addition remains traceable verbatim
(ignoring case) to source-derived bundle text**. The allowlist can supply grammar and
attribution/decontextualization connective tissue, never outside facts.

This amendment resolves a measured contradiction in the conv-26 GLM-5.2 E2 07h loss ledger:
of 144 `grounding_rejected` decisions, the dominant class (about 40–60%) was scaffolding the prompt
itself mandates — preserve attribution ("X said Y") and resolve pronouns/possessives — rejected only
because exact connective strings such as `said` (13 rows), `Caroline said` (9), `said she` (7), or
`Caroline's` (5) did not occur whole in a bundle element. Thirteen empty additions were also rejected
despite adding nothing. Gold facts died as a result: the source turn `Melanie: Yeah, I painted that
lake sunrise last year!` yielded a correct attributed claim, then layer 2 rejected
`Melanie said, ` because the source used a speaker-label colon. Token-grain matching admits that
prompt-required scaffolding without weakening content or numeric traceability. Rejections still drop
the claim and are ledgered; `edit_detail.failed_tokens` names the tokens that failed.

**Refined by D65 (media).** For media-derived documents grounding is **two hops**: the anchor
(layer 1) proves the claim derives from the *representation* (document.md); it cannot prove the
ASR heard or the VLM saw correctly. The layer-4 sampled audit therefore becomes
**modality-aware** — the auditor listens to the referenced time interval / looks at the
referenced frame or region, never only the derived Markdown (which would grade the converter
against its own output). `plan/designs/media_design.md` §4.

## D33. E2 selection-drops and decontextualization edits are append-only, versioned state

**Decision.** Every Selection drop (with reason) and every decontextualization edit is written to an
append-only, version-stamped `claim_extraction_decisions` table. Rebuild reads stored claims +
decisions and never re-calls the model (the LLM rungs are replay-from-storage, like any
non-deterministic stage — D7); the per-chunk worker is idempotent on content-hash + extractor version
(D12). Drops become auditable and recoverable (a better prompt re-examines only the drop set), and the
eval metrics come for free.

**Context.** The same durable-state discipline the resolution and supersession layers use, applied to
the extraction transcript. (C8.)

**Amendment (2026-07-27, issue #161).** The transcript also records the Claimify-stage losses that
were previously silent: `claimify_omitted` (a kept span the model returned no claim for — including
spans not verbatim-findable in the document, which formerly vanished traceless) and
`grounding_rejected` (a returned claim a D32 gate rejected, with the gate named in `edit_detail`).
Accounting rules: every returned claim independently ends accepted or rejected; every keep with no
range-attributable returned claim gets exactly one omission row; orphan rejections suppress no
omission. On D56 zero-claim reuse the prior transcript is copied forward — the `no_info` marker is
fabricated only when the prior transcript is itself empty. Detail:
`plan/designs/e2_e3_claims_relations_design.md` §3 amendment.

## D34. E2 Selection is the value filter — there is no pre-extraction value gate

**Decision.** Junk-control lives at the **proposition grain**, in-call: Selection (D31) decides
**verifiability** — not relevance (handled by K2 scope views, D16) and not ambiguity (the
disambiguation step). Together with **D2** redundancy-collapse (duplicate facts → one relation +
`evidence_count`) and exact-content-hash idempotency (D12), this replaces the pre-extraction value/
salience gate, which is **not built** (D25). Selection's metrics stand alone.

**Context.** The chunk-level value gate (former D26–D30) was over-engineered for a ~1.5–2× lever and
concentrated the worst correctness risk; the in-call verifiability filter is cheaper, safer, and
ablation-proven. (D25; value_gate_research; claimify_research C4.)

**Refined by D59.** The opinion-drop narrows to *unattributed* opinion: a stance attributed to a
resolvable holder is a verifiable proposition about the holder and is kept, normalizing to a
holder-anchored observation. Verifiability remains the keep/drop line — attribution is what
makes a stance verifiable.

## D35. Selection recall envelope (defer-don't-DROP, one grain down)

**Decision.** Because a Selection drop is a hard delete with no second-copy net for a uniquely-attested
fact, Selection biases **conservative KEEP**; protects **never-drop classes** (quantities, dates,
named-entity + predicate, change-of-state language) regardless of phrasing; offers a low-confidence
**`kept_flagged`** outcome (mark-for-review, not delete); records all drops in the D33 ledger for
version-filtered re-examination; and is tuned against **per-fact** false-drop (canary CI), never a
corpus average.

**Context.** Mirrors the recall-conservative discipline the dropped gate carried (former D29), relocated
to the grain where junk is actually identifiable. (C4; D33.)

---

> **D36–D40 provenance.** D36–D40 formalize the E0 (document layer) + corpus-filesystem analysis
> (`_feature_planning/e0/` — Claude + Codex). Binding design: `plan/designs/e0_files_design.md`.
> Numbers/choices are starting points to measure (CLAUDE.md), not committed constants.

## D36. E0 is the document layer — a chain of idempotent sub-workers, not a renumber

**Decision.** E0 stays a single product layer (*files / structured document*) implemented as a short
chain of separately-idempotent, separately-observable sub-workers: **ingest** (store raw + hash) →
**convert** (raw → Markdown) → **structure** (PageIndex tree + roles + spans + summaries + placement
hint) → **crossref** (citations / document links). PageIndex post-processing is **not** promoted to a
top-level stage; E1/E2/E3 are **not** renumbered.

**Context.** The E-numbers name *product layers* (files → chunks → claims → relations); PageIndex
structure is metadata *about the document*, before chunking, so it belongs to E0. Renumbering would
churn every doc that references E1–E3 for no architectural gain (the L→E rename cost is the cautionary
precedent). Each sub-worker keys idempotency on `content_hash + its own version` (D12) so a single
config change doesn't rerun the whole chain.

**Consequences.** E0's output contract is unchanged (durable artifacts + queryable structure, ready
for E1). Operational complexity is handled by decomposition, not numbering.

## D37. E0 storage split — GCS holds bodies, Postgres holds the index; ID-addressed; mount-ready

> **Refined by D51.** The raw bucket's "never mounted" arm is reversed: raw is now mounted
> read-only but **off the navigation path** (explicit pointers only), with mandatory data-access
> audit logging and mime-routed storage classes (so "cold" is no longer blanket). The storage
> split, ID-addressing, and Postgres-metadata rules below are unchanged. Rationale in D51 and
> `e0_files_design.md` §2/§5.
>
> **Refined by D55.** `content_hash` identifies a document **version** (deduplicated as a
> content object); the *logical document* is a **lineage** identified by connector-native
> `(source_kind, source_ref)`, with append-only version rows. The GCS layout
> (`<doc_id>/<content_hash>/…`) already anticipated exactly this. `UNIQUE(deployment,
> content_hash)` moves to the content-object/version level. Rationale in D55 and
> `evidence_lifecycle_design.md` §2.

**Decision.** Two GCS buckets per deployment: a **raw** bucket (immutable originals, cold, strict
IAM, **never mounted**) and an **artifacts** bucket (Markdown + `pageindex.json` + conversion
sidecars, standard storage, reachable from the mounted corpus filesystem). Canonical objects are
**ID-addressed** (`doc_id` + `content_hash`), never title-addressed. **Postgres never stores document
body/Markdown text** — only compact query-critical metadata (identity, versions, state, artifact
URIs, hashes, costs, and the section index: titles/paths/roles/spans/summaries). `content_hash`
(sha256 of raw bytes) is the idempotency key (D12) and the only surviving dedup (idempotency, not a
value tier — D25).

**Context.** Postgres is the E-plane ledger; GCS is the blob store. Storing 1M document bodies in
Postgres bloats it for nothing and puts text where agents can't mount it. The precise rule (bodies in GCS, queryable metadata in Postgres) keeps the spine queryable while the
text lives where it can be mounted.

**Consequences.** Postgres stays lean; the artifact store is mount-friendly (D40); a converter
change re-converts by version (D7).

## D38. Configurable raw → Markdown conversion module

**Decision.** A pluggable, **configurable** conversion module (a reusable open-source library):
interface `convert(bytes, mime, hints) -> { markdown, blocks[] }` where `blocks` carry **page +
character offsets back to the source** (load-bearing for E2 grounding D32, chunking, PageIndex). A
**router** selects a converter by input type per-deployment config (digital PDF → text extract;
scanned/complex PDF + images → OCR e.g. Mistral OCR; office/html/email → markitdown; text →
passthrough). **Versioned** (`converter_version`): a converter/routing change re-converts affected
docs and rebuilds downstream.

**Context.** Conversion quality gates the whole pipeline, so it is pinned and reprocessable.
Generalizes common practice (Mistral OCR for PDFs, markitdown elsewhere) into a routing table. (User
proposal.)

**Refined by D57.** `blocks[]` moves **out** of the converter contract: converters are
heterogeneous (Mistral OCR exposes only per-page Markdown; markitdown plain Markdown), so the
contract weakens to what every tool can deliver — `document.md` + a **page map** + `media[]` —
and a single shared, deterministic **blockizer** (ours, `blockizer_version`) derives the block
sequence from `document.md`. Offsets into document.md stay exact (grounding, D32); source
back-pointers become best-effort provenance tiers. The conversion route is pinned per lineage.
`e1_chunks_design.md` §2.

**Refined by D65 (media).** The router gains three media routes (audio → diarized ASR; video →
ASR + adaptive keyframes + optional shot notes; standalone picture → VLM description behind a
document-vs-picture discriminator), and the contract generalizes once more: the page map
becomes a **source map** (character intervals → typed locators: page / image region / time
range / video region) and the output adds a **manifest** recording route, models, versions,
and per-section derivation labels — `convert(bytes, mime, hints) → { document.md, source_map,
derived_assets[], manifest }`. `plan/designs/media_design.md` §2/§4.

## D39. PageIndex provides per-document structure — sidecar + PG index, structure-only, summaries kept, placement-hint-extended

**Decision.** PageIndex builds a per-document hierarchical tree (`node_id`, `title`, `summary`,
nested nodes, spans). It is used as **structure, not a retrieval engine** (we keep chunk + embed +
graph, D8/D9). Stored **both** as a `pageindex.json` sidecar (artifact) **and** a Postgres
`document_sections` index (queryable path/role/span per chunk for E1/E2). **Per-section summaries are
kept** (cheap, per-section) as **context never facts** — feeding E1 prefixes, navigation, and
selection-explainability; the corpus's *global* high-level picture remains the K plane's job, so it
never depends on summary quality. The PageIndex output is **extended with a `placement` hint**: a
proposed path for the document (and key sections) in the corpus's hypothetical directory tree —
advisory input to the P3 projection (D40), not a commitment.

**Context.** Structure is load-bearing (section-aware chunk boundaries + the E2 role signal); summaries
are cheap polish worth measuring, not deleting on intuition. The placement hint lets E0 seed the
corpus filesystem (P3, D40) — a per-document path guess produced where the document is freshly
understood, reconciled into a coherent tree by the projection.

**Refined by D57 (representation).** Sections are persisted as **block ranges** on the
deterministic block grid (a snap rule normalizes PageIndex's LLM-drawn spans into a well-formed
partition; sections never cut through a block; blocks are never derived from sections). The
tool, roles, summaries, and placement hints are unchanged. `e1_chunks_design.md` §3.

## D40. P3 — the corpus filesystem: a mountable, rebuildable projection

**Decision.** The system builds a **canonical corpus filesystem** (a published navigable view, no source-of-truth): a materialized **GCS bucket
laid out as a directory tree** organizing the whole corpus for agent navigation, **mounted read-only**
to agentic workers (`gcsfuse`). It is a **P-plane projection** (P3) — derived, holds no
source-of-truth, **rebuilt from Postgres + document artifacts**, like P1/P2. A projection worker
materializes/maintains the tree from the **placement hints** (D39) + entities/relations + the K-plane
structure: folders by topic/source/time/entity, leaves linking to per-document artifacts, a generated
`_index.md` / `llms.txt` at each level. K (compiled understanding) and P3 (navigable index over
sources) cross-link and compose; they are not duplicates.

**Context.** Agentic consumers need to browse the memory as a filesystem, which requires a navigable
corpus tree. Cross-document organization is a function of evolving knowledge, so it must be a
**rebuildable projection** (P3), not E0 state — per-document structure is E0 (intrinsic), corpus
organization is P3 (derived). Realizes the "extend PageIndex with placement → projection materializes
a mounted bucket tree" design.

**Consequences.** Agents browse a stable, navigable hierarchy and drill into raw sources; the tree
reorganizes as the corpus grows without touching truth (placement hints are inputs). New projection in
plane P alongside P1 (search) and P2 (graph).

**Refined (P3↔K reconciliation — closes `questions.md` #25).** The phrase "+ the K-plane
structure" above is corrected: **K is a cross-link, not a structural input** — P3's *shape* is
built from Postgres (placement hints, entities/relations) + the E0 artifacts only, per the
binding `e0_files_design.md` §6. This keeps P3 rebuildable from the E spine (it does not
inherit the K repo's source-of-truth burden or its deletion-manifest reach); P3 `_index.md`
files and K pages link to each other, in both directions, as consumers — never as inputs.

---

## D41. Claims carry an immutable, source-asserted validity interval (asserted vs. adjudicated time)

**Decision.** A claim gains a structured **world-time interval as the source asserted it** —
`claim_valid_from` / `claim_valid_until`, plus a `claim_valid_precision` (year/quarter/day/…/open/
unknown) and a `claim_valid_kind` (proposition-validity vs. event-time vs. measurement-period). It is
the structured form of the date decontextualization already resolves into the claim text ("launched
*in 2024*"), emitted in the same E2 call. Date text introduced through `added_context` remains
**grounded** by D32 — every numeric token must exist in the source-derived union — while #158 governs
computed structured dates separately. It is **evidence about *when***, epistemically identical to
`claim_text` (evidence about *what*) and `source_span` (evidence about *where in the source*).
Adjudicated, current-fact validity stays **exclusively on relations** (`valid_from`/`valid_until` +
`invalidated_at`, D3).

**Why this is not a second validity authority** (stated so a future reader need not re-derive it).
Three *mechanical* properties — not the `_asserted_` naming — keep claim-validity from competing with
the relation window:

1. **Immutable** — no `UPDATE` path, no `invalidated_at`, no `status`, no `superseded_by`. A column
   that cannot be revised cannot be "current belief."
2. **Many-valued per fact** — N sources may assert N different, even contradictory, windows and *all
   stand forever*. Many-valued-by-source is the signature of *evidence* (like `evidence_count`'s N
   rows); a belief authority is single-valued-by-fact.
3. **No fact-identity** — keyed only by `claim_id`, never addressable as "the validity of fact F," so
   it structurally cannot answer "fact F is true at T."

So D3's "absurd task" never returns: a contradicting source makes a **new** claim with its own
immutable window; nothing ever closes an existing claim's window. The relation adjudicator **may
consult** `claim_valid_*` as one evidence input (better than re-parsing claim text) but the relation
window stays its *computed, recorded, monotonic* verdict — never a reduction over claim columns, never
read back to override the verdict, never reopened by a late-arriving retrospective.

**Context.** World-validity windows previously lived only on relations, but **many claims yield no
relation by design** (D2: n-ary facts; single-entity / attribute facts; literal- or quantity-object
facts like "revenue was \$5M in FY2023" — objects must be entities and time is never a value, D18). For
those, the fact's world-time survived only inside NL claim text, unqueryable. An immutable asserted
interval on the claim closes that gap without a Date-node, a literal-object relation, or claim
supersession. Converged recommendation of an independent Codex analysis, a four-angle internal design
workflow, and an adversarial "amend-the-decisions" review — the last of which, tasked to argue *for*
restructuring, concluded the claim/relation split, D6, and relation-only supersession should all stay.

**Consequences.**
- New evidence-grain retrieval: a `claims_as_of(t)` search recipe answers "what did sources assert held
  over T," through normalized claim validity columns, zero LLM (D9). Belief-as-of stays relations-only (D10); the recipe
  registry/linter **bars** `claims_as_of` from answering "currently true."
- The relation adjudicator gets structured temporal inputs (a claim "Alice joined in March 2024" can
  seed `works_for.valid_from`; "Alice left in January 2026" can seed closure) instead of re-parsing
  text — with a monotonicity guard so a late retrospective cannot move an adjudicated window.
- **Refines D3 and D6 in wording, not substance**: claims may carry an *immutable* interval, never a
  *revisable* one; validity-as-current-fact still has exactly one home. **Compatible with D18** —
  the interval lives on the claim, not as a relation object/predicate or Date-node (D18 governs
  relation/edge time and is untouched).
- **Residual non-goal (documented):** two sources asserting *incompatible* windows for a
  **non-relational** fact both stand as evidence with no relation to host a contradiction/verdict;
  retrieval surfaces both. Structured *supersession of non-relational restatements* is **not** in the
  claims plane — a fact that needs an adjudicated current value is promoted to a relation (the D5
  `other:` funnel), or a future "E3 proposition-fact layer" is added. Recurrence ("every Q4") and
  un-datable anchor-events ("as of the merger") are out of the single-interval model; the documented
  upgrade is an expressivity child table (btree-indexed, D23-restamped), built only on measured demand.
  Full detail: `e2_e3_claims_relations_design.md` §5/§7, `postgres_schema_design.md` §8/§15/§17.

**Amendment (2026-07-29, issue #158).** When a source uses a relative time such as "yesterday" or
"last year" and its document header supplies an absolute date, E2 must resolve the relative time
against that in-document anchor and store the result in `claim_valid_from` / `claim_valid_until` with
the honest available precision. The claim text keeps the source's relative wording; the computed date
exists only in the structured valid-time fields. If the document has no absolute anchor, E2 leaves the
fields empty rather than guessing. D32's text-membership gate remains unchanged: it checks added claim
text, not these structured fields. Evidence payloads, including `claims_verbatim` and `explain`, now
surface `claim_valid_from` and `claim_valid_until`, so an answer agent can use the extracted time.
The same rule applies when the relative expression is inside a preserved direct quotation or
attributed claim.

**Amendment (2026-08-03, retrieval surface Batch B).** The D41-era no-default-index stance is
superseded for PostgreSQL claim-window retrieval. The default schema now carries the partial index
`(deployment_id, claim_valid_from, claim_valid_until) WHERE claim_valid_precision <> 'unknown'`.
The public `claims_as_of(from, to)` recipe filters stamped evidence in PostgreSQL before any optional
bounded semantic rerank, excludes `unknown` precision exactly, and reports the exact number excluded.
This is the storage decision bound by
[`agent_retrieval_surface_design.md` §3.1](plan/designs/agent_retrieval_surface_design.md#31-entity-and-time-spines-batch-b):
stamped claims are the minority, and the partial index avoids making global vector nomination a
recall ceiling. P1 validity scalars remain an alternative only if measured PostgreSQL p95 exceeds the
design's 250 ms adoption trigger.

---

## D42. E0 records document origin at ingestion (external vs. system-generated)

**Decision.** Every input gets an immutable `origin` stamped at **E0 ingest** — at minimum
distinguishing **external** (came from outside the system boundary) from **self/system-generated**
(produced by this deployment's own agents or workers — e.g. an email an operating agent sent).
Capture only; no consuming logic is built now.

**Context.** Provenance — "did this document come from the world, or from us?" — is knowable *only*
at the moment of ingestion; once a document is chunked → claimed (E2) → related (E3), self-generated
and external assertions are indistinguishable. The motivating case is a closed agent-driven loop where
the system's own outputs are re-ingested: without an origin stamp, an agent's own assertions inflate
`evidence_count` (D2) and entrench beliefs (K3) as if independently corroborated — a silent
self-confirmation loop that corrupts the corpus's headline confidence signal. This is the one piece of
that scenario with a **capture-now-or-lose-it** asymmetry; everything else it raises
(operational-state scopes, an E→K signal/interrupt channel, decision↔evidence-snapshot links) is
additive — a **documented scope boundary** whose admission condition is an agent-operations
deployment actually existing, not a phase marker.

**Consequences.** A small, mandatory E0 metadata field (extensible to richer origin classes and
per-action lineage grouping when needed). The intended first consumer — confidence/belief math that
counts *independent external* evidence rather than raw `evidence_count`, discounting self-generated
echoes — is a **documented non-goal** (a scope boundary with a named admission condition: build it
when belief math is designed, unblocked by this capture). No change to D2/D3/D6.

**Refined by D45–D47 (the K trigger surface).** One of the deferred items — the **E→K
signal/interrupt channel** — is now designed, its condition met (an agent-operated deployment is a
named target): routing-rule **subscriptions** with a **dispatch** consequence invoke registered
agentic workflows with debounced, delta-carrying payloads; page-level watches serve authored
consumers (`k_layers_design.md` §5). Origin capture itself is unchanged, and it is what keeps the
resulting loop non-circular — a re-ingested plan is stamped system-generated and never counts as
independent external evidence. The other boundary items (operational-state scopes,
decision↔evidence-snapshot links) remain documented non-goals.

---

## D43. Two canonical layers — typed relations for the graph, an untyped entity-anchored observation layer for non-graph facts; supersession by entity-blocking + adjudication

**Decision.** Plane E keeps **two** canonical fact layers, split by what they *are*, not merged:

1. **Relations** (unchanged, D2–D5/D18) — distinct **entity→entity** facts with a *governed predicate*.
   Typed because a graph needs typed edges; this is the only layer that projects to P2 (the graph).
2. **Observations** (new) — facts asserted about **one entity** whose object is a *value or a statement*,
   not another entity ("Acme's headcount is 600", "Acme's FY2023 revenue was \$5M"). An observation is
   **anchored to a resolved entity** and is **not typed by any governed attribute vocabulary**. It
   carries the same **bi-temporal** validity windows as a relation, so non-relational facts finally get
   first-class temporal validity and supersession (the gap relations-only left).

**The slot is found, not declared.** Supersession/contradiction among observations reuses the exact
pattern relations already use — *blocking + cheap-first adjudication* (D4) — but blocks on the
**resolved entity** (an exact key) instead of a `(subject, predicate)` pair: a new value-claim about
entity *E* → fetch *E*'s live observations (indexed; exhaustive for that entity) → for a hub entity with
many, order by **semantic similarity** over the observation label using the
versioned write-path cache → the adjudicator decides
per candidate (each gated on a **positive same-thing match** judged *semantically* from the `statement` —
same property, and for a period figure same period and value-compatibility — exactly as relations judge
"same predicate", with **no typed value/period column**): **supersede** (cap the prior `valid_until` at
the new `valid_from`), **contradict/coexist** (same property + same period, incompatible value → both
stand, shared `contradiction_group`), **evidence** (same property + value → add evidence, collapse
redundancy), or **new**. The **no-cap rule** carries the period distinction without a column:
`valid_from`/`valid_until` is the **world-validity of the belief**, and only a **changing effective
state** (headcount/balance/status) is ever capped; a **measurement / fixed-period figure** ("FY2023
revenue") is **never** capped — it doesn't stop being true at period-end, its window stays open, and a
conflicting same-period figure coexists. The conflict slot is `entity + same-property + same-period`, all
matched semantically (FY2023 *revenue* \$5M vs \$7M conflict; FY2023 revenue vs FY2023 *profit*, or FY2023
vs Q1-2023, do not). The "never silently resolve" property is a
**binding adjudicator contract** (supersede only on a positively-matched prior above an explicit margin,
with a persisted reason; otherwise coexist) **plus an eval gate** — not a schema invariant. The design is
explicit that this is policy, and that it fails toward *duplicate coexisting rows*, never silent
overwrite.

**Context.** Non-relational facts (values, measures, single-entity properties) need temporal validity
and supersession — relations couldn't hold them (a relation's object must be an entity), and surfacing
them statelessly is information-lossy. Two fuller alternatives were explored and **rejected** (their
work is preserved in closed PRs, not on main):
- *A unified, typed `facts` table* (one table for entity- and literal-object facts, supersession gated
  by a registered relationship type + a governed `value_domain`/`cardinality` vocabulary). Rejected:
  it merges graph and non-graph data under one roof — a heavy mental model — and the per-attribute
  typing (`value_domain`, `unit_dimension`, `cardinality`) must be LLM-inferred, is brittle, and adds
  registry-maintenance cost. The typing existed only to make literal supersession *schema-enforced*; if
  supersession is adjudicated (as relations always have been), the typing is unnecessary.
- *Mutating claims to carry validity.* Rejected (D3): it destroys the immutable evidence record and
  faces the "absurd task" of closing every prior claim. The observation row is the right unit of
  supersession — one window closes, N immutable claims stay as evidence.

The **D6 "one belief home"** objection does **not** apply to two tables here: a relation and an
observation can never represent the *same* belief (entity-object vs value-object are disjoint), so they
cannot drift against each other the way a relation and a duplicate "proposition-fact" could. Two
disjoint canonical layers, not one polymorphic table, is the simpler correct shape.

**Consequences.**
- **Relations are untouched** — typed, governed, graph-projected, with their existing `(s,p,o)` blocking
  and overlap-EXCLUDE.
- **Observations are deliberately lean** — entity-anchored, bi-temporal, evidence-linked. The value and
  any reporting period live in the NL `statement` (matched semantically); there is **no governed
  attribute registry, no `value_domain`/`unit_dimension`/`cardinality`, no structured value/period
  column, and no typed EXCLUDE.** Supersession is the adjudicator's job (CI-gated), not a schema
  invariant. (A structured `value` for cross-entity numeric range scans is an additive change if that
  need ever becomes real — deliberately omitted now.)
- **No semantic-clustering recall hole.** Because observations are anchored to a *resolved entity*
  (exact key), every prior observation about that entity is found by the exact block — semantic search
  only *ranks* candidates for a hub entity; it never gates membership. The only residual fuzziness is
  the supersede-vs-coexist *judgment*, which fails safe to coexist.
- **Retrieval is through projections** (D9/D94): observations are embedded in PostgreSQL P1 (semantic + value
  search; entity-anchored timelines); they **never** enter the P2 graph (D18 holds — a value is not a
  node). The canonical layer is storage; projections serve queries.
- **Claims stay immutable** (D2/D3), entity-linked (mentions), with asserted validity (D41) feeding an
  observation's initial window.
- The "never silently resolve" guarantee moves from a (would-be) schema gate to an **adjudicator
  fail-safe + eval gate** — the rigor lives in E3/eval, not the DDL.

Design: `plan/designs/observations_design.md`. Schema: `postgres_schema_design.md` §9.A. Normalization:
`e2_e3_claims_relations_design.md` §5. Open items (qualitative/opinion belief — still an *upstream* E2
question; the enforcement dial) tracked in `questions.md`.

---

## D44. The P2 projection contract — Postgres `v_graph_*` views are the LadybugDB COPY boundary; merge-redirect + keep-retracted + casts live in Postgres

**Status:** Ladybug/COPY/Parquet/generation portions superseded by D98. The
surviving semantic requirement is redirect-safe, deployment-keyed, temporal
graph views over PostgreSQL authority data. D98 drops the six snapshot-export
`v_graph_*` views named below and replaces them with no-row private live source
views plus SQL/PGQ metadata. Only `v_graph_survivor` remains as the private
merge-resolution authority behind `v_memory_entity_survivor`; it is not a graph
export or public query surface. The D98 graph design owns the current schema
and contracts.

**Decision.** The Postgres→LadybugDB (P2) projection is defined by a set of read-only **Postgres views**
(`v_graph_entities`, `v_graph_documents`, `v_graph_relates`, `v_graph_mentioned_in`, `v_graph_crossref`,
`v_graph_is_document`, + the shared `v_graph_survivor`) — `postgres_schema_design.md` §10.A. The LadybugDB
side is then a trivial `COPY <T> FROM SQL_QUERY('pg', 'SELECT * FROM v_graph_<t>')` (or the same view via
the Parquet hop). The graph model is **one `Entity` node + one `Document` node**, and **one generic
`RELATES` rel table with `predicate` as a property** (+ structural `MENTIONED_IN`, `DOC_CROSSREF`,
`IS_DOCUMENT`) — *not* per-type node tables or per-predicate rel tables (the vocabulary is governed,
extensible registry data, not DDL; D5/D15/D18). Entity ids stay native **`UUID`** primary keys.

**Context.** A full multi-agent analysis (Codex + Antigravity, both source-verified against the LadybugDB
tree, + an internal multi-angle workflow, both review rounds) confirmed the Postgres structures transfer
**cleanly** *because* the graph is a dumb projection (D6): it inherits outcomes (a believed
`(subject, predicate, object)` fact + validity windows), never constraints — so generated columns, EXCLUDE
arms, composite FKs, and the D18 domain/range signatures correctly **stay in Postgres**. The transfer
reduces to three mechanical transforms (cast `timestamptz` → naive UTC; cast Postgres ENUM → text; drop
graph-irrelevant columns) — which belong in the **views**, the single auditable boundary. Full record:
`plan/analysis/ladybug_translation_research/SYNTHESIS.md`.

**Consequences.**
- **Two correctness rules the projection MUST obey** (a naive `WHERE status='active'` projection is
  *wrong*): (1) **merge-redirect** — `entities.merged_into` is a redirect, not a rewrite, and relations
  are not re-pointed in PG, so endpoints must be recursively resolved to their surviving entity (cycle-safe;
  a pre-snapshot validation gate aborts on cycles/dangling endpoints) or every edge touching a merged
  entity is silently dropped; (2) **keep every retracted edge by default** for *transaction-time* as-of
  (not `invalidated_at IS NULL` and not an age filter), while **aligning node/edge retention** — an edge
  whose survivor-redirected endpoint was retired/forgotten (§13) is dropped because that endpoint cannot
  be an emitted node. Parallel edges (distinct `relation_id`) are preserved, never blind-`DISTINCT`-
  collapsed (same-(s,p,o) collapse is E3's job, D43). A finite hot-snapshot horizon requires a measured
  P2 design revision; it is not a Phase-0 literal, setting, migration input, or hidden default (D69).
- **`observations` and claims never project** (D43/D18): a value is not a node, and a LadybugDB REL
  endpoint must be a node table — the engine rule and the design rule are the same constraint.
- **As-of (refines D10).** LadybugDB has no native temporal semantics, **and you cannot `MATCH`-traverse a
  projected graph** — `PROJECT_GRAPH[_CYPHER]` feeds GDS algorithms only (it is `(STRING,STRING)`; there is
  no `MATCH … IN GRAPH`). As-of is therefore **inline path-predicate filtering** (`WHERE all(r IN rels(p)
  …)`) for correctness, or a **materialized persistent `CREATE GRAPH`/`USE GRAPH`** for heavy/repeat
  analytics. D10's "as-of via projected graphs" holds for *algorithms*, not path traversal — note added.
- **Transport.** `COPY <Node|Rel> FROM SQL_QUERY('pg', …)` is verified, but the **committed transport
  stays the Parquet hop (D7)** until cross-DB attach throughput at 10⁷–10⁸ rows is measured; both
  transports consume the same views. Graph-derived metrics (`pagerank`/`graph_degree`) are computed
  post-load (D11), never reprojected.
- **Spikes** (none blocking): UUID-PK smoke test on the deployed build; attach scan-pushdown/throughput;
  the merge-recursion cycle gate; inline multi-hop path-filter performance. Tracked in `questions.md`.

---

> **D45–D47 provenance.** D45–D47 formalize the plane-K design (July 2026), triggered by the
> second step-back review (`plan/analysis/design_review_2026_07.md`, F1) and the K-plane design
> discussion it opened; they **accept objections O2 and O4** (`plan/analysis/objections.md`).
> Binding design: `plan/designs/k_layers_design.md`. Numbers/thresholds are placeholders to be
> measured (CLAUDE.md).

## D45. Plane K compilation is planned and manifest-driven — planner / writer / driver replace free agent sessions

> **D98 amendment.** The planner/writer/driver, mechanical routing, citations,
> and staleness contracts stand. Remove `community` from the closed rule set,
> remove D11 community keys/writeback, and remove `community_changed`; entity,
> subtree, predicate, document, scope-interest, and manual rules remain.

**Decision.** The K plane is produced by a compile system with three roles: a **planner** (LLM)
that owns *structure* — which pages exist and each page's **routing rules**, recorded as
append-only `knowledge_plan_decisions`; **writers** (LLM — Codex/OpenCode, optionally full agent
sessions with retrieval tools) that own *content* — one writer per page per cycle, full creative
latitude; and a deterministic **driver** that computes staleness, schedules writers in dependency
order (a scope's shared model page first, children before parents, the root index last), validates
outputs, and is the repo's **only automated committer**. Routing rules are **mechanical** — a
closed kind set (`entity`, `entity_subtree`, `predicate_beat`, `doc_set`,
`scope_interests`, `manual`) evaluated by SQL over keys plane E already produces (canonical
entities, governed predicates, document metadata) via an inverted key index; an
LLM never decides routing at evidence-arrival time. **Citations are a binding writer output**
(recorded in `knowledge_artifact_evidence`, uncited candidates counted). **Staleness is
mechanical**: a page is stale iff its recorded `inputs_hash` (candidate evidence IDs + validity
fingerprints + curation + child summaries + prompt/model version) no longer matches — computed,
never guessed. In-session merge-conflict retry and the hot-file rolling-window worker are
**removed** (refines D12); the semantic linter is demoted from staleness detection to prose
quality assurance.

**Context.** The prior mechanism (concurrent sessions editing shared files) left the two
load-bearing steps — routing new evidence to pages (`knowledge_refresh_queue.artifact_id` NULL =
"decide which at processing time") and deciding which pages exist — as unrecorded, per-cycle LLM
improvisation, then added contention machinery to survive its consequences. It also made "is this
page stale?", "which pages does this deletion touch?", and "is coverage complete?" undecidable,
because the compile's read set was never recorded. Plane K was the only non-deterministic stage
whose decisions were not durable state — this applies D33's ledger discipline (extraction ledger,
adjudication transcripts, resolution decisions) to the last holdout. Routing rides on E-plane
labels, so it costs no new intelligence and zero LLM calls (the D9 rule, applied to the routing
path). **Accepts O4** (input manifests / semantic regenerability).

**Consequences.** Staleness, deletion reach, and incremental refresh become SQL ("recompile only
summaries whose referenced claims changed" is now exact); contention is structurally impossible
(disjoint writes + one committer); every compiled page carries freshness provenance (feeds the
mixed-freshness story); K cost scales with dirty pages; planner structure decisions are
reviewable, blast-radius-gated state (D24 pattern). New control-plane tables in
`postgres_schema_design.md` §11. Full design: `k_layers_design.md`.

## D46. Two page kinds — compiled vs authored; the ownership contract narrows K's precious surface to human-authored content

**Decision.** Every K artifact is one of two kinds. **Compiled** pages are evidence-derived:
machine-owned body, regenerated by their writer when stale. **Authored** pages are first-class
human/agent-authored content (target states, designs, decisions, position papers): **never
auto-regenerated**; when evidence they cite changes they receive a **review flag**, not a
rewrite. Both kinds carry citations; authored pages declare them (plus optional **watch rules**
— routing rules whose consequence is a flag) in frontmatter the driver syncs to Postgres. Human
input to compiled pages lives in a per-page **curation sidecar** (pins, exclusions, corrections,
guidance) — a first-class compile input whose enforceable subset is enforced mechanically. A
direct human edit to a compiled body is detected (`content_hash` mismatch) and **quarantined**
into a proposed sidecar entry — never silently overwritten, never silently absorbed.

**Context.** Two forces. (1) Not all knowledge is derivable from evidence: a to-be architecture
or a mapping decision *is not compiled from claims* — it is authored content that must still know
what evidence it stood on (the migration deployment's as-is/to-be case). (2) D1's "the git repo
is not reproducible" over-scoped the precious surface: compiled pages are semantically
regenerable from the spine + recorded inputs; only human words are irreducible.

**Consequences.** Backup criticality concentrates on authored pages + sidecars (refines D1). The
deletion cascade reaches K mechanically: compiled pages recompile without removed evidence,
authored pages flag for the author (the system never rewrites human words, even to forget); the
hard-forget residual is git *history* erasure, named in `k_layers_design.md` §10. Authored
decisions get automatic invalidation alerts when the ground under them moves — "contradictions
are surfaced, never silently resolved" extended to the knowledge plane.

## D47. One compilation mechanism, N scopes — K1 is the default scope, K3 is the belief tier (accepts O2)

> **Refined by D73.** D47's one-mechanism/many-scopes decision stands, but its shipped K3
> belief-tier configuration is withdrawn. The shipped layout is K1 plus K2 scopes; normative
> principles and stances are authored K2 content. The Decision/Context/Consequences below
> record D47 at adoption time; D73 is the current policy.

> **D98 amendment.** K1 no longer contains topic/community pages. Entity pages,
> source digests, and the root index remain the default layout.

**Decision.** Plane K runs **one mechanism**; K1/K2/K3 survive as *content tiers*, not separate
machinery. **K1** = the default scope (entity pages, source digests, the
root index). **K2** = additional purpose scopes — each a git subtree + registry rows (D16),
each with a **shared model page** (vocabulary + domain shape) that is a declared compile input of
every page in the scope (cross-page coherence). **K3** = the belief tier: compiled pages under
stricter configuration — rules select only settled evidence (`evidence_count ≥ N`, no live
`contradiction_group`; N is a placeholder to measure), updates are evidence-gated (never
timer-driven), and every belief cites supporting **and** contradicting evidence. The separate
`k3_beliefs_design.md` is folded into `k_layers_design.md`.

**Context.** Objection O2: by mechanism, K1/K2/K3 were one thing (compile evidence → git
markdown) wearing three names, and a layer must earn its existence with a distinct mechanism.
The belief tier's distinctness is *configuration* (evidence gating, mandatory dual-role
citations), not machinery — exactly O2's "curated view seeded from high-evidence,
zero-contradiction relations", now with a defined update rule. The "whose beliefs are these"
question stays open (`questions.md` #5) — the mechanism is agnostic to its answer; the answer
will configure it, not replace it.

**Consequences.** One pipeline to build and operate; "general" is just a scope; new scope = a
subtree + registry rows + rules (never new machinery). Dedicated K3 machinery would be justified
only by a use case the belief-tier configuration provably cannot express — a documented
alternative, not a plan. The tier layout itself is **configuration, not contract**: K1–K3 is the
shipped default; a deployment — including any user of the open-source library — may reshape,
rename, drop, or invent scopes and tiers freely. What is *not* configurable is the framework
contract: page kinds + ownership (D46), binding citations (D45), the single automated committer,
and the trigger surface's acyclicity ("knowledge structure is configuration, not machinery" —
the D15 principle one plane up; `k_layers_design.md` §2).

---

> **D48–D51 provenance.** D48–D51 formalize the retrieval design (July 2026), driven by the
> scenario battery (`plan/analysis/retrieval_scenarios.md`, S1–S59 — written first, per the
> review's F4: validate the query surface against concrete consumer questions before it
> hardens). Binding design: `plan/designs/retrieval_design.md`. Numbers are placeholders to be
> measured (CLAUDE.md).

## D48. Projections propose, the spine disposes — hydration re-verifies against live Postgres

**D94/D98 amendment:** for PostgreSQL-native P1, nomination and authority
confirmation execute in one statement/MVCC snapshot. Live graph expansion can
share that snapshot and needs no P2 nomination/confirmation round trip;
progressive evidence/source deepening still uses by-ID hydration.

**Decision.** Every **query-engine result** (API / CLI / MCP) is confirmed
against live PostgreSQL authority before reaching a caller. PostgreSQL-native
P1 ranking joins its authority view in the same statement and MVCC snapshot;
live graph expansion reads invariant views directly in the same database snapshot. Confirmation re-reads validity
windows, invalidation state, and contradiction membership; ineligible candidates
are dropped, and relevant drop/candidate counts are reported. **Compound results
revalidate as units** (a graph path with one invalidated edge drops whole — never returned
with a hole, never silently re-routed). Two surfaces are explicitly *outside* the invariant:
**mounted reads** (snapshot reads by construction — covered by visible freshness metadata +
the skill's verify-on-spine motion, D51) and **K prose** (re-checking a page's cited IDs
detects staleness but cannot repair a stale synthesis — K answers are always compiled-grain
with freshness state, never live-confirmed belief).

**Context.** Every entry channel is a projection with lag (P1 write-behind, P2 an hours-old
snapshot per D7, K debounced). Without a single confirmation point, mixed freshness
(`questions.md` #23) forces every consumer to reason about three store ages — or worse, serves a
superseded fact as current (the zombie-fact class D3 exists to kill). With the rule, staleness
can only cost **recall** (bounded by projection cadence, reported per source), never
**correctness** (live, always). D94 removes the P1 cross-store confirmation hop;
P2 remains independently projected and keeps its batched by-ID confirmation.

**Consequences.** Mixed-freshness reasoning becomes data (per-source freshness stamps in the
envelope, D49) instead of consumer folklore. Projections stay dumb and rebuildable (D6/D7
untouched). The nominate-then-drop artifact is surfaced honestly. Hydration depth is progressive
(record → evidence → sources → bytes), so the confirmation hop doubles as the provenance walk.

**Clarified 2026-07-30 (chunk search).** A P1 chunk nomination is not returned directly.
Postgres first confirms that the chunk belongs to the lineage's current ready
version/representation; P1 then supplies the text body it owns, and the engine verifies and
separates the Postgres-recorded generated prefix from source text. A missing row, stale source
coordinate, or prefix mismatch is a hydration drop. This preserves D48 even though large chunk
bodies deliberately do not live in Postgres.

## D49. The response envelope: grain type-discipline, inline contradictions, typed negatives, freshness stamps

> **D98 amendment (2026-08-27).** P2 snapshot freshness and generation
> provenance are removed. The envelope's grain, contradiction, negative,
> truncation, and per-source honesty rules survive; graph freshness is the
> applied live PostgreSQL statement/transaction instant and temporal scope.

**Decision.** Every retrieval response is an **envelope** carrying, besides results: the
**grain** (`fact` / `evidence` / `compiled` / `composite` — declared by every primitive and
recipe, enforced at composition: current-fact answers may be assembled only from
validity-filtered relations/observations; claims never answer "is it true now" — D41's bar made
mechanical; a `composite` answer is `parts[]`, each part strictly single-grain, so mixed
answers like S47's said-vs-believe pair never dilute the discipline); **contradiction
co-members never silently absent** (inline up to a guaranteed cap; beyond it the block always
carries `group_id` + returned/total + a continuation — one-sided answers are a **contract
violation**, not a ranking choice); **per-source freshness stamps** (PG live;
P1 write lag; live graph applied statement/transaction instant; K `compiled_at`
+ staleness + open-flag count — the K block is the
reader-facing flag surface `k_layers_design.md` §11 spike 9 called for, and P3's `_index.md`
mirrors it for the browse path) **including each channel's `believed_at` horizon** (`null` means
that the channel is not age-bounded). Under D69 the live history relation view has no retention-age
horizon: it keeps all invalidated relations whose survivor-redirected endpoints remain emitted
active nodes. A channel with a real age boundary still returns a typed `boundary` naming its
fallback rather than silently truncating history;
**explicit truncation markers** with continuations (no silent caps — hub answers are ranked
pages, never a quiet top-k, never a timeout); the applied temporal scope echoed in
composition-ready, discriminated form (`mode`, `evaluated_at`, `believed_at`,
mode-specific world-time fields, and the **identity regime** — resolution
follows *current* aliases/merge-redirects by default; pre-merge identity reconstruction is the
explicit transcript-based `identity_as_of` recipe over D21's `resolution_decisions` /
`merge_events`, and the envelope states which regime answered); and a **typed negative
taxonomy**: `unknown_entity` / `known_empty` / `boundary` (named limitation + workaround —
e.g. the D43 cross-entity numeric-scan boundary) / forgotten ≡ never-existed (not a kind —
indistinguishability is the requirement; as a CI gate it activates only when the end-to-end
deletion cascade, `questions.md` #24, is designed). There is deliberately no `denied` kind:
content-level authorization is out of library scope (D50 trust model).

**Context.** The callers are agents that must *reason about* answers, not just receive them; and
the requirements make three read-path properties non-negotiable: the claim/relation temporal
split explicit, contradictions surfaced never resolved, hard-forget indistinguishable from
absence. A taxonomy of "no" cannot be retrofitted onto a deployed API. Mixed-grain answers
("everything Alice *said* + what we *believe*", S47) stay honest only if the grain travels with
the data as a type, not a doc-comment.

**Consequences.** Contract tests become CI (grain truthfulness, co-member completeness,
truncation marking, forgotten≡never-existed). Agents plan against freshness and
flag counts instead of guessing. Envelope size on hub answers is a named spike.

**Refined by D65 (media).** The envelope's provenance block additionally carries **source
locators** (deep links to the exact page/region/time interval of the raw original) and the
**derivation disclosure** labels (`derivation_kind` + `evidence_mode`) for media-derived
evidence; a deployment without a configured media embedder reports the missing
`media_segments` search channel as the existing typed `boundary` negative — configuration
absence, never design absence. `plan/designs/media_design.md` §4/§5/§7.

> **Refined by D87 (2026-08-10).** A D49 `Envelope` remains the complete
> response contract for one retrieval authority. `Envelope.parts` and
> `EnvelopePart` are removed; the `composite` grain remains available only for
> one operation's cohesive typed result, not as an envelope-of-envelopes.
> The sole cross-authority composition is `ContextBundle/v1`, exactly
> `{contract: "ContextBundle/v1", testimony: Envelope,
> facts: Envelope}` with extra fields forbidden. It preserves the two complete
> child envelopes instead of flattening or partially copying them. Thus
> `answer_context` is an explicit, narrow exception to the opening sentence's
> “every response is an envelope” wording, not a competing second way to
> represent the same composition. D87 additionally binds `fact_context` to the
> required closed `temporal_scope` result union in
> `open_query_space_design.md` §3.1; `answer_context` preserves that child block
> unchanged.

## D50. Query capability = composable zero-LLM primitives; recipes are registry data

> **D98 amendment (2026-08-27).** The zero-LLM compositional model survives,
> but its graph primitive is the typed live PostgreSQL graph plus the three
> bounded helpers. Ladybug/Cypher/P2 generation wording below is superseded.

**Decision.** The query machine is **primitives + recipes + surfaces**. Primitives are typed,
orthogonal, side-effect-free, zero-LLM operations: `resolve` (the registry's non-LLM tiers
T0–T3 — exact, trigram, phonetic, embedding; no T4 adjudication on the hot path; ranked
candidates, never a silent guess; current identities with merge-redirects disclosed), `lookup`,
`search` (channel × target), `graph`,
`fuse` (RRF as an explicit operator), `rerank` (graph-distance / evidence-count / flagged
cross-encoder), `hydrate` (progressive depth), `transcript` (the audit trail as a query),
`delta`, `pages_about` (the K rule-key index read backwards — the reader's discovery index),
enumerated `aggregate` forms, and streaming `scan` (the batch surface, separate resource pool).
**Recipes are registry rows, not code** (the D5/D15/D45 move): declared compositions with
name / description / typed parameters / a typed primitive chain / **`output_grain`** and
**`answer_intent`** enums / version — so the linter enforces grain semantics **mechanically on
the enums** at registration (`answer_intent = current_facts` requires `output_grain = fact`
over validity-filtered belief primitives; prose-name checks are advisory only), the eval
harness measures recall@k per recipe, and **MCP tools render from the registry** the way
extraction prompts render from the ontology. Recipes add
convenience, never capability (testable: each recipe replays as its primitive chain and diffs
empty). **Non-goal:** any NL→query-plan compiler on the query path — the callers are agents;
the intelligence lives in the caller (D9 taken to its conclusion).

**Context.** The zero-LLM rule means the system cannot be smart at query time; it must be
composable, self-describing, and honest instead. Registry-declared recipes are how the
query-plan vocabulary evolves by governance rather than code accretion, and how three surfaces
(API/CLI/MCP) stay automatically consistent. `aggregate` is enumerated because an unbounded
ad-hoc GROUP BY over 10⁸ rows is a denial-of-service against the spine; `scan` is the escape
hatch.

**Consequences.** Adding a query pattern = inserting a registry row. Temporal composition needs
no machinery beyond D49's parameter echo. **Trust model — content-level authorization and
per-user scoping are library non-goals**: a deployment is one trust domain (every agent that
reaches it is trusted with all of it); isolation is achieved by **deployment separation** —
the deployment model's own mechanism (registries §1) — never by content filtering inside one
deployment (which would have to hold across every channel at once; mounts cannot
query-time-filter, so it degenerates to a deployment inside a deployment). Perimeter security
(who reaches the API/mounts) is deployment infrastructure. D16's filtered snapshots remain a
scope-view/performance tool, no longer carried as access control. (Refines D16's
access-isolation arm; `retrieval_design.md` §9.)

> **Refined by D83 and D87.** The registry-backed and zero-LLM mechanics stand,
> but the public assured-operation registry is closed to exactly the four
> platform-owned D87 descriptors. Adding a customer query pattern means adding
> a saved query, not minting a top-level MCP tool. A fifth assured operation
> requires the evidence gate in `open_query_space_design.md` §1.13.

## D51. Consumption is filesystem-first for agent harnesses; four read-only mounts (raw included, off-path); a consumption skill ships with the system

**Decision.** The primary consumers are **agentic coding harnesses** (Claude Code, Codex,
OpenCode). Four surfaces mount read-only where the environment allows: **P3** (navigate first),
**E0 artifacts** (Markdown + structure + *derived* media — figures, thumbnails, transcripts),
**E0 raw originals** — mounted but **off the navigation path**: reached only via explicit
pointers from P3 stubs / `document.md` frontmatter, for whole-file media ingestion (video /
audio / photos — conversion is lossy exactly there), with **mandatory data-access audit
logging** and **mime-routed storage classes** (agent-readable media → standard/nearline;
audit-only originals → archive) — reversing D37's never-mounted arm while keeping its storage
split — and the **K repo** (read-only checkout). **Precedence rule:** full mount/API parity is
required (some environments cannot mount; API/CLI then carry everything, including byte fetches
by artifact handle); when mounts are available, agents are instructed to prefer the filesystem
for everything a filesystem can do (navigate, read, grep) and reserve API/CLI for what has no
filesystem equivalent (semantic search, graph traversal, as-of, hydration, transcripts, deltas).
The system ships a **consumption skill** — versioned with the system, partially rendered per
deployment (scopes, mounts, enabled recipes differ) — teaching a cold agent the planes, the
grains (and why `claims_as_of` never answers "is it true now"), contradiction and freshness
semantics, the mount layout, the precedence rule, and the orient(K) → verify(spine) →
audit(evidence) motion. Scenario **S58** — a never-seen harness using the memory correctly from
the skill alone — is the skill's acceptance test, run per revision.

**Context.** Harnesses are exceptionally good at filesystem work; mounted trees cost the serving
stack nothing and fit how harnesses already operate. The raw-mount reversal: the old rule's
audit property came from *logging*, not unmountedness (a gcsfuse read is a GCS read under Cloud
Audit Logs); its Markdown-first intent is a *navigation* property (promotion ≠ reachability);
its real cost was archive-class retrieval fees — solved by routing storage class per mime, not
by denying access. For whole-file media the original **is** the artifact: duplicating a 2 GB
video into the artifacts bucket would be pure waste, and a transcript is precisely the lossy
rendering a multimodal agent needs to bypass. The skill is the D15 registry-renders-the-prompt
move aimed at consumers: the system must be usable well with zero human explanation.

**Consequences.** `media/` in artifacts holds only *derived* media; whole-file originals serve
from the raw mount. E0 gains a storage-class routing spike. EXIF / embedded-metadata exposure
via raw is accepted under per-deployment IAM — the deployment is one trust domain (D50); data
with a different trust boundary belongs in a separate deployment, never behind an in-library
filter. The skill joins the eval surface (S58). Requirements §Retrieval is reframed around
harness-first consumption.

**Refined by D65 (media).** Confirmed and completed: raw pointers gain **typed source
locators** rendered as deep links (`original.mp3#t=873`) so the agent lands on the exact
moment/region, not a 90-minute file; unmounted parity requires a **locator-aware serving
operation** (a seekable, codec-aware segment — a naive byte-range is a false promise for
arbitrary video); the skill additionally teaches the three kinds of time and the derivation
disclosure labels. `plan/designs/media_design.md` §4/§8.

## D52. Execution classes are bound — no agent harness on volume or query paths; every LLM worker carries a ledger

**Decision.** Every worker in the system is one of three execution classes (inventory +
per-worker contracts: `plan/analysis/workers.md`): **deterministic** (pure computation; may
invoke non-generative inference such as embeddings or OCR), **programmatic LLM** (fixed-shape,
schema-constrained calls inside a cheap-first cascade — spend scales with ambiguity, never
volume, D4/D17), or **agent harness** (a Claude Code / Codex / OpenCode tool-loop session with
a declared write surface it may not exceed). Two bindings: (1) an agent harness may exist
**only on plane K and the review/audit seats** — never on a per-document, per-claim, or query
path (D9's zero-LLM query rule, generalized to the write side); (2) **any worker that gains an
LLM call gains an append-only transcript with it** — the D33 ledger discipline as a standing
rule for new workers, not a per-design choice.

**Context.** Compiling the worker inventory showed the discipline already holds everywhere
without being stated: the three load-bearing LLM workers (the extractor, the adjudicator pair,
the K writers) are exactly the three with transcript tables (`claim_extraction_decisions`,
`*_adjudications`, `knowledge_compilations`), and the harness surface is exactly plane K plus
review. A harness on a volume path would be unrecorded per-item improvisation at corpus scale —
the same failure D45 rejected for K routing — and cost/latency with no compensating judgment
gain.

**Consequences.** New workers classify before they are built; a proposed harness anywhere
outside plane K / review must argue against this decision, not drift in. The orchestration
design (`plan/designs/orchestration_design.md`) operationalizes the classes (queues, lanes,
budgets, DLQ); the schema's `pipeline_stage` / `pipeline_component` / `processing_target`
enums carry a value for every worker (schema §1).

## D53. Producer/checker separation across model families

**Decision.** Every **checking seat** — the sampled grounding judge (D32 layer 4), the
contradiction and citation-faithfulness evals (D22/O6, k_layers §7), the reviewer agent
consuming the D24 band and K plan-decision reviews, and the K reflection pass — runs on a
**different model family than the producer it checks**. With Codex/OpenCode fixed as plane K's
producer agents (requirements, D45), checker seats default to the **Claude family**; if a
producer changes family, its checkers move.

**Context.** Already stated for reflection in `k_layers_design.md` §7 ("a different
agent/model than the planner — fresh eyes"), assumed by D32's "self-grading is optimistic",
and implicit in D24's review-outside-the-proposing-context. Generalized here because the
failure mode is uniform: same-family checking correlates blind spots exactly where the design
depends on independence — a judge sharing the producer's family inherits the producer's biases
about what looks correct.

**Consequences.** Model assignments in `pipeline_component_versions` make the split auditable
(producer and checker versions name their models). Applies to every future eval/judge seat by
default; running a checker in the producer's family is a recorded exception, not a quiet
config choice.

---

> **D54–D56 provenance.** D54–D56 formalize the evidence-lifecycle analysis (July 2026) —
> review finding F3 (re-extraction inflation) + document versioning for watched sources —
> produced as two parallel independent analyses (internal + Codex) with a reconciling
> SYNTHESIS: `plan/analysis/evidence_lifecycle/`. Binding design:
> `plan/designs/evidence_lifecycle_design.md`. Numbers are placeholders to be measured
> (CLAUDE.md).

## D54. Testimony currency + the counting rule — evidence_count ≡ distinct current-testimony lineages

> **Refined by D73.** The testimony-currency and counting contract stands. Only D54's former
> K3-eligibility consequence is removed because there is no shipped K3 tier.

**Decision.** Claims gain **testimony currency**: a claim is *current testimony* iff it belongs
to its document lineage's current extraction basis under the lineage's versioning mode
(re-extraction: the superseded generation's claims flip non-current, wholesale by coordinates —
no content matching; `living`-mode version supersession: claims whose chunks left the current
version flip non-current; `snapshot` mode: version succession flips nothing). Currency is
**bookkeeping, never validity**: an append-only, reason-coded transitions ledger (the D33
pattern; replayable, D7) plus a cached flag — no adjudication, no `invalidated_at`, claims
immutable in every D3 sense; transaction-time reconstructions still see old generations. The
cached counts are redefined once: **`evidence_count`/`contradict_count` (relations and
observations) ≡ distinct document lineages with current-testimony support, per stance** —
invariant under re-extraction, version churn, and within-document repetition; D42's
independence math gets its denominator (distinct *external* lineages). Zero-current-support
handling splits by cause: **source/curator-driven** loss (living-mode removal, deletion at
source or by operator) **closes** solely-supported facts per shape (states: `valid_until` cap;
measurements: `invalidated_at` — D43 no-cap), recorded as `retracted_source_removal` — no
flag; **processing-driven** loss (a new extractor generation fails to re-derive a claim from
an *unchanged* file) is mechanically undecidable (artifact-corrected vs extractor-regressed
demand opposite actions) and is **flagged `support_withdrawn`** for review — the flag's *only*
trigger; the flag rate per extractor version doubles as the rollout canary. Flagged facts
carry their state in the retrieval envelope. K stability: compiled-page `inputs_hash` keys on **fact state**, never raw
claim IDs; claim-grain citations key on `(lineage, chunk_content_hash)`; "a new claim row for
the same testimony" is not an evidence change (the stale-storm guard). Retrieval claim
primitives default to current testimony with an audit opt-in; P1's default channel indexes
current testimony only (re-extraction replaces the searchable claim; the audit channel sees all
generations).

**Context.** Review F3: evidence-once is keyed `(fact_id, claim_id)`, and a re-extraction mints
new claim IDs for the same sentences — every extractor generation doubled the headline
confidence signal (D9 reranking and adjudication weight), non-uniformly (only
re-extracted documents inflate), while duplicate generations polluted claim search. The
orchestration lanes (D52-era work) make re-extraction routine, so the leak was structural.
Both parallel analyses converged on the counting meaning ("current testimony from distinct
sources — never claim rows, extractor generations, source versions, or poll cycles"); the
divergent mechanism (a reified evidence-basis layer with a cross-generation assertion matcher)
was **rejected** — the matcher is the riskiest component in either proposal and every consumer
is servable from coordinates the pipeline already records; it remains the documented
documented alternative in exact-key mode only, adopted only on measured insufficiency (SYNTHESIS §2; design §9).

**Consequences.** Counts become comparable across facts again and mean what consumers always
assumed. Fail-safe direction preserved: withdrawn support flags, never silent vanishing (the
D25 lesson). Schema: a currency ledger + cached flag on claims; count-definition comments on
relations/observations; `support_withdrawn` review kind. Recount cost is bounded (a lineage's
evidence links) — hub-lineage cost is a spike.

**Refined by D65 (precision fix).** Three identities kept apart: the **source snapshot**
(`version_id`), the **representation** (`representation_id` — one conversion run's immutable
output; a version can own several generations, one current), and the **extraction basis** =
`(representation_id, blockizer_version, structurer_version, extractor_version)` — so "the
toolchain changed" and "the source changed" are formally distinct events, and the structurer
(already an extraction boundary in D56's `extraction_input_hash`) is named in the basis. This
matters most for media, where the common upgrade is the *converter* (a better ASR/VLM
re-reads unchanged bytes → a new representation object): such upgrades flow the
processing-driven ruleset exactly as an extractor bump does (currency swap; counts unmoved —
same lineage; `support_withdrawn` on non-rederivation; never retraction). The basis
coordinate is persisted on occurrence records and currency transitions.
`evidence_lifecycle_design.md` §1/§3; `plan/designs/media_design.md` §6.

## D55. Document lineages and immutable versions — connector-native identity; snapshot vs living semantics

> **Clarified 2026-07-30 (metadata no-op):** an identical-byte observation may
> advance `source_version_ref` so a connector does not refetch forever, but it
> never mutates the existing version's `source_modified_at`. That timestamp is
> stable extraction input and feeds immutable claim `asserted_at`; changing or
> clearing it without reprocessing would make the version row disagree with its
> derived header and claims. The revision value is therefore a mutable connector
> cursor, not permission to rewrite semantic snapshot metadata.

**Decision.** The *logical document* is a **lineage** (stable `doc_id`) identified by
connector-native **`(source_kind, source_ref)`** (Drive file ID, message ID, watched URL;
renames/moves are metadata over a stable ref; a new ref is a new lineage). Lineages carry
append-only **version** rows (one per observed snapshot; conversion/structure provenance,
artifact URIs, `source_modified_at` → derived claims' `asserted_at`, D41) referencing
deduplicated **content objects** (bytes stored/converted once per `content_hash`, even across
lineages). Each lineage has a **`versioning_mode`**: **`snapshot`** (fail-safe default — every
version is independent dated testimony forever; right for versioned archival sources) or
**`living`** (the current version is the source's standing statement; superseded-version-only
claims lose currency per D54). **Absence is never *silent* retraction — in `living` mode,
removal retracts** (stress-test amendment O-B; the interim `removal_semantics: review`
softener was **removed** on user review — a documented alternative, not a dial): removal of a
fact's **sole current support** adjudicates the fact closed, **per shape** — relations and
effective-state observations get `valid_until` capped at the version's `source_modified_at`;
measurement/fixed-period observations get `invalidated_at` instead (capping valid-time would
violate D43's no-cap rule — the figure stays true *of its period*; what ends is our belief) —
both recorded as `retracted_source_removal`: loud, attributed, reversible; with other current
support, decrement only. Rationale: `living` *declares* the current version the source's
standing statement — serving a fact whose only support left that statement, while a review
queue waits, is the zombie-fact failure; wrong retracts are visible and self-healing. Every
source class the softener seemed to serve is served by the modes themselves (rolling logs are
misclassified snapshots; a messy living doc's sole-supported facts deserve to end); its re-add
condition — a measured source class with unacceptable false-retract rate that snapshot cannot
serve — is recorded in the design. The `support_withdrawn` review flag survives independently
as the *re-extraction* zero-support path (D54). Retraction checks evaluate **after the
connector's sync cycle completes**, so an intra-cycle section *move* resolves as a support
swap, never retract-then-reassert. **Deletion is uniform** (user decision): deleting a
document — one version, a lineage by operator, or **the file observed deleted at its source**
(treated as lineage deletion, stamped with the observing sync cycle) — removes its
contribution: claims retained as history with currency ended; solely-supported facts closed
per shape, recorded; no flag, no per-mode split. A source also always retracts by asserting a
retraction — itself a claim. Changed content is **new testimony** through ordinary E2→E3 (supersession
where it conflicts — D3/D4/D43 unchanged). Watched-source ingestion debounces (a stability
window coalesces rapid edits; unchanged revision/etag and unchanged bytes are no-ops).
Deletion gains a grain: delete a version (currency ends; lineage continues) / delete a lineage
(the existing cascade) / hard-forget (S55 semantics across versions). P3 paths and K
citations anchor on lineages (the F6 stability contract).

**Context.** The system had no model for a document that changes — the primary ingestion mode
for every target deployment (watched Drive folders, mail, URLs). Without lineage identity,
every edit is an unrelated document and the unchanged 95 % of its content double-counts —
versioning *is* the inflation problem at document grain. The E0 GCS layout
(`<doc_id>/<content_hash>/…`) always implied this design. The snapshot/living split is the
honest answer to "what does an edit *mean*": a property of the source, not of the system —
and the parallel analyses' one gap in each other (Codex missed `snapshot`; the internal
analysis initially had occurrences only implicitly) is reconciled in the SYNTHESIS.

**Consequences.** `documents` becomes the lineage table; new `document_versions` +
`content_objects` (schema §6); sections/chunks/claims hang off versions with the lineage
denormalized. Refines D37 (identity) and enriches D41 (per-version assertion times). Connector
identity rules per source kind are a named spike.

## D56. Content-addressed reuse — the cost of a new version is proportional to the edit

**Decision.** Extraction and embedding work is keyed by **content, not by document version**:
E2 idempotency keys on the **`extraction_input_hash`** — a fingerprint of **stable components
only**: the chunk's own block hashes + neighbor-chunk block hashes + stable header facts + the
extractor version + the structurer version (a stable config string — so a deliberate structurer
bump, which can reclassify section roles that Selection depends on, is a re-extraction boundary
by key construction; Codex review F10). **No LLM output participates in the key** (section path, summaries, and the
E1 prefix are excluded — non-deterministic across re-runs, they would make the key unmatchable:
the ~0%-reuse hazard; LLM-derived context is instead **carried forward** for unchanged regions,
D7 replay discipline — amendment A3). An unchanged chunk reuses its claims (re-attached to the
new version's chunk row); a chunk whose *neighbors* changed correctly re-extracts; embeddings
key on (chunk content hash, embedding version); conversion artifacts on (content object,
converter version). Reuse alignment is a **block-hash sequence diff** (A1) with
anchor-stabilized chunk boundaries (A2) — mechanics bound in `e1_chunks_design.md` §7. Reconciliation (D54) runs once per completed
basis change and emits **delta-only** K triggers. The efficiency ladder, cheapest exit first:
connector-metadata no-op → content-object no-op → conversion reuse → chunk-grain extraction
reuse → delta-only downstream. The claim-occurrence record is the **`chunk_claims` map**
(written on fresh extraction and on reuse — one immutable claim attaches to every
version-chunk that carried it; exact, never inferred from content-hash joins) — how
`claims_as_of` answers over living documents.

**Context.** An hourly watcher over an edited corpus must not pay per-version costs
proportional to document size (a 50-page doc with a two-paragraph edit re-extracts ~2 chunks,
carries ~148 forward). Extends D12/D25's content-hash idempotency one grain down — same
principle, finer key. The known boundary (chunk-boundary shift re-hashing unchanged text) is
bounded by section-aware chunking and measured by the reuse-rate spike; boundary-stabilized
chunk packing is bound in `e1_chunks_design.md` §4 (the spike measures its parameters, not whether it exists).

**Consequences.** Chunks gain content/input hashes; E2 workers check the reuse key before
calling the model; the E2/E3 cost model for watched sources scales with edit volume. Reuse
hit-rate and per-source conversion floors are spikes.

**Refined by D65 (representation-aware reuse).** "Conversion artifacts key on (content
object, converter version)" becomes an **identified immutable object**: the
`document_representations` row (representation-addressed artifact paths; a version's
`current_representation_id` swaps only on downstream completion — `media_design.md` §6).
Reuse gains the representation dimension (a chunk belongs to a representation's block grid;
an unchanged toolchain re-run replays the stored representation per D7), and the
`chunk_claims` occurrence map becomes the **occurrence-grain provenance home**: it carries
the resolved derivation labels + locator set for the claim occurrence (schema §7), because
those vary per representation generation even when the claim text does not (timestamps,
speaker labels, model family). D57–D58 formalize the chunking-strategy design discussion (July
> 2026), including the stress-test amendments A1–A3
> (`plan/analysis/evidence_lifecycle/stress_test_amendments.md`). Binding design:
> `plan/designs/e1_chunks_design.md`. Numbers are placeholders to be measured (CLAUDE.md).

## D57. The block substrate — a deterministic blockizer owns identity; sections snap to the block grid

**Decision.** Between conversion and everything else sits one deterministic layer: the
**blockizer** (ours, versioned `blockizer_version`) derives the document's **block sequence**
(paragraph-grain structural atoms: paragraphs, headings, list items, atomic tables, code
fences) from `document.md` via CommonMark-grammar segmentation + normalization, emitting
`blocks.json` (ordinal, type, char span into document.md, best-effort page/bbox provenance,
`block_hash`). **Converters do not produce blocks** (they are heterogeneous — Mistral OCR
exposes only per-page Markdown): the converter contract is `document.md` + a page map +
`media[]` (refines D38), and one shared blockizer runs downstream of every route — no
per-converter block semantics can drift. `document.md` stays clean Markdown — the immutable,
content-hash-addressed **coordinate system** that claims' spans, blocks, sections, and chunks
all reference by offset. Blocks are **not Postgres rows** (sidecar + derived keys only, the
D37 split). **PageIndex sections are persisted as block ranges**: a deterministic snap rule
normalizes the structurer's LLM-drawn spans onto the block grid (backward-snap, partition
enforcement, nesting validation, degrade-to-parent — a document never fails structuring).
Direction invariant: sections are *expressed in* block coordinates; **blocks are never derived
from sections** (LLM output must not touch the identity layer). Blocks alone carry identity
through edits (the D56 diff); sections carry meaning; both are views over one text.

**Context.** The chunking discussion's two corrections: (1) the idealized "converters emit
blocks" story fails against real tools (closed OCR outputs), so blocks must be derived by one
deterministic parser we own; (2) "chunks are whole blocks" ∧ "chunks never cross sections" is
satisfiable only if sections are unions of whole blocks — and LLM span output needs a
deterministic normalization target anyway (the system's standing propose/dispose pattern).
Block imperfection is tolerable by design: a mis-merged block costs diff *locality*, never
correctness — a far lower bar than sections, which is why blocks and not sections carry
identity.

**Consequences.** New E0 artifact (`blocks.json`) + `blockizer_version` on versions; grounding
gains one fixed coordinate system with tiered source provenance (exact into document.md;
page/bbox best-effort); a converter swap or blockizer bump is a document-wide reuse boundary
(route pinned per lineage). Design: `e1_chunks_design.md` §2–§3.

**Refined by D65 (media).** The best-effort provenance tier generalizes from `{page?, bbox?}`
to the typed **`SourceLocator` union** (page / image region / time range / video region —
version-pinned, precision-honest, integer milliseconds), fed by the converter's **source map**
(the page map generalized). Blocks from time-coded media carry time-range locators the same
way paper blocks carry pages. `e1_chunks_design.md` §2; `plan/designs/media_design.md` §4.

## D58. Chunks are non-overlapping runs of whole blocks; retrieval is multi-granularity by architecture

**Decision.** A chunk is an ordered run of **whole blocks within one section**, packed by
semchunk (the imposed constraint, kept as the packer) to a measured token budget, with
**anchor-stabilized boundaries** (packing restarts at content-defined anchor blocks, so an
early edit perturbs packing only to the next anchor — load-bearing for sectionless documents).
**No overlap, ever**: overlap double-extracts (duplicate claims within one generation — the
inflation D54 just killed), bloats P1 with near-duplicates, and its offset-arithmetic
boundaries destroy D56 reuse; the E2 bundle's ±N neighbors provide cross-boundary context
explicitly instead. Edge rules: an oversized *atomic* block (a table) becomes its own
oversized chunk; a pathological giant paragraph falls back to deterministic sentence-splitting.
`chunk_content_hash = hash(ordered block hashes)`; the reuse key adds `structurer_version`
(F10) and per-chunk commits under batching (F9). **Embedding granularity:** the dilution
problem is answered by architecture, not tiny chunks — **claims are the needle index** (P1
embeds every decontextualized claim; the ideal fine-grain unit by construction), **chunks are
the passage index** (sized for coherence; BM25 catches verbatim needles; RRF fuses), and
default search recipes **filter out `references`/`nav`/`boilerplate`/`legal` chunks by role**
(a normalized section-authority join inside ranked search; D25 untouched). **Extraction
batching** decouples cost from granularity: E2 batches a section's contiguous chunks per call
(bundle shared; claims still anchor per-chunk; idempotency keys stay per-chunk). The
**embedding-model choice (questions #3) is the design's one open branch point**: conventional
model → the E1 prefix stage exists (stored, carried forward); contextual model → the prefix
stage is deleted. Everything else is invariant across that branch.

**Context.** Chunks serve six masters (retrieval granularity, embedding quality, extraction
units, grounding, reuse stability, cost); the user's dilution objection is correct for
chunks-only systems and answered here by the claims channel — small-chunk/sliding-window
strategies approximate what decontextualized claims already are. Sliding windows are the worst
choice on every axis that matters to this system.

**Consequences.** semchunk honored as packer; token budget, anchor criterion, batch size,
blockizer fidelity, and reuse hit-rate are spikes (`e1_chunks_design.md` §10); P1 chunk rows
gain a role scalar; the E1 design no longer blocks on #3 — it branches on it.

---

> **D59 provenance.** D59 resolves the **attributed-stance / qualitative-belief fork** — review
> finding F2 (`plan/analysis/design_review_2026_07.md`), left open through the observations and
> lifecycle designs — by user decision (July 2026): option 2 of the fork (keep attributed
> stance; normalize to holder-anchored observations), with option 3 (surfaced distributions)
> recorded as the documented alternative.

## D59. Attributed stance is a keep class — stances become observations on their holder

**Decision.** E2 Selection's opinion-drop narrows to **unattributed** opinion. A stance
**attributed to a resolvable holder** — "X said / believes / prefers / opposes Y", including
the document author's own voice (the bundle header names the author, so an email's "I think we
should delay" attributes to its sender) — is a **verifiable proposition about X** (D32's
attribution rule already carries the epistemics: "*X said* Y" entails "X said Y", never "Y")
and is **kept**: extracted as an attributed claim, then normalized (E3) into an **observation
anchored on the holder** — statement e.g. "Bob opposes the pricing change" — untyped and
bi-temporal like every observation, on unchanged D43 machinery. A changed mind is **ordinary
supersession** (a stance is an effective state: the old stance's window caps at the new
stance's asserted time — "what did Bob think in March?" is an ordinary as-of query);
conflicting same-time reports of X's stance coexist via `contradiction_group`. **The guard:**
a stance claim never asserts its *content* as a world-fact — no relation or observation about
Y itself is ever derived from "X believes Y"; only the stance-about-X. Still dropped,
unchanged: holderless opinion, advice, hypotheticals, generic truisms (the rest of the D31
Selection list); a stance whose holder cannot be decontextualized to a resolvable entity
falls back to **drop** (the existing `opinion` ledger reason, which now means
*unattributed-only*).

**Context.** For the target deployments (assistant, agency brain, law engine), "what does X
think about Y, and did it change?" is core memory content, and the blanket opinion-drop
discarded it at extraction (F2). The keep/drop line is verifiability, exactly as D34 states —
what changed is recognizing that *attribution makes a stance verifiable*: you can check the
source and confirm X said it. Stances then get precisely the treatment they need for free:
they change over time, which is what bi-temporal observations with supersession were built
for. **Documented alternative (not built):** surfaced distributions — store every stance
assertion, never adjudicate a current stance, surface the spread ("3 for, 2 against, shifting
over June"); adopt only if group-stance distributions prove load-bearing, on measured demand.

**Consequences.** Scenario S37 ("who disagreed with the ESB decision?") unblocks: stance
observations, holder-anchored, semantically searchable, as-of-queryable. Selection's rubric
and golden set gain stance keep/drop coverage (extends D22/D35; **stance-holder resolution
quality is a spike** — "the team" must resolve to the right entity or the candidate drops).
Requirements §E2 updated; refines D31/D34 (the Selection lists), touches no schema DDL
(stance observations are ordinary `observations` rows; the drop ledger's `opinion` reason
narrows in meaning).

---

## D60. The library boundary — this repo is the complete single-deployment memory system; the human/operations layer is a separate product

**Decision.** The system ships as an **open-source library (Apache-2.0) with a commercial cloud
around it** — the Sentry-shaped split: fully self-deployable OSS, with the cloud absorbing the
infrastructure hardship and adding the human layer. This repo delivers the **complete memory system
for one deployment**: every stage that determines what the memory believes and whether it can be
trusted — E0–E3, the registries + resolution cascade (D17), supersession/contradiction (D3/D4/D43),
grounding (D32), the K compile machine (D45–D47), P1/live graph/P3, the retrieval primitives/recipes/envelope
+ MCP server + CLI + mounts + consumption skill (D48–D51), the review CLI (D24), the eval harness +
canaries (D22/D35), cost metering with enforced budgets, DLQ, and the deletion cascade — plus a
runnable self-host stack (D61). Two **binding constraints on all future design work**:

1. **Correctness is never gated.** No mechanism that determines whether the memory can be trusted
   may live outside this repo or be conditional on a commercial offering.
2. **The cloud consumes this library unmodified**, through published extension points; no extension
   point may allow a consumer to bypass an invariant (ingestion always writes through E0; review
   always appends reversible D24-style verdicts; a control plane is never an authority for E/K/P
   truth).

Two **documented non-goals of the library** (scope boundaries, not phases): a **human web UI** — the
consumers are agent harnesses, and the agent surfaces (API / CLI / MCP / mounted filesystems) are the
complete consumption story (D48–D51; D24 already draws exactly this line for review tooling — CLI in
the library, web UI outside — generalized here to every surface); and a **multi-tenant control
plane** (orgs/users/SSO, billing, fleet management) — one deployment is one trust domain (D16, D50),
and operating *many* deployments is the cloud product's job.

**Context.** Written into the decision log — rather than left as business context — because this
boundary erodes *silently*: a design doc casually assumes a dashboard exists, or a
correctness-adjacent feature lands cloud-side under revenue pressure, and each step looks small. The
split principle in one line: **agents get the library; humans and operations get the cloud.** The
system's designed consumers are agent harnesses (requirements §Retrieval); a web UI appears nowhere
in the library's design, so the human layer is a genuinely separate product, not a carve-out that
weakens the OSS — which is also why the biggest commercial risk is *not* giving away too much but
shipping an OSS that nobody can run or trust (either kills the adoption the cloud depends on). The
supporting analysis lives in the (private) cloud repo; per Rule 1 the reasoning is carried inline
here so this entry stands alone.

**Consequences.** Future designs must not assume a web UI or shared tenancy. The retrieval API
carries a swappable perimeter-auth seam (API keys in the library; D50's trust model unchanged).
Watched-source/connector contracts (D54) write through E0, never around it. `README.md` carries the
outward promise (the "Open source and the cloud" section); `CLAUDE.md` Rule 3 carries the inward
enforcement; requirements name self-hostability explicitly. Governance instruments (CLA with a
relicense grant, trademark policy) are tracked in `questions.md` and must be settled before outside
contributions are accepted.

**Phase-7 scope reconciliation (2026-07-21).** "Complete memory system" means the OSS library
ships the mechanisms required for correctness, portability, and one-deployment self-hosting; it
does not absorb the hosted service's operating policy. The library therefore owns resumable
backfill/reprocessing, reproducible scale batteries, provider-neutral I/O batching, cost metering
and configurable budget parking, typed telemetry plus CLI inspection, the deletion contract and
adapter hooks, release artifacts, and a portable-state/restore contract. Real corpus forecasts,
monetary ceilings,
HA/failover topology, dashboard backends, backup schedules, fleet capacity, on-call runbooks, and
vendor-specific topology tuning belong to the deployment operator or `ultimate-memory-cloud` and
are not OSS implementation gates. Reference adapters remain in this repo; operating the reference
deployment does not. A hard-forget operation must purge every active library-controlled surface
and emit durable state that prevents a restore from resurrecting forgotten data; physically
expiring provider backups is the operator's implementation of that contract. This is an
application of D60's existing boundary, not a new subsystem or a retreat from correctness.

---

## D61. Provider ports — the deployment substrate is pluggable; the imposed constraints become the reference deployment

> **D98 amendment (2026-08-27).** D61's no-engine-abstraction principle
> survives, but LadybugDB is removed from the fixed engine identity. The graph
> implementation is PostgreSQL 19 SQL/PGQ plus bounded frontier functions.

**Decision.** The deployment *substrate* is reached only through narrow **ports** (interfaces with
swappable implementations), each with exactly **two maintained adapters** — a **self-host adapter**
and the **reference adapter** (which is also what the cloud offering runs):

| Port | Self-host adapter | Reference adapter |
|---|---|---|
| Object store (raw, artifacts, snapshots) | S3-compatible (e.g. MinIO); local FS for dev | GCS |
| Task queue / scheduler (at-least-once announcement, scheduled delivery, rate limits) | Postgres-backed queue (`SKIP LOCKED`; application retry/DLQ state is the row, D12/D67) | Cloud Tasks + Cloud Run jobs |
| Mount publication (P3 + artifact/raw/K mounts, D51) | local directory trees | GCS + gcsfuse |
| K git remote | any git remote | hosted per-deployment repo |
| Model / embedding providers | BYO keys | configured providers |
| Telemetry export | OTLP / stdout | managed collection |
| Auth perimeter | API keys (the D50 trust model) | swappable middleware (SSO lives outside the library) |
| Hard-forget manifest + store purge capabilities (D74/D94) | dedicated append-only manifest root + LocalFS/local-Git erasure; PostgreSQL transaction scrubs P1 state | separately durable manifest store + reference object/mount/K erasure; PostgreSQL transaction scrubs P1 state |

**Anti-goal — the engine is not abstracted.** PostgreSQL 19 with
pgvector/pg_textsearch and the live SQL/PGQ/bounded-frontier graph, the E/K/P data model,
PageIndex/semchunk/Claimify, and the K compile machine are the system's *identity*, not substrate; no
port wraps them, and no design should hedge on them. The requirements' former "Imposed constraints"
section is re-titled the **reference deployment**: the fixed production profile (Postgres on Hetzner;
GCP Cloud Run jobs via Cloud Tasks; GCS + gcsfuse) — now *a profile of the ports* rather than an
assumption embedded in every design.

**Context.** As previously written, the requirements pinned the deployment substrate to one vendor's
cloud accounts — an "open-source library" a self-hosting user could not actually run (D60's
biggest-failure-mode). The port set is deliberately narrow — substrate only, two adapters each,
provider maximalism rejected — so the fix costs little: the queue port's self-host adapter is barely
new machinery (dead-letter state is already Postgres rows), mount publication already produces plain
generated files (D40), and the K driver already speaks ordinary git.

**Consequences.** Requirements §"Imposed constraints" reframed (fixed engine choices vs. ports vs.
reference profile). Designs that reference Cloud Tasks/GCS semantics mean the *port contract*
(at-least-once delivery, scheduling, rate limiting, immutable versioned paths, read-only mounts)
with the reference adapter as one implementation. A runnable self-host stack (docker-compose
profile) becomes definable — part of the D60 deliverable. The packaging/distribution design
(packages, deployment profiles, upgrade + migration policy) is a planned design doc, tracked in
`questions.md`.

**Refined by D62 (the queue row, strengthened).** The task queue port is **delivery-only**:
`processing_state` (D12) is the sole authority for what must run; both adapters merely *announce*
rows (self-host: `LISTEN/NOTIFY` + `SKIP LOCKED` claiming with transactional enqueue; reference:
Cloud Tasks push), and one **janitor sweep** re-announces lost deliveries on both — closing the
reference adapter's non-transactional-enqueue window with the same mechanism. A third
**test-tier** in-process adapter exists as test infrastructure, outside the two-maintained-adapter
discipline. `packaging_distribution_design.md` §3.

**Refined by D67 (queue state and vocabulary).** The port announces an existing
`processing_state` row by `processing_id`; route and `not_before` in a delivery envelope are
snapshots only. Postgres owns nullable lane, due time, defer reason, handler-attempt limit, and the
DLQ. The self-host initial wake is a schema-owned transactional `AFTER INSERT` notification, not a
port-side insert; explicit port announcements only wake existing rows. Cloud Tasks delivery
attempts and self-host wake-ups cannot consume an application attempt.

**Refined by D74/D94 (portable hard-forget).** `ForgetManifestPort` is the sole
durable source of lineage-forget intent outside the ordinary restore set.
`ObjectPurgePort`, `CorpusFsPurgePort`, and `KGitPurgePort` are narrow erasure
capabilities implemented by the same two maintained store adapters above, not
new engine abstractions or provider families. PostgreSQL P1 state is scrubbed
inside the PostgreSQL transaction, not through a separate adapter. Every
serving readiness pass re-honors every manifest; local completion is progress,
never permission to skip a separately restored store or database.

---

> **D62 provenance.** D62 formalizes the packaging/distribution brainstorm (July 2026, user +
> Claude; PR #37), filling the unwritten design D60/D61 named. Binding design:
> `plan/designs/packaging_distribution_design.md`.

## D62. Delivery artifacts, delivery-only task execution, and the enforced code architecture

**Decision.** The library ships as **three artifacts**: the GitHub repo (source + the design
corpus), **one PyPI package positioned as the client** (base install = typed SDK + CLI + MCP
server; extras `[server]`, `[connectors-*]`, `[k]`; the original 2026-07-13 naming was later
superseded by D76: dist/import `rememberstack`, CLI `remember`, product RememberStack, canonical
home `remember.dev`; the pre-release mechanical rename is complete), and
**one shared container image on GHCR + a CI-tested docker-compose self-host profile** (Postgres +
MinIO + api + worker; one image runs the API, worker, or setup command because those processes
share the same package and dependencies; the ten-minute quickstart is a release gate). The **client surface** is: query
(SDK/CLI/MCP), **lineage-aware ingest** (`source_kind/source_ref/source_modified_at/
versioning_mode` optional on push — external feeders get full D54–D56 lifecycle semantics;
writes always through E0), **connector management never execution** (connectors run
deployment-side — sync-cycle semantics must not depend on a client process), and the D24
review/admin CLI. **Task execution is one model with two delivery shells**: work is
`processing_state` rows (D12 — the sole authority); handlers are registered per stage,
idempotent, shell-agnostic; the self-host shell wakes on `LISTEN/NOTIFY` and claims with
`SKIP LOCKED` (enqueue transactional with the caller's state writes), the reference shell is
Cloud Tasks push; a **janitor sweep** re-announces lost deliveries on both. **The code
architecture is hexagonal with mechanically enforced arrows**: `model/core/spine/ports/
adapters/llm/workers/surfaces/eval/profiles`; core is pure and infra-free; SQL only in
`spine/`; vendor SDKs only in `adapters/`; **import-linter contracts fail CI on illegal
imports** (architecture erosion fails loudly); profiles are explicit composition roots — no DI
framework. **Portability rides rebuild-first (D7, refined by D75)**: portable state = Postgres +
raw/artifact objects + the K repo + the separately durable D74 manifest root; native operator tools
move the authoritative stores, readiness re-honors deletion before serving, and projections rebuild
after restore — the cloud↔self-host migration path in both directions without a transport subsystem.

**Context.** Fills D60's deliverable and D61's profile mechanics. Redis/arq was considered for
the self-host queue and not chosen for maintenance: a second stateful service in every
deployment and the loss of transactional enqueue, bought for throughput this LLM-bound
pipeline never needs — the port contract still admits a community adapter. The delivery-only
framing dissolves the push-vs-pull asymmetry the two shells would otherwise leak into
application code.

**Consequences.** Roadmap §3 and Phases 0/5/7 updated (port interfaces + self-host adapters +
compose in Phase 0; PyPI packaging in Phase 5; release engineering + portable restore drill in
Phase 7). The remaining stack-convention slots (package manager, lint, CI provider, secrets)
still gate WP-0.1. `questions.md` §11a's packaging item closes; the rename + CLA gates stay
open there.

**Refined by D67 (task execution only).** Both shells announce a `processing_id`; any route or
schedule values carried by the delivery provider are non-authoritative snapshots. The handler
re-reads Postgres, where lane, `not_before`, defer reason, application attempts, budget parking,
and dead-letter state have one normalized home. The self-host schema trigger couples initial row
creation and `NOTIFY` in one transaction; the delivery port never creates the row.

> **Superseding note (2026-07-17) — `PLAN-RECONCILIATION-WP-0.1-STACK-CONVENTIONS` /
> WP-0.1.** The final historical sentence above no longer describes the repository: the
> formerly open package-manager, lint/format, layout/naming, CI-provider, and secrets/config
> slots now have merged implementations or binding enforcement. [PR #39](https://github.com/writeitai/remember-stack/pull/39)
> (merge [`eccc693`](https://github.com/writeitai/remember-stack/commit/eccc693a16d3e32305f142f8f6e04273793996e0))
> established `uv` with a committed [`uv.lock`](uv.lock), Hatchling in
> [`pyproject.toml`](pyproject.toml), the single-package [`src/rememberstack/`](src/rememberstack/)
> layout and test naming, Ruff/Pyright/pytest/coverage, and GitHub Actions
> [CI](.github/workflows/ci.yml). [PR #41](https://github.com/writeitai/remember-stack/pull/41)
> (merge [`ec5ce3a`](https://github.com/writeitai/remember-stack/commit/ec5ce3ac8e3ca3850ac0eab4e3bce7a8dc87d470))
> established the typed pydantic-settings/`SecretStr`/`SecretBytes` convention and Ruff's ban
> on direct environment access. That evidence supersedes only D62's obsolete WP-0.1 gate
> claim: it closes the roadmap stack-conventions gate and records WP-0.1 done. It does **not**
> claim that D61 ports, the two delivery shells, the intended hexagonal package directories,
> or import-linter contracts are implemented; those remain the planned
> [WP-0.4](plan/plans/phase-0-foundations.md). D76 later closed the mechanical release rename,
> and D77 closed the remaining owner governance gate through explicit risk acceptance and the
> repository-native bounded CLA.

## D63. The embedding model is port configuration; default `qwen3-embedding-8b` via OpenRouter — the E1 branch resolves to conventional + prefix

**Decision.** The embedding model is **per-deployment provider-port configuration** (D61), never
architecture: every embedded artifact already carries an embedding version resolving to
`pipeline_component_versions` (model, dimension, params), and changing models is a
version-scoped re-embed batch (D7/D12), not a redesign. The **shipped default** is
**`qwen/qwen3-embedding-8b`** served through the OpenRouter adapter of the embedder port
(OpenAI-compatible embeddings API; $0.01/M input tokens, 32K context — a starting point to
re-verify at contract time), with **self-hosting the open weights (Apache-2.0) as the second
documented adapter** of the same port. This resolves the E1 branch point
(`e1_chunks_design.md` §5): the default is a **conventional** (non-contextual) embedder, so the
**context-prefix stage exists as designed**; the contextual mode (voyage-context-class / late
chunking) remains the fully designed alternate configuration a deployment may choose — the
choice is port config plus a re-embed migration, never new design work. **D94
amendment (2026-08-14):** the PostgreSQL reference profile pins Qwen output and
pgvector columns to **1,536 dimensions**. Pgvector float32 HNSW cannot index the
model's 4,096-dimensional default. Another deployment may provision one
supported fixed dimension, but changing it is an explicit semantic-channel
maintenance migration, not a runtime knob or a benchmark gate.

**Context.** F8 named extraction-side spend and the embedding model as the dominant unmade cost
decisions, and questions #3 called the model "the single hardest thing to change later". The
default was chosen for three properties over benchmark deltas: **strongly multilingual**
(100+ languages — the inflected-language deployment path, registries §5, makes English-only
embedders a trap), **open weights** (self-hosting is a real second adapter, and the model
cannot be discontinued out from under the corpus — the discontinuation risk is what makes
"hardest to change" dangerous), and **hosted-cheap at one of the most-used embedding slots on
OpenRouter** (ecosystem liquidity: multiple providers serve it). "Hardest to change" is
thereby mitigated, not avoided — the migration path (version-filtered re-embed + P1 batch
rebuild) exists by design and is exercised by drills.

**Consequences.** The `context_prefix` worker's conditional existence resolves to *exists*
(workers inventory row 6; the per-chunk prefix call stays in the E1 cost model per F8's
three-calls-per-chunk math). E1 spike 8 narrows from "which model" to "which stored dimension +
prefix quality", measured on the golden set. P1 index/parameter choices unblock (dimension now
bounded). Questions #3 closes; review finding F8 closes. The embedder port gains its two named
adapters (OpenRouter-hosted; self-hosted weights).

**Amendment (D80, 2026-08-03).** The product path remains **conventional** embedders with the
same default model and dimension knob. How location enters the embedding string is **no longer**
“per-chunk LLM context-prefix stage exists.” **D80** binds a **versioned deterministic
embedding-input policy** (conditional location headers, typed location facts, durable batch
embed). **Contextual embedders are a product non-goal** (interchangeability). See
`plan/designs/e1_embedding_input_policy.md` and D80.

## D64. Core predicates grow to 16 — `uses` and `reports_to` promoted from the watchlist

**Decision.** The D18 seed core gains two predicates, taking the core from 14 to **16**:
**`uses`** (Person | Organization → Product — adoption/consumption of a product/system/tool;
change-prone, ordinary supersession; deliberately distinct from `works_on`, which means
building/active engagement, not using) and **`reports_to`** (Person → Person — the
organizational reporting line; change-prone). Both move from the predicate watchlist
(registries §4) into the core table with these tight signatures; their formerly designated
pack homes (systems; work/HR) no longer apply to them. The watchlist keeps `owns`/
`acquired_by`, `lives_in`, and the guardrailed `enables`; the D5 `other:` promotion funnel
remains the default path for everything else — this is an owner promotion, not a change to
the funnel rule.

**Context.** The watchlist promotes on demonstrated `other:` volume, not intuition. These two
are promoted ahead of volume because every named deployment (registries §1) needs them
first-class from day one: "who uses which system/tool" is the backbone of the migration
deployment's as-is landscape and a bread-and-butter assistant/agency query ("person A uses
software X"), and `reports_to` is the org-chart backbone of people-centric retrieval. Both
carry exactly the properties that qualified the original fourteen: tight domain/range over
core types (the D18 gate bites), natural evidence aggregation (the same usage/reporting fact
recurs across sources), and clean supersession semantics (tool adoption and reporting lines
end and change — the bi-temporal model fits). Waiting for the funnel would have meant an
interim of `other:uses` / `other:reports_to` edges that bypass domain/range validation
(tier='other' is ungoverned until promotion) for facts already known to be wanted governed.

**Consequences.** Registries §4: the core table has 16 rows (`related_to` stays last as the
permissive parent); the watchlist shrinks to three entries. p2 §3's seed vocabulary updated;
extraction prompts pick both up by registry render (D15 — rows, not prompt engineering).
Core-tier obligations attach: D22 golden-set coverage for both, and the core stability
commitment (a future split pays the D15-flagged split cost). Signature notes: systems-pack
subtypes (`System`/`Module ⊂ Product`) inherit into `uses`'s range via D15 inheritance;
`reports_to` stays strictly person-to-person (a role-based reporting line is modeled through
the person holding the role).

---

> **D65 provenance.** D65 binds the media-handling analysis (July 2026) — produced as two
> parallel independent analyses (internal + Codex gpt-5.6-sol) with a reconciling SYNTHESIS:
> `plan/analysis/media_handling/`. Both divergences were resolved in Codex's favor
> (media search designed-in, not a boundary; claim-grain derivation disclosure). Binding
> design: `plan/designs/media_design.md`. Numbers and tool picks are starting points to be
> measured (CLAUDE.md).

## D65. Media is an E0 input modality — bound routes, typed source locators, derivation disclosure, and direct media search

**Decision.** Standalone images, audio, and video enter the system as **E0 inputs, never a new
plane or parallel pipeline**: a media file is a source whose testimony reaches the system
through a lossy, versioned transcription, with the original always one explicit pointer away.
Eight bindings. (1) **Canonical text lives in `document.md`** — all text eligible for
extraction, search, and grounding; a transcript existing only in a sidecar (`.vtt`/JSON) is
*interchange*, never canonical, and does not exist as testimony (fixes the
`e0_files_design.md` §2 transcript-placement ambiguity); `media/` holds only regenerable
derived assets (keyframes, crops, thumbnails, interchange transcripts), whole-file originals
stay on the raw mount (D51 unchanged). (2) The **D38 router gains three media routes**, each a
versioned converter: audio → **diarized ASR** (one block per speaker turn; speakers resolved
to entities only on positive evidence, else kept as stable anonymous labels — wrong
attribution corrupts stance memory (D59), missing attribution merely loses claims); video →
ASR + **adaptive keyframes** + optional VLM shot notes; standalone picture → **VLM
description** + OCR of visible text, behind a document-vs-picture discriminator (MIME cannot
tell a scanned page from a photo). Each route emits **sectioned Markdown** whose sections
carry their derivation kind structurally. (3) The **converter contract generalizes** (refines
D38/D57 again): `convert(bytes, mime, hints) → { document.md, source_map, derived_assets[],
manifest }` — the page map becomes a **source map** (character intervals → locators), and the
manifest is the route's complete self-account (component graph, execution context per D61,
output hashes, coverage policy + result, gaps/warnings, range→derivation labels). (4)
**Typed `SourceLocator` union** (`page | source_range | image_region | time | video_region` —
normative schema: `media_design.md` §4), pinned via its carrier to the document **version and
representation** (never a lineage or P3 path), precision-honest on every variant (never
fabricated by interpolation), integer milliseconds half-open on a declared timeline (never
frame numbers); grounding becomes **two hops**
(claim → `source_span`, exact — D32 unchanged; span → source map → raw locator, converter
precision) and D32's sampled audits become **modality-aware** (the auditor listens to the
interval / looks at the region — auditing only the derived Markdown would grade the converter
against its own output); deep links on every surface (P3 stubs, frontmatter, envelope
provenance handles, a locator-aware serving operation for unmounted parity — mounted, the
structured locator + local seek; the `#t=` fragment is display rendering, not a path). (5)
**Derivation disclosure**: converters label mode-homogeneous ranges with `derivation_kind` +
**`evidence_mode`** (`source_expression | model_observation | model_interpretation`; labeling
is total across all routes); claims **inherit both through their `source_span` →
labeled-range intersection** (a span crossing modes takes the most-mediated one) —
deterministic, cached on the claim's occurrence record (`chunk_claims`), no per-claim
judgment anywhere; the retrieval envelope surfaces them **per evidence item**; the mode is
disclosure, never a verdict (Selection's verifiability rules still govern keeps), and
distinct-lineage counts stay the only confidence input — correlation-aware adjustment is a
documented alternative, not in the system. (6) **Representations become identified immutable
objects**: a conversion run's output is a `representation_id`-keyed object
(`document_representations`), representation-addressed artifact paths
(`<doc_id>/<content_hash>/<representation_id>/…`), a `current_representation_id` pointer
swapped only on downstream completion — a re-conversion never overwrites the coordinate
system old claims resolve against; the **extraction basis** is `(representation_id,
blockizer_version, structurer_version, extractor_version)` (precision-fixes D54/D56): an
ASR/VLM upgrade is a processing-driven re-derivation (currency swap, counts unmoved,
`support_withdrawn` on non-rederivation — never retraction). (7) **P1 gains the
`media_segments` semantic target** — a logical target over per-modality PostgreSQL P1
tables/indexes (one row per image / keyframe / bounded audio segment; modality + embedding
family/version/dimension + representation + immutable locator per row; RRF-fused, zero LLM on
the query path, rebuildable); embedders are port configuration (D63), capability is
advertised **per query→target modality pair**, and any unconfigured pair answers as D49's
typed `boundary`. (8) **P3 shows media stubs + previews only** — stub frontmatter carries
`raw_uri` + duration + preview links; never whole raw media in the tree, never per-keyframe
pseudo-documents; raw stays off-path but fully reachable, mounted and unmounted (D51).

**Context.** The driving requirement: *the memory ingests the derived information; the
consuming agent keeps access to the raw files whenever it decides it needs them.* Both
analyses found the conceptual model already right (built in the D51 round) and the machinery
below it missing: no media routes at all in the router table; block provenance built for paper
(`{page?, bbox?}` — a claim from minute 14 of a recording could only point at the whole
file); model-mediated testimony auditable and correctable but invisible at read time; the
basis definition not naming the converter whose upgrade is the *common* media event. Direct
media search is designed in rather than deferred because **access is not discovery**: an agent
can open any file it has found, but it cannot decide to open a file it never retrieved, and
derivations are selective — the VLM never mentioned the small red connector, the transcript
says nothing about the alarm sound; under CLAUDE.md Rule 2 the earlier "documented boundary
with an admission condition" framing was deferral dressed as a boundary, and the mechanism is
cheap by design (one more P1 target riding existing projection machinery).

**Consequences.** Design home: `plan/designs/media_design.md` (routes, locators, disclosure,
lifecycle, search, mounts, spikes). Cross-edits: `e0_files_design.md` §2–§3 (canonical-text
rule; generalized contract; routes), `e1_chunks_design.md` §2 (locator union replaces
`{page?, bbox?}`), `evidence_lifecycle_design.md` §1/§3 (basis), `e2_e3` §3.3
(modality-aware audits), `retrieval_design.md` §3/§5/§8 (media_segments target; envelope
locators + disclosure; skill teaches the three kinds of time — media-timeline `start_ms` ≠
world validity D41 ≠ transaction time). Scenarios: S59 strengthened (deep link to the exact
interval, mounted and unmounted); S62 (media-segment discovery), S63 (image-region grounding)
added. Counting is already safe: a caption and a transcript of one video are two views of
**one** lineage (D54); the envelope keeps derivation-family provenance visible (ten images
captioned by one VLM family share one systematic perception error — composes with D42).
Refines D38/D57 (contract, routes), D51 (completed with locator deep links), D32 (two-hop +
modality-aware audits), D54/D56 (representation objects + basis + occurrence provenance),
D49 (envelope + boundary); D8/D9/D63 unchanged.

## D66. The public documentation site — the WriteIt docs module in-repo, with a same-PR truthfulness contract

**Decision.** The project ships a **public documentation site** for humans (developers
evaluating, installing, operating the system) as a delivery artifact beside D62's three: a
self-contained static Next.js + MDX app at **`website/`** in this repository, exported to
plain HTML and served by **GitHub Pages at `docs.remember.dev`** (verified custom domain plus
a DNS-only CNAME in the `remember.dev` zone; deploy via
`.github/workflows/docs-deploy.yml` on pushes to main touching
`website/**`; PRs build as a check). The stack **replicates the proven WriteIt docs module**
(loopy-loop's documentation site, itself lifted from orchestra's — the pattern of Next.js's
own docs), inheriting its argued decisions and its adversarial-review fixes wholesale:
`@next/mdx` page-as-route authoring, Tailwind v4 + typography themed to the WriteIt palette
with an open font, `remark-gfm`/`rehype-slug`/`rehype-pretty-code`, **Pagefind + `cmdk`** ⌘K
search over the built HTML (self-hostable, no search service), a hand-maintained navigation
array, `output: 'export'`. Two standing rules keep it truthful through implementation:
(1) **same-PR docs** — any PR changing user-facing behavior (CLI, API/MCP, configuration,
mounts, connectors, deployment, the consumption skill) updates the affected `page.mdx` in
that PR, bound in `CLAUDE.md` and in the roadmap's WP execution rules; (2) **docs describe
what ships** — pages document behavior on `main`, never aspirations; the full-scope intent
stays in `plan/`; unshipped subsystems appear only on `/docs/project-status`; pages are
created when their subject ships (target IA in `website/README.md`), and empty placeholder
stubs are prohibited. Seeded now: Introduction, Concepts, Architecture, Project Status —
the material already true before features ship.

**Context.** The coding agents are about to build the system phase by phase; if docs are an
afterthought they will drift from day one — so the contract is installed *before* phase 1,
at the two places implementing agents already read (CLAUDE.md, roadmap §6). Replicating the
sibling module instead of redesigning: the decisions were already argued and reviewed for
loopy-loop (framework choice vs Fumadocs, GitHub Pages vs Firebase/Vercel, Pagefind vs
hosted search, palette/font substitution, accessibility fixes), and org-wide consistency of
the docs stack is itself worth more than any local optimization. The docs/skill split
mirrors the system's own epistemology: the *site* serves humans, the D51 *consumption skill*
serves agents against a running deployment — they must agree but never merge; and
plan-vs-docs is claims-vs-facts honesty applied to the project itself (the design states
intent; the docs state what is currently true of the artifact).

**Consequences.** Design home: `plan/designs/docs_site_design.md`; authoring conventions +
target IA: `website/README.md`; CLAUDE.md gains the docs section; roadmap §6 gains the
same-PR rule; eval check `delivery_docs_site` guards the contract. One-time ops step
recorded (Pages source + custom domain + DNS) — until bound, the site serves under
`writeitai.github.io/rememberstack/` where root-relative assets do not resolve.
Non-goals: versioned docs, docs SaaS/external search, server-rendered features;
API-reference pages render from the assured-operation registry when retrieval ships (D50) rather than
being hand-maintained.

> **Reconciled 2026-07-30 with the managed-cloud D14/D22 domain allocation.**
> The 2026-07-23 claim that this GitHub Pages site would own the apex is
> superseded. RememberStack keeps one product identity across two scoped
> surfaces: `remember.dev` is the product and managed-cloud home, while
> `docs.remember.dev` is the canonical repository-owned OSS documentation
> home. The subdomain is served directly by this repository's GitHub Pages
> deployment; the cloud project is not an authority or proxy for OSS
> documentation. Analysis:
> `plan/analysis/docs_domain_ownership_reconciliation.md`. Design home:
> `plan/designs/docs_site_design.md` §2.

---

## D67. Queue routing and retry state have one normalized home in Postgres

**Decision.** `processing_state` is the authoritative work ledger and also owns the fields that
govern delivery: `lane`, `not_before`, `defer_reason`, `attempts`, and `max_attempts`. A plane-E
row has `lane='steady'` or `lane='backfill'`; a K- or P-plane job has `lane IS NULL` because those
trigger models do not use lanes. The logical queue route is therefore
`(deployment_id, stage, lane)`, with `NULL` meaning the one unlaned route for that deployment and
stage. No physical queue name is persisted. Lane is routing and cost-attribution state, not part of
the D12 idempotency key: discovering the same `(deployment, target, stage, component_version)` in
both lanes cannot create two units of work. First insertion establishes the route; a duplicate
steady enqueue may promote a pending/failed backfill row so live work keeps its freshness
guarantee, while a backfill enqueue can never demote steady work. An explicit operator replay may
also reroute a dead letter. Historical cost rows keep the lane on which each billed call ran.

Promotion changes only backfill-specific waiting. A `budget`-parked row becomes steady/pending,
clears that defer reason, sets `not_before=now()`, and immediately faces the steady budget check;
if the steady budget is also exhausted it parks against that window. A caller-requested
`scheduled` wait and a failed row's `retry_backoff` are preserved exactly, including
`not_before`, attempts, and error, so promotion cannot bypass an intended schedule or a failure
backoff. The promoted row is then announced on its new route.

`not_before` is the one canonical name for the earliest instant at which work may be claimed;
`run_after` is retired as a synonym. `defer_reason` makes the reason queryable:
`scheduled` is caller-requested future delivery, `retry_backoff` is a failed application attempt
waiting for its backoff, and `budget` is healthy work parked until its budget window rolls.
Immediate work has no defer reason. Budget parking sets `status='pending'`, moves `not_before`,
and changes neither `attempts` nor `last_error`; it can never cause dead-lettering.

`attempts` counts application handler executions that actually began, not Cloud Tasks delivery
attempts or self-host wake-ups. `max_attempts` is the total execution limit; its starting value is
three, preserving D12's initial attempt plus at most two retries. A retryable handler failure with
attempts remaining sets `status='failed'`, records the full failure through the worker boundary,
and schedules `not_before` with `defer_reason='retry_backoff'`. A failure at the limit, or a
classified non-retryable failure, sets `status='dead_letter'`. The DLQ remains exactly those
Postgres rows; there is no adapter-owned DLQ.

Attempts are monotonic across manual replay so cost-ledger deduplication remains stable. Replaying
a dead letter sets it back to `pending` and raises `max_attempts` above the current `attempts` by
the operator-approved allowance; it does not reset `attempts` to zero.

Every `cost_ledger` row names its owning `processing_id`, the handler `attempt`, a
`provider_call_id`, and a deterministic `call_key` that identifies one logical call attribution
within that attempt (for example D31's `selection` and `decontextualize` calls). The
processing/attempt/call-key tuple is unique, so an acknowledged-late retry cannot double-bill while
one handler attempt may still make multiple calls. A batched provider call shares one
`provider_call_id` across the participating processing rows and allocates tokens/cost pro rata as
D31 requires; those slices must sum to the provider total and may not cross lanes. Nullable
diagnostic target fields are not part of deduplication. `cost_ledger.lane` records the
authoritative lane copied from the claimed `processing_state` row when the call begins. Budget
enforcement sums by
`(deployment_id, stage, lane, occurred_at-window)`; unlaned K/P costs use `lane IS NULL` rather
than inventing a third operational lane. A matching btree begins with
`(deployment_id, stage, lane, occurred_at)`. The self-host runnable index begins with
`(deployment_id, stage, lane, not_before)` over pending/failed rows, so workers can claim due work
with `FOR UPDATE SKIP LOCKED` without inspecting `payload`.

The task-queue port and its adapters are **delivery-only**. They may announce a delivery envelope
for an already committed `processing_id` plus a snapshot of route and `not_before`, but never
insert the work row. The receiving worker must re-read and atomically claim Postgres. A stale
duplicate, an early delivery, a mismatched route snapshot, or a Cloud Tasks attempt header cannot
override the row or increment `attempts`. Self-host initial enqueue has no state/announcement
crash window because a Postgres `AFTER INSERT` trigger emits the `NOTIFY` transactionally; the
self-host adapter's explicit `announce` operation emits only a wake-up for an existing row
(retry, replay, janitor). Cloud Tasks creation remains post-commit and is repaired by the shared
janitor. Correctness-critical route, schedule, retry, budget, and DLQ state is never hidden in
`payload`.

**Context.** D61/D62 made adapters delivery-only, packaging required queue/lane plus scheduled
delivery, and orchestration required per-lane budgets with no-retry parking. The schema had none
of the normalized lane/due-time fields or indexes, leaving an implementer to put them in opaque
JSON, trust delivery-provider metadata, or fork semantics between self-host and GCP. This decision
makes the same state machine implementable by both shells and keeps D16 deployment isolation,
D12 idempotency, and D60's correctness-in-the-library boundary intact.

**Consequences.** `plan/designs/packaging_distribution_design.md` §3 uses `not_before` and an
announce-existing-row contract; `plan/designs/orchestration_design.md` §§2–4 and §6 use the same
state transitions; `plan/designs/postgres_schema_design.md` §§1–2 specify the enums, columns,
constraints, claim query, and indexes, and §16 maps this decision to both tables. D12 is refined
only in retry vocabulary (`attempts` is total handler starts; default three means two retries),
and D61/D62 are refined only by making delivery snapshots explicitly non-authoritative. No queue
Protocol, migration, adapter, or runtime implementation is created by this decision.

---

**Refined (2026-07-18) — batched-call attribution simplified.** A batched provider call is
billed as **one** `cost_ledger` row on the claiming processing row; `provider_call_id` and
pro-rata slicing are removed. A batch window is a section's contiguous chunks (D58), so it can
never cross a document or a lane — lane budgets and document-level accounting stay exact
without splitting, and nothing downstream consumed per-chunk cost. The
`(processing_id, attempt, call_key)` uniqueness and multi-call attempts are unchanged;
per-chunk cost splitting returns only via a measured need.

## D68. Each deployment has its own Postgres instance or schema

**Decision.** The physical tenancy realization is **schema-/database-per-deployment**. Each
deployed memory system operates in its own Postgres instance or isolated schema; one operational
database does not route rows for several deployments. The `deployment_id` column remains on every
deployment-scoped table and is constant within that database/schema. It is a stable identity and a
structural defense-in-depth key for composite uniqueness and foreign keys, not a cross-deployment
routing key.

**Context.** This makes the physical contract explicit and reconciles sources that already agree
on it. `registries_design.md` §1 says separate deployments have separate Postgres
instances/schemas, registries, and graphs. D16 says scope sharing occurs only within one deployment
and that separate deployments are fully independent instances. D50 makes a deployment one trust
domain and requires a separate deployment for a different trust boundary. The Postgres projection
contract (§10.A) already states that one graph snapshot is one deployment because Postgres is
separate per deployment, and the resolved tenancy entry in `questions.md` records the same answer.

The rejected alternative was one shared operational database with `deployment_id` as the leading
column in every blocking GIN index. Composite foreign keys can prevent accidental cross-deployment
references in that topology, but it conflicts with the independent-instance trust boundary and
adds a constant leading key to blocking indexes under the selected topology. Multi-deployment fleet
management belongs to the D60 cloud control plane; it is not a second tenancy model inside the
single-deployment library.

**Consequences.** `postgres_schema_design.md` §0 carries this as the sole operational contract.
The `deployments` table identifies the deployment served by its database/schema; after structural
Alembic head exists, D69's library-owned `bootstrap_deployment(...)` creates or verifies that one
row from typed profile inputs before it creates any deployment-scoped core registry row. Composite
deployment-scoped keys remain as defense in depth. The three blocking GIN indexes are
single-column (`ix_entities_name_trgm`, `ix_aliases_lemma_trgm`,
`ix_aliases_lemma_dm`), and `btree_gin` is not a required extension; `btree_gist` remains required
for the relations exclusion constraint. D23 records the exact index expressions and the reconciled
partition estate.

---

## D69. Unbounded graph-edge retention and post-head deployment bootstrap

> **D98 amendment (2026-08-27).** The no-age-cut retention semantics survive
> in `memory_v1.memory_history` and the eligible live/history edge views.
> The six snapshot-export `v_graph_*` views, Ladybug COPY, P2
> snapshots/generations, and their rebuild/spike procedure below are superseded
> by the live PostgreSQL graph contract. The separately named
> `v_graph_survivor` merge helper remains identity infrastructure only.

**Decision.** This refinement closes three executable-contract gaps found while preparing the
WP-0.2 migration (`postgres_schema_design.md` former §10.A retention predicate and former §3 seed
ownership; `registries_design.md` former §4 `Document⊂CreativeWork` shorthand):

1. **The P2 relation projection is unbounded by age by default.** `v_graph_relates` emits every
   relation, whether live or invalidated, when both recursively survivor-redirected endpoints exist
   as emitted active entity nodes. Endpoint joins are the retention boundary. There is no
   invalidation-age `WHERE` clause, retention literal, setting, Alembic argument, or hidden input.
   A finite hot-snapshot horizon may replace this default only through a measured P2 design
   revision; the P2 spike measures whether one is needed rather than supplying a Phase-0 value.
2. **Alembic owns schema shape, not deployment data.** `upgrade head` creates structural objects
   only. The library operation
   `bootstrap_deployment(DeploymentBootstrapInput) -> DeploymentBootstrapResult` runs after head in
   one database transaction. It validates typed profile inputs; creates or verifies the single D68
   `deployments` row; creates or verifies the eight core entity-type roots; creates or verifies the
   sixteen core predicates; creates or verifies every concrete predicate signature; then commits.
   Any failure rolls back the whole operation. Its typed input/result and implementation belong to
   WP-0.3's library-owned tenancy/pipeline substrate, not to Alembic or a cloud control plane.
3. **The exact core is registry data, not shorthand.** `registries_design.md` §4 is the normative
   inline manifest. It fixes every required and behavior-bearing entity-type/predicate field and all
   116 concrete signatures. All eight entity types are roots. In particular,
   `Document.parent_type = NULL` and
   `Document.schema_org_ref = 'https://schema.org/CreativeWork'`; `CreativeWork` is the external
   schema.org anchor, not a ninth registry row. Extension-pack definitions and per-deployment pack
   activation remain separate from this universal core.

**Bootstrap identity, idempotency, and conflicts.** The idempotency key is the D68
`deployment_id`. Profile input maps directly to the documented deployment columns; database
defaults own status and timestamps. Each registry key is compared against the complete normative
manifest value: `(deployment_id, type)`, `(deployment_id, predicate)`, and
`(deployment_id, predicate, subject_type, object_type)`. The sole mutable-field rule is explicit:
`predicates.usage_count` is inserted as zero, but a retry verifies it is non-negative and preserves
its runtime-maintained value. A retry with the same complete definition succeeds without duplicates
or mutation. A conflicting deployment identity/profile value, core-row definition, extra/missing
core key, or signature set raises a typed bootstrap conflict and leaves no partial writes.

**Context.** The former view contained executable SQL
`interval '<retention>'`, but no binding source supplied a value. The former seed sentence assigned
deployment-scoped rows to a migration even though a fresh structural migration has none of D68's
truthful deployment UUID/slug/name/bucket inputs. The core list also used the same `⊂` glyph for
Document's external schema.org anchor and for extension rows' real intra-registry parent FKs. Two
independent PostgreSQL 16.14 reproductions confirmed the interval, NOT NULL, and FK failures. The
eight-root Document representation was separately proven executable.

**Rejected alternatives.** A magic or sentinel deployment, empty/placeholder buckets, nullable or
global core rows, a global seed template, a seed trigger, deployment data in migration history,
Alembic `-x` or environment side channels, and a newly invented Phase-0 retention setting/default
are rejected. They weaken D23/D68 constraints, hide correctness inputs, make migrations vary by
deployment data, or merely move the unresolved choice. A finite retention horizon remains a named
measured design alternative, not an unimplemented promise.

**Consequences.** D44/D49, schema §§0/2/3/10.A/16/17, registries §§1/4, P2 §8, retrieval §3,
questions 20a(e), and the Phase-0 WP-0.2/WP-0.3 boundary carry this contract. WP-0.2 remains
responsible for the complete structural migration and its PostgreSQL lifecycle proof. WP-0.3 owns
the typed bootstrap runtime and its transaction/idempotency/conflict tests. D15/D18/D23/D60/D64/D68,
the extension-pack model, indexes, and partition estate are otherwise unchanged.
This is a design/plan reconciliation only: it changes no shipped user-visible behavior or
configuration, so D66 requires no website or `/docs/project-status` edit and no aspirational public
documentation is added.

**Refined (2026-07-18) — the signature manifest is derived, not hand-listed.** The compact
domain/range unions plus the deterministic expansion rule (product of unions, subject-major;
same-kind diagonal for `part_of`; `any` = all eight roots in display order) are the normative
form of the 116-signature manifest, in both `registries_design.md` §4 and the packaged
`core_manifest`. The 116 concrete rows are always derived by that rule and count-asserted at
bootstrap and at import — the same 116 rows, one representation, no hand-maintained expansion
to drift from its source. Point 3's "normative inline manifest" is refined accordingly; nothing
else in D69 changes.

---

## D70. Per-stage model defaults are port configuration; the extraction default is `gpt-5.6-luna`

> **D98 amendment (2026-08-27).** Per-stage model configuration remains
> binding. Any model seat or generation work attributed below specifically to
> Ladybug/P2/community building is removed; live graph reads add no LLM stage.

**Decision.** Per-stage LLM choices are per-deployment **model-provider port configuration**
(D61), never architecture — every stage's calls resolve through
`pipeline_component_versions` (model + prompt hash), so changing a model is a version bump
with version-scoped reprocessing (D7/D12), not a redesign. The **shipped extraction default
(E2 Claimify, both calls) is `gpt-5.6-luna`** (OpenRouter `openai/gpt-5.6-luna`; $1/$6 per 1M
at decision time — re-verify at contract time): the cheap end of the current smart tier,
strongly multilingual (the registries §5 inflected-language path), native structured output
for registry-constrained extraction, and prompt-cache pricing that lands exactly on E2's
shared per-document bundle. The same default serves the adjudication cascades' **small
rung**; the **frontier rung** defaults to `gpt-5.6-sol`. Checker seats stay cross-family per
D53 (grounding and eval judges default to a non-OpenAI family). K producer seats stay as
fixed by requirements (Codex/OpenCode) and are not this decision's subject.

**Context.** Phase 1's entry gate #4 needed the extractor pick. "Cheap yet smart, and
interchangeable — not set in stone" is the owner's requirement; the port + versioning
machinery is what makes interchangeable true, and the golden set (D22) measures the default
before any number locks.

**Consequences.** Phase 1's entry gates are both closed (#3 → D63, #4 → this decision for the
extractor seat; the phase-2/6 seats inherit the same principle and are gated by their own
phases' measurements). Gate register and questions #4 updated; a deployment overrides any
seat in its profile.

## D71. The structure route is a port-configured LLM seat; no PageIndex service dependency

**Decision.** The full D39 structure route runs entirely inside the library. The structurer
is an ordinary model-provider port seat (D61/D70): a prompt over `document.md` asking for the
PageIndex-style section tree (titles, roles, char spans, one-line summaries, nesting) plus
the placement hint, with the deterministic snap (e1 §3) normalizing whatever comes back onto
the block grid. **"PageIndex" names the output shape, not a dependency** — neither the hosted
PageIndex API nor a vendored self-hosted deployment of the tool is part of the system. The
seat defaults to the extraction tier (`openai/gpt-5.6-luna`) and is overridden per deployment
like every other seat (`REMEMBERSTACK_STRUCTURER_*`).

**Context.** Gate #7 asked "hosted API or self-hosted?" — a cost/privacy/rebuild trade.
Examined against the machinery that had accumulated since the question was posed, both
options buy nothing: the snap already makes any LLM's proposal safe (a malformed tree
degrades to a coarser partition, never a failure), so the *only* thing the external tool
would contribute is the proposal itself — which any configured frontier/smart-tier model
produces from the same prompt. The hosted API would move document content outside the
deployment's configured providers (privacy regression, and a D60 boundary erosion); the
self-hosted deployment would add an operational dependency the deployment must run, version,
and secure, for no correctness gain.

**Consequences.** Gate #7 is closed. Privacy: documents reach only the deployment's own
model provider. Cost: the seat rides the same execution-class ladder as every stage (D52),
and short documents skip the call entirely (the synthetic root serves them). Rebuild: every
section row and `pageindex.json` sidecar carries `structurer_version`, so reprocessing is
version-scoped like any component bump (D7/D12). Degradation is total: no provider, a short
document, or a failed call all land the synthetic root — a document never fails structuring.

## D72. Community detection runs natively — Louvain ships on the deployed engine (refines D11)

**Status:** superseded by D98. Retained as the historical WP-4.4 result; the
runtime no longer computes, stores, or exposes communities or centrality.

**Decision.** Community detection runs **inside the graph engine** on the freshly built
snapshot: `LOUVAIN` over a projected graph, alongside `PAGE_RANK`, `K_CORE_DECOMPOSITION`,
and `WEAKLY_CONNECTED_COMPONENTS`. Assignments and centralities are still written back to
**Postgres** (D6: the graph stays a projection, and analytics are never reprojected into the
node tables). D11's external igraph/graspologic pass is **removed as machinery**, not
deferred — a simpler mechanism makes it unnecessary at any scale — and remains documented
here as the fallback shape if a future engine build drops the algorithm.

**Context.** D11 rested on a source-tree survey of the pre-fork engine
(`plan/analysis/ladybug_capabilities.md` §3: "No Louvain/Leiden"). Verified live against the
deployed build (`ladybug` 0.18.2) during WP-4.4 scoping: `LOUVAIN` is registered and is real
community detection, not a relabeled connected-components pass — on two 4-cliques joined by a
single bridge, WCC reports one component while Louvain correctly returns the two cliques
(asserted as a canary in the spike battery, so a future build that drops it fails loudly).
The `leiden | louvain` schema enum already anticipated both.

**Consequences.** No external analytics dependency, no second export consumer, and no
cross-process handoff for the community pass: the rebuild worker computes assignments on the
graph it just loaded and persists them **only once that snapshot publishes** — a snapshot that
fails validation or upload leaves no derived rows behind. The writeback lands in `communities`
(one row per detected community, membership carried by `entity_graph_metrics.community_id` —
there is no separate members table) and `entity_graph_metrics` (pagerank, degree, k-core,
community, component), and both are GC'd when their snapshot is superseded: they are
per-snapshot derived state, not history. Analytics measure CURRENT connectivity — the
projection retains invalidated and expired edges for transaction-time as-of (D69), and a
filtered projected graph keeps those withdrawn facts from inflating centrality or fusing
communities. The detector generation registers as a `community_detector`
`pipeline_component_versions` row (D12), so an algorithm or label-model change is traceable to
the assignments it produced. Community *labels* (the K1 navigation aid) remain a
batched micro-LLM call over each community's top members by PageRank, versioned under the
`community_detector` component (p2 §7). The general lesson is recorded with the engine
rulebooks: **vendored capability surveys go stale — verify on the deployed build**, which is
exactly what the WP-4.1 battery exists to do.

## D73. Core principles are authored K2 content; the shipped K3 belief tier is removed (refines D47)

> **D98 amendment (2026-08-27).** Authored K2 principles and the removal of K3
> remain binding. Community-derived K routing/topic pages and P2 community
> change inputs are removed; entity/source/root/manual scope inputs survive.

**Decision.** Plane K ships with **K1 general knowledge plus any number of K2 purpose
scopes**. It does not ship a K3 belief tier. Personal or organizational core principles — for
example, "prefer simple codebases" — are normative commitments, not conclusions an evidence
threshold can discover. They live as **authored pages in a K2 purpose scope**, cite the
experiences and decisions they rest on, and use D45/D46/WP-6.6 watches, review flags, and
dispatch when that ground changes. Compiled K2 pages may summarize recurring evidence and
suggest a candidate principle, but only an accountable author may promote, rewrite, or retire
the principle. No numeric stance score is inferred.

The system's current evidence-qualified facts remain in E3 and are served through the D48–D51
retrieval contract. Compiled K1/K2 pages may synthesize those facts, but are freshness-stamped
prose, not a separate belief authority. D47's **one compilation mechanism, N scopes** remains
binding; only its K3 default is withdrawn. The already-migrated `knowledge_layer = 'K3'` enum
label remains an inert compatibility value: built-in configuration and behavior never create
or special-case it, and removing an unused PostgreSQL enum value does not justify a destructive
schema rewrite.

**Context.** Gate #5 exposed a category error in the old K3 proposal. "The evidence currently
supports X" is an epistemic summary; "I want my projects to favor X" is a chosen stance. The
first is already represented by E3 facts and ordinary compiled summaries. The motivating
personal-memory use case needs the second: a tiny, cross-project operating doctrine whose
words remain under the user's control while the system keeps it connected to changing project
evidence. K2 already supplies the scope, shared model page, compiled support material, authored
ownership, citations, watches, and notification flow. Selectivity and distillation do not earn
a new tier.

**Consequences.** Question #5 and WP-6.7 close by removal rather than implementation; Phase 6
ends at WP-6.6. There is no belief-threshold spike, belief-only scheduling exception,
machine-promotion path, or calibrated confidence score to build. Supporting/contradicting
citation roles remain useful generic provenance, but no special tier mandates both roles on
every page. A future concrete use case that cannot be expressed as a K2 scope must earn a new
decision; K3 is not a reserved roadmap promise. D45/D46, authored review flags, single-committer
compilation, and configurable scope layout are unchanged. This changes terminology and project
status documentation, but adds no new public runtime feature or configuration surface under
D66.

## D74. Hard-forget is an append-first, fail-closed lineage purge with one portable manifest

> **D98 amendment.** The graph is removed from the external purge inventory.
> PostgreSQL authority/P1 scrubbing removes it from later live graph statements;
> only P3 has graph-adjacent snapshot prefixes, builders, caches, and pointers.

**Decision.** Hard-forget targets one document lineage and runs one straight, resumable path
across the existing lifecycle cascade, PostgreSQL authority/P1/live-graph scrubbing, object
deletion, clean P3 rebuild plus old-snapshot removal, and K history erasure. Before the request is accepted, a
content-free, versioned manifest is appended through a narrow `ForgetManifestPort`; that manifest
is the durable source of intent outside the ordinary restore set. PostgreSQL materializes it and
tracks the ordinary worker's progress. While a request is `preparing` or an accepted manifest is
incomplete, every serving surface for the deployment fails closed. Completion requires mechanical
store verification and the S55 contract; failures remain visible and never reopen admission.

The manifest carries only stable IDs, hashes, exact object/P3-snapshot targets, P1 row IDs, and K
artifact IDs needed to replay after any one store has already been scrubbed. A readiness step
enumerates manifests before traffic, rematerializes missing work, and re-honors the external purge
for **every** manifest even when PostgreSQL still says `complete`; an old database, index, snapshot,
object bucket, local serving cache, or K restore therefore cannot serve forgotten content.
Completed source-identity/content hashes remain irreversible ingest guards. Physical
backup schedules and expiry remain operator/cloud policy under D60, but a backup is not an active
serving store until manifest replay succeeds.

Authored K pages and compiled-page curation sidecars must be redacted by their accountable owner
(human or agent) before manifest append. The library reports the exact blocking paths and never
invents replacement authored prose. Once clean, the K adapter erases affected paths from all
reachable history and re-adds only their sanitized current files. Independently supported facts
survive; source-exclusive payloads and derived identities do not. The normative workflow, record
shape, adapter responsibilities, restore gate, and acceptance canary live in
`plan/designs/hard_forget_design.md`.

**Context.** Question #24 was the remaining gap between normal, audit-preserving deletion and the
S55 promise. Rebuilding derived surfaces only changes their current state: it does not erase
immutable P3 bytes, local serving copies, K history, or data restored from an older backup.
PostgreSQL backups may contain prior authority/P1/live-graph-visible state. A database-only
tombstone also cannot survive restoration of a database from before the request. Conversely,
putting backup topology, lifecycle schedules, or a hosted deletion controller in the OSS library
would cross D60 and violate the simplicity rule.

**Rejected alternatives.** Distributed rollback/transactions across the active stores; a second deletion
scheduler or control plane; provider-specific backup policy in the library; semantic similarity
erasure; treating hard-forget as normal soft deletion; silently rewriting authored prose; and
claiming projections purge "for free." Durable preparation followed by append-first acceptance,
idempotent stages, and a temporary serving/ordinary-work barrier (with the forget coordinator
explicitly authorized) are intentionally less available but much smaller and easier to prove.

**Consequences.** Gate #24 is resolved and WP-7.5 may implement the design. The OSS adds one
portable manifest port and exact purge hooks, not a hosted operations layer. D75/WP-7.7 require
the operator to carry the same separate manifest root before restored data becomes readable; the
library does not transport stores. S55 activates only when WP-7.5's deterministic active-store and
restore canary is green; design resolution alone does not pretend the runtime contract is shipped.

## D75. Portability is a state-and-ordering contract; operators move bytes (refines D60, D62, D74)

**Decision.** The OSS library defines which deployment state is authoritative and the only safe
restore order; it does not implement `remember export` / `remember import`, a universal archive
format, or a
backup coordinator. Portable state is the PostgreSQL database, raw and artifact objects, the K Git
repository, and the separately durable D74 hard-forget manifest root. Operators transfer those
stores with their native tools while preserving the deployment id. P1 and P3 are derived state
and are rebuilt through their normal production paths after restore rather than copied as portable
state. The live graph is PostgreSQL schema metadata and authority views restored with the database.

The manifest root is transferred and verified first. Preserving the deployment id is an operator
precondition; changing deployment identity is outside this portability contract. After the other
authoritative stores are restored and ordinary schema migrations run, the existing hard-forget
readiness pass must
rematerialize and re-honor every manifest before any public or ordinary-work admission opens. Only
then do the ordinary P1/P3 builders and live-graph catalog/helper ensure run and the S55/control canaries gate traffic. Omission or loss
of the manifest root is an unsafe restore. An unavailable or unprovisioned root fails readiness;
a reachable empty replacement cannot disclose lost manifests, so transfer verification remains an
operator obligation. Snapshot consistency, credentials, provider-specific copy commands, progress,
retries, retention, and backup scheduling remain operator or `ultimate-memory-cloud`
responsibilities under D60.

**Context.** D62 correctly made rebuild-first portability part of the no-lock-in promise but
over-specified a pair of library CLI commands. Coordinating PostgreSQL, arbitrary object providers,
and Git behind one command would require an archive protocol, partial-failure and resume state,
credential handling, consistency policy, and provider-specific transport behavior. Mature native
tools already own those jobs. D74 supplies the only extra correctness mechanism the memory system
itself must own: portable forget intent that survives an older data restore.

**Rejected alternatives.** A library-managed multi-store archive; a backup scheduler or migration
control plane; exporting P1/P3 bytes; embedding provider credentials; and treating a
database-only dump as complete portable state. Each either duplicates operator tooling, crosses the
D60 boundary, or can resurrect forgotten content.

**Consequences.** WP-7.7 becomes a contract-and-drill work package, not a runtime feature. Its
acceptance adds a real PostgreSQL old-state restore/rematerialization proof and composes it with the
logical whole-store and real self-host independent-external-store canaries from WP-7.5. The
already-shipped WP-7.4/WP-7.5 contracts, as amended by D98, separately prove delegation to the production P3
builder and live-graph readiness; they are dependencies rather than a claimed composed restore test. The result proves
preserved control data, manifest-first replay, and forgotten-data non-resurrection without new
ports, schema, settings, services, or public CLI commands. Deployment-id preservation and
manifest-root transfer verification remain explicit operator preconditions.

## D76. RememberStack is the product; remember.dev is its canonical home

**Decision.** The public product mark is **RememberStack** and its canonical website is
`https://remember.dev`. Public attribution may read “RememberStack by WriteIt.ai.” The repository
is `writeitai/remember-stack`, the single PyPI distribution and Python import are `rememberstack`,
the container is `ghcr.io/writeitai/remember-stack`, and the deliberately shorter CLI executable is
`remember`. Product prose, help/version output, package metadata, and release artifacts identify
the product as RememberStack; `remember` is a command name, not a second product mark.

The pre-release working identifiers (`Ultimate Memory`, `ultimate-memory`, `ultimate_memory`,
`ugm`, and `UGM_*`) received one clean mechanical rename before the first public release. Runtime
configuration uses the `REMEMBERSTACK_*` prefix. Because no public artifact exists, the project
ships no compatibility package, import, CLI alias, or duplicate environment variables.

The repository and container paths use the readable slug `remember-stack`; the hyphen is URL
punctuation, not part of the product mark. Python keeps the conventional unhyphenated
distribution/import `rememberstack`.

**Context.** The 2026-07-13 decision to use `remember.dev` as both address and brand left the
generic word “remember” carrying the whole identity and exposed a close in-category neighbour,
Remembra. A compound product mark preserves the exceptional domain while making the source name
clearer. RememberStack accurately describes the layered OSS system and can cover both the library
and a hosted deployment without inventing a second architecture or brand.

Preliminary exact-name checks on 2026-07-22/23 found no in-category RememberStack product or
package, but did find Recallstack in the same agent-memory category and Remembra remains close.
Those checks are not legal clearance. The product/domain pairing is adopted for implementation;
focused attorney clearance against both neighbours remains a release gate rather than a claim
made by this engineering decision.

**Rejected alternatives.** `RememberKit` is already an active agent-memory package; `RecallStack`
is already an active product; plain `remember.dev` leaves the generic word doing all trademark
work; `RememberOS` overstates the product; and separate names for the OSS library and hosted
service add complexity before either has earned it. Using `rememberstack` as the executable was
also rejected: the verb-shaped `remember` command is clearer and no compatibility alias is needed.

**Consequences.** The release-gate rename in D62/WP-7.6 executed mechanically against these exact
targets. `remember.dev` is the canonical product and managed-cloud home;
`docs.remember.dev` is the canonical OSS documentation URL in website metadata
and deployment documentation. Alternate documentation hosts redirect to the
docs subdomain. This is a hostname split within one RememberStack identity, not
a second product brand; see D66 and
`plan/analysis/docs_domain_ownership_reconciliation.md`. The repository rename is complete.
Domain/DNS setup, PyPI Trusted Publisher, and public GHCR visibility are account-level release
steps. The former legal-clearance and CLA gates are resolved by D77.

## D77. Public release proceeds with an in-repository, self-hosting-bounded CLA

**Decision.** The owner accepts the documented naming risk around Recallstack and Remembra and
chooses to release RememberStack without making focused attorney clearance a blocking gate. This
is a risk acceptance, not a finding of trademark availability or legal clearance.

Before the first outside contribution, the repository enforces the RememberStack Contributor
License Agreement v1.0. Contributors retain their copyright. They grant WriteIt.ai s.r.o. the
copyright and patent rights needed to maintain, distribute, and relicense their contributions, but
the outbound grant is bounded: every future license must keep the corresponding source available
without a license fee and permit personal, research, nonprofit, and internal commercial
self-hosting without seat, instance, data-volume, or duration limits. A future license may restrict
offering RememberStack as a hosted or managed service to third parties. Apache-2.0 remains the
launch license, and versions already published under it stay Apache-2.0.

Acceptance is deliberately repository-native. Each human-authored pull request contains an exact
versioned assent checkbox, and a metadata-only `pull_request_target` workflow fails the `CLA`
status unless it is checked. The workflow never checks out or executes pull-request code.
Agreement revisions are versioned, require fresh assent, and do not retroactively enlarge an
earlier grant. Known dependency/automation bots are exempt because they cannot assent; maintainers
remain responsible for the provenance of bot-authored changes.

The repository also ships a narrow trademark policy. Copyright permissions remain broad:
truthful references, interoperability identifiers, unmodified redistribution, and clearly
distinguished "based on RememberStack" forks need no permission. The policy reserves product,
service, package, domain, logo, and endorsement uses that could imply an official source. It does
not claim that the marks are registered.

**Context.** D60's governance analysis rejected a plain DCO because it would not preserve the
option to adopt a source-available license if a competing hosted offering becomes material. An
unbounded CLA would solve that business problem by creating a larger contributor-trust problem.
The bounded grant makes the intended trade explicit. It is adapted from the Harmony Contributor
Agreement Template rather than invented from a blank page.

The owner chose not to add a hosted CLA service, OAuth application, or separate signatory
database. GitHub's authenticated identity, pull-request body, edit history, and required status
form the acceptance record. This is the smallest mechanism that enforces the project decision
inside the public repository. Legal review remains advisable, particularly before an actual
relicense, but is no longer a release gate.

**Consequences.** `CLA.md`, `TRADEMARKS.md`, `CONTRIBUTING.md`, the pull-request template, and the
`CLA` workflow ship together. The `CLA` status becomes a required `main` check with administrator
enforcement after the workflow lands. The PyPI pending publisher, matching protected GitHub
environment, and protected `v*` tag ruleset are already configured. The first GHCR push creates
the container package; making that package public is the only post-publish owner action. WP-7.6
may proceed to its first tagged artifact proof after CLA activation.

## D78. LoCoMo measures the ordinary OSS query system, not a claims-only shortcut

> **D99 amendment.** The current protocol is `RS-LoCoMo-Full-v16`. It retains
> v15's 21-tool surface, dataset, models, and budgets while rolling the D99
> resolver/convergence generations. The answer loop mechanically enforces its
> existing instruction that identity metadata alone cannot justify `Unknown`:
> one bounded content-bearing read is required. Existing malformed-reader retry
> remains unchanged.
>
> **D97 amendment.** The current protocol is `RS-LoCoMo-Full-v15`: it retains
> the D98 live-graph 21-tool surface but fingerprints the D97
> `fact_context@2` / `answer_context@2` neighborhood semantics. V14 and earlier
> protocol identities are historical.
>
> **D98 amendment.** `RS-LoCoMo-Full-v14` removed P2/Cypher from the answer
> surface, verified live PostgreSQL graph readiness and P3, and fingerprinted
> the 21-tool catalog. That graph cut remains binding; earlier protocol text is
> historical.

> **Refined by D85 and D87.** V10 and v11 remain historical protocol records.
> The D87 assured catalog requires the separately fingerprinted v12 contract in
> `plan/designs/locomo_benchmark_design.md`; removed operation names are not
> restored as compatibility tools.

> **Amended 2026-08-07 (v10 — measure the shipping clean-cutover surface):**
> the only executable protocol is **`RS-LoCoMo-Full-v10`**. It uses
> `openai/gpt-5.6-luna` for both answering and judging and exposes exactly the
> three operations shipping on `main`: `resolve_entity`, `question_context`,
> and `current_context`. The runner pins and verifies the authoritative
> `surface_manifest_hash` and compares the live recipe descriptors with the
> canonical three before ingestion and again before answering; those
> descriptors include hashes computed from the live registry chains. It also
> requires live lineage/current-version coordinates to equal durable run
> checkpoints before every upload and before answering, and requires a new
> version from every ingest. Both Luna seats pin
> reasoning effort to `none` and reject a different provider-resolved model.
> The obsolete v9
> executable profiles and
> their frozen 20-tool catalog are removed rather than retained as a
> compatibility layer. Historical artifacts keep their own identities; v9
> and v10 scores are not comparable. See
> [`locomo_current_surface_cutover.md`](plan/analysis/locomo_current_surface_cutover.md)
> and the companion design §2.

> **Amended 2026-07-24 (protocol v2 — stronger judge):** the judge is
> `openai/gpt-5.6-luna` and the protocol is **`RS-LoCoMo-Full-v2`**. The judge is
> one call per question against nine agent calls, so it is the cheapest component
> to strengthen, and its verdict is the primary metric. This is a design
> judgement, not a measured result: no leniency comparison between the two judge
> models has been run. The **answer agent stays on
> `gpt-4o-mini`**: it is under measurement, and keeping it at the commodity tier
> keeps baseline comparison honest. No v1 result is comparable to a v2 result.
> Everything else in D78 is unchanged. See
> [`plan/designs/locomo_benchmark_design.md` §2.1](plan/designs/locomo_benchmark_design.md).
>
> **Also amended 2026-07-25 (provenance and preflight):** the image carries the
> source revision it was built from, the readiness response reports it, and the
> answer stage refuses a deployment whose revision differs from the prepared run
> — an unstamped image is a hard stop, because a checkout proves nothing about
> what the containers run. Ingest additionally preflights the provider with one
> chat and one embedding call before uploading anything. Provider responses that
> ignore the declared JSON schema are reported with provider metadata rather than
> a bare decode failure (design §2.4); no in-adapter retry is claimed, and the
> rate of such failures is unmeasured. The revision stamp and the preflight both
> follow from real failures observed on 2026-07-25: a fresh host silently served the released
> `0.1.0` image against a development checkout, and a run configured with the
> `.env.example` placeholder key ingested every session and then failed each
> model-calling stage with HTTP 401. See §§2.2–2.3 of the companion design.
>
> **Also amended 2026-07-26 (v3 — strict-representable agent step):** the answer
> agent's tool arguments travel as a JSON-encoded string (`arguments_json`), and
> the protocol is **`RS-LoCoMo-Full-v3`**: Azure, enforcing strict structured
> output, rejects a free-form arguments object with HTTP 400, so v2's schema was
> invalid for compliant providers. The agent loop takes the first complete JSON
> object from the string and records any trailing text. No v2 score ever
> existed. See the companion design §2.4 and the v2→v3 note in §2.
>
> **Also amended 2026-07-27 (v4 — recipe ergonomics and answer-loop guards):**
> the protocol is **`RS-LoCoMo-Full-v4`**. Public recipe descriptors carry
> when-to-use guidance; `claims_hybrid_rrf` hydrates ranked claim text (keeping
> RRF scores) so the envelope is usable without a follow-up the descriptor never
> mentioned; the answer-agent prompt forbids identical tool+arguments retries,
> requires switching tools after a useless result, and requires a claims search
> before "Unknown". Prompt, tool-catalog hash, and descriptors all change, so
> the protocol version bumps. No v3 score is comparable. See the companion
> design §2 (v3→v4 note) and §7.
>
> **Also amended 2026-07-27/29 (v5, strong variant, and reader recovery):**
> `identity_as_of` discloses its recent-first bound and accepts a recoverable
> limit, which changed the catalog and created v5. The separately fingerprinted
> `RS-LoCoMo-Full-v5-strong` changes the answer agent to
> `openai/gpt-5.6-luna` with reasoning effort `none`. Final-answer structured
> reads may retry twice within the existing nine-call budget; the retry count is
> durable and charged. Weak and strong results are not comparable, and all
> pre-amendment measurements were smoke diagnostics. See companion design §2.
>
> **Also amended 2026-07-30 (v6 — explicit LoCoMo temporal ingestion):** the
> dataset supplies session wall times without a timezone. V6 treats them as UTC
> in this adapter only, records `source_timezone_basis=assumed_utc`, discloses
> the assumption in rendered text, and sends the aware value as
> `source_modified_at`. This repairs the boundary with E2, whose deterministic
> document header must carry an absolute date before relative-time arithmetic is
> allowed. The shared SDK and HTTP surface continue to reject naive or non-UTC
> datetimes, durable models enforce the same invariant, and E0 validates before
> writing raw bytes. Missing source time stays unknown. Rendered bytes,
> ingestion metadata, and derived claim times change, so v5 and v6 scores are
> not comparable. See the companion design §§2 and 3.
>
> **Also amended 2026-07-30 (v7 — independent hybrid evidence retrieval):**
> semantic and BM25 claim nominations are now genuinely independent, and live
> source chunks provide a second hybrid evidence path when extraction omitted
> an answer. The public `question_context` recipe composes both paths while
> preserving claims and chunks as distinct types. Chunk bodies are returned
> only after D48 confirmation of the current ready version/representation. The
> answer agent uses `question_context` first; its repeated prompt sees a compact
> response projection that retains freshness and every evidence row, while
> durable records retain the raw envelopes. The tool
> catalog, prompt, adapter identity, and protocol fingerprints change, so v6
> and v7 results are not comparable. See the companion design §§2, 3, and 7.
>
> **Also amended 2026-07-31 (v8 — answer-stage correctness):** the conv-47 v7
> re-score was 91/150. Its six-word answer cap caused 7 terminal invalid-answer
> failures and made 19 longer gold answers structurally impossible to reproduce
> completely. V8 requires the shortest complete entity/value phrase, permits
> twenty words, and forbids explanations or reasoning. The same run also lost
> 23/150 questions to malformed structured completions on the first agent call,
> before any tool result. The existing two-retry allowance now applies there as
> well as at the reader position and is shared across the answer loop; every
> attempt consumes the ordinary per-question, run-wide, and cost budgets. Plain
> provider outages remain non-retried. Reader-position attempts and additional
> first-step calls are reported separately. The prompt, adapter identity,
> runner behavior, and fingerprints change, while the tool catalog remains
> unchanged, so v7 and v8 results are not comparable. See the companion design
> §§2 and 7.
>
> **Also amended 2026-08-03 (v9 — Batch B retrieval and flag-gated answer cap):**
> four ordinary public recipes add entity-anchored documents and testimony,
> source-validity-window testimony, and current chunk neighbors. Their descriptors
> roll the catalog and protocol identities to **`RS-LoCoMo-Full-v9`** and
> **`RS-LoCoMo-Full-v9-strong`**. The answer-length limit is now the fingerprinted,
> persisted `answer_word_cap` protocol field; both registry entries set it to `None`,
> so v9 renders no word-count instruction and applies no word-count guard by default.
> The qualitative shortest-complete-phrase/no-explanation rule remains unconditional.
> V9 scores are not comparable to v8 or earlier. See
> [`agent_retrieval_surface_design.md` §4](plan/designs/agent_retrieval_surface_design.md#4-benchmark-protocol-impact-v9).

**Decision.** The first competitive benchmark is **`RS-LoCoMo-Full-v1`** over the exact pinned
LoCoMo ten-conversation file and categories 1–4. Each conversation is an isolated deployment;
each session is one immutable Markdown source. The deployment processes every document through
the ten implemented continuous E/P1 routes, then publishes fresh P2 and P3 projections.

Questions are answered by a bounded `gpt-4o-mini` agent using only the deployment's ordinary
registry-rendered public recipe tools. The agent may resolve entities, read current relations and
observations, search/hydrate evidence, inspect timelines and transcripts, discover K pages, and
traverse P2. It has at most eight tool calls and nine model calls per question. Every request,
response envelope, model binding, component version, latency, usage, and failure is recorded.
One frozen `gpt-4o-mini` judge pass supplies the primary accuracy metric; official deterministic
LoCoMo F1 is secondary. Gold answers and evidence never enter retrieval or answer context.

The API, not an operator assertion, proves readiness: every exact expected stage generation for
the requested versions is terminal and P2/P3 builds began after their latest terminal stage. The
answer command checkpoints that report and refuses a changed tool catalog. Prompts, schemas,
tool catalog, dataset/manifests, rendered documents, models, adapter, and repository revision
are fingerprinted. Failures and missing records remain in the full denominator.

The former `RS-LoCoMo-v1 J@30` hard-coded `search_claims(k=30)` path is not the primary
RememberStack benchmark. It bypasses the truth, graph, recipe, envelope, and agent-consumption
logic the OSS package is designed to provide. A claims-only result may return as a separately
named diagnostic/ablation, never as the full-system headline.

Plane K is disclosed rather than simulated. The stock self-host profile has no reproducible K
planner/writer runtime or seeded routing rules; a `pages_about` negative is honest but is not K
coverage. A K-enabled run requires explicit repository/runtime/routing fingerprints, K
settlement in readiness, and a new protocol version.

P3 is built and freshness-checked, but this remote recipe-agent protocol has no filesystem
mount and therefore makes no claim that P3 navigation improved its answers. A mount-enabled
answer protocol receives a new name and fingerprint.

The adapter remains unshipped repository tooling. It does not vendor or download the CC BY-NC
dataset, own deployment creation/destruction, expose benchmark-only queries, or become a general
benchmark framework. Run-absolute call limits and a reported-spend stop threshold remain
mandatory; provider account limits are the hard monetary boundary. Implementation and
synthetic checks alone do not produce a score. Provider execution remains
owner-invoked; the owner authorized one fresh full v10 publication run on
2026-08-07.

**Context.** Published “LoCoMo scores” use materially different datasets, ingestion units,
retrieval depths, answer models, judges, prompts, and repetition counts. More importantly, a
fixed claims retrieval benchmark would measure only one RememberStack projection after the owner
explicitly chose to evaluate the full OSS memory logic. A named, traced agent protocol preserves
comparability without reducing the product to dense RAG.

**Rejected alternatives.** Publishing the claims-only `J@30` score as full-system; a
benchmark-specific SQL/search tool; one service per unused stage enum; treating reconciliation
alone as readiness; requiring an operator to type “index ready”; rebuilding P2/P3 after every
document; pretending an empty K plane ran; mixing conversations; gold leakage; automatic
deployment destruction; dataset vendoring; and an orchestration/dashboard framework.

**Consequences.** Compose runs the ten real continuous routes by default and offers one explicit
P2/P3 build command. Fact labeling follows adjudication and reconciliation, while readiness
joins it with claim embedding. The API/query writer share `P1Settings`. WP-8.2 continues with
this protocol; matched baseline runs in WP-8.3 must use the same public tool budget and
fingerprint. Any changed tool inventory, call budget, model, prompt, schema, judge repetition,
ingestion mapping, or K mode is a separately named protocol.

## D79. Document structure is parsed deterministically; summaries are bottom-up, bounded, and orientation-only

> **D98 amendment (2026-08-27).** The deterministic structure and summary
> contract survives. Any downstream P2 graph generation/community consequence
> is superseded; document structure reaches live graph views directly through
> PostgreSQL authority and reaches P3 through its remaining explicit build.

**Decision (2026-07-27, owner-directed: the system must scale; revised after Grok + Codex
review of PR #164). Refines D71.** The E0 structure stage stops asking one LLM call to draw the
whole section tree — spans, roles, and summaries — over up to 200K characters in a single
schema-constrained response. Instead: (1) the section **skeleton is parsed deterministically**
from the heading blocks conversion emits, with the LLM demoted to a fallback — triggered by
insufficient heading density or oversized unsegmented leaves, not only zero headings — that
returns exact block-contained anchor **strings** resolved by deterministic search, never raw
character offsets; (2) **roles are assigned** by deterministic normalized-title rules, then a
bounded title-only classifier for undecided headings, then explicit `body`; (3) **summaries are
produced bottom-up** on a dedicated flash-class summary seat — leaf calls sharded at block grain
under a hard token ceiling, parent calls reading their own direct blocks plus child one-liners
with balanced fan-in — parallel, cached per section on content + child-summary + model + prompt
+ version hashes; (4) the **placement hint rides the root reduction call** (null on the degraded
path, P3 falls back to its type default); (5) **summaries are consumed as orientation only**:
they feed the E2 bundle's D31 "section path + summary" element (target + ancestors) as
**orientation**, but they are **never a grounding source** — no `summary` added-context
kind exists, additions must still come from source-derived elements — and they are **excluded
from extraction-correctness inputs** (`extraction_input_hash`), so re-summarization never
invalidates or re-extracts unchanged chunks. **Amendment (D80):** summaries are **not** default
E1 **embedding text** inputs (the default embedding-input policy is deterministic location
facts + body; see `e1_embedding_input_policy.md`). The §4 output contract (every document gets
`document_sections` rows on the block grid, non-null roles, sidecar + PG index, placement hint)
is unchanged. Design detail: `plan/designs/e0_files_design.md` §4.1.

**Context.** Three structural cliffs are visible in the shipped one-shot route (observed code
risks — per-cliff rates not yet measured in-repo): character-offset proposals the deterministic
snap can make well-formed but never correct (no anchor recovery); a single long-context
structured-output call — the call shape `locomo_benchmark_design.md` §2.4 documents as
provider-unreliable, compounded by known long-context neglect and output-token ceilings; and the
`max_prompt_chars` cut past which content is unseen by the model. The route bills the
extraction-tier structurer seat (default `gpt-5.6-luna`) up to ~50K input tokens on a near-cap
document for work that is mostly transcription of explicit markdown structure, and the summaries
it produces were write-only — the extraction bundle never saw them despite the D31 design table
listing them (issue #163). Pipeline order (structure → chunk → extraction) already guarantees
summaries exist before extraction; only consumption was missing.

**Rejected alternatives.** Upgrading the one-shot seat to a stronger model (pays more to stand
closer to the same cliffs); per-chunk instead of per-section summaries (cost scales with chunks,
duplicates the E1 prefix); keeping summaries write-only (unmeasurable quality, unused spend);
page-anchored offsets à la original PageIndex (our substrate is markdown, not paginated PDF);
letting the fallback LLM keep proposing raw offsets (the failure mode being removed); admitting
`summary` as an `added_context` grounding source (an abstractive summary would let invented
phrases pass the membership-only D32 layer-2 check — a fact-injection channel, per both
reviews); hashing ancestor summaries into `extraction_input_hash` (one leaf edit would fan out
into document-wide re-extraction, breaking D56's edit-local reuse).

**Consequences.** `structurer_version` splits into hash-stamped generations per D12 — skeleton,
role pass, summary seat, placement — immutable with a current pointer (first-write-wins section
rows and the write-once sidecar are never edited in place; regeneration writes a new generation
and moves the pointer; existing deployments backfill as a legacy generation; sidecar URIs
versioned). `REMEMBERSTACK_STRUCTURER_*` narrows to the fallback structure-proposal seat; the
summary seat is a new D70 per-deployment binding defaulting to a flash-class model. The D38
converter contract gains the explicit clause that conversion preserves/emits heading syntax when
the source has structure. The E1 prefix prompt gains the size-capped target + ancestor
one-liners; the E2 bundle change bumps the extractor version. Cost improvement is expected, to
be measured, not asserted (#150 scorecard canaries; Selection drop quality on low-value roles;
prefix quality). Implementation (#165) is sequenced behind the #161 loss ledger so the bundle
change lands with its measurement in place; entity hints (#163) remain a separate, later
decision gated on #148 lint.

**Amendment (2026-07-28, owner-directed after the complicated-template-PDF discussion; revised
per Grok + Codex review of PR #167): the skeleton sanity check.** Heading density can be
healthy while the parsed tree is template junk (running headers, TOC pages, scrambled heading
order surviving conversion as valid headings), so the parser path gains a coherence judge
between parse and roles. A normative, versioned stat schema (duplicate-title global AND
sibling ratios, raw-heading-level jumps, same-scheme-run numbering inversions plus scheme
switches, tiny/zero direct-body ratios, oversized-leaf ratio and heading density — the same
formulas the demotion gates use — title-shape distributions, sibling fanout; exact formulas,
zero-cases, and floors as named versioned constants in the design; no stat-to-verdict
thresholds shipped) is computed and persisted, then one budget-bounded call on a new
**skeleton-check seat** (`REMEMBERSTACK_SKELETON_CHECK_*`, D70, default flash-class) reads the
stats block and anomaly-exemplar-sampled `(level, title, size)` lines — never section
content — and answers with ONE closed enum (`coherent | incoherent_<primary-defect>`, §2.4's
single-enum lesson; structurally unable to propose structure). `incoherent` demotes to the LLM
fallback route, whose output gets one TERMINAL check — incoherent there degrades to the
synthetic root (honest no-structure over a plausible-wrong tree), no cycles. The persisted
`check_outcome` separates `provider_error | invalid_response | not_run_short` from verdicts so
fail-open (kept because the check is a non-authority guard and fail-closed would amplify
provider blips into corpus-wide fallback traffic — NOT because of document survival, which the
fallback also guarantees) is never bookkept as coherence. Provenance: a fifth `skeleton_check`
generation joins the D79 split, with an append-only per-document check record (D52) carrying
candidate-skeleton hash, stats, sampled-input hash, outcome, component/model/prompt/schema
hashes, failure envelope, and cost; the final skeleton generation records the selecting check
and a route tag; a checker bump mints a new skeleton generation only when the route flips;
D53 producer-family is recorded N/A for the deterministic parser. Audit: route tags, stats +
outcomes, and structure canaries validated against a sampled labeled skeleton-quality set are
primary; the #161 loss ledger is a supplementary correlative only (a bad tree can mis-role
sections so losses never reach the Claimify ledger). The owner's no-reinvention requirement
resolves to REUSING the repository's existing pinned `markdown-it-py` blockizer (D57's single
shared tokenizer) with heading metadata (raw level, normalized title) exposed under a
`blockizer_version` bump — never a second parallel parse. Scope fence: the check judges the
tree, not the text under it — intra-section reading-order scrambles are the conversion-layer
track (D38, issue #168). Design detail: `plan/designs/e0_files_design.md` §4.1.

**Amendment (2026-07-29, Wave-3 review of #165): the summary→prefix second-order channel.**
Summaries feed the E1 prefix input (as this decision mandates) and the stored prefix is a
quotable `added_context` element, so a summary-informed prefix is a bounded second-order path
by which non-neighbouring content can reach the grounding surface. Adjudicated: status quo on
quotability, widening of reach; accepted as layer-3/4 audit territory with an output-constraining
prefix instruction (describe location only, never restate a summary's assertions). Also
recorded: carried prefixes keep their summary generation until content re-chunks or the
prefixer version bumps — the no-fan-out corollary. Detail: `plan/designs/e0_files_design.md`
§4.1 consumption bullet.

**Supersession note (D80, 2026-08-03).** The default product path **no longer** feeds summaries
into E1 embedding text or treats free-form location headers as the grounding channel for
location. Typed source/connector location elements ground under D80; summaries remain
orientation-only (this decision’s clause 5). Historical Wave-3 text above records the prior
second-order channel; it is not the D80 default.

## D80. Conventional embedding input is a versioned deterministic policy — conditional location headers, typed location facts, no default location LLM (amends D63 path; refines D56 vector reuse)

**Decision (2026-08-03).** The product path for chunk (passage) embeddings is **conventional
embedders only** (`texts → vectors`), chosen for **interchangeability** under version-scoped
re-embed migration. **Contextual embedding models** (APIs that embed a span using undeclared
document context) are a **documented product non-goal**; they are not required to implement or
operate RememberStack.

How a chunk becomes embedding text is owned by a **versioned embedding-input policy**
(`plan/designs/e1_embedding_input_policy.md`), not by a per-chunk LLM “where this sits”
completion:

1. **Location facts** — typed coordinates (document title, `source_kind`, **`source_shape`**,
   section title path, role, connector message metadata when present, field provenance).
2. **Pure policy** — total function selecting `body_only` or `location_header` and rendering
   optional **bounded deterministic header** + body → **embedding text**.
3. **Conditional headers** — present when they disambiguate multi-context passages; **absent**
   for many short **message_atom** bodies so headers do not dominate vectors. Location remains
   available as normalized spine facts joined by admitted recipe filters.
4. **No default LLM** on the location/render path. D79 **summaries** stay orientation-only:
   not default embedding text; not grounding sources.
5. **E2 grounding:** free-form rendered headers are **not** grounding-union members; **typed
   source/connector/deterministic location elements** are, so decontextualized claims may still
   ground location tokens under `body_only`.
6. **Vector reuse** for passage embeddings requires matching
   `embedding_text_hash + embedding_input_policy_version + embedder_generation` — content-hash-
   only vector carry-forward is insufficient when location participates in the embedded string.
   Extraction identity keys remain free of LLM output (D56).
7. **Execution:** prepare (facts + render) stamps per chunk; **embed** in capability-bounded
   batches with durable progress and a representation readiness barrier — not one document-
   level all-or-nothing location+embed transaction.
8. **Connector metadata** for message corpora (channel/thread/author/time) is a typed ingest
   contract (D61 family); policy must not invent those fields.

**Context.** Analysis and dual review of the monolithic LLM context-prefix stage
(`plan/analysis/e1_context_prefix_efficiency/`, Fable + Codex reviews) showed geometric failure
on large first ingests, weak marginal value of per-chunk location LLM calls, overloaded
`context_prefix` consumers, and the need for short-message behavior (Slack). Owner direction:
no hotfixes; conventional interchangeable embedders; full-scope contracts.

**Amends.** D63 consequences: the “context-prefix stage exists as per-chunk LLM” product path
is **replaced** by the embedding-input policy; contextual alternate is **non-goal** (not erased
from history). D79 consumption: summaries do **not** feed default embedding text (supersedes
the summary→prefix input channel for the default policy). D56: vector reuse rule refined as
above; block extraction reuse unchanged.

**Consequences.** New binding design `e1_embedding_input_policy.md`; e1 §5 points here; E2
grounding and retrieval filter surfaces gain explicit contracts; workers inventory
drops default per-chunk location LLM; `document_versions.source_shape` and
`document_sections.role` are normalized filter authorities, while P1 keeps one
configured current attestation rather than copied generation scalars;
implementation is a multi-PR program (schema, policy module, embed graph, connectors), not a
single handler patch.

**D94 amendment (2026-08-14).** P1 search rows do not copy location/filter
scalars. Ranked queries join normalized location authority inside the same
PostgreSQL statement. Permanent search generations are replaced by one current
configuration plus unready/rebuild/verify/publish maintenance.

**Rejected.** Contextual embedders as product requirement; always-on location headers; default
per-chunk location LLM; hotfix-only durability inside the old monolith; ungoverned connector
JSON as P1 filters; free-form header as sole E2 location grounding channel without typed
replacement.

## D81. Query-sandbox contracts follow enforceable authorities, not parallel approximations (refines D68)

**Decision (2026-08-05, Batch B correction review).** The open SQL sandbox uses one
deployment-derived query login and its enforceable 64 MiB `temp_file_limit` for both
interactive and entitled analytical requests; no second analytical role is introduced merely
to advertise a larger temporary-file allowance. Raw SQL containing U+0000 is rejected before
pglast. Every rejected or failed `QueryResult/v1` carries zero rows and
`empty_result=true`. Discovery serializes every field of the authoritative `TierLimits`
record rather than a hand-picked subset. A public request executes `READ ONLY, REPEATABLE
READ`, because its bounded internal confirmation work and caller statement must share one
snapshot.

D68's database boundary applies to content-bearing deployment databases. Provisioning revokes
default `PUBLIC` database privileges before deployment content or query credentials exist,
then grants `CONNECT` to that database's derived login; the pool/HBA route offers that login
only its bound database. PostgreSQL effective privileges are additive, so revoking from one
role cannot override `PUBLIC`. We therefore make no false claim that an arbitrary
unprovisioned or administrative database has a per-role deny ACL; such databases must contain
no deployment content. The implementation analysis and PostgreSQL sources are recorded in
`plan/analysis/open_query_space_batch_b_corrections.md`.

**Context.** Review found six cases where code or prose duplicated an authority and drifted:
an analytical cap larger than the only role setting; parser-first handling of NUL; a model
default that made rowless failures say non-empty; six manually selected discovery fields from
a 19-field record; `READ COMMITTED` prose beside a repeatable-read executor; and a test over two
migrated databases described as proof about every database in a cluster.

**Rejected alternatives.** A second analytical login solely for a temp cap; RLS; cluster event
triggers that mutate all future database ACLs; a custom SQL lexer for the NUL precondition; a
second discovery-limit schema; weakening the executor to `READ COMMITTED`.

**Consequences.** The binding design, limits manifest, discovery payload, result honesty, and
tests now name the same authorities. Cross-deployment acceptance tests prove isolation between
provisioned deployment databases, while provisioning order and routing carry the boundary for
the cluster around them. Changes to `TierLimits`, including the 64 MiB analytical temp cap,
roll `surface_manifest_hash` through the existing manifest generator.

## D82. The Cypher boundary stays lexical/read-only; unavailable graph metadata is null; question context v4 reuses existing authorities

**Status:** public-Cypher and P2 portions superseded by D98. The surviving
default-deny public-query principle applies to SQL; server-owned SQL/PGQ is not
a parser bypass or a public arbitrary-language endpoint.

**Context-operation portion superseded by D87.** The Cypher, graph, and
authority decisions below remain binding. D87 removes `question_context` v4,
its optional channels, and `current_context` from the target public catalog.

**Decision (2026-08-05).** The Cypher pre-engine gate remains a conservative token scanner,
not a handwritten parser: it default-denies statement openings and rejects the pinned
external-action/session/maintenance family anywhere outside quotes and real engine comments.
LadybugDB `read_only=True` remains the mutation authority. The pinned engine's 30-hop ceiling
is engine-native. Graph type/property references are null until the engine supplies structural
parse metadata; `confirm=true` checks only top-level engine-typed `NODE`/`REL` values labelled
`Entity`/`RELATES` and warns when none were confirmable. Forgeable structs and scalar UUID
projections remain snapshot-scoped, and engine `INTERNAL_ID` offsets are never public.
The observed pinned-engine physical-address family — `id`, `rowid`,
`internal_id`, `offset`, `hash`, `cast`, `string`, and `to_string` — is refused
in function-call position, including backtick-quoted names. These functions can
expose or derive a physical address directly or erase its engine type; ordinary
public `e.id` and ``e.`id` `` properties remain available.

The P2 rebuild reads the same D48/D54-bearing `memory_v1` relations as the live query surface,
and reader caches are keyed and verified by deployment plus immutable snapshot identity and a
validated leaf version. Cypher shares SQL's kill-switch/admission/audit objects. Pure
PostgreSQL graph helpers remain invoker-security; their transaction-local cap marker makes them
parallel-unsafe, while only projection-backed functions need the no-login definer bridge.
Their paired-clock refusal is internal to the two documented helpers; PUBLIC
has no function EXECUTE privilege in `memory_v1`, and the routed query role is
granted only the manifest-enumerated functions. Nested engine `INTERNAL_ID`
types are refused just like scalar physical IDs. A failure to install the
engine statement timeout fails closed before execution.

Cypher query identity uses the existing scanner's normalized token sequence
plus pinned-engine logical parameter families. It ignores formatting and real
engine comments without pretending that the engine exposes an AST or adding a
second parser.

`question_context` v4 adds default-false `include_facts` and `include_entities` flags. Facts
reuse `current_context`'s semantic nomination, D48/D41 confirmation, both-stance evidence,
30-fact cap, fixed evidence depth 3, and 60-association budget. Entities combine exact
resolution before semantic nomination, deduplicate by survivor ID, confirm once through
`memory_v1.entities_current`, then cap the live survivors at 20. P1 and PostgreSQL are the existing
authorities; graph expansion is not silently added to either context operation.

**Rationale.** The removed Cypher walker repeatedly guessed structural meaning incorrectly.
Reintroducing it for hop bounds, scalar-ID provenance, or graph references would recreate the
same defect family. An ordinary Python child process would add RPC and snapshot-path plumbing
without the filesystem/network confinement the earlier worker contract claimed; the observed
LadybugDB INT128 fault raises rather than hanging or corrupting shared state. Building a nominal
worker would therefore add complexity without meeting its security claim.

**Consequences.** External actions that read-only does not stop remain load-bearing pre-engine
rejections. Mutations may reach the read-only engine but can never commit and map to
`cypher_not_allowed`. Snapshot failures after pinning retain snapshot provenance and snapshots
older than 3600 seconds warn. Reopen true process confinement only when the hosting layer can
provide it or observed engine behavior requires a fault boundary. The three assured
descriptors, exhaustive P2 schema, Cypher/graph signatures, and `surface_manifest_hash` roll
atomically with v4. D81 is assigned to the stacked Batch B contract-correction decision that
precedes this branch at merge time.

**Rejected.** A second Cypher parser; UUID/column-name authority guessing; empty arrays for
unavailable dependency metadata; a confinement-free subprocess described as a sandbox;
implicit P2 neighbors in context operations with no caller-visible graph request.

## D83. Open query makes a clean pre-release cut; retained operations consume `memory_v1`

> **Refined by D98.** The clean-cut/no-compatibility principle remains. The two
> Cypher entry points are removed, leaving seven open-query infrastructure
> operations; graph access is live SQL helpers and typed operations.

**Refined by D87.** The clean-cut/no-compatibility decision remains binding;
the retained operation set is replaced by the four authority-aligned
operations in D87. D87 also supersedes the legacy transport-name exception:
the target uses operation terminology and carries no `/recipes` or recipe-named
SDK/CLI alias.

**Decision (2026-08-06).** RememberStack has no users or integrations that
require compatibility with the 17 demoted recipe adapters. They are removed
from the seeded/public catalog now, without a deprecation window, compatibility
telemetry, or a benchmark-gated removal protocol. Their query patterns remain
as discoverable, non-tool `examples.*` saved queries. The shipping intent
surface remains exactly `resolve_entity`, `question_context`, and
`current_context`, plus the nine open-query infrastructure entry points.

The three assured operations keep their D49 `Envelope` contracts and existing
transport entry points, but every retained live-result path uses the accepted
`memory_v1` views as its invariant authority. In particular, entity resolution,
claim/chunk confirmation, current-fact confirmation, contradiction enrichment,
and retained graph-edge confirmation do not reconstruct D41, D48, or D54 from
base tables. The
registry remains as the small versioned descriptor/execution authority for the
three operations; renaming every recipe transport is not part of this cut.

Before SQL or saved-query execution, the runtime verifies the live `memory_v1`
shape against the checked-in manifest and fails `schema_version_mismatch` on
drift. Server-owned SQL/PGQ separately verifies the pinned live property-graph
contract. P1-backed
SQL functions are executor-resolved query-embedding + ranked-statement bridges. Body
fetch verifies the reproducible embedding-text hash and source/prefix
separation; the source-content hash remains a coordinate until the body store
contains enough ordered-block material to reproduce it.

**Context.** The prior dual-surface design assumed active consumers and made a
paid noninferiority run a gate before adapter removal. The owner confirmed that
no one uses the library and directed a clean implementation now. Analysis:
`plan/analysis/open_query_space_clean_cutover.md`.

**Consequences.** Bootstrap atomically replaces each deployment's recipe rows
with the three canonical descriptors, and registry reads pin their canonical
versions, so old local seeds or same-name custom versions cannot replace or add
tools. Full-v9 stays historical and is not executable compatibility code;
RS-LoCoMo-Full-v10 is the operator-invoked paid protocol over the shipping
surface. Correctness fixtures for the three operations, per-request
interface-shape checks, and the deploy/CI exact-definition comparator replace
migration-parity and adapter-usage gates.

**Rejected.** RLS; preserving unused adapters for hypothetical callers; a
180-day no-user deprecation window; renaming all transports as a substitute for
fixing authorization; a second operation registry; an in-PostgreSQL Lance
runtime; claiming a body hash can be verified from bytes that do not reproduce
its input.

## D84. Extract work is leased per chunk so Claimify can run in parallel

**Decision (2026-08-07).** Stage `extract_claims` is addressed primarily at
**chunk** grain: `target_kind = chunk`, `target_id = chunk_id`, same
`E2_EXTRACTOR_VERSION` Claimify generation as before. After E1 embedding
completes for a representation, the engine enqueues one extract job per chunk
(idempotent). Each worker runs Claimify for a single chunk (with the usual
read-only neighbour bundle). `normalize_relations` is enqueued for the document
version only when every chunk of that representation has terminal extract for
the extractor version (barrier). In-flight **version-level** extract rows left
by older images act as **coordinators** only: they fan out chunk jobs and/or
fire the barrier; they do not re-run serial whole-document Claimify.

**Context.** Version-serial extract made `worker-extract-claims` replicas
ineffective on a single large document (BEAM 1M/10M). E1 already partitions
text into section-bounded chunks; D56 already keys reuse on chunks;
`ProcessingTarget.CHUNK` already exists on the ledger. Analysis:
`plan/analysis/chunk_level_extract_analysis.md`. Binding design:
`plan/designs/chunk_level_extract_design.md`.

**Consequences.** Queue depth for `extract_claims` approximates unfinished
chunks — the correct signal for self-host and UMC worker scale-up. Total model
tokens are unchanged; concurrency rises. Normalize remains version-scoped
(entity-sharded normalize is a separate track). UMC auto-deploy keeps the same
worker image and service names; no control-plane work manufacture.

**Rejected.** Scaling version-level extract only; external per-chunk queues
that bypass `processing_state` (D67); making normalize chunk-scoped in the
same change; automatic skip of dead-lettered chunks for the barrier in v1.

## D85. The full-system LoCoMo answer seat gets the complete shipped read plane

> **Refined by D98.** The current successor is v14 with four assured, seven
> primitive, seven open-query, and three P3 tools (21 total). P2/Cypher and its
> readiness fields are absent; earlier version counts remain historical.

**Refined for the next current-system protocol by D87.** V11 remains
self-describing evidence for the surface it measured. Implementing D87 changes
the assured catalog and therefore requires a new benchmark protocol identity;
no existing artifact is silently reinterpreted.

**Decision (2026-08-07).** `RS-LoCoMo-Full-v11` replaces v10 as the sole
executable current-system protocol. Its Luna answer agent can choose among all
shipped read-only retrieval paths: the three assured operations, seven direct
HTTP/SDK primitives, nine open-query infrastructure operations, and bounded
list/search/read over an ordinary published P3 mount. P1 is reachable through
assured, primitive, and SQL paths; P2 through Cypher; P3 through its filesystem
contract. The protocol fingerprints all 22 descriptors and verifies that the
P3 mount marker equals the readiness report's P3 projection version.

Writes, connector/control operations, raw originals, artifacts, internal-only
primitives, and Plane K are not answer tools. K is not composed by this
benchmark; raw has a separate attributed audit contract. Product reads continue
through the public SDK, and P3 uses `LocalMountPublisher`: there is no
benchmark-only database, object-store, graph, or HTTP retrieval path.

**Context.** V10 built and checked P2/P3 but gave its answer agent only the
three assured operations. That measured an assured-operation subset, not the
current system the owner directed the publication run to score. Analysis:
`plan/analysis/locomo_full_retrieval_agent.md`. Binding design:
`plan/designs/locomo_benchmark_design.md` §§2, 4, 6, and 7.

**Consequences.** V10 artifacts remain self-describing but no v10 compatibility
runner remains. A v11 result is not comparable to v10 because the prompt, tool
catalog, traces, and reachable retrieval planes differ. The same eight-tool,
nine-agent-call, revision, isolation, readiness, and spend guards remain. SQL,
Cypher, or P3 argument mistakes may be returned to the bounded agent for one
corrective plan. Classification uses the typed public error code, including for
HTTP-200 `QueryResult/v1` failures; quota, concurrency, schema drift,
projection/store, transport, and server failures remain terminal.

**Rejected.** Calling the three-operation seat “full”; adding only SQL/Cypher
while omitting primitives/P3; private benchmark reads from PostgreSQL internals,
object storage, or graph files; inventing a P3 HTTP endpoint; pretending absent K ran.

## D86. E3 unknown entity types: retry then drop (not coerce)

**Decision.** When the E3 normalizer emits an entity type outside the deployment
`entity_types` registry, re-call the normalizer for that claim (small inner
budget, unique cost `call_key`s). If still illegal, **drop** the offending
relation/observation assertion and continue the version job. Do **not** coerce
to Concept. Do **not** auto-create registry types from LLM output. Track
unknown-type rates. Gate before mint on the real resolve path
(`CascadeResolver`).

**Context.** BEAM 1M normalize dead-lettered on FK `entities.type = Process`
after successful Claimify (~15k claims), leaving zero observations and no scores.
Unknown predicates already soft-drop; unknown types did not. Relation triples
often fail closed via signature gate first; **observations** had no type gate
and were the primary FK path.

**Alternatives.** Coerce to Concept (rejected). Auto-register types (rejected).
Status quo job DLQ (rejected). Per-claim work-ledger fan-out (deferred here; later decided as D88).

**Consequences.** Inner LLM cost on a rare path; residual drops are re-derivable;
version normalize completes so later stages and scoring can run. Bumps
`E3_NORMALIZER_VERSION`.

**Design.** `plan/designs/e3_unknown_entity_type_gate_design.md`  
**Analysis.** `plan/analysis/e3_unknown_entity_type_gate_analysis.md`

**Vacated by D96** for entity types (extract no longer emits a class).
Unknown **predicates** remain D5.

## D87. Context operations mirror identity, testimony, and fact authorities

> **D98 amendment (2026-08-27).** The four context operations and their grain
> boundaries survive. Cypher/P2 snapshot confirmation is removed; graph
> context uses typed live PostgreSQL operations and bounded helpers.

**Decision (2026-08-10).** The assured retrieval catalog contains exactly four
operations:

- `resolve_entity` determines ranked current survivor identities;
- `testimony_context` returns high-recall claims and source passages only;
- `fact_context` returns semantically relevant relations and observations under
  an explicit world-time scope; and
- `answer_context` returns the complete testimony and fact responses as two
  named members of `ContextBundle/v1`, without flattening their grains.

`question_context`, `current_context`, `include_facts`, and `include_entities`
are removed. There are no aliases, deprecation rows, or compatibility paths.
The library has no consumers requiring them.

All three context operations require a query and accept optional confirmed
`entity_ids` (one to twenty unique UUIDs when present). Omitting IDs preserves
deployment-wide semantic retrieval. When IDs are supplied, PostgreSQL confirms
every ID as a current survivor in the deployment before nomination. Any absent,
retired, forgotten, or foreign ID returns an opaque `unknown_entity` D49
negative with no results; malformed or out-of-bounds input is
`invalid_parameter`. No operation drops only the bad IDs or silently resolves
names. Entity and fact-time eligibility constrain the candidate set before
bounded relevance ranking; a global top-k followed by scope filtering is
forbidden. For multiple anchors, candidates associated with any anchor are
eligible and candidates covering more anchors rank first without an any/all
public switch.

`fact_context` defaults to current facts and also supports one valid-time
instant, overlap with a valid-time interval, and the complete past-through-now
historical set the system still believes. Future-starting facts remain
reachable through explicit `at`/`overlap` modes. A boolean `only_current` is forbidden because its
false case cannot distinguish those intents. These modes use current system
belief and return both stored clocks. Historical system-belief reconstruction
remains the explicit `facts_as_of(valid_at, believed_at, ...)`/open-SQL audit
path.

Every `fact_context` envelope discloses the applied selection through D49's
required, discriminated `temporal_scope`: `current` and `history` carry the
evaluation/belief instant, `at` additionally carries `at`, and `overlap`
additionally carries `from` and `to`. `answer_context` preserves that complete
child field unchanged.

`ContextBundle/v1` is the D49 refinement bound above. `answer_context` exposes
only the shared query, optional entity IDs, and fact-time selector; each child
runs with its canonical defaults. A completed bundle always contains both
literal child envelopes, including their independent negatives, freshness,
truncation, and hydration-drop disclosure. A schema or execution failure in
either child fails the whole request with no half-bundle. Under a frozen store,
active embedding configuration, and evaluation clock, the children are field-for-field
equal to direct child calls; the bundle layer may add or alter no child field.

**Context.** The former three-operation catalog was small but conceptually
mixed. `question_context` described its input, not its output authority, and
optional flags added facts and entities to testimony retrieval. Meanwhile
`current_context` confirmed only `facts_current`, putting the bi-temporal fact
history behind a harder SQL/planning path. The public operations did not match
RememberStack's defining separation between source testimony and adjudicated
facts.

**Rejected alternatives.** Keep `question_context` and add more flags; rename
it `all_sources_context` (bounded results are not all sources, and facts are not
sources); add `only_current`; require entity IDs; let every context operation
resolve names independently; expose only the two layer operations and make
every agent compose them; flatten testimony and facts into one ranked envelope.

**Consequences.** Humans and agents get one routing rule: what was said →
testimony; what is or was true → facts; both → answer; ambiguous identity →
resolve. `answer_context` is pure composition and must be membership- and
order-equivalent—and, under frozen inputs, field-for-field equivalent—to direct
child calls. The operation registry, manifest hash,
API/SDK/CLI/MCP descriptors, consumption skill, public docs, and LoCoMo protocol
identity roll atomically in the same delivery. Open SQL/Cypher, saved queries,
direct primitives, and P3 remain independent read infrastructure rather than
being hidden or removed.

The intent transports use operation terminology as part of the same clean cut:
`GET /operations`, `POST /operations/{name}`, SDK
`list_operations`/`run_operation`, and CLI `remember operations list|run`.
Recipe-era transport names are not retained as aliases; the saved-query
registry remains separately named.

This decision is a narrow amendment to the prior anti-accretion gate.
`answer_context` is admitted without frequency evidence because it adds no
retrieval authority, ranking, hydration, parameter-specific behavior, or
result transformation: it is exactly the two existing complete operations in
one typed transport response. That exception applies only to this named
composition. A fifth assured operation, another bundle, or any composition that
changes either child still must pass the ordinary evidence gate.

**Design.** `plan/designs/open_query_space_design.md` §3.1

**Analysis.** `plan/analysis/context_operation_model_analysis.md`

## D88. E3 normalize work is leased per claim so large documents can normalize in parallel

**Decision (2026-08-10).** Stage `normalize_relations` is addressed primarily at
**claim** grain: `target_kind = claim`, `target_id = claim_id`, same
`E3_NORMALIZER_VERSION` generation as D86. When the extract barrier would
enqueue a single version-level normalize job, the engine instead enqueues one
normalize job per accepted claim of that representation (idempotent). Each
worker runs the existing single-claim normalize path (including D86 type gate,
resolve/mint, relation upsert) and writes observations under the D43 entity
lock. **Relation supersession** and **embed_claim** are enqueued only when every
expected claim of that **document version** has terminal normalize success
(strict barrier, same family as D84). Legacy version-level normalize rows act as
**coordinators** only (fan-out + barrier), not serial whole-version loops.

**Context.** After D84, extract scales with workers; normalize remained one
version lease walking thousands of claims serially (BEAM 1M ~15k claims,
multi-hour wall clock). Scaling `worker-normalize-relations` did not help. D86
deferred per-claim fan-out while fixing FK dead-letter. Analysis:
`plan/analysis/e3_claim_level_normalize_fanout_analysis.md`. Binding design:
`plan/designs/e3_claim_level_normalize_fanout_design.md`.

**Consequences.** Queue depth for `normalize_relations` approximates unfinished
**claims** — a correct signal for self-host and UMC scale-up. Continuous
multi-doc ingest remains correct because barriers are **per document version**,
not global. Relation evidence attach is commutative under concurrency.
Observation final adjudication is a **post-barrier ordered flush** (D43 remains
order-sensitive). Supersession is version-scoped after the barrier with a bound
origin-claim evidence selector and `asserted_at` direction. Barrier evaluation
requires a version/representation advisory lock (D84 pattern). Fan-out materializes
the full expected claim set in the extract-handoff transaction. Component version
bumps for fan-out so coordinator success is not mistaken for normalize readiness.
Postgres carries O(claims) processing rows per large version.

**Design review.** Codex REQUEST_CHANGES absorbed into the design revision
(`design/reviews/REVIEW_codex-sol_e3_claim_level_normalize_fanout_design_2026-08-10.md`).

**Rejected.** Scale version-level normalize only; rely on FIFO queue order for
adjudication correctness; run supersession inside each claim job; global or
lineage-wide barriers; document-order entity typing as a fan-out prerequisite;
automatic skip of dead-lettered claims for the barrier in v1; per-chunk
normalize as the v1 grain (remains a viable later alternative).

**Amends.** D84 handoff (enqueue claim fan-out instead of one version normalize);
D86 “per-claim fan-out deferred” is superseded by this decision for work grain
only — D86 drop/retry rules remain binding inside each claim job.

## D89. Fact retrieval shares one PostgreSQL authority and one operation deadline

> **D98 amendment.** The fact-authority/deadline decision remains binding. Its
> v13 benchmark identity is historical; D98 rolls the current protocol to v14
> and the 21-tool live-graph catalog without changing these fact SQL contracts.

**Decision (2026-08-11).** The unchanged 24-relation `memory_v1` surface factors
current fact evidence into two ungranted private authorities:
`v_memory_fact_claim_live` owns the coordinate-bound current-testimony
association and `v_memory_evidence_lineage_live` owns the D54 fact × document
lineage × stance aggregation. `fact_claim_evidence_live`, `evidence_lineage`,
and `facts_visible_history` compose those helpers and apply
`v_memory_fact_visible` once. Retained assured operations continue to read
`memory_v1`; application code may not reconstruct evidence counts or review
state from base tables.

`fact_context` confirms P1 nominations in bounded exact-key batches, but every
statement while it holds one pooled connection shares one 25-second monotonic
database deadline. Each statement receives only the remaining time. Exhaustion
fails the operation and releases the connection instead of multiplying the
transport timeout by the number of refill batches.

The manifest and benchmark identity roll to `RS-LoCoMo-Full-v13`. V13 retains
the v12 dataset, Luna models, answer prompt, call budgets, and complete 23-tool
retrieval plane, while binding the current D88 11-stage ingest contract with
claim-level normalize fan-out and a distinct observation-adjudication stage.
Verified backups can restore the exact run they protect, but ingest, answer,
and judge checkpoints are never adopted across pipeline or repository
revisions.

**Context.** During the first v12 answer pass, timed-out
`facts_visible_history` expansions continued in PostgreSQL, filled all 15 API
pool slots, and caused unrelated reads to fail. Direct base-table SQL was fast
but would have violated D83 by duplicating D41/D48/D54 rules in `QueryEngine`.
The factored database prototype returned 30 exact history rows in about eight
seconds and 15 facts' representative evidence in about 5.5 seconds on the
ingested LoCoMo store.

**Rejected.** Rebuild D54 and support state in `QueryEngine`; planner settings
without changing the repeated authority expansion; increase the HTTP or pool
timeouts; add a new public query-space relation; silently keep the v12 name
with a different pinned manifest; preserve cross-revision ingest compatibility
for an unused benchmark harness.

**Design.** `plan/designs/open_query_space_design.md` §3.2 and
`plan/designs/locomo_benchmark_design.md` §§2, 9.

**Analysis.** `plan/analysis/fact_context_authority_performance.md`.

## D90. Observation flush work is leased per version-scoped entity unit so post-barrier D43 can run in parallel

**Decision (2026-08-12).** Stage `adjudicate_observations` under the entity
fan-out generation is addressed at **version-scoped entity flush units**, not
bare canonical entity ids. A durable membership table records each unit as
`(deployment_id, version_id, normalizer_version, subject_entity_id)` with a
generated `unit_id`. The ledger row uses `target_kind = entity`,
`target_id = unit_id`, and component version
`e3-obs-flush-2026.08a:claim-fanout-1:entity-fanout-1`. When the claim-normalize barrier would open observation
flush, that transaction materializes the complete membership set and processing
rows (or records durable empty completion). Each worker applies D43 **serially
within the unit** in total order `(asserted_at NULLS LAST, claim_id, statement)`
under the entity advisory lock for the unit. **Relation supersession** and
**embed_claim** are enqueued as **sibling** follow-ups only when every unit for
that version + normalizer generation has terminal success. Legacy version-serial
flush at the pre-fanout component version remains until drained and is mutually
exclusive with unit fan-out for the same version.

**Context.** After D88, claim normalize scales; observation flush remained one
version lease. On BEAM 1M (~2.4k entities / ~6.2k staged assertions), residue
path ~3 assertions/min made multi-day wall clock. Dual design review (Claude +
Codex, 2026-08-12) rejected bare `target_id = subject_entity_id` because D12
work identity has no version dimension and entities are deployment-global.
Analysis: `plan/analysis/e3_entity_obs_flush_fanout_analysis.md`. Binding design:
`plan/designs/e3_entity_obs_flush_fanout_design.md`.

**Consequences.** Queue depth approximates unfinished **units**. Continuous
multi-doc ingest stays version-scoped without silent cross-version observation
loss. Membership carries representation/chunker/extractor coordinates for
barrier lock and supersession reconstruction. Same-entity units share one apply stream that drains all unapplied
staging for that entity in global `(asserted_at NULLS LAST, claim_id, statement)`
order (not per-unit min_asserted_at slices), so overlapping version slices cannot
evidence-collapse away intermediate state. Empty completion uses `obs_flush_version_state` only.
Within-unit order uses `(asserted_at NULLS LAST, claim_id, statement)`.
Readiness, lifecycle, and forget join membership by version. Handlers load
coordinates from membership, not payload alone. Entity lock spans the unit
apply so writers do not interleave mid-sequence.

**Design review.** Claude and Codex both REQUEST_CHANGES on the first draft;
blocking findings absorbed into the design revision (reviews under
`design/reviews/REVIEW_*_e3_entity_obs_flush_fanout_design_2026-08-12.md`).

**Rejected.** Scale version-level flush only; bare entity target_id; in-process
pool without ledger grain; parallel assertion apply within one unit;
assertion-grain jobs; payload-only membership; mixed legacy+fan-out on one
version; unlock-for-LLM without revalidation.

**Amends.** D88 §5.6 ledger grain for the post-barrier flush (product rule of
per-entity ordered apply preserved; lease identity becomes version-scoped unit).
Does not amend D43 ladder semantics, `hub_top_k`, or claim-normalize fan-out.

## D91. Request-path provider spend is a sibling ledger; export is a pull union

**Decision (2026-08-13).** Worker provider spend stays on `cost_ledger` with
D67 identity (`processing_id` NOT NULL, lock-and-copy attribution). Interactive
provider calls (search, assured operations, lookup, SQL nomination, in-process
QueryEngine embeds) write `surface_cost_ledger`. The operator read model is
`v_cost_receipts`. HTTP export is `GET /ops/cost-export/v1` on a **separate
bind**, never a route on the customer FastAPI app. Fail-open on meter persist
only after a durable `persist_failures` increment; otherwise the query fails
503 `surface_cost_unrecorded`.

**Context.** `QueryEngine._embed` and `selfhost_embed_query` discarded
`response.usage`. Stuffing search into `processing_state` would fake work.
Issue #258.

**Design.** `plan/designs/request_path_metering_and_cost_export_design.md`.
**Analysis.** `plan/analysis/request_path_metering_and_cost_export_analysis.md`.
**Reviews.** `design/reviews/REVIEW_*_request_path_meter_export_design*.md`
(Claude APPROVE_WITH_NITS; Codex APPROVE after cursor replay/forward split).

**Amends.** D67 is worker-only after this decision. Worker `occurred_at` is
insert-time `clock_timestamp()`. Worker `outcome` is written at the meter site.

**Rejected.** Synthetic processing rows; nullable `cost_ledger.processing_id`;
export on the customer perimeter; derive token host from API URL (that is D92).

## D92. `remember login` is a CLI device-grant client, not a second credential store

**Decision (2026-08-13).** `remember login` / `logout` live in the base CLI.
They require an explicit `--token-host` / `REMEMBERSTACK_TOKEN_HOST`. The
credential file is CLI-only; `MemoryClient` / `ClientSettings` do not read it.
Logout revokes then unlinks. No engine-native second credential.

**Design.** same document §6.
**Issue.** #268.

## D93. P1 Lance bulk writes and ticker index maintenance

**Status:** superseded by D94. Retained as the historical contract for the
Lance implementation that D94 removes.

**Decision (2026-08-13; remumbered 2026-08-14).** P1 Lance writes and indexes
are maintained as a rebuildable projection (D8), not as a second spine.
Drafted as D91 while that number was still free; `main` assigned D91/D92 to
request-path metering and `remember login`, so this decision is **D93**:

1. **Bulk metadata.** `update_fact_metadata` batches matched-only
   `merge_insert` on `(deployment_id, kind, fact_id)`. Skip rows whose
   eligibility scalars already match Lance. Do not insert unmatched keys
   (would null vectors). Ensure facts join-key indexes before large merges.
   Writers must not call synchronous `optimize()` / `create_index` under
   `label_lock` or an embed/label lease.
2. **Three operations, one ticker (not three jobs, not a pipeline stage):**
   - compact — `table.optimize()` (fold unindexed tails into **existing**
     indexes).
   - retrain — `create_index(..., replace=True)` IVF/FTS.
   - ensure — create contracted indexes if missing or wrong type.
   Compact is not a retrain. Retrain is not on the read path. The ticker
   try-locks the table and chooses at most one op. Writers stay outside
   that lock (Lance allows concurrent writes).
3. **Discovery.** Writers bump `p1_lance_table_stats` after a **vector
   rewrite**. The ticker reads stats (probes Lance if stale). Backfill
   finalizer / admin call the same port under the same lock. Grain is
   physical `(lance_root, table)`.
4. **Heavy fires on durable amount of change**, not calendar-only. Table
   stats hold `changed_rows_since_heavy` and `change_mass_since_heavy`,
   incremented only when a **vector is rewritten**. Eligibility-only and
   skip-unchanged metadata do **not** count. Chunks are more sensitive
   (lower changed-row fraction and change-mass thresholds) than short-text
   facts/claims. `heavy_rebuild_min_hours` is an anti-thrash cap.
5. Under sustained high write rate, heavy is **best-effort**: rate-defer
   without burning attempts; conflict after a full train is one long
   `not_before`; escalate to durable `awaiting_operator` rather than silent
   thrash.

**Context.** BEAM-scale `label_relation` spent hours in per-row Lance
`update` after embeds finished (~7.9k facts → thousands of tiny fragments).
Inline `_maintain_indexed_tail` only called `optimize()` from process-local
counters and never scheduled IVF retrain. Official LanceDB OSS docs
(retrieved 2026-08-13): `optimize` = compaction + prune + incremental index
update, not full ANN retrain
([reindexing](https://docs.lancedb.com/indexing/reindexing)); each write
commits a fragment ([performance](https://docs.lancedb.com/performance)).
Dual design review (Claude + Codex, 2026-08-13, r1–r4) reached
APPROVE_WITH_NITS; trigger/change-mass rules were then made explicit.

**Consequences.** Compose gains `maintain-p1` (gates default off) as a
loop, not `worker --stage`. No `maintain_p1_index` ledger stage and no
reclaim/heartbeat. Vectors stay in Lance only; Postgres keeps stamps, text,
and the stats row. Content/embedding migration rebuild (`p1_batch_rebuild`)
remains a separate family.

**Ticker amendment (2026-08-14).** Ledger units were dropped after
implementation review showed reclaim/heartbeat/attempt fences exist only
because maintain was modeled as a D67 attempt. Analysis:
`plan/analysis/p1_lance_maintain_ticker_analysis.md`. Rejected path:
`plan/proposals/p1_lance_maintain_ledger_units.md`.

**Design.** `plan/designs/p1_lance_maintenance_design.md`.
**Analysis.** `plan/analysis/p1_lance_maintenance_analysis.md`.
**Companion rulebook.** `plan/analysis/lance_indexing_maintenance.md`.

**Rejected.** Per-row `table.update` loops; always-heavy “one proper job”;
calendar-only heavy; counting eligibility-only writes as change-mass;
process-local mutation counters as estate policy; vectors as a live second
copy in Postgres; claimed `maintain_p1_index` units / reclaim / heartbeat;
stopping writers during optimize/retrain; wiring maintain into per-version
readiness.

**Amends.** Clarifies D8 write/maintenance contracts for the Lance
projection. Does not amend D9 query path or D48 hydration.

## D94. P1 search is PostgreSQL-native

> **D98 amendment (2026-08-27).** PostgreSQL 19 replaces the PostgreSQL 18
> image baseline. P1 remains pgvector/pg_textsearch PostgreSQL state, while the
> former P2/deep-hydration split is replaced by live graph traversal in the
> same database; Ladybug is absent.

**Decision (2026-08-14; D98 image amendment 2026-08-27).** P1 moves from
LanceDB into PostgreSQL 19. Pgvector
with HNSW is the required semantic implementation, and pg_textsearch is the
required BM25 implementation for admitted claims/chunks lexical channels.
Built-in `ts_rank`/`ts_rank_cd` are not relabelled as BM25. Semantic and BM25
lists remain independent and fuse by RRF over stable IDs. LanceDB is removed;
it is not a fallback, optional backend, compatibility store, or dual-write
target.

P1 remains a logical, rebuildable retrieval plane, but it does not get a
generalized set of mirror tables. One private `chunk_search` sidecar stores the
normalized searchable chunk body and its current embedding because exact chunk
bodies remain object-store artifact slices. Claims, relations, observations,
and entities already have natural PostgreSQL text rows, so each stores one
current derived embedding on that row and indexes the existing text in place
where a lexical channel is admitted. Future media search follows its accepted
natural segment/representation row; D94 does not invent a `p1_media` table.

Search rows do not duplicate entity arrays, temporal state, lineage,
eligibility, or other filtering scalars. Every P1 result joins the normalized
invariant-bearing authority tables/views inside the ranked PostgreSQL statement
and transaction snapshot. Projection lag may cost recall, but an invalidated,
wrong-deployment, wrong-lineage, or temporally ineligible row cannot become
output. Query embeddings are still produced through the configured embedding
provider and passed to PostgreSQL; no LLM or in-database embedding generation
is added.

There is one active embedding per searchable record. An incompatible model,
dimension, or embedding-input-policy change makes the affected semantic channel
unready, rebuilds and verifies disposable state during maintenance, publishes
the new configuration, and discards temporary build state. Permanent dual
generations and mixed-generation reads are removed from the P1 contract.

**Context.** The Lance estate on `cc8cb23e` contains 2,496 lines of direct
adapter/maintenance production code plus 1,274 lines of dedicated tests before
query-bridge, backup, and cross-store recovery branches are counted. D93 needed
a 451-line ticker for Lance fragment and index maintenance. PostgreSQL now has
the complete minimum search stack. The deciding benefit is removal of an
independent consistency, maintenance, backup, and recovery boundary—not SQL
ergonomics or a benchmark claim.

**Consequences.** Ordinary DML maintains HNSW/BM25 entries; autovacuum and
standard PostgreSQL telemetry own routine cleanup. PostgreSQL 19, advanced
through the reviewed beta/RC/GA replace-and-restore gates, plus pinned pgvector
and pg_textsearch builds
becomes a reference/self-host image requirement. Search shares PostgreSQL CPU,
WAL, storage, backup volume, and failure blast radius. P1 stays derived and
private, outside `memory_v1`, and can be rebuilt from authority and immutable
artifacts.

There are no compatibility consumers. Implementation directly rebuilds P1,
switches the single supported runtime, and deletes all Lance dependencies,
adapters, configuration, maintenance state, migrations, backup paths, runtime
documentation, tests, fixtures, and data. It runs focused functional contract
checks, not dual writes, shadow reads, Lance parity, scale benchmarks, or paid
retrieval benchmarks.

**Design.** `plan/designs/postgres_p1_search_projection_design.md`.
**Analysis.** `plan/analysis/postgres_p1_search_projection_analysis.md`.
**Open, unchosen scale proposal.**
`design/proposals/pgvectorscale_default_index.md`.

**Rejected.** Keep D93 indefinitely; pgvector plus built-in FTS while calling
it BM25; IVFFlat as the default; install or benchmark DiskANN without a present
need; generalized P1 mirror tables; copied entity/filter scalars; permanent
dual embedding generations; ParadeDB's broader AGPL/commercial search surface;
an automatic row-count index switch; Lance/PostgreSQL dual writes; query-time
index builds; RLS.

**Supersedes/amends.** Supersedes D8's Lance placement and D93. Amends D9's
physical channels while preserving independent semantic/BM25 lists, RRF and
zero LLM calls. Amends D48 so P1 confirmation is an authority join in the same
PostgreSQL statement; live graph expansion shares PostgreSQL authority while
progressive evidence/source hydration remains. Amends D37 narrowly so
normalized current chunk search text may live in private `chunk_search` while
exact source documents/bodies remain in object storage. Amends D61's fixed
engine inventory by replacing LanceDB with pgvector and pg_textsearch. Amends
D80's permanent dual-generation P1 cutover with one active embedding and an
explicit unready/rebuild/publish maintenance procedure. Amends D63 by pinning
the reference Qwen/pgvector profile to 1,536 dimensions. Amends D23 by removing
`claims` from monthly partitioning so its in-row BM25 index has global corpus
statistics without a duplicated claim-search table.

## D95. Entity identity is the real-world referent

> **D99 amendment (2026-08-28).** T4 is tri-state; only supported
> `different` creates a cannot-link or contributes to authoritative novelty.
> Insufficient evidence or truncated candidate/adjudication work may mint only
> a merge-eligible provisional fragment, followed by post-profile neighborhood
> convergence. Provider calls run outside the lemma-lock transaction with
> locked revalidation before commit. D99 contains the complete amendment.

**Decision (2026-08-26; T0 rule revised same day).** One `entity_id` per
real-world referent. Names generate **candidates**; they are not the
verdict. **T0 never auto-merges.** Exact lemma match only **lists**
distinct active entity ids with that cleaned spelling (0, 1, or many).
The verdict is T3 or T4:

- **T3** (embeddings of mention+claim vs candidate **profile**) may
  accept a repeat of a known person without an LLM — this is the scale
  path, not T0.
- **T4** when the profile is empty, fights the claim, or several exact
  candidates exist. D99 makes this tri-state: `same` links, supported
  `different` contributes to an authoritative new referent only after a
  complete candidate pass, and insufficient/truncated work may mint only a
  merge-eligible provisional fragment.

A short `profile_summary` (plus salient observations) is evidence for
T3/T4, never the identity key. Job, city, and employer changes update
the profile, not the id. Relatedness is a **relation**. The golden-pair
harness must not treat lemma equality as an automatic match. There is
**no** common-name census and **no** “turn exact-T0 on when the corpus
is large” switch: a large store has more collisions; T3+profile is how
repeats stay cheap. Keeping pre-D95 exact-hit as a **manual,
default-off** flag is an unchosen proposal
(`design/proposals/optional-exact-t0-accept.md`); do not ship it in
WP-I.5. Its trigger, if ever, is a closed unique namespace — not
entity count.

**Context.** Exact-lemma merge at confidence 1.0 made father/son
impossible. A “distinctive lemma / common-name list” shortcut still
auto-merges the second `Jan` unless thousands of given names are
maintained, and enabling that shortcut on a large corpus is backwards.
Operator: a clean entity table is essential; T4 on *uncertain* identity
is acceptable; T4 on every repeat of James is not.

**Consequences.** Homonyms can reach T4. Repeats of a profiled entity
are embedding calls, not judges. First clash / empty profile pays T4.
Same-lemma races stay serialized by the lemma lock; the lock no longer
implies a single row. D22 must include same-name non-matches.

**Design.** `plan/designs/entity_identity_and_retrieval_design.md`
**Analysis.** `plan/analysis/entity_identity_and_retrieval_analysis.md`
**Sequencing.** `plan/plans/entity_identity_and_retrieval.md`
**Proposal (unchosen).** `design/proposals/optional-exact-t0-accept.md`

**Rejected.** T0 exact as always-verdict; T0 auto-merge for “distinctive”
names plus a given-name stoplist; enabling exact-T0 because the corpus
is large (birthday paradox; more collisions, not fewer); identity =
description; `(name, type)` unique key; entity-level `related[]`; T4
emitting mushy relatedness; T4 on every mention of a known person.
Optional exact-lemma auto-accept remains an **unchosen proposal**, not
a shipped switch.

**Amends.** Refines D17 (T0 meaning). Does not replace the T0–T4 ladder,
D20, or D21.

## D96. No entity types; profile is observation prose

> **D98 amendment (2026-08-27).** Untyped entity identity and profile prose
> remain binding. The consequence below applies to live property-graph
> vertices, not P2 snapshot nodes; no graph generation is implied.

**Decision (2026-08-26; revised same day).** There are **no** entity
type classes. E3 emits **names** only. Mentions are a naming transcript
(string, claim, span) with no `emitted_type`. `entities.type`, hats,
`predicate_signatures`, and D18 domain/range as a write gate are
**out**. Dual-role facts (`works_for` with a person as object) persist
because both ends are ids.

What would have been “Company”, “bank”, “based in Italy” lives in
**observations** (D43) and in the **profile**. The profile is a cached
projection of the entity’s important observations (and salient
relations): `profile_summary` prose plus those statements passed to T4
and T3. It is not a classification and not the identity key. “List
banks” is fact/profile **text** retrieval. Facets, if ever added, are
derived from observations — they are not a return of `entities.type`.

Bare head nouns are not entities. Source surfaces become aliases.
`generic_identifier_guard` is populated in resolve. D86 is **vacated**
(nothing typed to reject). Unknown predicates still follow D5.

**Context.** Required classes and first-mint type forked SAP, glued
homonyms’ D18 gates, and implied a Company twin of a person. Optional
M2M hats were considered as a middle path; they still duplicate facts
(“is a bank”) and do not help default retrieval. Operator chose
Hindsight-shaped untyped entities with profile text. Czech
law/judgments still get “list banks” via observations, not a Bank type.

**Consequences.** Extract prompt has no type list. Resolver thresholds
are not per-type (one measured curve, D22). Signature-reject paths
disappear; predicate vocabulary (D5) remains. Live property-graph vertices have no
type property. Extension packs that only shipped types are unused;
packs may still add predicates.

**Design / analysis / sequencing.** same as D95.

**Rejected.** Required extract class; first-mint type as law; M2M hats
on the id; hats on facts as a second vocabulary; `(name, type)` unique;
D18 pre-resolve; deleting mentions; dropping types *instead of* D95.

**Amends.** Withdraws D18 entity types and domain/range. Vacates D86.
Does not withdraw D5, D15’s **predicate** extend-never-fork, D43, or
D95.

## D97. Default retrieval is entity neighborhood plus fact text

**Decision (2026-08-26).** The ordinary question path does not require a
predicate (and there is no type filter). Resolve names to `entity_id`s; load
**observations and relations** for those ids; `neighborhood` with an
**empty** predicate list (already: every `RELATES` edge); match **fact
text** (`fact_label`, observation `statement`, claims). A predicate is an optional narrowing filter and, when used, may be
**any stored predicate** (governed or `other:`). There is no type
filter. Observations are not graph
neighbors. Callers must not be required to guess `other:traveled` vs
`visited`. Unlabeled `related_to` with no fact sentence is rejected as
the only edge shape (hairball). Clean neighborhoods (no filler nouns, no
duplicate-me) are part of the contract.

**Context.** `GraphQueries.neighborhood` already walks all relations when
`predicates` is empty; registry `parent_predicate=related_to` is not the
hop. Hindsight retrieves by fact text + shared entities; Graphiti walks
`RELATES_TO*` and searches the fact sentence. Retrieval that keys on
predicate vocabulary falls over on the D5 escape hatch.

**Consequences.** Assured context operations (D87) default to the same
shape. The changed public identities are `fact_context@2` and
`answer_context@2`; version 1 remains the pre-D97 historical contract, not an
alias. The LoCoMo protocol rolls from v14 to v15 with the descriptor/manifest
change. No new query-path LLM (D9). Graph hops still need an id, not a raw name.

**Design / analysis / sequencing.** same as D95.

**Rejected.** Require a predicate on every neighborhood; query-time
union of all types named X; collapse every edge to unlabeled
`related_to` without fact text.

**Amends.** Clarifies D9/D50/D87 defaults. Does not change zero-LLM
query path, envelope grain, or hydration (D48–D49).

## D98. The graph is live PostgreSQL 19 SQL/PGQ plus work-bounded frontier traversal

**Decision (2026-08-27).** Move the reference runtime directly to the latest
PostgreSQL 19 prerelease, initially the exact Beta 3 image proven by the D98
experiment, and replace LadybugDB P2 with a live PostgreSQL graph in one cut.
`memory_v1.memory_current` and `memory_v1.memory_history` are SQL/PGQ property
graphs over the existing normalized authority views; they copy no graph rows.
Server-owned fixed one-hop patterns use `GRAPH_TABLE` in the cutover.
Each fixed operation first runs a static deployment/anchor-first relational
`budget + 1` guard in the same repeatable-read transaction; application control
flow sends `GRAPH_TABLE` only after admission, so dense-hub refusal never
depends on planner short-circuit behavior.
Two-hop and variable-depth neighborhoods and shortest paths use replacement
deployment-scoped, level-at-a-time frontier functions with hard expansion,
frontier, result, depth, temp, and time budgets. Predicate, valid-time, and
belief-time filters apply during every expansion.

Remove LadybugDB, Parquet graph export, graph objects/generations/manifests,
local downloads and reader swaps, P2 rebuild/readiness, public Cypher, and the
unproved global PageRank/k-core/WCC/Louvain/community products. Current graph
degree is computed from PostgreSQL relation adjacency. P1 search remains the
semantic/BM25 entry point; graph expansion starts from resolved ids and can
share the same MVCC snapshot with search and authority hydration. There is no
backward-compatible or dual-run path because there are no consumers.

Public arbitrary SQL/PGQ is not part of this cut. The current exhaustive
`pglast` 8.4 gate embeds PostgreSQL 18 grammar and rejects PGQ syntax.
Server-owned parameterized PGQ begins now; public PGQ waits for a PG19-capable
AST gate and a separate admission review. The documented PG19 subset is fixed
path concatenation, element patterns/predicates, label disjunction, property
references, `GRAPH_TABLE`, view-backed element tables, and property-graph
DDL/catalog/ACL support. It lacks quantified paths/edges, path variables,
TRAIL/SIMPLE/ACYCLIC and shortest/any-path modes, graph identity/topology
functions, and within-match aggregates, so SQL/PGQ alone is not the traversal
engine. The PostgreSQL 19 Beta 3 rewriter produced a deployment-wide plan for
the view-backed two-hop pattern under the required live/provenance semantics,
so the bounded frontier implementation owns depth two and above until a later
PostgreSQL release passes the same plan and latency gates.

**Context.** PostgreSQL already owns entities, relations, both temporal clocks,
merge redirects, evidence, and documents. Ladybug added a second operational
estate and recall lag mainly to serve shallow, entity-anchored queries. A
PostgreSQL 19 Beta 3 arm64 experiment proved property graphs over views, fixed-hop
temporal predicates, recursive eligible-subgraph shortest path, pgvector 0.8.6
HNSW, pg_partman 5.5.0, and pg_textsearch 1.3.1 BM25. pg_textsearch required a
small pinned PG19 compatibility patch and then passed all 71 upstream SQL
regression tests. Release still requires the same complete linux/amd64 and
linux/arm64 matrix, with amd64 blocking managed deployment. Pre-GA storage is
disposable/replayable; every beta/RC/GA image bump reruns the full capability,
migration, backup, and restore gates.

**Consequences.** Relation commits are visible to later graph statements
without build/publish lag, and graph metadata restores with PostgreSQL. Graph
reads now share authority/P1 CPU, memory, I/O, vacuum, and fault boundaries, so
the design requires a bounded graph pool/role, short statement/transaction
timeouts, read-only transactions, hard depth/result/byte clamps, cancellation,
tenant-first predicates, supporting indexes, and representative plan/concurrent
load gates. There is no graph-generation rollback; recovery is database
recovery plus replayable property-graph DDL. Hard forget has no graph artifact
inventory beyond PostgreSQL backups and bounded in-flight MVCC snapshots.
The obsolete six snapshot-export views are removed. SQL/PGQ element properties
carry only identities and traversal filters; fact labels, support/contradiction
counts, confidence, and provenance are hydrated from the public authority
views in the same repeatable-read transaction.

**Design.** `plan/designs/p2_graph_design.md`.

**Analysis.** `plan/analysis/postgres19_sqlpgq_live_graph_analysis.md`.

**Sequencing.** `plan/plans/postgres19_sqlpgq_live_graph.md`.

**Design review.** Round-one Claude Opus and Antigravity findings and their
dispositions are recorded in
`design/reviews/postgres19_live_graph_design_review_round1.md`; final approval
and the subsequent behavior-level dispositions are recorded in
`design/reviews/postgres19_live_graph_design_review_round2.md`.

**Rejected.** Retain Ladybug for Cypher/analytics; SQL/PGQ alone; recursive SQL
alone; Apache AGE label-table duplication; pgGraph CSR generations; closure
tables; unbounded generated joins; parser bypass; permanent dual run; waiting
for PostgreSQL 19 GA despite having no irreplaceable user database.

**Supersedes/amends.** Supersedes D6, D7's graph-specific rebuild contract,
D10, D11, D13, D16, D72, and the Ladybug-specific parts of D44, D69, D70, D73,
D79, and D82. Amends the community-specific parts of D45/D47; the graph/restore
parts of D74; the benchmark/query-surface parts of D78/D83/D85/D89; and
D9/D48/D49/D50/D61/D87/D94/D96 so graph
expansion is live PostgreSQL execution rather than P2 hydration while
preserving zero-LLM retrieval, authority confirmation, evidence hydration,
bounded and truncation-honest results, and PostgreSQL-native P1. D82's default-deny public
query principle remains; only its public Cypher surface is removed.

## D99. Identity uncertainty is provisional and converges after profile publication

**Decision (2026-08-28).** Amend D95 so T4 returns exactly three evidentiary
outcomes: `same`, positively supported `different`, and
`insufficient_evidence`. Only `different` writes an automatic
`resolution_exclusions` cannot-link. Candidate generation and T4 adjudication
retain their strict work limits, but both report completeness. A checked prefix
that found no match cannot prove that an unchecked candidate is absent.

Here `complete` means **not truncated by the configured result limit**; it does
not claim perfect T1/T2 blocking recall. An authoritative cascade outcome means
that no candidate was surfaced by an untruncated blocking pass and every
surfaced candidate received supported `different`, not that the real world was
searched exhaustively.

When relations or observations require an endpoint before identity is decidable,
the resolver may mint an active, merge-eligible **provisional fragment**. The
append-only resolution decision records `identity_authority=provisional`, the
candidate completeness/adjudication counts, the bounded candidate/verdict audit,
and one deterministic T3 outcome reason. It writes no exclusion for an
insufficient or unchecked candidate. An authoritative same-lemma novelty mint
requires an untruncated candidate set and supported `different` verdicts for
every surfaced candidate, within the configured blocking recall ceiling.

Publishing a current profile after relation evidence, or after the D88/D90
entity observation flush that opens only when every claim in that document
version has finished normalization (the claim-normalize barrier), nominates the
refreshed entity's distinct alias lemmas to D21's existing bounded
neighborhood clusterer. This is the production convergence boundary. Missing or
stale profiles remain ambiguity. Automatic clustering stays fail-closed without
an accepted D22 calibration; configured reversible small merges remain allowed,
and the existing blast-radius guard routes large components to one deduplicated
human review proposal. Proposal identity is deterministic over deployment,
sorted live roots, and cluster-configuration fingerprint. Exact replay
deduplicates; changed membership inserts a replacement and auto-resolves only
overlapping pending proposals, never resolved history. Human-confirmed and positively supported difference
edges remain hard cannot-links. The migration marks pre-D99 automatic binary
rows as ineffective `legacy_binary` audit records; human rows remain effective.
A new supported-different or human decision can revalidate the canonical pair,
and a later superseding decision can retire it while retaining decision ids.

The normalized-lemma PostgreSQL advisory lock continues to serialize identity
writes, but T3 embeddings and T4 generation no longer run inside its transaction.
The resolver snapshots and fingerprints the bounded candidates/profiles under a
short lock, calls providers unlocked, then reacquires the lock and reconstructs
the snapshot before commit. Changed state discards and meters the stale result
and retries a bounded number of times. Exhaustion is a typed retryable contention
failure; stale authority never commits. This follows the profile refresher's
existing optimistic revalidation pattern.

Every final resolution decision records why T3 accepted, evaluated without
accepting, or could not run: `accepted`, `below_threshold`,
`multiple_candidates`, `profile_missing`, `profile_stale`,
`embedding_missing_or_wrong_generation`, or `embedding_hash_mismatch`. The
fixed vocabulary is safe for aggregate metrics; entity identifiers and names
remain audit fields and are forbidden as metric labels. D95's several-candidate
route still goes to T4. A unique-top/margin T3 policy requires a separate D22
curve and explicit D95 amendment.

For benchmark agents, an ambiguous `resolve_entity` response remains explicit
and never becomes a silent guess, but identity metadata alone cannot justify
terminal `Unknown`. The LoCoMo answer loop enforces at least one bounded
testimony/fact/combined-context attempt first. The existing accounted
invalid-completion retry already covers malformed structured reader output and
is not duplicated.

The ordinary v16 benchmark keeps `auto_merge_enabled=false` and performs no
human proposal acceptance before scoring. This prevents gold-informed operator
action from becoming a hidden answer tool. Unattended fragmentation may persist
until a proposal is accepted; v16 records proposal/member/blast diagnostics and
tests content fallback separately from any later review experiment.

**Context.** A fresh v0.6.0 LoCoMo `conv-26` run created 19 active exact-name
Caroline entities and four Melanie entities. T3 made zero of 1,089 decisions.
All 87 exclusions were automatic; 58 separated Caroline fragments. The second
Caroline arrived 109 ms after the first and received a high-confidence T4
non-match whose rationale said identity could not be established because no
profile existed. Candidate loading stopped at ten and T4 at three, yet three
rejections could mint another entity. Every fragment eventually had a current
profile; no production caller invoked neighborhood clustering, no merge event
ran, and the earlier exclusions would have blocked repair. One normalization
item also timed out while provider latency extended the lemma-lock transaction.

**Consequences.** Under-merging remains safer than false merging, but it is now
explicit and repairable instead of becoming false negative authority. Ingestion
continues through thin evidence. Resolution decisions grow by bounded diagnostic
JSON only; there is no second entity registry or provisional status table.
`resolution_exclusions` gains explicit basis/effectiveness and logical
support/retirement decision pointers so clustering can distinguish authority.
Provider calls may be wasted on a revalidation conflict, but their spend remains
metered and the bounded retry prevents livelock. Convergence adds local profile-
publication work and review proposals, never a deployment-wide sweep. Merge and
unmerge remain append-only, reversible PostgreSQL redirects and are visible to
the live graph after commit. With fail-closed auto merge, an unattended run can
remain fragmented until an operator accepts a proposal; D99 makes that state
diagnosable and non-poisoning rather than claiming silent convergence.

Operationally, T3 outcome counts, provisional-mint count, incomplete-search
count, convergence reports, proposal deduplication, and resolver-revalidation
retries become required health evidence. Ordinary drain must finish without
manual worker serialization in the deterministic acceptance workload; repeated
real contention may still consume the outer work-ledger attempt budget and
dead-letter visibly for ordinary operator replay. A convergence failure leaves already-durable facts
and profiles intact and retries idempotently; it does not roll them back.

**Rejected.** Keep binary T4 and tune the prompt/confidence; restore exact-name
auto-merge; remove work limits; treat a bounded prefix as exhaustive; park
ingestion until identity is certain; globally re-cluster the deployment; enable
uncalibrated automatic merge; discard all legacy exclusions without
retaining their audit, or keep legacy binary rows effective without new
evidence; increase lock timeout; silently choose one ambiguous retrieval
candidate; add a second malformed-answer retry subsystem.

**Design.** `plan/designs/entity_identity_and_retrieval_design.md` §3 and §7;
`plan/designs/registries_design.md` §3 and §6.

**Analysis.**
`plan/analysis/entity_resolution_uncertainty_and_convergence.md`.

**Issue.** #319.

**Amends.** D95's binary T4, unconditional T4 non-match mint/exclusion, and
lemma-lock consequence; D21's incremental clustering lifecycle; D22's per-tier
diagnostics; D24 proposal idempotency; D78's benchmark-agent `Unknown` guard.
Preserves D20 registry self-containment, D95's no exact-name auto-merge rule,
D97's explicit ambiguity and zero-LLM engine query path, and D98's live graph.
