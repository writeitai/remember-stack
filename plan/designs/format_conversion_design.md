# Format conversion — every family, one contract (Design)

**Status:** D133, accepted 2026-09-23; binding when merged.

> **Refined by D138 (2026-09-27).** The shipped family set, detection order (§2.2),
> profile content (§4) and cards for unrecognized bytes are bound in
> [`workspace_formats_design.md`](workspace_formats_design.md). Row-level queries and container
> expansion are not part of the system (§4.6, §5); their reviewed designs are proposals.
**Analysis:** [format coverage and the conversion architecture](../analysis/format_coverage_and_conversion_architecture.md).
**Refines:** D38 (router), D65 (converter contract and locators), D117
(parking scope), D132 (text-flavour routing), D54 (counting identity), D74
 **Composes with:** D134
([document metadata and search](document_metadata_and_search_design.md)):
every converter also returns the document's general metadata, and claims
about a document name it.
Numbers below are starting points to measure, not committed constants.

**What this design binds, and what it deliberately does not.** It binds the
*framework* every format goes through: detection, the registry, the three
postures, profile rules, extraction eligibility, and the new locators. It does **not** specify how any individual format is parsed or
rendered. The family table in §3 is the **target coverage**, with starting
values. **Each family is delivered on its own, through a dedicated family
design, its own implementation, and its own test suite (§10)**, and a family
is not supported until all three exist. Until then its files are recognized,
stored and parked (D117), never half-converted.

This design is the one home for *which formats the engine accepts and what
it does with each*. [`e0_files_design.md`](e0_files_design.md) keeps the
conversion stage's place in E0; [`media_design.md`](media_design.md) keeps
the audio, video and image routes and the normative locator schema (§4),
which this design extends.

## 1. The model: family → posture → converter

