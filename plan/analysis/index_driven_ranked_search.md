# Analysis: index-driven ranked search (issue #504)

**Status:** non-binding analysis, 2026-10-21. It supports
[D142](../../decisions.md#d142-ranked-search-nominates-from-the-index-and-confirms-in-one-snapshot)
and the binding changes in
[the P1 search design](../designs/postgres_p1_search_projection_design.md) §8–§9.
**Issue:** [writeitai/remember-stack#504](https://github.com/writeitai/remember-stack/issues/504).
**Evidence:** [D140 analysis §11](version_effective_time_and_section_references.md#11-measured-the-time-scope-cost-2026-10-02)
and `benchmarks/d140_scope/results/2026-10-02/`.

## 1. The problem in one paragraph

On the benchmark corpora, every ranked search on `main` — "find the chunks or
claims most similar to this query" — took seconds and got slower in direct
proportion to how much text the deployment held. Whether real deployments plan
the same way is the pending check below. The engine has the right indexes: an HNSW index
for meaning-based (semantic) search and a BM25 index for keyword search. An
index of this kind can answer "give me the 20 best matches" by looking at a
small part of the data. But the SQL statements we send combine that ranking
with a long list of visibility checks (is the chunk's document deleted? is its
version in force at the requested time? does it mention the requested
entity?) in one statement, and PostgreSQL's planner answers that statement by
scoring **every** chunk of the deployment, discarding the invisible ones and
sorting the rest. At 30,000 chunks that is about a second; at 50 million it
would be minutes.

### Measured evidence

The D140 benchmark (2026-10-02, one `n2-highmem-16` VM, PostgreSQL 19 beta 3
built from `Dockerfile.postgres`, every table analysed, caches warm):

| corpus | statement | `main` p95 |
| --- | --- | --- |
| undated, 30,000 chunks | BM25 chunk search | 1,261 ms |
| undated, 30,000 chunks | semantic chunk search | 682 ms |
| undated, 30,000 chunks | `facts_context` | 5,239 ms |
| undated, 30,000 chunks | `claims_and_sources_context` | 7,296 ms |
| dated, 500,000 chunks | BM25 chunk search | 2,759 ms |
| dated, 500,000 chunks (100,000 with vectors) | semantic chunk search | 844 ms |

`EXPLAIN ANALYZE` of one BM25 chunk query on `main` showed a sequential pass
over all 30,000 chunks followed by a sort. The interactive budget in
[the retrieval design §10](../designs/retrieval_design.md#10-performance-envelope-and-topology)
is p95 ≤ ~300 ms per operation at the 1M-document target.

**Caveats, stated plainly.** The data was synthetic (random text, random unit
vectors). Index behaviour and planner choices on real text and embeddings may
differ (a hypothesis, not a measured fact either way). The check on a real deployment
(`EXPLAIN (ANALYZE, BUFFERS)` of each ranked statement against a copy of a real
LoCoMo database) is still pending: on 2026-10-21 a full LoCoMo run is in
progress and its database must not be loaded. §6 explains why the chosen
design does not depend on the outcome of that check; the check stays the first
step of the delivery plan because it may reveal additional causes.

## 2. Why the planner scans everything — the code on `main`

All ranked statements live in `src/rememberstack/adapters/postgres_p1.py`.
Their shape (chunk BM25, `search_chunks_lexical_scored`, abbreviated):

```sql
SELECT indexed.chunk_id, -(indexed.search_text <@> to_bm25query(:q, 'ix_chunk_search_bm25'))
FROM chunk_search AS indexed
JOIN memory_v1.chunks_all_versions_live AS published ON …      -- live version?
JOIN public.document_version_scope …                          -- D140: in force?
JOIN memory_v1.documents_live AS document ON …                -- document live?
LEFT JOIN document_sections AS section ON …
[JOIN LATERAL (… memory_v1.mentions_live …) AS entity_scope]   -- entity filter
WHERE indexed.deployment_id = :d AND <filters>
ORDER BY [entity_scope.coverage DESC,] indexed.search_text <@> …, indexed.chunk_id
LIMIT :k
```

The semantic chunk/claim statements, the claim BM25 statement, the
entity-scoped `nominate_testimony_scored`, and the fact statements
(`search_facts_scored`, `nominate_facts_scored`, one `UNION ALL` branch per
relation/observation table with the D140 fact evidence gate) follow the same
pattern. Five properties of this shape each push the planner away from an
index-ordered scan, and several push it towards a full scan even when it would
consider the index:

1. **The `ORDER BY` is not the index's order.** An HNSW or BM25 index can
   only return rows ordered by its own distance expression. Every statement
   adds a tie-break (`, indexed.chunk_id`), and entity-scoped statements put
   `coverage DESC` *before* the distance. With coverage first, no index order
   can satisfy the sort at all; with the tie-break, PostgreSQL needs an
   incremental sort on top of the index scan, which the cost model often
   rejects in favour of "score everything, sort once".
2. **Visibility lives in views.** `chunks_all_versions_live`,
   `documents_live`, `claims_live` and `facts_visible_history` are views over
   several tables. To use the index, the planner must drive a nested loop from
   the index scan and probe each view per candidate. When it cannot estimate
   how many candidates survive the views, a hash join over the whole table is
   the "safe" plan.
3. **Post-filtering an approximate index is lossy unless the index keeps
   going.** An index scan returns the best N rows and the filters are applied
   afterwards. If most of those N are invisible, fewer than `k` rows survive.
   pgvector's *iterative scan* (`hnsw.iterative_scan = strict_order`, set on
   every semantic read since D94) keeps scanning until enough rows pass, but
   stops at `hnsw.max_scan_tuples` (the engine uses 20,000) or at its memory
   limit without saying which — the caller cannot tell "no more matches"
   from "gave up" (pgvector 0.8.6 "Iterative Scan Options"; the scan code
   also notes strict ordering can skip closer candidates found later).
4. **The pinned BM25 extension truncates post-filtered scans.** The image pins
   `pg_textsearch` 1.3.1 (revision `578ff529`, plus the D98 PostgreSQL 19
   compatibility patch). Its README at that revision says that with
   post-filtering "the index returns top-k results *before* filtering. If your
   WHERE clause eliminates most results, you may get fewer rows than
   requested." In other words, at 1.3.1 an index-ordered BM25 plan with our
   filters would be *wrong*; the correct plans available are a full scan or
   exact scoring of a selectively prefiltered subset. Upstream fixed this: 1.4.0 (2026-08-18) adjusts the internal
   top-k by filter selectivity, and 1.5.0 (2026-10-01) makes post-filtered
   scans "automatically grow their internal scoring batch until the LIMIT is
   filled or matches are exhausted", fixes "silent result truncation in deep
   scans" and "unstable BM25 scores", and adds PostgreSQL 19 beta support
   upstream (so the D98 patch becomes unnecessary). 1.5.1 (2026-10-02) is a
   bug-fix release.
5. **Small synthetic tables flatter the full scan.** At tens of thousands of
   rows a sequential scan really is cheap. This is why the real-data check
   matters — but the measured 500,000-chunk run shows the same plan, and the
   cost grows linearly.

The binding P1 design §9 already *intends* index-driven filtered search
("PostgreSQL continues the ANN scan until it finds the requested eligible
rows…"). The implementation's plans do not deliver it, and points 3 and 4 mean
that simply forcing an index plan would trade slowness for silent truncation.

## 3. What a correct fix must preserve

These are existing binding rules; the fix may not weaken any of them.

- **D48 — projections propose, the spine disposes.** A search index may only
  *nominate*; every returned row is confirmed against authority (live
  lineage/version/representation, not deleted or forgotten) in one database
  snapshot, so a deletion committed before the snapshot can never appear.
- **D87 — no global top-k followed by filtering.** Eligibility (entity scope,
  fact time, D140 time scope) constrains the candidate set *before* the
  relevance cut. Concretely: the k results returned must be the k best
  *eligible* rows, not the eligible survivors of the k best rows.
- **D140 semantics:** the time scope (`current`/`at`/`overlap`/`history`),
  the fact evidence gate (§8.1), claim occurrence selection (§3.4) and the
  `scope_pending` probe (§3.7) are evaluated before the cut.
- **Honest truncation (D49).** Any bound that stops work early is disclosed
  through the envelope's truncation marker and counters; it never poses as
  "no more results".
- **Deterministic order:** score, then stable ID.
- **Zero LLM calls on the query path (D9).**

## 4. Options

All options are compared **at the selected versions**: `pg_textsearch` 1.5.1
(post-filtered BM25 scans grow until filled or exhausted) and pgvector 0.8.6
(iterative scans that can still stop at a tuple or memory cap).

### Option A — keep one statement and steer the planner

Rewrite each statement so an index plan becomes attractive: drop the
tie-break from `ORDER BY`, turn view joins into `EXISTS` probes, upgrade
`pg_textsearch`, raise `hnsw.max_scan_tuples`, and where needed
`SET LOCAL enable_seqscan = off` for the whole statement.

- **For:** smallest diff; keeps "one statement" literally; with 1.5.1 the
  BM25 side no longer truncates silently.
- **Against:** planner controls then apply to the authority joins too, and
  the plan is still chosen per query from estimates. Coverage-first ordering
  still cannot use an index. The HNSW side can still stop at its tuple or
  memory cap, and a single statement gives the engine no way to tell that
  apart from exhaustion, so the honest-truncation rule cannot be met.

### Option B — nominate from an index cursor, confirm by primary key, continue (chosen)

Per target and channel, inside the operation's one read-only
`REPEATABLE READ` **ranked-read context** (one snapshot, pinned evaluation
and belief instants):

1. **One nomination cursor** over a join-free statement: the search table,
   the deployment and attestation predicates, any partial-index predicate,
   and the ordering expression. One cursor is one executor, so BM25 keeps
   one scoring generation (pg_textsearch captures corpus statistics per
   executor, not per transaction snapshot — re-running a statement could
   rescore under newer statistics) and HNSW one continuing iterative scan.
   Planner controls apply to this `DECLARE` only, and an `EXPLAIN` of the
   statement must show the expected index.
2. **Disjoint fetches** from the cursor (`FETCH n`, `n` doubling from `4·k`);
   scores are carried, never recomputed.
3. **Confirmation** of each batch by primary key with the existing predicate
   builders (one implementation; the lesson of D140 implementation review
   round 3).
4. **Stop** on `k_filled` (past any equal-score run), `exhausted`
   (channel-specific proof), or a disclosed bound.

- **For:** the index plan is enforced and checked, not hoped for; eligibility
  is exact; every stop has a reason the engine can report; no BM25 rescoring.
- **Against:** more statements per channel; one snapshot must span the
  operation, including the SQL sandbox (snapshot export/import); semantic
  search becomes approximate; a plan-attestation pass per nomination.

### Option B′ — re-run a growing `LIMIT` per round (rejected variant)

The first draft of this analysis re-issued the index query with a larger
`LIMIT` each round. Rejected: each statement captures a new BM25 scoring
generation (so scores and tie boundaries can shift between rounds under
concurrent ingestion), and every round repeats the previous rounds' index
work.

### Option C — copy visibility onto the search rows

Store "visible", "in force from/until", entity IDs, etc. on `chunk_search`
and the in-row embedding tables, so the filter is a plain column predicate
the index scan evaluates.

- **Against:** D94 explicitly rejected copied entity/filter scalars. D140
  periods are corrected retroactively and evaluated at arbitrary instants; a
  correction or a deletion would rewrite thousands of search rows, and every
  write path (ingest, D135 delete, D74 forget, entity merges) would gain a
  second place that must stay consistent. The HNSW side would still
  post-filter. Rejected.

### Option D — partial or partitioned indexes per visibility class

Already used where a stable boolean exists (`ix_claims_current_*` on
`is_current_testimony`). Time-scoped chunk visibility has no stable boolean,
and per-deployment partitioning does not address versions. Kept for the
existing claim case; not a general answer.

### Option E — a different ANN engine (pgvectorscale/DiskANN)

An unchosen proposal already exists
([`design/proposals/pgvectorscale_default_index.md`](../../design/proposals/pgvectorscale_default_index.md)).
It changes index quality and filtering mechanics for vectors only, not the
BM25 or authority problems. Out of scope; its adoption trigger is unchanged.

### The lexical zero-score question

Today's lexical statements order *all* eligible rows by the standalone BM25
operator and ID, with no match requirement. A query matching nothing returns
`k` zero-score rows; a query with fewer than `k` matches gets a zero-score
tail, and RRF counts those rows as lexical evidence. An index scan only
streams matching postings, so option B cannot reproduce that tail. Two
choices:

- **positive-match only (chosen):** a lexical result must match at least one
  analyzed term; zero-score rows are never lexical results on any path. The
  tail carried no keyword signal, and D140's `scope_pending` probe already
  required a term match. This is a behaviour change, measured separately in
  the quality gates;
- **fill the zero-score tail by a stable-ID walk of eligible rows** after
  matches are exhausted: preserves today's output, but adds an unbounded walk
  whose rows carry no relevance information. Rejected.

## 5. How option B behaves — worked numbers

Let `k` be the requested result count and `s` the share of fetched candidates
(near the top of the ranking) that pass confirmation. Fetches are disjoint
batches of 80, 160, 320, … rows for `k = 20` (`n₀ = 4k`, doubling); after
fetch *j* the cursor has delivered 80·(2ʲ − 1) rows.

| situation | `s` | fetches until ≈ 20 confirmed |
| --- | --- | --- |
| LoCoMo-style corpus, nothing deleted, no periods | ≈ 1 | 1 (80 rows) |
| a deployment with some deletions | 0.8 | 1 |
| law, `current`, paragraph unchanged in 3 versions | ≈ 0.33 | 1 (≈ 26 survivors) |
| law, `current`, unchanged in 20 versions | ≈ 0.05 | 3 (80 + 160 + 320 = 560 rows, ≈ 28 survivors) |
| entity filter with a rare entity | ≪ 0.01 | exact scoring of the entity's items (design §8.4) |

Each fetch costs one cursor fetch plus one confirmation query over the new
IDs. **In the common case** fetched-candidate and confirmation work grows
with `k/s`, not with corpus size. Index-internal work — BM25 posting/block
traversal and pg_textsearch's internal rescoring when it grows its batch
([scan.c at v1.5.1](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/access/scan.c#L579-L627)),
and HNSW graph visits — is not bounded by `k/s`; the deadline bounds it and
the benchmark measures it separately. That
assumption fails in named cases (design §9.1): when a fixed small eligible set
sits among a growing number of ineligible rows, `s` itself falls as the
corpus grows and work grows until the budget stops it; long runs of equal
scores must be read whole at the cut; broad entity scopes cost coverage rows.
These are reported, with returned-`k` and truncation rates, rather than
claimed away.

**Ties.** Two candidates with the same score must be ordered by stable ID. An
index returns equal scores in arbitrary order, so the loop does not cut inside
a run of equal scores: after `k` are confirmed it continues until the next
fetched score is strictly worse than the `k`-th confirmed score. In versioned
corpora identical text produces long runs of equal BM25 scores — the
duplication §7 discusses.

## 6. The chosen design in brief

The binding text is the [P1 search design §8–§9](../designs/postgres_p1_search_projection_design.md#8-query-and-filtering-contract).
In brief:

- **Ranked-read context (§8.2):** one read-only `REPEATABLE READ` transaction
  per operation, with `evaluated_at`/`believed_at` passed as parameters, owned
  by the query engine; the SQL sandbox executor exports its request snapshot
  and the adapter imports it, so the sandbox's own D48 confirmation and the
  adapter's nominations read one state. The original draft's claim that the
  bridge "inherits" a shared snapshot unchanged was wrong: the adapter opens
  its own connection while the bridge confirms on the executor's.
- **Nominate, confirm, continue (§8.3):** as option B.
- **Coverage first (§8.4–§8.5):** testimony coverage from survivor-resolved
  mentions; fact coverage from survivor-resolved endpoints counted against
  `ranking_entity_ids`; an entity work budget whose overflow returns a
  `boundary`; exact scoring of tiers up to a starting threshold of 5,000;
  facts merged by `(coverage DESC, distance, fact_kind, fact_id)`.
- **Structured result (§8.6):** rows plus termination reason, counters,
  limits and attestation, propagated through RRF, the fact merge, envelopes,
  `QueryResult.semantic_invocations[]` and `scope_pending.complete`.
  `known_empty` only when every channel was exhausted.
- **HNSW exhaustion (§8.6):** a drained iterative scan is not proof; an exact
  count of searchable rows must not exceed the rows streamed, otherwise the
  stop is `ann_scan_bound`.
- **Plans (§9):** planner controls scoped to the nomination `DECLARE` and
  attested by `EXPLAIN`; an unexpected plan ends the call with
  `plan_rejected` before fetching.

### 6.1 Exactness

**Round-2 finding: two BM25 scorers.** pg_textsearch 1.5.1 (commit
`fa142d04`) does not score a row identically on all paths. The index scorer
for rows still in the in-memory memtable uses the **raw** document length
([bmw.c L438–L454](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/scoring/bmw.c#L438-L454));
segments and the standalone `<@>` operator use the **fieldnorm-quantized**
length ([query.c L959–L966](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/types/query.c#L959-L966),
whose comment says the quantization exists "to ensure the operator produces
identical scores to index scans"; [fieldnorm.c L52–L69](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/segment/fieldnorm.c#L52-L69)
maps 40 and 41 tokens to 40). Holding one scoring generation does not fix
this. The reviewer's single-term example over a corpus of average length 100
(three rows of length 48, one of 50, one of 67) orders 48, 48, 48, 50, 67 in
the index, while quantized scoring makes the 67-token row (length 64 after
quantization) the winner; at `k = 1` the loop would stop after seeing a
strictly worse 50-token row and never fetch the winner.

Options considered:

- **make raw-length index scoring canonical** — rejected: a row's score, and
  so its rank, would change when a storage-maintenance spill moves it from
  memtable to segment; the exact small tier and any oracle could not
  reproduce it;
- **rescore the fetched prefix with the standalone operator** — rejected:
  the index order is still wrong, so the true winner may never be fetched;
- **a local patch making both scorers bitwise equal** — set aside during
  implementation (2026-10-21): the index's float4 accumulation in index
  order differs from the operator's float8 accumulation in query-term order
  on every BM25 scoring path (memtable, segment WAND, single-term fast path,
  top-k heap), so the patch would be broad and need rebasing on every
  upgrade;
- **bounded reconciliation (chosen after the round-3 approval — a
  post-approval design change submitted to the dual review)** — keep the standalone operator as the
  canonical score, evaluated in the cursor's executor, and stop only past a
  proven margin: quantization rounds lengths down by less than 1/9, so for
  every row `index ≤ canonical ≤ index/(1−1/9)` (plus float slack). The loop
  stops when the `k`-th confirmed canonical score exceeds
  `(1+ε(n))⁴/(1−1/9)` times the last fetched canonical score, `ε(n)`
  bounding float4 accumulation over the query's `n` terms (≈ 1.126 for a
  4,096-character query; review r1 P1-2). No
  extension change; a scorer-bound gate re-verifies the bound in CI on every image
  build (design §8.3, §9).

With one scoring generation, the ordered scan streams matching rows in
non-increasing *index* score, which the margin relates to the canonical score (Block-Max WAND for
the leading top-k, then batch growth until the cursor is drained). With
positive-match semantics, continuation and the tie rule, a BM25 result equals
canonical scoring of the same eligible matching rows under that generation.
The canonical score pins per-term float4 scores **and** their float8
accumulation in query-term order — the standalone operator's accumulation at
1.5.1 ([declaration](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/types/query.c#L811-L815),
[accumulation](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/types/query.c#L1056-L1067)).
It is intended to reproduce today's standalone ordering; whether multi-term
rounding and tie sets are unchanged is measured by the upgrade comparison
not assumed.

HNSW is approximate. Today's semantic search is accidentally exact because the
planner scans everything; moving to the index trades recall for speed. How
much recall is lost on our data is not known yet — it is measured per
stratum in the release gate (§8) rather than assumed.

## 7. Duplicate text in versioned corpora

D140 creates one chunk row — and therefore one `chunk_search` row with its own
text and embedding — per document version, even when a paragraph is
unchanged. D140's reuse only avoids re-extracting claims. In a corpus where a
paragraph stays unchanged across 20 versions, its text sits in each index 20
times. With option B this costs in three ways:

1. **Fetch depth:** for `current` queries, roughly one in 20 near-top
   candidates is visible (§5), so the loop needs several fetches.
2. **Tie runs:** the 20 copies tie on BM25 score; the tie rule must read
   all of them before cutting.
3. **Index size:** the index holds up to 20 entries for that text. (The
   embedding *call* is not repeated: E1 already carries a vector forward from
   the previous version when the chunk's content hash and embedding-input
   hash match — D80, `workers/e1.py` "cross-version carry-forward". The
   duplicate is in index rows, not in provider spend.)

The remedy is to index each **distinct indexed input once** and keep the list
of chunks (and so versions) that carry it. Each nominated entry is fanned out
to its member chunks **before** the relevance cut, and members are confirmed
and ranked exactly like chunk candidates today, so chunk results keep their
grain (separate results for separate eligible editions in every scope). Chunk
rows stay per version (they anchor citations, positions, occurrences,
navigation and deletion). The mechanism is specified in
[the distinct-text search index proposal](../proposals/distinct_text_search_index.md).

**Is it needed?** Unknown until we have a real versioned corpus. Candidates
examined per result is not by itself the right signal: it also counts
deletions, entity/document/time rejections and batch over-fetch, and a high
value can remain high after deduplication. The proposal therefore requires an
**isolated prototype** on a copy of the LegalIt pilot corpus (20–50 heavily
amended Czech acts with all versions) and a fixed query set, measuring
candidates examined per result by rejection reason, the share attributable to
shared indexed inputs, tie-run lengths, latency, returned-`k`/truncation
rates and lexical/hybrid quality — for D142 alone and with the prototype.
Investigation thresholds (starting points, owner to confirm): a median of ≥ 5
examined candidates per result attributable to shared inputs, or p95 out of
budget when extrapolated to the full corpus. Adoption additionally requires a
demonstrated latency or completeness benefit at equal or better quality.

**Embedding reuse is already implemented** for the case that matters here.
E1's embed stage copies the previous version's vector when an unchanged chunk
has the same content hash and the same embedding-input hash (D80). It can
miss when the embedding input changes although the body does not — for
example a D80 location header that names a section path renumbered by an
amendment. The pilot checks how often that happens on real law (count
embedding calls when a second version of an act is ingested). A miss rate
worth fixing would be handled in E1/D80, independently of #504.

## 8. Verification

The executable list is the delivery plan's WP-RS.7–9; the substance:

1. **Real-data plan check** (first): `EXPLAIN (ANALYZE, BUFFERS)` for each
   ranked statement on a copy of a real deployment, after the current LoCoMo
   run, to confirm the causes in §2 and catch any others. It stays before
   adapter implementation.
2. **Equivalence (BM25):** for randomized corpora with deletions, periods,
   entity scopes and ties, the loop's results equal exact scoring of the same
   eligible matching rows **under the same extension version and scoring
   generation**, with the scorer-bound fixtures of design §9 (40/41-token
   ties with reversed stable IDs, mixed spilled/unspilled rows, multi-term
   near-ties, the 48/50/67 single-term counterexample, both sides of the
   exact-tier threshold). Absent terms, empty analyzed queries, fewer-than-`k` matches
   and both sides of the exact-tier threshold are covered. Concurrent
   term-skewed inserts with spills/compaction between fetches must not change
   a running nomination's scores.
3. **Adversarial and boundary tests:** a filter that rejects almost all top
   candidates (continuation reaches `k`); BM25 exhaustion (`exhausted`, no
   truncation); HNSW stops at `max_scan_tuples` and at the memory limit before
   the work budget (`ann_scan_bound`), distinguished from proven exhaustion;
   a reached work budget (`work_budget` with counters); empty-cap and
   one-channel-cap results through the envelope (never `known_empty`); tie
   runs across a fetch boundary; hub-entity tiers and the entity-budget
   boundary; the D140 gate and `scope_pending.complete`. **Snapshot
   boundary, split in two:** a deletion committed *before* the context's
   snapshot is absent; a deletion committed *between fetches* does not change
   that invocation's view (the row stays visible to it) and is absent in the
   next invocation — nomination and confirmation agree in both cases, and the
   reported authority time is the pinned instant. Concurrent insert/delete
   and effective-period boundary cases also run through the real SQL bridge.
4. **Scaling benchmark:** the size sweep and strata of design §9, `main`
   against the change, reporting latency together with returned-`k` rate,
   truncation rate, fetched candidates and coverage rows. A flat latency curve
   with a rising truncation rate is a failure, not a pass.
5. **Quality gates** (design §9; starting points for the owner to confirm):
   HNSW mean recall@10 ≥ 0.95 against exact scoring with no stratum below
   0.90; LoCoMo answer accuracy by the predeclared paired non-inferiority test
   of design §9 (question as sampling unit, three runs per side, paired
   bootstrap one-sided 95% bound, fixed 1.0 pp margin, inconclusive when
   underpowered). The scorer-bound gate (design §9) runs in CI on the built
   image, randomized spill-mixed corpus included. The positive-match lexical change and the
   `pg_textsearch` upgrade are measured separately against today's answers.
   The LoCoMo part is a paid run and waits until the in-progress run has
   finished.

### 8.1 Scaling sweep results (WP-RS.8, 2026-10-21)

`benchmarks/p1_ranked/sweep.py` ran on an isolated GCE VM (n2-highmem-8, one
PostgreSQL 19 container per side built from that side's `Dockerfile.postgres`:
`main` with pg_textsearch 1.3.1 + the D98 patch, the branch with 1.5.1).
The corpus is the D140 seed (lineages × 5 versions × 10 chunks, half
periodised) with chunk text rewritten to Zipf-skewed words of 20–300 words,
half of the lineages repeating their text across versions, and D134 metadata
on every version. BM25 chunk search, `k = 20`, 40 two-word queries sampled
from the corpus (8 on `main` at 300k: each `main` query scores every chunk).
Latency is p50 / p95 in ms.

| chunks | stratum | `main` | branch | branch terminations (of 40) |
| --- | --- | --- | --- | --- |
| 30k | current | 4,644 / 4,668 | 82 / 609 | `k_filled` 30, `exhausted` 10 |
| 30k | history | 14,540 / 14,572 | 34 / 83 | `k_filled` 38, `exhausted` 2 |
| 300k | current | 49,599 / 49,959 | 88 / 183 | `k_filled` 40 |
| 300k | history | 148,045 / 148,325 | 66 / 96 | `k_filled` 40 |
| 3M | current | not run (linear: ≈ 500 s) | 415 / 1,581 | `k_filled` 40 |
| 3M | history | not run | 98 / 291 | `k_filled` 40 |
| 3M | 6 documents (exact tier) | — | 107 / 109 | `exhausted` 40 |
| 3M | 60 documents (exact tier) | — | 511 / 518 | `exhausted` 40 |
| 3M | 600 documents (1%, loop) | — | 3,809 / 5,026 | `k_filled` 14, `exhausted` 16, **`work_budget` 10** |

The 3M branch rows were measured right after the index rebuild; a warm
re-run gave `current` 165 / 396 and `history` 99 / 288. `main`'s 1% document
stratum is not comparable (its run predates the metadata fix, so the filter
matched nothing).

Readings:

- Unfiltered `current` and `history` latency grew sublinearly in this run
  (88 ms → 415 ms p50 for `current` from 300k to 3M chunks, against `main`'s
  linear growth); the measurements do not establish corpus-independent
  latency (review r2 P2-4). Fetched candidates stayed in the hundreds.
- Small document filters are exact and fast through the small-set tier.
- **A 1% document filter at 3M chunks (about 6,000 eligible chunks, above the
  5,000 exact-tier threshold) still fails the scaling gate:** 25% of queries
  stop at the work budget (disclosed as `p1_work_budget`), with p50 3.8 s.
  Nominating from the BM25 index cannot reach rows that are 1% of a large
  match set within the budget, and exact scoring above the threshold costs
  about 0.5 ms a row. This is the case the distinct-text proposal and a
  filter-first plan would have to address; it is reported, not solved.
- Three harness problems were found and fixed on the way: the D140 seed's
  text is degenerate (every word is `w0`, because `hashint8` returns a 32-bit
  hash that the seed divides by 2⁶³), the seed writes no D134 metadata (a
  document filter matched nothing), and confirmation by `= ANY(:batch_ids)`
  let the planner start from every eligible row (17 s for one 4,000-ID batch
  at 30k chunks; now a per-candidate `LATERAL` probe). The D140 benchmark's
  own BM25 numbers were measured on the degenerate text.

### 8.2 Review r1 rerun at 3M chunks (2026-10-21) — diagnostic

These numbers are diagnostic: the public envelopes did not hydrate (fixed in
the harness after review r2), the 10% row predates the final 20,000-row
pushdown limit, and only BM25 chunk search was swept.

After the round-1 fixes (filter pushdown, per-candidate confirmation, fixed
query set from the generator's Zipf distribution), on an isolated VM
(n2-highmem-8, the branch image) over 3M chunks (60,000 lineages × 5 × 10,
half duplicated across versions, D134 metadata). Results are archived in
`plan/analysis/data/d142_sweep_3m_2026-10-21.json` (query hash
`c5d83714…`, corpus md5 `972a88f9…`).

Adapter-level comparison on the same corpus (BM25 chunks, k = 20, 20 queries):

| stratum | plain loop p50 / p95 ms | early stops | pushdown p50 / p95 ms | early stops |
| --- | --- | --- | --- | --- |
| 1% documents (6,000 eligible) | 3,915 / 4,528 | 4 `work_budget` | **618 / 827** | **0** |
| 10% documents (60,000 eligible) | 1,665 / 2,158 | 1 `work_budget` | 4,743 / 4,997 | 0 |

So the pushdown limit is 20,000 eligible rows; broad filters use the loop.

Public operation (`search_chunks`, 40 queries), adapter terminations:

| stratum (eligible current chunks) | p50 / p95 ms | terminations | truncation |
| --- | --- | --- | --- |
| current (600,000) | 389 / 2,330 | 39 `k_filled`, 1 `work_budget` | 2.5% |
| history | 253 / 438 | 39 `k_filled`, 1 `work_budget` | 2.5% |
| 0.1% documents (600, exact tier) | 328 / 422 | 40 `exhausted` | 0 |
| 1% documents (6,000, pushdown) | 816 / 3,071 | 17 `exhausted`, 23 `k_filled` | **0** |
| 4,990 rows (exact tier) | 1,434 / 2,195 | 40 `exhausted` | 0 |
| 5,010 rows (pushdown) | 684 / 2,582 | 18 `exhausted`, 22 `k_filled` | 0 |
| 10% documents (60,000, pushdown at 100k limit) | 4,883 / 6,406 | 40 `k_filled` | 0 |

(The 10% row predates lowering the limit to 20,000.) The public envelopes
were `known_empty` with every candidate dropped at hydration: the synthetic
seed carries no hydratable chunk bodies, so public latency includes
confirmation of every candidate but not result rendering. That is a harness
limit, not a product finding.

**Gate status.** Completeness now passes for the measured filtered strata:
no early stop in the 1% stratum (it was 25%). The latency half does **not**:
the retrieval design's interactive budget is p95 ≈ 300 ms, and at 3M chunks
unfiltered `current` measures p95 2.3 s and the 1% stratum 0.8 s
(adapter) to 3.1 s (public). A design-level answer for the owner, not
implemented: (1) keep the immutable `doc_id` on `chunk_search` (lineage
identity, not visibility — D94's objection was to copied visibility) so a
document filter becomes an index-scan predicate without enumeration or a
large parameter array; (2) the distinct-text proposal for duplicated
editions; (3) an explicit owner budget for filtered ranked search at the
target size. The 50M target and the semantic, claim, fact and context
channels were not swept.

### 8.3 Review r2 rerun at 300k chunks, final configuration (2026-10-22)

The final configuration (pushdown limit 20,000, `ef_search` 200,
per-statement deadline clamps) through the public `search_chunks` (BM25,
k = 20, 40 queries from the fixed query set), on an isolated VM over 300,000
chunks (6,000 lineages × 5 × 10, half duplicated, D134 metadata), with
public results hydrating. Archived in
`plan/analysis/data/d142_sweep_300k_2026-10-22.json`, per-query accounts
included.

| stratum (eligible current chunks) | path | p50 / p95 ms | k returned | early stops |
| --- | --- | --- | --- | --- |
| current (60,000) | loop | 152 / 263 | 40 of 40 | 0 |
| history | loop | 112 / 267 | 40 of 40 | 0 |
| 0.1% documents (60) | exact tier | 78 / 90 | 7 (fewer matches exist) | 0 |
| 1% documents (600) | exact tier | 235 / 351 | 18 | 0 |
| 10% documents (6,000) | pushdown | 600 / 859 | 37 | 0 |
| 4,990 rows | exact tier | 1,334 / 2,084 | 35 | 0 |
| 5,010 rows | pushdown | 505 / 780 | 35 | 0 |

Readings: no stratum stops early, and every query that has `k` matches
returns them. At 300k chunks the unfiltered strata meet the p95 ≈ 300 ms
budget; filtered strata above about 600 eligible rows do not. The exact tier
is slower than the pushdown on either side of its threshold (p95 2.1 s at
4,990 rows against 0.8 s at 5,010), so the threshold for document-filtered
sets is set too high; that is input to the open latency decision (review r2
P1-6), not changed here.

**Owner decision (2026-10-22, D142 amendment).** The scaling gate's latency
criterion is these measured, size- and stratum-specific budgets at 300k
chunks, each paired with completeness (0 early stops); multi-second filtered
reads are accepted for now; 3M and 50M are ungated until measured. The
≈ 300 ms goal moves to WP-RS.11 (immutable `doc_id` on search rows, re-tuned
exact-tier threshold; issue #508). These measurements are not a 300 ms pass
for the filtered strata.

## 9. Out of scope

- **Reranking.** A precise reranker (for example TypeSafe AI's Jev model,
  already analysed for adjudication in
  [jev_adjudication_analysis.md](jev_adjudication_analysis.md)) could reorder
  the top candidates after retrieval. It does not make retrieval faster and
  is a separate, later experiment; if adopted it may only reorder, never drop,
  evidence.
- Graph traversal (D98) and the D140 graph gate: unchanged.
- A new public parameter: none. `k`, filters and time scopes keep their
  meaning; only latency and the (now honest) truncation behaviour change.

## 10. Sources

All read 2026-10-21; links are pinned to immutable commits or tags.

- pg_textsearch, pinned revision `578ff529` (v1.3.1, 2026-06-23):
  [README, "Filtering with WHERE clauses"](https://github.com/timescale/pg_textsearch/blob/578ff529894992fb9e67cae4c69424e65c84868e/README.md#filtering-with-where-clauses).
- pg_textsearch release notes:
  [v1.4.0](https://github.com/timescale/pg_textsearch/releases/tag/v1.4.0) (2026-08-18),
  [v1.5.0](https://github.com/timescale/pg_textsearch/releases/tag/v1.5.0) (2026-10-01),
  [v1.5.1](https://github.com/timescale/pg_textsearch/releases/tag/v1.5.1) (2026-10-02).
- pg_textsearch v1.5.1 source at commit `fa142d049d08eac45a263f5abc250c8877ae076e`:
  [README, PostgreSQL version compatibility](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/README.md#postgresql-version-compatibility),
  [executor-scoped scoring snapshot](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/scoring/snapshot.c#L128-L215),
  [memtable BMW scorer](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/scoring/bmw.c#L438-L454),
  [standalone scorer](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/types/query.c#L959-L966),
  [fieldnorm table](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/segment/fieldnorm.c#L52-L69),
  [continuation and internal rescoring](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/access/scan.c#L579-L627),
  [cursor generation test](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/test/scripts/standalone_snapshot.sh#L305-L341).
- pgvector v0.8.6 (the pinned version):
  [README, iterative scan options](https://github.com/pgvector/pgvector/tree/v0.8.6#iterative-scan-options),
  [HNSW scan implementation](https://github.com/pgvector/pgvector/blob/v0.8.6/src/hnswscan.c#L247-L318).
- PostgreSQL 19, [repeatable read isolation](https://www.postgresql.org/docs/19/transaction-iso.html#XACT-REPEATABLE-READ).
