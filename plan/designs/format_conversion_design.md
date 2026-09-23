# Format conversion — every family, one contract (Design)

**Status:** D133, accepted 2026-09-23; binding when merged.
**Analysis:** [format coverage and the conversion architecture](../analysis/format_coverage_and_conversion_architecture.md).
**Refines:** D38 (router), D65 (converter contract and locators), D117
(parking scope). **Composes with:** D132 (byte-class detection, PR #452) and
D134 ([document subject entities](document_subject_entity_design.md)).
Numbers below are starting points to measure, not committed constants.

This design is the one home for *which formats the engine accepts and what
it does with each*. [`e0_files_design.md`](e0_files_design.md) §3 keeps the
conversion stage's place in E0; [`media_design.md`](media_design.md) keeps
the audio, video and image routes and the normative locator schema (§4),
which this design extends.

## 1. The model: family → posture → converter

Every accepted file belongs to a **format family** (a group of formats handled
the same way, e.g. "spreadsheet" covers XLSX, XLS and ODS). Each family has
one **posture** — what the engine produces from it — and a converter that
implements that posture through the unchanged D65 contract
(`document.md` + `source_map` + `derived_assets` + `manifest`).

| Posture | What `document.md` holds | Used for |
|---|---|---|
| **full** | A complete reading of the content | Prose and testimony: text, Markdown, HTML, DOCX, PPTX, EPUB, email bodies, PDF, images, audio, video; small structured files (§4.1) |
| **profile** | A description of the file: what it is about, its structure, identifying values, formulas — *not* its rows (§4) | Structured data: spreadsheets, CSV/TSV, large JSON/NDJSON, Parquet/Arrow, SQLite, logs |
| **expand** | A listing of the container's members; each member becomes a **child document** that is routed on its own (§5) | Archives (ZIP, TAR, 7z), mailboxes (mbox, PST), message exports, email attachments, images embedded in documents |
| **card** | A short deterministic **file card**: name, detected type, size, embedded metadata the format exposes (§6) | Recognized formats with no reading by design (CAD, disk images, audio plugins, fonts, proprietary binaries) |

A file can combine postures where the format is both: an email (`.eml`) is
**full** for its headers and body and **expand** for its attachments; a DOCX
with embedded figures is **full** for its text and **expand** for its figures.

Postures are chosen per family in the registry (§2), and per file by the
size-and-shape rule where a family allows both **full** and **profile**
(§4.1).

## 2. The format registry

One engine-shipped registry replaces the operator-built route table. Each
entry states, for one family:

| Field | Meaning |
|---|---|
| `family` | Stable name (`spreadsheet`, `delimited`, `json`, `office_document`, …) |
| `detection` | The byte signatures or structural tests that identify it. Detection runs on bytes (D132); the declared MIME and file extension are hints only. |
| `canonical_mimes` | The MIME types the family stores and routes under, plus an alias list (`application/x-zip-compressed` → `application/zip`, `text/x-markdown` → `text/markdown`) |
| `posture` | `full`, `profile`, `expand`, `card`, or a size-and-shape rule choosing between `full` and `profile` |
| `converter` | The converter implementing the posture, by name |
| `requires` | What the converter needs to run: nothing (library-local), or a named provider capability (OCR, vision, ASR) |
| `max_bytes` | The accepted size for this family |
| `cost_class` | An opaque label the metering port receives (`text`, `scan_page`, `image`, `audio_minute`, `video_minute`, `data_profile`, …). The engine never prices; a deployment's metering maps labels to prices (D61). |
| `storage_class` | Hot or cold for the original (D51, D132) |

**Routing key normalization.** The routing key is the stored MIME after D132
detection, lower-cased, with parameters removed (`text/plain;
charset=utf-8` → `text/plain`) and aliases resolved. An exact entry wins; a
family wildcard (`image/*`) applies only when the registry declares one.

**Deployment configuration is an overlay, not a table.** A deployment can:
turn a family off; select or configure a provider for a converter that needs
one (keys, model); and lower `max_bytes`. It cannot delete the registry by
omission. Setting one route never silently removes the others — the failure
mode that left a hosted deployment with three working formats.

**Requirements decide readiness, not acceptance.** A family whose converter
needs an unconfigured provider (PDF OCR without an OCR key) is still
accepted: the original is stored and conversion parks with `no_route`
exactly as D117 defines, and the operator's `resume-no-route` releases it
after configuration. A family the deployment turned off is refused at ingest
with a typed error naming the family — storing something the operator has
explicitly disabled would be surprising. An unrecognized byte class stays a
D132 refusal.

**Detection must cover every family in §3.** D132's class list (PDF, image,
audio, video, office, text) is extended with SQLite (`SQLite format 3\0`),
Parquet (`PAR1` head and tail), Arrow IPC, generic ZIP, TAR, gzip, 7z, mbox
(`From ` line structure), OLE containers beyond office (PST, MSG), and
text-level structure tests for JSON, NDJSON, CSV/TSV and common log formats
(done after the strict UTF-8 check establishes text). A container's members
are detected again from their own bytes; a member's name is only a hint.

