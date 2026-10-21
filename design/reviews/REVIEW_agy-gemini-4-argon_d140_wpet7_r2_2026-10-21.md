# Code Review (Round 2): PR #506 — WP-ET.7 (`origin/feat/d140-wpet7-period-aware-adjudication`)

- **Reviewed commit**: `3419f29520a27ebd5a3f49fd63762e44d82661c3` (Round-2 fix range: `1c5cfc05d2..3419f29520`, commits `d081b2b7a` and `3419f2952`)
- **Base commit**: `eb28bcfa8e03923eb082339df3b3e5b3f8dd3c76` (`origin/main`)
- **Reviewer**: agy (Round 2)

---

## 1. Round-1 Closure Audit (`REVIEW_wpet7_r1_sol.md` + `REVIEW_wpet7_r1_agy.md`)

All 12 distinct findings across both Round-1 reviews were audited against `1c5cfc05..HEAD` and verified empirically by running the PostgreSQL integration suite on `HEAD` (`3419f2952`, 28/28 passing) and re-running the new Round-2 regression tests against `1c5cfc05` engine code (all failing on `1c5cfc05` as detailed below).

| Round-1 ID(s) | Summary | Status | Evidence on `HEAD` (`3419f2952`) & Failure Verification on `1c5cfc05` |
| :--- | :--- | :--- | :--- |
| **Sol P1-1 / Agy P0-1** | First periodised fact on an entity bypassed window adjudication via `_sole_new_fact` (`novelty_gate`) and stamped `period_basis`, leaving the fact permanently `[None, None, unknown]` and invisible to `facts_needing_review_on`. | **Closed** | `FactAdjudicator.prepare` (`src/rememberstack/spine/fact_adjudication.py:736-740`) and `FactAdjudicator.apply` (`src/rememberstack/spine/fact_adjudication.py:871-873`) now check `if snapshot["facts"] or _periodised(snapshot["claims"]):` (`any("in_force" in claim for claim in claims)` at `fact_adjudication.py:421-423`). In `jev` mode, `_adjudicate_jev` (`fact_adjudication.py:574-580`, `952-1000`) routes both periodised first facts and `match_choice == "NEW"` through `_jev_new_fact` + `_single_in_force_window` (`fact_adjudication.py:405-418`), adopting the claim's single `source_in_force` interval when `source_world_precision` is `None`/`"unknown"` and leaving multi-interval unions unknown. Proven by `test_first_periodised_fact_is_adjudicated_with_its_periods` (`test_d140_period_adjudication_pg.py:840-873`, fails on `1c5cfc05` with `AssertionError: assert 0 == 1` on `len(rig.provider.generated_prompts)`) and `test_jev_first_periodised_fact_is_dated_by_its_single_period` (`test_d140_period_adjudication_pg.py:876-912`, fails on `1c5cfc05` with `AssertionError: assert (None, None, 'unknown') == (datetime(2015, 1, 1, ...), None, 'open')`). |
| **Sol P1-2** | Fingerprint check did not serialize with period writers (`A -> B -> A` revert while a worker prepared under `B` allowed stale `B` window to commit after `A` was restored and its fan-out had already run). | **Closed** | `share_period_fence_on` and `period_fence_barrier_on` (`src/rememberstack/spine/period_adjudication.py:247-276`) add a per-deployment advisory transaction lock (`hashtextextended('period-fence:' || deployment_id, 0)`). `FactAdjudicator.apply` (`fact_adjudication.py:824`) and `FactWindowReviewer.review` (`fact_window_review.py:244`) acquire `pg_advisory_xact_lock_shared` inside their apply transaction before re-reading `period_basis`, holding it until commit; `FactWindowReviewHandler._fan_out` (`src/rememberstack/workers/fact_window_review.py:101-104`) acquires `pg_advisory_xact_lock` (exclusive) on the first page before comparing `stored_basis IS DISTINCT FROM current_basis`. Proven by `test_review_apply_waits_for_the_period_fence` (`test_d140_period_adjudication_pg.py:1051-1079`, fails on `1c5cfc05` with `assert worker.is_alive()` being `False`). |
| **Sol P1-3 / Agy P1-1** | Periods declared at ingest via `EffectiveTimeCatalog.declare_at_ingest_on` never queued `review_fact_windows` when re-ingesting existing bytes (`created=False`). | **Closed** | `declare_at_ingest_on` (`src/rememberstack/spine/effective_time.py:252-318`) now returns `bool` (`True` only when periods changed vs prior declarations). `DocumentCatalog.record_upload` (`src/rememberstack/spine/document_catalog.py:257-277`) checks `if declared and not created:` and calls `request_window_review_on(..., reason="ingest_period_declared")` in the same transaction. Proven by `test_ingest_period_correction_on_an_existing_version_queues_review` (`test_d140_period_adjudication_pg.py:915-947`, fails on `1c5cfc05` with `ValueError: not enough values to unpack (expected 1, got 0)`). |
| **Sol P1-4 / Agy P1-2** | `enqueue_fact_projection_repair` used constant `target_id = fact_id` and `repair_name = "window-review"`, so `A -> B -> A -> B` correction cycles collided on `idx_processing_jobs_dedup` after the second replacement. | **Closed** | `_apply_review` (`src/rememberstack/spine/fact_window_review.py:671`) now passes `target_id=adjudication_id` (the fresh UUID of the `fact_adjudications` row written by that replacement) to `enqueue_fact_projection_repair`. Proven by `test_every_applied_review_repairs_its_profiles_and_never_reembeds_the_fact` (`test_d140_period_adjudication_pg.py:950-994`, which drives three consecutive window replacements `v1 -> v2 -> v3 -> v1` and asserts `repairs == 3`; fails on `1c5cfc05`). |
| **Sol P1-5** | `_apply_review` cleared `fact_label` / `obs_label` and `embedding*` on window replacement, forcing re-embedding of unchanged fact text on period-only edits. | **Closed** | `_apply_review` (`src/rememberstack/spine/fact_window_review.py:552-602`) now updates only `valid_from, valid_until, valid_precision, window_claim_ids, updated_at` and `period_basis`, preserving the existing fact label and embedding while still invalidating entity `profile_summary` / entity `embedding` and scheduling `label_relation` so `LabelFactsHandler` refreshes only the entity profiles (which render `[valid_from..valid_until)`). Proven by `test_every_applied_review_repairs_its_profiles_and_never_reembeds_the_fact` (`test_d140_period_adjudication_pg.py:950-994`) and `test_periodised_new_version_reextracts_only_the_edit_and_a_period_change_none` (`test_d140_period_reuse_pg.py:331-371`, fails on `1c5cfc05` at line 337 with `AssertionError: assert (None, None, None, None, None) == ('Topic 12 has fact 12', ...)`). |
| **Sol P1-6** | Unbounded lineage fan-out (`facts_needing_review_on` walked every fact in one transaction with per-fact Python round-trips) and unbounded claim fetch in `_review_inputs`. | **Closed** | `_FACT_BASIS` (`src/rememberstack/spine/period_adjudication.py:133-183`) computes `period_basis` set-wise in SQL (`encode(sha256(...), 'hex')`). `facts_needing_review_on` (`period_adjudication.py:412-473`) and `FactWindowReviewHandler._fan_out` (`src/rememberstack/workers/fact_window_review.py:83-147`) page by `(kind, fact_id)` with `REVIEW_PAGE_SIZE = 500` and enqueue a deterministic continuation job (`uuid5(NAMESPACE_URL, f"fact-window-review-next:{work.processing_id}")`) when a page is full. `_review_inputs` (`fact_window_review.py:441-474`) counts total claims via `SELECT count(*)` and fetches only `LIMIT 40` in SQL. Proven by `test_lineage_fan_out_pages_and_continues` (`test_d140_period_adjudication_pg.py:997-1048`, fails on `1c5cfc05` with `TypeError: FactWindowReviewHandler.__init__() got an unexpected keyword argument 'page_size'`). |
| **Sol P2-1** | Completed model answers in `FactWindowReviewer.review` were not durable or shared across duplicate reviews / post-inference retry. | **Closed** | Migration `p9_39_0060` (`src/rememberstack/spine/migrations/versions/p9_39_0060_d140_period_window_review.py:67-93`) creates `fact_window_review_attempts` keyed by `(deployment_id, fact_kind, fact_id, input_digest)`. `FactWindowReviewer` claims the digest under the entity lock before inference (`fact_window_review.py:212-222`, `314-367`), defers duplicate workers for up to `900s` via `ApplicationInputChanged`, persists the model answer in its own transaction before apply (`fact_window_review.py:223-235`, `370-395`), reuses stored answers without re-calling the model, and deletes attempt rows once applied (`fact_window_review.py:651-657`). Proven by `test_a_completed_answer_is_reused_and_a_claimed_review_is_not_repeated` and `test_a_live_claim_without_an_answer_blocks_a_duplicate_review` (`test_d140_period_adjudication_pg.py:1082-1143`, both fail on `1c5cfc05`). |
| **Sol P2-2 / Agy P2-1** | Deletion review enqueue happened outside the deletion transaction in `DeletionService`, and was missing in `DocumentDeleter`, `LifecycleCatalog.delete_lineage`, and `ReconcileHandler._retire_deleted_testimony`. | **Closed** | `LifecycleCatalog.delete_version` (`src/rememberstack/spine/lifecycle.py:614-619`) and `LifecycleCatalog.delete_lineage` (`lifecycle.py:643-648`) now call `_request_review_on` inside the tombstone transaction (covering `DeletionService` and `DocumentDeleter._delete_fenced`, while skipping hard-forget via `active_forget_id_on`). `CycleFinalizer.finalize_ready` (`src/rememberstack/workers/reconcile.py:485-494`) enqueues inside the episode transaction, and `ReconcileHandler._retire_deleted_testimony` (`reconcile.py:347-357`) returns `follow_up=_window_review(...)`. Proven by `test_deleting_a_lineage_queues_review_in_its_tombstone_transaction` (`test_d140_period_adjudication_pg.py:1146-1153`, fails on `1c5cfc05` with `AssertionError: assert 3 == (3 + 1)`). |
| **Sol P2-3** | Owner regression test `test_periodised_new_version_reextracts_only_the_edit_and_a_period_change_none` picked `LIMIT 1` claim shared by `v1` and `v2`, making the period-correction half a vacuous no-op (`replace_calls == 0`). | **Closed** | `src/tests/workers/test_d140_period_reuse_pg.py:261-371` now selects the edited `v1`-only claim (`NOT EXISTS (... ch.version_id <> :v1)`), verifies `facts_needing_review_on` is empty before the period correction, moves `v2`'s start from `2026-01-01` to `2025-07-01`, runs `FactWindowReviewHandler` (`decision="replace"`), verifies the fact window changes to `[2024-01-01, 2025-07-01)` with `review_calls == 1`, verifies `obs_label` and `embedding` on the fact remain intact, and runs the real `LabelFactsHandler` proving `0` embedding calls occur during profile refresh. Fails on `1c5cfc05`. |
| **Agy P2-2** | Hard-forget (`_scrub_mutable_fact_payloads`) did not clear `period_basis` on surviving shared facts or remove `fact_window_review_attempts`. | **Closed** | `_scrub_mutable_fact_payloads` (`src/rememberstack/spine/forget.py:669-683`) now sets `period_basis = NULL` on surviving shared `relations` and `observations` and deletes their `fact_window_review_attempts` rows. Proven by `test_mutable_fact_payloads_and_date_witnesses_are_erased` (`src/tests/spine/test_forget_catalog.py:1641-1709`, fails on `1c5cfc05`). |
| **Agy P2-3** | `_review_inputs` truncated to 40 claims without prioritizing periodised claims. | **Closed** | `_review_inputs` (`src/rememberstack/spine/fact_window_review.py:462-470`) now orders by `(c.claim_id = ANY(:window_claims)) DESC, EXISTS (... WHERE s.periodised) DESC, (e.stance = 'supports') DESC, c.asserted_at DESC NULLS LAST, c.claim_id LIMIT :limit`. |
| **Sol Nit-1 / Agy Nit-1 / Agy Nit-2** | Misleading `NULL` `period_basis` column comment; unnecessary `JOIN documents` in `_LINEAGE_FACTS`; per-fact loop in `record_period_basis_on`. | **Closed** | Column comments in `p9_39_0060_d140_period_window_review.py:48-63` and `plan/designs/postgres_schema_design.md:1487-1492`, `1556-1561` updated; `_LINEAGE_FACTS` (`period_adjudication.py:398-403`) filters `ch.doc_id = :doc_id` directly; `record_period_basis_on` (`period_adjudication.py:231-244`) uses a single set-based `UPDATE ... FROM unnest(...)`. |

