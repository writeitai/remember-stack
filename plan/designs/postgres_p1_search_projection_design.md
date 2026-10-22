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

- `vector` (0.8 or later, for iterative index scans; the image pins 0.8.6); and
- `pg_textsearch`, including its required preload configuration, at **1.5.1
  or later** (D142). 1.5.0 added upstream PostgreSQL 19 beta support, so the
  D98 compatibility patch is retired; it also made post-filtered BM25 index
  scans grow their internal batch until the `LIMIT` is filled or matches are
  exhausted and fixed silent truncation in deep scans. Releases before 1.4.0
  return the index's top-k *before* filtering, which §8 must never rely on.
  Upstream describes PostgreSQL 19 support as best-effort with non-blocking
  CI, so compatibility on the pinned PostgreSQL 19 image is a **local gate**:
  the image build runs the extension's upstream SQL tests and the §8.3
  cursor/scoring-generation tests on that exact image.
  1.5.1 scores unspilled (memtable) rows with raw document length while
  segments and the standalone operator use fieldnorm-quantized length
  (§8.3). The image carries **no local patch** for this: §8.3's stop margin
  is proven for both scorers (a post-approval design change, §8.3), and the
  §9 scorer-bound gate forces re-verification whenever the pinned revision
  changes.

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
authority confirmation of every nomination in the operation's ranked-read
context (§8.2–§8.3). Cleanup then removes or
clears disposable search state through the normal lifecycle transaction or
repair worker.

`p1_search_channels` is the small durable current-state authority for query
readiness. It has exactly one row per deployment, target, and channel, carrying
the current model/dimension/policy (or BM25 text configuration), `ready`, and
`updated_at`. It is not a generation history and contains no search records.
Ranked searches require the matching ready row; ordinary asynchronous row lag
does not flip global readiness, while an incompatible maintenance operation
does.

No request path writes, repairs, builds indexes, or calls an LLM.

## 8. Query and filtering contract

### 8.1 What a ranked search must return

A ranked search returns, for one target and channel, the best **eligible**
rows up to `k`, ordered by score, then stable item ID, together with an
honest account of how it ended (§8.6). "Eligible" means every authority rule
holds: live lineage/version/representation (D48), the D140 time scope and
occurrence rules, the requested entity scope, documents filter, fact time and
D140 fact evidence gate, and any public SRF filter. Eligibility constrains the
candidates *before* the relevance cut — a global top-k followed by filtering
is forbidden (D87). Any bound that stops a search early is disclosed; it never
looks like "no more results" (D49).

**Lexical results are positive matches only (D142 behaviour change).** A BM25
result is a row that matches at least one analyzed query term with a positive
BM25 score. A query whose terms match nothing — including a query that
analyzes to no terms — returns no lexical rows and ends `exhausted`; a query
with fewer than `k` matches returns those matches and no zero-score tail.
Before D142 the lexical statements ordered *all* eligible rows by the
standalone operator, so a non-matching query could return `k` arbitrary
zero-score rows and RRF counted them as lexical evidence. That tail carried no
keyword signal; removing it changes hybrid fusion only where a zero-score row
used to take a rank. The change is measured by the §9 quality gate. The same
rule holds on every lexical path: index nomination, exact scoring of small
entity tiers (§8.4), the open query space SRFs, and the D140 `scope_pending`
probe (which already required a term match).

A semantic query is embedded once per syntactic invocation by the configured
embedding provider. Hybrid search ranks the semantic and BM25 lists
independently and fuses their one-based ranks by RRF; raw vector distances
and BM25 scores are never added or treated as calibrated equivalents.

### 8.2 The ranked-read context: one snapshot, pinned instants

Every ranked operation runs inside one **ranked-read context**: a single
`READ ONLY`, `REPEATABLE READ` PostgreSQL transaction on one P1 read
connection, plus the operation's pinned evaluation instant `evaluated_at`
and, where the operation has one, its belief instant `believed_at`. These
instants are computed once by the caller and passed to every statement as
parameters; no statement in the context reads `now()`, `statement_timestamp()`
or `clock_timestamp()` for eligibility, because a stable MVCC snapshot does
not freeze statement clocks.

