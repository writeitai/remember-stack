# Version effective time, section identity, and section-level references — analysis

**Status:** analysis (non-binding). **Date:** 2026-09-30. **Decision:** [D140](../../decisions.md#d140-declared-effective-periods-section-keys-and-version-aware-references).
**Binding design:** [effective time and section references](../designs/effective_time_and_section_references_design.md).

This document explains why the engine needs four general capabilities that it does not have
today, what the corpus and code on `main` (1dc23f21) already provide, which alternatives were
considered, and what each choice costs. Each capability is useful to any memory deployment
that keeps versioned documents. The first corpus that needs all four at once happens to be
the Czech statute book, so it serves as the running example; the engine itself stays a
memory engine and gains no legal vocabulary.

## 1. The problem, in general terms

Many sources publish **successive versions of one document, each of which is in force for a
declared period**, and they point **from one part of a document to a specific part of
another**:

| Source | Versions with a declared period | Part-to-part references |
| --- | --- | --- |
| A company policy, price list or terms of service | "valid from 1 March 2026"; the next edition replaces it | "see section 4.2 of the Travel Policy" |
| A standard or specification (RFCs, ISO, internal API specs) | edition 3 supersedes edition 2 on a stated date | "as defined in section 5 of RFC 9110"; "this document updates RFC 7231" |
| A contract and its amendments | amendment 2 changes clause 7 from 1 July | "clause 7.3 of the Master Agreement" |
| A wiki, docs site or Confluence space | a page revision is "the current process" from its publish date | links to `page#heading-anchor` |
| **Statutes** (motivating example below) | each consolidated version has an effective-from and effective-until date | "§ 5 odst. 2 applies as in § 12"; "act 303/2013 amends § 5 of act 89/2012" |

Three questions follow, and today's engine answers none of them for document text:

1. **What does the document say *now*?** "Now" means *the version in force now*, which is not
   necessarily the version ingested most recently. Publishers release versions before they
   take effect, and archives are often back-filled out of order.
2. **What did it say *on a past date*?** A lawyer with a 1996 case, an auditor checking which
   price list applied to a 2024 invoice, an engineer asking which spec edition a 2019 system
   was built against.
3. **What does this passage refer to, and what refers to it?** — resolved to the right
   *part* of the right *version*.

A fourth, economic problem appears as soon as many versions are ingested: **most of each new
version is identical to the previous one**, so processing cost must follow the edit, not
the document size.

## 2. Motivating example: the Czech statute book (e-Sbírka open data)

The state publishes the whole statute book as open data (the LegalIt project, a separate
client repository, holds a snapshot: 148 files, 4.7 GB compressed, taken 2026-09-29). The
parts that matter here:

- **Acts and consolidated versions.** An act (`002PravniAkt`, e.g. `eli/cz/sb/1918/8`) has an
  ordered list of consolidated versions (`001PravniAktZneni`). Each version is a *complete
  text with every amendment already applied* and carries `znění-datum-účinnosti-od`
  (effective from), `znění-datum-účinnosti-do` (effective until, often `null` = open) and a
  repealed flag. **The publisher has already done the amendment arithmetic**: nobody needs to
  replay "amendment X replaces paragraph 3" to reconstruct a past text; every past text is
  published whole.
- **Stable provision identity.** Each version's fragments (`003PravniAktZneniFragment`,
  1.24 GB compressed) point at a version-independent fragment record (`004PravniAktFragment`)
  and carry a stable anchor inside the act (`par_1`, `par_2/frag_201`). "§ 5" is the same
  provision across versions even when its text changes.
- **Typed, already-resolved references.** `008PravniAktOdkaz` (328 MB compressed) links a
  source fragment to a target fragment, per version, with a type code: `INTUSTAN` (another
  provision of the same act), `JINEUSTAN` (a provision of another act), `JINYPRED` (another
  act as a whole), `NADRSBIR` (the act this one implements), `CZECHVOC` (the definition of a
  term), and a flag `odkaz-je-statický` — a **static** reference means a fixed version of the
  target; a **dynamic** one means "whatever is in force". The same logical reference repeats
  in every version that contains it (shared `odkaz-base-id`).
- **Amendment links.** `007PravniAktKonsolidacniVazba` links an amending act's clause to the
  exact fragment it changed, with the date the change takes effect and an instruction type.

