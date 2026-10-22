# Amendment audit

Reviewed PR [#505](https://github.com/writeitai/remember-stack/pull/505) at `cdc22288d35d795c82c5c741e2e51b17023ce7c2`, including its live body, against round 3’s Judgements and the round-5 verdict. Read AGENTS.md → CLAUDE.md, README.md, plan/README.md and the design-corpus skill first. The owner’s option-A decision is accepted; this audit concerns its documentation and evidence.

| Requirement | Met / not met | Evidence |
| --- | --- | --- |
| Named size- and stratum-specific latency budgets | **Met** | [D142](/Users/jpuc/code/moje/remember-stack-504/decisions.md:6968) and [P1 design §9](/Users/jpuc/code/moje/remember-stack-504/plan/designs/postgres_p1_search_projection_design.md:647) name seven budgets at 300k chunks: 300 / 300 / 300 / 500 / 1,000 / 2,500 / 1,000 ms p95. |
| Accurate absolute eligible-row counts alongside document fractions | **Not met** | Current and filtered counts agree with the archive. The history row incorrectly calls all 300,000 indexed edition rows eligible; the described mixed corpus selects **180,000** under history. See P2-1. |
| Public operation, channel and measurement boundary | **Met** | [Measured conditions](/Users/jpuc/code/moje/remember-stack-504/plan/designs/postgres_p1_search_projection_design.md:657): public `QueryEngine.search_chunks`, BM25, k=20, in-process without HTTP. The [harness](/Users/jpuc/code/moje/remember-stack-504/benchmarks/p1_ranked/sweep.py:248) uses that operation. |
| Corpus, versions and duplication shape | **Met** | Conditions specify 6,000 lineages × 5 versions × 10 chunks, half periodised, approximately half repeating editions, 120,120 copied rows, 20–300 words, 50,000-word vocabulary, D134 metadata and no vectors. Archive arguments, duplicate count and generator agree. |
| Machine, cache and competing load | **Met** | §9 specifies n2-highmem-8, 8 vCPU/64 GB/pd-ssd, PostgreSQL 19 branch image, 8 GB shared buffers, 128 MB work memory, warm cache and integration-suite CPU contention against another database. These are stated conditions; the JSON does not independently record host/cache telemetry. |
| Query population and repetitions | **Met** | §9 specifies 40 two-word queries, generator distribution, seed 142, query hash, one execution per stratum, sequentially without concurrency. All seven archived query lists equal the reconstructed seed-142 list. |
| Latency paired with returned-k, completeness and early-stop rates | **Met** | D142 and §9 pair every latency with returned-k counts and zero early stops; complete outcomes must be `k_filled` or `exhausted`. All 280 archived per-query accounts satisfy this. Fewer-than-k results are exhausted searches, not hidden truncation. Production early stops retain typed disclosure. |
| Explicit acceptance of multi-second filtered reads | **Met** | [D142](/Users/jpuc/code/moje/remember-stack-504/decisions.md:7005), §9 and the PR body explicitly accept them; the 4,990-row exact stratum has a 2.5 s p95 budget. |
| No 300k-to-3M/50M extrapolation | **Met** | [D142](/Users/jpuc/code/moje/remember-stack-504/decisions.md:7008), §9, retrieval design, delivery status and the PR amendment limit acceptance to 300k; the nonhydrating 3M run is diagnostic and 3M/50M remain ungated. |
| Approximately 300 ms retained for WP-RS.11 with its own gate | **Met** | [Delivery plan](/Users/jpuc/code/moje/remember-stack-504/plan/plans/index_driven_ranked_search.md:49) names immutable `doc_id`, threshold retuning and issue #508. Its gate specifies all five filtered populations at 300k, p95 ≤300 ms, zero early stops and unchanged returned-k, plus hydratable 3M measurements or a size-specific owner decision. |
| Quoted measured latencies and completeness match the archive | **Met** | All 14 rounded p50/p95 values, seven returned-k counts and 120,120 duplicated rows match [the 300k archive](/Users/jpuc/code/moje/remember-stack-504/plan/analysis/data/d142_sweep_300k_2026-10-22.json). The history eligibility label is the separate error above. |
| Corpus/public claims reconciled without stale gate or owner-pending text | **Not met** | D142, §9 and [retrieval §10](/Users/jpuc/code/moje/remember-stack-504/plan/designs/retrieval_design.md:632) explicitly qualify the old interactive budget. Analysis §8.3 and several PR-body statuses still call P1-6 open; the PR’s current RS.8 summary still describes failure against the prior gate. See P2-2. |
| `1223ba6f5` changes only tests/review material | **Met** | Exactly two paths change: 84 added lines in `src/tests/surfaces/test_query_sandbox_budget.py` and the 51-line archived round-5 review. No production, benchmark, dependency or configuration changes. The added coverage exercises both dynamic-OID timeout SQLSTATEs and classifier controls. |

Archive cross-check, in table order: p50/p95 (ms) = **152/263, 112/267, 78/90, 235/351, 600/859, 1,334/2,084, 505/780**; returned-k out of 40 = **40, 40, 7, 18, 37, 35, 35**. Every named p95 budget is met. Query SHA-256 is `c5d837141e98b3a18b26db53675ec26228ec786ced4241d44dbaaace8175873e`; corpus MD5 is `b63f5cd71918a4c5b3a0d7be5c494a61`.

Verification used read-only git/PR inspection, static source checks and database-free archive assertions. `git diff --check fb90bfd24..cdc22288d` passed. No tests or benchmarks were executed. No Docker/PostgreSQL, deployment, paid provider or LoCoMo resource was accessed. No tracked files were modified. Rule 4 remains unchanged: neither reviewed commit alters ingestion code, reuse identities or paid-work inputs. The owner-directed post-merge follow-ups are not findings.

## P0

None.

## P1

None. The owner’s latency amendment is not challenged.

## P2

1. **Correct the history stratum’s eligible population in the amendment.** [P1 design](/Users/jpuc/code/moje/remember-stack-504/plan/designs/postgres_p1_search_projection_design.md:650), [D142](/Users/jpuc/code/moje/remember-stack-504/decisions.md:6977), and the PR-body amendment table say 300,000 eligible history rows. That is the indexed/live edition population, not the population selected by this operation’s temporal predicate.

   The [seed](/Users/jpuc/code/moje/remember-stack-504/benchmarks/d140_scope/run.py:168) declares periods for exactly 3,000 of 6,000 lineages. History selects all five editions for those lineages. For the other 3,000, [the projection](/Users/jpuc/code/moje/remember-stack-504/src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py:251) gives only the served version a nonempty interval. [Chunk confirmation](/Users/jpuc/code/moje/remember-stack-504/src/rememberstack/adapters/postgres_p1.py:2095) requires that interval to overlap the history window. Therefore the history-eligible population is **3,000 × 5 × 10 + 3,000 × 1 × 10 = 180,000**. This is a source-derived count; no database was queried.

   The archive’s history `eligible_current_chunks=60000` is also not a history count: [the harness](/Users/jpuc/code/moje/remember-stack-504/benchmarks/p1_ranked/sweep.py:191) always counts `chunks_live`, ignoring `time_scope` for that metric. It cannot substantiate the amendment’s 300,000 claim. Correct the eligible count consistently, distinguish it from 300,000 indexed edition rows, and explain the archive’s current-only field. The measured 112/267 ms and 40/40 returned-k remain valid; this correction needs no new latency gate or owner decision.

2. **Finish replacing the superseded latency-decision status.** [Analysis §8.3](/Users/jpuc/code/moje/remember-stack-504/plan/analysis/index_driven_ranked_search.md:602) still calls the threshold evidence input to an “open latency decision.” In the inspected [PR body](https://github.com/writeitai/remember-stack/pull/505), round 5 says “Still open … waiting on the owner’s decision” (body line 39), round 2’s table says “OPEN, an owner decision” (line 75), and the current “What is implemented” RS.8 row says the p95 budget is not met (line 163). The “Scaling sweep” section still points to §8.2 “with the gate status,” omitting the accepted §8.3 result (line 179).

   These unqualified statuses conflict with the new amendment and the delivery plan’s amended pass. Mark historical owner-pending statements as resolved/superseded on 2026-10-22, change the analysis’s open-decision sentence, and make the current RS.8/sweep summary report the amended 300k pass with diagnostic, ungated 3M evidence. Preserve historical measurements and original review verdicts. This is documentation reconciliation, not a request to revisit the owner’s choice or run post-merge gates.

## Nits

None.

Verdict: Request changes
