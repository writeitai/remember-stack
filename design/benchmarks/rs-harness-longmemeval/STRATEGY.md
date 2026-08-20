# RS-Harness-MemEval-v1 — Claude Code ± RememberStack

**Status:** binding strategy for the product harness A/B (Track C).  
**Date:** 2026-07-30  
**Worktree:** `ugm_3` · branch `feat/cc-rs-longmemeval-harness`

## 1. Goal

Show that **Claude Code** (the product people use) answers long-horizon memory
questions **better and/or cheaper** when RememberStack is available than when it
is not.

This is **not**:

- an API mini-agent (`RS-LoCoMo-Full-v5`);
- Mem2Act tool-call JSON;
- STATE-Bench Azure sim/judge;
- a coding/SWE resolution claim.

It **is** a Grep-study-shaped harness ablation:

> same Claude Code · same model · same questions · **no RS** vs **RS MCP + mounts**

## 2. Why LongMemEval-S (first), not BEAM-10M

| Stage | Dataset | Why |
|---|---|---|
| **v1 (now)** | **LongMemEval-S** subset (n=20 smoke → n=50 dev) | Runnable, known from “Is Grep All You Need?”, ~115k-token histories, abilities include update + abstention |
| **v1.1** | LongMemEval-S full 500 | If Δ is real |
| **v2** | BEAM-1M stratified subset | Harder than stuffing; only after v1 works |
| **v3 (optional)** | BEAM-10M slice | Capstone cost; not required to ship the story |

LoCoMo is continuity-only if needed later (contamination / short context caveats).

## 3. Protocol identity

```text
protocol                 RS-Harness-MemEval-v1
harness                  Claude Code (CLI, local Max subscription)
backbone                 whatever CC session model is pinned (record exact id)
dataset                  LongMemEval-S cleaned (pin HF revision when downloaded)
arms                     bare | rs
surfaces (rs arm)        MCP recipes + P3 corpus mount + Plane-K mount (K1)
primary metric           answer accuracy (official LongMemEval scorer if available;
                         else frozen LLM judge with fixed prompt)
secondary                turns, wall time, tool-call counts, RS recipe/MCP call rate
```

A change to mounts, skill, recipe catalog, or CC model creates a new protocol
fingerprint (or explicit version bump).

## 4. Arms

### 4.1 `bare` — Claude Code without RememberStack

- No `remember` MCP server.
- No P3 / K mounts.
- No paste of the full history into the user message beyond what the protocol
  allows (default: **question only** + short system skill that forbids inventing
  history).
- Fresh CC session (or explicit isolation) per item or per batch as recorded.

### 4.2 `rs` — Claude Code + RememberStack

- Deployment has ingested the item’s history (one document stream per history).
- Continuous E/P1 pipeline drained; **P2/P3 built**; **Plane K (K1) compiled**
  for in-scope pages; mounts published.
- Claude Code has:
  1. **MCP** `remember mcp` → all public recipes (search, graph, pages_about, …);
  2. **Filesystem mounts** for **P3 corpusfs** and **Plane K** (read-only);
  3. Generated **consumption skill** (`SKILL.md`) teaching orient → verify → audit.
- History is **not** dumped into the prompt; agent must use mounts/MCP.

## 5. Required product surfaces (explicit)

The owner requires that the `rs` arm is not “claims search only”:

| Surface | Role in this protocol |
|---|---|
| **Plane K / K1** | Compiled knowledge pages for orientation; agent must be able to open K paths or `pages_about` |
| **P3 file layer** | Navigable corpus tree (`_index.md`, stable paths); agent may `ls` / read / grep |
| **MCP recipes** | Semantic/graph/time-travel ops files cannot do |
| **Consumption skill** | Cold-start curriculum for Claude Code |

If K1 is empty after compile, the skill must say so honestly; the run records
`k_page_count` and whether orientation fell back to P3. A run that claims
“K available” with zero pages is invalid.

## 6. Execution tiers

| Tier | Items | Purpose |
|---|---:|---|
| smoke | 8–12 stratified by ability | Wire MCP, mounts, K, scoring |
| development | 50 | Primary effect-size estimate |
| publication | 500 (full S) or BEAM-1M later | Deliberate campaign |

Stratify LongMemEval abilities: extraction, multi-session, temporal, knowledge
update, abstention.

## 7. Local Max-subscription path

Claude Code Max OAuth **can** drive this path (unlike STATE-Bench’s API client).

```text
1. docker compose up (local or Hetzner for heavy workers)
2. ingest LongMemEval histories for selected item ids
3. drain pipeline → project P2+P3 → compile K1 → publish mounts
4. write .claude/mcp.json + mount paths into the harness workspace
5. for each arm × item:
     claude -p "<fixed prompt + question>"  (or interactive scripted session)
6. collect answer text + optional tool logs
7. score
```

**RS pipeline cost** uses OpenRouter (or configured providers) for extract/K.  
**Claude Code turns** bill against Max for the agent loop.

## 8. Fairness contract

1. Same Claude Code version and model string on both arms.  
2. Same per-item wall/time budget if enforced.  
3. Same fixed user prompt template (only skill/MCP differ).  
4. No gold answer or evidence IDs in context.  
5. Failures count as wrong.  
6. Report RS tool call rate on `rs` arm (if zero, the arm is invalid).  
7. Report mount use (reads under P3/K paths) when loggable.

## 9. What we will claim

✅ “On LongMemEval-S subset, Claude Code + RememberStack (MCP + P3 + K1) scored
higher / used fewer turns than Claude Code alone under protocol
`RS-Harness-MemEval-v1`.”

❌ Not “SOTA vs Mem0 on published tables” unless we re-run Mem0 under this
protocol.  
❌ Not interchangeable with `RS-LoCoMo-Full-v5` numbers.

## 10. Deliverables in-repo

| Path | Purpose |
|---|---|
| `design/benchmarks/rs-harness-longmemeval/STRATEGY.md` | this document |
| `design/benchmarks/rs-harness-longmemeval/RUNBOOK.md` | operator steps |
| `benchmarks/rs_harness_longmemeval/` | manifests, prepare, score helpers, CC wrappers |
| `.benchmark-runs/…` | local run artifacts (gitignored) |

## 11. Sequencing

1. Write strategy + runbook + harness skeleton.  
2. Download/pin LongMemEval-S; commit smoke item ids.  
3. Bring up local Compose; smoke-ingest one history.  
4. Build P3; compile K1; publish mounts; render skill.  
5. Configure Claude Code MCP + mounts for a workspace.  
6. Run smoke: 8 items × {bare, rs}.  
7. Score and write results note under `design/benchmarks/…/results/`.  
8. Expand to n=50 if smoke is clean.

## 12. Risks

| Risk | Mitigation |
|---|---|
| K compile not in default compose continuous path | Explicit one-shot K driver after ingest; record page count |
| CC ignores MCP | Fixed skill + prompt requiring tool use; fail if rs arm has 0 MCP calls |
| Harness dominates memory (Grep paper) | Freeze CC; only toggle RS surfaces |
| Cost of 50× histories | Smoke first; Hetzner for workers optional |
| Judge noise | Prefer official LongMemEval scorer when available |
