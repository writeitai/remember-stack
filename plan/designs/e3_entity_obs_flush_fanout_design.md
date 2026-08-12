# Design: entity-grain observation flush fan-out

**Status:** design PR — binding once dual design review findings are absorbed
and this lands on `main`  
**Date:** 2026-08-12  
**Decision log:** D90  
**Analysis:** [e3_entity_obs_flush_fanout_analysis.md](../analysis/e3_entity_obs_flush_fanout_analysis.md)  
**Amends:** D88 §5.6 post-barrier flush grain (version-serial → entity-parallel);
handoff after claim barrier in [e3_claim_level_normalize_fanout_design.md](e3_claim_level_normalize_fanout_design.md)  
**Preserves:** D43 / [observations_design.md](observations_design.md) entity
block + apply-in-order; D86 inside claim jobs; D88 claim normalize grain  
**Pattern:** same family as D84 `complete_chunk_extract` and D88
`complete_claim_normalize` (advisory lock + complete + anti-join barrier +
enqueue in one transaction)

## 1. Decision

1. **Primary work unit** for stage `adjudicate_observations` under the entity
   fan-out generation is one **subject entity**:
   - `target_kind = entity` (`ProcessingTarget.ENTITY` already exists)
   - `target_id = subject_entity_id`
   - `component_version = OBS_FLUSH_VERSION` with a **fan-out generation
     suffix** (append `:entity-fanout-1` to the D88 obs-flush string) so legacy
     version-serial flush rows are not confused with entity jobs at readiness.
2. **Fan-out** when the **claim normalize barrier** would enqueue a single
   version-level obs flush: enqueue **one obs-flush job per distinct
   `subject_entity_id` in staging** for that version + normalizer generation,
   via a **single set-based insert of the complete expected entity set in the
   same transaction as the claim barrier handoff** (§5.2). Not open-ended
   “discover entities later.”
3. **Barrier:** enqueue version-level **downstream** work
   (`adjudicate_supersession`, then existing embed chain) only when every
   expected entity has `status=succeeded` at the **entity-fanout** obs-flush
   component version. Soft D43 outcomes (evidence / new / contradict / supersede
   under fail-safe rules) still yield job `succeeded`. `dead_letter` / `failed`
   / missing entity rows **block**.
4. **Within entity:** assertions apply **strictly serially** in
   `(asserted_at, claim_id)` order under the entity advisory lock. Never
   parallelize apply within one entity.
5. **Across entities:** concurrent entity jobs are allowed and intended.
6. **Legacy** version-level `adjudicate_observations` rows at the **pre-entity-fanout**
   component version remain serial whole-version flush handlers until drained.
   New images must not treat a **coordinator** version-level success (if any)
   as “observations flushed.”
7. **Empty staging:** skip entity grain; open supersession (and embed) from the
   claim barrier path exactly as today’s empty-observation hop.
8. **Reliability:** entity handlers must **not** hold one multi-assertion DB
   transaction open across remote LLM/embed calls (§5.7).

## 2. Problem (why this exists)

D88 made claim normalize parallel. Observation flush stayed one version lease
walking every entity serially. On BEAM-scale staging (~2k+ entities, hub
residue at ~3 assertions/min), wall clock becomes multi-day and extra adj
workers idle. Analysis §1–2.

## 3. Rationale

- D43 is **entity-keyed**. Open observation sets do not cross subject entities.
- D88 already chose **stage then flush** so claim completion order is not document
  order; the flush’s remaining serial bottleneck is **across entities**, not
  “need one global queue.”
- `ProcessingTarget.ENTITY` and stage `adjudicate_observations` already exist.
- D84/D88 landed the only acceptable durability pattern for fan-out: **atomic
  expected-set insert + barrier anti-join under advisory lock**.
- In-process pools without ledger grain fail D67 ops truth (queue depth, DLQ,
  replay).

## 4. Continuous ingestion

