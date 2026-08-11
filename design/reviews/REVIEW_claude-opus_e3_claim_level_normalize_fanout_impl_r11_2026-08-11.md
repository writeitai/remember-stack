# Round-11 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** APPROVE_WITH_NITS

**Reviewer:** Claude (opus-5)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `99c16adb`
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`
**Peer review this round:** `design/reviews/REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r11_2026-08-11.md`

## Summary

I confirm Codex r11's central finding. `complete_claim_normalize` now runs the
required sequence — discover the representation set, take every barrier lock in
one deterministic order, mark succeeded, then evaluate barriers — and the r10
deadlock path is closed. I re-derived the deadlock-freedom argument from the
lock primitives rather than from the call order alone, and it holds for reasons
Codex did not check but which are worth recording (below).

One item Codex missed: **the branch is currently red.** A migration was added
without registering it in the linear-chain assertion, so
`src/tests/spine/test_migrations.py::test_revision_graph_is_one_linear_structural_chain`
fails. It is a one-line test-data update with no behavioral or design
consequence, but it must land before merge.

I found one additional testing nit: the structural test that nominally guards
the r11 fix cannot actually detect its reversion.

I agree with Codex's N1–N3 as non-blocking, and verified N1 at the cited code.

## Confirmed: the completion sequence is correct

`complete_claim_normalize` (`src/rememberstack/spine/work_ledger.py:311-429`)
performs, inside one `engine.begin()` transaction:

1. Load the claim identity for the processing row
   (`src/rememberstack/spine/work_ledger.py:332-334`).
2. Discover the primary coordinates plus every other version that lists this
   claim as a D56 occurrence, grouped by representation
   (`src/rememberstack/spine/work_ledger.py:335-362`).
3. Acquire one advisory lock per representation through `sorted(by_rep)`
   (`src/rememberstack/spine/work_ledger.py:363-367`).
4. Mark the row succeeded, only now
   (`src/rememberstack/spine/work_ledger.py:368-374`).
5. Enqueue handler follow-ups, then evaluate each candidate's extract gate and
   normalize barrier and enqueue the observation flush, all under the same held
   locks (`src/rememberstack/spine/work_ledger.py:375-428`).

The `99c16adb` diff removes the previously retained single-representation lock
that ran before the sorted loop, so no lock is taken out of order. That matches
the design's complete-plus-barrier serialization contract
(`plan/designs/e3_claim_level_normalize_fanout_design.md:154-179`).

### Why the sorted order is actually sufficient (checks Codex did not make)

Sorting a dictionary's keys only yields a global lock order if the keys are
canonical and the lock namespace is not shared with a differently-ordered path.
Both preconditions hold here, and neither is self-evident from the diff:

- **The sort key is canonical.** `by_rep` is keyed by `str(...)` of a
  representation id. The primary key comes from a pydantic-typed `UUID` field
  (`src/rememberstack/workers/base.py:64-67`); the sibling keys come from
  `chunks.representation_id`, declared `uuid NOT NULL`
  (`src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py:544`),
  which the driver returns as `uuid.UUID`. Every key is therefore the same
  canonical lowercase hex form, so the lexicographic string sort is a total
  order that is identical in every transaction. Had either side been stored as
  `text`, two transactions could have sorted the same representation
  differently and the fix would not have worked. It is not, so the fix does.
- **The lock namespace is not shared with a single-lock path that could invert
  the order.** The two advisory locks hash distinct namespace prefixes —
  `d84-representation:` (`src/rememberstack/spine/work_ledger.py:1208-1214`)
  and `d88-normalize-barrier:`
  (`src/rememberstack/spine/work_ledger.py:1216-1224`) — so they are different
  keys. `complete_chunk_extract` takes only the D84 lock
  (`src/rememberstack/spine/work_ledger.py:273-276`), and neither
  `_enqueue_claim_normalize_fanout` nor `_extract_barrier_ready` takes any
  advisory lock. No D84-then-D88 / D88-then-D84 cycle can form.
- **Both locks are transaction-scoped.** They use `pg_advisory_xact_lock`, not
  the session variant. The `WorkNotRunningError` raised at
  `src/rememberstack/spine/work_ledger.py:371-374` now fires while locks are
  held; because the raise aborts the transaction, PostgreSQL releases them. The
  reordering did not introduce a lock leak on the not-running path.
- **The row locks underneath do not reintroduce a cycle.** Two concurrent
  extract completions for different representations of a version can both try
  to insert the normalize row of a shared D56 claim. The fan-out selects claims
  `ORDER BY cl.ingested_at, cl.claim_id`
  (`src/rememberstack/spine/work_ledger.py:1239`), a total order, so the
  relative insert order of any shared claim is the same in both transactions —
  a plain wait, not a cycle.

## Must-fix before merge

### M1 — The branch fails `test_migrations.py`; a new migration is not registered in the chain assertion

The branch adds `p9_08_0029_normalize_claim_fanout` (revision `p9_08_0029`,
down-revision `p9_07_0028` —
`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:12-13`).
`test_revision_graph_is_one_linear_structural_chain` asserts the full revision
tuple literally (`src/tests/spine/test_migrations.py:81`), and that test file is
untouched by this branch, so its tuple still ends at `p9_07_0028`:

```text
E  AssertionError: Left contains one more item: 'p9_08_0029'
src/tests/spine/test_migrations.py:81: AssertionError
1 failed, 151 passed, 286 skipped in 28.82s
```

I verified this is a regression from this branch and not pre-existing: on `main`
the last migration file is `p9_07_0028_chunk_extract_indexes.py`, which matches
main's asserted tuple.

The revision chain itself is correct — `p9_08_0029` descends from `p9_07_0028`,
keeping the graph linear. Only the test's expected tuple is stale. The fix is to
append `"p9_08_0029"` to the tuple at `src/tests/spine/test_migrations.py:81`.

Codex r11 reported 32 passed because it ran three targeted files
(`test_e3_claim_normalize_fanout.py`, `test_chunk_level_extract.py`,
`test_selfhost_profile.py`); the migration test is outside that set.

## Nits

### N4 — The structural test for the r11 fix cannot detect the fix being reverted

`test_claim_complete_rechecks_sibling_version_barriers` asserts
`"sorted(by_rep)" in source` (`src/tests/workers/test_e3_claim_normalize_fanout.py:206-218`).
That substring occurs **twice** in the method — once for the lock loop
(`src/rememberstack/spine/work_ledger.py:363`) and once for the barrier loop
(`src/rememberstack/spine/work_ledger.py:378`). The assertion therefore passes
unchanged if someone moves the lock loop back below `_COMPLETE`, which is
exactly the r10 defect `99c16adb` repaired. The test pins that sorting happens
somewhere, not that locking precedes completion.

Since the test is already source-inspecting, making it pin the ordering is
nearly free — compare the source offset of `_ADVISORY_LOCK_NORMALIZE_BARRIER`
against that of `_COMPLETE` and require the lock to come first. (I checked the
current offsets: 2539 vs 2707, so such an assertion passes today.) This is a
sharper form of Codex N3, which asked for a future PostgreSQL lock-order test;
that remains the real regression guard, but the cheap structural version should
not be left mis-scoped in the meantime.

### N1–N3 (Codex) — agreed, non-blocking

I re-checked N1 at the cited code and it reads as described: the handler
snapshots occurrence versions after normalization and stages generated
assertions under each, falling back to the payload version when the query
returns nothing (`src/rememberstack/workers/e3.py:243-249`), so a version that
attaches after the snapshot gets no version-local staging row. I agree this is
not permanent loss on the shown path.

On N2, one detail supports Codex's "duplicate-recheck, not loss" reading: the
work-row idempotency key is
`(deployment_id, target_kind, target_id, stage, component_version)` and excludes
`content_hash` (`src/rememberstack/spine/work_ledger.py:898-917`). So if two
candidate grids for one version both pass the gates, the second flush enqueue
collapses onto the existing row rather than creating a competing one — the first
candidate's `chunker_version` payload wins. Benign for v1, and a further reason
the durable per-version subscription Codex proposes is the right eventual fix.

N3 I agree with as written, subject to N4 above.

## Checks run

```text
uv run pytest src/tests/workers/ src/tests/spine/ src/tests/profiles/ -q
→ 1 failed, 151 passed, 286 skipped in 28.82s
```

The single failure is M1. No E3/D88 test fails.

## Mergeability

**Yes for v1, after M1.** The D88 solid path is correct and the r10 deadlock is
genuinely closed — the sorted-lock repair rests on preconditions I checked
independently (canonical uuid sort keys, disjoint lock namespaces,
transaction-scoped locks, deterministic fan-out insert order), and all of them
hold. The only thing standing between this branch and a green merge is the
one-line migration-chain tuple update; it carries no design risk. Track N1–N4 as
follow-up hardening rather than another review round.
