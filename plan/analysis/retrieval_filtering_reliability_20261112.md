# Reliable retrieval: SQL filtering, ranked indexes, and continuation

Date: 2026-11-12. Status: non-binding analysis; SQL prototypes only.

This analysis investigates retrieval failures in the full LoCoMo run based on
`13ce4a5224709b3e3cda65c429d5915d47e95e89`.
The diagnostic runtime uses main merge `e8c1b0468dc8ad7d07298bc391d5539dd748ac00`;
the three core retrieval files are byte-identical between those revisions.
This document does not change D142, production SQL,
benchmark questions, model settings, or result labels.

## Problem and owner priority

The owner asks whether repeatedly fetching more index candidates to replace
ineligible rows is unnecessarily fragile. Could SQL `WHERE` clauses do the
filtering first, while retaining vector and text indexes where they help?
Reliability takes priority over speed. The goal is to return eligible,
relevant evidence consistently, and distinguish an incomplete search from
an authoritative absence of evidence.

The active publication run has two terminal retrieval timeouts, but many
more tool calls returned a structured deadline boundary before the answering
agent recovered through another tool. A correct final answer does not make
those empty searches healthy. Conversely, a deadline on one compound tool
call does not prove that one SQL statement ran for the full deadline.

## What the current implementation actually does

`src/rememberstack/adapters/p1_ranked.py:nominate_and_confirm` opens one
index-ordered server-side cursor, fetches disjoint batches, confirms each
batch against SQL eligibility rules, and continues if too few eligible rows
remain. It does not rerun the search from its beginning. Confirmation occurs
inside the server before results reach an agent; this is not an agent-side
filter that exposes ineligible data first.

The loop is bounded by candidate work, a deadline, index-scan bounds, and
explicit termination reasons. It can still amplify work substantially:
`work_budget(k) = max(20_000, 200*k)`. A public request for 50 results with
`candidate_k=200` can pass 201 to an inner nomination branch, allowing
40,200 fetched candidates in that branch. The initial batch is four times
the inner request, then doubles. The configured candidate pool is not a
cap on every row examined by SQL or an index.

The engine already has several paths, not one universal refill algorithm:

| Request shape | Current mechanism | Investigation implication |
| --- | --- | --- |
| Facts with explicit entity IDs | Direct scoped SQL with `WHERE`, authority/evidence gates and distance ordering | Bypasses the generic continuation loop |
| Unscoped facts | Per-fact-table index nomination and SQL confirmation | Continuation can amplify work |
| Claims/chunks with a small enumerated scope | Exact scoring of eligible IDs | Filter-first strategy already exists |
| Intermediate document scope | Eligible IDs pushed into nomination `WHERE` | Index and filtering already combine |
| Broad claims/chunks scope | Index nomination followed by SQL confirmation | Selectivity and confirmation plans matter |

Both terminal conv-26 failures supplied an entity ID to `facts_context`.
Their fact search therefore uses the first row of this table. Blaming those
particular timeouts on the refill loop would contradict the current source.
Later claims/chunks calls can use different paths and need separate tracing.

Source anchors: `postgres_p1.py:nominate_facts_scored`, `_run_testimony`,
`_pushdown`, `_require_fact_scope_budget`; `query_engine.py:facts_context`,
`_nominate_facts_context`, `_CONFIRM_CONTEXT_ENTITIES`,
`_CONFIRM_FACTS_CONTEXT_BY_KIND`, `_operation`.

## Results from the retained stores

The [machine-readable measurements](retrieval_filtering_20261112/measurements.json)
and [artifact/reproduction notes](retrieval_filtering_20261112/README.md)
contain the complete statement captures, plans, SQL prototypes and receipts.
The [GCS receipt](retrieval_filtering_20261112/gcs-receipt.json) records an
immutable archive of evidence checkpoint `c813c9230`, independently downloaded
and verified against all 108 manifest entries. This analysis archive is
separate from the campaign's full-store backups; its receipt was added after
archival and is deliberately outside that archive's file manifest.
The following are SQL-only observations on conv-26, using a frozen stored
vector. They are not newly generated benchmark answers or question-embedding
recall measurements. Each diagnostic operation has a 20-second bound. A
reported timeout means **greater than that bound**, not a completed timing.

