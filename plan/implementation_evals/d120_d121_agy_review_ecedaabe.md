# Antigravity review of parent-integrated D120/D121

Exact reviewed SHA: `ecedaabe4957760ebd690ffd0f2575c078fa0c70`.
Command: `agy --dangerously-skip-permissions --print-timeout 180m0s -p "<review prompt>"`.
Read-only; pending upstream D119 fixes were disclosed.

I have launched the unit tests in the background and will continue the analysis once the run completes.
root agent idle; waiting for 2 background task(s) (bounded by --print-timeout)
I have launched pyright in the background and will continue once it finishes.
Waiting for pyright to finish.
Waiting for pyright background task to finish.
### Scope & Verification

- **Repository**: `/Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication`
- **Reviewed Head SHA**: `ecedaabe4957760ebd690ffd0f2575c078fa0c70`
- **Stacked On Base**: `1debaf80` (D119 checkpoint)
- **Worktree State**: Clean and untouched (read-only inspection).
- **Upstream D119 Pending Fixes (PR #400)**: Acknowledged as separate in-flight work (query envelope origin-occurrence selection, two-span 500-version fixture, manifest snapshot, and Compose head migration); not claimed resolved here.

---

### Commands Actually Run

```bash
git rev-parse HEAD
# Output: ecedaabe4957760ebd690ffd0f2575c078fa0c70

git status --porcelain
# Output: (clean)

git rev-parse --short 1debaf80
# Output: 1debaf80

# Read-only Unit & Verification Tests
uv run pytest \
  src/tests/core/test_concise_adjudication.py \
  src/tests/benchmarks/test_locomo_protocol.py \
  src/tests/benchmarks/test_locomo_runner.py::test_single_run_summary_json_is_unchanged \
  src/tests/workers/test_claim_valid_time.py \
  src/tests/workers/test_e3_bare_head_noun.py
# Output: 90 passed in 8.87s

uv run pytest src/tests/core/test_source_passages.py
# Output: 8 passed in 1.68s

uv run pytest src/tests/core/test_fact_windows.py
# Output: 20 passed in 1.70s

uv run ruff check \
  src/rememberstack/core/concise_adjudication.py \
  src/rememberstack/model/concise_adjudication.py \
  src/rememberstack/spine/fact_adjudication.py \
  src/rememberstack/spine/fact_applications.py \
  src/rememberstack/workers/e1.py \
  src/rememberstack/workers/e2.py \
  src/rememberstack/workers/e3.py \
  benchmarks/locomo/protocol.py \
  benchmarks/locomo/runner.py \
  benchmarks/locomo/model.py
# Output: All checks passed!

uv run pyright \
  src/rememberstack/core/concise_adjudication.py \
  src/rememberstack/model/concise_adjudication.py \
  src/rememberstack/spine/fact_adjudication.py \
  src/rememberstack/spine/fact_applications.py \
  src/rememberstack/workers/e1.py \
  src/rememberstack/workers/e2.py \
  src/rememberstack/workers/e3.py \
  benchmarks/locomo/protocol.py \
  benchmarks/locomo/runner.py \
  benchmarks/locomo/model.py
# Output: 0 errors, 0 warnings, 0 informations

python .github/ci/check_test_inventory.py
# Output: test inventory OK: unit=108 integration=60 discovered=168

uv run lint-imports
# Output: Contracts: 5 kept, 0 broken

uv run ruff format --check src/ benchmarks/
# Output: 503 files already formatted
```

---

### Focus Area Assessment & Cold-Reader Analysis

#### 1. E2 Selection & Claimify Prompts ([`e2.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/workers/e2.py#L163-L286))
- **Coherent Multi-span Source Claims**: The instructions cleanly explain keeping coherent statements spanning multiple sentences or clauses together without over-splitting at every "and" (e.g., the Joanna third screenplay example), while separating independently dated or independently attributed assertions.
- **Origin vs. Supporting References**: Clear and unambiguous. The first label in `source_refs` must be a TARGET origin-eligible passage overlapping a Selection keep; additional labels supply context from target or same-section neighbours. Explicitly warns that citing a larger passage containing dropped sentences does not authorize extracting those dropped statements.
- **Source-Only Evidence**: Explicit instruction that bundle text is untrusted source data, and section summaries are strictly orientation, never evidence or added context.
- **Attribution**: Plainly distinguishes attributed stances from assertions of fact: `"Nate said he won" must never become an unqualified "Nate won"`.
- **Drop Handling & Verbatim Spans**: Specific drop classes and outcomes are explained without confusing jargon; instructs copying verbatim substrings of target into `source_span` during Selection.
- **Source vs. World Dates**: Clearly distinguishes source reporting time from world occurrence dates. Explicit rule that relative dates in quotes/attributed text must be resolved against the document header anchor, written into `claim_text` as ISO values, and listed in `added_context`. Updated phrasing correctly notes that verbatim phrasing is retained by cited source passages rather than a deprecated single `source_span`.

#### 2. E3 Assertion Meaning & Scoped `uses_claim_window` ([`e3.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/workers/e3.py#L82-L135))
- **Entity vs. Proposition**: Plain language explains that sharing an entity (person, event, etc.) does not make two assertions the same proposition. Contrasts winning, participating, and enjoying the same tournament.
- **Scoped `uses_claim_window`**: Accurately explains that `uses_claim_window: true` applies *only* to the specific assertion for which the claim's world-time window is valid testimony (illustrating with the 2019 hiring vs. 1990 founding example). This matches [`apply_fact_decision.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/apply_fact_decision.py#L112-L114) which defaults to unknown (`FactWindow()`) when `uses_claim_window=False`.

#### 3. Concise Fact Presentation, Typed Mapping & Adjudication Prompt ([`fact_adjudication.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/fact_adjudication.py#L38-L113), [`concise_adjudication.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/core/concise_adjudication.py), [`model/concise_adjudication.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/model/concise_adjudication.py))
- **Bidirectional Win / Participation / Enjoyment Distinction**: The prompt explicitly covers both directions without denying implication:
  > *"Winning, participating and enjoying that tournament are different propositions. A win implies participation, but storing only participation loses the result. Conversely, participation or enjoyment is not positive evidence of a win. Preserve each assertion's meaning; repeated reports of the same win belong to the winning fact."*
- **Correction vs. Separate Fact**: The prompt clarifies that the writer cannot rewrite an existing statement (a separate fact is required if no existing statement can represent the assertion), but date corrections for date-neutral statements keep the same fact and replace its chosen window with cited evidence (e.g., keeping "won Tournament A" while changing its chosen window from 5 November to 6 November).
- **Unavailable Witnesses (`W-names`)**: Handled cleanly. Unhydrated window witnesses are projected as `window_claims_not_supplied: ["W1", ...]`. The prompt explicitly instructs: `"W-names disclose window witnesses whose text is not supplied; you may not cite them"`. The translator enforce-checks claim handles against `mapping.claims`, so any attempt to cite `W` handles fails validation.
- **Typed Mapping & Safety**: [`project_concise_inputs`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/core/concise_adjudication.py#L174) and [`translate_prompt_decision`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/core/concise_adjudication.py#L512) strictly map between `F`, `C`, `A`, `E`, `S` attempt-local handles and stored UUIDs. Stale replies are rejected by compare-and-swap on `prepared.attempt_id` and fingerprinting, not handle spelling.

#### 4. Full-v30 Generation Consistency
- Extractor generation: `...:assertion-clarity-3` ([`e1.py:67`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/workers/e1.py#L67)).
- Normalizer generation: `...:assertion-clarity-2` ([`fact_adjudication.py:35`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/fact_adjudication.py#L35)).
- Adjudicator generation: `relation-adjudicator-2026.09d:concise-handles-4` and `obs-adjudicator-2026.09d:concise-handles-4` ([`fact_adjudication.py:33-34`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/fact_adjudication.py#L33-L34)).
- LoCoMo Ingest component versions match in [`benchmarks/locomo/protocol.py:76,82,85`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/benchmarks/locomo/protocol.py#L76-L86).
- LoCoMo Protocol bumped to `RS-LoCoMo-Full-v30`, adapter version `locomo-full-adapter-2026.09-concise-adjudication-v30`.

#### 5. Classifier / Cache Machinery & Locking
- **No Classifiers**: No secondary prompt-checker or classifier models were introduced.
- **No Caching Machinery**: No global caches or mutable mapping registries; mappings are derived deterministically per attempt snapshot.
- **No Model Calls Under Locks**: [`FactAdjudicator.prepare`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/fact_adjudication.py#L205) acquires locks, snapshots inputs, commits the attempt record, and releases the connection. The model call (`self._provider.generate(...)`) and translation run entirely outside database transactions and locks in [`FactAdjudicator.drain`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/fact_adjudication.py#L158-L193).

---

### Findings

#### Blockers
*None* scoped to this reviewed code.

#### Material Findings
1. **Outdated generation and version reference in [`benchmarks/locomo/README.md`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/benchmarks/locomo/README.md#L3-L9)**:
   - *Issue*: Under heading `# RS-LoCoMo-Full-v30 setup`, lines 3–6 state:
     ```markdown
     v29 (2026-09-14) pins D120/D121 processing: assertion-preserving extractor,
     normalizer and adjudicator prompts, plus compact attempt-local adjudication
     handles. Ingest generations append `assertion-clarity-2` and
     `concise-handles-3`. Stores ingested under v28 are not comparable and must be
     re-ingested.
     ```
     This text is leftover from the earlier unintegrated draft: Full-v29 was D119 alone, whereas Full-v30 is the combined D119+D120+D121 protocol (as stated in [`locomo_benchmark_design.md:3-6`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/plan/designs/locomo_benchmark_design.md#L3-L6)). Furthermore, Full-v30's actual pins in [`protocol.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/benchmarks/locomo/protocol.py#L76-L86) are `assertion-clarity-3` (extractor) and `concise-handles-4` (adjudicator).
   - *Proposed Correction*:
     ```markdown
     v30 (2026-09-14) pins combined D119/D120/D121 processing: assertion-preserving extractor,
     normalizer and adjudicator prompts, plus compact attempt-local adjudication
     handles. Ingest generations append `assertion-clarity-3` and
     `concise-handles-4`. Stores ingested under v28 or v29 are not comparable and must be
     re-ingested.
     ```

#### Nits
1. **Variant protocol comment in [`benchmarks/locomo/protocol.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/benchmarks/locomo/protocol.py#L126)**:
   - *Issue*: In the docstring for `GEMMA_VERTEX_ANSWER_AGENT_MODEL`, the text states:
     `The variant protocol keeps every v29 pin -- ingestion bindings, prompts,`
   - *Proposed Correction*: Update `v29` to `v30` to match the protocol version.

2. **Incomplete reserved-prefix list in [`PromptNewFact.handle`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/model/concise_adjudication.py#L25-L27)**:
   - *Issue*: The field description says: `"It must not reuse a supplied F, C, A, E, or S name."`, omitting `T` and `W`. The prompt correctly lists `F/C/A/E/S/T/W`, and `_TYPED_HANDLE` regexes `^([FCAESTW])([1-9]\d*)$`.
   - *Proposed Correction*: Change the docstring to: `"It must not reuse a supplied F, C, A, E, S, T, or W name."`

3. **Minor phrasing tension in [`_NORMALIZE_PROMPT`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/workers/e3.py#L83)**:
   - *Issue*: PURPOSE says `"Turn the CLAIM into one or more assertions:"`, while DECISION RULES says `"Emit zero or more of:"`. For empty/unsupported claims, the normalizer can validly emit zero assertions (`NormalizationResponse(relations=(), observations=())`).
   - *Proposed Correction*: Update PURPOSE to: `"Turn the CLAIM into zero or more assertions:"`.

---

### Verdict

**APPROVE WITH NITS** (Scoped to PR402 at `ecedaabe4957760ebd690ffd0f2575c078fa0c70`).
The parent prompt refactoring successfully achieves plain-English clarity and eliminates jargon without sacrificing semantic rigor or token constraints. All unit, formatting, and import contract tests pass cleanly without regressions. Ready for parent final integration with PR #400 upstream fixes.