Every accepted file belongs to a **format family** (a group of formats handled
the same way, e.g. "spreadsheet" covers XLSX, XLS and ODS). Each family has
one **posture** — what the engine produces from it — and a converter that
implements that posture through the D65 contract (`document.md` +
`source_map` + `derived_assets` + `manifest`, extended by D134 with the
document's general metadata).

| Posture | What `document.md` holds | Used for |
|---|---|---|
| **full** | A complete reading of the content | Prose and testimony: text, Markdown, HTML, DOCX, PPTX, EPUB, email bodies, PDF, images, audio, video; small structured files (§4.1) |
| **profile** | A description of the file: what it is about, its structure, identifying values, formulas — *not* its rows (§4) | Structured data: spreadsheets, CSV/TSV, large JSON/NDJSON, Parquet/Arrow, SQLite, logs |
| **card** | A short deterministic **file card**: name, detected type, size, embedded metadata the format exposes (§6) | Recognized formats with no reading by design (CAD, fonts, disk images, executables) |

Containers are not expanded (§5): an email lists its attachments by name, and a document's
embedded images are not extracted.

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

> **Replaced by D138 §3** for the shipped registry: extension first, then declared MIME, then
> content, with D132 refusing only contradictions and unrecognized bytes ending as a card. The
> byte signatures below remain the reference for adding families that D138 does not list.

Detection decides the family from bytes, in this fixed order; the first
match wins. The declared MIME and the file extension are **hints** used only
where a step says so.

1. **Binary signatures (D132's classes, extended).** PDF; images, audio and
   video by signature and ISO BMFF brand (D132); SQLite (`SQLite format 3\0`
   header); Parquet (`PAR1` at both ends); Arrow IPC (`ARROW1`); 7z; gzip
   (then a TAR test on the decompressed head); TAR (`ustar` at offset 257);
   PST mailboxes (`!BDN` file header);
   and the **card** formats: DWG (`AC10` version header), fonts (TrueType
   `00 01 00 00` or `true`, OpenType `OTTO`, WOFF `wOFF`, WOFF2 `wOF2`),
   disk images (ISO 9660 `CD001` at offset 32769, VMDK `KDMV`, VHD
   `conectix`), and executables (ELF `7F 45 4C 46`, PE `MZ` with a `PE\0\0`
   header at the offset it names, Mach-O magic numbers).
2. **ZIP packages by member names**, most specific first: EPUB and ODF
   (a stored `mimetype` member naming the type); OOXML (`[Content_Types].xml`
   plus `word/`, `ppt/` or `xl/` members, D132); message-export ZIP shapes
   (for example a Slack export's `channels.json` and `users.json`). A ZIP
   matching none of these is an **archive**.
3. **OLE containers by stream names:** legacy Word, Excel and PowerPoint
   (D132, with the declared legacy office MIME selecting the subtype), MSG
   (`__properties_version1.0` stream). An OLE container matching none is
   refused.
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
   4c. **RTF** — the file starts with `{\rtf`.
   4a. **Line-shaped message exports** — each shape's declared line grammar
      (for WhatsApp: `[date, time] Name: text`, with continuation lines),
      matched by at least 80% of non-empty lines.
   4b. **DXF (card)** — the text CAD exchange format: a `0` group code line
      followed by `SECTION` at the start of the file.
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

**The order is binding; each family's exact test is its family design's.**
This list fixes which families exist, the order in which they are tried and
the kind of evidence each uses. The precise signature, offsets and grammar
for a family are specified and tested in that family's design (§10.1 item 3)
and must be placed at the position given here; a family design that needs a
different position changes this list first.

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
| Word processing | `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `application/vnd.oasis.opendocument.text`, `application/rtf`, `application/msword` | full | office document (local) | 100 MB | text | `source_range`; `page` where paginated |
| Presentation | `application/vnd.openxmlformats-officedocument.presentationml.presentation`, `application/vnd.oasis.opendocument.presentation`, `application/vnd.ms-powerpoint` | full | office document (local) | 200 MB | text | `page` (slide) |
| E-book | `application/epub+zip` | full | e-book (local) | 100 MB | text | `source_range` |
| PDF | `application/pdf` | full | PDF: text layer (local) per page; OCR (OCR provider) for pages without one | 200 MB | text; `scan_page` per OCR'd page | `page` with region |
| Image | `image/png`, `image/jpeg`, `image/webp`, `image/heic`, `image/tiff`, `image/gif` | full | image OCR + description (OCR and vision providers; D115) | 50 MB | image | `image_region` |
| Audio | `audio/mpeg`, `audio/wav`, `audio/mp4`, `audio/ogg`, `audio/flac` | full | diarized ASR (ASR provider; D65) | 2 GB | audio_minute | `time` |
| Video | `video/mp4`, `video/quicktime`, `video/webm`, `video/x-matroska` | full | ASR + keyframes (ASR and vision providers; D65) | 10 GB | video_minute | `time`, `video_region` |
| Captions | `text/vtt`, `application/x-subrip` | full | caption (local) | 10 MB | text | `time` |
| Email message | `message/rfc822`, `application/vnd.ms-outlook` | full | email (local) | 100 MB | text | `source_range` |
| Mailbox | `application/mbox`, `application/vnd.ms-outlook-pst` | card | mailbox (local) | 20 GB | archive | member record |
| Message export | `application/vnd.remember.chat-export+json` (ChatGPT `conversations.json` shape), `application/vnd.remember.slack-export+zip` (ZIP with `channels.json` and `users.json`), `text/vnd.remember.whatsapp-chat` (WhatsApp `[date, time] Name: text` lines) | card | message export → dialogue transcript (local) | 5 GB | archive; children text | `json_pointer` / `line_range` |
| Spreadsheet | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `application/vnd.ms-excel.sheet.macroEnabled.12`, `application/vnd.ms-excel`, `application/vnd.oasis.opendocument.spreadsheet` | profile, or full when small | spreadsheet profiler (local + one model call) | 200 MB | data_profile | `sheet_range` |
| Delimited | `text/csv`, `text/tab-separated-values` | profile, or full when small | table profiler (local + one model call) | 5 GB | data_profile | `table_region` |
| JSON | `application/json`, `application/x-ndjson` | profile, or full when small | JSON profiler (local + one model call) | 5 GB | data_profile | `json_pointer` |
| Columnar / database | `application/vnd.apache.parquet`, `application/vnd.apache.arrow.file`, `application/vnd.sqlite3` | profile | table profiler (local + one model call) | 20 GB | data_profile | `table_region` |
| Log | `text/x-log` | profile | log profiler (local + one model call) | 20 GB | data_profile | `line_range` |
| Notebook | `application/x-ipynb+json` | full | notebook (local) | 100 MB | text | `json_pointer` |
| Markup | `application/xml`, `application/yaml`, `application/toml` | profile, or full when small | markup (local + one model call when profiled) | 1 GB | text when full; data_profile when profiled | `json_pointer`-style path |
| Geo | `application/geo+json`, `application/vnd.google-earth.kml+xml`, `application/gpx+xml` | profile | geo profiler (local + one model call) | 5 GB | data_profile | `json_pointer` |
| Calendar / contacts | `text/calendar`, `text/vcard` | full | calendar/contact (local) | 50 MB | text | `source_range` |
| Archive | `application/zip`, `application/x-tar`, `application/gzip`, `application/x-7z-compressed` | card | archive (local) | 20 GB | archive | member record |
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
shapes, step 4a for line shapes); adding a shape
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

### 4.1–4.2 Profile content

What a profile contains, and when a data file is profiled, is bound in
[`workspace_formats_design.md`](workspace_formats_design.md) §5 (D138): always a deterministic
profile — structure, a five-row head sample, defined names — with no model call and no claim
extraction.

### 4.3 The `computed` evidence mode

`DerivationRange.evidence_mode` gains a fourth value, `computed`: text a
deterministic library derived from source values (counts, ranges, totals,
inferred types). It is neither the source's own words (`source_expression`)
nor a model's output (`model_observation`, `model_interpretation`).
Mediation order for claims crossing ranges (`media_design.md` §5):
`model_interpretation` > `model_observation` > `computed` >
`source_expression`.

### 4.4 Values that leave the file

Only the five-row head sample leaves a data file (D138 §5). It is part of `document.md`, which
is stored in the deployment like any document; no model sees the rows at conversion time.

### 4.5 What E2 extracts from a profile

Claims come from `profile_heading`, `profile_overview`, `profile_values` and
`profile_formulas` ranges, and **not** from `profile_structure` ranges.
Claims about the file itself name it ("The workbook Q3_sales_2025.xlsx
covers…", D134 §5), so they are searchable by the file's name.
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

### 4.6 Row-level queries

Not part of the system. Coding agents compute on the original file (D138). The reviewed
design for SQL over normalized copies is the [data query proposal](../proposals/data_query.md).

## 5. Containers

Container expansion into child documents is not part of the system. An archive gets a card
listing its members (D138 §6); an agent opens the archive with its own tools. The reviewed
design is the [container expansion proposal](../proposals/container_expansion.md).

## 6. The card posture

A **file card** is a deterministic `document.md` for a recognized format the
engine does not read: a heading with the file name, then detected family and
MIME, size, and the metadata the format itself exposes (a font's family
name, a CAD file's declared units). Labels: `file_card` / `source_expression`
for copied names and metadata strings, `computed` for sizes and counts.
Coverage is `policy="card"`, `complete=False`. The card makes the file
discoverable (its name and metadata feed `search_documents`, D134); the original is
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

- **Row-level memory for structured data.** Coding agents compute on the
  original file; memory holds its profile (D138).
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
  that an agent must answer from memory, run through the normal pipeline.
- **Performance:** conversion time and memory for the family's largest
  allowed file stay within the design's limits.

The cross-cutting mechanisms this design binds — the registry and
detection (§2), profiles and extraction eligibility (§4), file cards (§6)
and the locators (§7) —
are each implemented and tested as their own unit against this design
before the first family that depends on them.
