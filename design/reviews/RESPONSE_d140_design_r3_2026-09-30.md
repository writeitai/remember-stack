# D140 design review round 3 — response

Review: [REVIEW_gpt-6-sol_d140_design_r3_2026-09-30.md](REVIEW_gpt-6-sol_d140_design_r3_2026-09-30.md)
(request changes; 0 P0, 3 P1, 6 P2, 1 nit; ten partially-closed items all tied to these). Each
finding was checked against `origin/main` 1dc23f21. "Design" means
`plan/designs/effective_time_and_section_references_design.md`. Changes are kept minimal.

| Finding | Resolution |
| --- | --- |
| P1-1 pending supplied set ignored | Latest PUT wins: each PUT locks the version row and takes the next per-version `request_seq`; the intent is the newest non-superseded generation; a retry of the intent is a no-op, a PUT equal to the active body cancels a pending one, anything else becomes the new pending generation and supersedes older pending ones; the worker re-checks under the same lock that its generation is still pending before activating (design §6.1, §6.3; schema; plan tests). |
| P1-2 belief-pinned paging over a current-only projection | Page order never depends on belief: filter-only `search_documents` walks D134's lineage order built on immutable ingest times and evaluates scope per candidate batch at the pinned belief instant (projection when still current, ledgers otherwise); scan capped per page. `versions_in_scope` requires `doc_ids` whenever `believed_at` is given, so it never claims to enumerate a past belief (design §3.2, §3.6; `open_query_space_design.md`). |
| P1-3 section-key uniqueness grain | Verified: `p1_04_0019` made sections unique per `(structure_generation_id, node_path)` and `document_catalog.py` writes per generation. Keys are now unique per structure generation; every key lookup uses the version's current representation's current structure generation; the schema sketch gains `structure_generation_id` and the correct path uniqueness (design §4.2; schema §6). |
| P2-1 decision contradicts retry rule | D140 item 1 now states the exact latest-version same-byte retry exception (decisions.md D140). |
| P2-2 schema comments | `document_versions` header and column comments describe the `source_modified_at`, then `published_at` fallback, the latest-version-only no-op, the new-key exception and A→B→A (schema §6). |
| P2-3 "most recently ingested" | Now "newest served (ready) version" (decisions.md D140 context). |
| P2-4 unmeasured 64 MiB bound | Measured on the motivating corpus; method and numbers in analysis §10; the bound in design §6.3 now cites them. |
| P2-5 public view "pinned target version" | Removed; the view lists the target lineage and section key only (`open_query_space_design.md`). |
| P2-6 delivery order in the decision | D140 consequences now state the architectural result only; PR order lives only in the delivery plan. |
| Nit — E0 sentence | Split into two sentences (`e0_files_design.md` §4A). |

Disagreements: none.
