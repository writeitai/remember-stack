# WP-ET.7 implementation review — round 3

Reviewed `dc44041f55c60ff376767a2438399db5e6a30e1c`, the complete `origin/main...HEAD` diff, and the sole round-3 fix commit, `dc44041f5` (`git log 3419f295..HEAD`). Read both supplied round-2 reviews, their response, D140 §8.2, D118, the WP-ET.7 row, and the design-corpus skill. The checked-out branch has no `AGENTS.md` and its older `CLAUDE.md` stops at Rule 3; I also read `origin/main:AGENTS.md` → `origin/main:CLAUDE.md`, including Rule 4. The explicit requirement in this review request that period changes cause no re-extraction or re-embedding remains binding.

## Closure audit of both round-2 reviews

### Findings raised in round 2

| Finding | Status | Evidence at this HEAD |
| --- | --- | --- |
| **Sol P1-1:** ordinary apply certifies periods its answer did not consume | **Partially closed** | `src/rememberstack/spine/fact_adjudication.py:868` adds a watch and `:901` rechecks it after recording the basis. Both new ordinary-apply tests pass and fail behaviorally on `3419f295`. However, the watch starts after final snapshot validation and is itself assembled across separate SQL snapshots. Two additional interleavings still leave an incorrect window with a matching current basis and no repair. See **R3-P1-1**. |
| **Sol P1-2:** production entity-profile repair violates the no-re-embedding invariant | **Partially closed** | `src/rememberstack/spine/fact_window_review.py:743` stops indiscriminate vector invalidation. The new excluded-fact profile test proves that an unchanged rendered profile costs no call. A profile displaying the reviewed fact still gets re-embedded: `src/tests/workers/test_d140_period_reuse_pg.py:383` explicitly requires one call, and `:392` requires that extra call after draining. The response documents this as a new decision, but the requested owner invariant has no such exception. See **R3-P1-2**. |
| **Sol P1-3:** one transient provider failure strands the claim and exhausts retries | **Closed** | `src/rememberstack/spine/fact_window_review.py:213` records the stable job owner; `:233` releases its unanswered claim on failure; `:393` permits the owner to resume. `src/rememberstack/workers/fact_window_review.py:81` converts another owner's live claim into a deferred follow-up instead of spending retries. The worker/ledger regression at `src/tests/spine/test_d140_period_adjudication_pg.py:1199` passes here and dead-letters on the old engine. The deferral test also reaches the intended behavioral failure on the old engine after adding only its missing schema column. |
| **Sol P1-4:** an invalid cached citation permanently poisons retries | **Closed** | `src/rememberstack/spine/fact_window_review.py:232` validates citations before durable answer storage, and the exception path releases the claim. The `C999` → valid response regression at `src/tests/spine/test_d140_period_adjudication_pg.py:1252` passes here with two calls and fails on `3419f295`, which replays `C999`. Valid completed answers remain reusable after an apply failure. |
| **Sol inherited nit / Argon Nit-1:** migration module docstring has obsolete NULL meaning | **Closed** | `src/rememberstack/spine/migrations/versions/p9_39_0060_d140_period_window_review.py:8` now matches the column comments and design: NULL means the adjudicated evidence contained no periodised claim. |

Argon's round-2 review raised no other new P0/P1/P2 findings. Its broader closure claims were also checked, including the inherited findings listed below.

### Inherited findings carried through the round-2 audits

These are separate from new regression claims: a fix already present in `3419f295` should continue to pass there.

