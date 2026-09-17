# Configurable System One Jev fact adjudication engine

**Status:** D126, accepted 2026-09-17; binding when merged.
**Analysis:** [Jev adjudication analysis](../analysis/jev_adjudication_analysis.md).
**Related:** [D118 mutable fact windows](mutable_fact_windows_design.md),
[D118 fact application contract](mutable_fact_application_contract.md),
[D121 concise adjudication inputs](concise_adjudication_inputs_design.md).

---

## 1. Decision and boundary

Provide an alternate, dedicated fact adjudication engine powered by TypeSafe AI's
System One model (`jev-latest`), configurable via environment variable alongside
the existing generative LLM prompt adjudicator.

Fact adjudication in RememberStack (`FactAdjudicator`, D118) reconciles staged
assertions against candidate facts for the same entity across both relation and
observation planes (observation writes flow through `FactAdjudicator`). It does
not synthesize new text, rewrite claims, or generate summaries. Its primary
operational responsibility on the streaming write path is deciding:
1. **Target placement:** does the incoming assertion reinforce an existing fact
   (`F1`, `F2`, ...) or establish a new fact (`N1`)?
2. **Stance:** does the assertion support or contradict the target fact?
3. **Date window:** does evidence justify updating the target fact's world-time
   window (keep, adopt incoming claim dates, or clear)?

Generative LLMs (`gpt-5.6-luna`, `gemma-4-26b`) applied to this seat suffer from
severe prompt token bloat (accounting for 34,475,491 input tokens and $9.16 of the
$12.40 total ingestion bill in LoCoMo conv-42 v28), handle format rejections
(hallucinating reserved F-names such as `F2`), and semantic drift (false-positive
merges where shared event context is conflated with propositional equivalence).

The Jev adjudication engine replaces the generative chat completion step with
calibrated System One `Choice` decision primitives. Code constructs structured
state from the D121 concise presentation and evaluates typed questions in parallel;
the model returns categorical choices and calibrated probability distributions;
code translates the result into a valid subset of `PromptFactDecision` and commits
it via the canonical `apply_fact_decision()` pipeline.

### Documented Scope Boundary
The Jev engine is dedicated to single-assertion streaming placement and target
window resolution. It explicitly emits empty tuples for complex multi-fact residue
reassignments (`support_moves = ()`), succession caps on predecessor facts
(`updates = ()`), and distinct cross-fact contradiction links
(`contradict_with = ()`). Single-assertion streaming placement assigns the new
assertion directly; earlier assertions are not mutated without an explicit
multi-assertion reassignment prompt. Where complex historical restructuring is
required, the generative prompt adjudicator remains available.

The generative prompt engine remains the default baseline. The engine selection
is controlled by environment variable `REMEMBERSTACK_FACT_ADJUDICATION_ENGINE`.

---

## 2. Configuration and environment bindings

Configuration is divided into the existing `FactAdjudicationSettings` (for engine
selection and confidence thresholding) and a sibling `TypeSafeSettings` class:

### `FactAdjudicationSettings` (`REMEMBERSTACK_FACT_` prefix, `extra="ignore"`)

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `engine` | `Literal["prompt", "jev"]` | `"prompt"` | Selected adjudication engine. Controlled via `REMEMBERSTACK_FACT_ADJUDICATION_ENGINE` (or `REMEMBERSTACK_FACT_ENGINE`). |
| `confidence_floor` | `float` | `0.75` | Minimum confidence score ($0.0$ to $1.0$). Shared across both prompt and Jev engines. Match choices below this floor fail-safe to `NEW`. |

### `TypeSafeSettings` (`REMEMBERSTACK_TYPESAFE_` prefix, `extra="ignore"`)

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `api_key` | `str \| None` | `None` | Authentication bearer token. Plain string matching repo conventions (`OpenRouterSettings.api_key`). Required when `engine="jev"`. |
| `model` | `str` | `"jev-latest"` | Model identifier. Note: `jev-latest` is an unpinned floating tag pointing to TypeSafe's flagship System One model. |
| `base_url` | `str` | `"https://api.typesafe.ai/v1"` | API base URL. |
| `timeout_s` | `float` | `30.0` | Client HTTP timeout in seconds (matching `timeout_s` convention). |
| `fallback_to_prompt` | `bool` | `False` | When true, provider network errors (5xx, timeouts after retries) fall back to the prompt adjudicator. Sub-floor confidence does NOT fall back. |

