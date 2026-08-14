# P2 Graph Layer — Design (formerly L6)

Drill-down of the P2 (graph) requirements from `../requirements/requirements_v3.md`. Inspirations: Graphiti/Zep
(bi-temporal edges, episode provenance, communities) and the supersession-architecture review
(graph restricted to entity adjacency, single source of truth for validity, no replicated
invalidation state).

For a worked explanation of the claims / relations / evidence model and bi-temporality, see
`../analysis/concepts.md`.

> **Re-evaluated 2026-08-14.** D13 and LadybugDB remain binding. Moving P1 into
> PostgreSQL does not imply moving P2: Apache AGE retains a duplicate graph
> projection, shares the authority database's resource/fault boundary, and has
> not yet proved the traversal-time temporal predicate required by filtered
> shortest paths. Direct recursive PostgreSQL is the smaller future candidate
> only if full public Cypher is deliberately removed. See the
> [analysis](../analysis/postgresql_p2_graph_analysis.md) and the open,
> unchosen [proposal](../../design/proposals/postgresql_p2_graph.md).

## 1. Role: a derived projection, never an authority

The single most important decision. The graph is a **read-optimized projection of facts that
already live in Postgres**. It makes no decisions, holds no state of its own, and can be
deleted and rebuilt at any time without data loss.

Concretely:

- **Validity is decided upstream.** Supersession, contradiction, and entity merges all happen
  in the E2/E3 pipeline (adjudication at the relation level, D3) and are recorded in Postgres.
  The graph mirrors the outcome. (Graphiti
  runs LLM-driven edge invalidation at graph-write time; we deliberately don't — that judgment
  already happened at E2. The graph writer is dumb and deterministic.)
- **No embeddings in the graph.** Semantic entry points come from PostgreSQL P1. Storing
  vectors in two places is how stores drift apart (documented Mem0 failure mode). The graph's
  job starts when you already have an entity ID.
- **No supersession blocking in the graph.** `(entity_id, predicate)` blocking runs on Postgres
  scalar indexes — transactional, cheap, and available even when the graph is mid-rebuild.

What the graph IS for:

| Query | Example |
|---|---|
| Neighborhood expansion | "everything we know about entity X" — typed, time-filtered adjacency |
| Relationship lookup | "how are X and Y connected?" (1–2 hops) |
| As-of reconstruction | "what did we believe about X on 2025-03-01?" |
| Structural navigation | citation chains, person↔org affiliations, doc↔entity mentions |
| graph analytics | communities, PageRank, paths — feeding K1 topic hints (§7) |

## 2. Ontology and schema

LadybugDB is schema-full (typed node/rel tables). Resolution (**D44**): **one `Entity` node table with
`type` as a property** (not per-type tables) and **one generic `RELATES` table with `predicate` as a
property** (not per-predicate tables) — the vocabulary is governed, extensible *registry data*, not DDL
(D5/D18). `UUID` is a valid node primary key (verified in LadybugDB source/tests). The whole projection is
defined by Postgres `v_graph_*` views (`postgres_schema_design.md` §10.A), loaded via `COPY … FROM
SQL_QUERY('pg', …)` or the Parquet hop. Full translation analysis:
`../analysis/ladybug_translation_research/SYNTHESIS.md`.