| Earlier finding, as audited in round 2 | Status now | Evidence |
| --- | --- | --- |
| **Sol R1 P1-1 / Argon R1 P0-1:** first periodised fact bypasses adjudication | **Closed** | `src/rememberstack/spine/fact_adjudication.py:738` bypasses the novelty shortcut for periodised claims; `:407` handles JEV's first fact. Both first-fact PostgreSQL tests pass. This does not close the concurrent ordinary-apply race above. |
| **Sol R1 P1-2:** review/revert race without serialization with fan-out | **Partially closed overall** | The shared apply/exclusive fan-out fence at `src/rememberstack/spine/period_adjudication.py:351` closes the original review/revert ordering. The ordinary-apply part remains open under **R3-P1-1**. |
| **Sol R1 P1-3 / Argon R1 P1-1:** identical-content ingest correction has no review trigger | **Closed** | `src/rememberstack/spine/document_catalog.py:266` queues the request in the declaration transaction; the ingest regression passes. |
| **Sol R1 P1-4 / Argon R1 P1-2:** repeated corrections collide on repair deduplication | **Closed** | `src/rememberstack/spine/fact_window_review.py:737` keys repair by a fresh adjudication receipt; the three-replacement test at `src/tests/spine/test_d140_period_adjudication_pg.py:972` passes. |
| **Sol R1 P1-5:** period-only replacement re-embeds unchanged content | **Partially closed** | Fact vectors survive. Production entity-profile embeddings still violate the broader requested guarantee; **R3-P1-2**. |
| **Sol R1 P1-6:** unbounded lineage fan-out and review preparation | **Partially closed overall** | `src/rememberstack/spine/period_adjudication.py:516` pages lineage facts and `src/rememberstack/spine/fact_window_review.py:544` limits review claim payloads in SQL. Those fixes remain. The new ordinary-apply watch reintroduces unbounded worker materialization through another path; **R3-P1-3**. |
| **Sol R1 P2-1:** completed answers are not durable/shared | **Closed for the reported behavior** | Attempts remain keyed by fact and exact digest; the saved-answer retry test at `src/tests/spine/test_d140_period_adjudication_pg.py:1094` passes, with no additional model call. The two failure/retry defects identified in round 2 are closed as above. |
| **Sol R1 P2-2 / Argon R1 P2-1:** deletion enqueue is missing or outside the transaction | **Closed for the reported paths** | Requests remain inside version/lineage tombstone transactions (`src/rememberstack/spine/lifecycle.py:612`, `:643`) and source-deletion finalisation (`src/rememberstack/workers/reconcile.py:485`); late-testimony retirement returns deterministic follow-up work at `:348`. Closing-edition and lineage-deletion regressions pass. |
| **Sol R1 P2-3:** the owner test's period-change half is vacuous | **Partially closed** | It selects a v1-only claim, first proves no review is needed, changes the period, requires one real replacement, checks the resulting window and preserved fact vector, and now runs the production profile refresher. It is no longer vacuous. Its changed assertion permits an embedding call instead of proving the requested zero-call invariant. |
| **Argon R1 P2-2:** hard forget retains bases and review attempts | **Closed** | `src/rememberstack/spine/forget.py:671` clears the basis and `:679` deletes touched attempts. The erasure regression passes with the owner-bearing attempt row. |
| **Argon R1 P2-3:** truncation can exclude periodised evidence | **Closed for the reported ordering defect** | `src/rememberstack/spine/fact_window_review.py:536` retains window-witness, periodised-evidence, then support ordering before SQL LIMIT. |
| **Sol/Argon inherited nits:** NULL wording, unnecessary document join, per-fact UPDATE loop | **Closed** | Migration docstring fixed as above; `src/rememberstack/spine/period_adjudication.py:506` filters `chunks.doc_id`; `:234` writes bases set-wise. |

### Counterchecking the new tests against `3419f295`

All six new adjudication test functions pass at HEAD. I ran them against an isolated archive of `3419f295`'s engine and migrations, using only a dummy `ReviewDeferred` class to allow collection:

| Test in `src/tests/spine/test_d140_period_adjudication_pg.py` | Old-engine result |
| --- | --- |
| `test_ordinary_apply_never_certifies_periods_its_decision_did_not_see` (`:1280`) | Fails: expected `ApplicationInputChanged` is not raised. |
| `test_ordinary_apply_never_certifies_evidence_periods_it_was_not_shown` (`:1336`) | Fails: expected `ApplicationInputChanged` is not raised. |
| `test_a_failed_inference_releases_its_claim_so_the_retry_succeeds` (`:1199`) | Fails behaviorally: two retries and a dead letter instead of one retry and successful recovery. |
| `test_an_answer_citing_an_unsupplied_claim_is_never_replayed` (`:1252`) | Fails behaviorally: the second attempt replays the invalid `C999` citation. |
| `test_a_review_whose_window_leaves_profiles_unchanged_embeds_nothing` (`:1410`) | Fails behaviorally: two embedding calls instead of the one initial profile build. |
| `test_another_jobs_live_claim_defers_instead_of_paying_or_failing` (`:1126`) | Initially fails on missing `claimed_by`, which alone is not behavioral proof. Repeated with only that column added: fails with the old `ApplicationInputChanged` instead of deferral. |

