# Design: PostgreSQL-native P1 search

**Status:** binding architecture, implemented with the D94 cutover
**Date:** 2026-08-14
**Decision:** [D94](../../decisions.md#d94-p1-search-is-postgresql-native)
**Analysis:**
[postgres_p1_search_projection_analysis.md](../analysis/postgres_p1_search_projection_analysis.md)
**Supersedes:** D8's LanceDB placement, D93, and the removed D93 maintenance design
**Amends:** D9, D23, D37, D48, D61, D63, and D80 as recorded in D94;
PostgreSQL version/runtime amended by D98; ranked-query execution (§8–§9),
the `pg_textsearch` pin (§6) and the benchmark scope (§3) amended by
[D142](../../decisions.md#d142-ranked-search-nominates-from-the-index-and-confirms-in-one-snapshot)
([analysis](../analysis/index_driven_ranked_search.md))

## 1. Problem

P1 provides semantic and lexical retrieval over chunks, claims, fact labels,
entity profiles, and later accepted media representations. Its LanceDB
implementation created a second database, a confirmation hop, a separate
backup/recovery path, fragment maintenance, cross-store generation state, and
failure modes that did not improve the public retrieval contract.

There are no compatibility consumers. The replacement therefore needs no
dual-write period, shadow reads, legacy adapter, data conversion, or parity
benchmark. It needs one clear storage authority, rebuildable derived indexes,
and functional proof that the existing retrieval contracts still hold.

## 2. Decision

P1 SHALL be implemented in PostgreSQL 19 using:

- `pgvector` for semantic vectors and HNSW indexes;
- `pg_textsearch` for the admitted BM25 channels;
- ordinary PostgreSQL columns, joins, and B-tree indexes for filtering; and
- reciprocal-rank fusion (RRF) over independently ranked semantic and lexical
  candidates.

P1 remains a logical, rebuildable retrieval plane. It is not a family of
duplicated search tables:

- chunks use one private `chunk_search` table because exact chunk bodies remain
  slices of object-store `document.md`, outside PostgreSQL authority rows;
- claims, relations, observations, and entities keep their natural text in
  their existing PostgreSQL rows and store one current derived embedding there;
- a future media representation is searchable only on its accepted natural
  segment/representation row; this decision does not create `p1_media`;
- eligibility, entity membership, temporal state, and lineage are not copied
  into search rows. Search confirms every index nomination against the
  normalized authority tables/views in the same PostgreSQL transaction
  snapshot (§8).

LanceDB is removed completely from the active system. It is not a fallback,
compatibility store, optional backend, or migration source after cutover.

## 3. Scope and non-goals

This design decides the P1 storage engines, physical placement, query
confirmation, rebuild behavior, and LanceDB removal boundary.

It does not:

- change the claims/facts distinction or either temporal axis;
- move P3 source artifacts into PostgreSQL (D98 separately removes P2 graph
  snapshots by serving the live graph from PostgreSQL authority views);
- add a generic search-engine abstraction;
- add in-database embedding generation or an LLM to the query path;
- add lexical fact/entity/media APIs that are not already admitted;
- add copied filter columns merely to avoid ordinary indexed joins;
- adopt pgvectorscale or a PostgreSQL graph extension;
- run a storage-engine comparison (the D94 cutover itself ran no scale or
  quality benchmark; D142's index-driven execution is accepted only with the
  scaling benchmark and HNSW quality gate in §9); or
- add RLS. D68's authenticated deployment binding and explicit predicates
  remain the security perimeter.

## 4. Storage topology

| Target | Canonical searchable text | Derived semantic state | Lexical state |
| --- | --- | --- | --- |
| Chunk | `chunk_search.search_text` (derived from object-store source slice) | `chunk_search.embedding` | BM25 on `chunk_search.search_text` |
| Claim | existing `claims.claim_text` | embedding columns on `claims` | BM25 on `claims.claim_text` |
| Relation | existing `relations.fact_label` | embedding columns on `relations` | not admitted; semantic only |
| Observation | existing `observations.statement` | embedding columns on `observations` | not admitted; semantic only |
| Entity | existing canonical profile/description row | embedding columns on that row | not admitted; semantic only |
| Media | future D65 natural segment/representation row | embedding on that row | only if separately admitted |

“Embedding columns” means one vector plus the minimum attestation needed to
prove what produced it: model/generation, input-policy version, and input-text
hash. They are derived, disposable columns, not testimony or adjudicated truth.
For observations the exact embedded input is the current search/rank text
chosen by the observation label policy (`statement` when no distinct label is
produced), and the text hash attests that choice.

PostgreSQL authority and search state being co-located does not erase their
logical distinction. Authority can rebuild P1; P1 cannot create or change
authority.

### 4.1 Claim partition amendment

`claims` SHALL be non-partitioned. Its current-testimony semantic and BM25
channels span ingestion months, while pg_textsearch partitioned indexes use
partition-local BM25 statistics. One claims table permits global partial HNSW
and BM25 indexes over `is_current_testimony = true` and avoids inventing a
duplicated `claim_search` sidecar. This narrowly amends D23; other partitioned
ledgers and evidence joins are unchanged.

Chunk and claim BM25 use `text_config='simple'` as the single multilingual,
language-neutral baseline. Language-specific index families are not added by
this design.

## 5. `chunk_search` contract

`chunk_search` is the only new P1 sidecar required by this design. Its logical
shape is:

```sql
CREATE TABLE chunk_search (
  deployment_id uuid NOT NULL,
  chunk_id uuid NOT NULL,
  search_text text NOT NULL,
  embedding vector(1536),
  embedding_model text,
  embedding_input_policy_version text,
  embedding_text_hash text,
  PRIMARY KEY (deployment_id, chunk_id)
);
```

The reference profile pins Qwen3-Embedding-8B output to 1,536 dimensions on
both hosted and self-host adapters. Pgvector's float32 HNSW limit is 2,000;
leaving the model's 4,096-dimensional default would not be indexable. The
provider request, component version, column typmod, query vector, and readiness
check must all agree on 1,536. An incompatible change is an explicit maintenance
migration under §10, not arbitrary runtime schema variation.
An adapter that returns any other dimension fails the work item and cannot make
the semantic channel ready; the library does not silently truncate an
unstamped provider response.

The table has a deployment-first lifecycle cascade and D23 auditor check
against the partitioned chunk authority row, a BM25 index on `search_text`, and a pgvector HNSW
index on `embedding`. It contains no copied entity arrays, timestamps,
validity/currentness flags, source-kind guesses, or generated summaries.

### 5.1 Search-text derivation

`search_text` is exactly the current deterministic E1 body normalization of the
canonical source slice:

1. read the accepted `document.md` artifact;
2. slice by the chunk's authoritative character offsets;
3. normalize line endings;
4. collapse whitespace runs to one space; and
5. trim leading and trailing whitespace.

It contains no LLM rewrite, summary, neighboring chunk, claim text, or generated
location header. The D80 location header may remain part of semantic embedding
input, but it is attested separately and never enters BM25 text.

Exact source bytes remain in the object/artifact estate. PostgreSQL holds one
normalized searchable copy per live chunk, not another full source document.

## 6. Extensions and supported PostgreSQL

Reference and self-host images SHALL run PostgreSQL 19. Before GA, they pin the
latest proven beta/RC image digest and replay from disposable storage; after
GA, they track the current patched 19.x minor. The image carries pinned and
tested builds of:

- `vector` (0.8 or later, for iterative index scans); and
- `pg_textsearch`, including its required preload configuration, at **1.5.1
  or later** (D142). 1.5.0 added upstream PostgreSQL 19 beta support, so the
  D98 compatibility patch is retired; it also made post-filtered BM25 index
  scans grow their internal batch until the `LIMIT` is filled or matches are
  exhausted and fixed silent truncation in deep scans. Releases before 1.4.0
  return the index's top-k *before* filtering, which §8 must never rely on.
  The image build still runs the extension's upstream SQL tests.

Startup/readiness verifies PostgreSQL major version, extension availability,
and the configured **1,536** embedding dimension. Missing requirements fail readiness;
the runtime does not silently fall back to exact scans, built-in FTS, or Lance.

Pgvector HNSW is the only accepted initial ANN index. Pgvectorscale remains an
unchosen future proposal. It is neither installed nor benchmarked by this
change.

## 7. Writes and readiness

Embedding production remains asynchronous:

1. an authority row becomes eligible;
2. the work ledger schedules its current embedding input;
3. the worker computes the embedding through the configured provider;
4. one PostgreSQL transaction updates the natural row, or upserts
   `chunk_search`, with the vector and all attestation fields; and
5. the work item becomes complete only after that transaction commits.

For chunks, `search_text` may be written before the external vector returns so
the lexical channel can become ready independently. The row is semantic-ready
only when its non-null vector attestation matches the deployment's active
embedding configuration.

Projection lag may reduce recall; it must never expose an ineligible record.
Deletion, invalidation, supersession, and hard-forget are enforced by the
same-statement authority predicates described in §8. Cleanup then removes or
clears disposable search state through the normal lifecycle transaction or
repair worker.

`p1_search_channels` is the small durable current-state authority for query
readiness. It has exactly one row per deployment, target, and channel, carrying
the current model/dimension/policy (or BM25 text configuration), `ready`, and
`updated_at`. It is not a generation history and contains no search records.
Ranked statements require the matching ready row; ordinary asynchronous row lag
does not flip global readiness, while an incompatible maintenance operation
does.

No request path writes, repairs, builds indexes, or calls an LLM.

## 8. Query and filtering contract

### 8.1 What a ranked search must return

A ranked search returns the `k` best **eligible** rows of one target and
channel, ordered by score, then stable item ID. "Eligible" means every
authority rule holds: live lineage/version/representation (D48), the D140
time scope and occurrence rules, the requested entity scope, documents
filter, fact time and D140 fact evidence gate, and any public SRF filter.
Eligibility constrains the candidates *before* the relevance cut — a global
top-k followed by filtering is forbidden (D87). Any work bound that stops the
search early is disclosed by the envelope's truncation marker and counters;
it never looks like "no more results" (D49).

A semantic query is embedded once per syntactic invocation by the configured
embedding provider. BM25 queries use pg_textsearch against the admitted text
column. Hybrid search ranks the semantic and BM25 lists independently and
fuses their one-based ranks by RRF; raw vector distances and BM25 scores are
never added or treated as calibrated equivalents.

### 8.2 How it is executed: nominate, confirm, refill (D142)

Ranking and eligibility are **separate statements in one snapshot**, so that
the index is always used and eligibility is always exact. For each target and
channel, the shared P1 adapter runs a loop inside one `READ ONLY`
transaction at `REPEATABLE READ` isolation:

1. **Nominate.** An *index query* reads only the search table, the
   deployment predicate, any predicate that names a partial index
   (`is_current_testimony` for claims), and the channel's distance expression:
   `… WHERE deployment_id = :d ORDER BY <distance> LIMIT :n`. It contains no
   joins, no authority views, and no additional sort keys, so its only sensible
   plan is the HNSW or BM25 index scan. It returns IDs and scores in score
   order.
2. **Confirm.** A *confirmation query* applies the target's complete
   eligibility predicates — the same predicate builders the adapter uses for
   every filter, scope and gate — to `item_id = ANY(:candidate_ids)` for the
   newly nominated IDs only. It is a primary-key probe per candidate.
3. **Refill.** Confirmed rows accumulate in score order. If fewer than `k` are
   confirmed, the next index query asks for the next batch (doubling `n`,
   starting from `4·k`) and step 2 confirms only IDs not seen before. The loop
   ends when `k` rows are confirmed **and** the next nominated score is worse
   than the `k`-th confirmed score (so a run of equal scores is never cut in
   the middle and ties order by stable ID), or when the index returns fewer
   rows than asked (exhausted), or when the operation's work budget (§12) is
   spent (truncated, disclosed).

PostgreSQL takes the transaction snapshot at the first statement; every
round sees exactly that database state, and read-only `REPEATABLE READ`
transactions cannot fail with serialization errors. A deletion committed
before the snapshot is never returned; one committed later is a normal later
state change. That snapshot is the D48 linearization point (reported by the
open query space as `pg_confirmed_at`).

The semantic and BM25 loops of one hybrid search are independent and may run
concurrently on separate pooled connections before RRF; each loop is
internally consistent. The D97 operation deadline and bounded read admission
govern every transaction.

**Exactness.** BM25 index scans use Block-Max WAND, an exact top-k
algorithm over the same index statistics the full scan uses; with refill to
`k` and the tie rule, a BM25 result equals an exact full scan of the eligible
rows. HNSW is approximate (§9); its recall is a measured quality property.

### 8.3 Entity-scoped nomination (coverage first)

Entity-scoped searches rank by entity coverage (how many requested entities
the item mentions) before score. No text index produces that order, but the
mention tables are indexed by entity:

1. coverage for every scoped item is computed from survivor-resolved mentions
   (counts only, no scoring), within an entity-mention work budget;
2. tiers are taken from the highest coverage down; a tier at or below the
   exact-scoring threshold (starting point 5,000 items, to be measured) is
   scored exactly with the standalone operators (`<=>`, `<@>`) and sorted;
3. a larger tier (a hub entity) runs the §8.2 loop with tier membership added
   to confirmation.

D140's `scope_pending` probe replays the same nomination with pending
lineages widened; it never has its own copy of the ranking logic.

### 8.4 Facts

Fact search runs one §8.2 loop per fact table (relations, observations), each
on its own HNSW index, with the fact authority view, fact-time and entity
predicates and the D140 evidence gate in confirmation. The branches merge by
distance, then qualifier and ID, as before.

D48 confirmation therefore remains mandatory in meaning, and it is not a
remote hop: nomination and confirmation read one PostgreSQL snapshot. The
implementation SHALL NOT materialize an unbounded candidate list in
application memory; every round is bounded by its batch size and the work
budget.

## 9. Index and planner contract

HNSW and BM25 indexes are maintained by ordinary PostgreSQL DML. Autovacuum and
standard PostgreSQL telemetry own routine cleanup; there is no P1 compaction
ticker, fragment ledger, or application-managed index tail.

Each indexed vector column has one fixed dimension and compatible distance
operator. Authority joins in confirmation need conventional indexes on their
existing primary/join keys; confirmation probes are by primary key. This
design does not denormalize authority data into search rows or create a
generic indexing framework. Index queries are deliberately trivial so that
their plan does not depend on cost estimates; a regression test asserts that
each index query's plan is an index scan.

ANN remains approximate. The HNSW index query runs with pgvector's iterative
scan in strict-order mode (`hnsw.iterative_scan = strict_order`) and a
transaction-local `hnsw.max_scan_tuples` sized to the round's batch within
the operation's work budget, so a round is not cut short by the default
`ef_search`. If an HNSW index query returns fewer rows than requested while
the deployment still has unvisited rows, the result is reported as truncated,
never as exhausted. Query-time search parameters are bounded,
transaction-local, and observable; public requests cannot set arbitrary GUCs.

Index-driven execution is accepted only with two measurements, recorded in
the analysis and repeated when the search code changes materially:

- **scaling** — p95 per channel and for the context operations across a size
  sweep (30k → 300k → 3M chunks and the 50M target), `main` against the
  change; p95 must stay roughly flat as the corpus grows and meet the
  retrieval design's interactive budget at the target size;
- **HNSW quality** — recall@k against exact search on sampled real queries,
  and LoCoMo answer accuracy before and after on identical data. A drop
  beyond run-to-run noise blocks release until `ef_search`/iterative-scan
  tuning closes it.

Index each distinct search input once (one entry per identical text and
embedding input, shared by the per-version chunks that carry it) is a
designed, **unchosen** option with an evidence-based adoption trigger:
[distinct-text search index proposal](../proposals/distinct_text_search_index.md).

## 10. Rebuild and incompatible embedding changes

There is one active embedding per searchable record. Permanent dual generations
are removed from the P1 contract.

For a compatible repair, the worker recomputes missing or mismatched rows in
place. For an incompatible model, dimension, or input-policy change:

1. mark the affected semantic channel unready;
2. rebuild the derived vector state and HNSW index in maintenance state (using
   temporary columns/tables only when the migration requires them);
3. verify row coverage, dimensions, attestation, and input hashes;
4. overwrite the current `p1_search_channels` configuration and mark the
   channel ready in the publication transaction; and
5. discard temporary state.

Temporary semantic-search unavailability during this explicit maintenance is
accepted. BM25 may remain available when its text contract is unchanged. This
is simpler than retaining two permanent generations for a library with no
compatibility consumers.

## 11. Backup, recovery, and failure behavior

PostgreSQL backup/restore now captures authority and current P1 state together.
Object-store source artifacts remain covered by their existing backup contract.
There is no separate Lance snapshot, manifest, upload window, or cross-store
recovery ordering.

After restore, readiness validates extension versions, search indexes, active
embedding attestation, and work-ledger completeness. Derived state can be
recomputed from authority plus immutable artifacts. Until required state is
complete, the affected channel reports typed unavailability or partial
readiness according to its existing public contract; it never silently returns
wrong-lineage data.

Expected failures are:

- embedding provider failure: work remains retryable; lexical text can still be
  ready;
- extension/index unavailable: readiness fails for that channel;
- stale/missing derived row: recall decreases and repair is scheduled;
- stale extra derived row: same-statement authority predicates reject it;
- PostgreSQL unavailable: P1 and authority are unavailable together and recover
  through one database procedure.

## 12. Security and resource bounds

Every P1 statement carries the authenticated deployment predicate and the D68
transaction binding. Private search state is absent from the public
`memory_v1`, open SQL, and raw-primitives schemas. Public Cypher is absent; server-owned
SQL/PGQ sees P1 data only through invariant views. RLS remains forbidden
because it would add a second implicit authorization system and undermine the
explicit, reviewable deployment-predicate contract.

Public operations retain bounded `k`, candidate depth, query length, entity
count, timeout, and output budgets. Each §8.2 search additionally has a
**work budget**: a maximum number of nominated candidates (starting point
`max(200·k, 20,000)`, to be measured) and the operation deadline. Reaching it
ends the loop with the truncation marker set and the envelope's
candidate/eligible/drop counters filled, so the caller can see that more
eligible rows may exist. Query strings, source text, vectors, and
credentials are not emitted to logs. Explain plans and index diagnostics remain
operator-only.

## 13. Implementation and deletion plan

Implementation is a direct replacement on latest `main`:

1. build and pin PostgreSQL 19, pgvector, and pg_textsearch in the reference and
   test images;
2. add `chunk_search`, in-row embedding columns, indexes, readiness checks, and
   migrations;
3. change writers and query adapters to the PostgreSQL-native paths;
4. rebuild disposable P1 state from authority and immutable artifacts;
5. run focused functional checks: clean migration, representative ingest,
   semantic/BM25/hybrid queries, entity and temporal filters, invalidation,
   hard-forget, backup/restore, and typed failure behavior;
6. switch the single supported runtime to PostgreSQL-native P1; and
7. delete all LanceDB runtime estate in the same delivery series.

The deletion includes dependencies, imports, adapters, configuration, feature
flags, maintenance ticker/locks/state, `p1_lance` migrations, backup/restore
paths, runtime documentation, and Lance-specific tests and fixtures. With no
compatibility users, migration history may be rewritten before release rather
than preserving Lance tombstones.

No dual write, shadow read, Lance parity run, storage-engine benchmark, scale
benchmark, or paid retrieval benchmark is part of this work. Focused contract
tests are required; benchmarking is not.

Historical analysis, superseded designs, and decision-log entries remain as
history and are labelled accordingly. Completion means no active architecture,
runtime code, dependency, manifest, configuration, migration, backup procedure,
or current operational documentation depends on or offers LanceDB.

## 14. Consequences

Benefits:

- one hot-path database, transaction snapshot, backup, and recovery procedure;
- no cross-store confirmation network hop or synchronization machinery;
- efficient semantic, BM25, entity, temporal, and lineage filtering in one SQL
  statement;
- much less P1 code and operational state;
- no generalized mirror tables or speculative scale architecture.

Costs:

- normalized live chunk search text and vectors add PostgreSQL table, index,
  WAL, backup, vacuum, and replica volume;
- search and authority share PostgreSQL CPU, I/O, and blast radius;
- incompatible embedding changes require a planned semantic-search maintenance
  window; and
- native extension versions become part of the supported PostgreSQL image.

These costs are accepted. The normalized chunk text is the minimum searchable
copy required for BM25; the other targets avoid redundant text copies entirely.

D142's nominate–confirm–refill execution (§8.2) adds:

- **benefit:** search work grows with `k` divided by the share of
  eligible candidates near the top of the ranking, not with corpus size;
  plans no longer depend on the planner's estimate for a large join;
  truncation is detectable and disclosed;
- **cost:** one or more extra round trips per channel (typically one or two
  rounds; more when most near-top candidates are ineligible, for example many
  superseded editions of unchanged text — the case the distinct-text proposal
  addresses), a transaction that spans several statements, and semantic
  search that is approximate (HNSW) instead of an accidental exact scan,
  guarded by the §9 quality gate.
