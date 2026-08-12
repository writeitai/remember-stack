# Design: entity-grain observation flush fan-out

**Status:** revised through dual design r3 — Claude APPROVE_WITH_NITS (r3+r4); Codex
r3 ordering gap closed in this revision — binding once landed on `main`  
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
     `component_version = "e3-obs-flush-2026.08a:claim-fanout-1:entity-fanout-1"`  
     (literal; extends D88 `e3-obs-flush-2026.08a:claim-fanout-1`).
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
7. **Across different versions of the same entity:** units may exist
   concurrently in the ledger, but **at most one apply stream for a given
   `subject_entity_id` may run at a time**. When a worker holds that exclusive
   apply, it applies **all unapplied staging for that entity** across every
   non-dead-letter unit (all versions) in the **global** total order
   `(asserted_at NULLS LAST, claim_id, statement)` — not one version slice at a
   time by `min_asserted_at` (§5.5). Unit rows remain version-scoped for barrier
   membership; apply order is entity-global. After the stream, every unit whose
   staging slice is empty is completed (idempotent no-op complete if another
   stream already drained it).
8. **Empty staging:** no units; durable empty completion **only** via
   `obs_flush_version_state` (or equivalent), **never** via a
   `document_version` processing row at the fan-out component version (§5.1,
   §5.7). Enqueue supersession **and** `embed_claim` as sibling follow-ups.
9. **Legacy** version-serial flush at pre-`:entity-fanout-1` component version
   remains until drained. Fan-out and legacy for the **same** version must not
   both run (§5.7). Zero-chunk / empty-extract call sites must not insert
   `document_version` rows at the fan-out component version (§5.8).
10. **LLM / TX:** bind per-assertion durable writes **without** releasing the
    entity lock mid-unit in a way that allows another writer to interleave
    *inside* this unit’s ordered sequence (§5.6).

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
| `representation_id` | required for barrier lock + supersession/embed reconstruction |
| `normalizer_version` | claim-normalize generation that wrote staging |
| `chunker_version` | required for supersession origin-claim reconstruction |
| `extractor_version` | required when embed/supersession payload needs it; store always |
| `subject_entity_id` | D43 block key |
| `doc_id` | for forget / operator display |
| `content_hash` | copy from barrier parent |
| `min_asserted_at` | min claim `asserted_at` in this unit’s staging at fan-out (null if all undated); claim-order key |
| `created_at` | audit |

**Unique:** `(deployment_id, version_id, normalizer_version, subject_entity_id)`.

**Durable empty / fan-out state (binding):** table `obs_flush_version_state`
keyed `(deployment_id, version_id, normalizer_version)` with columns at least
`representation_id`, `chunker_version`, `extractor_version`, `content_hash`,
`fanout_status` (`materialized` | `empty_complete` | `barrier_complete`),
`completed_at`. **Never** use a `processing_state` row with
`target_kind=document_version` at the fan-out component version as this signal
(that shape is reserved for legacy pre-fanout handlers only).

### 5.2 Fan-out (claim barrier transaction)

When claim barrier is ready for version V + normalizer generation N:

1. If a **non-terminal legacy** version-level `adjudicate_observations` row exists
   for V at a **pre-entity-fanout** component version → **do not fan out**; leave
   legacy to finish (or ops dead-letters legacy first). Mutual exclusion §5.8.
2. If membership already materialized for (V, N) → do not re-insert units;
   only ensure barrier evaluation can still fire (idempotent complete path).
3. Else `SELECT DISTINCT subject_entity_id, doc_id …` plus per-entity
   `min(asserted_at)` from staging joined to claims for (V, N). Coordinates
   `representation_id`, `chunker_version`, `extractor_version` come from the
   claim-barrier parent payload / representation catalog (must be known at
   barrier time — same as today’s version flush payload).
4. **Empty:** upsert `obs_flush_version_state` to `empty_complete`; enqueue
   `adjudicate_supersession` + `embed_claim` (sibling follow-ups, same stages and
   component versions as `AdjudicateObservationsHandler` today).
5. **Non-empty:** insert one membership row + one processing_state row per
   entity (`target_kind=entity`, `target_id=unit_id`, fan-out component version);
   set `obs_flush_version_state.fanout_status=materialized`.

`content_hash` / `lane` from barrier parent work.

Payload on the processing row is **cache only**. Handler and barrier **must**
load coordinates from membership / `obs_flush_version_state` by `unit_id` /
version. Missing membership → non-retryable.

### 5.3 Handler (entity unit)

1. `unit_id = work.target_id`; load membership; validate deployment matches;
   note `subject_entity_id = E`.
2. **Single-flight entity-global drain** under entity lock for E (§5.5–§5.6):
   load **all unapplied staging** for E whose `(version_id, normalizer_version)`
   has a non-dead-letter membership unit (not incomplete pre-barrier staging).
   Order by **`(asserted_at NULLS LAST, claim_id, statement)`**.
3. If no unapplied rows remain for this unit’s slice: succeed no-op for this
   `unit_id` only (siblings drained earlier self-complete the same way when
   claimed; do not force-complete foreign `pending` rows).
4. Apply D43 for each staging row in that global order; after each cap of an
   open observation, apply §5.5.3 late-arrival re-split if needed.
5. Delete each staging row in the same durable write as its successful apply.
6. Return success for the **claimed** `unit_id` via `complete_entity_obs_flush`.

