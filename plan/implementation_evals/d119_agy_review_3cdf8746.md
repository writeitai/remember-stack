# Independent Read-Only Final Review: D119 PR #400

**Repository:** `/Users/jpuc/code/moje/ultimate_memory/ugm-multispan-claims`  
**Branch:** `feat/multi-span-claim-extraction`  
**Head Commit SHA:** `3cdf874640b7a1d25ac70178b1bcd0e134c60c8d` (verified via `git rev-parse HEAD`; working tree clean, published and up to date with remote)  
**Primary References:** [CLAUDE.md](../../CLAUDE.md), [D119 Design](../../plan/designs/multi_span_claim_extraction_design.md), [D119 Analysis](../../plan/analysis/multi_span_claim_extraction.md), [decisions.md (D119)](../../decisions.md#L5795-L5833)  
**Review Execution:** Pure read-only. No files modified, no databases reset, port 55440 untouched, no paid model calls, no remote benchmark runs.

---

## Executive Summary & Verdict

### **Verdict: APPROVE**

The implementation on PR #400 exact commit `3cdf874640b7a1d25ac70178b1bcd0e134c60c8d` satisfies the full accepted scope of **D119** without architectural leakage, speculative machinery, or regression gate weakening.

1. **Coherent extraction & grounding:** The engine assigns deterministic, request-local labels (`S1`, `S2`, …) to clipped blocks and kept ranges within the target and same-section neighbors. The model selects from supplied references; it never invents character offsets. Origin ownership requires overlap with a Selection-kept proposition; non-origin citations allow supporting neighbor body text without resurrecting dropped propositions.
2. **Deterministic version reuse (D56):** Preserves identical claim UUIDs across content-identical extraction windows. Remapping translates every span through unambiguous `(target, previous, next)` window slots based on relative offsets inside content-identical windows with exact text equality verification. Substring-first-match (`str.find`) ambiguity and side swapping are prevented.
3. **Database schema & migration refusal:** Migration [`p9_31_0052`](../../src/rememberstack/spine/migrations/versions/p9_31_0052_multi_span_claim_evidence.py) strictly refuses populated claim stores without data loss. The immutable SQL constraint helper [`chunk_claims_evidence_spans_ok`](../../src/rememberstack/spine/migrations/versions/p9_31_0052_multi_span_claim_evidence.py#L84-L112) is NULL-safe, rejects non-array/empty/>8/fractional/negative/inverted shapes, has its `EXECUTE` privilege revoked from `PUBLIC`, and preserves the view-owner / query-role privilege split.
4. **Hydration & coordinate consistency:** Scalar claim fields (`char_start`, `char_end`, `source_span`) remain immutable origin anchors. API [`EvidenceResult.evidence_spans`](../../src/rememberstack/model/envelope.py#L285-L315) consistently selects the origin occurrence matching its [`chunk_id`](../../src/rememberstack/model/envelope.py#L295-L305), while [`memory_v1.claim_occurrences_live`](../../src/rememberstack/spine/migrations/versions/p9_31_0052_multi_span_claim_evidence.py#L22-L62) serves current-version remapped positions.
5. **Full 500-version proof & integration:** The full 500-version E0–E3 lifecycle test [`test_500_versions_reuse_handler_remap_and_lineage_facts`](../../src/tests/workers/test_d119_versions.py#L538-L613) with two separately labeled passages, all 500 occurrences checked, [`LifecycleCatalog.recount`](../../src/rememberstack/spine/lifecycle_catalog.py) count = 1, and edited-context invalidation passed in 609.85s on the sequential test runner.

---

## Detailed Requirement Analysis

### 1. Coherent Extraction & Passage Grounding
* **Prompt contracts:** [`_SELECTION_PROMPT`](../../src/rememberstack/workers/e2.py#L163-L179) and [`_CLAIMIFY_PROMPT`](../../src/rememberstack/workers/e2.py#L182-L216) instruct the models to extract coherent assertions across conjunctions while keeping independently dated or attributed events separate.
* **Deterministic labeling:** [`build_passage_catalog`](../../src/rememberstack/core/source_passages.py#L107-L170) clips blocks to target and same-section previous/next chunks, merging Selection-kept ranges.
* **Offset separation:** The LLM receives [`render_passage_catalog`](../../src/rememberstack/core/source_passages.py#L172-L188) with labels `[S1]`, `[S2]`, ... and outputs [`CandidateClaim.source_refs`](../../src/rememberstack/model/claims.py#L175-L188). Character offsets are never invented by the model.
* **Origin ownership & keep overlap:** [`resolve_source_refs`](../../src/rememberstack/core/source_passages.py#L190-L245) requires that the first reference be `origin_eligible` (in target chunk and overlapping a Selection keep). In [`_grounded_claim`](../../src/rememberstack/workers/e2.py#L825-L930), failure rejects with [`GroundingGate.ORIGIN_NOT_ELIGIBLE`](../../src/rememberstack/workers/e2.py#L801) or [`GroundingGate.OUTSIDE_KEPT_RANGES`](../../src/rememberstack/workers/e2.py#L798).
* **Bounded span list:** Bounded cap [`MAX_EVIDENCE_SPANS = 8`](../../src/rememberstack/core/source_passages.py#L31-L35). Excess references trigger [`GroundingGate.TOO_MANY_SOURCE_REFS`](../../src/rememberstack/workers/e2.py#L802).
* **Canonicalization:** [`canonicalize_spans`](../../src/rememberstack/core/source_passages.py#L247-L263) keeps origin first, deduplicates, and sorts secondary spans in document order. Disjoint spans remain disjoint intervals (no document-wide bounding box).
* **Provenance vs. Entailment:** Exact body citations ground provenance; [`entailment_self_verdict`](../../src/rememberstack/model/claims.py#L189) is recorded advisory, while loss-ledger recording ([`_claimify_omitted_decision`](../../src/rememberstack/workers/e2.py#L1340-L1365), [`_grounding_rejected_decision`](../../src/rememberstack/workers/e2.py#L1300-L1338)) accounts for drops and ungrounded claims. Tested in [`test_handler_whole_block_origin_does_not_resurrect_drop`](../../src/tests/workers/test_claimify_loss_ledger.py#L827-L873).

### 2. D56 Version Reuse & Occurrence Remapping
* **Fingerprint stability:** In [`_chunk_record`](../../src/rememberstack/workers/e1.py#L579-L600) and [`extraction_input_hash`](../../src/rememberstack/core/chunker.py#L141-L155), `neighbor_block_hashes` is a strict `(previous_hash, next_hash)` pair for same-section neighbors (`section_id == chunk.section_id`), using `""` for absent neighbors.
* **Side & absence identity:** Moving identical text from previous to next alters the hash, preventing coreference direction errors. Tested in [`test_previous_only_and_next_only_neighbor_hashes_differ`](../../src/tests/workers/test_d119_reuse.py#L231-L247).
* **Occurrence remapping:** In [`remap_evidence_spans`](../../src/rememberstack/core/source_passages.py#L265-L306), spans map through matching slots in [`window_bounds`](../../src/rememberstack/core/source_passages.py#L308-L325) `(target, previous, next)`. Relative offsets within the matching window translate into the new window, and prior text is verified against current text: `prior_md[...] == current_md[...]`.
* **Zero substring-first-match:** Offsets are computed mathematically via window deltas, completely eliminating substring search collisions. Tested in [`test_remap_uses_window_offsets_not_first_substring`](../../src/tests/core/test_source_passages.py#L140-L163).
* **Claim identity preservation:** [`ExtractClaimsHandler._reuse_extracted_chunk`](../../src/rememberstack/workers/e2.py#L450-L506) re-attaches the exact prior claim UUIDs via [`attach_reused_claims`](../../src/rememberstack/spine/claim_catalog.py#L104-L175).

### 3. Media Provenance & Occurrence Locators
* **Multi-span aggregation:** [`resolve_spans_occurrence_provenance`](../../src/rememberstack/model/occurrence_provenance.py#L174-L202) evaluates each span against converter derivation ranges and unions locators via [`_locator_union`](../../src/rememberstack/model/occurrence_provenance.py#L368-L387) without interpolating gaps.
* **Conservative mediation mode:** [`_merge_span_provenances`](../../src/rememberstack/model/occurrence_provenance.py#L344-L366) requires every span to be labeled before assigning a mode (most-mediated mode wins). If any span is unlabeled, mode remains `None` (fails safe, never claims complete `source_expression`). Tested in [`test_mixed_known_and_unknown_spans_are_not_source_expression`](../../src/tests/model/test_occurrence_provenance.py#L141-L157).

### 4. Database Migration & SQL Constraint
* **Refusal guard:** [`p9_31_0052_multi_span_claim_evidence.py`](../../src/rememberstack/spine/migrations/versions/p9_31_0052_multi_span_claim_evidence.py#L65-L81) locks `claims` and `chunk_claims` in `ACCESS EXCLUSIVE` mode and checks `SELECT EXISTS`. Populated tables raise a descriptive `RuntimeError`, preserving existing data and leaving the migration at `p9_30_0051`. Tested in [`test_upgrade_refuses_populated_pre_d119_claim_store`](../../src/tests/spine/test_d119_migration.py#L33-L99).
* **SQL check helper:** [`chunk_claims_evidence_spans_ok`](../../src/rememberstack/spine/migrations/versions/p9_31_0052_multi_span_claim_evidence.py#L84-L112) is marked `IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog, pg_temp`. It validates:
  * Non-array payloads reject.
  * Array lengths outside `[1, 8]` reject.
  * Non-object elements reject.
  * Missing `char_start` or `char_end` keys reject.
  * Non-number types (e.g. JSON null, strings, booleans) reject.
  * Fractional numbers (regex `\\.`) reject.
  * Negative offsets reject.
  * Non-positive spans (`char_end <= char_start`) reject.
* **Privilege revocation:** Migration explicitly runs `REVOKE ALL ON FUNCTION chunk_claims_evidence_spans_ok(jsonb) FROM PUBLIC`. Tested in [`test_evidence_spans_helper_is_null_safe_and_not_public`](../../src/tests/spine/test_d119_multi_span.py#L103-L142).
* **View ownership & role split:** [`memory_v1.claim_occurrences_live`](../../src/rememberstack/spine/migrations/versions/p9_31_0052_multi_span_claim_evidence.py#L136-L153) is owned by `rememberstack_view_owner`, and `SELECT` is granted to `rememberstack_query_<database>`.

### 5. Hydration Paths & Surface Invariants
* **Origin coordinate consistency:** In [`query_engine.py`](../../src/rememberstack/surfaces/query_engine.py#L3885-L4120), lateral joins across all hydration queries (`_CURRENT_FACT_EVIDENCE`, `_CONFIRM_CLAIMS_CURRENT`, `_CONFIRM_CLAIMS_CURRENT_SCOPED`, `_CONFIRM_CLAIMS_HISTORY`, `_HYDRATE_EVIDENCE_CLAIMS`) join `chunk_claims` on:
  ```sql
  WHERE cc.deployment_id = claim.deployment_id
    AND cc.claim_id = claim.claim_id
    AND cc.chunk_id = claim.chunk_id
  ORDER BY cc.created_at, cc.derivation_kind NULLS FIRST
  LIMIT 1
  ```
  This ensures [`EvidenceResult.chunk_id`](../../src/rememberstack/model/envelope.py#L295) and [`EvidenceResult.evidence_spans`](../../src/rememberstack/model/envelope.py#L304) identify the exact same origin chunk and representation.
* **Tested after offset shifts:** [`test_envelope_origin_spans_match_identified_chunk_after_reuse`](../../src/tests/workers/test_d119_versions.py#L471-L536) explicitly proves that after an offset-shifting version edit, API `EvidenceResult` retains original offsets for the origin chunk, while `memory_v1.claim_occurrences_live` delivers shifted offsets matching the new version.

### 6. Architectural Discipline (CLAUDE.md Rules 1–3)
* **No MVP framing or speculative machinery:** No fragment graphs, vector stores, extra checker models, second date windows, or citation caching layers were added.
* **Library boundary preserved:** All mechanisms are fully native to this repo; no cloud-only dependencies.
* **Documentation updated in same PR:** [Ingestion pipeline docs](../../website/src/app/docs/ingestion/pipeline/page.mdx#L110-L122), [retrieval envelope docs](../../website/src/app/docs/retrieval/envelope/page.mdx#L82-L95), [project status](../../website/src/app/docs/project-status/page.mdx#L13-L19), and [openapi.json](../../openapi.json#L937-L950) are kept truthful to current code.

---

## Validation & Test Execution Evidence

All checks executed in this review were clean and passing:

| Test Suite / Command | Scope | Result | Duration |
| :--- | :--- | :--- | :--- |
| `test_source_passages.py` + `test_occurrence_provenance.py` + `test_d119_reuse.py` | Unit / Passages / Provenance / Reuse | **27 passed** | 3.24s |
| `test_locomo_runner.py` | Benchmark / Protocol / Staged runner | **69 passed** | 8.61s |
| `test_query_space_manifest.py` + `test_openapi_export.py` + `test_chunker.py` + `test_claim_valid_time.py` + `test_claimify_loss_ledger.py` | Manifests / Schema / Packaging / Loss Ledger | **84 passed** | 14.61s |
| `test_e2_occurrence_provenance.py` | Handler provenance / OCR & Obs / Stale blocks | **5 passed** | 1.99s |
| `python3 .github/ci/check_test_inventory.py` | Test inventory drift gate | **OK (unit=107, int=59)** | 0.81s |
| `uv run lint-imports` | Layer boundary import contract | **5 kept, 0 broken** | 0.45s |
| `uv run ruff check src/ benchmarks/` | Style & linter | **All checks passed** | 0.35s |
| `uv run ruff format --check src/ benchmarks/` | Formatting check | **499 files formatted** | 0.18s |
| `uv run pyright src/ benchmarks/` | Strict static typecheck | **0 errors, 0 warnings** | 22.84s |
| **Sequential PostgreSQL Test Suite (Port 55440)** | E0–E3 500-versions lifecycle, migration refusal, check helper, queryspace manifest | **8 passed** | **609.85s (10m 09s)** |

### Sequential PostgreSQL Test Suite Breakdown (Log: `/tmp/ugm-d119-grok-20260914/parent-final-pg.log`):
1. [`test_envelope_origin_spans_match_identified_chunk_after_reuse`](../../src/tests/workers/test_d119_versions.py#L471): Passed.
2. [`test_500_versions_reuse_handler_remap_and_lineage_facts`](../../src/tests/workers/test_d119_versions.py#L538): Passed. Verified 500 synthetic document versions, zero unexpected model calls on notes, 1 unique claim, 500 occurrence rows with 2 remapped spans each, `LifecycleCatalog.recount` count = 1, and edited-context invalidation.
3. [`test_record_extraction_persists_complete_span_list`](../../src/tests/spine/test_d119_multi_span.py#L78): Passed.
4. [`test_evidence_spans_helper_is_null_safe_and_not_public`](../../src/tests/spine/test_d119_multi_span.py#L103): Passed.
5. [`test_chunk_claims_check_rejects_missing_endpoints`](../../src/tests/spine/test_d119_multi_span.py#L144): Passed.
6. [`test_upgrade_refuses_populated_pre_d119_claim_store`](../../src/tests/spine/test_d119_migration.py#L33): Passed.
7. [`test_live_introspection_equals_the_checked_in_manifest`](../../src/tests/spine/test_query_space_batch_a.py): Passed.
8. [`test_core_prose_is_authority_for_live_graph_and_claims_verbatim`](../../src/tests/surfaces/test_open_query_batch_f.py#L966): Passed.

---

## Findings & Observations

### Blockers / Material Findings
* **None.** All preliminary draft findings noted by parent supervision (SQL subquery in CHECK, view column order, unknown span mediation handling, cap wording, weak 500-version loop, manual migration refusal, previous/next hashing ambiguity, SQL NULL safety, and consumer coordinate mismatch) have been cleanly resolved and verified by regression tests.

### Non-blocking Nits / Informational Notes
1. **Header timestamp invalidation:** As documented in [D119 §5](../../plan/designs/multi_span_claim_extraction_design.md#L137-L142) and tested in [`test_header_timestamp_is_part_of_extraction_input_hash`](../../src/tests/workers/test_d119_reuse.py#L212-L229), changes to source header timestamps invalidate chunk extraction hashes. This preserves the existing conservative semantic boundary for relative world-time resolution; any finer timestamp invalidation policy is a separate future concern.
2. **Lane boundary separation:** PR #400 strictly limits its changes to coherent multi-span extraction and version reuse. Further prompt wording refinements belong to PR #402 (D120/D121), and cross-document reference context belongs to PR #403 (D122/D123). No leakage between branches was observed.

---

## Conclusion

PR #400 at commit `3cdf874640b7a1d25ac70178b1bcd0e134c60c8d` is complete, correct, and robust. It satisfies all binding criteria of D119 and CLAUDE.md. Once parent confirms final CI workflow gates pass, the branch is ready for merge.