So for this corpus the hard problems of the D36 citation design (finding citation strings,
fuzzy-matching them to documents) do not arise: the references arrive resolved. What the
engine lacks is a place to put them and a way to read text by effective date.

The mapping from e-Sbírka to engine calls (one lineage per act, one version per consolidated
text, Markdown headings with section keys, supplied references) belongs in the LegalIt
client, not in the engine.

## 3. What `main` provides today (verified 2026-09-30, origin/main 1dc23f21)

### 3.1 Versions and "current"

- A document **lineage** (`documents`, identity `(source_kind, source_ref)`) owns immutable
  **versions** (`document_versions`) and a `current_version_id` pointer
  (`plan/designs/evidence_lifecycle_design.md` §2, D55; schema in
  `plan/designs/postgres_schema_design.md` §6). The pointer moves to the newest ingested
  version.
- Every chunk read path selects chunks through `memory_v1.chunks_live`, which joins
  `dl.current_version_id = c.version_id`
  (`src/rememberstack/spine/migrations/versions/p9_04_0025_coordinate_binding.py:142-199`).
  Semantic and lexical chunk search (`src/rememberstack/adapters/postgres_p1.py:716-790`) and
  `adjacent_chunks` (`src/rememberstack/surfaces/query_engine.py:3930-3960`, `AND
  d.current_version_id = ch.version_id`) therefore see **only the most recently ingested
  version** of each lineage. Old versions are stored and their claims may stay current
  testimony (`snapshot` mode), but their passages cannot be retrieved.
- There is **no notion of when a version is in force.** `source_modified_at` is "when the
  source says this snapshot was authored/modified"; it is immutable, feeds claim
  `asserted_at` (D41/D55) and is not an in-force date: a typo fix is modified today but the
  rule it states has been in force since 2019.
- `versioning_mode = living` means "the newest version is the source's standing statement"
  and retires claims found only in superseded versions (D54/D55,
  `evidence_lifecycle_design.md` §2–§3).

### 3.2 Time vocabulary

- Facts (relations and observations) have one world-time window `[valid_from,
  valid_until)` (D118, `plan/designs/mutable_fact_windows_design.md`). Retrieval selects facts
  with `current` / `at` / `overlap` / `history` (`src/remember/models.py:419-474`; SQL
  predicate `src/rememberstack/surfaces/query_engine.py:4233-4246`; MCP `_TIME_SCHEMA`
  `src/remember/mcp_tools/_definitions.py:418-449` on `facts_context` and
  `combined_context`). `NULL` bounds mean unbounded; `history` means "started by the
  evaluation instant".
- Claims keep a source-asserted validity (D41) and are searchable with `claims_as_of`.
- `SearchRequest` (`src/remember/models.py:275`) and `claims_and_sources_context` take **no
  time scope**. `search_documents` has `versions: current|all`; its `as_of` is a paging
  instant, not an in-force date.
- Open PR #486 (D118 amendment, no code) states that belief-time reads test world time
  against each fact's **current** window. Anything below that updates a fact window does so
  through ordinary D118 adjudication, so #486's rule applies unchanged.

### 3.3 Sections

- Sections come from the deterministic heading parser (D79,
  `src/rememberstack/core/structure_skeleton.py:81`) or a model-proposed fallback; they are
  stored per version in `document_sections` with a materialized `node_path` (`'0.2.1'`)
  (`plan/designs/postgres_schema_design.md` §6, `src/rememberstack/model/sections.py:193`).
- `node_path` is positional: inserting a heading renumbers every following sibling, so it is
  **not** an identity across versions. Heading numbers are recognized by regex
  (`structure_skeleton.py:46-48`) only for skeleton statistics.
- The parser has **no support for heading attributes**. A Pandoc-style `## § 5 {#par_5}`
  keeps `{#par_5}` inside the title.

### 3.4 Cross-references

- D36 names a `crossref` E0 sub-worker. `plan/designs/e0_files_design.md` §4A designs it:
  document-to-document rows `(from_doc_id, to_doc_id NULL, kind, context)`, kinds
  `cites | links_to | attaches | replies_to`, deterministic extraction per kind, cheap-first
  resolution (exact keys → fuzzy title → small model), late binding.
