# WP-ET.7 implementation review — round 4

Reviewed `3b676c171aff6e44b2492b27fe6792fd34b0dfc3`, `origin/main...HEAD`, and the sole fix commit in `dc44041f..HEAD`, `3b676c171` (one-statement period watch). HEAD matches `origin/feat/d140-wpet7-period-aware-adjudication`.

Read the working agreement first: this worktree has no `AGENTS.md` and its `CLAUDE.md` stops at Rule 3, so I also read `origin/main:AGENTS.md` → `origin/main:CLAUDE.md`, including all of Rule 4. Read the design-corpus skill, D140 §8 and its in-place refinement, decisions.md D140, D118, the WP-ET.7 row, the supplied round-3 review, and the response. The profile-embedding owner decision is explicitly excluded from findings in this round.

## Closure audit

### Round-3 findings

| Finding | Status | Evidence at HEAD |
| --- | --- | --- |
| **R3-P1-1: ordinary apply certifies periods its decision never saw** | **Closed for the reported ordinary-apply races** | `src/rememberstack/spine/fact_adjudication.py:833` captures the watch before final validation at `:844`; `:864` additionally compares the validated shown periods with the watch. `src/rememberstack/spine/period_adjudication.py:255` reads stale candidates, the complete period digest and shown periods in one SQL statement. Ordinary apply retains stale bases and rechecks after recording (`fact_adjudication.py:901`, `:912`). Both new race regressions pass at HEAD and fail behaviorally on `dc44041f`, as detailed below. A separately discovered race in the **review** path is R4-P1-1 below; it does not invalidate this ordinary-apply fix. |
| **R3-P1-2: a period-only correction re-embeds a displayed entity profile** | **Not closed — owner decision pending** | Behavior is deliberately unchanged: `src/rememberstack/spine/fact_window_review.py:743` retains existing vectors before production profile repair, and `src/tests/workers/test_d140_period_reuse_pg.py:383` expects one embedding when the rendered profile changes. The owner is choosing between (a) embedding the changed rendered profile and (b) separate dated presentation with date-free embedded text. Per this round's instructions, this is an audit note, not a finding or a reason for the verdict. |
| **R3-P1-3: ordinary apply materializes every candidate's evidence in the worker** | **Closed** | The SQL watch returns a digest, bounded candidate IDs and only shown claims' periods (`period_adjudication.py:305`, `:332`, `:364`). The post-write check also remains in SQL (`:440`). The 5,001-unshown-claim regression passes its 256 KiB worker-memory bound and verifies that a subsequent change to unshown evidence is still detected. On `dc44041f` it fails the memory assertion at **4,684,390 bytes**. |

### Counterchecking every test added by the round-4 fix

Ran HEAD's three new tests against both HEAD and an isolated `git archive dc44041f` engine and migration tree. No compatibility shim or schema alteration was needed. Both engines used the same PostgreSQL 17 environment and the migration substitutions described under validation.

| Test in `src/tests/spine/test_d140_period_adjudication_pg.py` | HEAD | `dc44041f` |
| --- | --- | --- |
| `test_a_correction_after_final_validation_is_never_certified` (`:1414`) | Pass: retry chooses the corrected 2024 end before certifying its basis. | **Behavioral failure** at `:1464`: the fact remains open instead of ending in 2024. |
| `test_a_correction_inside_the_watch_is_never_certified` (`:1468`) | Pass: unshown evidence's correction leaves a stale basis and a review is queued. | **Behavioral failure** at `:1522`: stored and corrected bases match, certifying the unreviewed change. |
| `test_the_period_watch_stays_bounded_over_5001_evidence_claims` (`:1526`) | Pass: bounded worker preparation and detection of the unshown period change. | **Behavioral failure** at `:1598`: approximately 4.7 MB of traced worker memory. |

These are substantive regressions, not collection/schema failures. The second test's injection now reaches basis recording because the watch no longer calls the old multi-statement helper; the post-record digest check catches that later interleaving too. The stale test on the old engine still reproduces the originally reported gap.

### Findings carried through the round-3 audit

The earlier fixes below remain present; their relevant acceptance tests pass. They were not introduced by this round, so they need not fail on `dc44041f`.

