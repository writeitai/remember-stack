# Claude Opus review — D99 proposal-only convergence lock follow-up

**Date:** 2026-08-28

**Scope:** the D99 follow-up that changes proposal-only convergence from an
exclusive to a shared deployment identity-epoch advisory lock, plus the
required cross-closure observation-lock ordering.

## Trigger

A fresh RememberStack v0.7.0 LoCoMo `conv-26` run at the established worker
counts dead-lettered one `normalize_relations` row after three statement
timeouts waiting for the shared identity-epoch lock. Automatic merge was
disabled, so convergence could only queue review proposals; it nevertheless
held the identity epoch exclusively.

## Review command

Both rounds used the operator-required command shape:

```text
claude --dangerously-skip-permissions --model claude-opus-5 --effort xhigh -p "<review prompt>"
```

Claude inspected the working-tree diff and the relevant resolver, profile,
clustering, review, supersession, and test paths. It was instructed not to
modify files.

## Round 1 — `REQUEST_CHANGES`

Claude agreed that a shared epoch lock is correct when `auto_merge_enabled` is
false, but found that it exposed a latent observation-lock cycle after accepted
merges. Convergence acquired each member closure separately, so overlapping
closures could produce `obs:2 -> obs:1` while profile publication used the
global `obs:1 -> obs:2` order. It also found that the initial test exercised
only the lock-selection helper and that the design named resolver workers even
though they do not take this epoch lock.

The revision:

- pre-locks the union of every member identity closure in one globally sorted
  entity-id order while holding the shared identity epoch;
- keeps proposal-only convergence shared and all mutation-capable convergence
  exclusive;
- names supersession adjudication and profile publication as the affected
  shared-lock paths; and
- tests the real `recluster_neighborhood` call site for both policies plus an
  overlapping merged-closure order.

## Round 2 — `APPROVE`

Claude approved the revision. It verified that every observation-lock acquirer
now follows a compatible total order, every identity mutation remains behind
the exclusive epoch, no shared-to-exclusive upgrade path exists, merged
closures stay stable while the shared epoch is held, and the second-pass locks
are a subset of the pre-locked union. It also checked lint, formatting, typing,
the unit inventory, and the unit suite.

The review noted two non-blocking follow-ups: closure CTEs are currently read
twice per member during convergence, and the synthetic order test proves the
global pre-lock directly rather than replaying a present entity through the
second pass. They are intentionally not expanded in this corrective PR: the
duplicate read is bounded and correctness-preserving, and the approved source
invariant guarantees that the stable second-pass closure is a subset of the
union. One misleading test filter was removed immediately.

**Final verdict: `APPROVE`.**
