# Workspace formats — coding-agent-first ingestion (Design)

**Status:** D138, accepted 2026-09-27; PDF route superseded by D139; binding when merged.
**Analysis:** [coding-agent-first ingestion](../analysis/coding_agent_first_ingestion_analysis.md).
**Realizes:** D133 ([format conversion](format_conversion_design.md)) for the families a
professional workspace contains; it is the family design set D133 §10 requires for them.
**Refines D133:** deterministic, search-only profiles; cards for unrecognized bytes;
extension-first detection; container expansion and `data_query` leave the system (§8).
**PDF amendment:** D139 requires OCR for every page of every accepted PDF.
**Composes with:** D134 (converters fill general document metadata).

## 1. Principle

Memory is a **map for coding agents**, not a copy of the workspace. The agents that read it
(Claude Code, Codex, similar harnesses) open files and compute on them. For every file, memory
records what it is, where it is, who wrote it and when, and how it is shaped; for prose it also
holds the text. It never ingests spreadsheet rows beyond a five-row sample. An agent that needs
the numbers opens the file; memory's job is to get it to the right file, sheet and column fast.

Every file that arrives is stored and gets a reading, a profile or a card when
conversion succeeds. A PDF above its effective OCR admission limit instead
has a failed version with a typed reason and no card or `document.md` reading.
D117 parking is the interim state for a recognized family whose converter
needs a provider that is not configured or is not yet built; parked files are
stored, listed and resumable. Nothing is silently dropped.

**No model calls outside prose.** Search-only text, profiles and cards run E0's structure step
without model calls: a deterministic section skeleton from headings, no model-written summaries
or roles, and no other LLM stage. Only prose reaches model-written structure (D79) and claim
extraction. Prose includes the provider-backed media readings (D65/D115 transcripts and image
descriptions) when a deployment configures those routes.

## 2. Four outcomes

| Outcome | `document.md` holds | Claims extracted (E2)? |
|---|---|---|
| **prose** | the text | yes |
| **search-only text** | the text | no — chunked and embedded, findable by search |
| **profile** | a deterministic description of a data file (§5) | no |
| **card** | a short description of a file memory does not read (§6) | no |

Search-only uses D133 §4.5's eligibility mechanism: converters label every range with a
`derivation_kind`; the eligibility policy lists the kinds that are never claim-extracted; E1 cuts
chunks where eligibility changes and E2 schedules Selection only for eligible chunks. The
ineligible kinds are `code`, `config`, `log`, `other_text`, `large_text`, `profile_structure`,
`profile_sample`, `file_card` and `pdf_page_status`. Everything else a converter emits is prose.

## 3. Detection and routing

1. **Extension first.** The lower-cased extension selects a family from §4. The compound
   extensions `.tar.gz`, `.tar.bz2`, `.tar.xz` and `.tar.zst` count as `tar`. Extensionless
   files named `Dockerfile`, `Containerfile`, `Makefile`, `Jenkinsfile`, `Procfile`, `LICENSE`,
   `README`, `CODEOWNERS`, `BUILD`, `WORKSPACE`, and dotfiles such as `.gitignore`,
   `.editorconfig`, `.npmrc` and `.env*`, are text.
2. **Declared MIME second.** A declared MIME that is specific (not `application/octet-stream`
   and not a guessed `text/plain`) and known to the registry decides when the extension does not.
3. **Content last.** Otherwise: valid UTF-8 with no NUL byte in the first 64 KiB is
   `other_text` (search-only; an unknown text file is not assumed to be prose); anything else is
   `binary`.

The family's canonical MIME is what E0 stores and routes on. A converter that finds bytes that
do not match its family (a corrupt `.xlsx`, a `.pdf` without a PDF header) fails the version with
a typed reason; it never falls back silently.

**Byte detection (D132), when present,** runs before step 1 and may refuse only a declaration the
bytes contradict (non-PDF bytes declared `.pdf`). Bytes it does not recognize are never refused:
they continue to step 1 and end, at worst, as a `binary` card.

