# Format conversion — every family, one contract (Design)

**Status:** D133, accepted 2026-09-23; binding when merged.
**Analysis:** [format coverage and the conversion architecture](../analysis/format_coverage_and_conversion_architecture.md).
**Refines:** D38 (router), D65 (converter contract and locators), D117
(parking scope), D132 (text-flavour routing), D54 (counting identity), D74
(forget inventory). **Composes with:** D134
([document subject entities](document_subject_entity_design.md)).
Numbers below are starting points to measure, not committed constants.

**What this design binds, and what it deliberately does not.** It binds the
*framework* every format goes through: detection, the registry, the four
postures, the profile and expansion machinery, `data_query`, and the new
locators. It does **not** specify how any individual format is parsed or
rendered. The family table in §3 is the **target coverage**, with starting
values. **Each family is delivered on its own, through a dedicated family
design, its own implementation, and its own test suite (§10)**, and a family
is not supported until all three exist. Until then its files are recognized,
stored and parked (D117), never half-converted.

This design is the one home for *which formats the engine accepts and what
it does with each*. [`e0_files_design.md`](e0_files_design.md) keeps the
conversion stage's place in E0; [`media_design.md`](media_design.md) keeps
the audio, video and image routes and the normative locator schema (§4),
which this design extends; [`retrieval_design.md`](retrieval_design.md) §3
lists `data_query` among the primitives.

## 1. The model: family → posture → converter

Every accepted file belongs to a **format family** (a group of formats handled
the same way, e.g. "spreadsheet" covers XLSX, XLS and ODS). Each family has
one **posture** — what the engine produces from it — and a converter that
implements that posture through the D65 contract (`document.md` +
`source_map` + `derived_assets` + `manifest`, extended by §5.1 with member
descriptors for expanding families).

| Posture | What `document.md` holds | Used for |
|---|---|---|
| **full** | A complete reading of the content | Prose and testimony: text, Markdown, HTML, DOCX, PPTX, EPUB, email bodies, PDF, images, audio, video; small structured files (§4.1) |
| **profile** | A description of the file: what it is about, its structure, identifying values, formulas — *not* its rows (§4) | Structured data: spreadsheets, CSV/TSV, large JSON/NDJSON, Parquet/Arrow, SQLite, logs |
| **expand** | A listing of the container's members; each member becomes a **child document** routed on its own (§5) | Archives, mailboxes, message exports, email attachments, images embedded in documents |
| **card** | A short deterministic **file card**: name, detected type, size, embedded metadata the format exposes (§6) | Recognized formats with no reading by design (CAD, fonts, disk images, executables) |

A file can combine postures where the format is both: an email (`.eml`) is
**full** for its headers and body and **expand** for its attachments; a DOCX
with embedded figures is **full** for its text and **expand** for its figures.

## 2. The format registry

One engine-shipped registry replaces the operator-built route table. Each
entry states, for one family: detection (§2.2), canonical MIME types,
posture, converter, provider requirements, `max_bytes`, and an opaque
`cost_class` label. The complete shipped entries are §3. Non-canonical MIME
spellings are mapped by one registry-wide alias table (§3), not per entry. The original's
storage class is not per entry: D132's rule applies to every family (image,
audio and video originals hot; all other originals cold).

### 2.1 Configuration is an overlay

A deployment can turn a family off, select or configure a provider for a
converter that needs one (keys, model), and lower `max_bytes`. It cannot
delete the registry by omission, and setting one route never removes the
others. Outcomes at ingest:

| Situation | Outcome |
|---|---|
| Bytes not recognized as any family | Refused (D132 typed refusal) |
| Family turned off by the deployment | Refused with a typed error naming the family |
| Over the family's `max_bytes` | Refused with a typed error naming the limit |
| Family recognized but not yet shipped (§10) | Stored; conversion parks with `no_route` (D117) until the family ships |
| Family on, converter needs an unconfigured provider | Stored; conversion parks with `no_route` (D117); `resume-no-route` releases it after configuration |
| Family on and ready | Stored and converted |

`cost_class` is a label the metering port receives (`text`, `scan_page`,
`image`, `audio_minute`, `video_minute`, `data_profile`, `archive`, `card`).
The engine never prices; a deployment's metering maps labels to prices
(D61).

### 2.2 Detection and precedence

Detection decides the family from bytes, in this fixed order; the first
match wins. The declared MIME and the file extension are **hints** used only
where a step says so.

