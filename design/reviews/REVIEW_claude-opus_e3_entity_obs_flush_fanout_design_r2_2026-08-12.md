# Design review (round 2) — D90 entity-grain observation flush fan-out

**Reviewer:** claude-opus
**Date:** 2026-08-12
**Branch / PR:** `design/d89-entity-obs-flush-fanout` (#262)
**Under review (revision after dual REQUEST_CHANGES):**
`plan/designs/e3_entity_obs_flush_fanout_design.md` (rewritten),
`plan/analysis/e3_entity_obs_flush_fanout_analysis.md` §7,
`decisions.md` §D90,
`plan/designs/e3_claim_level_normalize_fanout_design.md` §5.6 (amendment
paragraph rewritten)
**Round 1:**
[REVIEW_claude-opus_…_2026-08-12.md](REVIEW_claude-opus_e3_entity_obs_flush_fanout_design_2026-08-12.md) (B1–B8),
[REVIEW_codex-sol_…_2026-08-12.md](REVIEW_codex-sol_e3_entity_obs_flush_fanout_design_2026-08-12.md) (B1–B5)

## Verdict

**REQUEST_CHANGES** — narrow. Twelve of the thirteen round-one blocking findings
are genuinely closed, and the decision itself is unchanged and correct. Two
binding gaps remain, both in the **hand-off out of the barrier**, and both land
in the "silent loss / stalled version" class rather than the cosmetic one. Each
is a paragraph-sized doc fix, not a redesign.

## Summary

The revision does the hard thing right. The version-scoped flush unit
(`obs_flush_entity_units` + `target_id = unit_id`) is the correct answer to the
D12 identity trap that both reviewers hit, and it is now carried consistently
through fan-out, barrier anti-join, readiness, forget, ops replay, and the test
plan. §1.2 explains *why* a bare `subject_entity_id` fails in plain language, and
analysis §7 restates it for a cold reader — that is the Rule 1 bar. The
version-wide staging clear is explicitly prohibited on the entity path, mutual
exclusion with legacy is bound in both directions, the total order is pinned to
`(asserted_at NULLS LAST, claim_id, statement)`, and §5.6 now binds the property
that actually matters (no writer interleaves inside a unit; no unlock-for-LLM
without revalidation) instead of leaving two unsafe shapes open. Cross-version
source-time ordering is correctly reframed as a **documented non-goal with a
scope boundary** rather than a phase — that is legitimate design content under
Rule 2, and the "not worse than concurrent version-level flushes today" claim
checks out (two version-level flush rows are already independently leaseable).

What is still open is the last inch. The membership row (§5.1) is the design's
declared source of truth for everything the ledger cannot carry — but it omits
`representation_id` and `chunker_version`, which are exactly the two coordinates
the pinned downstream hand-off needs. Follow §5.4.4 and §5.1 literally and the
barrier enqueues a supersession job whose reconstruction arm silently no-ops and
whose `RECONCILE` chain never fires — with no failing job anywhere (R1).
Separately, a `document_version` row at the fan-out component version is
simultaneously **required** by §5.1's empty-marker option A and **undefined** by
§5.7/§5.9's dispatch table, while four existing enqueue sites will produce
exactly that shape the moment `OBS_FLUSH_VERSION` is bumped (R2).

---

## Closed first-round findings

| Round-1 finding | Status | Where closed |
| --- | --- | --- |
| **Claude B1 / Codex B1** — entity work identity has no version dimension | **Closed** | §1.1–1.2 bind `obs_flush_entity_units` keyed `(deployment_id, version_id, normalizer_version, subject_entity_id)` with `target_id = unit_id`; §8 records the bare-`entity_id` rejection; analysis §7 gives the cold-reader explanation; D90 records it. The chosen shape is verified sound against the code: no consumer joins `target_kind='entity'` rows to `entities` (no `ProcessingTarget.ENTITY` producer exists today), so overloading the kind with a membership PK collides with nothing. |
| **Claude B2 / Codex B4 (readiness)** — no durable version↔job join | **Closed** | §5.4.3 evaluates the anti-join over membership for `(deployment_id, version_id, normalizer_version)`; §5.8 binds readiness/lifecycle to join membership by `unit_id`; §5.1 requires a durable empty-success signal readable without scanning staging. Matches the `_NORMALIZE_CLAIM_STATUS` derivation shape (`readiness.py:335-394`), including its zero-children ⇒ `succeeded` arm. |
| **Claude B3 / Codex B4 (forget)** — payload scrub strands an unrelated version | **Closed, structurally** | §5.8 forbids killing units by bare canonical entity id and scopes the scrub to membership `version_id` / `doc_id`. Verified: the existing scrub predicate is `target_id = ANY(:entity_ids) OR …` (`forget.py:1442-1467`) — with `target_id = unit_id` it can no longer match a live unit, and §5.2's "payload is cache only; load coordinates from membership" removes the payload-nulling hazard for the unit handler. (It does **not** remove it for the barrier — see R1.) |
| **Claude B4 / Codex B5** — cutover coexistence, version-wide staging clear | **Closed** | §5.3 "Do **not** call version-wide `clear_staged_observations`" (the `e3.py:778-783` safety net); §5.2.1 blocks fan-out while a non-terminal legacy row exists; §5.7 blocks the legacy claim once membership exists, and binds capability-gate / stop-drain-restart before any producer exposes unit rows. §5.3's no-op-success arm now names its premise (unit is the sole deleter of its slice). |
| **Claude B5 / Codex B3** — apply atomicity / stale LLM verdicts | **Closed** | §5.6 binds the invariant rather than a code shape: the entity lock is held across the **whole unit apply**, "read under lock → unlock → LLM → write" is rejected by name, the prepare-then-apply arm requires revalidation-under-lock with abort-and-restart, and the narrow `(…, claim_id, statement)` staging delete is called out as available (staging PK includes `statement`, `p9_08_0029:22-34`). Mid-unit reader visibility is stated and justified via readiness gating. Both permitted shapes satisfy the invariant, so leaving two is not a Rule 2 deferral. |
| **Claude B6 / Codex B2 (total order)** — ordering key not total | **Closed** | `(asserted_at NULLS LAST, claim_id, statement)` bound in §1.5, §5.3.2, §5.5 and D90 — matching `_SELECT_OBS_STAGING_ORDERED` (`fact_catalog.py:650-659`). NULLS-LAST is pinned, not offered as a choice. |
| **Codex B2 (cross-version source-time order)** | **Closed as a scope boundary** | §1.7 / §8 / §10 state it as a non-goal with the honest comparison to today's multi-lease flushes, and point at the existing D43 repair rather than inventing one. Legitimate Rule 2 content (documented alternative, not "later"). See N3 for one honesty edit. |
| **Claude B7 (siblings)** — supersession vs embed topology | **Partly closed** | §1.8 / §5.4.4 now bind **sibling** `adjudicate_supersession` (adjudicator version) + `embed_claim` (P1 embed version), matching `e3.py:799-831`, and the empty arm enqueues both. The *payload* half and the four legacy enqueue sites are **not** closed — R1, R2. |
| **Claude B8** — Rule 2 "v1" framing | **Closed** | No "v1 / defer / phase" framing survives in the binding design or D90, and the D88 §5.6 amendment paragraph is rewritten exactly as asked ("D88 binds per-entity ordered apply; D90 binds the ledger work unit…"). |
| **Claude N1** (handler name), **N9** (tests), **N10** (decisions.md wording) | **Closed** | Design uses `AdjudicateObservationsHandler`; §9 adds the two-version, forget-unrelated-doc, legacy-coexistence, statement-tie-break, DLQ and exactly-once cases; D90 is stated in version-scoped terms. |

---

## Remaining blockers

### R1 — Membership omits `representation_id` / `chunker_version`, so the pinned supersession hand-off silently adjudicates nothing and never chains `RECONCILE`

**Anchors:** design §5.1 (binding columns), §5.2 ("payload … is **cache only**.
Handler **must** load coordinates from membership"), §5.4.1, §5.4.4;
`src/rememberstack/workers/e3.py:865-897` (supersession reconstruction arm),
`:903-907` (reconcile chain), `:799-820` (today's payload),
`src/rememberstack/spine/work_ledger.py:1216-1224`
(`d88-normalize-barrier:<representation_id>`),
`src/rememberstack/spine/forget.py:1442-1445` (`payload = NULL`).

§5.4.4 pins the hand-off as "payload may omit `relation_ids`; handler
reconstructs as today". Reconstruction is real but **conditional**: it fires only
when `version_id`, `representation_id`, `normalizer_version` **and**
`chunker_version` are all present as strings in the payload
(`e3.py:872-886`). If any is missing, the arm is skipped, `relation_ids` stays
`[]`, the adjudication loop body never executes, and the handler returns
`succeeded`. Worse, the chain guard at `:903-905` returns a bare
`HandlerOutcome()` when `representation_id` is absent — so **`RECONCILE` is never
enqueued for the version** either.

§5.1's membership columns are `unit_id, deployment_id, version_id,
normalizer_version, subject_entity_id, doc_id, content_hash, created_at`. Neither
`representation_id` nor `chunker_version` is there. Since §5.2 makes the
processing-row payload cache-only — and forget can null it outright
(`forget.py:1442`, no status filter) — an implementer following the design
literally has **no bound source** for those two fields at barrier time.

**Failure scenario.** Version V flushes 40 units. Between fan-out and the last
completion, `hard_forget` runs for an unrelated document that shares an entity;
the LIKE-on-payload arm nulls the surviving unit rows' payloads. The last unit
completes, `complete_entity_obs_flush` builds the supersession follow-up from
membership (`version_id`, `normalizer_version`, `doc_id`) with no
`representation_id`. `AdjudicateSupersessionHandler` adjudicates **zero**
relations, reports success, and enqueues nothing. V's relation supersession is
silently skipped and V never reconciles — no dead letter, no failing job, and
readiness reports `reconcile` missing forever (`selfhost.py:899-903` expects it).

The same missing column blocks §5.4.1: the barrier lock family the design chose
to reuse is keyed on `representation_id`
(`d88-normalize-barrier:<representation_id>`, `work_ledger.py:1216-1224`), and
`complete_entity_obs_flush` cannot compute that key from membership. Re-deriving
it from `document_versions.current_representation_id` at completion time is not
stable: if the current representation flips mid-flush, two concurrent completers
take **different** lock keys, each sees the other's unit as not-yet-succeeded, and
neither fires the barrier — the exact missed-fire the lock exists to prevent.

**Required change.** Add `representation_id` and `chunker_version` to the §5.1
binding columns (both are known in the claim-barrier transaction that
materializes membership), and state in §5.4.4 the **exact** supersession payload
the barrier emits — `version_id`, `representation_id`, `doc_id`,
`normalizer_version`, `chunker_version`, `relation_ids` omitted — with one
sentence on why omitting `relation_ids` is safe *only* when the other four are
present. Then state in §5.4.1 that the barrier lock key is derived from
membership `representation_id`, not from a live `current_representation_id`
lookup.

### R2 — A `document_version` row at the fan-out component version is both required (§5.1) and undefined (§5.7/§5.9), and four existing enqueue sites will create one

**Anchors:** design §5.1 ("**Optional durable empty marker:** either a
version-level processing row at fan-out component version with
`target_kind=document_version` …"), §5.2.4, §5.7 table, §5.9 dispatch rule;
`src/rememberstack/spine/work_ledger.py:713-734` (empty extract),
`:770-791` (all claims already succeeded — replay/migration),
`src/rememberstack/workers/e1.py:631-651` (no chunks),
`src/rememberstack/workers/e2.py:1067-1086` (no chunks).

§5.7 binds two generations only — pre-fanout ⇒ version-serial handler,
`:entity-fanout-1` ⇒ **unit fan-out only** — and §5.9 dispatches on exactly those
two combinations. But §5.1 offers, as one of two permitted empty markers, a
`document_version` processing row **at the fan-out component version**: a row
shape §5.7 declares illegal and §5.9 cannot route. And four call sites in the
current code enqueue `target_kind=DOCUMENT_VERSION, stage=adjudicate_observations,
component_version=OBS_FLUSH_VERSION` — they inherit the bumped constant
automatically. Two of them (`e1.py:639`, `e2.py:1075`) fire on versions with **no
chunks**, which never reach a claim barrier at all, so §5.2's empty path never
runs for them.

**Failure scenario (stall).** A PDF yields no chunks. `e1` enqueues a pending
`document_version` obs-flush row at `…:entity-fanout-1`. A worker claims it
(claims filter on deployment/stage/lane only, `work_ledger.py:999-1012`); §5.9's
dispatch matches neither branch. Either it dead-letters — the version never
reaches supersession/embed/reconcile and the connector cycle waits forever
(`lifecycle.py:1024-1072`) — or the handler falls back to legacy behavior *at the
fan-out generation*, which §5.7 forbids and which drags the version-wide
`clear_staged_observations` back in.

**Failure scenario (unguarded overlap).** The replay arm at
`work_ledger.py:770-791` fires on a version that **does** have staging. Both
mutual-exclusion guards in §5.7 are keyed on "legacy = *pre*-fanout component
version", so a `document_version` row at the *fan-out* version is invisible to
both: §5.2.1 will still fan out units, and §5.7's second bullet will not stop the
version-level row from running. That is precisely the legacy-plus-fan-out overlap
§5.7 exists to forbid, reached through the design's own enqueue paths.

Note the fork is not resolvable by an implementer from the doc: if
`OBS_FLUSH_VERSION` is *replaced* by the fan-out string, these four sites break as
above; if the fan-out generation is a *second* constant, they keep working but
legacy never drains, contradicting §1.9.

**Required change.** Bind one rule and make §5.1 consistent with it: **no
`document_version` work row may exist at the fan-out component version**. State
that all four shortcut sites must instead write the durable empty-completion
signal and enqueue the two sibling follow-ups (§5.2.4), and pick the
`obs_flush_version_state` variant for that marker so an empty completion can never
be confused with — or `ON CONFLICT DO NOTHING` against — a claimable work row.
While there, state the exact resulting component-version string (see N1), since
which of the two readings applies depends on it.

---

## Nits (non-blocking)

- **N1 — exact component-version string (repeat of round-1 N2).** §1.1 still says
  `OBS_FLUSH_VERSION` "with suffix `:entity-fanout-1`". The current value is
  `"e3-obs-flush-2026.08a:claim-fanout-1"` (`e3.py:64`), so "append" yields a
  double suffix. Write the literal and say whether `:claim-fanout-1` is replaced.
- **N2 — internal cross-references are off by one.** §1.5, §1.10 and §5.3.4 cite
  "§5.7" for the locking rules (that is §5.6); §1.9 and §5.3.3 cite "§5.8" for
  exclusivity (that is §5.7). A cold reader lands on the wrong table each time.
- **N3 — §1.7 overstates the reverse-arrival repair.** "Repair of reverse arrival
  uses existing D43 `_pull_valid_from_earlier` / open-slice rules" reads as
  coverage. That helper only pulls `valid_from` earlier on *equivalent-evidence
  collapse*, and explicitly refuses when a later cap boundary exists
  (`observation_adjudication.py:673-712`). Since D90 correctly declares
  schedule-independent multi-version ordering out of scope, say what the
  boundary actually is: out-of-order arrival of three or more distinct states can
  leave overlapping open windows, and the existing helper repairs only the
  equivalent-evidence case. Honest > reassuring.
- **N4 — replay re-fire of the claim barrier.** §5.2.2 ("do not re-insert units;
  only ensure barrier evaluation can still fire") names the requirement but not
  the mechanism. If every unit already succeeded, no completion event remains to
  fire the barrier. The claim path solved this explicitly
  (`work_ledger.py:770-791`); say the re-fire path evaluates the anti-join inline
  and enqueues the siblings when the set is already complete.
- **N5 — rollback after units exist.** §5.7 binds roll-forward but not roll-back.
  An old image claims by stage+lane and would treat `target_id = unit_id` as a
  version id. D88 §5.7 has the matching row ("roll all workers together"); mirror
  it: rollback requires draining unit rows first.
- **N6 — barrier lock contention is worth one sentence.** With the shared
  representation-scoped namespace, ~2.4k unit completions per version serialize on
  one key and each runs the membership anti-join. That is the same shape D88
  already survives at 15k claims, so it is fine — but state that the anti-join
  must be index-served (§5.9's partial index needs the membership-by-version index
  as its partner), since it runs once per unit completion under the lock.
- **N7 — why the pinned set cannot miss rows (repeat of round-1 N7).** Still
  unstated, and it is one checkable sentence: each claim job commits its staging
  writes in its own transaction *before* the ledger completes it
  (`workers/base.py:292-307`), so a barrier requiring all claim rows `succeeded`
  necessarily observes all staging.
- **N8 — §5.6's "bind unless measured otherwise".** The invariant is bound, which
  is what matters, but that phrase reads as an open knob. Prefer "binding; a
  measured alternative that preserves the same invariant may replace the concrete
  shape".
- **N9 — cost-key stability (repeat of round-1 N4).** §7's
  `observation_flush:{unit_id|entity_id}:{index}` — under per-assertion commits the
  index shifts across attempts as the remaining list shrinks
  (`observation_adjudication.py:176`). A `claim_id`/`statement`-derived key
  attributes retries stably. Ops-only.
- **N10 — analysis still says `ObservationFlushHandler`** (analysis §1.1 line 23);
  the class is `AdjudicateObservationsHandler` (`e3.py:715`). The design was
  fixed; the analysis was not.
- **N11 — test plan additions** for the two blockers: empty/no-chunk version
  completes the obs stage end-to-end under the fan-out generation (R2); barrier
  emits a supersession payload that actually reconstructs `relation_ids` and
  chains `RECONCILE` (R1); forget nulling unit payloads mid-flush does not change
  the barrier's hand-off (R1).
- **N12 — pre-existing, out of D90's scope:** `e3_claim_level_normalize_fanout_design.md`
  still carries "for v1" in its own §5.6 heading and body around the (now
  correctly rewritten) D90 amendment paragraph. Worth a sweep when D88 is next
  touched; not this PR's obligation. Same for the "Chosen v1 / Reject v1" cells in
  the D90 analysis §2 — analysis is explicitly non-binding, so Rule 2 does not
  bite, but the design and analysis now use different vocabulary for the same
  rejections.

---

## Checklist

Same twelve items as round one, for comparability.

| # | Item | R1 | R2 | Note |
| --- | --- | --- | --- | --- |
| 1 | Expected entity set pin vs live staging DISTINCT after partial flush | Concern | **Pass** | Membership is materialized atomically in the claim-barrier transaction and is the anti-join's set (§5.2, §5.4.3); staging drain no longer erases the expected set. Add N7's one-sentence proof that the DISTINCT cannot miss rows. |
| 2 | Empty staging path | Fail | **Fail** | §5.2.4 now enqueues both siblings and requires a durable empty signal — but §5.1's marker option A collides with §5.7/§5.9, and the two no-chunk sites never reach the claim barrier at all (R2). |
| 3 | Dead_letter / missing entity rows block supersession | Concern | **Pass** | §5.4.3 makes "membership row without a succeeded processing row at the fan-out version" the blocking predicate, so *missing* is now observable — the thing that was impossible in round one. |
| 4 | Within-entity order + undated `asserted_at` | Fail | **Pass** | `(asserted_at NULLS LAST, claim_id, statement)` bound in three places and in D90; matches `fact_catalog.py:650-659`. Undated supersede boundary explicitly left to existing D43 rules rather than redefined. |
| 5 | Cross-entity independence | Pass | **Pass** | Unchanged and still verified: D43 writes and the candidate block are keyed by `subject_entity_id`. |
| 6 | Continuous multi-version ingest + entity advisory locks | Fail | **Pass** | Unit ids make two versions of one entity two leases; §5.6 keeps the lock across the unit apply so no writer interleaves inside an ordered sequence; cross-version source-time order is a stated non-goal, and the "no worse than today" comparison holds (two version-level flush rows are already independently leaseable). See N3 for the honesty edit. |
| 7 | `complete_entity_obs_flush` lock ordering vs `complete_claim_normalize` | Pass (concern) | **Concern** | Shared representation-scoped namespace is a defensible pick (Codex's recommendation over mine), and no cycle is reachable — but the key has no bound source in membership, and an unstable derivation reintroduces the missed-fire race (R1). |
| 8 | Idempotent re-run after partial entity progress | Concern | **Pass** | §5.3's no-op-success arm now states its premise, and §5.7 + the ban on version-wide clear make the premise true; per-row staging delete + evidence-PK idempotency cover the crash-mid-unit path. |
| 9 | Legacy version-serial cutover | Fail | **Concern** | Mutual exclusion is bound in both directions and the mixed-image rule is stated — but both guards key on "pre-fanout version", which the R2 row shape evades. Rollback unbound (N5). |
| 10 | "No LLM in multi-assert TX" implementable without re-opening design | Fail | **Pass** | §5.6 binds the invariant, rejects the TOCTOU shape by name, requires revalidate-or-abort for the prepare-then-apply arm, and names the narrow staging delete. Both permitted shapes satisfy the same guarantee. |
| 11 | Readiness / lifecycle / forget | Fail | **Concern** | Readiness and lifecycle now have a real join path (§5.8) and forget is scoped by membership rather than bare entity id — the round-one strand-an-unrelated-version hazard is gone. Remaining: the empty-signal artifact is unbound in a way that collides with dispatch (R2), and forget's payload nulling still bites the *barrier* (R1). |
| 12 | Overclaiming vs under-specifying | Concern | **Concern** | No overclaiming; §1.7, §6 and §11 are honest about what is and is not guaranteed, and the largest-hub caveat survives. Under-specification is now confined to the barrier's outbound edge (R1) and one row shape (R2). |

---

## What would make this an approve

1. Add `representation_id` and `chunker_version` to §5.1's binding columns and
   write the exact supersession payload in §5.4.4, plus the membership-derived
   barrier lock key in §5.4.1 (R1).
2. Bind "no `document_version` work row at the fan-out component version", route
   the four shortcut enqueue sites to the empty-completion path, and pick the
   `obs_flush_version_state` marker so it cannot alias a claimable row (R2).
3. Optionally sweep N1–N3 (literal version string, the off-by-one section
   references, and the honest statement of what the reverse-arrival repair does
   not cover).

The decision — version-scoped entity units, serial-in-unit apply under the entity
lock, strict membership barrier before sibling supersession + embed — needs no
change and should survive both fixes verbatim.