---

## 2. Fresh Round-2 Review of `1c5cfc05..HEAD`

### 2.1 Per-Deployment Period Fence (`share_period_fence_on` / `period_fence_barrier_on`)
- **Race closure**: Every fact writer that stamps `period_basis` (`FactAdjudicator.apply` at `fact_adjudication.py:824` and `FactWindowReviewer.review` at `fact_window_review.py:244`) takes `pg_advisory_xact_lock_shared(hashtextextended('period-fence:' || deployment_id, 0))` *before* its final input fingerprint / `period_basis` check and holds that shared xact lock until its transaction commits. `FactWindowReviewHandler._fan_out` (`workers/fact_window_review.py:101-104`) takes `pg_advisory_xact_lock(hashtextextended('period-fence:' || deployment_id, 0))` (exclusive) on the first page (`after is None`) before reading `stored_basis IS DISTINCT FROM current_basis`. Consequently:
  - Any writer that validated its input fingerprint before a period mutation committed must finish and commit *before* the post-mutation lineage fan-out can pass `period_fence_barrier_on`, so the fan-out is guaranteed to see the writer's stale `period_basis` and enqueue a fresh per-fact review.
  - Any writer that acquires `share_period_fence_on` *after* the exclusive barrier sees the committed post-mutation period state during its under-lock re-read and aborts with `ApplicationInputChanged`.
