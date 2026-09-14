# Antigravity review of D120/D121 lean-prompt checkpoint

Read-only. Command:

```text
agy --dangerously-skip-permissions --print-timeout 180m0s -p "<review prompt>"
```

Reviewed SHA: `d84af5a48a0f5af1149a4c6bbd5aaf6b201cd5e6`.
Prompt commit: `ef17559a1616c8c276ea5934658aedf5b5029d6d`.

# Independent Read-Only Implementation Review: D120 / D121 Checkpoint

**Review Target:**
- **Repository Head SHA:** `d84af5a48a0f5af1149a4c6bbd5aaf6b201cd5e6` (Confirmed via `git rev-parse HEAD`)
- **Substantive Prompt Commit:** `ef17559a1616c8c276ea5934658aedf5b5029d6d`
- **Branch:** `feat/concise-adjudication-inputs`
- **Worktree:** `/Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication`
- **PR:** [#402](https://github.com/writeitai/remember-stack/pull/402) (draft, not merge-ready)
- **Status:** Read-only inspection complete; working tree clean; no remote stores, DB resets, or paid model calls.

---

## Executive Summary & Verdict

### Verdict: **APPROVE WITH NITS**

The lean-prompt checkpoint in `ef17559a` effectively shortens `_FACT_PROMPT` (from ~7.1 kB template down to 4,393 UTF-8 bytes) by unifying previously duplicated purpose, rules, and example blocks into coherent, human-readable prose. It successfully retains **all required semantic invariants** without introducing new classifiers, flags, or routing machinery. The reconstructed 20-fact / 40-claim / 40-application measurement fixture faithfully benchmarks the realistic bounded path, cleanly documents and tests that measurements are UTF-8 bytes rather than billed tokens, and prompt-contract tests no longer freeze superficial section headers.

Static checks and test suites run cleanly:
- **`pyright`**: 0 errors, 0 warnings.
- **`ruff check` & `ruff format --check`**: All checks passed; 2 files already formatted.
- **`pytest src/tests/core/test_concise_adjudication.py`**: 13 passed in 5.98s.
- **`pytest src/tests/core/`**: 485 passed in 12.05s.

---

## Evaluation of `_FACT_PROMPT` Semantic Requirements

File: [`src/rememberstack/spine/fact_adjudication.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/fact_adjudication.py#L38-L111)

### 1. Bidirectional Proposition Identity
- **Requirement:** Distinguish win vs. participate vs. enjoy; attach only on the FULL proposition in *both* directions (incoming stronger than candidate, and incoming weaker/contextual candidate).
- **Shipped Text:**
  ```text
  Lines 50–55:
  Attach as supports only when the testimony supports the
  FULL proposition, including attribution, negation and necessary qualifiers.
  - "Took first place in Tournament A" can repeat "won Tournament A".
  - Winning, participating and enjoying that tournament are different propositions.
    A win is not participation or enjoyment, and participation or enjoyment is not
    positive evidence of a win just because the event is shared.
  ```
- **Assessment:** Fully preserved. The prompt states that a win cannot be reduced to participation/enjoyment, and participation or enjoyment is not positive evidence of a win simply because they share the event. Attachment is explicitly conditioned on the FULL proposition.

### 2. Date-Neutral Correction
- **Requirement:** Candidate "won Tournament A", chosen window 5→6 November; writer cannot rewrite statements.
- **Shipped Text:**
  ```text
  Lines 61–65:
  The writer cannot rewrite an
  existing statement; create a fact when no supplied statement can represent the
  assertion. Do not mint another identity merely because testimony repeats or
  corrects dates. A correction can keep "won Tournament A" while changing its
  chosen date from 5 November to 6 November.
  ```
- **Assessment:** Fully preserved. The prompt uses the date-neutral statement `"won Tournament A"` (avoiding the prior defect where the statement text itself embedded `"won on 5 November"`), states that the writer cannot rewrite statements, and clarifies that date adjustments replace the chosen window without minting a duplicate fact identity. Tested via `assert "won on 5 November" not in _FACT_PROMPT`.

### 3. Source Reporting Time vs. Raw Inclusive Dates vs. Exclusive Chosen Dates vs. Belief Times
- **Requirement:** Differentiate source reporting time (`source_said_at`), raw inclusive `source_world_*`, exclusive canonical `chosen_*`, and unused database belief timestamps.
- **Shipped Text:**
  ```text
  Lines 77–80:
  source_said_at is when a source spoke or published, never a fallback world date.
  source_world_* are raw source dates with inclusive ends. chosen_* are stored
  canonical UTC bounds with EXCLUSIVE ends. Database belief times are not shown
  and never determine world dates.

  Lines 95–97:
  A supported succession update may cap a supplied predecessor at its successor's
  WORLD start, never now or publication time. Another win or the end of a reporting
  period does not close belief in the earlier fact.
  ```
- **Assessment:** Fully preserved. All four temporal concepts are clearly distinguished.

### 4. Inclusive-to-Exclusive Day Example
- **Requirement:** "3 through 5 November" becomes `[3 Nov 00:00 UTC, 6 Nov 00:00 UTC)`; do not advance stored ends again.
- **Shipped Text:**
  ```text
  Lines 81–82:
  "3 through 5 November" at day precision becomes [3 November 00:00 UTC,
  6 November 00:00 UTC). Do not advance an already stored end again.
  ```
- **Assessment:** Fully preserved verbatim with explicit instruction not to advance already stored ends.

### 5. Calendar Boundaries Aligned to Unit Starts
- **Requirement:** Day/month/quarter/year boundaries aligned to unit starts, not observed midnight.
- **Shipped Text:**
  ```text
  Lines 82–84:
  Calendar
  precision uses the corresponding day/month/quarter/year boundaries aligned to
  unit starts; it is not an exactly observed midnight.
  ```
- **Assessment:** Fully preserved and improved over parent candidate by including `"aligned to unit starts"`.

### 6. Exact Instant Window
- **Requirement:** Exact instant is a one-microsecond window.
- **Shipped Text:**
  ```text
  Lines 84–85:
  An exact instant uses a
  one-microsecond window.
  ```
- **Assessment:** Fully preserved.

### 7. Missing End vs. Precision=Open
- **Requirement:** Missing end is unknown unless `precision=open`; known start + unknown end is not open.
- **Shipped Text:**
  ```text
  Lines 86–87:
  A missing end means unknown unless evidence explicitly supports precision=open
  (ongoing). Known start with unknown end keeps its boundary precision, never open.
  ```
- **Assessment:** Fully preserved. Specifically strengthens the parent draft by appending `", never open."`.

### 8. `uses_claim_window` Scoping
- **Requirement:** `uses_claim_window` applies only to the specific assertion it belongs to.
- **Shipped Text:**
  ```text
  Lines 92–94:
  For a new fact, uses_claim_window copies the canonical claim window only for the
  assertion it applies to; otherwise dates start unknown. A claim mentioning hiring
  in 2019 and founding in 1990 does not date both alike.
  ```
- **Assessment:** Fully preserved with the concrete hiring (2019) vs founding (1990) counterexample.

### 9. References, Merged Aliases, Support Moves, and W-names
- **Requirement:** `support_moves` vs. incoming `target`; W-names non-citable; `same_as`; T-names indicate wording, not identity.
- **Shipped Text:**
  ```text
  Lines 69–74:
  same_as/canonical_* identify merged aliases.
  T-names refer to exact repeated wording in the text dictionary; equal wording
  from separate sources is still separate testimony. W-names disclose window
  witnesses whose text is not supplied; you may not cite them.
  Use supplied names of the required kind. New facts need distinct declared names
  such as win or N1, not reserved F/C/A/E/S/T/W names. No guessed IDs or names.

  Lines 101–106:
  Use the closed schema. target assigns the incoming assertion. support_moves
  reassign older A-names with their expected F-name to an explicit target; never
  move the incoming assertion this way or move support automatically by date.
  Every declared new fact must receive evidence. Window supporting_claims may
  cite only supplied C-names. Unknown names, wrong kinds, and W-names are
  rejected.
  ```
- **Assessment:** Fully preserved. Clearly distinguishes assigning the incoming assertion (`target`) from reassigning older assertions (`support_moves`), forbids citation of `W-names`, notes `same_as`, and explicitly documents that `T-names` are shared wording across sources, not identical testimony.

### 10. No New Classifier, Flags, or Machinery
- **Diff Inspection:**
  The diff for commit `ef17559a` in [`src/rememberstack/spine/fact_adjudication.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/fact_adjudication.py) modifies only:
  1. Component version strings: `relation-adjudicator-2026.09d:concise-handles-3` and `obs-adjudicator-2026.09d:concise-handles-3`.
  2. The prompt constant `_FACT_PROMPT`.
- **Assessment:** No new classes, methods, flags, parameters, or dispatch branches were added.

---

## Review of Bounded Measurement Fixture & Size Reporting

File: [`src/tests/core/test_concise_adjudication.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/tests/core/test_concise_adjudication.py#L793-L1059)

### 1. Fixture Construction (`_bounded_varied_snapshot`)
- **Structure:**
  - 20 distinct facts (`statements[0..19]`, `fact_id` 1000–1019).
  - 40 claims (2 claims per fact: `claim_a` and `claim_b`).
  - 40 applications/assertions (2 assertions per fact: `app_a` and `app_b`).
  - Realistic variety: First 5 statements reuse local LoCoMo v28 source-linked audit wording; subsequent statements are plausible conversational facts; includes shared source spans (e.g. `shared_span = "It's about loss, identity, and connection."`), cross-source duplicates, and unhydrated window witnesses (`witness = UUID(int=9000 + index) if index % 4 == 0 else None`).
- **Invariants Verified by Test:**
  Lines 1048–1052:
  ```python
  presentation, _mapping = project_concise_inputs(snapshot=_bounded_varied_snapshot())
  assert len(presentation["facts"]) == 20
  assert len(presentation["claims"]) == 40
  assert len(presentation["assertions"]) == 40
  assert any(row.get("window_claims_not_supplied") for row in presentation["facts"])
  ```

### 2. Labeling of Metrics (UTF-8 Bytes vs. Billed Tokens)
- **Implementation in `_prompt_schema_size_report`:**
  - Every size key is labeled `*_utf8_bytes` (e.g. `full_snapshot_utf8_bytes`, `compact_input_utf8_bytes`, `old_prompt_plus_schema_utf8_bytes`, `new_prompt_plus_schema_utf8_bytes`).
  - Token-like keys are restricted to optional proxy tokenizer counts (`*_tiktoken_cl100k_proxy`) and explicit guard flags:
    ```python
    report: dict[str, Any] = {
        "fixture": label,
        "reconstructed": True,
        "not_billed_tokens": True,
        ...
    }
    ```
  - Test enforcement (lines 1033–1037):
    ```python
    for key in report:
        if "token" in key:
            assert key == "not_billed_tokens" or key.endswith(
                "_tiktoken_cl100k_proxy"
            )
    ```
- **Evaluation Document:**
  [`plan/implementation_evals/d120_d121_implementation_20260914.md`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/plan/implementation_evals/d120_d121_implementation_20260914.md#L157-L159):
  Explicitly documents: *"No billed-token saving is claimed. Whitespace word counts are not reported as model tokens."*

### 3. Prompt-Contract Tests
- **Section Heading Freezing Removed:**
  In [`src/tests/core/test_concise_adjudication.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/tests/core/test_concise_adjudication.py#L497-L528), the old rigid assertions freezing section headings (`PURPOSE`, `INPUTS`, `DECISION RULES`, `EXAMPLES`, `OUTPUT`) were removed.
- **Semantic Assertions Retained:**
  Tests now assert required semantic phrases:
  ```python
  assert "source_said_at" in _FACT_PROMPT
  assert "untrusted" in _FACT_PROMPT
  assert "W-names" in _FACT_PROMPT
  assert "same_as" in _FACT_PROMPT
  assert "FULL proposition" in _FACT_PROMPT
  assert "positive evidence of a win" in _FACT_PROMPT
  assert "won Tournament A" in _FACT_PROMPT
  assert "5 November" in _FACT_PROMPT and "6 November" in _FACT_PROMPT
  assert "won on 5 November" not in _FACT_PROMPT
  assert "one-microsecond" in _FACT_PROMPT
  assert "quarter" in _FACT_PROMPT
  ```

---

## Findings

### Finding 1 (Nit - Low Severity)
- **File:** [`src/rememberstack/spine/fact_adjudication.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/rememberstack/spine/fact_adjudication.py#L61-L65)
- **Quoted Lines:**
  ```python
  61: Completed historical facts remain candidates. The writer cannot rewrite an
  62: existing statement; create a fact when no supplied statement can represent the
  63: assertion. Do not mint another identity merely because testimony repeats or
  64: corrects dates. A correction can keep "won Tournament A" while changing its
  65: chosen date from 5 November to 6 November.
  ```
- **Observation:** The phrasing *"The writer cannot rewrite an existing statement; create a fact when no supplied statement can represent the assertion"* is clear to downstream implementers, but in edge cases where an incoming claim asserts a corrected date alongside a new qualification (e.g. "won Tournament A as an amateur"), an LLM could wonder whether to attach or split. The surrounding text ("Do not mint another identity merely because testimony repeats or corrects dates") sufficiently guards the core case, but when D119 multi-span extraction lands, verifying that prompt examples remain clear regarding statement creation will be beneficial.

### Finding 2 (Nit - Informational)
- **File:** [`src/tests/core/test_concise_adjudication.py`](file:///Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication/src/tests/core/test_concise_adjudication.py#L508-L524)
- **Quoted Lines:**
  ```python
  508:     assert "source_said_at" in _FACT_PROMPT
  ...
  523:     assert "one-microsecond" in _FACT_PROMPT
  524:     assert "quarter" in _FACT_PROMPT
  ```
- **Observation:** While `test_prompts_state_assertion_identity_in_plain_language` verifies `source_said_at`, `one-microsecond`, and `quarter`, it does not explicitly assert the presence of `source_world_*` and `chosen_*` (even though both are in `_FACT_PROMPT` at lines 78–79). Adding simple string assertions for `source_world_` and `chosen_` in that contract test would prevent accidental regression during future prompt edits.

---

## Conclusion

Commit `ef17559a` and HEAD `d84af5a4` represent a solid, clean, and well-verified checkpoint. The reduction in prompt size is achieved without sacrificing any semantic rules, date semantics, or identity safeguards. Draft pins and pending D119 integration remain acknowledged non-defects.
