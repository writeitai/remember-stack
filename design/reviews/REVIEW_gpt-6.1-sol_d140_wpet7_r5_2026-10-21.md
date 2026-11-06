# WP-ET.7 implementation review — round 5

Reviewed HEAD `17c81c1ff96c000bc8cf58368847ea4869fb9ebb`, matching `origin/feat/d140-wpet7-period-aware-adjudication`, against `origin/main...HEAD`. The sole fix commit after round 4 is `17c81c1ff` (`3b676c17..HEAD`). Fresh findings in this round are restricted to P0/P1, as requested.

Read the working agreement first. This worktree has no `AGENTS.md` and its `CLAUDE.md` stops at Rule 3; I additionally read `origin/main:AGENTS.md` → `origin/main:CLAUDE.md`, including all of Rule 4. Read the design-corpus skill, D140 §8 and its refinement, D140 in `decisions.md`, D118, the WP-ET.7 row, the supplied round-4 review and the author's response.

## Closure audit

| Finding carried by round 4 | Status | Evidence at HEAD |
| --- | --- | --- |
| **R4-P1-1: review certifies a window against a different period state** | **Closed** | `src/rememberstack/spine/fact_window_review.py:544` obtains both the complete basis and shown periods through `read_period_inputs_on`; its SQL statement reads them from one snapshot (`src/rememberstack/spine/period_adjudication.py:245`, `:327`). Final validation compares the prepared digest at `fact_window_review.py:270`, and apply stores that validated read's basis at `:664`. The deterministic repeated-correction regression passes at HEAD and fails behaviorally on `3b676c17`, detailed below. |
| **R3-P1-1; R2-P1-1; R1 ordinary-apply serialization: certifying periods the decision never saw** | **Closed** | The pre-validation watch and shown-period equality remain (`src/rememberstack/spine/fact_adjudication.py:833`, `:864`). Post-write verification now returns the bases and digest from the same statement (`src/rememberstack/spine/period_adjudication.py:426`); `fact_adjudication.py:913` stores those returned values, excluding previously stale candidates. This also removes the old gap between recording a basis and separately checking it. All four ordinary-apply interleaving regressions at `test_d140_period_adjudication_pg.py:1285`, `:1341`, `:1414`, `:1468` pass. |
| **R3-P1-2; R2-P1-2; R1-P1-5: profile embedding after a period correction** | **Partially closed — owner decision pending** | Fact vectors remain unchanged (`src/rememberstack/spine/fact_window_review.py:628`); profile repair retains existing vectors (`:743`). The production-path test still expects one embedding when the displayed profile text changes (`src/tests/workers/test_d140_period_reuse_pg.py:388`). The owner is choosing between embedding that changed text and a date-free embedded profile with separate temporal presentation. This is solely an audit note, excluded from findings and verdict as instructed. |
| **R3-P1-3: ordinary apply materializes all candidate evidence** | **Closed** | Complete evidence aggregation remains in SQL. Only bounded fact bases/stale IDs, a digest and shown-claim periods leave the database (`src/rememberstack/spine/period_adjudication.py:295`, `:354`). The 5,001-evidence regression at `src/tests/spine/test_d140_period_adjudication_pg.py:1527` passes its worker-memory bound and detects a later change to unshown evidence. |
| **R2-P1-3: transient failure strands a claimed review** | **Closed** | Stable ownership, failure release and live-owner deferral remain (`src/rememberstack/spine/fact_window_review.py:212`, `:233`, `:392`; `src/rememberstack/workers/fact_window_review.py:83`). Real worker/ledger retry and duplicate-deferral tests at `test_d140_period_adjudication_pg.py:1204` and `:1131` pass. |
| **R2-P1-4: invalid cached citation poisons retries** | **Closed** | Citation validation precedes durable answer storage (`src/rememberstack/spine/fact_window_review.py:231`), and failure releases the claim. The invalid-then-valid citation regression at `test_d140_period_adjudication_pg.py:1257` passes. |
| **R1: first periodised fact bypasses adjudication** | **Closed** | The periodised novelty exception remains at `src/rememberstack/spine/fact_adjudication.py:738`; JEV's first-fact path remains at `:407`. Both first-fact regressions (`test_d140_period_adjudication_pg.py:871`, `:901`) pass with grounded windows. |
| **R1: review/revert race without a fan-out barrier** | **Closed** | Review apply acquires the shared fence before validation (`src/rememberstack/spine/fact_window_review.py:260`); first-page fan-out acquires it exclusively (`src/rememberstack/workers/fact_window_review.py:124`). The existing blocking test passes. Additional probes committing corrections after the certifying read prove that fan-out waits for both ordinary and review commits, then queues their stale facts. |
| **R1: identical-content ingest correction has no trigger** | **Closed** | Existing-version declarations queue review in the ingest transaction (`src/rememberstack/spine/document_catalog.py:266`). The no-op-ingest regression at `test_d140_period_adjudication_pg.py:948` passes and checks that processing work is not added. |
| **R1: repeated corrections collide on repair deduplication** | **Closed** | Repair targets use a fresh adjudication receipt (`src/rememberstack/spine/fact_window_review.py:681`, `:737`). The A → B → A → B regression at `test_d140_period_adjudication_pg.py:977` passes and requires three distinct repairs. |
| **R1: unbounded fan-out and review preparation** | **Closed for the reported materialization defects** | Fan-out pages examined facts (`src/rememberstack/spine/period_adjudication.py:623`), and durable continuations carry a keyset cursor (`src/rememberstack/workers/fact_window_review.py:155`). Review selects at most 40 claims in SQL (`src/rememberstack/spine/fact_window_review.py:537`). The page-size-one test passes; an additional 1,003-fact probe covers every fact exactly once across three 500-fact pages and confirms stable retry IDs. Complete basis hashing still scales with evidence cardinality. |
| **R1: completed answers are not durable/shared** | **Closed** | Attempts are keyed by fact and exact input digest (`src/rememberstack/spine/fact_window_review.py:339`); validated answers are stored before apply (`:243`). The failed-apply regression at `test_d140_period_adjudication_pg.py:1099` reuses its answer without another model call and verifies cleanup after success. |
| **R1: deletion enqueue missing or outside its transaction** | **Closed for the reported paths** | Version and lineage deletion enqueue inside their tombstone transactions (`src/rememberstack/spine/lifecycle.py:614`, `:643`). Source finalization uses the pinned cascade transaction (`src/rememberstack/workers/reconcile.py:485`); late testimony has a deterministic follow-up (`:348`). Closing-edition and lineage-deletion regressions pass. |
| **R1: reuse regression is vacuous** | **Closed** | `src/tests/workers/test_d140_period_reuse_pg.py:241` ingests two differently dated versions, measures incremental extraction/embedding, selects a real v1-only extracted claim, proves its basis initially matches, then requires one review, an actual replacement and production profile repair. It checks unchanged pipeline rows and fact-vector contents. The test passes. The profile embedding expectation is the owner note above. |
| **Hard forget retains bases/attempts** | **Closed** | `src/rememberstack/spine/forget.py:671` clears bases and `:678` deletes attempts. The populated-payload erasure regression at `src/tests/spine/test_forget_catalog.py:1561` passes, including repeated scrubbing. |
| **Truncation excludes periodised evidence** | **Closed for the reported ordering defect** | Window witnesses, periodised claims and supports are prioritized before the SQL limit (`src/rememberstack/spine/fact_window_review.py:529`). |
| **Inherited nits: NULL wording, unnecessary document join, per-fact UPDATE loop** | **Closed** | Migration `p9_39_0060_d140_period_window_review.py:8` states the correct NULL meaning; lineage lookup filters `chunks.doc_id` (`src/rememberstack/spine/period_adjudication.py:613`); basis storage remains set-wise (`:220`). |

