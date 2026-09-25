# Format coverage and the conversion architecture

**Status:** non-binding analysis supporting D133 and D134.
**Date:** 2026-09-23. **Evidence inspected:** engine `origin/main` `81f292e4`;
open PR #452 (proposed D132, byte-class detection); installed `markitdown`
0.1.6 package metadata; DuckDB security documentation (cited in §6).
**Binding outcome:** [format conversion design](../designs/format_conversion_design.md)
(D133) and [document metadata and search](../designs/document_metadata_and_search_design.md)
(D134).

## 1. The question

Can the conversion architecture we have — one converter contract (D38, D57,
D65), per-deployment routes, stored originals (D51/D117) — take *any* format
an agent is likely to be handed (office files, spreadsheets, JSON, CSV,
logs, archives, email, special-purpose formats), and what has to change so it
does? A second, narrower question fell out of the answer: when memory
records something *about a file* ("the Q3 sales workbook covers EU revenue
by region"), how does that claim point at the file?

"Wrong" here is expensive in two directions. If the architecture cannot
represent a format family, every later converter becomes a special case
that bypasses the contract. If it represents a family *badly* — for example
running claim extraction over every row of a 50,000-row spreadsheet — the
system pays model cost proportional to data volume and still cannot answer
the questions people ask of that data.

## 2. What the engine does today (code, not design)

### 2.1 What routes exist

| Fact | Where |
|---|---|
| The stock route table is two entries: `text/markdown` and `text/plain` → `passthrough`. | `core/conversion.py:29-32` |
| Setting `REMEMBERSTACK_SELFHOST_CONVERSION_ROUTES` **replaces** the table; it does not extend it. | `profiles/selfhost.py:152-158, 266-275` |
| Four converter names exist: `passthrough`, `markitdown`, `mistral_ocr`, `image_ocr_description`. | `adapters/converters/__init__.py:73-78` |
| Routing is an exact dictionary lookup on the declared MIME string: no content sniffing, no parameter stripping (`text/plain; charset=utf-8` misses), no family (`image/*`) routes, no fallback chain. | `core/conversion.py` `ConversionRouter.converter_for` |
| An unrouted MIME is stored and its conversion parked with `no_route` (D117), resumable with `remember ops resume-no-route`. | `spine/document_catalog.py:99-103`, `workers/e0.py` |
| CLI/SDK guess MIME from the extension (`mimetypes.guess_type`), falling back to `application/octet-stream`. | `remember/client.py:807-824` |
| The watched-directory connector only picks up `.md`, `.txt`, `.html`. | `adapters/selfhost/watcher.py:16,38` |

So out of the box the engine turns exactly two formats into memory. PDF and
PNG/JPEG work only when an operator adds routes *and* provider keys; HTML only
after an opt-in route.

### 2.2 Where the engineering effort sits

| Converter | Lines | Emits a source map | Formats |
|---|---:|---|---|
| `image_ocr_description.py` | 1,092 | yes (whole-image locators) | PNG, JPEG |
| `mistral_ocr.py` | 579 | yes (page, region) | PDF, document images |
| `markitdown.py` | **68** | **no** (`source_map=None`, labels everything `source_expression`, `complete=True`) | nominally office, HTML, email, CSV, JSON |

The markitdown route is the one that would carry most real uploads, and it is
both the thinnest and — as installed — mostly non-functional: the engine
depends on bare `markitdown>=0.1.6` (`pyproject.toml:79`). The package's own
metadata declares format support as optional extras: `[docx]` (mammoth, lxml),
`[pptx]` (python-pptx), `[xlsx]` (openpyxl, pandas), `[xls]`, `[pdf]`,
`[outlook]` (olefile), `[all]`. None is installed, so DOCX/PPTX/XLSX would fail
if routed. The contract meanwhile already carries fields for timelines, tracks,
keyframes and video regions that no converter produces. Design and
implementation depth are inverted relative to likely upload volume.

### 2.3 Embedded images in PDFs

`mistral_ocr` requests `include_image_base64` and turns every embedded image
into a located `DerivedAsset` (`kind="embedded_image"`, page + bbox) stored
under the representation's `media/` and linked from `document.md`
(`mistral_ocr.py:490-548`, `workers/e0.py:484-553`). But the request never
asks for image annotations, so `description` is always empty, and the image
is never itself converted. A chart's numbers or a diagram's structure are
invisible to extraction and search: the image is *kept* but not *read*.

### 2.4 Detection is being addressed separately

Open PR #452 proposes D132: classify the uploaded bytes (signatures, office
ZIP members, strict UTF-8) before E0 stores anything, refuse unknown binary
or a declaration that contradicts the bytes, and use the resulting MIME for
routing, storage class and metering. That fixes "trust the client's MIME".
It does not give the engine more routes, and its class list (PDF, image,
audio, video, office, text) would refuse SQLite, Parquet, archives and mbox —
families this analysis wants recognized. D133 therefore composes with D132
and names the detection families it must cover (design §2).

