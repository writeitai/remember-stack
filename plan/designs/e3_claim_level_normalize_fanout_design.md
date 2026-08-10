# Design: claim-level E3 normalize fan-out

**Status:** revised after Codex design review — binding once review findings
absorbed and PR lands on `main`  
**Date:** 2026-08-10  
**Decision log:** D88  
**Analysis:** [e3_claim_level_normalize_fanout_analysis.md](../analysis/e3_claim_level_normalize_fanout_analysis.md)  
**Review:** [REVIEW_codex-sol_e3_claim_level_normalize_fanout_design_2026-08-10.md](../../design/reviews/REVIEW_codex-sol_e3_claim_level_normalize_fanout_design_2026-08-10.md)  
**Amends:** version-serial E3 path in [e2_e3_claims_relations_design.md](e2_e3_claims_relations_design.md); extract→normalize handoff in [chunk_level_extract_design.md](chunk_level_extract_design.md) §5.3  
**Preserves:** [e3_unknown_entity_type_gate_design.md](e3_unknown_entity_type_gate_design.md) (D86) inside each claim job  
**Pattern:** same family as D84 chunk-level extract (`complete_chunk_extract` +
representation advisory lock)

## 1. Decision

1. **Primary work unit** for stage `normalize_relations` is one **claim**:
   - `target_kind = claim` (`ProcessingTarget.CLAIM` already exists)
   - `target_id = claim_id`
   - `component_version = E3_NORMALIZER_VERSION` with a **fan-out generation
     suffix** (e.g. append `:claim-fanout-1` to the D86 string) so legacy
     version-serial rows are not confused with new coordinators at readiness.
2. **Fan-out** when extract barrier fires: enqueue **one normalize job per
   accepted claim** of that representation via a **single set-based insert of
   the complete expected claim set in the same transaction as the extract
   barrier handoff** (see §5.2). Not an open-ended “chunk batches later.”
3. **Barrier:** enqueue version-level **downstream** work only when every
   expected claim has `status=succeeded` at the **fan-out** normalizer
   component version. Soft assertion drops inside a claim job still yield
   `succeeded`. `dead_letter` / `failed` / missing rows **block**.
4. **Legacy** version-level rows at the **pre-fanout** component version remain
   serial handlers until drained or coordinated by migration. New images must
   not treat a **coordinator** version-level success as “normalize complete.”
5. **Claim normalize semantics unchanged** for LLM path: prompt, temp 0, D86
   type gate (generate-only soft path), signature/predicate gates, resolve/mint,
   relation upsert.
6. **Observations:** claim jobs **buffer** observation candidates (or write
   unresolved staging if needed) but **authoritative D43 adjudication** runs in
   a **post-barrier, per-version ordered flush** (§5.6). Entity lock alone is
   not order independence.
7. **Relation supersession:** only after barrier + observation flush policy in
   §5.6; **version-scoped** exact relation selector in §5.5. Not worker-local
   `relation_ids`.
8. **Correctness does not depend on job completion order** or FIFO queues for
   **relation evidence attach**. Observation and supersession use **source
   time (`asserted_at`)** and fixed selectors. Continuous multi-doc ingest is
   supported because barriers are **per document version**.

## 2. Problem (why this exists)

BEAM-scale documents produce thousands of claims. After D84, extract scales with
workers; normalize does not. A single version-level lease walks claims serially
(LLM + resolve), so wall clock is multi-hour and extra normalize replicas are
idle. D86 fixed FK dead-letter on unknown types but deferred fan-out.

## 3. Rationale

- Claims are independent **normalize LLM units** once extract has accepted them.
- Relation evidence attach is claim-keyed and idempotent under concurrency.
- D43 observation ladder is **order-sensitive today** (apply-in-order, cap by
  arriving `asserted_at`); parallel claim completion must not use “lock winner
  = document order.” Hence a post-barrier ordered flush (or a future D43
  commutativity redesign — out of scope for v1 product semantics).
