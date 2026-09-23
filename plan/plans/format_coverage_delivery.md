# Format coverage — delivery order

Build order for D133 ([format conversion](../designs/format_conversion_design.md))
and D134 ([document subject entities](../designs/document_subject_entity_design.md)).
The designs describe the complete system; this file only says what to build
first and why. Rationale: [analysis](../analysis/format_coverage_and_conversion_architecture.md).

The order follows value per unit of work: the free, local families most
agents upload come before the provider-backed media work that already has the
most design.

1. **Registry and routing.** Format registry with routing-key normalization,
   aliases and the overlay configuration model; detection families beyond
   D132's current list; per-family `max_bytes`, `cost_class`, typed refusal
   for disabled families. Ships the full default route set for existing
   converters. Depends on D132 (PR #452) merging first.
2. **Local full-reading converters with source maps.** Office documents
   (DOCX/PPTX/ODT/ODP/RTF), HTML, e-book, email body, captions, notebooks,
   calendar/contacts, digital-PDF text layer. Install the parser extras.
   Every one emits a real source map.
3. **Profiles and `data_query`.** The `computed` evidence mode; the
   extraction eligibility policy, E1 eligibility boundaries and E2 scheduling;
   spreadsheet, delimited, JSON, columnar/SQLite and log profilers; private
   Parquet query assets; the isolated `data_query` worker; new locator kinds.
4. **Document subject entities (D134).** `DOCUMENT` passage and self card,
   `subject_is_document`, the unique binding with row-locked minting and merge
   guard, `document_metadata` aliases, metadata observations for renames, and
   the forget scrub. Lands with or right after
   profiles, which produce the most self-subject claims.
5. **Expansion.** The `expand` sub-worker, member descriptors, member records
   and suppressions; `counting_lineage_id` on lineages and evidence rows (a
   migration of existing rows to `counting_lineage_id = doc_id`); descendant-
   closure delete and forget; archives; email attachments; mailboxes and
   message exports with the dialogue-transcript converter; embedded images
   through the image route; whole-tree expansion bounds.
6. **File cards** for recognized opaque formats.
7. **Remaining media routes** (audio, video) per `media_design.md`.

Each step updates the docs site in the same PR (D66) and keeps
`/docs/project-status` truthful about which families ship.
