# Design: Configurable System One Jev fact adjudication engine (D126)

**Status:** accepted 2026-09-17, binding when merged.
**Scope:** Fact and observation adjudication in RememberStack (`FactAdjudicator`,
D118/D121/v38). Providing a dedicated, alternate adjudication engine powered by
TypeSafe AI's System One model (`jev-latest`) alongside the default generative
prompt baseline.

---

## 1. Problem and architectural boundary

In the RememberStack ingestion pipeline, workers process raw source documents
through structuring (E0), prefixing (E1), claim extraction (E2), and
normalization (E3), producing staged relation and observation assertions.
Adjudication (`FactAdjudicator`, D118) reconciles each incoming assertion
against candidate facts for the same entity.

### A. The generative LLM defect
In LoCoMo `conv-42` benchmark runs (v28–v38), using generative LLMs (`gpt-5.6-luna`,
`gemma-4-26b`) for adjudication created severe operational defects:
1. **Token and cost dominance:** Adjudication consumed 34.5M input tokens ($9.16
   out of $12.40 total ingestion cost, 97.7% of the cost increase between v27 and
   v28).
2. **Semantic drift on proposition equivalence (False Merges):** Generative models
   repeatedly merged distinct propositions on shared context (e.g. merging Nate's
   tournament win with "the tournament was a fun experience" at 0.98 confidence).
3. **Handle formatting failures:** Inventing unsupplied handles (`F2` when only
   `F1` existed), requiring retry loops and feedback prompts.

### B. System One suitability
Adjudication does not synthesize new text, rewrite claims, or summarize. It is
strictly a categorical choice problem: does an assertion support an existing fact,
contradict it, or establish a new fact? TypeSafe's System One model (`jev-latest`)
evaluates typed `Choice` questions over prepared state and returns calibrated
probabilities and categorical decisions.

### C. D118 Streaming Placement Scope Boundary
Per D118, adjudication serves two contexts: single-assertion streaming placement
at ingestion time, and periodic multi-assertion consolidation.
- The streaming Jev engine implemented here is strictly a **single-assertion
  placement engine**. It decides the placement of the incoming assertion against
  existing candidate facts.
- Multi-assertion updates, predecessor caps, and re-linking (`support_moves`,
  `updates`, and `contradict_with`) remain explicit **empty tuples** on this
  streaming Jev path (`support_moves = ()`, `updates = ()`, `contradict_with = ()`).
  They are not a silent partial subset; they are the defined scope boundary for
  streaming placement.

The generative prompt engine remains the default baseline. The engine selection
is controlled by environment variable `REMEMBERSTACK_FACT_ADJUDICATION_ENGINE`
(or `REMEMBERSTACK_FACT_ENGINE`).

---

## 2. Configuration and environment bindings

Configuration is divided between the existing `FactAdjudicationSettings` and a
sibling `TypeSafeSettings` class:

### `FactAdjudicationSettings` (`REMEMBERSTACK_FACT_` prefix, `extra="ignore"`)

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `model` | `str` | `"openai/gpt-5.6-luna"` | Model identifier for the default generative prompt engine. Env: `REMEMBERSTACK_FACT_MODEL`. |
| `engine` | `Literal["prompt", "jev"]` | `"prompt"` | Selected adjudication engine. Controlled via `validation_alias=AliasChoices("REMEMBERSTACK_FACT_ADJUDICATION_ENGINE", "REMEMBERSTACK_FACT_ENGINE", "engine")`. |
| `confidence_floor` | `float` | `0.75` | Minimum confidence score ($0.0$ to $1.0$). Shared across both prompt and Jev engines. Match choices below this floor fail-safe to `NEW`. |

### `TypeSafeSettings` (`REMEMBERSTACK_TYPESAFE_` prefix, `extra="ignore"`)

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `api_key` | `str \| None` | `None` | Authentication bearer token. Plain string matching repo conventions (`OpenRouterSettings.api_key`). Required when `engine="jev"`. |
| `model` | `str` | `"jev-latest"` | Model identifier. Note: `jev-latest` is an unpinned floating tag pointing to TypeSafe's flagship System One model. |
| `base_url` | `str` | `"https://api.typesafe.ai/v1"` | API base URL. |
| `timeout_s` | `float` | `30.0` | Client HTTP timeout in seconds (matching `timeout_s` convention). |
| `fallback_to_prompt` | `bool` | `False` | When true, provider transport/network errors (HTTP 5xx, timeouts after retries) fall back to the generative prompt adjudicator. Sub-floor confidence does NOT fall back. |