| Read | Observed client time | What actually happened |
| --- | ---: | --- |
| Entity-scoped `facts_context`, k=50/candidate_k=200 | 20.02 s, incomplete | No continuation calls; direct scoped nomination consumed the deadline |
| Entity-scoped `claims_and_sources_context`, same limits | 20.02 s, incomplete | No continuation calls; exact nomination was fast, final claim hydration stalled |
| Unscoped claims/chunks, same question and vector | 1.00 s | Four index channels, each one fetch round; returned 50 claims and 50 chunks |
| Unscoped facts | 20.02 s, incomplete | One observed relation confirmation batch, containing 36 IDs, consumed the deadline |

In the fast unscoped claims/chunks operation, the semantic claim channel
fetched 711 IDs, lexical claims 404, semantic chunks 163, and lexical chunks
130. All four recorded zero rejected IDs and `exhausted`. There was no refill
tail to remove. This is evidence about this store and query, not a general
claim that continuation is always cheap or necessary.

The scoped claims capture reached a final hydration batch containing 256
claim IDs. Its earlier exact nomination statements took approximately
0.04–0.05 seconds. The final query already contains deployment, candidate-ID,
current-testimony, version/time, source visibility and entity predicates.
It is the physical execution of those predicates that is expensive.

### The repeated work is visible in EXPLAIN ANALYZE

Restricting that **same native hydration SQL** to 1 and 16 candidate IDs gives:

| Native hydration subset | Client execution | EXPLAIN execution | Root shared-buffer hits | Entity-visibility evaluations |
| --- | ---: | ---: | ---: | ---: |
| 1 claim | 1.02 s | 1.34 s | 1,904,074 | 1 |
| 16 claims | 15.35 s | 19.48 s | 30,259,743 | 16 |
| Full 256-ID batch | >20 s | >20 s | Not available after cancellation | Not measured to completion |

These are separate execution and instrumented-plan runs, not two components
to add together. Buffer hits count repeated page accesses, not unique pages
or disk reads; the completed plans reported zero shared blocks read from
storage. The 16-claim plan runs its coverage aggregate 16 times. Its nested
`entities_current` lookup averages 1,188.576 milliseconds per evaluation.
The database is repeatedly establishing the same requested entity's source
visibility while checking different claims.

Even the separate identity-only check is expensive: one captured lookup
of the requested entity takes 1.31 seconds under EXPLAIN, with 1,838,700
shared-buffer hits. A nested chunk/provenance branch executes 70,301 times.
Thus factoring the work out of each claim helps substantially, but does not
make the underlying entity-visibility query healthy at scale.

The direct scoped fact nomination expands to a 1,199-node estimated plan;
its top-level estimate is only two result rows. It has no HNSW scan. The
claims hydration estimated plan contains 231 nodes and estimates one row
for a batch that can return 256. A plan's size is not itself a correctness
failure, and estimated cost units are not milliseconds. Here they help
locate repeated authority/provenance expansion; the completed smaller actual
plans establish that repeated work really occurs.

### Prototype comparison: share eligibility work within the request

Two simple lookup-boundary prototypes were insufficient: putting the fact
authority view behind a per-fact `LATERAL ... OFFSET 0` lookup, or putting
mention authority behind per-mention lookups, still exceeded 20 seconds.
The PR preserves those negative results as well as the successful experiment.