## 3. Is the architecture extensible? Two separate questions

### 3.1 Mechanically: yes

The contract is `convert(bytes, mime) → {document.md, source_map,
derived_assets, manifest}`. Anything that can be rendered to text fits it, and
the parts that make it trustworthy are format-neutral:

- **One coordinate system.** All downstream offsets (chunks, claims, D32
  grounding) point into `document.md`, so a new format adds a converter, not a
  pipeline.
- **Locators describe layout, not formats.** Five kinds (`page`,
  `source_range`, `image_region`, `time`, `video_region`) already cover most
  formats: a PPTX slide is a page, a DOCX paragraph a source range.
- **Honest self-accounting.** Coverage policy + gaps, component graph with
  local-vs-provider execution (D61), and range labels separating the
  source's own words from a model's observations and interpretations (D65 §5).
- **Originals are always kept** (D51/D117), so a lossy reading never destroys
  the evidence.

The locator union is a closed discriminated union, but adding a variant is
additive: consumers dispatch on `kind`, and nothing downstream assumes the
set. Special-purpose formats (DICOM, GeoJSON, CAD, notebooks) fit as
converters that render a textual self-description while the original stays
available.

### 3.2 Semantically: not for structured data

The pipeline assumes **knowledge is a set of statements to extract from
text**. That is right for testimony — prose, slides, email, scans, images,
recordings. It is wrong for data whose value is its structure: spreadsheets,
large CSV/JSON, logs, Parquet, SQLite. Take a 50,000-row sales export:

- rendered as a Markdown table and chunked, claim extraction runs one model
  call per chunk — cost proportional to rows, the opposite of what memory
  should cost;
- the rendering discards what makes the data useful: types, formulas,
  cross-sheet references, nesting;
