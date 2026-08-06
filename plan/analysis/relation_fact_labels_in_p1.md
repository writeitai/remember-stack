# Analysis: Should relations live in P1 Lance, and with what label shape?

**Status:** Analysis — non-binding. **No binding decision yet.**  
**Date:** 2026-08-06  
**Questions:**

1. Should **relations** continue to be indexed as P1 “facts” rows (Lance), or
   is graph (P2) + claims/observations enough for entry?  
2. If relations stay in P1, should the embed text be **LLM-written** fact
   labels or a **deterministic** shape from `(subject, predicate, object)`?

**Binding context:**  
`plan/designs/p2_graph_design.md` §6 (relations vectors in Lance, not
Ladybug); `plan/designs/retrieval_design.md` (search targets include
`relations`); `plan/designs/observations_design.md` (observations are
non-graph, need P1).  
**Implementation:** `LabelFactsHandler` — one small LLM call per relation
then embed labels + observation statements.

**Evidence:** BEAM 100K smoke — 195 relations, multi-hour/ hung
`label_relation`, 0 durable labels after many LLM calls; observations already
carry natural-language `statement`s.

---

## 1. What problem relation fact labels solve

Retrieval needs an **entry** path for free-text questions about
entity–entity structure, e.g. “who uses Matplotlib?”, without first knowing
entity ids.

| Store | Strength | Weakness for free-text entry |
| --- | --- | --- |
| **P2 graph** | Paths, adjacency, structured filters on predicate | Poor “semantic bag of facts” search; no relation-property vectors (design constraint) |
| **P1 claims** | Full extracted language | Noisy, redundant, not collapsed to believed edges |
| **P1 relation fact labels** | One row per believed edge; smaller search space | Needs embeddable text per edge |
| **P1 observations** | Attributes/stances about one entity | Not two-entity edges |

Binding design (P2 §6) already decided: **relation vectors live in Lance**,
not in the graph snapshot. That decision was about **where vectors live**, not
**whether LLM prose is required**.

---

## 2. Option space

### 2.1 Keep relations in P1 vs drop them

| Option | Meaning | When it wins |
| --- | --- | --- |
| **R1. Keep** | `search(target=relations)` over fact-label embeddings | Free-text entry to edges is a product requirement; claim channel too noisy |
| **R2. Drop** | Only claims + observations + structured/graph | Graph-first UX; agents always resolve entities then traverse; cost of labeling is unacceptable |
| **R3. Hybrid** | Index only high-value predicates; or claims only for low-value `related_to` | Registry-aware; reduces volume |

**Analysis lean:** **R1 remains default** unless a retrieval eval shows
claim/obs channels cover relation questions at target quality. Dropping
relations from P1 is a **product/retrieval** change, not a free smoke
optimization — it removes a designed entry channel.

Structured scalar search (`object=Acme, predicate=works_for`) does **not**
need embeddings; free-text still does if users/agents phrase naturally.

### 2.2 Label text production

| Option | Embed text | LLM? | Pros | Cons |
| --- | --- | --- | --- | --- |
| **L1. LLM sentence** | “Craig uses Matplotlib.” | Yes | Natural language match to questions | Cost, latency, hang risk, non-determinism, prompt drift |
| **L2. Deterministic template** | `"{subject} {predicate_surface} {object}"` | No | Free, fast, idempotent, checkpoint-friendly | Predicate slugs may be ugly (`related_to`, `part_of`) |
| **L3. Deterministic + lexicon** | Map predicate → verb phrase from registry | No | Readable without LLM | Registry maintenance |
| **L4. Dual** | Template embed; optional LLM label for display only | Embed free | Search stable; UI pretty | Two fields to maintain |
| **L5. Claims only** | Don’t embed relations | No | Simplest write path | Loses edge-collapsed search |

**Analysis lean:** **L2 or L3 over L1** for write-path economics and
reliability. LLM labels are not required by the “vectors in Lance” decision.
Graphiti-style prose is a **quality preference**, not a consistency
requirement for `(s,p,o)` which is already structured.

