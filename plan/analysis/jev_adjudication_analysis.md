# Analysis: System One Jev for fact adjudication

**Status:** non-binding analysis, 2026-09-17.
**Scope:** Fact and observation adjudication in RememberStack (`FactAdjudicator`,
D118/D121/v38). Evaluating TypeSafe AI's System One model (`jev-latest`) as an
alternative to generative LLMs (`openai/gpt-5.6-luna`, `google/gemma-4-26b-a4b-it-maas`).

---

## 1. Problem: Generative LLMs are the wrong primitive for adjudication

In the RememberStack ingestion pipeline, workers process raw source documents
through structuring (E0), prefixing (E1), claim extraction (E2), and
normalization (E3), producing staged relation and observation assertions.
Adjudication (`FactAdjudicator`, D118) reconciles each incoming assertion
against candidate facts for the same entity, deciding:
1. Does the assertion support an existing fact, contradict an existing fact, or
   establish a new fact?
2. Does evidence justify changing world-time windows or reassigning support?

In both the benchmark runs (LoCoMo `conv-42`, runs v28 through v38) and live
ingestion, generative LLMs in the adjudication seat exhibit three fatal issues:

### A. Dominant token and dollar cost (97.7% of ingestion spend)
In audited conv-42 measurements:
- The adjudication stage consumed **34,475,491 input tokens** across 1,298 model
  calls.
- Adjudication accounted for **$9.16 out of the $12.40 total ingestion cost**
  (97.7% of the cost increase between v27 and v28).
- For hub entities with multiple existing facts (e.g. "User", "Nate", "Joanna"),
  the residue path repeatedly presents large context snapshots, paying compounding
  input token costs for every incoming assertion.

### B. Semantic drift on proposition equivalence (False Merges)
Generative LLMs confuse *shared topical context* with *propositional equivalence*:
- **Case 1 (Tournament Win):** Incoming claim `a80e17f7` asserted that Nate won
  the large tournament in late September. Candidate observation `1104873b`
  asserted that the tournament was a fun experience. The generative adjudicator
  attached support at **0.98 confidence** because both statements concerned the
  same tournament. Enjoying an event does not establish winning it; the proposition
  was lost in derived facts.
- **Case 2 (Final Win):** Incoming claim `58724598` asserted that Nate won the
  Valorant final. The adjudicator moved support to `3add09d3`, which asserted that
  Nate was in the final. Being in a final does not state that it was won.

A higher confidence threshold on generative models does not fix this: the model
was 98% confident in its error because its prompt framing encouraged finding
connections rather than testing logical entailment.

### C. Output schema and handle formatting failures
Generative models are required to output complex JSON matching `PromptFactDecision`
with attempt-local handles (`F1`, `N1`, `A1`, `C1`). As seen in PR #411, #415,
and #417, generative models frequently emit reserved handles out of turn (such as
inventing `F2` when only `F1` was supplied, or hallucinating new-fact handles),
requiring retry loops, rejection feedback prompts, and complex repair logic.

---

## 2. TypeSafe AI: System One decision primitives

TypeSafe AI provides **System One** models (flagship: `jev-latest`). Unlike
generative autoregressive LLMs, System One models do not synthesize open-ended
text, reasoning essays, or freeform JSON schemas. Instead, they evaluate
a supplied `state` against a map of typed `questions` and return calibrated
probabilities and categorical decisions:

1. **`Choice`**: Selects one option from a defined criterion map. Returns the
   selected option, the full probability distribution over all options, and a
   calibrated confidence score ($0.0$ to $1.0$).
2. **`Noul`**: Evaluates a yes/no condition. Returns the probability
   $P(\text{yes}) \in [0.0, 1.0]$.
3. **`Score`**: Rates state along ordered descriptive criteria levels, returning
   a probability-weighted expected score and confidence.

Crucially:
- **Parallel evaluation**: All questions over the same state execute in a single
  request in parallel.
- **Calibrated probabilities**: The model's distribution reflects actual
  semantic certainty, enabling rigorous confidence gating in code.
