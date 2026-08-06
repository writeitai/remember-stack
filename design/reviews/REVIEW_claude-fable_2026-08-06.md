# Review: rank-embedding cache + pipeline checkpointing designs, relation-label analysis

**Reviewer:** claude-fable (Claude Fable 5)
**Date:** 2026-08-06
**Scope:** `plan/designs/observation_rank_embedding_cache_design.md`,
`plan/designs/pipeline_checkpointing_design.md`,
`plan/analysis/observation_rank_embedding_cache.md`,
`plan/analysis/pipeline_checkpointing.md`,
`plan/analysis/relation_fact_labels_in_p1.md` — verified against
`plan/designs/observations_design.md` §3, `plan/designs/p2_graph_design.md` §6,
`src/rememberstack/spine/observation_adjudication.py`,
`src/rememberstack/spine/fact_catalog.py`, `src/rememberstack/workers/p1.py`,
`src/rememberstack/workers/e3.py`.

---

## Executive summary

1. **Rank-embedding cache design: Accept with changes.** The mechanism is right and its
   safety argument holds against the code, but the design is silent on cache memory
   bounds, on the durable table's deletion lifecycle, and on concurrent write-through —
   and its "phase 1 / phase 2 / v1.1" framing violates CLAUDE.md Rule 2.
2. **Checkpointing design: Accept with changes — one blocking defect.** The Phase E
   resume selector ("embedding_ref missing", §4.2) is **wrong against the actual
   schema**: `fact_label_embedding_ref` is never NULL after first labeling
   (`fact_catalog.py:486-495`), so every re-generation would stamp new labels while
   Lance silently keeps the old vectors — permanent false readiness. The design needs an
   explicit embed-generation marker. Its §4.5 readiness claim is also not what the query
   path enforces.
3. **Relation-label analysis: sound, and understated in its own favor.** The current LLM
   label prompt receives *only* the `(s,p,o)` triple (`p1.py:40-43`), so L1 adds zero
   information over a template. But the proposed eval measures only vector recall; S1's
   biggest risk is the **BM25/lexical channel** of the fused recipe, which the eval must
   cover. I lean S4 (lexicon) as default, not S1-first.

---

## 1. `plan/designs/observation_rank_embedding_cache_design.md`

### 1.1 Verdict

**Accept with changes.** The core decision — memoize per-`(model, observation_id)`
vectors instead of re-embedding the full open set on every residue assertion — is
correct, cheap, and its blast radius is genuinely bounded. Required changes are listed
in §1.5; none undermines the approach.

### 1.2 Correctness risks — assessed against the code

**The "ordering-only" safety claim holds, but the design asserts it instead of arguing
it (Rule 1).** I traced the three places a wrong cached vector could land
(`observation_adjudication.py`):

- **Exact-match evidence-collapse** (lines 183–201) fires on raw string equality
  *before* any embedding is consulted. A cache bug cannot cause a wrong
  evidence-collapse — the only no-LLM merge path never sees vectors.
- **Novelty gate** (line 256, `ranked[0][1] < novelty_floor`): a *deflated* similarity
  (wrong vector) yields a duplicate insert — the designed-safe failure. An *inflated*
  similarity pushes the assertion into the ladder, where the LLM reads the **actual
  statements** (`_ladder`, lines 496–533), not vectors — so the worst case is wasted
  LLM spend, never a wrong verdict.
- **Hub top-k truncation** (line 285): a permuted order can push the true match out of
  `hub_top_k` → duplicate coexisting row, exactly the failure D43 already accepts for
  poor ranking (`observations_design.md` §3, "No recall hole").

So the maximum severity of any cache bug is *duplicates + spend*, never a wrong cap.
**Required:** the design should state this three-path argument explicitly rather than
asserting "ordering only" — a future implementer must know that the safety rests on
(a) exact-match preceding rank and (b) the ladder reading real texts. If either is ever
refactored, the cache's severity bound silently changes.

