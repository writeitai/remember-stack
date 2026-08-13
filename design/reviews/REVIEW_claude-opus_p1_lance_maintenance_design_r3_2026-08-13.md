# Design re-review (r3) — D91 P1 Lance bulk writes and two-layer maintenance

**Reviewer:** claude-opus
**Date:** 2026-08-13
**Round:** 3 (after r2 APPROVE_WITH_NITS with R1–R2 open; codex-sol r2 REQUEST_CHANGES)
**Branch:** `feat/d90-entity-obs-flush-fanout` (docs untracked)
**Under review:** `plan/designs/p1_lance_maintenance_design.md` (revised r3)
**Prior reviews:**
`design/reviews/REVIEW_claude-opus_p1_lance_maintenance_design_r2_2026-08-13.md` (R1–R11),
`design/reviews/REVIEW_codex-sol_p1_lance_maintenance_design_r2_2026-08-13.md` (P1.2, P1.4, P1.5, P2.1)
**Re-verified against (this round):** `src/rememberstack/spine/work_ledger.py`
(`fail` at 632–676; `complete_chunk_extract` at 248; `complete_entity_obs_flush`
at 425), `src/rememberstack/ports/queue.py` (`announce` at 14–22). The r1/r2
verification log (live `lancedb==0.34.0` probes, CHECK constraints at
`p0_02_0002_infrastructure_registries.py:96-101`, `lane_is_valid`,
`_CLAIM_START` attempt increment, `_COMPLETE` semantics) still stands; the r3
text quotes those artifacts verbatim and correctly.

## Verdict

**APPROVE_WITH_NITS**

Both blocking items from r2 (**R1**, **R2**) are closed exactly as required, the
four P2s (**R3–R6**) are closed, the five editorial nits (**R7–R11**) are
absorbed, and all four remaining codex-sol r2 items (**P1.2, P1.4, P1.5,
P2.1**) are closed. On the three strict axes of this round — reclaim vs the
live CHECK constraints, heavy defer policy, atomic rerun — the design is now
correct, self-consistent with the code it cites, and carries acceptance tests
for each.

Five nits remain (R12–R16). Each is a one-to-two-line text tweak; **none gates
the `decisions.md` entry and no r4 re-review is needed** — fix them in the same
commit that enters D91. The one worth doing first is R12, which removes an
internal contradiction in §5.5.3's pure-rate-defer wording that could quietly
reintroduce a degraded form of the P1.5 failure.

## Disposition of r2 opens

