# Re-review r4: P1 Lance bulk writes and two-layer maintenance

**Reviewer:** Codex (`gpt-5.6-sol`)  
**Date:** 2026-08-13  
**Scope:** r4 of `plan/designs/p1_lance_maintenance_design.md`, reviewed
against my r3 `REQUEST_CHANGES`

## Verdict

**APPROVE_WITH_NITS**

R4 closes both remaining r3 blockers. The maintain protocol now uses the
existing claim attempt as an actual compare-and-transition fence, and the heavy
policy now states the honest best-effort product contract under sustained
writes, with bounded escalation to durable `awaiting_operator`. The remaining
items below are drafting/implementation-clarity nits; neither reopens the
approval gate.

## R3 blocker disposition

| Prior blocker | Status | R4 result |
| --- | --- | --- |
| **P1.4 attempt fence** | **Closed** | `ClaimedWork.attempt` is now binding on maintain completion, handler failure/defer, reclaim, conflict-defer, and heartbeat writes (§§5.5.2–5.5.3). Reclaim carries `processing_state.attempts AS observed_attempt` into `WorkLedger.fail(..., expected_attempt=...)`; completion locks and updates only a row matching `processing_id + running + expected_attempt`; heartbeat updates join to the still-running processing row at that attempt. Zero-row transitions reject stale attempt A rather than touching running attempt B. Acceptance explicitly forces A's complete and fail after B is running, and separately proves A's heartbeat cannot refresh B. |
| **P1.5 heavy progress contract** | **Closed** | The goal and policy now explicitly make heavy IVF best-effort under sustained writes and disclaim automatic eventual success (§§1, 4.1, 5.7 rule 3). Pure rate-defer does not burn ledger attempts; post-train conflict burns one attempt with long backoff; N/M/age exhaustion enters durable `operator_state=awaiting_operator`, alerts, and suppresses automatic heavy re-enqueue. The runbook path either establishes a real quiet window through the bounded writer gate / writer scale-down and force-runs one heavy, or leaves IVF knowingly stale. Acceptance covers sustained writes through escalation, no silent thrash, the durable action-required state, and success after operator quiescence. |

## P1.4 confirmation

The fence is now an ownership contract rather than a heartbeat heuristic:

- `complete_maintain_p1(..., expected_attempt=work.attempt)` predicates both
  its lock/read and success update on `status='running' AND
  attempts=:expected_attempt` (`design:856-906`).
- `WorkLedger.fail` gains the same expected-attempt comparison for normal
  failure, reclaim, and conflict-defer, with mismatch mapped to
  `WorkNotRunningError` (`:657-735`, `:908-917`).
- Reclaim projects the observed attempt before the select-to-fail race and
  passes it into the locked transition (`:657-703`).
- Heartbeat writes require the linked processing row to remain running at the
  expected attempt; an old heartbeat stops on zero rows (`:739-761`).
- The PR3 and acceptance plans test stale complete **and** stale fail while B
  remains running, plus the old-heartbeat case (`:1417`, `:1443-1447`).

This satisfies the r3 approval gate for P1.4.

## P1.5 confirmation

R4 makes a clear product decision: continuous high-rate writers may leave IVF
training stale indefinitely, and the system must not describe that regime as
eventually successful (`design:1069-1077`). It then binds the operational end
state:

- thresholds are explicit: 12 rate defers, 3 train conflicts, or 24 hours by
  default (`:1099-1108`);
- escalation persists `awaiting_operator`, emits a page-worthy signal, and
  prevents `ensure_maintain_due` from recreating heavy work (`:1109-1112`,
  `:953-963`);
- an operator can create an actual quiet window with the writer gate or
  label/embed scale-down, force one rebuild, and release the gate, or explicitly
  accept stale IVF (`:1113-1124`);
- acceptance tests both the terminal state under sustained writes and the
  post-quiescence successful rebuild (`:1440-1442`, `:1450`).

This satisfies the r3 approval gate for P1.5 without asserting a guarantee the
system cannot provide.

## Nits

1. Make defer-streak inheritance explicit on the succeed-as-skipped successor
   path. The counters are unit-local, while `complete_maintain_p1` inserts a new
   successor unit (`:818-825`, `:886-891`). State that the successor copies the
   incremented `rate_defer_count` and original `first_defer_at` (and whichever
   conflict streak fields remain applicable), or place the streak on the
   table-scoped control/stats row. The binding counter semantics and acceptance
   test already require this; spelling it out prevents an implementation from
   resetting to zero on every fresh unit.
2. Update the closing note at `:1491-1492`; it still says “Revised r3” and
   “re-review r3” even though the document header is r4 pending re-review r4.

## Approval gate

Satisfied. P1.4 has a real claim-attempt fence across all relevant maintain
transitions and liveness writes. P1.5 has an honest best-effort contract plus a
durable, auto-suppressing `awaiting_operator` terminal state and a concrete
operator quiescence path.