If retrieval eval later shows template mismatch (e.g. users never say
“part_of”), prefer **L3** lexicon before reintroducing per-edge LLM.

---

## 3. Deterministic shapes (candidates)

Assume registry names available: `subject.canonical_name`, `predicate`,
`object.canonical_name`.

| Shape ID | Template | Example |
| --- | --- | --- |
| **S1** | `{s} {p} {o}` | `Craig uses Matplotlib` |
| **S2** | `{s} —[{p}]→ {o}` | `Craig —[uses]→ Matplotlib` |
| **S3** | `{s} is {p} {o}` | awkward for `uses` |
| **S4** | lexicon: `{s} {verb(p)} {o}` | `Craig uses Matplotlib` with `part_of` → `is part of` |
| **S5** | include types | `Craig (Person) uses Matplotlib (Product)` |

**Recommendation for trial (revised after dual review):** prefer **S4**
(registry lexicon / full predicate templates) as the **primary** production
shape when templates exist; use **S1** as fallback for predicates without a
template. Avoid S2 arrows in embed text (tokenizer noise). Types (S5) help
disambiguation but lengthen text — evaluate separately.

**Rationale:** dual review notes the current LLM prompt receives **only**
`(s,p,o)` (`p1.py` fact-label prompt) — so L1 adds no information over a
template. Lexical/BM25 fusion is where raw slugs (`works_for`, `part_of`) hurt
most; vector-only eval is insufficient.

**Stability rule:** template must be a pure function of stored triple +
registry version so generation pin can include
`fact_label_template_version`.
---

## 4. Interaction with checkpointing

Deterministic labels make Phase L **CPU-only** and cheap to recompute, but
**still stamp** `fact_label` for:

- audit / display  
- avoiding recompute on huge corpora  
- embedding Phase E idempotency  

Checkpoint design remains valuable for **observation embeds** (2k+ statements)
even if relation labels are free.

---

## 5. Interaction with observation channel

Observations **must** stay in P1 (non-graph). Any plan that “drops facts from
Lance” must **not** drop observations. Relation-only drop is the only
coherent R2.

---

## 6. Risks

| Risk | Mitigation |
| --- | --- |
| Template poor recall | Eval suite of relation questions; fall back to lexicon (S4) |
| Predicate slug multilingual mess | Registry surface forms |
| Dropping relations without eval | Forbidden without smoke/full retrieval metrics |
| Two channels double-count same fact in RRF | Existing fusion design; monitor |

---

## 7. Recommended analysis conclusion

1. **Keep relations in P1** unless a measured **end-to-end channel ablation**
   says otherwise (default remains design R1). Ingest cost alone is not
   grounds to drop.  
2. **Prefer deterministic embed text (L2/L3) over per-relation LLM (L1)** for
   the readiness-critical path. Optional LLM **display** labels, if ever,
   must be a **separate field** and must not gate or overwrite the
   retrieval label.  
3. **Trial S4 (lexicon) primary, S1 fallback**; freeze the eval set before
   comparing to LLM.  
4. **Do not** remove relation rows from Lance as a smoke optimization alone.  
5. Binding change belongs in a short addendum to P1/P2/retrieval docs +
   worker behavior after eval.

---

## 8. Proposed eval plan (before binding “drop” or “LLM forever”)

| Eval | Metric |
| --- | --- |
| Relation questions (frozen set) | End-to-end search recipe (semantic **+ BM25/fusion** as shipped), not vector-only |
| Stratified critical slices | Confidence-bounded non-inferiority vs LLM labels (pre-registered δ) |
| Cost | LLM calls / doc, wall time `label_relation` |
| Channel ablation (only if considering R2) | Full drop of relation channel vs keep — separate gate |

Ship deterministic if fused-search quality meets the pre-registered gate;
only then drop LLM from the critical path. Drop the relation channel only
after a separate ablation passes.

---

## 9. Review asks

Codex + Claude Fable: challenge (1) keep-vs-drop, (2) template choice, (3)
whether claims channel already subsumes relation search for agent workloads.
