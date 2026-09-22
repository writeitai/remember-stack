# Cross-Turn Conversational Anaphora in Claim Extraction — Analysis

**Date:** 2026-09-22  
**Status:** Non-binding working analysis; supports D131  
**Problem Area:** Core engine claim extraction (E1/E2), dialogue coreference, multi-turn antecedent binding, semantic search indexing

---

## 1. Executive Summary and Forensic Evidence

Forensic analysis of the LoCoMo benchmark re-run on `conv-42` (`locomo-v38-jev-conv42-dev`) identified a critical failure mode in core claim extraction for multi-turn conversations: **the loss of question-established referents when speakers answer using anaphoric demonstratives or pronouns.**

### 1.1 The Forensic Case (`conv-42/qa/0094`)

* **Question:** *"What is Joanna's third screenplay about?"*
* **Gold Answer:** `loss, identity, and connection`
* **Gold Evidence:** `['D12:13', 'D12:14']`

#### The Raw Transcript (`conv-42/d12.md`)
```markdown
[D12:12 | 7:49 pm on 20 May, 2022 | UTC assumed] Joanna: Yeah. It's so nice to have friends who understand and appreciate my work - it's priceless being able to talk about it together and receive feedback. Here's a look at what I've been working on – it's been quite a journey, but I made it!
Dataset-provided derived image caption for D12:12: a photo of a notepad with a dog on it and a pen
Dataset-provided derived image search query for D12:12: handwritten screenplay notebook.

[D12:13 | 7:49 pm on 20 May, 2022 | UTC assumed] Nate: Wow, that looks great Joanna! Is that your third one?

[D12:14 | 7:49 pm on 20 May, 2022 | UTC assumed] Joanna: Yep! I chose to write about this because it's really personal. It's about loss, identity, and connection. It's a story I've had for ages but just got the guts to write it. It was hard, but I'm so proud of it.

[D12:15 | 7:49 pm on 20 May, 2022 | UTC assumed] Nate: That sounds impressive. You really do like writing about sadness and loss don't you.
```

#### What Was Stored in the Production Database
Inspecting the PostgreSQL partition for chunk `9164906c-5b94-4db1-850e-3e6b2c82ece4`:
1. In `claim_extraction_decisions`:
   - `[D12:13]` (*"Is that your third one?"*) was dropped with `decision_type: selection_drop` and `reason: question`.
   - `[D12:14]` (*"I chose to write about this because it's really personal."*) was KEPT by Selection.
   - `[D12:14]` (*"It's about loss, identity, and connection."*) was omitted by Claimify or merged.
2. In `claims`:
   - `claim_id`: `4616d766-0047-4829-a8e1-a04ac302b0c6`
   - `source_span`: `I chose to write about this because it's really personal.`
   - `claim_text`: `"Joanna said she chose to write a story because it was really personal and that the story was about loss, identity, and connection."`
3. In `observations`:
   - `observation_id`: `02ec512a-3468-500e-beea-c9b27a800abb`
   - `subject_entity_id`: `67384bd3-1d2d-4984-aa38-0459d6a577ce` (Joanna)
   - `statement`: `"Joanna said she chose to write a story because it was really personal and that the story was about loss, identity, and connection."`
4. In `entities`:
   - No entity exists for `"Joanna's third screenplay"`. Only `"Joanna's previous screenplay"` and `"another movie script Joanna contributed to"`.

#### The Downstream Failure Cascade
When the answer agent queried `facts_context(entity_ids=[Joanna_id], k=15, query="Joanna's third screenplay subject or plot")`:
1. Observation `02ec512a` contains neither the word `"screenplay"` nor the word `"third"`. It contains only the generic phrase `"a story"`.
2. In Session D4, Joanna discussed her *second* screenplay:
   - Fact 1: *"Joanna said her new screenplay is somewhat similar to her previous screenplay."*
   - Fact 8: *"Joanna said her new screenplay is her own story."*
3. Both D4 facts literally contain the keyword `"screenplay"`. Both vector embedding similarity and BM25 lexical matching ranked D4 facts at #1 and #8.
4. Observation `02ec512a` ranked outside the top 15 candidates and was never presented to the answering agent.
5. The answering agent answered using the D4 facts:
   > *"Her own story. Joanna said it was somewhat similar to her previous screenplay."* $\to$ **INCORRECT**.

---

## 2. Root Cause Analysis

### 2.1 The Asymmetry Between Selection and Claimify
The two-stage extraction architecture (D31/D119) separates proposition selection from claim formulation:
- **Selection** evaluates each candidate span within the target chunk. It correctly drops questions (`drop_question`) because a question does not assert a fact.
- **Claimify** takes the kept propositions and formulates standalone claims.