- Built: the table and enum
  (`src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py:510-532`,
  `p0_02_0001_extensions_enums.py:73`), the graph edge and helper
  (`p9_17_0038_postgres19_live_graph.py:153-204`, `graph_citation_path` at `:877`), the public
  view `memory_v1.document_crossrefs_live` (`p9_17_0038…:263-300`), SDK/HTTP
  `graph_citation_path`, deletion handling (`src/rememberstack/spine/forget.py`).
- **Not built:** the worker (`src/rememberstack/workers/e0.py` stops at structure), any
  writer (only tests insert rows), any MCP tool, and anything finer than document grain.
  `agent_retrieval_surface_design.md:285` defers a `citation_path` recipe until a corpus
  has cross-reference density.

### 3.5 Reuse across versions

- D56 promises that a new version costs in proportion to the edit: unchanged chunks reuse
  their claims through `extraction_input_hash`, embeddings carry forward by content.
- Implemented: E1 builds the key (`src/rememberstack/workers/e1.py:607-667`); E2 replays a
  prior Selection/Claimify result for the same key **within the same lineage**
  (`src/rememberstack/workers/e2.py:631-680`, `:781-830`); E1 carries embeddings forward by
  chunk content hash (`e1.py:270-320`); section summaries have a per-lineage content cache
  (`src/rememberstack/workers/e0_summary.py:684`).
- **Gap:** the key's "stable header facts" include the version's
  `source_modified_at`/`published_at` (`e1.py:628-635`, specified in
  `plan/designs/e1_chunks_design.md` §7 A3). Almost every new version has a new modification
  time, so **no chunk of a new version ever matches the previous version's key**: every
  version is re-extracted in full. D56's worked example ("a 50-page doc with a
  two-paragraph edit re-extracts ~2 chunks") does not hold on `main` for any source that
  stamps modification times, which is nearly all of them. The date is in the key because E2
  shows it to the model (`e2.py:1741-1749`) to resolve relative expressions ("last year")
  and because it becomes `asserted_at` (`e2.py:1295`).

### 3.6 Role-based search exclusion

The earlier conversational assessment worried that statute sections would be classified with
the `legal` role and hidden from default search. On `main` this does not happen: the role is
assigned deterministically only for titles such as "legal notice" or "terms and conditions"
(`structure_skeleton.py:300-330`), and search applies a role filter only when the caller
passes one as an equality filter (`postgres_p1.py:1602`). D58's "default recipes filter out
… legal chunks by role" is not implemented. **No change is needed**, and none is designed.

## 4. Alternatives

### 4.1 How to model "in force from / until"

| Option | Verdict |
| --- | --- |
| **A. One lineage per version**, caller keeps a map "act → date → doc_id" and filters with `documents.doc_ids` | Rejected. It works today but pushes the core question ("what is in force on date T") out of the engine, loses lineage-level reuse (D56 reuse is per lineage), and breaks P3 paths, citations and late binding, which anchor on lineages. |
| **B. Reuse `source_modified_at` as the in-force date** | Rejected. Modification time and entry into force are different facts (typo fixes, versions published months ahead of force, back-filled archives). It is also immutable extraction input; changing its meaning would change every claim's `asserted_at`. |
| **C. One mutable window column pair on `document_versions`** | Rejected as the authority. A later event (the next version, a repeal) must close a window after the fact, and a version's text can come back into force (a reverted policy); one row per version cannot express "in force 2019–2020 and again from 2023". |
| **D. Declared effective periods: zero or more periods per version, append-only declarations, `until` derived from the next declared start unless declared explicitly** | **Chosen.** Succession needs no write at all: the period of version *n* ends where the next declared start begins, whatever order versions arrived in. Only genuine gaps (a repeal, an expiry) need an explicit end. Corrections retract a declaration and add a new one; history stays auditable (the D33/D54 ledger pattern). |

Within D, two sub-questions:

- **Living lineages.** `living` already answers "which version is current" (the newest). A
  declared period is a second, competing answer. Allowing both would require rules for
  their disagreement and would drag D55's retraction machinery into effective time.
  **Chosen:** effective periods are accepted only on `snapshot` lineages; ingest rejects a
  period on a `living` lineage. A source whose versions have declared force is an archival,
  dated source — exactly what `snapshot` means.
- **Lineages without periods.** They keep today's behaviour under every time scope (the
  current pointer). The engine never infers periods. This keeps every existing corpus
  byte-for-byte unchanged in behaviour.

