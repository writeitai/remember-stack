# Round-3b implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Codex (gpt-5.6-sol)  
**Date:** 2026-08-11  
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `3ba0e918`  
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`

## Summary

The hard-forget delta fixes round-3 B4. The scrub now deletes every
`normalize_observation_staging` row for the deployment/document before claims
are deleted (`src/rememberstack/spine/forget.py:1249-1254,1360-1364`), and the
post-scrub residual query proves that none remains
(`src/rememberstack/spine/forget.py:1555-1561`). `doc_id` is non-null in the
staging schema, so the direct deployment/document predicate covers the staged
plaintext surface without depending on the claim join that forget later
removes. **Prior B4 is resolved.**

The other specifically called-out solid-path properties also remain in place:
claim retry always reruns the idempotent path rather than accepting a partial
write (`src/rememberstack/workers/e3.py:176-235`), connector-cycle finalization
waits on missing, pending, running, failed, and dead-lettered claim work
(`src/rememberstack/spine/lifecycle.py:1052-1069`), and claim completion retains
the dedicated transaction advisory lock around success, barrier evaluation, and
downstream enqueue (`src/rememberstack/spine/work_ledger.py:330-374`). The
binding design explicitly defers the two-connection race test for v1
(`plan/designs/e3_claim_level_normalize_fanout_design.md:177-184`), so its
absence is not a finding.

This is still not the simplest solid mergeable v1. The latest commit changes
only forget scrub SQL; round-3 B1-B3 remain. They are observable correctness
gaps against the binding fixed-set and source-time contracts, not requests for
extra architecture.

## Blocking findings

### B1 — Later extraction generations can enlarge an already-materialized barrier

The extract barrier is explicitly pinned to `extractor_version`
(`src/rememberstack/spine/work_ledger.py:287-303`), but that coordinate is not
passed into `_enqueue_claim_normalize_fanout`
(`src/rememberstack/spine/work_ledger.py:618-628`). Fan-out and both claim
barrier counts re-query all direct claims under only
`(representation_id, chunker_version)`
(`src/rememberstack/spine/work_ledger.py:1128-1163`). Derived readiness repeats
the same unpinned join (`src/rememberstack/spine/readiness.py:303-316`).

Claims are immutable, but a new extractor generation can append new claim rows
for the same representation/chunks. Those rows were not in the old atomic
fan-out, yet they immediately enlarge its later expected count. The old
normalizer generation can then wait on rows that were never enqueued, or absorb
work outside the closed handoff. This violates the binding definition of the
expected set as claims at the extract generation in force when the barrier
fired (`plan/designs/e3_claim_level_normalize_fanout_design.md:84-95`).

Carry `extractor_version` through the fan-out payload/barrier/readiness queries
and filter claims to it, or make later checks use the exact child set
materialized by the atomic handoff.

### B2 — Relation supersession still derives temporal direction from processing order

The evidence loaders now select each relation's latest supporting claim by
`asserted_at` (`src/rememberstack/spine/supersession.py:373-417`), but the
relation being processed is still unconditionally passed as `new` and every
open blocked relation as `old`
(`src/rememberstack/spine/supersession.py:163-193`). A supersede verdict then
closes `old` at `new["asserted_at"]` without orienting or swapping the pair by
source time (`src/rememberstack/spine/supersession.py:247-282`). The D88 selector
feeds relation IDs in UUID order, not temporal order
(`src/rememberstack/spine/fact_catalog.py:651-658`).

Therefore a source-older version that finishes second can be treated as the
successor and close a source-newer live relation at the older boundary. There
is an additional load-bearing gap in the current write path: `ClaimRecord` has
no `asserted_at` field and `_INSERT_CLAIM` does not persist one
(`src/rememberstack/model/claims.py:179-201`,
`src/rememberstack/spine/claim_catalog.py:266-282`), so ordinary newly extracted
claims fall back to completion-time `now()` in the closure SQL. D88's required
source-time direction cannot be obtained from those rows.

Persist the version's source assertion time on accepted claims, orient each
competing pair by that time before applying the verdict, and cover reversed
version completion.

### B3 — Observation ordering is per version, while continuous ingest is cross-version

One flush loads staging rows in `(asserted_at, claim_id, ...)` order
(`src/rememberstack/spine/fact_catalog.py:630-639`) and applies that version's
entity batch under the D43 entity lock
(`src/rememberstack/workers/e3.py:713-737`,
`src/rememberstack/spine/observation_adjudication.py:139-174`). Two version
flushes for the same entity can still acquire the lock in either order. The D43
candidate load does not carry existing source time
(`src/rememberstack/spine/observation_adjudication.py:723-732`), and a supersede
outcome always caps the existing observation at the incoming assertion time and
inserts the incoming assertion as successor
(`src/rememberstack/spine/observation_adjudication.py:407-448,747-754`).

Thus a 2019 version flushing after a 2024 version can cap the 2024 observation
at 2019; with the current null `asserted_at` extraction path, the fallback is
worker arrival time. The entity lock prevents concurrent writes but does not
provide the binding completion-order independence for continuous multi-document
ingest (`plan/designs/e3_claim_level_normalize_fanout_design.md:43-46,211-228`).

Make the D43 application source-time-aware across existing and incoming
assertions, or use a deterministic recomputation/ordering boundary that spans
the competing entity assertions, and cover both version completion orders.

## Non-blocking coverage note

Commit `3ba0e918` adds the correct scrub and proof SQL but no focused hard-forget
fixture case that seeds one matching staging row plus one control-document row.
Adding that regression would protect this plaintext-erasure contract, but the
simple scoped `DELETE` plus residual verification are sufficient to close the
known B4 defect; this test gap does not independently block merge.

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **22 passed in 5.89s**.

## Mergeability

**No.** The hard-forget staging leak is fixed, partial-skip is gone, connector
cycle waiting is claim-aware, the barrier lock is retained, and the race test
is validly deferred. B1-B3 still make the fan-out set and temporal fact result
depend on later extraction or worker/version completion order, so the branch is
not yet a solid v1 merge.
