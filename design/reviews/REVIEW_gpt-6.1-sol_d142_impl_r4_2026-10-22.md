# D142 implementation review, round 4 — PR #505

Reviewed `origin/main...HEAD` at **9ad1451bd6f4de5459ff3283f31c4c615225dfb2**, focusing on `156a9eb6c..HEAD`. Read AGENTS.md → CLAUDE.md, including Rule 4, the design-corpus skill, and the R3 review/response. P1-6 is excluded from reassessment as instructed.

## Closure audit

| R3 item | Status | Code and verification |
| --- | --- | --- |
| P2-1 — SQL-bridge budget-error classification | **Partly closed; still open** | The classifier in [nomination.py:870](/Users/jpuc/code/moje/remember-stack-504/src/rememberstack/surfaces/query_sandbox/nomination.py:870) correctly recognizes raw/wrapped 57014 and 25P04 and bounded-admission timeouts. Nomination, ordinary confirmation, coordinate confirmation, projection body reads, snapshot-import entry and final SQL execution now produce `statement_timeout`. Genuine projection/confirmation failures retain their codes. However, snapshot **export** and current chunk-generation lookup still intercept both SQLSTATEs before classification; see P2-1 below. All 11 committed budget tests pass, but their search double has no `imported_snapshot`, and their body cases call `_chunk_texts` directly. They do not exercise the two missed handlers. |
| Nit 1 — planner contract | **Closed** | [Design §9.1:590](/Users/jpuc/code/moje/remember-stack-504/plan/designs/postgres_p1_search_projection_design.md:590) now specifies `cursor_tuple_fraction=1`, `plan_cache_mode=force_custom_plan`, the attestation rationale, restoration of caller settings on healthy transactions, and rollback after cancellation. This matches `_PLANNER_CONTROLS` and the existing cleanup code. |
| Nit 2 — sweep description | **Closed** | [sweep.py:1](/Users/jpuc/code/moje/remember-stack-504/benchmarks/p1_ranked/sweep.py:1) describes the public BM25 chunk operation, actual strata, per-query outcomes/accounts, hashes and planning-only EXPLAIN with the production LIMIT. It explicitly leaves index-internal execution work unmeasured. The incremental diff changes prose only. |
| Nit 3 — exact paid-call accounting | **Closed in code; integration test inspected** | [test_reuse_lifecycle.py:846](/Users/jpuc/code/moje/remember-stack-504/src/tests/workers/test_reuse_lifecycle.py:846) requires a singleton target for every Selection and Claimify call and compares Counters against the expected rerun sets. Repeating A while omitting B now fails. Both checks remain in the passing living/dated test, outside the narrow known-gap xfail. The PostgreSQL fixture was not executed. |
| Nit 4 — BM25 domain guard | **Closed** | [p1_ranked.py:289](/Users/jpuc/code/moje/remember-stack-504/src/rememberstack/adapters/p1_ranked.py:289) validates the analyzed count before accessing the connection or opening the cursor. The docstring names the direct-call `ValueError`. A database-free check rejected **4,194,289 terms** with zero connection or confirmation calls; existing arithmetic/domain unit tests pass. |

## Judgements

**R3 P2-1 is not fully closed.** The repairs work on the paths they cover, but two existing bridge handlers still turn request-budget exhaustion into store/confirmation failure. This is the remaining R3 finding, not a new latency finding. I found no separate correctness regression introduced by `156a9eb6c..HEAD`.

**Classifier and error preservation:** 24 database-free classifier assertions covered raw and SQLAlchemy-wrapped budget errors, admission, explicit wrappers, a cause cycle, and negative controls: undefined table/column errors containing timeout text, lock timeout, statement complexity, built-in network `TimeoutError`, generic errors, and an unrelated timeout in implicit exception context. These controls were not misclassified. A further check confirmed that a non-budget DBAPI `.orig` takes precedence over an unrelated earlier explicit cause. Changed bridge catches retain the original error as `__cause__`; no silent success or swallowed failure was found. Public-executor controls preserve `p1_unavailable` for genuine projection/body failures, `confirmation_failed` for genuine confirmation failures, and `execution_error` for an ordinary final-SQL error.

**Owner invariant remains untouched.** The full PR does not change chunk packing, extraction/reuse/Claimify identity construction, embedding inputs, header facts, text-origin selection, lineage identity or carry-forward. The production changes near ingestion validate stored vectors; they do not re-key paid work. The closure commit changes only query-side production code and strengthens the existing reuse proof. No known Rule 4 gap is claimed fixed or hidden by a wider xfail.

**Verification:** **45 focused unit tests passed** in 2.64 s:

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  src/tests/surfaces/test_query_sandbox_budget.py \
  src/tests/adapters/test_p1_ranked.py \
  src/tests/surfaces/test_d142_accounts.py \
  src/tests/adapters/test_bounded_postgres_read.py
```

Additional in-memory reproductions exercised the public executor's export, generation lookup, snapshot import, coordinate/body and final-SQL paths, plus classifier controls and the upfront BM25 refusal. `git diff --check origin/main...HEAD` passed. No Docker/PostgreSQL stack, LoCoMo database, deployment or paid provider was accessed. No tracked files were modified.

## P0

None found.

## P1

No new finding. Existing P1-6 remains pending the owner's decision and was not reassessed.

## P2

1. **Finish R3 P2-1 at snapshot export and chunk-generation lookup.** [bridge.py:467](/Users/jpuc/code/moje/remember-stack-504/src/rememberstack/surfaces/query_sandbox/bridge.py:467) and [bridge.py:651](/Users/jpuc/code/moje/remember-stack-504/src/rememberstack/surfaces/query_sandbox/bridge.py:651).

   `_shared_snapshot` catches every `psycopg.Error` from `SELECT pg_export_snapshot(), now()` and converts it to `SandboxRejection(P1_UNAVAILABLE)`. The enclosing `_resolve_one` immediately re-raises `SandboxRejection` at line 363, bypassing the new classifier. `_pinned_generations` likewise converts every database error into `CONFIRMATION_FAILED`, before nomination's classified catch is entered. The executor handles these rejections before its raw timeout catch, so adding `TransactionTimeout` there cannot repair either path.

   Reproduced through `QuerySandboxExecutor.query_sql` with only the transaction/schema inspection replaced by in-memory stand-ins, leaving both production handlers intact:

   | Public invocation / injected failure point | SQLSTATE | Actual code | Required code |
   | --- | --- | --- | --- |
   | `SELECT rank FROM semantic_claims($1, 5)` / snapshot export, adapter supporting snapshot import | 57014 | `p1_unavailable` | `statement_timeout` |
   | Same | 25P04 | `p1_unavailable` | `statement_timeout` |
   | `SELECT rank FROM lexical_chunks($1, 5)` / current-generation read | 57014 | `confirmation_failed` | `statement_timeout` |
   | Same | 25P04 | `confirmation_failed` | `statement_timeout` |

   All four return zero rows with the wrong failure classification. An injected `UndefinedColumn` at the same points correctly retains `p1_unavailable`/`confirmation_failed`, respectively. Thus the remaining defect is precisely budget classification, without a reason to broaden genuine-error handling.

   **Fix:** apply `budget_exhausted`/`budget_rejection` inside these two catches before their ordinary mappings, retaining the original cause. Add database-free public-executor regressions for both SQLSTATEs at both points, using a search double that actually exposes `imported_snapshot` for the exporter case, plus non-budget controls. This completes the exporter-path requirement already stated in R3 P2-1.

## Nits

No new nits. R3 nits 1–4 are closed as recorded above.

Verdict: Request changes
