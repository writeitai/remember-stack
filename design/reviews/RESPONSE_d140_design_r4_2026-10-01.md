# D140 design review round 4 — response

Review: [REVIEW_gpt-6-sol_d140_design_r4_2026-10-01.md](REVIEW_gpt-6-sol_d140_design_r4_2026-10-01.md)
(**Approve**; 0 P0, 0 P1, 5 P2, 1 nit). All follow-ups applied before implementation.

| Finding | Resolution |
| --- | --- |
| P2-1 D134 filter-only order | Ordered by the lineage's newest version ingested at or before the cursor's as-of instant, then `doc_id`, independently of the judged edition (`document_metadata_and_search_design.md` §3). |
| P2-2 "per version" key uniqueness | "Per structure generation, read through the current generation" in `e0_files_design.md` §4.1 and the delivery plan WP-ET.4. |
| P2-3 stale schema annotations | `claims.asserted_at` comment points to `chunks.text_origin_at`; the D36 mapping names `document_reference_generations.crossref_version` (`postgres_schema_design.md`). |
| P2-4 period view derivation | The view has period grain and derives from live declarations (`effective_intervals` now); the projection only serves selection (design §2.2). |
| P2-5 fact-gate probe for a past belief | Current belief probes the projection; a supplied past `believed_at` evaluates the fact's bounded support lineages through `versions_in_scope(…, believed_at, doc_ids)` (design §8.1). |
| Nit analysis cost section | Cites the measured row counts (§10) and the stable-order listing path (analysis §6). |