### Countercheck of both newly added tests

Ran HEAD's tests against HEAD and an isolated `git archive 3b676c17` engine/migration tree. The old-engine run needed one **collection-only** stub for the newly imported `verified_period_bases_on`; the stub raises if called, and neither new test calls it. No schema or behavioral shim was used.

| New test in `src/tests/spine/test_d140_period_adjudication_pg.py` | HEAD | `3b676c17` |
| --- | --- | --- |
| `test_a_torn_review_read_cannot_certify_a_window_from_another_period` (`:1674`) | **Pass** | **Behavioral failure** at `:1671`: stored basis equals current basis, but the window ends **2022-01-01** while current periods end **2023-01-01**. This reproduces R4-P1-1. |
| `test_reviews_racing_real_period_writers_settle_on_the_final_periods` (`:1720`) | **Pass** | **Pass**. This is a concurrency smoke test, not failing-first evidence; timing alone did not reproduce the old race. The author's response accurately discloses this. |

Thus, **not every new test fails on the old commit**. The deterministic regression supplies the substantive failing-first proof; the threaded test adds queue-drain coverage. The earlier tests changed only to use the renamed helpers remain passing at HEAD.

## P0

None found.

## P1

None remain within this round's scope.

## P2

No new findings raised in this round.

## Nits

