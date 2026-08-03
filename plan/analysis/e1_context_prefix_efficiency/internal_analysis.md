# Internal analysis — E1 context-prefix efficiency

**Author:** orchestrator (Grok)  
**Date:** 2026-07-31  
**Status:** non-binding analysis  
**Companion:** `PROBLEM.md`; external agents under `external_agents/`

## 1. Problem restatement

The default E1 path improves conventional embeddings by prepending an
LLM-written **location prefix** to each chunk. That is a good retrieval idea.
The **execution shape** is wrong for large documents: one work item must finish
~N sequential structured LLM calls before any prefix or vector is durable. On
BEAM-scale chats (N≈749) this is slow, expensive to retry, and fragile to a
single provider glitch — exactly what we observed (hundreds of billed prefixes,
zero written rows, DLQ on non-JSON completion).

The quality goal is not “call an LLM N times.” The quality goal is **context-aware
passage vectors + replayable context for E2/hydration**, under D56/A3 economics
on *later* versions.

## 2. Quality goal (keep)

| Need | Why |
|---|---|
| Vectors know “where” the passage sits | Long multi-topic chats otherwise dilute / collide |
| Prefix text is location, not laundered facts | Grounding / review constraint in `_prefix_prompt` |
| Replay/carry-forward | Unchanged chunks must not re-pay LLM (A3) |
| P1 usable in finite time | Benchmarks and product ingest cannot wait on thrash |

**Quality is a property of the embedded text and stored context**, not of the
number of LLM round-trips.

## 3. Root failure modes (current)

1. **Blast radius = document** — one bad completion fails all N.  
2. **No mid-flight durability** — progress only in cost_ledger, not spine.  
3. **Retry ≈ full re-run** on first version (carry-forward empty).  
4. **Provider flakiness amplified by N** — JSON/schema failures scale with calls.  
5. **Asymmetry with E2** — extraction already thinks in per-chunk durable progress; embed does not.  
6. **Max completion tokens** can encourage huge bad completions (observed ~140k content) when the schema only needs a short string.

These are **orchestration and unit-of-work** bugs more than “prefix is a bad idea.”

## 4. Options evaluation

### O1 — Durable per-chunk (or micro-batch) prefix + embed units

**Idea:** Make progress unit ≤ one chunk (or K chunks), with `processing_state`
(or equivalent) committing prefix/embed independently; retries only incomplete
units.

| Dimension | Assessment |
|---|---|
| Quality | **Unchanged** if same prefix model/prompt |
| Cost | Same first-pass; **much lower retry cost** |
| Latency wall-clock | Can parallelize workers later; sequential still OK if durable |
| Flakiness | **Dramatically lower** blast radius |
| D56/A3 | **Fits** — stored prefixes still carry forward |
| Risk | Medium — ledger/stage graph change; must not break single-version semantics |

**Verdict:** Necessary hygiene regardless of other choices. Closest cousin to E2’s
per-chunk commit discipline. Strong **primary near-term + long-term** candidate.

### O2 — Hierarchical / section-level prefix

**Idea:** One LLM call per section (or path), chunks under that section share or
compose a deterministic template:  
`prefix = f"{section_prefix} · chunk {ordinal} of {n}"` or inherit only.

| Dimension | Assessment |
|---|---|
| Quality | **Likely good enough** for chats; papers with long homogeneous sections may need a light per-chunk tweak |
| Cost | O(sections) ≪ O(chunks) — often 10–50× reduction |
| Latency | Much shorter |
| Flakiness | Fewer calls → fewer failures |
| D56/A3 | Carry section prefixes + deterministic child materialization |
| Risk | Low–medium; need policy when section tree is flat/weak (chat batches) |

**Verdict:** Best **quality-preserving cost cut** for multi-section docs. For BEAM
chats, section tree may be weak → combine with O3 for leaves.

### O3 — Deterministic template prefix (no LLM)

**Idea:**  
`"{title} / {section_path} / ordinal={i} role={role}"` (+ optional neighbor titles).

| Dimension | Assessment |
|---|---|
| Quality | **Regression risk** vs LLM “where this sits”; may still beat naked chunks |
| Cost/latency/flaky | **Best** (zero LLM) |
| D56/A3 | Trivial carry-forward (deterministic) |
| Risk | Low; measure on LoCoMo/BEAM retrieval |

**Verdict:** Excellent **fallback** and possibly default for `source_kind=beam|chat`
until measured. Not the only long-term answer if papers still benefit from LLM
location language.

### O4 — Contextual embedder (D63 alternate)

**Idea:** Switch deployment embedder; **delete prefix stage**.

| Dimension | Assessment |
|---|---|
| Quality | Potentially **best** if model is strong; needs golden-set validation |
| Cost | Moves $ to embedding API; may be cheaper than N chat completions |
| Flakiness | Fewer moving parts |
| Fit | Already designed as alternate branch |
| Risk | Product/ops: model availability, dim migration, re-embed |

**Verdict:** Correct long-term **branch**, not the fastest BEAM unblock unless the
model is already wired and budgeted.

### O5 — Hardening only (max_tokens, JSON repair, cheaper model, retries)

| Dimension | Assessment |
|---|---|
| Quality | Same architecture |
| Flakiness | Helps at margins; **does not fix** O(N) sequential + all-or-nothing |
| Risk | Low |