```cypher
// Nodes
CREATE NODE TABLE Entity(
  id UUID PRIMARY KEY,          // canonical entity_id — survivors only (merged entities redirected, §10.A)
  name STRING,                  // canonical_name
  type STRING,                  // registry value: 8 D18 core types (Person|Organization|Place|Document|
                                //   Event|Concept|Project|Product) + extension subtypes — DATA, not schema
  summary STRING,               // short registry blurb (optional)
  created_at TIMESTAMP          // entities.created_at AT TIME ZONE 'UTC'
);

CREATE NODE TABLE Document(
  id UUID PRIMARY KEY,          // documents.doc_id (distinct id-space from Entity)
  title STRING,
  source_uri STRING,
  published_at DATE             // (current version's published_at AT TIME ZONE 'UTC')::date — v_graph_documents joins the lineage's current version (D55)
);

// Semantic edges — projections of RELATIONS (entity→entity facts), not of claims
CREATE REL TABLE RELATES(
  FROM Entity TO Entity,
  predicate STRING,             // governed predicate vocabulary, see §3 (D18)
  relation_id UUID,             // provenance — hydrate relation + evidence from Postgres
  fact STRING,                  // short label, NOT the full claim text
  evidence_count INT64, contradict_count INT64, confidence DOUBLE,
  contradiction_group UUID,     // both live sides of an unresolved contradiction share it
  valid_from TIMESTAMP,         // ┐
  valid_until TIMESTAMP,        // │ bi-temporal, inherited verbatim (cast AT TIME ZONE 'UTC')
  ingested_at TIMESTAMP,        // │
  invalidated_at TIMESTAMP      // ┘  (relations.status, a GENERATED column, is NOT projected — liveness
);                              //     is derived in Cypher: invalidated_at IS NULL — D6)

// Structural edges — from E0/E1 metadata, not claims
CREATE REL TABLE MENTIONED_IN(FROM Entity TO Document, mention_count INT64, first_seen TIMESTAMP); // aggregate, §10.A
CREATE REL TABLE DOC_CROSSREF(FROM Document TO Document, kind STRING, context STRING); // cites|links_to|attaches|replies_to
CREATE REL TABLE IS_DOCUMENT(FROM Entity TO Document);  // bridge: a Document-typed entity ↔ its E0 doc row
```

> **Observations and claims do NOT project (D43/D18).** A non-relational fact (a value about one entity —
> "Acme's headcount is 600") has no entity object, so it cannot be a REL (a LadybugDB endpoint must be a
> node, never a literal); it lives in Postgres authority + the private P1 projection only. Two correctness rules govern the projection
> (§10.A / D44): **merge-redirect** endpoints to surviving entities (a merge is a redirect, not a rewrite,
> so a naive `status='active'` join silently drops merged-endpoint edges), and **keep every retracted
> edge** (`invalidated_at` set, with no invalidation-age filter, D69) for transaction-time as-of — dropping
> only edges whose survivor-redirected endpoint was retired/forgotten (§13).

### Relations vs. claims — distinct concepts, distinct records

Claims (E2) and graph edges are NOT the same thing, and the mapping is many-to-many:

- a **claim** is a verifiable natural-language assertion as made by a source — possibly n-ary,
  qualified, an opinion or prediction; many claims flatten to no triplet at all
- a **relation** is a normalized binary fact `(subject_entity, predicate, object_entity)` —
  its identity is the fact itself, independent of who asserted it
- one claim can yield several relations; one relation can be evidenced by many claims

So Postgres holds a first-class `relations` table (subject_id, predicate, object_id,
bi-temporal fields) plus `relation_evidence(relation_id, claim_id, stance: supports |
contradicts)`. A **relation-normalization step** in the E3 stage (after claim extraction +
entity resolution) maps eligible claims onto relations against the predicate registry:

- `(s, p, o)` already exists with compatible validity → claim added as **evidence**
  (confidence up, no new fact — ten papers asserting the same affiliation = one edge)
- conflict detected → supersession/contradiction adjudication runs **at the relation level**
  ("Alice left Acme" supersedes the fact, not one sentence in one paper)

Rules:

- **Only canonical entities enter the graph.** Alias resolution and `same_as` merging happen in
  the Postgres registry before projection. The graph never contains two nodes for one entity.
- **Edges project relations, not claims.** Edge count scales with distinct facts, not corpus
  redundancy. Attribute claims ("X was founded in 1998") and non-normalizable claims stay
  E2-only — retrievable via PostgreSQL P1 and authority hydration. This keeps the graph lean (rough sizing at 1M
  docs: ~50M claims → far fewer distinct relations → graph of a few GB, comfortably embedded).
