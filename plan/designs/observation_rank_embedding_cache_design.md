# Design: Observation rank embedding cache (write path)

**Status:** Proposed binding design — **revised after dual review** (not yet
accepted).  
**Date:** 2026-08-06 (rev 2 after Codex + Claude Fable)  
**Reviews:** `design/reviews/REVIEW_claude-fable_2026-08-06.md`,
`design/reviews/REVIEW_codex-sol_2026-08-06.md`  
**Analysis:** `plan/analysis/observation_rank_embedding_cache.md`  
**Related:** `plan/designs/observations_design.md` §3 (D43); D63 embedding
generation identity.

---

## 1. Problem

Observation adjudication ranks open priors by embedding similarity before the
LLM ladder. Today each residue assertion embeds **NEW + all open statements**
again (`observation_adjudication.py` `_rank`). Hub entities re-embed the same
open texts repeatedly, driving multi-hour E3 tails and unnecessary spend.

## 2. Decision

**Write-path rank uses a versioned embedding memoization cache.** Each
distinct open observation statement is embedded at most once per
**embedder_generation** (best-effort under memory bounds), then reused for
cosine ranking. The exhaustive SQL entity block, fail-safe coexist, no-cap
rule, and `hub_top_k` ladder are **unchanged**. The cache is an optimization
over that block — **never** a membership filter.

## 3. Why this is safe (correctness bound)

Wrong or stale rank vectors **cannot** cause a wrong supersede/cap by
themselves. Against current code:

1. **Exact-match evidence collapse** runs on raw string equality **before**
   any embedding (`_add_with_block`).
2. **Novelty gate** using a bad similarity either inserts a duplicate (safe
   failure) or escalates to the LLM ladder.
3. **LLM ladder** reads **actual statement strings**, not vectors
   (`_ladder`).
4. **Hub top-k miss** yields at worst coexisting duplicates — already the
   designed fail-safe for poor ranking (`observations_design.md` §3).

Maximum severity of cache bugs: **duplicates + extra spend**, not silent
wrong caps — **unless** future refactors move merge decisions onto vectors
alone. The design’s safety claim is therefore conditional on (1)–(3)
remaining true.

## 4. Scope

| In | Out |
| --- | --- |
| `_rank` memoization + write-through rules | Changing novelty / supersede margins |
| Same embedder_generation as observation rank settings | Multi-model vector spaces |
| Process-local cache (required) | Requiring the durable P1 search projection at E3 write time |
| Optional durable PG cache | Replacing exhaustive SQL block with ANN |

## 5. Contracts

### 5.1 Embedder generation (identity)

Cache identity is **not** only the model name string. Per D63, resolve an
`embedder_generation` that includes at least:

- configured model id  
- stored dimension / truncation  
- distance / input policy parameters that affect the vector  
- adapter/component generation when it changes encoding  

Durable keys use `embedder_generation`, not bare `embedding_model`.

### 5.2 Cache key

```text
RankEmbedKey =
  (deployment_id,
   embedder_generation,
   content_key: ObservationId | NewStatementDigest)
```

- **Open prior:** `content_key = observation_id`.  
- **NEW assertion:** `content_key = sha256(utf-8 bytes of the exact string
  passed to embed)`. Today that string is the **raw** `statement` (same as
  exact-match compare). If normalization is introduced later, embed the
  normalized form **and** hash that form; do not mix contracts.  
- Observation **`statement` is immutable after insert** (precondition). If a
  future feature edits statements, it must invalidate this cache.

### 5.3 Alias rules (critical)

| Event | Allowed write-through |
| --- | --- |
| NEW embedded for rank, then **`_insert_new` creates a row** | May store NEW’s vector under the **new** `observation_id` |
| NEW collapses as **evidence** onto an **existing** observation | **Must not** store NEW’s vector under the existing id (stored statement may differ) |
| First mention / paths with no rank | Do not force an embed solely to fill cache |

