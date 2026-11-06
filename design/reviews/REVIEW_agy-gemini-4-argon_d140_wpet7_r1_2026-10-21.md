# Review: PR #506 — WP-ET.7: Period-Aware Fact Adjudication & Fact Window Review (`1c5cfc05`)

## Executive Summary & Architecture Assessment

1. **Correctness & D118 Authority**:
   - The core period-basis model (`period_basis` SHA-256 over `(claim_id, stance, in_force, also_undated)`), the `document_version_scope` projection lookup (`claim_in_force_on`), and keeping D118's single `(valid_from, valid_until, valid_precision, window_claim_ids)` as the sole fact-window authority are well-designed.
   - **However, there is a P0 bug in E3 (`FactAdjudicator.prepare` / `apply`)**: when an entity has no existing candidate facts (`not snapshot["facts"]`), `FactAdjudicator` takes the pre-D140 `_sole_new_fact` (`novelty_gate`) shortcut (`window=None`) without calling the model even when the claim carries `in_force` periods, and then `FactAdjudicator.apply` immediately stamps the non-null `period_basis` onto the new fact. As a result, the first fact on any entity in a periodised lineage is created with `valid_precision='unknown'` and its `period_basis` already matches `current[fact]`, so `review_fact_windows` also skips it. Single-edition periodised facts (and multi-edition facts where no second claim lands on the same entity, or where a later edition repeals a provision by omitting the section) are permanently left undated. In `test_d140_period_adjudication_pg.py`, this was masked because `_vat_rig` staged a second claim on the same entity (`Books`) whose canned response manually emitted an `updates` entry back-filling `F1`'s window.
2. **Design Ambiguity Resolution (`review_fact_windows` stage)**:
   - Adding `review_fact_windows` as an explicit D140 §8.2 / `decisions.md` D140 in-place refinement (without allocating a new decision number) is the right architectural resolution: `fact_applications` is per-claim and already applied when effective-period declarations change or a successor edition closes a predecessor's open `in_force` interval.
   - The two-step structure (lineage-level fan-out comparing stored `period_basis` against `fact_period_basis_on`, followed by per-fact lock-free model evaluation and entity-locked fingerprint re-check before mutating the window, logging to `*_adjudications`, and enqueueing `label_relation`) is sound, bounded, and race-safe, subject to the two P1 fixes below (`declare_at_ingest_on` missing trigger and `label_relation` `target_id` collision on `A → B → A` period oscillations).
3. **Hard Owner Invariant — Rule 4 (A new version costs only its edit, D56)**:
   - **Verified**: The PR touches no reuse-key or upstream extraction/embedding inputs (`BLOCKIZER_VERSION`, `CHUNKER_VERSION`, `E0_CONVERT_VERSION`, `E0_STRUCTURE_VERSION`, `SKELETON_PARSER_VERSION`, `REUSE_IDENTITY_VERSION`, `EXTRACTION_ELIGIBILITY_POLICY_VERSION`, `E2_EXTRACTOR_VERSION`, `REFERENCE_POLICY_VERSION`, `EMBEDDING_INPUT_POLICY_VERSION`, `E1_EMBED_VERSION`, `E3_NORMALIZER_VERSION`, `PROMPT_RENDERER_VERSION`, `RELATION_APPLICATION_VERSION`, `OBSERVATION_APPLICATION_VERSION`, `extraction_input_hash`, `reuse_identity_hash`, `claimify_input_hash`, `embedding_text_hash`).
   - `test_periodised_new_version_reextracts_only_the_edit_and_a_period_change_none` in `src/tests/workers/test_d140_period_reuse_pg.py` is a real, non-vacuous end-to-end pipeline test: it ingests a 15-section document and a 1-section edit with `modified_at` and declared periods, asserts `1 <= selection_v2 <= 3`, `reuse_ratio >= 0.7`, `embeds_v2 <= selection_v2 + 1`, and then performs a retroactive period change and verifies `0` pipeline stage runs, `0` embeddings, and `1` `PromptWindowReview` call.
