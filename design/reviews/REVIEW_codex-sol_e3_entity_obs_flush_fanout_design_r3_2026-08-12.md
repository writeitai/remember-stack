# Round-3 design review: D90 entity-grain observation flush fan-out

**AGENT:** `codex-sol`  
**Date:** 2026-08-12  
**Branch:** `design/d89-entity-obs-flush-fanout` at `08b0d8fd`

## Verdict

**REQUEST_CHANGES** — one binding D43 ordering gap remains. The ledger,
barrier, empty-path, hand-off, cutover, locking, readiness, lifecycle, and forget
contracts are otherwise ready.

## Summary

The current revision closes the two round-2 hand-off blockers. Membership and
`obs_flush_version_state` now durably carry the representation/chunker/extractor
coordinates needed for the barrier and downstream reconstruction; the empty
completion artifact is pinned; `document_version` work is forbidden at the
fan-out component generation; and all shortcut call sites are explicitly routed
to empty completion or the legacy generation. The first-round identity,
missing-child, false-barrier, payload-loss, stale-LLM, cutover, and total
within-unit-order findings also remain closed.

The remaining Codex round-2 ordering finding is only partially closed. The new
single-flight rule prevents two units for one entity from running together, and
§5.5.1 fixes the specific `t3, t1, t2` overlapping-window example. But ordering
an entire version slice by its **minimum** assertion time is not the same as
applying all assertions for the entity in the bound total order. Because D43's
outcome itself can be order-sensitive (especially evidence collapse), a
validly completed unit set can still leave the wrong current observation and
history. This meets the stated REQUEST_CHANGES threshold: wrong D43 truth can be
followed by an otherwise successful downstream barrier.

## Prior findings closed/open

| Prior finding | R3 status | Verification |
| --- | --- | --- |
| Codex B1 / Claude B1: bare entity ledger identity collides across versions | **CLOSED** | `unit_id` is the D12 ledger target; durable membership is unique on `(deployment_id, version_id, normalizer_version, subject_entity_id)` (design §1.1–2, §5.1). |
| Claude B2 / Codex B4: no authoritative expected set, missing child undetectable | **CLOSED** | Membership and processing rows are materialized atomically, and readiness anti-joins membership to the fan-out processing generation; absent and non-succeeded rows block (design §5.2, §5.4). |
| Claude B3 / Codex B4: readiness, lifecycle, empty completion, and shared-entity forget | **CLOSED** | `obs_flush_version_state` is the durable zero-unit success signal; readiness/lifecycle/forget are version-scoped through membership, and payload is not authoritative (design §5.1, §5.8). |
| Codex B3 / Claude B5: stale LLM decision or interleaving inside a unit | **CLOSED** | The entity lock spans the whole unit; unlock-without-revalidation is rejected; per-assertion write plus staging deletion stays atomic under the retained lock (design §5.6). |
| Codex B5 / Claude B4: legacy overlap, version-wide clear, mixed-image cutover | **CLOSED** | Same-version mutual exclusion is bidirectional, the entity path forbids version-wide staging clear, and fan-out producer enablement requires capable workers or stop/drain/restart (design §5.7). |
| Claude B6 / Codex B2: non-total within-unit key and unspecified NULL placement | **CLOSED within a unit** | `(asserted_at NULLS LAST, claim_id, statement)` is pinned consistently in the binding design and D90. |
| Claude B7: supersession/embed topology and reconstruction coordinates | **CLOSED** | Both follow-ups are siblings; membership/state supply stable reconstruction fields; the representation barrier key is loaded from durable state (design §5.1, §5.4). |
| Claude r2 R1: missing `representation_id` / `chunker_version` makes supersession silently no-op | **CLOSED** | Membership/state now include `representation_id`, `chunker_version`, and `extractor_version`; the barrier may omit `relation_ids` only when all reconstruction fields are present (design §5.1, §5.4). |
| Claude r2 R2: illegal `document_version` row at the fan-out generation | **CLOSED** | `obs_flush_version_state` is the only empty marker; fan-out dispatch is entity-only; zero-chunk and other shortcut sites must not create the illegal row shape (design §5.1, §5.7). |
| Codex r2 B1: same-entity cross-version schedule can change D43 history | **OPEN (narrowed)** | Single-flight plus §5.5.1 closes the cited three-distinct-state overlap, but `min_asserted_at` orders units rather than all assertions and the repair covers windows, not order-sensitive evidence/contradiction outcomes. See B1 below. |
| Claude B8: prohibited `v1` framing in this binding design/D90 | **CLOSED** | The D90 binding documents no longer frame the decision as a temporary phase. |

