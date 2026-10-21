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
IDs. **In the common case** work grows with `k/s`, not with corpus size. That
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

BM25 nomination streams matching rows in exact score order within one scoring
generation (pg_textsearch uses Block-Max WAND for the leading top-k and grows
its batch afterwards). With positive-match semantics, continuation and the tie
rule, a BM25 result equals exact scoring of the same eligible matching rows
under that generation.

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
   generation**. Absent terms, empty analyzed queries, fewer-than-`k` matches
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
   0.90; LoCoMo answer accuracy, at least three runs per side on identical
   data, passes when the change's mean is not lower than `main`'s by more than
   the larger of one percentage point and the two-sided 95% confidence
   interval of the difference. The positive-match lexical change and the
   `pg_textsearch` upgrade are measured separately against today's answers.
   The LoCoMo part is a paid run and waits until the in-progress run has
   finished.

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

- `pg_textsearch` README at the pinned revision `578ff529` (v1.3.1, 2026-06-23)
  and at tags v1.4.0/v1.5.0/v1.5.1; release notes v1.4.0 (2026-08-18), v1.5.0
  (2026-10-01), v1.5.1 (2026-10-02), github.com/timescale/pg_textsearch,
  read 2026-10-21.
- pgvector README "Filtering" and "Iterative Index Scans",
  github.com/pgvector/pgvector, read 2026-10-21 (image pins 0.8.6; latest
  tag 0.8.7).
