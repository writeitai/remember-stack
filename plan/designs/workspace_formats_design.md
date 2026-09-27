# Workspace formats — coding-agent-first ingestion (Design)

**Status:** D138, accepted 2026-09-27; binding when merged.
**Analysis:** [coding-agent-first ingestion](../analysis/coding_agent_first_ingestion_analysis.md).
**Realizes:** D133 ([format conversion](format_conversion_design.md)) for the families a
professional workspace contains, and is the family design set D133 §10 requires for them.
**Refines D133:** deterministic profiles without a model overview, a stored head sample,
search-only families, cards for unrecognized bytes, and deferral of `data_query` and container
expansion (§8). **Composes with:** D134 (converters fill general document metadata).

## 1. Principle

Memory is a **map for coding agents**, not a copy of the workspace. The agents that read it
(Claude Code, Codex, similar harnesses) open files and compute on them. For every file, memory
records what it is, where it is, who wrote it and when, and how it is shaped; for prose it also
holds the text. It never ingests thousands of spreadsheet rows. An agent that needs the numbers
opens the file; memory's job is to get it to the right file, sheet and column quickly.

Every file that arrives gets a terminal outcome: a reading, a profile, or a card. Nothing is
silently dropped.

## 2. Four outcomes, and whether claims are extracted

| Outcome | `document.md` holds | Claims extracted (E2)? |
|---|---|---|
| **full, prose** | the text | yes |
| **full, search-only** | the text | no — chunked and embedded, findable by search |
| **profile** | a deterministic description of a data file (§5) | only from its heading, identifying values and formulas sections |
| **card** | a short description of a file memory does not read (§6) | no |

"Search-only" uses D133 §4.5's extraction-eligibility mechanism: converters label ranges with a
`derivation_kind`, the eligibility policy lists the kinds that are never claim-extracted, and E1
cuts chunks where eligibility changes. The ineligible kinds are: `code`, `config`, `log`,
`large_text`, `profile_structure`, `profile_sample`, and `file_card`.

## 3. Detection and routing

1. **Extension first.** The file name's extension, lower-cased, selects a family from the
   registry table (§4). Extensionless files named `Dockerfile`, `Makefile`, `Jenkinsfile`,
   `LICENSE`, `README`, `CODEOWNERS`, and dotfiles such as `.gitignore`, `.editorconfig`,
   `.env*` are text.
2. **Declared MIME second.** A declared MIME that is specific (not `application/octet-stream`
   or a guessed `text/plain`) and known to the registry wins over a missing extension.
3. **Content last.** Otherwise: valid UTF-8 with no NUL bytes in the first 64 KiB is plain text;
   anything else is `binary` and gets a card.

The chosen family's canonical MIME is what E0 stores and routes on. A converter that finds the
bytes do not match its family (a corrupt `.xlsx`, a PDF without a PDF header) fails the version
with a typed reason; it never falls back silently. When D132's byte detection ships, it runs
before step 1 and its refusals still apply to declarations that contradict the bytes.

**Routing is the registry, overlaid.** The engine ships the full family table below. A
deployment's conversion-route setting now **adds to or overrides** entries (for example, routing
images to the OCR-and-description converter once provider keys exist); it no longer replaces the
table.

## 4. The family table

Starting values: text read in full up to **1 MB**; beyond that, the `large_text` profile (§5.3).

| Family | Extensions | Converter | Outcome |
|---|---|---|---|
| `markdown` | md, markdown, rst | text | full, prose |
| `text` | txt, and extensionless text | text | full, prose (≤1 MB) / large-text profile |
| `log` | log | text | full, search-only / large-text profile |
| `code` | py, js, jsx, ts, tsx, cjs, mjs, cts, mts, java, kt, scala, go, rs, c, h, cpp, hpp, cc, cs, rb, php, swift, sh, bash, zsh, ps1, bat, sql, r, sas, sps, lua, pl, gradle, groovy, tf, hcl, css, scss, less, vue, svelte, j2, jinja, template, jmx, `Dockerfile`, `Makefile`, `Jenkinsfile` | text | full, search-only |
| `config` | json, jsonl, ndjson, yaml, yml, toml, ini, cfg, conf, properties, env-style dotfiles, xml, svg, editorconfig, gitignore, gitattributes, dockerignore, npmignore | text (JSON/JSONL over 1 MB → structured profile) | full, search-only |
| `html` | html, htm | markitdown | full, prose |
| `notebook` | ipynb | notebook | full: Markdown cells prose, code cells `code`, outputs dropped |
| `email` | eml | email | full, prose (headers, body; attachments listed by name) |
| `word` | docx, doc, odt, rtf | office (markitdown; LibreOffice for doc/odt/rtf) | full, prose |
| `presentation` | pptx, ppt, odp | office (markitdown; LibreOffice for ppt/odp) | full, prose; one `page` locator per slide |
| `pdf` | pdf | pdf (pypdfium2 text layer) | full, prose; one `page` locator per page |
| `spreadsheet` | xlsx, xlsm, xls, ods | spreadsheet (openpyxl; xlrd for xls; LibreOffice for ods) | profile, or full when small |
| `delimited` | csv, tsv | table | profile, or full when small |
| `dataset` | parquet, sav, xpt, sas7bdat, dta | dataset (pyarrow; pyreadstat) | profile |
| `image` | png, jpg, jpeg, gif, webp, tif, tiff, bmp, heic, avif | card (or D115 route when providers are configured) | card |
| `media` | wav, mp3, m4a, flac, ogg, mp4, mov, webm, mkv, avi | card | card |
| `archive` | zip, tar, tgz, gz, 7z, rar | archive card | card listing members (zip, tar) |
| `binary` | everything else, and unrecognized bytes | card | card |

