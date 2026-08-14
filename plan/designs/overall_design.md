# Overall System Design

The architecture that satisfies `plan/requirements/requirements_v3.md`. This document is the
map; per-layer designs (this directory) are the territory. Decision rationale lives in
`decisions.md` (root, cited as D1–D78); supporting research in `plan/analysis/`.

## 1. System overview: three planes (D14)

The system is a DAG across three planes, not a ladder. The plane determines the operational
rules — trigger model, source of truth, mutability, rebuild semantics.

```
 PLANE E — EVIDENCE (per-document chain; Postgres is truth)
            ┌── registries: entities, predicates ──┐
            ▼                  ▼                    ▼
 inputs ─► E0 files ─► E1 chunks ─► E2 claims ─► E3 relations
           (raw+md+         (semchunk,   (claimify,    (normalization,
            PageIndex,      ctx prefix)  coref)        evidence, supersession)
            placement)      │            │   │
                 │          │            │   │      PLANE K — KNOWLEDGE (debounced,
                 │          │            │   │      LLM-compiled; git is truth)
                 │          │            ▼   ▼
                 │          │        K1 general / K2 purpose scopes
                 │          │            │   │
                 ▼          ▼            ▼   ▼      PLANE P — PROJECTIONS (scheduled
            ┌─────────────────────────────────────┐  rebuild; derived, no authority)
            │ P1 search indexes (Postgres:        │
            │    pgvector + pg_textsearch BM25)   │
            │ P2 graph snapshot (LadybugDB)       │
            │ P3 corpus filesystem (GCS tree,     │
            │    mounted read-only to agents)     │
            └─────────────────────────────────────┘
                              │
                              ▼
                 RETRIEVAL: API / CLI / MCP / mounted FS
            entry (Postgres P1) → expand (P2) → hydrate (PG → GCS); browse (P3)
```

*(Diagram note: the arrows descending into plane P come from the E columns (E0 artifacts → P3;
E1/E2/E3 → P1/P2) — plane P derives from the E spine only. K sits beside that flow: K pages
and P3 cross-link as consumers of each other, but K is never a structural input to any
projection — D40 refined; `e0_files_design.md` §6.)*

| Plane | Trigger | Source of truth | Mutability | "Rebuild" means |
|---|---|---|---|---|
| **E** | per-document chain | Postgres | append-only, windows close | n/a — it *is* the truth |
| **K** | debounced/windowed | git repo | agent- and human-edited | re-compile (semantic) |
| **P** | scheduled cycle | none | immutable snapshots | from Postgres, every cycle |

## 2. Stores and sources of truth (D1, D6)

| Store | Role | Authority | Rebuildable from |
|---|---|---|---|
| **Postgres** (Hetzner) | spine: inputs, document/section metadata, chunk metadata, claims, entities, predicates, relations, evidence, validity, processing state and costs; rebuildable **P1** vectors/BM25 indexes plus the sole `chunk_search` sidecar (D94) | **source of truth** for plane E; P1 state remains derived | authority is PITR-backed; P1 rebuilds from authority + artifacts |
| **GCS — raw** | immutable original files | source of truth for file bytes | — |
| **GCS — artifacts** | per-document markdown + `pageindex.json` + conversion sidecars, one immutable **representation** per conversion run (E0, D37/D65: `…/<content_hash>/<representation_id>/…`) | source of truth for converted bodies (nondeterministic converter output is **replayed from storage**, D7 — a toolchain bump creates a new representation beside the old, never regenerates in place) | — (like raw: backed up, not regenerated) |
| **GCS — corpus fs** | **P3**: corpus organized as a mounted directory tree (D40) | derived | Postgres + artifacts (every cycle) |
| **git repo** | plane K: compiled + authored knowledge (K1 plus K2 purpose scopes, D47/D73) | **source of truth** — irreducibly the human-authored content; compiled pages are semantically regenerable from the spine + recorded inputs (D45/D46) | — (own backups) |
| **LadybugDB** | **P2**: graph projection of entities + relations | derived | Postgres (every cycle) |

Two hard rules: validity/invalidation state exists **only** in authoritative Postgres
columns — derived P1 state and Ladybug never independently decide
truth (D6/D94); and every derived store must be
reproducible by a tested batch path, exercised routinely (D7).

## 3. Core data model (D2, D3, D5)

Worked explainer with examples: `plan/analysis/concepts.md`.

