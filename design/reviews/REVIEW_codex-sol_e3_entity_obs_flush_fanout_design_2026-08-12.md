# Design review: D90 entity-grain observation flush fan-out

**Verdict:** REQUEST_CHANGES  
**Reviewer:** Codex (codex-sol)  
**Date:** 2026-08-12

## Summary

- Entity grain is the right unit for parallelizing D43 across disjoint subject-entity blocks.
- The proposed ledger address is not version-qualified, so two versions that mention the same entity collapse onto one `processing_state` row.
- The processing rows also do not durably identify one version's expected set, making missing-child detection and version barriers unimplementable as written.
- Sorting only inside each version does not preserve D43 source order across concurrent versions, and the undated/tied assertion order is not total.
- Both allowed “LLM outside transaction” shapes can apply a verdict computed from a stale entity block after the transaction advisory lock has been released.
- The empty-set readiness signal, connector-cycle membership, forget behavior, and legacy-generation precedence are not bound.
- The cutover permits old stage-only workers to claim new entity rows; a capability-gated or stop/drain transition is required before fan-out can be enabled.

The scaling direction should be retained, but these gaps can cause wrong observation
windows, a stuck or falsely open barrier, unrelated-version work loss, and unsafe
mixed-image execution. They must be resolved in the binding design before
implementation.

## Blocking findings

### B1. `entity_id` is not a version-qualified ledger identity, and the expected set has no durable version membership

D90 binds each child to `target_kind=entity`, `target_id=subject_entity_id`, and
one constant `OBS_FLUSH_VERSION` (`plan/designs/e3_entity_obs_flush_fanout_design.md:18-29`).
The existing D12 key is unique on
`(deployment_id, target_kind, target_id, stage, component_version)`
(`src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py:62-95`),
and enqueue uses that exact conflict target
(`src/rememberstack/spine/work_ledger.py:957-982`). Therefore V2 cannot enqueue
its own flush for entity E after V1 has inserted `(deployment, entity, E,
adjudicate_observations, :entity-fanout-1)`. It receives V1's row and V1's
payload/status instead. This directly contradicts the claim that new documents
and versions have independent trees
(`plan/designs/e3_entity_obs_flush_fanout_design.md:69-90`).

The barrier definition has a second circularity. It declares the inserted
`processing_state` rows to be the expected set, while also requiring a missing
processing row to block (`plan/designs/e3_entity_obs_flush_fanout_design.md:162-184`).
Without a durable parent/child association or manifest, an absent row is not
observable as missing; it simply is not a member of the query. Nor can an entity
row be assigned to a version from authoritative ledger columns. The only version
coordinate is JSON payload, which the D12 schema explicitly treats as open-ended
handler input rather than work identity
(`src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py:79-94`).
This also makes “empty retry is success” unsafe: a wrong/stale version or
normalizer payload is indistinguishable from a legitimately cleared entity and
can let the row succeed while the intended staging slice remains
(`plan/designs/e3_entity_obs_flush_fanout_design.md:138-160,263-272`).

**Required change:** Bind a version-qualified, deployment-qualified child
identity that remains one independently leaseable unit per `(version,
normalizer generation, subject entity)`, plus a durable authoritative association
from the version fan-out to every expected child. It must fit D12 without using a
mutable payload or a per-document software `component_version` as the missing key
dimension. Define the exact idempotency constraint, fan-out insert, handler
coordinate validation, barrier anti-join, replay lookup, and missing-row test
against that association. The claim-barrier transaction must atomically mark the
fan-out materialized and insert the complete association/child set.

### B2. Per-version sorting is insufficient for continuous same-entity ingest, and the asserted order is not total

The design fixes assertion order only inside one version's entity job and permits
version barriers to proceed independently
(`plan/designs/e3_entity_obs_flush_fanout_design.md:69-90,138-157,186-194`).
After B1 is fixed, two or more jobs for the same entity can reach D43 in arbitrary
version order. The entity lock serializes their writes, but does not order them by
source time.

The current D43 reverse-arrival repair is not generally commutative. It ignores
closed candidates when choosing the next interaction
(`src/rememberstack/spine/observation_adjudication.py:250-255`) and only handles an
incoming assertion that is earlier than the currently open slice
(`src/rememberstack/spine/observation_adjudication.py:443-507`). For three changing
states at `t1 < t2 < t3`, flush order `t3, t1, t2` can create both `[t1,t3)` and
`[t2,t3)` instead of capping the first predecessor at `t2`. Thus the D90 claim
that continuous version trees are independent can produce overlapping/wrong D43
windows even though every individual write held the entity lock.

Undated assertions are more visibly schedule-dependent: a supersede with no
boundary falls back to database `now()`
(`src/rememberstack/spine/observation_adjudication.py:910-917`), while D90 leaves
the null policy as “nulls-last or documented sentinel”
(`plan/designs/e3_entity_obs_flush_fanout_design.md:140-147`). Those choices are
not equivalent. The stated key is also not a total order: staging permits one
claim to emit multiple statements for the same entity
(`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:22-34`),
so `(asserted_at, claim_id)` ties can still be applied in arbitrary order. The
current query adds `statement` as a final tie-breaker
(`src/rememberstack/spine/fact_catalog.py:650-659`), but D90 explicitly binds
“only” the shorter key.

