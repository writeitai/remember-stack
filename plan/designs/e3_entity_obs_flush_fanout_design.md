# Design: entity-grain observation flush fan-out

**Status:** revised after dual design review (Claude REQUEST_CHANGES, Codex
REQUEST_CHANGES) — binding once this revision lands on `main`  
**Date:** 2026-08-12  
**Decision log:** D90  
**Analysis:** [e3_entity_obs_flush_fanout_analysis.md](../analysis/e3_entity_obs_flush_fanout_analysis.md)  
**Reviews:**
[REVIEW_claude-opus_e3_entity_obs_flush_fanout_design_2026-08-12.md](../../design/reviews/REVIEW_claude-opus_e3_entity_obs_flush_fanout_design_2026-08-12.md),
[REVIEW_codex-sol_e3_entity_obs_flush_fanout_design_2026-08-12.md](../../design/reviews/REVIEW_codex-sol_e3_entity_obs_flush_fanout_design_2026-08-12.md)  
**Amends:** D88 §5.6 flush **ledger grain** (version-serial lease →
version-scoped entity units in parallel); handoff after claim barrier in
[e3_claim_level_normalize_fanout_design.md](e3_claim_level_normalize_fanout_design.md)  
**Preserves:** D43 entity block + apply-in-order;
[observations_design.md](observations_design.md); D86 inside claim jobs; D88
claim normalize grain  
**Pattern:** D84/D88 advisory lock + complete + anti-join barrier + atomic
expected-set insert — with a **durable version↔unit membership** because entity
ids are not version-scoped

## 1. Decision

1. **Primary work unit** for stage `adjudicate_observations` under the entity
   fan-out generation is one **version-scoped entity flush unit**:
   - Durable membership row in `obs_flush_entity_units` (name fixed in impl
     migration) keyed  
     `(deployment_id, version_id, normalizer_version, subject_entity_id)`  
     with a generated `unit_id uuid` primary key.
   - Ledger row: `target_kind = entity`, `target_id = unit_id` (the membership
     PK — **not** the bare canonical `subject_entity_id`),  
     `stage = adjudicate_observations`,  
     `component_version = OBS_FLUSH_VERSION` with suffix **`:entity-fanout-1`**.
2. **Why not `target_id = subject_entity_id`:** D12 work identity is
   `(deployment_id, target_kind, target_id, stage, component_version)` with no
   version column. Canonical entities are deployment-global. Two versions that
   stage observations for the same entity would `ON CONFLICT DO NOTHING` onto
   one row and either skip a version’s slice or open a false barrier. Chunk and
   claim grains avoid this because their structural path to a version is durable
   and “do once” is correct for the claim; flushing entity E for version V1 is
   **not** correct for V2.
3. **Fan-out** when the claim-normalize barrier would open obs flush: in **that
   same transaction**, materialize the complete unit set + ledger rows for every
   distinct staging entity for that version + normalizer generation (or take the
   empty path). Mark fan-out materialized on a durable version-level flag/row so
   “missing unit” is detectable.
4. **Barrier:** supersession + embed follow-ups enqueue only when every unit in
   the membership set for that version + normalizer generation has a terminal
   `succeeded` processing row at the fan-out component version. `dead_letter` /
   `failed` / missing processing row for a membership unit **blocks**.
5. **Within unit (one entity, one version slice):** apply assertions **serially**
   in total order  
   `(asserted_at NULLS LAST, claim_id, statement)`  
   under the entity advisory lock for the whole unit apply (§5.7).
6. **Across different `subject_entity_id` values:** concurrent units are allowed.
7. **Across different versions of the same entity:** concurrent units are allowed
   and are serialized by the entity lock for writes. Cross-version **source-time
   total order of apply** is **not** guaranteed by D90 and is **not worse than
   today’s** concurrent version-level flushes (already multi-lease). Repair of
   reverse arrival uses existing D43 `_pull_valid_from_earlier` / open-slice
   rules. A schedule-independent multi-version recompute of open windows is
   **out of scope** (separate design if product requires it).
8. **Empty staging:** no units; durable empty completion for the version; enqueue
   supersession **and** `embed_claim` as sibling follow-ups (same topology as
   today’s flush handler — not supersession-only).
9. **Legacy** version-serial flush at pre-`:entity-fanout-1` component version
   remains until drained. Fan-out and legacy for the **same** version must not
   both run (§5.8).
10. **LLM / TX:** bind per-assertion durable writes **without** releasing the
    entity lock mid-unit in a way that allows another writer to interleave
    *inside* this unit’s ordered sequence (§5.7).

## 2. Problem

D88 parallelized claim normalize. Observation flush stayed one version lease.
BEAM-scale staging (~2k+ entities) makes multi-day wall clock; extra adj workers
idle. Analysis §1.

## 3. Rationale

- D43 writes are entity-keyed → parallel **across entities** is sound.
- D88 staging already isolates claim completion order from D43 apply order.
- D12 requires a version-qualified lease identity for work that is not
  version-idempotent → membership `unit_id`.
- D84/D88 durability protocol is required; payload-only membership is not.

## 4. Continuous ingestion

