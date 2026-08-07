# Design: chunk-level extract (E2 work grain)

**Status:** accepted for implementation (binding once merged to `main`)  
**Date:** 2026-08-07  
**Analysis:** [`../analysis/chunk_level_extract_analysis.md`](../analysis/chunk_level_extract_analysis.md)  
**Decisions:** D12, D56, D58, D67; **new:** extract ledger grain is the chunk  
**Supersedes for work addressing only:** version-serial loop in
`ExtractClaimsHandler.handle` as the *sole* production path.

This document is implementer-facing. A cold reader should be able to change the
workers and tests without reopening product debate.

---

## 1. Problem

`extract_claims` is leased **once per document version** and then walks every E1
chunk serially inside one handler. Scaling `worker-extract-claims` does not
parallelize Claimify on a single large document (BEAM 1M/10M). E1 already
partitions text into section-bounded chunks; the ledger must claim work at that
grain.

## 2. Decision

1. **Primary work unit** for stage `extract_claims` is one **chunk**:
   - `target_kind = chunk`
   - `target_id = chunk_id`
   - same `stage` and `component_version` (`E2_EXTRACTOR_VERSION`) as today
2. **Fan-out** from E1 after chunk embedding completes: one extract job per
   chunk of the representation (idempotent enqueue).
3. **Barrier:** enqueue `normalize_relations` for the version only when every
   chunk of that representation has terminal extract at the extractor version.
4. **Legacy** non-chunk extract rows act as **coordinators**: fan out chunk jobs
   and/or fire the barrier; they do not re-run serial whole-document Claimify.
5. **Claimify semantics unchanged** (prompts, grounding, D33, D56).

## 3. Rationale

- Chunks are independent for Claimify except read-only neighbour context.
- D56 already keys reuse and "already extracted" on chunks.
- `ProcessingTarget.CHUNK` already exists on the ledger enum.
- Queue depth becomes a true load signal for UMC / self-host worker scaling.

## 4. Alternatives (summary)

| Option | Outcome |
| --- | --- |
| Scale workers only | Rejected — no single-doc speedup |
| Fixed multi-chunk batches only | Optional tuning of batch size later; default batch size **1** |
| Cloud queue per chunk without ledger grain | Rejected — D67 |
| Entity-sharded normalize first | Deferred — separate design |

Full argument: analysis §2–3.

## 5. Contracts

### 5.1 Enqueue after embed (`_extract_follow_up`)

Inputs: `ClaimedWork` from embed (or equivalent), `ChunkSource`, chunk list for
`representation_id` + current chunker version.

| Case | Follow-up |
| --- | --- |
| 0 chunks | One `normalize_relations` on the **version** target (unchanged identity used today for normalize) |
| N ≥ 1 chunks | N × `extract_claims` with `target_kind=chunk`, `target_id=chunk_id` |

Payload for each chunk job (JSON object):

```json
{
  "version_id": "<uuid>",
  "representation_id": "<uuid>",
  "chunk_id": "<uuid>"
}
```

`content_hash` and `lane` copy from the parent work row.

### 5.2 Handler behavior

```text
handle(work):
  if work.target_kind == CHUNK:
    extract_one_chunk(work)
    maybe_enqueue_normalize(representation)
    return
  else:
    # legacy coordinator
    fan_out_chunk_extract_jobs(representation)
    maybe_enqueue_normalize(representation)
    return  # no serial multi-chunk Claimify
```

`extract_one_chunk`:

1. Load source via `representation_id` from payload.
2. Load all chunks for neighbour bundle (same `_bundle_text` as today).
3. Locate the chunk matching `work.target_id` / payload `chunk_id`.
4. If missing → non-retryable error.
5. If `chunk_already_extracted` → no LLM; still run barrier.
6. Else D56 reuse or `_extract_chunk` (unchanged body).

### 5.3 Barrier (atomic with work completion)

The barrier **must not** run only inside the handler before the work row is
marked succeeded (two last chunks can each see the other still `running` and
both skip normalize; or an output-only check can fire while a sibling later
dead-letters). Dual-review (Codex P1.1): complete and barrier share one ledger
transaction.

**API:** `WorkLedger.complete_chunk_extract(...)` (or equivalent) in one
transaction:

1. Mark the current `extract_claims` / chunk row `succeeded` (same as
   `complete`).
2. Load the expected chunk id set for `representation_id` (+ chunker generation
   used when those chunks were written).
3. Require **for every expected chunk id** a `processing_state` row with
   `stage=extract_claims`, `target_kind=chunk`, `target_id=chunk_id`,
   `component_version=E2_EXTRACTOR_VERSION`, and `status=succeeded`.
4. Also require extract **output** evidence per chunk
   (`chunk_already_extracted`) so a crashed-after-write / before-complete
   replay still converges.
5. If any expected chunk has a row in `pending` / `running` / `failed` /
   `dead_letter`, or is missing, **do not** enqueue normalize.
6. If all succeeded: enqueue `normalize_relations` targeting
   **`document_version` / `version_id`** (not chunk), payload
   `{version_id, representation_id}`, with `ON CONFLICT DO NOTHING`.

Handler returns no normalize follow-up itself; the worker calls
`complete_chunk_extract` when `target_kind=chunk`.

**Dead letter:** blocks the barrier until `replay` succeeds. No automatic skip
in v1.