The context is the unit of consistency. Everything one operation reads from P1
and from authority runs in it: channel readiness checks, every nomination
cursor and fetch, every confirmation query, coverage computation, both fact
branches, the D140 `scope_pending` probe, and the final authority confirmation
of the returned rows. PostgreSQL takes the snapshot at the first statement;
read-only `REPEATABLE READ` transactions cannot fail with serialization
errors. A row deleted by a transaction that committed before the snapshot is
never returned; a deletion committed while the operation runs does not change
what this operation sees, and is visible to the next operation.

**Who owns the context.**

- *Engine operations* (search, the context operations, `search_documents`):
  the query engine opens one context per operation through the P1 port and
  passes it to every port call. Channels run sequentially in that context by
  default. An implementation MAY run channels concurrently only on helper
  connections that import the context's snapshot (`pg_export_snapshot()` in
  the context, `SET TRANSACTION SNAPSHOT` as the first statement of each
  helper's `REPEATABLE READ` transaction) and use the same pinned instants.
- *Open query space* (the SQL sandbox): the executor's request transaction is
  already `READ ONLY, REPEATABLE READ` (§ executor). At its start the executor
  exports its snapshot; the P1 adapter opens its context by importing that
  snapshot, and uses the executor's `evaluated_at`. Nomination, continuation,
  confirmation and the executor's own D48 confirmation of the returned IDs
  therefore read one snapshot, and `pg_confirmed_at` reports that snapshot's
  pinned evaluation instant. If the import fails (the exporting transaction
  ended, or the import is refused), the invocation fails with
  `p1_unavailable`; it never falls back to an independent snapshot.

The D97 operation deadline and bounded read admission govern the context's
transaction.

### 8.3 Nominate, confirm, continue (D142)

Ranking and eligibility are separate statements inside the context, so that
the index is used and eligibility stays exact. For each target and channel:

1. **Open one nomination cursor.** A server-side cursor
   (`DECLARE … NO SCROLL CURSOR`) over a statement that reads only the search
   table, the deployment predicate, the attestation predicates
   (model/policy/non-null vector), any predicate naming a partial index
   (`is_current_testimony` for claims), and the channel's ordering expression:

   ```sql
   SELECT id, <score> FROM <search table>
   WHERE deployment_id = :d AND <attestation and partial-index predicates>
   ORDER BY <distance or BM25 expression>
   ```

   It has no joins, no authority views and no secondary sort keys. The plan is
   enforced and attested as §9 describes. One cursor is one executor: for
   BM25 it fixes **one scoring generation** (pg_textsearch captures corpus
   statistics and segment state per executor, not per transaction snapshot,
   so re-running a statement could rescore under newer statistics while
   authority rows stay repeatable). For HNSW it is one continuing iterative
   index scan.
2. **Fetch a batch** (`FETCH n`, starting with `n = 4·k`, doubling per fetch,
   capped by the remaining work budget). Batches are **disjoint**: each fetch
   continues the same ordered stream. Every fetched row keeps the score it was
   fetched with; nothing is rescored.
   **Filter pushdown (post-approval design change, review r1 P1-9).** A
   document-filtered chunk search first enumerates its eligible rows from
   the matching versions (at most 20,000, a measured starting point). Up to the
   exact-tier threshold they are scored exactly (§8.4); above it the
   enumerated set is pushed into the index scan as a membership filter
   (`chunk_id = ANY(:eligible)`, hashed by PostgreSQL), so the cursor
   streams eligible rows only, still in the index's order and one scoring
   generation, under the same stop rules; an HNSW exhaustion count is
   filtered alike. Above 20,000 eligible rows the plain loop runs. Claim
   searches with a document filter use the plain loop (their filter is per
   occurrence).
3. **Confirm the batch.** A confirmation query applies the target's complete
   eligibility predicates — the same predicate builders the adapter already
   uses for every filter, scope and gate — to each candidate of the batch. It
   is a primary-key probe per candidate: the batch is unnested and each ID is
   checked by a `LATERAL` existence subquery. (A set predicate
   `item_id = ANY(:batch_ids)` lets the planner start from the eligibility
   joins instead, so confirmation grows with the corpus; the scaling sweep
   measured 17 s for one 4,000-ID batch at 30k chunks, against 0.13 s for
   the same batch probed per candidate.) It should return, per rejected ID,
   the first failed rule class (authority, scope, entity, document, fact
   time, evidence gate) for the counters; the first implementation counts
   rejections without the class.