- the questions people ask of data are aggregations ("total Q3 revenue by
  region"). No amount of claim retrieval answers an aggregation correctly.

More converters do not fix this; the fix is a different *representation
posture*.

### 3.3 Four structural gaps

1. **One input, one document.** Email with attachments, ZIP archives, PDFs
   with figures, message exports (mbox, chat JSON) are containers. The
   contract has no way to emit child documents with provenance to the parent.
2. **Routing is operator plumbing.** Exact MIME match, a two-entry default
   and replace-not-extend configuration mean every deployment rebuilds the
   table by hand — which is how a hosted deployment ended up with three
   routes (text, Markdown, PDF) and silently failed everything else.
3. **No pointers into structured sources.** No locator for a spreadsheet
   cell range, a table's column/rows, a JSON path, or a log line range.
4. **Admission policy is scattered.** Size caps and provider limits live
   inside individual converters (e.g. Mistral's 50 MB). No single place says,
   per family: accepted or not, up to what size, which posture, which
   converter, which cost class.

## 4. Metadata instead of rows for structured data

**Proposal (owner, 2026-09-23):** for XLSX, large CSV and JSON, logs, Parquet
and SQLite, memory tracks what the file is — what it is about, its sheets,
tables, columns, important formulas and relations — not the rows.

The reasoning: memory's job is to know **that the data exists, what it is,
and how to get at it**. Answering from the rows is a query over the original,
not a memory lookup. We call the metadata reading a **profile**.

Refinements reached in discussion:

1. **Small is content.** A 15-row pricing table or a 40-line `config.json`
   *is* the knowledge; converting it fully is cheaper and better than
   describing it. Posture is decided per file by size and shape.
2. **Values that link, not rows.** Column names alone cannot answer "which
   file has Acme's orders?". The profile keeps a bounded set of *identifying*
   values — top distinct values of low-cardinality or identifier-like columns,
   date spans, totals, named cells — so the file links to entities in memory.
   Measure columns and free-text columns contribute statistics only.
3. **Formulas and structure are the spreadsheet's knowledge.** Named ranges,
   pivot tables, cross-sheet references and the formulas behind headline
   numbers belong in the profile; per-cell formulas do not.
4. **Logs are partly event data.** A log profile (source, span, services,
   level distribution) is right as the default. The genuinely useful memory
   in logs is often *events* (errors, deploys). An event digest is recorded as
   a documented alternative, not part of the design (§7).

How a profile is produced: a **deterministic profiler** (library code: sheets,
columns, types, counts, ranges, null rates, top values, formulas) and **one
bounded model call** that writes the overview from the profile plus a few
sample rows. The sample rows are shown to the model, never stored in
`document.md`.

### 4.1 Does the contract carry a profile?

About 80% already fits:

| Need | Existing primitive | Fit |
|---|---|---|
| Profile text | `document.md` with a section per sheet/table | as is |
| "Rows deliberately not represented" | `ConversionCoverage(policy=…, complete=False, gaps=…)` | as is |
| Deterministic facts vs model summary | `DerivationRange` (`derivation_kind` is free text) | needs one more `evidence_mode` (see below) |
| Component graph | `ConverterManifest.components` | as is |
| Pointers into the file | — | four new locator kinds |
| Queryable normalized copy | `DerivedAsset` | fits; needs a declared asset kind |
| Answering from rows | — | a new retrieval primitive over the stored data |

**Evidence mode.** A row count or a column minimum is neither the source's
own words (`source_expression`) nor a model's observation. Labeling it
`source_expression` would overstate it (the source never *said* "48,210
rows"); labeling it `model_observation` would suggest a model was involved.
A fourth mode, `computed`, keeps the disclosure honest. Column names, sheet
names, formula text and named-cell values copied verbatim stay
`source_expression`.

**Extraction eligibility.** Left alone, E2 would turn a profile's structure
tables into schema trivia ("Sheet Q3 has a column Revenue"). Those ranges
should be searchable (they are how a file is found) but not claim-extracted;
claims come from the overview and key-values sections. The range labels
already give E2 the signal.

## 5. Finding documents and claims about them

A profile produces claims about **the file itself** ("the Q3 sales workbook
covers EU revenue by region"), and prose does the same ("this report covers
the 2025 audit"). Separately, agents ask for files directly ("find
Q3_sales_2025.xlsx"), by who and when ("emails from Alice"), and for
information limited to certain files ("everything about Project X from
Alice's emails").

### 5.1 First answer: documents as entities (not chosen)

The first D134 draft made a document an entity bound one-to-one to its
lineage, so claims about a file had the file as their subject. It went
through three review rounds (§9) and grew a `DOCUMENT` metadata passage,
a subject flag, subject-position binding, merge guards, per-source alias
bookkeeping and forget rules.

The owner then asked (2026-09-24) whether this was needed at all:
`documents` already records every file's name, and everything derived from a
file already links to it. Working through "find me all info about X from
emails from Alice" settled it. That question needs three things: to filter by
format (email), to filter by **sender**, and to run normal retrieval limited
to those documents. None of them needs the document to be an entity. What is
missing is **structured, filterable metadata about documents**, a **document
search**, and **document filters on retrieval**. A person entity for Alice can
widen the sender match, but that entity already exists. Files as nodes in the
fact graph ("Alice authored the Q3 report" as a relation) are the only thing
the entity adds, and nothing yet shows a need for it. The design moved to
`plan/proposals/document_subject_entities.md` with that adoption trigger.

### 5.2 Chosen: general metadata, document search, named self-references

- **General metadata fields**, shared by every format (`authors` rather than
  an email-only `from`), so one filter works across emails, office
  documents, PDFs and chat exports. Each family maps its native fields.
- **`search_documents`** over names, metadata and content, and **document
  filters on `search`**.
- **Claimify names the document in self-references.** The owner kept this
  from the entity approach: a claim that says which file it is about can be
  found by the file's name. It is limited to self-references, because a file
  name appended to every claim would repeat provenance and pollute embeddings
  as the D129 world-time suffix did.
- **A document's own name is never made an entity**, so two same-named files
  cannot merge through a name entity.

## 6. The data query primitive — engine choice

The agent needs to answer aggregations over a profiled file. Alternatives:

| Option | Assessment |
|---|---|
| Load rows into PostgreSQL and reuse the open-query-space sandbox | Violates D37 (Postgres stores no document bodies); a million-row CSV becomes a million Postgres rows per version; version and forget cascades multiply. Rejected. |
| Agent downloads the original and computes | Works only for mounted harnesses with a code runner, burns context, and is not a memory surface. Kept as the fallback that already exists (raw mount / `source_open`). |
| Per-format query languages (jq for JSON, SQL for SQLite, pandas for XLSX) | Several dialects and sandboxes to secure. Rejected for surface area. |
| **One embedded analytical engine (DuckDB) over a normalized copy** | One SQL dialect for CSV, XLSX sheets, Parquet, JSON and SQLite; in-process; reads Parquet natively. **Chosen.** |

DuckDB's settings are **not** a sandbox on their own. Its security guide
says to treat SQL like shell code: untrusted SQL needs a separate
minimal-privilege process or container, network isolation and
application-level timeouts, and the settings (`enable_external_access=false`,
`allowed_directories`, disabling extension autoload/autoinstall and community
extensions, `memory_limit`, `threads`, `max_temp_directory_size`, then
`lock_configuration=true`) are defence in depth. It also warns that malicious
queries can exhaust memory, disk, CPU or network. Sources:
<https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview>,
<https://duckdb.org/2025/03/06/gems-of-duckdb-1-2> (both retrieved
2026-09-23). The first draft of this analysis treated the settings as
sufficient; review (§9) corrected it. The design therefore runs each query in
an isolated worker process over a staged read-only copy of only the needed
tables, with OS resource limits and a parent-enforced wall time, and applies
the settings inside it.

**The normalized copy.** Converting each table to Parquet at conversion time
(stored as a derived asset) makes the query path format-independent, fast and
typed, at the cost of storing the data a second time. The alternative —
querying originals directly — would need a DuckDB reader per format at query
time and would re-parse XLSX on every call. The storage cost is accepted.

## 7. Alternatives recorded, not chosen

- **Full-row extraction for structured data** — rejected (§3.2): cost scales
  with rows, and it still cannot answer aggregations.
- **Refuse structured formats** — rejected: the file is useful to agents, and
  D117 already established store-first behavior.
- **A separate ingestion path for message exports** — rejected. Conversations
  are already ingested as dialogue-shaped documents (the shape D131's
  extraction handles). An mbox or chat export is a *container* whose members
  are conversations; fan-out plus a transcript converter reuses everything.
- **Log event digest** — a converter posture that extracts error/deploy/
  anomaly events from logs as dialogue-like records. Not chosen: event
  selection needs its own evaluation, and the profile plus data query already
  answer "what happened between 10:00 and 10:05". Adoption trigger: agents
  repeatedly need log events as remembered facts rather than queried rows.
- **Always mint document entities at ingest** — rejected for entity-space
  pollution at scale (§5).
- **Mistral per-image annotation instead of embedded-image children** —
  cheaper (one parameter) but produces a caption without OCR and without the
  image route's two-lane contract. Container fan-out handles embedded figures
  through the same image route as standalone images.

## 8. Sequencing

Sequencing is in [the delivery plan](../plans/format_coverage_delivery.md);
it does not belong in the design.

## 9. Independent review

**Superseded forget details.** The review rounds below added member
suppressions, a restructured v2 forget manifest and per-member refusal rules.
The owner later chose the simplest rule (2026-09-24): delete and hard forget
act on the uploaded document and cover every expanded member; members are
never deleted or forgotten on their own. Those review resolutions are
history, not the current design (D133 §5.4).

Rounds 1–4 reviewed the first D134 draft (documents as entities). Their
D134 rows describe that draft, which §5.1 records as not chosen; the current
D134 is §5.2.

Codex (gpt-6-sol, high reasoning) reviewed the first draft adversarially and
returned 15 findings (12 must-fix, 3 should-fix; verdict "not
implementation-ready"). All were accepted; none was rejected. The
substantive corrections:

| Finding | Resolution in the design |
|---|---|
| Registry had no concrete entries, precedence, or D132 compatibility | Full shipped registry (§3), fixed detection precedence (§2.2), explicit refinement of D132's text-flavour rule |
| JSON "object top level" exception was unbounded | Hard 200-row / 20,000-character bounds, no shape exceptions (§4.1) |
| Sample rows, Parquet copies and identifying values were under-specified | Copies-and-disclosures table, eligibility and sensitive-field rules, minimum frequency (§4.4) |
| E2 has no eligibility hook; chunks could mix ranges | Versioned eligibility policy, E1 boundary rule, Selection scheduling, reuse basis (§4.5) |
| DuckDB settings are not isolation | Isolated worker process, staged directory, OS limits, extension controls, wall time (§4.6; §6 above) |
| Parquet under `media/` would be mounted and unaudited | Private `query/` prefix, never mounted (§4.6) |
| `data_query` contradicted retrieval's primitive list and MCP rule | Added to retrieval §3 and the MCP exposure rule |
| Converter contract cannot emit children; no member schema | `expand` sub-worker, member descriptors, `document_members`, partial failure, readiness (§5.1) |
| Member paths collide and renumber | Collision-safe member keys per container kind (§5.2) |
| D54 counts `DISTINCT doc_id`, so children inflate counts | `counting_lineage_id` on lineages and evidence rows (§5.3) |
| Forget cascade and zip bombs were assertions | Descendant-closure manifest, member suppressions, whole-tree bounds (§5.4–§5.5) |
| Self card had no citable passage or persisted marker | `DOCUMENT` metadata passage, `subject_is_document` flag, deterministic alias match (D134 §3–§4) |
| Binding, mint race, alias enum undefined | Reuse `documents.document_entity_id` as a unique binding, row-locked mint, `document_metadata` provenance, merge guard (D134 §1–§2) |
| Rename and forget left names behind | Metadata observation for renames; forget removes sourced aliases and renames or retires the entity (D134 §2, §8) |
| Old contract still in binding text and evals | Retrieval, schema, E1, E0, lifecycle, hard-forget designs and eval checks updated |

### Round 2

A second Codex pass checked the revision: 4 findings resolved, 10 partially
resolved and 1 not resolved, plus 6 new must-fix problems (8 new findings in
all) in the added mechanisms. All were accepted:

| Finding | Resolution |
|---|---|
| Registry used placeholder MIME lists; aliases and storage class missing | Concrete canonical MIME types per family, an alias list, named message-export shapes; storage class stated once as D132's rule |
| A `query/` prefix inside the mounted artifacts bucket is still mounted | Third object-store root, the **private store**, with separate IAM, never mounted (E0 §2) |
| Eligibility cuts at character ranges conflict with whole-block chunks | Converters must change eligibility only at block starts; conversion validates it; E1 cuts at those blocks |
| Network isolation was conditional; temp files in a read-only directory | No-network OS sandbox required, otherwise `data_query` is a typed `boundary`; separate input and scratch directories |
| `QueryResult/v1` is PostgreSQL-bound and forbids a generic envelope adapter | Distinct `DataQueryResult/v1` with its own fields and errors |
| Expansion would edit the parent's immutable representation | Conversion-time gaps only in the parent; per-member outcomes in mutable member records; stable `member:<key>` handles resolved at render time |
| Ordinal suffixes shift on insertion | Keys use paths, identifiers or content hashes only, never positions |
| An open-query projection still counted distinct `doc_id` | `evidence_lineage` and count comments use `counting_lineage_id` |
| The D74 manifest still described one lineage | Manifest v2: per-descendant entries, member suppressions, alias-contribution keys, restore guards |
| A `DOCUMENT` citation could bind an object | Claimify's `document_is_subject` field validated at the gate; E3 binds only the subject position |
| One `source_doc_id` per alias row cannot keep two sources apart | `alias_contributions` table; an alias survives while any contribution does |

### Round 3

A verification pass resolved most items and left four: the registry decision
text still promised per-entry aliases and storage classes (now: one alias
table, D132's storage rule); one schema comment still copied member skip
reasons into the parent's immutable coverage (removed); member keys could
change form when a duplicate appeared (every key now has one fixed form,
content-hash-qualified for archive members and attachments, which means an
edited archive member becomes a new lineage); and alias contributions lacked
each source's spelling (now stored, and the displayed alias rebuilt from
survivors). Separately, the v2 forget manifest had listed alias lemmas,
which are names; it now lists only entity IDs, keeping the manifest
content-free.

### Round 4

A narrow verification confirmed the member-key, coverage and alias fixes and
found three remaining inconsistencies, all fixed: the design text still said
each registry entry carries aliases; manifest v2 and the suppression table
stored member keys, which can contain file names (both now store the key's
SHA-256); and §5.3 still described edited archive members as new versions,
contradicting the content-hash keys (now: only message-export conversations
gain versions; edited archive members and attachments are new lineages).
