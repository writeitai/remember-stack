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
| WP-8.3 | Baselines: Mem0 OSS + Graphiti OSS from the survey, plus BM25 and dense-RAG floors; hosted/vendor numbers are contextual only | WP-8.1 memo | WP-8.2 | baseline runs | reproducible baseline numbers | planned |
| WP-8.4 | Metrics + instrumentation: accuracy per suite, latency (P50/P95), token + $ cost per op (cost_ledger), ingestion throughput | schema §2; retrieval §10 | WP-8.2 | metrics pipeline | one reproducible metrics artifact per run | planned |
| WP-8.5 | **Capability benchmark** (ours, from the S-battery): the differentiators competitors lack — bi-temporal as-of (S9/S10/S15), contradiction surfacing (S23), provenance hydration (S5), watched-source lifecycle (edit/retract/delete), forget (S55) | retrieval_scenarios.md | WP-8.2 | capability suite + narrative doc | each capability demonstrated + scripted | planned |
| WP-8.6 | Methodology + results publication (honest: include losses; pin versions; publish configs) | all above | WP-8.3–8.5 | report | reviewed; reproducible by a third party | planned |

## WP-8.1 selection

The current landscape survey and binding handoff are in
[`phase_8_benchmark_selection.md`](../analysis/phase_8_benchmark_selection.md). The initial
portfolio is LoCoMo QA, LongMemEval-S, MemoryAgentBench FactConsolidation-SH/MH, and
MultiHop-RAG retrieval. Regular development uses committed deterministic subsets; full runs are
publication events with an explicit preflight cap. Retrieval scoring runs before any shared
reader or judge, and every report separates one-time build cost from serving cost.

The matched baseline set is BM25, minimal dense RAG, Mem0 OSS, and Graphiti OSS. DMR is rejected
as saturated; LongMemEval-V2 and the agent-environment suites remain watch/deferred items rather
than expanding WP-8.2. The reusable prompt for independent external research is
[`phase_8_deep_research_prompt.md`](../analysis/phase_8_deep_research_prompt.md).

## WP-8.2 LoCoMo setup

The first adapter is the reviewed `RS-LoCoMo-Full-v3` protocol (v1 with a stronger judge; D78 amendment 2026-07-24):

- analysis and comparability limits:
  [`locomo_benchmark_analysis.md`](../analysis/locomo_benchmark_analysis.md);
- binding adapter and pre-run design:
  [`locomo_benchmark_design.md`](../designs/locomo_benchmark_design.md); and
- unshipped repository harness: `benchmarks/locomo/`.

Its smoke, development, and publication manifests pin 8, 200, and 1,540 question IDs. Compose
now runs the complete ten-route continuous lifecycle and exposes a one-shot P2/P3 build. The
answer harness verifies exact stage/projection readiness and lets a bounded agent choose the
ordinary public recipe tools; the former claims-only J@30 path is not the headline. No real
ingest, query, answer-agent, judge, or score run has occurred. WP-8.2 remains in progress until
the owner reviews the setup and an eight-question smoke completes against an isolated deployment.