4. **Stop** under exactly one termination reason (§8.6):
   - `k_filled` — `k` rows are confirmed **and** the stream has moved past the
     `k`-th confirmed score (the next fetched score is strictly worse), so a
     run of equal scores is never cut in the middle and ties order by stable
     ID; the tie lookahead consumes fetches like any other batch;
   - `exhausted` — the stream ended and completeness is proven (§8.5);
   - `work_budget`, `deadline` or `ann_scan_bound` — work stopped early;
     disclosed as truncation.

**One canonical BM25 score.** Every lexical ranking — index nomination, the
exact scoring of small entity tiers (§8.4), the open query space SRFs, the
D140 `scope_pending` probe and every test oracle — uses one score: the
pg_textsearch BM25 function over the **fieldnorm-quantized** document length
(the one-byte length encoding segments store) and the scoring generation's
corpus statistics, as the **standalone `<@>` operator** computes it: each
per-term score in float4, the total accumulated in float8 in the analyzed
query's term order (pg_textsearch 1.5.1), and the float8 total is the
canonical score. Every path that ranks lexically evaluates that operator; the
index is used only to *nominate* in an order that is provably close to it
(below). Two rows tie only when their canonical scores are bitwise equal.

The canonical score is the standalone `<@>` operator's score, evaluated in
the nomination cursor's own executor (so in the same scoring generation): the
cursor projects `-(text <@> query)` while ordering by the raw `<@>`
expression, which pg_textsearch's planner hook serves from the index's own
score. **The index order is therefore not the canonical order**, and the
stopping rule must not assume it is. Upstream intends the two scores to agree
(the standalone operator quantizes length "to ensure the operator produces
identical scores to index scans"), but in pg_textsearch 1.5.1 rows still in
the in-memory memtable are scored by the index with their raw length, while
segments and the standalone operator use the quantized length; per-term
float4 scores are also accumulated differently (float4 in index order versus
float8 in query-term order).

**Bounded reconciliation — post-approval design change (2026-10-21).** The
round-3-approved design bound one canonical scorer by a local patch to
pg_textsearch. During implementation the patch proved broad (analysis §6.1:
the index accumulates float4 terms in index order on four separate scoring
paths, the operator accumulates float8 in query-term order), so this section
replaces it. The change is submitted to the dual review as a design change,
not as an implementation detail. Two facts bound the gap for every
row, without patching the extension:

1. Quantization only rounds a length down (largest table entry ≤ length), so
   quantized length ≤ raw length. BM25's per-term weight
   `tf·(k1+1) / (tf + k1·(1 − b + b·len/avg))` decreases with length, so
   **index score ≤ canonical score** (up to float rounding).
2. The quantization error is below 1/9 of the length for every length in
   pg_textsearch 1.5.1's `FIELDNORM_DECODE_TABLE` (worst case 0.11111). For
   `A = tf + k1·(1−b) > 0` and `c = k1·b/avg`, the per-term ratio
   `(A + c·raw) / (A + c·quantized) ≤ raw / quantized < 1 / (1 − 1/9)`, and a
   sum of positive terms keeps the bound, so **canonical ≤ index ÷ (1 − 1/9)**.

Floating point is bounded per query, not by a fixed slack (review r1 P1-2: a
fixed `10⁻⁴` fails for 40,000 single-occurrence terms in one long document,
ratio 1.12533 against 1.12523). `n` is the parser's own term count for the
query (`ts_debug` under the channel's `simple` configuration, every token
counted, compound tokens and their parts alike — review r2 P2-1). Each
scorer sums `n` positive terms computed in float4 with at most 16 roundings
each (the standalone operator adds them in float8 and casts the exact sum),
so with `m = n + 16`, `x = m·2⁻²⁴` and `γ = x/(1−x)` a computed score `C'`
and its real value `C` satisfy `C(1−γ) ≤ C' ≤ C(1+γ)`. With `ε = 2x`, both
`1+γ ≤ 1+ε` and `1/(1−γ) ≤ 1+ε` hold exactly while `x ≤ 1/4`; longer queries
(over about four million terms) are refused. Removing or adding each prime
costs one factor along `C'(u) ≤ C(u)(1+ε) ≤ I(u)(1+ε)/(1−1/9) ≤
I'(u)(1+ε)²/(1−1/9) ≤ I'(last)(1+ε)²/(1−1/9) ≤ I(last)(1+ε)³/(1−1/9) ≤
C(last)(1+ε)³/(1−1/9) ≤ C'(last)(1+ε)⁴/(1−1/9)`, so
`M = (1+ε(n))⁴ / (1 − 1/9)`. The loop
stops `k_filled` only when the `k`-th confirmed canonical score is strictly
greater than `M · canonical(last)`: no unfetched row can enter the result or
tie with its last member. In the reviewer's single-term example (lengths 48,
50, 67; average 100) the loop keeps fetching past the 50-token row until the
67-token winner is fetched. The cost is fetching rows whose canonical score is
within about 12.5% of the `k`-th, reported in the counters.

