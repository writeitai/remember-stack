# Analysis: a lean PostgreSQL-native P1

**Status:** non-binding analysis supporting D94
**Date:** 2026-08-14
**Repository baseline inspected:** `origin/main` at `cc8cb23e`
**Question:** after removing LanceDB, which search data belongs on existing
PostgreSQL rows and which data genuinely needs a derived table?

## 1. Conclusion

Use PostgreSQL 18 with pgvector HNSW and pg_textsearch BM25, but do not recreate
LanceDB as a family of duplicated PostgreSQL tables.

The lean physical model is:

- index text and store one active embedding on the existing claim, relation,
  observation and entity row when that row is already the natural search grain;
- create one private `chunk_search` table because chunk body text does not exist
  in PostgreSQL and the authoritative `chunks` table is a historical coordinate
  ledger rather than the current search corpus;
- let future media tables carry their own text/vector columns when the media
  implementation defines their natural search grain;
- join normalized authority tables for deployment, entity, lineage and temporal
  filtering instead of copying arrays and eligibility state into search rows.

P1 remains a logical projection plane. Physically it is ordinary indexes and
derived columns on natural records plus the one unavoidable chunk search table,
not a parallel copy of the data model.

## 2. Current local shape

### Chunk bodies

The `chunks` table stores `chunk_id`, document/version/representation/section
coordinates, block and character ranges, hashes and policy stamps. It does not
store the body. The exact converted document is the immutable `document.md`
artifact; E1 obtains a chunk by slicing
`document_md[char_start:char_end]`.

The D80 renderer normalizes line endings, collapses whitespace and trims the
slice. The current Lance P1 row stores that normalized body as `text` and stores
the vector produced from the approved embedding input. Therefore removing
Lance while retaining chunk BM25 requires one searchable text copy somewhere
inside PostgreSQL. A separate generic P1 layer does not avoid those bytes.

### Other targets

- claims already store the standalone `claim_text`;
- relations already store `fact_label`;
- observations already store `statement`;
- entities already store canonical identity/profile fields.

Copying those texts, deployment IDs, entity IDs and temporal scalars into
parallel search rows would add synchronization and joins without creating a new
search grain. Their derived embeddings can live beside those records and remain
excluded from public authority views.

### Existing Lance cost

The inspected baseline has 2,496 lines of direct Lance adapter/maintenance
production code before query bridges, backup and recovery branches are counted.
It carries a second write, delete, hard-forget, backup, generation and index
maintenance lifecycle. D93 exists because ordinary Lance writes create
fragments/unindexed tails that require application-owned maintenance.

The architectural gain from PostgreSQL is eliminating that physical boundary.
It does not require duplicating the same logical shape inside PostgreSQL.

## 3. PostgreSQL search capabilities

External capability sources were checked on 2026-08-14.

Pgvector provides PostgreSQL vector columns, distance operators, exact search,
and HNSW/IVFFlat indexes. HNSW has no training step, accepts incremental writes,
and can be combined with ordinary PostgreSQL filters. Its own documentation
describes hybrid composition with PostgreSQL text search and RRF or reranking.
Source: <https://github.com/pgvector/pgvector>.

The dimension contract cannot remain implicit. Pgvector HNSW indexes support
`vector` through 2,000 dimensions and `halfvec` through 4,000. The current
Qwen3-Embedding-8B model can emit up to 4,096 dimensions but supports
user-selected output dimensions from 32 through 4,096; OpenRouter's embedding
request exposes a `dimensions` parameter. The reference profile therefore pins
Qwen output and PostgreSQL `vector` columns to **1,536 dimensions**. This keeps
ordinary float32 cosine HNSW, avoids half-precision/expression-index machinery,
and closes a real incompatibility without a benchmark program. A later dimension
change follows the explicit maintenance contract.
Sources: <https://huggingface.co/Qwen/Qwen3-Embedding-8B> and
<https://openrouter.ai/docs/api/api-reference/embeddings/create-embeddings>.

Pg_textsearch provides a PostgreSQL BM25 index and top-k ordering operator, with
WAL/replication/vacuum integration and PostgreSQL 18 support. It is selected
instead of relabeling PostgreSQL's `ts_rank`/`ts_rank_cd`, whose official
documentation says the built-in rankers use no global corpus information.
Sources: <https://github.com/timescale/pg_textsearch> and
<https://www.postgresql.org/docs/current/textsearch-controls.html>.

