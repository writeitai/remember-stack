# Format conversion — every family, one contract (Design)

**Status:** D133, accepted 2026-09-23; binding when merged.

> **Refined by D138 (2026-09-27), and D139 for PDFs.** The shipped family set, detection order (§2.2),
> profile content (§4) and cards for unrecognized bytes are bound in
> [`workspace_formats_design.md`](workspace_formats_design.md). Row-level queries and container
> expansion are not part of the system (§4.6, §5); their reviewed designs are proposals.
> Every accepted PDF page goes through OCR, as bound by D139 and the PDF family entry there.
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
values. **Each family is delivered on its own, through a family design (D138
for the families it lists, a dedicated one otherwise), its own implementation,
and its own test suite (§10)**, and a family is not supported until all three
exist. Until then its files are recognized,
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
| **full** | A complete reading of the content — claim-extracted for prose, search-only for code, config and logs | Documents, slides, email, OCR-derived PDF readings, notes; code and configuration |
| **profile** | A deterministic description of a data file — its structure and a few head rows, never its rows (§4) | Spreadsheets, delimited files, datasets; text over the reading limit |
| **card** | A short deterministic **file card**: name, path, detected type, size, and what the format declares cheaply (§6) | Media, archives, images without configured providers, unknown bytes, oversized files |

Which family gets which posture, and which families are search-only, is bound in
[`workspace_formats_design.md`](workspace_formats_design.md) §2 and §4 (D138).

Containers are not expanded (§5): an email lists its attachments by name, and a document's
embedded images are not extracted.

## 2. The format registry

One engine-shipped registry replaces the operator-built route table. Each
entry states, for one family: detection (§2.2), canonical MIME types,
posture, converter, provider requirements, a reading limit, and an opaque
`cost_class` label. The shipped entries are D138's (§3). Non-canonical MIME
spellings are mapped by one registry-wide alias table (§3), not per entry. The original's
storage class is not per entry: D132's rule applies to every family (image,
audio and video originals hot; all other originals cold).

### 2.1 Configuration is an overlay

A deployment can add or override routes (for example, select a provider-backed
converter once its keys exist). It cannot delete the registry by omission, and
setting one route never removes the others. Every file is stored. Outcomes:

| Situation | Outcome |
|---|---|
| Bytes a declaration contradicts (D132) | Refused with a typed error |
| Bytes not recognized as any family | Stored; a `binary` card (D138 §6) |
| Over the family's reading limit | Stored; the head/tail profile for text, otherwise a card stating the reason (D138 §3, §5.2), except PDF: its effective pre-OCR limit is the lower of the family limit and provider input ceiling, and exceeding it fails the version with a typed reason, no card and no `document.md` reading |
| Family recognized but its converter not built | Stored; conversion parks with `no_route` (D117) until it is |
| Family on, converter needs an unconfigured provider | Stored; conversion parks with `no_route` (D117); `resume-no-route` releases it after configuration |
| Family on and ready | Stored and converted |

`cost_class` is a label the metering port receives (`text`, `scan_page`,
`image`, `audio_minute`, `video_minute`, `data_profile`, `archive`, `card`).
The engine never prices; a deployment's metering maps labels to prices
(D61). An accepted PDF page is a source page in a valid PDF admitted under
the effective pre-OCR limit; admission failures contribute zero. The PDF
family records only `scan_page` with quantity equal to that source page count,
including text-layer pages, empty OCR pages and response gaps. A provider's
`pages_processed` field is diagnostic rather than the billable quantity. The
separate managed cloud maps the same count to `doc-scan`, without also
charging `doc-text`; implementing that receipt mapping belongs to the cloud.

### 2.2 Detection and precedence

> **Replaced by D138 §3** for the shipped registry: extension first, then declared MIME, then
> content, with D132 refusing only contradictions and unrecognized bytes ending as a card. The
> byte signatures below remain the reference for adding families that D138 does not list.

The list below is **not** a detection order. It is a catalogue of byte signatures and
structural tests a family design may use to recognize or validate its format.

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
   an unrecognized format and gets a `binary` card (D138).
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

The detection order is D138 §3's. The exact signature a family uses is specified and tested in
its family design (§10.1 item 3). A declaration the bytes contradict is a D132 refusal.