1. **Binary signatures (D132's classes, extended).** PDF; images, audio and
   video by signature and ISO BMFF brand (D132); SQLite (`SQLite format 3\0`
   header); Parquet (`PAR1` at both ends); Arrow IPC (`ARROW1`); 7z; gzip
   (then a TAR test on the decompressed head); TAR (`ustar` at offset 257).
2. **ZIP packages by member names**, most specific first: EPUB and ODF
   (a stored `mimetype` member naming the type); OOXML (`[Content_Types].xml`
   plus `word/`, `ppt/` or `xl/` members, D132); message-export ZIP shapes
   (for example a Slack export's `channels.json` and `users.json`). A ZIP
   matching none of these is an **archive**.
3. **OLE containers by stream names:** legacy Word, Excel and PowerPoint
   (D132, with the declared legacy office MIME selecting the subtype), MSG
   (`__properties_version1.0` stream), PST (`!BDN` header). An OLE container
   matching none is refused.
4. **Text.** Strict UTF-8 validation as D132 defines it. Then structural
   tests on the whole file, in order:
   1. **mbox** — the first line starts `From ` and at least one RFC 5322
      header block follows.
   2. **Email message** — an RFC 5322 header block containing `From:` and
      at least one of `Date:`, `Message-ID:`, `Received:`, followed by a
      blank line.
   3. **Captions** — WebVTT (`WEBVTT` first line) or SRT (numbered cues with
      `-->` timestamps in the first cue).
   4. **Calendar / contacts** — `BEGIN:VCALENDAR` or `BEGIN:VCARD`.
   5. **JSON** — the whole file parses as one JSON value; **NDJSON** — every
      non-empty line parses as a JSON value and there are at least two. A
      JSON value with `nbformat` and `cells` keys is a **notebook**; a
      FeatureCollection/Feature with `type` and `geometry` is **GeoJSON**.
      Known export shapes (a list of conversations with message arrays, as
      the message-export converters declare) are **message exports**.
   6. **HTML** — D132's recognizable-markup test.
   7. **XML** — a well-formed document; KML and GPX by root element.
   8. **Delimited** — only when the declared MIME or extension says CSV or
      TSV (a hint): the file parses with that delimiter under RFC 4180
      quoting and every record has the header's column count. A failed test
      falls through.
   9. **Log** — only when the declared MIME or extension says log: at least
      80% of lines match one of the log grammars the log profiler declares.
   10. **YAML / TOML** — only on a declared hint, and the file parses.
   11. Otherwise **Markdown** when declared as Markdown (D132 rendering hint),
       else **plain text**.

A declaration that contradicts a binary class is a D132 refusal. Within the
text class a failed structural test never refuses — it falls through to the
next test, ending at plain text — so the outcome is deterministic for given
bytes and hints.

**This refines D132's text-flavour rule.** D132 routes CSV, JSON and code
with plain text. Under D133 JSON, NDJSON, delimited and log text route to
their families when the structural test passes, and meter under their
family's `cost_class`. Code and other text stay plain text.

**The routing key** is the family's canonical MIME: lower-cased, parameters
removed (`text/plain; charset=utf-8` → `text/plain`), aliases resolved
(`application/x-zip-compressed` → `application/zip`). Container members are
detected again from their own bytes; a member's name is only a hint.

## 3. The target registry

The families the engine is designed to cover. A row becomes a **shipped**
registry entry only when that family's design, implementation and tests are
done (§10); the family design may revise the row's converter, limits and
locators. Detection (§2.2) recognizes every family from the start, so an
upload of a family that has not shipped yet is stored and parked with
`no_route` (D117) and processed once it ships. "Local" means a library
installed with the engine, no provider call. `max_bytes` values are starting
points.

| Family | Canonical MIME types | Posture | Converter (requires) | `max_bytes` | `cost_class` | Primary locator |
|---|---|---|---|---:|---|---|
| Plain text | `text/plain` | full | passthrough (local) | 100 MB | text | `line_range` |
| Markdown | `text/markdown` | full | passthrough (local) | 100 MB | text | `source_range` |
| HTML | `text/html` | full | web document (local) | 50 MB | text | `source_range` |
| Word processing | `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `application/vnd.oasis.opendocument.text`, `application/rtf`, `application/msword` | full + expand (images) | office document (local) | 100 MB | text | `source_range`; `page` where paginated |
| Presentation | `application/vnd.openxmlformats-officedocument.presentationml.presentation`, `application/vnd.oasis.opendocument.presentation`, `application/vnd.ms-powerpoint` | full + expand (images) | office document (local) | 200 MB | text | `page` (slide) |
| E-book | `application/epub+zip` | full + expand (images) | e-book (local) | 100 MB | text | `source_range` |
| PDF | `application/pdf` | full + expand (images) | PDF: text layer (local) per page; OCR (OCR provider) for pages without one | 200 MB | text; `scan_page` per OCR'd page | `page` with region |
| Image | `image/png`, `image/jpeg`, `image/webp`, `image/heic`, `image/tiff`, `image/gif` | full | image OCR + description (OCR and vision providers; D115) | 50 MB | image | `image_region` |
| Audio | `audio/mpeg`, `audio/wav`, `audio/mp4`, `audio/ogg`, `audio/flac` | full | diarized ASR (ASR provider; D65) | 2 GB | audio_minute | `time` |
| Video | `video/mp4`, `video/quicktime`, `video/webm`, `video/x-matroska` | full | ASR + keyframes (ASR and vision providers; D65) | 10 GB | video_minute | `time`, `video_region` |
| Captions | `text/vtt`, `application/x-subrip` | full | caption (local) | 10 MB | text | `time` |
| Email message | `message/rfc822`, `application/vnd.ms-outlook` | full + expand (attachments) | email (local) | 100 MB | text | `source_range` |
| Mailbox | `application/mbox`, `application/vnd.ms-outlook-pst` | expand (messages) | mailbox (local) | 20 GB | archive | member record |
| Message export | `application/vnd.remember.chat-export+json` (ChatGPT `conversations.json` shape), `application/vnd.remember.slack-export+zip` (ZIP with `channels.json` and `users.json`), `text/vnd.remember.whatsapp-chat` (WhatsApp `[date, time] Name: text` lines) | expand (conversations); each child full | message export → dialogue transcript (local) | 5 GB | archive; children text | `json_pointer` / `line_range` |
| Spreadsheet | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `application/vnd.ms-excel.sheet.macroEnabled.12`, `application/vnd.ms-excel`, `application/vnd.oasis.opendocument.spreadsheet` | profile, or full when small | spreadsheet profiler (local + one model call) | 200 MB | data_profile | `sheet_range` |
| Delimited | `text/csv`, `text/tab-separated-values` | profile, or full when small | table profiler (local + one model call) | 5 GB | data_profile | `table_region` |
| JSON | `application/json`, `application/x-ndjson` | profile, or full when small | JSON profiler (local + one model call) | 5 GB | data_profile | `json_pointer` |
| Columnar / database | `application/vnd.apache.parquet`, `application/vnd.apache.arrow.file`, `application/vnd.sqlite3` | profile | table profiler (local + one model call) | 20 GB | data_profile | `table_region` |
| Log | `text/x-log` | profile | log profiler (local + one model call) | 20 GB | data_profile | `line_range` |
| Notebook | `application/x-ipynb+json` | full + expand (image outputs) | notebook (local) | 100 MB | text | `json_pointer` |
| Markup | `application/xml`, `application/yaml`, `application/toml` | profile, or full when small | markup (local + one model call when profiled) | 1 GB | text when full; data_profile when profiled | `json_pointer`-style path |
| Geo | `application/geo+json`, `application/vnd.google-earth.kml+xml`, `application/gpx+xml` | profile | geo profiler (local + one model call) | 5 GB | data_profile | `json_pointer` |
| Calendar / contacts | `text/calendar`, `text/vcard` | full | calendar/contact (local) | 50 MB | text | `source_range` |
| Archive | `application/zip`, `application/x-tar`, `application/gzip`, `application/x-7z-compressed` | expand | archive (local) | 20 GB | archive | member record |
| Opaque, recognized | `application/vnd.remember.card`, with the detected format named in the manifest: DWG/DXF CAD, TrueType/OpenType/WOFF fonts, ISO/VMDK/VHD disk images, ELF/PE/Mach-O executables | card | file card (local) | 20 GB | card | none |

**Aliases.** One registry-wide table maps non-canonical MIME spellings to
the canonical types above; the shipped table is: `application/x-zip-compressed` → `application/zip`;
`text/x-markdown` → `text/markdown`; `application/x-yaml`, `text/yaml` →
`application/yaml`; `text/xml` → `application/xml`; `application/csv` →
`text/csv`; `application/jsonl`, `application/x-jsonlines` →
`application/x-ndjson`; `audio/x-wav` → `audio/wav`; `image/jpg` →
`image/jpeg`; `application/x-sqlite3` → `application/vnd.sqlite3`.

The three message-export shapes are the shipped set. Each export converter
declares its detection test (§2.2 step 4.5 for JSON shapes, step 2 for ZIP
shapes, a line grammar tested after step 4.4 for line shapes); adding a shape
is a registry entry. Where the table lists a type in `vnd.remember.*`, no
registered media type exists and the engine uses its own.

Bytes not recognized as any family are refused by D132. Adding a family is a
registry entry (detection step, row above) plus, where needed, a converter —
never a change to the contract or the pipeline.

**Local by default, with real source maps.** Every family without a provider
requirement works in a stock deployment with no configuration. Office,
e-book and email converters use maintained open-source parsers installed
with the engine (for example markitdown with its `docx`, `pptx`, `xlsx`,
`xls` and `outlook` extras, which the bare package does not include). Each
local converter emits a source map. A range it cannot map is named in
`coverage.gaps`; a converter never reports `complete=True` without a map for
a format that has structure.

## 4. The profile posture

A **profile** tells memory what a data file is, so an agent knows it exists,
what it contains and how to query it, without memory ingesting its rows.

### 4.1 Full or profile: the size rule

For families that allow both, the converter first renders the full reading,
measures it, and keeps it only when it is within **both** bounds; otherwise
it discards it and profiles:

- at most **200 data rows in total** across all sheets or tables (for JSON
  and markup: at most 200 array elements in total across all arrays);
- at most **20,000 characters** of rendered `document.md`.

The bounds are hard: no shape exception makes a large file a full reading.
Rendering stops as soon as either bound is exceeded, so a large file is
never fully rendered. The manifest records the branch taken and the measured
values.

### 4.2 What a profile contains

`document.md` for a profiled file, in this order. Each numbered item is its
own Markdown section, and each section is one labeled range:

1. **Heading** — the file name as the source version names it.
   Label: `profile_heading` / `source_expression`.
2. **Overview** — a short paragraph written by one bounded model call from
   the deterministic profile and a few sample rows: what the data is about,
   its time span, its grain ("one row per order line").
   Label: `profile_overview` / `model_interpretation`.
3. **Structure** — per sheet or table: row and column counts; per column the
   name, inferred type, null rate, distinct count, and minimum/maximum for
   ordered types. Label: `profile_structure` / `computed`.
4. **Identifying values** (§4.4). Label: `profile_values` / `computed`.
5. **Formulas and relations** (spreadsheets, databases) — named ranges,
   defined tables, pivot tables, cross-sheet references, foreign keys, and
   the formulas of cells referenced from other sheets or named ranges
   (headline formulas), capped at 50 (starting value).
   Label: `profile_formulas` / `source_expression`.

The manifest's coverage is `policy="profile"`, `complete=False`, with gaps
naming what is not represented ("rows of `Orders` (48,210) not
represented").

### 4.3 The `computed` evidence mode

`DerivationRange.evidence_mode` gains a fourth value, `computed`: text a
deterministic library derived from source values (counts, ranges, totals,
inferred types). It is neither the source's own words (`source_expression`)
nor a model's output (`model_observation`, `model_interpretation`).
Mediation order for claims crossing ranges (`media_design.md` §5):
`model_interpretation` > `model_observation` > `computed` >
`source_expression`.

### 4.4 Identifying values and what leaves the file

Identifying values let memory link a data file to the people, companies and
products it concerns ("which file has Acme's orders?") without storing rows.

- **Eligible columns:** at most 1,000 distinct values and a distinct-to-row
  ratio of at most 0.2 (categorical), or name- or code-shaped values under
  the same distinct cap (identifier-like). Measure (numeric) and free-text
  columns (median length over 64 characters) are never eligible.
- **Excluded columns:** any column whose name or values match the profiler's
  versioned sensitive-field patterns — email addresses, phone numbers,
  payment card and bank account numbers, government identifiers, and
  names containing `password`, `secret`, `token`, `key`. These contribute
  statistics only.
- **Selection:** the top values by frequency, each occurring in at least 3
  rows, capped at 20 per column and 200 per file. Also date spans and totals
  of summable columns.

These rules limit what is amplified into searchable, claim-extracted text.
They are not access control: a deployment is one trust domain (D50), and the
original is stored and reachable as always (D51).

**Copies and disclosures of the data**, stated exactly:

| Copy | Where | Who reads it |
|---|---|---|
| The original | raw store (D51) | raw mount and `source_open`, audited |
| Normalized tables | private query store (§4.6) | `data_query` only, audited |
| Profile text (no rows) | `document.md` and downstream indexes | all read surfaces |
| Up to 20 sample rows per table | the overview model call | the configured model provider (D61 execution context `provider:<name>` in the manifest) |

The sample rows are not written to `document.md`, chunks, claims, or the
manifest. They are subject to the deployment's existing model-call recording
policy exactly like every other provider call's inputs; a deployment that
records full prompts records them there.

### 4.5 What E2 extracts from a profile

Claims come from `profile_heading`, `profile_overview`, `profile_values` and
`profile_formulas` ranges, and **not** from `profile_structure` ranges.
Structure is chunked, embedded and searchable — it is how a file is found by
a column name — but turning it into claims ("Sheet Q3 has a column Revenue")
would flood the fact layer with schema trivia.

The mechanism:

1. An engine-level, versioned **extraction eligibility policy** maps
   `derivation_kind` to eligible or not. Its only ineligible kind is
   `profile_structure`.
2. **Eligibility changes only at block boundaries.** E1 chunks are runs of
   whole blocks (D57/D58), so the boundary must be one. It is a converter
   output obligation: every section of §4.2 starts with a Markdown heading on
   its own line after a blank line, which the blockizer always treats as a
   block start, and no labeled range of an ineligible kind shares a block with
   an eligible range. The convert stage validates this after blockizing: a
   representation where an eligibility change falls inside a block is a
   converter error (`ConversionError`, not retried), never a silently
   mis-chunked document. E1 then forces a chunk boundary at every block where
   eligibility changes, so a chunk is wholly eligible or wholly ineligible;
   eligibility is stored on the chunk, computed from the labeled ranges and
   the policy version.
3. **E2 schedules Selection only for eligible chunks.** An ineligible chunk
   completes deterministically with zero propositions and publishes no D122
   reference cards, like an empty Selection result; the Selection barrier
   counts it complete.
4. **Reuse (D56):** the policy version joins Selection's reuse basis, so a
   policy change re-extracts exactly the affected chunks.

### 4.6 The queryable copy and `data_query`

At conversion time a profiler writes each table it profiled as a normalized
Parquet file: sheets, CSV, database tables, JSON arrays of records and
parsed log lines. These are **private query assets**, stored in the
deployment's **private store** — a third object-store root beside raw and
artifacts (`e0_files_design.md` §2) that no mount, P3 projection or `hydrate`
depth ever reads — at `<doc_id>/<content_hash>/<representation_id>/<table>.parquet`.
Only the `data_query` worker's staging step reads it. It is purged with the
representation and inventoried by hard forget (§5.4). The manifest lists the
assets with hashes and table names. Staged container members (§5.1) live in
the same store. Parquet gives one typed layout for every
format; the cost is a second stored copy, accepted because the alternative
re-parses the original on every query.

**`data_query` is a direct retrieval primitive**, bound in
[`retrieval_design.md`](retrieval_design.md) §3:

```
data_query(version_id, representation_id?, sql, params?) → envelope (evidence grain) carrying DataQueryResult/v1
```

- **What it runs:** one read-only SQL statement in DuckDB (an in-process
  analytical database) over that representation's tables, exposed as views
  named by table.
- **Isolation.** DuckDB's own guidance is that its settings are defence in
  depth and untrusted SQL needs a sandbox
  (<https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview>,
  retrieved 2026-09-23). So:
  - each query runs in a **separate worker process** with no credentials and
    **no network**: the process runs in an OS network sandbox with no
    interfaces (a network namespace, or a container started without a
    network). A deployment platform that cannot provide one does not enable
    `data_query`; the primitive then answers with a typed `boundary`, never
    with a weaker sandbox. OS resource limits cap CPU time, address space,
    open files and file size;
  - the parent stages copies of only the needed Parquet files into a
    per-query **input directory** the worker can read but not write, and
    creates a separate, empty per-query **scratch directory** for DuckDB's
    temporary files;
  - DuckDB is configured before any agent SQL: `allowed_directories` = the
    input directory, `enable_external_access=false`,
    `autoinstall_known_extensions=false`, `autoload_known_extensions=false`,
    `allow_community_extensions=false`, `temp_directory` = the scratch
    directory with `max_temp_directory_size`, `memory_limit`, `threads`, then
    `lock_configuration=true`;
  - the parent enforces **wall time** by killing the process and cancels on
    caller disconnect; both directories are deleted afterwards.
  - Starting limits: 10 seconds wall time, 1 GiB memory, 2 threads, 1 GiB
    temporary disk, 1,000 returned rows.
- **Result: `DataQueryResult/v1`**, its own contract — not the open query
  space's `QueryResult/v1`, which is bound to PostgreSQL and the `memory_v1`
  schema. Fields: `contract = "DataQueryResult/v1"`; `statement_sha256` (of
  the SQL text exactly as received) and `params`; `columns[]` with name,
  DuckDB type and a portable type (`integer`, `decimal`, `float`, `text`,
  `boolean`, `date`, `timestamp`, `binary`, `nested`); `rows[]`;
  `row_count`; `truncated`; `elapsed_ms`; and `source` = document version,
  representation, and each table read with its asset hash. The D49 envelope
  carries it as the payload of its single evidence item — `data_query`'s own
  envelope binding, not a generic result adapter. Errors are typed:
  `invalid_sql`, `write_rejected` (anything other than one read-only query),
  `timeout`, `resource_limit`, `invalid_parameter`, and `boundary` when the
  document has no query assets (not a profiled family, or conversion
  incomplete) or the platform cannot sandbox the worker.
- **Authorization and audit:** the same authorization path as
  `hydrate depth=bytes` and `source_open`; every call audits principal,
  document version, statement hash and rows returned.
- **Surfaces:** HTTP API, SDK, CLI and MCP (retrieval §7). No mount
  equivalent: mounted agents already have the original on the raw mount.

## 5. The expand posture — child documents

A container's members become **child documents**: ordinary E0 documents,
written through the normal ingest path (ingestion always writes through E0,
D60/D61), detected from their own bytes, routed by the registry, and
processed like any upload.

### 5.1 The expand stage

Expansion is an E0 sub-worker, `expand`, after `convert`:

```
ingest ──► convert ──► expand ──► structure ──► crossref
```

- `convert` returns the parent's own reading plus **member descriptors**
  (`ConversionResult.members`): for each member its member key (§5.2),
  member path, relation (`archive_member`, `attachment`, `message`,
  `conversation`, `embedded_image`), its locator in the parent, its own
  timestamp when the container records one, and its bytes staged in the private store (§4.6) keyed by the parent's
  content hash and the member key.
- `expand` reads the descriptors and, for each member, performs one E0
  ingest write with the child's lineage identity. It is idempotent: the
  member record's primary key and E0's content-hash no-op make a replay a
  no-op.
- **Member records** (schema: `document_members`) link parent version →
  child document version: `(parent_version_id, member_key)` primary key,
  member path, relation, child `doc_id` and `version_id`, parent locator,
  and a per-member status (`ingested`, `skipped` with reason, `failed`).
- **Two records, two lifetimes.** The parent's representation is immutable
  once written (D65). Its `coverage.gaps` names only what *conversion* left
  out, which the bytes fix: members beyond the expansion bounds, unsafe
  paths, decorative images below the floor. What happens to each member
  afterwards is mutable state in `document_members`: `ingested`, `skipped`
  (refused by detection, over its family's limit, or suppressed by §5.4) or
  `failed`, with the reason. Other members proceed when one fails.
- **Readiness:** the parent's representation becomes current when its own
  reading completes; it does not wait for its children. The parent version
  carries a separate expansion status (`pending`, `complete`, `partial`)
  — a scoped readiness fact in the sense of `media_design.md` §4b. Each
  child becomes ready on its own schedule.
- The parent's `document.md` lists its members by name and relation, each
  with a stable **member handle** (`member:<member_key>`) rather than a path
  to a child that may not exist yet. P3 and retrieval resolve a handle through
  `document_members` when they render, so links appear once a child is
  ingested and the parent is never rewritten. The listing does not inline
  member content, so the same text is never extracted twice.

### 5.2 Member identity

A child lineage's identity is `source_kind="container_member"`,
`source_ref="<parent doc_id>:<member key>"` (D55). The member key must be
unique within one parent version and stable across parent versions where
the member is "the same thing":

| Container | Member key |
|---|---|
| Archive | `<normalized path>@sha256:<member hash>`, always |
| Email attachment | `<attachment file name>@sha256:<attachment hash>`, always |
| Mailbox | `sha256:<message bytes hash>`, always (a mailbox message's bytes do not change) |
| Message export | The shape's native conversation identifier, always (each shipped shape has one: ChatGPT conversation `id`, Slack channel ID, and the fixed key `chat` for a single-conversation WhatsApp file) |
| Embedded image | `sha256:<image bytes hash>`, always, with every occurrence's locator on its member record |

Every key has the same form whatever else the container holds: no key uses a
position or ordinal, and none changes shape when a duplicate appears.
Byte-identical repeats collapse to one member because they have the same
key.

Consequences, stated so they are not surprises: renaming **or editing** a
file inside an archive, or an attachment, makes a new child lineage and
retires the old one as absent — archive and attachment members have no
version history of their own, because a key that followed edits would need
the path alone, and a path alone is not unique inside an archive. A growing
chat conversation keeps its lineage and gains versions. Adding a member never
changes another member's key, and an unchanged figure in an edited document
keeps its child lineage.

A child's bytes are the member's exact bytes where the container delimits
them. Where it does not (one conversation inside a chat-export JSON), the
bytes are a canonical serialization of that member, and the member record
says so. A child inherits the parent's `versioning_mode`; its
`source_modified_at` is the member's own timestamp (ZIP entry time, email
`Date`, message timestamp) when the container records one, else the
parent's.

### 5.3 Counting and versions

- **Counting (D54).** A child and its parent are one source for confirmation
  counting. `documents.counting_lineage_id` is written once at lineage
  creation: the lineage's own `doc_id` for a root, the root container's
  `counting_lineage_id` for a child. Evidence rows denormalize it write-once
  like `doc_id`, and D54 counts `COUNT(DISTINCT counting_lineage_id)`
  (`postgres_schema_design.md` §13.1). Relation and observation counts,
  confirmation, reconciliation and projections all use it.
- **Versions (D55/D56).** A new parent version re-expands. A member with the
  same key reuses the existing child lineage and, when its content hash is
  unchanged, the existing child version. Only message-export conversations
  can gain a new child version, because only their keys (native IDs) survive
  a content change; an edited archive member or attachment has a new key and
  is a new child lineage (§5.2). A member absent from the new parent version
  has its child lineage retired as absent.

### 5.4 Forgetting

- **Normal deletion** of a parent tombstones its **descendant closure** —
  every lineage reachable through `document_members` — in the same
  lifecycle operation.
- **Hard forget (D74)** of a parent builds one forget manifest whose
  inventory is the descendant closure, admitted behind one barrier;
  every child is scrubbed like the parent, including private query assets
  and staged member objects.
- **Forgetting one child** records a **member suppression** (schema:
  `document_member_suppressions`: parent `doc_id` and the SHA-256 of the
  member key — never the key itself, which can contain a file name). `expand`
  skips suppressed keys in every later expansion of that parent — including
  re-expansion of an unchanged parent version after restore — and records
  `skipped` with reason `suppressed` on the member record. The suppression is
  content-free (a hash, not a name or bytes) and is carried in the forget manifest, so
  restore replay re-creates it (`hard_forget_design.md` §2).

### 5.5 Bounds

Expansion is bounded against hostile or accidental blow-up (zip bombs,
recursive archives). Bounds apply to the **whole tree under one root**,
not per level: nesting depth at most 4; at most 10,000 members in total;
total expanded bytes at most 10× the root's size and never more than
50 GB. Decompression is streamed and stops when a bound is reached. Member
paths are normalized and rejected when absolute or escaping (`..`).
Anything skipped is named in the relevant parent's `coverage.gaps`.

### 5.6 Embedded images

Images embedded in documents (PDF figures, DOCX/PPTX/EPUB images, notebook
outputs) become `embedded_image` children and run the image route (D115),
so a chart's labels and a diagram's structure become searchable text with a
locator back to the parent's page and region. Decorative images are skipped
below a size floor (starting value: under 64 × 64 pixels or under 4 KiB),
recorded in the parent's coverage. The parent keeps its `media/` copy and
link for display.

### 5.7 Message exports and mailboxes

A mailbox expands into one child per message; a chat export into one child
per conversation. A conversation child converts to a **dialogue
transcript** — speaker turns with timestamps — the same shape
conversational documents already have, so D131's cross-turn extraction
applies unchanged. There is no separate ingestion path for conversations.

## 6. The card posture

A **file card** is a deterministic `document.md` for a recognized format the
engine does not read: a heading with the file name, then detected family and
MIME, size, and the metadata the format itself exposes (a font's family
name, a CAD file's declared units). Labels: `file_card` / `source_expression`
for copied names and metadata strings, `computed` for sizes and counts.
Coverage is `policy="card"`, `complete=False`. The card makes the file
discoverable and gives claims a document to be about (D134); the original is
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

- The converter contract's shape (D65), apart from the member descriptors
  of §5.1: profiles and cards are ordinary `document.md` + source map +
  derived assets + manifest.
- D117 parking for families whose converter needs an unconfigured provider.
- D132's byte classes, refusals and object storage classes; §2.2 extends the
  detection list and refines only the text-flavour rule.
- The media routes and their binding details (`media_design.md` §2).
- Conversion pinning per lineage (D57): changing a family's converter or
  posture is a converter version bump flowing the lifecycle's
  processing-driven rules.

## 9. Documented non-goals

- **Row-level memory for structured data.** Rows are queried with
  `data_query`, not extracted.
- **Log event digests.** Recorded with its adoption trigger in the
  analysis §7.
- **Executing active content.** Macros, scripts in documents, and
  executables are never run; a macro-enabled spreadsheet is profiled from
  its stored values and formula text only.

## 10. Delivering a family: design, implementation, tests

Every family in §3 is delivered as its own unit of work, one family at a
time, in the order of the [delivery plan](../plans/format_coverage_delivery.md).
Each unit has three parts, and the family ships only when all three are
merged.

### 10.1 The family design

A binding document at `plan/designs/formats/<family>_design.md`, written
before implementation, with at least:

1. **Scope.** The exact formats and variants covered (e.g. DOCX but not
   password-protected DOCX; PDF 1.x–2.0; which chat-export versions), and
   what is explicitly out, with the outcome for each excluded case (typed
   refusal, parking, or card).
2. **Parser choice.** The library or service used, the alternatives
   considered and why they lost, licence, maintenance status, and the
   official documentation cited with retrieval date.
3. **Detection.** The exact test for §2.2, its position in the order, and
   the ambiguous cases it must separate.
4. **Rendering.** What `document.md` looks like for this family: headings,
   paragraphs, lists, tables, footnotes, comments, tracked changes, speaker
   notes, headers/footers, hidden content — each either rendered in a stated
   way or listed as a coverage gap. For the profile posture: which statistics,
   which values, which formulas, and how the overview prompt is built.
5. **Source map.** Which locator each rendered range gets, at what precision,
   and what cannot be mapped.
6. **Derivation labels.** Which ranges are `source_expression`, `computed`,
   or model output, and which are extraction-ineligible.
7. **Limits and failures.** Size, page, row, member and time limits; what
   happens on corrupt, truncated, encrypted or oversized input; which
   failures are retryable.
8. **Security.** Parser isolation needs for this format (XML entity
   expansion, zip bombs, macros, embedded scripts, external references),
   and how each is neutralized.
9. **Cost.** Provider calls per file, the `cost_class`, and expected cost for
   typical files.
10. **Test plan** (below).

### 10.2 The implementation

One pull request (or a short series) implementing exactly that design: the
detection test, the converter, the registry row moved from target to
shipped, dependencies pinned, and the docs site updated in the same PR
(D66) including `/docs/project-status`.

### 10.3 The tests

Each family ships with its own suite; a family without it is not shipped:

- **A fixture corpus** of real-world files for the family, including
  variants from different producers (e.g. Word, LibreOffice and Google Docs
  exports), edge cases, and hostile inputs (corrupt, encrypted, oversized,
  zip-bomb, XXE where applicable). Fixtures are small and committed, or
  generated deterministically.
- **Detection tests:** every fixture is classified as this family, and
  near-miss files from other families are not.
- **Golden rendering tests:** each fixture's `document.md`, source map and
  derivation labels match a reviewed expected output.
- **Source-map tests:** sampled ranges resolve to the right place in the
  original (page, slide, cell, line, time).
- **Failure tests:** every failure case in the design produces its stated
  outcome, never a partial representation.
- **An end-to-end retrieval check:** a handful of questions per fixture
  that an agent must answer from memory (and, for profiled families, with
  `data_query`), run through the normal pipeline.
- **Performance:** conversion time and memory for the family's largest
  allowed file stay within the design's limits.

The cross-cutting mechanisms this design binds — the registry and
detection (§2), profiles and extraction eligibility (§4.1–§4.5),
`data_query` (§4.6), expansion (§5), file cards (§6) and the locators (§7) —
are each implemented and tested as their own unit against this design
before the first family that depends on them.