Do **not** call version-wide `clear_staged_observations`.

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
4. If ready → enqueue **once** (idempotent), loading coordinates from
   `obs_flush_version_state` + membership (`representation_id`,
   `chunker_version`, `normalizer_version`, `extractor_version`, `doc_id`):
   - `adjudicate_supersession` with `target_kind=document_version`,
     `target_id=version_id` (never `unit_id`), existing adjudicator component
     version (payload may omit `relation_ids` only when all reconstruction
     fields are present), and
   - `embed_claim` with `target_kind=document_version`, `target_id=version_id`,
     existing P1 embed component version  
   as **sibling** follow-ups (preserve today’s topology; readiness/lifecycle
   only see version-level work for these stages). Set
   `obs_flush_version_state.fanout_status=barrier_complete`.

Barrier advisory lock: reuse the exact `complete_claim_normalize`
representation barrier key family with `representation_id` from
`obs_flush_version_state` (not re-derived ad hoc).

### 5.5 Ordering

| Scope | Rule |
| --- | --- |
| Assertions for one `subject_entity_id` | Global total order `(asserted_at NULLS LAST, claim_id, statement)` across **all** unapplied staging for that entity among non-dead-letter units |
| Across different entities | Any completion order (true parallel) |
| Unit claim / single-flight | At most one apply stream per `subject_entity_id`; claiming any unit for E starts the entity-global drain |
| Undated `asserted_at` | Sort last (`NULLS LAST`); supersede boundary uses existing D43 undated rules (`now()` where already coded) — not redefined here |

#### 5.5.1 Why not per-unit `min_asserted_at` ordering alone

Ordering **units** by minimum assertion time is insufficient. Example (Codex r3):

- Unit A: `t1: value=A`, `t3: value=A` (same statement → evidence collapse on open A)
- Unit B: `t2: value=B`, with `t1 < t2 < t3`
- If A runs as a whole before B: result `A[t1,t2), B[t2,∞)`  
- Source order requires: `A[t1,t2), B[t2,t3), A[t3,∞)`

Window re-cap cannot recreate the missing A-at-`t3` slice after evidence collapse.
Therefore the binding rule is **entity-global merge-apply** of unapplied staging
(§5.5 table), not unit-at-a-time apply. Acceptance tests must include this case
and undated/tied assertions split across units.

#### 5.5.2 Optional safety net (not a substitute for §5.5)

Entity-local validity recompute after writes remains allowed as defense in depth,
but **does not** replace global ordered apply of unapplied staging.

#### 5.5.3 Late-arriving unit after a peer already completed (binding)

Entity-global merge only sees **unapplied** staging. If unit A fully applied
`{t1:A, t3:A}` first (t3 collapsed as evidence on open A), then unit B later
materializes `{t2:B}`, a plain supersede of open A at t2 yields
`A[t1,t2), B[t2,∞)` and loses A-at-t3.

**Required:** when an apply caps open observation O at boundary T, any **evidence
claims** (or reassertions) already attached to O with `asserted_at > T` (total
order) must be re-materialized as subsequent open slices after the cap — not left
only as evidence on a capped row. Concretely the staggered acceptance case:

1. Fully succeed unit A `{t1:A, t3:A}` alone.  
2. Later materialize/apply unit B `{t2:B}`.  
3. Final slices must still be `A[t1,t2), B[t2,t3), A[t3,∞)`.

Impl may walk evidence claim `asserted_at` on O after cap, or rebuild open
history for E from durable adjudications + claim times. This is a D43 co-requisite
of multi-version continuous flush under D90.

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
| `:entity-fanout-1` | Unit fan-out only; **no** `document_version` processing rows at this component version |

**Mutual exclusion for a given version V:**

- If non-terminal **legacy** version-level flush exists for V → claim barrier
  must not materialize unit fan-out for V.  
- If unit membership or `obs_flush_version_state` is materialized for V → legacy
  handler must refuse to claim work for V (or ops dead-letters legacy before
  enabling fan-out).  
- Entity-path **must not** invoke version-wide staging clear.  
- Mixed-image rollout: enable fan-out only when all workers understand entity
  units (capability / stop-drain-restart), same class as D88 mixed-image rule.

**Call sites that today enqueue version-level `adjudicate_observations` at
`OBS_FLUSH_VERSION`** (claim barrier empty, all-claims-already-succeeded hop,
E1/E2 zero-chunk paths): after the component-version bump they must either:

- write `obs_flush_version_state.empty_complete` + enqueue supersession +
  `embed_claim` directly (preferred for true empty observation sets), or  
- enqueue a **legacy-component-version** version-serial row only if a serial
  path is still required for cutover — never a `document_version` row at the
  fan-out component version.

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
| Per-unit apply ordered only by min_asserted_at | Rejected (Codex r3 evidence-collapse case) |
| Window-only recompute without global ordered apply | Rejected (§5.5.1) |

## 9. Test plan (minimum)

| Case | Expect |
| --- | --- |
| Claim barrier, 3 staging entities | 3 units + 3 processing rows |
| Two versions, same subject entity | 2 distinct unit_ids; both can succeed |
| V2 after V1 succeeded for same entity | V2 still gets its own unit and apply |
| Same entity, two pending units | single apply stream; global assertion order |
| Unit A {t1:A,t3:A} + unit B {t2:B} co-present | final slices A[t1,t2), B[t2,t3), A[t3,∞) |
| Unit A completes alone then B {t2:B} arrives | same final slices via §5.5.3 re-split |
| Supersession payload fields | reconstruction fields always present from membership/state |
| Zero-chunk empty path | no document_version row at fan-out component version |
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

- D43 ladder / `hub_top_k` / model seats (except §5.5.3 late-arrival re-split).  
- Parallel ladder pair judgments.  
- Relation supersession fan-out.

## 11. Success criteria

- Entity units drain concurrently under N workers; wall clock ≉ sum(all entities).  
- Largest hub still bounds critical path.  
- No silent cross-version observation loss (dual-review B1 closed).  
- Ops can replay one unit without replaying the version.