### Architectural Layering and Seam Separation (D61/D62)
To preserve the strict architectural separation where `spine` never imports
`adapters`:
1. **Port Protocol (`src/rememberstack/ports/systemone.py`):**
   Defines the runtime-checkable protocol:
   ```python
   @runtime_checkable
   class SystemOnePort(Protocol):
       def evaluate(
           self,
           *,
           model: str,
           state: Mapping[str, Any],
           questions: Mapping[str, Any],
           timeout_s: float | None = None,
       ) -> tuple[Mapping[str, Any], ProviderCallUsage]: ...
   ```
2. **Adapter (`src/rememberstack/adapters/typesafe.py`):**
   `TypeSafeSystemOneClient` implements `SystemOnePort`. It consumes `TypeSafeSettings`
   and defines:
   ```python
   class ConfigurationError(ValueError):
       """Raised when required TypeSafe configuration is missing or invalid."""
   ```
   If `TypeSafeSettings.api_key` is missing or blank at initialization,
   `TypeSafeSystemOneClient.__init__` immediately raises `ConfigurationError`.
3. **Spine (`src/rememberstack/spine/fact_adjudication.py`):**
   `FactAdjudicator.__init__` accepts:
   - `engine: Engine`
   - `model_provider: ModelProviderPort` (used when `engine="prompt"` or for fallback)
   - `settings: FactAdjudicationSettings | None = None`
   - `systemone_provider: SystemOnePort | None = None`
   If `self._settings.engine == "jev"` and `systemone_provider is None`:
   `FactAdjudicator` raises `ValueError("systemone_provider must be supplied when fact adjudication engine is 'jev'")`.
   Spine imports only from `ports.systemone`, never from `adapters`.
4. **Composition Root (`src/rememberstack/profiles/selfhost.py`):**
   When constructing `FactAdjudicator`, if `fact_settings.engine == "jev"`,
   wires `typesafe_client = TypeSafeSystemOneClient(settings=TypeSafeSettings())`
   and passes `systemone_provider=typesafe_client`. Missing API keys fail fast
   at startup via `ConfigurationError`.

---

## 3. State construction and question design

### A. State construction from D121 concise presentation
The Jev adjudicator consumes the **entire** dictionary `presentation` returned by
`project_concise_inputs(snapshot=prepared.inputs)` (D121) as System One `state`.
The returned dictionary contains:
- `presentation["incoming_assertion"]`: the handle string `"A1"`.
- `presentation["assertions"]`: list of assertion items where `item["incoming"] is True`
  (or `item["handle"] == presentation["incoming_assertion"]`) contains the incoming
  assertion's statement (`item["content"]`), claim reference (`item["claim"]`),
  subject/object entity references, and context entities.
- `presentation["facts"]`: list of candidate facts for the entity (`F1`, `F2`, ...),
  each with statement (or `statement_ref` into factored text), chosen world dates,
  and canonical endpoints.
- `presentation["claims"]`: dictionary of citable source claims (`C1`, `C2`, ...),
  each with source metadata and raw world dates.
- `presentation["entities"]`: resolved entities and aliases (`same_as`).
- `presentation["sources"]`: source documents.
- `presentation["text"]`: factored text dictionary (`T1`, `T2`, ...).
- `presentation["evidence"]`, `presentation["contradiction_sets"]`, and truncation flags
  (`limits`, `potentially_truncated`, `context_truncated`).

No keys are discarded, filtered, or reconstructed. Passing the full presentation
dictionary preserves exact parity with the generative prompt input.

### B. Empty Candidate Short-Circuit
If `presentation["facts"]` is empty (a new entity or first assertion), the
adjudicator short-circuits immediately in code to the deterministic `_sole_new_fact`
path, bypassing the network call entirely (0 tokens, 0 latency).

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
  - `{"F1": "...", "F2": ...}` for each candidate in `presentation["facts"]`.
    If a candidate fact's statement is stored as `statement_ref`, its text is
    resolved from `presentation["text"][row["statement_ref"]]` so the criterion
    shows the full statement text alongside its chosen world dates.
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
  justify changing its stored world-time window? If the assertion is NEW, select keep."
