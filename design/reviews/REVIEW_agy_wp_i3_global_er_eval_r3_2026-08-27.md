# Adversarial implementation review: PR 312, WP-I.3 global ER eval (round 3)

**Reviewer:** Antigravity (`agy`)  
**Date:** 2026-08-27  
**PR:** `writeitai/remember-stack#312`  
**HEAD reviewed:** `e3220c3518f9f02c0ebe9b4d394f103faa511d96` (verified; clean)  
**Verdict:** **Approve**

The reviewer inspected the complete diff from main and the binding D95-D97
sources. It specifically verified the two corrected round-1 P1s and all final
hardening: surface-derived canary membership, paired-context T3 eligibility,
T4 evidence ordering, atomic resolver-version recording, migration lifecycle,
global threshold provenance, and strict D96 type rejection.

## Findings

- P0: none.
- P1: none.
- P2: none.
- P3: none.

## Verified behavior

- Same-lemma non-matches remain visible under expected blocking T0 and the
  actual deciding tier.
- A loose context-bearing T3 band is observable and blocks the suite.
- Exact names without two-sided evidence do not enter name-only T3.
- Both label classes and a same-lemma negative canary are required.
- A same-lemma false merge cannot be diluted by easy positives.
- Null or stale `expected_blocking_tier` metadata cannot bypass the canary.
- An unregistered resolver version causes the recorded run transaction to
  roll back.
- Upgrade/downgrade schema, JSON values, indexes, and catalog comments are
  covered by real PostgreSQL tests.
- `t0_exact_accept` is absent from shipped code.

## Invocation

```text
agy --dangerously-skip-permissions --print-timeout 180m0s -p "<final exact-SHA PR review prompt>"
```

## Verdict

**Approve.** WP-I.3 satisfies its acceptance contract on
`e3220c3518f9f02c0ebe9b4d394f103faa511d96`.

