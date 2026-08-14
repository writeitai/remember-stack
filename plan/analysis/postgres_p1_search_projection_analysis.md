# Analysis: collapse P1 search into PostgreSQL

**Status:** non-binding analysis supporting D94
**Date:** 2026-08-14
**Repository baseline inspected:** `origin/main` at `cc8cb23e`
**Question:** should P1 remain a LanceDB projection beside PostgreSQL, or become
a rebuildable search projection inside the authoritative PostgreSQL database?

## 1. Why this question is open again

LanceDB was selected when the important comparison was “specialized vector
store versus an in-memory PostgreSQL HNSW index.” That comparison is now too
narrow. The current P1 is responsible for vector search, lexical search,
filter scalars, generation pinning, bulk writes, hard-forget purges, local
storage, backup participation, commit-conflict retries, compaction, IVF/FTS
retraining, readiness and a PostgreSQL confirmation hop. D93 added a dedicated
maintenance ticker because these duties were not automatic in LanceDB OSS.

The relevant PostgreSQL alternative is a three-part stack:

1. `pgvector` stores embeddings and supplies exact, HNSW and IVFFlat search.
2. `pg_textsearch` supplies corpus-aware BM25 ranking and a top-k index. Built-in
   PostgreSQL `ts_rank`/`ts_rank_cd` remain useful but are not BM25.
3. `pgvectorscale` can replace the HNSW index with StreamingDiskANN when a
   measured corpus no longer fits the HNSW resource envelope. It does not
   provide text search or hybrid fusion.

All three are PostgreSQL extensions. They keep authority rows, derived search
rows, filters, joins and index visibility inside one MVCC/backup boundary.

## 2. Local evidence

The following direct Lance-specific estate exists on the inspected baseline:

| Production file | Lines | Responsibility |
| --- | ---: | --- |
| `src/rememberstack/adapters/selfhost/lance.py` | 1,804 | tables, vectors, FTS, indexes, retries, search, bulk mutation |
| `src/rememberstack/spine/p1_maintain_ticker.py` | 451 | discovery and compact/retrain/ensure policy |
| `src/rememberstack/spine/p1_maintain_lock.py` | 101 | cross-process maintenance exclusion |
| `src/rememberstack/adapters/selfhost/p1_locked_purge.py` | 86 | hard-forget coordination |
| `src/rememberstack/spine/migrations/versions/p9_12_0033_p1_lance_maintain.py` | 54 | maintenance state |

That is 2,496 production lines before shared P1 interfaces, query-bridge code,
backup/restore branches and cross-store recovery are counted. Three dedicated
test files add 1,274 lines. A PostgreSQL implementation replaces part of this
estate, so these are delete-or-replace candidates rather than a claimed net
deletion count.

More important than line count are the independent failure modes:

- PostgreSQL can commit truth while a Lance write fails or is delayed.
- Lance can nominate an old generation or an invalidated ID, requiring a
  second PostgreSQL confirmation statement.
- deletes and hard-forget must reach two physical stores;
- Lance datasets need their own backup, restore and index-readiness handling;
- ordinary writes create fragments/unindexed tails and can conflict at commit;
- compacting an index is not the same operation as retraining IVF/FTS.

D93 is evidence that this cost is current rather than hypothetical: a BEAM
ingest spent hours performing small Lance mutations, and the shipped IVF/FTS
indexes required a new durable amount-of-change policy plus maintenance loop.

## 3. External capabilities checked

All external sources below were retrieved on **2026-08-14**.

### 3.1 pgvector

The official pgvector documentation states that:

- vector data participates in PostgreSQL ACID, point-in-time recovery and
  joins;
- HNSW has no training step and accepts incremental inserts;
- IVFFlat is trained from existing data and therefore remains sensitive to
  initial population and list selection;
- filtering can use ordinary PostgreSQL indexes, partial indexes,
  partitioning and iterative ANN scans;
- hybrid search is composed with PostgreSQL text search and fused with RRF or
  a cross-encoder.

Source: <https://github.com/pgvector/pgvector>.

### 3.2 PostgreSQL built-in full-text search

PostgreSQL parses documents to `tsvector`, parses queries to `tsquery`, and
ranks matches with `ts_rank` or cover-density `ts_rank_cd`. Its documentation
explicitly says those rankers use no global corpus information and that
ranking many matches can be expensive. They are not BM25.

Source: <https://www.postgresql.org/docs/current/textsearch-controls.html>.

### 3.3 pg_textsearch

Tiger Data's PostgreSQL-licensed `pg_textsearch` extension provides:

- BM25 with configurable `k1` and `b`;
- a `USING bm25` index and `<@>` ordering operator;
- PostgreSQL language/text-search configurations;
- Block-Max WAND top-k execution;
- expression, partial and partitioned indexes;
- WAL/replication/vacuum integration and automatic memtable spill/segment
  compaction.

The current release supports PostgreSQL 17 and 18 and requires
`shared_preload_libraries`. `bm25_force_merge()` is an optional post-bulk-load
optimization, not a correctness or visibility step. Current limitations
include no native phrase queries, partition-local BM25 statistics and
synchronous compaction during some spills.

Sources: <https://github.com/timescale/pg_textsearch> and
<https://github.com/timescale/pg_textsearch/releases>.

### 3.4 pgvectorscale

