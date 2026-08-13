# Analysis: P1 Lance write path and maintenance at BEAM scale

**Status:** non-binding analysis  
**Date:** 2026-08-13  
**Related:** D8 (P1 inline projection), D9 (zero-LLM query path), D67 (ledger
work truth), D87 (fact eligibility scalars), R3 in
`design/benchmarks/review-pr193-risks.md`  
**Companion rulebook (prior, non-binding):**
[`lance_indexing_maintenance.md`](lance_indexing_maintenance.md)  
**Designed-but-missing worker family:** `plan/analysis/workers.md` §6.3
(`p1_batch_rebuild` + Lance compaction)  
**Binding decision:** D91 (entered; trigger/change-mass amendment 2026-08-13)  
**Driver:** BEAM-scale P1 write-path pain observed 2026-08-13 on self-host
compose after Phase E embeds finish quickly, then stall on fact metadata
refresh and long-lived fragment/index debt.

## 1. Problem

### 1.1 What is slow (primary incident)

After `LabelFactsHandler` Phase E embeds complete in reasonable wall clock
(batched provider calls + `upsert_facts` via `merge_insert`), the same handler
calls `update_fact_metadata` for **every** fact touched by the document:

```text
LabelFactsHandler.handle
  label_lock(deployment)
  Phase L: stamp deterministic labels
  Phase E: embed batches → fact_index.upsert_facts (merge_insert, good)
  fact_index.update_fact_metadata(fact_metadata_for_document(...))  ← pain
```

Implementation today (`src/rememberstack/adapters/selfhost/lance.py`):

```text
update_fact_metadata(rows):
  for row in rows:                    # one Lance commit per fact
    table.update(where=deployment+kind+fact_id, values=status/times)
  _maintain_indexed_tail(facts)
```

Observed on BEAM-scale self-host (order-of-magnitude, 2026-08-13):

| Signal | Observation |
| --- | --- |
| Facts refreshed | ~7.9k |
| Write shape | per-row `table.update(...)` |
| Fragments | thousands of ~19 KB fragments |
| Wall clock | multi-hour metadata pass after embeds finished |
| Disk | `facts.lance` ~2.7 GB+ and climbing under version/fragment churn |