- Supersession needs a **complete** competing set and `asserted_at` direction,
  not process order — after a version barrier with a bound selector.
- D84’s landed pattern: **advisory lock + complete + barrier anti-join +
  enqueue in one transaction** is required, not optional prose.

## 4. Continuous ingestion

Expected claim set = accepted claims of **one closed document version**, fixed
when that version’s extract barrier succeeds.

| Scenario | Behavior |
| --- | --- |
| New documents while V normalizes | New trees; do not enlarge V’s set |
| New version of same lineage | New set + barrier |
| Claim `dead_letter` on V | V blocked; others proceed |
| Soft drop inside claim job | Job `succeeded`; counts for barrier |

No global “ingestion quiet” wait.

## 5. Contracts

### 5.1 Expected claim set

```text
expected_claim_ids(deployment_id, representation_id, version_id) =
  claim_ids accepted for chunks of that representation
  at the extract generation in force when the extract barrier fired
```

Membership is **deployment-scoped** and must join claim → chunk → representation
→ version so payload UUIDs cannot cross tenants or versions. Re-query is OK if
deterministic; optional durable manifest is allowed but not required if fan-out
is one atomic set insert (§5.2).

### 5.2 Fan-out durability (binding protocol)

**Chosen protocol (v1):** set-based `INSERT … SELECT` (or equivalent batched
insert of the **complete** expected set) of all claim normalize rows
**in the same database transaction** as the extract barrier’s successful
handoff that today enqueues one version normalize.

Consequences:

- Extract barrier completion and full claim-job materialization are atomic.
- A crash cannot leave “coordinator done, half the children missing.”
- After insert, evaluate whether all children already `succeeded` (migration /
  replay) and if so enqueue downstream in that same transaction edge.
- Wake strategy: ledger insert notifications / announce as today; if one
  transaction of 15k rows is too large for a given deployment, **still** must
  not mark handoff complete until the full set exists — prefer raising the
  transaction limit or using a **single** bulk insert; a multi-batch fan-out is
  only allowed if a durable `fanout_complete` prerequisite is implemented
  (analysis alternative). **v1 default: one complete insert transaction.**

Idempotency: unique
`(deployment_id, claim, claim_id, normalize_relations, E3_FANOUT_VERSION)`.

Payload per claim job:

```json
{
  "version_id": "<uuid>",
  "representation_id": "<uuid>",
  "claim_id": "<uuid>",
  "doc_id": "<uuid>"
}
```

Handler **validates** claim_id belongs to representation_id/version_id/doc_id
for the deployment before work.

### 5.3 Claim job handler

```text
handle(work) where target_kind == CLAIM:
  validate coordinates (§5.1)
  if already evidence-normalized at E3_FANOUT_VERSION: skip LLM
  else: normalize_one_claim  # D86 generate soft boundary; resolve systemic re-raises
  upsert relations for this claim
  stage observation candidates for this claim (see §5.6) — do not final-adjudicate
    out of order in v1
  complete_claim_normalize(...)  # lock + succeed + barrier
```

Legacy `target_kind=document_version` at **pre-fanout** component version:
serial whole-version loop (old behavior) until drained.

Legacy/new **coordinator** at fanout version (if any version-level row exists):
fan-out only + barrier check; **never** mark version normalize “complete” by
coordinator success alone.

### 5.4 Barrier (atomic complete + serialization lock)

Mirror landed D84 `complete_chunk_extract`
(`work_ledger.py` representation advisory lock):

**API:** `WorkLedger.complete_claim_normalize(...)` in **one** transaction:

1. Acquire **version-scoped** (or representation-scoped) advisory lock  
   `hash(deployment_id || normalize-barrier || representation_id || E3_FANOUT_VERSION)`  
   (one bigint key, same pattern as other locks in-repo).
2. Mark current claim row `succeeded` (must be `running`).
3. Set-based anti-join: every expected claim_id has a row with
   `stage=normalize_relations`, `target_kind=claim`, matching component version,
   **`status=succeeded`** only (not “any terminal”).
