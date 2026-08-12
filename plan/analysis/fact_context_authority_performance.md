# Fact-context authority and latency analysis

**Status:** non-binding implementation analysis  
**Date:** 2026-08-11  
**Scope:** the PostgreSQL confirmation and evidence reads used by the
`fact_context` assured operation

## Problem

The first full LoCoMo v12 answer pass against revision `d72a3399` exposed an
operational failure rather than an ingest failure.  Several concurrent
`fact_context` calls reached the client transport timeout while PostgreSQL kept
executing their expanded `memory_v1.facts_visible_history` plans.  Fifteen such
queries occupied the whole API pool, after which unrelated retrieval calls
failed waiting for a connection.

This path must retain the accepted contracts:

- `fact_context` confirms every P1 nomination against PostgreSQL and returns
  composite `(fact_kind, fact_id)` identities, as specified by
  `plan/designs/open_query_space_design.md` under **Eligibility precedes bounded
  ranking**.
- retained assured operations consume `memory_v1` as their invariant authority
  rather than rebuilding D41, D48, or D54 from base tables (`decisions.md`,
  D83).
- D54 counts one current-testimony document lineage per fact and stance;
  `memory_v1.evidence_lineage` is its sole public counting relation
  (`plan/designs/open_query_space_design.md`, the `evidence_lineage` contract).

## Production evidence

Read-only measurements used the already-ingested LoCoMo store.  No benchmark
records or source data were changed.

| Query shape | Observed wall time |
| --- | ---: |
| Existing `facts_visible_history`, one exact fact | exceeded 30 seconds |
| Existing `fact_claim_evidence_live`, 15 exact facts | about 42 seconds |
| Exact membership through `v_memory_fact_visible`, 30 facts | about 3.4 seconds |
| Direct current evidence association for 15 facts | about 0.27 seconds |
| Shared-helper prototype, full history rows for 30 facts | about 8 seconds |
| Shared-helper prototype, representative evidence for 15 facts | about 5.5 seconds |

The existing history view expands `v_memory_fact_visible`, then expands the
public evidence view, which expands the same fact-visibility authority again.
That repeated authorization tree is the structural cause.  Planner settings
alter the severity but do not make the old shape safe: the exact one-fact case
still exceeded the interactive transport budget.

## Alternatives considered

### Reconstruct evidence and support state in `QueryEngine`

This is fast because indexed base association tables can be read directly, but
it creates a second implementation of D54 and support-state rules.  It violates
D83 and can drift from open SQL, saved queries, and future fact consumers.  It
is rejected.

### Keep the views and change planner settings only

Disabling nested loops and JIT improves some plans but leaves the repeated
authorization expansion intact.  It also makes latency highly dependent on
cardinality and cache state.  It is rejected as an incomplete fix.

### Factor the repeated work into private PostgreSQL authorities

Create two ungranted private views: one for a current claim-to-fact association
and one for its fact × document-lineage × stance aggregation.  The existing
public `fact_claim_evidence_live`, `evidence_lineage`, and
`facts_visible_history` views retain their names, columns, and meanings, but
join the fact-membership authority only once.  `QueryEngine` continues to read
the public `memory_v1` contract.  This is the chosen implementation because it
is the smallest option that is both fast and single-sourced.

## Runtime bound and failure behavior

Fact confirmation will retain bounded batches for predictable plans, but all
statements executed while one pooled connection is held share one monotonic
operation deadline.  Before each statement, PostgreSQL receives only the
remaining time as `statement_timeout`.  Exhausting that budget fails the call
and releases the connection; it cannot multiply a transport timeout by the
number of candidate batches.

Candidate enumeration may use indexed base coordinates only to nominate a
bounded set.  Exact membership, time, survivor, evidence, and contradiction
results are still confirmed by PostgreSQL authorities before anything is
returned.  A migration downgrade restores the prior public definitions and
drops the two private helpers.  The public API and the 24-view query-space
surface do not grow.

## Validation required

- Fresh PostgreSQL migration upgrade, downgrade, and re-upgrade.
- Existing D41, D48, D54, coordinate-collision, and manifest gates.
- Explicit proof that both helpers have no `PUBLIC` or query-role grants.
- Focused assured-operation tests, including one operation-level deadline test.
- Read-only production-plan timing before resuming the paused benchmark.