### Fail-Fast Startup Validation
When `FactAdjudicationSettings.engine == "jev"`, `FactAdjudicator.__init__`
validates that `TypeSafeSettings.api_key` is non-empty. If missing or blank, it
raises `ConfigurationError` immediately at startup rather than failing at the
first ingestion attempt.

---

## 3. State construction and question design

### A. State construction from D121 concise presentation
The Jev adjudicator consumes the exact deterministic presentation dictionary
produced by `project_concise_inputs(snapshot=prepared.inputs)` (D121):
- `presentation["incoming_assertion"]`: incoming assertion handle (`A1`),
  claim reference, subject/object references, world date hints, and statement.
- `presentation["facts"]`: list of candidate facts for the entity (`F1`, `F2`, ...),
  each with statement, chosen world dates, attribution, and current assertion handles.
- `presentation["claims"]`: citable source claim dictionary (`C1`, `C2`, ...).
- `presentation["text"]`: factored text dictionary (`T1`, `T2`, ...).

This ensures identical evidence presentation between the prompt and Jev engines.
No semantic fields, attribution, or temporal context are omitted.

### B. Empty Candidate Short-Circuit
If `presentation["facts"]` is empty (a new entity or first assertion), the
adjudicator short-circuits immediately in code to the deterministic `NEW` fact
path (`_sole_new_fact`), bypassing the network call entirely (0 tokens, 0 latency).

### C. Typed Questions (`POST https://api.typesafe.ai/v1/systemone`)
When candidates exist, all three questions are dispatched in a single parallel
HTTP call over the presentation state:

#### 1. `match` (`Choice`)
- **Instructions:**
  "You are adjudicating an incoming assertion against candidate stored facts for
  the same entity in a memory system. Determine whether the incoming assertion
  affirms the exact same core proposition and truth claim as one of the candidate
  facts, or if it is a new, distinct fact.
  - An assertion matches an existing fact ONLY if it asserts the exact same
    proposition, truth claim, and outcome (including attribution and qualifiers).
  - Sharing a topic, event, or entity (e.g. attending vs winning a tournament, or
    participating in a final vs winning it) is NOT a match: select NEW.
  - Contradictions (e.g. lost the tournament vs won the tournament) match the
    target fact so they can be recorded as contradictory.
  - If no candidate fact represents the exact proposition, select NEW."
- **Criteria:**
  - `{"F1": "...", "F2": ...}` for each candidate in `presentation["facts"]`,
    citing its statement and chosen world dates.
  - `"NEW"`: "The incoming assertion is a new proposition not represented by any
    candidate fact, or only shares general context without making the exact same claim."

#### 2. `stance` (`Choice`)
- **Instructions:**
  "If the incoming assertion matches one of the candidate facts, does it support
  or contradict that matched fact? (If the assertion is a new fact not matching
  any candidate, select not_applicable)."
- **Criteria:**
  - `"supports"`: "The assertion affirms and provides positive evidence for the matched fact."
  - `"contradicts"`: "The assertion directly denies or provides incompatible contrary evidence against the matched fact."
  - `"not_applicable"`: "The assertion does not match any candidate fact (new fact)."

#### 3. `window_action` (`Choice`)
- **Instructions:**
  "If the incoming assertion matches an existing candidate fact, does evidence
  justify changing its stored world-time window?"
- **Criteria:**
  - `"keep"`: "Keep the candidate fact's current stored world-time window."
  - `"use_claim"`: "Replace the fact's window with the incoming claim's resolved world dates."
  - `"clear"`: "Clear the fact's world dates because evidence shows the date is completely unknown or invalid."

---

## 4. Response translation and writer contract

The Jev client receives typed responses:
- `match_choice = response.choices["match"].choice`
- `match_confidence = response.choices["match"].confidence`
- `stance_choice = response.choices["stance"].choice`
- `window_choice = response.choices["window_action"].choice`

### Translation Rules into `PromptFactDecision`

1. **Sub-Floor Inconclusive Gate:**
   If `match_confidence < settings.confidence_floor` (0.75):
   Per D118 fail-safe rules, inconclusive decisions coexist as a new fact to
   prevent destructive false merges. The translator forces `match_choice = "NEW"`
   with `confidence = match_confidence`.
   *(Note: Confidence floor guards diffuse probability distributions; the
   explicit exclusion criteria in §3.C.1 are what prevent high-confidence topical
   false merges).*

2. **When `match_choice == "NEW"`:**
   - `target = "N1"`
   - `new_facts = (PromptNewFact(handle="N1", assertion=mapping.incoming_assertion),)`
   - `stance = "supports"` (the declaring assertion always supports its new fact;
     `stance_choice` is ignored)
   - `window = None` (copies incoming claim window via existing writer mechanics)
   - `updates = ()`, `support_moves = ()`, `contradict_with = ()`
   - `rationale = f"Jev System One: new proposition (confidence {match_confidence:.2f})"`

