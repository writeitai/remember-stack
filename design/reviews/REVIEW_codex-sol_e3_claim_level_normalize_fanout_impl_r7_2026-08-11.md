# Round-7 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Codex (gpt-5.6-sol)  
**Date:** 2026-08-11  
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `ec64a05f`  
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`  
**Prior review:** `design/reviews/REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r6_2026-08-11.md`

## Summary

The two r6 blockers are fixed in their edited production paths. Fan-out and
both barrier counts now traverse the D56 occurrence map
(`src/rememberstack/spine/work_ledger.py:1156-1207`), the claim handler checks
membership through `claim_occurs_on_chunks`
(`src/rememberstack/workers/e3.py:184-202`), and the version-scoped relation
selector now loads occurrences through `chunk_claims`
(`src/rememberstack/spine/claim_catalog.py:341-349`). Observation apply and
entity staging retirement now execute inside the same `engine.begin()` block
(`src/rememberstack/spine/observation_adjudication.py:144-182`), closing the
apply/commit/delete crash window rather than merely narrowing it. The new
earliest-boundary guard also refuses a pull when another live slice has a later
cap (`src/rememberstack/spine/observation_adjudication.py:673-718,929-941`).

One production correctness hole remains in the same D56 cutover case. The
fan-out and barrier see reused occurrences now, but public readiness and the
connector-cycle wait still discover claims through immutable origin
`claims.chunk_id`. A reused current-version claim can therefore be pending,
running, failed, or dead-lettered while those two consumers treat the current
version as having no such claim. This violates the binding readiness and cycle
rules and can let a healthy connector cycle run absence-based closure before
normalization is complete.

This is not yet the simplest solid mergeable v1.

## Blocking finding

### B1 — D56 reused claims are still absent from readiness and connector-cycle waiting

The schema is explicit that `claims.chunk_id` is immutable origin provenance
and `chunk_claims` is the exact per-version occurrence map
(`src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py:570-595`).
Commit `ec64a05f` correctly applies that rule to fan-out, barrier evaluation,
handler membership, and the supersession selector, but two binding consumers
still use the origin edge:

- `_NORMALIZE_CLAIM_STATUS` joins `cl.chunk_id = c.chunk_id`
  (`src/rememberstack/spine/readiness.py:308-322`). For a representation whose
  claims were reused from older chunks, `count(cl.claim_id) = 0`, so its first
  CASE arm reports normalize `succeeded`
  (`src/rememberstack/spine/readiness.py:289-303`) even if a current-generation
  claim row is pending or dead-lettered. The derived row unconditionally
  replaces any version-level coordinator status
  (`src/rememberstack/spine/readiness.py:147-155`).
- `_SELECT_READY_CYCLES` also joins current-version chunks directly to
  `claims.chunk_id` (`src/rememberstack/spine/lifecycle.py:1057-1067`). A
  current-version D56 occurrence whose origin chunk belongs to an earlier
  version is invisible, so its existing pending/running/failed/dead-lettered
  normalize row does not block finalization.

The cycle case is data-visible. Once current-version chunk extraction has
succeeded, an all-reused version need not have any other version-level work row
that blocks the query while its claim normalize row is unfinished. The cycle
can then be claimed and healthy-cycle zero-support closure runs
(`src/rememberstack/workers/reconcile.py:311-321`) despite the design requiring
claim dead letters to block readiness and connector-cycle finalization
(`plan/designs/e3_claim_level_normalize_fanout_design.md:230-239`). A mixed
version has the same failure once its fresh claims succeed while a reused claim
is the unfinished one.

Use `chunk_claims` for the expected occurrence set in both readiness and the
cycle wait, while preserving the cycle query's intentional presence-only join
to `processing_state` for legacy serial rows. Keep the active extractor,
chunker, normalizer, representation, and deployment pins where the consumer has
them. Add a behavioral D56 regression with an origin chunk in an older version,
a current-version `chunk_claims` link, and a current-generation pending or
dead-lettered claim row; readiness must not report normalize succeeded and the
cycle must not finalize.

## Non-blocking notes

- The new regressions remain mostly source-text tripwires. The atomic-retire
  test only inspects method source
  (`src/tests/workers/test_e3_claim_normalize_fanout.py:193-206`), and the
  handler fixture called a D56 check still sets `claim.chunk_id` equal to the
  current chunk (`src/tests/workers/test_e3_claim_normalize_fanout.py:225-259`),
  so it does not exercise a reused occurrence. These are test-quality issues,
  not additional production blockers.
- The fan-out and `claims_for_chunks` occurrence queries do not select distinct
  claim ids (`src/rememberstack/spine/work_ledger.py:1159-1169`,
  `src/rememberstack/spine/claim_catalog.py:343-349`). Ledger uniqueness and
  the downstream distinct relation selector preserve correctness, but a claim
  attached to multiple selected chunks causes redundant enqueue attempts and
  duplicate catalog objects. `DISTINCT` would better match the binding
  expected-claim *set* and avoid needless work.
- There is still no behavioral database regression for reverse-order stored
  observation windows; the current test inspects Python/SQL text
  (`src/tests/workers/test_e3_claim_normalize_fanout.py:175-190`). The clamp
  reads correctly for the r6 three-assertion overlap, so this remains test
  quality only.

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **31 passed in 3.64s**.

`git diff --check ec64a05f^ ec64a05f` completed cleanly.

## Mergeability

**No.** The r6 fan-out/barrier/handler occurrence fix, atomic observation flush
retirement, and neighbour-boundary clamp are sound. However, D56 occurrence
semantics stop at those edited paths: readiness can report a reused-claim
version normalized and connector-cycle finalization can pass while its actual
claim job is unfinished or dead-lettered. Carrying the same `chunk_claims` join
into those two binding consumers, with one real reused-occurrence regression,
should make this the simplest solid v1.
