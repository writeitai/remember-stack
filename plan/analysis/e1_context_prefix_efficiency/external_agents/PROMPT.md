# Independent analysis task — E1 context-prefix efficiency

You are analyzing RememberStack (repo root: current cwd is the engine repo
`ugm_3/ugm` or the path given in the shell). **This is analysis only — do not
change binding designs or production code.** Write a single self-contained
markdown report.

## Output path

Write your full report to:

`plan/analysis/e1_context_prefix_efficiency/external_agents/<YOUR_AGENT>.md`

where `<YOUR_AGENT>` is `claude` or `codex` as appropriate for this run.

## Required reading (in order)

1. `plan/analysis/e1_context_prefix_efficiency/PROBLEM.md` — problem frame  
2. `plan/designs/e1_chunks_design.md` — especially §5 (embedding branch / prefix), §7 A3 (carry-forward)  
3. `src/rememberstack/workers/e1.py` — `EmbedChunkHandler`, `_resolve_prefix`, `_prefix_prompt`  
4. `src/rememberstack/adapters/openrouter.py` — structured JSON completion failure modes  
5. Skim how E2 batches/commits progress for contrast (claims extraction workers)

## Question

**What is the best way to keep (or improve) retrieval quality of context-aware
chunk embeddings, while making prefix generation efficient and non-flaky on
large first-ingest documents (hundreds–thousands of chunks)?**

Cover both:

- **Near-term** (unblock BEAM / long chat ingest this week)  
- **Architectural** (durable design shape that should win long-term)

## Report structure (use these headings)

1. **Problem restatement** (in your own words; cold reader)  
2. **Quality goal** — what the prefix is *for*; what “good enough” means  
3. **Failure modes of current design** (latency, cost, blast radius, durability, provider flakiness)  
4. **Options** — evaluate at least: durable per-unit work, hierarchical/section prefix, deterministic template, contextual embedder, model/protocol hardening, two-phase embed, multi-chunk batch prefix, checkpointed flush. Add options if better. For each: quality, cost, latency, flakiness, D56/A3 fit, implementation risk  
5. **Recommendation** — ranked primary + secondary; explicit “do not do”  
6. **Spike plan** — smallest experiment to validate the primary recommendation  
7. **Binding design impact** — what would need a design amendment vs pure implementation  
8. **Open questions**

## Rules

- Prefer the simplest design that preserves quality; complexity must justify itself.  
- Cite concrete paths/symbols when claiming how the code works.  
- Distinguish **analysis** from **binding** — you are not deciding D63 or rewriting e1 design.  
- Do not invent external product claims without saying they are hypotheses.  
- End with a short **executive recommendation** (≤8 lines).
