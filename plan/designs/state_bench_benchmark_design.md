# STATE-Bench Agent Learning Track — binding design

> **Status:** binding setup for WP-8.7 (Track B). Implementation and pure/synthetic tests
> are allowed. No real STATE-Bench / locked-simulator / provider publication run is authorized
> until the owner completes the smoke checklist and preflight cap.

## 1. Protocol

```text
protocol                 RS-STATE-Learning-v1
upstream                 microsoft/STATE-Bench
upstream commit          4efcbf2d4fe60df04878859b692d9391f3d5b33a
upstream version         v0.8.1
protocol_id (upstream)   state_bench_v0.8.1_gpt54
track                    Agent Learning Track
domains                  travel, customer_support, shopping_assistant
retrieve_learnings top_k 3
official num_runs        5
simulator / judge        protocol-locked GPT-5.4 (upstream; do not substitute)
primary metric           Δ pass@1 vs empty-memory arm (paired)
secondary                pass^5, UX, cost/task, lookup latency, memory-call rate
```

A change to adapter version, recipe name, rendering format, extraction serializer, manifests,
or upstream pin creates a new protocol version (or an explicit fingerprint roll recorded in
`run.json`).

## 2. Acceptance boundary (setup complete when)

- Manifests validate: smoke (5/domain), development (15/domain), publication (50/domain).  
- Trajectory serializer is deterministic and train/test leakage-checked.  
- Empty and RememberStack arms implement `retrieve_learnings` without domain-tool side effects.  
- Parallel matrix + `--num-workers` drivers are documented and unit-tested for plan shape.  
- Pure tests pass; no real locked-judge campaign required for merge of the setup.

WP-8.7 remains **in progress** until an owner-authorized smoke (1 domain × 5 tasks × 1 run ×
{empty, RS}) completes under the preflight cap.

## 3. Arms

| Arm key | Role | Backend |
|---|---|---|
| `empty` | No-memory floor | `retrieve_learnings` → `[]` (tool still present) |
| `full_context` | Ceiling diagnostic | Inject packed train text (may truncate; **label ceiling**) |
| `bm25` | Lexical floor | Shared serialized units |
| `dense` | Semantic floor | Shared units + pinned embedder (later) |
| `mem0` | Competitor OSS | Pinned Mem0 commit (later) |
| `graphiti` | Competitor OSS | Pinned Graphiti commit (later) |
| `rememberstack` | Subject | Public `MemoryClient` + frozen recipe |

**First implementation ships `empty` + `rememberstack`.** Other arms are registry stubs so the
matrix and fingerprints stay stable.

### Sub-protocols

- **`shared`:** every backend receives the same documents from
  `serialize_trajectory_document` (no LLM extraction). **This is the only implemented
  sub-protocol in the first setup.**  
- **`native`:** RememberStack (and later native competitors) ingest the same raw trajectory JSON
  via ordinary public ingest; empty/bm25 still use shared text units for fairness labels.
  **Not implemented yet** — prepare/plan refuse the `native` label so it cannot be
  mis-fingerprinted as shared bytes.

Matched competitor tables require the same sub-protocol label.

## 4. RememberStack surface contract

- **Ingest:** public `MemoryClient.ingest` only; one document per train trajectory;
  `source_kind="state_bench_train"`, `source_ref="{domain}/{task_id}"`.  
- **Retrieve:** one frozen recipe, default `claims_hybrid_rrf`, arguments
  `{query, k=top_k}`; convert the envelope to ≤3 strings via
  `format_learnings_from_envelope`.  
- **No** benchmark-only SQL, **no** extra agent tools beyond STATE’s
  `retrieve_learnings`, **no** test-agent writes.  
- MCP may be parity-smoked later; headline runs use the Python SDK (less transport variance).  
- Plane K is not required; never claim K coverage.

## 5. Parallelism

```text
                    ┌── domain travel ──────── workers ──┐
matrix cell  ───────┼── domain support ────── workers ──┼── merge metrics
(arm, subproto)     └── domain shopping ───── workers ──┘
```

1. **Within domain:** STATE `run_batch --num-workers N` (thread pool).  
2. **Across domains:** three cells in parallel (separate processes; separate RS deployments
   for `rememberstack`).  
3. **Across arms:** independent cells; empty does not need Compose.  
4. **Multi-host:** optional; assign domains or task-id subsets; hosts never share a live
   Compose volume. Merge only scored trajectory trees + our run fingerprints.

RS workers share one read-only deployment per domain after ingest settles. That is intentional
and **unlike** LoCoMo’s per-conversation isolation (D78).

## 6. Run lifecycle (RememberStack arm)

```text
prepare  →  serialize train docs  →  ingest (isolated deployment)  →  drain pipeline
         →  projections  →  install agent into STATE-Bench agents/  →  run_batch
         →  record metrics path + fingerprint
```

`prepare` is local and provider-free. Remote stages require `--execute` and explicit caps.

## 7. Fingerprint fields (`run.json`)

- protocol name + adapter version  
- upstream commit + version + protocol_id  
- arm + sub-protocol  
- domain set + manifest sha256  
- repository revision (git) + image revision if Compose is used  
- recipe name + render format version  
- agent model name / reasoning level  
- num_runs, top_k, worker count  
- cost cap USD  

Mismatch at eval time is a hard stop.

## 8. Tiers

| Tier | Tasks / domain | Runs | Arms (minimum) |
|---|---:|---:|---|
| smoke | 5 | 1 | empty, rememberstack |
| development | 15 | 1–2 | empty, rememberstack (+ floors when ready) |
| publication | 50 | 5 | full matched set |

Manifests live in `benchmarks/state_bench/manifests/`.

## 9. Pre-run checklist

- Clean git revision equals prepared `run.json`.  
- STATE-Bench checkout at pinned commit.  
- Locked eval client configured (do not substitute).  
- Train trajectories present; test task IDs never used in extraction.  
- For RS: stamped image, ten workers healthy, readiness true after ingest.  
- Account hard limit + CLI reported-spend stop threshold set.  
- No claim that WP-8.5 differentiators were scored by this suite.

## 10. Repository layout

```text
benchmarks/state_bench/          # RS-owned adapter (this repo)
  protocol.py model.py trajectories.py retrieve.py runner.py cli.py
  agents/                        # StateBenchAgent subclasses (installed into STATE-Bench)
  parallel/                      # matrix planning + cell runner
  manifests/
plan/analysis/state_bench_track_b_analysis.md
plan/designs/state_bench_benchmark_design.md
```

Upstream STATE-Bench remains a **pinned external checkout**, not vendored.
