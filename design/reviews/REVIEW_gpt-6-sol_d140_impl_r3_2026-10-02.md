# D140 implementation review — round 3, `d28b0a34`

Compared `origin/main...HEAD` and audited `2bcb681b..HEAD` against the approved D140 contract. WP-ET.7 and WP-ET.8 are outside this PR. The D140 scoped retrieval module passes on HEAD (30 tests); Ruff and `git diff --check origin/main...HEAD` pass.

## Closure audit

| Round-2 item | Status | Evidence and regression check |
| --- | --- | --- |
| P1-6: pending probe must follow grain/channel | **Partially closed** | `src/rememberstack/surfaces/query_engine.py:418-489` probes semantic/BM25 at chunk/claim grain. The new semantic-only test at `src/tests/surfaces/test_d140_scoped_retrieval.py:1712-1723` and claim-text-only test at `:1726-1749` pass on HEAD and both fail on `2bcb681b` (`scope_pending=None`). Entity-filtered compound context still uses a different nomination path; see P1. |
| P2: soft deletion between filter-only pages | **Closed** | `src/tests/surfaces/test_d140_scoped_retrieval.py:1765-1796` asserts a returned lineage never repeats after its newest version is tombstoned. It passes on HEAD and on `2bcb681b`, where the production fix already existed; it fails on pre-fix `c57c723c` with four results for three lineages. |
| P2: pinned pending across correction and clear/redeclare | **Closed** | `src/tests/surfaces/test_d140_scoped_retrieval.py:1799-1844` covers both changes between pages. Both cases pass on HEAD and on `2bcb681b`, where the production fix already existed; both fail on pre-fix `c57c723c` with no pending lineage reported. |
| P2: 50-million-chunk latency target | **Partially closed; accepted follow-up** | `plan/plans/effective_time_and_section_references.md:76-84` discloses that the target was not measured and links [issue #504](https://github.com/writeitai/remember-stack/issues/504), verified open, for index-driven ranking and the full-scale comparison. This remains a measurement follow-up, not a regression test. |
| Nit: contradiction-only evidence wording | **Closed** | `plan/designs/effective_time_and_section_references_design.md:874-877` now points to the in-scope evidence rule above, which includes the contradiction-only case. |
| Nit: trailing blank line | **Closed** | The extra EOF line was removed from `plan/analysis/version_effective_time_and_section_references.md`; `git diff --check origin/main...HEAD` is clean. |

## P0

None.

## P1

- `src/rememberstack/surfaces/query_engine.py:451-489` (and `:1425-1437`): **Entity-filtered compound context can hide a pending lineage.** The real `claims_and_sources_context` nomination uses `nominate_testimony_scored`, which joins resolved mentions from all versions and orders by entity coverage before score (`src/rememberstack/adapters/postgres_p1.py:1019-1051`). The new probe instead calls `search_*_scored` with `entity_ids`; those methods join `memory_v1.mentions_live` (`postgres_p1.py:627-638`, likewise for the other channels), a current-content-only view. A readable older edition with the matching entity mention is therefore invisible to the probe when a different ready edition is served and a third, in-force edition is converting. I reproduced this with three editions: the real history-scoped testimony nomination returned the older chunk, the probe's history-scoped chunk nomination returned zero, and the compound response was empty with `scope_pending=None`. The two new tests use a two-edition fixture whose readable edition is also served (`src/tests/surfaces/test_d140_scoped_retrieval.py:1690-1708`), so they miss it. **Fix:** for entity-filtered context, replay the actual `nominate_testimony_scored` query over a bounded set of pending lineages and their readable editions, preserving survivor resolution and coverage-first ranking; compare against the real nomination cut. Add a three-edition regression test with the entity mention only on the non-served readable edition. Reconcile the score-only wording in design §3.7 (`plan/designs/effective_time_and_section_references_design.md:429-435`) with coverage-first nomination.

## P2

- `plan/plans/effective_time_and_section_references.md:76-84`: The accepted 50-million-chunk latency target remains unmeasured. **Fix:** complete [issue #504](https://github.com/writeitai/remember-stack/issues/504) and record the full-scale scoped-versus-`main` result after ranked search uses the index at that scale.

## Nits

None.

Verdict: Request changes
