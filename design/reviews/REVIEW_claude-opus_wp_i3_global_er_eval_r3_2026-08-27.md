# Adversarial implementation review: PR 312, WP-I.3 global ER eval (round 3)

**Reviewer:** Claude Opus 5 (`claude-opus-5`, xhigh effort)  
**Date:** 2026-08-27  
**PR:** `writeitai/remember-stack#312`  
**HEAD reviewed:** `e3220c3518f9f02c0ebe9b4d394f103faa511d96` (verified; clean)  
**Verdict:** **Approve**

The reviewer inspected the full `main...e3220c35` diff and re-read
`CLAUDE.md`, the D95-D97 handoff, accepted design section 3.4 and section 8,
WP-I.3, and D22/D95/D96. It explicitly re-audited every round-1 blocker and
the four final hardening changes. It did not modify tracked files.

## P0/P1 findings

None.

## Verified closure

| Contract | Result on reviewed HEAD |
| --- | --- |
| Same-lemma T3 visibility | Context-bearing pairs embed `surface + context`; the regression records the same false merge under blocking T0 and deciding T3. |
| Empty-profile safety | Exact names without evidence skip name-only cosine and reach T4. |
| Non-vacuous gate | Positive and negative labels plus a measured same-lemma negative canary are mandatory. |
| Dilution resistance | A canary false merge blocks while 100 easy positives keep the global precision above its floor. |
| Metadata independence | Canary membership comes from `normalized_lemma(surface_a) == normalized_lemma(surface_b)`, not `expected_blocking_tier`. |
| Recorded-run atomicity | A missing `resolver_versions` target raises `ResolutionSuiteRecordError` and rolls back the `eval_runs` insert. |
| Migration/provenance | Default bands survive upgrade; the lossy downgrade is explicit; prior catalog comments restore exactly; resolver version is `resolver-2026.08a`. |
| Type cut | No live type-keyed eval/config path remains; stale provider type keys fail the intended D96 hard cut. |
| Rejected proposal | No `t0_exact_accept` implementation exists in shipped code. |

## Checks reported by the reviewer

- Ruff lint and format checks passed.
- Changed-module type checks passed.
- Resolver suite: 11 passed.
- Migration suite: 8 passed, including empirical PostgreSQL 18 round trips.
- Other touched test groups: 46 passed.

## Non-blocking WP-I.4 follow-ups

1. Same-lemma T3 rejection should escalate to T4 when profile evidence fights
   the claim; T3 is the repeat-accept path, not a final same-name reject path.
2. WP-I.4 must put all evaluated pairs in the same `name + profile/context`
   feature space rather than measuring bare-name and profile-like embeddings
   with one global band.
3. Pin the new T4 prompt ordering with an assertion on the rendered prompt.

The reviewer also suggested clearer `unannotated` blocking diagnostics and a
more literal name for the same-lemma false-merge guard. These do not weaken the
gate or reopen a blocker.

## Invocation

```text
claude --dangerously-skip-permissions --model claude-opus-5 --effort xhigh -p "<final exact-SHA PR review prompt>"
```

## Verdict

**Approve.** No P0 or P1 remains on
`e3220c3518f9f02c0ebe9b4d394f103faa511d96`.

