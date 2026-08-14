# Implementation re-review — D91 PR2 maintain port and table locks (after Codex r1)

**Agent:** claude-opus
**Date:** 2026-08-13
**Target:** branch `feat/d91-pr2-maintain-port`, PR #275 (stacked on PR #271)
**Commit reviewed:** `e1ccd552` ("fix(p1): close D91 PR2 lock, ensure-cache,
and stats gaps")
**Prior review:** `REVIEW_codex-sol_d91_pr2_maintain_port_2026-08-13.md`
(REQUEST_CHANGES at `1d098943`; blocking P1.1 unlocked hard-forget purge,
P1.2 session-lock leak on partial acquire/release, P1.3 ensure trusting
process-local caches, P1.4 ambiguous lock root; required P2.1 estate-wide
finalizer lock, P2.2 incomplete stats)
**Scope:** the fix commit against those six findings — `spine/p1_maintain_lock.py`,
`adapters/selfhost/p1_locked_purge.py`, `profiles/selfhost_forget.py`,
`spine/backfill.py`, the ensure/stats paths in `adapters/selfhost/lance.py`,
`model/p1_maintain.py`, and the test updates
**Design:** D91 `plan/designs/p1_lance_maintenance_design.md` §5.3 / §5.7 /
§15 PR2 — read on `design/d91-p1-lance-maintenance` at `87079b4b`
(design PR #270, still open)

---

## Verdict

**REQUEST_CHANGES.**

Four of the five substantive fixes are correct, and I confirmed the two
riskiest ones by execution rather than by reading the diff: the rewritten lock
helper survives a real failed second acquire without leaking a session
advisory lock into the pool, and explicit ensure now physically repairs a
dropped scalar index on the same adapter instance. The hard-forget purge is
genuinely serialized against maintenance now, and the finalizer takes one
table lock at a time with a mandatory `lance_root`.

Approval is blocked by one deterministic defect the fix commit itself
introduced: the finalizer's drain-barrier test still asserts the *old* port
call (`build_search_indexes()`), which `BackfillFinalizer` no longer makes. I
ran `src/tests/spine/test_backfill.py` against a real PostgreSQL and the test
fails (`assert maintenance.builds == 1` → `0 == 1`). It passed local
validation only because the module skips without `REMEMBERSTACK_DATABASE_URL`
— but CI's **Integration (workers)** job runs `src/tests/spine` with a live
database, so the currently-pending check will fail. This cannot merge as-is.
Two smaller required corrections (unbounded purge lock wait; the lock-behavior
tests the prior review asked for) can ride the same fix round.

## Blocking findings

### B1 — The finalizer drain-barrier test asserts a call the finalizer no longer makes (fails in CI)

The fix commit changed `BackfillFinalizer.build_search_indexes()` from one
estate-wide `port.build_search_indexes()` call to per-table
`ensure_search_indexes` + `rebuild_vector_indexes` + `rebuild_text_indexes`
under individual locks (`spine/backfill.py:133-145`). The test fake was not
updated: `_RecordingIndexMaintenance` increments its counter only inside
`build_search_indexes()` (`src/tests/spine/test_backfill.py:119-121`), so the
post-drain assertion `maintenance.builds == 1`
(`src/tests/spine/test_backfill.py:264`) is now unsatisfiable.

Reproduced against a real PostgreSQL (fresh
`ghcr.io/dbsystel/postgresql-partman` container, alembic head, same setup as
CI):

```text
uv run pytest src/tests/spine/test_backfill.py -q --tb=short
FAILED src/tests/spine/test_backfill.py::test_search_indexes_build_only_after_backfill_has_drained
E   assert 0 == 1  (maintenance.builds)
1 failed, 2 passed in 80.19s
```

The PR's local validation reported these three tests as *skipped* (no
database URL), which is how the regression escaped. CI's Integration
(workers) job (`.github/workflows/ci.yml`, `uv run pytest src/tests/workers
src/tests/spine`) provides a database, so the pending check will fail.

Required change: rewrite the fake to record the calls the finalizer actually
makes, and while doing so give the test the lock-visibility the prior review
asked for — assert the per-table call sequence (ensure → vector → text per
table), and that each triple runs under the held key
`p1_table_maintain_lock_key(lance_root, table)` (e.g. record
`pg_advisory_lock` holders from the fake via a second connection, or assert
the key material with a recording lock seam). The drain-barrier half of the
test (refuse while undrained) still passes and should be kept as-is.

### B2 — Purge lock acquisition is unbounded; D91 requires a bounded wait with forget-step retry

`LockingP1Purge.purge_rows()` acquires all four table locks via
`pg_advisory_lock` with no timeout (`adapters/selfhost/p1_locked_purge.py:34-43`,
`spine/p1_maintain_lock.py:37-38`). D91 §5.7 rule 5 is binding on exactly this
interaction: "purge waits on the lock (**bounded wait; fail forget step with
retry if lock wait exceeds policy**)". The prior review's P1.1 required change
included the same bound.