Expected entity set is fixed when the **claim normalize barrier** fires for a
closed document version:

```text
expected_entity_ids(deployment_id, version_id, normalizer_version) =
  DISTINCT subject_entity_id
  FROM normalize_observation_staging
  WHERE deployment_id / version_id / normalizer_version match
```

| Scenario | Behavior |
| --- | --- |
| New documents while V flushes | New trees; do not enlarge V’s set |
| New version of same lineage | New staging + new expected set |
| Entity `dead_letter` on V | V’s supersession/embed blocked; other entities proceed |
| Empty staging | No entity jobs; supersession enqueued |

Appending observations to a **closed** version’s staging after the claim barrier
without a new normalize generation is out of model for this barrier (same as
D88 closed claim set).

## 5. Contracts

### 5.1 Expected entity set

Membership is deployment-scoped via staging rows that claim normalize already
wrote under that deployment. Fan-out **must** pin the set in the claim-barrier
transaction (set insert). Re-query of staging for “who still has rows” is for
handlers, not for growing the expected set.

### 5.2 Fan-out durability (binding protocol)

**Chosen protocol (v1):** in the same transaction that marks the last claim
normalize succeeded and finds the claim barrier ready:

1. `SELECT DISTINCT subject_entity_id …` from staging for the version +
   normalizer generation (or empty).
2. If empty → enqueue `adjudicate_supersession` (and existing embed follow-up
   policy) as today.
3. If non-empty → set-based insert of **all** entity `adjudicate_observations`
   jobs at `OBS_FLUSH_VERSION` with `target_kind=entity`.

Do **not** enqueue a separate version-level “coordinator” job for the happy
path. (Optional: a legacy version-level row at the **old** component version may
still exist for cutover drains — §5.8.)

`content_hash` and `lane` copy from the barrier parent work (claim complete /
version content hash already used for obs flush today).

### 5.3 Entity job payload

```json
{
  "version_id": "<uuid>",
  "representation_id": "<uuid>",
  "subject_entity_id": "<uuid>",
  "doc_id": "<uuid|null>",
  "normalizer_version": "<E3_NORMALIZER_VERSION string>",
  "chunker_version": "<string>",
  "extractor_version": "<string>"
}
```

`normalizer_version` is the **claim-normalize generation** that wrote staging
(not the obs-flush component version). Required so the handler loads the correct
staging slice.

### 5.4 Handler behavior (entity grain)

For `target_kind=entity` and fan-out `OBS_FLUSH_VERSION`:

1. Validate payload coordinates.
2. Load staging rows for
   `(deployment_id, version_id, subject_entity_id, normalizer_version)` ordered
   by `(asserted_at, claim_id)` (join claims for `asserted_at`; undated claims
   use the same tie-break as D88 undated supersession — **stable `claim_id`
   order after nulls-last or documented sentinel**).
3. If no rows remain (idempotent retry after success): treat as success path for
   barrier participation (complete with no-op apply).
4. Apply D43 for that entity only:
   - Under entity advisory lock `deployment_id:obs:{entity_id}` for **write**
     phases.
   - **Serial** assertion order from step 2.
   - Existing outcomes: evidence / new / contradict / supersede fail-safe.
5. Delete staging rows for that entity + version + normalizer generation only
   after successful apply for those rows (§5.7 commit shape).
6. Return success to the worker; **do not** enqueue supersession here.

Systemic failures re-raise for ledger retry/DLQ. Soft D43 decisions are not
job failures.

### 5.5 Completion + barrier (`complete_entity_obs_flush`)

Mirror `complete_claim_normalize` / `complete_chunk_extract`:

**API:** `WorkLedger.complete_entity_obs_flush(...)` in **one** transaction:

1. Take version/representation-scoped advisory lock (same family as claim
   barrier; document the key namespace so claim-complete and entity-complete do
   not deadlock — prefer **one shared representation barrier lock** ordered
   consistently, or distinct locks with a fixed acquire order documented in
   impl).
