# Round-11 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** APPROVE_WITH_NITS

**Reviewer:** Codex (gpt-5.6-sol)  
**Date:** 2026-08-11  
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `99c16adb`  
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`  
**Prior review:** `design/reviews/REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r10_2026-08-11.md`

## Summary

No blocking finding remains. `99c16adb` closes the r10 deadlock: the completion
transaction discovers the complete currently-visible representation set first,
acquires every normalize-barrier lock in deterministic representation-id order,
and only then marks the claim succeeded and evaluates the version barriers
(`src/rememberstack/spine/work_ledger.py:331-378`). There is no retained primary
lock before the sorted loop.

The primary D88 path is solid and mergeable for v1. The extract completion,
closed-set fan-out, already-succeeded replay check, claim completion, and
downstream enqueue remain on transactional/idempotent edges. The sibling
extract-readiness gate prevents the partial-D56 early fire from r9, while the
handler stages its generated observations for all occurrence versions visible
at execution time (`src/rememberstack/spine/work_ledger.py:384-404`,
`src/rememberstack/workers/e3.py:240-260`).

There are residual late-attach and historical-grid edges worth tracking. They
do not justify another request-changes round: a closed fan-out whose claims are
already succeeded explicitly re-evaluates the barrier, and generated
observation output always has at least its original payload-version staging
copy. I could not show permanent data loss from the residuals below.

## Blocking findings

None.

## r10 blocker resolution

### The multi-representation lock order is now correct

`complete_claim_normalize` now performs the steps in the required order:

1. Load the claim identity and discover the primary plus all currently visible
   D56 occurrence grids (`src/rememberstack/spine/work_ledger.py:331-362`).
2. Acquire one advisory lock per representation through `sorted(by_rep)`
   (`src/rememberstack/spine/work_ledger.py:363-367`).
3. Mark the processing row succeeded only after all those locks are held
   (`src/rememberstack/spine/work_ledger.py:368-374`).
4. Evaluate every gated candidate barrier and enqueue downstream work while the
   same locks and transaction remain active
   (`src/rememberstack/spine/work_ledger.py:378-429`).

All completion transactions therefore acquire any overlapping representation
locks in the same global order. Transactions that discover different subsets
cannot form the A-then-B / B-then-A cycle from r10: a subset still preserves the
same relative order for every shared key. Occurrences committed after the
snapshot also do not introduce an out-of-order lock into this transaction; the
later version's own closed fan-out performs its independent barrier recheck.

This satisfies the binding complete-plus-barrier serialization contract
(`plan/designs/e3_claim_level_normalize_fanout_design.md:154-179`). The remaining
fact that completion exceptions sit outside the handler's failure boundary is
not a reason to block this patch now that the introduced deterministic deadlock
path is gone (`src/rememberstack/workers/base.py:292-307`).

## Residual nits

### N1 — A version attached after the claim handler's staging snapshot has no version-local copy

The claim handler snapshots occurrence versions after normalization and stages
the generated assertions under those version ids; if none are returned it
retains the payload version as a fallback
(`src/rememberstack/workers/e3.py:240-260`). A D56 occurrence attached only
after that snapshot does not receive another version-keyed staging row. The
staging key and flush lookup are explicitly version-scoped
(`src/rememberstack/spine/fact_catalog.py:401-443,634-658`).

This can make a late version's flush a no-op even though it reused a claim that
generated an observation. It is not permanent loss on the shown path: at least
the original version owns the durable staging row, D43 apply and per-entity
staging retirement commit atomically, and a failed original flush remains
replayable (`src/rememberstack/spine/observation_adjudication.py:121-182`). In
addition, when the late version's extract barrier closes, fan-out evaluates the
normalize barrier even if every claim enqueue conflicts with an already-
succeeded row (`src/rememberstack/spine/work_ledger.py:735-791`).

For a later hardening pass, claim-owned durable normalized output copied or
joined when each version subscription closes would remove the dependency on
the original version's flush/replay and make version-local completeness more
explicit.

### N2 — Sibling discovery is occurrence-based rather than a durable D88 subscription

The sibling query returns every occurrence grid at the extractor generation,
including its `chunker_version`, without recording that the specific grid was
the one whose D88 fan-out closed
(`src/rememberstack/spine/work_ledger.py:1034-1044`). The new
`_extract_barrier_ready` check correctly excludes an incomplete active grid
(`src/rememberstack/spine/work_ledger.py:384-394`), but a retained closed legacy
grid or multiple closed chunker grids can still be considered. A sibling enqueue
also inherits `content_hash` and `lane` from the completing claim's retained
payload rather than loading sibling-owned values
(`src/rememberstack/spine/work_ledger.py:405-425`).

The ordinary v1 path is not dependent on that heuristic: the version's own last
extract completion carries its correct coordinates and atomically invokes the
fan-out (`src/rememberstack/spine/work_ledger.py:268-309`), and that fan-out
rechecks already-succeeded claims before enqueueing the flush. The residual is
therefore principally a cutover/rechunking metadata and duplicate-recheck edge,
not a demonstrated permanent-loss path. A durable closed-fan-out subscription
with version-owned hash/lane would make the recovery path exact.

### N3 — The new lock-order regression remains structural

The focused test asserts only that the method source contains sibling discovery,
the extract gate, and `sorted(by_rep)`
(`src/tests/workers/test_e3_claim_normalize_fanout.py:206-218`). That is adequate
to review the small r11 repair, but a future PostgreSQL test should record actual
lock invocation order for overlapping representation sets. Late-subscriber
staging and stale/multiple-grid selection would also benefit from explicit
regressions. The dedicated two-connection last-claim race remains expressly
deferred for v1 by the design
(`plan/designs/e3_claim_level_normalize_fanout_design.md:177-184`).

## Solid-path assessment

The previously reviewed invariants remain intact:

- extract completion and the complete claim fan-out share one transaction
  (`src/rememberstack/spine/work_ledger.py:268-309`);
- fan-out, expected-count, and succeeded-count queries use `chunk_claims` and
  pin deployment, version, representation, chunker, extractor, and normalizer
  generations (`src/rememberstack/spine/work_ledger.py:1226-1277`);
- claim payload membership is validated before normalization, and observation
  staging fans out to visible occurrence versions
  (`src/rememberstack/workers/e3.py:240-273`);
- observation flush ordering uses claim `asserted_at`, and D43 apply plus entity
  staging retirement is atomic
  (`src/rememberstack/spine/fact_catalog.py:650-659`,
  `src/rememberstack/spine/observation_adjudication.py:138-182`);
- hard forget scrubs normalize staging
  (`src/rememberstack/spine/forget.py:1242-1253`).

## Requested checks

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **32 passed in 5.78s**.

`git diff --check 99c16adb^ 99c16adb -- src` is also clean.

## Mergeability

**Yes.** `99c16adb` removes the out-of-order primary lock and restores a single
deterministic lock order before claim completion. Together with the already
landed extract gate, multi-version staging, pinned occurrence selectors,
source-time ordering, atomic staging retirement, and forget scrub, this is the
simplest solid mergeable v1. Track N1-N3 as follow-up hardening rather than
holding D88.