No new findings raised in this round.

## Structural argument and fresh fix review

**The implementation differs from the round-5 brief:** period writers still do **not** acquire the period fence. The commit and response explicitly explain this, and the binding design does too (`plan/designs/effective_time_and_section_references_design.md:994`). Periods can change between a decision's final read and its write. The reviewed guarantee is coherent certification followed by ordered repair, rather than freezing period writers.

That alternative is sound for the audited paths:

1. A review's complete basis and displayed periods come from one SQL snapshot. Repeating A → B → A corrections cannot reproduce the old combination of basis A and presentation B. Final fingerprint validation binds the answer to those inputs, and apply stores that read's basis.
2. Ordinary apply watches before final validation, checks shown-period equality, then reads the post-write bases and complete watched-period digest together. Evidence changes involve shown claims, so the digest covers the same claim set before and after application. A mismatch rolls back; a match stores only the verified bases. Previously stale candidates retain their stale bases for review.
3. A later committed period change leaves the stored basis stale. Both certifiers hold the shared fence through commit. The change's first fan-out page takes the exclusive fence, so it cannot inspect the old stored basis and finish before the in-flight certification commits. Subsequent pages may proceed without the fence: the first barrier already waited out decisions based on inputs preceding that change.

I independently tested corrections committed **after** ordinary post-write verification and **after** review final validation. Both corrections committed while the certifier was still running, confirming that writers are unfenced. In each case the concurrent fan-out blocked until commit and then queued the stale fact. These probes passed.

The writer/trigger audit covers period replacement/retraction and clear (`effective_time.py:146`, `:207`), no-op ingest corrections (`document_catalog.py:266`), version/lineage deletion (`lifecycle.py:614`, `:643`), source-deletion finalization and late testimony (`workers/reconcile.py:348`, `:485`). New-version occurrence/current-reading changes receive their review through successful reconciliation (`:236`), with reprocessing/deletion handling an unfinished version as §8.2 specifies. Hard forget uses its own fence and scrub rather than scheduling ordinary reviews. There is no claim that every occurrence write immediately queues a review.

No new lock-order cycle was found. Certifiers acquire forget → identity → entity coordination, then the shared period fence before final validation. Fan-out acquires only the exclusive period fence and performs MVCC reads; it does not wait for entity/fact/application row locks. Provider inference runs outside these transactions. The fix adds no writer lock or deployment-wide extraction serialization.

The first-page exclusive transaction does temporarily pause applies across the deployment. Paging bounds examined fact count, worker payload and follow-up transaction size; it does **not** establish constant query latency, because complete evidence hashing and lineage lookup still depend on corpus shape. The 1,003-fact paging probe and 5,001-evidence memory regression passed. No million-document contention benchmark was run, so the design's reference to millisecond applies is not established by this review. The fix does not introduce a new unbounded worker materialization or lock cycle.

