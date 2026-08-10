# Design: claim-level E3 normalize fan-out

**Status:** accepted for implementation after design review (binding once on `main`)  
**Date:** 2026-08-10  
**Decision log:** D88  
**Analysis:** [e3_claim_level_normalize_fanout_analysis.md](../analysis/e3_claim_level_normalize_fanout_analysis.md)  
**Amends:** version-serial E3 path in [e2_e3_claims_relations_design.md](e2_e3_claims_relations_design.md); extract→normalize handoff in [chunk_level_extract_design.md](chunk_level_extract_design.md) §5.3  
**Preserves:** [e3_unknown_entity_type_gate_design.md](e3_unknown_entity_type_gate_design.md) (D86) inside each claim job  
**Pattern:** same family as D84 chunk-level extract

## 1. Decision

1. **Primary work unit** for stage `normalize_relations` is one **claim**:
   - `target_kind = claim` (`ProcessingTarget.CLAIM` already exists)
   - `target_id = claim_id`
   - `component_version = E3_NORMALIZER_VERSION` (includes D86 generation)
2. **Fan-out** when the extract barrier would today enqueue a single version
   normalize: enqueue **one normalize job per accepted claim** of that
   representation (idempotent), instead of one version-level serial job.
3. **Barrier:** enqueue version-level **downstream** work
   (`adjudicate_supersession`, `embed_claim`) only when every expected claim of
   that version has terminal normalize success at the current normalizer
   component version.
4. **Legacy** version-level `normalize_relations` rows act as **coordinators**:
   fan out claim jobs and/or fire the barrier; they do not re-run the serial
   whole-version claim loop.
5. **Claim normalize semantics unchanged** per claim: prompt, temp 0, D86 type
   gate, signature/predicate gates, resolve/mint, relation upsert.
6. **Observations:** each claim job writes observations through
   `ObservationAdjudicator` under the existing **entity lock** (D43). No
   requirement to wait for all claims of the entity.
7. **Relation supersession:** **not** run inside claim jobs. One
   **version-scoped** `adjudicate_supersession` after the barrier.
8. **Correctness does not depend on job completion order** or FIFO queues.
   Continuous multi-doc ingest is supported because barriers are **per document
   version**.

## 2. Problem (why this exists)

BEAM-scale documents produce thousands of claims. After D84, extract scales with
workers; normalize does not. A single version-level lease walks claims serially
(LLM + resolve), so wall clock is multi-hour and extra normalize replicas are
idle. D86 fixed FK dead-letter on unknown types but explicitly deferred fan-out.

## 3. Rationale

- Claims are independent **normalize** units once extract has accepted them
  (shared read-only registry/predicate catalogs).
- Relation evidence attach is claim-keyed and idempotent; observation adjudicate
  already serializes per entity.
- Supersession needs a **complete** competing relation set and claim
  `asserted_at`, not process order — so it belongs **after** a version barrier.
- `ProcessingTarget.CLAIM` and cost keys `normalize:{claim_id}:aN` already exist.
- Same operational story as D84: queue depth becomes a real scale signal.

## 4. Continuous ingestion

The barrier’s expected set is **only** the accepted claims of **one closed
document version** (fixed when extract for that version is complete).

| Scenario | Behavior |
| --- | --- |
| New documents arrive while V normalizes | New fan-out trees; do not enlarge V’s expected set |
| New version of same lineage | New claim set + new barrier |
| Claim job dead-letters on V | V’s barrier holds; other versions proceed |
| Soft drop inside a claim job (D86, signature, unknown predicate) | Job **succeeds**; barrier counts it done |

Do **not** define the barrier over “all claims in the deployment” or “until
ingestion stops.”

## 5. Contracts

### 5.1 Expected claim set

When extract barrier succeeds for representation R / version V:

```text
expected_claim_ids =
  all claim_id accepted for chunks of R at the extract generation in force
```

That set is **immutable** for the barrier of V. Implementation may re-query
claims for those chunk ids rather than store the set, but the membership rule
must be deterministic and closed (no later append to V without a new version).