| Earlier finding | Status and evidence |
| --- | --- |
| **R2 P1-1 / R1 ordinary-apply portion of the serialization finding** | **Closed**, through R3-P1-1's watch/validation/recheck protocol above. Both older ordinary-apply regressions (`test_d140_period_adjudication_pg.py:1285`, `:1341`) also pass. |
| **R2 P1-2 / R1 P1-5: period-only embeddings** | **Partially closed; owner decision pending.** Fact vectors are preserved. The displayed-profile question is the single owner note above. |
| **R2 P1-3: transient provider failure strands a claimed review** | **Closed.** Stable job ownership, release of failed inference claims and deferral of another owner's live claim remain at `fact_window_review.py:213`, `:233`, `:393` and `workers/fact_window_review.py:83`. Real worker/ledger retry and deferral tests (`:1204`, `:1131`) pass. |
| **R2 P1-4: an invalid cached citation poisons retries** | **Closed.** Citations are checked before answer storage (`fact_window_review.py:232`), with release on failure. The invalid-then-valid citation regression (`test_d140_period_adjudication_pg.py:1257`) passes. |
| **R1 first periodised fact bypasses adjudication** | **Closed.** The periodised novelty exception (`fact_adjudication.py:738`) and JEV first-fact path (`:407`) remain. Both first-fact tests (`:871`, `:901`) pass with grounded dates. |
| **R1 review/revert race without a fan-out barrier** | **Closed for that ordering.** Shared review apply (`fact_window_review.py:261`) and the exclusive first-page barrier (`workers/fact_window_review.py:124`) remain; the blocking test (`:1076`) passes. R4-P1-1 identifies a different issue: a review's basis and displayed periods can themselves be inconsistent. |
| **R1 identical-content ingest correction has no trigger** | **Closed.** `document_catalog.py:266` queues in the declaration transaction. The no-op-ingest regression (`test_d140_period_adjudication_pg.py:948`) passes and confirms no extra processing job. |
| **R1 repeated corrections collide on repair deduplication** | **Closed.** Repair is keyed by a fresh adjudication receipt (`fact_window_review.py:681`, `:737`). The A → B → A → B regression (`:977`) requires three distinct repairs and passes. |
| **R1 unbounded fan-out / review preparation** | **Closed for the reported materialization issues.** Fan-out pages facts (`period_adjudication.py:617`), continuations are durable follow-ups (`workers/fact_window_review.py:155`), and review claims are limited in SQL (`fact_window_review.py:544`). The remaining ordinary-watch materialization is closed above. |
| **R1 completed answers are not durable/shared** | **Closed for the reported retry behavior.** Attempts are keyed by fact and exact input digest; a failed-apply retry reuses its saved answer without another model call (`test_d140_period_adjudication_pg.py:1099`). |
| **R1 deletion enqueue missing or outside the transaction** | **Closed for the reported paths.** Version and lineage tombstones queue in their transaction (`lifecycle.py:612`, `:643`); source finalization uses the pinned cascade transaction (`workers/reconcile.py:485`); late testimony produces deterministic follow-up work (`:348`). Closing-edition and lineage-deletion tests pass. |
| **R1 period-change reuse test is vacuous** | **Closed as a vacuity finding.** `test_d140_period_reuse_pg.py:235` ingests two dated versions, selects a genuinely v1-only extracted claim, proves no review before the correction, requires one real replacement, verifies its window, preserves the fact vector and runs production profile repair. The profile embedding expectation remains subject to the owner decision above. |
| **Hard forget retains bases/attempts** | **Closed.** `forget.py:671` clears the basis and `:679` deletes attempts. The populated-payload erasure regression (`test_forget_catalog.py:1561`) passes, including repeated scrubbing. |
| **Truncation excludes periodised evidence** | **Closed for the ordering defect.** Review selection prioritizes window witnesses, periodised evidence and supports before its SQL limit (`fact_window_review.py:536`). |
| **Inherited nits: NULL wording, unnecessary document join, per-fact UPDATE loop** | **Closed.** Migration `p9_39_0060_d140_period_window_review.py:8` has the corrected meaning; lineage lookup filters `chunks.doc_id` (`period_adjudication.py:607`); bases are updated set-wise (`:234`). |

## P0

None found.

## P1

### R4-P1-1 — Window review can certify a window against a different period state

**Location:** `src/rememberstack/spine/fact_window_review.py:502`, `:549`, `:591`; final fingerprint comparison at `:271` and basis write at `:664`.

**Problem:** The ordinary watch is now coherent, but `_review_inputs` still reads its complete `basis` and the claims' displayed in-force intervals in separate SQL statements. Period writers do not take the entity lock or period fence. At the production engine's default READ COMMITTED isolation, those statements can describe different committed period states. Repeating that inconsistent combination during final validation makes the fingerprints equal without proving that the answer consumed the periods being certified.

This is reproducible with real, separately committed `set_effective_periods` transactions and repeated corrections:

1. The stored VAT-15% window/basis originally ends in 2024. Correct the closing edition to **A = 2023**, making review necessary.
2. During preparation, read **basis A**, then commit **B = 2022** before reading the claim intervals. The model input carries **periods B**. Restore A after input construction.
3. The model correctly chooses a 2022 end from those supplied periods.
4. During final validation under the entity lock, shared period fence and fact-row lock, repeat the same sequence: read **basis A**, commit B, read **periods B**, then restore A before applying.
5. The prepared and final fingerprints match. Review writes the 2022 window with **basis A**. Current periods are A, so every pending fan-out sees a matching basis and skips this fact.

The HEAD probe produced:

```text
window_end:                 2022-01-01T00:00:00+00:00
actual_period_end:          2023-01-01T00:00:00+00:00
stored_basis_matches_current: true
fanout_will_review:           false
```

This requires repeated corrections during both input reads; it is an ABA concurrency failure, not a claim that one isolated correction always causes it. It leaves an incorrect authoritative window with no guaranteed repair after all correction jobs finish. The shared fence cannot prevent it because it excludes fan-outs, not period writers. The probe also reproduces on `dc44041f`; this is a newly identified defect in the PR's review stage, distinct from the fixed ordinary-apply race.

