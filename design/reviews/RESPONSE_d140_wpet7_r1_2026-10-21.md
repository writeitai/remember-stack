# Response to WP-ET.7 dual review round 1 (PR #506, reviewed head `1c5cfc05`)

Reviews: `REVIEW_gpt-6.1-sol_d140_wpet7_r1_2026-10-21.md` (Sol) and
`REVIEW_agy-gemini-4-argon_d140_wpet7_r1_2026-10-21.md` (Argon). Every P0, P1 and P2 is
addressed. Each fix has a test. Unless noted otherwise, the test fails on `1c5cfc05`.

Run on PostgreSQL 19: the new proofs and the extended forget proof run against `1c5cfc05`'s
engine sources.
- **The two proofs runnable there:** `test_d140_period_reuse_pg.py` and the extended forget
  proof. Both **fail** on `1c5cfc05`. The reuse test fails at the assertion that the fact's
  label and vector survive the review, because the old code cleared them. The forget proof
  fails because the attempts table does not exist.
- **The adjudication proofs:** they import the new fence and review-attempt APIs, so they
  cannot collect on `1c5cfc05`.

| Finding | Fix | Test |
| --- | --- | --- |
| Sol P1-1 / Argon P0-1: the first periodised fact bypasses window adjudication but stamps its basis | `prepare`/`apply` keep the novelty shortcut only when no snapshot claim is periodised. Otherwise the attempt is adjudicated (method `small_model`), and the basis is recorded after that decision. The `jev` engine dates the first fact, and every NEW fact, from a single in-force interval. Design §8.2 has a new "first fact is adjudicated too" paragraph. | `test_first_periodised_fact_is_adjudicated_with_its_periods`, `test_jev_first_periodised_fact_is_dated_by_its_single_period`; `_vat_rig` no longer relies on a second claim to date the first fact |
| Sol P1-2: the fingerprint check does not serialise with period writers | A per-deployment **period fence**. Every window or basis writer (ordinary apply, review apply) holds it shared from final validation through commit. A lineage fan-out passes it exclusively, holding no other lock, before reading stored bases. Lock order is documented. In the reviewer's revert interleaving, the fan-out now waits for the review to commit, sees the stored B, and reviews again. | `test_review_apply_waits_for_the_period_fence` (a held barrier parks the review before validation; it commits only after release) |
| Sol P1-3 / Argon P1-1: periods declared at ingest never enqueue review | `declare_at_ingest_on` returns whether it wrote a declaration. A declaration or correction on an existing (D55 no-op) version queues the review in the ingest transaction, with no pipeline work. A new version is reviewed on completion (reconcile), because its reused claims have no occurrences until it is processed; reviewing at ingest would close and reopen continuing facts. | `test_ingest_period_correction_on_an_existing_version_queues_review` |
| Sol P1-4 / Argon P1-2: A → B → A → B strands the projection repair | The repair job is keyed by the review's adjudication receipt (a fresh id per applied review), not by the input digest. | `test_every_applied_review_repairs_its_profiles_and_never_reembeds_the_fact` (3 replacements, 3 distinct repair jobs) |
| Sol P1-5 (owner invariant): a period-only correction re-embeds unchanged fact text | A review changes only the window. It no longer clears or recomputes the fact's label or vector; the vector attests only the label text (`embedding_text_hash(label)`), and labels are built from the statement. The profile repair job still runs, because entity profiles render fact windows, and the label stage finds nothing to relabel or embed for the fact. | the same test (label and vector unchanged across 3 replacements), and the owner regression running the real `LabelFactsHandler` on the repair (zero embedding calls) |
| Sol P1-6: fan-out and preparation are unbounded | The period basis is computed set-wise in SQL. The fan-out walks pages of 500 facts, each page one bounded transaction, and a full page queues a continuation job with its cursor. Review inputs fetch only the 40 claims shown, selected and ordered in the database, plus a count. | `test_lineage_fan_out_pages_and_continues` (page size 1: two pages cover both facts) |
| Sol P2-1: completed answers are not durable or shared | New `fact_window_review_attempts` (one row per fact and input fingerprint). A review claims the row before inference and stores the answer before applying it. A retry reuses the stored answer, a fresh claim without an answer makes a duplicate job retry later, and an applied answer's row is deleted. | `test_a_completed_answer_is_reused_and_a_claimed_review_is_not_repeated`, `test_a_live_claim_without_an_answer_blocks_a_duplicate_review` |
| Sol P2-2 / Argon P2-1: deletion enqueue is not crash-safe; paths are missing | The request is queued in the transaction of every deletion write: `LifecycleCatalog.delete_version`, `delete_lineage` (public delete, operator delete, and the forget path, which is skipped while a forget is active), source-deletion finalisation (one request per episode), and late-testimony retirement (follow-up). | `test_deleting_a_lineage_queues_review_in_its_tombstone_transaction`, `test_deleting_the_closing_edition_reviews_the_fact_it_ended` |
| Sol P2-3: the owner regression is vacuous about the period change | The fact now rests on a claim only version 1 carries. Its basis is recorded and the pre-change fan-out is proven empty. Moving version 2's start moves that claim's period end. The review must replace the window, and the test asserts the new window and that the fact's label and vector are unchanged. It runs the real label/profile repair (zero embedding calls) and asserts no chunk, claim, chunk-claim, extraction-decision or search rows change and no extraction call is made. | `test_periodised_new_version_reextracts_only_the_edit_and_a_period_change_none` |
| Argon P2-2: hard forget leaves `period_basis` | The forget scrub sets `period_basis = NULL` on the touched facts and deletes their review attempts. | `test_mutable_fact_payloads_and_date_witnesses_are_erased` (extended) |
| Argon P2-3: truncation does not prioritise periodised claims | The review ordering is window claims, then claims from periodised lineages, then supports, newest first, done in SQL. | covered by the review proofs (periodised claims are always presented) |
| Sol nit: NULL meaning | The column comments and schema design now say that NULL means the adjudicated evidence had no periodised claim. | — |
| Argon nit 1: needless join | The fan-out filters `chunks.doc_id` directly. | — |
| Argon nit 2: per-fact update loop | `record_period_basis_on` is one set-based `UPDATE … FROM unnest(…)`. | — |

Other changes made with these fixes:
- **Migration `p9_39_0060`:** adds `fact_window_review_attempts`. The catalog contract table list and constraint counts, and the migration table count, are updated.
- **`test_d140_migration.py`:** now upgrades to head before verifying the head contract.
- **Corpus:** design §8.2, `postgres_schema_design.md` and D140's refinement note are updated in place.
