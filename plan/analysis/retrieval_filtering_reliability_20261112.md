# Reliable retrieval: SQL filtering, ranked indexes, and continuation

Date: 2026-11-12. Status: non-binding investigation in progress.

This analysis investigates retrieval failures in the full LoCoMo run based on
`13ce4a5224709b3e3cda65c429d5915d47e95e89`.
The exact source revision and measured artifacts are recorded below as the
investigation proceeds. This document does not change D142, production SQL,
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

## Alternatives to measure

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

## Measurement plan and limitations

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

Measured findings and the final recommendation will be added in subsequent
commits to this PR. Existing relevant analyses are
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