The revised `test_periodised_new_version_reextracts_only_the_edit_and_a_period_change_none` **passes on both HEAD and `3419f295`**, including its new production-profile assertions. It therefore does not establish closure of R2 P1-2: both engines make the permitted-by-test extra profile embedding call. The forget test's change only adds the owner required by the new schema; with that schema compatibility column it also passes on the old engine, as expected for unchanged erasure behavior.

## P0

None found.

## P1

### R3-P1-1 — The watch can still certify periods the validated decision never saw

**Location:** `src/rememberstack/spine/fact_adjudication.py:868`; `src/rememberstack/spine/period_adjudication.py:292`, `:325`.

**Problem:** The final snapshot is read at `fact_adjudication.py:827` and its hash checked at `:847`, but `watch_periods_on` captures a later view of the periods. Period writers do not participate in the fence. A correction committed between those reads becomes the watch's baseline, so the post-write comparison succeeds even though the answer consumed the earlier periods. A first fact has no existing candidate whose stale basis could save it.

Reproduced at HEAD using a real separate declaration transaction after the final application snapshot returned: the answer chose `[2015, open)`, the declaration was corrected to end in 2024, apply succeeded with an open window, the stored basis matched the corrected current basis, and fan-out found **zero** reviews.

There is also an internal watch race for unshown evidence: it computes candidate bases at `period_adjudication.py:292`, then reads evidence intervals at `:325`. A correction between these statements can leave `stale` empty while the watch records the corrected intervals. A second HEAD probe, with only the incoming undated claim supplied, corrected the dated evidence's end from 2024 to 2023 in that gap. The fact retained its 2024 end, acquired the 2023 basis, and its fan-out again queued **zero** reviews. The submitted tests inject the correction later, just before basis recording, after both vulnerable gaps.

**Concrete fix:** Bind certification to the exact final validated inputs. Capture candidate stale-state and the complete period watch coherently, anchored before final snapshot validation, and explicitly check supplied periods against that validated snapshot. Record only a basis justified by that state, then revalidate through the write. A database-side digest can cover unshown evidence without copying it into the worker. Add both interleavings above and require either rollback/retry or a preserved stale basis that guarantees review. Update §8.2's claim that ordinary apply certifies only consumed periods to describe the resulting protocol.

### R3-P1-2 — Period-only edits still trigger production profile embeddings

**Location:** `src/rememberstack/spine/fact_window_review.py:743`; `src/tests/workers/test_d140_period_reuse_pg.py:383`; `plan/designs/effective_time_and_section_references_design.md:1032`.

**Problem:** `invalidate_profiles=False` avoids needless embeddings when the reviewed fact is absent from the entity's profile. When it is displayed, its changed window changes the profile input and the existing production refresher calls `embed` (`src/rememberstack/spine/profile_refresher.py:103`). The revised owner regression proves this happens and now requires **one additional embedding call** after a pure period correction. A large lineage correction can repeat this for many endpoint entities despite unchanged source text.

The response's decision to allow changed profiles to re-embed does not satisfy this review's explicit hard requirement that period changes never trigger re-embedding. Refining D140 in place cannot silently relax that owner constraint. The fact/chunk/claim vector preservation improvements are real, but do not close this finding.

**Concrete fix:** Provide a period-only projection path that refreshes temporal presentation from the authoritative fact windows without regenerating reusable semantic embeddings. Keep vectors correctly attested to their actual semantic input; simply retaining a vector while changing its attested text is not a fix. Reconcile the design with the owner invariant, and make the full production-path regression require **zero** embedding calls, including when the changed fact appears in an entity profile.

### R3-P1-3 — The fix introduces unbounded all-evidence materialization in ordinary apply

**Location:** `src/rememberstack/spine/period_adjudication.py:310`, `:321`, `:325`, `:342`.

**Problem:** Every ordinary application in a deployment that has ever had periods fetches **all evidence claim IDs** for every candidate fact into a Python list/set/tuple. It then materializes every claim's interval payload in a dictionary and repeats that complete read for the final comparison. The 100-claim model bound and 500-fact fan-out pages do not bound this path. A popular candidate fact can have arbitrarily many supporting claims, even when the incoming claim is undated.

