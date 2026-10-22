# Closure audit

Reviewed `44118ae07` against the supplied round-5 approval at `17c81c1ff`, owner-decision commit `ac5366426`, both merge parents, and the live [PR #506 body](https://github.com/writeitai/remember-stack/pull/506) on 2026-10-22. Read AGENTS.md → CLAUDE.md, including Rule 4, and applied the design-corpus skill.

The owner's decision is accepted without reconsideration. D140 in [decisions.md](/Users/jpuc/code/moje/remember-stack-wpet7/decisions.md:6811) and [design §8.2](/Users/jpuc/code/moje/remember-stack-wpet7/plan/designs/effective_time_and_section_references_design.md:1080) accurately record option (a): embed a profile when its rendered dated text changes; this projection refresh re-processes no document text and is outside Rule 4. Both record rejection of the date-free alternative. The design and PR correctly link the conditional entity-identity follow-up, [#509](https://github.com/writeitai/remember-stack/issues/509).

Closure is incomplete because several summaries still contradict or fail to mark that resolution; see P2. No runtime merge regression was identified.

# Merge interaction audit

- **Fact authority and time scopes:** deployment-wide nomination confirms batches through `_FACT_AUTHORITY_JOIN` / `facts_visible_history`, `_fact_time`, and `fact_in_scope` before candidates count toward k. Scoped branches apply the same authority and evidence gate before their LIMIT; `_SURVIVOR_SCOPE_CTE` budgets absorbed endpoints consistently with survivor-based coverage. See [postgres_p1.py](/Users/jpuc/code/moje/remember-stack-wpet7/src/rememberstack/adapters/postgres_p1.py:1535). A window review updates the same authoritative fact row's dates and `period_basis`, preserving its label/vector; retrieval therefore sees reviewed windows without a fact re-embedding or separate search-row synchronization.
- **Snapshot and pushdown:** public ranked operations retain D142's read-only REPEATABLE READ context and pinned instant. Nomination, confirmation, hydration and period-scope predicates share that context. Eligible-set enumeration retains the complete query predicates, and pushdown retains confirmation. WP-ET.7's certifying reads and period fence remain on the independent worker write path; the merge does not replace them with the retrieval context. Deadline failures remain typed boundaries.
- **Profile vectors:** review → projection repair → `LabelFactsHandler` → `EntityProfileRefresher.refresh_for_facts` reaches the production batched refresh. Every returned vector passes `_vector_literal` → `require_p1_vector` before publication ([profile_refresher.py](/Users/jpuc/code/moje/remember-stack-wpet7/src/rememberstack/spine/profile_refresher.py:283), [validation](/Users/jpuc/code/moje/remember-stack-wpet7/src/rememberstack/spine/profile_refresher.py:726)). Float4 overflow/underflow, non-finite and zero vectors are rejected. Exact semantic branches exclude legacy zero vectors; HNSW exhaustion counts exclude them too. Changed-text debouncing survives the merge.
- **Migration and CI:** static graph validation found 60 revisions, one head `p9_39_0060`, and the required parent `p9_38_0059`, with no missing/duplicate revisions. D142 added no migration. Unit and integration path lists are exact unions of both parents: 153 and 78 unique existing paths respectively, retaining both D140 proofs and D142 tests.
- **Rule 4 and tests:** the delta since round 5 changes no chunking, extraction/reuse identity, text-origin or D80 embedding-input code. The production reuse regression correctly asserts exactly one changed-profile embedding, while the unchanged-profile case asserts zero; no stale behavioral assertion was found. Their PostgreSQL execution was not repeated here.

Validation: **138 selected database-free tests passed in 4.81 s**, covering period adjudication, text scopes, fact windows, concise inputs, text origin, D142 ranking/vector validation, search accounts, timeout classification and mocked fact-search paths. `git diff --check 17c81c1ff HEAD` passed. No Docker/PostgreSQL stack, database-backed tests, LoCoMo database or deployment operations were run. The tracked worktree remains clean; nothing was committed or pushed.

# P0

None.

# P1

None.

# P2

1. **Finish reconciling summaries with the resolved profile decision.** The live [PR body](https://github.com/writeitai/remember-stack/pull/506) still has two unqualified contradictory summaries:

   - Line 69, the round-3 heading, says **“P1-2 awaiting an owner decision”**. Annotate it as resolved on 2026-10-22, preserving the historical review outcome.
   - Line 46 says **“the real label/profile repair runs with zero embedding calls”**. The current production proof requires one changed-profile embedding ([test assertion](/Users/jpuc/code/moje/remember-stack-wpet7/src/tests/workers/test_d140_period_reuse_pg.py:388)), as PR line 29 correctly explains. Mark the old summary superseded, or describe zero document/fact embeddings plus the changed-profile call.

   Also qualify [design §2.3](/Users/jpuc/code/moje/remember-stack-wpet7/plan/designs/effective_time_and_section_references_design.md:229): “Periods never trigger reprocessing” and “not part of any … embedding … input” should explicitly describe **document ingestion inputs**, with a reference to §8.2's accepted profile refresh. The blanket wording otherwise overstates the exemption the owner actually accepted. The correct new paragraph and PR opening do not reconcile these remaining summaries. This requires wording changes only, with no new owner decision or runtime gate.

# Nits

1. The [reuse-test module description](/Users/jpuc/code/moje/remember-stack-wpet7/src/tests/workers/test_d140_period_reuse_pg.py:8) still summarizes the correction/review as having “no extraction or embedding call.” Qualify this as document/fact work and mention the changed-profile refresh, matching the assertions below. The assertions themselves are current.

Verdict: Request changes