- **Criteria:**
  - `"keep"`: "Keep the candidate fact's current stored world-time window."
  - `"use_claim"`: "Replace the fact's window with the incoming claim's resolved world dates."
  - `"clear"`: "Clear the fact's world dates because evidence shows the date is completely unknown or invalid."

---

## 4. Response translation and writer contract

The Jev client receives typed responses:
- `match_choice = response["match"]["choice"]`
- `match_confidence = response["match"]["confidence"]`
- `stance_choice = response["stance"]["choice"]`
- `window_choice = response["window_action"]["choice"]`

### Response Validation
1. If `match_choice` is neither `"NEW"` nor in `mapping.facts`:
   raise `ProviderInvalidResponseError(f"unrecognized match handle: {match_choice}")`.
2. If `stance_choice` not in `{"supports", "contradicts", "not_applicable"}`:
   raise `ProviderInvalidResponseError(f"unrecognized stance: {stance_choice}")`.
3. If `window_choice` not in `{"keep", "use_claim", "clear"}`:
   raise `ProviderInvalidResponseError(f"unrecognized window action: {window_choice}")`.
4. If `match_choice != "NEW"` and `stance_choice == "not_applicable"`:
   raise `ProviderInvalidResponseError("matched candidate cannot have not_applicable stance")`.

### Translation Rules into `PromptFactDecision`

1. **Sub-Floor Inconclusive Gate:**
   If `match_confidence < settings.confidence_floor` (0.75):
   Per D118 fail-safe rules, inconclusive decisions coexist as a new fact to
   prevent destructive false merges. The translator forces:
   - `target = "N1"`
   - `new_facts = (PromptNewFact(handle="N1", assertion=mapping.incoming_assertion),)`
   - `stance = "supports"`
   - `window = None`
   - `updates = ()`, `support_moves = ()`, `contradict_with = ()`
   - `confidence = match_confidence`
   - `rationale = f"Jev System One: sub-floor confidence ({match_confidence:.2f} < {settings.confidence_floor:.2f}); preserve coexistence."`
   *(Note: `confidence` carries the sub-floor score `match_confidence`. When
   `FactAdjudicator.apply()` runs, its existing invariant check
   `if decision.confidence < self._settings.confidence_floor:` confirms this score
   and wraps the application into the canonical `coexist` new-fact path. Both
   layers agree on creating a coexisting new fact).*

2. **When `match_choice == "NEW"` (and `match_confidence >= settings.confidence_floor`):**
   - `target = "N1"`
   - `new_facts = (PromptNewFact(handle="N1", assertion=mapping.incoming_assertion),)`
   - `stance = "supports"` (the declaring assertion always supports its new fact;
     `stance_choice` and `window_choice` are ignored)
   - `window = None` (copies incoming claim window via existing writer mechanics)
   - `updates = ()`, `support_moves = ()`, `contradict_with = ()`
   - `confidence = match_confidence`
   - `rationale = f"Jev System One: new proposition (confidence {match_confidence:.2f})"`

3. **When `match_choice` is an admitted candidate handle (`"F1"`, `"F2"`, ...):**
   - `target = match_choice`
   - `new_facts = ()`
   - `stance = "contradicts"` if `stance_choice == "contradicts"` else `"supports"`
   - `updates = ()`, `support_moves = ()`, `contradict_with = ()`
   - `confidence = match_confidence`
   - **Grounded window construction:**
     - Locate incoming assertion item in `presentation["assertions"]` where
       `item["handle"] == presentation["incoming_assertion"]`.
     - Extract `incoming_claim_handle = item.get("claim")`.
     - If `item.get("claim_not_supplied")` or `incoming_claim_handle` is None or
       not in `mapping.claims`: `window = None`.
     - Else if `window_choice == "keep"`: `window = None`.
     - Else if `window_choice == "clear"`:
       Build `PromptGroundedWindow(window=FactWindow(), supporting_claims=(incoming_claim_handle,))`
       using bare `FactWindow()` (`valid_precision=ClaimValidPrecision.UNKNOWN`, both endpoints absent).
     - Else if `window_choice == "use_claim"`:
       Locate claim row in `presentation["claims"]` where `row["handle"] == incoming_claim_handle`.
       If `row.get("source_world_precision")`:
         Parse `valid_from = datetime.fromisoformat(row["source_world_from"])` if `row.get("source_world_from")` else None.
         Parse `valid_until = datetime.fromisoformat(row["source_world_until"])` if `row.get("source_world_until")` else None.
         Precision = `ClaimValidPrecision(row["source_world_precision"])`.
         Call `fact_window = fact_window_from_raw(valid_from=valid_from, valid_until=valid_until, precision=precision)`.
         Build `PromptGroundedWindow(window=fact_window, supporting_claims=(incoming_claim_handle,))`.
       Else: `window = None`.
   - `rationale = f"Jev System One: matched {match_choice} ({stance}) with confidence {match_confidence:.2f}"`