### 5.4 Rank algorithm

1. Start from the exhaustive open-candidate list from SQL (never join away
   missing cache rows).  
2. Resolve vectors via cache; **chunk provider embeds** to the active text
   count / token cap (never one unbounded “all misses” call on huge hubs).  
3. **Single-flight** concurrent misses for the same key (thread-safe).  
4. Validate every vector before cache write **and** on durable read: count,
   expected dims, finite components, nonzero norm. Invalid ⇒ miss; repeated
   invalid provider output ⇒ error (never cache).  
5. Cosine rank; return `(candidate, score)` list as today.  
6. Key↔vector pairing is positional against the embed request; tests must
   use distinguishable mock vectors.

### 5.5 Process-local cache (required)

- Lifetime: **worker process** (matches current long-lived adjudicator
  binding) **or** explicitly reset at document boundary — implementation
  MUST pick one and document it.  
- **Memory bound required:** LRU (or equivalent) with max entries and/or max
  bytes; export a cache-size metric.  
- Under eviction, “embed at most once” becomes **best-effort**; correctness
  unchanged.  
- Durable store unavailable ⇒ fall back to process/live embed; **never**
  shrink the candidate set.

### 5.6 Durable cache (optional)

Postgres sketch (revised):

```sql
CREATE TABLE observation_rank_embeddings (
  deployment_id        uuid NOT NULL,
  observation_id       uuid NOT NULL,
  embedder_generation  text NOT NULL,
  dims                 int  NOT NULL,
  encoding_version     text NOT NULL,  -- float width / endianness / layout
  vector               bytea NOT NULL,
  checksum             bytea NOT NULL,
  statement_sha256     bytea NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, observation_id, embedder_generation),
  FOREIGN KEY (deployment_id, observation_id)
    REFERENCES observations (deployment_id, observation_id)
    ON DELETE CASCADE
);
```

- `INSERT … ON CONFLICT DO NOTHING` (or replace only when sha matches
  policy).  
- Write-through of NEW ids only **inside** the adjudication transaction or
  **after** commit (no orphan durable rows for rolled-back inserts).  
- Included in hard-forget / deployment deletion via FK cascade or explicit
  purge hooks (Rule 3: no derived personal data outside deletion scope).

### 5.7 Not the durable P1 projection (write path)

Rank cache is independent of the P1 facts channel. E3 may run before P1;
rank text is `statement`.  

`observations_design.md` §3 describes this as embedding similarity over open
statements (versioned vector cache; ordering only).

## 6. Failure and recovery

| Failure | Behavior |
| --- | --- |
| Embed API error | Propagate; no cache write |
| Corrupt durable row | Miss → re-embed |
| Embedder generation change | New key space |
| Durable store down | Degrade to process/live embed |
| Process crash | Cold process cache; durable survives if present |

## 7. Observability

- Distinguish billable `rank:miss` embeds from hits (logs/metrics).  
- Hits are not billable usage.  
- Cache size / eviction counters.

## 8. Alternatives considered

| Alternative | Why not sole solution |
| --- | --- |
| Keep re-embedding | Quadratic hub cost |
| P1-only rank source | Timing/coupling; text drift |
| Drop embed rank | More LLM or worse ordering |

## 9. Acceptance criteria (implementation)

1. Two residue asserts: open texts embedded once (mock call counts), under
   bound.  
2. Evidence-collapse does not write NEW vector under existing id.  
3. Embedder generation change forces re-embed.  
4. Provider chunking respected for large miss sets; candidate count preserved.  
5. Invalid vectors never cached.  
6. `observations_design.md` §3 prose updated.  
7. Durable table (if shipped) has FK/delete behavior and conflict tests.

## 10. Review disposition

Dual review: **Accept with changes** (both agents). This revision absorbs
P0 items from both reviews. Ready for a second pass or implementation PR
once stakeholders accept this text.
