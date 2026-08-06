# RS-Harness-BEAM-v1 — Claude Code ± full RememberStack

**Status:** binding strategy (Track C product harness A/B).  
**Date:** 2026-07-31  
**Supersedes for primary dataset:** LongMemEval-first framing in  
`design/benchmarks/rs-harness-longmemeval/` (that path remains a wire-up note only).

## 1. Goal

Show that **Claude Code** is more effective when it can use **RememberStack end-to-end**
than when it cannot — especially when conversation scale is large enough that
context stuffing and naive harness exploration fail.

**Primary dataset: BEAM** ([ICLR 2026](https://github.com/mohammadtavakoli78/BEAM)),
**subsets only**, staged by scale. Hero tier: **10M-token class** histories.

```text
same Claude Code · same model · same BEAM probes
  arm bare  = no RS
  arm rs    = full RS product surface (not claims-only)
```

## 2. Why BEAM (and why 10M matters)

BEAM conversations are built at **128K / 500K / 1M / 10M** token scales with
~2,000 validated probes across **10 memory abilities** (incl. contradiction,
knowledge update, abstention, temporal order).

At 10M tokens:

- stuffing the transcript into Claude Code is impractical;
- harness agents thrash (partial reads, lost threads, compaction);
- external memory is a **necessity**, not a polish.

**We only run subsets** (fixed item manifests). Never “all of BEAM” as day-one.

## 3. Scale stages (all subsets)

| Stage | Scale bucket | Items (order of magnitude) | Purpose |
|---|---|---:|---|
| **S0 smoke** | smallest available BEAM bucket that downloads cleanly (prefer 128K; if only larger exists, 1 convo × few probes) | 4–8 probes | MCP + mounts + K + score path |
| **S1 wire** | 128K or 500K | ~12–20 | Stable CC A/B |
| **S2 real** | **1M** | ~20–40 stratified | First serious Δ |
| **S3 hero** | **10M** | ~10–30 stratified on 1–few convos | Product claim: harness fails without RS |

Promotion requires a green previous stage (surfaces work; `rs` arm actually calls tools / reads mounts).

## 4. Protocol identity

```text
protocol                 RS-Harness-BEAM-v1
harness                  Claude Code (local Max OK for agent turns)
dataset                  BEAM (pin git/HF revision at prepare)
scale                    recorded per run: 128k | 500k | 1m | 10m
arms                     bare | rs
primary metric           answer accuracy (BEAM scorer if present; else frozen judge)
secondary                turns, wall time, tokens if available,
                         MCP call counts by recipe family,
                         P3/K path reads if loggable
```

## 5. Full RS surface (do not omit P1/P2)

Earlier drafts over-emphasized “MCP + P3 + K1”. That was incomplete packaging,
not a design to skip search or graph.

### 5.1 What the `rs` arm must expose

| Layer | What it is | How Claude Code reaches it |
|---|---|---|
| **E** | Ingested evidence pipeline (claims, relations, …) | Feeds everything below |
| **P1** | Search indexes (Lance, hybrid retrieval) | **MCP recipes** e.g. `claims_hybrid_rrf`, `claims_verbatim`, related search recipes |
| **P2** | Knowledge graph | **MCP recipes** e.g. `graph_neighborhood`, graph path recipes; not a primary “files” mount |
| **P3** | Corpus filesystem | **Read-only mount** (`mounts/p3`) — `ls` / read / grep |
| **K / K1** | Compiled knowledge pages | **Read-only mount** (`mounts/k`) + MCP `pages_about` |
| **Skill** | Consumption curriculum | `.claude/skills/rememberstack/SKILL.md` |

**P1 and P2 are not ignored** — they are accessed through the **same MCP recipe
registry** as any other query surface. There is usually no separate “P1 mount”
because P1 is an index, not a document tree. Calling only “P3 + K” without MCP
search/graph would be a broken product demo.

### 5.2 Required skill motion (rs arm)

1. **Orient** — K1 pages / `pages_about`  
2. **Navigate** — P3 tree when useful  
3. **Retrieve** — P1 via hybrid/claims recipes  
4. **Relate** — P2 via graph recipes when multi-entity  
5. **Verify / audit** — fact grain then evidence hydration  
6. **Answer** — short factual line (`ANSWER: …`)

A valid `rs` run must show **non-zero** use of retrieval surfaces (MCP and/or
mounts). Zero MCP and zero mount reads ⇒ arm invalid.

### 5.3 Build checklist before `rs` scoring

1. Histories ingested; continuous **E → P1** workers drained  
2. **P2** projection built  
3. **P3** projection built and mounted  
4. **K1** compiled (or explicit known-empty with owner waiver — default **fail** if zero pages after compile attempt)  
5. Mounts + MCP + skill published into CC workspace  

## 6. Arms

### `bare`

- No RememberStack MCP  
- No P3/K mounts  
- Question (+ fixed preamble) only — **no** history dump  
- Expect failure / Unknown / thrash at large scales  

### `rs`

- Full surface §5  
- History only inside RS (not pasted)  
- Same CC model and budgets as `bare`  

## 7. Fairness

- Same Claude Code version + model string  
- Same step/time budget if enforced  
- Same judge  
- Fixed probe list (manifest); never resample after seeing scores  
- Report scale bucket + item ids + deployment revision + recipe catalog hash  

## 8. Claims

✅ “On a BEAM-{scale} **subset**, Claude Code + full RS (P1/P2 via MCP, P3+K mounts)
outperformed Claude Code alone under `RS-Harness-BEAM-v1`.”

❌ Not BEAM vendor SOTA unless competitors re-run under this harness.  
❌ Not interchangeable with LoCoMo Full-v5 API scores.

## 9. Repo layout

| Path | Role |
|---|---|
| `design/benchmarks/rs-harness-beam/STRATEGY.md` | this document |
| `design/benchmarks/rs-harness-beam/RUNBOOK.md` | operator steps |
| `benchmarks/rs_harness_beam/` | prepare / ingest / run_cc / score (BEAM-native) |

Legacy LongMemEval harness under `rs_harness_longmemeval` may be reused as
implementation reference only.

## 10. Immediate execution plan

1. Pin BEAM checkout; inventory which scale files exist locally.  
2. Build **smoke subset** (smallest scale, few probes, stratified abilities).  
3. Prepare run dir + CC workspaces (bare / rs).  
4. Bring up Compose; ingest **one** conversation at smoke scale.  
5. Drain E/P1; build **P2+P3**; compile **K1**; publish mounts + MCP + skill.  
6. `claude -p` bare vs rs on smoke probes.  
7. Only then climb S2/S3 subsets toward **10M**.
