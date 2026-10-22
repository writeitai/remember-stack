# Response to GPT-6.1 Sol D142 design review, round 1

Review: [REVIEW_gpt-6.1-sol_d142_design_r1_2026-10-21.md](REVIEW_gpt-6.1-sol_d142_design_r1_2026-10-21.md)
(reviewed head `94569f37`). "Design" below means
`plan/designs/postgres_p1_search_projection_design.md`; "analysis" means
`plan/analysis/index_driven_ranked_search.md`.

## P1

| # | Finding | Resolution |
| --- | --- | --- |
| P1-1 | BM25 equivalence excluded today's zero-score rows | Verified: `search_chunks_lexical_scored`/`search_claims_lexical_scored` order all eligible rows with no match requirement. Adopted **positive-match-only** lexical semantics on every path (index nomination, exact entity tiers, SRFs, `scope_pending`, which already required a term match): design §8.1; equivalence restated as "exact scoring of the same eligible *matching* rows under one scoring generation" (§8.3, analysis §6.1); recorded as an explicit behaviour change in D142 Consequences and design §14; measured separately in the §9 gates; the zero-score walk alternative compared and rejected (analysis §4 "The lexical zero-score question"); tests for absent terms, empty analyzed query, fewer-than-`k`, both sides of the exact-tier threshold (plan WP-RS.7). |
| P1-2 | `REPEATABLE READ` does not freeze the BM25 scoring generation | Replaced repeated `LIMIT` queries with **one server-side `NO SCROLL` cursor per nomination** (one executor → one scoring generation; one continuing HNSW scan), disjoint doubling fetches, scores carried through confirmation and never recomputed (design §8.3; rejected variant B′ in analysis §4). Continuation and tie lookahead specified; concurrency test with term-skewed inserts and spill/compaction between fetches (plan WP-RS.7, analysis §8.2). |
| P1-3 | A short HNSW batch was declared exhausted | Channel-specific termination (design §8.6): BM25 ≥ 1.5.1 drained cursor = exhausted; a drained HNSW scan is `exhausted` only if an exact count of searchable rows (same attestation predicates, non-null vector) does not exceed the rows streamed, else `ann_scan_bound`. Tuple cap, memory stop and strict-order skipping named; writers reject zero-norm/non-finite vectors (WP-RS.1); tests separate cap/memory stops from genuine exhaustion (WP-RS.7). |
| P1-4 | The bridge does not share the adapter's snapshot | Verified: the adapter opens its own SQLAlchemy connection; the bridge confirms on the executor's psycopg connection. Defined the **ranked-read context** (design §8.2): one `READ ONLY, REPEATABLE READ` transaction per operation, owned by the query engine; pinned `evaluated_at`/`believed_at` passed as parameters (no statement clocks); covers readiness, all fetches, coverage, both fact branches, `scope_pending` and final confirmation. The SQL sandbox exports its request snapshot and the adapter imports it; a failed import fails `p1_unavailable`. `pg_confirmed_at` reports the pinned instant. Concurrency only via snapshot import. Open query space §9 text updated; bridge tests in WP-RS.7. |
| P1-5 | The fact merge dropped coverage-first ranking | Design §8.4–§8.5: fact coverage = distinct `ranking_entity_ids` among survivor-resolved endpoints, eligible scope (`entity_ids`) kept distinct; merge by `(coverage DESC, distance, fact_kind, fact_id)`; branch depth only after ordering and eligibility; exact scoring for bounded tiers; tests for two- vs one-anchor across tables, redirected endpoints, graph-expanded scopes, overlapping UUIDs (WP-RS.7). |
| P1-6 | Truncation had no path to callers | Design §8.6: a private **structured search result** (rows, termination, counters by rejection class, effective limits, attestation) including zero-row results; propagation through RRF, the fact merge, envelopes (`known_empty` only when every channel exhausted), `QueryResult.semantic_invocations[]` (already carries termination reason/counts), and `Freshness.scope_pending.complete` (D140 §3.7 revised; count becomes a lower bound when incomplete). Plan WP-RS.4; end-to-end empty-cap and one-channel-cap fixtures in WP-RS.7. |
| P1-7 | Nomination SQL is still cost-planned | Agreed — "join-free" makes the index plan likely, not certain. Design §9: planner controls (`enable_seqscan`/`enable_sort`/`enable_bitmapscan` off, low `cursor_tuple_fraction`) scoped to the nomination `DECLARE` and restored before confirmation; `EXPLAIN (FORMAT JSON)` of the exact declared statement must show the expected index, otherwise `plan_rejected` → `p1_unavailable` before any fetch; readiness checks index validity; prepared/custom plans covered because the declared statement is attested; attestation cost reported by the benchmark; forced-sequential-plan and prepared-statement tests in WP-RS.7. |

