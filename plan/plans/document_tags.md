# Document tags — delivery order

Build order for D156 ([document tags](../designs/document_tags_design.md)). The design
describes the system; this file says what to build first and why. Nothing is built yet.

Each step merges with its tests and, where it changes user-facing behaviour, its
`website/` docs in the same PR (D66), keeping `/docs/project/not-built-yet` truthful.

1. **Store and write tags.** `document_tags` migration; normalization and limits;
   `tags` on ingest (add-only, no version for identical bytes); `update_document_tags`
   on HTTP, SDK, CLI and MCP; tags returned by `GET /documents` and `search_documents`;
   deletion and hard-forget handling. Prove the D56 rule first: the provider-call-counting
   reuse test shows tagging, retagging and clearing make no model calls and no versions.
   Tags are useful from this step on as a `search_documents` filter.
2. **Filter `search` and `search_documents` by tags.** Add `tags` to the existing D134
   filter object and its eligibility path; switch filtered relation/observation evidence to
   the D156 §4.4 rule (supporting from matching documents, contradictions from everywhere,
   two-sided totals). Qualify a 1% and a 90% tag filter on a large retained store.
3. **Filter the context operations.** The `documents` input on
   `claims_and_sources_context@5`, `facts_context@5` and `combined_context@6`, the
   `documents_filter` envelope block and the empty-filter explanation; manifest roll and MCP
   `tool_version` bumps. This is the step agents feel, so it carries the end-to-end check:
   a memory holding two tagged products answers a product-filtered question with that
   product's support and the other product's contradiction marked out of filter.
4. **SQL view.** `memory_v1.document_tags_live`, its manifest entry and a saved-query example
   that joins facts to tags through supporting claims.

Step 3 depends on step 2's eligibility work; steps 1 and 4 are independent of the
retrieval changes.
