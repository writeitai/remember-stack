# Format coverage — delivery order

Build order for D133 ([format conversion](../designs/format_conversion_design.md))
and D134 ([document metadata and search](../designs/document_metadata_and_search_design.md)).
The designs describe the complete system; this file says what to build first
and why. Rationale: [analysis](../analysis/format_coverage_and_conversion_architecture.md).

**One unit at a time.** Every item below is its own unit of work with three
parts, each reviewed and merged in order:

1. **Design.** For a format family, a dedicated family design at
   `plan/designs/formats/<family>_design.md` with the contents
   `format_conversion_design.md` §10.1 requires. A foundation item is
   implemented against the relevant section of D133 or D134 directly; if
   building it reveals a gap, the gap is fixed in that design first.
2. **Implementation.** Exactly what the design says, with the docs site
   updated in the same PR (D66).
3. **Tests.** The family's own suite (§10.3): fixture corpus, detection,
   golden rendering, source map, failures, an end-to-end retrieval check,
   and performance.

A family is not supported, and not listed as supported in
`/docs/project-status`, until all three parts are merged. Until then its
uploads are stored and parked (D117).

The order follows value per unit of work: the free, local families most
agents upload come first; provider-backed media, which already has the most
design, comes last.

## A. Foundations

Each is implemented and tested against the named design section before any
family that depends on it.

| # | Unit | Design | Needed by |
|---|---|---|---|
| A1 | Registry, routing-key normalization, alias table, overlay configuration, typed refusals, parking of unshipped families | D133 §2 | everything |
| A2 | Detection order extended beyond D132 (binary signatures, ZIP and OLE members, text structure tests) | D133 §2.2 | everything; depends on D132 (PR #452) |
| A3 | Source-map requirement for local converters; the four new locator kinds | D133 §3, §7 | B-items |
| A4 | Profile machinery: size rule, `computed` mode, identifying-values rules, extraction eligibility in E1/E2 | D133 §4.1–§4.5 | C-items |
| A5 | Private store and `data_query` (sandboxed worker, `DataQueryResult/v1`) | D133 §4.6 | C-items |
| A6 | General document metadata, `search_documents`, document filters on `search`, self-reference naming in Claimify, own-name rule in E3 | D134 | every family (each maps its metadata) |
| A7 | Expansion: `expand` sub-worker, member records and suppressions, `counting_lineage_id`, descendant-closure delete and forget (manifest v2), whole-tree bounds | D133 §5 | D-items |
| A8 | File cards | D133 §6 | E1 |

## B. Full-reading families (local)

| # | Family | Notes for its design |
|---|---|---|
| B1 | Word processing (DOCX, ODT, RTF, DOC) | Comments, tracked changes, headers/footers; embedded images need A7 |
| B2 | HTML | Boilerplate removal vs faithfulness; scripts and styles |
| B3 | PDF text layer | Per-page choice between text layer and OCR; reading order |
| B4 | Presentation (PPTX, ODP, PPT) | Speaker notes, slide order, grouped shapes |
| B5 | Email message (EML, MSG) | Headers, quoted replies, HTML vs text parts; attachments need A7 |
| B6 | E-book (EPUB) | Chapter structure, footnotes |
| B7 | Captions (SRT, VTT) | Timing to `time` locators |
| B8 | Notebook (IPYNB) | Code vs output cells; image outputs need A7 |
| B9 | Calendar and contacts (ICS, VCF) | One entry per event/contact; recurrence |

## C. Profiled families

| # | Family | Notes for its design |
|---|---|---|
| C1 | Delimited (CSV, TSV) | Dialect and header detection; type inference |
| C2 | Spreadsheet (XLSX, XLSM, XLS, ODS) | Formulas, named ranges, pivots, merged cells, hidden sheets |
| C3 | JSON and NDJSON | Record-array vs object shapes; nested normalization for Parquet |
| C4 | Columnar and database (Parquet, Arrow, SQLite) | Schema, foreign keys, large-file streaming |
| C5 | Log | Supported log grammars; line parsing into tables |
| C6 | Markup (XML, YAML, TOML) | Tree mapping to `json_pointer`; full vs profile |
| C7 | Geo (GeoJSON, KML, GPX) | Geometry statistics; feature properties |

## D. Expanding families

| # | Family | Notes for its design |
|---|---|---|
| D1 | Archive (ZIP, TAR, gzip, 7z) | Path normalization, bounds, nested archives |
| D2 | Email attachments | Built on B5 |
| D3 | Embedded images | Built on B1/B3/B4/B6/B8 and the image route |
| D4 | Mailbox (mbox, PST) | Message splitting, threading |
| D5 | Message exports (ChatGPT, Slack, WhatsApp) | One design per export shape; dialogue transcript rendering |

## E. Cards and media

| # | Family | Notes for its design |
|---|---|---|
| E1 | Opaque recognized formats | Which formats, which metadata each exposes |
| E2 | Scanned PDF OCR | Reconciles the existing Mistral OCR converter with B3 |
| E3 | Images | Existing D115 converter brought under the registry, plus WebP, HEIC, TIFF, GIF |
| E4 | Audio | `media_design.md` §2 |
| E5 | Video | `media_design.md` §2 |