- **Code owns workflow**: Code handles data structures, mapping, and database
  mutations; the model is only consulted for the semantic judgment.

---

## 3. Applying Jev to RememberStack adjudication

Adjudication does not need text generation. Its core questions are strictly
decisional:

```
Incoming Assertion (A1): "Nate won the tournament in late September"
Candidate Facts:
  - F1: "the tournament was a fun experience" (said on 2023-09-28)
```

We map this directly into TypeSafe primitives over the prepared concise state:

### Question 1: Match selection (`Choice`)
- **Instructions:**
  "You are adjudicating an incoming assertion against candidate stored facts for
  the same entity. Determine whether the incoming assertion affirms the exact
  same core proposition and truth claim as one of the candidate facts, or if it
  is a new, distinct fact.
  - An assertion matches an existing fact ONLY if it asserts the exact same
    proposition, truth claim, and outcome (including attribution and qualifiers).
  - Sharing a topic, event, or entity (e.g. attending vs winning a tournament, or
    participating in a final vs winning it) is NOT a match: select NEW.
  - Contradictions (e.g. lost the tournament vs won the tournament) match the
    target fact with contradictory stance.
  - If no candidate fact represents the exact proposition, select NEW."
- **Criteria:**
  - `"F1"`: Candidate fact statement + world dates.
  - ...
  - `"NEW"`: "The assertion is a new proposition not represented by any candidate
    fact, or only shares general context without making the exact same claim."

### Question 2: Stance (`Choice`)
- When `match != "NEW"`, determine stance:
  - `"supports"`: Positive evidence affirming the same claim.
  - `"contradicts"`: Incompatible claim about the same property and time period.

### Question 3: Date window replacement (`Noul` / `Choice`)
- Evaluates whether the incoming claim's resolved world dates explicitly replace
  or correct the candidate fact's window.

---

## 4. Expected impact and comparative metrics

| Metric | Generative LLM (`gpt-5.6-luna` / `gemma-4-26b`) | System One Jev (`jev-latest`) |
| :--- | :--- | :--- |
| **Input tokens per assertion** | 15,000 – 30,000 (entire prompt + schema) | 300 – 800 (clean state + typed questions) |
| **Output tokens per call** | 500 – 1,200 tokens (JSON schema) | ~20 – 40 tokens (typed decision) |
| **Estimated conv-42 cost** | $9.16 (v28) | **< $0.20** (>95% reduction) |
| **Proposition equivalence** | Prone to false merges on shared context | Calibrated against explicit exclusion criteria |
| **Schema/Reference errors** | Common (`F2` invalid refs, formatting) | **Zero** (typed primitives owned by client) |
| **Execution mode** | Sequential chat completion | Parallel question batching |

---

## 5. Architectural configuration & rollback safety

To maintain zero regression risk:
1. **Configurable via environment variable:**
   - `REMEMBERSTACK_FACT_ADJUDICATION_ENGINE`:
     - `"prompt"` (default): existing generative LLM path (`ModelRequest` + `PromptFactDecision`).
     - `"jev"`: System One Jev adjudication engine.
2. **Standardized configuration:**
   - `REMEMBERSTACK_TYPESAFE_API_KEY`: API key for `api.typesafe.ai`.
   - `REMEMBERSTACK_TYPESAFE_MODEL`: default `"jev-latest"`.
   - `REMEMBERSTACK_TYPESAFE_BASE_URL`: default `"https://api.typesafe.ai/v1"`.
   - `REMEMBERSTACK_TYPESAFE_TIMEOUT_SECONDS`: default `30.0`.
   - `REMEMBERSTACK_TYPESAFE_CONFIDENCE_FLOOR`: default `0.75`.
3. **Fail-safe fallback:**
   - If Jev confidence is below the configured floor, or if an unexpected
     provider error occurs, the engine can optionally fall back to the prompt
     adjudicator or fail closed per worker retry policy.
4. **Preservation of database contracts:**
   - The Jev engine produces the exact same typed `FactApplicationDecision`
     consumed by `apply_fact_decision()`. Postgres locking, CAS, and idempotency
     remain 100% untouched.