2. Mark this entity processing row `succeeded`.
3. Anti-join / count: every expected entity job at fan-out `OBS_FLUSH_VERSION`
   is terminal `succeeded` (missing / failed / dead_letter / pending / running
   → **not** ready).
4. If ready → enqueue `adjudicate_supersession` (version target) with the same
   payload fields today’s version flush uses for origin-claim supersession
   selector (`version_id`, `representation_id`, `chunker_version`,
   `normalizer_version`, `doc_id`, …), then existing embed follow-up policy.

Expected entity membership for the anti-join is the set of **processing_state
rows** inserted at fan-out (ledger is source of truth after insert), not a
live re-DISTINCT of staging (staging rows disappear as entities finish).

### 5.6 Ordering policy (binding)

| Scope | Rule |
| --- | --- |
| Within entity | Apply in `(asserted_at, claim_id)` only |
| Across entities | Any completion order |
| Claim normalize vs flush | Claim barrier before any entity flush fan-out |
| Supersession | Only after entity barrier |
| Global staging sort | Optional implementer convenience when building per-entity lists; **not** a cross-entity schedule |

### 5.7 Commit shape and LLM boundaries (binding)

**Problem today:** one `engine.begin()` around an entire entity batch including
remote generate/embed.

**v1 binding:**

1. **No remote model I/O inside a transaction that spans more than one
   assertion’s durable write.**  
   Allowed patterns (pick one in impl; document choice in PR):
   - **Per-assertion transactions:** lock entity → read block → (optional
     LLM outside TX) → write outcome + delete that assertion’s staging row →
     commit; repeat.  
   - **Prepare then apply:** under short locks, read; LLM outside; short TX
     apply all prepared outcomes in order + clear staging (only if prepare is
     pure function of locked snapshot — harder; default to per-assertion).
2. **Idempotency:** re-running an entity job after partial progress must not
   double-count evidence or corrupt open windows. Prefer deleting a staging row
   only in the same TX as that assertion’s successful apply; evidence PK /
   existing D43 idempotency remains the safety net.
3. **Entity lock:** still required around any write that mutates open
   observations for that entity (continuous ingest / concurrent versions).

This is a reliability contract, not a performance optional.

### 5.8 Component version and legacy

| Generation | Behavior |
| --- | --- |
| Pre-`:entity-fanout-1` obs flush | Version-level serial handler (today) |
| `:entity-fanout-1` | Entity jobs + barrier; claim barrier enqueues entity set |

Cutover:

- Deploy code that understands both.
- New claim barriers enqueue entity fan-out only.
- In-flight version-serial flush jobs finish or are operator dead-lettered +
  re-enqueued as entity set (ops runbook; same class as D88 serial normalize
  cutover on BEAM).

### 5.9 Readiness / lifecycle / forget

| Surface | Rule |
| --- | --- |
| Pipeline readiness for obs flush generation | Entity-grain rows at fan-out version; version-level success alone insufficient |
| Connector-cycle / lifecycle waits | Wait on entity jobs (include `dead_letter`), same spirit as D88 claim wait |
| Forget / scrub | Staging already scrubbed with version; entity processing rows follow existing processing_state scrub policy — extend if forget lists stages by grain |

### 5.10 Worker topology

- Stage remains `adjudicate_observations` (no new Postgres enum value required
  for stage).
- Compose already runs `worker-adjudicate-observations`; scale replicas for
  queue depth.
- Handler dispatches on `target_kind` + component version (entity vs legacy
  document_version).

### 5.11 Indexes / migrations

- Partial index on `processing_state` for
  `(deployment_id, stage, target_kind, component_version, status)` where
  `stage = adjudicate_observations AND target_kind = entity` (mirror claim
  normalize index).
- Staging already keyed by entity; ensure load-by-entity query is indexed
  (`deployment_id, version_id, normalizer_version, subject_entity_id`) if not
  already covered by PK/prefix.

