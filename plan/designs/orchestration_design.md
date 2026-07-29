# Orchestration — Workers, Queues, Lanes, Budgets, DLQ (Design)

How the workers *run*: the operational plane that connects the per-worker designs. The worker
**inventory** — every worker, its behavior contract, its execution class — is
`../analysis/workers.md`; per-worker *semantics* live in the layer designs. This document binds
what none of them owns: queue topology, the steady-state/backfill lane split, budget-enforcement
semantics, dead-letter operations, and provider-neutral I/O discipline. Decisions: **D52–D53**
(execution-class and model-family invariants) and **D67** (normalized queue-state ownership),
building on D12 (idempotent workers, retries, DLQ), D7 (replay-not-recall), D16 (deployment
isolation), D23 (scale), D25 (ungated volume).
Numbers here are starting points to measure, not committed constants (CLAUDE.md).

> **Reading this cold.** Plane E is a per-document chain of workers (E0
> ingest→convert→structure→crossref; E1 chunk/prefix/embed; E2 extract/ground; E3
> resolve/normalize/adjudicate/label), each delivered through the D61 queue port when the
> previous stage completes; the reference adapter uses Cloud Tasks/Cloud Run and the self-host
> adapter uses Postgres wake-ups. Planes K and P are debounced/scheduled aggregate workers.
> `processing_state` in Postgres is the idempotency + status + dead-letter ledger (one row per
> target/stage/version — schema §2) and, under D67, the authority for route, due time, retry, and
> parking state; `cost_ledger` meters every model call with lane attribution. A **lane** is a
> parallel plane-E queue set for the same stages with its own rate limits and budgets. Scheduled
> K/P jobs are explicitly unlaned.

## 1. No workflow engine — the chain is the orchestrator (scope boundary)

There is deliberately **no Airflow / Temporal / Step Functions layer**. The orchestrator is the
composition of three things that already exist:

1. **The D61 task-delivery port** — at-least-once announcement, transport redelivery, and
   rate limiting (Cloud Tasks in the reference adapter; Postgres wake-ups when self-hosted);
2. **`processing_state`** — state, idempotency, application retries, scheduling, and dead
   letters (D12/D67);
3. **the chain rule** — a completing worker enqueues the next stage for its target.

A workflow engine would duplicate all three, add a second stateful system to operate, and put
pipeline state somewhere other than the spine — against the grain of "state has one home"
(D6). Cross-stage coordination needs nothing more because stages are **independent idempotent
steps**: there is no distributed transaction to manage, only the next enqueue. This is a scope
boundary, not a deferral: a future need (e.g. deadline-bound human-in-the-loop steps spanning
stages) must be argued against this section, not assumed.

Here "retry" in item 1 means retrying a transport delivery. It may produce duplicate pushes but
cannot start another application attempt unless the Postgres row is due and claimable. Handler
attempt count, backoff, and terminal dead-lettering belong only to `processing_state` (D67).

## 2. Queue topology

- **One logical route per `(deployment, stage, lane)`.** Per-deployment isolation is a hard rule (D16 —
  separate instances never share infrastructure paths). Per-*stage* queues exist so backlogs
  do not couple: a slow OCR batch (convert) must not starve extraction; E3's Postgres-heavy
  adjudication is rate-limited independently of E2's LLM-throughput limits. Queue count is
  configuration, not architecture — stages × lanes × deployments is tens of queues, well
  inside platform limits. For lane-managed plane-E rows, `lane` is `steady` or `backfill`.
  K/P debounce or schedule jobs set `lane IS NULL`; their route is the single unlaned
  `(deployment, stage, NULL)` route, not a third lane. Physical queue names are derived adapter
  configuration and are not work identity.
- **Enqueue granularity mirrors `processing_state` targets**: document-grain through E0;
  chunk-grain from E1 fan-out (chunking is the fan-out point); E3 adjudication consumes
  **batches per (document, entity)** — the observation-design batching rule (one block fetch,
  batched adjudication) applied as the enqueue unit, which also concentrates database I/O (§5).
- **Rate limits and concurrency are per-queue config**, sized against the two shared
  bottlenecks: Postgres write capacity through the configured pool and
  per-provider LLM rate limits. Queues meter *volume*; the cascades already meter *ambiguity*
  (D4) — the two knobs are deliberately different mechanisms.
- **K and P workers keep their own trigger models** (debounce window; schedule — D12) and do
  not use lanes; lanes are a plane-E concept. Their jobs still record `processing_state` rows
  (`build_snapshot`, `compile_knowledge`, …) like everything else, with `lane IS NULL` and
  normalized `not_before` when their debounce/schedule is in the future. A domain trigger ledger
  may also carry a debounce timestamp—for example `knowledge_refresh_queue.not_before` coalesces
  evidence-change signals—but that row is not task delivery state. When it materializes a K/P job,
  the unlaned `processing_state` row is the only authority consulted by the D61 queue port.

