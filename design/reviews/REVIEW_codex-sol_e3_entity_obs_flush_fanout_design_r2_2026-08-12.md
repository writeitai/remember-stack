# Round-2 design review: D90 entity-grain observation flush fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Codex (`codex-sol`)

**AGENT:** `codex-sol`

**Date:** 2026-08-12

**Branch:** `design/d89-entity-obs-flush-fanout` at `e80d6341`

**Round-1 reviews:**
[`REVIEW_claude-opus_e3_entity_obs_flush_fanout_design_2026-08-12.md`](REVIEW_claude-opus_e3_entity_obs_flush_fanout_design_2026-08-12.md),
[`REVIEW_codex-sol_e3_entity_obs_flush_fanout_design_2026-08-12.md`](REVIEW_codex-sol_e3_entity_obs_flush_fanout_design_2026-08-12.md)

## Summary

The rewrite closes almost all of the dual-review correctness gaps. The durable
`obs_flush_entity_units` membership gives each `(version, normalizer generation,
entity)` an independent D12 ledger identity; fan-out and membership are atomic;
the barrier can detect a missing processing row; handlers reconstruct
coordinates from membership; empty completion is durable; readiness, lifecycle,
forget, replay, cutover, and downstream sibling follow-ups now have
version-scoped contracts. The total within-unit order is pinned, the entity lock
spans the unit, and stale LLM decisions cannot be applied after an unvalidated
unlock.

One first-round blocker remains. D90 explicitly permits same-entity units from
different versions to apply in lock-acquisition order rather than source-time
order (`plan/designs/e3_entity_obs_flush_fanout_design.md:55-61,181-186`). The
current reverse-arrival path is not schedule-independent: after a later open
slice exists, each older arrival is capped at that same open successor while
previously closed slices are excluded from consideration
(`src/rememberstack/spine/observation_adjudication.py:250-255,443-497`). Three
changing states applied in order `t3, t1, t2` therefore produce overlapping
`[t1,t3)` and `[t2,t3)` windows instead of `[t1,t2)` and `[t2,t3)`. That is a
wrong D43 historical outcome, not merely an undocumented limitation.

The claim that this is “not worse than today” does not close the finding. D90
says it preserves D43/D88 and amends only the flush ledger grain
(`plan/designs/e3_entity_obs_flush_fanout_design.md:11-16`; `decisions.md:3619-3621`),
while D88 already states that the observation ladder is order-sensitive and
that entity locking alone is not order independence
(`plan/designs/e3_claim_level_normalize_fanout_design.md:36-46,59-64`). An
existing reachable defect is still a binding conflict when the new design
adopts it as the steady-state continuous-ingest contract.

## Closed first-round findings

| First-round finding | Disposition | Round-2 closure |
| --- | --- | --- |
| Codex B1; Claude B1 — bare entity ledger identity / cross-version collision | **CLOSED** | `unit_id` is the ledger target and membership is unique on `(deployment_id, version_id, normalizer_version, subject_entity_id)` (`design` §1.1-2, §5.1). Two versions sharing an entity receive distinct units. |
| Claude B2 — no durable version-to-job join / missing-child detection | **CLOSED** | Membership is authoritative, is materialized with processing rows in the claim-barrier transaction, and is anti-joined to processing state; a membership row without a processing row blocks (`design` §1.3-4, §5.2, §5.4). |
| Claude B3 — hard forget can strand an unrelated shared-entity job | **CLOSED** | Work coordinates no longer depend on payload, and forget is scoped through membership `version_id` / `doc_id`, with an explicit prohibition on bare-entity scrubbing (`design` §5.8). |
| Codex B3; Claude B5 — stale LLM result / lock release inside an ordered unit | **CLOSED** | The allowed shapes either retain a session/xact entity lock for the unit or revalidate the open block and restart on change. Per-assertion apply and staging deletion share a short transaction while the session lock remains held (`design` §5.6). |
| Codex B4 — empty readiness, lifecycle, and forget contract | **CLOSED** | Empty fan-out has a durable success timestamp; readiness joins membership and treats empty success explicitly; lifecycle includes unit DLQs; forget uses version membership (`design` §5.1, §5.8). The exact empty-marker schema remains a nit. |
| Codex B5; Claude B4 — legacy/fan-out coexistence and mixed-image cutover | **CLOSED** | A non-terminal legacy row blocks fan-out, materialized membership excludes legacy work, the entity path forbids version-wide staging clear, and producer enablement is capability-gated or stop/drain/restart (`design` §5.2.1, §5.7). |
| Claude B6; total-order portion of Codex B2 — tied and undated assertions | **CLOSED within one unit** | The key is exactly `(asserted_at NULLS LAST, claim_id, statement)` and D43's existing undated boundary rule is retained (`design` §1.5, §5.3, §5.5). Cross-version ordering remains open below. |
| Claude B7 — ambiguous empty/downstream handoff | **CLOSED** | Both `adjudicate_supersession` and `embed_claim` are explicitly enqueued once as sibling follow-ups for empty and non-empty completion (`design` §1.8, §5.2.4, §5.4.4). |

## Remaining blockers

### B1 — Same-entity cross-version scheduling can still create wrong D43 windows

D90 binds only a per-version sort and deliberately makes same-entity
cross-version order the advisory-lock schedule
(`plan/designs/e3_entity_obs_flush_fanout_design.md:50-61,181-186`). Holding the
lock for each whole unit prevents interleaving; it does not place the units in
source-time order.

For three positively matched changing-state assertions on entity E at
`t1 < t2 < t3`, let the three version units acquire the lock in order
`t3, t1, t2`:

