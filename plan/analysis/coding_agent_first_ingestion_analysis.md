# Coding-agent-first ingestion of workspace formats

**Status:** non-binding analysis supporting D138. **Date:** 2026-09-27.
**Binding outcome:** [workspace formats design](../designs/workspace_formats_design.md) (D138),
which realizes D133 ([format conversion](../designs/format_conversion_design.md)) for the
families a real workspace contains.

## 1. The question

D133 bound a framework (families, postures, registry) and deliberately left every
individual format to its own design. The first concrete target is the
[Workspace-Bench](workspacebench_benchmark_analysis.md) workspaces: a matched smoke run needs
RememberStack to ingest whole professional workspaces, not a hand-picked subset. What is the
leanest set of families and rules that turns such a workspace into useful memory, and what in
D133 should change once we build it rather than describe it?

## 2. Who reads the result

The consumers are **coding agents** such as Claude Code and Codex. They open files, run Python,
read spreadsheets with pandas or openpyxl, and compute. They do not need memory to hold every
spreadsheet row; they need memory to tell them **which file holds what, where it is, and how it
is shaped**, so they open the right file first and write correct code against it. Prose is
different: the knowledge is the text, and memory should hold it.

This splits the work cleanly:

- **Prose** (documents, slides, email, PDFs with text, notes) → full reading, claims extracted.
- **Data** (spreadsheets, CSV, statistical datasets, large JSON) → a compact deterministic
  profile: sheets, columns, types, counts, and a few head rows, so an agent can write code
  against the file without opening it blind.
- **Code and configuration** → searchable text, but no claim extraction: statements such as
  "the function returns a list" are noise in a fact memory and cost an LLM call per chunk.
- **Opaque bytes** (media, archives, binaries, unknown formats) → a card: the file is findable
  by name, path, type and size; the agent opens it with its own tools.

## 3. The pinned workspace inventory

Read from the central directory of the pinned English archive
(`Workspace-Bench/Workspace-Bench-Workspaces` revision
`e245d63bfa20cfdb708cd8e78145ffb087155857`, `filesys_en.zip`, 18.72 GB) by HTTP range
requests, without downloading the archive: **23,268 files, 24.2 GB uncompressed, 89 distinct
extensions** (the paper's "74 extensions" counts a cleaner set; ours includes dotfiles and
extensionless files).

| Group | Extensions (file count) | Files | Notes |
|---|---|---:|---|
| Spreadsheets | xlsx 6,943; xls 310 | 7,253 | Median xlsx 11.5 KB, p99 4.2 MB, max 29 MB |
| Delimited | csv 433 | 433 | Up to 296 MB (survey microdata) |
| PDF | pdf 2,619 | 2,619 | 8.2 GB; median 1.1 MB, p99 33 MB |
| Word processing | docx 1,378; doc 207; pages 2 | 1,587 | |
| Presentations | pptx 598; ppt 17 | 615 | 4.1 GB; one pptx is 527 MB |
| Plain text / notes | md 2,186; txt 924; markdown 12; log 21 | 3,143 | One txt is a 379 MB data table |
| Code | js 1,002; ts 906; java 357; py 330; css 132; sh 85; sql 26; go 7; scala 2; cjs/mjs/cts 16; others | ~2,870 | Small files |
| Config / markup | json 1,168; yaml/yml 322; xml 19; html 75; svg 26; toml, ini, properties, env-style dotfiles | ~1,700 | JSON max 0.6 MB; jsonl 4 |
| Notebooks / email | ipynb 12; eml 49 | 61 | |
| Images | png 657; jpg/jpeg 416; gif 9; avif 2; psd 2 | 1,086 | |
| Statistical data | xpt 72; sav 41; sas7bdat 1; parquet 3; sas/sps (syntax) 4 | 121 | parquet up to 335 MB |
| Media | wav 16; mp3 4; mp4 4 | 24 | 2.4 GB |
| Archives | zip 9; rar 1 | 10 | 740 MB |
| Binary / other | dat 883 (game saves); extensionless 719 (incl. git LFS objects up to 689 MB); exe, iso, pyc, ttf, dmp, tmp, step, pack/idx | ~1,650 | |

