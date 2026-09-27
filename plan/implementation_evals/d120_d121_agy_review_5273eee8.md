# Antigravity review of D120/D121

Read-only. Command:

```text
agy --dangerously-skip-permissions --print-timeout 180m0s -p "<review prompt>"
```

Reviewed SHA: `5273eee8197d8b7c89c5d12c70c9000661d9d902`.
Worktree: `/Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication`.
Prompt: `/tmp/ugm-d120-d121-grok-20260914/agy-review-prompt.txt`.
The reviewer's waiting-status lines before the report are omitted.

# Independent Read-Only Implementation Review: RememberStack D120 / D121

**Review Target:**
- **Repository Head SHA:** `5273eee8197d8b7c89c5d12c70c9000661d9d902` (Verified via `git rev-parse HEAD`)
- **Branch:** `feat/concise-adjudication-inputs`
- **Worktree:** `/Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication`
- **PR:** https://github.com/writeitai/remember-stack/pull/402 (draft)
- **Base:** `origin/main` (Design squash `4a803c1d`, PR #401 merged)
- **Status:** Clean working tree; read-only inspection performed.

---

## Executive Verdict: **APPROVE WITH NITS**

The implementation cleanly and faithfully delivers the bindings of [D120](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/plan/designs/processing_prompt_clarity_design.md) (processing prompt clarity, bidirectional proposition preservation, date-neutral proposition handling) and [D121](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/plan/designs/concise_adjudication_inputs_design.md) (deterministic concise evidence projection, closed typed handle translation, strict CAS/fingerprint attempt isolation).

All static and runtime checks pass cleanly:
- **`ruff format` & `ruff check`:** Clean (0 errors).
- **`import-linter`:** 5 contracts kept, 0 broken.
- **`check_test_inventory.py`:** Discovered 163 tests (106 unit, 57 integration) matching inventories.
- **`pyright`:** 0 errors, 0 warnings.
- **Unit Suite:** 90 tests passed in 5.12s.
- **PostgreSQL Integration Suite:** 23 tests passed (7 concise adjudication PG proofs + 16 fact application writer tests) on the lane's test database.

---

## Detailed Evaluation by Review Focus

### 1. Correctness of the Typed Projector & Closed Decision Translator
- **Attempt-Local Projection:**
  In [`src/rememberstack/core/concise_adjudication.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/core/concise_adjudication.py#L174-L466), `project_concise_inputs` derives a deterministic, compact presentation from the frozen prepared snapshot. Mappings for `F1..`, `C1..`, `A1..`, `E1..`, `S1..` are built bijectively from stable row order.
- **Closed Schema Translation:**
  [`translate_prompt_decision`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/core/concise_adjudication.py#L512-L581) translates [`PromptFactDecision`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/model/concise_adjudication.py#L83-L143) into [`FactApplicationDecision`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/model/fact_application.py#L107-L135).
- **Collision and Type Enforcement:**
  [`_fact_reference`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/core/concise_adjudication.py#L487-L497) and [`_require`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/core/concise_adjudication.py#L468-L485) reject malformed, wrong-kind, or unsupplied handles without guessing. Declared new-fact names are explicitly prohibited from colliding with reserved prefixes (`F`, `C`, `A`, `E`, `S`, `T`, `W`):
  ```python
  # src/rememberstack/core/concise_adjudication.py:524-526
  declared = {fact.handle for fact in response.new_facts}
  for handle in declared:
      if _typed_kind(handle) is not None:
          raise ValueError(f"new-fact handle {handle} collides with a supplied name")
  ```
- **Constraint Invariants:**
  [`PromptFactDecision.consistent_references`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/model/concise_adjudication.py#L110-L142) rejects duplicate window replacements, unassigned/undeclared new handles, duplicate assertion moves, and self-contradictions at parse time.

### 2. Prompt Clarity for a Cold Human Reader
- **Vocabulary & Role Separation:**
  The prompts across E2 selection/claimify, E3 normalization, and spine fact adjudication now share consistent, human-readable definitions under a prominent `WHAT THESE WORDS MEAN` block:
  ```text
  WHAT THESE WORDS MEAN
  - A claim is the immutable record of what a source said, including exact wording.
  - An assertion is one relation or observation taken from a claim: one proposition.
  - A fact is the stored interpretation of supporting and contrary testimony about
    that proposition. Facts can change as new evidence arrives.
  - An entity is a person, event, place, or other referent. Sharing an entity is
    not the same as sharing a proposition. "Nate won Tournament A", "Nate
    participated in Tournament A", and "Nate enjoyed Tournament A" concern the
    same people and event but assert different things.
  ```
- **Clock Discrimination:**
  Distinguishes source reporting time (`source_said_at`), source-stated world dates (`source_world_*`), and chosen fact dates (`chosen_from`, `chosen_until`, `chosen_precision`). Database belief timestamps are explicitly excluded from the model envelope and never conflated with world dates.

### 3. Date Ambiguity & Proposition Immutability
- **Half-Open Intervals & Unit Semantics:**
  Prompts in [`_FACT_PROMPT`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/fact_adjudication.py#L103-L125) and [`_NORMALIZE_PROMPT`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/workers/e3.py#L123-L126) clearly state that raw source dates are inclusive while stored ends are exclusive:
  ```text
  - Raw source ends are inclusive. "3 November through 5 November" at day
    precision becomes the stored window [3 November 00:00 UTC, 6 November 00:00
    UTC). Stored ends are already exclusive; do not advance 6 November again.
  - A day is a calendar day, not a precisely observed midnight instant.
    Month/year precision must not invent a precise day.
  - A missing end does not mean ongoing. Only explicitly supported precision=open
    has that meaning.
  ```
- **No Statement Rewrites:**
  The prompt contract expressly forbids assuming the writer can rewrite an existing fact's statement upon date correction:
  ```text
  - Incoming correction of the same win from 5 November to 6 November; candidate
    "won Tournament A" with chosen window 5 November: attach and replace that
    chosen window with cited C-names. The stored statement stays the date-neutral
    win; the writer cannot rewrite it.
  ```

### 4. Semantic Information Retention
- **Structural Integrity:**
  - `doc_id` / source distinctions: Identical text across distinct sources produces separate `C` claims mapped to distinct `S` source handles; the text is factored into `text` (e.g. `T1`) for presentation deduplication only, retaining each occurrence's attribution and timestamps.
  - Witnesses: Hydrated window claims are rendered as `window_claims: ["C1"]`. Unhydrated window witnesses present in the database but absent from the bounded claims payload are disclosed as `window_claims_not_supplied: ["W1"]`.
  - Aliases: Redirected entities retain canonical equivalence via `same_as` links in `entities`, and `canonical_subject` / `canonical_object` are explicitly emitted whenever an assertion or fact's entity handle differs from the canonical cluster survivor.
  - Contradictions & Evidence: Groups are emitted as `contradiction_sets` of `F`-handles when > 1 member is in the candidate set; individual `evidence_count` and `contradict_count` are retained; incomplete evidence rows increment `evidence_not_supplied`.
  - Assertion Content: Factoring repeated statements replaces only the `"statement"` key with `"statement_ref"` inside normalized assertion dictionaries via [`_compact_content`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/core/concise_adjudication.py#L136-L156), leaving qualifiers and bindings intact.

### 5. Mapping, Retry, Forget & Concurrency Safety
- **Handle Validation:**
  Unknown handles or wrong-prefix handles (e.g. `C1` as target, `F99`, `W1` as supporting claim) raise `ValueError` inside [`translate_prompt_decision`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/core/concise_adjudication.py#L468-L581). In [`FactAdjudicator.drain`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/fact_adjudication.py#L227-L233), this is trapped and raised as [`ApplicationInputChanged`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/fact_applications.py#L27), leaving the database decision unwritten and allowing safe retry.
- **Stale Attempt Rejection via CAS & Fingerprint:**
  In [`FactApplicationCatalog.publish_decision`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/fact_applications.py#L390-L421):
  ```sql
  UPDATE fact_applications a SET decision=CAST(:decision AS jsonb)
  WHERE deployment_id=:deployment_id AND application_id=:application_id
    AND attempt_id=:attempt_id AND input_hash=:input_hash
    AND decision IS NULL AND applied_at IS NULL
    AND NOT EXISTS (SELECT 1 FROM unnest(a.input_claim_ids) id
      WHERE NOT EXISTS (SELECT 1 FROM claims c WHERE c.deployment_id=a.deployment_id AND c.claim_id=id))
  RETURNING application_id
  ```
  A response for an older attempt fails CAS because `attempt_id` has advanced, NOT because `F1` changed spelling. This invariant is directly verified by [`test_stale_attempt_is_rejected_by_cas_not_f1_spelling`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/tests/spine/test_concise_adjudication_pg.py#L240-L280).
- **Source Deletion / Forget Protection:**
  The subquery `NOT EXISTS (SELECT 1 FROM unnest(a.input_claim_ids) id WHERE NOT EXISTS (SELECT 1 FROM claims ...))` guarantees that if any input claim was forgotten during inference, publication is blocked. Verified by [`test_deleted_source_cannot_publish_translated_decision`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/tests/spine/test_concise_adjudication_pg.py#L215-L238).

### 6. Simplicity & YAGNI
- No speculative context-only facts, `editable` flags, checker models, or external mapping registries were introduced.
- Deterministic projection and translation run entirely in memory using standard library data structures.
- Tests do not treat keyword presence as semantic quality proof; contract tests verify human-readable instructions exist, while model quality is acknowledged as an eval-stage concern.

---

## Findings

### Nit 1: Missing Exception Chaining in `_prompt_schema_size_report`
- **Severity:** Low / Nit
- **File:** [`src/tests/core/test_concise_adjudication.py:835-836`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/tests/core/test_concise_adjudication.py#L835-L836)
- **Code:**
  ```python
  except Exception:
      pass
  ```
- **Why:** In `_prompt_schema_size_report`, the optional `tiktoken` proxy import/encode block catches bare `Exception` and silently passes. While `tiktoken` is intentionally not a dev dependency, catching `Exception` without checking for `ImportError` / `ModuleNotFoundError` could swallow unexpected errors (e.g. `AttributeError` or encoding issues) if `tiktoken` is present in an environment.
- **Recommendation for Post-Integration:** Narrow to `except (ImportError, ModuleNotFoundError): pass`.

### Nit 2: Unused Local Function in `test_core_manifest` Fixtures / Test Harness
- **Severity:** Informational / Nit
- **File:** [`src/tests/core/test_concise_adjudication.py:480`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/tests/core/test_concise_adjudication.py#L480)
- **Code:**
  ```python
  _presentation, mapping = project_concise_inputs(snapshot=_snapshot())
  ```
- **Why:** In tests verifying translation failure on unknown handles (`test_wrong_kind_and_unknown_handles_fail`), `_presentation` is unused. Prefixing with `_` is clean, but a direct mapping helper could reduce boilerplate in unit test fixtures.

---

## Evaluation of Intended Fixes for this SHA

| Intended Fix | Status | Exact Evidence |
| :--- | :--- | :--- |
| **Bidirectional proposition preservation** | **VERIFIED** | Prompts in `_SELECTION_PROMPT`, `_CLAIMIFY_PROMPT`, `_NORMALIZE_PROMPT`, and `_FACT_PROMPT` explicitly state: "keep stronger assertions separate from weaker related ones", "participation or enjoyment of that tournament is not positive evidence of a win", and "do not collapse a win into participation". Verified by unit tests in `test_concise_adjudication.py:497-530`. |
| **Date-correction example is date-neutral** | **VERIFIED** | Candidate in prompt and D120 design is `"won Tournament A"` with chosen window 5 November (NOT `"won on 5 November"`). Statement rewriting is explicitly disclaimed: `"The stored statement stays the date-neutral win; the writer cannot rewrite it."` |
| **Stale attempt rejection via CAS/fingerprint** | **VERIFIED** | Database CAS on `(attempt_id, input_hash)` in `publish_decision` rejects stale attempts. Proven by PG integration test `test_stale_attempt_is_rejected_by_cas_not_f1_spelling`. |
| **Size evidence compares full prompt+schema** | **VERIFIED** | Compares old prompt + UUID schema against new prompt + handle schema across small (13,142 vs 9,345 bytes), varied (16,765 vs 17,457 bytes), and best-case repeated (28,890 vs 73,558 bytes) fixtures. Accurately labeled as UTF-8 bytes and optional proxy, not billed tokens. |

---

## Known Draft Limits & Residual Risks

1. **D119 PR #400 Multi-Span Claims Integration:**
   As recognized in `plan/handoffs/2026-09-14-d120-d121-grok.md`, PR #400 is concurrently implementing multi-span extraction. When rebasing onto PR #400:
   - Do NOT overwrite D119's coherent multi-span claim language in `_CLAIMIFY_PROMPT` with simplest-atomic splitting.
   - Project any new multi-span fields that `claims` snapshots introduce into `project_concise_inputs`.
   - Regenerate final component versions and Full-v29 protocol pins from the unified source.
2. **Real-Model Inference vs Mocked Doubles:**
   All unit and PostgreSQL integration tests use `FakeModelProvider` or static response routers. While they provide complete, rigorous verification of serialization, schema validation, mapping translation, CAS, and database persistence invariants, they do not measure actual LLM semantic compliance in production. Full LoCoMo v29 benchmark execution will be necessary once integrated.
3. **Byte Counts vs Billed Tokens:**
   Compaction savings are measured in raw UTF-8 bytes and optional `cl100k_base` proxy tokens. These do not represent exact billed token counts or vendor pricing.

---

## Conclusion

This SHA (`5273eee8197d8b7c89c5d12c70c9000661d9d902`) completely and cleanly resolves all parent review findings, correctly implements concise projection and handle translation, maintains strict database invariants and concurrency guarantees, and clarifies processing prompts for human readability without introducing speculative complexity.

**Verdict: APPROVE WITH NITS** (Ready for integration with D119 PR #400).
