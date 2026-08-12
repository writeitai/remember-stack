# Analysis: entity-grain observation flush fan-out

**Status:** non-binding analysis  
**Date:** 2026-08-12  
**Related:** D88 (claim normalize fan-out), D84 (chunk extract), D43/D4
(observation adjudication), D12/D67 (ledger work truth)  
**Driver:** BEAM 1M on `umc-beam-bench-01` after D88 claim normalize completed
(~15k claims succeeded). Post-barrier `adjudicate_observations` is a **single
version-level job** applying D43 across ~2.4k entities / ~6.2k staged
assertions serially. Measured residue path ~3 assertions/min (≈4–5 sequential
LLM pair-verdicts × ~3–8 s each). Wall clock multi-day if uninterrupted;
scaling `worker-adjudicate-observations` does not help (one lease).

## 1. Problem

### 1.1 What is slow

After D88, claim normalize scales. Observation **truth** does not:

1. Claim jobs **stage** observation candidates (`normalize_observation_staging`).
2. Claim barrier enqueues one `adjudicate_observations` row with
   `target_kind=document_version`.
3. `ObservationFlushHandler` loads all staging, groups by entity, then for each
   entity calls `ObservationAdjudicator.add_observations` **serially**.
4. Residue path (similar-not-identical statements on an entity with open priors)
   runs up to `hub_top_k` (default **5**) small-model ladder calls **per
   assertion**, sequentially, over OpenRouter.

BEAM staging skew (observed 2026-08-12): ~2368 entities, top hubs 245 / 167 /
167 assertions — residue-heavy hubs dominate wall clock. Long tail of small
entities waits behind them in a single lease.

### 1.2 Why scaling workers fails

Same class as pre-D84 extract and pre-D88 normalize: **one ledger row → one
holder**. Extra `worker-adjudicate-observations` replicas idle.

### 1.3 What is *not* the problem

- D43 semantics (entity block, apply-in-order, fail-safe no-cap) — remain
  binding.
- Claim-level normalize (D88) — finished its job; staging is complete for the
  version when the claim barrier fires.
- “Need global document order across entities” — D43 is **entity-anchored**;
  open observation sets do not cross subject entities.

### 1.4 Secondary reliability issue (serial version job)

`add_observations` today holds a **single DB transaction open for an entire
entity batch**, including remote LLM/embed calls. On a hub of hundreds of
residue assertions that can mean multi-hour open TX, connection pool pressure,
and a kill/restart that rolls back uncommitted progress (staging clear only at
end of batch). Re-apply is intended to be evidence-PK idempotent, but **wasted
LLM spend** and **zombie `running` rows** (seen on BEAM when workers were
recreated) are operationally painful.

## 2. Alternatives

| Option | Verdict | Why |
| --- | --- | --- |
| Scale adj workers only | Reject | No second lease on one version job |
| In-process thread pool inside version job | Reject as sole v1 | Weaker ops signal; accounting; still one row; easy to share connections badly |
| Parallel assertions **within** one entity | Reject | Breaks D43 open-set order / supersede semantics |
| Parallel ladder judgments without ordered apply | Reject v1 | “First decisive in rank order” is order-sensitive; redesign later |
| Lower `hub_top_k` only | Complementary, not sufficient | Reduces work per residue assertion; does not free long tail of entities |
| Faster model / lower latency only | Complementary | Does not fix single-lease ceiling |
| Per-**entity** flush jobs | **Chosen v1** | Matches D43 isolation + D88 pattern; `ProcessingTarget.ENTITY` exists |
| Per-assertion flush jobs | Reject v1 | O(staging) rows; still need entity lock + order; little gain vs entity grain when hubs dominate critical path |
| Commutative D43 redesign (no order) | Out of scope | Separate product design; D88 deferred it |

## 3. Continuous ingestion

Entity flush barriers remain **per document version + normalizer generation**,
not global.

| Scenario | Behavior |
| --- | --- |
| New docs while V flushes | Independent trees |
| New version of same lineage | New staging generation / expected entity set |
| One entity DLQ on V | V’s supersession/embed barrier holds; other entities continue |
| Empty staging (no observations) | Skip entity grain; open supersession as today |

Expected entity set = distinct `subject_entity_id` in staging for
`(deployment_id, version_id, normalizer_version)` at the moment the **claim
normalize barrier** fires (or empty → supersession).

## 4. Fact layer interaction

| Layer | Parallel entity flush | Correctness mechanism |
| --- | --- | --- |
| Staging | Written only during claim normalize | Claim barrier precedes fan-out |
| Open observations | Concurrent across **entities** | Per-entity advisory lock + ordered apply |
| Exact evidence attach | Concurrent OK | Existing evidence PK / ON CONFLICT |
| Supersede caps | Within entity only | Serial apply under entity lock |
| Relation supersession | **After all entity flush jobs succeed** | Version barrier (same family as claim barrier) |
| Embed claim | After supersession (unchanged chain) | Existing follow-ups |

## 5. Ordering invariant (cold-reader restatement)

**Within one subject entity:** assertions must be applied in
`(asserted_at, claim_id)` order so that open-window caps, evidence collapse, and
contradiction groups see a stable prior set. Parallel claim completion is why
staging + flush exist (D88 §5.6).

**Across subject entities:** no shared open set. Completing entity B before
entity A is correct even if some of B’s claims have later `asserted_at` than
A’s. Global sort of all staging rows is only a convenient way to **build**
per-entity ordered lists; it is not a cross-entity apply schedule.

## 6. Critical-path math (BEAM-shaped)

Rough model:

- Wall serial ≈ Σ entity_cost  
- Wall parallel (W workers) ≈ max(hub costs) + tail / W (idealized)  
- Hub cost ≈ residue_assertions × ~hub_top_k × LLM_latency  

Parallel entity fan-out **does not** shrink the largest hub; it **drains the
long tail concurrently**. That is still the right scale-out for 2k+ entities
when one serial lease currently queues all of them.

## 7. Ledger identity trap (must not ship bare entity target_id)

D12 work identity is
`(deployment_id, target_kind, target_id, stage, component_version)` — **no
version column**. Canonical `subject_entity_id` is deployment-global. If the
ledger used `target_id = subject_entity_id`, two versions staging the same
entity would collide (`ON CONFLICT DO NOTHING`), skip a version’s flush slice,
and can open a false barrier. Chunk/claim fan-out avoid this because their
structural path is version-tied and “do once” is correct; **entity flush is not
version-idempotent**.

Therefore the solid unit is a durable membership row
`(deployment_id, version_id, normalizer_version, subject_entity_id)` with a
generated `unit_id` used as ledger `target_id` under `target_kind=entity`.

## 8. Recommendation

Bind **version-scoped entity flush units** after the claim normalize barrier:

1. Membership table + atomic set insert (D84/D88 protocol).  
2. `complete_entity_obs_flush` + shared representation barrier lock + anti-join
   on membership → supersession **and** embed_claim (siblings).  
3. Within unit: serial total order `(asserted_at NULLS LAST, claim_id, statement)`.  
4. Entity lock held for the whole unit apply; no stale LLM apply after unlock
   without revalidation.  
5. Fan-out generation suffix; exclusive cutover vs legacy version-serial flush.  
6. Forget/readiness join membership by version — never scrub by bare entity id alone.

Binding design: `plan/designs/e3_entity_obs_flush_fanout_design.md` (decision
log **D90**). Dual design review REQUEST_CHANGES absorbed into that revision.