**Concrete fix:** Build the review's complete period basis and bounded displayed claim periods from **one SQL snapshot**, both when preparing and when validating. Keep all-evidence comparison in SQL and the 40-claim model bound. Preserve the existing entity/fact locks, shared apply fence and lock-free inference: a later writer can then only leave a genuinely stale stored basis for its fan-out to detect. Add the repeated-correction interleaving above to `test_d140_period_adjudication_pg.py`, requiring rejection/retry or a stale basis that guarantees review; equal fingerprints over mismatched basis/presentation must never certify the result. Document the coherent review-input requirement in D140 §8.2 in place.

Reproduction retained at `/private/tmp/wpet7-r4-sol/test_r4_probes.py`, test `test_a_torn_review_cannot_certify_a_window_from_another_period`; its correctness assertion fails at both reviewed engines.

## P2

No new findings raised in this round.

## Nits

No new findings raised in this round.

## Design and implementation assessment

The keep-or-replace stage is a sound, narrow resolution of D140's missing already-applied-evidence path. It leaves D118's fact row as the sole window authority, grounds replacements in supplied claims, respects the confidence floor and records before/after adjudication history. The D140 entry (`decisions.md:6782`), §8.2, D118 cross-reference and WP-ET.7 row refine the existing decision in place without a new decision number. R4-P1-1 prevents calling the implementation race-free; the conceptual refinement itself is justified.

I found no lock-order cycle introduced by the period fence. Apply takes forget → identity → entity → shared period fence; fan-out takes only the exclusive fence and performs MVCC reads, without acquiring entity or fact-row locks. Provider inference runs outside these transactions. Only the first fan-out page takes the barrier; later pages are safe against decisions validated before that change because the first barrier already waited for their commits. Newer changes have their own requests/barriers. Deterministic continuation and per-request fact IDs support retries, and completed-answer storage avoids repaying after failed apply. This establishes bounded worker payloads and transactions by fact count, not constant query latency: full-evidence hashing still scales with evidence cardinality, and the first page temporarily blocks window applies deployment-wide. No million-document contention benchmark was run.

The extra first-periodised-fact call is justified: with no identity candidate, the engine still must decide whether the edition's dates govern the proposition or merely the document reporting it. That decision belongs to adjudication. The normal candidate/claim limits and translator retry bound apply; unchanged bases and duplicate triggers cost no review call. Undated first facts retain the free novelty path, and JEV's documented single-interval path makes no generative first-fact call. Canned models verify plumbing and application, not real-model semantic accuracy.

Rule 4 was checked explicitly. The PR changes no chunk packing, block grid, Selection/reuse/Claimify hash input, header facts, text origin, embedding input policy or upstream toolchain generation. The period-only path queues review and projection repair without extraction or chunk/claim/fact embedding work. The reuse test exercises an actual correction and replacement; the six existing dated-origin/reuse regressions also pass. The displayed-profile embedding choice is solely the pending owner decision recorded above.

Undated parity remains intact. AST comparison with `origin/main` confirms the original `_FACT_PROMPT` and all pre-existing adjudication/normalizer/flush version expressions are unchanged. Period keys and guidance are conditional; undeclared lineages queue no review. Prompt parity, original novelty-call count and JEV tests pass. No legal-specific engine branch was found.

Migration `p9_39_0060` is additive and linear. Its table/constraints, required owner field, catalog inventory and count pins (including 633 NOT NULL constraints), reset registration and migration-head pins agree. Queue enum/contract, worker export, self-host handler and Compose service are wired; readiness correctly excludes this per-lineage/per-fact stage from per-version completion. Downgrade/reupgrade preserved existing fact windows, retained the enum value as documented and restored the required `claimed_by` column. Deletion triggers and forget cleanup retain the audited fixes.

## Validation and limits

- **104 focused non-database tests passed**; 23 database-gated cases skipped in those invocations.
- **58 PostgreSQL acceptance/regression cases passed**, covering the full period-adjudication module, period reuse, forget, ordinary application writes/retries and dated text-origin reuse.
- All **three round-4 regression tests fail behaviorally on `dc44041f`**, without compatibility shims. The additional review-coherence probe fails its correctness assertion at HEAD and at `dc44041f` with the diagnostic above.
- The `p9_39_0060` downgrade/reupgrade smoke check passed. `git diff dc44041f..HEAD --check` passed.

Database checks used an isolated local **PostgreSQL 17** instance configured for UTC. Docker was unresponsive. As in the previous review's local validation, unavailable PostgreSQL 19 graph DDL and BM25 setup were omitted, and default partitions replaced pg_partman setup. Period projections, SQL hashes, advisory locks, ordinary application/review logic, production profile repair and forget code were exercised unchanged. This is not a clean PostgreSQL 19 extension/catalog-suite run or a large-corpus contention benchmark.

No tracked file was modified, committed or pushed. The pre-existing worktree deletion of `.ensure_pg.sh` was left untouched.

Verdict: Request changes