### 5.2 Fan-out enqueue (replaces single version normalize from extract barrier)

| Case | Follow-up |
| --- | --- |
| 0 claims | Enqueue version-level terminal branches only (adjudicate no-op path + embed_claim), same as empty normalize today |
| N ≥ 1 claims | N × `normalize_relations` with `target_kind=claim`, `target_id=claim_id` |

Payload (JSON object) for each claim job:

```json
{
  "version_id": "<uuid>",
  "representation_id": "<uuid>",
  "claim_id": "<uuid>",
  "doc_id": "<uuid>"
}
```

`content_hash` and `lane` copy from the parent extract/normalize coordinator
work. `component_version = E3_NORMALIZER_VERSION`.

Idempotency: unique
`(deployment_id, claim, claim_id, normalize_relations, E3_NORMALIZER_VERSION)`.

### 5.3 Claim job handler

```text
handle(work):
  if work.target_kind == CLAIM:
    normalize_one_claim(work)   # D86 + gates + resolve + relation upsert
    write_observations_for_claim(work)  # D43 under entity lock per subject
    complete_claim_normalize(...)  # marks succeeded + maybe barrier
    return
  else:
    # legacy version-level coordinator
    fan_out_claim_normalize_jobs(version)
    maybe_fire_normalize_barrier(version)
    return  # no serial multi-claim loop
```

`normalize_one_claim`:

1. Load `ClaimForNormalization` for `target_id` / payload `claim_id`.
2. If missing → non-retryable.
3. If already evidence-backed normalized for this normalizer generation
   (existing replay markers) → no LLM; still participate in barrier.
4. Else run today’s single-claim body (including D86).
5. Do **not** enqueue adjudicate/embed from the claim job.

### 5.4 Barrier (atomic with work completion)

Mirror D84: **complete + barrier in one ledger transaction**.

**API:** `WorkLedger.complete_claim_normalize(...)` (name illustrative):

1. Mark the current claim normalize row `succeeded`.
2. Resolve expected claim ids for the version/representation in payload.
3. Require **for every expected claim id** a `processing_state` row with
   `stage=normalize_relations`, `target_kind=claim`, `target_id=claim_id`,
   `component_version=E3_NORMALIZER_VERSION`, `status=succeeded`.
4. If any expected claim is missing or in `pending` / `running` / `failed` /
   `dead_letter`, **do not** enqueue downstream.
5. If all succeeded: enqueue **once** (idempotent):
   - `adjudicate_supersession` on **document_version** / `version_id`
   - `embed_claim` as today  
   Supersession payload must identify the **version** (and representation if
   needed), **not** a partial worker-local list of relation ids. The
   adjudicator loads the version-scoped relation/evidence set from the spine.

**Dead letter:** blocks the barrier until `ops replay` succeeds on that claim
job. Soft assertion drops inside a succeeded claim do **not** block.

### 5.5 Supersession and observations (fact layer)

| Concern | Rule |
| --- | --- |
| Claims | Never superseded; immutable evidence only |
| Relation supersession | After barrier; version-scoped; uses claim `asserted_at` |
| Observations | In claim job via D43 entity lock |
| Process order | Not a correctness input |
| Entity mint type | First-under-lemma-lock (status quo); no doc-order typing in v1 |

### 5.6 Readiness and connector-cycle finalization

Version readiness for `normalize_relations` must treat the stage complete when
every expected **claim** has succeeded normalize at the normalizer component
version (or legacy version-level row succeeded under old images). Connector-cycle
SQL must wait on pending/running/failed **claim** normalize children, not only
version-level rows (same class of fix as D84 §5.3.1).

### 5.7 Metrics / ops

- Pending/running counts for `normalize_relations` approximate **claim** backlog.
- Document: scale `worker-normalize-relations` on that depth (now meaningful).
- Retain D86 structured logs per claim (`e3.unknown_entity_type*`, etc.).
- Optional: log `e3.normalize_barrier_fired version_id=… claim_count=…`.

## 6. Failure and recovery

