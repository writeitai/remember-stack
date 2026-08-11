# Round-10 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Codex (gpt-5.6-sol)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `e09a2d31`
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`
**Prior review:** `design/reviews/REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r9_2026-08-11.md`

## Summary

`e09a2d31` makes useful progress on all three r9 residuals. Sibling barrier
rechecks now require that candidate grid's extract barrier to be ready,
candidate groups are iterated by sorted representation id, and a running claim
handler copies its generated observation assertions to every occurrence version
visible at that point (`src/rememberstack/spine/work_ledger.py:376-407`,
`src/rememberstack/workers/e3.py:240-260`). The requested 32 tests and targeted
Ruff format check pass.

The fixes do not yet close the underlying production paths, however. The old
primary-representation lock is still acquired before the new sorted loop, so
the multi-lock deadlock remains. Observation output is copied only during the
one shared handler execution, leaving later reuse of an already-succeeded claim
with no version-owned staging. Finally, extract readiness proves that a grid is
closed, not that it is the D88-subscribed grid; historical/pre-D88 and stale
chunker grids remain eligible, and the first such sibling candidate can retain
the wrong payload and routing coordinates.

These are correctness/availability defects in normal shared-claim and cutover
flows, not the explicitly deferred PostgreSQL two-last-claim race test. This is
therefore not yet the simplest solid mergeable v1.

## Blocking findings

### B1 — The retained primary lock still defeats the sorted global lock order

`complete_claim_normalize` takes `barrier.representation_id` immediately on
entry (`src/rememberstack/spine/work_ledger.py:331-335`). Only after that does it
discover the complete occurrence set and enter the new sorted lock loop
(`src/rememberstack/spine/work_ledger.py:346-381`). Re-locking the primary in
the sorted loop is harmless by itself, but the transaction has already acquired
that lock out of order.

Two shared claims can still deadlock when concurrent fan-outs leave one claim's
retained payload on representation A and another's on B while both occur on
A and B:

1. C1 completion acquires A at line 332.
2. C2 completion acquires B at line 332.
3. C1's sorted loop re-enters A and waits for B.
4. C2's sorted loop waits for A while retaining B.

No database invariant requires overlapping claims to retain the same primary
representation: each globally unique claim row keeps the payload of whichever
version fan-out inserted that claim first (`src/rememberstack/spine/work_ledger.py:738-762`,
`src/rememberstack/spine/work_ledger.py:907-941`). Replays and out-of-order
version processing can therefore produce the opposing primary-lock sets above.

PostgreSQL will abort one completion transaction. That remains a stranded-work
failure: the runner's exception boundary ends after the handler, while
`complete_claim_normalize` is invoked afterward
(`src/rememberstack/workers/base.py:223-303`). The aborted completion rolls back
the success update, but the earlier claim transaction already left the row
`running`; claim selection only admits `pending` and `failed`
(`src/rememberstack/spine/work_ledger.py:1002-1015`).

Resolve the full representation set first and acquire every barrier lock exactly
once in the same deterministic order before marking the row succeeded. The
source-level assertion for `sorted(by_rep)` does not prove this property
(`src/tests/workers/test_e3_claim_normalize_fanout.py:206-218`).

### B2 — One-time “currently visible” staging does not support later reuse of a succeeded shared claim

The new handler query snapshots the occurrence versions once, after generation,
and writes version-keyed staging only for those results
(`src/rememberstack/workers/e3.py:240-260`,
`src/rememberstack/spine/claim_catalog.py:170-183`). The staging primary key and
flush lookup are version-scoped
(`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:22-34`,
`src/rememberstack/workers/e3.py:749-755`). There is no subsequent copy path.

A fully sequential production failure remains:

1. V1's handler normalizes shared claim C, stages C only for the then-visible
   V1 occurrence, and C succeeds.
2. V1's observation flush stays pending or later dead-letters.
3. A later V2 extraction reuses C and atomically materializes V2's fan-out.
4. Enqueueing C conflicts with the already-succeeded shared claim row, so the
   handler does not run again; the ready check immediately enqueues V2's flush
   (`src/rememberstack/spine/work_ledger.py:738-794`,
   `src/rememberstack/spine/work_ledger.py:907-941`).
5. V2's flush loads no V2 staging but still succeeds and chains supersession and
   embedding (`src/rememberstack/workers/e3.py:751-783,799-830`).

V2 can therefore finish without C's observation ever reaching D43 if V1's
flush fails. The same gap exists if an occurrence is attached after the handler
snapshot but before claim completion. This is not the design's deferred
two-connection *last-claim barrier* test; it follows directly from the durable
one-row-per-claim identity and ordinary D56 reuse.

The version barrier needs a durable way to consume the normalized claim's
observation output even when the version subscribes after the claim job has
succeeded—for example, claim-owned durable output joined/copied when each
version fan-out closes, or an equivalent version subscription contract. Merely
copying to versions visible during the original handler run is insufficient.

### B3 — Extract readiness does not identify a D88-subscribed grid or preserve sibling coordinates

The new `_extract_barrier_ready` gate correctly prevents the partial-V2 early
fire described in r9 (`src/rememberstack/spine/work_ledger.py:387-397`). But
`_VERSIONS_WITH_CLAIM_OCCURRENCE` still selects every occurrence grid at the
extractor version, without evidence that this version/grid participated in the
D88 fan-out generation (`src/rememberstack/spine/work_ledger.py:1037-1047`). A
closed pre-D88 version or a retained old chunker grid can satisfy extract
readiness. If its shared claims have since succeeded through other versions,
the claim barrier also returns ready and opens a new-generation observation
flush for that stale candidate (`src/rememberstack/spine/work_ledger.py:398-431`,
`src/rememberstack/spine/work_ledger.py:798-833`). That contradicts the cutover
contract: pre-fanout version success is ready only under the old generation, and
old serial work drains without becoming a D88 coordinator
(`plan/designs/e3_claim_level_normalize_fanout_design.md:230-239`).

Multiple closed chunker grids for one sibling version make this nondeterministic:
the occurrence query has no order, while observation-flush identity is only the
version target plus stage/component version. The first eligible grid retains
its payload because later enqueue conflicts do not replace it
(`src/rememberstack/spine/work_ledger.py:907-941`). The flush then uses that
retained `chunker_version` to choose its claims and relations
(`src/rememberstack/workers/e3.py:784-795`).

Sibling enqueueing also still copies `content_hash` and `lane` from the
completing claim's retained primary payload, not from the sibling version
(`src/rememberstack/spine/work_ledger.py:411-428`). Thus a steady sibling can be
routed as backfill and carry another version's content provenance.

The sibling set must be scoped to closed D88 version subscriptions/manifests
with version-owned representation, chunker, extractor, normalizer, hash, lane,
and document coordinates. A raw occurrence plus extract-ready check does not
provide that contract.

## Test assessment

The requested tests remain useful and green, but the new coverage is structural:

- the completion test checks only that `_extract_barrier_ready` and
  `sorted(by_rep)` occur in the method source
  (`src/tests/workers/test_e3_claim_normalize_fanout.py:206-218`);
- the claim-handler test returns only one version from the new catalog method
  and asserts merely that some staging occurred
  (`src/tests/workers/test_e3_claim_normalize_fanout.py:349-382,488-496`).

Neither test exercises actual lock acquisition order, a late subscriber to an
already-succeeded claim, a stale/pre-D88 grid, sibling-owned lane/hash, or the
version-specific contents of the flush. Add focused regressions for those
paths; the dedicated two-connection last-claim race may remain deferred exactly
as D88 allows (`plan/designs/e3_claim_level_normalize_fanout_design.md:177-184`).

## Solid-path and requested checks

The previously solid implementation remains intact outside these blockers:
extract completion and the complete claim fan-out share one transaction;
expected/readiness/selectors use `chunk_claims` with deployment, version,
representation, chunker, extractor, and normalizer pins; claim membership
validation remains in place; `asserted_at` drives supersession orientation and
observation reverse-arrival behavior; earliest-boundary pull and cap clamp are
preserved; D43 apply plus per-entity staging retirement is atomic; hard forget
scrubs staging; connector-cycle waiting is presence-only; and the conceptual
multi-version completion recheck now exists.

Requested tests:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **32 passed in 4.44s**.

Requested format check:

```text
uv run ruff format --check src/rememberstack/spine/work_ledger.py \
  src/rememberstack/workers/e3.py \
  src/tests/workers/test_e3_claim_normalize_fanout.py
```

Result: **passed — 3 files already formatted**.

`git diff --check e09a2d31^ e09a2d31 -- src` is also clean.

## Mergeability

**No.** `e09a2d31` closes the partial-extract symptom and adds the right
multi-version concepts, but the actual lock sequence is still deadlockable, the
one-time staging snapshot cannot serve later subscribers to a succeeded shared
claim, and occurrence discovery still admits stale/non-D88 grids with borrowed
downstream coordinates. Close those three production paths before merging v1.