**Vector/key alignment.** "Batch miss embed in one provider call" (§4.2) must specify
that key↔vector pairing is positional against the request; a misalignment is exactly
the wrong-vector case above (bounded, but wasteful and hard to detect). An acceptance
test with distinguishable mock vectors should pin this.

**Statement immutability is a load-bearing, unstated precondition.** The process-local
cache keys by `observation_id` with **no** sha guard (only the durable table has
`statement_sha256`). This is sound today — I verified no code path mutates
`statement` (`_CAP_WINDOW` and `_SET_GROUP` touch only `valid_until` /
`contradiction_group`, `observation_adjudication.py:723-738`), and
`observations_design.md` §3 declares `statement` never changes. But the design must
state "observations.statement is immutable after insert" as an explicit precondition,
so any future statement-edit feature knows it must invalidate this cache.

### 1.3 Missing failure modes

1. **Unbounded memory growth (process-local).** The adjudicator is a long-lived
   instance bound to `NormalizeRelationsHandler` (`e3.py:92-103`), so "lifetime: one
   `ObservationAdjudicator` instance / worker process job" (§4.3) means the cache
   accumulates across every document a worker ever processes. The default model
   `qwen/qwen3-embedding-8b` is 4096-dim; fp32 ≈ 16 KB/vector. A worker that ranks
   1M distinct observations holds ~16 GB. The design has **no eviction policy, no size
   bound, no metric**. Required: define a bound (LRU with max entries, or scope the
   cache to one document job) and an observable cache-size counter.
2. **Durable table lifecycle / deletion.** `observation_rank_embeddings` (§4.4) stores
   vectors derived from statement text — derived personal data. The repo's own rule
   (CLAUDE.md Rule 3) is that deletion is fully in-repo; a derived-vector side table
   silently outside the deletion scope is a real gap. Required: FK with cascade to
   `observations` (or explicit inclusion in the deletion design), and a statement of
   what happens on deployment delete.
3. **Concurrent write-through (phase 2).** Two workers embedding the same open
   concurrently both write through; the PK will make the second write fail. Specify
   idempotent semantics: `ON CONFLICT (deployment_id, observation_id, embedding_model)
   DO NOTHING` (optionally verify sha equality on conflict).
4. **NEW write-through vs. transaction rollback.** `add_observations` runs the whole
   entity batch in one transaction (`observation_adjudication.py:133`). If the durable
   write-through for a NEW observation happens on a separate connection before the
   batch commits, a rollback leaves an orphan cache row keyed by an `observation_id`
   that never existed. Harmless (the id never appears in a future block), but the
   design should require write-through for NEW ids either inside the adjudication
   transaction or after commit, and note the orphan case.
5. **Truncated durable vectors.** "Never store empty/partial vectors" (§4.4) is
   write-side only. Add a cheap read-side check: `len(vector bytes) == dims × width`,
   else treat as miss (same fail-closed path as the sha mismatch).

### 1.4 Implementation hazards vs. current code

- `_rank` embeds `(statement, *candidate_statements)` in one call and zips
  positionally (`observation_adjudication.py:545-557`). The cache refactor changes
  this to per-key resolution — the `strict=True` zip discipline must survive.
- In-batch inserts enter the candidate list via `_remember_candidate` (line 667) with
  fresh statements; these are precisely the hash-keyed-then-id-keyed NEW entries of
  §4.1. The design matches the code here — good.
- §4.2 wording bug: "On `_insert_new` / **evidence-collapse** that already embedded
  NEW for rank: write-through NEW's vector under the new `observation_id` when a row
  is created." Evidence-collapse **creates no row** — that is its purpose. Answer
  analysis open question 3 explicitly: on evidence-collapse there is no new id; the
  hash-keyed entry simply remains (and is correct if the identical statement is
  asserted again — though note the exact-match path would collapse it before rank
  anyway).
