# Phase 8 — Competitive Benchmarks

**Goal:** demonstrate the system against the field — external, comparative, reproducible.
Distinct from the internal D22 harness: that answers "are we correct against our own golden
set"; this answers "are we better than the alternatives on shared ground", plus "what can we
do that they cannot".

**Entry gates:** Phases 1–5 done (ingestion, lifecycle, projections, full retrieval). A benchmark
records which Plane-K runtime actually ran; an empty/unconfigured K plane is never represented as
coverage. Each run records its actual cost and explicit run cap; no deployment-owner budget is
an OSS benchmark gate (D60).
**Exit criteria:** a published methodology + results document (reproducible runs, pinned
versions, honest losses included); the capability benchmark demonstrates the differentiators
end to end.

| WP | Goal | Reads | Depends | Deliverable | Acceptance | Status |
|---|---|---|---|---|---|---|
| WP-8.1 | **Benchmark landscape survey** — the field moves; select at execution time. Candidates to evaluate (verify currency then): LoCoMo, LongMemEval, DMR-class conversational-memory suites; multi-hop QA (HotpotQA/MultiHop-RAG-class) for graph strengths; latency/cost protocols the competitors publish | — (web survey; D22 for fit) | phase gates | selection memo (analysis/) | chosen suites + rationale + baseline list | done |
| WP-8.2 | Adapter layer: the system as a memory backend behind each benchmark's protocol (ingest/query interfaces, session semantics) | retrieval §3–7; benchmark specs | WP-8.1 | adapters | benchmark harness runs end-to-end on a sample | in progress — LoCoMo protocol/setup implemented; owner-reviewed real smoke deliberately pending |
| WP-8.2W | **Workspace-Bench heterogeneous-workspace track:** pin the upstream runner/task/workspace revisions; inventory all file types; preflight ingestion spend; add the same task agent + native filesystem control and the matched control + RememberStack read-plane arm | Workspace-Bench analysis; E0/media designs; retrieval §3–7 | WP-8.1, WP-8.2 shared manifests/cost envelope | Workspace-Bench adapter, ingest audit, paired five-task smoke | every file remains in coverage accounting; rubrics/gold graph never enter memory; both arms run with identical agent/judge/resources; smoke emits format coverage, failures, latency, tokens, and cost | planned |
| WP-8.3 | Baselines: Mem0 OSS + Graphiti OSS from the survey, plus BM25 and dense-RAG floors; hosted/vendor numbers are contextual only | WP-8.1 memo | WP-8.2 | baseline runs | reproducible baseline numbers | planned |
| WP-8.4 | Metrics + instrumentation: accuracy per suite, latency (P50/P95), token + $ cost per op (cost_ledger), ingestion throughput | schema §2; retrieval §10 | WP-8.2, WP-8.2W | metrics pipeline | one reproducible metrics artifact per run | planned |
| WP-8.5 | **Capability benchmark** (ours, from the S-battery): the differentiators competitors lack — bi-temporal as-of (S9/S10/S15), contradiction surfacing (S23), provenance hydration (S5), watched-source lifecycle (edit/retract/delete), forget (S55) | retrieval_scenarios.md | WP-8.2 | capability suite + narrative doc | each capability demonstrated + scripted | planned |
| WP-8.6 | Methodology + results publication (honest: include losses; pin versions; publish configs) | all above | WP-8.2W, WP-8.3–8.5 | report | reviewed; reproducible by a third party | planned |

## WP-8.1 selection

The current landscape survey and binding handoff are in
[`phase_8_benchmark_selection.md`](../analysis/phase_8_benchmark_selection.md). The initial
portfolio is LoCoMo QA, LongMemEval-S, MemoryAgentBench FactConsolidation-SH/MH, MultiHop-RAG
retrieval, and Workspace-Bench-Lite. Workspace-Bench adds a distinct heterogeneous-file track: a
direct ingestion audit plus a matched native-agent versus agent-plus-RememberStack comparison.
Regular development uses committed deterministic subsets; full runs are publication events with
an explicit preflight cap. Retrieval and ingestion diagnostics run before any shared reader, task
agent, or judge, and every report separates one-time build cost from serving cost.

The matched memory-backend baseline set is BM25, minimal dense RAG, Mem0 OSS, and Graphiti OSS.
Workspace-Bench instead uses the identical native-filesystem task agent as its control because the
retrieval baselines cannot create benchmark artifacts. DMR is rejected as saturated;
LongMemEval-V2 and the other agent-environment suites remain watch/deferred items. The reusable
prompt for independent external research is
[`phase_8_deep_research_prompt.md`](../analysis/phase_8_deep_research_prompt.md).

## WP-8.2 LoCoMo setup

The current adapter is the reviewed `RS-LoCoMo-Full-v15` protocol. It preserves
the earlier judge, strict-representable `arguments_json`, answer-loop guards,
recoverable identity history, and assumed-UTC ingestion contract while adopting
the D98 live graph and 21-tool catalog (D78 and amendments):

- analysis and comparability limits:
  [`locomo_benchmark_analysis.md`](../analysis/locomo_benchmark_analysis.md);
- binding adapter and pre-run design:
  [`locomo_benchmark_design.md`](../designs/locomo_benchmark_design.md); and
- unshipped repository harness: `benchmarks/locomo/`.

Its smoke, development, and publication manifests pin 8, 200, and 1,540 question IDs. Compose
runs the complete continuous lifecycle and exposes a one-shot P3 build; the graph
is live PostgreSQL. The answer harness verifies exact stage, graph, and P3
readiness and lets a bounded agent choose the complete 21-tool read surface; the
former claims-only J@30 path is not the headline. No real
ingest, query, answer-agent, judge, or score run has occurred. WP-8.2 remains in progress until
the owner reviews the setup and an eight-question smoke completes against an isolated deployment.

## WP-8.2W Workspace-Bench setup

Workspace-Bench is selected because its five role workspaces contain 20,476 files across 74
extensions and its tasks label essential files and dependency edges. It complements LoCoMo's
conversation-only input with office documents, PDFs, spreadsheets, presentations, code and
configuration, email, images, archives, statistical data, and revision-like file lineage.

The binding analysis and adapter handoff are in
[`workspacebench_benchmark_analysis.md`](../analysis/workspacebench_benchmark_analysis.md).
The proposed exact five-task smoke and nested 20-task development slice, with paired execution,
cost formulas, and acceptance gates, are in
[`workspacebench_smoke_subset_analysis.md`](../analysis/workspacebench_smoke_subset_analysis.md).
Implementation must produce two separate artifacts:

- a deterministic ingestion audit by extension/MIME, including unsupported and failed files,
  throughput, provider calls, tokens, and cost; and
- official task results for an identical native-filesystem agent control and a matched arm that
  adds only RememberStack's shipping public read plane.

The proposed smoke IDs are `124, 300, 340, 358, 388`. The nested 20-task development slice covers
four tasks per role, all 16 source extensions used by Lite task manifests, and all 11 requested
output extensions. Publication uses all 100 Lite tasks in both arms. The 388-task full suite is a
separately authorized extended publication run. No executable adapter or committed manifest exists
yet, no dataset has been downloaded into this repository, and no Workspace-Bench agent or judge
call has run. The observed Lite dataset card also needs an explicit license clarification, or an
exact-byte derivation from the Apache-2.0 full task release, before publication.