## 6. Failure and recovery

| Failure | Behavior | Recovery |
| --- | --- | --- |
| Entity job DLQ | Barrier blocks supersession | `ops replay` that processing_id |
| Partial entity progress then crash | Staging rows remain for unapplied assertions | Retry entity job; idempotent apply |
| Worker kill mid-running | Ledger retry/backoff; avoid zombie running (existing reclaim rules) | Same as other stages |
| Empty entity job (staging already clear) | Succeed no-op | Barrier counts success |
| Claim barrier race | Fan-out only inside claim complete TX when barrier ready | D88 complete path |
| Wrong `normalizer_version` in payload | Load empty or wrong slice | Non-retryable or fail loud in handler validation |

## 7. Observability

- Cost keys remain entity/assertion scoped (extend
  `observation_flush:{entity_id}:{index}:…` as today).
- Metrics (impl): entity jobs pending/running/succeeded/dlq; assertions applied
  per entity; hub size histogram; barrier wait age.
- Ops: queue depth ≈ unfinished **entities** for this stage generation — correct
  scale signal.

## 8. Alternatives (summary)

| Option | Outcome |
| --- | --- |
| Version-serial only | Status quo; reject for BEAM-scale |
| In-process parallel entities | Reject as sole v1 (D67) |
| Parallel within entity | Reject (D43) |
| Assertion-grain jobs | Reject v1 (row explosion; order still serial per entity) |
| Commutative D43 | Out of scope |

## 9. Implementation sketch (non-binding detail)

1. Bump `OBS_FLUSH_VERSION` → `…:entity-fanout-1`.  
2. `_enqueue_entity_obs_flush_fanout` from claim barrier path (replace single
   version enqueue when staging non-empty).  
3. `ObservationFlushHandler` split: entity path + legacy version path.  
4. `WorkLedger.complete_entity_obs_flush`.  
5. Migration for index.  
6. Tests: fan-out set size; barrier blocks on missing/dlq; two entities
   concurrent apply order independence; within-entity order; empty staging;
   cutover legacy version handler; no LLM inside multi-assert TX (unit/architecture
   test or inspect harness).  
7. BEAM cutover runbook: stop serial version flush if running; enqueue entity
   set from staging DISTINCT; scale adj workers.

## 10. Test plan (minimum)

| Case | Expect |
| --- | --- |
| Claim barrier, 3 entities in staging | 3 entity jobs, 0 version fan-out job |
| Claim barrier, 0 staging | supersession enqueued, 0 entity jobs |
| 2/3 entities succeeded | no supersession |
| 3/3 succeeded | supersession once (idempotent) |
| Entity DLQ | no supersession |
| Within entity reverse staging insert order | apply still `(asserted_at, claim_id)` |
| Two entities parallel | both succeed; no cross lock on different entity keys |
| Replay entity job after success | no-op success / no duplicate corrupt caps |
| Legacy component version version-job | serial path still works for cutover |

## 11. Out of scope

- Changing D43 ladder logic, `hub_top_k`, or model seats.  
- Parallelizing ladder pair judgments.  
- Relation supersession fan-out.  
- UMC control-plane autoscaling policies (consumes queue depth only).

## 12. Open implementation choices (ops only)

| Choice | Guidance |
| --- | --- |
| Per-assertion TX vs prepare/apply | Prefer per-assertion unless measured otherwise |
| Shared vs separate advisory locks for claim vs entity barriers | Fixed acquire order; document in impl PR |
| Whether extractor_version is required on entity payload | Include if supersession/embed payload needs it; else omit |

## 13. Success criteria

- BEAM-shaped staging: entity queue drains with **N** adj workers at roughly
  min(N, entity_count) concurrency; wall clock no longer ≈ sum(all entities).  
- Largest hub still bounds critical path (expected).  
- No increase in D43 correctness incidents under dual-review tests.  
- Ops can DLQ/replay a single entity without replaying the version.
