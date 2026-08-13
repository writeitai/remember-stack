# Implementation review: D91 PR2 maintain port and table locks

**Reviewer:** Codex (`gpt-5.6-sol`)

**Date:** 2026-08-13

**PR:** [#275](https://github.com/writeitai/remember-stack/pull/275)

**Branch:** `feat/d91-pr2-maintain-port`

**Commit:** `1d098943d4cbe927939f45a93fe055ea72437f34`

**Stack base:** [#271](https://github.com/writeitai/remember-stack/pull/271),
`2c0c2b7157cee314755270c1995ec745ac233742`

**Design source:** [D91 at `87079b4b`](https://github.com/writeitai/remember-stack/blob/87079b4bcae964484e6ab8fb6a528ed8fd8506b5/plan/designs/p1_lance_maintenance_design.md)

## Verdict

**REQUEST_CHANGES**

The first-build adapter work is substantially right: the maintenance protocol
has the requested operations, `P1_INDEX_MATRIX` contains all four P1 tables and
the binding entity/prefilter columns, and the new real-Lance test proves that
`build_search_indexes()` can run twice and build entity indexes. The targeted
suite, Ruff, and Pyright pass.

The PR is not ready to merge because the shared-lock protocol is incomplete and
the explicit ensure operation can falsely report success while a contracted
index remains absent. In particular, hard-forget still runs Lance's
`delete_unverified` cleanup without the new lock, and acquisition/release
failures can leak PostgreSQL session advisory locks into SQLAlchemy's pool.
Those are correctness issues, not test-polish nits.

This was reviewed in two independent passes: port/adapter/index behavior, and
locks/backfill/test realism. Both passes independently reached
`REQUEST_CHANGES`; the findings below are the reconciled result.

## Blocking findings

### P1.1 — Hard-forget does not take the shared table lock before `delete_unverified`

`LanceChunkIndex.purge_rows()` now documents that callers must already hold the
table-scoped P1 maintain lock (`lance.py:1318-1322`), but the shipped caller does
not satisfy that precondition:

- `SelfHostHardForget.compose()` injects a raw `LanceChunkIndex`
  (`profiles/selfhost_forget.py:83-88`).
- `HardForgetHandler.honor()` calls `purge_rows()` directly
  (`workers/forget.py:207-213`).
- `_purge_table_rows()` performs `delete()` followed by
  `optimize(cleanup_older_than=timedelta(0), delete_unverified=True)`
  (`lance.py:1416-1427`).
- The only live caller of `hold_p1_table_maintain_locks()` is the backfill
  finalizer; repository search finds no lock around the hard-forget path.

Advisory locks are cooperative, so locking only the finalizer does not serialize
it with an unlocked purge. This is the exact interaction D91 labels a corruption
hazard. LanceDB's public API warning says `deleteUnverified` is safe only when no
other process is working on the dataset; otherwise the dataset can be corrupted
([LanceDB `OptimizeOptions`](https://lancedb.github.io/lancedb/js/interfaces/OptimizeOptions/#deleteunverified)).

Required change: make the self-host forget composition own the Postgres engine
and exact `lance_root`, acquire the same per-table key before each affected
delete+optimize, and make that acquisition bounded so a long or wedged rebuild
causes the forget step to fail/retry rather than hang indefinitely. Add a real
lock-interaction regression for purge versus maintain/finalizer.

### P1.2 — Partial acquire or release failure can leak session locks into the pool

In `hold_p1_table_maintain_locks()` (`p1_maintain_lock.py:34-43`), the cleanup
`try/finally` begins only after every acquisition and the following `commit()`:

```text
for key in keys:
    acquire(key)
commit()
try:
    yield
finally:
    for key in reversed(keys):
        release(key)
```

If acquisition N or the commit fails, the earlier locks never reach the
`finally`. During cleanup, one failed unlock also aborts the loop and leaves the
remaining keys held.

This matters specifically because these are session locks. PostgreSQL states
that a session advisory lock survives transaction rollback and remains held
until an explicit matching unlock or session end
([PostgreSQL advisory-lock semantics](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS)).
SQLAlchemy normally returns the DBAPI connection to its pool and resets it with
`rollback()`, rather than ending the backend session
([SQLAlchemy pool reset-on-return](https://docs.sqlalchemy.org/en/20/core/pooling.html#reset-on-return)).
An acquisition-path exception can therefore return a lock-owning backend to the
pool and later block unrelated work or deadlock against itself.

Required change: track successfully acquired keys inside a cleanup scope that
covers acquisition as well as the body; attempt every matching unlock even if
one fails; and invalidate/terminate the pooled connection whenever complete
cleanup cannot be proven. Add PostgreSQL failure-injection tests for a failed
second acquire and a failed first release, verifying from another session that
all keys are free.

### P1.3 — Explicit ensure trusts process-local caches instead of physical index state

`ensure_search_indexes()` promises to create missing matrix indexes
(`ports/p1_index.py:299-303`). `_ensure_matrix_indexes()` dispatches scalar and
text entries to `_ensure_scalar_index()` / `_ensure_text_index()`
(`lance.py:1103-1144`), but both helpers return solely because an in-process
"ready" set contains the key. The explicit maintenance call then skips the
physical `list_indices()` check.

I reproduced this against the shipped adapter and pinned LanceDB:

1. Upsert one chunk, which fills `_scalar_indexes_ready`.
2. Drop `deployment_id_idx` through Lance.
3. Call `ensure_search_indexes(tables=("chunks",))` on the same
   `LanceChunkIndex`.
4. The returned report says `operation='ensure'`, while `deployment_id` is still
   absent from `list_indices()`.

The new build-twice test does not catch this: it never removes or corrupts an
index, and `build_search_indexes()` immediately follows ensure with heavy
vector/FTS replacement. D91's ensure contract is independently triggered,
list-first, and self-healing. A process-local read/write fast-path cache cannot
be authoritative for it.

Required change: have explicit ensure reconcile the current physical index list
for every requested matrix entry, including contracted type, regardless of
ready-cache state. Add same-instance drop-and-repair tests for scalar and FTS
indexes and an ensure-only wrong-type repair test.

### P1.4 — The lock namespace can silently differ from the adapter's Lance estate

`BackfillFinalizer.__init__()` accepts `lance_root=None` and independently
defaults it to `/var/lib/rememberstack/lance` (`backfill.py:105-115`). The
maintenance adapter is injected separately and exposes no root identity.
`SelfHostSettings.lance_root` is configurable (`profiles/selfhost.py:81-96`).

Consequently, a caller can inject an adapter connected to a custom root, omit
the duplicate finalizer argument, and successfully take locks for an unrelated
default path. Because the lock key is exactly resolved-root plus table
(`p1_maintain_lock.py:17-19`), the operation then has no mutual exclusion with
the real estate.

Required change: make the root identity explicit and non-optional at composition
or bind adapter plus lock identity in one composition object. Add a custom-root
test that proves two callers derive the same key.

## Other required corrections

### P2.1 — The finalizer holds the whole estate while rebuilding one table

`BackfillFinalizer` acquires all four table locks at once, then calls the
all-table convenience method (`backfill.py:133-136`). Sorted acquisition avoids
deadlock, but a multi-hour chunks retrain unnecessarily holds claims, facts, and
entities locks throughout; unrelated per-table maintenance and purge cannot
proceed.

D91 makes the physical table the maintenance grain and requires the finalizer
to lock each present table around that table's ensure+heavy work. Use the
table-selecting port methods under one table lock at a time. This also makes the
new helper's name and behavior genuinely table-scoped rather than estate-scoped
for the duration of a rebuild.

### P2.2 — `MaintainReport` does not fulfill the stats contract

`TableMaintainStats` exposes only one row/unindexed/fragment snapshot
(`model/p1_maintain.py:8-21`), although D91 requires per-table before/after
values. `conflicts_retried` exists but always remains zero: neither
`_create_index_with_retry()` nor `_optimize_with_retry()` returns its retry
count, and every report uses the default (`lance.py:1176-1248`). In addition,
`ensure_search_indexes()` starts one timer before the table loop, so later
tables receive cumulative rather than per-table duration (`lance.py:972-982`).

Complete this port model now, before PR3/PR4 consumers make the incomplete shape
durable: report before and after snapshots, actual retry counts, and per-table
durations. Public LanceDB guidance specifically recommends `index_stats()` and
`num_unindexed_rows` for coverage monitoring
([LanceDB optimization guide](https://docs.lancedb.com/search/optimize-queries#index-coverage-monitoring)).

## What is correct

### Port and adapter

- `LanceChunkIndex` structurally satisfies the runtime-checkable
  `P1IndexMaintenancePort`.
- The port includes ensure, light optimize, vector rebuild, text rebuild, stats,
  and the compatibility `build_search_indexes()` method
  (`ports/p1_index.py:292-328`).
- `build_search_indexes()` delegates to ensure, vector heavy rebuild, and text
  heavy rebuild; the heavy paths use `replace=True` (`lance.py:962-966,
  1011-1063`).
- Vector rebuild retains the 256-row gate and IVF_FLAT partition calculation.

### Full index matrix

`P1_INDEX_MATRIX` (`lance.py:46-76`) matches the D91 matrix:

- chunks: vector, FTS text, deployment/chunk/generation keys, `doc_id`, and the
  `source_kind` / `source_shape` / `section_role` prefilters;
- claims: vector, FTS text, deployment/claim keys, `doc_id`, and
  `is_current_testimony`;
- facts: vector, deployment/fact keys, `kind` Bitmap, `status`, and all four
  time predicates;
- entities: vector, deployment/entity keys, and `type` Bitmap.

That agrees with LanceDB's public guidance to index filter and merge-join
columns and to use Bitmap for low-cardinality categorical values
([query optimization](https://docs.lancedb.com/search/optimize-queries),
[scalar indexes](https://docs.lancedb.com/indexing/scalar-index)). A diagnostic
over four real tables confirmed that the first explicit ensure creates every
matrix entry.

### Build twice and shipped-adapter coverage

`test_build_search_indexes_is_rerunnable_and_covers_entities`
(`test_lance_retrieval.py:550-592`) instantiates the shipped
`LanceChunkIndex`, lowers the vector gate to one row, writes chunks and an
entity, calls `build_search_indexes()` twice, and inspects real Lance indices.
That is meaningful adapter coverage, and it passes.

The assertion for the entity vector index is too permissive, however: it accepts
either exact `IVF_FLAT` or any index whose columns contain `vector`
(`test_lance_retrieval.py:587`). Assert the contracted vector type exactly.
The test also does not enumerate the full chunk/claim/fact matrix, which allowed
the explicit-ensure defect above to escape.

`test_backfill.py` is only structural for this PR's new behavior. Its
`_RecordingIndexMaintenance` fake implements the expanded methods but records
only `build_search_indexes()` (`test_backfill.py:113-143`); the finalizer test
asserts the drain barrier and call count, not lock keys, blocking, root identity,
release on body failure, or partial-acquire cleanup (`test_backfill.py:228-262`).
Those missing lock tests are required with the fixes above.

## Verification

```text
uv run pytest -q \
  src/tests/adapters/test_lance_retrieval.py \
  src/tests/spine/test_backfill.py
10 passed, 3 skipped in 6.80s
```

All three skipped tests are the PostgreSQL-backed backfill module in this local
environment because `REMEMBERSTACK_DATABASE_URL` was unavailable. The direct
Lance adapter coverage did run.

```text
uv run ruff check <reviewed Python files>
All checks passed!

uv run pyright <reviewed Python files>
0 errors, 0 warnings, 0 informations

isinstance(LanceChunkIndex(...), P1IndexMaintenancePort)
True
```

At review time the public PR checks showed Quality, Unit, Contract smoke,
Integration (adapters), Compose quickstart, and PR gate passing; worker/surface
integration remained pending, and the separate CLA check was failing.

## Final assessment

The adapter's matrix and build-twice direction are good, and the PR does include
a genuine shipped-adapter regression. Approval is blocked by the incomplete
cooperative lock protocol, exception-unsafe session-lock lifecycle, false-success
ensure path, root-key ambiguity, and incomplete stats result. Fix those paths
and add PostgreSQL lock-failure plus explicit ensure-repair coverage before
re-review.

The D91 design source is still an open public PR (#270); land it before or with
the implementation stack so the contract reviewed here is present on the target
history. No private source or connector was used for this review.
