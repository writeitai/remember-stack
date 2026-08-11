# Round-9 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Codex (gpt-5.6-sol)  
**Date:** 2026-08-11  
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `894e9804`  
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`  
**Prior review:** `design/reviews/REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r8_2026-08-11.md`

## Summary

`894e9804` fixes the narrow missed recheck identified in r8: after the shared
claim row is marked succeeded, `complete_claim_normalize` loads its actual
`target_id`, discovers every `chunk_claims` occurrence, takes each candidate's
normalize-barrier lock, and evaluates the candidate barrier
(`src/rememberstack/spine/work_ledger.py:331-416,1016-1032`). In the exact
closed-V2/all-reused sequence from r8, this can now enqueue V2's observation
flush. The CI fixes are also complete: the requested tests, targeted format
check, full configured format check, and full pyright check all pass.

The occurrence scan is not yet a version-barrier subscription, though. It can
see a version before that version's extract barrier closes, and it can see old
chunker grids or pre-D88 versions that never materialized this D88 fan-out. It
therefore can open a sibling barrier over a partial or stale expected set. In
addition, the one shared claim execution still stages observations only for the
version in its retained work payload, while the new code starts independent
flushes for sibling versions. Those sibling flushes do not contain the shared
claim's staged observations and inherit the original version's content hash and
lane. Finally, completion now acquires an unbounded set of representation locks
without a global order, introducing a deadlock path after the handler's retry
boundary.

These are production correctness/availability issues, not residual test-quality
nits, so this is not yet the simplest solid mergeable v1.

## Blocking findings

### B1 — A claim occurrence is not proof that the sibling version has a closed D88 barrier

The binding expected set is one **closed** version, fixed when that version's
extract barrier succeeds
(`plan/designs/e3_claim_level_normalize_fanout_design.md:68-76,84-100`). D56,
however, writes a `chunk_claims` occurrence while processing each individual
chunk (`src/rememberstack/spine/claim_catalog.py:79-106,269-278`), before D84 has
necessarily completed the other chunks and atomically fired the full fan-out
(`src/rememberstack/workers/e2.py:280-323`,
`src/rememberstack/spine/work_ledger.py:268-308`).

`_VERSIONS_WITH_CLAIM_OCCURRENCE` selects every grid with the claim, with no
condition that the grid's extract barrier closed or its D88 fan-out was
materialized (`src/rememberstack/spine/work_ledger.py:1022-1032`). The subsequent
barrier query counts whatever occurrences are visible at that instant
(`src/rememberstack/spine/work_ledger.py:381-389,1231-1265`). A concrete early
fire is:

1. V1 owns shared claim work C, which remains running.
2. One V2 chunk finishes D56 reuse and publishes C's occurrence; another V2
   chunk has not yet completed extract and will later contribute fresh claim D.
3. C completes. The new occurrence scan discovers V2. At this moment V2's
   visible expected set is only `{C}`, so expected and succeeded counts match
   and V2's observation flush is enqueued.
4. The remaining extract later adds D and the real fan-out runs. The observation
   flush identity is only `(deployment, version target, stage, component
   version)`, so its correct re-enqueue conflicts and does not replace the
   prematurely retained payload (`src/rememberstack/spine/work_ledger.py:859-925,945-970`).

V2 can consequently flush, adjudicate, embed, reconcile, and report downstream
progress before D ever normalizes. The same lack of subscription scoping lets
the scan consider historical chunker grids and pre-D88 versions merely because
they contain the claim; the selected candidate key explicitly includes every
`c.chunker_version` (`src/rememberstack/spine/work_ledger.py:1024-1031`).

The completion recheck must target only version barriers that were registered
atomically when their extract barrier closed, with the exact version,
representation, chunker, extractor, normalizer, content hash, lane, and doc
coordinates. A durable per-version subscription/manifest remains the direct v1
shape; raw occurrence discovery is too early and too broad.

### B2 — Sibling flushes do not own the shared claim's staged observations or version coordinates

There is still one claim work payload. The claim handler stages every generated
observation under that payload's single `version_id`
(`src/rememberstack/workers/e3.py:240-249`), and staging is version-keyed both on
write and read (`src/rememberstack/spine/fact_catalog.py:401-443,634-658`). The
new completion path does not copy or otherwise expose those rows to subscribed
sibling versions; it only enqueues their flushes
(`src/rememberstack/spine/work_ledger.py:391-415`). A sibling flush therefore
loads only its own version and sees none of C's observations
(`src/rememberstack/workers/e3.py:723-766`).

That breaks independent barriers in the exact r8 scenario. V2's no-op flush can
succeed and continue even if V1's flush later fails, so V2 can be declared done
without C's observations ever reaching D43. It also defeats the binding ordered
flush contract: the sibling version is not flushing the staged assertions of
its complete expected claim set
(`plan/designs/e3_claim_level_normalize_fanout_design.md:140-144,293-307`).

The sibling enqueue also uses `barrier.content_hash` and `barrier.lane` from the
original retained work row rather than V2's values
(`src/rememberstack/spine/work_ledger.py:394-412`). Those fields are not selected
by `_VERSIONS_WITH_CLAIM_OCCURRENCE`, so a later version can be routed and
chained with an earlier version's provenance. Because conflict handling keeps
the existing work payload and only conditionally promotes a pending backfill
row, the later correct fan-out does not generally repair that metadata
(`src/rememberstack/spine/work_ledger.py:892-925`).

A version subscription must own the downstream coordinates and define how one
claim-normalize result becomes staged input for every participating version
(for example, version-neutral claim output copied/joined into each subscribed
flush). Merely rechecking and enqueueing the sibling barrier is not sufficient.

### B3 — Multi-representation advisory locks are acquired in no stable order, and completion failures bypass retry handling

The old path acquired one representation lock. The new path acquires the
payload representation first, then additional representations in the database's
unordered `SELECT DISTINCT` result (`src/rememberstack/spine/work_ledger.py:331-334,361-380,1022-1032`). Overlapping shared claims can require overlapping
sets in different orders. For example, a long-running claim introduced in V1
may span V1/V2/V3 while a claim introduced in V2 spans V2/V3; an unordered first
scan can hold V3 while waiting for V2 as the other completion holds V2 while
waiting for V3. PostgreSQL will abort one transaction as a deadlock.

That exception is especially unsafe here: `WorkerRunner` catches handler
exceptions only through line 291, then calls `complete_claim_normalize` outside
that `try` block (`src/rememberstack/workers/base.py:223-303`). A deadlock during
completion therefore does not call `ledger.fail`; `_COMPLETE` rolls back and the
processing row remains `running`, while `claim_one` only selects `pending` and
`failed` rows (`src/rememberstack/spine/work_ledger.py:987-1000`).

If one completion transaction must hold multiple representation locks, resolve
the registered candidate set first and acquire every lock in one deterministic
global order before changing work state. The completion call also needs the same
retry/failure boundary as handler execution, or an equivalent recovery
guarantee.

## Test assessment

The new test verifies only that the method's source mentions
`_VERSIONS_WITH_CLAIM_OCCURRENCE` and that the SQL contains two tokens
(`src/tests/workers/test_e3_claim_normalize_fanout.py:206-216`). It does not run
the ledger, prove a closed V2 gets one flush, distinguish a partial/pre-fan-out
V2, inspect sibling staging or coordinates, or exercise multiple locks. It
would pass with all three blockers above.

For the replacement design, add PostgreSQL regressions for:

- closed all-reused V2 and mixed V2 with the shared claim last: exactly one V2
  flush and complete version-owned coordinates;
- a shared occurrence visible before V2's last extract completes: no early
  flush;
- the shared claim's observation output being available to each independent
  version flush;
- a stale chunker/pre-D88 occurrence not becoming a D88 barrier subscription;
- deterministic lock acquisition for overlapping subscription sets.

The dedicated two-connection last-claim race remains explicitly deferred by the
v1 design (`plan/designs/e3_claim_level_normalize_fanout_design.md:177-184`), but
the new multi-lock behavior is a separate risk introduced by this fix.

## Solid-path and CI recheck

The r8 solid path is otherwise unchanged: D84 completion and fan-out remain one
transaction; the ordinary single-version claim barrier still uses its dedicated
advisory lock; expected/ready counts retain deployment/version/representation/
chunker/extractor/normalizer pins; readiness and connector-cycle membership use
the D56 occurrence map; observation apply and staging retirement are atomic;
source-time supersession/reverse-arrival handling and hard-forget staging scrub
remain intact.

Requested tests:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **32 passed in 6.04s**.

Requested format check:

```text
uv run ruff format --check src/rememberstack/spine/work_ledger.py \
  src/rememberstack/spine/supersession.py \
  src/tests/workers/test_e3_claim_normalize_fanout.py
```

Result: **passed — 3 files already formatted**.

Additional CI checks:

```text
uv run ruff format --check src/ benchmarks/
```

Result: **passed — 361 files already formatted**.

```text
uv run pyright src/ benchmarks/ --pythonversion 3.13
```

Result: **passed — 0 errors, 0 warnings, 0 informations**.

`git diff --check 894e9804^ 894e9804 -- src` is also clean.

## Mergeability

**No.** `894e9804` repairs the one-version-only recheck and clears the prior CI
blockers, but the substitute fan-out mechanism does not identify closed,
registered version barriers, does not carry shared observation output or
version-owned enqueue metadata to sibling versions, and acquires multiple
locks without a stable order. Preserve the useful target-id lookup, but put
sibling participation on a durable version-scoped contract before merging.