- **Contradictions project too**: an unresolved contradiction between relations becomes two
  live edges with a shared `contradiction_group` property, so retrieval can surface both sides.
- This matches Graphiti's actual model: their edges are facts with episode lists as
  provenance — episodes ≈ our claims, edge ≈ our relation.

## 3. Predicate vocabulary governance

Free-text predicates explode ("works_at" / "employed_by" / "is employee of") and silently break
both entity-keyed blocking and graph queries. So:

- A **predicate registry table in Postgres**: `predicate, description, synonyms[], status`.
- E2 extraction is constrained to the registry vocabulary, with an `other:<freetext>` escape
  hatch.
- A periodic job reviews frequent `other:` values and promotes them (or maps them to existing
  predicates). The ontology evolves by governance, not by accretion.
- Seed vocabulary = the **16 core predicates** (D18, extended by D64: `works_for, member_of,
  affiliated_with, founded, located_in, part_of, authored, created, about, knows_about, knows,
  participated_in, works_on, uses, reports_to, related_to`); the authoritative list +
  domain/range signatures live in `registries_design.md` §4.
  Extend per K2 domain via packs. (The graph stores `predicate` as a property — D18 domain/range is
  enforced upstream by the E3 normalizer, not in the graph.)

## 4. Bi-temporality and as-of queries

Edges carry the four bi-temporal timestamps of their **relation** (the Graphiti/Zep edge
model; D3):

- `valid_from` / `valid_until` — when the fact was true in the world
- `ingested_at` / `invalidated_at` — when the system learned it / learned it was superseded

(Claims carry *different*, **immutable** time evidence — `asserted_at`, the D41
source-asserted interval, `ingested_at` — and never an `invalidated_at`: the revisable
four-column window exists only at the relation level. See D3/D41.)

Default retrieval filter (current beliefs):

```cypher
MATCH (a:Entity {id: $id})-[r:RELATES]-(b:Entity)
WHERE r.invalidated_at IS NULL
  AND (r.valid_until IS NULL OR r.valid_until > $now)
RETURN a, r, b;
```

Time-travel (`as_of` on both axes — "what was true at T as we knew it at T"):

```cypher
WHERE r.ingested_at <= $as_of
  AND (r.invalidated_at IS NULL OR r.invalidated_at > $as_of)
  AND r.valid_from <= $as_of
  AND (r.valid_until IS NULL OR r.valid_until > $as_of)
```

**Multi-hop as-of (D44 correction, sharpened by the source investigation).** For
variable-length traversal, the temporal predicate must be written **inside the recursive
pattern** — LadybugDB's inline recursive predicate `(r, _ | WHERE …)`. It is evaluated per
edge *during* traversal (source-verified at the neighbor-scan level, `on_disk_graph.cpp:308`;
a failing edge never enters the search frontier), so `SHORTEST` finds the shortest path **in
the as-of subgraph**:

```cypher
MATCH p = (a:Entity {id:$id})
    -[rs:RELATES* SHORTEST 1..3 (r, _ | WHERE
          r.ingested_at <= $as_of
      AND (r.invalidated_at IS NULL OR r.invalidated_at > $as_of)
      AND (r.valid_from  IS NULL OR r.valid_from  <= $as_of)
      AND (r.valid_until IS NULL OR r.valid_until >  $as_of))]-
    (b:Entity)
RETURN p;
```

