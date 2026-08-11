# Round-5 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Codex (gpt-5.6-sol)  
**Date:** 2026-08-11  
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `3af2bb1b`  
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`  
**Prior review:** `design/reviews/REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r4_2026-08-11.md`

## Summary

Commit `3af2bb1b` fixes the concrete insert-path defect from r4 B1. First,
no-open-candidate, and clear-novelty observations now persist the incoming
claim's `asserted_at` as `valid_from` and retain it in the in-transaction
candidate block (`src/rememberstack/spine/observation_adjudication.py:208-287`).
The coexist, contradiction, and residual-new paths do the same
(`src/rememberstack/spine/observation_adjudication.py:359-570`). A later
source-older *different* value can therefore reach the reverse-arrival branch
with a dated open candidate. **The specific null-`valid_from` defect in prior
r4 B1 is resolved.**

The r4 B2 coordinate defect is also resolved for the normal fan-out path. All
three claim-set queries bind claim and chunk deployment plus chunk version,
representation, chunker generation, and claim extractor generation
(`src/rememberstack/spine/work_ledger.py:1156-1203`). `ClaimForNormalization`
now carries deployment and extractor coordinates
(`src/rememberstack/model/relations.py:60-71`), and the claim handler rejects a
target/payload claim mismatch, deployment/document/extractor mismatch, and a
claim whose chunk is not in the supplied representation/version
(`src/rememberstack/workers/e3.py:151-197`). **Prior r4 B2 is resolved.**

The implementation is still not the simplest solid mergeable v1. An
observation that collapses as equivalent evidence bypasses all of the fixed
insert paths and leaves the fact window dependent on version completion order.
That is within D88's binding source-time/continuous-ingest contract, not a v2
hardening concern.

## Blocking finding

### B1 — Equivalent-observation collapse still makes `valid_from` completion-order dependent

The exact-statement fast path finds an open observation, attaches evidence,
and returns without comparing the incoming `asserted_at` with the existing
candidate's `valid_from` (`src/rememberstack/spine/observation_adjudication.py:189-207`).
The adjudicated `ObservationOutcome.EVIDENCE` path does the same for semantic
equivalence (`src/rememberstack/spine/observation_adjudication.py:327-358`).
`_evidence` only inserts the evidence link and recounts; it never adjusts the
observation window (`src/rememberstack/spine/observation_adjudication.py:710-730`).

Consequently, if an exact/equivalent statement asserted in 2024 flushes first,
the observation is inserted with `valid_from = 2024`. When the same fact from
2019 flushes second, it collapses into that row and the window remains 2024.
Reversing completion order produces `valid_from = 2019`. The new
`valid_from=asserted_at` changes correctly date initial inserts, but they make
this formerly hidden evidence-collapse asymmetry observable in the fact's
world-validity window.

That violates the binding requirement that observation correctness use source
time rather than completion order
(`plan/designs/e3_claim_level_normalize_fanout_design.md:43-46`) and the explicit
acceptance expectation that reverse completion yields the same windows
(`plan/designs/e3_claim_level_normalize_fanout_design.md:293-305`). Preserve the
source-earliest boundary (or otherwise recompute the canonical collapsed
window) on both exact and adjudicated evidence collapse, under the existing
entity transaction/lock.

The added test does not exercise this behavior—or even the fixed supersede
flow. It source-inspects `_add_with_block` and counts occurrences of the text
`valid_from=asserted_at`
(`src/tests/workers/test_e3_claim_normalize_fanout.py:173-181`). That assertion
can pass while a required path is broken and cannot prove database windows.
Cover two dated equivalent assertions and two dated superseding assertions in
both version-completion orders, asserting identical stored windows, as required
by the design acceptance case.

## Non-blocking coverage notes

- The coordinate tests inspect SQL predicates and exercise only the successful
  claim-handler case (`src/tests/workers/test_e3_claim_normalize_fanout.py:48-67,184-319`).
  Focused negative cases for wrong payload claim, deployment, document,
  version, representation, and extractor generation would better protect the
  r4 B2 fix and the design's cross-tenant acceptance case.
- The fan-out remains a Python loop of individual ledger inserts inside one
  transaction (`src/rememberstack/spine/work_ledger.py:646-706`), rather than
  the design's preferred set-based/bulk insert. Atomicity is preserved, so this
  is not a correctness blocker, but the binding BEAM-scale dry-run/plan remains
  absent.

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **28 passed in 4.05s**.

`git diff --check 3af2bb1b^ 3af2bb1b` also completed cleanly.

## Mergeability

**No.** The two concrete r4 defects are fixed for ordinary inserts and normal
claim fan-out/handling, and the requested suite is green. However, equivalent
observation evidence still produces different `valid_from` windows solely from
cross-version completion order, while the binding design requires the same
windows. A small source-time-aware evidence-collapse fix plus a real
reverse-order regression should make this solid for v1 without adding new
architecture.