## Remaining blockers

### B1 — Minimum-time unit ordering plus window-only repair does not preserve D43 apply order

The design holds the entity lock for a whole version unit and claims same-entity
units by `(min_asserted_at, version_id, unit_id)` (design lines 50–64,
201–223). A unit may contain many assertions for that entity. Consequently, two
pending slices with overlapping time ranges are not merged into the global
`(asserted_at NULLS LAST, claim_id, statement)` order.

A concrete changing-state case:

1. Unit A contains `t1: value=A` and `t3: value=A`; unit B contains
   `t2: value=B`, with `t1 < t2 < t3`.
2. Unit A is claimed first because its minimum is `t1`. D43 inserts A at `t1`,
   then treats the same A assertion at `t3` as evidence for the still-open A
   observation. No separate `t3` observation slice exists; this is the normal
   D43 evidence outcome (`observations_design.md` §3, step 3).
3. Unit B then applies `t2: B`. It supersedes the open A slice and leaves B
   current. The §5.5.1 trigger does not fire: `t2` is not earlier than that A
   observation's `valid_from=t1`.
4. The result is `A [t1,t2), B [t2,∞)`. Source-order application requires
   `A [t1,t2), B [t2,t3), A [t3,∞)`.

Even an unconditional re-cap of existing `valid_from` / `valid_until` values
cannot create the missing A-at-`t3` slice after the `t3` assertion has collapsed
as evidence. The same structural issue can affect which prior receives evidence
or contradiction grouping; it is not just an interval-overlap cleanup problem.
Every unit can still report `succeeded`, so the barrier will enqueue
supersession/embed over the wrong D43 state.

**Required closure:** bind one correctness-preserving rule for overlapping
same-entity version slices. Either merge/drain all ready assertions for an
entity in the full total order while preserving durable per-unit progress, or
define a schedule-independent semantic replay/recompute that can reverse
evidence collapse and rebuild observation slices/outcomes—not only re-cap
existing windows. Add an acceptance case with two units containing
`{t1:A, t3:A}` and `{t2:B}` and require the three source-ordered slices above;
also cover tied/undated assertions split across units.

## Nits

- Several internal references still point to the wrong section: whole-unit
  locking at lines 53 and 166–167 cites §5.7 instead of §5.6, while the
  empty/cutover references at lines 67–72 point to §5.8 instead of §5.7 where
  the call-site rule and exclusivity live.
- Pin the exact fan-out component-version literal. “With suffix
  `:entity-fanout-1`” remains ambiguous when the existing value already has a
  generation suffix.
- Refresh the non-binding analysis: it still names `ObservationFlushHandler`,
  describes embed as following supersession rather than as a sibling, and
  states the shorter `(asserted_at, claim_id)` order (analysis lines 23,
  95–103).
- The implementation design should pair the same-entity atomic claim rule with
  its serving membership/index shape; §5.9 currently names only a
  membership-by-version index.
- Replace “bind unless measured otherwise” in §5.6 with wording that keeps the
  invariant unconditionally binding even if the concrete lock shape changes.

## Final recommendation

**Merge design? No.** Close B1 by making the full D43 outcome independent of
overlapping version-unit boundaries (or by truly applying the entity's
assertions in the global total order). After that narrow correction, the design
should be approvable; no ledger, barrier, empty-path, downstream hand-off,
cutover, readiness, lifecycle, or forget blocker remains.