- §4.5's claim that E3 runs before P1 is verified: `_terminal_branches` deliberately
  does not fan out fact labeling (`e3.py:172-182`). Keeping Lance out of the E3 write
  path is correct and consistent with `p2_graph_design.md` §6's division of labor.

### 1.5 Concrete required changes

- **Rule 2 violation (must fix before acceptance):** remove "phase 1 / phase 2 /
  v1 / v1.1" framing (§3 scope table, §4.3, §4.4 headings, §7). CLAUDE.md forbids
  phase framing in design docs. The full-scope system is multi-worker at millions of
  documents, so the durable store is part of the *complete* design with the
  process-local map as its in-memory layer — describe both as one mechanism and let
  `plan/plans/` sequence the build. ("Process-local only" as a permanent scope
  boundary would be defensible too, but then say so as a non-goal, not as "v1".)
- Add the three-path severity argument (§1.2 above) to the design body.
- Define a cache size bound / eviction policy and a size metric (§1.3.1).
- Add deletion/lifecycle for `observation_rank_embeddings` (§1.3.2).
- Specify `ON CONFLICT` write-through semantics and NEW-id write-through ordering
  relative to the adjudication transaction (§1.3.3–4).
- Fix the §4.2 evidence-collapse wording; answer analysis open question 3 in the
  design.
- State the statement-immutability precondition.

### 1.6 Optional improvements

- Read-side dims validation on durable rows.
- Note (as a documented non-goal, to avoid coupling) that the durable cache and the
  P1 observation embed use the same default model over the same text today
  (`obs_label` is written as a copy of `statement`,
  `observation_adjudication.py:711-721`) — a future unification could halve
  observation embed spend, but only if the obs-label contract is pinned to
  `statement`.
- Acceptance criteria: add a memory-bound test or at least assert the cache-size
  metric exists.

---

## 2. `plan/designs/pipeline_checkpointing_design.md`

### 2.1 Verdict

**Accept with changes — one blocking correctness defect (§2.2.1) plus a false
invariant claim (§2.2.2).** The L/E two-phase shape (analysis option D) is the right
call and matches the BEAM evidence; the defects are in the resume selector and the
readiness story, both fixable in the doc.

### 2.2 Correctness risks

**2.2.1 (Blocking) The Phase E selector cannot be "embedding_ref missing".**
§4.2 defines embed work as "relations with label for generation AND embedding_ref
missing." Against the actual schema this is wrong: `_STAMP_FACT_LABEL` sets
`fact_label_embedding_ref = relation_id::text` and nothing ever clears it
(`fact_catalog.py:486-495`). After the first generation the ref is permanently
non-NULL. Consequence on any re-generation (label version bump or embedding model
change): Phase L stamps the new `fact_label_version`, Phase E's "ref missing"
selector matches **nothing**, and the design's own failure table ("Generation pin
change → selectors pick all rows for new generation", §6) is not delivered. Lance
keeps serving the previous generation's vector under a Postgres row stamped current —
permanent, silent false readiness. Fix (pick one, state it in the design):

- **(a) Recommended:** add `fact_label_embedding_version text` (and
  `obs_label_embedding_version` for symmetry); Phase E selector is
  `embedding_version IS DISTINCT FROM :generation`; stamp it only after the Lance
  upsert. This also enables the pin split in §2.5.1.
- **(b)** Phase L explicitly sets `fact_label_embedding_ref = NULL`; Phase E re-sets
  it. Works, but widens the stale-Lance window semantics of §2.2.2 and loses the
  pin-split option.

**2.2.2 §4.5 claims an invariant the query path does not enforce.** "Semantic search
over facts MUST treat missing embedding ref as 'not in channel'" — but fact search
goes straight to Lance (`FactIndexPort.search_facts`,
`src/rememberstack/ports/p1_index.py:139`); no query path reads the PG ref. Actual
readiness semantics are **Lance-row presence**:

- *First-time labeling:* correct by construction — the row isn't in Lance until
  Phase E, so labeled-but-unembedded facts are invisible. §4.5 holds.
