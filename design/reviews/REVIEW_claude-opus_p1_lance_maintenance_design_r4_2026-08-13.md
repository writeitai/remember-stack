# Design re-review (r4) — D93 P1 Lance bulk writes and two-layer maintenance

**Reviewer:** claude-opus
**Date:** 2026-08-13
**Round:** 4 (after r3: claude-opus APPROVE_WITH_NITS with R12–R16; codex-sol
REQUEST_CHANGES with P1.4 attempt fence + P1.5 heavy progress contract)
**Branch:** `feat/d90-entity-obs-flush-fanout` (docs untracked)
**Under review:** `plan/designs/p1_lance_maintenance_design.md` (revised r4)
**Prior reviews:**
`design/reviews/REVIEW_claude-opus_p1_lance_maintenance_design_r3_2026-08-13.md`,
`design/reviews/REVIEW_codex-sol_p1_lance_maintenance_design_r3_2026-08-13.md`
**Re-verified against code (this round):**
`src/rememberstack/ports/queue.py` (`announce` at 15–23: keyword-only
`processing_id`, `route_snapshot: QueueRoute`, `not_before_snapshot` — the r4
reclaim pseudocode now matches it exactly);
`src/rememberstack/spine/work_ledger.py` (`ClaimedWork.attempt =
int(row["attempts"])` at 1339; `_CLAIM_START` increments `attempts` at
1400–1412; `_COMPLETE` at 1434–1439 fences on only
`processing_id + status='running'`; `fail` at 632–676 takes no
`expected_attempt` today and raises `WorkNotRunningError` on non-running at
654–657). These confirm the design's `expected_attempt` extensions are real,
necessary changes — not descriptions of behavior that already exists — and
they are correctly scoped into PR3 (§6.3). The r1–r3 verification log (live
`lancedb==0.34.0` probes, CHECK constraints at
`p0_02_0002_infrastructure_registries.py:96-101`, `lane_is_valid`,
`UNLANED_STAGES`, partial-index citation) still stands.

## Verdict

**APPROVE_WITH_NITS**

All five of my r3 nits (**R12–R16**) are closed as specified, and both
remaining codex-sol blockers are closed with binding mechanisms:

- **P1.4 (attempt fence)** — every maintain ownership-changing and liveness
  write is now compare-and-transition on `ClaimedWork.attempt`, including the
  heartbeat, with the exact acceptance test Codex demanded (stale attempt A's
  complete **and** fail both rejected while B runs).
- **P1.5 (heavy progress)** — the design takes the honest-narrowing route
  Codex explicitly sanctioned, and goes further than the minimum: heavy under
  sustained writes is now a **recorded product decision** (best-effort, §1
  item 11 / K17 / §12), with a binding terminal escalation
  (`awaiting_operator` after N/M/age budgets), a real quiet-window mechanism
  (`maintenance_writer_gate=hold` or compose scale-down — not `label_lock`
  expansion), and acceptance tests that sustain writes through the budget and
  specify what changes the writer conditions before the forced heavy succeeds.

Two nits remain (**R17–R18**), both in the escalation machinery added this
round. Each is a one-to-three-sentence binding clarification; **neither gates
the `decisions.md` entry and no r5 re-review is needed** — fix them in the
same commit that enters D93. R17 is the one that matters: as written, a cold
implementer could zero the defer-streak counters on each successor unit,
which would silently break the N-consecutive escalation trigger (the §16
acceptance test would catch it, but the doc should not rely on the test to
disambiguate the mechanism).

## Disposition of my r3 nits (R12–R16)

