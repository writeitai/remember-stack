# Design review (round 3) — D90 entity-grain observation flush fan-out

**Reviewer:** grok (read-only design inspection after r2 absorb `08b0d8fd`)  
**Date:** 2026-08-12  
**Branch / PR:** `design/d89-entity-obs-flush-fanout` (#262)  
**Under review:** `plan/designs/e3_entity_obs_flush_fanout_design.md` @ `08b0d8fd`,  
`decisions.md` §D90, r1/r2 reviews under `design/reviews/REVIEW_*entity_obs_flush*`  
**Cross-check:** `e3.py` (OBS_FLUSH_VERSION, supersession reconstruct, sibling follow-ups),  
`work_ledger.py` (claim barrier / advisory locks), `observation_adjudication.py`  
(reverse-arrival / open_candidates), `processing.py` (`ProcessingTarget.ENTITY`),  
`fact_catalog.py` (session advisory lock on dedicated connection)

## Verdict

**REQUEST_CHANGES** — one remaining **blocker** (follow-up lease identity).  
r2 dual-review blockers (membership coordinates, ban `document_version` at
fan-out generation, reverse-arrival recompute) are **closed** in `08b0d8fd`.  
No redesign of the unit model is required.

## Summary

Post-r2 design correctly binds version-scoped flush **units** (`unit_id` as
ledger `target_id`), durable membership + `obs_flush_version_state`, exclusive
cutover vs legacy version-serial flush, within-unit total order, whole-unit
entity lock / no TOCTOU LLM apply, sibling supersession + embed_claim, and
§5.5.1 schedule-independent open-window recompute for multi-version reverse
arrival. That closes the class of failures that would silently drop a version’s
flush or leave overlapping D43 windows under continuous ingest.

What remains is a hand-off identity gap: when the last unit completes (or empty
completion fires), §5.4 lists payload coordinates and says “preserve today’s
topology” but never binds **`EnqueueWork.target_kind` / `target_id`** for
supersession and embed. Today’s handlers enqueue
`target_kind=document_version`, `target_id=<version_id>`
(`e3.py:799-828`). The completing work row’s `target_id` is a **unit_id**. A
literal port that copies `work.target_id` into follow-ups creates orphan
leases, readiness/lifecycle that never see the expected version-scoped jobs, and
a version that looks “obs flush done” while supersession/reconcile never run for
that version — the same silent-stall class as Claude r2 R1.

## Closed r2 findings (this revision)

| Finding | Status |
| --- | --- |
| Claude r2 R1 — membership missing `representation_id` / `chunker_version` | **Closed** — §5.1 columns + version_state; barrier lock key from membership/state, not live `current_representation_id` |
| Claude r2 R2 — `document_version` row at fan-out component version | **Closed** — §1.8, §5.1, §5.7, §5.8 call sites → `obs_flush_version_state` only |
| Codex r2 B1 — reverse multi-version windows `t3,t1,t2` | **Closed as co-requisite** — §5.5.1 full ordered recompute; acceptance case in §9 |
| r1 B1 bare entity target_id | **Still closed** — unit_id membership |

## Blocking findings

### B1 — Barrier / empty follow-ups must bind lease identity to the version, not the unit

**Severity:** BLOCKER  
**Anchors:** design §1.4, §1.8, §5.2.4–5, §5.4.4; `src/rememberstack/workers/e3.py:799-828`  
(current sibling enqueue); readiness/lifecycle expectations for version-scoped
`adjudicate_supersession` / `embed_claim` / `reconcile`.

**Gap.** Completing work under fan-out has `target_kind=entity`,
`target_id=unit_id`. Follow-ups must **not** inherit that lease identity.
Design never states the binding triple:

- `target_kind = document_version`
- `target_id = version_id` (from membership / `obs_flush_version_state`)
- stages/component versions as today (`ADJUDICATOR_VERSION`, `P1_EMBED_CLAIMS_VERSION`)

“Preserve today’s topology” is not an implementation contract under a cold
read of §5.4 alone.

**Failure.** Last unit succeeds → barrier enqueues supersession with
`target_id=unit_id` → ops/readiness look for version-level rows → reconcile
chain never attaches to the version → connector cycle waits; or duplicate
conflicting shapes if a later empty path also enqueues correctly.

**Required change (paragraph-sized).** In §5.2 (empty) and §5.4 (barrier), bind
exactly:

```text
EnqueueWork(
  target_kind=document_version,
  target_id=<version_id from membership/version_state>,
  stage=adjudicate_supersession | embed_claim,
  component_version=<existing adjudicator / P1 embed versions>,
  payload={
    version_id, representation_id, doc_id, normalizer_version,
    chunker_version,  # required when relation_ids omitted
    # relation_ids optional; omit only when reconstruction coords all present
  },
)
```

Add a §9 case: “last unit complete → supersession + embed_claim rows exist with
`target_id = version_id`, not `unit_id`.”

## High (non-blocking if B1 fixed; fix before impl freeze)

### H1 — Session entity lock must pin dedicated-connection ownership

**Anchors:** design §5.6 preferred shape; code today uses
`pg_advisory_xact_lock` for entity apply
(`observation_adjudication.py:884`); session-lock precedent
`fact_catalog.py:174-192` (`label_lock` on `engine.connect()` held across
commits).

**Gap.** Preferred scale path is session advisory lock + short per-assertion TX.
Without “dedicated connection, not returned to the pool while held; unlock in
`finally`,” pool reuse leaks locks or unlocks the wrong session.

**Required change.** One sentence in §5.6: session locks use a dedicated
connection (same pattern as `FactCatalog.label_lock`); xact-lock whole-unit
remains the small-entity default.

### H2 — Same-entity single-flight claim mechanism is unbound

**Anchors:** §1.7, §5.5 (“at most one `running`”; claim order
`(min_asserted_at, version_id, unit_id)`).

**Gap.** Correctness of windows is carried by §5.5.1 recompute + entity lock,
so this is not a silent D43-wrongness blocker. But implementers need either:
claim SQL that defers other units for the same `subject_entity_id`, or handler
requeue when another unit is running. Pin one mechanism (handler defer is
enough) so dual-flight running rows are not “surprise allowed.”

### H3 — Exact fan-out component-version string still ambiguous

**Anchors:** §1.1 “OBS_FLUSH_VERSION with suffix `:entity-fanout-1`”; code
`OBS_FLUSH_VERSION = "e3-obs-flush-2026.08a:claim-fanout-1"` (`e3.py:64`).

**Gap.** Append vs replace yields different strings and breaks cutover/dispatch.
Write the **literal** generation string (recommend replace claim-fanout suffix:
`e3-obs-flush-2026.08a:entity-fanout-1`) in design + D90.

## Nits

- N1: analysis §4 still says embed follows supersession (non-binding; siblings).  
- N2: D88 design still has “v1 choice” wording (§5.6) — Rule 2 nit, no D90 effect.  
- N3: D90 decision text should note r2 absorb + remaining B1 if not fixed in same
  commit as this review response.  
- N4: §5.5.1 is implementable; prefer one concrete algorithm name in impl PR
  (“entity-local re-cap along total order after each reverse insert”).

## Checklist (12 items)

| # | Item | Assessment |
| --- | --- | --- |
| 1 | Expected entity set pin vs live staging | **PASS** — membership at claim barrier TX |
| 2 | Empty staging path | **PASS** — `obs_flush_version_state.empty_complete` + siblings; no doc_version at fan-out gen |
| 3 | DLQ / missing unit blocks supersession | **PASS** — anti-join on membership |
| 4 | Within-entity order + undated | **PASS** — `(asserted_at NULLS LAST, claim_id, statement)` |
| 5 | Cross-entity independence | **PASS** |
| 6 | Continuous multi-version + locks | **PASS** with §5.5.1 co-requisite (was FAIL at r2) |
| 7 | complete_entity_obs_flush vs claim-barrier locks | **PASS** — shared family / fixed order |
| 8 | Idempotent re-run after partial unit | **PASS** — per-assertion staging delete under lock |
| 9 | Legacy version-serial cutover | **PASS** — mutual exclusion §5.7 |
| 10 | No LLM in multi-assert TX / TOCTOU | **PASS** — §5.6; H1 for session-lock ops detail |
| 11 | Readiness / lifecycle / forget | **PASS** via membership; **FAIL until B1** for follow-up identity |
| 12 | Overclaiming vs under-specifying | **PASS** after r2 absorb except B1 hand-off identity |

## Recommendation

1. Absorb **B1** (and preferably H1–H3) in a small design commit on #262.  
2. Then **APPROVE** for implementation.  
3. Impl PR must ship §5.5.1 recompute with the design’s acceptance case, not
   only unit fan-out.

## Live BEAM signal (context only, not a design defect)

Host `46.224.68.100` D88 claim normalize: **15 000/15 000 succeeded**. Obs flush
still **one version lease**, ~2.5–3 assertions/min, staging ~5.7k remaining —
exactly the scale pain D90 targets. Do not block design merge on that drain;
do not restart the serial adj worker mid-run without need.
