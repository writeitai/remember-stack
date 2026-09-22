# D131 — Cross-Turn Conversational Anaphora and Question-Affirmation Resolution in Claim Extraction

**Status:** proposed design, 2026-09-22; implementation must land through a reviewed PR.
**Analysis:** [problem, forensic evidence and alternatives](../analysis/cross_turn_conversational_anaphora_analysis.md).
**Decision:** [D131](../../decisions.md#d131-cross-turn-conversational-anaphora-and-question-affirmation-resolution-in-claim-extraction).

---

## 1. Problem and Scope

In conversational transcripts and multi-party dialogues, speakers routinely communicate using anaphora across turns:
- Speaker A asks a question or introduces a referent: *"Is that your third one?"*
- Speaker B affirms or responds: *"Yep! I chose to write about this because it's really personal. It's about loss, identity, and connection."*

Under the existing two-stage extraction architecture (D31/D119):
1. **Selection** evaluates individual propositions and drops questions (`drop_question`) because questions are not factual assertions.
2. **Claimify** resolves pronouns and formulates standalone claims. However, because `_CLAIMIFY_PROMPT` lacked explicit rules for cross-turn conversational anaphora and question-affirmations, models treated the dropped question as absent or hazardous context. To avoid unverified additions, models replaced anaphoric demonstratives (`"this"`, `"that"`, `"it"`, `"one"`) with vague generic nouns (*"a story"*).

This creates severe downstream degradation:
- The specific entity (*"Joanna's third screenplay"*) is never extracted into the `claims` catalog.
- The observation attached to the subject is generic (*"Joanna said she chose to write a story..."*).
- Lexical and vector search fail to retrieve the observation when users ask about the specific work or milestone (e.g., *"What is Joanna's third screenplay about?"*), causing retrieval to surface irrelevant older works (e.g., her second screenplay) that explicitly mention *"screenplay"*.
- The entity layer (E3) fails to create or resolve an entity for the work.

D131 establishes the binding extraction contract for resolving cross-turn conversational anaphora and question-affirmations into self-contained standalone claims.

---

## 2. Conversational Anaphora and Question-Affirmation Resolution Contract

The Claimify worker prompt (`_CLAIMIFY_PROMPT`) must explicitly instruct models on the following semantic resolution rules:

1. **Question-Affirmation Antecedent Binding:**
   When an utterance affirms or answers a preceding question or dialogue turn (e.g., Speaker A asks *"Is that your third one?"* regarding a screenplay, and Speaker B replies *"Yep! I chose to write about this because it's really personal. It's about loss, identity, and connection"*), the affirmative response (*"Yep!"*, *"Yes"*, *"Exactly"*, *"That's right"*) confirms the referent from the question.
   Demonstratives and pronouns such as `"this"`, `"that"`, `"it"`, or `"one"` in the answering turn must be resolved to the specific antecedent established by the dialogue (e.g., *"her third screenplay"*), rather than degrading into a vague generic noun (*"a story"*, *"a project"*, *"an item"*).

2. **Negative and Hedged Non-Affirmation Boundary:**
   If the speaker negates, deflects, or expresses uncertainty (*"No, that's something else"*, *"Not quite"*, *"Maybe someday"*), the model must NOT bind the question's premise as true. The resulting claim must accurately capture the speaker's actual statement and attribution.

3. **Standalone Completeness:**
   Every claim must stand alone so that a reader or vector search engine understands who and what it refers to without needing the surrounding dialogue. Dropping an established antecedent turns a specific factual assertion into noise.

---

## 3. Preservation of Specific Entities, Ordinals, and Qualifiers

1. **Ordinal and Work Specificity:**
   When dialogue establishes a numbered or ordinal milestone (*"third screenplay"*, *"second marathon"*, *"fourth album"*, *"2024 tax filing"*), the standalone claim must explicitly preserve the ordinal number and the specific entity kind.
   Models must never drop an established ordinal or specific noun in favor of a vague generalization (e.g., replacing *"third screenplay"* with *"a story"* or *"screenplay"*).

2. **Multi-Span Coherence:**
   Under D119, one coherent assertion that spans multiple sentences in the answering turn (e.g., the motivation to write the screenplay and its three core themes) must be formulated as a single self-contained claim:
   > *"Joanna said she chose to write her third screenplay because it was really personal, and that it is about loss, identity, and connection."*

---

## 4. Source Passage Citation and Grounding Invariants (D32 / D119)

1. **Citation of Supporting Passages:**
   Under D119, the origin reference (`source_refs[0]`) must be an origin-eligible target passage overlapping the Selection keep (Speaker B's answering turn).
   The preceding dialogue passage that establishes the antecedent (Speaker A's question turn) must be cited as a supporting passage in `source_refs`.

2. **Deterministic Token Grounding (D32):**
   In multi-turn transcripts, dialogue turns within a chunk are present in `target_chunk`.
   Antecedents established within the chunk belong to `target_chunk` and require no `added_context` entry.
   If an antecedent word is drawn from an immediately preceding same-section chunk or cited earlier reference card, it belongs to the D32 layer-2 `grounding_elements` union (`previous_same_section_neighbour`, `card_passage`).
   When tagged with its proper `source_kind`, it passes deterministic token verification (`_failed_added_context_tokens`) cleanly and will not be rejected by `ADDED_CONTEXT_UNVERIFIED`.

---

## 5. Economic, Latency, and Pipeline Boundaries

1. **Zero Additional Ingestion Calls:**
   This contract modifies the prompt guidance of the existing Claimify stage. It requires no additional model calls, no new worker stages, and no secondary LLM verification pass.
   Ingestion latency and LLM call counts remain invariant.

2. **Token Overhead:**
   The prompt addition in `_CLAIMIFY_PROMPT` adds approximately 110 prompt tokens. At cached input rates on Luna/Gemma/Vertex, the cost impact is negligible (< $0.0001 per chunk).

3. **No Schema Changes:**
   The database schema (`claims`, `chunk_claims`, `claim_extraction_decisions`, `observations`, `entities`) requires no alterations. The fix operates entirely within the semantic decontextualization layer of the core engine.

---

## 6. Test Matrix and Verification Gates

Implementation PRs must provide the following deterministic and live verification gates:

1. **Unit Prompt Contract Proofs:**
   Verify that `_CLAIMIFY_PROMPT` contains the exact conversational anaphora and question-affirmation guidance, and that prompt formatting renders cleanly without template syntax errors.
2. **Grounding Gate Proofs:**
   Verify that a candidate claim resolving demonstratives to antecedents established in a preceding question turn passes `_grounded_claim` without being rejected by `ADDED_CONTEXT_UNVERIFIED`.
3. **Loss Ledger Invariants:**
   Verify that valid cross-turn anaphora claims are not ledgered as `claimify_omitted` or `grounding_rejected`.
4. **Live Benchmark Validation:**
   Re-running ingestion on `conv-42/d12` must extract a claim containing `"third screenplay"` and `"loss, identity, and connection"`, resolving `conv-42/qa/0094`.