The route is not the idempotency key. `processing_state` remains unique on
`(deployment, target_kind, target_id, stage, component_version)`, so a backfill seeder cannot
duplicate work already discovered by steady ingestion. First insertion establishes the route. A
duplicate steady enqueue may promote a pending/failed backfill row, preserving the live freshness
guarantee; a backfill enqueue never demotes steady work. An explicit operator replay may move a
dead-letter row between lanes. Each change affects future delivery rather than rewriting
historical costs.

Promotion has a closed transition table. For `defer_reason='budget'`, change the lane to steady,
keep `status='pending'`, clear the backfill defer reason, set `not_before=now()`, and run the steady
budget pre-flight when claimed (which may park it against the steady window). For
`defer_reason='scheduled'`, change only the lane and preserve the caller's `not_before`. For a
failed row with `defer_reason='retry_backoff'`, change only the lane and preserve status, due time,
attempts, and failure. Immediate pending work keeps its existing due/past `not_before`. Every
promotion is announced on the steady route after commit.

## 3. Two lanes: steady-state and backfill

Backfill (initial corpus load; version-bump reprocessing) and live ingestion run the **same
pipeline, same workers, same idempotency keys** — but **separate lanes**, so a million-document
backfill can never starve a fresh document's freshness SLA ("plane E processes promptly per
document" — requirements). Rules:

- **A backfill is defined by a version filter, nothing else** (D12): "all documents where
  `converter_version < X`", "all chunks where `extractor_version < Y`". A deterministic
  **backfill seeder** job — the one worker this document adds to the inventory — enumerates
  the filter into the backfill lane in bounded batches. Resumability is free: re-seeding is a
  no-op for every already-succeeded `(target, stage, version)` row, so a crashed or paused
  backfill continues by re-running the seeder.
- **Lane budgets are separate** (§4): backfill spend is a knob turned deliberately, never a
  surprise on the steady-state budget. The two dominant backfill costs are metered per lane
  and known in advance: extraction-side LLM calls (volume-proportional — design-review F8) and
  database/provider I/O (§5).
- **Priority is structural, not scheduled**: the steady lane's rate limits are never reduced
  for a backfill; a backfill gets whatever headroom the shared bottlenecks have left. No
  priority scheduler exists or is needed.

## 4. Budget enforcement — park, never drop

"Budgets enforced, not advisory" (overall §8) gets concrete semantics here:

- **Declaration**: budgets are per `(deployment, stage, lane, window)` — e.g. "extract_claims
  / steady / $N per day" — in deployment config.
- **Enforcement is a deterministic pre-flight check in the worker**: after locking one due row,
  read the deduplicated `cost_ledger` total for its authoritative lane and aligned window in the
  same claim transaction. The single-deployment library uses the existing range index directly;
  it adds no cache or second budget-state service. Concurrent calls may still finish after another
  worker passed pre-flight — enforcement is a dam, not a reservation system. If the budget is
  exhausted, the worker
  **parks** by setting `status='pending'`, `defer_reason='budget'`, and `not_before` to the
  window roll, then re-announces the row and exits *without* starting the handler
  (`processing_state.attempts` and `last_error` are untouched).
- **Parking is not failure.** Nothing dead-letters because of budget; no work is lost or
  skipped; the chain resumes exactly where it stopped when the window rolls or the budget is
  raised. Parking between stages is safe **by construction** — stages are independent
  idempotent steps, so there is no half-open cross-stage state to corrupt.
- **Exhaustion state carries the tier breakdown** (`cost_ledger.tier` — which cascade rung
  spent the money) through the admin/telemetry surfaces: an operator or hosting product can
  distinguish "the corpus got bigger" from "a prompt regression made the frontier rung fire
  10× more often" without reconstructing it from logs. Alert routing and dashboards are not
  library responsibilities (D60).

Each logical model/provider call writes an attribution row identified by
`(processing_id, attempt, call_key)`. The handler assigns a deterministic stage-local call key—D31
uses separate `selection` and `decontextualize` keys—so multi-call attempts are fully counted and
an acknowledged-late retry of one call is an idempotent insert. A batched call (a D58
window) is billed as one row on the claiming processing row; a batch never crosses a document or a
lane by construction, so lane budgets and document-level accounting stay exact without cost
splitting. `cost_ledger.lane` is copied from
`processing_state.lane` when that call begins. The budget lookup is therefore a range sum on
`(deployment_id, stage, lane, occurred_at)`; K/P calls can be metered on their unlaned route with
`lane IS NULL`, but they do not silently join either plane-E lane. A delivery envelope or Cloud
Tasks header cannot choose the attribution.

The model-provider port returns every successful generation or embedding together with required
provider accounting (resolved model name, input/output tokens, USD cost, and latency). A worker
binds a small cost-meter port to the running `processing_id`; each model-using handler writes that
usage under its deterministic call key immediately after the provider returns, before it consumes
the output. Missing or malformed provider accounting is an adapter error rather than an implicit
zero, so configured ceilings cannot be silently bypassed. Deterministic and non-worker consumers
may use the provider port without inventing a processing row; enforced worker-route budgets remain
anchored solely to `processing_state`.