The translated `PromptFactDecision` is then passed through the existing, verified
`translate_prompt_decision(response=prompt_decision, mapping=mapping)` function (keyword-only).
The resulting `FactApplicationDecision` commits through PostgreSQL locking, CAS,
and idempotency checks identically to generative decisions.

---

## 5. D61 Substrate Seam, Metering, Fingerprinting, and Pipeline Generations

### A. Generation Identity and Single Helper Authority
To prevent cross-engine contamination and ensure that prompt and Jev cannot share
prepared attempts or complete each other's in-flight rows, Jev binds distinct plane
adjudicator versions:
- `RELATION_APPLICATION_VERSION = "relation-adjudicator-2026.09d:concise-handles-5:d123-context-nom-2:output-fields-1:new-fact-refs-1:target-discipline-1:rej-feedback-1"`
- `OBSERVATION_APPLICATION_VERSION = "obs-adjudicator-2026.09d:concise-handles-5:d123-context-nom-2:output-fields-1:new-fact-refs-1:target-discipline-1:rej-feedback-1"`
- `RELATION_APPLICATION_VERSION_JEV = "relation-adjudicator-2026.09a:jev-choice-match-3"`
- `OBSERVATION_APPLICATION_VERSION_JEV = "obs-adjudicator-2026.09a:jev-choice-match-3"`
- `JEV_ADJUDICATOR_VERSION = "jev-adjudicator-2026.09a:choice-match-3"`

All engine-aware pipeline components derive versions from single helper authorities
defined in `src/rememberstack/spine/fact_adjudication.py`:
1. `active_adjudicator_versions(engine: str) -> tuple[str, str]`:
   - Returns `(RELATION_APPLICATION_VERSION_JEV, OBSERVATION_APPLICATION_VERSION_JEV)` when `engine == "jev"`.
   - Returns `(RELATION_APPLICATION_VERSION, OBSERVATION_APPLICATION_VERSION)` when `engine == "prompt"`.
2. `active_question_identity(engine: str) -> str`:
   - Returns `JEV_ADJUDICATOR_VERSION` when `engine == "jev"`.
   - Returns `"fact-prompt-v1"` when `engine == "prompt"`.
3. `active_flush_version(engine: str) -> str`:
   - Computes:
     `rel_ver, obs_ver = active_adjudicator_versions(engine)`
     `f"e3-obs-flush:entity-fanout-1:{FACT_NORMALIZER_VERSION}:{rel_ver}:{obs_ver}"`.

**Every call site reads these helpers:**
- **`workers/e3.py` (`E3Handler._stage_normalizations`)**: Stages `adjudicator_version`
  matching `active_adjudicator_versions(settings.engine)`.
- **`spine/fact_adjudication.py` (`FactAdjudicator.prepare`)**: Admits head applications
  matching `adjudicator_versions=active_adjudicator_versions(self._settings.engine)`.
- **`spine/work_ledger.py` (`register_version_applications_on` & `check_flush_barrier`)**:
  Passes `active_adjudicator_versions(engine)` as `relation_version` and `observation_version`,
  and matches `active_flush_version(engine)`.