### 5.3.1 Readiness and connector-cycle finalization

Version-scoped readiness (`target_kind=document_version` only) **must** be
updated: after this change, primary `extract_claims` rows target chunks.
Pipeline readiness treats `extract_claims` for a version as ready when every
chunk of that version’s current representation has a succeeded extract row
(component version match), or the legacy version-level extract row succeeded.
Connector-cycle finalization SQL must likewise wait on pending/running/failed
**chunk** extract children, not only version-level rows (Codex P1.2).

### 5.4 Idempotency

- Chunk extract rows: unique on
  `(deployment_id, chunk, chunk_id, extract_claims, E2_EXTRACTOR_VERSION)`.
- Normalize: unique on existing version-level key — concurrent barrier winners are safe.
- Fan-out from embed and from legacy coordinator both use the same enqueue helper.

### 5.5 Metrics / readiness (OSS)

`processing_state` aggregates by `stage` already power readiness. After this
change, `extract_claims` pending/running counts approximate **chunk** backlog.
Document for operators and UMC: scale `worker-extract-claims` on that depth.

No new public HTTP API is required for v1.

## 6. Failure and recovery

| Failure | Behavior |
| --- | --- |
| Provider blip on one chunk | That processing row retries; other chunks continue |
| Chunk dead-lettered | Barrier holds; version normalize not enqueued; ops `replay` that processing_id |
| Worker crash mid-chunk | Ledger attempt semantics unchanged |
| Deploy mid-drain | New image coordinates legacy version extract rows into chunk jobs |
| Double barrier | Idempotent normalize enqueue |

## 7. Security and tenancy

No new trust boundary. Workers still only process rows for their deployment DB.
Chunk ids are internal UUIDs; no new mount surface.

## 8. Costs

- **Model $:** unchanged in total (same Claimify calls); higher **concurrency** may
  hit rate limits sooner — ops sets replica caps.
- **Postgres:** O(chunks) extract rows instead of O(documents). Acceptable at BEAM
  1M (~10k rows/doc) and 10M (~1e5 rows/doc) for steady lane; monitor bloat.
- **Enqueue latency:** fan-out should be batched in one ledger transaction where
  the API allows, or chunked batches of enqueues (implementation detail; preserve
  idempotency).

## 9. UMC auto-deploy and auto-worker-scaling

### 9.1 Auto-deploy

- Same container image, same worker entrypoints (`worker --stage extract_claims`).
- No new Coolify service names, secrets, or migrations of the `processing_target`
  enum (CHUNK already exists). Alembic may still be needed only if readiness SQL
  lives purely in app code (preferred).
- Rolling upgrade: **UMC and compose must roll extract workers with the same
  image generation as embed** (monorepo single image). Mixed-image windows where
  an old E2 binary can claim `target_kind=chunk` rows are unsafe (old handler
  serial-loops the whole doc). Mitigation: deploy all engine workers from one
  image revision; keep the window short. New code treats non-chunk extract rows
  as coordinators only. (Codex P1.3 residual risk documented, not ignored.)

### 9.2 Auto-worker-scaling (desired UMC story)

UMC must **not** manufacture extract work. It may:

1. Observe OSS `processing_state` (or a safe aggregate export) for
   `stage=extract_claims`, statuses pending/running.
2. Set replica count for the data-plane `worker-extract-claims` process.

Chunk-level grain makes that observation **useful**. Control plane remains non-
authoritative for work truth (D2/D6/D67 as used in UMC integration analysis).

Scale-to-zero remains a separate gated capability in UMC analysis; this design
only improves scale-up fidelity.

## 10. Implementation plan (ordered)

1. Shared helper: `enqueue_chunk_extracts(...)` + `maybe_enqueue_normalize_after_extract(...)`.
2. Change `_extract_follow_up` in E1 to fan-out.
3. Branch `ExtractClaimsHandler.handle` on `ProcessingTarget.CHUNK` vs legacy.
4. Unit/integration tests (see §11).
5. Runbook note: scale extract workers against chunk backlog.
6. Decision log entry in `decisions.md`.

No extractor version bump unless Claimify behavior changes (it should not).

## 11. Tests (acceptance)

| Test | Expectation |
| --- | --- |
| Multi-chunk doc, sequential worker runs of chunk jobs | All chunks extracted; exactly one normalize row created |
| Two chunk completions race barrier | Still one normalize row |
| One chunk dead-lettered | No normalize row |
| Zero-chunk representation | Normalize enqueued; no chunk extract rows |
| Legacy version-level extract claim | Fans out chunk jobs; does not double Claimify already-extracted chunks |
| D56 reuse path on chunk job | No provider call; barrier can still fire |
| Existing E2 chain acceptance | Still green (update expectations if they assumed one extract row) |

## 12. Out of scope

- Entity-sharded E3 normalize  
- Chunk-level embed ledger grain (optional later)  
- Changing chunk token budget  
- UMC control-plane autoscaler implementation (engine exposes the signal only)

## 13. Open implementation choices (not product forks)

- Enqueue batch size inside one transaction vs multiple calls.  
- Whether payload must repeat `chunk_id` when it equals `target_id` (yes, for
  symmetry with other stages).  

Default: implement the simplest correct barrier and fan-out; optimize enqueue
batching only if tests or BEAM fan-out show load issues.
