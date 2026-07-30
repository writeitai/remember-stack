# Track B — Mem2ActBench analysis

**Date:** 2026-07-30  
**Status:** non-binding analysis for WP-8.7b (replaces STATE-Bench as first Track B bring-up)  
**Upstream pin:** `Cantaloupe-M/Mem2ActBench` @ `b00726940b5abbe9bd324bdd7a2cb272f5c62a29`

## Why switch from STATE-Bench

STATE-Bench Agent Learning Track is a clean empty→memory A/B, but:

- official sim/judge lock to **Azure GPT-5.4**;
- public leaderboard is nearly empty (Microsoft-only rows on old 0.4.4);
- cost and credential friction dominate before any product signal.

Mem2ActBench (ACL 2026 long) measures **memory → grounded tool call**:
given a long multi-topic session, produce the correct tool name + parameters.
Gold labels are **deterministic** (no LLM judge). Paper reports weak active
memory use across frameworks; Mem0-style systems score poorly on related
memory→action tables the owner cares about.

## What the released artifact is

Upstream ships a **dataset construction pipeline** plus released files under
`Mem2ActBench/`:

| File | Role |
|---|---|
| `toolmem_conversation.jsonl` | 2,029 long sessions (avg ~12.7 turns, ~3.2k tokens) |
| `qa_dataset.jsonl` | **400** evaluation items (query + gold `tool_call`) |
| `benchmark_statistics.json` | corpus stats |

There is **no** official multi-backend runner for Mem0/Graphiti/RS. We own a thin
adapter that:

1. resolves each QA item to a session (source-id subset match);
2. ingests session turns into the system under test (or not);
3. asks one frozen reader model for a single tool call;
4. scores with deterministic name + argument matchers.

## Resolution coverage

At pin `b007269`, **323 / 400** QA items resolve to a unique session via
`source_conversation_ids ⊆ session.original_conversation_ids` (prefer lowest
`token_count` on ties). **77** items do not resolve cleanly and are **excluded**
from committed manifests until linkage is fixed upstream or by a stronger
resolver. Publication tier is therefore **323** resolved items, not 400.

Level mix among resolved items: L1 231, L2 88, L3 2, L4 2.

## Arms (budget plan)

| Arm | Meaning |
|---|---|
| `empty` | Reader sees **query + tool schema only** (no history, no RS) |
| `rememberstack` | Ingest full session turns into RS; retrieve; reader sees query + schema + retrieved strings |
| `full_context` | Ceiling: reader sees full session transcript (diagnostic, not matched “memory system”) |

**Mem0 is deferred** as a later optional competitor arm when budget allows — not
required to prove empty→RS. The owner’s interest in Mem0 weakness is
**narrative**, not a day-one dependency.

## Cost shape (why this is cheaper than STATE)

- No multi-turn user simulator.  
- No locked Azure GPT-5.4 judge.  
- One structured completion per item (tool call JSON).  
- RS cost = one-time session ingest (amortized if many QA share a session) + cheap retrieval.

Smoke (12 items × empty + RS) is a handful of reader calls plus RS ingest of a
small session set.

## Risks

1. **Incomplete official harness** — we implement scoring; paper metrics may differ in edge cases.  
2. **77 unresolved QA** — do not claim full 400 until resolved.  
3. **L3/L4 almost empty** in resolved set — multi-hop claims are weak.  
4. **License** — derived ToolACE/BFCL/OASST1 material; MIT claimed on README path, verify before commercial use.  
5. **Reader model confounds memory** — freeze one reader across arms (same as LoCoMo answer agent).

## Decision

Adopt **RS-Mem2Act-v1** as first Track B suite for RememberStack. STATE-Bench
moves to deferred/watch. Design: `plan/designs/mem2act_benchmark_design.md`.