Raw-length index scoring is not adopted as the canonical score: it would make
a row's rank depend on whether a storage-maintenance spill has happened, and
exact small tiers could not compute it. A local scorer patch was considered
and set aside: making the index's accumulation bitwise equal to the
standalone operator touches every BM25 scoring path in the extension
(memtable, segment WAND, single-term fast path, top-k heap), whereas the
margin needs no extension change and stays valid if upstream unifies the
scorers.

**Exactness.** Within one scoring generation, with positive-match semantics,
continuation until the margin condition and the tie rule, a BM25 result equals
canonical (standalone) scoring of the same eligible matching rows, which is
also how exact small tiers and every oracle score. It is *intended* to match
today's standalone-scored ordering of those rows; the upgrade comparison in §9
measures it. HNSW is approximate (§9).

### 8.4 Entity-scoped nomination (coverage first)

Entity-scoped searches rank by entity coverage before score. Coverage is
defined per target:

- **testimony (chunks, claims):** the number of distinct requested anchor
  survivors that resolved mentions of the item name (the existing
  `nominate_testimony_scored` survivor resolution);
- **facts (relations, observations):** the number of distinct
  `ranking_entity_ids` among the fact's survivor-resolved endpoints (subject,
  and object for relations). The eligible scope (`entity_ids`, which may be a
  graph-expanded neighbourhood) and the ranking anchors stay distinct, as
  today.

No text index produces this order, but mentions and fact endpoints are
indexed by entity:

1. Coverage for every scoped item is computed from those tables (counts, no
   scoring) within an **entity work budget** on the number of mention or
   endpoint rows read. If the budget is exceeded, coverage counts are not
   trusted: the operation returns a typed D49 `boundary` negative (reason
   `entity_scope_too_broad`) instead of a ranking built on partial coverage.
2. Tiers are taken from the highest coverage down. A tier at or below the
   exact-scoring threshold (a **starting point of 5,000 items**, to be
   measured) is scored exactly — `<=>` for semantic, the canonical BM25
   score (`<@>`, positive matches only) for
   lexical — and sorted by score, then ID.
3. A larger tier runs the §8.3 cursor loop with tier membership added to
   confirmation.

D140's `scope_pending` probe replays this same nomination with pending
lineages widened; it never has its own copy of the ranking logic.

**Small document-filtered sets (post-approval addition, 2026-10-21).** A
document-filtered **chunk** search enumerates its eligible rows from the
matching versions (§8.3 filter pushdown). When the filter leaves at most the
exact-tier threshold, those rows are scored exactly and the search is
`exhausted`; larger sets go to the pushdown. Claim searches with a document
filter (a per-occurrence predicate) always use the loop. The scaling sweep
motivated it: with a 1% document filter the plain loop stopped at the work
budget on 5% of queries at 30k chunks and 30% at 300k, which §9's scaling
gate counts as a failure (rising truncation).

### 8.5 Facts

Fact search runs one §8.3 loop per fact table (relations, observations), each
on its own HNSW index, in the operation's context, with the fact authority
view, fact-time and entity predicates and the D140 evidence gate in
confirmation. Without entity anchors the branches merge by
`(distance, fact_kind, fact_id)`. With anchors, the whole result — both
branches — is ordered by `(coverage DESC, distance, fact_kind, fact_id)`
using the §8.4 fact coverage, and any per-branch depth is applied only after
that ordering and eligibility; a two-anchor relation therefore outranks a
closer one-anchor observation.