```
documents ─< chunks                    entities ──< entity_aliases
    │                                      │
    └─< claims ──< claim_entity_mentions ──┘        predicates (governed registry)
          │                                              │
          └──< relation_evidence >── relations ──────────┘
               (stance: supports        (subject, predicate, object,
                | contradicts)           valid_from/valid_until,
                                         ingested_at/invalidated_at)
```

- **Documents** — **lineages** (connector-native identity: the Drive file ID, the message ID)
  with append-only **versions** over deduplicated content objects; `snapshot | living` semantics
  per lineage; watched sources ingest edits as versions, reusing unchanged chunks' work (D55/D56).
- **Claims** — immutable NL assertions; identity = assertion-by-a-source; temporally classified and
  carrying an immutable **source-asserted validity interval** (D41); never superseded themselves.
  Claims carry **testimony currency** (D54 — bookkeeping, never validity): counts and default
  search consider *current* testimony; history remains queryable.
- **Relations** — distinct **entity→entity** facts; identity = the fact; bi-temporal validity windows;
  the unit of supersession and contradiction (D3); the only layer projected to the graph (D18).
- **Observations** — non-graph facts about **one entity** (a value/statement: headcount, revenue, a
  status); entity-anchored, **untyped** (no governed attribute vocabulary), same bi-temporal validity;
  supersession adjudicated by entity-blocking + the D4 cascade, fail-safe to coexist (D43). Never enter
  the graph; project to PostgreSQL P1 only.
- **Evidence** — many-to-many (for both relations and observations); corpus redundancy collapses into
  evidence counts (free confidence/salience signal).
- **Entities** — canonical registry with aliases, types, cached resolutions; only canonical
  IDs flow downstream.
- **Predicates** — governed vocabulary with `other:` escape and periodic promotion (D5).
- **Ontology** — universal schema.org-aligned core + user extensions anchored to it
  (extend-never-fork), with domain/range constraints; lives in the registries (D15).
  K2 scopes share one entity space and one graph; scope views are registry-declared
  projections, never separate databases (D16).

## 4. Plane E: ingestion pipeline (per-document chain, D12)

Each stage is a Cloud Run worker triggered via Cloud Tasks; each completion enqueues the next
stage for that document. All workers idempotent (content hash + processing version), max 2
retries then dead-letter into Postgres. (Queue topology, steady-state vs backfill lanes,
budget enforcement, and DLQ operations: `orchestration_design.md`, D52–D53.)

1. **E0** (document layer, a chain of idempotent sub-workers — D36; design: `e0_files_design.md`):
   **ingest** (store raw to GCS + `content_hash`) → **convert** (raw → Markdown via the configurable
   module, D38 — incl. the D65 media routes: diarized ASR, video ASR + keyframes, image
   description; each run lands as an immutable **representation**, `media_design.md` §6) →
   **structure** (PageIndex tree + roles + spans + summaries + a **placement hint**,
   D39) → **crossref** (citations). Bodies live in GCS (raw + artifacts buckets); Postgres holds only
   metadata + the queryable section index (D37).
2. **E1** (design: `e1_chunks_design.md`, D57–D58): the **blockizer** derives the deterministic
   block sequence from document.md; PageIndex sections snap to the block grid; semchunk packs
   whole blocks into non-overlapping, section-bounded, anchor-stabilized chunks → context
   (the E1 prefix — exists under the D63 default conventional embedder; a contextual-embedder port config deletes it, D63) → embed
   → P1. Unchanged blocks reuse prior claims/embeddings across document versions (D56).
3. **E2 → E3** (every chunked document — there is no pre-extraction value gate, D25): coreference
   (D19: in the E2 extraction call, all languages) → **Claimify extraction with in-call Selection**
   (proposition-level verifiability KEEP/REWRITE/DROP — the value filter that replaces any
   pre-extraction gate; the E0 PageIndex section path is fed in so references/boilerplate/intro/
   conclusion drop at proposition grain) → entity resolution (tiered cascade T0–T4, D17, via the
   entity registry) → relation normalization (predicate registry, domain/range D18) →
   **supersession cascade** (D4): novelty gate → `(entity_id, predicate)` blocking over
   relations → cheap-first escalation → write-time outcomes (`supersedes` closes windows,
   `contradicts` flags, `same_as` proposes merges). Registry detail: `registries_design.md`.

Note on P1: embedding work is scheduled from E-plane readiness and committed
asynchronously as one current vector/attestation on natural claim, fact, and
entity rows, or as the `chunk_search` row for a chunk. P1 remains a logical
plane-P object—fully rebuildable from authority and immutable artifacts,
carrying no authority itself (D94).

## 5. Planes K and P: aggregate derivation (debounced/scheduled, D12)