**Required change:** Bind the semantic order for all assertions that may contend
on one entity, including concurrently ready versions, three-or-more out-of-order
state changes, null `asserted_at`, and multiple assertions from one claim. Either
provide a global/durable entity sequencing protocol or amend D43 with a
schedule-independent insertion/recompute algorithm that updates the correct
predecessor and successor windows. Choose one exact total order (including null
placement and a final assertion tie-breaker); do not leave sentinel semantics to
implementation. Acceptance must reverse three version completions and cover two
undated conflicting states and same-claim multi-assertion input.

### B3. The LLM/transaction alternatives allow stale D43 decisions

The reliability goal is valid, but both proposed alternatives permit remote model
work after a locked read and before a later write transaction
(`plan/designs/e3_entity_obs_flush_fanout_design.md:196-218`). The existing entity
lock is `pg_advisory_xact_lock` and is released with its transaction
(`src/rememberstack/spine/observation_adjudication.py:121-182,884-895`). Once the
read transaction ends, another version's job can alter the open set while the
first worker is calling the model. Reacquiring the lock only for the write does
not make the prepared verdict valid for the new block. “Pure function of locked
snapshot” does not solve that time-of-check/time-of-use race.

This can apply evidence, contradiction grouping, or a supersede cap to a prior
that is no longer the correct open/ranked candidate. It also means the design's
“under the entity advisory lock” language is weaker than the current D43 batch
contract, which reads, decides, and writes under one transaction lock.

**Required change:** Select and bind a correctness-preserving critical-section
protocol. Valid shapes include a per-assertion transaction that retains the
transaction entity lock across read/model/write (it still avoids a
multi-assertion transaction), a carefully managed session-level entity lock, or
an optimistic snapshot/version token that is revalidated under the write lock
and forces recomputation on any block change. In every shape, applying one
assertion and deleting exactly its staging row must be atomic. Specify lock
lifetime, revalidation, failure cleanup, and how ordered assertions from
competing version jobs may interleave; this cannot remain an implementation-PR
choice.

### B4. Empty fan-out, readiness, lifecycle, and forget do not have an implementable version-scoped contract

D90 forbids a fan-out coordinator and directly enqueues downstream work for an
empty staging set (`plan/designs/e3_entity_obs_flush_fanout_design.md:101-119`).
That leaves no `adjudicate_observations` row or durable empty-set marker from
which pipeline readiness can report the fan-out generation succeeded or derive
an honest `finished_at`. Current readiness begins with version-target rows and
only adds explicit derived aggregates for D84 chunks and D88 claims
(`src/rememberstack/spine/readiness.py:81-157,228-237`). The one-line rule “use
entity-grain rows” does not define the zero-row case, legacy precedence, missing
membership, or status/timestamp aggregation
(`plan/designs/e3_entity_obs_flush_fanout_design.md:236-242`).

Connector-cycle finalization likewise has joins for version work, chunk extract,
and claim normalize, but no version-to-entity-flush association
(`src/rememberstack/spine/lifecycle.py:1024-1072`). It cannot wait on a specific
version's entity dead letter without the durable membership required by B1.

The forget rule is unsafe as stated. Existing scrub clears payload on every
processing row whose `target_id` is any affected/resolved entity
(`src/rememberstack/spine/forget.py:1440-1467`). With D90, a document that mentions
shared entity E can therefore erase the required payload of an unrelated
pending flush for another document/version that also targets E. After admission
reopens, that unrelated job can dead-letter or lose its coordinates. “Follow
existing processing_state scrub policy” is not sufficient for a shared entity
target.

**Required change:** Use the durable version/child association from B1 to bind
derived readiness and lifecycle status for missing, pending, running, failed,
dead-letter, succeeded, replayed, and zero-child fan-outs, including the source
of `finished_at` and exact old/new generation precedence. Bind forget to
cancel/scrub only children belonging to the forgotten version/document while
preserving pending work for other documents that share the entity; include the
association/manifest in scrub and verification. Add zero-observation readiness,
entity-DLQ lifecycle, missing-child, and shared-entity forget tests.

### B5. The legacy cutover is unsafe for a stage-only worker fleet

“Deploy code that understands both” followed by “new claim barriers enqueue
entity fan-out” is not an enforceable transition protocol
(`plan/designs/e3_entity_obs_flush_fanout_design.md:221-250`). Workers currently
claim by deployment, stage, and lane only; they do not filter target kind or
component version (`src/rememberstack/spine/work_ledger.py:999-1012`). The current
observation handler assumes a version-level row and does not branch on target
kind/component generation
(`src/rememberstack/workers/e3.py:715-831`). During a rolling deployment, any old
`worker-adjudicate-observations` replica can therefore claim a new entity row,
load/clear the whole version slice, and enqueue downstream with the entity id as
the work target.