**Entity-scoped facts: bounded exact scan (post-approval alternative to the
§8.4 tier loop, review r2 P2-7).** With entity anchors, fact search does not
run the tier loop. It first counts the facts whose *stored* endpoints are
any entity the requested survivors absorbed (the merge-redirect expansion
that `facts_visible_history` resolves when it scores), reading at most the
entity work budget plus one rows across both fact tables. Above the budget
(50,000 rows, a starting point) the operation answers the typed
`entity_scope_too_broad` boundary; at or below it, every in-scope fact is
scored exactly, ordered by `(coverage DESC, distance, fact_kind, fact_id)`,
with full fact authority and the D140 gate before the cut. Exactness and the
input bound are the reasons; the cost of a 50,000-row exact scan has not been
measured at scale and is not a ≤ 5,000-row tier.

Entity profile search (`semantic_entities` and the engine's entity
nomination) uses the same §8.3 loop on `ix_entities_embedding_hnsw`, with
survivor identity and `entity_type` against `entities_current` in
confirmation.

Every semantic loop searches with `hnsw.ef_search = 200`, set
transaction-locally for the operation. On a synthetic clustered corpus
(100,000 vectors, 1,536 dimensions, 500 clusters) mean recall@10 was 0.887
at pgvector's default 40, 0.959 at 100 and 0.999 at 200 and 400
(`plan/analysis/data/d142_recall_2026-10-21.json`); real-query recall is
still to be measured.

### 8.6 Termination and the structured search result

Each port call returns a private **search result**, not a bare tuple of
nominations:

| field | meaning |
| --- | --- |
| `rows` | confirmed rows: stable ID, score, rank, channel |
| `termination` | `k_filled`, `exhausted`, `work_budget`, `deadline`, `ann_scan_bound`, `plan_rejected` |
| `counters` | nominated (fetched), confirmed, rejected by rule class, fetch rounds, coverage rows read |
| `limits` | effective `k`, work budget, `hnsw.ef_search`, `hnsw.max_scan_tuples`, exact-tier threshold |
| `attestation` | snapshot identity, pinned `evaluated_at`/`believed_at`, model/policy/BM25 configuration |

**When is a stream exhausted?** Channel-specific:

- **BM25 (pg_textsearch ≥ 1.5.1):** the cursor drained. Version 1.5.0 fixed
  silent truncation in deep scans and grows post-filtered batches until
  matches are exhausted, so a drained cursor means every matching row was
  streamed.
- **HNSW:** a drained iterative scan is **not** proof of exhaustion — it can
  also stop at `hnsw.max_scan_tuples`, at its memory limit, or skip closer
  candidates under strict ordering. The stream counts as `exhausted` only
  when an exact count of the deployment's searchable rows (same attestation
  predicates, non-null vector) is no greater than the number of distinct rows
  the cursor produced; that count runs only when a stream ends short, inside
  the work budget and deadline. Otherwise the termination is `ann_scan_bound`.
  Writers reject zero-norm and non-finite vectors, so every searchable row is
  reachable by the index.

**Propagation.** Termination is never lost on the way out:

- RRF fusion, the fact branch merge and every compound operation carry a
  per-channel termination list; the combined result is truncated if any
  contributing channel is not `k_filled` or `exhausted`.
- The envelope's truncation marker and candidate/eligible/drop counters come
  from the search results. An empty result is `known_empty` only when every
  contributing channel ended `exhausted`; an empty result after a bound is a
  truncated empty result, never `known_empty`.
- The open query space records each invocation, including zero-row ones, in
  `QueryResult.semantic_invocations[]` (requested/candidate/eligible counts,
  termination reason, snapshot, authority time) so the truncation survives an
  empty outer SQL result.
- The D140 `scope_pending` probe reports `complete: false` when any replayed
  nomination did not end `k_filled` or `exhausted`; its `count` is then a
  lower bound.

## 9. Index and planner contract

HNSW and BM25 indexes are maintained by ordinary PostgreSQL DML. Autovacuum and
standard PostgreSQL telemetry own routine cleanup; there is no P1 compaction
ticker, fragment ledger, or application-managed index tail.