A family's claim extraction and converter are the table; nothing else varies per family.

## 5. Profiles

### 5.1 Spreadsheets, delimited and datasets

A file is read in **full** (as Markdown tables) when every table has at most 200 data rows and
the rendering is at most 20,000 characters (D133 §4.1, unchanged). Otherwise its
`document.md` is, in order:

1. **Heading** — file name, family, size, and (spreadsheets) sheet count.
   `profile_heading` / `source_expression`.
2. **Structure** — per sheet or table: its name, used range or row and column counts, and per
   column the header, inferred type, and non-empty count. `profile_structure` / `computed`.
3. **Head sample** — the first 5 data rows per table as a Markdown table, each cell cut to 80
   characters. `profile_sample` / `source_expression`. This is what lets an agent write the right
   pandas call without opening the file blind.
4. **Identifying values** — D133 §4.4's rules (categorical or identifier-like columns, top
   values occurring at least 3 times, sensitive columns excluded), capped at 10 values per column
   and 100 per file. `profile_values` / `computed`.
5. **Formulas and names** (spreadsheets) — defined names and up to 20 distinct formulas that
   other sheets reference. `profile_formulas` / `source_expression`.

There is **no model call**: the profile is deterministic and cheap. Row counts come from
streaming the file (CSV) or file metadata (Parquet, statistical formats, spreadsheet
dimensions); nothing loads a whole large table into memory. Readers stop after the head sample
plus what counting needs, and a spreadsheet larger than 50 MB is profiled from its sheet list
and dimensions only.

### 5.2 Structured JSON

JSON or JSONL over 1 MB gets a structure profile instead of full text: top-level type, record
count for arrays and JSONL, the key tree to depth 3 with value types, and the first 3 records
pretty-printed and cut to 2,000 characters. `profile_structure` and `profile_sample`.

### 5.3 Large text

Text, logs and code over 1 MB: line count, byte size, the first 50 lines and the last 20 lines.
`large_text`. A 379 MB tab-separated file whose extension is `.txt` is described, not read.

## 6. Cards

A card is a few lines: file name, source path, family, detected format, byte size, and what the
format itself declares cheaply:

- **image** — width, height, format (Pillow header read; the pixels are not decoded);
- **archive** — member count and up to 200 member paths with sizes (zip and tar; 7z and rar get
  the card without a member list);
- **media, binary** — nothing beyond the common fields.

Label `file_card` / `computed`; coverage `policy="card"`, `complete=False`. Cards are
search-only. The original is stored and served as always (D51); the agent opens it with its own
tools.

## 7. Metadata (D134)

Converters fill the general document metadata where the format declares it:

| Family | `title` | `authors` | `recipients` | `created_at` / `modified_at` |
|---|---|---|---|---|
| word, presentation, spreadsheet (OOXML) | core `title` | core `creator` | — | core `created` / `modified` |
| pdf | Info `Title` | Info `Author` | — | Info `CreationDate` / `ModDate` |
| email | Subject | From | To, Cc | Date |
| notebook | first `# ` heading | — | — | — |

`family` is the family name from §4.

## 8. What changes in D133

- **§4.2 overview** — not built. Profiles are deterministic (§5.1); claims about the file still
  come from the eligible heading, values and formulas sections.
- **§4.4 sample rows** — a small head sample is stored (§5.1) in a search-only section.
- **§4.6 `data_query` and Parquet copies** — deferred. Coding agents compute on the original.
  The D133 design is the plan when agents without file access need row-level answers.
- **§5 expand** — deferred. Archives get a member-listing card (§6). Embedded images in
  documents are not extracted as children.
- **§2.1 unrecognized bytes** — get a `binary` card instead of a refusal.
- **§3 registry** — the table in §4 above is the shipped registry; D133 §3's wider target table
  remains the direction for families not listed here.

## 9. Security and limits

- Parsers read untrusted bytes. XML is parsed with `defusedxml`; zip and tar are listed from
  their directories only, never extracted; LibreOffice runs headless with a per-file timeout
  (120 s starting value) in a temporary directory and no network use; every converter has a
  wall-time limit and fails the version on timeout.
- No macro, script or embedded object is executed.
- Converter failures are typed and visible (version `failed` with reason). Cards and profiles
  never hide a failure as an empty success.

## 10. Tests

Each family ships with fixtures generated in the test suite (small, deterministic): detection
by extension and content; the outcome and eligibility labels per family; profile content and
the full-when-small branch; size caps; card fields; metadata mapping; corrupt-file failures; and
legacy Office conversion when LibreOffice is present (skipped with a clear reason otherwise).
The Workspace-Bench ingestion audit (a later smoke step) is the end-to-end check across the real
89-extension workspace.