- **Deadlock freedom & lock ordering**:
  - Fact writers (`FactAdjudicator.apply`, `FactWindowReviewer.review` apply transaction) acquire locks in strict order:
    1. `hard-forget:{deployment_id}` (shared advisory xact lock, in `application_block`)
    2. `{deployment_id}:identity-epoch` (shared advisory xact lock, in `application_block`)
    3. `{deployment_id}:obs:{root}` (exclusive advisory xact lock, in `application_block`)
    4. `period-fence:{deployment_id}` (shared advisory xact lock, via `share_period_fence_on`)
    5. Row locks (`claims`, `relations`/`observations`, `fact_applications`).
  - Lineage fan-out (`FactWindowReviewHandler._fan_out`) acquires *only* `period-fence:{deployment_id}` (exclusive advisory xact lock) in a short read-only transaction (`period_fence_barrier_on` + `facts_needing_review_on`) and acquires no entity lock, forget lock, or row lock while holding it.
  - Because `period-fence:{deployment_id}` is a leaf lock in the writer hierarchy and the sole lock in the fan-out transaction, there is no cycle in the wait-for graph and deadlocks are impossible.
- **Contention at scale**:
  - Model inference never executes under `period-fence` or `application_block`.
  - Normal adjudication and per-fact review applies take `period-fence` in *shared* mode only during the in-DB apply transaction (milliseconds) and never block each other.
  - Exclusive acquisition occurs only once on the first page (`after is None`) of a lineage fan-out job and is released as soon as the first 500-row page query returns.

