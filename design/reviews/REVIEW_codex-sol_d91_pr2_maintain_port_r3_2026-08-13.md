# Implementation re-review: D91 PR2 maintain port (r3)

**Reviewer:** Codex (`gpt-5.6-sol`)

**Date:** 2026-08-13

**PR:** #275

**Commit:** `45b40d19`

## Verdict

**REQUEST_CHANGES**

The lock fixes are now substantive and verified. Hard-forget takes only the
affected table locks, acquisition uses a bounded `pg_try_advisory_lock` poll,
unsuccessful unlocks invalidate the pooled connection, and the new adversarial
tests pass against PostgreSQL. Rebuild reports also contain real before
snapshots, and the backfill finalizer integration module is green.

One blocking ensure defect remains. LanceDB 0.34.0 reports the contracted
`IvfFlat` configuration through `list_indices()` as `index_type="IvfFlat"`, but
the implementation recognizes only `"IVF_FLAT"`. As a result, every explicit
ensure treats a healthy vector index as wrong and runs
`create_index(..., replace=True)`. I reproduced a healthy index advancing the
Lance table from version 11 to 12 on a second ensure. This violates D91's
list-first, non-destructive ensure contract and can turn deploy/tick ensure into
an unintended full IVF retrain.

The report contract also remains incomplete: scalar/FTS ensure can retry commit
conflicts, but `_create_index_with_retry()` returns no count and
`ensure_search_indexes()` always reports `conflicts_retried=0`.

## Requested-change disposition

| Prior finding | Disposition at `45b40d19` | Evidence / remaining work |
| --- | --- | --- |
| P1.1 | **RESOLVED** | `LockingP1Purge` takes the shared lock only for tables with nominated IDs and passes a 30-second default bound. Contention raises `P1MaintainLockTimeout`; the real-PostgreSQL purge timeout/proceed test passes. |
| P1.2 | **RESOLVED** | Cleanup attempts every acquired-key unlock, treats `false`/`NULL` as failure, and invalidates on any release/commit failure. Failed-second-acquire and failed-first-release tests prove every key is free from another session. |
| P1.3 | **PARTIAL / BLOCKING** | Dropped scalar repair and wrong HNSW-to-IvfFlat replacement work, but the healthy-type comparison uses the wrong runtime spelling and destructively replaces a healthy `IvfFlat` on every ensure. |
| P1.4 | **RESOLVED** | The finalizer requires `lance_root`; the purge composition passes the same configured root used by `LanceChunkIndex`; all callers use `p1_table_maintain_lock_key`. |
| P2.1 | **RESOLVED** | `BackfillFinalizer` locks one table around that table's ensure/vector/text sequence, then releases it before the next table. The PostgreSQL-backed finalizer module passes. |
| P2.2 | **PARTIAL / REQUIRED** | Ensure, optimize, vector rebuild, and text rebuild now stamp before/after snapshots and per-table duration; optimize reports retries. Ensure still discards its actual scalar/FTS create-index retry count. |
| B1 | **RESOLVED** | The stale terminal assertion now observes four per-table ensure calls, and `src/tests/spine/test_backfill.py` passes against real PostgreSQL. |
| B2 | **RESOLVED** | The blocking advisory-lock call was replaced with a deadline-bound try-lock poll; the hard-forget timeout is exercised end to end through `LockingP1Purge`. |
| R1 | **PARTIAL / REQUIRED** | The requested PostgreSQL acquire/release/purge lock regressions now exist and pass. Exact vector-type, dropped-FTS, wrong-Bitmap-type, and finalizer full-sequence/lock-ownership coverage are still absent. |
| R2 | **PARTIAL / REQUIRED** | Rebuild before-stats are fixed and verified with nonzero rows/fragments. Ensure retry accounting remains permanently zero. |

## Blocking findings (if any)

### P1.3 — Healthy IVF_FLAT indexes are replaced by explicit ensure

The vector branch at `lance.py:1153-1158` accepts a physical vector index only
when `index.index_type == "IVF_FLAT"`. That is not the value returned by the
pinned LanceDB 0.34.0 Python API. A real `IvfFlat(...)` index is listed as
`"IvfFlat"`; an `IvfHnswFlat(...)` index is listed as `"IvfHnswFlat"`.

I reproduced both sides against the shipped adapter:

```text
wrong-type repair:
before [('IvfHnswFlat', ['vector'])]
after  [('IvfFlat', ['vector'])]

healthy ensure repeated:
first  [('IvfFlat', ['vector'])] version 11
second [('IvfFlat', ['vector'])] version 12
```

The first result confirms that known wrong-type replacement now works. The
second is the blocker: because `"IvfFlat" != "IVF_FLAT"`, the second ensure
falls through to `_build_vector_index(..., replace=True)` and creates a new
Lance version. On production-sized tables that is the full train which ensure
is specifically required not to run for a healthy index.