4. **Undated Corpora Invariance (e.g. LoCoMo)**:
   - **Verified**: `deployment_ever_periodised_on` and `lineage_ever_periodised_on` short-circuit on `document_effective_time_events`. For undated lineages, `in_force` / `source_in_force` keys are omitted from claim payloads, `render_fact_prompt` returns `_FACT_PROMPT.format(...)` byte-for-byte identically without `_PERIOD_NOTE`, `period_basis` remains `NULL`, and `request_window_review_on` enqueues nothing.
5. **Migration `p9_39_0060`, Wiring, Concurrency, and Generality**:
   - Migration `p9_39_0060_d140_period_window_review.py` and all migration pin surfaces (`test_migrations.py`, `database_reset.py`, `.github/ci/migration-smoke.sh`, `.github/workflows/ci.yml`, `postgres_schema_design.md`) are consistent and idempotent.
   - Worker/compose/self-host/queue wiring (`PipelineStage.REVIEW_FACT_WINDOWS`, `FactWindowReviewHandler`, `compose.yaml`, `profiles/selfhost.py`) is complete.
   - Engine logic is domain-general with no legal-specific branching.

---

## P0 Findings

### P0-1: Sole new fact (`novelty_gate`) bypasses period adjudication AND stamps `period_basis`, permanently leaving single-fact entities in periodised lineages undated

- **Location**:
  - `src/rememberstack/spine/fact_adjudication.py:727-734` (`FactAdjudicator.prepare`)
  - `src/rememberstack/spine/fact_adjudication.py:835-846` (`FactAdjudicator.apply`)
  - `src/rememberstack/spine/fact_adjudication.py:404-405, 561-562` (`_adjudicate_jev`)
- **Problem**:
  1. In `FactAdjudicator.prepare` (`src/rememberstack/spine/fact_adjudication.py:730`), when an entity has no existing candidate facts (`not snapshot["facts"]`), `prepare` bypasses the adjudicator and constructs `_sole_new_fact(claims=snapshot["claims"])` (`method="novelty_gate"`), which hardcodes `window=None` on every `NewFact` regardless of whether `snapshot["claims"]` carries `in_force` intervals.
  2. When `apply_fact_decision` (`src/rememberstack/spine/apply_fact_decision.py:106-114`) applies that `NewFact`, an undated sentence in a periodised edition (e.g. `"Value added tax on books is 15 percent."` or `"The daily allowance is 45 EUR."`) has `claim_valid_precision = 'unknown'` and `decision.window = None`, so `uses_claim_window` is `False` and the fact is inserted with `valid_from=None, valid_until=None, valid_precision='unknown', window_claim_ids='{}'`.
  3. Immediately afterward, `FactAdjudicator.apply` (`src/rememberstack/spine/fact_adjudication.py:840-845`) calls `record_period_basis_on`, stamping the non-null `period_basis` hash of the claim's `in_force` intervals onto the newly inserted fact—recording that the fact's `unknown` window was already adjudicated against those intervals.
  4. When `ReconcileHandler` completes the version and queues `review_fact_windows`, `facts_needing_review_on` (`src/rememberstack/spine/period_adjudication.py:353`) compares `current[fact] != stored[fact]`, sees they are equal, and skips the fact.
  5. Consequently, the first fact on any entity in a periodised lineage (such as Edition 1 of the Travel Policy in D140 §11 step 1, any single-edition periodised document, or any provision repealed in Edition 2 by section omission where no second claim touches the entity) **never** receives its effective period window—neither during E3 nor during `review_fact_windows`.
  6. Relatedly, in `_adjudicate_jev` (`src/rememberstack/spine/fact_adjudication.py:404-405, 561-562`), `if not presentation.get("facts"): return _sole_new_fact(...)` and `elif match_choice == "NEW": window = None` never consult `_single_in_force_window(claim_row)`, so the Jev adjudicator path cannot date a `NEW` fact from `source_in_force` even when `window_action == "use_claim"`.
  7. In `src/tests/spine/test_d140_period_adjudication_pg.py:389-390` (`_vat_rig`), this bug was hidden because after `(old_fact,) = _drain(rig)` (which left `old_fact` with `valid_from=None, valid_precision='unknown'` and a non-null `period_basis`), the helper immediately staged a second claim on the *same* entity (`Books`) whose canned model output manually included `updates: [{"target": "F1", ...}]` to back-fill `old_fact`'s window.
