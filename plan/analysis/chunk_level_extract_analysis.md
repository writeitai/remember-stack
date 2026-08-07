# Analysis: chunk-level extract work grain (E2)

**Status:** analysis (non-binding)  
**Date:** 2026-08-07  
**Question:** Should `extract_claims` work items address one **chunk** instead of one
**document version**, so Claimify can run in parallel across workers on a single large
document?

**Related binding material:** D12 (idempotent work), D56 (chunk-grain extraction reuse),
D58 (E1 chunker), D67 (work ledger), `plan/designs/e2_e3_claims_relations_design.md`,
`plan/designs/orchestration_design.md`, `src/rememberstack/workers/e2.py`.

**Drivers:** BEAM 1M/10M single-conversation wall-clock; UMC desire that
`worker-extract-claims` replicas actually move one-document drains; queue-depth
signals for future auto-scaling.

---

## 1. Problem

### 1.1 What happens today

After E1 `embed_chunk` completes for a representation, the handler enqueues **one**
`extract_claims` row:

- `target_kind` = same as upstream work (typically document / document version identity)
- `target_id` = that identity
- `payload` = `{version_id, representation_id}`

`ExtractClaimsHandler.handle` then:

1. Loads **all** chunks for the representation.
2. Loops them **serially**, running two Claimify LLM calls per unfinished chunk.
3. On success, enqueues **one** `normalize_relations` for the version.

So N `worker-extract-claims` processes only help when **many document versions** are
queued. On one BEAM-scale conversation they mostly idle.

### 1.2 Why this hurts

| Scale | Approx. chunks | Serial extract shape |
| --- | ---: | --- |
| LoCoMo conversation | tens–low hundreds | minutes–tens of minutes |
| BEAM 100K | ~750 | hours under rate limits |
| BEAM 1M (median ~1.1M tokens) | ~5–8k projected | many hours |
| BEAM 10M (~12M tokens) | ~70–95k projected | multi-day extract alone |

Claimify is the expensive independent unit of work. The packer already produced a
stable list of those units (E1 chunks). The ledger does not expose them as claimable
jobs.

### 1.3 What already exists in our favor

- **`ProcessingTarget.CHUNK`** is already a first-class enum value on the ledger
  (`model/processing.py`). The identity key
  `(deployment_id, target_kind, target_id, stage, component_version)` can address a
  chunk without a migration of the enum.
- **D56 / `chunk_already_extracted` / `extraction_input_hash`** already make extract
  **replay and reuse chunk-grained**. The handler already skips finished chunks inside
  the version loop.
- Neighbor/context for Claimify already loads the full chunk list for a representation
  when building a bundle; processing one chunk still needs that read, but does not
  need exclusive ownership of other chunks' extract work.

---

## 2. Alternatives considered

### A. Keep version-level extract; scale workers only

**Pros:** zero code.  
**Cons:** does not change single-doc critical path. Rejected as the solution to the
stated problem (still the right multi-doc scaling story).

### B. Batch extract (K chunks per job, K fixed)

**Pros:** fewer ledger rows; less enqueue fan-out.  
**Cons:** partial failure retries re-do K; still serial inside the batch; worse
queue-depth signal for autoscalers. Viable as a **tuning** of C (batch size = 1..K),
not a different architecture.

### C. One ledger row per chunk (`target_kind=chunk`) — **preferred**

**Pros:** true parallelism; retries only failed chunks; queue depth = unfinished
chunks; fits D12/D56; UMC can scale `worker-extract-claims` on pending count.  
**Cons:** fan-out write amplification on enqueue; need a correct **barrier** before
normalize; in-flight version-level rows need a compatibility path.

### D. Chunk extract via external queue only (Cloud Tasks message per chunk, no ledger grain change)

**Pros:** looks like "autoscaling."  
**Cons:** violates D67 / M3: `processing_state` is authoritative work truth; adapters
only announce. Rejected.

### E. Entity-sharded normalize first

**Pros:** larger remaining wall-clock after extract.  
**Cons:** harder correctness (same-entity order, hubs); does not remove serial Claimify.
Tracked as a **follow-on**, not a substitute (see
`design/proposals/observation-adjudication-efficiency.md`).

---

## 3. Preferred shape (C) in detail

### 3.1 Fan-out

When E1 finishes embedding a representation (existing `_extract_follow_up` site):

1. List chunks for `(representation_id, chunker_version)`.
2. If **zero** chunks: enqueue version-level `normalize_relations` as today (empty
   extract).
3. If **N ≥ 1** chunks: enqueue **N** `extract_claims` rows with:
   - `target_kind = chunk`
   - `target_id = chunk_id`
   - `stage = extract_claims`
   - `component_version = E2_EXTRACTOR_VERSION` (unchanged Claimify generation unless
     behavior changes)
   - `payload` includes at least `representation_id`, `version_id`, `chunk_id`
     (chunk_id also is target_id; payload denormalizes for handler convenience)

Idempotency: re-running fan-out hits `ON CONFLICT` on the ledger unique key — safe.

### 3.2 Handler

`ExtractClaimsHandler` for chunk targets:

1. Resolve chunk + representation source.
2. Load sibling chunks for neighbour context (same as today).
3. Run **one** chunk path: already-extracted skip / D56 reuse / `_extract_chunk`.
4. **Do not** loop the whole document.
5. After success, run **barrier check** (below).