A second claims prototype calculates the requested visible entities **once**
in a materialized CTE. It joins current-content mentions to that set and
aggregates coverage for all candidate claims together. It retains the current
source-coordinate, claim visibility and entity visibility semantics: the
existing `mentions_live` view is current-content mentions left-joined to
`entities_current`; filtering its resolved entity IDs makes that left join
equivalent to joining the filtered visible entity set.

| Same candidate IDs | Native SQL | Materialized shared-coverage prototype | Value comparison |
| --- | ---: | ---: | --- |
| 1 | 1.02 s | 1.33 s | All returned column values hash identically |
| 16 | 15.35 s | 1.02 s | All returned column values hash identically |
| 256 | >20 s | 1.16 s | 256 rows returned; full native comparison did not complete |

The prototype's 256-row actual plan evaluates entity visibility once, executes
in 1.39 seconds, and records 1,933,475 shared-buffer hits. The 16-row prototype
records 1,863,886 hits instead of 30,259,743. This supports eliminating repeated
eligibility work, rather than removing eligibility itself.

This is not a production-ready rewrite: equality on the completed subsets
does not qualify all deletion, merge, historical-time and provenance cases.
The diagnostic uses the current internal view definition deliberately; a
runtime implementation must share authoritative SQL builders/contracts rather
than maintain a second independent interpretation of visibility.

### Independent affected conversation

The same diagnostic on conv-30 selected the first recorded current-time,
entity-scoped deadline for each tool (`qa/0005` facts and `qa/0025` claims),
without using answer correctness or gold labels to choose the cases.
Both native operations exceeded 20 seconds, again with **zero continuation
calls**. The full 256-ID shared-coverage prototype completed in 1.17 seconds.
Native and prototype runs restricted to the same first claim each completed
in approximately 1.00 second and produced identical complete row hashes.

This repeats the work-amplification pattern in a different conversation and
question topic. It does not establish universal historical-scope equivalence
or a full-batch original/prototype comparison: the original full hydration
again did not finish inside the diagnostic bound.

## What SQL filtering does and does not guarantee

A `WHERE` clause defines which rows may qualify. It does not force PostgreSQL
to evaluate the filter before all other expensive work. Views, joins, and
common table expressions can be rearranged by the planner. Nor does a `WHERE`
clause make approximate vector search exact: pgvector documents that HNSW
filtering occurs after its index scan, potentially returning too few matches.
Its iterative scan facility also continues scanning to find more matches;
moving continuation into an extension does not remove that underlying issue.

An explicit eligible-row boundary followed by exact distance scoring can
make membership and top-k by distance auditable for a bounded scope. A
materialized CTE is one possible boundary, with memory, temporary-file and
full-scope work costs. Materializing a broad corpus indiscriminately can
make performance worse. SQL plans and actual row counts are necessary to
tell these cases apart.

The current D142 implementation was introduced because a combined filter,
join and distance query could score and sort the full corpus instead of
using the intended ranked index. Abandoning it everywhere without replacing
that protection would recreate a known scaling failure.

## Alternatives and their tradeoffs

1. **Continue as today, with better measurement.** Keep index nomination,
   bounded confirmation, and explicit early-stop reporting. Measure rejected
   candidates, fetch rounds, SQL amplification and recall. This preserves
   scale behavior, but retains significant continuation and score-bound logic.
2. **Filter first and rank exactly.** Obtain the complete eligible set through
   relational indexes, then compute vector distance or lexical scores and
   select top-k. This gives an exact reference for the chosen metric, but work
   grows with the eligible scope, which can be the whole project.
3. **A reliability-first hybrid.** Apply selective relational predicates at
   their owning tables, use exact ranking for bounded eligible scopes, and
   retain ranked indexes for broad scopes. Treat an incomplete approximate
   search explicitly; an exact completion/verification operation must be
   separately bounded and accounted for, not a silent arbitrary refill.

Exact ordering by embedding distance is not proof of semantic relevance.
Eligibility correctness, search completeness, ranking recall and operational
completion are separate properties. The comparison must preserve all source
visibility, deletion, model/policy generation, entity and temporal rules.