Pgvectorscale builds on pgvector and adds StreamingDiskANN, Statistical Binary
Quantization and optimized `smallint[]` label filtering. Arbitrary SQL
predicates are supported through streaming post-filtering; they are not all
magically pushed into the DiskANN graph. Its SQL distance operators remain the
pgvector operators, so changing HNSW to DiskANN need not change the public
query contract or RRF implementation.

The project still describes itself as early stage. It is relevant when a
measured HNSW index no longer fits the available memory or misses the filtered
search latency/recall envelope, not merely because the extension exists.

Source: <https://github.com/timescale/pgvectorscale>.

## 4. Alternatives

### A. Keep LanceDB and D93

This preserves known search behavior and the existing implementation. It also
preserves a second physical lifecycle, projection drift, PostgreSQL
confirmation queries and all of D93's maintenance machinery. It wins only if
representative measurements show a material recall, latency or cost advantage
that PostgreSQL cannot meet.

### B. pgvector plus built-in PostgreSQL FTS

This has the smallest dependency footprint and removes the second store. It
does not preserve BM25 semantics. PostgreSQL's own documentation identifies
the lack of global ranking information and the cost of ranking large match
sets. This is a useful fallback and phrase-search channel, not the primary
lexical replacement.

### C. pgvector HNSW plus pg_textsearch BM25

This removes the store boundary while preserving independent semantic and
BM25 nomination channels. Both operate over stable IDs in PostgreSQL and can
apply the same authority filters in the same statement. HNSW needs no training
ticker; BM25 maintenance is extension-owned. The costs are the binding
PostgreSQL 18 baseline, native-extension packaging, more PostgreSQL
WAL/storage/CPU, and the
need to tune autovacuum and resource isolation.

### D. pgvectorscale DiskANN plus pg_textsearch from day one

This targets larger disk-resident vector estates and may reduce HNSW memory.
It also introduces the least mature component before a measured need exists.
Its optimized label filter is constrained to `smallint[]`; UUID deployment,
entity and temporal filters still require schema/query planning and benchmarks.

### E. ParadeDB `pg_search` plus pgvector

ParadeDB offers a broader Tantivy-backed search engine inside PostgreSQL. That
surface is intentionally larger than the required BM25 top-k channel, and its
AGPL/commercial licensing is a material distribution decision. It is not the
YAGNI baseline.

## 5. Hybrid and authority semantics

Semantic distance and BM25 scores are not comparable. The stable contract is
two independently bounded top-k scans over the same logical target followed by
rank-based reciprocal-rank fusion (RRF):

`score(id) = semantic_weight/(k + semantic_rank) + lexical_weight/(k + lexical_rank)`.

The default remains rank fusion, not raw-score addition. A cross-encoder remains
an explicit optional reranker.

Moving P1 into PostgreSQL does **not** make the projection authoritative.
Embeddings are still produced asynchronously and can lag. It does allow the
ANN/BM25 scan and the authority join to execute inside one PostgreSQL statement
and snapshot. Invalidated or wrong-generation rows are therefore ineligible
without a separate network/storage confirmation loop. Projection lag can still
cost recall; it cannot create current-truth output when the authority join is
correct.

## 6. Maintenance comparison

PostgreSQL indexes are updated as part of normal `INSERT`, `UPDATE` and
`DELETE` processing. Autovacuum performs ordinary dead-row cleanup.

- HNSW requires no retraining. Evidence-driven `REINDEX CONCURRENTLY` remains
  available for bloat, corruption, changed parameters or measured degradation.
- IVFFlat can require rebuilding when trained centroids become unsuitable and
  is therefore not the default.
- pg_textsearch automatically spills and compacts its index state; an optional
  force-merge may follow a bulk load.
- DiskANN implements PostgreSQL insert and vacuum paths; no separate indexing
  service or periodic retraining contract is documented.

This removes the need for the Lance-specific compact/retrain/ensure ticker. It
does not remove ordinary database maintenance or the embedding/backfill worker.

## 7. Recommendation

Adopt **C** as the initial binding architecture:

- PostgreSQL remains the only authoritative store and gains private,
  rebuildable P1 search tables.
- `pgvector` is required; HNSW is the initial ANN index.
- `pg_textsearch` is required for the claims/chunks BM25 channels.
- built-in PostgreSQL FTS is not relabelled as BM25.
- pgvectorscale is an accepted index-level scale option, but becomes the
  default only after the adoption trigger in
  `design/proposals/pgvectorscale_default_index.md` is met.
- Lance is removed rather than retained as a dual-write compatibility path.

The decision is based on eliminating a consistency and operations boundary,
not SQL aesthetics. There are no library users whose compatibility requires a
dual-write/shadow-read period. The implementation can rebuild the derived P1
estate offline, prove parity, switch once and delete the Lance path.

## 8. Proof required before implementation is called complete

1. Frozen exact-search oracles for every semantic target and frozen lexical
   fixtures for claims/chunks.
2. Filtered recall@k and p95 for deployment, generation, entity and temporal
   filters at representative row counts.
3. Current Lance versus PostgreSQL hybrid parity using identical candidate
   bounds and RRF.
4. Bulk-ingest throughput, WAL/storage growth, autovacuum behavior and restart
   recovery.
5. Hard-forget, generation cutover, backup/restore and extension-upgrade drills.
6. LoCoMo or another paid model benchmark only when explicitly started by an
   operator; it is not an automatic design or CI gate.
