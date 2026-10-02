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
  `plan/designs/postgres_schema_design.md` §6). The pointer is the **served** version: it
  moves to a version only when its representation has finished processing
  (`src/rememberstack/spine/document_catalog.py:642-666`) and back to the newest remaining
  ready version when the served one is deleted (`src/rememberstack/spine/lifecycle.py`
  `_REPOINT_AFTER_VERSION_DELETE`). `document_inventory.py:11-17` keeps "newest observed" and
  "served" apart.
- Identical bytes to the lineage's *latest* version are a no-op that may advance the mutable
  `source_version_ref` cursor (`document_catalog.py:160-215`, `_ADVANCE_VERSION_CURSOR`);
  bytes identical to an *older* version (A→B→A) create a new version sharing the content
  object — migration `p3_01_0008` dropped the per-lineage `UNIQUE (doc_id, content_hash)`
  that the schema design still showed.
- Every chunk read path selects chunks through `memory_v1.chunks_live`, which joins
  `dl.current_version_id = c.version_id`
  (`src/rememberstack/spine/migrations/versions/p9_04_0025_coordinate_binding.py:142-199`).
  Semantic and lexical chunk search (`src/rememberstack/adapters/postgres_p1.py:716-790`) and
  `adjacent_chunks` (`src/rememberstack/surfaces/query_engine.py:3930-3960`, `AND
  d.current_version_id = ch.version_id`) therefore see **only the served version** of each
  lineage. Old versions are stored and their claims may stay current
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
- **A version deleted (D135)**: deletion is a soft tombstone (`lifecycle.py`
  `_TOMBSTONE_VERSION`), so nothing cascades; every D140 read joins non-deleted versions, so
  the version's declarations leave interval derivation and the previous version's derived end
  moves to the next remaining start. Text-origin lookup only considers non-deleted versions
  at chunk creation, so new chunks never draw on a deleted version; values already recorded
  stay (they are extraction input, like `asserted_at`).
- **A reference to a target not yet ingested**: stored with `to_doc_id = NULL`; late binding
  resolves it when the target lineage appears. Until then, and after the target is deleted
  or forgotten, the row reads as `target_unavailable` — one status for all three cases so a
  reader cannot learn that a document once existed. A floating reference whose target has no
  version in force anywhere in the read window reads as `target_not_in_force`, never silently
  dropped. A section key missing in the chosen target version is
  returned as "section not present in this version" (a provision can be repealed while its
  act remains).
- **Hard forget of a target (D74)**: `to_doc_id` is cleared as today; the source's own
  target identity text is the *source's* content and is retained, like `raw_citation`, so
  the reference can re-bind if the target is ingested again. Public views never expose it for
  an unresolved target (the existing `document_crossrefs_live` rule).
- **Supplied reference sets replaced**: a new set for the same version is a new generation;
  rows of the previous generation stop being live in the same transaction (no window with
  both or neither). An invalid set (unknown source section key) is rejected whole and the
  previous set stays live.
- **Last period retracted**: the lineage stays periodised, so the withdrawn edition is simply
  not in force; only an explicit clear returns the lineage to served-version semantics.

## 6. Cost

Numbers are starting points to be measured, not commitments.

- **Storage.** Periods: a few rows per version. Section keys and section content hashes: two
  columns on existing rows. References: one row per reference per source version. For the
  statute book this was measured (§10): 12.2 million reference rows over 113,446 source versions,
  at most 13,975 per version. Partitioning follows the
  existing E0 tables if needed (D23).
