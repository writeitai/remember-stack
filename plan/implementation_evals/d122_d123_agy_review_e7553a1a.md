# Antigravity Final Exact-Head Review: PR #403 (e7553a1a)

- **Target Repository:** `/Users/jpuc/code/moje/ultimate_memory/ugm-document-context`
- **Reviewed Head SHA:** `e7553a1ac3de0db03d02237e2988c0923bcd6fb6` (`e7553a1a`)
- **Base Commit:** `6205fe0968d3a36f783396cec31ee98a09aa7338` (`origin/main`)
- **PR:** [#403](https://github.com/writeitai/remember-stack/pull/403)
- **Binding Specifications:**
  - [D122: Stable source references shared during document extraction](../designs/document_reference_context_design.md)
  - [D123: Source-backed entity context for fact nomination](../designs/contextual_fact_nomination_design.md)
  - D119: Source-anchored multi-span extraction and evidence reuse
- **Review Date:** 2026-09-14
- **Verdict:** **APPROVED** (No material findings in the review scope; the listed type, unit and PostgreSQL checks passed. Parent CI gates remain separate.)

---

## 1. Executive Summary

PR #403 introduces the complete two-phase extraction architecture (D122) and contextual fact nomination (D123) rebased onto `main` (`6205fe09`).

The implementation strictly maintains the architecture established in prior designs and reviews:
1. **D122 (Two-Phase E2 Extraction Split):**
   - `EXTRACT_CLAIMS` executes Selection only: identifies propositions and source-backed referents, persisting the frozen result once to `selection_results`.
   - `SelectionChunkBarrier` evaluated atomically in `WorkLedger.complete_chunk_selection` under representation advisory lock transitions chunks to `GROUND_CLAIMS` only once all chunk selections for the representation exist.
   - `GROUND_CLAIMS` runs Claimify after the barrier opens. Preceding referents are bounded to whole cards within the preceding 8 chunks, formatted text capped at 4,096 characters, with citation labels (`S{next_index}`) assigned before length measurement.
   - Zero-keep chunks complete deterministically with 0 model calls.
   - Negative dependency version reuse: `claimify_input_hash` hashes the target Selection input hash, ordered Selection input hashes of all preceding 8 chunk producers (including zero-card producers), and reference policy version, stamped onto the existing `chunks` row without a separate receipt table.
   - Source grounding enforcement: only card passages cited in candidate `source_refs` enter grounding token elements; uncited card passages cannot ground tokens (`ADDED_CONTEXT_UNVERIFIED`).
   - Prompt fidelity: the reference section is plainly titled `EARLIER REFERENCES:`, and upstream source-passage wording is fully preserved.
   - Pipeline readiness and sync-cycle completion derive from both `EXTRACT_CLAIMS` and `GROUND_CLAIMS`.
2. **D123 (Contextual Fact Nomination):**
   - Assertion normalizer captures up to 4 generic context references per assertion (`MAX_CONTEXT_REFS = 4`), truncating extra items on the frozen response without rejecting the assertion.
   - Standard entity resolver resolves context entities with live decision validation (`superseded_by IS NULL` and claim mention match).
   - Atomic staging in `FactApplicationCatalog.stage` writes `application_context_bindings` with stable original ordinals and deduplicates aliases to the first ordinal.
   - Nomination preserves the same-subject 20-fact baseline (`_FACT_LIMIT = 20`) and nominates up to 8 extra facts (`_CONTEXT_FACT_LIMIT = 8`, total <= 28) sharing current positive testimony context, ordered first for model presentation.
   - Canonical subject closures are strictly excluded from context matching.
   - Internal snapshot fingerprints include all context bindings and canonical reverse-closure memberships (`context_hash`), and row lock verification detects concurrent changes (`ApplicationInputChanged`).
   - Hard-forget scrubs `selection_results` and cascades `application_context_bindings`; late publication after forget starts is rejected.
3. **LoCoMo Full-v31 Protocol Alignment:**
   - Protocol identity updated to `RS-LoCoMo-Full-v31` (`locomo-full-adapter-2026.09-document-context-v31`).
   - Pinned `EXPECTED_PROMPT_RENDERER_VERSION = "concise-handles-2"`.
   - `EXPECTED_PIPELINE_STAGES` includes `ground_claims`.
   - All extractor and normalizer generation strings match runtime constants.

---

## 2. Verification of Prior Review Findings

All findings and notes recorded in `plan/implementation_evals/d122_d123_agy_review_2c547937.md` have been addressed and verified on HEAD (`e7553a1a`):

1. **`assertion_rows` Typing in `concise_adjudication.py`:**
   - In `src/rememberstack/core/concise_adjudication.py` (line 188), `assertion_rows` is explicitly annotated:
     ```python
     assertion_rows: list[dict[str, Any]] = list(snapshot.get("assertions", ()))
     ```
   - Eliminated `list[Never]` inference; Pyright reports 0 errors in `concise_adjudication.py`.
   - **Status:** Verified.

2. **`_snapshot` Return Type in `test_d123_context_nomination.py`:**
   - In `src/tests/spine/test_d123_context_nomination.py` (line 59), `_snapshot` is annotated to return `dict[str, Any]`:
     ```python
     def _snapshot(*, engine: Engine, case: WriterCase, app: UUID) -> dict[str, Any]:
     ```
   - Eliminated all 5 Pyright indexing/membership errors.
   - **Status:** Verified.

3. **Narrowed UUID Annotation in `e3_test_doubles.py`:**
   - In `src/tests/workers/e3_test_doubles.py` (line 44), `RecordingResolver.__init__` narrowed `identities` parameter from `dict[str, object] | None` to `dict[str, UUID] | None`.
   - **Status:** Verified.

4. **Stale `_extract_chunk` Error in `test_claimify_loss_ledger.py`:**
   - In `src/tests/workers/test_claimify_loss_ledger.py`, tests drive the handler through public pipeline stages (`PipelineStage.EXTRACT_CLAIMS` then `PipelineStage.GROUND_CLAIMS`) via `handler.handle(work=work, meter=...)`.
   - No private `_extract_chunk` call remains.
   - **Status:** Verified.

5. **Full-v31 Protocol Pins Rebased & Aligned:**
   - `benchmarks/locomo/protocol.py` correctly defines `PROTOCOL_NAME = "RS-LoCoMo-Full-v31"`, pins `ground_claims`, and incorporates `:d122-source-references-1` and `:d123-context-refs-2:...:d123-context-nom-2`.
   - All 52 protocol tests in `src/tests/benchmarks/test_locomo_protocol.py` pass.
   - **Status:** Verified.

6. **Hard-Forget Scrubbing & Late-Publication Rejection:**
   - In `src/tests/spine/test_forget_catalog.py`, `test_mutable_fact_payloads_and_date_witnesses_are_erased` proves `selection_results` and `application_context_bindings` are scrubbed while control data survives.
   - `test_late_extraction_response_cannot_publish_after_forget_starts` proves both `SelectionCatalog.freeze` and `ClaimCatalog.record_extraction` reject late publication with `ForgetInProgressError`.
   - **Status:** Verified.

---

## 3. Architecture & Invariant Inspection

### 3.1 D122: Two-Phase Extraction Split & Version Reuse
- **First-Writer Wins & Atomic Publication:** `SelectionCatalog.freeze` uses `INSERT INTO selection_results ... ON CONFLICT (deployment_id, chunk_id, extractor_version) DO NOTHING`, re-reads the committed row, and asserts input hash equality.
- **Zero-Keep Determinism:** Chunks with zero kept propositions publish their cards (if any) and complete Claimify with zero LLM calls, avoiding wasted model invocations.
- **Representation Barrier:** `WorkLedger.complete_chunk_selection` uses advisory locks on `representation_id` and checks `_SELECTION_BARRIER_MISSING` to enqueue all representation chunks into `PipelineStage.GROUND_CLAIMS` only after all chunk selections exist.
- **Occurrence Remapping:** Cards are remapped using fixed relative slots `target - 9 .. target + 1` via `reference_windows` in `extraction_references.py`, correctly preserving relative neighbor identity.
- **Negative Dependency Reuse:** `claimify_input_hash` hashes the target Selection input hash, ordered Selection input hashes of all preceding 8 chunks, and the reference policy version string. Changing or invalidating any preceding chunk (even one with 0 cards) cleanly invalidates Claimify reuse while allowing Selection reuse.
- **Single Receipt Storage:** Claimify input hash is stamped directly onto `chunks.claimify_input_hash`, avoiding redundant receipt tables while guaranteeing concurrency safety under `FOR UPDATE`.

### 3.2 D123: Contextual Fact Nomination Semantics
- **Context Binding Invariants:** `FactApplicationCatalog.stage` validates a live resolver decision (`superseded_by IS NULL` and claim mention match) for each context entity, rejects duplicate ordinals, and deduplicates aliases to the first ordinal.
- **Nomination Boundaries:** `application_snapshot` nominates up to 20 baseline same-subject facts, plus up to 8 extra facts sharing current positive testimony context (`support_stance = 'supports'` and `is_current_testimony = true`), bounding total nominated facts to <= 28. Shared context facts are ordered first.
- **Snapshot Fingerprinting & Invalidation:** Fingerprint includes `context_hash` (digest of all context bindings + canonical membership hash). Any changes to context entities, memberships, or reverse closures trigger `ApplicationInputChanged`.

### 3.3 Clarity & YAGNI
- No speculative abstractions, third model calls, new event tables, per-fact event columns, or date windows.
- Database reset fixture (`src/tests/database_reset.py`) cleanly detects schema `p9_32_0053` and resets test schemas safely without weakening migration invariants.
- `SelfHostProfile` accepts `model_provider: ModelProviderPort`, enabling clean dependency injection for test doubles while keeping production defaults unchanged.

---

## 4. Checks Actually Performed

All checks were executed directly against checkout `e7553a1ac3de0db03d02237e2988c0923bcd6fb6` in `/Users/jpuc/code/moje/ultimate_memory/ugm-document-context`. Disposable PostgreSQL tests used exclusive database port 55440 (`REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55440/ugm_d119_test"`). Ports 55439, 55441, and 55442 were untouched. No repository edits were made.

### 4.1 CI Fast-Gate Quality Suite
1. **Test Inventory Drift Check:**
   ```bash
   python3 .github/ci/check_test_inventory.py
   ```
   **Result:** `test inventory OK: unit=111 integration=63 discovered=174` (Exit 0)

2. **Architecture Import Boundary Linter:**
   ```bash
   uv run lint-imports
   ```
   **Result:** Analyzed 300 files, 805 dependencies. `Contracts: 5 kept, 0 broken` (Exit 0)

3. **Ruff Linter:**
   ```bash
   uv run ruff check src/ benchmarks/
   ```
   **Result:** `All checks passed!` (Exit 0)

4. **Ruff Format Check:**
   ```bash
   uv run ruff format --check src/ benchmarks/
   ```
   **Result:** `515 files already formatted` (Exit 0)

5. **Whole-Tree Pyright Typecheck:**
   ```bash
   uv run pyright src/ benchmarks/ --pythonversion 3.13
   ```
   **Result:** `0 errors, 0 warnings, 0 informations` across entire codebase (Exit 0)

### 4.2 Unit Test Suites (No Postgres)
1. **Core & Worker PR Unit Pack (231 tests):**
   ```bash
   uv run pytest \
     src/tests/core/test_selection_references.py \
     src/tests/core/test_context_references.py \
     src/tests/core/test_concise_adjudication.py \
     src/tests/workers/test_e3_context_references.py \
     src/tests/benchmarks/test_locomo_protocol.py \
     src/tests/benchmarks/test_locomo_runner.py \
     src/tests/benchmarks/test_locomo_store_backup.py \
     src/tests/workers/test_claimify_loss_ledger.py \
     src/tests/workers/test_d119_reuse.py \
     src/tests/workers/test_d119_versions.py \
     src/tests/workers/test_e2_occurrence_provenance.py -q
   ```
   **Result:** `231 passed, 2 skipped in 10.19s` (Exit 0)

2. **LoCoMo Protocol Suite (52 tests):**
   ```bash
   uv run pytest src/tests/benchmarks/test_locomo_protocol.py -v
   ```
   **Result:** `52 passed in 4.89s` (Exit 0)

3. **Claimify Loss Ledger & Accounting Suite (35 tests):**
   ```bash
   uv run pytest src/tests/workers/test_claimify_loss_ledger.py -v
   ```
   **Result:** `35 passed in 2.75s` (Exit 0)

4. **D119 Versions & Reuse Suite:**
   ```bash
   uv run pytest src/tests/workers/test_d119_versions.py src/tests/workers/test_d119_reuse.py -v
   ```
   **Result:** `3 passed, 2 skipped in 3.52s` (Exit 0)

### 4.3 Real-PostgreSQL Integration Suites (Port 55440)
1. **D123 Context Nomination Suite (9 tests):**
   ```bash
   REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55440/ugm_d119_test" \
     uv run pytest src/tests/spine/test_d123_context_nomination.py -v
   ```
   **Result:** `9 passed in 23.31s` (Exit 0)

2. **Hard-Forget Catalog & Scrubbing Suite (6 tests):**
   ```bash
   REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55440/ugm_d119_test" \
     uv run pytest src/tests/spine/test_forget_catalog.py -q
   ```
   **Result:** `6 passed in 16.12s` (Exit 0)
   *(Combined: 15 real PG context + forget proofs passed)*

3. **D122 Selection Storage & Barrier Suite (4 tests):**
   ```bash
   REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55440/ugm_d119_test" \
     uv run pytest src/tests/spine/test_d122_selection.py -q
   ```
   **Result:** `4 passed in 55.91s` (Exit 0)

4. **D122 Cross-Section E2 Integration Suite (3 tests):**
   ```bash
   REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55440/ugm_d119_test" \
     uv run pytest src/tests/spine/test_d122_e2.py -q
   ```
   **Result:** `3 passed in 66.30s` (Exit 0)

5. **Pipeline Readiness Catalog Suite (16 tests):**
   ```bash
   REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55440/ugm_d119_test" \
     uv run pytest src/tests/spine/test_pipeline_readiness.py -q
   ```
   **Result:** `16 passed in 29.66s` (Exit 0)

6. **Spine Migration Chain & Refusal Suite (13 tests):**
   ```bash
   REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55440/ugm_d119_test" \
     uv run pytest src/tests/spine/test_migrations.py -q
   ```
   **Result:** `13 passed in 243.04s` (Exit 0)

7. **Worker E2 Chain Suite (4 tests):**
   ```bash
   REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55440/ugm_d119_test" \
     uv run pytest src/tests/workers/test_e2_chain.py -q
   ```
   **Result:** `4 passed in 30.66s` (Exit 0)

8. **Worker Reuse Lifecycle Suite (6 tests):**
   ```bash
   REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55440/ugm_d119_test" \
     uv run pytest src/tests/workers/test_reuse_lifecycle.py -q
   ```
   **Result:** `6 passed in 100.90s` (Exit 0)

9. **Worker E3 Chain Suite (6 tests):**
   ```bash
   REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55440/ugm_d119_test" \
     uv run pytest src/tests/workers/test_e3_chain.py -q
   ```
   **Result:** `6 passed in 42.50s` (Exit 0)

10. **Worker Lifecycle Reconciliation Suite (14 tests):**
    ```bash
    REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55440/ugm_d119_test" \
      uv run pytest src/tests/workers/test_lifecycle_reconciliation.py -q
    ```
    **Result:** `14 passed in 604.21s` (Exit 0)

---

## 5. Findings and Merge Recommendation

- **Material Findings:** None (0 blockers, 0 material defects).
- **Static Typing:** Clean across the entire repository (`0 errors, 0 warnings`).
- **Linter & Formatting:** Fully compliant with Ruff rules and formatting standards.
- **Specification Conformance:** Full compliance with D122, D123, and Full-v31 contracts.
- **Verdict:** **APPROVED**. PR #403 at SHA `e7553a1ac3de0db03d02237e2988c0923bcd6fb6` is structurally sound, comprehensively tested, and ready for parent merge upon CI completion.


Parent note: the review inspected runtime `e7553a1a`. Later commits correct only temporal-prompt and retrieval-chain test fixtures and update this evidence record. The review did not run every repository integration suite; its exact commands and results are listed above.
