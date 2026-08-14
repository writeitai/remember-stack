# Design: PostgreSQL-native P1 search projection

**Status:** binding architecture, implementation pending (D94)
**Date:** 2026-08-14
**Decision:** [D94](../../decisions.md#d94-p1-search-is-a-postgresql-projection-not-a-lancedb-store)
**Analysis:**
[postgres_p1_search_projection_analysis.md](../analysis/postgres_p1_search_projection_analysis.md)
**Scale proposal:**
[pgvectorscale_default_index.md](../../design/proposals/pgvectorscale_default_index.md)
**Supersedes:** D8's Lance placement, D93 and
[p1_lance_maintenance_design.md](p1_lance_maintenance_design.md)
**Amends:** D9, D48 and D61 as recorded in D94

## 1. Problem and decision

P1 is the rebuildable search projection over chunks, claims, fact labels,
entity profiles and future media segments. It must provide independent
semantic and lexical candidate channels, generation-safe filtering, optional
entity and temporal constraints, and stable IDs that can be hydrated to
provenance-bearing records.

P1 SHALL live inside the authoritative PostgreSQL database as private derived
tables and indexes:

- `pgvector` supplies vector columns, distance operators and the initial HNSW
  indexes;
- `pg_textsearch` supplies the BM25 lexical indexes;
- PostgreSQL B-tree/GIN indexes supply scalar and entity filters;
- RRF combines independently ranked semantic and lexical candidate sets;
- `pgvectorscale` StreamingDiskANN is the accepted scale replacement for HNSW
  only after its recorded trigger is met.

LanceDB is not retained as a fallback, compatibility store or dual-write
target. PostgreSQL authority tables remain distinct from P1 projection tables:
co-location removes a physical consistency boundary; it does not make an
embedding or BM25 row source testimony or adjudicated truth.

## 2. Scope and non-goals

This design decides:

- the P1 storage and indexing engines;
- the derived-row, authority-join and generation contracts;
- vector, lexical and hybrid execution;
- maintenance, backup, failure and migration behavior;
- the narrow gate for adopting DiskANN.

This design does not:

- change the claims/facts distinction or either temporal axis;
- move P2 graph snapshots or P3/object bytes into PostgreSQL;
- generate embeddings inside PostgreSQL;
- add an LLM to the query path;
- add a search-engine abstraction or support multiple P1 stores;
- make BM25 available for a target that has no public lexical contract;
- implement public media-segment search before D65's media row contract lands;
- add RLS. D68's authenticated deployment binding and explicit predicates
  remain the security perimeter.

## 3. Physical topology

```text
authoritative Postgres tables / immutable artifact handles
                         │
             embedding + projection workers
                         │
                         ▼
       private PostgreSQL P1 search tables
       ├── vector column + HNSW (pgvector)
       ├── content column + BM25 (pg_textsearch)
       └── scalar/entity indexes (PostgreSQL)
                         │
       one statement: rank + authority join/filter
                         │
             optional RRF / reranking
                         │
                    QueryResult
```

The API process still calls the configured embedding provider for a semantic
query string. It passes the resulting vector to PostgreSQL and runs one
bounded statement. This thin query-embedding step is not a storage bridge and
does not call an LLM.

Postgres is now the single hot-path database for P1. P2 remains an embedded
snapshot and source bodies remain in the object/artifact estate.

## 4. Required extensions and supported PostgreSQL

The reference and self-host images SHALL run PostgreSQL 18, continuously
patched to the current 18.x minor, and pin tested native builds of:

- `vector` (required);
- `pg_textsearch` (required and listed in `shared_preload_libraries`);
- `vectorscale` only when the DiskANN proposal has been adopted for that
  release/deployment profile.

Migrations verify extension availability before creating projection objects.
A missing or wrong major-version extension fails deployment readiness; the
runtime never silently falls back to exact scans, built-in FTS or Lance.
Extension versions are build inputs and appear in readiness/diagnostic output.

## 5. Projection-table contract

P1 uses target-specific private tables rather than one polymorphic vector
heap. The initial logical tables are:

| Logical table | Search text | Semantic input | Public lexical channel |
| --- | --- | --- | --- |
| `p1_chunk_search` | source chunk text | D80 chunk embedding input | yes |
| `p1_claim_search` | immutable claim statement | D80 claim embedding input | yes |
| `p1_fact_search` | canonical relation/observation fact label | fact-label input | not until separately admitted |
| `p1_entity_search` | canonical profile/description | entity-profile input | no |
| `p1_media_search` | representation caption/locator text | modality-specific input | D65 implementation only |

Physical names may include an embedding-family/dimension suffix, but callers
never construct those names. A physical ANN index contains one fixed vector
type, dimension and distance operator. A dimension or incompatible embedding
family change creates a new physical table/index and uses the generation
cutover protocol; it never mixes incomparable vectors in one index.

Every text-target row carries at least:

- `deployment_id` and stable target ID;
- embedding family, model/version, dimension and projection generation;
- `embedding_input_policy`, `embedding_text_hash` and the vector;
- canonical lexical/search text where that target has a lexical channel;
- source version/representation coordinates needed to reject mixed lineage;
- derived `entity_ids` used by the optional entity-scoped search contract;
- target-specific eligibility scalars useful for query planning.

Projection scalars are performance data, not authority. The query statement
MUST join the relevant invariant-bearing PostgreSQL authority view or an
equivalent private subquery and reapply deployment, generation, validity,
currency and lineage predicates before a row can leave P1.

Projection tables are excluded from `memory_v1`, open SQL and raw primitives.
Callers reach them only through the typed semantic/lexical operations and
their saved-query compositions.

## 6. Writes, visibility and generation cutover

Embedding generation remains asynchronous:

1. An authority row or representation becomes eligible.
2. The existing work ledger schedules the target's embedding work.
3. The worker computes the contracted embedding input and external embedding.
4. One PostgreSQL transaction upserts the P1 row and its complete coordinates.
5. Readiness counts only rows whose coordinates match the intended generation.

The source row and its embedding cannot be created atomically across an
external provider call. That lag is explicit and may reduce recall. Once the
embedding is available, its P1 upsert is ordinary PostgreSQL DML and its index
entry is maintained automatically.

Invalidation, deletion and hard-forget do not trust a delayed projection
mutation for correctness: the same-statement authority join makes an
ineligible target invisible immediately. The projection row is then deleted
by the normal lifecycle/hard-forget transaction or repair path and reclaimed
by vacuum.

A generation switch is:

`build rows -> build/verify indexes -> exact/parity checks -> atomic active-generation switch -> retire old rows`.

Mixed-generation results are forbidden. A query pins one complete generation
for every invoked target/channel and fails `generation_unavailable` rather
than falling forward.

## 7. Vector search

The initial ANN index is pgvector HNSW with the distance operator contracted by
the configured embedding model. HNSW is selected because it accepts
incremental rows without an IVF training/retraining lifecycle.

The semantic operation:

1. validates the authenticated deployment and target generation;
2. embeds the query once per syntactic invocation;
3. runs a bounded vector top-k statement with all required filters and the
   authority join;
4. returns stable IDs, one-based rank, distance/score metadata and the
   PostgreSQL statement timestamp;
5. hydrates deeper provenance only when the caller requests it.

ANN is approximate. The evaluation suite compares it with an exact scan under
the same authority predicates. Query-time HNSW parameters use transaction-local
settings and are observable; request input cannot set arbitrary database GUCs.

IVFFlat is not a supported default because its trained lists recreate a
retraining policy. DiskANN may replace HNSW under §13 without changing the
semantic operation or distance SQL.

## 8. Lexical search and BM25

Claims and chunks use a `pg_textsearch` BM25 index on the same canonical text
stored beside their embedding. Built-in `ts_rank`/`ts_rank_cd` MUST NOT be
reported as BM25.

A lexical operation:

1. executes a bounded `ORDER BY content <@> query LIMIT n` candidate scan;
2. applies the same authority, deployment, generation, entity and temporal
   predicates as its semantic sibling;
3. returns the raw BM25 score and one-based lexical rank;
4. never compares that raw score directly with a vector distance.

The analyzer/text configuration is a versioned input. Changing it creates a
new projection generation and BM25 index. Phrase search is not part of the
initial BM25 contract because pg_textsearch does not store positions; any
future phrase channel is separately named and tested rather than emulated
silently.

## 9. Hybrid search and reranking

Hybrid search means two independent candidate lists over the same target:

- semantic top-k from pgvector HNSW or DiskANN;
- lexical top-k from pg_textsearch BM25.

The two queries may run as CTEs in one statement or concurrently through the
same PostgreSQL pool. The implementation chooses from measured query plans,
but both lists must share one pinned generation and equivalent authority
filters.

The default fusion is RRF over stable IDs. Raw vector and BM25 scores are never
added. Channel weights and candidate bounds are named recipe inputs with safe
caps. The existing optional cross-encoder remains an explicit final reranker;
it is not required for P1 correctness and does not become implicit.

## 10. D48 after co-location

D48 remains the correctness rule, with a simpler P1 mechanism:

- P1 search rows nominate, while authority views dispose.
- For P1, nomination and confirmation execute inside one PostgreSQL statement
  and MVCC snapshot. There is no Lance call followed by a second PostgreSQL
  confirmation statement and no cross-store dropped-candidate window.
- P2 remains a snapshot and still requires the existing confirmation/unit-drop
  behavior when live confirmation is requested or required.
- Hydration still progressively resolves evidence, source handles and bytes;
  it is no longer needed merely to discover whether a P1 ID is live.

`dropped_by_hydration` remains meaningful for P2 and deep source hydration.
P1 authority-filter rejection is ordinary candidate filtering and is exposed
through channel candidate/eligible counters rather than a fake hydration drop.

## 11. Maintenance and operations

Ordinary index maintenance belongs to PostgreSQL:

- inserts/updates maintain HNSW and BM25 entries;
- autovacuum cleans dead tuples and updates planner statistics;
- pg_textsearch automatically spills/compacts index segments;
- initial bulk loads create indexes after loading where that is faster;
- an optional `bm25_force_merge` may run once after a large batch build;
- `REINDEX CONCURRENTLY` is operator-controlled recovery/tuning for measured
  bloat, corruption, changed construction parameters or degraded performance.

There is no P1 compact/retrain/ensure ticker, table-stat ledger, process lock or
request-path rebuild. Index existence and extension versions are migration and
readiness checks. Autovacuum lag, index size, dead tuples, statement latency and
recall samples are observed with standard PostgreSQL telemetry.

Changing an embedding model still requires embedding/backfill work. An index
cannot create its own vectors.

## 12. Backup, recovery and failure behavior

P1 rows and index definitions participate in the PostgreSQL backup/PITR
boundary. P1 remains derived, so restore correctness is:

1. restore authoritative PostgreSQL state and immutable artifact handles;
2. verify the pinned extensions;
3. validate P1 generation/hash coverage;
4. rebuild missing/incompatible P1 rows or indexes;
5. advertise search readiness only after the target generation passes.

No Lance directory, version history or separate P1 object backup remains.

| Failure | Required behavior |
| --- | --- |
| PostgreSQL unavailable | P1 and authority reads fail `pg_unavailable`; no stale independent P1 serving |
| required extension missing/wrong | startup/readiness fails; no silent engine fallback |
| P1 row missing | recall gap is measured/repaired; authority data remains intact |
| index missing/corrupt | take affected channel unready; exact scan is diagnostic only; rebuild/reindex |
| BM25 spill/compaction latency | bounded statement/write telemetry exposes it; tune or rebuild outside request path |
| HNSW resource envelope exceeded | evaluate the accepted DiskANN proposal; do not add an application cache/store |
| generation mismatch | fail `generation_unavailable`; never mix or fall forward |

Co-location increases the resource/blast-radius coupling between transactional
PostgreSQL work and search. Connection pools, statement timeouts, work memory,
index-build resources and autovacuum are therefore explicit capacity inputs.
This is accepted in exchange for removing the independent consistency boundary.

## 13. Pgvectorscale adoption gate

Pgvectorscale is compatible with the binding P1 query/schema contract but is
not the initial required index. Promote StreamingDiskANN only when the accepted
proposal's measured trigger is met and a representative comparison proves:

- filtered recall is no worse than one percentage point below the exact/HNSW
  baseline at contracted k;
- the resource problem that triggered evaluation is materially improved;
- ingestion, vacuum, restart, backup and hard-forget behavior pass;
- UUID/entity/temporal filters work within the latency envelope despite the
  extension's specialized `smallint[]` label limitation;
- the public semantic, fusion and generation contracts do not change.

The switch is an index rebuild, not a new data store or public API version.

## 14. Security

- No RLS is introduced.
- Every search statement obtains `deployment_id` from the authenticated,
  deployment-bound executor and applies it explicitly.
- Projection tables are private and have no caller grants.
- Native extensions are supply-chain-sensitive code inside PostgreSQL: builds
  are pinned, scanned and upgraded through reviewed images/migrations.
- Query text, vectors and filters remain parameterized; user input cannot name
  indexes, tables, extension functions or GUCs.
- Entity and temporal filters are repeated at the authority join even when a
  projection scalar/index also applies them for speed.

## 15. Implementation sequence and exit gate

1. Add the PostgreSQL image/extensions and private schema migrations.
2. Implement target-specific projection writes and exact-search fixtures.
3. Backfill claims/chunks/facts/entities from authoritative rows/artifacts.
4. Build HNSW and BM25 indexes; run filtered exact-versus-ANN and lexical
   fixtures.
5. Implement same-statement authority joins and hybrid RRF.
6. Run the representative parity/scale/recovery gates from the analysis.
7. Switch P1 reads once; remove Lance dependencies, adapters, ticker, locks,
   backup handling and data.
8. Update OSS docs/images and exercise a clean install plus restore.

There is no compatibility dual-write, 30-day shadow-read period or retained
Lance rollback estate. Before the read switch, rollback is the branch/commit;
after it, P1 is rebuilt from authority. The paid benchmark is never started
automatically.

Implementation is complete only when no active binding design, public docs,
composition path or shipped dependency presents Lance as part of current P1.
Historical analyses, reviews and implementation notes remain labelled history.