4. If incomplete → commit success of this claim only; no downstream.
5. If complete → enqueue **once** (idempotent `ON CONFLICT DO NOTHING`):
   - observation flush work if modeled as separate stage, **or** inline flush
     then:
   - `adjudicate_supersession` (version target, payload identifies version +
     representation + normalizer generation — **no** partial relation_ids list
     required from workers)
   - `embed_claim` as today

**Missed-fire race:** without the advisory lock, two last claims can each see
the other as non-succeeded under read-committed and both skip enqueue. The lock
is **mandatory**, not a nit.

**Tests:** two real connections interleaved so each sees the other running
before either commits; expect exactly one downstream pair.

### 5.5 Relation supersession selector (binding)

After barrier, supersession work for version V:

```text
relation_ids =
  DISTINCT relation_id from relation_evidence
  WHERE claim_id IN expected_claim_ids(V)
    AND normalizer_version = E3_FANOUT_VERSION  # or generation match policy
```

- Do **not** use “relations created_at in window” or “touched” as the selector.
- Do **not** pass only worker-local created ids.
- For each relation, evidence used in prompts / boundary direction prefers
  **claim `asserted_at`**, not ingestion/`occurred_at` order, when choosing
  predecessor vs successor among supporting claims (impl may need a small
  supersession helper change; bind the product rule here).
- Generation replay: skip relations already adjudicated at current adjudicator
  version when safe (existing pattern).

Cross-version continuous ingest: two version barriers may race on one
subject/predicate block; existing block lock serializes writers. Direction must
follow **asserted_at**, so late older testimony does not “win” solely by
finishing normalize later.

### 5.6 Observations (binding order policy for v1)

**v1 choice:** claim jobs **do not** call final D43 ladder as the sole write of
truth under arbitrary arrival order.

Instead:

1. Claim job may **stage** observation assertions (subject resolved entity id +
   statement + claim_id + doc_id + asserted_at) in spine tables or a dedicated
   staging structure that is claim-idempotent.
2. After normalize barrier (same txn edge or immediately chained stage
   `flush_observations` on the version), flush **per entity** with assertions
   sorted by `(asserted_at, claim_id)` and apply D43 **in that order** under the
   entity lock.

This preserves D43’s “apply in order” contract while allowing parallel LLM
normalize. A future D43 redesign for true commutative insert may remove the
flush stage; that is a separate design.

### 5.7 Readiness, connector-cycle, cutover

| Rule | Binding |
| --- | --- |
| Normalize ready for version at **fanout** generation | All expected **claim** rows `succeeded` at `E3_FANOUT_VERSION` |
| Version-level row succeeded at fanout generation | **Coordinator only** — insufficient for readiness |
| Pre-fanout component version version-level `succeeded` | Ready under **old** generation only (legacy serial complete) |
| Claim `dead_letter` | Version normalize **not** ready; connector-cycle **must** wait (include DLQ children, unlike a pending-only wait) |
| Cutover | Bump component version; extract barrier starts enqueuing claim fan-out; drain old serial jobs |
| Rollback | Stop new fan-out; old image cannot safely claim `target_kind=claim` — roll all workers together; claim rows at new version remain until replay/drain policy |

### 5.8 D86 inside claim jobs

Unchanged: generate-only soft `ProviderInvalidResponseError`; illegal types
retry then drop; systemic provider/DB re-raise; mint defense; cost keys
`normalize:{claim_id}:aN` / `aN:failure` for soft path only.

## 6. Failure and recovery

| Failure | Behavior |
| --- | --- |
| Provider blip on one claim | Row retries; peers continue |
| Claim dead-letter | Barrier holds; no adjudicate/embed; `ops replay` that processing_id |
| Crash mid claim LLM | Attempt semantics unchanged |
| Two last claims race | Advisory lock ⇒ one barrier fire |
| Partial fan-out | **Prevented** by atomic full-set insert (§5.2) |
| Coordinator success without children | Impossible if children inserted in same txn as handoff |
| Downstream already present | Idempotent enqueue |