- *Re-generation:* the previous generation's row (old label, old vector) remains
  fully queryable during the entire L→E window — a window this design deliberately
  widens from milliseconds to potentially hours. That is arguably *desirable* (no
  availability gap during re-embedding), but it directly contradicts §4.5 as
  written ("Labeled-but-unembedded relations are not queryable via Lance").

Fix: rewrite §4.5 to state the real rule — readiness is presence in Lance; the PG
stamp is a progress/replay marker, not a query-path gate; during re-generation the
prior generation's rows remain queryable until replaced (stated as accepted
behavior). A cold reader must not come away believing unembedded ⇒ invisible.

**2.2.3 Lock/selector race under per-batch locking (§4.4).**
`relations_for_labeling` filters deployment-wide relations by evidence-in-doc
(`fact_catalog.py:466-481`); a relation evidenced in two documents is selected by
both doc jobs. Today the session-scoped lock held across the whole pass
(`fact_catalog.py:171-190`) serializes them, so the second job's selector sees the
stamp. §4.4's preferred "hold lock across a batch" breaks that: between batches, two
jobs can both select the same unstamped relation → duplicate label LLM calls,
racing double-stamps (same content at temp 0 — benign result, real double spend),
and concurrent Lance upserts of one `fact_id`. The design must choose and state:
**re-run the selector under the lock at each batch boundary** (claim work under the
lock, release between batches), or explicitly accept bounded duplicate spend as the
price of shorter critical sections. Silence here will produce an implementation
that passes single-worker tests and double-bills in production.

### 2.3 Missing failure modes

1. **Embedding-model config change between Phase L and Phase E** (worker restart with
   new `REMEMBERSTACK_P1_EMBEDDING_MODEL`). With the fused pin, the new generation
   string re-selects everything for **re-LLM labeling** even though label text is
   model-independent — correct but maximally wasteful. Handled cleanly only by the
   pin split (§2.5.1).
2. **Phase L visibility.** Once `fact_label` is stamped early, any consumer reading
   `relations.fact_label` (hydration, operator queries) sees labels hours before the
   vector exists. Benign — the label is valid content — but the design should say so
   in one sentence, since today label-visible implies embedded.
3. **Batch response misalignment.** Per-batch embed → stamp loops must keep the
   `strict` zip discipline of the current code (`p1.py:210-214`); a short vector
   response silently paired would stamp refs for vectors never written.

### 2.4 Implementation hazards vs. current code

- `record_fact_label` must be **split**: today it stamps label + version + ref in one
  statement (`fact_catalog.py:486-495`); Phase L needs label+version only, Phase E
  the embed marker. `_STAMP_OBSERVATION_EMBEDDING` needs the symmetric change under
  fix (a). The design's touchpoints (§10) name `fact_catalog.py` but not this split —
  make it explicit, it is the heart of the change.
- Acceptance criterion 1 ("no re-LLM after kill/restart") requires the Phase L stamp
  to **commit per relation** (or per small batch). `record_fact_label` already opens
  its own transaction per call (`fact_catalog.py:219`) — compatible, but the design
  should state the commit granularity; §7's open question 2 in the analysis ("one
  commit per relation vs per batch of 10") is left unanswered in the design.
- Acceptance criterion 3 (batch ≤ 1024) implies changes to `EmbedClaimsHandler`,
  which today embeds **all** of a document's claims in one provider call
  (`p1.py:91-97`) and stamps all at the end (`p1.py:113-116`). §5 gives the
  normative pattern but §10 doesn't list the concrete change; add it.
- The `label_lock` is a session advisory lock on a dedicated connection — crash-safe
  (PG releases on disconnect), so per-batch reacquisition is viable; note the churn
  cost is negligible relative to LLM calls.

### 2.5 Concrete required changes

- **(P0)** Replace §4.2's "embedding_ref missing" with an explicit embed-generation
  marker — recommend `fact_label_embedding_version` / `obs_label_embedding_version`
  (§2.2.1 fix (a)).