### 3.3 Barrier before normalize

Normalize must not start until every chunk of that representation has a terminal
extract outcome for `E2_EXTRACTOR_VERSION` (claims and/or decision rows such that
`chunk_already_extracted` is true).

On each successful chunk extract completion:

```text
if every chunk for representation is extracted at E2_EXTRACTOR_VERSION:
    enqueue normalize_relations for the document/version (idempotent)
```

Concurrency: two chunks finishing last both may attempt enqueue; ledger
`ON CONFLICT` makes double enqueue a no-op. **Do not** require a separate coordinator
stage unless measurement shows fan-out latency is a problem.

Failure policy:

- Chunk job fails/retries as today (bounded attempts → DLQ for **that chunk only**).
- Normalize is **not** enqueued while any chunk for the representation is still
  pending/running/failed-retryable for this component version.
- A chunk in **dead_letter** blocks the barrier (version cannot silently normalize
  with missing extract). Ops replay of that `processing_id` unblocks — same
  operational class as today's whole-version DLQ, but smaller blast radius.

### 3.4 Compatibility with in-flight version-level extract rows

Deployments mid-drain may still have `extract_claims` rows whose target is not
`chunk`. Handler behavior:

- If `target_kind == chunk`: single-chunk path + barrier.
- Else (legacy document / document_version extract): **fan-out then succeed** without
  re-running serial Claimify if possible:
  1. Enqueue per-chunk extract jobs (idempotent).
  2. If all chunks already extracted, enqueue normalize.
  3. Complete the legacy row as a coordinator, not as a second serial extract.

This preserves auto-deploy: old images leave version jobs; new images convert them
into chunk jobs without double-billing Claimify when chunks already finished.

### 3.5 What does **not** change

- Claimify prompts, grounding, D33 ledgers, D56 reuse keys.
- E1 packing policy (~400 whitespace-tokens / section, anchors).
- E3 normalize still **one job per version** (entity-sharded normalize is separate).
- API/MCP surfaces; OpenAPI; client wheels.

---

## 4. UMC auto-deploy and auto-worker-scaling

### 4.1 Auto-deploy must not break

Managed data planes deploy the same engine image and run stage-named workers
(`worker-extract-claims`, etc.). Chunk-level extract is **engine-internal**:

- No new worker process type required.
- No new compose service name.
- No new secret or env var required for the default path.
- Rolling deploy: legacy version-level rows are coordinated into chunk rows (3.4).

Therefore UMC image auto-deploy of a build that contains this change should continue
to work without control-plane changes.

### 4.2 Auto-worker-scaling becomes *meaningful*

Today, autoscaling `worker-extract-claims` on "queue depth" sees **~1 pending row per
document**, which under-signals load. After this change, pending depth for
`stage=extract_claims` is approximately **unfinished chunks**, which is the right
signal for:

- Coolify / Docker replica counts on self-host packs  
- Future packed-host burst workers  
- Cloud Run-style scale-out *when* UMC adopts queue-depth dispatch (still OSS-ledger
  authoritative; cloud only scales process count)

**Contract for UMC (observation only, no work truth in CP):**

- Safe metric: count of `processing_state` rows with
  `stage = extract_claims` and `status IN (pending, running)` (optionally by lane).
- Scale-up when depth / age exceeds thresholds; scale-down when idle — **without**
  inventing cloud-side extract jobs.

Scale-to-zero remains gated by existing UMC analysis (wake path / due work); this
design does not unlock scale-to-zero by itself, but it makes scale-**up** honest.

### 4.3 Cost and rate limits

Parallel Claimify multiplies concurrent provider calls. Budgets (D67) and provider
limits still apply per deployment. Fan-out does not change total tokens; it changes
concurrency. Deployments may need lower `worker-extract-claims` replica caps when
provider RPM is the bottleneck — ops, not correctness.

---

## 5. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Normalize starts early | Barrier uses authoritative per-chunk extract presence; tests for races |
| Double normalize enqueue | Ledger idempotency key on version normalize row |
| Fan-out storm (90k chunks) | Single transaction or batched enqueue; measure; optional batch size later |
| Neighbour context race | Bundle is read-only over chunk rows; extract writes claims, not sibling chunk text |
| Partial DLQ silent success | Barrier blocks on dead_letter; readiness reports unfinished extract |
| Mid-deploy mixed images | Legacy coordinator path (3.4) |

---

## 6. Recommendation

**Adopt alternative C** as binding design: chunk-targeted `extract_claims` work,
fan-out from E1 embed completion, per-chunk Claimify, barrier before normalize,
legacy version-level rows coordinate only.

**Defer** entity-sharded normalize to its own design track.

**UMC:** no deploy-path break; document pending-extract depth as the scale signal for
`worker-extract-claims`.

---

## 7. Implementation sketch (for the design doc to freeze)

1. `_extract_follow_up` → fan-out N chunk jobs (or normalize if N=0).  
2. `ExtractClaimsHandler` branches on `target_kind`.  
3. Barrier helper on `ClaimCatalog` / `ChunkCatalog`.  
4. Tests: multi-chunk parallel completion → single normalize; DLQ blocks; legacy
   fan-out; zero chunks.  
5. Optional ops note in `design/benchmarks/runbook.md` for scaling extract workers.

Decision log entry: extract work grain is chunk (high-level, reverse-costly for
ops metrics and BEAM drains).
