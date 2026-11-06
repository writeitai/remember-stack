# Response to GPT-6 Sol D140 implementation review, round 3

Review: [REVIEW_gpt-6-sol_d140_impl_r3_2026-10-02.md](REVIEW_gpt-6-sol_d140_impl_r3_2026-10-02.md)
(reviewed `d28b0a34`). All round-2 items were closed; the 50-million-chunk target is the
accepted follow-up [#504](https://github.com/writeitai/remember-stack/issues/504).

## P1

| Finding | Resolution | Test |
| --- | --- | --- |
| The pending probe of an entity-filtered `claims_and_sources_context` used `search_*_scored` (current-content mentions), while the real nomination uses `nominate_testimony_scored` (mentions across versions, survivor resolution, coverage-first ranking) | One code path: `nominate_testimony_scored` takes an optional `pending_doc_ids`, which widens its candidate predicate to "selected by the scope **or** in a readable (ready, current-reading) edition of these lineages" and leaves everything else — survivor resolution, mention join, coverage-first ordering, limit — unchanged. The probe replays the request's own nominations (claim and chunk, semantic and BM25, at `candidate_k`, same scope, reusing the query embedding) with the pending lineages; a lineage is named when any of its items makes that top k. Design §3.7 now states the coverage-first case. | `test_an_entity_scoped_context_names_a_lineage_reached_only_on_an_older_edition` (three editions: entity mentioned only on an older readable non-served edition, another edition served, the in-force one converting; fails on `d28b0a34` with `scope_pending = None`) |