> **Never combine `SHORTEST` (or `ALL SHORTEST` / `WEIGHTED_SHORTEST`) with an outer
> `WHERE all(r IN rels(p) …)` filter.** The outer form is applied **after** matching — no
> optimizer pushdown exists — so `SHORTEST` searches the *unfiltered* graph, the filter then
> discards the found path, and the query **silently returns nothing** although a longer path
> satisfying the predicate existed at `$as_of`. With plain (non-shortest) `*1..k` matching the
> outer form is merely wasteful; with any shortest-path mode it is a correctness bug. Inline
> predicates accept rel-only or node-only conjuncts (our temporal filter is rel-only — fits).
> Full evidence and the complete querying rulebook every implementer and agent must follow:
> `../analysis/ladybug_query_semantics.md`.

You **cannot** `MATCH`-traverse a `PROJECT_GRAPH[_CYPHER]` projection — projected graphs feed GDS
algorithms only (PageRank/components/paths); path `MATCH` runs on the persistent catalog (`USE GRAPH`).
For heavy/repeat as-of analytics, materialize a persistent as-of graph (`CREATE GRAPH` at rebuild). This
**refines D10**, whose "as-of via projected graphs" holds for *algorithms*, not path traversal. (Source-
verified; `../analysis/ladybug_translation_research/SYNTHESIS.md` §4.)

Supersession never deletes an edge — adjudication closes the **relation's** window in
Postgres, the projection mirrors it into the edge's `valid_until`/`invalidated_at`. The
evidence claims keep their own bi-temporal record (when asserted / when ingested) — two
clocks, two purposes: claims record what sources said and when; relations record what the
system currently holds true. History stays queryable forever.

## 5. Sync architecture: rebuild-first, snapshots for readers

LadybugDB's concurrency model (verified): **one READ_WRITE process XOR many READ_ONLY
processes** on the same database files. Don't fight this — design around it.

### The writer: periodic full rebuild

Instead of incremental event application, the P2 worker **rebuilds the whole graph from
Postgres on every cycle**. The projection inputs are the Postgres **`v_graph_*` views** (D44,
`postgres_schema_design.md` §10.A) — they encapsulate the casts (timestamptz→UTC, enum→text), the
**merge-redirect** of endpoints to surviving entities, the **keep-retracted** rule (endpoint joins are
the retention boundary), and the `MENTIONED_IN` aggregation, so the worker is dumb:

1. Read the projection views. Two transports consume the *same* views:
   - **Parquet hop (committed baseline, D7):** `COPY (SELECT * FROM v_graph_<t>) TO '<t>.parquet'`, then
     `COPY <T> FROM '<t>.parquet'` into a fresh LadybugDB DB (bulk path; tens of millions of rows in
     minutes). **Load order: all node tables before any rel table** (endpoints resolve against node PKs
     at COPY time — a missing endpoint throws).
   - **ATTACH-direct (optimization, spike before adopting):** `ATTACH '<pg-ro-conn>' AS pg (dbtype
     postgres)` then `COPY <T> FROM SQL_QUERY('pg', 'SELECT * FROM v_graph_<t>')` — no Parquet hop. Both
     `COPY <Node|Rel> FROM SQL_QUERY` are verified; pending: cross-DB scan throughput/pushdown at 10⁷–10⁸.
2. **Validation gate before publish:** every retained edge endpoint resolved to exactly one emitted
   survivor (no merge cycle, no dangling endpoint), and per-table graph-count == view-count. A failure
   **aborts** the snapshot.
3. Checkpoint, upload to GCS as an **immutable versioned snapshot**
   (`gs://…/graph/snapshots/<timestamp>/`), then update the `latest` pointer. Graph-derived analytics
   (PageRank/communities, D11) are computed **after** load and written back to Postgres — never
   reprojected into the node tables (that would be circular).

Why rebuild-first instead of incremental:

- **Zero drift by construction.** The "rebuildable from Postgres" requirement isn't a dusty
  disaster-recovery script — it's exercised every cycle. Consistency checking, merge
  handling, and out-of-order event headaches all disappear.
- **Entity merges become trivial.** A merge that re-points thousands of edges is a nightmare
  incrementally and a no-op in a rebuild.
