# Adversarial implementation review: PR 312, WP-I.3 global ER eval (round 1)

**Reviewer:** Claude Opus 5 (`claude-opus-5`, xhigh effort)  
**Date:** 2026-08-27  
**PR:** `writeitai/remember-stack#312`  
**HEAD reviewed:** `abd87e20feaf532ab0c8d8af40adee5aff2145f7`  
**Verdict:** **Request changes**

The reviewer read `CLAUDE.md`, the D95-D97 handoff, the accepted entity
identity design sections 3.4 and 8, WP-I.3, D22/D95/D96, the complete PR diff,
and surrounding resolver, migration, schema-contract, and test code. It
confirmed that no `t0_exact_accept` behavior was present.

## P0 findings

None.

## P1 findings

1. `judge_pair` hard-skipped T3 for every same-lemma pair. The report therefore
   claimed that a T0-reachable false merge could be attributed to T3 or T4,
   while T3 attribution was structurally impossible. This would leave the
   profile-backed T3 scale path unmeasured before WP-I.5.
2. The global gate could pass with only positive labels because precision was
   numerically 1.0 rather than undefined. A growing corpus could also dilute
   same-lemma false merges below the 0.90 global precision floor.

## Non-blocking findings

- The reviewer questioned strict stale-type rejection; this was retained as
  the intentional D96 hard cut requested by the prior WP-I.2 review.
- It requested a resolver-version bump for the changed provenance shape and
  exact restoration of pre-I.3 catalog comments on downgrade.
- It noted that the binding PostgreSQL design should name both blocking and
  deciding diagnostics, and that the empty-profile fake verdict proves harness
  plumbing rather than model quality.

## Corrections

The next two commits closed the findings:

- `c410c136`: context-bearing same-lemma pairs exercise T3; both label classes
  and a measured T0 negative canary are mandatory; any canary false merge
  blocks independently of the global curve; 100 easy positives prove the guard
  cannot be diluted; resolver provenance moved to `resolver-2026.08a`; exact
  migration comments and their round-trip test were added.
- `e3220c35`: the canary derives from actual normalized surfaces rather than
  editable expected-tier metadata; both contexts are required before
  same-lemma T3; missing resolver-version persistence rolls back the run; the
  T4 evidence slot precedes the question.

## Invocation

The review used the required command form:

```text
claude --dangerously-skip-permissions --model claude-opus-5 --effort xhigh -p "<exact-SHA PR review prompt>"
```