**Routing is the registry, overlaid.** The engine ships the family table below. A deployment's
conversion-route setting **adds or overrides** entries (for example, routing images to the D115
OCR-and-description converter once provider keys exist); it no longer replaces the table.
The PDF invariant survives overlays: a PDF route must OCR every page and cannot
be replaced by text-layer extraction or by a per-page split.

**Size.** Every file is stored. A file larger than its family's reading limit
gets a card that says why, except a PDF. The PDF has one **effective pre-OCR
limit**, the lower of the family reading limit and the configured OCR
provider's input ceiling. Exceeding it leaves the original stored but fails
the version with a typed limit reason, no card and no `document.md` reading;
the check runs before any converter call. If a provider reports a smaller
ceiling only after admission, conversion fails with a typed provider-limit
reason, again without a card. Starting family values: 100 MB for office
documents and PDF, 200 MB for spreadsheets (profiled from sheet dimensions
only above 50 MB). For example, a 100 MB PDF family limit and a 50 MB OCR
provider ceiling give one effective pre-OCR limit of 50 MB.

## 4. The family table

Text-converter families are read in full up to **1 MB**; beyond that, they use
the head/tail profile of §5.2. PDFs use their OCR limit in §3 and are never
sent to a head/tail profile.

| Family | Extensions | Converter | Outcome |
|---|---|---|---|
| `markdown` | md, markdown, mdx, rst, adoc, asciidoc, org, textile | text | prose |
| `text` | txt, srt, vtt, and the named README and LICENSE files | text | prose |
| `other_text` | text found by content only (§3 step 3) | text | search-only |
| `log` | log, out, err, trace | text | search-only |
| `code` | py, pyi, js, jsx, ts, tsx, cjs, mjs, cts, mts, java, kt, kts, scala, go, rs, c, h, cc, cpp, cxx, hh, hpp, hxx, m, mm, cs, fs, rb, php, swift, dart, zig, sol, lua, pl, r, jl, sh, bash, zsh, fish, ps1, bat, cmd, sql, sas, sps, do, gradle, groovy, cmake, tf, hcl, proto, graphql, gql, css, scss, sass, less, vue, svelte, j2, jinja, template, jmx, and the named build files of §3 (not README or LICENSE) | text | search-only |
| `config` | json, jsonc, json5, jsonl, ndjson, geojson, yaml, yml, toml, ini, cfg, conf, cnf, properties, plist, xml, svg, kml, gpx, env-style dotfiles, ignore files | text | search-only |
| `html` | html, htm, xhtml | markitdown | prose |
| `ebook` | epub | markitdown | prose |
| `notebook` | ipynb | notebook | Markdown cells prose; code cells `code`; outputs dropped |
| `email` | eml | email | prose (headers and body; attachments listed by name and size) |
| `word` | docx, docm, dotx, doc, odt, rtf | office (markitdown; LibreOffice converts doc, odt, rtf first) | prose |
| `presentation` | pptx, pptm, ppsx, potx, ppt, odp | office (python-pptx per slide; LibreOffice converts ppt, odp first) | prose, one `page` locator per slide |
| `pdf` | pdf | OCR (every page; provider-backed) | prose, one `page` locator per page |
| `spreadsheet` | xlsx, xlsm, xltx, xls, ods | spreadsheet (openpyxl; xlrd for xls; LibreOffice converts ods first) | profile |
| `delimited` | csv, tsv, psv, tab | table | profile |
| `dataset` | parquet, feather, arrow, sav, por, xpt, sas7bdat, dta, sqlite, sqlite3, db | dataset (pyarrow; pyreadstat; sqlite3 opened read-only) | profile |
| `image` | png, jpg, jpeg, gif, webp, tif, tiff, bmp, heic, avif, psd | card (the D115 route when providers are configured) | card |
| `media` | wav, mp3, m4a, flac, ogg, aac, mp4, mov, webm, mkv, avi | card | card |
| `archive` | zip, tar (and compound tar), 7z, rar, gz, bz2, xz, zst | card | card; member listing for zip and tar |
| `binary` | pages, numbers, key, msg, mbox, rds, rdata, everything else, and unrecognized bytes | card | card |

The table states each family's successful conversion outcome; §3 states the
PDF's typed failure at its effective size limit.

## 5. Profiles

### 5.1 Spreadsheets, delimited files and datasets