This matches LanceDB's documented failure mode: **each write commits a new
version and a new fragment**, so a per-row loop pays per-call overhead at every
row and many small fragments slow later scans
([performance](https://docs.lancedb.com/performance), retrieved 2026-08-13).

### 1.2 Why embeds looked "done" while the job was not

Phase E stamps Postgres readiness **after** each Lance upsert batch
(Lance-before-ref). The metadata pass runs **after** all batches, still under
`label_lock`, still on the same `label_relation` lease. Operators see:

- embed HTTP / cost meter progress complete,
- job still `running`,
- disk growth in `facts.lance`,
- no other `label_relation` work for the deployment (lock + single lease).

So the incident presents as "label_relation hung after embeds," but the hot
path is Lance **per-row update**, not the embedder.

### 1.3 Secondary ops issue (in scope only as related)

`embed_claim` was observed hanging mid-lease after ~256 / ~15 000 claims on the
same host window. That is a **separate** grain/progress problem (version-level
lease, long handler, zombie `running` if the process dies without fail). This
analysis notes it; the primary design track is **Lance write/maintenance
correctness at scale**, not claim/relation fan-out. (D91 ships stage-scoped
reclaim for maintain only; a shared reaper for embed jobs remains a later
track.)

### 1.4 Index maintenance gap (structural, not only this incident)

| Layer | What code does today | What OSS LanceDB docs say |
| --- | --- | --- |
| Inline write | `_upsert` → `merge_insert` (good for upserts) | Prefer `add` for pure appends; `merge_insert` for upsert; scalar index on join keys ([performance](https://docs.lancedb.com/performance), 2026-08-13) |
| Metadata refresh | per-row `update` | `update` is for filter-based scalar edits; large multi-row updates still fragment; bulk key-aligned changes fit `merge_insert` ([tables/update](https://docs.lancedb.com/tables/update), 2026-08-13) |
| Light maintain | `_maintain_indexed_tail` → `table.optimize()` after **20 mutations** or **100k unindexed rows** (in-process counters) | `optimize()` = compaction + prune + **incremental** index update ([reindexing](https://docs.lancedb.com/indexing/reindexing), 2026-08-13). On pinned 0.34.0, `retrain=` is a deprecated no-op |
| Heavy maintain | `_build_vector_index` (IVF_FLAT, partitions from row count) only from `build_search_indexes()` | Full ANN retrain is **not** the same as `optimize()`. OSS does **not** auto-reindex; Enterprise does background index work ([reindexing](https://docs.lancedb.com/indexing/reindexing), 2026-08-13) |
| When heavy runs | `BackfillFinalizer.build_search_indexes` after backfill drain, or manual/tests | No continuous scheduled full rebuild on steady ingest |
| Index coverage | chunks/claims/facts partial; **entities never indexed**; **facts.fact_id** missing from ensure/build | Rulebook R1/R6: index every filter and merge join key |

Critical distinctions:

1. **`optimize()` is light maintenance.** It folds unindexed tails into
   **existing** indexes and compacts fragments. It does **not** retrain IVF
   partitions when the table has grown far past the last train point.
2. **`create_index` / IVF_FLAT rebuild is heavy maintenance.** Needed when
   partition count, train quality, or FTS rebuild policy says so.
3. **In-process mutation counters** (`_mutations_since_optimize`) reset on
   process restart and are not shared across compose workers. Thresholds are
   therefore **best-effort per process**, not table-global policy.
4. **Compose has no maintenance worker.** Continuous routes in
   `compose.yaml` cover eleven Plane-E workers (convert, structure, chunk,
   embed_chunk, extract_claims, normalize_relations, adjudicate_observations,
   adjudicate_supersession, embed_claim, reconcile, label_relation). There is
   no `worker-maintain-p1` (or similar). `workers.md` §6.3 named
   `p1_batch_rebuild` + compaction as a projection worker; it was never a
   continuous pipeline stage.
5. **Physical grain is the table under one `lance_root`.** `count_rows`,
   fragments, `optimize`, and `create_index` are table-global. Deployment id is
   a column, not a table boundary. Continuous maintain policy and locks must be
   table-scoped (self-host is single-deployment per root by construction).

### 1.5 What is *not* the problem

- Postgres truth / fact catalogs — authoritative; Lance is rebuildable projection (D8).
- Deterministic fact labels (S4) — cheap; not the multi-hour wall clock.
- Batched embed upserts via `merge_insert` — already the right family for Phase E.
- Retrieval correctness without indexes — exhaustive + unindexed-tail search still
  works; the failure is **latency, disk, and write amplification**, not wrong
  answers (same class as `lance_indexing_maintenance.md` archetype C).
- LanceDB Enterprise / Geneva — out of OSS self-host scope.

## 2. Physical storage (as-built self-host)

| Layer | Location |
| --- | --- |
| Settings | `SelfHostSettings.lance_root` default `Path("/var/lib/rememberstack/lance")` (`profiles/selfhost.py`) |
| Compose volume | `app-state` → `/var/lib/rememberstack` on app/worker containers |
| Host (Docker named volume) | `/var/lib/docker/volumes/rememberstack_app-state/_data/lance` on the VM disk |
| Tables under root | `chunks`, `claims`, `facts`, `entities` (embedded Lance dataset directory) |
| Object store | **Not** MinIO/S3 for default Lance; MinIO holds raw/derived blobs, not P1 vectors |

Implications:

- Backup/restore of P1 = filesystem snapshot/copy of `lance_root` **after
  quiesce** (or volume snapshot) **or** full rebuild from Postgres (preferred
  authoritative recovery). Live copy while writers/maintain commit is not a
  defined consistent backup.
- Disk-full on the Docker volume fails writes mid-fragment; recovery is free
  space + optimize/rebuild, not "repair Postgres."
- Cloud may later mount network disk under the same `lance_root` port; adapters
  must not hardcode Docker volume paths. Shared multi-deployment root is out of
  D91 continuous-maintain scope.

## 3. Code map (cold reader)

| Concern | Path |
| --- | --- |
| Per-row metadata update | `LanceChunkIndex.update_fact_metadata` → `_update_with_retry` |
| Batch upsert | `LanceChunkIndex._upsert` → `merge_insert(...).when_matched_update_all().when_not_matched_insert_all()` |
| Inline light maintain | `_maintain_indexed_tail` / `_optimize_with_retry` |
| Heavy vector index | `_build_vector_index` (IVF_FLAT, `LANCE_TARGET_PARTITION_ROWS = 8192`) |
| Full index bootstrap | `build_search_indexes()` — chunks/claims/facts only; **no entities** |
| Port | `FactIndexPort.update_fact_metadata`, `P1IndexMaintenancePort.build_search_indexes` |
| Writer | `LabelFactsHandler` (`workers/p1.py`) under `label_lock` |
| Backfill barrier | `BackfillFinalizer.build_search_indexes` |
| Unlaned stages | `catalog_contract.UNLANED_STAGES` |
| Readiness expected set | `profiles/selfhost.py` `_expected_components` (per document version) |
| Compose routes | `compose.yaml` `worker-*` services; no maintain stage |
| Stage enum | `PipelineStage` in `model/queue.py` — no maintain stage today |
| Purge optimize | `_purge_table_rows` uses `delete_unverified=True` (unsafe if another process maintains the same dataset) |

## 4. Alternatives

| Option | Verdict | Why |
| --- | --- | --- |
| Leave per-row `update`, only raise optimize frequency | Reject as sole fix | Still O(N) commits + fragments; optimize cannot keep up with 7.9k tiny updates |
| Single SQL-like `update` with huge `IN (...)` | Reject | Awkward for heterogeneous values per row; still one rewrite path without keyed merge benefits |
| **Batched `merge_insert` for metadata columns** | **Chosen writer fix** | One (or few) commits for thousands of keys; matches upsert pattern already used by `_upsert`; docs recommend scalar index on join keys |
| Fold eligibility scalars into every Phase E `upsert_facts` only | Complementary | Reduces need for a separate metadata pass on **first** embed; supersession/lifecycle still need scalar refresh without re-embed |
| **Skip metadata refresh when unchanged** | **Required with batching** | Merge update is delete-and-reinsert; without skip, every label job re-tails the whole affected fact set |
| Rely on inline `_maintain_indexed_tail` forever | Reject as sole maintain | Process-local counters; runs on hot path under `label_lock`; never does full IVF retrain |
| Full `create_index` on every optimize | Reject | Multi-hour work on hot path; thrash under continuous write |
| **Two-layer policy: light optimize + periodic heavy rebuild** | **Chosen maintain model** | Matches OSS docs (optimize ≠ full reindex) |
| LanceDB Enterprise auto-index | Reject for OSS self-host | Not the default product surface; do not bake Enterprise assumptions |
| Move Lance to S3/MinIO in this design | Out of scope | Future; keep `lance_root` as the port |
| Deployment-scoped maintain units | Reject for continuous policy | Stats/locks would be wrong under table-global Lance ops |
| Claim/relation grain fan-out for embed/label | Separate track | Related hang class; not required to fix fragment storm |

## 5. Two-layer maintenance model (lesson)

```text
                    ┌─────────────────────────────────────┐
  Hot write path    │  batch merge_insert / upserts       │
  (embed/label)     │  enqueue-only light maintain        │
                    │  NEVER multi-hour rebuild / optimize│
                    └──────────────┬──────────────────────┘
                                   │ fragments / unindexed tail
                                   ▼
                    ┌─────────────────────────────────────┐
  Light / frequent  │  table.optimize()                   │
                    │  compact + prune + incremental idx  │
                    │  table-scoped exclusive lock        │
                    └──────────────┬──────────────────────┘
                                   │ growth / policy / admin
                                   ▼
                    ┌─────────────────────────────────────┐
  Heavy / periodic  │  create_index IVF_FLAT retrain      │
                    │  optional FTS rebuild               │
                    │  same table lock; not label_lock    │
                    └─────────────────────────────────────┘
```

Hard rule for product code: **never rebuild on the read path; never hold
`label_lock` for Lance maintenance; never synchronous optimize under lease.**

## 6. Worker gap vs designed estate

`workers.md` §6.3:

> `p1_batch_rebuild` + compaction — full rebuild from Postgres for drills and
> embedding migrations, plus the Lance compaction schedule.

Today:

| Capability | Status |
| --- | --- |
| Inline write projection | Implemented |
| `build_search_indexes` after backfill | Implemented, one-shot; entities omitted |
| Continuous compact schedule | **Missing** |
| Continuous IVF retrain policy | **Missing** |
| Embedding migration rebuild campaign | Partial (backfill seeder family); not the same as light maintain |
| Compose service | **Missing** |

Separation to preserve in design:

| Job family | Purpose |
| --- | --- |
| Light maintain (`optimize`) | Fragment + unindexed-tail hygiene under continuous ingest |
| Heavy reindex | IVF/FTS retrain when stats say so |
| Batch rebuild / embedding migration | Re-project from Postgres with new embedder generation (content rewrite) |

Confusing these three recreates the incident: migration tools used as hygiene,
or hygiene expected to retrain ANN.

**How the system finds out (observers, not three crons):** writers enqueue
light after they dirty a table; the maintain worker's idle
`ensure_maintain_due` probes durable stats / Lance and may enqueue light or
heavy; finalizer/admin run ensure and force heavy. Heavy discovery must be
**durable change-mass** (vector rewrites only) plus leftover unindexed ratio —
not calendar-only and not row-growth-only (flat-count updates still move IVF
points). Chunks are more sensitive than short fact labels; skip-unchanged
eligibility must not increment heavy mass.

Routing note: Plane-P / scheduled projection work is **unlaned** in this
codebase (`UNLANED_STAGES`). Continuous maintain belongs there, not on
Plane-E `steady`/`backfill` lanes. `lane=backfill` would deadlock
`BackfillFinalizer` drain.

## 7. Costs and risks

| Risk | Severity | Notes |
| --- | --- | --- |
| Per-row metadata writes at 10k+ facts | **High** | Multi-hour wall clock; disk blowup; blocks deployment label lock |
| Unbounded unindexed tail | **High** for query latency | Correctness preserved; D9 path degrades to flat/exhaustive. Merge updates also re-tail rows — skip-unchanged is required |
| Full rebuild on hot path | **High** | Starves label/embed; commit conflicts |
| Concurrent writer + optimize/heavy | **Medium** | Light uses short `_LANCE_COMMIT_RETRIES`; heavy must **not** re-train 8× with sub-second pauses — D91 binds write-rate defer (no attempt burn) + long conflict_defer `not_before` + best-effort product contract with terminal `awaiting_operator` under sustained high write (no fake eventual-success). Table lock serializes light+heavy; writers remain outside lock; ops quiet gate is `maintenance_writer_gate=hold` / compose scale-down |
| Disk full mid-compact | **High** ops | Compaction can temporarily **increase** space before prune ([reindexing](https://docs.lancedb.com/indexing/reindexing)) |
| Stuck maintenance lease | **High** without reclaim | No estate reaper today; D91 binds stage-scoped reclaim via attempt-fenced `WorkLedger.fail(retryable=True, expected_attempt=…)` + side-thread heartbeat + wall-clock advisory-lock probe (never hand-rolled CHECK-violating `status='failed'` UPDATE) |
| Stale attempt steals replacement | **High** without attempt fence | `status='running'` alone is insufficient; D91 binds `ClaimedWork.attempt` through complete / fail / reclaim / heartbeat |
| Lost `rerun_requested` successor | **High** without atomic complete | Generic `complete()` after handler return races enqueue; D91 binds attempt-fenced `complete_maintain_p1` in one TX |
| Unindexed nominator prefilters | **Medium** latency | `LANCE_FILTER_COLUMNS` (`doc_id`, chunk categoricals) used with `prefilter=True` but not all indexed today — D91 matrix closes |
| Process-local optimize counters | **Medium** | Under-maintain after restarts or multi-replica |
| Partial-column `merge_insert` with insert clause | **High if misused** | **Verified** on `lancedb==0.34.0`: with `when_not_matched_insert_all()`, unmatched keys land as `label=None, vector=None`. Matched-only (`when_matched_update_all` alone): preserves omitted columns; missing keys no-op |
| `delete_unverified=True` concurrent with maintain | **High** corruption | LanceDB requires no other process on the dataset; purge must take the table maintain lock |
| Wiring maintain into `_expected_components` | **High** readiness | Would mark every document version missing forever |

## 8. Success criteria (for binding design)

1. Metadata refresh of ~8k facts is **batched** (orders of magnitude fewer Lance
   commits than N) and **skips unchanged** scalars.
2. Light optimize is **ledger-backed** and **table-scoped**, not only
   process-local write-path counters; writers **enqueue only**.
3. Heavy IVF rebuild is **policy-triggered** on a dedicated unlaned worker, not
   `label_relation`; policy uses durable change-mass / changed-row fraction
   (chunks stricter than facts) and excludes eligibility-only writes.
4. `build_search_indexes` / backfill finalizer and continuous maintain share
   one maintenance contract on `P1IndexMaintenancePort`, including **entities**
   and a complete index matrix.
5. Metrics exist for fragments, unindexed rows, optimize/rebuild duration,
   commit conflicts — before auto-enable.
6. Self-host compose ships a maintain route with enable gates default off;
   `lance_root` remains the storage port; docs state backup and ops procedures
   (D66).
7. Zombie `running` maintain rows are reclaimed via attempt-fenced
   `WorkLedger.fail` + heartbeat; double-run of optimize/heavy is safe; live
   heavy is never reclaimed; stale attempt A cannot complete/fail/heartbeat B.
8. Heavy under continuous writes is **best-effort**: pure rate-defer without
   attempt burn, conflict_defer with long `not_before` (one train), and
   terminal durable `awaiting_operator` after N/M/age budget — not short
   multi-retry full retrains and not a fake eventual-success guarantee while
   writers never quiet.
9. Maintain completion is atomic with `rerun_requested` successor creation and
   fenced on `expected_attempt`.
10. Facts join-key indexes exist before large metadata merges (PR1).

## 9. Open questions (analysis residual)

Resolved into the binding design (see
`plan/designs/p1_lance_maintenance_design.md`): physical table grain; unlaned
route; readiness exclusion; skip-unchanged; enqueue-only writers; stage-scoped
reclaim via attempt-fenced `fail` + heartbeat; atomic maintain completion;
heavy **best-effort** progress + `awaiting_operator`; full index matrix incl.
prefilter columns; named lock owner (handler / purge / finalizer); enable
gates; one ledger protocol; PR1 join-key ensure; attempt fence on all
ownership writes.

Still implementation-detail / soak questions:

1. Exact settings namespace (`P1Settings` vs `SelfHostSettings` vs dedicated).
2. FTS rebuild cadence after first production measurements.
3. Whether a future shared ledger reaper replaces stage-scoped reclaim.
4. Exact `heavy_defer_write_rate` unit after first BEAM metrics.
5. Bounded TTL for `maintenance_writer_gate=hold` after soak.

## 10. References

### Local

- `src/rememberstack/adapters/selfhost/lance.py`
- `src/rememberstack/workers/p1.py` (`LabelFactsHandler`, `EmbedClaimsHandler`)
- `src/rememberstack/spine/backfill.py` (`BackfillFinalizer`)
- `src/rememberstack/ports/p1_index.py`
- `src/rememberstack/model/queue.py`
- `src/rememberstack/profiles/selfhost.py`
- `src/rememberstack/spine/catalog_contract.py`
- `compose.yaml`
- `plan/analysis/workers.md` §6.3
- `plan/analysis/lance_indexing_maintenance.md`
- `design/benchmarks/review-pr193-risks.md` R3
- `plan/designs/p1_lance_maintenance_design.md` (D91)
- `design/reviews/REVIEW_claude-opus_p1_lance_maintenance_design_2026-08-13.md`
- `design/reviews/REVIEW_codex-sol_p1_lance_maintenance_design_2026-08-13.md`

### Public LanceDB docs (retrieved 2026-08-13)

- https://docs.lancedb.com/performance — batch writes; fragments/versions;
  `merge_insert` vs `add`; scalar indexes on filter/join columns; optimize after
  large writes.
- https://docs.lancedb.com/tables/update — `update` vs `merge_insert`; updated
  rows leave the index until reindexed; rebuild after large update batches.
- https://docs.lancedb.com/indexing/reindexing — `optimize()` = compaction +
  prune + incremental index update; OSS manual cadence; Enterprise auto;
  compaction may temporarily increase disk.

---

*Non-binding. Implementation follows the binding design
`plan/designs/p1_lance_maintenance_design.md` (D91).*