The smoke and development task manifests touch 16 of these extensions (CSV, DOC, DOCX, HTML,
Java, JSON, Markdown, PDF, PNG, PPT, PPTX, Python, TXT, XLS, XLSX, XML). The protocol still
ingests every file, so every extension needs a terminal outcome.

## 4. Cost is the design constraint

Claim extraction (E2) is an LLM call per chunk. Applied naively to this workspace it would run
over ~2,900 code files, ~1,700 config files, 7,253 spreadsheets rendered as tables and 1,650
binaries. Three levers keep it bounded without hiding anything:

1. **Search-only families.** Code, configuration, markup, logs and data profiles are chunked
   and embedded (findable by search) but not claim-extracted. D133 §4.5 already has the
   mechanism (extraction eligibility by `derivation_kind`); this extends it from one section of
   a profile to whole families.
2. **Profiles, not rows.** A 296 MB CSV becomes a few kilobytes of profile. D133's size rule
   (≤200 rows and ≤20,000 characters read in full) stays.
3. **Cards for opaque bytes.** A 689 MB git LFS object costs one tiny card.

## 5. Where building it changes D133

| D133 said | Building it for coding agents shows | D138 does |
|---|---|---|
| A profile's overview is written by one model call per file | 7,253 spreadsheets × one call is real cost, and a coding agent gets more from exact column names and head rows than from a paragraph | No model overview. Profiles are deterministic. The existing E2 pass over the profile's eligible sections (heading, identifying values, formulas) still yields claims about the file. |
| Sample rows are shown to the model and never stored | The agent writing pandas code benefits most from seeing a few real rows | Store a small head sample (≤5 rows per table, cells truncated) in a search-only section. |
| `data_query` runs SQL over normalized Parquet copies | Coding agents compute on the original file directly; the profile's path, sheet and column names are what they need | `data_query` and the Parquet copies are not built now (YAGNI). The D133 design stays the plan if agents without file access need it. |
| Containers expand into child documents | Ten archives in 23k files; expansion needs its own stage, schema and forget rules | Archives get a card that lists their members. Expansion is not built now. |
| Unrecognized bytes are refused (D132) | A workspace connector must account for every file; refusing a game save file drops it from memory and from the audit | Unrecognized bytes get a generic binary card. Refusal stays for bytes that contradict their declared type. |
| One family per converter module | Many families share one reader (all code is text) | One `text` converter serves plain text, Markdown, code, config and logs; family decides eligibility. |
| Image route requires OCR and vision providers | A deployment without those keys would park every image | Without providers, an image gets a card (dimensions, format); with them, the D115 route runs. |

## 6. Legacy Office

`.doc` and `.ppt` (224 files) have no maintained pure-Python reader. LibreOffice in headless mode
converts them to `.docx`/`.pptx`, after which the ordinary readers apply; it is the standard,
deterministic choice and runs locally. `.xls` (310 files) is read directly with `xlrd`, which
markitdown already depends on for its `xls` extra. The cost is image size: LibreOffice adds a few
hundred megabytes to the engine image. A deployment without LibreOffice parks `.doc`/`.ppt`
under D117 until it is installed.

## 7. Alternatives considered

- **Ingest everything as text through markitdown.** Simplest code, but renders every
  spreadsheet as a Markdown table (row-ingestion by another name) and runs claim extraction over
  code.
- **Skip files the benchmark tasks do not name.** Leaks benchmark structure into the memory build
  (forbidden by the Workspace-Bench protocol) and is useless outside the benchmark.
- **Skip `.git` directories and game saves by path rules.** Tempting, but a connector-level
  ignore list is a separate policy decision; cards make them nearly free, so the audit keeps them.
- **Keep the model-written overview for small data files only.** Adds a branch and a cost line
  for little benefit to a coding agent; revisit if search quality on data files proves weak.