| # | r2 issue | Status | Where closed |
| --- | --- | --- | --- |
| **R1** (P1) | Reclaim SQL violated both `processing_state` CHECKs; `WorkLedger.fail` already correct | **Closed** | §5.5.2 deletes the invalid UPDATE, quotes both CHECKs verbatim (matches `p0_02_0002:96-101`), **forbids** the hand-rolled form by name, and binds reclaim to per-row `WorkLedger.fail(..., retryable=True)` + queue re-announce. The attempt-exhausted branch is stated (`dead_letter`, returns `None`) and its coalesce meaning is closed the way r2 asked: a `dead_letter` unit is **not open** per §5.5.1, so the next tick enqueues a fresh unit (§5.5.2 comment, §5.7 rule 3, §9 rows, §16 "Dead-letter coalesce" test). Decision item 12, K6, §12 rejected-alternatives row all carry it. **Verified this round:** `fail()` (`work_ledger.py:632-676`) returns the scheduled `datetime` on retry and `None` on dead-letter, exactly as the design's inline comment claims. |
| **R2** (P1) | `_LANCE_COMMIT_RETRIES` (~3.6 s pause budget) bound to multi-hour `create_index`; up to ~24 wasted retrains | **Closed** | §5.7 rule 6 splits retry policy by operation cost: merge/light keep `_LANCE_COMMIT_RETRIES` + sub-second jitter; heavy `create_index` gets **at most one full train per claim**, with post-train conflict → `conflict_defer` (fail retryable once, `heavy_conflict_not_before_s` floor 15–60 min). §5.3 heavy semantics, §5.5.3 handler (`except CommitConflict` path), §5.4 knobs, §9 row, §12 rejected row ("wrong cost model"), K17, decision item 11. Worst case is stated: one wasted train per attempt (not eight), terminal `dead_letter` after exhaustion, visible and not-open. §16 "Concurrent upsert + heavy" now drives to the terminal state ("not 8 full retrains in one claim; eventually rebuilds … or dead_letters with visible metric then admin/fresh unit succeeds") — the r2 ask verbatim. |
| **R3** (P2) | Lock acquisition had three callers and no bound owner; finalizer took no lock | **Closed** | §5.7 "Lock owner (binding — one seam for all callers)": Postgres session advisory locks keyed on hash of `(lance_root_key, table_name)`, taken via an `Engine` **held by the caller**, never inside the engine-less adapter. The three-row caller table covers handler, hard-forget purge (must hold the lock before `delete_unverified`; `_purge_table_rows` reachable only under it), and `BackfillFinalizer` (same lock per table around ensure+heavy). §6.2, §6.4, §9, K10, PR2. |
| **R4** (P2) | `ensure_maintain_due` had no probe floor; `queue_wake` is estate-wide | **Closed** | §5.4 adds `maintain_probe_min_s` (60) and `maintain_reclaim_min_s` (60); §5.5.4 binds durable-stats-first (`p1_lance_table_stats` read before any Lance probe), probe/reclaim floors in the pseudocode, and a "Wake-channel cost" paragraph naming `_WAKE_CHANNEL` and why the floors exist. |
| **R5** (P2) | Partial-unique-index coalesce alternative not expressible in PostgreSQL | **Closed** | §5.5.1 now **forbids** it with the reason and the PostgreSQL partial-index citation, binds the advisory-xact-lock + `SELECT … FOR UPDATE` implementation in `enqueue_p1_maintain`, and demotes the unit-local `open` column to optional-later. §12 records it; PR3 says "no partial-index-by-ledger-status". |
| **R6** (P2) | 2 h stale cutoff can be shorter than a legitimate heavy rebuild; no fencing | **Closed — with the stronger mechanism** | §5.5.2 liveness: side-thread heartbeat (`maintain_heartbeat_s=60`, stale at 3×) is **binding for heavy**; a live process with a fresh heartbeat is never reclaimed. Wall-clock becomes a secondary net whose heavy cutoff **must exceed** measured p99 (`p1_lance_rebuild_duration_ms`, §5.4 knob + rules), and the advisory-lock probe I suggested is included as optional strengthening. §16 adds "Live heavy not reclaimed" and "Stale worker after reclaim". Residual noted at R15. |