D91 binds IVF_FLAT and distinguishes ensure from heavy replacement. LanceDB's
public vector documentation likewise treats flat and HNSW-backed IVF as
different index families ([LanceDB vector indexes](https://docs.lancedb.com/indexing/vector-index)).

Required change: compare against the actual 0.34.0 `list_indices()` type value
(prefer a small normalized/type-mapping helper), accept `IvfFlat` as healthy,
and continue replacing `IvfHnswFlat` and other known wrong vector types. Add a
regression that runs ensure twice and proves the second call neither invokes
vector `create_index` nor advances the table version. Tighten
`test_build_search_indexes_is_rerunnable_and_covers_entities`; its current
assertion at `test_lance_retrieval.py:587` accepts any index merely because its
columns contain `vector`, which allowed this defect through.

## Other required corrections (if any)

### P2.2 / R2 — Ensure retry accounting is still not truthful

`TableMaintainStats` binds `conflicts_retried`, but
`_create_index_with_retry()` (`lance.py:1209-1234`) returns `None`. Its scalar,
Bitmap, and FTS callers also return no retry count, and
`ensure_search_indexes()` (`lance.py:968-991`) never populates the field.
Therefore a successful ensure after one or more `Retryable commit conflict`
exceptions still reports zero.

The rebuild before-snapshot half of this finding is closed; do not change it
again. Vector/text heavy rebuilds intentionally do not short-retry a full train,
so zero is truthful there. The remaining change is to return and aggregate the
short scalar/FTS ensure retries and assert a forced retry in an adapter test.

### R1 — Finish the index/finalizer regressions requested in earlier rounds

The five new PostgreSQL lock tests are meaningful and close the lock-lifecycle
test gap. The remaining requested coverage is still missing:

- same-instance dropped-FTS repair;
- known wrong scalar type repair, especially `facts.kind` BTree to Bitmap;
- wrong-vector replacement plus healthy-IvfFlat no-replacement;
- the finalizer's complete per-table `ensure -> vector -> text` sequence while
  the corresponding custom-root lock is held.

The current custom-root tests call the key function with `Path` and `str`, but
do not observe either `LockingP1Purge` or `BackfillFinalizer`. The production
wiring is correct by inspection; the requested integration proof is not yet
present.

## Nits

None beyond the required test-strengthening above.

## What is fixed and verified

- **Bounded lock wait:** `pg_try_advisory_lock` is polled against a monotonic
  deadline, with positive timeout/poll validation. PostgreSQL documents that
  this function returns immediately with a boolean, while session locks remain
  held until unlock or session end
  ([advisory-lock functions](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS),
  [session semantics](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS)).
- **Unlock/invalidate:** every acquired key is attempted in reverse order;
  `pg_advisory_unlock` must return literal `true`; any cleanup uncertainty
  invalidates the physical connection. The injected first-release failure
  leaves both keys acquirable from other sessions.
- **Purge scope:** facts-only purge ignores a held chunks lock, while a held
  facts lock times out before the adapter is called and succeeds after release.
  This preserves the safety requirement around
  `optimize(delete_unverified=True)`; LanceDB documents that unverified cleanup
  assumes no in-progress transaction
  ([LanceDB Python API](https://lancedb.github.io/lancedb/python/python/#lancedb.table.Table.optimize)).
- **Before-stats:** ensure, light optimize, vector rebuild, and text rebuild all
  copy before values into their report. A nonempty-table probe returned
  `row_count_before=1`, `row_count=1`, `num_fragments_before=1`, and
  `num_fragments=1` for both rebuild operations.
- **Backfill table grain:** the finalizer holds one root/table lock for that
  table's ensure + vector + text operations. Its PostgreSQL-backed suite is
  green.
- **Full PR2 matrix and port:** all four tables remain represented; entities,
  prefilter columns, `facts.fact_id`, and Bitmap bindings are present;
  `build_search_indexes()` remains ensure + heavy with replacement on heavy.
- **Focused execution:**

  ```text
  REMEMBERSTACK_DATABASE_URL=<local PostgreSQL> \
    uv run pytest -q src/tests/spine/test_p1_maintain_lock.py
  5 passed in 12.70s

  uv run pytest -q \
    src/tests/adapters/test_lance_retrieval.py \
    src/tests/adapters/test_selfhost_purge.py
  15 passed in 15.74s

  REMEMBERSTACK_DATABASE_URL=<ephemeral PostgreSQL+pg_partman> \
    uv run pytest -q src/tests/spine/test_backfill.py
  4 passed in 53.66s

  uv run ruff check <reviewed files>
  All checks passed!

  uv run ruff format --check <reviewed files>
  10 files already formatted

  uv run pyright <reviewed implementation files>
  0 errors, 0 warnings, 0 informations
  ```

The bounded wait, unlock-invalidating cleanup, rebuild before-stats, and real
PostgreSQL lock tests are closed. Wrong-vector replacement is also implemented,
but the IVF type check is not closed until a healthy `IvfFlat` is recognized
without replacement. **REQUEST_CHANGES**.