- **Read cost.** Current-belief selection reads a per-lineage projection
  (`document_version_scope`) maintained in the writing transaction; ranked search probes it by
  primary key for each candidate inside the ranked statement. Filter-only listings walk the
  stable lineage order (newest version ingested by the cursor's as-of instant, then `doc_id`) and
  evaluate the scope per candidate batch — the projection for current belief, the ledgers for
  the page's candidate lineages when the belief instant is pinned in the past. Floating reference resolution is one lookup per returned
  reference. The verification target is in design §3.2.
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

## 8. Review round 1 (GPT-6 Sol) — choices made

The first design review ([review](../../design/reviews/REVIEW_gpt-6-sol_d140_design_r1_2026-09-30.md),
[response](../../design/reviews/RESPONSE_d140_design_r1_2026-09-30.md)) found twelve P1 and
seven P2 problems. Where it offered alternatives, this section records the choice and why.

| Question | Options | Chosen and why |
| --- | --- | --- |
| Address of a pinned target version | `source_version_ref`; lineage + `content_hash`; **a caller `version_key`** | `version_key`. The cursor mutates on identical-byte observations (D55) and is not unique; `content_hash` is not unique within a lineage after A→B→A. A caller key is immutable, unique per lineage, and is what publishers already have (edition numbers, consolidated-version ids). Pinning without a key is not offered: an address that can drift is worse than none. |
| Handles on scoped results | make P3 paths version-aware; **return version-addressed handles and a P3 path only when it opens the selected version** | The second. P3 is a lineage projection (D40); making it version-aware is a separate filesystem design. `source_open` already takes a version. |
| Claim evidence under a scope | the origin occurrence; **the occurrence in the selected version** | The occurrence: `chunk_claims` already stores per-occurrence spans and locators (D65/D119), so the returned coordinates match the returned version and survive deletion of the origin version. |
| Retracting the last period | forbid it; fall back to the served version; **keep the lineage periodised until an explicit clear** | Sticky state. Forbidding it blocks a legitimate correction ("this edition was never in force"); falling back resurrects withdrawn text as current. |
| Facts that rest only on text not in force | leave it to E3 adjudication; **a deterministic read-time evidence gate** | The gate. It needs no model call, cannot be skipped by an adjudication that has not yet run, and leaves D118's window as the only fact-time authority. E3 still receives the intervals as input (separate PR) to make windows better, not to make answers safe. The gate ships with time-scoped text retrieval so the two never disagree. |
| Floating resolution under ranges | one version per reference; **a temporal join over the read window** | The join: a range can span a target amendment, so one row per target version with `applies_during`; overlapping target versions are all returned and flagged concurrent. |
| Text-origin time and deletion | recompute when the origin is deleted; **record once, immutable** | Recording once. It is extraction input and becomes `asserted_at`; recomputing would change the meaning of already extracted claims and break replay (D7). New chunks never draw on deleted versions. |
| One date per chunk | header only; **header, reuse key and fresh claims' `asserted_at`** | All three, so a re-extraction after a toolchain bump reads and stamps the same date. |
| "Did this section change" | own-block hash; **subtree hash, with the own-block hash also kept** | Subtree for `changed` (a changed sub-paragraph changes the provision); own-block for `own_changed`. |
| `versions: all` with a scope | reject the combination; **all versions within the scope** | Within the scope; `history` is the audit of all editions. No mode silently ignores a requested time restriction. |
| Large supplied sets | multipart staged upload; **one NDJSON request bounded by bytes** | One request: complete-set replacement stays atomic and simple; measured per-version sets are far below the bound (§10: largest 4.2 MB). |
| Invalid source section in a supplied set | demote to document grain; keep with a status; **reject the whole set** | Reject: supplied data is deterministic and the caller can fix it; a partial or broadened set would misattribute references. |
| Amendments without a date | nullable date; **explicit `change_date_known`** | Explicit, so a missing date is never read as "no timeline". |

## 9. Review round 2 (GPT-6 Sol) — choices made

[Review](../../design/reviews/REVIEW_gpt-6-sol_d140_design_r2_2026-09-30.md),
[response](../../design/reviews/RESPONSE_d140_design_r2_2026-09-30.md). The guiding rule for
this round was the simplest rule that stays correct at scale; several findings were closed by
narrowing a contract rather than adding machinery.

| Question | Options | Chosen and why |
| --- | --- | --- |
| Identity of versions, reference sets and extracted references | three separate rules; **one rule: rows are keyed by `version_id` and a generation, never by content** | One rule. A `version_key` is assigned only at creation (a new key always creates a version; an existing key on a later observation is rejected with a message pointing at the period API). Supplied and extracted references are *generations* per `(version_id, origin)` with one active generation replaced atomically. A→B→A then needs no special case anywhere. Key aliases were rejected: they would make a pinned address resolve to one of several versions. |
| Back-filled older editions and text origin | keep "earliest ingested"; **only matches dated no later than the incoming version** | The reviewer's rule. It is one comparison, makes out-of-order ingestion safe, and never re-dates existing claims. Unknown incoming dates disable inheritance (conservative). |
| Evidence gate placement | filter hydrated results and refill; **eligibility predicate before every relevance bound** | The predicate, as D87 already requires for entity and fact-time eligibility; no refill loop and no change to ranking or budgets. |
| `search_documents` grain with several editions | version grain; **lineage grain with a representative and every matching edition listed** | Lineage grain keeps D134's contract and paging; listing matching editions gives version-level access without a second result model. |
| Clear vs belief-pinned reads | snapshot selected versions in cursors; **mode transitions as ledger events** | Ledger events: one small append-only table, the same evaluation rule as declarations, and past belief instants become exactly reconstructable (cursors stay small). |
| Scale of selection | evaluate the ledger per query; **a current-belief projection maintained per lineage in the writing transaction** | The projection turns scoping into a primary-key probe per ranked candidate and a GiST scan for listings; the ledgers remain the authority and rebuild it. |
| Reference sets over 64 MiB | multipart staging; **a justified per-version bound as a scope boundary** | References are anchored in text, so their number is bounded by the text; 64 MiB of NDJSON exceeds what any supported document can anchor. Staging would add state and expiry for a case that cannot occur. |
| Keys of pre-D140 sections | report them absent; **backfill keys with the hashes; report `not_indexed` until then** | The key is a pure function of the stored heading text, so the same backfill job derives it. |

## 10. Measured: references per version in the statute book (2026-09-30)

Round 3 of the design review asked for evidence behind the 64 MiB bound on a supplied reference
set (design §6.3). Measured on the motivating corpus.

**Input.** `gs://legalit-io-esel-opendata/esel-opendata/2026-09-29/datove-sady-esbirka/008PravniAktOdkaz.json.gz`
from the LegalIt snapshot of 2026-09-29 (manifest
`gs://legalit-io-esel-opendata/esel-opendata/2026-09-29/manifest.json`; file size 327,739,642
bytes, SHA-256 `d9229c79168c6609bd50231484aad213d8ccc678310ced0a79a8626b2a2372d2`, matching the
manifest; upstream Last-Modified 2026-09-28 23:07:14 GMT).

**Method.** The file was streamed through `gunzip` into `ijson` (C backend) over the top-level
`položky` array, in a disposable Cloud Shell session; no data was kept. For each reference item,
the source version is `znění-fragment-zdroj.znění-dokument-id`. Each target fragment in
`znění-fragment-cíl` became one NDJSON line in the design's item shape — `kind`,
`from_section_key` (the source fragment anchor, e.g. `par_2/frag_201`), `target`
(`source_kind`, `source_ref` = the act's ELI path, `section_key` = the target anchor, and
`version_key` for static references), `binding` (`pinned` for static, else `floating`),
`source_label` (the publisher's type code) and `context` (the citation text) — serialized with
`json.dumps(ensure_ascii=False)` plus a newline, and its UTF-8 length was summed per source
version. External references without a target fragment became one line with a URL target.

**Results.**

| Measure | Value |
| --- | --- |
| reference items | 12,182,855 |
| NDJSON lines (one per target fragment) | 12,228,739 |
| source versions with at least one reference | 113,446 |
| references per version — median | 14 |
| references per version — 99th percentile | 1,406 |
| references per version — 99.9th percentile | 3,240 |
| references per version — maximum | 13,975 (version 223999) |
| NDJSON bytes, largest version | 4,158,303 (≈ 4.0 MiB) |
| mean bytes per NDJSON line | 282.5 |

The five largest versions carry 13,105–13,975 references each (≈ 4.0 MB each), consecutive
consolidations of the same large acts. **Conclusion:** the 64 MiB bound is about 15 times the
largest version in the whole statute book, so it is kept as a scope boundary (design §6.3).

## 11. Measured: the time-scope cost (2026-10-02)

Harnesses: `benchmarks/d140_scope/run.py` (dated corpus; `main`'s exact pre-D140 chunk
statements against the scoped ones) and `benchmarks/d140_scope/undated.py` (an undated,
LoCoMo-style corpus run unchanged on `origin/main` 1dc23f21 and on this branch). Raw results:
`benchmarks/d140_scope/results/2026-10-02/`. One short-lived GCE VM (n2-highmem-16, 16 vCPU,
125 GB RAM, 1 TB pd-ssd, `remember-stack` project, deleted afterwards); PostgreSQL 19 beta 3
from this repository's `Dockerfile.postgres` with `shared_buffers=32GB`; synthetic text and
random unit vectors, no model calls; every table analysed after loading.

**Dated corpus**: 10,000 lineages × 5 versions × 10 chunks = 500,000 chunks; half the lineages
declare yearly periods; 100,000 chunks carry vectors (HNSW). 40 queries per mode after a
warm-up; p95 in milliseconds:

| statement | `main` (served version) | `current` | `at 2003` | `history` |
| --- | --- | --- | --- | --- |
| BM25 chunk search | 2,759 | 2,630 (0.95×) | 2,640 (0.96×) | 8,037 |
| semantic chunk search | 844 | 868 (1.03×) | 868 (1.03×) | 2,482 |

`current` and `at` select one version per lineage and meet the 1.2× target. `history` selects
every version, so it ranks five times the candidates; it is a different query (all
editions), not the same query scoped. Projection rewrite for one lineage with 1,000 versions:
49 ms p50, 54 ms p95 (target under 100 ms).

**Undated corpus** (no periods anywhere): 500 conversations × 60 turns = 30,000 chunks,
90,000 claims (each with its origin occurrence), 16,000 relations and 4,000 observations,
each supported by 1–3 claims. Identical data, seeds, warm-up and 60 queries × 2 repeats on
both sides; p95 in milliseconds:

| path | `main` | branch | change |
| --- | --- | --- | --- |
| `search_chunks` BM25 | 1,261 | 1,231 | −2.4% |
| `search_chunks` semantic | 682 | 649 | −4.8% |
| `claims_and_sources_context` | 7,296 | 4,824 | −34% |
| `facts_context` | 5,239 | 5,046 | −3.7% |

No path is slower, so no undated fast path was added. (`claims_and_sources_context` is faster
because the scope join replaces `main`'s served-version view joins.)

**What was not measured, and why.** The design's target corpus (1 M lineages, 5 M versions,
50 M chunks) was not run. On this data shape `main`'s ranked statements do not use the BM25 or
vector index at all: the planner joins the visibility views first, scores every live chunk
of the deployment and sorts (`EXPLAIN ANALYZE` on `main`: a sequential pass over all 30,000
chunks for one BM25 query). Absolute latency therefore grows linearly with corpus size before
D140 — seconds at 30,000–500,000 chunks — and a 50-million-chunk query would take minutes on
both sides. That is a property of the existing engine, not of the synthetic setup (statistics
were current, caches warm) and not of D140, whose ratio to `main` was 0.95–1.03× at 500,000
chunks (a ratio at full scale is an extrapolation, not a measurement). Index-driven ranked
search at that scale is separate work.
