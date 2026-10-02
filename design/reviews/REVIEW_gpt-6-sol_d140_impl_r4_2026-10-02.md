# D140 implementation review — round 4, `d4f8c10d`

Reviewed `git diff origin/main...HEAD` against the approved D140 contract, with the new-commit audit limited to `d28b0a34..HEAD`. WP-ET.7 and WP-ET.8 are outside this PR.

## Closure audit

| Round-3 P1 | Status | Evidence and regression check |
| --- | --- | --- |
| Entity-filtered `claims_and_sources_context` could omit a pending lineage whose entity mention exists only on an older, readable, non-served edition | **Closed** | The new three-edition test at `src/tests/surfaces/test_d140_scoped_retrieval.py:1851-1911` passes on HEAD and fails on an isolated `d28b0a34` archive with `scope_pending=None`. `query_engine.py:446-456,514-576` replays each claim/chunk, semantic/BM25 nomination at the request's `candidate_k` through `nominate_testimony_scored`; `postgres_p1.py:944-987,1694-1700` widens its candidate predicate to readable editions of bounded pending lineages. The shared SQL at `postgres_p1.py:1045-1079` retains survivor resolution, distinct-entity coverage, coverage-first ordering and the same limit. The ordinary answer is built first at `query_engine.py:1491-1499`; probe IDs feed only freshness at `:1500-1531`, so pending picks cannot enter its top-k or evidence. Design §3.7 now records coverage-first probing at `plan/designs/effective_time_and_section_references_design.md:435-439`. |

Validation: `src/tests/surfaces/test_d140_scoped_retrieval.py` passes (31 tests); Ruff on the changed Python files and `git diff d28b0a34..HEAD --check` pass. The 50-million-chunk target remains the accepted follow-up in issue #504.

## P0

None.

## P1

None.

## P2

None new in this round.

## Nits

None.

Verdict: Approve
