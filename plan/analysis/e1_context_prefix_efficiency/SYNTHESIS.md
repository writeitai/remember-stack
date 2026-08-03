# Synthesis — E1 context-prefix efficiency

**Date:** 2026-08-03  
**Status:** non-binding analysis (converged recommendation)  
**Corpus:**

| Document | Role |
|---|---|
| `PROBLEM.md` | Shared problem frame |
| `internal_analysis.md` | Orchestrator (Grok) |
| `external_agents/claude.md` | Claude Code independent (xhigh) |
| `external_agents/codex.md` | Codex independent (xhigh) |

**Not binding design.** Implementation may proceed for pure hygiene; default policy
changes (deterministic prefixes as shipped default, work-graph redesign) need an
e1 / D63 amendment after measurement.

---

## 1. Convergence (all three analyses)

### Agreed diagnosis

1. **Prefix is a good quality idea** for conventional embedders: location signal
   prepended to the chunk before embedding.
2. **The failure is execution shape**, not the idea:
   - one document-level `embed_chunk` work item;
   - **all** prefixes generated before any write;
   - sequential LLM calls at O(chunks);
   - one bad completion → full retry from chunk 1 on first version.
3. **`_resolve_prefix` already short-circuits** on stored `context_prefix` — the
   replay hook exists; the **writer that would feed it mid-flight does not**.
4. **E2 (and D79 section summary) already treat similar multi-call work as
   durable / degradable** — E1 is the outlier.
5. **Provider hygiene is mandatory but insufficient alone**: cap tokens,
   no/minimal reasoning, reject giant “prefix” strings; do not rely on
   infinite document-level retries.
6. **Do not** put LLM prefix bytes into D56 identity keys; **do not** silently
   ship bare-chunk embeddings as the new default without measurement.

### Agreed near-term package (this week / BEAM unblock)

Ranked composition — **all pure implementation** unless noted:

| # | Change | Why all three want it |
|---|---|---|
| 1 | **Checkpoint prefixes** (write `context_prefix` + `prefixer_version` as each unit succeeds; batch flush every ~16–32 OK) | Same quality; retry = unfinished only |
| 2 | **Protocol hardening** — per-request low `max_tokens`, `reasoning_effort=none`, flash-class seat if available, hard max prefix length | Stops the observed ~140k non-JSON / max_tokens-class DLQ |
| 3 | **Bounded concurrency** (~8) for independent prefix calls | Wall-clock; pattern exists in `e0_summary.py` |
| 4 | **Sub-batch embeddings** (~64–128) with unique `call_key`s and per-batch commit | Avoids next all-or-nothing failure after prefixes |
| 5 | **Measured template arm** (title / section path / role / ordinal / turn meta) for BEAM A/B | May remove LLM entirely for chat-scale docs |

### Agreed long-term ranking

1. **If measurement shows deterministic location templates ≥ LLM prefixes** on
   passage recall → amend design and **default to templates** for conventional
   embedders (simplest durable win).
2. **Else hierarchical / window descriptors** (one LLM call per section or
   bounded chat window + deterministic per-chunk suffix) — not per-chunk LLM.
3. **Contextual embedder** remains the designed D63 alternate that **deletes**
   the prefix stage — strategic evaluation, not this week’s scramble.
4. Full **per-chunk `processing_state` fan-out** is optional architecture, not
   required if checkpointing works (Claude: explicitly “do not”; Codex: clean
   but heavier; internal: O1-lite first).

---

## 2. High-value findings only in external agents

These should update anyone who only read `PROBLEM.md` / internal notes:

### From Claude

1. **Current LLM prefix may be near-zero marginal value over a good template.**
   `_prefix_prompt` uses **numeric** `section_path` (`0.2.1`), not
   `document_sections.title`, while forbidding restating summaries — so the model
   often lacks human-readable location. **Pass section titles into the prompt**
   (and E2 bundle) is the cheapest quality fix and a precondition for fair
   template-vs-LLM measurement.
2. **Checkpoint is D56-safe:** carry-forward SQL requires `embedding_ref IS NOT
   NULL`, so prefix-only mid-flight rows cannot leak into reuse vectors.
3. **Degrade like D79, don’t DLQ the document:** after bounded retries, stamp a
   **distinct** `prefixer_version` for template fallback so later runs can
   re-attempt LLM without cementing degradation forever.
4. **Confirm DLQ root cause in minutes:** inspect failed prefix `cost_ledger`
   `tokens_out` — if ≈ max_completion_tokens, truncation explains repeated
   identical failures.

### From Codex

1. **Prefix has three consumers** (vector, P1 text/FTS, **E2 grounding union**).
   Changing prefix content is not “retrieval only” — it changes what E2 may
   quote. Templates must stay source-derived titles/metadata, **not** abstractive
   section summaries.
2. **One giant embed batch is a second blast radius** after prefixes; batching is
   required either way.
3. **Contextual embedder is not a free config flip for D56:** vector reuse keyed
   only by chunk content hash may be unsound if the vector depends on neighbors.
4. **Generic JSON repair is worse than a deterministic fallback** (accepts
   ambiguous prose into retrieval/grounding).

### From internal (Grok)

- Source-kind policy (chat vs paper) as product lever.
- Explicit “do not keep hoping OpenRouter holds for N sequential calls.”

---