Always a profile, never the rows. `document.md` contains, in order:

1. **Heading** — file name, family, byte size, and number of sheets or tables.
   `profile_structure` / `computed`.
2. **Per sheet or table** — its name, its row and column counts, and one line per column with
   its header and a type inferred from the sample rows (`integer`, `number`, `date`, `boolean`,
   `text`, `empty`), at most 100 columns listed with the rest counted, and at most 50 sheets or
   tables described with the rest listed by name only. `profile_structure` / `computed`.
3. **Head sample** — the first 5 data rows as a Markdown table, each cell cut to 80 characters,
   at most 30 columns shown with the rest counted. `profile_sample` / `source_expression`.
4. **Defined names** (spreadsheets) — workbook-level names and their references, at most 50.
   `profile_structure` / `source_expression`.

Counts come from metadata where the format has it (spreadsheet dimensions, Parquet and
statistical-file headers); where it does not (Arrow IPC, SPSS portable, SAS transport), the
profile says the count is not recorded rather than reading every row. A delimited file is
parsed only up to its header and five sample records; its length is an approximate line
count taken from the raw bytes (a quoted value may span lines, so it is not a record
count). No reader loads a whole table into memory; spreadsheet and dataset readers read only
metadata and the sample rows. A spreadsheet over 50 MB, or an `.xlsx` whose shared-strings
table expands past 100 MB, is profiled from its sheet list and dimensions only: no sample,
no column list, and the profile says so. An `.xls` over 10 MB lists its sheet names and
visibility only, because its reader (xlrd) has no bounded row read and loads a whole sheet
to read any row of it. These sizes are starting values. The header row is the first
non-empty row; a cell holding a formula with no cached value counts as non-empty and is
shown as its formula text.

There is no model call and no claim extraction. The profile is found by search (its sheet,
column and file names are all in the text) and by `search_documents`.

### 5.2 Head/tail profile for large text

Any text-converter family (prose text, code, config, logs) over 1 MB gets: line count, byte size, the first
50 lines and the last 20 lines, each line cut to 500 characters. `large_text`, search-only. A
379 MB tab-separated data file whose extension is `.txt`, or a 2 MB JSON export, is described,
not read. PDFs do not use this profile: every PDF at or below its effective
pre-OCR limit is OCR'd in full, even when its byte size exceeds 1 MB.

## 6. Cards

A card is a few lines: file name, source path, family, format, byte size, plus what the format
declares cheaply:

- **image** — width, height and format from the file header (Pillow; pixels are not decoded);
- **archive** — for zip, the member count and up to 200 member paths with sizes from the central
  directory; for tar, the same from a streamed read that stops after 200 members or 64 MB
  scanned, saying the list is partial when it stops; other archive and compressed formats get the
  card without a listing;
- **media, binary, oversized files other than PDFs** — the common fields, and
  for oversized files the reason. A PDF over the effective limit reports a
  typed failed version under §3, with no card or `document.md` reading.

Label `file_card` / `computed`; coverage `policy="card"`, `complete=False`. The original is
stored and served as always (D51); the agent opens it with its own tools.

## 7. Office, PDF, email and notebook details

- **Office metadata and slides.** The office converter reads `docProps/core.xml` from the OOXML
  package itself (with `defusedxml`) for D134 metadata, and reads presentations slide by slide so
  each slide's text gets its own `page` locator. Word documents are rendered with markitdown.
- **LibreOffice** (`soffice --headless --convert-to`) converts doc, odt and rtf to docx, ppt and
  odp to pptx, and ods to xlsx:
  one process per file, a fresh temporary profile directory per call
  (`-env:UserInstallation=file:///<tmp>`), a 120-second limit, no network. A deployment without
  LibreOffice parks those extensions under D117 until it is installed.