Each indexed vector column has one fixed dimension and compatible distance
operator. Authority predicates in confirmation need conventional indexes on
their existing primary/join keys; confirmation probes are by primary key. This
design does not denormalize authority data into search rows or create a
generic indexing framework.

**Nomination plans are enforced and attested, not hoped for.** PostgreSQL
plans every statement by estimated cost; a single-table `ORDER BY … LIMIT`
can still be planned as a sequential scan plus sort (small tables, selective
deployment predicates, large fetch sizes). Removing joins makes the index plan
likely, not certain. Therefore, for the nomination cursor only:

1. immediately before `DECLARE`, the context sets transaction-local planner
   controls `enable_seqscan = off`, `enable_sort = off`,
   `enable_bitmapscan = off`, `cursor_tuple_fraction = 1` and
   `plan_cache_mode = force_custom_plan`. `EXPLAIN` cannot plan a `DECLARE`,
   and a cursor is planned for its first `cursor_tuple_fraction` of rows while
   the attested `SELECT` is planned for all of them; with fraction 1 the two
   are planned alike (the statement's `LIMIT`, the work budget, already
   bounds the rows), so the attestation covers the plan the cursor runs, and
   forcing custom plans keeps a prepared execution on that plan too.
   Immediately after `DECLARE` the context restores the caller's own values —
   on a healthy transaction; after a cancelled `EXPLAIN`/`DECLARE` it leaves
   them to the rollback so the original error survives — so confirmation and
   authority queries plan normally (the objection to steering the full
   statement does not apply here);
2. before declaring, it runs `EXPLAIN (FORMAT JSON)` of the exact nomination
   statement (planning only, no execution) and requires an index scan on the
   expected index (`ix_chunk_search_embedding_hnsw`, `ix_chunk_search_bm25`,
   `ix_claims_current_*`, `ix_relations_embedding_hnsw`,
   `ix_observations_embedding_hnsw`) as the node producing rows. Any other plan
   ends the call with termination `plan_rejected`, surfaced as
   `p1_unavailable`, before any rows are fetched — never a silent full scan;
3. readiness (`p1_search_channels`) also verifies that each expected index
   exists and is valid.

Generic and custom prepared plans are covered because attestation checks the
plan of the statement actually declared. The plan-attestation cost is one
planning pass per nomination; the scaling benchmark reports it.

ANN remains approximate. HNSW cursors run with pgvector's iterative scan in
strict-order mode (`hnsw.iterative_scan = strict_order`) and a
transaction-local `hnsw.max_scan_tuples` derived from the work budget; §8.6
defines how an early stop is reported. Query-time search parameters are
bounded, transaction-local, and observable; public requests cannot set
arbitrary GUCs.

**Release gates.** Index-driven execution is accepted only with these
measurements, recorded in the analysis and repeated when search code changes
materially. All numbers are starting points for the owner to confirm:

- **scaling** — per channel and for the context operations, on synthetic and
  real-shaped corpora across a size sweep (30k → 300k → 3M chunks and the 50M
  target), `main` against the change, reporting together: p50/p95 latency,
  returned-`k` rate, truncation rate, fetched candidates, coverage rows read
  and plan-attestation cost. Strata: fixed and declining eligible fractions,
  long tie runs, superseded editions, rare and hub entities, `current`/`at`/
  `history` scopes. Index-internal work (§9.1) is reported next to fetched
  counts. **Pass (amended by the owner, D142, 2026-10-22):** the
  size- and stratum-specific budgets below, each paired with completeness
  (every search `k_filled` or `exhausted`, early-stop rate 0, any early stop
  disclosed with its typed reason); adversarial strata are reported, not
  hidden. The ≈ 300 ms interactive budget stays the **target** for every
  common stratum, owed by the follow-up WP-RS.11 (immutable `doc_id` on search
  rows and a re-tuned document exact-tier threshold, issue #508). Multi-second
  filtered reads are accepted for now. Budgets hold for 300k chunks only;
  3M and the 50M target are ungated until measured on hydratable public
  results.

  | Stratum (300k chunks) | Eligible rows | Path | p95 budget | Measured p50 / p95 | Completeness |
  | --- | --- | --- | --- | --- | --- |
  | `current`, unfiltered | 60,000 current chunk rows | loop | **≤ 300 ms** | 152 / 263 ms | 40/40 return k; 0 early stops |
  | `history`, unfiltered | 180,000 history-eligible rows (of 300,000 indexed edition rows) | loop | **≤ 300 ms** | 112 / 267 ms | 40/40 return k; 0 early stops |
  | 0.1% of documents (6) | 60 | exact tier | **≤ 300 ms** | 78 / 90 ms | 0 early stops (7 return k; the rest have fewer matches) |
  | 1% of documents (60) | 600 | exact tier | **≤ 500 ms** | 235 / 351 ms | 0 early stops (18 return k; the rest have fewer matches) |
  | 10% of documents (600) | 6,000 | pushdown | **≤ 1,000 ms** | 600 / 859 ms | 0 early stops (37 return k) |
  | 499 documents | 4,990 | exact tier | **≤ 2,500 ms** | 1,334 / 2,084 ms | 0 early stops (35 return k) |
  | 501 documents | 5,010 | pushdown | **≤ 1,000 ms** | 505 / 780 ms | 0 early stops (35 return k) |

  Measured conditions (all binding on the criterion): the public
  `QueryEngine.search_chunks` operation, BM25 channel, k = 20, in-process (no
  HTTP); a synthetic corpus of 6,000 lineages × 5 versions × 10 chunks = 300,000
  chunk rows, half the lineages periodised (declared effective periods) and half
  repeating version 1's text in later versions (120,120 duplicated rows), chunk
  text 20–300 Zipf-skewed words from a 50,000-word vocabulary, D134 metadata on
  every version, no vectors; one GCE n2-highmem-8 VM (8 vCPU, 64 GB, pd-ssd),
  the branch's PostgreSQL 19 engine image in Docker (`shared_buffers` 8 GB,
  `work_mem` 128 MB), warm cache (corpus seeded and indexed in the same session,
  heap and indexes fit in shared buffers), while the integration test suite ran
  on the same VM against another database (contended CPU, no other load on this
  database); 40 two-word queries drawn from the generator's own Zipf
  distribution (seed 142, query hash `c5d83714…`), each run once per stratum,
  sequentially, no concurrency. Archive:
  `plan/analysis/data/d142_sweep_300k_2026-10-22.json`. "Fewer matches" means
  the search was `exhausted` with fewer than k positive lexical matches among the
  eligible rows — a complete answer, not an early stop.

- **HNSW recall** — recall@k of the HNSW path against exact scoring, per
  stratum above, on sampled real queries; acceptance threshold: mean
  recall@10 ≥ 0.95 and no stratum below 0.90;
- **answer quality (paired non-inferiority, predeclared)** — LoCoMo answer
  accuracy, `main` against the change, on identical ingested data and the same
  question set. *Sampling unit:* the question. *Runs:* three answer runs per
  side; each question's score per side is its mean correctness over that
  side's runs. *Estimator:* the mean over questions of the paired difference
  (change − `main`). *Test:* a one-sided 95% lower confidence bound on that
  mean by paired bootstrap over questions (10,000 resamples, fixed seed,
  recorded). *Margin:* a fixed non-inferiority margin of 1.0 percentage point
  (a starting point the owner confirms before the run). *Decision:* pass when
  the lower bound is above −1.0 pp; fail when the one-sided 95% **upper**
  bound is below −1.0 pp; otherwise the result is **inconclusive** — an
  underpowered result never passes; the owner then chooses more runs or a
  decision. The rule, seed and question set are fixed before the first run;
- **scorer bound** (every CI run, on the built engine image) — the margin is
  re-verified whenever the extension changes, and the loop is checked against
  an exact canonical oracle. A unit gate fails when the image pins a
  `pg_textsearch` revision other than the one whose length table and scorers
  the margin was verified on (`BM25_BOUND_VERIFIED_REVISION`), checks the
  quantization error against that table, and emulates the float32
  long-query counterexample against `ε(n)`. On the built image, a randomized
  corpus with half its rows spilled to a segment and half in the memtable,
  lengths across the exact and quantized ranges, and multi-term queries with
  repeated terms checks, for every streamed row against
  `bm25_get_current_score()`, `index ≤ canonical·(1+ε)²`,
  `canonical ≤ index·(1+ε)²/(1 − 1/9)` and the index order; further proofs
  check the loop, the filter pushdown and the exact tier against the oracle
  under a restrictive filter, the 48/50/67 counterexample and the 40/41 tie.
  A failure blocks the image; it would mean the extension changed its length
  quantization or scoring and the margin must be re-derived;
