# Round-6 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Codex (gpt-5.6-sol)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `99673d80`
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`
**Prior review:** `design/reviews/REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r5_2026-08-11.md`

## Summary

Commit `99673d80` closes the r5 source-earliest-window defect in production
code. Exact-statement collapse attaches evidence and pulls the open
observation's `valid_from` earlier (`src/rememberstack/spine/observation_adjudication.py:198-216`),
semantic `EVIDENCE` collapse does the same
(`src/rememberstack/spine/observation_adjudication.py:349-363`), and the guarded
update changes only a null or later boundary
(`src/rememberstack/spine/observation_adjudication.py:893-899`). The cycle query
also no longer treats an absent claim-grain row as unfinished: it now joins
only existing claim-normalize work and blocks on its non-succeeded states,
including `dead_letter` (`src/rememberstack/spine/lifecycle.py:1052-1067`).
The newly added handler tests exercise payload-claim, claim-coordinate, and
representation/version rejection branches
(`src/tests/workers/test_e3_claim_normalize_fanout.py:213-318`).

The D88 lock, atomic transaction boundary for fan-out, extractor/deployment/
version pins, source-time supersession orientation, staged-observation forget
scrub, and component-version wiring reviewed in earlier rounds remain intact.

This is still not the simplest solid mergeable v1. The claimed flush retry fix
leaves the exact commit/delete failure window from r5 open. A separate existing
D56 path also means the supposedly complete expected set silently omits reused
claim occurrences, which can bypass current-generation normalize at cutover.

## Blocking findings

### B1 — The expected set omits D56 reused claim occurrences

D56 does not rewrite an immutable claim's origin `claims.chunk_id` when a later
version reuses it. It attaches the claim to the new version's chunk in
`chunk_claims` (`src/rememberstack/spine/claim_catalog.py:79-106`), and the copy
SQL inserts only that occurrence link
(`src/rememberstack/spine/claim_catalog.py:254-263`).
The schema defines `claims.chunk_id` as origin provenance and `chunk_claims` as
the exact per-version occurrence map
(`src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py:570-595`).

D88's fan-out and both barrier counts instead join `claims` to `chunks` through
the origin `cl.chunk_id`
(`src/rememberstack/spine/work_ledger.py:1156-1203`). Therefore, a current
representation whose accepted claims were all reused has an expected count of
zero. `_enqueue_claim_normalize_fanout` takes the empty-extract branch and opens
observation flush directly (`src/rememberstack/spine/work_ledger.py:646-683`),
even though the representation carries accepted claim occurrences. This
violates the binding expected-set definition, which is all accepted claims of
the representation's chunks at the pinned extract generation
(`plan/designs/e3_claim_level_normalize_fanout_design.md:84-95`).

The cutover failure is concrete: a claim normalized only by the pre-fan-out
serial generation can be reused into the first post-cutover version. Because
the D88 query sees no claim, it creates no current-generation claim row and
still advances downstream. The legacy success has thereby been treated as if
the fan-out generation had no expected claim, contrary to the generation
separation the design requires.

Two adjacent paths have the same origin/occurrence mismatch. The claim handler
proves membership by comparing the immutable origin `claim.chunk_id` with the
current representation's chunks, so a correctly fanned-out reused occurrence
would be rejected (`src/rememberstack/workers/e3.py:169-197`). The observation
flush's supersession selector calls `claims_for_chunks`
(`src/rememberstack/workers/e3.py:769-779`), whose query also filters
`claims.chunk_id` directly (`src/rememberstack/spine/claim_catalog.py:326-333`).

Use the `chunk_claims` occurrence map for the expected-set fan-out, both barrier
counts, coordinate membership, and the version-scoped origin-claim selector,
while retaining the landed deployment, representation, version, chunker, and
extractor pins. Cover a D56-reused claim whose origin chunk belongs to an older
version and assert that the current representation neither takes the zero-claim
path nor rejects valid occurrence coordinates.

### B2 — Per-entity staging retirement is still not atomic with D43 apply

The new handler applies one entity and then calls
`clear_staged_observations_for_entity`
(`src/rememberstack/workers/e3.py:747-762`). That narrows retries after a later
entity fails, but it does not close the r5 failure window. `add_observations`
owns and commits its own transaction
(`src/rememberstack/spine/observation_adjudication.py:121-174`), while the clear
method opens a second transaction
(`src/rememberstack/spine/fact_catalog.py:459-477`). A worker crash, connection
failure, or database exception after the apply commits but before the delete
commits leaves that entity's staging rows present. The normal retry path loads
them again (`src/rememberstack/workers/e3.py:735-746`) and re-adjudicates the
already committed entity.

This remains data-visible for a superseding pair. On the first attempt, A is
capped and B becomes open. On retry, exact matching considers only open rows
(`src/rememberstack/spine/observation_adjudication.py:189-197`), so capped A
does not short-circuit; a repeated supersede can insert another source-earlier
A slice through the reverse-arrival branch
(`src/rememberstack/spine/observation_adjudication.py:433-464`). Evidence
uniqueness does not prevent that new observation id.

The added test only source-inspects the handler for the deletion method name
(`src/tests/workers/test_e3_claim_normalize_fanout.py:191-198`); it would pass
with the commit/delete gap unchanged and does not simulate a retry. Retire the
entity's staging in the same transaction as its D43 writes, or write a durable
per-entity flush marker atomically with those writes. Add a regression that
fails after one entity's apply has committed and proves the retry creates no
additional observation or adjudication rows.

## Non-blocking notes

- The source-earliest implementation is correct for the r5 exact and semantic
  evidence-collapse paths, but its test still asserts source text and SQL text
  rather than stored windows
  (`src/tests/workers/test_e3_claim_normalize_fanout.py:173-188`). The binding
  acceptance case calls for identical windows under reverse completion order
  (`plan/designs/e3_claim_level_normalize_fanout_design.md:293-305`). A real
  two-order database regression should replace or supplement this tripwire.
- The missing-row cycle stall is fixed, but the join still has no
  `component_version`, chunker, or extractor-generation predicate
  (`src/rememberstack/spine/lifecycle.py:1057-1067`). Because processing-state
  identity includes component version
  (`src/rememberstack/spine/work_ledger.py:906-930`), a stale non-succeeded
  claim row can block a later successful generation. This is not needed to
  establish the current blockers, but the wait should be aligned with the
  pinned expected set before another fan-out or chunker generation is deployed.
- The coordinate test exercises the combined claim-coordinate and membership
  rejection branches, but not its stated cross-tenant case:
  `work.deployment_id` is never varied, and the fake chunk catalog ignores
  representation/chunker arguments
  (`src/tests/workers/test_e3_claim_normalize_fanout.py:243-246,262-318`). The
  production checks read correctly; explicit deployment and representation
  cases would make the security acceptance evidence honest.

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **31 passed in 3.70s**.

`git diff --check 99673d80^ 99673d80 -- src` and
`git diff --check 5b75a6c9 99673d80 -- src` both completed cleanly.

## Mergeability

**No.** The earliest-window and legacy missing-row fixes are real, and most of
the D88 stack is solid. However, v1 still has an expected-set hole on the
normal D56 reuse path and an at-least-once retry gap between observation apply
and staging retirement. Both can be fixed without new architecture: use the
existing occurrence map consistently, and make per-entity flush retirement
atomic with D43 apply (or its durable marker). Those changes plus behavioral
regressions would make this the simplest solid mergeable v1.