### 2.2 Paged Lineage Fan-Out & Bounded Preparation
- `facts_needing_review_on` (`period_adjudication.py:412-473`) computes `period_basis` set-wise in SQL via `_FACT_BASIS` (`sha256` over `string_agg` of per-claim intervals) and pages through `relation` then `observation` facts in keyset order (`after = (kind, fact_id)`) with `REVIEW_PAGE_SIZE = 500`.
- When a page is full (`len(facts) == page_size`), `FactWindowReviewHandler._fan_out` (`workers/fact_window_review.py:130-143`) enqueues a continuation job with deterministic `request_id = uuid5(NAMESPACE_URL, f"fact-window-review-next:{work.processing_id}")` carrying `after: [last_kind, str(last_id)]`. Retries of a page are idempotent on both the per-fact jobs and the continuation job.

### 2.3 Durable Review Attempts (`fact_window_review_attempts`)
- `_claim_attempt_on` (`fact_window_review.py:314-367`) runs inside the prepare `application_block` before calling the model:
  - If an `answer` is already stored for `(deployment_id, fact_kind, fact_id, input_digest)`, it returns the parsed `PromptWindowReview` immediately (`reused_answer = True`) without a model call.
  - If an unanswered claim exists with `claimed_at >= now() - interval '900 seconds'`, it raises `ApplicationInputChanged` so the duplicate worker retries later instead of running duplicate inference.
  - Otherwise it inserts/refreshes `claimed_at = now()`.