These two required extensions cover the current contract. Pgvectorscale is an
unchosen future DiskANN proposal, not a baseline dependency or a reason to add
an abstraction now. Source: <https://github.com/timescale/pgvectorscale>.

## 4. External implementations

Sources were inspected on 2026-08-14 at the pinned commits below.

### Hindsight

Hindsight's default PostgreSQL memory store keeps the searchable unit directly
in `memory_units`: `text`, `embedding`, context, temporal fields and fact type.
Its pg_textsearch index is built over `memory_units.text`.

Hindsight can also store `documents.original_text` and `chunks.chunk_text` in
PostgreSQL, and does so by default. Those source bodies are explicitly described
as cold and never searched; deployments may disable their persistence or route
them to a dedicated document store. Hindsight therefore co-locates the text and
vector for the grain it actually searches, without requiring raw source chunks
to be that grain.

Sources:

- <https://github.com/vectorize-io/hindsight/blob/2e8c221c54b1dd2f6cc003f63accf3a01a077332/hindsight-api-slim/hindsight_api/alembic/versions/5a366d414dce_initial_schema.py>
- <https://github.com/vectorize-io/hindsight/blob/2e8c221c54b1dd2f6cc003f63accf3a01a077332/hindsight-api-slim/hindsight_api/engine/retain/chunk_storage.py>
- <https://github.com/vectorize-io/hindsight/blob/2e8c221c54b1dd2f6cc003f63accf3a01a077332/hindsight-api-slim/hindsight_api/engine/memories/base.py>

### Letta

Letta's natural retrieval grain is a passage. Its PostgreSQL
`source_passages` and `archival_passages` rows contain both `text` and
`embedding`, plus the passage's native archive/source metadata. It can
additionally write an external vector backend, but PostgreSQL is written first.
That is a direct in-row design because the passage is already the stored memory
record; Letta does not have RememberStack's separate immutable document,
coordinate ledger, testimony and adjudicated-fact grains.

Sources:

- <https://github.com/letta-ai/letta/blob/56ba9c2d9720ba109850fd39fb3c77c2a571e493/letta/orm/passage.py>
- <https://github.com/letta-ai/letta/blob/56ba9c2d9720ba109850fd39fb3c77c2a571e493/letta/services/passage_manager.py>

These implementations support one principle rather than one universal schema:
put searchable text and its vector on the natural search grain. RememberStack
needs a chunk sidecar only because its natural chunk ledger deliberately omits
body text and retains historical coordinates.

## 5. Alternatives

### A. Parallel P1 table for every target

This gives every target the same physical shape and allows multiple permanent
embedding generations. It also copies already-authoritative text and filters,
adds joins to every query, and recreates projection synchronization inside the
same database. Uniformity alone does not justify it.

### B. Put all chunk search data on `chunks`

This is initially the fewest tables. It makes the monthly partitioned historical
ledger carry body text, vectors and search indexes for obsolete versions. Search
eligibility is narrower than ledger retention, so the ANN/BM25 corpus would grow
with history rather than with the current searchable set.

### C. Search only extracted claims/facts; keep chunks object-only

This mirrors Hindsight more closely and saves the PostgreSQL chunk-text copy. It
removes lexical retrieval over source testimony and forces every returned chunk
body through object hydration. Exact names, identifiers, quotations and material
missed by extraction would lose their independent lexical channel. That conflicts
with RememberStack's explicit testimony surface.

### D. Lean hybrid — chosen

Use in-row indexes where the text already exists and a single `chunk_search`
table where it does not. This retains independent source and claim retrieval,
keeps the historical chunk ledger small, and minimizes duplicated state.

## 6. Chosen physical contract

`chunk_search` contains one row per currently admitted chunk:

```text
deployment_id
chunk_id
search_text
embedding
embedding_model
embedding_input_policy_version
embedding_text_hash
```

Its key is `(deployment_id, chunk_id)`. `search_text` is the deterministic D80
normalized chunk body: normalize line endings, collapse whitespace, trim. It is
not an LLM rewrite, summary, claim, neighboring text or generated location
header. The exact formatted source remains the object-store `document.md`
slice. The location header may affect the vector input but remains a separately
labeled field on the chunk authority path and never enters chunk BM25.