Failure scenario: the backfill finalizer starts a heavy IVF retrain on a
BEAM-scale `chunks` table (multi-hour by design); a hard-forget request
arrives; the forget worker calls `purge_rows` and blocks inside
`pg_advisory_lock` indefinitely — no heartbeat, no retry, no operator signal.
The forget path is the one place D74 promises bounded, observable progress.

Required change: set a lock acquisition bound (`lock_timeout` on the lock
connection, or `pg_advisory_lock` guarded by `SET LOCAL lock_timeout` /
`statement_timeout` for the acquire statements, or a `pg_try_advisory_lock`
poll loop with a deadline), surface the timeout as a retryable forget-step
failure, and cover it with a test that holds one table lock from a second
session and proves the purge fails within the bound instead of hanging. The
helper's cleanup path already handles a timed-out acquire correctly (verified
below), so the bound composes with the existing code.

## Other required corrections

### R1 — The lock/purge protocol still has none of the regression tests the prior review required

Codex required four specific proofs; none were added, and the fix round
demonstrates why they matter (B1 shipped a failing test precisely because no
test observes lock-path behavior):

- PostgreSQL failure-injection for a failed second acquire and a failed first
  release, verifying from another session that all keys end free. I performed
  the failed-second-acquire experiment manually (see Verification) and the
  helper behaves correctly — turn that experiment into a committed test so the
  behavior can't regress; the failed-release case is still unexamined.
- A purge-versus-finalizer lock interaction regression (blocked purge waits;
  proceeds after release).
- A custom-root test proving the finalizer and the purge wrapper derive the
  same key for the same non-default `lance_root`.
- FTS drop-and-repair and ensure-only wrong-type repair companions to the new
  scalar-repair test. The wrong-type conversion path
  (`_ensure_typed_index`, e.g. the binding `facts.kind` BTree→Bitmap
  correction from §5.3.1) is live code with zero coverage.

### R2 — Stats contract remains partially unfulfilled on the rebuild paths

`ensure_search_indexes` and `optimize_tables` now report genuine per-table
durations, before/after snapshots, and (for optimize) real
`conflicts_retried` — that part of P2.2 is fixed. But
`rebuild_vector_indexes` / `rebuild_text_indexes`
(`adapters/selfhost/lance.py:1023-1075`) still populate only the *after*
snapshot, so every rebuild report carries `row_count_before=0` /
`num_fragments_before=0`, which reads as "the table was empty before the
rebuild" — worse than absent data for the PR4/PR5 consumers (change-mass and
escalation counters read these fields per §5.4/§5.7). `_create_index_with_retry`
also still returns no attempt count, so `conflicts_retried` is structurally 0
for ensure and both rebuilds. Complete the shape now, before PR3 makes it
durable in `p1_lance_table_stats`.

## What is fixed and verified

### P1.2 (lock leak) — fixed, verified by failure injection