The operator alternative “dead-letter legacy row + re-enqueue entity set” also
does not bind exclusive ownership, readiness treatment, or rollback after new
rows exist. Running the legacy whole-version handler and the entity children for
one version at the same time is not a safe cutover contract.

**Required change:** Specify a capability-gated two-phase rollout or an explicit
stop/drain cutover: all observation consumers must understand both generations
before any producer can expose entity rows, and old consumers must remain unable
to claim them. Bind how an in-flight/partially progressed legacy row is either
allowed to finish or atomically converted, never both; define old-generation
readiness and rollback after entity children exist. Add a mixed-image negative
test or deployment guard and a partial-legacy-progress conversion test.

## Non-blocking nits

- Bind one concrete barrier-lock namespace and acquisition order. The safest
  reading is to reuse the existing representation barrier lock; if distinct
  locks are retained, state exactly which transaction takes both and in what
  sorted order. Add a real two-connection last-two-entities test for missed fire
  and exactly-one downstream enqueue
  (`plan/designs/e3_entity_obs_flush_fanout_design.md:162-180,329-335`).
- The payload schema makes `extractor_version` present, while the “ops-only”
  table leaves whether it is required open
  (`plan/designs/e3_entity_obs_flush_fanout_design.md:120-136,329-335`). Make the
  schema and downstream selector contract agree.
- The analysis and implementation sketch call the current class
  `ObservationFlushHandler`; the repository class is
  `AdjudicateObservationsHandler`
  (`plan/analysis/e3_entity_obs_flush_fanout_analysis.md:20-27`,
  `plan/designs/e3_entity_obs_flush_fanout_design.md:293-300`,
  `src/rememberstack/workers/e3.py:715-716`).
- “Adjudicate supersession, then existing embed chain” is ambiguous because the
  current observation flush emits `adjudicate_supersession` and `embed_claim` as
  sibling follow-ups. State whether D90 preserves that topology or intentionally
  serializes it (`plan/designs/e3_entity_obs_flush_fanout_design.md:31-33,177-180`,
  `src/rememberstack/workers/e3.py:799-830`).

## Explicit checklist

| # | Review item | Assessment | Review |
| --- | --- | --- | --- |
| 1 | Expected entity set pin vs live staging DISTINCT after partial flush | **FAIL** | The design correctly rejects live staging after progress, but `processing_state` alone neither version-qualifies membership nor exposes a missing row. B1 requires a durable manifest/association. |
| 2 | Empty staging path | **FAIL** | Downstream enqueue is named, but no durable empty completion signal, readiness timestamp, or legacy precedence is defined. See B4. |
| 3 | Dead letter / missing entity rows block supersession | **FAIL** | `dead_letter` blocking is stated and replay would create a new completion edge, but missing-child detection is impossible when the child rows themselves define membership. See B1/B4. |
| 4 | Within-entity order + `asserted_at` undated claims | **FAIL** | The order is only per version, is not total for multiple assertions from one claim, and leaves null placement/sentinel semantics open; undated supersede falls back to wall-clock time. See B2. |
| 5 | Cross-entity independence | **PASS** | D43's authoritative block and writes are keyed by `subject_entity_id`; no global apply order is needed across distinct entity blocks. This does not imply independence between two version jobs for the same entity. |
| 6 | Continuous multi-version ingest and entity advisory locks | **FAIL** | Same-entity rows collide in D12, and after fixing that, arbitrary cross-version order plus the current predecessor-only repair can produce overlapping windows. LLM-outside-TX also admits stale writes. See B1-B3. |
| 7 | `complete_entity_obs_flush` lock ordering vs `complete_claim_normalize` | **CONCERN** | A serialization lock and fixed order are required, which is directionally correct, but the actual shared/distinct namespace remains open. The normal path makes children visible only after claim-barrier commit, so no concrete deadlock is forced yet; bind and race-test the exact topology. |
| 8 | Idempotent rerun after partial entity progress | **FAIL** | Per-assertion apply + staging delete in one transaction is the right recovery primitive, but the allowed stale-prepare path and unvalidated empty slice can still commit a wrong/no-op completion. See B1/B3. |
| 9 | Legacy version-serial cutover | **FAIL** | Component suffix and dual dispatch are correct goals, but mixed old/new workers are not prevented and conversion/rollback is not exclusive. See B5. |
| 10 | “No LLM in multi-assert transaction” implementability | **FAIL** | The negative rule is clear; the positive read/lock/model/revalidate/write protocol is not, and two explicitly allowed shapes are unsafe under concurrent entity writes. See B3. |
| 11 | Readiness / lifecycle / forget implications | **FAIL** | Entity children are not version-joinable, zero-child success is invisible, lifecycle cannot find version-specific entity DLQs, and existing entity-target scrub can damage unrelated shared-entity jobs. See B4. |
| 12 | Overclaiming vs under-specifying | **FAIL** | The design soundly claims cross-entity scale-out, but overclaims version independence, queue truth, and preserved D43 outcomes while leaving ledger identity, global same-entity order, snapshot validity, empty readiness, and rollout protocol unresolved. |
