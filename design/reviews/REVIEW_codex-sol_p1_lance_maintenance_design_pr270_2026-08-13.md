# Design review: PR #270 — P1 Lance bulk writes and maintenance (D93)

**Reviewer:** Codex (`gpt-5.6-sol`)  
**Date:** 2026-08-13  
**PR:** `#270` (`design/d91-p1-lance-maintenance` → `main`)  
**Head:** `68d8d6f7aa67e3ce08f684f2c059f3286df8ad38`  
**Scope:** final D93-entry revision, with emphasis on §5.4.1 and the trigger /
change-mass amendment

## Verdict

**APPROVE_WITH_NITS**

No blocker remains in the focused contracts. The design now gives a cold
implementer one coherent trigger model, and the D93 entry captures the material
decisions rather than merely linking to them.

## Focused review

| Concern | Result | Review |
| --- | --- | --- |
| Trigger observers | **Pass** | §5.4.1 assigns distinct authority to all three observers: P1 writers update durable counters and may enqueue **light only**; idle `ensure_maintain_due` reads durable stats first, probes Lance when needed, and may enqueue light or policy-qualified heavy; finalizer/admin/CLI ensure indexes and may force heavy, with the finalizer explicitly outside `heavy_enabled`. D93 item 3 preserves this division. |
| Three modes | **Pass** | `ensure_indexes`, `light`, and `heavy` have separate triggers and effects. Light is incremental `optimize`; heavy is replacement IVF/optional FTS rebuild; ensure creates missing contracted indexes. Content/embedding migration remains a separate rebuild family, so it is not accidentally treated as a fourth maintain mode. |
| Change mass vs calendar | **Pass** | Heavy discovery is driven by durable vector-rewrite row count/mass, row growth, or leftover unindexed ratio. Flat-count vector updates can therefore trigger heavy. `heavy_rebuild_min_hours` is consistently an anti-thrash floor, not a calendar discovery signal. D93 item 4 records that distinction. |
| Chunk sensitivity | **Pass** | Chunks have the strictest changed-row fraction (`0.05`), lower mass threshold (`2e6`) than claims/facts, and lower growth threshold (`5%`). The binding ordering is explicit even though starting values remain subject to BEAM measurement. D93 states that chunks are more sensitive than short-text facts/claims. |
| Skip-unchanged mass exclusion | **Pass** | The authoritative counters increment only when the Lance `vector` column is actually written. Eligibility-only matched merges, skip-unchanged rows, and no-op upserts are expressly excluded. D93 item 4 repeats the exclusion, and PR5/acceptance tests cover it. |
| D93 completeness | **Pass** | D93 contains the bulk-write rule, all three maintain modes, all three observer classes, physical grain, durable change-mass policy, chunk sensitivity, eligibility exclusion, calendar-floor semantics, and the sustained-write `awaiting_operator` outcome. It is complete enough to serve as the decision-log authority while leaving mechanisms to the binding design. |

The external premise also checks out against public LanceDB documentation:
`optimize()` performs compaction, cleanup, and incremental updates to existing
indexes, while OSS deployments manage cadence themselves
([reindexing](https://docs.lancedb.com/indexing/reindexing)); per-call writes
create fragment/version pressure, merge updates delete and reinsert matched
rows, and indexed join columns matter at scale
([performance](https://docs.lancedb.com/performance)). Those facts support the
light/heavy separation and skip-unchanged requirement.

## Nits

1. Finish the r4 escalation-state cleanup. §5.5.1 and §5.6 correctly make
   `p1_lance_table_stats` authoritative for `rate_defer_count`,
   `conflict_defer_count`, `first_defer_at`, and `operator_state`, with the unit
   field display-only. Residual text still checks `unit.operator_state`, says to
   bump `unit.rate_defer_count`, puts defer counters on `p1_maintain_units` in
   PR3, and says the acceptance counters live on the unit (§§5.5.3, 15, 16).
   Point those references at table stats so implementation and tests cannot
   revive the cross-successor reset bug. The explicit authoritative rule keeps
   this below the change-request threshold.
2. Remove stale lifecycle wording: the design footer still says it becomes
   binding “once D93 is entered,” and the analysis footer/reference still calls
   D93 “proposed,” although the header and `decisions.md` show it entered. This
   is editorial only.

## Approval gate

Satisfied. PR #270 may merge with the two consistency nits handled in this PR
or the first implementation-doc touch; neither changes the decision or requires
another design round.