### B. Snapshot Hash & Attempt Fingerprinting
`snapshot_hash` binds:
```python
def snapshot_hash(
    *,
    snapshot: Mapping[str, Any],
    engine: str = "prompt",
    question_identity: str = "fact-prompt-v1",
) -> str:
    renderer_version = f"{PROMPT_RENDERER_VERSION}:{engine}:{question_identity}"
    return sha256(canonical_json({"renderer_version": renderer_version, "snapshot": dict(snapshot)}).encode()).hexdigest()
```
**Crucial invariant:** Both `FactAdjudicator.prepare()` and `FactAdjudicator.apply()`
call `snapshot_hash` with the exact same arguments:
```python
expected_hash = snapshot_hash(
    snapshot=snapshot,
    engine=self._settings.engine,
    question_identity=active_question_identity(self._settings.engine),
)
```
The prepared attempt records `engine` and `question_identity`. An in-flight prompt
attempt cannot be CAS-published or applied by Jev, preventing cross-engine contamination.

### C. Meter Key Namespace
The `:jev` suffix is specifically the `call_key` namespace suffix for
`meter.record(call_key=f"{base_receipt_key}:jev", tier="fact_adjudication_jev", usage=usage)`.
This guarantees that if fallback to prompt occurs, the fallback call key does
not collide with the Jev attempt key.

### D. Spend Accounting (`ProviderCallUsage`)
The TypeSafe client constructs the real, canonical `ProviderCallUsage`:
- If TypeSafe returns a response lacking `usage` or `input_tokens`, it raises
  `ProviderAccountingError("TypeSafe response carries no token usage")`.
- `model_name = f"typesafe/{settings.model}"` (e.g. `"typesafe/jev-latest"`)
- `tokens_in = int(usage["input_tokens"])`
- `tokens_out = int(usage.get("output_tokens", 0))`
- `cost_usd = Decimal(str(round(Decimal(tokens_in) * Decimal("0.000000042"), 6)))`
  (TypeSafe pricing table: $0.042 per 1M input tokens, output tokens free).
- `latency_ms = int(elapsed_s * 1000)`
Recorded via `meter.record(call_key=f"{base_receipt_key}:jev", tier="fact_adjudication_jev", usage=usage)`.

---

## 6. Failure modes and operational behavior

1. **Provider errors (HTTP 429, 500, 502, 503, 504, timeouts):**
   - Retried up to 3 times with exponential backoff on HTTP 429, 500, 502, 503, 504.
   - If retries fail:
     - If `fallback_to_prompt=True`: log warning and delegate attempt to prompt engine (using `self._provider`).
     - If `fallback_to_prompt=False`: raise `ProviderCallError` with parsed usage. Standard
       worker backstop retries or DLQ policies apply.
2. **Sub-floor confidence:**
   - Evaluated within Jev as `NEW` fact fail-safe. Does NOT fall back to prompt.
3. **Network isolation:**
   - Standard outbound HTTPS on 443 to `api.typesafe.ai`. `HTTP_PROXY` and
     `HTTPS_PROXY` respected via `httpx`.

---

## 7. Acceptance evidence

1. **Unit tests (`src/tests/spine/test_jev_adjudication.py`):**
   - Deterministic translation from Jev choices into valid `PromptFactDecision`.
   - Complete `window_action` translation (`keep`, `use_claim`, `clear`) using `fact_window_from_raw`, C-name derivation, and bare `FactWindow()`.
   - Startup refusal when `engine="jev"` and `api_key` is absent (`ConfigurationError(ValueError)`).
   - `ProviderInvalidResponseError` on unrecognized handles, invalid stances, or invalid window actions.
   - Sub-floor confidence handling and integration with `apply()`.
   - Engine isolation: hash mismatch when comparing prompt and Jev attempts.
2. **Regression benchmarks:**
   - Nate tournament win (`a80e17f7`) vs "fun experience" (`1104873b`): classified as `NEW`.
   - Nate Valorant final win (`58724598`) vs "in the final" (`3add09d3`): classified as `NEW`.
   - True re-assertions: matched to candidate with `stance="supports"` and confidence $\ge 0.75$.
3. **LoCoMo conv-42 run:**
   - 29 sessions processed with `REMEMBERSTACK_FACT_ADJUDICATION_ENGINE="jev"`.
   - Ingestion cost and token usage measured and reported without committed constant claims.
   - Zero dead letters, clean CAS receipts, and verified backup.