## Reliability-first recommendation

Combine relational filtering and vector/text ranking, with an explicit
boundary between finding eligible IDs and ranking them. Do not remove every
continuation path as a response to these particular failures: the failing
scoped operations do not use it, and the successful index operation required
only one fetch round per channel. Also do not treat the current implementation
as satisfactory merely because its loops have finite limits.

The immediate implementation candidate is **set-based, request-local
eligibility work**. Compute shared entity/source visibility once under the
existing repeatable-read snapshot and pinned time. Restrict claim, chunk or
fact IDs through selective relational predicates, then join the common
eligibility result. Preserve independent subject/object eligibility for
relations. The successful coverage prototype demonstrates the direction;
the underlying entity-provenance plan and fact paths still require their own
measured fixes.

For ranking, distinguish two request shapes:

- **A bounded eligible scope**, such as facts about one person or chunks in
  selected document versions: use relational indexes to establish the scope,
  apply complete eligibility, then rank the complete eligible set by vector
  distance and/or the canonical lexical score. There is no reason to repeatedly
  nominate from unrelated project rows when the eligible set is cheaply known.
- **A broad eligible scope**, potentially millions of rows: retain HNSW/BM25
  acceleration and push applicable scalar/membership filters into nomination.
  Confirm IDs using the same eligibility rules and shared request-local work.
  Any continuation must be one bounded scan with an explicit result account.
  For a requested exact result, use a separately admitted exact eligible-scope
  computation or report that it could not complete. Never silently present an
  approximate or truncated search as proof that no matching evidence exists.

Vector search and SQL indexes are not competing sources of truth. A B-tree
can find a person's candidate fact IDs, then vector distance can rank those
facts exactly within that scope. HNSW is itself a PostgreSQL index that avoids
scoring every vector in broad searches. Running both and unioning their first
few results does not, by itself, prove completeness or correct ranking.

The present `k_filled` result describes an ordered nominated stream; with
HNSW it does not prove that no closer row exists outside the approximate
search. Eligibility correctness, top-k completeness in the scored set, and
semantic recall need distinct evidence. Keep a filtered exact SQL oracle for
recall comparisons, including scopes with many excluded rows.

### Concrete delivery and acceptance criteria

1. **Expose where work goes.** Record branch choice, pool admission, embedding,
   SQL execution, fetch rounds, fetched/confirmed/rejected IDs, confirmation
   batches, hydration and early-stop reason. Preserve the distinction between
   a deadline, pool wait, exhausted scope and approximate-scan bound. The
   diagnostic scripts here provide investigation evidence, not shipped telemetry.
2. **Remove repeated eligibility work.** Generalize the shared-coverage query
   through one maintained authority contract. Inspect and bound the underlying
   entity-provenance query too: once per request is better than once per claim,
   but its measured 1.8 million cached page accesses remain excessive.
3. **Make selective filters control physical work.** Measure materialized
   eligible-ID sets and correlated primary-key probes. For fact relation
   subject/object filters, compare separately indexed endpoint branches with
   the current joined `OR` shape, preserving deduplication and coverage order.
   Adding more `WHERE` text without inspecting the plan is insufficient.
4. **Use exact scoped ranking and indexed broad ranking deliberately.** Base
   thresholds on rows/provenance examined, temporary storage and concurrency,
   not LoCoMo names or particular questions. The current 5,000/20,000/50,000
   thresholds are existing starting points, not newly validated universal limits.
   Compare against exact eligible top-k and explicitly report incomplete work.
5. **Qualify before adopting.** Check identical complete values and membership
   for current/history time, superseded facts, merged/forgotten entities,
   deleted sources, changed document readings, and multi-source facts. Test
   against growing unrelated data, highly selective and broad scopes, ties,
   eight concurrent readers, pool saturation and cancellation. Require bounded
   work per candidate/scope in actual plans, not fragile assertions about the
   planner choosing one particular node name.