| Failure | Behavior |
| --- | --- |
| Provider blip on one claim | That row retries; other claims continue |
| Claim dead-lettered | Barrier holds; no adjudicate/embed for version; `ops replay` that processing_id |
| OpenRouter empty body mid-run | Same as today at claim grain; smaller blast radius than whole version |
| Worker crash mid-claim | Ledger attempt semantics unchanged |
| Two claim completions race barrier | Idempotent downstream enqueue; still one adjudicate + one embed row |
| Deploy mid-drain | New image: version-level normalize rows become coordinators; claim rows use new handler |

## 7. Security and tenancy

No new trust boundary. Claim ids are internal UUIDs. Workers remain
deployment-scoped.

## 8. Costs

- **Model $:** ~unchanged total; concurrency may hit rate limits — ops caps replicas.
- **Postgres:** O(claims) rows per version; acceptable for BEAM 1M-class; monitor.
- **Enqueue:** batch fan-out where the ledger API allows; preserve idempotency.

## 9. UMC / compose

- Same image and `worker --stage normalize_relations` entrypoint.
- Rolling upgrade: all normalize workers from one image revision (mixed old
  serial handler claiming `target_kind=claim` is unsafe).
- UMC may scale on claim-grain backlog; must not manufacture work (D67).

## 10. Implementation plan (ordered)

1. Analysis + this design + D88 + design review.  
2. `enqueue_claim_normalizes(...)` + change extract barrier / coordinator path.  
3. Branch `NormalizeRelationsHandler` on `CLAIM` vs legacy version.  
4. `complete_claim_normalize` barrier + version-scoped supersession enqueue.  
5. Readiness / connector-cycle SQL updates.  
6. Tests (§11).  
7. Runbook: scale normalize on claim backlog; BEAM cutover notes.  
8. Bump `E3_NORMALIZER_VERSION` only if behavior provenance requires it (fan-out
   alone may keep D86 version if normalize outputs are identical; prefer bump
   with a `:claim-fanout-1` suffix for auditability).

## 11. Tests (acceptance)

| Test | Expectation |
| --- | --- |
| Multi-claim version, sequential claim jobs | All claims normalized; exactly one adjudicate + one embed enqueued |
| Two claim completions race barrier | Still one adjudicate and one embed row |
| One claim dead-lettered | No adjudicate/embed |
| Zero-claim version | Terminal branches without claim jobs |
| Legacy version-level normalize claim | Fans out claim jobs; no double serial loop |
| Already-normalized claim job | No provider call; barrier can still fire |
| D86 Process type twice | Drop; claim job succeeds; barrier can complete |
| Parallel two claims same lemma | Lemma lock; no double entity row for same lemma |
| Parallel two claims same entity observations | Entity lock; no lost writes; coexist/ladder per D43 |
| Supersession input | Adjudicator sees relations from multiple claim jobs for the version |
| Continuous: two versions in flight | Barriers independent |

## 12. Out of scope

- Per-chunk normalize grain as v1 (deferred alternative; see analysis)  
- Document-order entity typing  
- Incremental supersession per claim completion  
- Changing Claimify, D86 drop rules, or predicate signatures  
- Entity-sharded normalize  
- Automatic skip of dead-lettered claims in the barrier  

## 13. Alternatives not chosen

| Alternative | Why not |
| --- | --- |
| Scale workers only | Single version lease |
| In-process only parallelism | Weaker ops signal; harder ledger accounting |
| FIFO as correctness | Reordering under load; continuous ingest |
| Supersession inside each claim job | Partial graph; wrong coexist/supersede |
| Global / lineage barrier | Breaks continuous multi-doc ingest |
| Coerce types / auto-register | Already rejected in D86 |

## 14. Open implementation choices (not product forks)

- Enqueue batch size for claim fan-out.  
- Whether payload must repeat `claim_id` when equal to `target_id` (yes, for
  symmetry with chunk extract).  
- Exact supersession query: “all open relations with evidence from this
  version” vs “all relations created/touched in this version” — pick the
  smallest query that includes every competing fact for D3/D4 blocking.