| Scenario | Behavior |
| --- | --- |
| New docs while V flushes | Separate membership sets / unit ids |
| New version same lineage | New `version_id` → new units even for shared entities |
| One unit DLQ | Only that version’s supersession/embed blocked |
| Empty staging | Empty completion + supersession + embed_claim |

## 5. Contracts

### 5.1 Membership table (`obs_flush_entity_units`)

Binding columns (logical; exact SQL in migration):

| Column | Role |
| --- | --- |
| `unit_id` | PK; ledger `target_id` |
| `deployment_id` | tenant |
| `version_id` | document version being flushed |
| `normalizer_version` | claim-normalize generation that wrote staging |
| `subject_entity_id` | D43 block key |
| `doc_id` | optional; for forget / payload-free handler |
| `content_hash` | copy from barrier parent |
| `created_at` | audit |

**Unique:** `(deployment_id, version_id, normalizer_version, subject_entity_id)`.

**Optional durable empty marker:** either a version-level processing row at
fan-out component version with `target_kind=document_version` used **only** for
empty completion / readiness timestamp, or a row in a small
`obs_flush_version_state` table `{deployment_id, version_id, normalizer_version,
fanout_status, completed_at}`. Impl picks one; design requires a **durable empty
success signal** that readiness can read without scanning staging.

### 5.2 Fan-out (claim barrier transaction)

When claim barrier is ready for version V + normalizer generation N:

1. If a **non-terminal legacy** version-level `adjudicate_observations` row exists
   for V at a **pre-entity-fanout** component version → **do not fan out**; leave
   legacy to finish (or ops dead-letters legacy first). Mutual exclusion §5.8.
2. If membership already materialized for (V, N) → do not re-insert units;
   only ensure barrier evaluation can still fire (idempotent complete path).
3. Else `SELECT DISTINCT subject_entity_id, doc_id …` from staging for (V, N).
4. **Empty:** write empty completion signal; enqueue `adjudicate_supersession` +
   `embed_claim` (sibling follow-ups, same stages/versions as
   `AdjudicateObservationsHandler` today).
5. **Non-empty:** insert one membership row + one processing_state row per
   entity (`target_kind=entity`, `target_id=unit_id`, fan-out component version).
   Set fan-out materialized.

`content_hash` / `lane` from barrier parent work.

Payload on the processing row is **cache only**. Handler **must** load
coordinates from membership by `unit_id` (target_id). Missing membership →
non-retryable.

### 5.3 Handler (entity unit)

1. `unit_id = work.target_id`; load membership; validate deployment matches.
2. Load staging for
   `(deployment_id, version_id, subject_entity_id, normalizer_version)` ordered by  
   **`(asserted_at NULLS LAST, claim_id, statement)`**.
3. If no staging rows: succeed (slice already applied / cleared by **this**
   unit’s prior progress only — see §5.8 exclusivity).
4. Apply D43 for that entity only, total order from step 2, under entity lock
   for the **entire unit apply** (§5.7).
5. On full unit success: ensure no staging remains for that slice; return
   success to worker (completion via `complete_entity_obs_flush`).

Do **not** call version-wide `clear_staged_observations`. Only entity-scoped
(or per-assertion-scoped) deletes for this unit’s slice.

### 5.4 Completion + barrier (`complete_entity_obs_flush`)

One transaction:

1. Acquire the **same representation barrier advisory lock family** used by
   `complete_claim_normalize` (shared namespace preferred; if two keys exist,
   acquire in a **fixed global order** documented in the impl PR). This serializes
   last-unit barrier fire with claim-barrier fan-out edges.
2. Mark this unit’s processing row `succeeded`.
3. Ready iff every membership unit for `(deployment_id, version_id,
   normalizer_version)` has a processing row at fan-out component version with
   `status=succeeded`. Any membership unit without a row, or with
   pending/running/failed/dead_letter, is **not** ready.
4. If ready → enqueue **once** (idempotent):
   - `adjudicate_supersession` at existing adjudicator version (payload may omit
     `relation_ids`; handler reconstructs as today), and
   - `embed_claim` at existing P1 embed version  
   as **sibling** follow-ups (preserve today’s topology).

### 5.5 Ordering

| Scope | Rule |
| --- | --- |
| Within unit | `(asserted_at NULLS LAST, claim_id, statement)` only |
| Across entities | Any completion order |
| Across versions same entity | Entity lock serializes writes; apply order is lock schedule, not global source-time (same class as concurrent version flushes today) |
| Undated `asserted_at` | Sort last (`NULLS LAST`); supersede boundary uses existing D43 undated rules (`now()` where already coded) — not redefined here |

### 5.6 LLM and locking (binding)

**Chosen pattern:** **session / transaction-scoped entity lock held for the whole
unit apply**, with **short write transactions per assertion** only if the lock
remains held across them (session-level advisory lock), **or** keep a single
DB transaction for the unit’s writes after all LLM results for the unit are
prepared **only if** the open block is revalidated under the lock immediately
before apply and any change aborts prepare and restarts the unit (no silent
stale apply).

**Rejected as sole path:** “read under lock → unlock → LLM → lock → write
without revalidation” (TOCTOU).