### 4.2 How retrieval chooses versions

| Option | Verdict |
| --- | --- |
| A separate `as_of_date` parameter on search | Rejected: a second time language beside `current/at/overlap/history`. Agents already learn one vocabulary for facts. |
| **The fact time scope, applied to version selection** | **Chosen.** `current` = in force at the evaluation instant; `at T` = in force at T; `overlap [a, b]` = in force at any point of the range; `history` = every version whose force has started. For lineages without periods every mode reads the current pointer, as today. |

A consequence that follows directly from the chosen semantics: under `current`, a repealed
document (no period contains now) and a not-yet-effective document (every period starts in
the future) contribute **nothing**. That is the intended answer to "which rules apply now".

### 4.3 Section identity across versions

| Option | Verdict |
| --- | --- |
| Positional `node_path` | Not an identity (renumbers on insertion). |
| Title text or heading number | Titles change ("§ 5 Scope" → "§ 5 Scope and exceptions"); numbering styles vary; two sections can share a title. |
| Model-assigned identity | Non-deterministic; violates the D57 direction invariant (model output never becomes identity). |
| **An explicit key written in the source: a heading attribute `{#key}`** | **Chosen.** It is the de-facto Markdown convention (Pandoc, kramdown, markdown-it-attrs, MkDocs, Docusaurus, GitHub-style anchors) and the natural target of `page#anchor` links. It is deterministic, source-owned and costs nothing when absent. Converters that see HTML `id` attributes on headings can emit the same syntax. |

The key is stored on the section with a **section content hash** (hash of the section's
block hashes), which makes "did § 5 change between these versions" a comparison, not a
diff.

### 4.4 Where references live and how they bind

| Question | Options | Chosen |
| --- | --- | --- |
| Grain of a reference row | (a) per lineage with a period; (b) **per source version** | (b). Sections, chunks and claims already hang off versions; references are derived from a version's content the same way, so they are immutable, idempotent per version, and deleted with it. The e-Sbírka data is itself per version. Row count grows with versions × references (to be measured, §6). |
| Target address | (a) internal ids only; (b) **source identity** `(source_kind, source_ref)` + optional version ref + optional section key | (b). A caller can name a target that is not ingested yet; late binding attaches it when it arrives (D36 §4A already requires late binding). |
| Target version | (a) always the current pointer; (b) **pinned or floating** | (b). *Pinned* names one target version (a citation of "RFC 7231 as published", a static statutory reference). *Floating* resolves to the target version in force at the reading instant (the usual "see § 12"). Floating resolution happens at read time, so a reference never has to be rewritten when the target gets a new version. |
| Section endpoints | resolved section ids vs keys | **Keys**, resolved at read time inside the chosen version. Section ids are per version; keys survive versions. |
| Who writes rows | extractor only vs **extractor and caller** | Both write the same table with an `origin` column. Sources with explicit link data (wikis, spec indexes, the statute book) should not be forced through extraction; the caller-supplied path is deterministic and writes through E0 (Rule 3). |

**Kinds.** The existing kinds stay. Three general kinds are added, each with non-legal
examples:

- `refers_to` — an in-text pointer to another passage or document that is not a
  bibliographic citation: "see section 4.2", `page#anchor` links resolved to a section,
  "as defined in § 3". (`cites` stays for bibliographic citations found in reference lists.)
- `amends` — the source changes the target's text or force from a stated date: contract
  amendments, errata, RFC "Updates:", a policy memo that replaces clause 7, an amending act.
  The row carries the date the change takes effect.
- `implements` — the source elaborates or executes the target: an implementing regulation,
  an implementation profile of a standard, a procedure implementing a policy.

Term-definition links (e-Sbírka `CZECHVOC`) are `refers_to` with a target section; the
caller's own type code is kept verbatim in an opaque `source_label` column, returned but not
interpreted. This keeps every caller-specific taxonomy out of the engine enum.

### 4.5 Making the D56 promise true across dated versions

