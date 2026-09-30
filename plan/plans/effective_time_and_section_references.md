# Effective time and section references — delivery (D140)

Build order for [the D140 design](../designs/effective_time_and_section_references_design.md).
Architecture lives in the design; this file only sequences it.

**Where the work lands.** Pull request #500 currently contains the design corpus only. By the
owner's decision, the implementation of WP-ET.1–6 is added to **the same pull request #500 as
further commits after the design review approves**, and #500 is reviewed again as a whole
before it merges. Nothing in this table ships until those commits exist and pass. WP-ET.7 and
WP-ET.8 are separate, later pull requests.

| WP | Scope | Design | Depends on | Delivery |
| --- | --- | --- | --- | --- |
| WP-ET.1 | Effective periods: `documents.effective_time`, `document_versions.version_key` (+ unique index), `document_effective_periods` with composite ownership FK; `effective_intervals`, `document_effective_periods_live`, `versions_in_scope`; ingest `effective_from`/`effective_until`/`version_key` (HTTP, SDK, MCP `ingest`, CLI if it exposes ingest flags) with snapshot-only, duplicate-start and version-key rules; `PUT …/effective-periods`, `DELETE …/effective-periods` + SDK `set_effective_periods`/`clear_effective_time` | §2, §3.2 | — | #500, after design approval |
| WP-ET.2 | Time-scoped text retrieval: `time` on chunk/claim search, `claims_and_sources_context`, `search_documents` (scoped `versions: all`, as-of pinning); `chunks_all_versions_live`; `adjacent_chunks` and passage hydration on any non-deleted ready version; version-addressed handles and optional `p3_path` with `served_version`; occurrence-based claim evidence; `effective` on results; `Freshness.scope_pending` | §3 | WP-ET.1 | #500, after design approval |
| WP-ET.3 | Evidence gate for fact reads: in-scope support predicate in `facts_context`, `combined_context`, relation/observation lookups and graph neighbourhood/path; `fact_in_scope_support` SQL function; `facts_current` manifest comment | §8.1 | WP-ET.1 | #500, after design approval — **must ship with WP-ET.2**: periodised text retrieval without the gate would let fact reads answer from an edition that text reads exclude |
| WP-ET.4 | Section keys: trailing heading attribute parsing (new parser generation), `section_key`, `own_content_hash`, `subtree_content_hash` (nullable) with per-version key uniqueness and warnings; deterministic backfill job for existing sections; `section_history` (HTTP, SDK, MCP) with absence rows and paging | §4, §6.2 | WP-ET.1 | #500, after design approval |
| WP-ET.5 | Text origin time: `reuse_identity_hash` (+ index) and `text_origin_at` on chunks; E1 lookup; E2 header, `extraction_input_hash` and fresh claim `asserted_at` use it | §5 | — | #500, after design approval |
| WP-ET.6 | References: `document_crossrefs` extension and `document_reference_sets` with composite FKs; NDJSON `PUT …/references` + `GET …/references` + SDK `set_references`; E0 `crossref` sub-worker validating (all-or-nothing) and materializing supplied sets; late binding on ingest; `document_references` (HTTP, SDK, MCP) with temporal join, statuses and keyset paging; graph source view dedupe; public view columns; D135 visibility and D74 forget handling for the new rows | §6, §7, §9 | WP-ET.4 | #500, after design approval |
| WP-ET.7 | E3 adjudication input: in-force interval sets shown to adjudication; re-adjudication enqueued on declaration changes | §8.2 | WP-ET.1, WP-ET.3, D118 runtime | separate PR — changes E3 prompts/inputs and needs its own review and benchmark check. Correctness of default answers does not depend on it, because WP-ET.3's gate already prevents answers resting on text not in force |
| WP-ET.8 | D36 extraction rungs writing `origin = extracted` rows (links, attachments, replies, citation mining and grammars, fuzzy and small-model resolution) | §6.4, `e0_files_design.md` §4A | WP-ET.6 | separate PR — independent of supplied references; large and model-dependent |

## Migration

The schema change is additive except in three places, each handled explicitly:

1. **New section hashes on a populated table.** `own_content_hash` and `subtree_content_hash`
   are added **nullable**. An idempotent backfill job recomputes them for existing sections from
   each representation's stored `blocks.json` and section block ranges (deterministic, no model,
   no reprocessing). Readers treat `NULL` as unknown (`changed = null`). The columns stay
   nullable in the schema; newly structured sections always write them.
2. **Parser generation rollover.** Section-key parsing bumps `SKELETON_PARSER_VERSION`, which
   is part of `structurer_version` and therefore of the D56 extraction key and the D65
   extraction basis. Existing versions are not re-structured. The first new version of each
   existing lineage after deployment misses reuse once and extracts in full; later versions
   reuse normally. Deployments that want to avoid this spike can schedule it like any other
   extractor or structurer bump.
3. **`document_crossrefs` replacement.** On `main` no code writes the table (only tests insert
   rows), so the migration refuses to run if it holds rows and otherwise recreates it with the
   new columns, enums and foreign keys, then recreates the dependent views and property graph in
   the same transaction (the live-graph migration rule).

`chunks.text_origin_at` and `reuse_identity_hash` are nullable; chunks created before D140 keep
`NULL` and are never matched by the text-origin lookup, so the first dated version after
deployment of each lineage takes its own date (today's behaviour) and later versions reuse.

## Obligations for the implementation commits

- Same-PR public documentation (D66): `website/src/app/docs/**` pages for ingest, search and
  time, the new operations and the query space, and `/docs/project/not-built-yet` listing
  WP-ET.7/8 as not built.
- Tests: period derivation and scope selection (out-of-order declarations, gaps, overlaps,
  retraction of the last period, clear, deletion, belief-instant pinning), version-key rules,
  the evidence gate (future-only, repealed-only, mixed and undeclared support; conflicting claim
  dates), occurrence-based claim hydration after origin deletion, parser key extraction and
  hashes (own vs subtree), backfill, text-origin reuse (unchanged, changed, neighbour-changed,
  deleted origin, toolchain bump), reference validation and materialization, late binding,
  temporal-join resolution (overlap, concurrent targets, pinned, statuses) and paging;
  PostgreSQL integration tests for scoped statements, views and functions; composite-FK
  rejection of cross-lineage rows.
- MCP catalogue changes bump the affected tools' `tool_version` (D136).
