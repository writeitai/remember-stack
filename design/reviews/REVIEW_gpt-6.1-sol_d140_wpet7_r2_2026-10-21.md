# WP-ET.7 implementation review — round 2

Reviewed PR #506 at `3419f29520a27ebd5a3f49fd63762e44d82661c3`, including `origin/main...HEAD` and both fix commits, `d081b2b7a` and `3419f2952`. Read both supplied round-1 reviews, the author's response, D140 §8 and its in-place refinement, D118, and the WP-ET.7 row. This checkout and its ancestors have no `AGENTS.md`; `CLAUDE.md` contains Rules 1–3. The owner's explicit changed-parts-only/no-re-embedding requirement in this review request was treated as binding.

## Closure audit of both round-1 reviews

“Closed” below refers to the original finding. Test limitations are stated separately; compilation against a new API is not counted as behavioral proof.

| Round-1 finding | Status | Evidence at this HEAD and regression assessment |
| --- | --- | --- |
| **Sol P1-1 / Argon P0-1:** first periodised fact bypasses adjudication | **Closed** | `src/rememberstack/spine/fact_adjudication.py:738` retains the novelty shortcut only without periodised claims; `:403` and `:953` handle JEV's first/NEW fact. The two first-fact tests at `src/tests/spine/test_d140_period_adjudication_pg.py:849` and `:879` pass here. On `1c5cfc05` they fail respectively with **zero model calls** and `(NULL, NULL, unknown)` instead of the expected period. Neither relies on another assertion repairing the first fact. |
| **Sol P1-2:** final fingerprint check races with period writers | **Partially closed** | The shared apply/exclusive fan-out fence at `src/rememberstack/spine/period_adjudication.py:247` closes the original **review** B→A revert interleaving: an independent concurrent probe confirms the fan-out waits, then finds the committed stale basis and queues repair. The new fence test at `src/tests/spine/test_d140_period_adjudication_pg.py:1054` also fails on the old engine with a no-op import shim. However, ordinary apply recomputes and certifies an unconsumed basis after validation; see **P1-1** below. The submitted test only tests blocking, not that ordinary-apply interleaving. |
| **Sol P1-3 / Argon P1-1:** identical-content ingest period correction has no trigger | **Closed** | `src/rememberstack/spine/document_catalog.py:266` transactionally queues review when a declaration changes on an existing version. The test at `src/tests/spine/test_d140_period_adjudication_pg.py:926` fails on the old engine because there is no review job. Its unchanged-declaration case is a no-op. Delaying a newly created version's review until completion is now explicitly documented, including failed/no-route processing. |
| **Sol P1-4 / Argon P1-2:** repeated A→B→A→B correction loses repair work | **Closed** | `src/rememberstack/spine/fact_window_review.py:671` keys repair by a fresh adjudication receipt. The submitted test at `src/tests/spine/test_d140_period_adjudication_pg.py:955` fails on old fact-vector clearing before reaching its final count. An independent cycle probe, marking each previous repair succeeded, reaches **three distinct repairs here versus two on `1c5cfc05`**. |
| **Sol P1-5:** period-only replacement re-embeds unchanged content | **Partially closed** | Fact labels/vectors are preserved, and the submitted replacement tests genuinely exercise that fix. But `src/rememberstack/spine/fact_window_review.py:661` still invalidates endpoint entity vectors through projection repair. The production profile refresher then embeds them again. See **P1-2**. |
| **Sol P1-6:** fan-out and review preparation materialize unbounded inputs | **Closed** | `src/rememberstack/spine/period_adjudication.py:412` pages facts, computes bases in SQL, and `src/rememberstack/spine/fact_window_review.py:450` fetches at most 40 claim payloads. The submitted page-size-one test at `src/tests/spine/test_d140_period_adjudication_pg.py:1007` fails on the old constructor API, so is not by itself an old-code behavioral proof. An independent default-page probe covers **1,003 affected facts in 500/500/3**, without duplicates; old code returns all 1,003 in one handler result. |
| **Sol P2-1:** completed answers are neither durable nor shared | **Partially closed** | `src/rememberstack/spine/fact_window_review.py:212` claims and stores answers. The completed-answer test at `src/tests/spine/test_d140_period_adjudication_pg.py:1077` passes here and makes a second provider call on old code. The live-claim test at `:1109` depends on a new helper; an independent pre-existing-claim probe confirms suppression here and an unwanted call on old code. The new failure/retry semantics remain broken: **P1-3 and P1-4**. |
| **Sol P2-2 / Argon P2-1:** deletion enqueue is missing or outside the deletion transaction | **Closed for the reported paths** | Requests now accompany operator/public tombstones in `src/rememberstack/spine/lifecycle.py:609` and `:637`, source-deletion finalisation in `src/rememberstack/workers/reconcile.py:482`, and late-testimony retirement at `:345`. The new lineage-deletion test at `src/tests/spine/test_d140_period_adjudication_pg.py:1144` fails on old code with no additional job. The closing-edition test passes. The new tests do not independently inject crashes into every source/late-testimony path; those closures also rely on inspection of transaction/retry placement. |
| **Sol P2-3:** owner regression's period-change half is vacuous | **Partially closed** | `src/tests/workers/test_d140_period_reuse_pg.py:103` now selects a **v1-only** claim, records its basis, proves the initial fan-out empty, changes its interval, requires replacement, and checks the resulting window/vector. This fails behaviorally on old code's vector clearing. However, `:356` substitutes `RecordingProfileRefresher`, hiding actual profile embedding work. Thus it proves extraction reuse and fact-vector preservation, but not the complete no-embedding requirement; see **P1-2**. |
| **Argon P2-2:** hard forget retains period basis | **Closed** | `src/rememberstack/spine/forget.py:671` clears the basis and `:678` deletes affected review attempts. The extended erasure test passes here. On old code it initially fails because the new table is absent; after adding only that test-compatible table, it reaches the real assertion and fails with `basis-over-forgotten-claims` still present. |
| **Argon P2-3:** 40-claim truncation can exclude periodised evidence | **Closed** | SQL ordering at `src/rememberstack/spine/fact_window_review.py:462` prioritises periodised evidence after window witnesses. The response's existing small examples do not prove truncation behavior. An independent test with **41 newer undated supports**, no window witnesses, and older periodised support includes the periodised claim here and excludes it on old code. |
| **Sol nit:** incorrect meaning of NULL basis | **Partially closed** | Column comments and schema design are corrected. The migration's opening docstring still contains the old meaning; see **Nits**. |
| **Argon nit 1:** unnecessary document-version join | **Closed** | `src/rememberstack/spine/period_adjudication.py:402` filters `chunks.doc_id` directly. No behavioral regression test is needed for this query simplification. |
| **Argon nit 2:** per-fact UPDATE loop | **Closed** | `src/rememberstack/spine/period_adjudication.py:231` uses one `UPDATE ... FROM unnest(...)`. No per-fact database round trip remains in that writer. |

