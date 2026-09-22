# D131 — Cross-Turn Conversational Anaphora and Question-Affirmation Resolution in Claim Extraction

**Status:** accepted, 2026-09-22; implementation must land through a reviewed PR.
**Analysis:** [problem, forensic evidence and alternatives](../analysis/cross_turn_conversational_anaphora_analysis.md).
**Decision:** [D131](../../decisions.md#d131-cross-turn-conversational-anaphora-and-question-affirmation-resolution-in-claim-extraction).

---

## 1. Problem and Scope

In conversational transcripts and multi-party dialogues, speakers routinely communicate using anaphora across turns:
- Speaker A asks a question establishing a referent: *"Is that your third one?"*
- Speaker B affirms: *"Yep! I chose to write about this because it's really personal. It's about loss, identity, and connection."*

Under the existing two-stage extraction architecture (D31/D119):
1. **Selection** evaluates individual propositions and drops questions (`drop_question`) because questions are not factual assertions.
2. **Claimify** receives the kept propositions alongside the full target chunk bundle, same-section neighbours, and passage catalog. However, because `_CLAIMIFY_PROMPT` lacked explicit rules for cross-turn conversational anaphora and question-affirmations, models treated the antecedent in the preceding question as hazardous or ambiguous context. Models replaced anaphoric demonstratives (`"this"`, `"that"`, `"it"`, `"one"`) with vague generic nouns (*"a story"*).

This causes severe downstream degradation:
- The specific entity (*"Joanna's third screenplay"*) is never extracted into the `claims` catalog.
- The observation attached to the subject is generic (*"Joanna said she chose to write a story..."*).
- Lexical and vector search fail to retrieve the observation when users ask about the specific work or milestone (e.g., *"What is Joanna's third screenplay about?"*), causing retrieval to surface irrelevant older works (e.g., her second screenplay from Session D4) that explicitly mention *"screenplay"*.
- The entity layer (E3) fails to create or resolve an entity for the work.

D131 establishes the binding extraction contract for resolving cross-turn conversational anaphora and question-affirmations into self-contained standalone claims.

---

## 2. Conversational Anaphora and Multi-Hop Resolution Contract

The Claimify worker prompt (`_CLAIMIFY_PROMPT`) must explicitly instruct models on the following semantic resolution rules:

1. **Unambiguous Affirmative Commitment:**
   When an utterance affirmatively commits to the premise of a preceding question or dialogue turn (e.g., Speaker A asks *"Is that your third one?"* regarding a screenplay, and Speaker B replies *"Yep! I chose to write about this because it's really personal"*), the affirmative commitment (*"Yep!"*, *"Yes"*, *"Exactly"*, *"That's right"*) confirms the referent.
   Demonstratives and pronouns such as `"this"`, `"that"`, `"it"`, or `"one"` in the answering turn must be resolved to the specific antecedent established by the dialogue (e.g., *"her third screenplay"*), rather than degrading into a vague generic noun (*"a story"*, *"a project"*, *"an item"*).
   Merely answering a question without affirming its premise does not license binding.

2. **Two-Hop Anaphora Resolution within the Bundle:**
   In dialogue, questions themselves frequently contain anaphoric pronouns (e.g., Nate asks *"Is that your third one?"*, where *"one"* refers to *"screenplay"* established in D12:10 or D12:12).
   Claimify must resolve multi-hop referential chains across the supplied bundle:
   - Hop 1: Answering turn demonstrative (*"this"*) binds to question referent (*"third one"*).
   - Hop 2: Question pronoun (*"one"*) binds to the specific work kind (*"screenplay"*) established in the preceding dialogue turn, neighbour, or cited reference card.
   - Result: *"Joanna said she chose to write her third screenplay because it was really personal..."*.
   Both hops must be strictly supported by the supplied bundle, never outside knowledge.

3. **Denials, Corrections, and Alternative Referents:**
   If the speaker negates or corrects the question:
   - Correction (e.g., *"No, that's actually my fourth one"*): the model extracts the speaker's corrected assertion (*"her fourth screenplay"*), never the premise of the rejected question (*"third screenplay"*).
   - Direct Denial without alternative (e.g., *"No, that's not my third one"*): the model emits the attributed negative stance only if it constitutes a specific factual proposition (*"Joanna said that is not her third screenplay"*); otherwise, omit.

4. **Hedges, Uncertainty, and Affirmation Discourse Markers:**
   - If the speaker deflects, hedges, or expresses doubt (*"Not sure yet"*, *"Maybe someday"*, *"Hard to say"*), the model must NOT bind the question's premise as a settled fact.
   - Affirmation particles used as discourse markers (e.g. Turn opening with *"Yeah, it's nice to see friends"*, responding to an unshown topic) must NOT be bound to an unrelated antecedent.
   - If the surrounding dialogue leaves multiple competing referents plausible, the candidate must be omitted per D31's ambiguity rule.

5. **Standalone Completeness:**
   Every claim must stand alone so that a reader or vector search engine understands who and what it refers to without needing the surrounding dialogue. Dropping an established antecedent turns a specific factual assertion into noise.

---

## 3. Preservation of Specific Entities, Ordinals, and Qualifiers

1. **Ordinal and Work Specificity:**
   When dialogue establishes a numbered or ordinal milestone (*"third screenplay"*, *"second marathon"*, *"fourth album"*, *"2024 tax filing"*), the standalone claim must explicitly preserve the ordinal number and the specific entity kind.
   Models must never drop an established ordinal or specific noun in favor of a vague generalization (e.g., replacing *"third screenplay"* with *"a story"* or *"screenplay"*).

2. **Permitted Multi-Sentence Coherence:**
   Under D119, coherent multi-sentence assertions (e.g., motivation and core themes) *may* be formulated together as one claim when clearly connected:
   > *"Joanna said she chose to write her third screenplay because it was really personal, and that it is about loss, identity, and connection."*
   However, D119 permits coherence; it does not mandate combining every related assertion if they are independently meaningful.

---

## 4. Source Passage Citation and Complete Evidence Chain (D32 / D119)

### 4.1 Complete Citation Chain in `source_refs`
Under D119, a standalone claim that resolves an antecedent across turns must cite all passages establishing the complete evidence chain:
1. The origin passage (`source_refs[0]`): the answering turn overlapping the Selection keep.
2. Supporting passage(s): the turn establishing the ordinal/premise (Speaker A's question).
3. Supporting passage(s): any preceding body passage, neighbour, or cited reference card establishing the work kind (e.g. *"screenplay"*), if not present in the question turn itself.

This ensures the stored evidence matches the gold benchmark evidence shape (e.g. `['D12:13', 'D12:14']`).

### 4.2 Deterministic Token Grounding (D32) vs. Semantic Verification Layers
It is critical to distinguish the mechanical grounding gates from semantic verification:
1. **Layer-2 Deterministic Token Grounding (D32 / D119):**
   - In multi-turn transcripts, turns within a chunk belong to `target_chunk` and require no `added_context` entry.
   - Antecedents in immediately preceding/succeeding same-section chunks belong to the grounding elements union (`previous_same_section_neighbour`, `next_same_section_neighbour`).
   - Words drawn from cited reference cards enter the union via `card_passage_texts`.
   - The `source_kind` tag is advisory per D32/D119; it does not enable or veto matching.
   - Tokens appearing in the grounding union pass mechanical token verification (`_failed_added_context_tokens`) without triggering `ADDED_CONTEXT_UNVERIFIED`.
2. **Layer-3 Model Entailment Self-Verdict:**
   - Mechanical token presence does not guarantee semantic entailment.
   - The model must independently evaluate `entailment_self_verdict=True` only when the complete cited evidence actually supports the resolved assertion.
3. **Layer-4 Sampled Independent Audit:**
   - Independent verification pipelines audit extracted claims against source spans to catch semantic over-binding or hallucinated antecedent links.

---

## 5. Component Generation Invalidation (D56)

1. **`E2_EXTRACTOR_VERSION` Bump:**
   Because Claimify prompt changes alter the extraction semantics of kept propositions, the implementation PR must bump `E2_EXTRACTOR_VERSION` in `src/rememberstack/workers/e1.py` (e.g., appending `:d131-anaphora-1`).
   This ensures that:
   - D56 `extraction_input_hash` safely invalidates existing cached chunk extractions on re-ingestion, preventing reuse of stale, generic claims.
   - Unchanged inputs within the new generation continue to reuse occurrences cleanly.
2. **Benchmark Protocol Validation:**
   Update `EXPECTED_INGEST_COMPONENT_VERSIONS["extract_claims"]` and `["ground_claims"]` in `src/tests/benchmarks/test_locomo_protocol.py` to match the new version string.

---

## 6. Alternatives Survey and Economic Estimates

### 6.1 Alternatives Considered and Rejected
1. **Relax Selection to Keep Questions:**
   Rejected. Questions are interrogatives, not truth-assertable propositions. Keeping them would pollute the claim catalog with speculative statements (*"Nate asked if that was her third screenplay"*).
2. **Query-Time Anaphora Resolution in Answer Agent:**
   Rejected. Violates D1/D48. If stored claims are generic (*"a story"*), vector and BM25 search fail to retrieve the observation into top-$K$. The answering agent cannot repair missing context.
3. **Dedicated E1 Dialogue-Rewriting Pass:**
   Rejected. Pre-processing transcripts with a conversation-rewriter LLM call before chunking would double E1 latency and cost, while obscuring verbatim source character offsets.
4. **Explicit Claimify Question-Affirmation Contract (D131 — Selected):**
   Selected. Operates entirely within the existing Claimify prompt and call, preserving exact provenance, zero extra worker stages, and zero additional LLM invocations.

### 6.2 Economic and Latency Estimates
1. **Zero Additional Ingestion Calls:**
   Requires no new worker stages, no extra scheduled calls, and no secondary LLM verification pass.
2. **Token Overhead (Estimated):**
   The prompt addition in `_CLAIMIFY_PROMPT` adds approximately 110 to 130 prompt tokens. At cached input rates on Luna/Vertex/OpenRouter, the cost impact is estimated at < $0.0001 per chunk.
3. **Latency (Estimated):**
   Model execution latency is estimated to remain within normal variance (±5%) since output claim lengths are comparable.
4. **No Schema Changes:**
   The database schema (`claims`, `chunk_claims`, `claim_extraction_decisions`, `observations`, `entities`) requires no migrations.

---

## 7. Test Matrix and Verification Gates

Implementation PRs must provide the following behavioral, deterministic, and live verification gates:

1. **Unit Prompt Contract Proofs:**
   Verify that `_CLAIMIFY_PROMPT` contains the exact conversational anaphora and question-affirmation guidance, and that prompt formatting renders cleanly without template syntax errors.
2. **Behavioral Extraction Proofs (Positive and Negative Boundaries):**
   - **Affirmation:** Speaker A: *"Is that your third one?"* $\to$ Speaker B: *"Yep! It's about loss."* $\implies$ extracts *"Joanna said her third screenplay is about loss"*.
   - **Correction:** Speaker A: *"Is that your third one?"* $\to$ Speaker B: *"No, this is actually my fourth."* $\implies$ extracts *"Joanna said this is her fourth screenplay"*, never third.
   - **Direct Denial:** Speaker A: *"Is that your third one?"* $\to$ Speaker B: *"No, that's something else."* $\implies$ does not bind third screenplay as an established work.
   - **Hedge / Uncertainty:** Speaker A: *"Is that your third one?"* $\to$ Speaker B: *"Maybe someday, I haven't decided yet."* $\implies$ does not bind third screenplay.
   - **Ambiguity:** Speaker A: *"Did you like it?"* $\to$ Speaker B: *"Yes."* with multiple competing items $\implies$ candidate omitted.
   - **Bare Affirmation:** Verify that a short response like *"Yep!"* is handled appropriately by Selection and Claimify.
3. **Grounding Gate Proofs:**
   Verify that a candidate claim resolving demonstratives to antecedents established in a preceding question turn passes `_grounded_claim` without being rejected by `ADDED_CONTEXT_UNVERIFIED`.
4. **Entity Layer (E3) Resolution Verification:**
   Verify that during entity resolution (E3), a claim explicitly naming *"her third screenplay"* causes E3 to extract or resolve an entity mention for *"Joanna's third screenplay"*, rather than leaving the work unrepresented in `entities`.
5. **Loss Ledger Invariants:**
   Verify that valid cross-turn anaphora claims are not ledgered as `claimify_omitted` or `grounding_rejected`.
6. **Component Version & Readiness:**
   Verify `E2_EXTRACTOR_VERSION` bump invalidates stale cache and passes readiness checks in `test_pipeline_readiness.py` and `test_locomo_protocol.py`.
7. **Live Benchmark Validation:**
   Re-running ingestion on `conv-42/d12` must extract a claim containing `"third screenplay"` and `"loss, identity, and connection"`, resolve the entity in E3, and enable `facts_context` retrieval to resolve `conv-42/qa/0094`.