A HEAD probe supplying one claim to the watch and attaching 5,001 evidence claims to one candidate produced **5,001 retained IDs and 5,001 retained interval payloads**. This grows with complete evidence cardinality, unlike the existing streamed membership hashes and database-side basis computation. At the required corpus scale it can exhaust worker memory and repeatedly strand ordinary adjudication. It executes while entity/participant locks and the deployment's shared period fence are held, also delaying lineage barriers.

**Concrete fix:** Keep the complete comparison in SQL and return bounded per-fact/watch digests, retaining only the already-bounded supplied claim payloads in Python. Preserve detection of unshown-evidence changes; do not solve this by truncating evidence coverage. Add a high-evidence-cardinality regression that verifies worker preparation remains bounded. This is introduced by `dc44041f5` and regresses the previously closed boundedness requirement.

## P2

No new findings raised in this round.

## Nits

No remaining findings; the shared round-2 migration-docstring nit is closed.

## Design and verification assessment

The keep-or-replace window-review stage is a justified, narrow D140 refinement. It fills the missing path for already-applied evidence, preserves D118's single authoritative window, uses supplied claims and the confidence floor, appends adjudication history, and schedules recoverable projection work. The D140 entry, §8.2, D118 cross-reference and WP-ET.7 row describe it in place without inventing another decision number. The remaining defects above prevent calling the implementation race-free, bounded, or compliant with the requested embedding invariant.

The fence itself has no evident new lock-order cycle: writers acquire forget → identity → entity → shared period fence; fan-out takes only the exclusive period fence and performs reads, without acquiring entity or fact-row locks. Inference runs outside these transactions. Only the first fan-out page takes the exclusive barrier, and continuations use deterministic IDs and keyset cursors. This bounds returned work and follow-up transactions, not query latency; no million-document contention benchmark was performed. The new all-evidence watch worsens time spent holding the shared fence.

The added first-periodised-fact call is justified: even with no identity candidate, the adjudicator must decide whether the version's in-force dates date this proposition or merely the document reporting it. The prompt path adds one ordinary bounded attempt for that first fact; subsequent facts already used adjudication. Undated first facts retain the free novelty shortcut, and JEV's documented single-interval path adds no generative call. Canned answers establish input plumbing and grounded application, not real-model semantic accuracy.

The normal VAT, retroactive correction, clear/redeclare, mixed dated/undated support and closing-edition deletion acceptance tests pass. No legal-specific engine branch was found. No chunking, block-grid, Selection/reuse/Claimify hash input, header fact, text-origin, embedding-input policy or upstream toolchain-version change appears in the PR diff. The reuse test genuinely ingests two differently dated versions and measures incremental work; its period-correction half now exercises a real replacement and production repair, with the forbidden extra embedding exposed above.

Undated snapshots omit period fields, and undeclared lineages queue no review. AST comparison with `origin/main` confirmed the original `_FACT_PROMPT` and all pre-existing adjudication/normalizer version values are unchanged. The rendering/parity tests pass, including the unchanged first-fact shortcut and model-call count.

Migration `p9_39_0060` remains additive and linear. Its new required `claimed_by` column matches both runtime inserts and the schema design, and the not-null catalog pin is now 633. The table, constraints, head pins, reset registration, queue contract, worker export, Compose service and self-host handler wiring agree. A local downgrade/reupgrade smoke check preserved existing fact windows, retained the enum value as documented and restored the required owner column. Deletion triggers and hard-forget cleanup retain the fixes audited above.

Validation: **96 focused non-database tests passed**, and **23 PostgreSQL acceptance tests passed** across the adjudication, reuse and focused forget modules. The six new tests were counterchecked against the old engine as detailed above. Three additional probes reproduced the two stale-certification cases and the unbounded materialization. Database checks used an isolated local **PostgreSQL 17** instance with unrelated PostgreSQL 19 graph DDL and unavailable BM25 setup omitted, and default partitions substituted for pg_partman setup. Period projections, application SQL, advisory locks, model routing, profile refresher and review/forget code were unchanged. This is not a clean PostgreSQL 19 extension/catalog-suite run. No tracked files were modified, and nothing was committed or pushed.

Verdict: Request changes
