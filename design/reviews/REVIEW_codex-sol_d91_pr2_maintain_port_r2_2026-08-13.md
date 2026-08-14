# Implementation re-review: D91 PR2 maintain port and table locks

**Reviewer:** Codex (`gpt-5.6-sol`)

**Date:** 2026-08-13

**PR:** [#275](https://github.com/writeitai/remember-stack/pull/275)

**Branch:** `feat/d91-pr2-maintain-port`

**Commit:** `e1ccd55288fd9b1ec23caacd0a085fbddb8fac9a`

**Stack base:** [#271](https://github.com/writeitai/remember-stack/pull/271),
`2c0c2b7157cee314755270c1995ec745ac233742`

**Fix commit:**
[`e1ccd552`](https://github.com/writeitai/remember-stack/commit/e1ccd55288fd9b1ec23caacd0a085fbddb8fac9a)

**Prior review:**
[`REVIEW_codex-sol_d91_pr2_maintain_port_2026-08-13.md`](https://github.com/writeitai/remember-stack/blob/e1ccd55288fd9b1ec23caacd0a085fbddb8fac9a/design/reviews/REVIEW_codex-sol_d91_pr2_maintain_port_2026-08-13.md)

**Design source:**
[`p1_lance_maintenance_design.md` at `87079b4b`](https://github.com/writeitai/remember-stack/blob/87079b4bcae964484e6ab8fb6a528ed8fd8506b5/plan/designs/p1_lance_maintenance_design.md)

## Verdict

**REQUEST_CHANGES**

The fix commit moves each prior finding in the right direction, but it does not
close four of the six requested changes. Hard-forget now uses the shared lock,
the finalizer requires the configured root and locks one table at a time, and
explicit ensure no longer trusts a stale ready-cache for dropped scalar
indexes. Those are real improvements.

The remaining blockers are concrete: hard-forget lock acquisition is still
unbounded; an unlock error can still return a session-lock-owning connection to
the pool; ensure accepts a wrong vector index type; and maintenance reports
still omit required before snapshots and retry counts on several operations.
The new code also has no lock/purge regression, while the updated PostgreSQL
backfill test is internally stale and will fail when its database fixture runs.

## Requested-change disposition

| Finding | Disposition | Re-review result |
| --- | --- | --- |
| P1.1 | **PARTIAL** | Hard-forget now takes the common lock, but acquisition remains unbounded and the wrapper is untested. |
| P1.2 | **PARTIAL / BLOCKING** | Acquired keys are tracked and every release is attempted, but any release error that is followed by a successful `commit()` skips invalidation and can leak session locks into the pool. |
| P1.3 | **PARTIAL / BLOCKING** | Clearing ready-caches repairs dropped scalar indexes, but vector ensure still accepts any index on `vector`, including the wrong ANN type. Requested FTS and wrong-type regressions are absent. |
| P1.4 | **RESOLVED IN CODE** | `BackfillFinalizer` now requires `lance_root`, and hard-forget passes the same explicit root to the common lock helper. The requested custom-root regression is still absent. |
| P2.1 | **RESOLVED IN CODE; TEST BROKEN** | The finalizer locks and rebuilds one table at a time. Its integration fake still records only the removed all-table convenience call, so the live test's terminal assertion cannot pass. |
| P2.2 | **PARTIAL / BLOCKING** | Ensure/optimize gained before fields, optimize reports retries, and ensure timing is per table. Rebuild reports still default all before values to zero, and ensure discards create-index retry counts. |

## Blocking findings

### P1.1 — Hard-forget can still wait forever for a maintain lock

`LockingP1Purge.purge_rows()` correctly encloses the raw adapter purge in the
common lock (`p1_locked_purge.py:24-43`), and `SelfHostHardForget.compose()` now
injects that wrapper. This closes the unlocked `delete_unverified` corruption
window from round one.

It does not close the required bounded-wait behavior. `_ACQUIRE` is still
`pg_advisory_lock(...)` (`p1_maintain_lock.py:13`), with no try-lock loop,
deadline, statement timeout, or cancellation path. The wrapper also acquires
all four table locks before deleting any nominated row
(`p1_locked_purge.py:33-36`), including tables whose ID tuples are empty. A
wedged maintain owner can therefore hang both ordinary hard-forget honoring and
startup readiness replay indefinitely.

PostgreSQL exposes `pg_try_advisory_lock` specifically as the non-waiting
session-lock operation in its public
[advisory-lock function table](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS).
Use a bounded acquisition protocol and let the hard-forget work fail/retry when
the deadline expires. Lock only affected tables, or acquire one affected table
around its delete+prune operation.

There is no regression for this new production seam. Repository search finds
no test importing `LockingP1Purge` or `hold_p1_table_maintain_locks`.
`test_lance_purge_removes_only_nominated_deployment_rows` still assigns the raw
`LanceChunkIndex` directly to `P1PurgePort`; it proves deletion semantics but
not purge/maintain exclusion or bounded waiting.

### P1.2 — Release failure still permits a pooled session-lock leak

The cleanup scope now covers acquisition, and `acquired` prevents releasing
keys that were never taken (`p1_maintain_lock.py:34-42`). The reverse-release
loop also continues after exceptions (`p1_maintain_lock.py:43-48`). Those fix
two structural gaps from round one.

However, the connection is invalidated only when `connection.commit()` itself
raises (`p1_maintain_lock.py:49-53`). If any `_RELEASE` execution raises but the
following commit returns normally, `release_errors` is raised at line 55 and
the context manager returns the connection to the pool without invalidating
it. The helper therefore cannot prove that all session locks are gone.

This is not theoretical cleanup polish. A failure-injection reproduction at
the reviewed head made the first release raise while `commit()` returned
normally; the context raised the injected error with `invalidated == False`.
Against PostgreSQL, one statement error also aborts the current transaction, so
later unlock attempts on that transaction need not release the remaining keys.
PostgreSQL documents that session advisory locks survive transaction rollback
and remain held until explicit release or session end
([advisory-lock semantics](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS)).
SQLAlchemy's normal pool reset is a `rollback()`, not backend-session
termination
([reset-on-return](https://docs.sqlalchemy.org/en/20/core/pooling.html#reset-on-return)).

Invalidate the connection whenever any release result is unsuccessful or any
release/cleanup step raises, not only when the final commit raises. Check the
boolean returned by `pg_advisory_unlock`, too. Add the previously requested
real-PostgreSQL failure-injection tests for failed second acquisition and
failed first release, probing every key from another session afterward.

### P1.3 — Ensure accepts a wrong vector index type

Clearing the table's process-local ready flags before explicit ensure
(`lance.py:975-977,1115-1120`) fixes the reproduced same-instance dropped
scalar defect. The added test is meaningful for that case.

The full physical/type reconciliation contract is still incomplete. The vector
branch checks only whether *any* index has `columns == ["vector"]`
(`lance.py:1140-1144`). It never verifies the binding `IVF_FLAT` type. I
reproduced this with 256 chunk rows and an `IvfHnswFlat` index:

```text
before [('IvfHnswFlat', ['vector'])]
after  [('IvfHnswFlat', ['vector'])]
```

`ensure_search_indexes(tables=("chunks",))` returned successfully and retained
the wrong ANN type. D91 binds IVF_FLAT for all four vector columns, and the
public LanceDB vector-index documentation distinguishes IVF_FLAT from HNSW
families as separate index choices
([vector indexes](https://docs.lancedb.com/indexing/vector-index)). Ensure must
compare the physical type and replace a known wrong vector index.

The round-one requested tests also remain incomplete: there is only a dropped
BTree repair test. Add same-instance dropped FTS coverage and ensure-only
wrong-type coverage, including a wrong vector type. Tighten the existing entity
assertion at `test_lance_retrieval.py:587`; it still accepts any index merely
because its columns contain `vector`, which encodes the same bug in the test.

### P2.2 — `MaintainReport` is still not a truthful before/after/retry report

`TableMaintainStats` now has before fields, and `ensure_search_indexes()` plus
`optimize_tables()` populate them. The ensure timer also moved inside the table
loop, and `_optimize_with_retry()` returns its actual retry count. Those parts
are resolved.

Two required gaps remain:

- `rebuild_vector_indexes()` and `rebuild_text_indexes()` take only the final
  snapshot (`lance.py:1027-1049,1055-1075`). Every `*_before` field silently
  remains the model default `0`, even for a non-empty table.
- `_create_index_with_retry()` still returns `None`
  (`lance.py:1195-1220`), and ensure never aggregates its retry attempts.
  `conflicts_retried` therefore reports `0` even after one or more successful
  create-index retries.

Populate before and after snapshots for every operation outcome and propagate
the actual retry count for ensure. Add assertions that force retry(s) and that
use nonzero table/fragment inputs, so model defaults cannot masquerade as
measurements.

## P2.1 test regression

The implementation now correctly performs, under one table lock at a time:

```text
ensure_search_indexes(table)
rebuild_vector_indexes(table)
rebuild_text_indexes(table)
```

at `backfill.py:133-145`. It no longer calls the fake's
`build_search_indexes()` method. But `_RecordingIndexMaintenance` increments
`builds` only in that unused compatibility method
(`test_backfill.py:113-139`), while the test still asserts `builds == 1` after
finalization (`test_backfill.py:263-264`). When the PostgreSQL fixture is
available, the finalizer leaves `builds == 0`, so this test fails.

The local run skipped all three tests in `test_backfill.py` because
`REMEMBERSTACK_DATABASE_URL` was unset; that skip hides the regression. Replace
the stale counter with ordered per-table call recording and assert the complete
sequence. That test should also prove the custom-root key and that the next
table can acquire its lock while a different table's rebuild is running.

## What is now correct

- `SelfHostHardForget.compose()` injects `LockingP1Purge` with the same
  configured `lance_root` used to construct `LanceChunkIndex`.
- `BackfillFinalizer.__init__()` no longer has an unrelated default root; the
  caller must provide the lock identity explicitly.
- The finalizer releases each table lock before starting work on the next
  table, matching D91's physical table grain.
- Explicit ensure discards ready-cache entries for the selected table, so a
  physically dropped BTree is discovered and rebuilt on the same adapter
  instance.
- Ensure duration is per table, and light optimize reports actual short retry
  attempts.
- The original full index matrix, build-twice behavior, entities coverage, and
  deployment-free maintenance port remain intact.

## Verification

Public GitHub metadata confirms that PR #275 is open at
`e1ccd55288fd9b1ec23caacd0a085fbddb8fac9a`, targeting stack base
`2c0c2b7157cee314755270c1995ec745ac233742`.

```text
uv run pytest -q \
  src/tests/adapters/test_lance_retrieval.py \
  src/tests/adapters/test_selfhost_purge.py \
  src/tests/spine/test_backfill.py
15 passed, 3 skipped in 12.33s
```

The three skips are the entire PostgreSQL-backed `test_backfill.py` module, not
irrelevant optional coverage.

```text
uv run ruff check <reviewed Python files>
All checks passed!

uv run ruff format --check <reviewed Python files>
9 files already formatted

uv run pyright <reviewed implementation files>
0 errors, 0 warnings, 0 informations

git diff --check 2c0c2b7157cee314755270c1995ec745ac233742...HEAD
# clean
```

Additional direct reproductions:

```text
wrong-vector-type ensure:
before [('IvfHnswFlat', ['vector'])]
after  [('IvfHnswFlat', ['vector'])]

release-error injection:
RuntimeError injected release failure
invalidated False
```

No private connector, private documentation, or non-public source was used.

## Final assessment

P1.4 and the P2.1 production flow are fixed. P1.1, P1.2, P1.3, and P2.2 are
not yet fully closed, and the finalizer's PostgreSQL integration test is stale.
**REQUEST_CHANGES**.
