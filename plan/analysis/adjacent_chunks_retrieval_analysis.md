# Adjacent Chunks Retrieval Primitive — Analysis

**Date:** 2026-09-21  
**Status:** Non-binding working analysis  
**Problem Area:** Chunk boundary cutoffs, conversational turn continuity, and query surface retrieval primitives

---

## 1. Problem Framing & Motivation

In memory engines and document-retrieval architectures, documents and conversational transcripts are segmented into discrete chunks (typically 1,000–2,000 characters). While chunking enables bounded vector embeddings and BM25 index terms, it introduces an inherent structural defect: **chunk boundary cutoffs**.

In conversational dialogues and multi-turn exchanges, natural context boundaries do not align with token/character boundaries:
1. **Dialogue Turn Splitting:** A question or setup occurs at the tail of chunk $N$, while the answer or critical detail appears at the start of chunk $N+1$.
   - *Example (`conv-42/qa/0094`):* In session D4, turn `[D4:10]`, Joanna mentions: *"I finally finished my first full screenplay and printed it last Friday... so I just started writing another one while I wait to hear back."* In turn `[D4:11]`, Nate asks: *"What's the new one about?"* In turn `[D4:12]`, Joanna answers: *"It's about a thirty year old woman on a journey of self-discovery after a loss."* When semantic search matches the mention of the screenplay in `[D4:10]`, the chunk boundary cuts off the explanation in `[D4:12]`.
2. **List & Enumeration Splitting:** An ongoing list of items (e.g., preferences, allergies, rules) spans across two consecutive chunks.
   - *Example (`conv-42/qa/0011`):* In raw session text, reptile and fur allergies appeared in one chunk, while dairy intolerance was discussed in the next turn.

### Current Retrieval Reality & Failure Mode
When an answering agent or external caller executes `search_chunks` or `claims_and_sources_context`:
* The query engine nominates chunks purely by semantic and BM25 relevance to the search query.
* Neighboring chunks that contain the continuation, context, or answer often lack the specific keywords of the query (e.g. `[D4:12]` mentions *"a journey of self-discovery after a loss"* without repeating *"screenplay"*), and therefore score lower than the cutoff threshold $K$.
* **The caller receives an incomplete snippet with no mechanism to read the preceding or following context.**
* Attempting to solve this via prompt instructions alone fails: telling an LLM to "guess surrounding keywords" forces synthetic search queries that waste latency and budget, or leads the LLM to hallucinate non-existent API tools.

---

## 2. Technical Options Considered

### Option 1: Direct Primitive `adjacent_chunks(chunk_id, window=1)` (Recommended)
Add a dedicated retrieval primitive to the RememberStack query surface that accepts an anchor `chunk_id` and an integer `window` parameter (default `1`, allowable `1..2`).
* Looks up the target chunk's `(doc_id, version_id, ordinal)`.
* Selects chunks with ordinals in `[ordinal - window, ordinal + window]` for that exact document version.
* Hydrates and returns the surrounding chunks in strict document order in the standard `Envelope.chunks` collection.

**Pros:**
* Clean, deterministic, surgical.
* Uses existing database indexes on `(doc_id, version_id, ordinal)`. No schema migration required.
* Exposable across all surfaces: HTTP API, Python SDK, CLI, MCP tools, and benchmark runner.
* Bounded token footprint: `window=1` returns $\le 3$ contiguous chunks (~750–1,000 tokens), preventing context blow-up.

**Cons:**
* Requires a follow-up tool call when the caller detects a truncated chunk.

### Option 2: Automatic Window Expansion in `search_chunks` / `claims_and_sources_context`
Add an optional `chunk_window: int = 0` parameter to existing search operations. When enabled, every nominated chunk automatically pulls its immediate neighbors.

**Pros:**
* Zero extra round-trips for the caller.

**Cons:**
* Substantially increases the payload size and token cost for every search candidate.
* Hard to manage when multiple nominated chunks are adjacent or overlapping.
* Blurs the boundary between targeted retrieval and full-document dumping.

### Option 3: Parameterizing Direction with `previous` and `next`
Instead of a symmetric `window: int = 1`, provide asymmetric parameters `previous: int = 1, next: int = 1` or `before: int = 1, after: int = 1`.

**Analysis:**
* Increases cognitive burden on LLM agents: when encountering an arbitrary chunk boundary, an agent rarely knows whether the missing sentence was just before or just after the cutoff.
* Introducing separate `before`/`after` parameters leads to extra reasoning overhead and potential parameter confusion.
* A single symmetric `window: int = 1` provides a zero-decision, complete neighborhood on both sides.

---

## 3. Decision Recommendation

Adopt **Option 1 (`adjacent_chunks` with symmetric `window`)**.
* Keep parameters minimal: `chunk_id: UUID` (required) and `window: int = 1` (default 1, minimum 1, maximum 2).
* Return standard `Envelope` with `chunks: tuple[ChunkEvidenceResult, ...]`, ordered by `ordinal`.
* Expose across the full surface hierarchy: engine -> REST API -> SDK (`MemoryClient`) -> CLI -> MCP -> answer agent catalog.
