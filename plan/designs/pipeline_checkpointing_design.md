# Design: Incremental checkpoints for long ingest stages

**Status:** Proposed binding design — **revised after dual review** (not yet
accepted).  
**Date:** 2026-08-06 (rev 2 after Codex + Claude Fable)  
**Reviews:** `design/reviews/REVIEW_claude-fable_2026-08-06.md`,
`design/reviews/REVIEW_codex-sol_2026-08-06.md`  
**Analysis:** `plan/analysis/pipeline_checkpointing.md`  
**Primary target:** `label_relation` (`LabelFactsHandler`).  
**D94 amendment (2026-08-14):** P1 is now a private PostgreSQL projection.
The pre-D94 code still sets `fact_label_embedding_ref = relation_id::text` in
the same update as the label; implementation of D94 removes that opaque
external-store reference and introduces the explicit generation state below.

---

## 1. Problem

Document-scoped workers that only persist at handler exit discard successful
sub-work on crash or provider failure. BEAM 100K: many label LLM calls, **zero**
durable `fact_label` rows after hang/503, retries re-paid the work.

## 2. Decision

**Long-running ingest handlers checkpoint durable unit progress
incrementally** with **split label vs embed generations** and **clear
readiness rules**.

For fact labeling/embedding:

1. **Label generation** (`label_generation`) — text of the fact label.  
2. **Embed generation** (`embed_generation`) — vectors for search.  
3. These **may differ**: e.g. embedding model rotates without re-labeling, or
   labels change without re-embedding until Phase E runs.  
4. Resume selectors and public search readiness use these markers explicitly —
   **not** “`fact_label_embedding_ref IS NULL`” against the current schema
   (that column is set at first label stamp and never cleared).

## 3. State machine (per relation / observation)

Define markers on Postgres (names illustrative; migration required):

| Marker | Meaning |
| --- | --- |
| `fact_label` + `fact_label_version` | Label text for `label_generation` |
| `fact_label_embed_version` | Embed generation last successfully indexed |
| matching `p1_fact_search` row | Search projection for the same deployment, fact ID and embed generation |

Logical states:

| State | Condition |
| --- | --- |
| **U** Unlabeled | `fact_label_version` ≠ current `label_generation` |
| **L** Labeled, not embedded for current embed gen | label current, `fact_label_embed_version` ≠ current `embed_generation` |
| **E** Embedded current | both generations current and a matching P1 row exists for that embed gen |

Transitions:

```text
U --produce_label--> L --p1_upsert_and_stamp--> E
```

**Re-label** (new `label_generation`): U/L path again; **must** force re-embed
(clear or advance embed version expectation) so the active P1 generation cannot
serve an old vector under a new label generation without Phase E.

**Re-embed only** (new `embed_generation`, same label): L→E without re-LLM.

### Observations

Same split: `obs_label_version` vs `obs_label_embed_version` (or equivalent).
Observations typically skip LLM label (statement is the text).

## 4. `label_relation` handler

### 4.1 Concurrency (normative)

**v1 requirement:** document-scoped advisory lock for the deployment label
pass **plus** row-level conditional updates (CAS) on stamp:

```sql
UPDATE relations SET ... WHERE relation_id = $1
  AND (fact_label_version IS DISTINCT FROM $label_gen)  -- label stamp
```

```sql
UPDATE relations SET fact_label_embed_version = $embed_gen, ...
 WHERE relation_id = $1
  AND fact_label_version = $label_gen
  AND (fact_label_embed_version IS DISTINCT FROM $embed_gen)
```

If `rowcount = 0`, another worker won; skip without re-billing when safe.

Do **not** treat “batch lock only” as equivalent without CAS: two docs
evidencing the same relation can race label production.

### 4.2 Phase L — label checkpoint

```text
for relation in relations needing label_generation:
    label = produce_label(relation)  # LLM or deterministic (orthogonal design)
    CAS stamp fact_label + fact_label_version
    # Does NOT set embed version; does NOT claim P1 readiness
```

### 4.3 Phase E — embed checkpoint

```text
for batch in relations/observations needing embed_generation
            (and label already current if relation requires label):
    vectors = embed(batch texts)  # chunked to provider cap
    transaction:
        upsert p1_fact_search(ids + vectors + generation coordinates)
        CAS stamp fact_label_embed_version
```

**Invariant:** the P1 row and its authority-side completion stamp commit in the
same PostgreSQL transaction. Neither may become visible alone.

### 4.4 Readiness (truthful)

D94 chooses one readiness rule. A fact is searchable only when the private P1
row, the authority row and the active generation coordinates agree. The search
statement joins them under one MVCC snapshot and reapplies deployment,
generation and validity predicates. A stale or orphaned projection row therefore
cannot leave P1, even before asynchronous cleanup removes it.

### 4.5 Lock duration vs checkpoints

Multi-hour lock without checkpoints is forbidden. Checkpoints occur **inside**
the locked pass at least every successful label unit and every successful embed
batch.

## 5. `embed_claim`

Each successful batch stamps claim embedding markers before the next batch.
Resume selects unstamped claims for the active embedder generation.

## 6. Failure / recovery

| Failure | Result |
| --- | --- |
| Crash mid Phase L | Resume unlabeled only (CAS) |
| Crash during P1 upsert/stamp transaction | Transaction rolls back; resume re-embeds or reuses a validated batch result and retries the idempotent upsert |
| Provider 5xx mid Phase E | Prior batches remain embedded; retry remainder |
| Generation bump | Selectors re-drive L and/or E as above |

**Billing:** at-least-once provider calls remain possible under retry; design does
not claim exactly-once spend. Idempotent stamps minimize **duplicate durable
work**, not necessarily duplicate HTTP.

## 7. Alternatives considered

| Alternative | Why insufficient alone |
| --- | --- |
| End-only stamp | Demonstrated data loss |
| “embedding_ref IS NULL” as needs-embed | **Broken on real schema** after first stamp |
| Child processing_state per fact | Heavy; defer |
| Stamp embed before the P1 row transaction | False readiness |

## 8. Acceptance criteria

1. Kill after ≥1 CAS label stamp; restart; no re-produce_label for stamped ids.  
2. Label generation bump forces the re-embed path; an old P1 row cannot satisfy
   the new generation.
3. Embed generation bump alone does not re-LLM labels.  
4. Provider batch size ≤ configured cap (≤1024 texts).  
5. Race test: two concurrent label attempts → one winner, no double success
   stamp without CAS.  
6. Readiness test proves the same-statement P1/authority generation join.
7. Schema migration for embed version columns reviewed for delete/forget.

## 9. Implementation touchpoints

- `workers/p1.py` — split Phase L / E  
- `fact_catalog.py` — selectors, CAS stamps, **stop setting embed ref in label
  stamp**  
- `p1_fact_search` migration and query — generation coordinates plus authority join
- Migrations + hard-forget inventory  

## 10. Review disposition

Dual review: **Accept with changes** (both agents). Blocking defect (needs-embed
via null ref) fixed in this revision via **split generations**. Concurrency and
readiness authority made normative.