**R7–R11 (editorial): all absorbed.** R7 — §5.3.1 chunks `deployment_id` role
is now "tenant filter" only; merge-key members listed correctly. R8 — the
entities gap now names the read-path `deployment_id` ensure in
`search_entities_scored` and states its fate (upgraded-store fallback once
`ensure_search_indexes()` owns the matrix). R9 — §5.5.1 identity table carries
`content_hash` with a stable per-unit diagnostic string and the NOT NULL
reason. R10 — `heavy_enabled` scope is explicit ("Does **not** gate
`BackfillFinalizer` / admin force / offline port tools"), repeated in §5.5.4,
§12, §16, K11. R11 — §5.3 "As-built bug closed" states the non-re-runnable
`build_search_indexes()` and §16 adds the "twice is ensure no-op + clean
retrain" test.

## Disposition of codex-sol r2 blockers

- **P1.4 (reclaim steal / stale completion)** — closed by the same mechanism
  as R1+R6: heartbeat liveness so a live multi-hour call is never classified
  dead; reclaim performs the exact retry-or-dead-letter transition via
  `fail()` including `not_before` and re-announce; completion goes through
  `complete_maintain_p1`, which locks the unit and the `running` processing
  row in one transaction and raises `WorkNotRunningError` otherwise. Both
  demanded acceptance tests exist (§16 "Live heavy not reclaimed", "Stale
  worker after reclaim"). Residual discussed at R15.
- **P1.2 (`rerun_requested` lossy edge + partial index)** — closed by
  §5.5.3's atomic `complete_maintain_p1` (pattern-verified this round:
  `complete_chunk_extract` / `complete_entity_obs_flush` exist at
  `work_ledger.py:248/425`): same coalesce lock, `SELECT … FOR UPDATE` on unit
  and running row, flag consumed and successor inserted **in the same
  transaction**, announce after commit. K16; §16 "Running race / atomic rerun"
  includes the process-death-at-boundary case. The impossible partial-index
  alternative is removed (R5).
- **P1.5 (heavy progress contract)** — closed by §5.7 rule 3: pre-train
  write-rate defer (`heavy_defer_write_rate`, durable stats, no `label_lock`
  probe), single-train `conflict_defer` with long `not_before`, an explicit
  terminal policy (dead-letter is visible, not-open, fresh unit next tick,
  admin force available — "deliberate and visible, not silent stall"), and the
  lock-owner seam (R3). §16 drives continuous writes through exhaustion to a
  provable end state.
- **P2.1 (index matrix incomplete; PR order)** — closed: §5.3.1 adds the
  nominator prefilter columns (`chunks.doc_id`/`source_kind`/`source_shape`/
  `section_role` and `claims.doc_id`, from `LANCE_FILTER_COLUMNS`) with an
  explicit out-of-matrix boundary, and PR1 now ensures the facts join keys
  **before** the large merge ("must not land large merges without join-key
  indexes"; K18).

## Strict-focus verification (this round's three axes)

1. **Reclaim vs CHECK constraints.** The design's quoted CHECKs match the live
   migration byte-for-byte. The bound path (`fail()` per row) satisfies both
   arms by construction: retry sets `failed`/`retry_backoff`/`not_before`
   (`_FAIL_RETRY`), exhaustion sets `dead_letter` (`_FAIL_DEAD_LETTER`) — no
   state the CHECKs forbid is ever written. The return contract the pseudocode
   relies on (`datetime | None`) is real (`work_ledger.py:634,639-642,672,676`).
2. **Heavy defer policy.** One train per claim, defer preferred over train,
   conflict costs exactly one attempt, exhaustion is terminal-and-visible, and
   light/merge keep the short budget. Internally consistent across §1.11, §5.3,
   §5.4, §5.5.3, §5.7 rules 3/6, §9, §12, §16, K17 — except one wording slip
   (R12 below).
3. **Atomic rerun.** `complete_maintain_p1` closes the flag-read → succeed
   window with a single transaction under the coalesce lock; the enqueue side
   (§5.5.2) sets `rerun_requested` only against a genuinely `running` unit, so
   every interleaving lands in exactly one of: coalesced-open, flag-consumed
   successor, or fresh unit. No loss window remains.

## Remaining nits (R12–R16 — fix in the D91 entry commit; no re-review)

- **R12 — §5.5.3's pure-rate-defer sentence contradicts itself; strike the
  fail-retryable alternative.** The paragraph says pure rate defer "should not
  burn the full attempt budget: prefer re-enqueue with `not_before` and
  succeed-as-skipped, **or fail retryable with long `not_before` without
  treating it as a train failure**" (and the handler comment carries the same
  "OR"). The second option *does* burn budget — `_CLAIM_START` increments
  `attempts` on every claim — so three consecutive rate defers dead-letter a
  heavy unit that never attempted a train: the P1.5 failure shape, now merely
  degraded (visible DLQ noise, self-healing via fresh unit) rather than
  silent. §5.7 rule 3 already binds the right mechanism (re-enqueue with
  `not_before`); make §5.5.3 match it and reserve fail-retryable for
  `conflict_defer`, where burning one attempt is the intent.
- **R13 — reclaim pseudocode's announce call doesn't match the port.** §5.5.2
  writes `queue.announce(processing_id, not_before=scheduled)`;
  `TaskQueuePort.announce` (`ports/queue.py:14-22`) requires
  `route_snapshot: QueueRoute` and `not_before_snapshot`. One clause: reclaim
  reconstructs the route snapshot (stage `maintain_p1_index`, lane NULL). This
  design was already burned once by a sketch that couldn't execute (r2 R1);
  don't leave a second one, however small.
- **R14 — reclaim loop must tolerate the select→fail race.** Between the
  candidate SELECT and `fail()`, an owner on the wall-clock fallback path may
  legitimately complete; `fail()` then raises `WorkNotRunningError`
  (`work_ledger.py:654-657`). One line: catch-and-skip per row. (Under the
  binding heartbeat path the candidate is dead and the race cannot arise.)
- **R15 — residual fencing window when the heartbeat *thread* dies but the
  Lance op lives.** Heartbeat freezes, wall-clock reclaim eventually fires, a
  successor claims, and the surviving owner's `complete_maintain_p1` can still
  succeed the successor's row (the transaction requires only
  `status='running'` on the `processing_id`). Lance-side safety holds (table
  lock + idempotent ops) and the flag is consumed atomically, so damage is
  attribution/queue-health only — the same residual r2 R6 accepted. Either
  promote §5.5.2's advisory-lock probe from optional to **binding** for the
  wall-clock fallback (a held lock is direct evidence of a live owner), or add
  one sentence explicitly accepting the attribution-only residual.
