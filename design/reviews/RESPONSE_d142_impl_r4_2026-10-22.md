# Response — D142 implementation review, round 4 (GPT-6.1 Sol)

Review: `REVIEW_gpt-6.1-sol_d142_impl_r4_2026-10-22.md`. One item remained:
R3 P2-1 at two more bridge handlers. **R2 P1-6 (latency) stays open for the
owner.** Rule 4 holds; no LoCoMo; no VM.

## R3 P2-1 — the remaining handlers, and a sweep for any others

- `_shared_snapshot` (`pg_export_snapshot`) and `_pinned_generations` (current
  chunk generation) now classify with `budget_exhausted` inside their catch
  and raise `budget_rejection` — the sandbox's `statement_timeout`, original
  cause kept — before the ordinary `p1_unavailable` / `confirmation_failed`
  mapping.
- Sweep of every broad catch in the query sandbox (bridge, executor,
  nomination and the rest of the package): the bridge's nomination,
  confirmation, coordinate and body handlers were already classified (r3);
  the executor's `_type_name` fallback, which turned any `format_type` error
  into a plain OID, now re-raises a budget error so the request answers
  `statement_timeout` rather than continuing in an aborted transaction; the
  executor's own handlers catch 57014/25P04 before `OperationalError` and the
  generic `psycopg.Error`; `nomination.py` has no broad catch; no other module
  has one.

Tests (`src/tests/surfaces/test_query_sandbox_budget.py`, database-free,
through `QuerySandboxExecutor.query_sql`): 57014 and 25P04 while exporting the
snapshot (a search double that really exposes `imported_snapshot`) and while
reading the current generation (`semantic_chunks`), each answering
`statement_timeout`; non-budget controls (`UndefinedColumn`) keep
`p1_unavailable` on the export path and `confirmation_failed` on the
generation path, pinned to those paths by their messages. The four budget
cases fail on the previous bridge.