Stored review answers retain exact-input reuse, claim ownership, release on provider/citation failure, and deferral of competing live owners. Their application, adjudication transcript, basis and recoverable repair enqueue commit together. Duplicate triggers with unchanged bases return without a model call.

The extra first-periodised-fact call is justified: even with no identity candidate, adjudication must decide whether a document establishes the proposition's dates or merely reports an event. It uses the existing bounded ordinary snapshot and retry/receipt path; cost grows with first assertions requiring this decision. Undated first facts retain their free novelty shortcut. JEV's documented deterministic single-interval path adds no generative first-fact call. Canned models establish plumbing and grounded application, not real-model semantic accuracy.

The keep-or-replace review remains a narrow D140 refinement of the missing already-applied-evidence path. D118's fact row remains the sole window authority; confidence, supplied-claim grounding and adjudication history remain enforced. The one-read/certification rule and rationale are reconciled in §8.2 (`:976`), D140 (`decisions.md:6782`) and the WP-ET.7 row (`plan/plans/effective_time_and_section_references.md:20`), without a new decision number. No legal-specific engine branch was found.

## Rule 4, parity, migration and wiring

The PR changes no chunk packing or block grid, Selection/reuse/Claimify hash inputs, header facts, text-origin policy, upstream embedding input policy or reuse toolchain version strings. Period-only corrections enqueue window review and profile projection repair; they do not schedule extraction or chunk/claim/fact embedding. The real replacement/reuse regression, six dated-origin tests and six existing reuse-lifecycle tests pass. The separate profile embedding owner decision remains excluded as directed.

Undated snapshots omit period keys. AST comparison with `origin/main` confirms that `_FACT_PROMPT` and all pre-existing adjudication/normalizer/flush version expressions remain unchanged. Undated parity and novelty-call-count tests pass; never-periodised lineages queue no review. The new shared advisory-lock/existence-probe work adds database operations, with no extra undated model calls.

Migration `p9_39_0060` remains additive and linear. Its six NOT NULL columns, two checks, FK, PK, 83-table pin, 633-NOT-NULL catalog pin, reset registration and migration-head pins agree. Downgrade/reupgrade preserved **1,003 existing fact windows**, retained the stage enum value as documented, and restored required `claimed_by`. Queue contracts, worker export, self-host handler and Compose service are wired; per-lineage/per-fact review is correctly excluded from per-version readiness. Public documentation describes the shipped stage. Deletion and forget fixes remain intact.

## Validation and limits

- **106 focused non-database tests passed**; 32 database-gated cases skipped and 6 cases deselected in those invocations.
- **68 PostgreSQL acceptance/regression cases passed**: the complete 26-test period-adjudication module plus 42 reuse, dated-origin, forget, ordinary-writer, rejection-retry and concise-adjudication cases.
- **3 additional PostgreSQL probes passed**: late-writer ordering for ordinary and review certification, and 1,003-fact fan-out coverage/retry stability.
- Both new tests were counterchecked on `3b676c17`: one substantive correctness failure and one passing concurrency smoke test, as reported above.
- Migration downgrade/reupgrade smoke check and `git diff 3b676c17..HEAD --check` passed.

Database checks used an isolated local **PostgreSQL 17** instance in UTC. Unavailable PostgreSQL 19 property-graph DDL and BM25 setup were omitted, and default partitions substituted for pg_partman setup. The reviewed period projections, hashes, advisory locks, ordinary/review application, production profile repair and forget behavior ran unchanged. This is not a clean PostgreSQL 19 extension/catalog-suite run or a large-corpus throughput measurement. Test logs and independent probes are under `/private/tmp/wpet7-r5-sol/`.

No tracked files were modified, committed or pushed.

Verdict: Approve
