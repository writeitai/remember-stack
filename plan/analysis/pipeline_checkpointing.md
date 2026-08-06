# Analysis: Mid-stage Postgres checkpointing (ingest workers)

**Status:** Analysis — non-binding.  
**Date:** 2026-08-06  
**Question:** Which long-running ingest stages should **persist durable progress
incrementally** so crashes, provider 5xx, and force-kills do not discard hours
of successful sub-work?  
**Evidence:** BEAM 100K smoke —

- `label_relation` ran LLM labels for many relations then hung/failed on embed;
  **0** `fact_label` stamps (handler only stamps at end).  
- Retries re-issued label LLM calls (cost_ledger showed ≫ relation count).  
- `normalize_relations` obs phase is multi-hour sequential work with limited
  mid-flight visibility.

---

## 1. Problem frame

Several handlers are **document-scoped single processing_state rows** that
internally loop over large sets (claims, relations, entity observation
batches). The work ledger retries the **whole stage**. If durable outputs are
only written at the end:

| Symptom | Cause |
| --- | --- |
| Lost LLM spend | Labels/embeds not stamped before crash |
| Inflated attempts cost | Replay redoes successful sub-units |
| Opaque hangs | No partial PG state; only cost_ledger crumbs |
| Operator despair | Force-kill loses “almost done” |

### Invariant already claimed (P1)

`LabelFactsHandler` documents: **Lance write before PG stamp** so Postgres
never advertises a missing vector. Checkpointing must **preserve** that order
per unit, not reverse it.

### Invariant for observations

Observation evidence is the replay marker for “claim normalized” (with
relation_evidence). Checkpointing must not leave half-applied adjudication
without evidence/adjudication rows.

---

## 2. Stages ranked by checkpoint need

| Stage | Internal loop | Durable unit today | Checkpoint urgency |
| --- | --- | --- | --- |
| `label_relation` | Relations (LLM) then all facts embed | End of handler only | **Critical** |
| `normalize_relations` (obs tail) | Per entity batch | Per entity commit (one tx per `add_observations`) | Medium — better progress UX; claim loop already writes relations immediately |
| `extract_claims` | Per chunk | Per chunk decisions (largely) | Lower if already chunk-idempotent |
| `embed_claim` | Batches | After each batch if stamped per batch | Medium (batching helps) |
| `structure` | Sections | Often incremental | Lower for this smoke |

**Focus of design:** `label_relation` first; patterns reusable for claim embed
and any “all-or-nothing document fan-in” handler.

---

## 3. Alternatives

| Option | Idea | Pros | Cons |
| --- | --- | --- | --- |
| **A. Status quo** | End stamp only | Simple | BEAM-class loss |
| **B. Per-relation stamp** | After each label+optional embed | Max resume fidelity | Many small Lance/PG ops |
| **C. Per embed batch stamp** | Label all in mem, embed batch N, stamp batch | Matches provider batching | Still loses labels if crash during label loop |
| **D. B+C hybrid** | Stamp label text in PG without embed ref; embed later | Labels survive; embed separate | Two-phase readiness (searchable only after embed) |
| **E. Sub-work ledger rows** | Child processing_state per relation | Perfect orchestration | Large schema/ops change |

**Recommended direction:** **D hybrid for label_relation**:

1. Persist `fact_label` + version **as soon as LLM (or deterministic) label
   exists**, without requiring embedding.  
2. Embed unlabeled-for-search rows in batches; stamp `fact_label_embedding_ref`
   only after Lance upsert (keep Lance-before-ref invariant for **embed**
   readiness).  
3. Idempotent selectors: `relations_for_labeling` skips labeled; 
   `relations_for_embedding` / observations skip those with ref for generation.

For **observation adjudication**, prefer committing **per entity** (already
mostly true) and ensuring claim-loop relation upserts remain immediate; add
progress metrics rather than redesigning the whole stage first.

---

## 4. Correctness constraints

1. **Idempotent resume** — re-entry must not double-bill if outputs exist
   (skip labeled; skip embedded).  
2. **No false readiness** — query paths that require vectors must filter
   `embedding_ref IS NOT NULL` (or equivalent), not merely `fact_label IS NOT
   NULL`.  
3. **Lance-before-ref** remains for any vector advertisement.  
4. **Generation pins** — label_version / embedding model still invalidate
   correctly (D63).  
5. **Locks** — deployment label lock may be held longer; document timeout /
   batch commit strategy so lock doesn’t become a multi-hour mutex without
   progress visibility (prefer shorter critical sections per batch).

---

## 5. Cost of not doing this

From BEAM smoke: hundreds of label LLM calls with **zero** durable labels after
hang/503. Each retry multiplies spend. Checkpointing is cheaper than another
full label pass.

---

## 6. Recommendation

Write a binding design for **incremental fact labeling and embedding
checkpoints** in P1 `label_relation` (and the same pattern for `embed_claim`
batch stamps if not already durable). Treat normalize obs as secondary
(progress + per-entity already).

---

## 7. Open questions

1. Should unlabeled-but-labeled-text relations be searchable via BM25 on PG
   before Lance embed? (Probably no — keep search on Lance only.)  
2. Transaction size: one commit per relation vs per batch of 10?  
3. Interaction with deterministic labels (no LLM): checkpoint still required
   for embed phase of 2k observations.