1. `t3` inserts O3 as `[t3, infinity)`.
2. `t1` is source-earlier than open O3, so the reverse-arrival branch inserts
   O1 and caps it at O3's `valid_from`: `[t1,t3)`.
3. `t2` reloads the entity block. O1 is closed and therefore excluded from
   `open_candidates`; O3 is again the only competitor. The same branch inserts
   O2 as `[t2,t3)` and leaves O1 unchanged.

The interval `[t2,t3)` now returns both O1 and O2 even though these are
successive values of one changing state, not a deliberate contradiction. D43
requires a supersede to cap the prior slice at the successor's `valid_from`
(`plan/designs/observations_design.md:117-119,184-195`). `_pull_valid_from_earlier`
does not repair this case: it handles equivalent evidence reuse, while the
changing-state reverse path inserts a new closed predecessor
(`src/rememberstack/spine/observation_adjudication.py:673-718` versus `:443-497`).

**Required change:** bind either:

- source-time sequencing for all ready units that contend on the same entity,
  while retaining concurrency across distinct entities; or
- a schedule-independent D43 insertion/recompute rule that finds the incoming
  slice's correct predecessor and successor and repairs both boundaries under
  the entity lock.

The acceptance case must apply three versions in completion order `t3, t1, t2`
and obtain exactly `[t1,t2)`, `[t2,t3)`, `[t3,infinity)`. The contract must also
state how the already-defined null and tie-break order applies across version
units, not only inside one unit.

## Nits

- **N1 — stale section references.** Several references point one section too
  far: whole-unit locking cites §5.7 but lives in §5.6, and exclusivity cites
  §5.8 but lives in §5.7 (`design` lines 53, 67, 70, 127, 151, 153). The failure
  table's legacy row is the one correct §5.7 reference.
- **N2 — Claude B8 is only partially closed.** The revised D90 design and D90
  decision removed MVP framing, but the amended D88 binding design still says
  “v1 product semantics,” “binding order policy for v1,” and “v1 choice”
  (`plan/designs/e3_claim_level_normalize_fanout_design.md:59-62,211-214`),
  contrary to `CLAUDE.md` Rule 2. This has no D43/barrier/data-loss effect and is
  therefore a nit under the round-2 verdict threshold.
- **N3 — pin one empty-state representation.** §5.1 leaves the durable empty
  signal as either a special document-version processing row or
  `obs_flush_version_state`. Both can satisfy correctness, but the choice changes
  readiness and dispatch topology and should be made in the binding design.
- **N4 — pin the exact component-version literal and barrier-lock key.** The
  design still says only “with suffix `:entity-fanout-1`” despite the current
  constant already carrying `:claim-fanout-1`, and leaves shared-versus-distinct
  lock keys to the implementation PR. Neither is a blocker if the generation is
  unique and the stated fixed acquisition order is obeyed.
- **N5 — refresh the non-binding analysis.** It still names the nonexistent
  `ObservationFlushHandler`, says embed follows supersession rather than being a
  sibling, and states the shorter `(asserted_at, claim_id)` ordering key
  (`plan/analysis/e3_entity_obs_flush_fanout_analysis.md:23,95-103`).
- **N6 — review status overclaims closure.** D90 and the analysis currently say
  all first-round blocking findings were absorbed (`decisions.md:3610-3612`;
  `analysis` lines 150-151). That should be updated after B1 is resolved.

## Checklist

| # | Review item | Round-2 assessment | Review |
| --- | --- | --- | --- |
| 1 | Expected entity set pin vs live staging after partial flush | **PASS** | Membership is durable and independent of draining staging; unit and processing-row creation occur in the barrier transaction. |
| 2 | Empty staging path | **PASS WITH NIT** | Durable success plus both sibling follow-ups are bound. Choose one empty-marker schema (N3). |
| 3 | Dead letter / failed / missing unit blocks downstream | **PASS** | Readiness is an anti-join from membership; every non-succeeded or absent child blocks. |
| 4 | Within-entity order and undated claims | **FAIL across versions** | The within-unit key is total and null placement is fixed, but independently scheduled version units are not globally source-ordered (B1). |
| 5 | Cross-entity independence | **PASS** | D43 blocks and writes by subject entity, so distinct entities can complete in any order. |
| 6 | Continuous multi-version ingest and entity locks | **FAIL** | Units no longer collide and the lock prevents concurrent writes, but lock scheduling can still produce overlapping historical windows (B1). |
| 7 | `complete_entity_obs_flush` vs claim-barrier lock ordering | **PASS WITH NIT** | The design requires a shared family or fixed global order; pin the concrete key topology (N4). |
| 8 | Idempotent rerun after partial unit progress | **PASS** | Per-assertion apply and staging deletion are atomic under the retained unit lock; empty-success is limited to this unit's own prior progress. |
| 9 | Legacy version-serial cutover | **PASS** | Same-version mutual exclusion, no version-wide clear on the entity path, and mixed-image gating are explicit. |
| 10 | LLM / transaction protocol | **PASS** | Unlock-without-revalidation is rejected; every allowed shape preserves snapshot validity and prevents another writer interleaving inside the unit. |
| 11 | Readiness / lifecycle / forget | **PASS** | All three derive through version-scoped membership, including empty success and unit DLQ, without shared-entity payload damage. |
| 12 | Overclaiming vs under-specifying | **FAIL** | D90 claims to preserve D43/D88 while explicitly accepting a schedule that can violate D43's source-time windows. The scale and barrier claims are otherwise appropriately bounded. |