**Preferred concrete shape for impl (bind unless measured otherwise):**

1. Take **session** advisory lock on entity (or xact lock spanning the unit).  
2. For each assertion in order:  
   - re-read open block under lock;  
   - if ladder needed, **LLM while lock held** (latency cost) **or** release is
     forbidden mid-unit without revalidate protocol above;  
   - write outcome + delete that staging row (staging PK already includes
     `statement`) in a short TX **still under the held session lock**.  
3. Release lock; complete ledger row.

If session locks are undesirable, **single xact for the whole unit** (today’s
shape) remains allowed for small entities; for large hubs, session lock +
per-assertion commit is the scale path. Both keep **no other writer interleaving
inside the unit**.

Mid-unit partial visibility to readers is acceptable while version readiness
still blocks “observations complete”; supersession/embed wait on the barrier.

### 5.7 Component version, cutover, exclusivity

| Generation | Behavior |
| --- | --- |
| Pre-`:entity-fanout-1` | Version-serial `AdjudicateObservationsHandler` only |
| `:entity-fanout-1` | Unit fan-out only |

**Mutual exclusion for a given version V:**

- If non-terminal **legacy** version-level flush exists for V → claim barrier
  must not materialize unit fan-out for V.  
- If unit membership is materialized for V → legacy handler must refuse to claim
  work for V (or ops dead-letters legacy before enabling fan-out).  
- Entity-path **must not** invoke version-wide staging clear.  
- Mixed-image rollout: enable fan-out only when all workers understand entity
  units (capability / stop-drain-restart), same class as D88 mixed-image rule.

### 5.8 Readiness, lifecycle, forget

| Surface | Rule |
| --- | --- |
| Readiness | Join `obs_flush_entity_units` → `processing_state` by `unit_id` for the version; empty completion signal reports succeeded for obs stage |
| Lifecycle / connector-cycle | Wait on unit rows for the version (include DLQ); not only document_version work |
| Forget | Scrub **units and processing rows for forgotten versions** via membership `version_id` / `doc_id`. Do **not** null payloads or kill units solely because `target_id` equals a canonical entity id that appears in a forgotten document’s entity set (that strand unrelated versions). Staging delete-by-doc remains. |

### 5.9 Workers and indexes

- Stage name unchanged; scale `worker-adjudicate-observations`.  
- Handler dispatches: entity unit (`target_kind=entity` + fan-out version) vs
  legacy document_version + pre-fanout version.  
- Indexes: membership by version; processing partial index on entity target_kind
  + fan-out component version + status; staging load-by-entity already PK-friendly.

## 6. Failure and recovery

| Failure | Behavior | Recovery |
| --- | --- | --- |
| Unit DLQ | Barrier blocks version | `ops replay` processing_id; membership maps unit → entity/version |
| Crash mid-unit | Staging remains for unapplied assertions | Retry unit; idempotent apply + per-row staging delete |
| Wrong target without membership | Non-retryable | Fix data / do not empty-succeed |
| Legacy + fan-out both live | Forbidden by §5.7 | Ops exclusive cutover |

## 7. Observability

- Queue depth ≈ unfinished **units** for the fan-out generation.  
- Cost keys: `observation_flush:{unit_id|entity_id}:{index}:…`.  
- Ops UI: resolve `unit_id` → subject entity + version via membership.

## 8. Alternatives

| Option | Outcome |
| --- | --- |
| Bare `target_id=subject_entity_id` | **Rejected** (D12 collision; dual review B1) |
| Version-serial only | Status quo; reject for scale |
| In-process pool without ledger | Reject (D67) |
| Parallel apply within entity | Reject (D43) |
| Assertion-grain ledger jobs | Reject (row explosion; order still serial) |
| Global cross-version source-time scheduler | Out of scope |
| Commutative D43 recompute | Out of scope |

## 9. Test plan (minimum)

| Case | Expect |
| --- | --- |
| Claim barrier, 3 staging entities | 3 units + 3 processing rows |
| Two versions, same subject entity | 2 distinct unit_ids; both can succeed |
| V2 after V1 succeeded for same entity | V2 still gets its own unit and apply |
| Empty staging | empty signal + supersession + embed_claim |
| 2/3 units succeeded | no supersession |
| 3/3 succeeded | supersession + embed_claim once |
| Unit DLQ | no supersession |
| Within-entity multi-statement same claim | order by statement tie-break stable |
| Forget doc A entities | version B’s units for shared entity still runnable |
| No version-wide staging clear on entity path | peer entities’ staging intact |
| Legacy non-terminal blocks fan-out | no unit insert |
| Partial unit retry | no double evidence corruption |

## 10. Out of scope

- D43 ladder / `hub_top_k` / model seats.  
- Parallel ladder pair judgments.  
- Schedule-independent multi-version open-window recompute.  
- Relation supersession fan-out.

## 11. Success criteria

- Entity units drain concurrently under N workers; wall clock ≉ sum(all entities).  
- Largest hub still bounds critical path.  
- No silent cross-version observation loss (dual-review B1 closed).  
- Ops can replay one unit without replaying the version.