However, in conversational dialogue, an answering turn often contains anaphora (`"this"`, `"that"`, `"it"`, `"one"`) whose referent was established in the *preceding question turn*. When Nate asked *"Is that your third one?"*, the referent (*"her third screenplay"*) was established. When Joanna responded *"Yep! I chose to write about this..."*, she explicitly affirmed Nate's question.

### 2.2 Why Existing Guidance Failed
In PR #400 (`plan/designs/multi_span_claim_extraction_design.md`) and `src/rememberstack/workers/e2.py`, the prompt contained:
```
Keep one coherent assertion together even when its support spans several
sentences. For example, statements about Joanna's third screenplay and its
three themes can support "Joanna's third screenplay explores loss, identity
and connection" when the source clearly connects them.
```
While this cited the exact third screenplay case as an example, it treated it as an illustration of **merging multiple sentences**, not **cross-turn question-affirmation coreference**.

Crucially, `_CLAIMIFY_PROMPT` warned:
```
Add only the context needed to identify the meaning. If the source leaves
several plausible interpretations, omit that candidate.
```
Fearing that replacing `"this"` with `"her third screenplay"` would be rejected as unverified added context, the model defaulted to the safest, vaguest generic noun: `"a story"`.

### 2.3 Grounding Gate Invariants (D32 / D119)
We inspected `_source_grounding_elements` and `_failed_added_context_tokens` in `src/rememberstack/workers/e2.py`:
- `_source_grounding_elements` includes:
  1. `target_chunk`: the full text slice of the target chunk (`document_md[chunk.char_start : chunk.char_end]`).
  2. `document_header`: deterministic document metadata header.
  3. `previous_same_section_neighbour` and `next_same_section_neighbour`.
  4. Typed `LocationElement` pairs.
  5. Supporting passages cited in `source_refs`.
- In LoCoMo transcripts, Nate's question `[D12:13]` and Joanna's reply `[D12:14]` sit in the **same chunk**.
- Therefore, all words in Nate's turn (`"third"`, `"one"`) are members of `target_chunk`!
- The antecedent entity word (`"screenplay"`) appears in the same chunk (`[D12:12]`) or in neighbouring context and reference cards.
- Replacing `"this"` with `"her third screenplay"` in `claim_text` is fully grounded:
  - If considered within target chunk, no `added_context` entry is required.
  - If cited via supporting passage references, the words exist in `grounding_elements` and pass token verification cleanly without triggering `ADDED_CONTEXT_UNVERIFIED`.

---

## 3. Survey of Alternatives

### Alternative A: Keep Questions in Selection
* **Mechanism:** Relax `_SELECTION_PROMPT` to emit `keep` for questions that introduce entities.
* **Why it fails:** Questions are not assertions. Emitting claims for questions violates the core spine invariant that claims record truth-assertable propositions. It would pollute the claim catalog with speculative interrogatives ("Is that your third one?").

### Alternative B: Query-Time Pronoun Resolution in Answer Agent
* **Mechanism:** Keep `"a story"` in the database, but instruct the answering agent to retrieve adjacent conversation turns and guess what `"a story"` refers to.
* **Why it fails:** Defeats the foundational premise of RememberStack (D1: clean, self-contained stored memory; D48: spine disposes). If stored claims are vague, vector embeddings match irrelevant queries and miss relevant ones. The answering agent cannot fix retrieval when the relevant record is not returned in top-$k$.

### Alternative C: Explicit Claimify Conversational Anaphora & Question-Affirmation Contract (Chosen)
* **Mechanism:** Add explicit, binding instructions to `_CLAIMIFY_PROMPT` requiring the model to resolve conversational anaphora when an affirmative turn confirms a question's antecedent, preserving specific entity names, ordinals, and qualifiers. Require citing the antecedent passage in `source_refs`.
* **Why it wins:**
  - Zero changes to storage schema or pipeline topology.
  - Zero extra LLM calls or latency overhead (handled within existing Claimify call).
  - Produces standalone claims with high semantic specificity for vector embedding, lexical search, and downstream entity resolution.
  - Preserves token grounding invariants under D32 and D119.

---

## 4. Verification and Acceptance Criteria

1. **Prompt Contract Verification:** Unit tests prove `_CLAIMIFY_PROMPT` explicitly instructs models on resolving question-affirmation antecedents across turns.
2. **Grounding Verification:** Unit tests prove claims resolving demonstratives to antecedents established in preceding turns pass deterministic D32/D119 grounding without `ADDED_CONTEXT_UNVERIFIED`.
3. **End-to-End Extraction:** Ingestion on `conv-42/d12` emits a claim explicitly naming `"Joanna's third screenplay"` and its themes `"loss, identity, and connection"`.
4. **Retrieval Precision:** `facts_context(entity_ids=[Joanna], query="Joanna's third screenplay")` ranks the D12 screenplay fact at #1, resolving `conv-42/qa/0094`.