- **It fits the trigger model.** P2 is a debounced aggregate layer anyway (requirements v2) —
  nobody needs second-level graph freshness; the cadence (start: hourly) is the freshness SLA.
- **Cheap at our scale.** A few GB rebuilt in minutes on one Cloud Run job. Only when rebuild
  time outgrows the cadence does incremental application pay for itself — and the watermark
  machinery can then be added without changing readers at all.

### The readers: read-only snapshot copies

The retrieval API/CLI never touch the writer's files. Each API instance:

- downloads the `latest` snapshot to local disk at startup,
- opens it `READ_ONLY` (multiple processes allowed — this is exactly the supported mode),
- polls the `latest` pointer and hot-swaps to a new snapshot when it appears.

This gives horizontally scalable reads, zero lock contention, cross-cloud friendliness
(Hetzner Postgres never serves graph queries), and free point-in-time debugging — old
snapshots ARE the graph as-of their timestamp.

### Alternative (a deliberate non-goal): incremental between rebuilds

Rebuild-first is the design. Incremental application is documented here only as the alternative
we would adopt **if** sub-hour graph freshness ever became a hard requirement — it is *not* part
of the current design. The shape, for the record: keep the periodic full rebuild as the anchor,
and between rebuilds apply claim events from a Postgres outbox (`graph_events`, watermark stored
in Postgres) to a working copy, publishing micro-snapshots; the rebuild still bounds drift to
one cycle. At the target scale, rebuild-first is sufficient (§5).

## 5b. Verified LadybugDB capabilities (source tree + official docs)

Surveyed from the vendored source (`../../_additional_context/ladybug`) and docs.ladybugdb.com:

| Capability | Status |
|---|---|
| Vector index | HNSW extension (cosine/l2/l2sq/dotproduct), **node-table properties ONLY** — rel/edge properties cannot be vector-indexed |
| Filtered vector search | yes, via projected graphs (`PROJECT_GRAPH`, `PROJECT_GRAPH_CYPHER`) |
| FTS | BM25 + stemming (28 languages) extension, **node-table STRING properties ONLY**; stopword changes require index rebuild |
| Paths / BFS | native Cypher: `*min..max`, `SHORTEST`, `ALL_SHORTEST`, `WEIGHTED_SHORTEST`, TRAIL/ACYCLIC modes |
| Projected graphs | inputs to **GDS algorithms only** — cannot be `MATCH`-traversed (D44); as-of *traversal* uses inline recursive predicates (§4) |
| Recursive-pattern predicates | `[e* … (r, n \| WHERE …)]` — evaluated **during traversal** (per-edge at the neighbor scan; node side via semi-mask); rel-only/node-only conjuncts; composes with `SHORTEST`/`ALL SHORTEST` (`../analysis/ladybug_query_semantics.md`) |
| Graph algorithms | PageRank, K-Core, WCC/SCC — **no Louvain/Leiden** (community detection needs an external pass) |
| Bulk load | multi-threaded `COPY FROM` Parquet/Arrow/CSV/NPY; `ATTACH` DuckDB/Postgres/SQLite |
| Concurrency | confirmed: one READ_WRITE process XOR many READ_ONLY processes; in-memory mode; WAL + checkpointing |
| Serving | embedded only — no REST server; Python/Node/Rust/Java/WASM bindings |

Two design touch-ups this verification forces:

- **As-of traversal**: there are no native temporal query semantics — implemented via
  **inline recursive-pattern predicates** on the temporal columns (§4; evaluated during
  traversal). Projected graphs serve *algorithms* only (D44), and the outer
  `all(r IN rels(p) …)` form is post-hoc — never combine it with `SHORTEST` (§4 warning).
- **Communities**: Louvain/Leiden are not shipped. Run community detection externally
  (e.g. igraph/graspologic over the same Parquet export that feeds the rebuild) and write
  assignments to Postgres; PageRank/K-Core/WCC can run natively in LadybugDB.

## 6. Retrieval flow — each store does one job

