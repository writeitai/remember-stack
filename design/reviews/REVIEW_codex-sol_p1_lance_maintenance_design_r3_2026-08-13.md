# Re-review r3: P1 Lance bulk writes and two-layer maintenance

**Reviewer:** Codex (`gpt-5.6-sol`)  
**Date:** 2026-08-13  
**Scope:** r3 of `plan/designs/p1_lance_maintenance_design.md`, reviewed
against my r2 `REQUEST_CHANGES`

## Verdict

**REQUEST_CHANGES**

R3 closes the atomic rerun edge, completes the binding index matrix, and moves
the mandatory facts join-key indexes ahead of the PR1 metadata merge. It also
fixes the invalid reclaim state transition and adds a credible liveness signal.
Two prior blockers remain, however: the reclaim protocol still has no actual
attempt fence, and the heavy policy still has no terminal progress mechanism
under sustained writes. Both are correctness/operability contracts already
required by r2, not new scope.

## R2 blocker disposition

| Prior blocker | Status | R3 result |
| --- | --- | --- |
| **Reclaim** | **Not closed** | `WorkLedger.fail` makes retry/dead-letter transitions CHECK-safe and the heartbeat prevents ordinary long-running work from being mistaken for dead. But neither completion, failure, reclaim, nor heartbeat is bound to the claimed attempt. |
| **Atomic rerun** | **Closed** | `complete_maintain_p1` takes the coalesce lock, locks unit + ledger rows, consumes `rerun_requested`, inserts the successor, and marks success in one transaction (§5.5.3). This closes the handler-return/completion loss window. Its ownership check must be attempt-fenced as part of the reclaim fix below. |
| **Heavy progress** | **Not closed** | Rate-based pre-train defer and one-train conflict backoff prevent thrash, but they do not guarantee progress or enter a state that can create a quiet maintenance window. |
| **Index matrix** | **Closed** | The matrix now includes the omitted chunk/claim nominator prefilters, entities, `facts.fact_id`, and a binding BITMAP type for `facts.kind` (§5.3.1). |
| **PR1 join keys** | **Closed** | PR1 explicitly ensures `deployment_id`, `kind`, and `fact_id` indexes before enabling the large matched-only metadata merge (§§5.2.1, 15), with an acceptance test for the ordering. |

## Remaining blockers

### P1.4 — “Running” is not an attempt fence

The design says the maintain completion path is attempt-aware through “the
claim token / processing_id ownership already enforced by `_COMPLETE`”
(`plan/designs/p1_lance_maintenance_design.md:696-702`), but its proposed API
takes only `processing_id` and `unit_id`, and its SQL predicate requires only a
running row (`:757-768`). That is the same unsafe ownership test identified in
r2.

The current ledger increments and returns `ClaimedWork.attempt` on each claim
(`src/rememberstack/spine/work_ledger.py:1328-1340,1402-1412`), while
`_COMPLETE` checks only `processing_id` plus `status='running'`
(`:1434-1439`). `WorkLedger.fail` likewise locks by `processing_id` and checks
only `status` before transitioning the row (`:632-675`). R3's reclaim pseudocode
calls that unfenced `fail(processing_id=...)` (`design:625-652`). Therefore:

1. reclaim can select stale attempt A, race with A failing and attempt B being
   claimed, then fail B;
2. after A is reclaimed and B is claimed on the same `processing_id`, A can
   complete or fail B's running attempt;
3. an old heartbeat writer can update the unit after replacement because
   `last_heartbeat_at` is not associated with an attempt.

A heartbeat reduces the chance of these races; it cannot fence them. Bind the
existing `ClaimedWork.attempt` (or mint a separate lease token) through every
maintain transition:

- `complete_maintain_p1(..., expected_attempt=claimed.attempt)` must update only
  `processing_id + status='running' + attempts=expected_attempt`;
- handler failure/defer and reclaim must use the same compare-and-transition;
- the reclaim scan must carry the observed attempt into the locked `fail` call;
- heartbeat writes must be conditional on the same processing row still being
  running at that attempt, so an old side thread cannot refresh a replacement;
- the stale-worker acceptance test must let attempt B become `running`, then
  force A to complete **and fail**, proving both are rejected and B remains
  running.

The optional table-lock probe is useful defense in depth but is not a substitute
for this compare-and-set fence.

### P1.5 — Deferral and dead-letter visibility still do not guarantee heavy progress

The binding policy defers whenever write/enqueue rate is above a threshold,
backs off after a train conflict, and, after attempt exhaustion, dead-letters
the unit so the next tick may create another one
(`plan/designs/p1_lance_maintenance_design.md:932-951`). Under continuous writes
above the threshold, every unit can be deferred indefinitely without ever
attempting a rebuild. If forced, every train may conflict and cycle through
dead-letter/fresh-unit indefinitely. `admin force` and metrics make the stall
visible but do not create the quiet window needed to end it.

The acceptance text exposes the gap: it allows dead-letter followed by an
admin/fresh unit that “succeeds” without specifying what changes the writer
conditions (`:1230`). This is still an assumed quiet window, not a progress
contract.

Bind a terminal escalation after a defined defer age/conflict budget. It can be
an operator-mediated path, but it must durably enter an explicit
`awaiting_quiescence`/equivalent state and provide a bounded table-writer gate or
documented stop-writers maintenance action that actually establishes the quiet
window, runs one rebuild, and releases the gate. It need not expand
`label_lock`. Acceptance must sustain writes through the normal retry budget,
exercise that escalation, and prove either the rebuild completes or the system
remains in a durable action-required state that does not claim eventual heavy
progress.

If best-effort heavy maintenance is the intended product contract instead,
narrow the design honestly: remove the “progress under continuous writes” goal
and eventual-success acceptance claim, and explicitly accept indefinitely stale
IVF training under sustained ingest. That would be a material decision change,
not a nit.

## Nits

No additional nits. The remaining requested changes are limited to the two r2
blockers above.

## Approval gate

Approve once (1) all maintain ownership-changing and liveness writes compare an
actual claim attempt/token, and (2) heavy maintenance has a binding terminal
escalation that either creates a quiet window or records a durable
operator-action state without claiming guaranteed progress.