## 3. The family table

The shipped registry covers at least these families. "Local" means a library
in the engine's install, no provider call.

| Family | Examples | Posture | Converter (requires) | Primary locator |
|---|---|---|---|---|
| Plain text | `.txt`, source code | full | passthrough (local) | `line_range` |
| Markdown | `.md` | full | passthrough (local) | `source_range` |
| HTML | `.html`, saved pages | full | web document (local) | `source_range` |
| Word processing | DOCX, ODT, RTF, DOC | full + expand (embedded images) | office document (local) | `source_range`; `page` where the format paginates |
| Presentation | PPTX, ODP, PPT | full + expand (embedded images) | office document (local) | `page` (one slide = one page) |
| E-book | EPUB | full | e-book (local) | `source_range` |
| PDF, digital | text-layer PDF | full + expand (embedded images) | PDF text (local) | `page` with region |
| PDF, scanned or mixed | image-only pages | full + expand (embedded images) | OCR (OCR provider) | `page` with region |
| Image | PNG, JPEG, WebP, HEIC, TIFF, GIF (first frame) | full | image OCR + description (OCR and vision providers; D115) | `image_region` |
| Audio | MP3, WAV, M4A, OGG, FLAC | full | diarized ASR (ASR provider; D65) | `time` |
| Video | MP4, MOV, WebM, MKV | full | ASR + keyframes (ASR and vision providers; D65) | `time`, `video_region` |
| Captions | SRT, VTT | full | caption (local) | `time` |
| Email message | EML, MSG | full + expand (attachments) | email (local) | `source_range` |
| Mailbox | mbox, PST | expand (one child per message) | mailbox (local) | member record |
| Message export | Slack/Teams/ChatGPT/WhatsApp exports | expand (one child per conversation) + full per child | message export → dialogue transcript (local) | `json_pointer` / `line_range` |
| Spreadsheet | XLSX, XLSM, XLS, ODS | profile, or full when small | spreadsheet profiler (local + one model call) | `sheet_range` |
| Delimited | CSV, TSV | profile, or full when small | table profiler (local + one model call) | `table_region` |
| JSON | JSON, NDJSON/JSONL | profile, or full when small or document-shaped | JSON profiler (local + one model call) | `json_pointer` |
| Columnar / database | Parquet, Arrow, SQLite | profile | table profiler (local + one model call) | `table_region` |
| Log | syslog, access logs, JSON logs, `.log` | profile | log profiler (local + one model call) | `line_range` |
| Notebook | `.ipynb` | full (cells and text outputs), expand (image outputs) | notebook (local) | `json_pointer` |
| Structured markup | XML, YAML, TOML | full when small, else profile | markup (local) | `json_pointer`-style path |
| Geo | GeoJSON, KML, GPX | profile | geo profiler (local + one model call) | `json_pointer` |
| Calendar / contacts | ICS, VCF | full (one entry per event/contact) | calendar/contact (local) | `source_range` |
| Archive | ZIP, TAR, TGZ, 7z | expand | archive (local) | member record |
| Opaque, recognized | CAD, fonts, disk images, executables, proprietary binaries | card | file card (local) | none |

Formats not listed are refused by D132 until a registry entry recognizes
them. Adding a family is a registry entry plus, where needed, a converter —
never a change to the contract or the pipeline.

**Local by default.** Every family without a provider requirement works in
a stock deployment with no configuration. The office, e-book, email and
notebook converters use maintained open-source parsers installed with the
engine (for example markitdown with its `docx`, `pptx`, `xlsx`, `xls` and
`outlook` extras, which the bare package does not include). Each local
converter emits a real source map; a converter that cannot map a range
leaves it unmapped and says so in `coverage.gaps` — it never claims
`complete=True` with no map for a format that has structure.

## 4. The profile posture

A **profile** tells memory what a data file is, so an agent knows it exists,
what it contains and how to query it, without memory ingesting its rows.

### 4.1 Full or profile: the size-and-shape rule

For families that allow both, a file is read **fully** when it is small
enough that its content *is* the knowledge, and **profiled** otherwise.
Starting values: delimited or spreadsheet data with at most 200 data rows per
table and at most 20,000 rendered characters in total; JSON of at most
32 KiB, or any JSON whose top level is an object rather than an array of
similar records (a config file, a single record). The manifest records which
branch was taken and why.

