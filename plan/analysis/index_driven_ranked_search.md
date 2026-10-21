# Analysis: index-driven ranked search (issue #504)

**Status:** non-binding analysis, 2026-10-21. It supports
[D142](../../decisions.md#d142-ranked-search-nominates-from-the-index-and-confirms-in-one-snapshot)
and the binding changes in
[the P1 search design](../designs/postgres_p1_search_projection_design.md) §8–§9.
**Issue:** [writeitai/remember-stack#504](https://github.com/writeitai/remember-stack/issues/504).
**Evidence:** [D140 analysis §11](version_effective_time_and_section_references.md#11-measured-the-time-scope-cost-2026-10-02)
and `benchmarks/d140_scope/results/2026-10-02/`.

## 1. The problem in one paragraph

Every ranked search on `main` — "find the chunks or claims most similar to
this query" — takes seconds and gets slower in direct proportion to how much
text the deployment holds. The engine has the right indexes: an HNSW index
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
vectors). Random vectors are a known worst case for HNSW, and a planner can
choose differently on real distributions. The check on a real deployment
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
   stops silently at `hnsw.max_scan_tuples` (the engine uses 20,000) — the
   caller cannot tell "no more matches" from "gave up".
4. **The pinned BM25 extension truncates post-filtered scans.** The image pins
   `pg_textsearch` 1.3.1 (revision `578ff529`, plus the D98 PostgreSQL 19
   compatibility patch). Its README at that revision says that with
   post-filtering "the index returns top-k results *before* filtering. If your
   WHERE clause eliminates most results, you may get fewer rows than
   requested." In other words, at 1.3.1 an index-ordered BM25 plan with our
   filters would be *wrong*, and a full scan is the only correct plan the
   planner has. Upstream fixed this: 1.4.0 (2026-08-18) adjusts the internal
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

### Option A — keep one statement and steer the planner

Rewrite each statement so an index plan becomes attractive: drop the
tie-break from `ORDER BY` (break ties in Python), turn view joins into
`EXISTS` probes, upgrade `pg_textsearch`, raise `hnsw.max_scan_tuples`, and
where needed `SET LOCAL enable_seqscan = off`.

- **For:** smallest diff; keeps "one statement" literally.
- **Against:** the outcome is still decided by cost estimates per query and
  per data distribution — the exact failure we are fixing. Coverage-first
  ordering still cannot use an index. `enable_seqscan = off` is a blunt
  instrument that also degrades the authority probes. Most importantly, both
  extensions report a capped scan exactly like an exhausted one, so the
  engine cannot meet the honest-truncation rule: a capped iterative scan
  returns fewer rows and nobody knows why.

### Option B — nominate from the index, confirm by primary key, refill (chosen)

Split each channel into two simple queries that the planner cannot get
wrong, run in a loop inside **one read-only `REPEATABLE READ` transaction**
(one snapshot for every round):

1. **Index query** — only the index and the deployment predicate:
   `SELECT id, score FROM chunk_search WHERE deployment_id = :d
   ORDER BY embedding <=> :v LIMIT :n` (or the BM25 operator). Nothing else in
   the statement, so the only sensible plan is the index scan.
2. **Confirmation query** — the existing eligibility predicates (views,
   D140 scope, documents filter, entity scope, fact gate) applied to
   `id = ANY(:candidate_ids)`. The adapter already supports a
   `candidate_ids` predicate on every ranked method; confirmation is a
   primary-key probe per candidate.
3. **Refill** — if fewer than `k` candidates are confirmed, fetch the next
   batch (double `n`) and confirm only the new IDs, until `k` are confirmed or
   the index has no more rows.

Because the snapshot is shared, every round sees the same database state;
because the index returns rows in score order and refill continues until `k`
eligible rows are found, the result equals "the best `k` eligible rows" —
the D87 rule — for exact indexes.

- **For:** planner-independent by construction; reuses existing predicates
  verbatim (one implementation, no parallel copy to drift — the lesson of
  D140 implementation review round 3); honest truncation is natural (the
  loop knows whether it stopped because the index ran out or because a work
  bound was reached); channels stay independent and can run concurrently on
  separate connections before RRF fusion.
- **Against:** more round trips per search (typically one or two rounds;
  §5 quantifies); the snapshot spans several statements, so the transaction
  isolation level becomes part of the contract; the open query space's
  "one statement" wording must become "one snapshot".

### Option C — copy visibility onto the search rows

Store "visible", "in force from/until", entity IDs, etc. on `chunk_search`
and the in-row embedding tables, so the filter is a plain column predicate
the index scan evaluates.

- **Against:** D94 explicitly rejected copied entity/filter scalars. D140
  periods are corrected retroactively and evaluated at arbitrary instants; a
  correction or a deletion would rewrite thousands of search rows, and every
  write path (ingest, D135 delete, D74 forget, entity merges) would gain a
  second place that must stay consistent. The index would still post-filter.
  Rejected.

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

## 5. How option B behaves — worked numbers

Let `k` be the requested result count and `s` the share of index candidates
(near the top of the ranking) that pass confirmation. One round with batch
`n` yields about `n·s` survivors.

| situation | `s` | rounds for k = 20, n₀ = 4k = 80 |
| --- | --- | --- |
| LoCoMo-style corpus, nothing deleted, no periods | ≈ 1 | 1 |
| a deployment with some deletions | 0.8 | 1 |
| law, `current`, paragraph unchanged in 3 versions | ≈ 0.33 | 1 |
| law, `current`, unchanged in 20 versions | ≈ 0.05 | 3 (80 → 160 → 320 → 640) |
| entity filter with a rare entity | ≪ 0.01 | exact scoring of the entity's items instead (§6.3) |

Each round costs one index query plus one confirmation query over the new
IDs. Work grows with `k/s`, not with corpus size — that is the property the
issue asks for. The bad case is high duplication, discussed in §7.

**Ties.** Two candidates with the same score must be ordered by stable ID.
An index returns equal scores in arbitrary order, so the loop must not cut
inside a run of equal scores: after reaching `k`, it continues until the
next candidate's score is worse than the `k`-th confirmed score. In versioned
corpora identical text produces long runs of equal BM25 scores — the same
duplication that §7 addresses.

## 6. The chosen design in detail (summary of the binding text)

### 6.1 Exact channels: BM25

`pg_textsearch` BM25 uses Block-Max WAND, which is an *exact* top-k
algorithm: the index returns the true best rows by BM25 score. The index
query and the full scan use the same index statistics, so scores are
identical. With refill-to-`k` and the tie rule, the final list is identical to
today's full-scan list. Requirement: upgrade the image to `pg_textsearch`
1.5.1 (the current release), which also removes the D98 patch; the image
gates already run the extension's SQL tests.

### 6.2 Approximate channels: HNSW

HNSW is approximate: it typically finds 95–99% of the true nearest
neighbours, tunable with `hnsw.ef_search` and iterative scans. Today's
semantic search is *accidentally exact* because the planner scans
everything. Moving to the index therefore trades a measurable, small recall
loss for speed. That is the standard trade in every vector store, but it can
change which evidence an answer sees, so it gets an explicit quality gate
(§8). The index query runs with iterative scan in `strict_order` and a
per-round `hnsw.max_scan_tuples` sized to the round's need within the
operation's work budget; if an index query returns fewer rows than asked
while the budget is spent, the result is disclosed as truncated, never as
exhausted.

### 6.3 Entity-scoped nomination (coverage first)

Entity-scoped statements rank by "how many requested entities this item
mentions" first, then by score. No text index can produce that order, but the
eligible set is defined by mentions, which are indexed by entity. The design:

1. Compute coverage for every scoped item from the mention tables (counts
   only, no scoring) — bounded by an entity-mention budget.
2. Take coverage tiers from the highest down. If a tier is small (below a
   measured threshold, starting point 5,000 items), score its items
   exactly with the standalone operators (`<=>`, `<@>`) and sort — no index
   needed, exact by construction.
3. If a tier is large (a hub entity), run the option B loop restricted to the
   tier's members (membership becomes part of confirmation).

`nominate_testimony_scored`'s survivor resolution and coverage rules are
unchanged; D140's `scope_pending` probe continues to reuse the same
nomination with pending lineages widened, so the two cannot drift.

### 6.4 Facts

`search_facts_scored` and `nominate_facts_scored` run one branch per fact
table (relations, observations), each on its own HNSW index. Each branch
becomes an option B loop whose confirmation applies the fact authority view,
the fact-time predicate, the entity predicate and the D140 evidence gate; the
branches merge by distance as today.

### 6.5 Snapshot and isolation

All rounds of one ranked invocation run in one `READ ONLY` transaction at
`REPEATABLE READ`. PostgreSQL takes the snapshot at the first statement; every
later round sees exactly that state, and read-only `REPEATABLE READ`
transactions cannot fail with serialization errors. That snapshot is the D48
linearization point that the open query space reports as `pg_confirmed_at`.
The D97/D140 operation deadline and the bounded read admission pool still
govern the transaction.

### 6.6 Where it lives

One adapter-level helper in `postgres_p1.py` implements nominate → confirm →
refill for a channel. The existing ranked methods keep their signatures and
their predicate builders; their SQL splits into an index query and a
confirmation query. The open query space bridge already calls these methods,
so the SRFs (`semantic_chunks`, `lexical_chunks`, …) inherit the change.

## 7. Duplicate text in versioned corpora

D140 creates one chunk row — and therefore one `chunk_search` row with its own
text and embedding — per document version, even when a paragraph is
unchanged. D140's reuse only avoids re-extracting claims. In a corpus where a
paragraph stays unchanged across 20 versions, its text sits in each index 20
times. With option B this costs in three ways:

1. **Refill depth:** for `current` queries, roughly one in 20 near-top
   candidates is visible (§5), so the loop needs several rounds.
2. **Tie runs:** the 20 copies tie on BM25 score; the tie rule must read
   all of them before cutting.
3. **Index size:** the index holds up to 20 entries for that text. (The
   embedding *call* is not repeated: E1 already carries a vector forward from
   the previous version when the chunk's content hash and embedding-input
   hash match — D80, `workers/e1.py` "cross-version carry-forward". The
   duplicate is in index rows, not in provider spend.)

The remedy is to index each **distinct indexed input once** and keep the list
of chunks (and so versions) that carry it. A search entry is visible if any of
its versions is in scope; the hit is mapped back to the version D140's rules
select. Chunk rows stay per version (they anchor citations, positions,
occurrences, navigation and deletion). Nothing is lost by construction: the
same text in every version is reachable through the shared entry. The
mechanism is fully specified in
[the distinct-text search index proposal](../proposals/distinct_text_search_index.md).

**Is it needed?** That depends on how much duplication realistic queries
meet, which is unknown until we have a real versioned corpus. The LegalIt
pilot (20–50 heavily amended Czech acts with all versions) will measure the
**effective duplication factor**: for realistic `current`-scope queries, the
number of index candidates examined per result returned. Decision rule
(starting points, to be confirmed by the owner):

- median effective factor below 5 and p95 latency within budget → option B
  alone; the proposal stays unchosen;
- median factor 5 or more, or p95 out of budget at pilot scale extrapolated
  to the full corpus → adopt the proposal as part of #504 delivery.

**Embedding reuse is already implemented** for the case that matters here.
E1's embed stage copies the previous version's vector when an unchanged chunk
has the same content hash and the same embedding-input hash (D80). It can
miss when the embedding input changes although the body does not — for
example a D80 location header that names a section path renumbered by an
amendment. The pilot checks how often that happens on real law (count
embedding calls when a second version of an act is ingested). A miss rate
worth fixing would be handled in E1/D80, independently of #504.

## 8. Verification

1. **Real-data plan check** (first): `EXPLAIN (ANALYZE, BUFFERS)` for each
   ranked statement on a copy of a real deployment, after the current LoCoMo
   run, to confirm the causes in §2 and catch any others.
2. **Equivalence tests** (exact channels): for randomized corpora with
   deletions, periods, entity scopes and ties, option B's BM25 results equal
   the full-scan results row for row.
3. **Adversarial tests:** a filter that rejects almost all top candidates
   (refill reaches `k`), an exhausted index (no truncation flag), a reached
   work bound (truncation flag with counters), tie runs across a batch
   boundary, a deletion committed between rounds (invisible: the snapshot
   predates it), hub-entity tiers, the D140 gate and `scope_pending`.
4. **Scaling benchmark:** extend `benchmarks/d140_scope` into a size sweep
   (30k → 300k → 3M chunks, then the 50M target on a short-lived VM if the
   sweep is flat) for each channel and for `facts_context`, `main` against the
   change. Pass: p95 roughly flat as size grows (sublinear), and within the
   §10 interactive budget at the target size.
5. **Quality gate for HNSW:** recall@k of the HNSW path against exact search
   on sampled real queries, and LoCoMo answer accuracy on `main` versus the
   change with identical data. A drop beyond run-to-run noise blocks the
   change until `ef_search`/iterative-scan tuning closes it. This is a paid
   run; it waits until the in-progress LoCoMo run has finished.

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