## 3. Recommended architecture (keep quality, shorter, not flaky)

### What “quality” means after synthesis

Enough **deterministic location signal** in the embedded string that passages
separate by document/section/turn — **not** “N poetic LLM sentences.” Measure
before defending per-chunk LLM as sacred.

### Target shape

```text
                    ┌─────────────────────────────┐
  chunks packed ──► │ resolve location context    │
                    │  1. stored prefix (replay)  │
                    │  2. template OR LLM unit    │  ◄── policy by source shape
                    │  3. checkpoint immediately  │
                    └─────────────┬───────────────┘
                                  ▼
                    ┌─────────────────────────────┐
                    │ embed in bounded batches    │
                    │ upsert P1 → stamp PG        │
                    └─────────────────────────────┘
```

**Location unit (LLM, if any):** section or chat window, not every chunk,  
**unless** N is small or measurement demands it.

**Always:** durable progress; capped provider calls; A3 carry-forward of exact
stored bytes; no LLM in identity keys.

### Near-term implementation sequence (recommended)

1. **S0:** `cost_ledger` diagnosis on the BEAM DLQ row (tokens_out / finish).  
2. **O5 + titles on prompt path** (Claude O12).  
3. **`record_prefixes` + flush loop** (O1/O9).  
4. **Concurrency + embed sub-batches.**  
5. **Template fallback with distinct `prefixer_version`.**  
6. **S1 fault-injection spike** (fail at chunk 300; prove resume + no rebill).  
7. **S2 quality A/B:** bare / current LLM / title-aware LLM / deterministic
   template on BEAM + a paper sample; predeclare non-inferiority margin.  
8. Only after S2: design amendment for default policy if template wins.

---

## 4. Explicit “do not do” (union of three)

- Infinite document-level retries / higher max_attempts only.  
- Silent bare-chunk default without measurement.  
- Generic “find JSON in the blob” repair as the main fix.  
- One request for hundreds of prefixes or thousands of embed texts.  
- Two-phase dual vectors without explicit generation filtering (defer).  
- Per-chunk processing_state fan-out as the first move (checkpoint first).  
- Splice D79 section **summaries** into prefixes that enter E2 grounding.  
- Put prefix text into `extraction_input_hash` / chunk content identity.  
- Switch product default to contextual embedder before reuse + quality spikes.

---

## 5. Binding design impact

| Action | Amendment needed? |
|---|---|
| Checkpoint, caps, concurrency, embed batching, title in prompt | **No** — implementation fidelity to existing contracts |
| Template fallback under distinct prefixer_version | **No** if temporary degradation; **yes** if permanent default |
| Deterministic template as shipped conventional default | **Yes** — e1 §5 / D63 wording today assumes per-chunk LLM |
| Section/window LLM as normal policy | **Yes** (small) |
| Contextual embedder as default | **Decision update** (alternate already designed) |
| Split embedding-orientation vs E2 grounding fields | **Yes** if summaries/LLM orientation diverge from grounding |

---

## 6. BEAM / harness implication

Unblock path:

1. Ship checkpoint + caps + batch embed (quality ≈ today’s LLM prefix when calls
   succeed).  
2. Optionally force **template** for `source_kind=beam` in the harness only to
   finish S0 smoke while S2 measures.  
3. Resume the existing dead-lettered version after (1); do not re-ingest unless
   representation changed.

---

## 7. Executive recommendation

**Keep context-aware embeddings; stop treating “749 serial LLM calls in one
transaction” as architecture.**

1. **This week:** durable prefix checkpoints + hard provider limits + concurrent
   prefix calls + batched embeds; optionally template-fallback with a distinct
   version stamp.  
2. **This week (measure):** deterministic title/section/turn template A/B vs LLM
   — after fixing title plumbing so the LLM arm is fair.  
3. **If template wins:** amend e1/D63 and delete per-chunk prefix LLM from the
   default.  
4. **If not:** section/window LLM, not per-chunk.  
5. **Later:** contextual embedder evaluation with correct D56 reuse semantics.

Flakiness is mostly **missing durability + unbounded completions + wrong work
grain**. Efficiency is mostly **don’t generate N times what a template or one
section descriptor already encodes**.

---

## 8. File map

```text
plan/analysis/e1_context_prefix_efficiency/
  PROBLEM.md
  internal_analysis.md
  SYNTHESIS.md          ← this file (early convergence)
  FULL_SCOPE_ARCHITECTURE.md  ← proper contracts (no hotfixes; conditional headers;
                                  Slack/short-message behavior; work graph)
  external_agents/
    PROMPT.md
    claude.md
    codex.md
```

**Direction update (owner preference):** conventional interchangeable embedders
only; contextual non-goal; **no hotfix program** — implement
`FULL_SCOPE_ARCHITECTURE.md` (embedding-input policy + durable multi-unit graph +
conditional location headers).

**External design review (2026-08-03):** Fable + Codex — see
`external_agents/fable.md`, `external_agents/codex_review.md`, and
`REVIEW_SYNTHESIS.md`. Direction accepted with mandatory amendments (E2 structured
grounding, connector metadata contract, model-independent policy counter, D56
location-aware reuse, storage/work-graph discipline).

Engine analysis lives under **`plan/analysis/`** (ugm convention). Promote
revised full-scope text into `plan/designs/e1_chunks_design.md` + decisions
only after review amendments.