- **Concrete Fix**:
  1. In `FactAdjudicator.prepare` (`src/rememberstack/spine/fact_adjudication.py:730`) and `FactAdjudicator.apply` (`src/rememberstack/spine/fact_adjudication.py:836`), only take the `_sole_new_fact` (`novelty_gate`) shortcut when no input claim carries `in_force`:
     ```python
     has_period_claims = any("in_force" in row for row in snapshot["claims"])
     decision = (
         None
         if snapshot["facts"] or has_period_claims
         else _sole_new_fact(claims=snapshot["claims"])
     )
     ```
     (and use the same `if snapshot["facts"] or any("in_force" in row for row in snapshot["claims"]):` condition in `FactAdjudicator.apply` at line 836).
  2. In `_adjudicate_jev` (`src/rememberstack/spine/fact_adjudication.py:404-405, 561-562`):
     - Do not early-return `_sole_new_fact` at line 404 when `any("source_in_force" in row for row in claims)`; instead, when `not candidate_rows`, set `match_choice = "NEW"` and still run the window step (`_render_window_prompt`) so Jev can choose `use_claim` / `custom` when periodised claims are present.
     - In the `elif match_choice == "NEW":` branch at line 561, when `claim_row.get("valid_precision", "unknown") == "unknown"`, set `window = _single_in_force_window(claim_row)` (or run `_render_window_prompt` when multiple intervals/custom windows apply) so Jev honors D140 §8.2 ("a claim with no dates of its own whose text was in force over exactly one interval is dated by that interval").
  3. In `src/tests/spine/test_d140_period_adjudication_pg.py`, assert in `_vat_rig` (or a dedicated test) that after ingesting and draining **only** the 2015 edition (`v_2015`), `old_fact` immediately has `valid_from == 2015-01-01T00:00:00Z` and `valid_precision == 'day'` before any 2024 edition or second claim is staged.

---

## P1 Findings

### P1-1: Declaring or correcting an effective period via `POST /ingest` (`declare_at_ingest_on`) never queues `review_fact_windows`

- **Location**:
  - `src/rememberstack/spine/effective_time.py:252-316` (`declare_at_ingest_on`)
  - `src/rememberstack/spine/document_catalog.py:252-284` (`ingest_document`)
- **Problem**:
  D140 §2.3 and §8.2 specify that when a caller re-ingests identical content (`created == False`, D55 no-op ingest with no new `version_key`) and supplies `effective_from` / `effective_until`, the period declaration or `effective_until` update is recorded on the existing version without reprocessing text and triggers fact window review. However, `declare_at_ingest_on` never calls `request_window_review_on(...)`. Because `enqueue_on` for `PipelineStage.CONVERT` on an already-processed version (`created == False`) hits the existing `succeeded` work row in `processing_state` and is a no-op, `ReconcileHandler` never runs again for that version. Thus, declaring or updating a period on an existing version via `POST /ingest` mutates `document_effective_periods` and `document_version_scope`, invalidates the facts' `period_basis`, and never queues `review_fact_windows`. (Likewise, if a newly created version with `effective_from` closes its predecessor's derived `effective_until` in `document_version_scope` at ingest commit time and is parked on `no_route` or fails before `reconcile`, the predecessor's facts are not queued for window review at declaration time.)
- **Concrete Fix**:
  At the end of `declare_at_ingest_on` in `src/rememberstack/spine/effective_time.py:316` (right after `_mark_declared_on`), call:
  ```python
  request_window_review_on(
      connection=connection,
      deployment_id=deployment_id,
      doc_id=doc_id,
      reason="ingest_period_declared",
  )
  ```
  Add a test in `src/tests/spine/test_d140_period_adjudication_pg.py` covering a period declared/updated via `declare_at_ingest_on` on an already-adjudicated version.

### P1-2: `_apply_review` keys the `label_relation` work `target_id` solely on `inputs['digest']`, causing `A → B → A` period changes to skip relabeling/re-embedding after clearing `fact_label` and `embedding` to `NULL`

- **Location**:
  - `src/rememberstack/spine/fact_window_review.py:450-463, 507, 520-532` (`FactWindowReviewer._apply_review`)
