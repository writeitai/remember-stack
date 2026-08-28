# Claude Opus 5 review — D99 proposal-only convergence coalescing

**Date:** 2026-08-28

**Reviewed commit:** `70bc6d10`

**Pull request:** #325

**Verdict:** `REQUEST_CHANGES`

## Invocation

The review used the operator-required command shape:

```text
claude --dangerously-skip-permissions --model claude-opus-5 --effort xhigh -p "<review prompt>"
```

The prompt asked for a read-only review of the complete diff, D99 evidence and
contracts, PostgreSQL advisory-lock semantics, proposal-only liveness,
auto-merge isolation, diagnostics, simplicity, and tests. It explicitly kept
the 585-provisional-fragment identity-quality problem outside this throughput
patch.

## Findings

1. **High — a dropped nomination is not guaranteed to recover.** A pass can
   gather before a concurrently published entity exists, while the new
   entity's nomination skips on the lemma lock. The holder can then produce a
   proposal from the earlier member set. A second interleaving lets the holder
   defer on a busy evidence lock after the publisher's nomination already
   skipped. The design's “later publication nominates again” argument does not
   prove the unattended tail case.
2. **Medium-high — different-lemma passes can interleave review supersession.**
   Removing the deployment-wide cluster lock allows concurrent proposal writers.
   `_SELECT_PENDING_MERGE_REVIEWS` has no total row order, so overlapping update
   loops can deadlock or leave overlapping uncommitted proposals unseen. The
   minimal defensive change is an `ORDER BY review_id`, with row locking to be
   evaluated in the follow-up.
3. **Medium — deferral is unobservable.** The two early returns are
   indistinguishable from a neighborhood with no work or no proposal, and the
   convergence wrapper currently discards reports. The next benchmark cannot
   directly count proposal contention from the returned model.
4. **Medium — binding language overstates best-effort behavior.** “Redundant,”
   “neighborhood lock,” and the unconditional one-proposal statement are
   stronger than the code's actual lemma-keyed, best-effort behavior.
5. **Medium — snapshot-only report generation deserves explicit analysis.** A
   repeatable-read proposal pass could avoid holding evidence locks, though it
   does not by itself coalesce repeated expensive clustering work or prove tail
   nomination liveness.
6. **Low — tests do not cover the two-layer drop interleaving or real
   PostgreSQL lemma-lock behavior.** The real evidence try-lock test should set
   a short contender lock timeout so a blocking regression fails instead of
   hanging CI.
7. **Low — public project-status wording should describe best-effort
   nomination truthfully.**

## What the reviewer approved

The reviewer confirmed that the patch uses PostgreSQL transaction-scoped
try-lock semantics correctly, preserves globally sorted member lock attempts,
cannot create an advisory-lock wait cycle in proposal-only mode, and leaves the
mutation-capable auto-merge lock sequence unchanged. The nine focused unit tests
passed in the review environment.

## Disposition

The operator explicitly directed that this PR be merged regardless of the
Claude verdict and that improvements may follow separately. Therefore #325
remains a narrow convoy-reduction patch. The liveness, ordered review-update,
observability, binding-language, snapshot-alternative, and test findings are
retained here for the immediate follow-up rather than silently dismissed or
expanded into this PR.