### Baseline test method

The new adjudication test module cannot collect unchanged against `1c5cfc05`, because it imports the new fence API. For behavioral comparison I used an isolated archive of the old sources, a **no-op fence import shim**, and the original round-1 VAT fixture so the first-fact bug would not mask unrelated tests. All nine newly added adjudication test functions fail there; seven reach behavioral failures, while pagination and the submitted live-claim test initially fail on missing APIs. The independent probes described above supply behavioral comparisons for those two cases and for correction-cycle repair. The revised owner test and extended forget test also fail on the old engine. No tracked checkout files were changed.

## P0

None found.

## P1

1. **Ordinary apply can stamp a new period basis onto a decision made against old periods.** `src/rememberstack/spine/fact_adjudication.py:877`, `src/rememberstack/spine/period_adjudication.py:226`.

   The new fence excludes fan-outs while apply commits; it does **not** exclude declaration writers. After the final snapshot validation, a separate period writer can commit a correction before `record_period_basis_on` reads the periods again. That function stamps the new basis onto the old answer. The waiting fan-out then sees matching bases and skips the fact permanently.

   Reproduced through real ordinary adjudication and a separate real declaration transaction: the model chose `[2015, open)`; the declaration was corrected to end in 2024 between validation and basis recording; the fact retained an open window, its stored basis equalled the corrected current basis, and fan-out returned **zero reviews**. This is the ordinary-apply portion of Sol R1 P1-2, still open.

   **Concrete fix:** bind the recorded basis to the inputs actually validated and consumed, including evidence changes made by the application. Alternatively, coordinate period/occurrence writers with the validation-to-commit section so those inputs cannot change there. Never certify a newly recomputed basis merely because a shared fan-out fence is held. Add this precise interleaving as a regression, alongside the existing review/revert test.

2. **The owner invariant still fails through production entity-profile repair; the regression replaces that path with a recorder.** `src/rememberstack/spine/fact_window_review.py:661`, `src/rememberstack/spine/apply_fact_decision.py:497`, `src/tests/workers/test_d140_period_reuse_pg.py:356`.

   Every accepted replacement calls the general repair helper, which clears the endpoint entities' embeddings and attestations. `EntityProfileRefresher` includes window annotations in its input and calls the embedding provider (`src/rememberstack/spine/profile_refresher.py:103`, `:705`). A probe first built an existing Books profile, changed only the period boundary, applied the review and ran production profile repair: **one additional entity embedding call** resulted. The fact vector itself correctly survived.

   The supplied owner regression runs real `LabelFactsHandler` with `RecordingProfileRefresher`; its zero-embedding assertion therefore excludes the remaining production embedding path. The in-place design text narrows the promise to fact vectors, whereas the review's hard requirement forbids period-triggered re-embedding.

   **Concrete fix:** provide a window-only projection repair that updates temporal presentation separately from reusable semantic embeddings, and reconcile that with the unchanged embedding-input policy. Do not retain a vector with a false attestation. Replace the recorder in the regression with the production refresher, prepopulate its profile/vector, drain the complete repair path, and require zero embedding calls. The present implementation does not satisfy that gate.