3. **When `match_choice` is a candidate handle (`"F1"`, `"F2"`, ...):**
   - `target = match_choice`
   - `new_facts = ()`
   - `stance = "contradicts"` if `stance_choice == "contradicts"` else `"supports"`
   - `updates = ()`, `support_moves = ()`, `contradict_with = ()`
   - **Grounded window construction:**
     - If `window_choice == "keep"`: `window = None` (preserves existing fact window).
     - If `window_choice == "use_claim"`:
       Code inspects the incoming assertion's resolved claim window from `snapshot`.
       If valid, builds:
       `PromptGroundedWindow(window=claim_window, supporting_claims=(incoming_claim_handle,))`.
       If incoming claim has no resolved window, `window = None`.
     - If `window_choice == "clear"`:
       `PromptGroundedWindow(window=FactWindow.cleared(), supporting_claims=(incoming_claim_handle,))`.
   - `rationale = f"Jev System One: matched {match_choice} ({stance}) with confidence {match_confidence:.2f}"`

4. **Unknown Handle Validation:**
   If `match_choice` is neither `"NEW"` nor an admitted candidate handle from
   `mapping.facts`, raise `ProviderInvalidResponseError` (no fuzzy guessing).

The translated `PromptFactDecision` is then passed through the existing, verified
`translate_prompt_decision(response, mapping)` function. The resulting
`FactApplicationDecision` commits through PostgreSQL locking, CAS, and idempotency
checks identically to generative decisions.

---

## 5. D61 Substrate Seam, Metering, and Fingerprinting

- **Adapter Seam:** Implemented in `src/rememberstack/adapters/typesafe.py` as
  `TypeSafeSystemOneClient`. Uses `httpx.Client` with connection pooling, bearer
  auth, and configured timeouts.
- **Spend Accounting:** TypeSafe returns `usage: {"input_tokens": N, "output_tokens": N}`.
  Mapped into `ProviderCallUsage(input_tokens=N, output_tokens=N, cost=None)` and
  recorded via `meter.record(call_key=receipt_key, tier="fact_adjudication_jev", usage=usage)`.
- **Adjudication Generation Fingerprint:**
  The Jev renderer pins its own identity string:
  `JEV_ADJUDICATOR_VERSION = "jev-adjudicator-2026.09a:choice-match-3"`
  When `engine="jev"`, receipt keys use `:jev` suffix, ensuring CAS and idempotency
  receipts distinguish prompt and Jev executions.

---

## 6. Failure modes and operational behavior

1. **Provider errors (HTTP 5xx, timeouts, connection failures):**
   - Retried up to 3 times with exponential backoff on HTTP 429 and 529.
   - If retries fail:
     - If `fallback_to_prompt=True`: log warning and delegate attempt to prompt engine.
     - If `fallback_to_prompt=False`: raise `ProviderCallError` with usage. Standard
       worker backstop retries or DLQ policies apply.
2. **Sub-floor confidence:**
   - Evaluated within Jev as `NEW` fact fail-safe. Does NOT fall back to prompt.
3. **Network isolation:**
   - Standard outbound HTTPS on 443 to `api.typesafe.ai`. `HTTP_PROXY` and
     `HTTPS_PROXY` respected.

---

## 7. Acceptance evidence

1. **Unit tests (`src/tests/spine/test_jev_adjudication.py`):**
   - Deterministic translation from Jev choices into valid `PromptFactDecision`.
   - Complete `window_action` translation (`keep`, `use_claim`, `clear`) with C-name witnesses.
   - Startup refusal when `engine="jev"` and `api_key` is absent.
   - `ProviderInvalidResponseError` on unrecognized choices.
2. **Regression benchmarks:**
   - Nate tournament win (`a80e17f7`) vs "fun experience" (`1104873b`): classified as `NEW`.
   - Nate Valorant final win (`58724598`) vs "in the final" (`3add09d3`): classified as `NEW`.
   - True re-assertions: matched to candidate with `stance="supports"` and confidence $\ge 0.75$.
3. **LoCoMo conv-42 run:**
   - 29 sessions processed with `REMEMBERSTACK_FACT_ADJUDICATION_ENGINE="jev"`.
   - Ingestion cost and token usage measured and reported without committed constant claims.
   - Zero dead letters, clean CAS receipts, and verified backup.
