# Proposal: ledger-backed `maintain_p1_index` units

**Status:** relevant, **not chosen**  
**Date:** 2026-08-14  
**Adoption trigger:** we need more than one maintain replica per
`lance_root`, **or** product requires maintain failures to share the D67
dead-letter / replay surface with extract/embed.  
**Superseded by:** D91 ticker amendment (2026-08-14) in
`plan/designs/p1_lance_maintenance_design.md`  
**Analysis:** `plan/analysis/p1_lance_maintain_ticker_analysis.md`

## What this is

The first D91 draft (design PR #270 through r4, implementation PR #276)
modeled continuous Lance maintenance as:

- `pipeline_stage = maintain_p1_index` (unlaned)
- `processing_target = p1_maintain_unit`
- table `p1_maintain_units` (heartbeat, `rerun_requested`, `claimed_attempt`)
- coalesce under an enqueue xact lock
- `complete_maintain_p1(..., expected_attempt=)`
- `reclaim_stale` via attempt-fenced `WorkLedger.fail`
- heartbeat side-thread during `create_index`

Three modes (`light` / `heavy` / `ensure_indexes`) were three job kinds.

## Why it lost

Lance `optimize()` and `create_index(..., replace=True)` are idempotent
dataset commits. Concurrent writers are allowed. The only exclusive rule is
`delete_unverified` vs any other worker, plus “do not run two maintainers on
one table.” That is an advisory lock, not a claimed attempt.

The ledger design then had to invent reclaim, heartbeat, and attempt fences
because a multi-hour IVF train does not fit D67’s short-job stale-cutoff.
That complexity is real if we *choose* the ledger. It is not implied by
Lance. Self-host has one root and one maintain process; a ticker is enough.

## What would have to become true

Adopt this proposal if any of:

1. **Multiple maintain processes** must share one `lance_root` and we want
   SKIP LOCKED claim semantics rather than “try-lock, skip table.”
2. Ops requires maintain failures on the **same DLQ / replay CLI** as
   `embed_claim` (attempt budget, `remember ops` poison/replay).
3. We need per-mode pending queues that survive ticker downtime for hours
   without a process running (durable “heavy is due” as a claimed row).

Until then the stats row (`operator_state`, `change_mass_since_heavy`) is
the durable control plane, and the ticker is the only runner.

## Cost of adopting later

Additive: enum values, `p1_maintain_units`, handler, reclaim. The ticker
lock key and `p1_lance_table_stats` stay. Do not run ticker and claimed
units on the same table without a written cutover (double-train).
