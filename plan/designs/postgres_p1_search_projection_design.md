# Design: PostgreSQL-native P1 search

**Status:** binding architecture, implemented with the D94 cutover
**Date:** 2026-08-14
**Decision:** [D94](../../decisions.md#d94-p1-search-is-postgresql-native)
**Analysis:**
[postgres_p1_search_projection_analysis.md](../analysis/postgres_p1_search_projection_analysis.md)
**Supersedes:** D8's LanceDB placement, D93, and the removed D93 maintenance design
**Amends:** D9, D23, D37, D48, D61, D63, and D80 as recorded in D94

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

P1 SHALL be implemented in PostgreSQL 18 using:

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
  into search rows. Search joins the normalized authority tables/views in the
  same PostgreSQL statement.

LanceDB is removed completely from the active system. It is not a fallback,
compatibility store, optional backend, or migration source after cutover.

## 3. Scope and non-goals

This design decides the P1 storage engines, physical placement, query
confirmation, rebuild behavior, and LanceDB removal boundary.

It does not:

- change the claims/facts distinction or either temporal axis;
- move P2 graph snapshots or P3 source artifacts into PostgreSQL;
- add a generic search-engine abstraction;
- add in-database embedding generation or an LLM to the query path;
- add lexical fact/entity/media APIs that are not already admitted;
- add copied filter columns merely to avoid ordinary indexed joins;
- adopt pgvectorscale or a PostgreSQL graph extension;
- run a storage-engine, scale, or retrieval-quality benchmark; or
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

Reference and self-host images SHALL run PostgreSQL 18, patched to the current
18.x minor, with pinned and tested builds of:

- `vector`; and
- `pg_textsearch`, including its required preload configuration.

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

A semantic query is embedded once per syntactic invocation by the configured
embedding provider. PostgreSQL then executes a bounded ranked statement that:

1. applies `deployment_id` before candidate rows can escape;
2. searches the target's HNSW index;
3. joins the target's invariant-bearing authority view/tables;
4. applies requested entity, temporal, lineage, eligibility, and current/history
   predicates using their normalized indexed columns; and
5. returns only authority-confirmed stable IDs and retrieval metadata.

BM25 follows the same contract against the admitted text column. Hybrid search
runs semantic and BM25 candidate subqueries independently and fuses their
one-based ranks by RRF. Raw vector distance and BM25 scores are never added or
treated as calibrated equivalents.

The current-testimony claim BM25 index is partial. Queries name it explicitly
with pg_textsearch `to_bm25query()` while repeating
`is_current_testimony = true`; implicit index discovery is not used for that
path. Chunk BM25 uses the full `chunk_search` index. Both use the binding
`simple` text configuration from §4.1.

Optional entity filtering is a normal SQL join through the canonical
entity-target association. The implementation SHALL NOT first materialize tens
of thousands of candidate IDs in application memory or send a giant `IN` list
to the vector search. PostgreSQL plans the filter and ranked search together.

D48 confirmation therefore remains mandatory in meaning, but it is no longer a
remote “search Lance, then ask PostgreSQL” hop. Ranking and authority
confirmation occur in one PostgreSQL statement and one transaction snapshot.

## 9. Index and planner contract

HNSW and BM25 indexes are maintained by ordinary PostgreSQL DML. Autovacuum and
standard PostgreSQL telemetry own routine cleanup; there is no P1 compaction
ticker, fragment ledger, or application-managed index tail.

Each indexed vector column has one fixed dimension and compatible distance
operator. Deployment-bound filters and the authority join need conventional
indexes on their existing join/filter keys. The implementation may add a
specific missing B-tree index demonstrated by a query plan, but this design does
not pre-emptively denormalize data or create a generic indexing framework.

ANN remains approximate. Functional tests prove contracts and structural
completeness, not a benchmark score. Query-time search parameters are bounded,
transaction-local, and observable; public requests cannot set arbitrary GUCs.

Filtered HNSW uses pgvector's transaction-local iterative scan in strict-order
mode (`hnsw.iterative_scan = strict_order`) with an operator-owned
`hnsw.max_scan_tuples` bound. The authority/entity/temporal predicates precede
the final result limit, so PostgreSQL continues the ANN scan until it finds the
requested eligible rows or reaches that bound; it does not take an unfiltered
top-k and then silently return the survivors. A selective plan may use an exact
filtered scan instead. If the bounded iterative scan cannot fill `k`, the
existing candidate/eligible/drop counters and truncation disclosure report the
shortfall. The library pins a pgvector release that supports iterative scans.

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
`memory_v1`, open SQL, Cypher, and raw-primitives schemas. RLS remains forbidden
because it would add a second implicit authorization system and undermine the
explicit, reviewable deployment-predicate contract.

Public operations retain bounded `k`, candidate depth, query length, entity
count, timeout, and output budgets. Query strings, source text, vectors, and
credentials are not emitted to logs. Explain plans and index diagnostics remain
operator-only.

## 13. Implementation and deletion plan

Implementation is a direct replacement on latest `main`:

1. build and pin PostgreSQL 18, pgvector, and pg_textsearch in the reference and
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