Neither plane is triggered per document — K is windowed/debounced ("N new claims or T
minutes"), P rebuilds on schedule; both summarize/project across the corpus.

- **K1/K2** (git): a manifest-driven compile system (D45–D47, refined by D73; design: `k_layers_design.md`).
  A **planner** LLM maintains which pages exist and each page's mechanical **routing rule**
  (entity / subtree / predicate / community / doc-set keys); **writer** LLMs (Codex/OpenCode)
  compile one page each from the rule's evidence + the page's human curation + child-page
  summaries; a deterministic **driver** computes staleness by SQL (rule diff + cited-evidence
  changes), schedules writers children-before-parents, and is the repo's only automated
  committer — no merge conflicts, no hot-file machinery. Incremental refresh is exact: the
  stale set *is* the refresh set. Two page kinds (D46): **compiled** (machine-owned body,
  regenerated) and **authored** (human/agent-owned — decisions, to-be designs — never
  regenerated, review-flagged when cited evidence changes). Citations land in
  `knowledge_artifact_evidence`; a periodic **semantic linter** remains as prose quality
  assurance (cross-page contradictions, broken links), no longer the staleness mechanism.
  Repo exposed via MCP + auto-generated `llms.txt`.
- **Core principles and stances**: authored pages inside a K2 purpose scope (D73), optionally
  supported by compiled cross-project pattern summaries. Only an accountable author promotes
  or changes a principle; citations, watches, review flags, and dispatch keep it connected to
  changing evidence without rewriting it. E3 remains the system's current fact state.
- **P2**: full rebuild from Postgres → Parquet → LadybugDB → validated immutable GCS
  snapshot; readers serve read-only copies and hot-swap (D7). Full design:
  `p2_graph_design.md`.
- **P1**: written inline by plane E (see §4); batch rebuild path exercised for embedding
  migrations and drills.
- **P3 — corpus filesystem** (D40): a rebuildable GCS directory tree organizing the corpus for
  agent navigation, **mounted read-only** to agentic workers. Built from E0 placement hints (D39)
  + entities/relations (K is cross-linked, never a structural input — D40 refined; P3 stays
  rebuildable from the E spine); folders by topic/source/entity, leaves linking to
  the E0 artifacts, generated `_index.md`/`llms.txt` at each level. Cross-links with K
  (understanding ↔ source). Full design: `e0_files_design.md` §6.

## 6. Retrieval architecture (D9, D48, D94)

```
entry                          expand                    hydrate
PostgreSQL P1: fact-label       LadybugDB snapshot:       Postgres authority:
  semantics; claim/chunk        neighborhood, paths,      relation → evidence
  semantic + BM25; scalars      as-of traversal           claims → documents
PG entity registry             (D10/D44)                 → GCS bytes
        └──── RRF fusion ──── graph-distance + evidence-count rerank ────┘
```

- Channels run in parallel; **RRF** fuses; rerankers: graph distance from focal entities,
  evidence count; optional cross-encoder. **Zero LLM calls** on the core path.
- **Projections propose, the spine disposes (D48):** P1 rank and authority
  confirmation run in one PostgreSQL statement/MVCC snapshot. P2 still
  nominates IDs that are re-verified against live Postgres; staleness can cost
  recall, never correctness.
- **The response contract (D49/D87):** each single-authority answer carries a
  grain-labeled envelope (fact / evidence / compiled), inline contradiction
  co-members, per-source freshness stamps (including K page staleness + open
  flags), explicit truncation, and a typed negative taxonomy. The sole
  cross-authority result is `ContextBundle/v1`, which preserves two complete
  child envelopes.
- Composable zero-LLM primitives + four **assured operations as closed registry rows**
  (D50/D87): `resolve_entity`, `testimony_context`, `fact_context`, and
  `answer_context`. Testimony and facts stay separate; the bundle returns their
  complete responses without blending. Reusable
  patterns such as `entity_timeline`, `explain`, and `claims_as_of` are
  non-tool `examples.*` saved queries; MCP renders only the assured catalog
  plus open-query infrastructure.
- Surfaces (D51): HTTP API, CLI, MCP server, and **four read-only mounts** (P3, E0 artifacts,
  E0 raw — off the navigation path, K repo checkout); **filesystem-first** for agent harnesses
  with full mount/API parity; a shipped **consumption skill** teaches cold agents the memory.
- `valid_at` / `believed_at` supported end-to-end on both time axes, composable across calls.
- Full design: `retrieval_design.md`; scenario battery: `plan/analysis/retrieval_scenarios.md`.

## 7. Deployment topology

- **Postgres on Hetzner**: pgBouncer pooling, TLS for cross-cloud access, PITR backups.
- **Workers on GCP**: Cloud Run jobs, Cloud Tasks queues (rate-limited, 2 retries + DLQ).
- **GCS**: input files, markdown, Parquet exports, graph snapshots.
- **git repo** (plane K): hosted remote + independent backup; written only by the aggregate
  workers and humans.
- Retrieval API holds the local LadybugDB snapshot; semantic/BM25 entry and
  authority filtering run in PostgreSQL. Source-byte hydration continues from
  PostgreSQL coordinates to GCS.

## 8. Cross-cutting concerns

- **Cost**: per-layer metering in Postgres; cheap-first cascades everywhere an LLM is
  involved; budgets enforced, not advisory.
- **Versioning**: prompt/model/embedding versions on every artifact; embedding migration is
  a planned batch path (re-embed by version filter).
- **Deletion cascade**: normal input removal propagates through currency/counting and reaches K
  mechanically via citations (D45/D46). D74 hard-forget additionally scrubs PostgreSQL, explicitly
  purges object/P1/old P2/P3/local-serving copies, erases affected K history after owner redaction,
  and re-honors one portable manifest before every serving readiness
  (`hard_forget_design.md`).
- **Maintenance**: PostgreSQL autovacuum/index telemetry and evidence-driven
  reindexing; P1 rebuild drills; semantic linter cadence; predicate-registry
  review. There is no P1 index-maintenance ticker (D94).
- **Observability**: typed pipeline telemetry plus CLI/admin inspection of DLQ, per-stage
  throughput, freshness, and spend; collection backends, dashboards, and alert routing belong to
  the deployment operator/cloud (D60).

## 9. Per-layer designs (this directory)

| Design doc | Scope | Status |
|---|---|---|
| `overall_design.md` | this document | current |
| `e0_files_design.md` | E0 document layer + P3 corpus filesystem (D36–D40) | **current** |
| `e1_chunks_design.md` | blocks + blockizer, sections on the grid, chunk packing, reuse mechanics (D57–D58) | **current** |
| `e2_e3_claims_relations_design.md` | claim extraction + relation normalization; why there is no value gate (D31–D35, D25) | **current** |
| `observations_design.md` | non-graph facts about one entity — untyped, entity-anchored, bi-temporal; supersession by entity-blocking + adjudication (D43) | **current** |
| `registries_design.md` | entity resolution, ontology, governance, review, eval (D15–D24) | **current** |
| `k_layers_design.md` | plane K: planner/writer/driver compile system, compiled + authored pages, K1 plus K2 purpose scopes (D45–D47, D73) | **current** |
| `k3_beliefs_design.md` | *(withdrawn — D73; principles are authored K2 content)* | — |
| `p2_graph_design.md` | graph projection, rebuild, snapshots, search | **current** |
| `retrieval_design.md` | the query machine: primitives, assured operations, envelope, mounts, skill (D48–D51, D87) | **current** |
| `postgres_schema_design.md` | spine schema, tables, indexes, partitioning, deletion cascade | **current** |
| `postgres_p1_search_projection_design.md` | PostgreSQL-native P1 storage, retrieval indexes, readiness, rebuild, and Lance removal (D94) | **current** |
| `orchestration_design.md` | worker runtime: queue topology, lanes, backfill seeding, budget enforcement, DLQ operations (D52–D53) | **current** |
| `evidence_lifecycle_design.md` | document versions, testimony currency, the counting rule, content-addressed reuse (D54–D56) | **current** |
| `packaging_distribution_design.md` | delivery artifacts, delivery-only task execution, enforced code architecture (D62) | **current** |
| `media_design.md` | media (images/audio/video): converter routes, source locators, derivation disclosure, media search (D65) | **current** |
| `docs_site_design.md` | public docs site: in-repo Next.js/MDX static module + same-PR truthfulness contract (D66) | **current** |
| `hard_forget_design.md` | lineage hard-forget, active-store purge, and restore non-resurrection (D74) | **current** |
| `locomo_benchmark_design.md` | pinned `RS-LoCoMo-Full-v13` complete-retrieval benchmark adapter and guarded run protocol (D78/D85/D87/D89) | **current** |

## 10. Open questions

Tracked in `questions.md` (root). Hard-delete obligations are resolved by D74; remaining open
items do not alter the active-store purge contract. (The embedding model is
decided — D63: port config, default `qwen3-embedding-8b`; D94 pins the reference
PostgreSQL profile to 1,536 dimensions.)