`hold_p1_table_maintain_locks` (`spine/p1_maintain_lock.py:22-55`) now opens
the cleanup scope *before* acquisition, records each acquired key, releases
every recorded key even when one release raises, and invalidates the pooled
connection when the final commit cannot confirm cleanup. I injected a real
failure against PostgreSQL: session B held the second sorted key; session A
ran the helper with `statement_timeout=1000` so the second `pg_advisory_lock`
raised `QueryCanceled` mid-acquisition. Result: the first key was released
(cluster-wide advisory count returned to the blocker's single lock), the
victim engine's pooled connection came back holding zero advisory locks, and
the original `OperationalError` propagated un-masked. That is exactly the
contract the prior review demanded. Missing piece: the committed test (R1).

### P1.1 (unlocked hard-forget purge) — closed at the composition seam

`SelfHostHardForget.compose()` now wraps the raw adapter in `LockingP1Purge`
bound to the same engine and the exact `lance_root` it gives
`LanceChunkIndex` (`profiles/selfhost_forget.py:88-92`), so
`HardForgetHandler.honor()` reaches `delete` +
`optimize(delete_unverified=True)` only inside the four table locks, in the
same sorted-key order the helper gives every caller — one lock namespace, no
deadlock cycle with the one-at-a-time finalizer. Pyright confirms the wrapper
structurally satisfies `P1PurgePort` at the compose site.
`verify_rows_purged` stays outside the lock, which is right — it only counts
rows. Remaining gap is the wait bound (B2).

### P1.3 (ensure trusted caches) — fixed, verified by execution

`ensure_search_indexes` drops the per-table process-local ready flags
(`_forget_index_cache`, `adapters/selfhost/lance.py:1115-1120`) before
`_ensure_matrix_indexes`, so the explicit path re-reads `list_indices()`
through the list-first create helpers instead of returning on cached state.
The new regression `test_ensure_repairs_dropped_scalar_index` reproduces the
prior review's exact escape (same instance upserts → drop
`deployment_id` index out-of-band → explicit ensure) and proves the BTree
index reappears physically. It passes; the hot-path caches are re-primed
afterwards so read-path ensure stays cheap.

### P1.4 (lock root ambiguity) — fixed

`BackfillFinalizer` now requires `lance_root` (`spine/backfill.py:105-115`;
the silent `/var/lib/rememberstack/lance` default is gone), and the purge
wrapper binds adapter + engine + root in one object. A caller can still pass
a finalizer root that differs from the adapter's, but it must now do so
explicitly; the custom-root key test (R1) is the remaining ask.

### P2.1 (estate-wide finalizer hold) — fixed

The finalizer locks exactly one table around that table's ensure + heavy
rebuild (`spine/backfill.py:133-145`), matching §5.7's binding caller table.
A multi-hour chunks retrain no longer blocks claims/facts/entities
maintenance or purge.

## Nits

- `_optimize_with_retry`'s trailing `raise RuntimeError("optimize retries
  exhausted")` (`lance.py:1268`) is unreachable — the last attempt re-raises
  inside the loop; it exists to satisfy the type checker. Harmless; a comment
  would stop a future reader from hunting for the path.
- In the helper's cleanup, `raise release_errors[0]` can replace a
  propagating body exception (the original stays chained as `__context__`).
  Acceptable, but worth an `ExceptionGroup` or explicit chaining when the
  failure-injection tests land.
- The entity vector-type assertion the prior review flagged as too permissive
  (`test_lance_retrieval.py:587`, accepts `IVF_FLAT` *or* any vector-column
  index) is unchanged.

## Verification

Environment: local darwin, `REMEMBERSTACK_DATABASE_URL` pointed at a fresh
`ghcr.io/dbsystel/postgresql-partman` container (the CI image digest),
alembic migrated to head.

```text
uv run pytest src/tests/adapters/test_lance_retrieval.py -q
11 passed in 11.53s        # includes the new ensure-repair regression

uv run pytest src/tests/spine/test_backfill.py -q          # real PostgreSQL
1 failed, 2 passed in 80.19s
FAILED ...::test_search_indexes_build_only_after_backfill_has_drained  (B1)

uv run ruff check <touched files>      # All checks passed!
uv run pyright <touched files>         # 0 errors, 0 warnings, 0 informations
```

Lock failure injection (manual, scripted against the same database): failed
second acquire under `statement_timeout` → first key freed, pool connection
clean, original exception propagated (details under P1.2 above).

PR checks at review time: Quality, Unit, Contract smoke, Integration
(adapters), Compose quickstart, Path filters, PR gate green; **Integration
(workers)** and Integration (surfaces) pending — Integration (workers) will
fail on B1. The separate CLA check is failing, as at the prior review.

## Final assessment

The fix commit genuinely closes the two correctness hazards that mattered
most — the unlocked `delete_unverified` purge and the leak-prone lock
lifecycle — and I could not break the new helper under injected failure. But
the round also shipped a deterministic CI failure in the very test that
guards the finalizer it modified, the binding bounded-wait clause of §5.7
rule 5 is still unmet on the forget path, and the lock protocol still has no
committed adversarial coverage. Fix B1 and B2, land the R1 tests (the B1
rewrite and R1's finalizer-lock assertions are naturally one change), and
round out R2's rebuild-path stats; re-review should then be quick.

The D91 design source (PR #270) remains an open PR; land it before or with
this stack so the contract cited here exists on the target history. No
private source or connector was used for this review.
