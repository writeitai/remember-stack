# Document tags — delivery order

Build order for D156 ([document tags](../designs/document_tags_design.md)). The design
describes the system; this file says what to build first and why.

**Status.** Steps 1, 2 and 4 are implemented: the registry, assignments, all four
surfaces, the D56 reuse proof, the two `memory_v1` views and `examples.facts_by_tag`
(steps 1 and 4), and the `tags` selector on `search` (claims and chunks) and
`search_documents` (step 2). Step 3 (the context operations, and with them the §4.4
fact-evidence rule) is not built yet.

**Step 2 as built.** The selector lives in the shared compiler
`core/document_filters.py`, so `search_documents`, claim and chunk search and the P1
fact eligibility (`search_facts_scored(documents=…)`, which step 3 consumes) share one
predicate. Tags resolve to ids before retrieval and an unregistered tag fails with
`unknown_tag`. With a D140 time scope the filter and the scope now hold on the same
occurrence for claims and on the same supporting occurrence for facts, for every
`documents` filter (D134's version-level fields included), where they were tested
independently before. A completed empty filtered claim/chunk search says whether a live
document matched (§4.5). Surfaces: HTTP and SDK `filters.tags` / `documents.tags`, MCP
`search_documents` `tags` (`tool_version` 4), CLI `--tag` / `--any-tag` / `--not-tag`
on `remember documents search` (the only CLI query command with document filters),
TypeScript parity.

What step 2 did not build, and why: no deployment surface searches relations or
observations with a `documents` filter (only P1's internal `search_facts_scored`
accepts one), so the §4.4 evidence rule — supporting evidence from matching documents,
contradictions and co-members from everywhere marked `in_documents_filter`, two-sided
`evidence_totals` — has no step 2 reader. It is built in step 3 inside the
`facts_context` evidence path, which is where fact evidence is assembled today. The
`entity_ids` combination is proven at the P1 nomination level; no public step 2 read
takes both.

Qualification (step 2): the D142 sweep (`benchmarks/p1_ranked/sweep.py`) gained 1% and
90% tag strata for chunk search and, with `--claims`, for claim search; the measured
numbers and plans are in the step 2 pull request.

Each step merges with its tests and, where it changes user-facing behaviour, its
`website/` docs in the same PR (D66), keeping `/docs/project/not-built-yet` truthful.

1. **Registry and assignments.** `tags` and `document_tags` migrations; the registry
   operations (`list_tags`, `create_tag`, `update_tag`, `delete_tag`) and `unknown_tag`
   strictness; `tags` on ingest (add-only; tags do not change existing version-creation rules,
   including new `version_key` values and re-ingest after deletion);
   `update_document_tags`; all on HTTP, SDK, CLI and MCP; tags returned by
   `GET /documents` and `search_documents`; deletion and hard-forget handling. Prove the
   D56 rule first: the provider-call-counting reuse test shows registry changes, tagging,
   retagging and clearing make no model calls and no versions. Callers can start tagging
   their documents from this step on, before any read filters by tags.
2. **Filter `search` and `search_documents` by tags.** Add `tags` to the existing D134
   filter object and its eligibility path; switch filtered relation/observation evidence to
   the D156 §4.4 rule (supporting from matching documents, contradictions from everywhere,
   two-sided totals). Qualify a 1% and a 90% tag filter on a large retained store.
3. **Filter the context operations.** The `documents` input on
   `claims_and_sources_context@5`, `facts_context@5` and `combined_context@6`, the
   §4.4 evidence marks and two-sided totals, and the empty-filter `known_empty` explanation; manifest roll and MCP
   `tool_version` bumps. This is the step agents feel, so it carries the end-to-end check:
   a memory holding two tagged products answers a product-filtered question with that
   product's support and the other product's contradiction marked out of filter.
4. **SQL views.** `memory_v1.tags_current` and `memory_v1.document_tags_live`, their
   manifest entries and a saved-query example that joins facts to tags through supporting
   claims.

Step 3 depends on step 2's eligibility work; steps 1 and 4 are independent of the
retrieval changes.
