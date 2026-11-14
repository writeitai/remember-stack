# Document tags — delivery order

Build order for D156 ([document tags](../designs/document_tags_design.md)). The design
describes the system; this file says what to build first and why.

**Status.** All four steps are implemented: the registry, assignments, all four
surfaces, the D56 reuse proof, the two `memory_v1` views and `examples.facts_by_tag`
(steps 1 and 4), the `tags` selector on `search` (claims and chunks) and
`search_documents` (step 2), and the `documents` filter on the three context
operations with the §4.4 fact-evidence rule (step 3).

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
on `remember documents search` (at step 2 the only CLI query command with document filters),
TypeScript parity.

Step 2 left the §4.4 evidence rule to step 3, because no step 2 deployment surface
searched relations or observations with a `documents` filter.

**Step 3 as built.** `claims_and_sources_context@5`, `facts_context@5` and
`combined_context@6` take the `documents` object `search` takes (`combined_context`
passes it unchanged to both children); `surface_manifest_hash` rolls, and the MCP
tools rise to `tool_version` 5, 5 and 6 with descriptions telling agents to call
`list_tags` first. The filter is an eligibility input of every existing nomination
channel (global and entity-scoped claim and chunk nomination; facts, including the
entity-profile channel, through `search_facts_scored`, the path a `predicate`
already takes, with step 2's exact tier for a selective filter) and is re-checked at
confirmation, through the step 2 compiler and its fact predicate
(`document_filters.fact_supported_matching`, now shared by P1, confirmation and the
co-member marks). The `facts_context` evidence
statement marks each lineage `in_documents_filter`, ranks matching lineages first
within each stance, keeps supporting rows only from matching lineages and returns the
matching count beside the deployment-wide D54 count; contradiction co-members are
marked the same way. These fields are omitted from unfiltered responses, which are
unchanged apart from the version numbers. A completed empty filtered read carries the
§4.5 explanation. Surfaces: HTTP `POST /operations/{name}` (`unknown_tag` 422),
SDK `documents=` on the three context methods, CLI `--tag` / `--any-tag` /
`--not-tag` on `remember query`, TypeScript parity. The end-to-end check is
`src/tests/surfaces/test_document_tag_context_pg.py`.

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
   filter object in the shared compiler, so claims, chunks, `search_documents` and P1
   fact eligibility share it; hold the filter and a D140 time scope on the same
   occurrence; the §4.5 completed-empty explanation on the reads it touches. Qualify a 1%
   and a 90% tag filter on a large retained store.
3. **Filter the context operations.** The `documents` input on
   `claims_and_sources_context@5`, `facts_context@5` and `combined_context@6`; the D156
   §4.4 fact-evidence rule in the `facts_context` evidence path (supporting evidence from
   matching documents, contradictions and contradiction co-members from everywhere marked
   `in_documents_filter`, two-sided `evidence_totals`), which no step 2 read needs because
   no deployment surface searches relations or observations with a document filter; and
   the empty-filter `known_empty` explanation; manifest roll and MCP
   `tool_version` bumps. This is the step agents feel, so it carries the end-to-end check:
   a memory holding two tagged products answers a product-filtered question with that
   product's support and the other product's contradiction marked out of filter.
4. **SQL views.** `memory_v1.tags_current` and `memory_v1.document_tags_live`, their
   manifest entries and a saved-query example that joins facts to tags through supporting
   claims.

Step 3 depends on step 2's eligibility work; steps 1 and 4 are independent of the
retrieval changes.