**Verdict:** **Do immediately** as hygiene (cap tokens for `ContextPrefix`, prefer
json_schema strict, short system constraint), but **not sufficient alone**.

### O6 — Two-phase embed (fast path then upgrade)

**Idea:** Phase A: template/naked embed → P1 live. Phase B: prefix-upgrade re-embed
when durable prefixes exist.

| Dimension | Assessment |
|---|---|
| Quality | Temporary lower quality; **needs version stamps** so recipes know embedding generation |
| Unblock | **Excellent** for harnesses |
| Risk | Medium — dual vectors / stale recipe confusion if not versioned |

**Verdict:** Strong **product/bench pragmatism** if combined with O1. Avoid dual
silent vectors without `embedding_version` / prefixer generation visibility.

### O7 — Multi-chunk batch prefix (one LLM call → N prefixes)

Like E2 batching.

| Dimension | Assessment |
|---|---|
| Quality | OK if batch small and schema lists N ids |
| Cost | Fewer round-trips; similar tokens |
| Flakiness | One failure still loses batch — **unless** partial parse + durable commit per id |
| Risk | Prompt packing, truncation |

**Verdict:** Good **with O1** (batch attempt, per-chunk commit). Bad alone if batch
is all-or-nothing.

### O8 — Source-kind policy (chat vs paper)

Conversations: deterministic or section-batch prefixes. Papers: hierarchical LLM
or full per-chunk if N small.

**Verdict:** High leverage for BEAM without harming paper quality path.

### O9 — Checkpoint flush every K (same work row)

Better than nothing; still awkward with ledger “running” semantics. Prefer true
per-unit work (O1) over checkpointing a monolith.

## 5. Recommendation (internal)

### Primary architecture (keep quality, fix flakiness)

**A. Change the unit of work (O1) — non-negotiable.**  
`embed_chunk` should not mean “all prefixes for the document.” Prefer:

- `prefix_chunk` (or batched K) durable, then  
- `embed_chunk` for chunks that have prefixes (or template),  

**or** one stage with **per-chunk processing rows** like E2.

Until a prefix is committed, retry must not re-bill completed chunk_ids
(replay from stored prefix — already coded in `_resolve_prefix` *if* rows exist).

### B. Reduce LLM count without killing context (O2 + O3 policy)

Default policy proposal (analysis-only):

| Source shape | Prefix strategy |
|---|---|
| Weak/no section tree, chat/transcript, N large | **Deterministic template** (O3) |
| Rich section tree, N large | **Section-level LLM prefix** + deterministic per-chunk composition (O2) |
| Small N (e.g. &lt; 32) | Optional full per-chunk LLM (today’s quality max) |

This preserves A3 (store whatever was used), cuts O(N) LLM, and keeps
location signal in the embedded string.

### C. Hygiene (O5) always

- Tight `max_tokens` for prefix calls (hundreds, not 32k).  
- Fail fast on oversized content.  
- Reasoning effort `none` for prefix model.  
- Structured output only; no prose fence tolerance without repair.

### D. Optional later

- Contextual embedder (O4) as deployment flag when validated.  
- Two-phase embed (O6) if product needs P1 before enrichment.

### Do not

- Delete prefixes globally without measurement or contextual embedder.  
- Put LLM prefix text into D56 identity keys.  
- Keep all-or-nothing document-level prefix loops and “hope OpenRouter holds.”

## 6. Near-term BEAM unblock (this week)

1. **O5** caps + model settings (hours).  
2. **Implementation spike O1-lite:** after each successful `_resolve_prefix`,  
   `UPDATE chunks SET context_prefix=...` (or batch every 1–8) **before** next call;  
   on retry, existing `_resolve_prefix` short-circuit kicks in. Even without new
   stages, this alone stops thrash.  
3. If still too slow/costly: **O3 for `source_kind=beam`** (and chat) for smoke,  
   measure P1 recipe quality vs bare.  
4. Only then climb BEAM scales.

O1-lite is the highest ROI: **same quality, non-flaky retries**, modest code change.

## 7. Spike plan

| Spike | Success criterion |
|---|---|
| S1 Checkpoint prefixes | Kill worker mid-doc; resume completes without re-billing finished chunks |
| S2 Template vs LLM on BEAM-100K subset | claims_hybrid_rrf / verbatim recall on 4–10 probes — Δ documented |
| S3 Section-level prefix | Call count ≤ 2× section count; quality ≥ template |

## 8. Binding design impact

| Change | Binding? |
|---|---|
| Checkpoint / per-chunk durable prefix in worker | **Implementation** if contracts stay (prefix stored, A3) — note in e1 as refinement of execution, not quality model |
| New stages / processing graph | **Orchestration design** touch |
| Default policy by source_kind | **e1 design amendment** (small) if it becomes product default |
| Contextual embedder default | **D63 / e1** already allows; switching default is a decision |
| Dropping prefix for conventional embedder | **Design decision** — needs measurement |

## 9. Executive recommendation

1. Treat flakiness as **missing durability of prefix work**, not “LLM prefixes are wrong.”  
2. **Ship O1-lite checkpointing immediately** so retries are incremental.  
3. **Cut call count** via section-level or deterministic prefixes for large/chat corpora.  
4. Keep conventional+prefix quality story; harden provider limits.  
5. Evaluate contextual embedder as the clean long-term delete-prefix branch.  
6. Do not block BEAM on perfect paper-grade per-chunk poetry.
