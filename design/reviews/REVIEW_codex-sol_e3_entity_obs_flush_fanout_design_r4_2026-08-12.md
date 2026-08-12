# Round-4 design review: D90 entity-grain observation flush fan-out

**AGENT:** `codex-sol`  
**Date:** 2026-08-12  
**Branch:** `design/d89-entity-obs-flush-fanout` at `6cc784ac`  
**Scope:** final re-review of §1.7, §5.5, and §5.5.1 after the Codex r3 B1 absorb

## Verdict

**REQUEST_CHANGES** — the new entity-global merge closes the r3
evidence-collapse trace when both units' staging is present before the drain,
but it does not close the same D43 failure across a valid continuous-ingest
schedule where the second unit is materialized later. The binding handler
contract also still describes the old per-unit load/apply.

This is within the requested blocker threshold: a unit set can succeed and
enqueue downstream work while leaving the wrong current observation and
history.

## Focus answer

For two already-materialized units, **yes**. The §1.7/§5.5 stream reads
`{t1:A, t3:A}` and `{t2:B}` together and applies the merged sequence
`t1:A, t2:B, t3:A`. D43 therefore produces exactly
`A[t1,t2), B[t2,t3), A[t3,∞)`. This directly fixes the `min_asserted_at`
unit-slice failure from r3.

It is not yet a complete cross-version ordering contract, however. "All
unapplied staging" is only the set that exists when the stream runs, and
successfully applied rows are deleted. D90 explicitly supports versions of the
same entity arriving at different times; there is no entity-wide finality or
source-time watermark that makes the first drain complete.

## Remaining blocker

### B1 — Later unit materialization can still lose the `t3:A` state

A valid schedule under the current text is:

1. Unit A is materialized first with `{t1:A, t3:A}`. Its entity-global drain
   sees no Unit B staging. D43 creates A at `t1`, collapses `t3:A` as evidence
   onto that open observation, deletes both staging rows, and succeeds Unit A.
2. Unit B is materialized later with `{t2:B}`. Its entity-global drain now has
   only `t2:B` to apply. It caps A at `t2`, inserts B open, deletes the row, and
   succeeds Unit B.
3. The final state is `A[t1,t2), B[t2,∞)`, not the source-ordered
   `A[t1,t2), B[t2,t3), A[t3,∞)`. The durable `t3:A` assertion may still be
   recoverable through claim/evidence history, but it is no longer in the
   "unapplied staging" stream and the design does not require replaying it.

§5.5.2 does not close this: entity-local recompute is optional, and a validity
window re-cap alone cannot reverse the evidence collapse, as §5.5.1 correctly
states. This is the same wrong D43 truth as the r3 case, with staggered rather
than co-present unit materialization.

There is also a binding internal conflict for the co-present case. §1.7 and
§5.5 require the global drain, while §5.3.2–5 still require loading, applying,
clearing, and completing only the triggering version slice. §5.6 likewise
continues to bind locking and preparation around a "unit apply." An
implementation following §5.3 can therefore retain the exact unit-at-a-time
behavior §5.5.1 rejects.

**Required closure:**

- Make §5.3 and §5.6 describe the entity-global load/apply and the completion
  of every drained unit, rather than a triggering-unit slice.
- Bind a schedule-independent path for an assertion whose total-order key
  precedes already-applied entity history. That can be a semantic replay from a
  durable complete assertion source or another mechanism, but it must rebuild
  evidence attachment, observation slices, contradiction/adjudication outcomes,
  and counts—not only re-cap existing validity windows.
- Add the staggered acceptance case: fully succeed Unit A
  `{t1:A,t3:A}`, then materialize/apply Unit B `{t2:B}`, and still require
  `A[t1,t2), B[t2,t3), A[t3,∞)`.

## Nits

- §1.5 and §5.3.4 still point whole-apply locking to §5.7; the locking contract
  is §5.6.
- §10 still says reverse-arrival recompute is the §5.5.1 exception, but the
  revised §5.5.1 no longer binds a recompute.

## Final recommendation

**Merge design? No.** Keep entity-global merge-apply—it is the correct fix for
all co-present staging and directly closes the original r3 trace. Extend it with
a required late-arrival semantic replay (and align the handler contract) so the
same evidence-collapse case cannot recur merely because Unit B is materialized
after Unit A succeeds.