- **Problem**:
  When `FactWindowReviewer._apply_review` replaces a fact's window, it clears `fact_label = NULL, fact_label_version = NULL, embedding = NULL, ...` (or `obs_label = NULL, embedding = NULL, ...`) on the fact row and clears `profile_summary = NULL, embedding = NULL, ...` on the endpoint entities via `enqueue_fact_projection_repair`, which enqueues a `PipelineStage.LABEL_RELATION` job with:
  ```python
  target_id=uuid5(NAMESPACE_URL, f"fact-window-review:{inputs['digest']}")
  ```
  Because `processing_state` enforces `UNIQUE (deployment_id, target_kind, target_id, stage, component_version)` and `enqueue_on` is a no-op when that row is already `succeeded`:
  1. Suppose a fact starts at period basis `B1` with window `W1`.
  2. The operator updates the period so the basis becomes `B2`; Review 1 (`stored_basis=B1, basis=B2`) replaces the window `W1 → W2` and enqueues `label_relation` with `target_id = uuid5(..., inputs['digest'])`, which succeeds.
  3. The operator reverts the period back to `B1`; Review 2 (`stored_basis=B2, basis=B1`) restores `W2 → W1`.
  4. The operator applies the `B2` period again; Review 3 sees the exact same `(fact_id, stored_basis=B1, basis=B2, presentation)` as Review 1 and therefore computes the **exact same `inputs['digest']`**.
  5. Review 3 updates the window `W1 → W2`, sets the fact's label/embedding and its entities' `profile_summary`/embedding to `NULL`, and calls `enqueue_on` with the same `target_id` as Review 1. `enqueue_on` sees Review 1's `succeeded` row and does nothing (`created=False`). The fact and its entities are left permanently unlabeled and unembedded (`NULL`).
  (Note that `FactWindowReviewHandler._fan_out` at `src/rememberstack/workers/fact_window_review.py:86-89` explicitly mixed `work.processing_id` into `target_id` to prevent this exact `A → B → A` collision, whereas `_apply_review` omitted `call_key` / `adjudication_id`.)
- **Concrete Fix**:
  In `FactWindowReviewer._apply_review` (`src/rememberstack/spine/fact_window_review.py:523`), use the fresh `adjudication_id` generated at line 507 (or include `call_key` in the `uuid5` seed):
  ```python
  target_id=uuid5(
      NAMESPACE_URL,
      f"fact-window-review:{call_key}:{fact_id}:{inputs['digest']}",
  ),
  ```

---

## P2 Findings

### P2-1: Non-`DocumentDeleter` deletion and late-testimony retirement paths do not queue `review_fact_windows`

- **Location**:
  - `src/rememberstack/workers/reconcile.py:264-345` (`ReconcileHandler._retire_deleted_testimony`)
  - `src/rememberstack/workers/reconcile.py:452-483` (`CycleFinalizer.finalize_ready`)
  - `src/rememberstack/workers/reconcile.py:578-598` (`DeletionService.delete_lineage`)
- **Problem**:
  While `DocumentDeleter._delete_fenced` (`reconcile.py:736`) and `DeletionService.delete_version` (`reconcile.py:573`) call `request_window_review`, three other deletion/retirement paths do not:
  1. `ReconcileHandler._retire_deleted_testimony` (`reconcile.py:345`): if a version or lineage is deleted while a version is still running in the pipeline, its E3 `FactAdjudicator` work can land *after* `delete_version` / `delete_document`'s `review_fact_windows` job has already run, and `_retire_deleted_testimony` returns `HandlerOutcome()` without calling `self._window_review(...)`.
  2. `CycleFinalizer.finalize_ready` (`reconcile.py:472-483`): when a connector-observed source deletion is finalized via `cascade_lineage_removal`, `request_window_review_on` is never called for facts shared with other lineages.
  3. `DeletionService.delete_lineage` (`reconcile.py:578-598`): when called outside hard-forget (`refresh_profiles=True`), it does not call `self._catalog.request_window_review(deployment_id=deployment_id, doc_id=doc_id, reason="lineage_deleted")`.