### 4.2 What a profile contains

`document.md` for a profiled file, in this order:

1. **Heading** — the file name as the source version names it.
2. **Overview** — a short paragraph written by one bounded model call from
   the deterministic profile and a few sample rows: what the data is about,
   its time span, its grain ("one row per order line"). The sample rows are
   model input only; they are never written to `document.md` or stored.
   Label: `profile_overview` / `model_interpretation`.
3. **Structure** — per sheet or table: row and column counts; per column the
   name, inferred type, null rate, distinct count, and minimum/maximum for
   ordered types. Label: `profile_structure` / `computed` (names copied
   verbatim are `source_expression`).
4. **Identifying values** — for columns detected as categorical or
   identifier-like (low distinct-to-row ratio, or name/code-shaped values):
   the top values by frequency, capped (starting value 20 per column,
   200 per file). Also date spans and totals of summable columns. Measure
   and free-text columns never contribute values. Label:
   `profile_values` / `computed`.
5. **Formulas and relations** (spreadsheets, databases) — named ranges,
   defined tables, pivot tables, cross-sheet references, foreign keys, and
   the formulas behind cells that other sheets or summaries reference
   (headline formulas), capped (starting value 50). Label:
   `profile_formulas` / `source_expression`.

The manifest's coverage is `policy="profile"`, `complete=False`, with gaps
naming what is not represented ("rows of `Orders` (48,210) not
represented"). This is the honest statement that the file holds more than
memory read.

### 4.3 The `computed` evidence mode

`DerivationRange.evidence_mode` gains a fourth value, `computed`: text a
deterministic library derived from source values (counts, ranges, totals,
inferred types). It is neither the source's own words (`source_expression`)
nor a model's output (`model_observation`, `model_interpretation`), and
labeling it as either would misstate how it was produced. Everything that
consumes evidence modes treats `computed` as deterministic but not verbatim.

### 4.4 What E2 extracts from a profile

Claim extraction runs on `profile_overview`, `profile_values` and
`profile_formulas` ranges and **not** on `profile_structure` ranges. Structure
is chunked, embedded and searchable — it is how a file is found by a column
name — but turning it into claims ("Sheet Q3 has a column Revenue") would
flood the fact layer with schema trivia. The rule is keyed on
`derivation_kind`, so it holds for every profiler. Claims whose subject is
the file bind to its document entity (D134).

### 4.5 The queryable copy and `data_query`

At conversion time a profiler writes each table it profiled as a normalized
Parquet file, stored as a derived asset of kind `data_table`
(`media/data/<table>.parquet`) with a `table_region` locator naming the
table. JSON arrays of records and parsed log lines are normalized the same
way; SQLite and Parquet originals are normalized table by table. Parquet
gives one typed, fast, format-independent layout for querying; the cost is a
second stored copy of the data, accepted because the alternative re-parses
the original format on every query.

**`data_query` is a direct retrieval primitive** (like `source_open`, D115;
not an assured operation, D87):

```
data_query(version_id, representation_id?, sql, params?) → QueryResult
```

- It runs one read-only SQL statement in DuckDB (an in-process analytical
  engine) against the representation's `data_table` assets, exposed as views
  named by their tables.
- Isolation follows DuckDB's security guidance: attach only that
  representation's tables, then `enable_external_access=false`, a
  `memory_limit` and `threads` cap, and `lock_configuration=true` before any
  agent SQL runs. No file, network or extension access is reachable from the
  query. Starting limits: 10 seconds, 1 GiB memory, 2 threads, 1,000 returned
  rows.
- The result reuses the open-query-space conventions: typed columns, a row
  cap with an explicit `truncated` flag, and provenance naming the
  document version, representation and tables read. Errors use the existing
  sandbox error taxonomy extended with DuckDB-specific causes.
- It is audited like `hydrate depth=bytes` and `source_open`: principal,
  document version, statement hash, rows returned.
- It is exposed on the HTTP API, SDK, CLI and MCP.

A document without `data_table` assets returns a typed `boundary` naming
why (not a structured family, or conversion not complete).

## 5. The expand posture — child documents

A container's members become **child documents**: ordinary E0 documents,
written through the normal ingest path (ingestion always writes through E0,
D60/D61), detected from their own bytes, routed by the registry, and
processed like any upload.

### 5.1 Identity and provenance

- A child lineage's identity is `source_kind="container_member"`,
  `source_ref="<parent doc_id>:<member path>"` (D55). The member path is the
  archive path, the MIME part path of an attachment, the message index in a
  mailbox, or `page-<n>/image-<k>` for an embedded image.
- A child's bytes are the member's exact bytes where the container delimits
  them (a ZIP entry, an attachment, an mbox message, an embedded image
  stream). Where the container has no byte boundary for a member (one
  conversation inside a chat-export JSON), the child's bytes are a canonical
  serialization of that member, and the member record says so.
- A **member record** links parent version → child document version, with
  the member path, relation (`archive_member`, `attachment`, `message`,
  `conversation`, `embedded_image`), and the member's locator in the parent
  (for an embedded image: page and region).
- The parent's `document.md` lists its members with links to their P3
  stubs. It does not inline their content, so the same text is never
  extracted twice.

### 5.2 Counting, versions, forgetting

- **Counting (D54).** A child and its parent are one source for confirmation
  counting: a fact supported by an email and by its attachment counts as one
  witness. The counting identity of a child is its root container's lineage.
- **Versions (D55/D56).** A new parent version re-expands. A member with the
  same member path and the same content hash reuses the existing child
  version; a changed member becomes a new child version; a vanished member's
  child lineage is retired as absent from the new parent version.
- **Forgetting.** Forgetting a parent cascades to all its children. A child
  can be forgotten alone; re-expanding the unchanged parent version does not
  resurrect it.

### 5.3 Bounds

Expansion is bounded against hostile or accidental blow-up (zip bombs,
recursive archives): starting values of nesting depth 4, 10,000 members per
container and 10× the container's size in total expanded bytes. Member paths
are normalized and rejected if absolute or escaping (`..`). Anything skipped
is named in the parent's `coverage.gaps`, never silently dropped.

### 5.4 Embedded images

Images embedded in documents (PDF figures, DOCX/PPTX images, notebook
outputs) become `embedded_image` children and run the image route (D115),
so a chart's labels and a diagram's structure become searchable text with a
locator back to the parent's page and region. Decorative images are skipped
by a size floor (starting value: under 64 × 64 pixels or under 4 KiB), and
the skip is recorded in the parent's coverage. The parent keeps its
`media/` copy and link for display.

### 5.5 Message exports and mailboxes

A mailbox or chat export expands into one child per message (mailbox) or per
conversation (chat export). A conversation child converts to a **dialogue
transcript** — speaker turns with timestamps — the same shape conversational
documents already have, so D131's cross-turn extraction applies unchanged.
There is no separate ingestion path for conversations.

## 6. The card posture

A **file card** is a deterministic `document.md` for a recognized format
the engine does not read: file name, detected family and MIME, size, and the
metadata the format itself exposes (for example a font's family name, an
image's EXIF camera model, a CAD file's declared units). Label: `file_card`
/ `computed` (copied metadata strings are `source_expression`). Coverage is
`policy="card"`, `complete=False`. The card makes the file discoverable and
gives it a document entity to hang claims on (D134); the original is
served as always (D51).

## 7. New locator kinds

Added to the normative union in [`media_design.md`](media_design.md) §4,
same conventions (every variant carries `precision`; the carrier record pins
version and representation):

```
  | { kind: sheet_range,  sheet, range?,                     precision: sheet | range | cell }
  | { kind: table_region, table, column?, row_start?, row_end?,
                                                             precision: table | column | rows }
  | { kind: json_pointer, pointer,                           precision: exact }
  | { kind: line_range,   start_line, end_line,              precision: exact }
```

- `sheet_range.range` is an A1 reference in upper case without the sheet
  prefix (`B2:F40`); `sheet` is the sheet name as stored.
- `table_region.table` is the table name the profiler assigned (the sheet or
  database table name; `data` for a single CSV). Rows are 1-based and
  half-open `[row_start, row_end)`, counted after the header.
- `json_pointer.pointer` is an RFC 6901 JSON Pointer (`/orders/12/total`).
  Markup families (YAML, TOML, XML) use the same syntax over their parsed
  tree, and the manifest names the tree mapping used.
- `line_range` is 1-based and half-open over the source's lines.

## 8. What this does not change

- The converter contract's shape (D65): profiles, cards and expansions are
  ordinary `document.md` + source map + derived assets + manifest.
- D117 parking for families whose converter needs an unconfigured provider.
- D132's byte-class decision and refusal of unrecognized bytes.
- The media routes and their binding details (`media_design.md` §2).
- Conversion pinning per lineage (D57): changing a family's converter or
  posture is a converter version bump flowing the lifecycle's
  processing-driven rules.

## 9. Documented non-goals

- **Row-level memory for structured data.** Rows are queried with
  `data_query`, not extracted.
- **Log event digests.** Not part of the log profiler; recorded with its
  adoption trigger in the analysis §7.
- **Executing active content.** Macros, scripts in documents, and
  executables are never run; a macro-enabled spreadsheet is profiled from
  its stored values and formula text only.