**This refines D132's text-flavour rule.** D132 routes CSV, JSON and code
with plain text. Under D133 JSON, NDJSON, delimited and log text route to
their families when the structural test passes, and meter under their
family's `cost_class`. Code and other text stay plain text.

**The routing key** is the family's canonical MIME: lower-cased, parameters
removed (`text/plain; charset=utf-8` → `text/plain`), aliases resolved
(`application/x-zip-compressed` → `application/zip`).

## 3. The shipped registry

The shipped family table — extensions, converters and outcomes — is
[`workspace_formats_design.md`](workspace_formats_design.md) §4 (D138). A new family is added by
a family design (§10) that extends that table in the same shape: extensions, converter, and
whether it is prose, search-only, a profile or a card.

## 4. The profile posture

A **profile** tells memory what a data file is, so an agent knows it exists,
what it contains and how to query it, without memory ingesting its rows.

### 4.1–4.2 Profile content

What a profile contains, and when a data file is profiled, is bound in
[`workspace_formats_design.md`](workspace_formats_design.md) §5 (D138): always a deterministic
profile — structure, a five-row head sample (none for spreadsheets over 50 MB), defined names —
with no model call and no claim
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

### 4.5 Extraction eligibility

Claim extraction runs only on prose. A versioned **extraction eligibility policy** lists the
`derivation_kind`s that are never claim-extracted; D138 §2 names them (code, config, logs, large
text, every profile section, cards). Those ranges are chunked, embedded and searchable.

The mechanism:

1. **Eligibility changes only at block boundaries.** E1 chunks are runs of whole blocks (D57/D58).
   Converters start every differently labelled section with a Markdown heading on its own line
   after a blank line, which the blockizer treats as a block start; the convert stage validates
   that no eligibility change falls inside a block and fails the conversion if one does.
2. **E1 forces a chunk boundary** at every block where eligibility changes and stores
   `chunks.extraction_eligible`, computed from the labelled ranges and the policy version.
3. **E2 schedules Selection only for eligible chunks.** An ineligible chunk completes
   deterministically with zero propositions and publishes no D122 reference cards; the Selection
   barrier counts it complete.
4. **Reuse (D56):** the policy version joins Selection's reuse basis.

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
served as always (D51). A PDF above its effective pre-OCR limit leaves the
original stored and its version failed with a typed reason; it receives no
card or `document.md` reading.

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

- The converter contract's shape (D65): profiles and cards are ordinary
  `document.md` + source map + derived assets + manifest.
- D117 parking for families whose converter needs an unconfigured provider.
- The PDF family's mandatory OCR route (D139): every accepted page, including
  one with a text layer, passes through OCR; deployment overlays cannot bypass it.
- D132's byte classes and object storage classes, with its refusals limited to
  declarations the bytes contradict (D138 §3).
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
  executables are never run; a macro-enabled spreadsheet is profiled like
  any other (D138 §5), without running its macros.

## 10. Delivering a family: design, implementation, tests

**D138 is the family design set** for every family in its §4 table: its per-family table and
sections cover the §10.1 contents for them. Each of those families still ships with the §10.3
tests. A family outside D138 needs its own family design as described here.

A family **outside** D138 is delivered as its own unit of work, in the order
of the [delivery plan](../plans/format_coverage_delivery.md), with the three
parts below; it ships only when all three are merged.

### 10.1 The family design

A binding document at `plan/designs/formats/<family>_design.md`, written
before implementation, with at least:

1. **Scope.** The exact formats and variants covered (e.g. DOCX but not
   password-protected DOCX; PDF 1.x–2.0; which chat-export versions), and
   what is explicitly out, with the outcome for each excluded case (parking
   or card; refusal only for a declaration the bytes contradict).
2. **Parser choice.** The library or service used, the alternatives
   considered and why they lost, licence, maintenance status, and the
   official documentation cited with retrieval date.
3. **Detection.** The exact test for §2.2, its position in the order, and
   the ambiguous cases it must separate.
4. **Rendering.** What `document.md` looks like for this family: headings,
   paragraphs, lists, tables, footnotes, comments, tracked changes, speaker
   notes, headers/footers, hidden content — each either rendered in a stated
   way or listed as a coverage gap. For the profile posture: which structure
   fields and sample rows, within D138 §5.
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