"No embeddings in the graph" means no vectors inside the LadybugDB snapshot — it does NOT mean
relations aren't semantically searchable. The semantic index for relations lives in the
private PostgreSQL **P1 fact projection**, keyed by `relation_id`.

### Decision record: why relation vectors live in P1, not LadybugDB

Considered and rejected: putting fact-label embeddings + HNSW into the graph snapshot for
one-engine hybrid search. Four reasons:

1. **Hard blocker — node-only indexes.** LadybugDB cannot vector- or FTS-index relationship
   properties. Indexing facts in-graph would require reifying every relation as a node
   (Entity→RelationNode→Entity), roughly doubling graph size and contorting every traversal.
2. **Snapshot economics.** 5–15M fact embeddings at 1024–1536 dims fp32 ≈ 20–90 GB inside
   every snapshot (vs. a few GB without), plus a full HNSW build per rebuild cycle (hours, not
   minutes). This kills the rebuild-first + ship-to-readers model that the rest of the design
   depends on.
3. **P1 exists regardless.** E1 chunks and E2 claims are not graph objects; their vectors
   must live in P1 anyway. Splitting the vector estate across two engines means two
   embedding pipelines, two index-maintenance regimes, two sets of failure modes.
4. **The join we avoid is cheap.** Vector search returns top-k (~100s) relation_ids; the graph
   then does ID-keyed expansion/BFS on them. That cross-store hop is microseconds of ID
   lookups — not worth an architecture to eliminate.

Division of labor: **PostgreSQL P1 = entry** (semantic candidate generation for facts,
semantic + BM25 candidate generation for claims/chunks, and scalar filters),
**LadybugDB = structure** (expansion, paths, graph-distance reranking, as-of traversal via
projected graphs). Revisit only if the snapshot model itself changes (e.g. an incremental
writer makes in-graph HNSW maintenance plausible) — and even then, reason #1 must first
disappear upstream.

### The relations search table (PostgreSQL P1)