- **PDF.** Before OCR, the converter counts source pages from the PDF's
  structural page tree, without reading any text layer. An invalid or
  encrypted PDF whose pages cannot be counted fails with a typed reason and
  no reading. The converter OCRs every counted page, whether the PDF is
  born-digital, scanned or mixed, and renders one page-located reading in
  `document.md`.
  It never inspects a text layer to choose a route, extracts text from that
  layer instead of OCR, or falls back to it after OCR failure. A successful
  OCR page with no visible text is recorded as empty; a failed or unreadable
  page is an explicit coverage gap/failure, never a silently omitted page.
  A successful OCR response with no visible text gets a `## Page N` marker
  in `document.md`, mapped to that page and labeled `pdf_page_status` /
  `computed`, which is extraction-ineligible. If the OCR response omits any
  counted page index, the converter records those indexes in its failure
  details and fails the version without publishing a partial reading. A
  whole-document provider-limit failure also fails the version; neither
  failure produces a card or `document.md` reading.
  The provider requirement is part of the registry entry; without a configured
  OCR provider the file parks under D117. An **accepted PDF page** is a source
  page in a valid PDF whose structural page count is known and that passed
  §3's effective pre-OCR limit. The engine
  records only `scan_page` with quantity equal to that source page count,
  including empty OCR pages and missing response indexes; an admission
  failure has zero accepted pages. Provider-reported `pages_processed` is
  diagnostic evidence, not this quantity. The separate managed cloud maps the same count to one
  `doc-scan` receipt and does not add a `doc-text` charge for the PDF; that
  mapping requires a cloud implementation change. The source map retains one
  `page` locator per page when conversion succeeds, including the empty-page
  status marker, and the
  converter/route version pins the representation for re-conversion.
- **Email.** Python's `email` package; the plain-text part, or HTML converted with markitdown.
- **Notebook.** JSON cells in order; outputs are dropped.

## 8. What changes elsewhere

- **D133 profiles** (§4.2–§4.5) are replaced by §5 here: no model overview, no identifying
  values, no formula tracking, no claim extraction from data files.
- **D133 container expansion** (§5) and **`data_query`** (§4.6) leave the system. Coding agents
  open archives and compute on data files with their own tools. Both designs move to proposals
  with adoption triggers: [container expansion](../proposals/container_expansion.md) and
  [data query](../proposals/data_query.md).
- **D133 detection** (§2.2) is replaced by §3's order; D133's families not in §4 stay the
  registry's direction.
- **D139 PDF route** replaces the text-layer and conditional OCR clauses in the
  earlier D38/D133/D138 contracts; §4 and §7 bind the single OCR route.
- **D134** keeps general metadata, `search_documents` and document filters. Only prose produces
  claims, so D134's self-reference naming applies to prose families.

## 9. Security and limits

- Parsers read untrusted bytes. OOXML package XML is read with `defusedxml`; archives are listed,
  never extracted; LibreOffice runs as in §7.
- Every converter has a wall-time limit (starting value 120 s) and fails the version on timeout.
- No macro, script or embedded object is executed.
- Failures are typed and visible (version `failed` with reason). A card or profile never hides a
  failure as an empty success.

## 10. Tests

Fixtures are generated in the test suite, small and deterministic, per family: detection by
extension, named file, declared MIME and content; the outcome and eligibility labels; profile
fields and caps; the head/tail threshold; card fields and archive listing limits; metadata
mapping; oversized-file cards; corrupt-file failures; LibreOffice conversions when `soffice` is
installed (skipped with a stated reason otherwise). The Workspace-Bench ingestion audit is the
end-to-end check across the real 89-extension workspace.

PDF fixtures include born-digital, scanned and mixed pages; each accepted page
must have an OCR attempt and, on success, a page locator, with no text-layer
bypass on empty OCR or provider failure. Test a 2 MB PDF below the effective
OCR limit: it is OCR'd in full, not head/tail profiled. Test an overlay attempting to route
PDFs to text extraction; it must be rejected. Test a PDF over the effective
pre-OCR limit: the original is stored, the version fails with a typed reason,
and no card or `document.md` reading exists. Test a PDF with a text-layer page,
an empty OCR page and a missing response index: the empty page gets a
page-located, extraction-ineligible status marker; the missing index fails
the version with a typed reason and no partial reading. Engine `scan_page`
quantity is the structural source page count, never provider `pages_processed`;
no engine `doc-scan` appears, and the cloud mapping produces that
same quantity as `doc-scan` without `doc-text` in the separate cloud follow-up.