## P2

| # | Finding | Resolution |
| --- | --- | --- |
| P2-1 | `k/s` is not corpus-independent | Design §9.1 "Cost model and its limits" (declining eligible share, long tie runs, broad entity scopes) and D142 Consequences; entity work budget overflow returns a typed `boundary` instead of trusting partial coverage (§8.4); benchmark strata and reporting of returned-`k`, truncation, fetched candidates, coverage rows alongside latency; "flat latency with rising truncation is a failure" (§9, analysis §8.4); 4k, 5,000 and the work budget remain labelled starting points. |
| P2-2 | The proposal changed chunk grain | Proposal rewritten: entries fan out to **every member chunk before the relevance cut**; members are confirmed and ranked (coverage, score, chunk ID) exactly like chunk candidates; separate results per eligible edition in every scope incl. `overlap` and concurrent editions; edition aggregation stays in `search_documents`; work budget counts member rows; worked example corrected (history returns ten chunk results); BM25 statistics change requires quality measurement, "more faithful" removed; tests listed. |
| P2-3 | The pilot trigger was circular | Proposal "Investigation trigger and adoption prerequisites" and plan "Decision point": an isolated prototype (P-1/P-2) on a copy of the LegalIt pilot corpus with a fixed query set, measuring examined candidates by rejection reason, the share attributable to shared indexed-input keys, tie runs, latency, returned-`k`/truncation and quality, for D142 alone and with the prototype; thresholds become owner-confirmed investigation thresholds; adoption needs a demonstrated benefit and reconciles the binding corpus in the adopting PR; no LegalIt-specific engine behaviour. |
| P2-4 | Contrary current contracts remained | Reconciled: design §3, §7, §11 failure list, §13 (now a historical cutover record; sequencing moved to the plan), §14; `retrieval_design.md` D94 amendment paragraph; `open_query_space_design.md` (invariant 5, SRF rows, `semantic_entities`, failure table, Batch C, executor snapshot paragraph); `p2_graph_design.md` image pin paragraph; D140 refinement note added inside the original D140 entry; D94/D98 notes refreshed. |
| P2-5 | Unverified assertions; non-executable gate | Removed "random vectors are a known worst case" and the generic 95–99% recall range (recall is now measured per stratum); "only correct plan" corrected to include exact scoring of a prefiltered subset; option A re-compared at the selected versions (1.5.1 fills BM25; HNSW caps remain); PG19 support stated as best-effort upstream → local image gate (design §6, graph design); executable gates: mean recall@10 ≥ 0.95, no stratum < 0.90; LoCoMo ≥ 3 runs per side, pass if not lower by more than max(1 pp, 95% CI); BM25 oracle on the same extension/generation; upgrade and lexical change measured separately; WP-RS.0 stays before WP-RS.2. All thresholds labelled starting points. |
| P2-6 | Deletion-between-rounds expectation reversed | Analysis §8.3 split: deletion committed before the snapshot is absent; deletion committed between fetches stays visible to that invocation and is absent on the next; nomination/confirmation agree and the reported authority time is the pinned instant. |

## Nits

| # | Resolution |
| --- | --- |
| 1 | Fetches are now disjoint cursor batches; the table states the convention (after fetch *j* the cursor delivered 80·(2ʲ − 1) rows) and the `s = 0.05` row needs three fetches (560 rows, ≈ 28 survivors). |
| 2 | `plan/README.md` now says eligibility is kept exact, early stops are reported and semantic recall is measured. |

## Disagreements

None. One point for the owner: the positive-match lexical change (P1-1) alters
which rows the lexical channel and RRF can return. I found no strong reason
against it — the dropped rows carried no keyword signal — but it is a real
behaviour change and is gated separately.

## Also corrected

The round-0 analysis said embedding reuse was absent; E1 already carries
vectors forward across versions (D80). Analysis §7 and the plan reflect this.