- After lock-free inference, `_store_answer_on` (`fact_window_review.py:225-235`, `370-395`) commits the answer in its own transaction before entering the apply block. If the apply block then loses a race (`ApplicationInputChanged`) and the input digest is later restored or retried, the stored answer is reused. Once `_apply_review` commits a `kept` or `replaced` outcome, it deletes all attempt rows for `(deployment_id, fact_kind, fact_id)` (`fact_window_review.py:651-657`).

### 2.4 First-Periodised-Fact Adjudication Path
- In `prompt` mode, bypassing `_sole_new_fact` when `_periodised(snapshot["claims"])` is `True` (`fact_adjudication.py:736`, `871`) adds **at most one model call for the first fact of an entity** when that fact's claim comes from a periodised lineage (subsequent claims on that entity already called the adjudicator because `snapshot["facts"]` is non-empty).
- In `jev` mode (`fact_adjudication.py:574-580`, `952-1000`), `_jev_new_fact` is deterministic when `snapshot["facts"]` is empty (`novelty_gate` with `_single_in_force_window`), adding **zero** extra LLM calls while still dating single-period facts and leaving multi-interval unions `unknown`.
- For undated corpora (LoCoMo, etc.), `_periodised(snapshot["claims"])` is `False`, so `_sole_new_fact` (`novelty_gate`), prompts, and adjudicator versions remain byte-for-byte identical.

---

## 3. Summary Against All Review Criteria

1. **Correctness & D118 authority**: Initial periodised facts, multi-version amendments, retroactive corrections, clear/redeclare, mixed dated+undated support, and closing-edition deletions all produce grounded half-open windows (`[valid_from, valid_until)`) through D118 (`fact_adjudications` + `relations`/`observations`).
2. **Design ambiguity resolution (`review_fact_windows`)**: Documented in-place in `decisions.md` (D140) and `plan/designs/effective_time_and_section_references_design.md` (§8.2) with no new decision number. Sound, race-free, idempotent, and bounded at scale.
3. **Hard Owner Invariant (CLAUDE.md Rule 4)**: Zero reuse inputs touched (`git diff origin/main...HEAD` leaves chunking, block grid, `extraction_input_hash`, `reuse_identity_hash`, `claimify_input_hash`, header facts, text origin, embedding input policy, and extraction toolchain versions untouched). Period corrections do not re-extract or re-embed chunks or facts, proven end-to-end by `test_periodised_new_version_reextracts_only_the_edit_and_a_period_change_none`.
4. **Undated corpora**: Byte-for-byte unchanged (`test_undated_adjudication_renders_and_records_exactly_as_before`).
5. **Migration `p9_39_0060`, pins, wiring, deletion & forget**: Linear revision chain verified (`p9_38_0059 -> p9_39_0060`), catalog contract and migration tests pass, worker/compose/self-host wiring complete, deletion and hard-forget paths covered.
6. **Generality**: No legal-specific logic in the engine.

---

## P0 (Blocking — Must Fix)

None.

---

## P1 (High — Must Fix)

None.

---

## P2 (Medium — Non-Blocking)

None.

---

## Nits

### Nit-1: Module docstring of `p9_39_0060` still has the Round-1 `NULL` `period_basis` phrasing
- **Location**: `src/rememberstack/spine/migrations/versions/p9_39_0060_d140_period_window_review.py:7-9`
- **Problem**: The SQL `COMMENT ON COLUMN` strings at lines 48–63 and `plan/designs/postgres_schema_design.md` were updated to reflect that `NULL` means either no active evidence is in a periodised lineage or the fact has not yet been adjudicated/reviewed under WP-ET.7, but the migration file's top module docstring (lines 7–9) still says `"NULL means the window has never been adjudicated against any declared period"`.
- **Concrete fix**: Update lines 7–9 of `p9_39_0060_d140_period_window_review.py` to match the updated column comment.

Verdict: Approve