This can be implemented without changing ingestion, embedding dimensions,
extracted claims, fact adjudication or paid-product reuse. If accepted work
changes public view definitions, follow the existing query-space manifest and
saved-query revalidation contract. Use a new answer-run identity after a
merged fix; keep the original run and all its answers/grades intact.

### Cost, operation and failure consequences

The investigation makes zero provider calls. Its prototypes increase no
ingestion bill and run only in read-only transactions on retained stores,
one diagnostic reader at a time. Their use of temporary materialization
consumes PostgreSQL CPU/memory and can spill to disk; materialize IDs and
shared eligibility inputs rather than complete text/vector rows by default.
Exact ranking still grows with the eligible set. For example, 50,000 vectors
of 1,536 dimensions contain about 76.8 million scalar components to score;
relational filtering does not make that free or constant time.

No new per-customer price or compute saving is established by these timings.
A production choice needs large-scope and concurrent CPU/memory measurements.
Do not replace visible timeouts with an unbounded exact scan or a hidden
sequence of retries. If available work is insufficient, retain an explicit
incomplete result and a usable diagnostic cause. Returning only checked rows
remains mandatory, including after deletion or a document-version change.

Request-local materialization avoids cross-request invalidation machinery.
A persistent eligibility projection could also help, but would need atomic
updates for deletion, reading changes and merge/supersession; stale flags would
be a correctness regression. That larger change is not justified by this
investigation alone.

## Measurement method and limits

Use retained, fully ingested stores, one read-only diagnostic at a time.
Capture the actual native SQL and its parameter shapes, branch selection,
cursor fetch/confirmation counters, and bounded PostgreSQL JSON execution
plans. Separate admission, embedding, SQL and hydration time. Freeze a stored
1536-dimensional vector for SQL-only comparisons and label it as a diagnostic
vector, not the original question embedding or a new scored answer.

No ingestion, schema migration, index rebuild, `ANALYZE`, provider call, or
production setting change is part of this investigation. Do not warm and
time several strategies without disclosing order and cache effects. Plans
that time out are censored measurements, not completed timings. Concurrent
campaign activity prevents a controlled throughput claim.

Measurements are single observations, not medians or a controlled throughput
study. Original execution precedes the prototype; cached pages and EXPLAIN
instrumentation affect absolute timings. The 16-row original plan's repeated
entity checks and the prototype's single check are structural evidence beyond
the wall-time difference. The native full-batch timeout prevents a complete
original/prototype equality claim. Broad recall, historical scopes and a new
end-to-end answer score are not measured here.

Both affected answer-state hashes remain unchanged after each diagnostic, and
retained PostgreSQL containers are restored to their prior stopped state.
The active campaign keeps its source, budgets and models. Completed stores
remain available for answer/judge replays; at the start of this analysis six
conversations had completed ingestion and verified scoring-base backups, while
four still needed completion. This PR does not claim all ten were ingested.

Existing relevant analyses are
[the original index-driven design analysis](index_driven_ranked_search.md),
[the November 6 deadline investigation](retrieval_database_deadline_20261106.md),
and [the November 6 work-amplification fixes](retrieval_work_amplification_20261106.md).
Those fixes already exist in the inspected source; they are not new proposals.

## External sources

Retrieved 2026-11-12; primary documentation only:

- [PostgreSQL 19: Using EXPLAIN](https://www.postgresql.org/docs/19/using-explain.html)
  — plan trees, actual work, JSON and buffer instrumentation.
- [PostgreSQL 19: WITH queries](https://www.postgresql.org/docs/19/queries-with.html#QUERIES-WITH-CTE-MATERIALIZATION)
  — CTE folding and the consequences of forcing materialization.
- [pgvector: filtering and iterative scans](https://github.com/pgvector/pgvector#filtering)
  — relational filter indexes, approximate filtering and bounded continuation.
