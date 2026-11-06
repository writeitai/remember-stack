# Response to GPT-6 Sol D140 implementation review, round 2

Review: [REVIEW_gpt-6-sol_d140_impl_r2_2026-10-02.md](REVIEW_gpt-6-sol_d140_impl_r2_2026-10-02.md)
(reviewed `2bcb681b`). The accepted judgements (contradiction-only rule, the one-time
re-extraction, the benchmark scope boundary) are unchanged. Each fix has a test that fails on
the reviewed code.

## P1

| Finding | Resolution | Tests |
| --- | --- | --- |
| P1-6 remainder: the pending probe was BM25-over-chunks whatever the request | The probe now replays each nomination the request ran — same grain (chunk or claim), channel (semantic or BM25) and k — over just the pending lineages' readable editions, before the readiness exclusion, reusing the request's query embedding (a per-request memo keyed by deployment and query text). A lineage is named when its score reaches the real nomination's k-th score (any score when it returned fewer than k) and, for BM25, it matches a term. Hits only name lineages and never enter the answer. `search_chunks` and `search_claims` probe their own grain and channel; `claims_and_sources_context` probes its four nominations. Design §3.7 binds the rule. | `test_a_semantic_only_match_names_its_pending_lineage` (no query term in the text; unrelated pending lineage not named), `test_a_claim_text_only_match_names_its_pending_lineage` (terms only in the claim text) |

## P2

| Finding | Resolution | Tests |
| --- | --- | --- |
| 50M-chunk target unmeasured | Tracked in [#504](https://github.com/writeitai/remember-stack/issues/504) (index-driven ranked search, then rerun the full-scale scoped-versus-`main` measurement); the plan links it. | — |
| Tests for the P2-1 tombstone and P2-2 pending fixes | Added. | `test_soft_deleting_a_returned_lineages_newest_version_never_repeats_it`; `test_pinned_pages_keep_reporting_pending_as_known_at_their_belief[correction, clear_redeclare]` (the converting lineage is examined only after the change; each fails with its fix reverted) |

## Nits

- Design §8.1: the later bullet now refers to "the in-scope evidence rule above".
- Analysis: the trailing blank line at EOF is removed (`git diff --check` clean).