- **(P0)** Rewrite §4.5 readiness to match the real enforcement point
  (Lance presence; re-generation stale-window stated as accepted behavior) (§2.2.2).
- **(P1)** **Split the pin.** Phase L pin = `FACT_LABEL_VERSION`; Phase E pin =
  `FACT_LABEL_VERSION + embedding_model`. The fused pin (§3 scope table) forces
  re-LLM of every label on an embedding-model rotation — at the system's stated
  scale that is the entire label spend, avoidable by construction. This is a
  simplification within full scope, not phasing.
- **(P1)** Specify the per-batch lock/selector discipline (§2.2.3).
- **(P1)** State Phase L commit granularity; answer analysis open question 2.
- **(P1)** Rule 2 compliance: "(future option)" in the §3 scope table and "defer" in
  §8 are deferral framing — restate as scope boundaries/rejected alternatives
  ("child processing_state rows per fact: rejected — reasons; a documented
  alternative, not a later phase").
- **(P2)** Add `EmbedClaimsHandler` explicitly to §10 touchpoints; one sentence on
  Phase L label visibility; carry the strict-zip guard into acceptance tests.

### 2.6 Optional improvements

- A `labels_done / embeds_done` progress counter (§7) is worth making *recommended*
  rather than optional: the BEAM symptom was precisely "opaque hang with only
  cost-ledger crumbs," and the counter is the operator-facing half of the fix.
- Consider stating that Lance `upsert_facts` is idempotent by `fact_id` as a named
  precondition (the §6 "re-upsert then stamp" recovery silently relies on it).

---

## 3. `plan/analysis/relation_fact_labels_in_p1.md`

### 3.1 Is keep-vs-drop argued soundly?

**Yes.** The analysis correctly reads `p2_graph_design.md` §6 as a decision about
*where vectors live*, not *whether LLM prose is required*; correctly identifies that
dropping relations from P1 removes a designed entry channel
(`retrieval_design.md` search targets; §6's "Lance = entry" division of labor) and
therefore demands a retrieval eval, not a smoke-cost argument; and §5's point that
observations must stay regardless (relation-only drop is the only coherent R2) is
right — observations have no graph home at all.

### 3.2 Is the deterministic S1/S4 recommendation sound vs. LLM?

**Yes — and the analysis understates its own strongest argument.** The current label
prompt hands the LLM *only* the triple: `"{subject} —[{predicate}]→ {object}"`
(`p1.py:40-43`), and the selector fetches only canonical names + predicate
(`fact_catalog.py:466-481`). The LLM therefore contributes **zero information** over
a template — its only possible value is surface fluency, which the S4 lexicon buys
deterministically. The analysis should say this outright. It also exposes a
discrepancy worth recording: `p2_graph_design.md` §6's example label ("Alice Novak
works at Acme **as VP of Engineering**") implies qualifier enrichment the
implementation neither does nor can do with its current inputs — the binding
addendum must touch that §6 prose ("regenerated when adjudication materially
changes the relation — one short sentence") so the design and the mechanism agree.

Two gaps:

1. **Canonical-name drift.** Labels bake in `canonical_name` at label time; a later
   entity merge/rename leaves stale labels under *both* L1 and L2/L3. Deterministic
   templates make recompute free — a further argument for the lean — but the
   invalidation trigger (re-label on rename) is specified nowhere and belongs in the
   binding addendum alongside `fact_label_template_version`.
2. The §3 stability rule (pure function of triple + registry version) is sound and
   matches D63 generation-pin thinking.

### 3.3 What eval must pass before binding

The §8 plan (Recall@k of gold `relation_id`, S1 vs LLM; cost; noise) is necessary
but **incomplete in one important way: it must evaluate the fused recipe, not the
vector channel alone.** Retrieval fuses BM25 + semantic via RRF
(`p2_graph_design.md` §6 pipeline and reranking). S1's biggest quality risk is
*lexical*: "Alice works_for Acme" will not BM25-match "who works at Acme?", while
both an LLM label and an S4 surface form will. Vector-only Recall@k will overstate
S1. Required before binding:

- Recall@k under the shipped recipe (`relation_hybrid_rrf`) for **S1 vs S4 vs L1**,
  on a fixed relation-question set (synthetic + LoCoMo-like), including
  slug-predicate cases (`works_for`, `part_of`, `related_to`).
- The cost/wall-time and claim-vs-relation noise metrics as proposed.
- A drop decision (R2/R3) additionally requires the claims/obs-channel coverage
  study the analysis already demands — agreed.

### 3.4 Disagreement with the lean

One, minor: **default to S4, not S1-first.** The analysis proposes trialing S1 and
falling back to S4 if the eval fails. Given (a) the lexical-channel exposure above
and (b) that the D18 predicate registry is governed and bounded — so requiring a
`surface_verb` at predicate-registration time is cheap and one-off — S4 with S1 as
the automatic fallback for predicates lacking a surface form is the better default.
It removes the most predictable eval failure mode up front instead of scheduling a
second round-trip. Everything else in §7's conclusions I agree with, including the
prohibition on dropping relations as a smoke optimization.

---

## 4. Prioritized fix list

**P0 — blocks acceptance**

1. *Checkpointing §4.2:* replace "embedding_ref missing" with an explicit
   embed-generation marker (`fact_label_embedding_version` /
   `obs_label_embedding_version`); the current selector is wrong against
   `fact_catalog.py:486-495` and silently breaks every re-generation.
2. *Checkpointing §4.5:* rewrite readiness — enforcement is Lance-row presence, not
   the PG ref; state the re-generation stale-window as accepted behavior.
3. *Rank cache:* define a process-local cache bound/eviction policy and size metric
   (long-lived adjudicator ⇒ unbounded growth; 4096-dim ⇒ ~16 KB/vector).
4. *Rank cache:* deletion/lifecycle story for `observation_rank_embeddings`
   (cascade or explicit inclusion in the deletion design).
5. *Both designs:* remove Rule 2 phase framing ("phase 1/2", "v1", "v1.1",
   "future option", "defer") — restate as one complete mechanism plus explicit
   non-goals; sequencing belongs in `plan/plans/`.

**P1 — should fix before implementation PR**

6. *Checkpointing:* split the label pin from the embed pin (label =
   `FACT_LABEL_VERSION`; embed = `FACT_LABEL_VERSION + embedding_model`) so an
   embedder rotation never re-LLMs labels.
7. *Checkpointing §4.4:* specify selector-under-lock discipline per batch, or
   explicitly accept bounded duplicate spend.
8. *Checkpointing:* state Phase L commit granularity (per relation vs. small batch).
9. *Rank cache:* `ON CONFLICT DO NOTHING` write-through semantics; NEW-id
   write-through ordered after/inside the adjudication transaction.
10. *Rank cache §4.2:* fix the evidence-collapse wording (no row is created); answer
    analysis open question 3.
11. *Rank cache:* add the three-path severity argument (exact-match precedes rank;
    ladder reads real texts; top-k truncation ⇒ duplicate) to the design body.
12. *Labels analysis:* extend the eval to the fused BM25+vector recipe; add
    canonical-name-drift invalidation to the binding addendum scope; record the
    zero-information-LLM observation and the p2 §6 prose discrepancy.

**P2 — non-blocking**

13. *Rank cache:* statement-immutability stated as precondition; read-side dims
    check on durable rows.
14. *Checkpointing:* add `EmbedClaimsHandler` to §10 touchpoints; upgrade the
    progress counter from optional to recommended; name Lance upsert idempotency as
    a precondition.
15. *Rank cache:* documented non-goal noting the rank-cache / P1-obs-embed overlap
    (same model, same text today) as a potential future unification.