- **Concrete Fix**:
  - In `ReconcileHandler._retire_deleted_testimony` (`src/rememberstack/workers/reconcile.py:345`), return `HandlerOutcome(follow_up=self._window_review(work=work, doc_id=doc_id, reason="deleted_testimony_retired"))`.
  - In `CycleFinalizer.finalize_ready` (`src/rememberstack/workers/reconcile.py:482`), call `catalog.request_window_review(deployment_id=deployment_id, doc_id=doc_id, reason="lineage_deleted")` inside the `with self._catalog.transaction() as catalog:` block.
  - In `DeletionService.delete_lineage` (`src/rememberstack/workers/reconcile.py:594-598`), when `refresh_profiles` is `True`, call `self._catalog.request_window_review(deployment_id=deployment_id, doc_id=doc_id, reason="lineage_deleted")`.

### P2-2: D74 hard-forget (`_scrub_mutable_fact_payloads`) does not clear `period_basis` on surviving facts that shared evidence with the forgotten lineage

- **Location**:
  - `src/rememberstack/spine/forget.py:661-675` (`_scrub_mutable_fact_payloads`)
- **Problem**:
  When a periodised lineage is hard-forgotten, surviving facts that also have support in another lineage (`affected_fact_ids`) have their `window_claim_ids` and window reset to `unknown` if witnessed by the forgotten lineage (`lines 661-667`) and their forgotten `*_evidence` rows deleted (`lines 1431-1441`), but `relations.period_basis` / `observations.period_basis` is left holding the SHA-256 digest derived from the forgotten lineage's `claim_id`s and `in_force` periods.
- **Concrete Fix**:
  In `_scrub_mutable_fact_payloads` (`src/rememberstack/spine/forget.py:669-673`), clear `period_basis = NULL` on `affected_fact_ids`:
  ```sql
  UPDATE {table} SET {label_clear}
    period_basis=NULL,
    embedding=NULL,embedding_model=NULL,embedding_input_policy_version=NULL,
    embedding_text_hash=NULL,updated_at=now()
    WHERE deployment_id=:deployment_id AND {kind}_id=ANY(:affected_fact_ids)
  ```

### P2-3: `_review_inputs` claim truncation (`_CLAIM_LIMIT = 40`) does not prioritize periodised claims over undated supporting claims

- **Location**:
  - `src/rememberstack/spine/fact_window_review.py:333-341` (`_review_inputs`)
- **Problem**:
  `_review_inputs` sorts evidence claims by `(str(row["claim_id"]) not in window_claims, row["stance"] != "supports", -_sort_ts(row["asserted_at"]), str(row["claim_id"]))` and slices `claims[:_CLAIM_LIMIT]` (`40`). If a fact has `window_claim_ids = '{}'` (or an undated window witness) and more than 40 supporting claims where newer undated claims outnumber older periodised claims, `claims[:40]` drops the periodised claims (`claim_id in intervals`), so the window-review prompt sees no `source_in_force` intervals at all.
- **Concrete Fix**:
  Prioritize periodised claims right after `window_claims` in `_review_inputs` (`src/rememberstack/spine/fact_window_review.py:333-340`):
  ```python
  claims.sort(
      key=lambda row: (
          str(row["claim_id"]) not in window_claims,
          str(row["claim_id"]) not in intervals,
          row["stance"] != "supports",
          -_sort_ts(row["asserted_at"]),
          str(row["claim_id"]),
      )
  )
  ```

---

## Nits

### Nit-1: Unnecessary `document_versions` join in `_LINEAGE_FACTS`

- **Location**: `src/rememberstack/spine/period_adjudication.py:310-324` (`_LINEAGE_FACTS`)
- **Problem**: `_LINEAGE_FACTS` joins `chunks ch` to `document_versions v` solely to filter `v.doc_id = :doc_id`, even though `chunks` already has a non-null `doc_id` column indexed by `idx_chunks_doc (deployment_id, doc_id)`.
- **Concrete Fix**: Filter directly on `ch.doc_id = :doc_id` and drop the `JOIN document_versions v` in `_LINEAGE_FACTS`.

### Nit-2: Per-fact `UPDATE` loop in `record_period_basis_on`

- **Location**: `src/rememberstack/spine/period_adjudication.py:215-225` (`record_period_basis_on`)
- **Problem**: `record_period_basis_on` executes one `UPDATE` statement per fact in a Python loop. While entity batches are typically small, a single set-based update (or `executemany`) avoids `N` round-trips when an entity has many facts.
- **Concrete Fix**: Pass the `(fact_id, basis)` pairs in a single `executemany` or `unnest` `UPDATE` statement.

Verdict: Request changes