| Option | Verdict |
| --- | --- |
| Drop the date from the key | Rejected. The date is real extraction input: the model resolves "last year" against it, and claims inherit it as `asserted_at`. Reusing across a changed date without care could bind a relative expression to the wrong year. |
| Ask the model whether it used the date and store the answer as a reuse guard | Rejected. Model output would decide reuse eligibility (the D7/D56 "no LLM output in identity keys" rule in spirit). |
| Detect relative-time expressions lexically | Rejected: language-specific, fragile, and not a memory-engine concern. |
| **Key on the chunk's *text origin time*** | **Chosen.** Within one lineage, when a new version contains a chunk whose own and neighbour block hashes (and non-date header facts) equal a chunk of an earlier live version, the text was written when that earlier chunk first appeared. The E2 header and the key both use that **text origin time** instead of the new version's date. Relative expressions are then resolved against the time the words were actually written — which is also what the reused claim's `asserted_at` already says (D56 attaches the original immutable claim to the new occurrence). Changed chunks, and chunks whose neighbours changed, get the new version's date and re-extract, exactly as today. |

This is general: it restores D56 for every watched Google Doc, wiki page and re-exported
report, not only for statutes.

### 4.6 Facts extracted from dated versions

A claim extracted from version *v* of a periodised lineage is evidence that the document
stated it *while v was in force*. Two options:

- Copy the period into `claim_valid_*`. Rejected: claims are immutable and their validity is
  what the text itself asserts (D41); the period can change later (a repeal), and one claim
  occurs in several versions (D56 reuse).
- **Expose the occurrence's in-force period to E3 as adjudication input** (the union of the
  periods of the versions the claim occurs in), and enqueue ordinary D118 re-adjudication
  when those periods change. **Chosen.** It keeps D118's single fact window authority; a
  period change is just new input to the same adjudication. Under PR #486's rule a
  belief-time read then shows the corrected window.

## 5. Failure and recovery

- **Conflicting declarations** (two versions of one lineage declared to start at the same
  instant): rejected at write with a conflict; nothing is stored. Idempotent repeats of the
  same declaration are no-ops.
- **Declarations arriving out of order** (back-fill): derived ends make order irrelevant.
- **A version deleted (D135)**: its periods disappear with it; the previous version's derived
  end moves to the next remaining start automatically. Text-origin lookup only considers
  live versions, so reuse never draws on a deleted version (D55 refinement already requires
  this).
- **A reference to a target not yet ingested**: stored with `to_doc_id = NULL`; late binding
  resolves it when the target lineage appears. A floating reference whose target is not in
  force at the reading instant is returned with an explicit status ("target not in force at
  T"), never silently dropped. A section key missing in the chosen target version is
  returned as "section not present in this version" (a provision can be repealed while its
  act remains).
- **Hard forget of a target (D74)**: `to_doc_id` is cleared as today; the source's own
  target identity text is the *source's* content and is retained, like `raw_citation`, so
  the reference can re-bind if the target is ingested again. Public views never expose it for
  an unresolved target (the existing `document_crossrefs_live` rule).
- **Supplied reference sets replaced**: a new set for the same version is a new generation;
  rows of the previous generation stop being live in the same transaction (no window with
  both or neither).

## 6. Cost

Numbers are starting points to be measured, not commitments.

- **Storage.** Periods: a few rows per version. Section keys and section content hashes: two
  columns on existing rows. References: one row per reference per source version. For the
  statute book the reference file is 328 MB compressed across all versions; the row count
  must be measured on the real corpus before sizing indexes. Partitioning follows the
  existing E0 tables if needed (D23).
- **Read cost.** Version selection is one indexed predicate over a small per-lineage period
  set; the chunk search statement gains one join. Floating reference resolution is one
  lookup per returned reference.
- **Processing cost.** Declaring or correcting a period never reprocesses anything: periods
  are not extraction input. With text-origin keys, a consolidated version whose only change
  is one paragraph re-extracts the chunks of that paragraph and their neighbours; everything
  else reuses claims, Selection results and embeddings. For the statute book this is the
  difference between paying for every version in full and paying roughly for the amendments.
- **Model cost of references.** Zero for supplied references. The D36 extraction rungs keep
  their cheap-first design.

## 7. What stays out

- The engine does not replay amendments to construct past texts. Sources that publish only
  "base text + amendments" need a client-side consolidator; that is a documented non-goal,
  not a gap (the engine stores and serves texts, it does not execute edit scripts).
- Section keys are not inferred by a model or from numbering.
- Sections are not graph vertices; section-level navigation goes through the references
  operation, not SQL/PGQ traversal (bounded fan-out, and the graph's value is in entity
  relations).
- No domain flag was needed. Nothing in the design is specific to legislation.
