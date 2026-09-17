# Configurable System One Jev fact adjudication engine

**Status:** D126, accepted 2026-09-17; binding when merged.
**Analysis:** [Jev adjudication analysis](../analysis/jev_adjudication_analysis.md).
**Related:** [D118 fact pipeline](fact_adjudication_design.md),
[D121 concise adjudication inputs](concise_adjudication_inputs_design.md).

---

## 1. Decision and boundary

Provide an alternate, dedicated fact adjudication engine powered by TypeSafe AI's
System One model (`jev-latest`), configurable via environment variable alongside
the existing generative LLM prompt adjudicator.

Fact adjudication reconciles staged assertions against existing facts for the
same entity. It does not synthesize new text, rewrite claims, or generate
summaries. Its decisions are strictly categorical:
1. Match target: does the assertion reinforce an existing fact (`F1`, `F2`, ...)
   or establish a new fact (`NEW`)?
2. Stance: does the assertion support or contradict the target fact?
3. Date window: does evidence justify updating the stored fact's world dates?

Generative LLMs (`gpt-5.6-luna`, `gemma-4-26b`) applied to this task suffer from
extreme prompt token bloat (accounting for 97.7% of ingestion costs in LoCoMo
benchmarks), format fragility (hallucinated reference handles), and semantic
drift (false-positive merges where shared event context is conflated with
propositional equivalence).

The Jev adjudication engine replaces the generative chat completion step with
calibrated System One decision primitives (`Choice`, `Noul`, `Score`).
Code constructs structured state and typed questions; the model returns
discrete categorical choices and calibrated probability distributions; code
translates the result into the canonical `FactApplicationDecision` and commits it
through existing database locking and compare-and-swap mechanics.

The generative prompt engine remains available as the baseline. The engine
selection is controlled by environment variable:
`REMEMBERSTACK_FACT_ADJUDICATION_ENGINE`.

---

## 2. Configuration and environment bindings

The adjudication engine is configured via standard Pydantic settings:

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `REMEMBERSTACK_FACT_ADJUDICATION_ENGINE` | `Literal["prompt", "jev"]` | `"prompt"` | Adjudication engine selection. `"prompt"` uses the generative LLM; `"jev"` uses TypeSafe System One. |
| `REMEMBERSTACK_TYPESAFE_API_KEY` | `SecretStr` | `None` | Authentication bearer token for the TypeSafe API. Required when engine is `"jev"`. |
| `REMEMBERSTACK_TYPESAFE_MODEL` | `str` | `"jev-latest"` | Model identifier (flagship System One model). |
| `REMEMBERSTACK_TYPESAFE_BASE_URL` | `str` | `"https://api.typesafe.ai/v1"` | API endpoint base URL. |
| `REMEMBERSTACK_TYPESAFE_TIMEOUT_SECONDS` | `float` | `30.0` | Client HTTP request timeout. |
| `REMEMBERSTACK_TYPESAFE_CONFIDENCE_FLOOR` | `float` | `0.75` | Minimum confidence score required to accept a decision without escalation. |
| `REMEMBERSTACK_TYPESAFE_FALLBACK_TO_PROMPT` | `bool` | `false` | When true, provider errors or sub-floor confidence fall back to the prompt adjudicator. When false, fail closed. |

---

## 3. State construction and question design

The Jev adjudicator consumes the exact same prepared concise snapshot produced by
D121 (`project_concise_inputs`), preserving full consistency with the existing
attempt mapping.

### A. State payload
The request `state` contains the structured semantic evidence:
- **`incoming`**: statement, claim handle, resolved world dates, said-on date,
  and context entities.
- **`candidates`**: list of existing candidate facts on the same entity, each
  with its local F-handle, statement, current world-time window, said-on date,
  and attribution.
- **`context`**: shared context entities and source passage metadata.

### B. Typed questions

All questions are sent in a single parallel HTTP request against `/v1/systemone`:

#### 1. `match` (`Choice`)
Selects the candidate fact that the incoming assertion relates to, or declares
a new fact:
- **Instructions:**
  "You are adjudicating an incoming assertion against candidate stored facts for
  the same entity in a memory system. Determine whether the incoming assertion
  affirms the exact same core proposition and truth claim as one of the
  candidate facts, or if it is a new, distinct fact.
  - An assertion matches an existing fact ONLY if it asserts the exact same
    proposition, truth claim, and outcome (including attribution and qualifiers).
  - Sharing a topic, event, or entity (e.g. attending vs winning a tournament, or
    participating in a final vs winning it) is NOT a match: select NEW.
  - Contradictions (e.g. lost the tournament vs won the tournament) match the
    target fact so they can be recorded as contradictory.
  - If no candidate fact represents the exact proposition, select NEW."
- **Criteria:**
  - Map of candidate handles: `{"F1": "...", "F2": ...}` where each description
    contains the candidate statement and world-time window.
  - `"NEW"`: "The incoming assertion is a new proposition not represented by any
    candidate fact, or only shares general context without making the exact same claim."