| # | r3 nit | Status | Where closed |
| --- | --- | --- | --- |
| **R12** | §5.5.3 pure-rate-defer offered a `fail(retryable=True)` alternative that burns attempt budget (`_CLAIM_START` increments on every claim) → false dead-letters | **Closed** | The fail-retryable alternative is struck everywhere and replaced with an explicit prohibition: §5.5.3 handler comment ("NEVER `WorkLedger.fail` for pure rate-defer"), the new "Defer / conflict outcomes (binding split — Claude R12)" table (**Forbidden:** `fail(retryable=True)` for pure rate-defer; only post-train `conflict_defer` burns one attempt, by intent), §5.7 rule 3, §5.7 rule 6 ("Pure rate-defer: zero trains; zero ledger fail-attempts"), §12 rejected-alternatives row, K20, and the §16 test "Pure rate-defer does not burn attempts" (N consecutive rate-defers must not dead-letter via `max_attempts`). `maintain_max_attempts` in §5.4 now says "conflict_defer / hard failures only — **not** pure rate-defer". Internally consistent. |
| **R13** | Reclaim pseudocode's `queue.announce(processing_id, not_before=scheduled)` didn't match `TaskQueuePort.announce` | **Closed** | §5.5.2 now constructs `QueueRoute(deployment_id, stage=PipelineStage.MAINTAIN_P1_INDEX, lane=None)` and calls `announce(processing_id=…, route_snapshot=…, not_before_snapshot=scheduled)`. **Verified this round** against `ports/queue.py:15-23` — parameter names and types match; `lane=None` is the unlaned route the design binds elsewhere (§5.5.5). The same corrected call shape appears in `complete_maintain_p1`'s post-commit successor announce. |
| **R14** | Reclaim loop must tolerate the select→fail race (owner completes between scan and lock; `fail` raises `WorkNotRunningError`) | **Closed** | §5.5.2 reclaim pseudocode wraps the per-row `fail` in `except WorkNotRunningError: continue` with the reason stated inline; §16 adds the "Reclaim select→fail race" test (caught per row, loop continues). The attempt fence subsumes the second variant of this race (scan observes attempt A, B claims before the lock): the fenced `fail` matches zero rows and raises the same error. |
| **R15** | Residual fencing window when the heartbeat *thread* dies but the Lance op lives (wall-clock reclaim → successor → surviving owner completes successor's row) | **Closed — both options taken** | The advisory-lock probe is promoted from optional to **binding** on the wall-clock arm (§5.5.2 liveness item 2: reclaim only if `pg_try_advisory_lock(table_maintain_key)` succeeds; a held lock proves a live owner; release immediately if taken for the probe), *and* the attempt fence independently closes the damage path (the surviving owner's `complete_maintain_p1` carries attempt A and cannot succeed attempt B's row — §9 rows "Heartbeat thread dead, Lance op alive" and "Stale attempt A after B claimed"). The r3 attribution-only residual no longer exists. |
| **R16** | `ensure_maintain_due` probe condition parsed as `missing OR (older AND …)` ambiguously | **Closed** | §5.5.4 pseudocode is fully parenthesized with an inline comment marking the parentheses binding: probe when stats are missing, OR when stats are stale AND (light poll due OR thresholds unknown). |

## Disposition of codex-sol r3 blockers

### P1.4 — attempt fence: closed

The r3 gap was that "running" plus `processing_id` was the entire ownership
test. R4 binds `ClaimedWork.attempt` through every maintain transition,
exactly as the blocker demanded:

- **Fence table (§5.5.2, binding):** `complete_maintain_p1` updates only
  `WHERE processing_id=:id AND status='running' AND
  attempts=:expected_attempt`; fail/reclaim/conflict_defer use the same
  compare-and-transition (`expected_attempt` required on the maintain path);
  the heartbeat UPDATE joins `processing_state` and writes only while the
  unit's row is `running` **at that attempt**, and a zero-row result stops the
  thread so an old side thread can never refresh a replacement.
- **`WorkLedger.fail` extension:** `expected_attempt: int | None = None`,
  with the fenced UPDATE spelled out and zero rows → `WorkNotRunningError`.
  Verified this round that today's `fail` has no such parameter and
  `_COMPLETE` has no attempt predicate — the design correctly treats both as
  changes and puts them in PR3 with `complete_maintain_p1`.
- **Reclaim carries the observed attempt:** the scan projects
  `processing_state.attempts AS observed_attempt` and passes it into the
  locked `fail`, closing race 1 (reclaim failing a replacement attempt).
  Race 2 (A completes/fails B after reclaim) is closed by the fenced
  complete/fail; race 3 (old heartbeat refreshes the replacement) by the
  attempt-conditional heartbeat UPDATE.
- **Acceptance:** §16 "Stale worker after reclaim / attempt fence" is the
  exact test shape the blocker required — attempt B `running`, force A's
  `complete_maintain_p1` **and** A's `fail`, both rejected, B remains running
  and can complete — plus "Heartbeat cannot refresh replacement" and the
  `p1_lance_stale_attempt_rejected{op}` metric (§8). PR3's validation column
  names the test.
- The unit stamps `claimed_attempt` at claim start (§5.5.1) so reclaim can
  cross-check, and K19 records the invariant. The table-lock probe remains
  defense-in-depth (R15), not a substitute — matching the blocker's closing
  sentence.

### P1.5 — heavy progress contract: closed (honest-narrowing route, with the escalation machinery)

Codex offered two exits: a binding terminal escalation with a real
quiet-window mechanism, or an honest narrowing to best-effort recorded as a
material decision. R4 does both at once:

- **The product contract is now explicit and recorded:** §1 item 11, the §4.1
  goal rewording ("honest best-effort … terminal `awaiting_operator`, not
  fake eventual-success"), K17, and two §12 rejected-alternatives rows
  ("Guaranteed eventual heavy under continuous high write — dishonest without
  a quiet window"; "Writer quiesce via expanded `label_lock`"). This is a
  material decision change entering D93 through the front door, which is what
  the blocker required if this route was taken.
- **Terminal escalation is binding, budgeted, and durable:** after
  `heavy_rate_defer_escalate_n` (12) consecutive pure rate-defers, or
  `heavy_conflict_defer_escalate_m` (3) consecutive conflict_defers, or
  `heavy_defer_age_escalate_h` (24 h) of continuous deferring, the unit enters
  durable `operator_state=awaiting_operator` with reason
  `heavy_needs_quiet_window`; processing is closed; `ensure_maintain_due`
  must not auto-enqueue another heavy while the flag is set (§5.4 rule, §5.5.4
  pseudocode, §5.7 rule 3). Attempt exhaustion on the conflict path escalates
  to the same state rather than silent fresh-unit thrash. Alerting is
  page-worthy on the transition (§8).
- **The quiet window is a real mechanism, not an assumption:**
  `maintenance_writer_gate=hold` (writers enqueue but do not start new
  Lance-mutating batches for the gated table, bounded period) or compose
  scale-down, followed by admin force-heavy that clears the flag on success —
  documented as the runbook path (§5.7 rule 3, §11), with the alternative
  "accept stale IVF" stated as a valid ops choice. `label_lock` is explicitly
  not expanded (§4.2 non-goal).
- **Acceptance closes the r3 gap Codex flagged at the old line 1230:** §16
  "Sustained high write rate + heavy (best-effort)" sustains writes through
  the N/age budget, requires the durable `awaiting_operator` state with
  metric/alert, forbids both infinite silent thrash and any
  automatic-eventual-success claim, and — the previously missing piece —
  specifies what changes the writer conditions (operator sets the gate or
  scales down) before the one forced heavy succeeds and clears the flag.
  "Pure rate-defer does not burn attempts" and the PR5 validation column
  cover the budget arithmetic.

## Remaining nits (R17–R18 — fix in the D93 entry commit; no re-review)

- **R17 — bind where the cross-unit defer streak and `awaiting_operator`
  durably live.** The escalation counters (`rate_defer_count`,
  `conflict_defer_count`, `first_defer_at`) and `operator_state` are defined
  as columns of `p1_maintain_units` (§5.5.1) — i.e. per unit — but the
  streaks they measure span **successor units**: every pure rate-defer
  succeeds-as-skipped and inserts a *fresh* unit (§5.5.3), so if the successor
  starts at zero the "N consecutive rate-defers" trigger can never reach 12,
  and the handler's `unit.operator_state` check reads a fresh unit's `null`
  rather than the table's flag. The text gestures at the right answer in two
  places ("bump durable rate_defer streak on **stats/latest unit**", §5.5.4;
  "while set for a `(root, table, mode=heavy)`", §5.4) but never binds it.
  One sentence fixes it — either: the `complete_maintain_p1` successor insert
  copies the three counters (bumped) and `operator_state` forward in the same
  transaction, or (cleaner, matches the state's table scope) the streak
  counters and operator flag move to `p1_lance_table_stats`, which is already
  keyed `(lance_root_key, table_name)`, with `p1_maintain_units` keeping at
  most a denormalized copy for diagnostics. The §16 sustained-write test
  would catch a zeroing implementation, but Rule 1 says the mechanism must be
  readable cold, not recoverable from a failing test.
- **R18 — `maintenance_writer_gate` needs a runtime-readable home.** §5.4
  places the gate in the settings group (a `REMEMBERSTACK_…` env-style knob
  documented in compose env), but §5.5.4/§5.7 have admin **set and clear it at
  runtime** and require live label/embed workers to observe `hold` before
  starting each new Lance-mutating batch. Process-start env can't do that
  without restarting the writers — which collapses the gate into the compose
  scale-down alternative and loses the reason it exists as a distinct path.
  One sentence: the gate is durable runtime state (e.g. a control row on
  `p1_lance_table_stats` or a small settings-override table) that writers
  check per batch, with the env knob at most a startup default. (Open
  question 5 already covers TTL/auto-release; this is only about where the
  flag lives.)

## Checklist re-run

| # | Contract | r2 | r3 | r4 |
| --- | --- | --- | --- | --- |
| 1 | Two-layer model complete | Pass | Pass | **Pass** |
| 2 | Bulk merge correctness (vectors/labels) | Pass | Pass | **Pass** |
| 3 | Batch semantics (dedupe, misses, failure) | Pass | Pass | **Pass** |
| 4 | Write amplification bounded end-to-end | Pass | Pass | **Pass** |
| 5 | Index set enumerated | Pass (wording) | Pass | **Pass** |
| 6 | One ledger protocol | Pass | Pass | **Pass** |
| 7 | Ledger grain matches physical objects | Pass | Pass | **Pass** |
| 8 | Lane / route valid | Pass | Pass | **Pass** |
| 9 | Readiness / profile wiring | Pass | Pass | **Pass** |
| 10 | Concurrency: writer ↔ maintain | Pass | Pass | **Pass** |
| 11 | Concurrency: maintain ↔ purge | Concern | Pass | **Pass** |
| 12 | Crash / stuck-lease recovery implementable | Concern | Pass | **Pass** (announce matches port; select→fail race handled) |
| 13 | `BackfillFinalizer` unified on the port | Concern | Pass | **Pass** |
| 14 | Migrations vs executable catalog contract | Pass | Pass | **Pass** (attempt-fence columns + counters in PR3 catalog scope) |
| 15 | Rollout realistic | Pass | Pass | **Pass** |
| 16 | PR plan realistic | Pass | Pass | **Pass** (fence test in PR3; escalation + gate in PR5) |
| 17 | Docs obligation (D66 same-PR) | Pass | Pass | **Pass** (runbook: `awaiting_operator`, writer gate, accept-stale) |
| 18 | Rule 1 (cold-reader legibility) | Pass | Pass | **Pass** (R17/R18 are the residual, one sentence each) |
| 19 | Rule 2 (full scope, no phasing) | Pass | Pass | **Pass** (best-effort heavy is a scope boundary stated as a non-goal + documented ops path, not a deferral) |
| 20 | Rule 3 (library boundary) | Pass | Pass | **Pass** (escalation, gate, and runbook all in-repo; no control-plane authority) |
| 21 | Analysis ↔ code accuracy | Pass | Pass | **Pass** (fresh checks: `announce`, `ClaimedWork.attempt`, `_CLAIM_START`, `_COMPLETE`, `fail`) |
| 22 | Heavy progress guarantee under ingest | Concern | Concern (Codex P1.5) | **Pass** (honest best-effort contract + binding escalation + real quiet window) |
| 23 | Self-seed cost on the real execution edge | Concern | Pass | **Pass** |
| 24 | Attempt-fenced ownership/liveness (Codex P1.4) | — | Fail | **Pass** (fence on complete/fail/reclaim/heartbeat; demanded tests present) |
| 25 | Escalation state durable across units | — | — | **Nit** (R17) |

## Closing

The r3 gate — my R12–R16 plus Codex's attempt fence and heavy-progress
contract — is fully met, and the new text is accurate against the code and
port signatures it cites (re-verified this round, including the previously
wrong `announce` sketch). The heavy best-effort narrowing is handled the way
Codex required: as a visible, recorded decision with real escalation and
quiet-window machinery, not a hedge. **Enter D93 in `decisions.md`.** Apply
R17–R18 (two short binding clarifications, R17 first) in the same commit;
neither changes a decision and neither needs another review round.
