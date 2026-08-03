# Problem frame — E1 context-prefix efficiency (2026-07-31)

**Status:** analysis (non-binding).  
**Home:** `plan/analysis/e1_context_prefix_efficiency/` (engine corpus; not a binding design change).  
**Trigger:** BEAM 100K conversation 1 smoke ingest → **749 chunks** → `embed_chunk` thrashing / DLQ while billing hundreds of prefix LLM calls before any durable progress.

## 1. What “prefix generation” is

Under the **conventional embedder** branch (D63 default), E1 does **not** embed bare chunk text. For each chunk it builds a short **context prefix** — an LLM sentence describing **where the passage sits** in the document (title, section path, orientation). That string is:

1. stored as `chunks.context_prefix` (replayable state), and  
2. **prepended** to the chunk body before the embedding vector is computed:

```text
{context_prefix}

{chunk body from document.md[char_start:char_end]}
```

Purpose: reduce embedding dilution and improve P1 semantic retrieval on long multi-topic docs without shrinking chunks to “needle-sized” windows. Design home: `plan/designs/e1_chunks_design.md` §5 (embedding branch), §7 A3 (carry-forward), worker `src/rememberstack/workers/e1.py` (`EmbedChunkHandler`).

With a **contextual** embedder (voyage-context / late-chunking class), the design already says **delete the prefix stage**. Default ship is conventional + prefix.

## 2. Current implementation shape (as of this analysis)

In `EmbedChunkHandler.handle`:

1. Load all chunks for the representation.  
2. Build `prefixes = tuple(_resolve_prefix(...) for chunk in chunks)` — **sequential, all chunks, in one work item**.  
3. Only then batch-embed fresh chunks.  
4. Only then upsert P1 + `record_embeddings` (writes `context_prefix` + embedding refs).

`_resolve_prefix` order: stored prefix → D56 carry-forward by content hash → **else LLM** (`ContextPrefix` JSON via OpenRouter structured output).

**Implications on first ingest of a large doc:**

| Property | Effect |
|---|---|
| Granularity of work | One `processing_state` row for **whole document version** at `embed_chunk` |
| Failure unit | Any single bad prefix completion fails the **entire** stage |
| Durability mid-flight | **Zero** prefixes/embeds written until all prefixes succeed |
| Retry cost | Retry restarts the generator; carry-forward only helps **later versions**, not mid-first-pass |
| Scale | BEAM 100K convo ~749 chunks → ~749 sequential LLM calls before embed |

Observed on BEAM smoke (2026-07-31): hundreds of `prefix:<chunk_id>` cost_ledger rows; `chunks.context_prefix` still null; DLQ with `OpenRouterInvalidResponseError: ContextPrefix: completion content is not JSON (len≈140k)`.

## 3. Constraints that must not be sacrificed for “efficiency”

From binding design (must remain true unless a new decision reopens them):

1. **A3 / D7** — LLM-derived context is **stored and carried forward**, not regenerated for unchanged content hashes.  
2. **Prefix is location-description only** — must not launder section-summary *facts* into grounding (prompt already constrains this).  
3. **Conventional + prefix is the default D63 path** — deleting prefixes without a contextual embedder is a quality/economics trade, not free.  
4. **D56 reuse keys exclude LLM output** — efficiency changes must not put prefixes into identity keys.  
5. **Ledger discipline** — retries, cost attribution, and idempotency must stay honest (prefer smaller durable units over silent best-effort).  
6. **P1 quality goal** — retrieval still needs *some* document context on conventional embeddings; “just embed naked chunks” is a quality regression unless measured and accepted.

## 4. Decision questions for this analysis

1. What is the **best near-term** fix so large first ingests (BEAM, long chats) complete without thrash?  
2. What **architectural** change keeps prefix *quality* (or better) while making the stage **shorter and non-flaky**?  
3. How do options rank on: quality, cost, latency, failure blast radius, fit with D56/A3, implementation risk, and benchmark unblock time?  
4. What is **spike-safe** (can ship for BEAM harness) vs what requires a binding design amendment?

## 5. Option space (starting list — agents may extend)

| ID | Idea | Sketch |
|---|---|---|
| O1 | **Per-chunk (or per-batch) durable prefix work** | Split `embed_chunk` so each prefix commits independently; retry only failures |
| O2 | **Hierarchical / section-level prefix** | One LLM call per section (or ancestor path), chunks inherit; far fewer calls |
| O3 | **Deterministic prefix (no LLM)** | Template from title + section path + role; zero LLM at E1 prefix |
| O4 | **Contextual embedder (D63 alternate)** | Delete prefix stage; model embeds with document context |
| O5 | **Cheaper/shorter prefix model + hard max tokens + JSON repair** | Keep architecture; harden OpenRouter path |
| O6 | **Two-phase embed** | Embed naked (or template) immediately for P1 readiness; upgrade vectors when prefixes land |
| O7 | **Batch multi-chunk prefix in one LLM call** | Like E2 batching: one call returns N prefixes |
| O8 | **Cap prefix scope for conversation docs** | Different packing/prefix policy for chat transcripts vs papers |
| O9 | **Checkpointed streaming write** | Keep one work row but flush prefixes every K successes (partial durability) |

## 6. Evidence to use

- Code: `src/rememberstack/workers/e1.py`, `adapters/openrouter.py` (JSON decode path), `model/chunks.py` (`ContextPrefix`)  
- Design: `plan/designs/e1_chunks_design.md` §§5,7; D63 embedding port  
- Ops observation: BEAM 100K/1 → 749 chunks; DLQ non-JSON completion; cost_ledger `prefix:*` progress without DB write  
- Related: E2 **already** batches and commits per-chunk extraction progress — asymmetry with E1 is a design smell worth addressing

## 7. Outputs expected

| File | Role |
|---|---|
| `internal_analysis.md` | Grok/orchestrator analysis |
| `external_agents/claude.md` | Claude Code independent analysis |
| `external_agents/codex.md` | Codex independent analysis |
| `SYNTHESIS.md` | Converged recommendation + spike plan + what would become a binding design change |