## 5. Provider-neutral I/O discipline (portable core of design-review F9)

The spine or another provider may be remote from a worker. The library therefore avoids chatty
I/O without embedding the reference deployment's Hetzner/GCP topology or its capacity targets in
core logic. Rules:

- **Batch writes per worker invocation.** A worker commits its target's rows in as few
  transactions as its semantics allow — never row-at-a-time chatter. The E3
  per-(document, entity) batching exists for adjudication correctness and pays again here.
- **Front-load reads.** A worker fetches its inputs (context bundle, blocking candidates) in a
  bounded number of round-trips at start — never incrementally inside a loop.
- **Portable scale tests exercise the real pool contract and injected latency**: the fixed D23
  profiles verify bounded round trips and batching behavior. Timings are recorded measurements,
  never a hosted-service SLA or an owner forecast.
- **Topology tuning is outside the library**: colocating workers, scaling pgBouncer, and choosing
  regions are operator/`ultimate-memory-cloud` decisions. Store roles (D1/D6) and the batching
  invariants stay unchanged.

## 6. Dead-letter operations

The DLQ **is** `processing_state` rows with `status='dead_letter'` (D12 — no separate
infrastructure). The operational surface over it is thin and deterministic:

- **Inspect** — SQL views by (stage, error class, component version, age); the preserved
  enqueue `payload` makes every dead letter self-describing and manually replayable.
- **Replay** — reset the row to `pending`, clear its retry defer reason, choose `not_before`, raise
  `max_attempts` above the current `attempts` by the approved replay allowance, and re-announce
  its `processing_id`. Attempts never reset, so cost-ledger deduplication remains stable.
  Unconditionally safe: the idempotency key is unchanged, so a partially-succeeded earlier attempt
  no-ops. Bulk replay is the same operation scoped by version or error class; an explicit operator
  transition may reroute it through the backfill lane when large. `payload` remains handler input
  for inspection, never the source of route, schedule, or retry state.
- **Poison detection** — the same target dead-lettering across ≥2 component versions is
  flagged for a human: that is a content pathology (a document that breaks the converter), not
  a transient, and no amount of retrying fixes it.
- **DLQ depth per stage is a first-class alert metric.** "Failures never disappear"
  (requirements) is only true if something is watching the place they go.

A handler execution increments `attempts` exactly once when it begins. On retryable failure,
`attempts < max_attempts` produces `status='failed'`, `defer_reason='retry_backoff'`, and a
backoff-derived `not_before`; at the total-attempt limit the row becomes `dead_letter`.
Non-retryable errors may dead-letter earlier. Delivery retries that never claim the row consume no
application attempt, and budget parking can never take either failure transition.

## 7. Observable state and telemetry — no dashboard subsystem

Everything below derives from tables that already exist (`processing_state`, `cost_ledger`,
`projection_snapshots`, `knowledge_compilations`). The library exposes it through typed admin/CLI
results and the D61 telemetry port; it does not ship a dashboard backend or a second bookkeeping
system:

- per-stage throughput / latency / error rate / DLQ depth (`processing_state`);
- spend by stage × cascade tier × lane against budget (`cost_ledger`);
- the end-to-end trace of one document — its `processing_state` rows, ordered, *are* the
  trace;
- freshness SLAs: plane E p95 ingest→relations latency; plane P `built_from_watermark` lag vs
  cadence; plane K evidence-change→recompile lag vs the configured window (the k_layers eval
  metric).

The deployment operator or cloud product chooses collection, retention, dashboards, and alerts.
Instance provisioning — Langfuse self-host versus cloud, and GlitchTip versus Better Stack — is
an infrastructure decision tracked separately from the engine.
Those consumers must derive their view from this state/telemetry rather than becoming another
authority for pipeline truth (D60/D61).

## 8. Invariants (D52, D53)

- **Execution classes are bound** (D52): every worker is deterministic, programmatic-LLM, or
  agent-harness per the inventory's rule; **no harness on a per-document, per-claim, or query
  path** — harness seats live only on plane K and the review/audit surfaces.
- **Every LLM-calling worker carries an append-only transcript** (D52 — the D33 ledger
  discipline as a standing rule for new workers).
- **Checker seats run on a different model family than their producers** (D53): judges,
  reviewer agents, and the K reflection pass never share a family with what they check.

## References

Inventory: `../analysis/workers.md`. Decisions: D4, D6, D7, D12, D16, D23, D25,
**D52–D53 and D67**
(`decisions.md`). Review findings: `../analysis/design_review_2026_07.md` (F8, F9).
Requirements: operational properties + imposed constraints (`../requirements/requirements_v3.md`).
Worker semantics live in their layer designs: `e0_files_design.md`,
`e2_e3_claims_relations_design.md`, `observations_design.md`, `registries_design.md`,
`k_layers_design.md`, `p2_graph_design.md`; infra tables: `postgres_schema_design.md` §2.