#### 2. `stance` (`Choice`)
Evaluates the logical stance of the incoming assertion relative to the matched fact:
- **Instructions:** "Does the incoming assertion support or contradict the target fact?"
- **Criteria:**
  - `"supports"`: "The assertion affirms and provides positive evidence for the matched fact."
  - `"contradicts"`: "The assertion directly denies or provides incompatible contrary evidence against the matched fact."

#### 3. `window_action` (`Choice`)
Determines the world-time window treatment:
- **Instructions:** "How should the world-time window of the target fact be updated?"
- **Criteria:**
  - `"keep"`: "Keep the existing fact's window unchanged."
  - `"use_claim"`: "Use the incoming assertion's resolved world-time window."
  - `"clear"`: "Clear the world-time window (dates are unknown or conflicting)."

---

## 4. Response translation and writer contract

The Jev client receives typed responses:
- `match_choice`: `response.choices["match"].choice`
- `match_confidence`: `response.choices["match"].confidence`
- `stance_choice`: `response.choices["stance"].choice`
- `window_action`: `response.choices["window_action"].choice`

The translator maps these typed outputs directly into `PromptFactDecision`:
1. If `match_choice == "NEW"`:
   - `target = "N1"`
   - `new_facts = (PromptNewFact(handle="N1", assertion=mapping.incoming_assertion),)`
   - `stance = "supports"`
   - `window = None` (copies incoming claim window via existing writer mechanics)
2. If `match_choice` is a candidate handle (`"F1"`, `"F2"`):
   - `target = match_choice`
   - `new_facts = ()`
   - `stance = stance_choice`
   - `window = None` (or translated grounded window if `window_action == "use_claim"`)
3. `confidence = match_confidence`
4. `rationale = f"Jev System One decision: {match_choice} ({stance_choice}) with confidence {match_confidence:.2f}"`

The resulting `PromptFactDecision` is then passed through the existing, verified
`translate_prompt_decision(response, mapping)` function.

**Critical guarantee:** The resulting `FactApplicationDecision` is identical in
structure and type to what the generative path produces. It runs through the
exact same PostgreSQL transaction, entity locking, compare-and-swap verification,
and idempotency receipts.

---

## 5. Security, isolation, and compliance

- **Library boundary (D60/D61):** The TypeSafe adapter lives entirely within the
  single-deployment open-source engine (`rememberstack.adapters.typesafe`). It
  does not depend on the Cloud control plane, multi-tenant database, or external
  management services.
- **Credential management:** `REMEMBERSTACK_TYPESAFE_API_KEY` is loaded from the
  environment or secret manager. It is never logged, persisted in receipts, or
  committed to source control.
- **Privacy and retention:** Only the semantic statement text and dates needed
  for adjudication are sent. No user identifiers, tenant keys, or raw file
  blobs are transmitted.
- **Egress and networking:** Outbound HTTPS requests to `api.typesafe.ai` over
  standard port 443. Standard proxy settings (`HTTP_PROXY`, `HTTPS_PROXY`) are
  respected via `httpx`.

---

## 6. Failure modes and operational behavior

1. **Provider timeout or 5xx:**
   - If `REMEMBERSTACK_TYPESAFE_FALLBACK_TO_PROMPT` is enabled, log a warning and
     invoke the prompt adjudicator.
   - If fallback is disabled, raise `ProviderCallError` with usage accounting.
     The standard worker retry / backoff schedule handles redelivery.
2. **Rate limiting (HTTP 429 / 529):**
   - The adapter performs exponential backoff retries up to 3 times before
     raising.
3. **Sub-floor confidence:**
   - If `match_confidence < confidence_floor`, the engine treats the match as
     inconclusive. Per D118 safety rules, inconclusive matches fail-safe to
     creating a separate fact (`"NEW"`) rather than performing an incorrect
     destructive merge or supersession.

---

## 7. Acceptance evidence

1. **Unit tests:**
   - Verify bijective translation from Jev `Choice` responses into valid
     `PromptFactDecision` and `FactApplicationDecision`.
   - Verify environment variable switching between `"prompt"` and `"jev"`.
   - Verify error handling and timeout configurations.
2. **Regression benchmarks:**
   - Execute the 2 bugged conv-42 assertion-loss cases (Nate tournament win vs fun
     experience; Nate Valorant final win vs attended final). Jev must classify
     both as `NEW`, preserving both assertions in derived facts.
   - Execute true identity cases. Jev must classify them as `F1` with
     `stance="supports"` and confidence $\ge 0.85$.
3. **End-to-end conv-42 processing:**
   - Ingest all 29 conv-42 sessions with `REMEMBERSTACK_FACT_ADJUDICATION_ENGINE="jev"`.
   - Measure total adjudication cost (< $0.50 vs historical $9.16) and total
     tokens (< 3M vs historical 34.5M).
   - Verify zero unapplied assertions, zero dead letters, and verified final GCS
     backup.