The table deliberately omits copied `entity_ids`, document status, validity
windows, lineage currency, source metadata and other eligibility scalars.
Queries join the existing normalized relations and invariant-bearing authority
views in the same PostgreSQL statement. If an entity-scoped candidate set is
large, PostgreSQL ranks that joined set; the design does not precompute UUID
arrays or add an application ID handoff merely to anticipate a planner problem.

Claims use BM25 on `claims.claim_text` and an HNSW index over a derived embedding
column on `claims`. Relations, observations and entities similarly keep their
one active embedding on the natural row. The public `memory_v1` views do not
expose derived vector columns.

This requires one D23 amendment: `claims` is no longer monthly partitioned.
Pg_textsearch supports partitioned tables but computes partition-local BM25
statistics, while the current-testimony claim corpus spans ingestion months.
Those scores are not one globally comparable claim ranking. Keeping claims in
one table allows global partial BM25/HNSW indexes over
`is_current_testimony = true` without creating a duplicated `claim_search`
table. At the designed roughly 50-million-row scale, this is the leaner
PostgreSQL shape; the remaining append-only ledgers and evidence joins keep
their D23 partitions.

Media is not forced into `chunk_search`. A future media search implementation
places text/vector fields on its accepted segment/representation rows because
those rows, not text chunks, are its natural grain.

## 7. Generations without permanent duplication

The initial implementation stores one active embedding per natural record and
one row per chunk in `chunk_search`. It does not retain parallel permanent
generations or add a generic generation registry.

One small `p1_search_channels` control row per target/channel records only the
current configured model/dimension/policy (or BM25 text configuration), readiness,
and update time. This gives rebuild/publication and the query path a durable,
constant-time readiness handshake without retaining historical generations or
duplicating search records.

An incompatible embedding-model, vector-dimension or embedding-input-policy
change is an explicit maintenance operation:

1. mark the affected semantic channel unready;
2. rebuild its embeddings/index using the new configuration;
3. verify structural completion and hashes;
4. publish the new configuration and mark the channel ready;
5. discard temporary rebuild state.

Queries never mix vector spaces because the affected channel does not serve
during the rebuild. This accepts temporary search unavailability in exchange
for a much smaller permanent schema. It does not affect authoritative SQL,
facts, graph snapshots or object artifacts.

Compatible retries and unchanged-input reuse still key on the input hash,
policy version and embedder identity. Those fields prove what produced the one
active vector; they do not require keeping every prior vector.

## 8. Storage and operational consequences

The chunk text is a real PostgreSQL cost: table bytes, BM25 index bytes, WAL,
replication and physical backups. It is not a new system-wide corpus copy,
because Lance already stores the same normalized chunk body and is deleted.

For intuition, a roughly 2 KiB chunk body is smaller than a 1,536-dimensional
float32 vector (about 6 KiB) or a 3,072-dimensional vector (about 12 KiB), before
ANN index overhead. Actual values depend on corpus and embedding model; no
benchmark is required to accept the architecture.

Growth is bounded structurally:

- `chunk_search` contains only the admitted current search set;
- one current vector exists per target row; historical claim rows retain their
  current-model vector so bounded historical testimony retrieval can rank them,
  but only current testimony enters the partial default HNSW/BM25 indexes;
- obsolete, superseded and forgotten chunk search rows are deleted;
- entity and temporal metadata are not copied;
- immutable document bytes remain in object storage rather than being copied
  wholesale into authority tables.

PostgreSQL now carries search CPU, WAL and index maintenance. In return there
is one transactional deletion/backup boundary, one query planner and no
application-owned compact/retrain ticker.

## 9. Implementation consequence

There are no compatibility consumers and no requirement for shadow reads,
dual writes, a Lance/PostgreSQL parity benchmark or a prolonged migration.
Implementation should:

1. add PostgreSQL 18, pgvector and pg_textsearch schema support;
2. add `chunk_search` and the in-row derived embedding columns/indexes;
3. redirect current workers and queries to PostgreSQL;
4. rebuild the disposable search state from authority and object artifacts;
5. switch once after deterministic completeness and functional contract tests;
6. delete Lance code, dependencies, configuration, maintenance state, backup
   handling, tests and runtime documentation.

Historical decisions, analyses and reviews remain clearly historical because
they explain the removed architecture. They are not runtime compatibility or
an implementation surface.
