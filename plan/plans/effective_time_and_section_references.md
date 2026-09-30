# Effective time and section references — delivery (D140)

Build order for [the D140 design](../designs/effective_time_and_section_references_design.md).
Architecture lives in the design; this file only sequences it. Work packages marked
**this PR** are implemented in the same pull request as the design, after the design review
approves; the others are separate pull requests.

| WP | Scope | Design | Depends on | Delivery |
| --- | --- | --- | --- | --- |
| WP-ET.1 | Effective periods: `document_effective_periods` table, `document_effective_periods_live` view, `versions_in_scope` function; ingest `effective_from`/`effective_until` (HTTP, SDK, MCP `ingest`, CLI if it exposes ingest flags) with snapshot-only and duplicate-start rules; `PUT …/effective-periods` + SDK `set_effective_periods` | §2, §3.2 | — | **this PR** |
| WP-ET.2 | Time-scoped retrieval: `time` on `SearchRequest` (chunks, claims), `claims_and_sources_context`, `search_documents`; `chunks_all_versions_live`; `adjacent_chunks` and passage hydration accept any live ready version; `effective` on results; history-mode collapse of identical chunks | §3 | WP-ET.1 | **this PR** |
| WP-ET.3 | Section keys: trailing heading attribute parsing (new parser generation), `section_key` + `section_content_hash` columns, per-version uniqueness with warnings; `section_history` operation (HTTP, SDK, MCP) | §4, §6.2 | WP-ET.1 (periods on output) | **this PR** |
| WP-ET.4 | Text origin time: date-free reuse identity lookup in E1, `chunks.text_origin_at`, E2 header and `extraction_input_hash` use it | §5 | — | **this PR** |
| WP-ET.5 | References: `document_crossrefs` extension (columns, kinds, binding, origin), `document_reference_sets`, `PUT …/references` + SDK `set_references`, E0 `crossref` sub-worker materializing supplied sets, late binding on ingest, `document_references` operation (HTTP, SDK, MCP), graph source view dedupe, public view columns | §6.1–§6.3, §7 | WP-ET.3 | **this PR** |
| WP-ET.6 | Facts from periodised versions: in-force interval set as E3 adjudication input; re-adjudication trigger on period changes | §8 | WP-ET.1, D118 runtime | separate PR — it changes E3 adjudication inputs and needs its own review and benchmark check; text retrieval (WP-ET.1–5) does not depend on it |
| WP-ET.7 | D36 extraction rungs writing `origin = extracted` rows (links, attachments, replies, citation mining and grammars, fuzzy and small-model resolution) | §6.4, `e0_files_design.md` §4A | WP-ET.5 | separate PR — independent of supplied references; large and model-dependent |

**Obligations for every WP in this PR**

- Same-PR public documentation (D66): the `website/src/app/docs/**` pages for ingest,
  search/time, the new operations and the query space, and `/docs/project/not-built-yet`
  updated so WP-ET.6/7 are listed as not built.
- Migrations follow the repository's migration conventions (one new revision chain; the D118
  empty-store rule does not apply because every change is additive or on the never-populated
  `document_crossrefs` table).
- Tests: unit tests for period derivation and scope selection (including out-of-order
  declarations, gaps, overlaps, deletion), parser key extraction, text-origin reuse (unchanged,
  changed, neighbour-changed, deleted-origin cases), reference materialization and late binding;
  PostgreSQL integration tests for the scoped search statements and views.
- The MCP catalogue changes bump the affected tools' `tool_version` (D136).