3. **A single transient provider failure leaves an unfinished claim that exhausts every normal retry.** `src/rememberstack/spine/fact_window_review.py:219`, `:360`.

   `_claim_attempt_on` commits a claim before inference. If generation fails, no code releases that owned claim. On redelivery the same job sees its own fresh unanswered claim as “another job” and raises `ApplicationInputChanged` for 900 seconds. The worker's default three attempts and 2/4-second retry delays exhaust in about six seconds, leaving the window review dead-lettered after one transient failure.

   Reproduced through the real `Worker`/`WorkLedger`, making scheduled retries immediately due: **retry_scheduled → retry_scheduled → dead_lettered, one provider call**, although a successful response was available for retry. The equivalent old-engine probe yields **retry_scheduled → succeeded**, with the provider called again. This is introduced by the fix commit.

   **Concrete fix:** track claim ownership and release the caller's unfinished claim on inference failure; allow the owning job to resume safely. A genuinely concurrent owner's live claim should defer work without burning the entire handler retry budget before the lease can expire. Add a worker-level transient-failure/recovery regression.

4. **An invalid cached model citation poisons all future retries of the same review.** `src/rememberstack/spine/fact_window_review.py:228`, `:569`.

   A schema-valid replacement citing an unsupplied handle such as `C999` is durably stored before handle validation. Apply rejects it, but that transaction rolls back without removing the stored answer. Every retry loads the same invalid response and fails again; it never asks for a corrected answer. The ordinary adjudicator already invalidates rejected saved answers, but the new review-attempt path does not.

   Reproduced with one invalid citation followed by an available valid response: the second review fails with the identical citation error and **zero additional model calls**. The equivalent old-engine probe calls again and successfully replaces the window. This is introduced by the fix commit.

   **Concrete fix:** validate supplied handles before persisting a completed usable answer, and durably invalidate a saved answer rejected during apply. Preserve paid valid answers across transient apply failures, while allowing a bounded fresh inference after invalid output. Test invalid-reference → valid-response recovery.

## P2

No new P2 findings raised in this round. Remaining gaps in the original P2 items are covered by the closure audit and the P1 findings above.

## Nits

- **Inherited NULL-description inconsistency.** `src/rememberstack/spine/migrations/versions/p9_39_0060_d140_period_window_review.py:8` still says NULL means the window has “never been adjudicated against any declared period.” A completed clear review also stores NULL. **Concrete fix:** use the corrected column-comment wording: the evidence last adjudicated contained no periodised claim.

## Design and verification assessment

The window-review stage is a justified, reasonably narrow **D140 refinement in place**, with no new decision number needed. It fills the missing already-applied-evidence path and updates D118's existing single window, with grounding, confidence checks and adjudication history; `period_basis` is a scheduling fingerprint rather than a second window authority. The current-state design, D140 entry and WP-ET.7 row describe the mechanism. Its claims about coordination and no re-embedding still need the fixes above.

The period fence's stated acquisition order introduces no evident lock cycle: apply takes forget/identity/entity locks before the shared fence, and fan-out takes its exclusive fence without those locks. Inference remains outside locks. The exclusive first-page transaction does pause all applies in that deployment while computing the page; pagination bounds handler output and follow-up transaction size, and the 1,003-fact probe confirms continuation coverage. This is not a measured million-document throughput guarantee. The ordinary-apply race prevents calling the protocol race-free.

The prompt engine's added first-fact call is justified: even without an identity comparison it must decide whether edition periods establish this proposition's world dates. It uses the existing bounded snapshot, applies once through the ordinary receipt path, and leaves never-periodised first facts on the free novelty path. JEV's single-period first-fact path is covered separately. Canned responses prove input plumbing and application behavior, not real-model semantic accuracy.

No chunking/block-grid, extraction/reuse/Claimify hash input, stable-header, text-origin, upstream embedding-policy or toolchain-version change was found. The revised reuse test genuinely measures incremental extraction and chunk embedding; its remaining profile gap is identified above. Undated claim snapshots omit period fields, prompts and adjudicator versions remain unchanged, first facts retain the novelty shortcut, and never-periodised lineages enqueue no review. The parity tests pass. No legal-specific engine branch was found.

Migration `p9_39_0060` adds the attempt table, five non-null columns, two checks, one FK and one PK consistently with the updated catalog counts and 83-table pin. CI head pins, reset registration, queue contracts, worker export, Compose and self-host wiring match the new stage. The isolated migration upgrade/downgrade/upgrade preserved fact rows and retained the enum value as documented. Forget clears bases and stored answers for touched facts.

Validation: **110 focused non-database tests passed**; **23 PostgreSQL acceptance tests passed** across the period, reuse and forget modules. PostgreSQL checks used an isolated local **PostgreSQL 17** database with unrelated PostgreSQL 19 property-graph DDL omitted, unavailable BM25 setup omitted, and monthly partition setup replaced with default partitions. The application SQL, period projections, advisory locks, model routing and review/forget code under review were unchanged. This is not a clean run of the full PostgreSQL 19 extension/catalog suite. Additional focused probes reproduced all four P1 findings; the two new retry defects also passed against the old engine, establishing that the fix introduced them.

Verdict: Request changes