## 7. Security and tenancy

- Workers only claim deployment-local rows.
- Payload coordinates must match the claim row’s lineage (reject cross-version
  or cross-deployment ids).
- No new public HTTP surface.

## 8. Costs

- Model $: ~same tokens; higher concurrency → rate limits / spend caps.
- Postgres: O(claims) processing rows + barrier anti-join cost; require EXPLAIN
  on expected-set query at BEAM scale in impl PR.
- Fan-out transaction size: large but one-shot; monitor duration and bloat.
- Notification amplification: many wakes after bulk insert — acceptable; may
  batch announces if the queue port supports it.

## 9. UMC / compose

Same image; `worker --stage normalize_relations`. Scale on claim-grain
pending/running. No control-plane work manufacture (D67). Single-revision
deploy for all normalize workers.

## 10. Implementation plan (ordered)

1. Land this design + D88 + absorb Codex review (this revision).  
2. Introduce `E3_FANOUT_VERSION` string; wire extract barrier to bulk claim
   enqueue under existing D84 txn (extend carefully).  
3. `complete_claim_normalize` with advisory lock + anti-join + downstream.  
4. Claim handler branch; stage observations; post-barrier ordered D43 flush.  
5. Version-scoped supersession load by origin claims + asserted_at direction.  
6. Readiness + connector-cycle SQL (DLQ-aware).  
7. Tests §11.  
8. Runbook + BEAM cutover.

## 11. Tests (acceptance)

| Test | Expectation |
| --- | --- |
| Multi-claim sequential completions | All succeeded; one obs flush path; one adjudicate; one embed |
| Two-connection last-claim race | Exactly one downstream pair |
| One claim dead-letter | No adjudicate/embed; readiness false |
| Zero-claim version | Terminal branches without claim jobs |
| Atomic fan-out | Kill after handoff txn ⇒ either no children and handoff incomplete, or full child set |
| D86 Process twice | Claim succeeds; soft drop; barrier can complete |
| D86 systemic resolve error | Claim fails/retries; not soft-success |
| Observations reverse completion order | Same windows after ordered flush |
| Supersession reverse version order | asserted_at drives boundary, not finish order |
| Selector | Only relations evidenced by expected claims |
| Continuous two versions | Barriers independent |
| Cross-tenant payload lie | Rejected |
| Readiness | Coordinator-only success ≠ ready |
| Connector-cycle | Waits on claim DLQ |
| BEAM-scale dry | Anti-join + bulk insert plans acceptable |

## 12. Out of scope

- Per-chunk normalize as v1 grain  
- Document-order entity typing  
- Commutative D43 redesign (optional later to remove flush)  
- Changing Claimify  
- Automatic skip of dead-lettered claims  
- Entity-sharded normalize  

## 13. Alternatives not chosen

| Alternative | Why not |
| --- | --- |
| Scale workers only | Single version lease |
| FIFO as correctness | Reorders under load |
| In-claim final D43 only | Order-sensitive under lock-winner race (Codex B3) |
| Worker-local relation_ids supersession | Incomplete graph under fan-out |
| Global barrier | Breaks continuous ingest |
| Chunked fan-out without durable complete bit | Permanent missing children (Codex B2) |
| Readiness = version row OR claims | Coordinator false ready (Codex B5) |

## 14. Open implementation choices (ops only)

- Bulk insert chunking **inside** one transaction (savepoints) vs single statement.  
- Whether observation flush is a separate pipeline stage row or inline after
  barrier in the same txn (prefer separate stage if flush LLM cost is large).  
- Exact advisory lock key formula (must be one bigint).  

**Not open:** barrier lock; full-set fan-out atomicity; supersession selector;
readiness precedence; ordered observation flush for v1.