One row per distinct fact: `relation_id`, a canonical **fact label** ("Alice Novak works at
Acme as VP of Engineering") with its embedding, plus scalar columns `subject_id, predicate,
object_id, valid_from, valid_until, invalidated_at, evidence_count`. The label is regenerated
when adjudication materially changes the relation (one short sentence — cheap).

This is Graphiti's edge-fact search relocated to the designated P1 projection. Searching
distinct facts instead of raw claims shrinks the search space ~5–10× and stops high-redundancy
facts from crowding the result list; scalar columns give filtered semantic search (predicate,
entity scope, as-of windows) before the vector stage. Fact-label BM25 remains an explicit
open-query deferral; D94 does not silently add a public lexical-facts channel.

### Search paths by query shape

| Query shape | Path |
|---|---|
| "How are A and B related?" | entity resolution → graph adjacency (no vectors) |
| "Who works at Acme?" | structured: relations `object=acme, predicate=works_for` (scalar only; D18 registry predicate) |
| "Alice's career changes?" | semantic over P1 relations, scoped to subject=alice; structured and graph signals may rerank |
| vague / no clear entity | semantic over relations AND claims; claim hits join to relations via evidence |
| "what did source X say" | claim/chunk search (E1/E2); relations hydrate *down* to evidence |

### Pipeline

```
query ──► entry points ──────────► expansion + rerank ──► hydration
          PG P1 relations          LadybugDB              Postgres authority
          (semantic)               neighborhood, paths,   relation → evidence
          PG P1 claims/chunks      as-of filtering,       claims → sources,
          (pgvector +              graph-distance         validity metadata,
           pg_textsearch)          reranking from         GCS pointers
          PG registry (entity
          name/alias lookup)       focal entities
```

### Reranking (Graphiti-inspired, zero LLM calls at query time)

- **RRF fusion** of the lexical/semantic/structured channels — default
- **graph-distance reranker**: BFS distance in the snapshot from the agent's focal entities
  ("facts near Alice") — the highest-value idea in Graphiti's search stack
- **evidence-count boost** (≈ Graphiti's episode-mentions reranker) — free from
  `evidence_count`
- optional **cross-encoder** as a flagged final stage for quality-over-latency calls
- hard rule: the core search path makes **no LLM calls** (this is how Zep hits ~300ms P95)

The API exposes composable primitives (entity lookup, `neighborhood(entity_id, predicates?,
as_of?, hops?)`, `path(a, b, max_hops)`, relation/claim search with filters) **plus named
search recipes** (`relation_hybrid_rrf`, `relation_near_entity`, `claims_verbatim`, …) so
agents pick a strategy instead of assembling plumbing per call.

## 7. Communities and K1 hints

LadybugDB ships some graph algorithms natively (PageRank, K-Core, connected components). Run
them on the snapshot after rebuild, write results **back to Postgres** (community assignments,
centrality scores) — the graph stays a projection. Uses, in priority order:

Note (corrected 2026-07-19, **D72**): `LOUVAIN` **is** shipped in the deployed engine's algo
extension — verified live on the build we run, and verified to be real community detection
(two cliques joined by one bridge split correctly, where WCC sees a single component). The
whole analytics pass therefore runs natively on the freshly built snapshot: PageRank, K-Core,
WCC, and Louvain. The earlier external-pass plan (igraph/graspologic over the Parquet export,
D11) is superseded and kept only as the documented fallback shape.

Community topic **labels** (`communities.label` — the human-readable name K1 topic pages and
compile hints carry) are written during the same writeback by a batched micro-LLM call over
each community's top members by PageRank (small model, versioned under the
`community_detector` component). Labels are navigation aids only; nothing load-bearing reads
them.

1. **K1 compile hints**: communities ≈ candidate topics; "claims in community C changed" is a
   better incremental-refresh trigger for K1 summaries than per-file signals (Zep uses
   communities exactly this way).
2. **Entity importance**: PageRank as a salience prior for retrieval ranking and K topic
   prioritization.
3. **Registry hygiene**: tiny disconnected components often indicate entity-resolution misses.

## 8. Failure modes

| Failure | Handling |
|---|---|
| Snapshot corruption / bad rebuild | Validation gate before `latest` pointer moves; readers never see it; previous snapshot stays serving |
| Writer crash mid-cycle | Snapshot upload is atomic (write-then-pointer-swap); next cycle just reruns — rebuilds are idempotent by construction |
| PG↔graph drift | Impossible beyond one cycle (rebuild) — no reconciliation jobs needed |
| Graph size growth | The D69 default is unbounded by invalidation age: retain every relation whose survivor-redirected endpoints remain emitted active nodes. Measure snapshot size/rebuild time and transaction-time demand; a finite hot-snapshot horizon requires a later binding P2 design revision with an explicit fallback contract, not a hidden Phase-0 value. |
| Predicate explosion | Registry governance (§3); rebuild makes vocabulary cleanups retroactive for free |

## 9. Open questions

1. Rebuild cadence to start — hourly or every 6h? (Cost is one Cloud Run job + a few GB of GCS
   traffic per cycle.)
2. Do `MENTIONED_IN` edges link to documents only, or also to PageIndex nodes (finer-grained,
   bigger graph)?
3. Should attribute claims ever project into the graph (as entity properties), or stay
   E2-only? (Current call: E2-only.)
4. Where do retrieval API readers run — same Cloud Run service as the rest of the API, with
   snapshot on local SSD? Snapshot size will decide.
5. Parameter binding inside inline recursive predicates (`(r, _ | WHERE r.ingested_at <=
   $as_of)`) is unverified upstream — no LadybugDB test exercises it
   (`../analysis/ladybug_query_semantics.md` R5). One-line test at implementation time;
   fallback is literalizing the timestamp into the query string.
