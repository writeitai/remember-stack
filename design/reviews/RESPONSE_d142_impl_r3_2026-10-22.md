# Response — D142 implementation review, round 3 (GPT-6.1 Sol)

Review: `REVIEW_gpt-6.1-sol_d142_impl_r3_2026-10-22.md`. Everything from
round 2 is closed except one P2 and four nits, fixed below. **R2 P1-6 (the
latency gate) stays open for the owner** and was not touched. Rule 4 holds:
nothing here changes chunking, reuse keys or extraction/embedding inputs.

## P2-1 — budget errors at the SQL bridge

One classifier, `budget_exhausted` (query sandbox `nomination.py`), walks the
exception chain, unwrapping SQLAlchemy's `DBAPIError.orig`, and recognises
SQLSTATE 57014 (`statement_timeout`), 25P04 (`transaction_timeout`) and a
bounded-admission timeout. The bridge applies it before its generic catches
on every path: nomination (was `p1_unavailable`), confirmation and chunk
coordinate confirmation (were `confirmation_failed`) and projection body
reads (were `p1_unavailable`); each answers the sandbox's existing
`statement_timeout` failure. The executor's own catch now covers
`TransactionTimeout` alongside `QueryCanceled`. Genuine failures keep
`p1_unavailable` and `confirmation_failed`.

Tests (`src/tests/surfaces/test_query_sandbox_budget.py`, database-free,
through the public `QuerySandboxExecutor.query_sql` with a stand-in
transaction): raw and SQLAlchemy-wrapped 57014 and 25P04 during nomination,
57014 and 25P04 during confirmation, all four during a body read, and a
non-budget control for each genuine code. Nine of the eleven fail on the
previous code.

## Nits

1. Design §9.1's planner contract now states `cursor_tuple_fraction = 1` and
   `plan_cache_mode = force_custom_plan`, why (a cursor is planned for its
   first fraction of rows, the attested `SELECT` for all of them; with 1 they
   plan alike, the `LIMIT` already bounding the rows), and that restoration
   happens only on a healthy transaction.
2. The sweep's module description lists exactly what it emits (public
   outcomes, per-query accounts, the production statement's plan without
   `ANALYZE`, hashes) and says index-internal execution work is not measured.
3. The owner-invariant regression requires every Selection and every Claimify
   call to map to exactly one chunk and compares a `Counter` of the called
   chunk ids with the expected rerun ids (new extraction identities, new
   Claimify identities) — in the passing living/dated test, outside the
   narrow expected failure.
4. `nominate_and_confirm` validates the BM25 margin's domain from the
   analyzed term count before it opens any cursor; a query beyond it raises
   `ValueError` up front. The docstring says so (and that capped public
   requests never reach it).
