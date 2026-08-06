# Analysis: Observation adjudication rank embedding cache

**Status:** Analysis — non-binding.  
**Date:** 2026-08-06  
**Question:** Should write-path observation ranking **embed each open
statement once** and reuse vectors, instead of re-embedding the full open set
on every residue assertion?  
**Related binding design:** `plan/designs/observations_design.md` §3 (D43).  
**Implementation today:** `src/rememberstack/spine/observation_adjudication.py`
`_rank`.  
**Evidence:** BEAM 100K smoke normalize tail (hub entity “User”: ~90 obs,
hundreds of rank embed rows; stage cost dominated by small_model + embeds).

---

## 1. Problem frame

When a new observation assertion is not free (exact match / first on entity /
clear novelty), the adjudicator:

1. Loads **all open** observations on the entity (exhaustive SQL block).
2. **Ranks** them by embedding cosine similarity to the new statement
   (ordering only; membership is not filtered).
3. Runs pairwise small-model verdicts on the top `hub_top_k` (default 5).

Step 2 today calls:

```text
embed([NEW, open_1, open_2, …, open_N])
```

on **every** residue assertion. For hub entities, open statements are
re-embedded dozens of times per document. That is pure repeat work: the
statement text of an existing observation does not change.

### What this is not

- **Not** P1/Lance retrieval indexing (`obs_label_embedding_ref` after
  `label_relation`). Rank happens **during E3 write**, often **before** P1
  projects the observation.
- **Not** changing exhaustive block, fail-safe coexist, or no-cap rules.
- **Not** multi-model embedding failover (same model id only).

Binding design §3 says hub narrowing uses “P1/Lance over the observation
label.” **Implementation diverges:** live `model_provider.embed` + in-process
cosine. This analysis treats the **implemented** hot path as the thing to fix,
and notes reconciling design text as a follow-up.

---

## 2. Cost / complexity model

Let:

- \(A\) = number of residue assertions on an entity in one document batch  
- \(N_i\) = open observation count when assertion \(i\) is processed  
- \(C_e(t)\) = cost/latency of embedding \(t\) texts  

**Today (no cache):**

\[
\sum_{i=1}^{A} C_e(1 + N_i)
\]

If opens grow \(N_i \approx i\) after inserts: roughly \(O(A^2)\) text embeds.

**With write-through cache (embed NEW once per assert; open texts once ever):**

\[
\sum_{i=1}^{A} C_e(1 + M_i)
\]

where \(M_i\) = number of **cache misses** among opens (≈ 0 after warm).  
Steady state per assert: embed **1** text (the NEW statement), plus optional
batch warm of initial open set.

### BEAM-shaped illustration (order of magnitude)

Suppose one hub: 90 open observations after growth; 90 residue asserts that
each re-rank all opens:

| Regime | Embed API text-instances (approx.) |
| --- | ---: |
| No cache | \(\sum (1+N) \approx 90\times45 \approx 4000+\) |
| Cache | \(\approx 90\) (new) + \(\approx 90\) (first open warm) \(\approx 180\) |

Even if half the path is free-exit (no rank), residual hub traffic still
dominates wall clock when embeds take 1–3s per multi-text call.

---

## 3. Alternatives

| Option | Description | Pros | Cons |
| --- | --- | --- | --- |
| **A. Status quo** | Live re-embed every rank | Simple; always fresh | \(O(A\cdot N)\) waste; hub tails hours |
| **B. Process-local cache** | Memoize vectors in adjudicator for job lifetime | Tiny change; big win one-doc | Lost on crash; multi-worker cold |
| **C. Durable PG cache** | Table keyed by `(observation_id, model, version)` | Survives crash; multi-worker | Schema + migration; invalidation |
| **D. Lance as rank source** | Read P1 vectors at write time | Aligns design text | P1 often **after** E3; label/text may differ from `statement`; couples write to index |
| **E. Hash-only / no embed rank** | Lexical or skip rank | Free | Loses semantic hub ordering; more LLM residue or more coexist |

**Recommended direction:** **B then C** (in-process first, durable if needed).
**D** only if E3 starts writing P1 early with **identical** embed text and model
pin as rank.

---

## 4. Correctness constraints

1. **Same model + version** — cache key must include embedding model id (and
   any embedder generation pin). Cross-model vectors must never mix.
2. **Same text** — key open rows by `observation_id` (immutable `statement`
   after insert). NEW keyed by content hash until an id exists; write-through
   under id after insert.
3. **Ordering-only rank** — wrong rank order still fail-safes to coexist;
   cache bugs that **permute** order are lower severity than bugs that
   **drop** block membership (must not).
4. **No Lance membership filter** — cache is vector memoization, not ANN
   candidate generation.

---

## 5. Failure / recovery

| Event | Expected behavior |
| --- | --- |
| Process crash mid-entity | Process cache lost; durable cache (if present) reloads; worst case re-embed misses |
| Model pin change | New key space; cold cache; full re-embed (correct) |
| Statement never changes | No invalidation needed for open rows |
| Embed API 5xx on miss | Fail assertion path as today; do not poison cache with empty vectors |

---

## 6. Recommendation (analysis only)

Proceed to a **binding design** that:

1. Requires write-path rank to use a **versioned vector cache**.
2. Starts with **process-local + write-through on insert**, optional **Postgres
   side table** for multi-worker / crash resume.
3. Explicitly **does not** depend on Lance for E3 rank in v1.
4. Records the design-vs-impl gap (Lance in prose vs live embed in code) and
   closes the prose to match chosen storage.

---

## 7. Open questions for design review

1. Store vectors in PG (`halfvec` / bytea) vs external blob vs Lance dual-write?
2. Is process-local enough for single-worker compose smokes, with durable as
   phase 2?
3. Should NEW’s vector after insert always write-through even if evidence-
   collapsed (no new row)?
