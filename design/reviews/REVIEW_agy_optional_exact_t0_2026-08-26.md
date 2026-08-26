# Adversarial Design Review: PR 307 (Record T0-Never-Merge and Reject Large-Corpus Exact-T0)

**Reviewer identity:** Antigravity (`agy`)  
**Date:** 2026-08-26  
**PR:** [writeitai/remember-stack#307](https://github.com/writeitai/remember-stack/pull/307)  
**Branch:** `origin/feat/d95-t0-exact-opt-in-proposal` vs `origin/main`  
**Review target:** Design-only diff across:
- [`decisions.md`](file:///Users/jpuc/code/moje/remember-stack/decisions.md) (D95 updates)
- [`plan/analysis/entity_identity_and_retrieval_analysis.md`](file:///Users/jpuc/code/moje/remember-stack/plan/analysis/entity_identity_and_retrieval_analysis.md) (§5.1)
- [`plan/designs/entity_identity_and_retrieval_design.md`](file:///Users/jpuc/code/moje/remember-stack/plan/designs/entity_identity_and_retrieval_design.md) (§3.1, §10)
- [`design/proposals/optional-exact-t0-accept.md`](file:///Users/jpuc/code/moje/remember-stack/design/proposals/optional-exact-t0-accept.md) (New unchosen proposal)
- [`plan/plans/entity_identity_and_retrieval.md`](file:///Users/jpuc/code/moje/remember-stack/plan/plans/entity_identity_and_retrieval.md) (Non-goals)
- [`design/README.md`](file:///Users/jpuc/code/moje/remember-stack/design/README.md) & [`design/proposals/README.md`](file:///Users/jpuc/code/moje/remember-stack/design/proposals/README.md) (Indices)

**Output path:** `/var/folders/wt/plp93ggs40586mdsvzzqy4c40000gp/T/grok-goal-6edec73546f9/implementer/REVIEW_agy_optional_exact_t0_2026-08-26.md`  
**Verdict:** **Approve**

---

## Executive Summary & Verdict

PR 307 formally solidifies the operator-mandated principle that **T0 exact-lemma matching generates candidate entity IDs and never acts as an auto-merge verdict**. It articulates the fundamental failure modes of pre-D95 exact-lemma auto-merging (Case A distinctive names vs Case B common names), rigorously disproves the intuition that exact-T0 should be re-enabled once a corpus grows large (the Birthday Paradox guarantees *more* collisions at scale, not fewer), and isolates the concept of an exact-T0 opt-in switch as an **unchosen, non-binding proposal** restricted to closed unique namespaces (SKUs, employee serial numbers).

Crucially, PR 307 protects the WP-I.1–WP-I.7 implementation sequence:
1. **D95 remains frozen and binding:** T0 produces candidates; repeats of known entities are cheaply accepted at T3 via profile vector embeddings without LLM judge calls; ambiguous, conflicting, or cold-start mentions escalate to T4.
2. **Rejection of large-corpus exact-T0 is mathematically sound:** At scale, name collisions peak, cold-start profiles for new homonyms are thinnest, and the blast radius of a single false merge is catastrophic across graph hops, relations, and D74 forget cascades.
3. **No scope creep into WP-I.5:** The proposal [`design/proposals/optional-exact-t0-accept.md`](file:///Users/jpuc/code/moje/remember-stack/design/proposals/optional-exact-t0-accept.md) is strictly unchosen and explicitly excluded from WP-I.5 deliverables.

**Verdict: Approve.** The analysis, design, decisions log, and proposal are coherent, mathematically sound, and internally consistent across all references.

---

## Direct Answers to Mandatory Questions

### 1. Is T0-never-auto-merge still the binding default?

**Yes.** T0-never-auto-merge is explicitly reinforced as the binding default throughout all primary architectural documents:

- **Decisions Log ([`decisions.md`](file:///Users/jpuc/code/moje/remember-stack/decisions.md) D95):**  
  > *"A name generates candidates. **T0 never auto-merges** (exact lemma only lists ids)... There is **no** common-name census and **no** 'turn exact-T0 on when the corpus is large' switch: a large store has more collisions; T3+profile is how repeats stay cheap."*
- **Binding Design ([`entity_identity_and_retrieval_design.md`](file:///Users/jpuc/code/moje/remember-stack/plan/designs/entity_identity_and_retrieval_design.md) §2, §3.1):**  
  > *"Exact match on `aliases.normalized_lemma` **lists** matching active **entity ids** (0, 1, or many)... **T0 never auto-accepts.** Same cleaned spelling is a clue, not a verdict. No common-name census. No 'distinctive lemma' shortcut."*
- **Analysis Doc ([`entity_identity_and_retrieval_analysis.md`](file:///Users/jpuc/code/moje/remember-stack/plan/analysis/entity_identity_and_retrieval_analysis.md) §5.1):**  
  > *"Binding remains D95 (T0 lists, never merges). The flag is an **unchosen proposal**... not WP-I.5 work."*
- **Implementation Plan ([`entity_identity_and_retrieval.md`](file:///Users/jpuc/code/moje/remember-stack/plan/plans/entity_identity_and_retrieval.md) WP-I.5):**  
  > *"WP-I.5: **T0 = candidate list only** (never auto-merge), after a recorded passing I.3+I.4 eval run... Hits = distinct active `entity_id`s."*
- **Proposal Document ([`optional-exact-t0-accept.md`](file:///Users/jpuc/code/moje/remember-stack/design/proposals/optional-exact-t0-accept.md)):**  
  > *"Status: open, unchosen — **not binding**, not implemented... Binding baseline: D95; T0 is a candidate list, never a merge."*

The architecture provides zero ambiguity: T0 is purely a blocking/candidate-generation tier.

---

### 2. Is enabling exact-lemma T0 because the corpus is large correctly rejected?

**Yes.** The rejection of "enable exact-T0 once the corpus is large" is thoroughly and correctly argued across §5.1 of the analysis, §3.1 of the design, and D95:

1. **The Birthday Paradox (Collision Rate Peaks at Scale):**  
   The intuitive belief that "large corpora contain mostly repeats of existing entities" ignores that the probability of homonym collisions grows superlinearly with entity population size. A personal diary may contain only one "Jan"; a 50,000-entity enterprise memory will contain multiple distinct "Jan"s, "John"s, and "James"es. Enabling exact-lemma auto-accept *because* the table is large activates unconditional merging at the exact regime where collision frequency is maximized.
2. **Extreme Asymmetry of Risk vs Reward:**  
   The computational cost T0 saves over T3 is negligible: exactly **one cosine similarity calculation** between the mention+claim embedding and the candidate profile vector. In return for saving a single vector dot product, exact-T0 risks a permanent false merge. The cost of one false merge is unbounded: every subsequent observation, relation, graph neighborhood hop, and D74 document forget cascade is permanently attached to the wrong `entity_id`.
3. **Amplified Cold-Start Vulnerability:**  
   When a second entity sharing a lemma (e.g., a newly hired employee named Jan) enters a large store, their initial profile is empty or thin. Under exact-T0, this second Jan is instantly and silently fused into the first Jan's entity record without profile comparison or judge adjudication.
4. **Production Hazard of Dormant Flags:**  
   Including a default-off exact-T0 flag in production code creates an operational hazard: teams facing T4 token costs or latency spikes flip the flag without recognizing that silent data corruption begins immediately.

---

### 3. Is the unchosen proposal's adoption trigger (closed unique namespace, not entity count) honest and not a back door into WP-I.5?

**Yes.** The unchosen proposal [`design/proposals/optional-exact-t0-accept.md`](file:///Users/jpuc/code/moje/remember-stack/design/proposals/optional-exact-t0-accept.md) is structured with strict, honest adoption boundaries:

1. **Closed Unique Namespace Requirement:**  
   Adoption is strictly conditioned on the tenant operating in an operator-asserted *closed unique namespace* (e.g., manufacturer SKU codes, employee ID numbers, inventory serials) where strings are engineered by policy to be globally unique. It explicitly forbids adoption on human given names, natural language entities, or polysemous shorthand (e.g., `SAP`).
2. **Mandatory Golden-Pair Eval Verification:**  
   The tenant's D22 golden evaluation suite must include same-lemma non-matches and pass with the flag enabled.
3. **Explicit Distinction from Identifier T0:**  
   The proposal distinguishes name-lemma exact matching from *Identifier T0* (e.g., exact matches on structured external identifiers like email, LEI, ORCID, ISBN). Identifier T0 is recognized as a separate, legitimate future path under D20 authority concepts, rather than conflating external IDs with lexical name lemmas.
4. **Hermetic Isolation from WP-I.5:**  
   - The proposal is explicitly listed in `design/proposals/README.md` as "Open — not implemented".
   - `decisions.md` D95 records: *"Keeping pre-D95 exact-hit as a manual, default-off flag is an unchosen proposal... do not ship it in WP-I.5."*
   - `plan/plans/entity_identity_and_retrieval.md` Non-goals explicitly mandates: *"shipping `t0_exact_accept` (or any exact-lemma auto-merge flag) in WP-I.5 — that idea is an unchosen proposal... and 'enable it after a large corpus' is a rejected trigger."*
   - No code, schema migrations, or tier configurations are allocated to WP-I.1 through WP-I.7 for this proposal.

There is no back door into WP-I.5.

---

### 4. Cold-Reader Gaps in Analysis §5.1, D95, or the Proposal

A comprehensive read reveals no logical gaps or architectural flaws. The following observations will help future implementers and reviewers maintain absolute clarity:

- **Granularity of Namespace Scoping in Mixed-Entity Stores (Proposal Observation):**  
  In [`optional-exact-t0-accept.md`](file:///Users/jpuc/code/moje/remember-stack/design/proposals/optional-exact-t0-accept.md), the hypothetical configuration is framed as a deployment-level flag on `resolver_versions.tier_config.t0_exact_accept`. In practice, almost all enterprise deployments contain mixed entity streams (e.g., structured SKU codes *and* unstructured human contacts). A deployment-wide boolean flag would either remain disabled or inadvertently expose human names to exact-T0 merging. The proposal's own warning (*"mixing 'this SKU is unique' with 'this given name is unique' is how the footgun returns"*) accurately flags this. Any future realization would need either namespace/entity-kind scoping or would naturally be superseded by structured Identifier T0 (LEI/email/ISBN).
- **Candidate Uniqueness vs Alias Provenance:**  
  Analysis §5.1 and Design §3.1 correctly emphasize counting *distinct active `entity_id`s*, not alias rows. Because an entity can possess both `source` and `llm_canonical` alias rows for the same normalized lemma, a single entity with multiple alias records generates exactly 1 candidate. This prevents false escalation to multiple-candidate T4 branches.
- **Fail-Safe Profile Invalidation (D74 Alignment):**  
  Analysis §5.1 notes that repeats of known persons are handled by T3 comparing mention+claim embeddings against candidate profiles. When D74 document forgetting invalidates a shared entity's profile, T3 cleanly falls back to fail-safe escalation (T4) rather than guessing on stale or empty embeddings.

---

## Detailed Technical Analysis

### Case A vs Case B: Why Lexical Uniqueness is a Transient Table Property

Analysis §5.1 presents a clear taxonomic breakdown of name lemmas:
- **Case A (Distinctive Lemma):** The store contains exactly one entity with spelling `sap` or a rare surname. The next mention feels like a repeat. Pre-D95 T0 merged this at confidence 1.0. However, lexical uniqueness is not an intrinsic property of the string; it is a temporary property of the database state at a single moment in time. The very first time a second real-world referent appears (e.g. father/son, a new company named SAP), exact-T0 silently merges them.
- **Case B (Common Lemma / Homonym):** High-frequency names (`John`, `Jan`, `James`) inevitably collide. Neither entity types nor exact lemmas can differentiate two people named John. Pre-D95 T0 permanently prevented profiles or LLM judges from evaluating them.

The proposed alternative of using a "common name stoplist" fails because:
1. Stoplist maintenance is an unscalable world census that varies across languages and locales (`Jan` is common in Czech, rare in English).
2. The critical error event occurs the *first time* a previously unique name collides (Case A transitioning to Case B). A static stoplist cannot predict which unique names will experience a collision.

By enforcing **T0 as candidate generation only**, both cases are resolved uniformly:
- **0 candidates:** Mint
- **1 candidate:**
  - Profile exists + embedding matches -> **T3 Accept** (cheap, no LLM)
  - Profile empty, conflicting, or thin -> **T4 Judge** -> (Match -> Merge; Different -> Mint 2nd ID)
- **>= 2 candidates:** **T4 Judge** / Disambiguate

---

## P0 / P1 Findings

**None.** There are zero P0 or P1 architectural flaws. The design diff is sound, defensive, and ready to merge.

---

## Nits & Non-Blocking Observations

1. **Nit (Documentation Link Verification):**  
   All relative markdown links across `decisions.md`, `design/README.md`, `design/proposals/README.md`, `design/proposals/optional-exact-t0-accept.md`, `plan/analysis/entity_identity_and_retrieval_analysis.md`, `plan/designs/entity_identity_and_retrieval_design.md`, and `plan/plans/entity_identity_and_retrieval.md` correctly resolve without broken paths.
2. **Nit (Whitespace):**  
   `git diff --check` across the branch diff is completely clean with zero trailing whitespace or formatting defects.

---

## Final Recommendation

PR 307 is approved as written. It provides the essential theoretical foundation and binding constraints preventing premature optimization and data-corrupting shortcuts during the execution of WP-I.1 through WP-I.7.