- **R16 — pseudocode precedence in `ensure_maintain_due`.** "if stats missing
  or older than `maintain_probe_min_s` AND (…)" parses as
  `missing OR (older AND …)`; parenthesize to match intent.

## Checklist re-run

| # | Contract | r1 | r2 | r3 |
| --- | --- | --- | --- | --- |
| 1 | Two-layer model complete | Pass | Pass | **Pass** |
| 2 | Bulk merge correctness (vectors/labels) | Pass | Pass | **Pass** |
| 3 | Batch semantics (dedupe, misses, failure) | Fail | Pass | **Pass** |
| 4 | Write amplification bounded end-to-end | Fail | Pass | **Pass** |
| 5 | Index set enumerated | Fail | Pass (wording) | **Pass** (R7/R8 fixed; prefilter columns added) |
| 6 | One ledger protocol | Fail | Pass | **Pass** |
| 7 | Ledger grain matches physical objects | Fail | Pass | **Pass** |
| 8 | Lane / route valid | Fail | Pass | **Pass** |
| 9 | Readiness / profile wiring | Fail | Pass | **Pass** |
| 10 | Concurrency: writer ↔ maintain | Concern | Pass | **Pass** |
| 11 | Concurrency: maintain ↔ purge | Fail | Concern (R3) | **Pass** (owner bound, all three callers) |
| 12 | Crash / stuck-lease recovery implementable | Fail | Concern (R1) | **Pass** (`fail()`-based, CHECK-safe, heartbeat-fenced) |
| 13 | `BackfillFinalizer` unified on the port | Concern | Concern (R3, R10) | **Pass** (locks taken; `heavy_enabled` scope explicit) |
| 14 | Migrations vs executable catalog contract | Fail | Pass | **Pass** |
| 15 | Rollout realistic | Concern | Pass | **Pass** |
| 16 | PR plan realistic | Concern | Pass | **Pass** (PR1 join-key ensure; reclaim in PR3) |
| 17 | Docs obligation (D66 same-PR) | Fail | Pass | **Pass** |
| 18 | Rule 1 (cold-reader legibility) | Fail | Pass | **Pass** |
| 19 | Rule 2 (full scope, no phasing) | Fail | Pass | **Pass** |
| 20 | Rule 3 (library boundary) | Pass | Pass | **Pass** |
| 21 | Analysis ↔ code accuracy | Nit | Pass | **Pass** (fresh spot-checks hold) |
| 22 | Heavy progress guarantee under ingest | — | Concern (R2) | **Pass** (R12 wording nit only) |
| 23 | Self-seed cost on the real execution edge | — | Concern (R4) | **Pass** (floors + durable-stats-first bound) |

## Closing

The r2 approval gate was "fix R1 and R2 in the design text before D91 is
entered". Both are fixed as specified, everything else landed alongside, and
the new text is accurate against the code it cites. **Enter D91 in
`decisions.md`.** Apply R12–R16 (five one-to-two-line edits, R12 first) in the
same commit; none changes a decision and none needs another review round.
