# D142 implementation review, round 5 — PR #505

Reviewed `origin/main...HEAD` at **fb90bfd24b92a564d46ae4290c2132f905664c49**, focusing on `9ad1451bd..HEAD`. Read AGENTS.md → CLAUDE.md, including Rule 4, the design-corpus skill, and the round-4 review/response. P1-6 was excluded from reassessment as instructed.

## Closure audit

| Item | Status | Code and verification |
| --- | --- | --- |
| R3/R4 P2-1 — snapshot export | **Closed** | [bridge.py:467](/Users/jpuc/code/moje/remember-stack-504/src/rememberstack/surfaces/query_sandbox/bridge.py:467) classifies budget exhaustion before `p1_unavailable`, retaining the original cause. The committed public-executor regressions use an adapter exposing `imported_snapshot`; both 57014 and 25P04 return `statement_timeout`. The genuine-error control retains `p1_unavailable` and checks the snapshot-specific message. |
| R3/R4 P2-1 — current chunk generations | **Closed** | [bridge.py:653](/Users/jpuc/code/moje/remember-stack-504/src/rememberstack/surfaces/query_sandbox/bridge.py:653) classifies before `confirmation_failed`, retaining the original cause. Both committed SQLSTATE cases pass through `semantic_chunks`; the genuine-error control retains `confirmation_failed` and checks the generation-specific message. The same handler serves lexical chunks. |
| Executor dynamic type lookup | **Closed in code; regression coverage nit** | [executor.py:111](/Users/jpuc/code/moje/remember-stack-504/src/rememberstack/surfaces/query_sandbox/executor.py:111) re-raises budget errors instead of returning an OID. Public-executor injections using an uncached array OID returned `statement_timeout` for both SQLSTATEs, with zero rows and no subsequent `fetchmany`. Direct checks preserve the exception object itself. The existing numeric-OID fallback remains for non-budget metadata errors. |
| Broad-catch sweep | **Complete for the sandbox package** | An AST inventory finds six broad bridge handlers: nomination, confirmation, export, body reads, generations and coordinate confirmation. Every one classifies budget errors before its ordinary mapping. The executor has the type-name handler and its two terminal generic database handlers; its explicit 57014/25P04 catch precedes both terminal handlers. Other sandbox catches are narrow validation/conversion or queue handling; nomination has no broad database catch. Public checks also cover wrapped snapshot-import entry failures. |
| Classifier specificity | **Verified** | [nomination.py:870](/Users/jpuc/code/moje/remember-stack-504/src/rememberstack/surfaces/query_sandbox/nomination.py:870) recognizes the two SQLSTATEs and SQLAlchemy admission/deadline timeouts through `.orig` or explicit causes. It does not inspect message text or implicit exception context. Thirty-eight controls passed, including misleading timeout messages, undefined tables/columns, lock/connection/complexity errors, built-in `TimeoutError`, implicit unrelated timeouts and cause cycles. A non-budget DBAPI `.orig` takes precedence over an unrelated earlier explicit cause. |

## Judgements

**R3/R4 P2-1 is closed.** Both remaining handlers now return the required budget outcome. The new tests are sensitive to the repair: all four budget cases fail when the two production handlers are loaded from `9ad1451bd`, while their genuine-error controls still pass.

**No new correctness bug was found in `9ad1451bd..HEAD`, including its interaction with the full PR.** No exhausted-budget error is swallowed at the audited catches. Genuine projection/export/body failures still yield `p1_unavailable`; genuine generation/confirmation/coordinate failures still yield `confirmation_failed`. Ordinary final-SQL errors retain their executor classification. Twenty direct checks verified original exception identity or cause preservation across all six bridge handlers and dynamic type lookup. The non-budget OID fallback is unchanged metadata behavior, not a new successful search after budget exhaustion.

**The owner's Rule 4 invariant remains untouched.** The closure commit changes only query-side production code and query tests. Across `origin/main...HEAD`, chunk packing, Selection/Claimify reuse identities, embedding inputs, header facts, text-origin inheritance, lineage identity and paid-product carry-forward remain unchanged. The production changes near ingestion validate vector values before storage; they do not re-key paid work. The strengthened living/dated reuse proof and narrowly scoped known-gap xfail were inspected, not executed. No existing Rule 4 gap is represented as solved.

**Verification:** 50 focused unit tests passed in 2.94 seconds:

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  src/tests/surfaces/test_query_sandbox_budget.py \
  src/tests/adapters/test_p1_ranked.py \
  src/tests/surfaces/test_d142_accounts.py \
  src/tests/adapters/test_bounded_postgres_read.py
```

Additional in-memory checks comprised 38 classifier controls, 36 public-executor failure injections, 20 exception/cause checks and the parent-handler sensitivity checks described above. All public failure injections returned zero rows and closed their transaction stand-in. `git diff --check origin/main...HEAD` passed. These checks verify Python control flow; no live PostgreSQL cancellation or transaction cleanup was exercised. No Docker/PostgreSQL stack, LoCoMo database, deployment or paid provider was accessed. No tracked files were modified.

## P0

None found.

## P1

No new finding. P1-6 remains the existing owner decision and was not reassessed.

## P2

None. R3/R4 P2-1 is closed.

## Nits

1. **Commit the dynamic-type and classifier negative controls.** [test_query_sandbox_budget.py:186](/Users/jpuc/code/moje/remember-stack-504/src/tests/surfaces/test_query_sandbox_budget.py:186) covers the two outstanding bridge handlers, but the suite has no regression for the new `_type_name` branch or the classifier's negative cases. Preserve an uncached OID case for each SQLSTATE, asserting `statement_timeout` and no row fetch, plus non-budget/message/context controls. The current code passes these checks; this is nonblocking coverage work.

Verdict: Approve pending P1-6 owner decision