- **lexical behaviour** — the BM25 positive-match change (§8.1) and the
  `pg_textsearch` upgrade are
  measured separately against today's answers on the same corpus, so their
  effects are attributable; the upgrade comparison reports whether the
  canonical ordering of matching rows equals today's standalone ordering,
  including multi-term tie sets.

Index each distinct search input once (one entry per identical text and
embedding input, shared by the per-version chunks that carry it) is a
designed, **unchosen** option with an evidence-based investigation trigger:
[distinct-text search index proposal](../proposals/distinct_text_search_index.md).

### 9.1 Cost model and its limits

Two kinds of work must not be confused.

- **Fetched-candidate and confirmation work** — rows fetched from the cursor
  and probed by confirmation. In the common case this is proportional to `k`
  divided by the share `s` of eligible candidates near the top of the ranking,
  independent of corpus size. The candidate counters and the work budget
  measure and bound it.
- **Index-internal work** — what the extension does to produce those rows:
  BM25 posting and block traversal, memtable scoring, and pg_textsearch's
  internal re-execution of scoring when it grows its batch during
  continuation; HNSW graph visits. A fixed `k/s` does not bound it, and the
  candidate counters do not see it. The operation **deadline** bounds it; the
  benchmark reports it separately (buffer reads and, where the extensions
  expose them, posting/block/scoring-pass counts and visited HNSW tuples)
  alongside fetched counts and completeness.

The common-case assumption fails in three named cases, which are reported
rather than hidden:

- **declining eligible share** — when a fixed small eligible set sits among a
  growing number of ineligible rows (for example many superseded editions of
  matching text), `s` falls as the corpus grows and nomination work grows
  until the work budget stops it (`work_budget`, disclosed);
- **long tie runs** — stable-ID exactness requires reading a whole run of
  equal scores at the cut; a run can be large (identical text in many
  versions or documents);
- **broad entity scopes** — coverage counting reads mention/endpoint rows in
  proportion to the anchors' mentions, bounded by the entity work budget.

The distinct-text proposal reduces the first two only where identical inputs
share an entry; equal scores across different embedding contexts remain.

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
- stale extra derived row: authority confirmation in the ranked-read context rejects it;
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

## 13. Historical record: the D94 cutover

The D94 replacement of LanceDB is complete. It was a direct replacement on
`main` with no dual write, shadow read, parity run or benchmark, and it deleted
all LanceDB runtime estate (dependencies, adapters, configuration, maintenance
state, `p1_lance` migrations, backup paths, documentation, tests and
fixtures). Historical analysis, superseded designs and decision-log entries
remain as history and are labelled accordingly.

That "no benchmark" scope applied to the cutover only. Current release gates
for ranked-search execution are in §9; build order for D142 lives in
[the delivery plan](../plans/index_driven_ranked_search.md), not here.

## 14. Consequences

Benefits:

- one hot-path database, transaction snapshot, backup, and recovery procedure;
- no cross-store confirmation network hop or synchronization machinery;
- semantic, BM25, entity, temporal, and lineage eligibility confirmed in one
  PostgreSQL snapshot (D142's ranked-read context);
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

D142's ranked-read context and nominate–confirm–continue execution (§8.2–§8.6)
adds:

- **benefit:** in the common case fetched-candidate and confirmation work
  grows with `k` divided by the share of eligible candidates near the top of
  the ranking, not with corpus size; index-internal work is bounded by the
  deadline and measured separately (§9.1, which also names the cases where the
  common case fails); nomination plans are enforced
  and attested; every early stop is reported with its reason;
- **cost:** cursor fetches and confirmation queries instead of one statement;
  one snapshot shared by the whole operation (including the SQL sandbox, via
  snapshot export/import); a plan-attestation pass per nomination; semantic
  search that is approximate (HNSW) instead of an accidental exact scan,
  guarded by the §9 recall and answer-quality gates;
- **behaviour change:** lexical (BM25) results are positive matches only
  (§8.1); non-matching rows no longer fill the lexical list or take RRF
  ranks. The change is measured separately in the §9 gates.
