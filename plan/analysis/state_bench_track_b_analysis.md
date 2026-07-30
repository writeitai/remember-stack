# Track B — STATE-Bench Agent Learning Track analysis

**Survey / design date:** 2026-07-30  
**Status:** non-binding analysis for WP-8.7 (Track B)  
**Scope:** first *agent + memory* product claim, parallel to Track A (WP-8.1–8.6).

## Decision under analysis

Which external suite should prove:

> Fixed agent harnesses do a much better job when they have access to RememberStack.

This is **Track B** (memory-augmented harness quality). It is complementary to **Track A**
(memory-backend quality on LoCoMo / LongMemEval-S / FactConsolidation / MultiHop-RAG).

## Recommendation

**Primary:** [STATE-Bench](https://github.com/microsoft/STATE-Bench) **Agent Learning Track**
at pin `4efcbf2` / `v0.8.1` (MIT).

**Runner-up / fallback:** Mem2ActBench (cheaper tool-call gold; thinner harness).  
**Later:** MemoryArena (online multi-session write→act; preview substrate today).

Independent reviews (Claude Opus + Codex, 2026-07-30) both selected STATE-Bench as primary.
See binding design: [`state_bench_benchmark_design.md`](../designs/state_bench_benchmark_design.md).

## Why STATE-Bench fits Track B

| Property | Evidence |
|---|---|
| Fixed harness | Same simulator, domain tools, judge, metrics as Main Track |
| Explicit memory seam | `retrieve_learnings(query, top_k=3) -> list[str]` |
| No-memory control | Main Track / empty hook on identical tasks → Δ is the product claim |
| Reliability | Official `pass@1` and `pass^5` (five runs) |
| Parallel eval | `run_batch --num-workers` (thread pool over tasks) |
| License | MIT |
| Standing | Microsoft OSS; public leaderboard; enterprise domains |

## What it does *not* prove

- Bi-temporal as-of, contradiction co-members, watched edit/retract/delete, hard-forget  
  → remain WP-8.5.  
- Online multi-session personal memory write loops → MemoryArena later.  
- Ordinary conversational LoCoMo scores → Track A.

Agent Learning learnings are **procedural experience from train trajectories**, not
user-profile memory. Extraction is **user-owned**; without a frozen write policy the
extractor confounds the backend comparison.

## Shared vs native write (critical)

Two sub-protocols must never be mixed in one table:

| Sub-protocol | Write path | What it isolates |
|---|---|---|
| **`-shared`** | One frozen serializer produces identical learning documents for every backend | Retrieval / organization under fixed units |
| **`-native`** | Each system’s ordinary write path over the same raw trajectories | Product-realistic ingest (where RS pipeline can help) |

Reporting only `-native` confounds extraction with memory quality.  
Reporting only `-shared` measures a retriever. Both, labeled, are required for a decision-useful claim.

## Parallelism (speed)

STATE-Bench evaluation is inject-once / query-many: train is offline, test tasks are independent.

| Layer | Mechanism | Notes |
|---|---|---|
| L1 task workers | STATE `--num-workers` | Within one domain/run; start ~8–16; back off on rate limits |
| L2 domains | 3 processes | `travel` / `customer_support` / `shopping_assistant` independent |
| L3 arms | Matrix of arms | Empty/BM25/dense need no RS stack; RS needs one deployment per domain (shared by workers) |
| L4 hosts | Optional multi-host | Partition domains or task subsets; merge metrics offline |

Unlike LoCoMo publication (one deployment per conversation, wipe volumes between samples),
STATE RS arms **reuse one deployment per domain** for all test tasks after a single train ingest.
That is the main speed win versus LoCoMo sharding.

Provider ceilings (OpenAI/Azure locked sim/judge + agent model) dominate wall time, not
RememberStack CPU. Do not co-schedule publication Track A and Track B against one account cap.

## Cost envelope (order of magnitude, not a quote)

Episodes ≈ multi-turn agent + simulator + judge. Rough pub shape:

- 3 domains × 50 tasks × 5 runs × N arms  
- Preflight must project USD/tokens per arm and refuse uncapped starts  
- Smoke: 1 domain × 5 tasks × 1 run × {empty, RS}  
- Dev: 1–3 domains × 15 tasks × 1–2 runs × all matched arms  

## Kill criteria (dev tier)

1. Full-context ceiling fails to beat empty by ≥5pp pass@1 → suite/agent config not memory-sensitive.  
2. RS − BM25 < 2pp across two domains under `-shared` → pivot to MemoryArena or fix rendering.  
3. Memory tool called on <30% of tasks → forced-prefetch diagnostic once; if still no lift, stop.  
4. Locked GPT-5.4 sim/judge unavailable → do not invent substitutes; fall back to Mem2ActBench.  
5. Publication claim “much better” requires pre-registered bar (design): ≥+8pp pass@1 vs empty,
   CI excluding zero, lift in ≥2 domains, no material pass^5 regression.

## Sources (retrieved 2026-07-30)

- https://github.com/microsoft/STATE-Bench  
- https://github.com/microsoft/STATE-Bench/blob/main/docs/AGENT_LEARNING_TRACK.md  
- https://github.com/microsoft/STATE-Bench/blob/main/docs/memory/builtin-hook.md  
- https://github.com/microsoft/STATE-Bench/blob/main/docs/eval/run-batch.md  
- https://opensource.microsoft.com/blog/2026/05/19/introducing-state-bench-a-benchmark-for-ai-agent-memory/  
- Local pin: commit `4efcbf2d4fe60df04878859b692d9391f3d5b33a`, package `v0.8.1`
