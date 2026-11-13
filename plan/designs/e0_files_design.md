# E0 — Files / Document Layer + the Corpus Filesystem (P3)

How a raw input becomes a **structured document** (E0), and how its original and
reader-useful derivatives become a **mountable document folder** (P3). E0 objects
are immutable; the corpus tree is a mutable, rebuildable projection. D36–D39,
D55/D65 and [D155](../../decisions.md#d155-the-corpus-filesystem-is-a-mutable-tree-of-document-folders)
bind the contract. [Analysis](../analysis/corpus_document_folders.md) explains
alternatives; [delivery order](../plans/corpus_document_folders.md) is separate.
Design acceptance does not mean this replacement is implemented.

## 1. Where this sits

E0 is the **document layer** of plane E. Its product is a *structured document*: the original
bytes, a clean Markdown rendering, a section tree, and cross-references — everything E1 (chunking)
and E2 (claim extraction) need. E0 is not a single worker; it is a short chain of **idempotent
sub-workers**, because document ingestion is genuinely several distinct, separately-failing jobs:

```
ingest ──► convert ──► structure ──► crossref
(store raw  (raw → md,   (PageIndex     (citations /
 + hash)    OCR/logic)   tree + roles    document links)
                         + placement)
```

These are *sub-workers of E0*, not new top-level stages: **the E-numbers name product layers**
(files → chunks → claims → relations), and PageIndex structure is metadata *about the document*
(before chunking), so it belongs to E0. We deliberately do **not** renumber E1→E2→E3 to give
structuring its own number — that's churn across every doc for no architectural gain (D36). The
complexity is handled by *decomposition into sub-workers*, each separately idempotent (on
`content_hash + that sub-worker's version`, D12) and separately observable.

## 2. Storage layout — object storage holds bodies, Postgres holds the index

Retained-resource evidence follows
[D152's engine capture contract](storage_capture_consumer_design.md).
Committed E0/representation references identify canonical resources; an
unregistered or failed copy remains separately classified exposure. Hot/cold
placement does not classify retention purpose. Capture introduces no source,
lineage, converter, chunk, Selection/Claimify or embedding identity change.
Existing processing admission and truth commits remain authoritative; a
storage observation cannot create either.

Three stores per deployment have distinct purposes (D37/D55/D65/D155):

- **raw** holds immutable source-of-truth originals. Ordinary E0 ingestion uses
  `<doc_id>/<content_hash>/original<suffix>`; D147 registered uploads can retain
  `uploads/<id>`. Always follow the recorded `raw_uri`, including content shared
  with another lineage; never reconstruct it from the document id. Raw remains
  private to the pipeline and API byte/`source_open` access. D51 MIME-based hot/cold
  routing and provider access logging apply. The deployment is one trust domain
  (D50); originals can contain EXIF, tracked changes and other source metadata.
- **artifacts** holds immutable readings under
  `<doc_id>/<content_hash>/<representation_id>/`: `document.md`, `blocks.json`,
  `conversion.json`, `meta.json`, optional `source_map.json`, and `media/`.
  The active PageIndex URI can instead be
  `<representation-prefix>/structure/<structure_generation_id>/pageindex.json`.
  These stored URIs and inventories, not assumed filenames, select the reading.
  Re-conversion creates another representation; old claims keep their old
  coordinate system. `current_representation_id` changes only on downstream
  completion. Artifacts contain both reader material and private pipeline output;
  mounting this store wholesale is not the customer consumption contract.
- **corpusfs** holds mutable **copies for readers**, including a copy of each
  published original. Its root is the filesystem root (§§5–6), with no snapshot
  prefix or `latest` pointer. All its objects are hot, including copies of cold
  raw originals: this is a browse surface, so readers must not need archive
  restoration. Raw and corpus original bytes occupy storage separately; that
  duplication and the cost of initially reading a cold raw object are accepted.
  D152 classifies these as distinct physical resources, not another source of truth.

The object-store port is provider-neutral. Corpus storage uses the existing S3
adapter for mountable.io, AWS S3, GCS XML/HMAC and MinIO (§5); local files remain a
development adapter. `gs://` in historical examples of E0 URIs denotes a provider
location, not a requirement for a native GCS adapter.

**Canonical-text rule (D65).** All text eligible for extraction, search and
span grounding lives in immutable `document.md`. A recording's transcript is its
Markdown body. Timing-preserving `.vtt`/`.srt`/JSON transcripts may also be reader
assets, but a sidecar alone is never testimony. `media/` contains derived images,
keyframes, crops, thumbnails and other reader assets; the original is copied
separately as `original<suffix>` in the document folder. P3 copies `document.md`
byte-for-byte, preserving offsets and `media/` relative links.

**Byte-authoritative admission (D132).** Before raw storage or a catalog row,
E0 inspects file signatures and header fields, ISO BMFF file-type brands
(distinguishing common MP4 video, M4A audio, and HEIC/AVIF images), and, for
office ZIP packages, their member names. Printable prefix strings alone do
not establish a binary class; a PDF token after a bounded preamble requires a
PDF object body and end marker, even if the file has trailing padding.
Remaining content must be valid UTF-8 text without binary controls; a UTF-8 BOM
and CRLF are valid. It detects text, PDF, image, audio, video, and office
classes. A contradictory declared class is a typed ingest refusal, surfaced
as HTTP 422. Binary bytes D132 cannot classify may use a D138 registry hint
for a family D132 does not cover (for example, an archive); the converter
validates that format. Remaining unrecognized binary is stored as a D138
binary card. The stored
`content_objects.mime` is the decided MIME and drives the D38 route, D51
original class, and, through that route, a metered deployment's cost class
(D150). Text flavours cannot be settled by
bytes: once bytes establish text, D133/D138 registry hints select Markdown,
CSV, code, configuration, or plain text and their distinct processing postures.
Textual email, notebooks, RTF, and delimited data keep their registry families;
their converters validate full structure. Recognizable HTML markup retains
`text/html` for its separate converter, and an empty upload
is valid text in every deployment; a metered one bills it at the per-source
minimum (D150).

For legacy OLE office containers, bytes establish the office class and the
declared legacy office MIME selects its subtype. Otherwise an ambiguous OLE
container is refused. This check identifies the class; a converter still
validates the complete format before producing evidence.

**Class on every object (D132).** The object-store write contract requires an
explicit class. Original video/audio/image bytes are `hot`, other originals
`cold` as D51 specifies. Canonical Markdown, source maps, blocks, PageIndex,
derived media, and all P3 copies/indexes are `hot` because queries and agents
read them. Conversion checkpoints and private K transcripts are `cold` because
only retry/replay paths open them. The policy lives in `core/storage_routing.py`;
storage adapters record the chosen class on every write.

(`content_hash` = sha256 of the raw bytes — the canonical *byte* identity, deduplicated in
`content_objects` and used in the path; the *logical document* identity is the lineage's
`(source_kind, source_ref)` — D55.)

Canonical objects are **ID-addressed** (`doc_id` + `content_hash`), never title-addressed — titles
change, collide, and contain hostile characters. Human-readable names live only in the corpus
filesystem projection (§6), whose metadata records these stable identities.

**The Postgres / object-store split (D37).** Postgres is the E-plane *ledger*; object storage holds bodies. The
rule is precise:

> **Postgres never stores document *body* text or Markdown bodies. It stores compact, query-critical
> metadata** — document identity, versions, processing state, artifact URIs, hashes, costs, and the
> *section index* (titles, paths, roles, spans, summaries). Bodies live in object storage.

This keeps Postgres lean (1M document bodies would bloat it for nothing) and puts the text where it
belongs — in object storage; P3 supplies the reader copies. `documents`:

```
documents(          -- the LINEAGE (D55): the logical document over time
  doc_id, deployment, source_kind, source_ref,     -- connector-native identity (Drive file ID, message ID, …)
  UNIQUE(deployment, source_kind, source_ref),
  versioning_mode,                                  -- snapshot (fail-safe) | living (D55)
  origin, current_version_id, title, first_seen_at, last_observed_at)

document_versions(  -- append-only observed snapshots of a lineage
  version_id, doc_id, content_hash → content_objects,  -- bytes deduplicated (stored once; converted once per toolchain, D65)
  version_no, source_version_ref, source_modified_at,  -- → derived claims' asserted_at (D41/D55)
  current_representation_id → document_representations, -- the LIVE reading (swap-on-completion, D65)
  status, ingested_at, superseded_at)

document_representations(  -- IMMUTABLE conversion outputs (D65): one row per (version, toolchain) reading
  representation_id, version_id,
  route, converter_*, blockizer_version, structurer_*,  -- what produced this reading
  markdown_uri, pageindex_uri, conversion_uri, blocks_uri, meta_uri,  -- …/<content_hash>/<representation_id>/…
  markdown_hash, manifest_hash, pageindex_hash, placement_version, section_index_version,
  status, created_at)                                    -- never updated after ready; never overwritten
```

PageIndex, summaries, and placement are **LLM-derived, non-deterministic** E0 state, so every
producing step is **versioned** (`structurer_*`, `placement_version`) and its output persisted. Like
every non-deterministic stage (D7), structure is **replayed from stored state** on rebuild and only
re-run on a version change; downstream E1/E2/P3 invalidation keys include `structure`/`converter`
versions, so a converter or structurer bump reprocesses exactly the affected documents.

Re-ingesting an identical file is a `content_hash` no-op (this is the *only* surviving "dedup" — as
idempotency, never a value tier, per D25) — except that identical bytes arriving under a different
name, title or path are a **metadata observation**: no version is created and the lineage's
`title` is unchanged; a `document_names` row is added so `search_documents` (D134) finds the file
under its new name. **A changed file from a watched source is a new
*version* of its lineage** (D55): connectors debounce rapid edits to one ingested version per
stability window; unchanged chunks of the new version **reuse** their prior extraction and
embeddings via the content-addressed keys (D56), so the cost of a version is proportional to
the edit, not the document — full design: `evidence_lifecycle_design.md` §2/§6.

**Deletion / forget (cascade).** Normal removal tombstones the lineage, purges its unshared **raw +
artifacts** objects, ends its testimony currency, and reaches K through the citation tombstone path
(lifecycle §8; schema §13.1). Irreversible hard-forget is D74's separate fail-closed workflow: it
also scrubs retained source payloads, explicitly purges P1 and resets P3 and its controlled
serving copies before repair, erases affected K history, and records a portable restore
barrier. A rebuild alone is not erasure (§6.7; `hard_forget_design.md`).

## 3. The conversion module (raw → Markdown) — D38

A **configurable, pluggable** converter — the boundary is library-shaped and reusable; its quality
gates everything downstream:

- **Interface (refined by D57, generalized by D65):** `convert(bytes, mime, hints) ->
  { document.md, source_map, derived_assets[], manifest }` — converters are heterogeneous
  (Mistral OCR exposes only per-page Markdown), so they emit only what every tool can deliver:
  the Markdown, a **source map** (which char-intervals of `document.md` came from which part
  of the source — for paper that is the old *page map*; for media it maps to typed locators:
  time ranges, image regions — `media_design.md` §4; nullable for pageless/unmappable
  formats), `derived_assets` (extracted images, keyframes, thumbnails, interchange
  transcripts: id, bytes, locator, caption — landing in `media/`, linked from the Markdown),
  and a **manifest** — the route's complete self-account: route taken, full component graph
  (models + versions), execution context (which adapter, local vs provider — D61), output
  hashes, coverage policy + result, gaps/warnings, and the range→derivation labels (required
  field list: `media_design.md` §2; labels: §5). **Blocks are not converter output**: the
  deterministic **blockizer** (one shared parser, `blockizer_version`) derives the block
  sequence from `document.md` downstream of every route, emitting `blocks.json` — see
  `e1_chunks_design.md` §2. Offsets into `document.md` are load-bearing (E2 grounding, D32;
  chunking; PageIndex); source locator provenance is best-effort per converter capability.
- **Routing by format family (D133).** An engine-shipped **format registry** maps every
  recognized family to a posture — **full** reading, **profile** (a description of a data
  file, not its rows), or **card** (a deterministic file card) — and to the converter
  implementing it. Deployments overlay
  the registry (add or override routes, for example provider-backed ones); they never replace
  it. The shipped families, detection order and profiles are bound in
  [`workspace_formats_design.md`](workspace_formats_design.md) (D138); the framework and
  locator kinds in [`format_conversion_design.md`](format_conversion_design.md). **Media
  routes (D65/D115),** bound in `media_design.md` §2, run when a deployment configures their
  providers; by default images, audio and video get cards (D138): audio → **diarized ASR**
  (transcript as document.md, one block per speaker turn); video → ASR + **adaptive
  keyframes** + optional VLM shot notes; every supported image → dedicated OCR plus an
  independent vision-LLM description call. Converters are versioned — a model or parser
  upgrade is a `converter_version` bump, flowing the processing-driven lifecycle ruleset
  (`evidence_lifecycle_design.md` §3).
  **PDFs (D139) always use OCR for every accepted page**, including pages with
  selectable text. The PDF route neither extracts text layers nor switches
  between text extraction and OCR by page; an unconfigured OCR provider parks
  conversion under D117.
- **Versioned** (`converter_version`): a converter or routing change re-converts the affected docs (a
  batch keyed by version), which rebuilds everything downstream — the D7 rebuildability discipline
  applied to the foundation.
- **Missing conversion routes park work — D117.** E0 accepts and stores otherwise
  admissible uploads even when no conversion route exists. It passes the configured
  route set into the catalog transaction, which checks the effective
  `content_objects.mime` (first-write-wins per content hash). Convert work starts
  `pending` with `defer_reason='no_route'`: claims exclude that reason regardless
  of `not_before`, so waiting spends no attempt or conversion usage and does not
  mark the version failed. `UploadIngestor.routable_mimes` is required; HTTP,
  local MCP and connector ingestion inherit the same behavior.

  After configuring a converter, `remember ops resume-no-route --deployment …`
  releases only parked, live versions whose effective MIME exists in the current
  configured route table. Adding one route cannot release other unsupported
  formats. A worker that still lacks the route parks the item again and refunds
  its just-started attempt; converter content errors remain ordinary failures.
  This handles configuration skew without a dead-letter loop. Matching uses
  the registry's normalized routing key (D133). Parking covers recognized families
  whose converter needs an unconfigured provider or is not yet built; every file is
  stored (D138). The admission and managed text-classification
  contracts remain in force; storage acceptance does not assert processing readiness.

  **Connector completeness:** a live observation parked with `no_route` keeps
  its sync cycle unfinalized, because unread content cannot justify source-absence
  retraction. The cycle remains completed but not finalized until processing
  resumes and finishes, or the parked version/lineage is explicitly deleted.
  Deleted parked observations no longer block the document-work barrier; other
  unfinished work still does. Independent source-tombstone cascades continue.
  This can retain a cycle's absence-based closures indefinitely and is the
  deliberate cost of not deriving absence from unsupported content.

  Raw availability is separate from processed currency: the raw APIs expose a
  stored parked version. P3 publishes only the served completed version (§6.2); a
  parked successor does not replace its original or derivatives on the mount, and a
  lineage with no completed version has no corpus folder. This replaces D117
  latest-stored-original browse publication, without changing storage or parking.
  Analysis and alternatives: [stored originals and parking](../analysis/unroutable_mime_parking.md).

Output Markdown → artifacts bucket; the source map + manifest + converter metadata → `conversion.json`; the
blockizer's `blocks.json` beside them; Postgres gets only the URIs + `converter_version` +
`blockizer_version`. The convert sub-worker runs converter-adapter + blockizer as one stage
(the blockizer is deterministic and cheap — no separate queue step). **The conversion route is
pinned per lineage** (D57): the router never silently picks different converters for different
versions of one lineage — a route change is a deliberate version bump.

## 4. PageIndex — per-document structure — D39

**What PageIndex is** (verified from the tool): a per-document hierarchical *table-of-contents tree*
(`node_id`, `title`, `summary`, nested `nodes`, page/char spans). It is marketed as a vectorless,
reasoning-based retrieval system; **for us it is structure, not a retrieval engine** — we keep the
chunk + embed + graph hybrid (D8/D9). We use its **tree + section roles + spans** to:

- give E1 **section-aware chunk boundaries** (never split mid-section; one chunk = one topic),
- give E2 the **section path/role signal** it consumes (D25/D31 — Selection drops
  references/boilerplate at proposition grain),
- carry **per-section summaries** (kept — see below), and
- carry a **placement hint** for the corpus filesystem (§6).

`role` is a small enum (extensible): `body, abstract, introduction, results, methods, discussion,
conclusion, references, appendix, table, figure_caption, nav, boilerplate, legal`. The structurer
assigns it; E2 uses it to drop low-value roles at proposition grain.

**Sections are persisted on the block grid (D57).** PageIndex's LLM-drawn spans are snapped to
block boundaries by a deterministic post-step (backward-snap, partition enforcement, nesting
validation, degrade-to-parent) and stored as **block ranges** — sections never cut through a
block, and blocks are never derived from sections (`e1_chunks_design.md` §3).

**Every document gets a section structure — unconditionally.** The *output contract* is that every
document has `document_sections` rows; whether the expensive PageIndex *tool* runs is an
implementation choice, not a contract gap. A short/simple document gets a **single synthetic root
section** (full-document span, `role=body`, a one-line summary, root path) — so E1/E2/P3 always have
a path/role to read.

**Where it lives — both, by role:**
- **`pageindex.json` sidecar** in the artifacts bucket — the reproducible, mountable artifact (the
  "JSON next to the Markdown"), and
- **Postgres `document_sections`** — the queryable index, because E1/E2 query path/role per chunk and
  must not re-parse JSON each time:
  ```
  document_sections(
    section_id, doc_id, parent_section_id, node_path,    -- e.g. '0.2.1'
    title, role, char_start, char_end, ordinal,
    summary, placement_path, structurer_version)
  ```

**Summaries: kept** (not dropped). They are per-*section* (cheap, not per-chunk), and they earn their
keep as **context, never as facts**: they feed agent navigation, E2 orientation (bundle), and
"why-was-this-selected" explainability. **D80:** they do **not** feed default E1 embedding text
(the embedding-input policy is deterministic location facts + body; see
`e1_embedding_input_policy.md`). They are versioned with the structurer. Crucially, the
*global* high-level picture of the corpus is the **K plane's** job (compiled knowledge over
relations/evidence), **not** these per-document summaries — so the high-level picture never depends
on summary quality, but the summaries usefully enrich local navigation.

**Optional topic hint (D39 amended by D155).** Root placement returns
`placement: null | [topic_segment, ...]`, never a type, document filename or
absolute path. The model chooses `null` when a topic adds no useful orientation;
for example a routine message can live directly in `email/`, while a report can
suggest `["finance", "annual-reports"]` beneath deterministic `pdf/`. P3 validates
and normalizes segments (§6.3). The persisted hint is the sole topic input; there
is no second model or whole-corpus topic reconciliation. Section structure still
serves document navigation, not additional corpus leaves.

### 4.1 Scalable structure route (D79, 2026-07-27) — deterministic skeleton, bounded summaries, orientation-only consumption

> **D138:** a representation with no claim-eligible range (search-only text, a data profile, a
> card) takes only the deterministic skeleton: no model call for skeleton checks, roles or
> summaries. Model-written structure is for prose.

The shipped route is a **single schema-constrained LLM call** over up to `max_prompt_chars`
of `document.md` (default 200K; documents under `min_blocks_for_llm` skip the call) returning
the entire tree — every span, role, and summary — in one response, with char offsets snapped
backward onto the block grid. Three structural cliffs are visible in that shape (observed code
risks; the per-cliff failure rates are not yet measured in-repo):

1. **Offset arithmetic without anchors.** The model proposes `char_start`/`char_end` as
   numbers; the snap makes any input *well-formed* but cannot make it *correct* — there is no
   title-text search or recovery (`section_snap.py` snaps positions only), so a miscounted
   offset yields a well-formed wrong tree with no error signal.
2. **Long-context structured output in one shot.** One call reads up to ~200K chars and must
   emit the full tree; provider structured-output unreliability is documented at
   `locomo_benchmark_design.md` §2.4, and long-context neglect plus output-token ceilings on
   large trees are known model failure modes. The shape confines the seat to strong models
   (default: the extraction-tier `gpt-5.6-luna`).
3. **The truncation cliff.** Content past `max_prompt_chars` is unseen by the model —
   mechanically it still lands under the root or the last proposed sibling, but its internal
   structure is never proposed.

D79 replaces the shape, not the contract (§4's output contract — `document_sections` rows for
every document, sidecar + PG index, block-grid spans, non-null roles, a placement hint — is
unchanged):

- **Skeleton: parse, don't ask.** A deterministic parser builds the section tree from the
  markdown heading blocks the conversion already emits (`BlockType.HEADING` — heading starts
  are block boundaries by construction). The converter contract (D38) is amended to state it
  explicitly: conversion **preserves or emits heading syntax when the source has structure**
  (it cannot invent structure from OCR text or `<div>` soup — that is the fallback's job).
  *No second tokenizer (owner requirement 2026-07-28, resolved by review): the repository's
  canonical GFM blockizer already runs the maintained `markdown-it-py` under D57's
  one-shared-blockizer rule — the owner's no-reinvention requirement is satisfied by REUSING
  it, never by introducing a parallel parse. The skeleton parser consumes the blockizer's
  heading tokens; where it needs raw heading level and a normalized title, that metadata is
  exposed on the heading blocks under a `blockizer_version` bump. The skeleton layer owns
  only tree construction and degradation.*
  The LLM fallback triggers on **insufficient heading density or an oversized unsegmented
  leaf** — both defined by exactly the formulas the sanity-check stat schema below gives
  them, one definition with two consumers — not only on zero headings (a media transcript
  with one `## Transcript` wrapper heading still needs segmentation). The fallback returns
  anchor **strings** — exact, block-contained substrings with occurrence-order
  disambiguation, resolved by deterministic search — never raw character offsets; an anchor
  that resolves ambiguously or not at all degrades to the enclosing parent, mirroring the
  snap's degrade-to-parent rule.
- **Section keys (D140).** A heading may end with a Pandoc/kramdown attribute block
  (`## 4. Per-diem allowance {#per-diem}`). The parser takes the block's `#identifier` as the
  section's stable `section_key` (1–200 characters from `[A-Za-z0-9_.:/-]`), strips the block
  from the title, and stores two deterministic hashes on every section: `own_content_hash` (the
  section's own blocks) and `subtree_content_hash` (its whole span, children included — what
  "this section changed" means). Sections that predate D140 get the hashes from a
  deterministic backfill over stored `blocks.json`; a new parser generation is one D56/D65
  extraction-basis rollover for each lineage's next version. Keys are unique per structure generation (a duplicate keeps the
  first heading and records a structure warning); reads resolve a key in the version's current
  representation's current structure generation; model-anchored fallback sections get no key.
  Keys identify a section across versions for section history and cross-references
  ([D140 design §4](effective_time_and_section_references_design.md#4-section-keys)).
- **Fallback section run-merging (D137):** When models propose section anchors in conversational
  transcripts or dialogue documents, individual dialogue turns (e.g. Joanna asking "So what's
  your favorite game?" at Block 61 and Nate answering "Yep! I'm currently playing..." at Block 62)
  are frequently proposed as section headings. If unresolved, each turn becomes an isolated
  single-block leaf section (`block_start == block_end`). Because E2 claim extraction (`e2.py`,
  `_neighbour_text`) inspects surrounding context only within the *same section*, this splits
  question turns from their answers, breaking cross-turn anaphora resolution (D131).
  *Rule:* In `resolve_fallback_skeleton()`, contiguous runs of single-block leaf sections are
  identified. If followed by an immediate next leaf section without subsections, the run is merged
  forward with that succeeding section into a single section spanning `[run_start, next_leaf.block_end]`.
  If at the end of the document or followed by a section with subsections, the run collapses into a
  single merged section spanning `[run_start, run_end]`. In all cases, the merged section is titled
  by the proposal at `run_start` (its first block), so title, heading level, and starting position
  remain aligned without in-place mutation. Sections with subsections are never absorbed and never
  absorb runs, protecting hierarchical structures (e.g. `PART ONE` and `Chapter 1` under D57).
  *Costs and Boundaries:*
  1. Flat unnested chapters: If a single-block section such as `PART ONE` is followed by a flat
     sibling leaf such as `Chapter 1`, the two merge and the section keeps only the `PART ONE`
     title. Hierarchy protection relies on the model emitting subsections.
  2. Long micro-turn runs: A run of 200 consecutive single-block turns collapses into a single
     section `[0..199]`, representing an honest flat document rather than 200 micro-sections.
  3. Non-contiguous boundary turns: If a question is the final block of a multi-block section and
     the answer begins the next section, or a single-block turn is stranded at document end without
     an answer, run-merging does not bridge across multi-block sections. Such cross-boundary
     dialogue remains a documented boundary of section-scoped context.
- **Skeleton sanity check (2026-07-28 addition, revised after Grok + Codex review): a judge,
  never a proposer.** Density can look healthy while the parsed tree is nonsense — a
  complicated print template whose running headers, TOC pages, or scrambled heading order
  survived conversion as syntactically valid headings. The exact state machine:

  ```
  parse candidate skeleton → compute + persist stats → density / oversized-leaf gates
    (a gate demotion enters the SAME fallback → TERMINAL-check sequence below —
    every fallback-produced tree faces the terminal judge, however the
    document got demoted; clarified 2026-07-28 with Wave-1 implementation)
    → eligibility (≥ MIN_CHECK_SECTIONS non-root sections; below it: outcome
      not_run_short, skeleton accepted)
    → check call → coherent: keep parsed skeleton
                 → incoherent_*: demote to LLM fallback route
                       → fallback tree → TERMINAL check call
                             → coherent: keep fallback tree
                             → incoherent_*: synthetic root (honest no-structure
                               beats a plausible-wrong tree; no second fallback,
                               no cycle)
    → roles → summaries → placement   (on whichever skeleton won)
  ```

  1. **Stat schema — normative, versioned (`stats_version`).** Heading universe: the
     candidate skeleton's sections in document order, synthetic root excluded; `n` its size;
     all stats null and the check `not_run_short` when `n < MIN_CHECK_SECTIONS` (starting
     point 3, a named versioned constant like every floor here — "no thresholds" means no
     stat-to-verdict rules, not no operational eligibility bounds). `level` is the RAW
     markdown heading level from the blockizer's heading tokens (materialized tree depth
     would make jumps identically zero — the parser assigns parents one edge at a time);
     exposing raw level + normalized title on heading blocks is a `blockizer_version` bump.
     `direct_body(s)` = the section's span minus its heading block and its children's spans.
     Titles normalize as NFKC + casefold + whitespace-collapse, digits kept. The stats:
     - `duplicate_title_ratio` = 1 − distinct/n over normalized titles; plus
       `max_title_multiplicity` and `sibling_duplicate_ratio` (duplicates among same-parent
       siblings) — the global ratio false-fires on legitimate per-chapter "Summary"
       headings, the sibling form does not;
     - `level_jump_count` = Σ max(0, level_{i+1} − level_i − 1), consecutive document order;
     - numbering: leading tokens by recognized scheme (`3.2` arabic-dotted, roman, `A.`
       alpha); `numbering_coverage` = numbered/n; `numbering_inversions` counted ONLY within
       same-scheme same-parent runs (a chapter restart is not an inversion);
       `numbering_scheme_switches` counted separately;
     - `tiny_section_ratio` = |direct_body < TINY_FLOOR| / n (starting point 80 chars) and
       `zero_direct_body_ratio`;
     - `oversized_leaf_ratio` = largest leaf span / document span — the same formula the
       oversized-leaf demotion gate uses;
     - `heading_density` = n per 10K document chars — the same formula the density gate uses;
     - `title_shape`: p50/p95 normalized-title length, fraction over LONG_TITLE (starting
       point 120 chars), fraction with letter-category ratio < 0.5, `empty_title_ratio`
       (character-class stats are script-dependent — reported as-is, no language-neutrality
       claim);
     - `max_sibling_fanout`.
  2. **One budget-bounded check call** on the **skeleton-check seat**
     (`REMEMBERSTACK_SKELETON_CHECK_*`, D70 binding, default flash-class). Input: document
     title (capped), source kind, the stats block, and ordered `(level, title, direct-body
     chars)` lines — titles ellipsized at LONG_TITLE, **never section content**, under a
     hard total prompt ceiling (versioned constant). When the tree exceeds the ceiling, the
     line sample is **anomaly-exemplar first, deterministic**: the most-repeated titles, the
     largest level jumps, the smallest direct bodies, the largest leaves, then head and tail
     — naive head+tail alone would hide mid-document template corruption, the motivating
     case — with omitted-line counts stated in the rendered block, and the sampled input
     hashed into the check record.
  3. **Output — one closed enum, §2.4's own lesson** (paired verdict+reason fields allow
     contradictory states; the schema makes them unrepresentable):
     `coherent | incoherent_repeated_boilerplate | incoherent_heading_sequence |
     incoherent_junk_titles | incoherent_over_fragmented` — the model picks the PRIMARY
     defect; `additionalProperties: false`; no free text, no confidence, structurally no
     ability to propose structure. (`incoherent_heading_sequence`, not "scrambled order" —
     the check sees heading order only, never body order.)
  4. **Outcomes and fail-open.** The persisted `check_outcome` is its own closed set —
     `not_run_short | coherent | incoherent_<reason> | provider_error | invalid_response` —
     so an outage is never bookkept as coherence; unchecked documents count in every audit
     denominator. On `provider_error`/`invalid_response` the parsed skeleton is kept
     (fail-open) because the check is a **non-authority guard**: fail-closed would convert
     every provider blip into extraction-tier fallback traffic corpus-wide. (Document
     survival is NOT the argument — the fallback path also never fails a document.)
  5. **Provenance: a fifth generation.** The D79 split gains `skeleton_check` alongside
     skeleton / roles / summaries / placement. Each run appends a document-level,
     append-only check record (D52): candidate-skeleton hash, `stats_version` + stats,
     sampled-input hash, `check_outcome`, checker component version and model/prompt/schema
     hashes, provider-failure envelope, cost attribution. The final skeleton generation
     records the selecting check record and a route tag (`parser |
     parser_demoted_check | fallback_density | fallback_leaf | fallback_after_check |
     synthetic_after_check`). A checker bump appends a new record and mints a new skeleton
     generation only when the route flips. D53 note: the deterministic parser has no
     producer model family — recorded N/A, not silently skipped.
  6. **Audit and earned gating.** Primary surfaces are the ones this design creates: route
     tags, persisted stats + outcomes, and structure canaries (role distribution,
     section-size histograms, Selection drop mix by role), validated against a **sampled,
     labeled skeleton-quality set** (human or independent audit — false negatives need truth
     labels). The #161 loss ledger is a supplementary downstream correlative only — a bad
     tree can mis-role sections so losses never reach the Claimify ledger at all. Stats may
     earn gate duty (skip the call when uniformly clean, demote without asking when
     egregious) only from that labeled data; this design ships no stat-to-verdict
     thresholds because they would be invented, not measured.
  Non-goals: the check judges the **tree**, not the text under it — a document whose
  intra-section reading order was scrambled by conversion needs the conversion-layer fix
  (layout-aware PDF/OCR, issue #168), which is its own track. Cost: one check call per
  eligible parser-path document; a demoted document pays check + fallback + terminal check.
- **Roles: deterministic first, classifier second.** The parser cannot produce the §4 role
  enum, and roles are load-bearing (Selection's low-value drops, crossref cite mining from
  `references` sections). Role assignment becomes: (1) deterministic normalized-title rules
  ("References"/"Bibliography" → `references`, "Abstract" → `abstract`, "Appendix" →
  `appendix`, media wrapper headings → their fixed roles, …); (2) a bounded title-only
  classifier call for headings the rules do not decide; (3) explicit `body` on failure. The
  role pass carries its own generation version.
- **Summaries: bottom-up, bounded by construction.** Leaf sections get a one-line summary
  from a dedicated **summary seat** (D70 port binding, default flash-class). Bounded means
  bounded: a leaf larger than the summary-call token ceiling is sharded at block grain and
  reduced; a parent call reads its **own direct blocks plus its children's one-liners** (a
  chapter's preamble is content too) with balanced fan-in when children are many. Calls are
  parallel and cached per section; the cache key hashes the ordered rendered block strings,
  rendered child lines (path + capped title + summary), model, prompt, generation parameters,
  and summarizer version. This is the call shape where cheap models are reliable: bounded
  context, no arithmetic, no giant output. The versioned token estimate is whitespace-based;
  the companion character ceiling is the hard bound for whitespace-poor/CJK/minified input,
  and every rendered call must fit both. **Degradation convention:** `summary_version` is
  non-null only when every section
  in the generation has a summary; any failed section call leaves that section null (and any
  ancestor that cannot receive all child one-liners null), may preserve successful summaries
  in independent subtrees, and makes the generation's `summary_version` null. Because root
  placement is emitted by the same complete reduction, `placement_version` is non-null iff
  `summary_version` is non-null and the root call returned a valid placement
  result. A deliberate `placement=null` is a successful result: keep its
  `placement_version` even though `placement_path` is null. On failure both
  placement fields are null. Thus an intentional no-topic answer is distinct from
  degradation and is reusable, rather than retried indefinitely.
- **Placement rides the root reduction.** The one-shot call also produced the D39 placement
  hint; that responsibility moves to the **document-level (root) summary call**, which sees
  exactly what placement needs — the title, source kind, and the child one-liners. On the
  degraded path (no summaries), placement is null and the P3 projection falls back to its
  type-based default, as it already must for failed structuring.
- **Consumption: orientation only, never load-bearing.** Summaries enter the **E2 bundle** as
  the D31 "section path + summary" element — target summary plus ancestor one-liners, so a
  chunk in subchapter 8.2 sees the book's and chapter 8's lines — for **orientation only**.
  **D80 supersession:** summaries do **not** enter default E1 **embedding text** (no summary→
  embedding-header channel on the product path). Two hard rules keep this sound:
  1. **A summary is never a grounding source.** Summaries are abstractive LLM text; if
     `added_context` could cite them, an invented phrase in a summary could be laundered into
     a claim through the membership-only D32 layer-2 check. Allowed grounding kinds follow
     D80: typed source/connector/deterministic location elements + body/neighbours — **no
     `summary` kind**, and free-form location headers are not grounding members. The model may
     *read* summaries to choose an interpretation; any text it *adds* must still come from
     source-derived elements.
  2. **Summaries are excluded from extraction-correctness inputs.** They do not enter
     `extraction_input_hash` (the same exclusion D56 already applies to LLM-derived context),
     so a re-summarization — including an ancestor summary changing because a sibling leaf
     was edited — never silently invalidates or re-extracts unchanged chunks. Better
     summaries improve *future* extractions; they never fan out into document-wide
     reprocessing.

  **Accepted second-order channel (2026-07-29, Wave-3 review).** Because summaries feed the
  E1 prefix input and the stored prefix is itself a quotable `added_context` element, a
  summary-informed prefix is a bounded second-order path by which content from
  NON-neighbouring sections (a bottom-up parent one-liner distills sibling subtrees) can
  reach the grounding surface — a reach the `_neighbour_text` same-scope rule deliberately
  forbids for direct quotation. Status quo on *quotability* (the prefix was always LLM
  text), a widening of *reach*. Accepted because the prefix's honesty was always layer-3/4
  audit territory; mitigated in-prompt by instructing the prefixer to use summaries only to
  DESCRIBE WHERE the passage sits, never to restate a summary's factual assertions.
- **Provenance and migration.** `structurer_version` splits into generations: skeleton
  (deterministic parser + fallback contract), **skeleton check** (the sanity-check bullet
  above), role pass, summary seat, and placement — each hash-stamped per D12. Structure/summary generations are immutable with a current pointer
  (the `(version_id, node_path)` first-write-wins rows and the write-once `pageindex.json`
  cannot be edited in place); a regeneration writes a new generation and moves the pointer,
  and existing deployments' rows are backfilled as a legacy generation. Sidecar URIs are
  versioned accordingly. `REMEMBERSTACK_STRUCTURER_*` narrows to the fallback
  structure-proposal seat; the summary seat gets its own binding. **Refines D71.**

Cost at scale: the shipped route can bill the structurer seat up to ~50K input tokens on a
near-cap document; D79 is a parser plus bounded flash-class calls — one skeleton-check
micro-call per eligible parser-path document, the per-section summary calls, and (for demoted
documents only) the fallback structure seat plus a terminal check — expected — to be measured,
not asserted — to be substantially cheaper on long documents and no worse on short ones (a
skeleton under `MIN_CHECK_SECTIONS` calls no checker; a document below `min_blocks_for_llm`
previously called no structurer either). Summary quality is judged only where
summaries are consumed (Selection drop quality, prefix quality, #150 scorecard canaries), not
in prose.

## 4A. Cross-references — the `crossref` sub-worker

> **D138:** for a representation with no claim-eligible range, cross-reference detection stays
> deterministic; the ambiguous residue is left unresolved instead of going to a model.
>
> **D140:** references belong to a source *version*, may name sections by key, bind their
> target `floating` or `pinned`, and are either extracted here or supplied by the caller. The
> complete contract — schema, kinds, binding, supplied reference sets, reading operations —
> is [the effective time and section references design §6](effective_time_and_section_references_design.md#6-cross-references);
> this section keeps the extraction rungs.

The last E0 sub-worker records how documents point at each other — the raw material for the
live `document_crossref` graph edges, the `document_references` operation, and one source of
the E2 bundle's entity hints (design-review F7). Product: `document_crossrefs` rows, one per
reference **made by one source version** (`from_version_id`), with optional source and target
section keys, a target named by source identity and resolved to `to_doc_id` when ingested,
and kinds `cites | links_to | attaches | replies_to | refers_to | amends | implements`.

The sub-worker has two inputs and writes one table:

- **Supplied references** (`origin = supplied`): each accepted PUT of a caller-provided NDJSON
  reference set is a new *generation*, stored as an artifact of the version (D140 §6.3). The
  generation is validated against the version's structure all-or-nothing (an unknown source
  section key rejects the set, never broadens a reference to document grain) and materialized
  deterministically; no model is involved. Pinned targets
  name the target version's immutable `version_key`.
- **Extracted references** (`origin = extracted`), below.

**Extraction — deterministic per kind:**

- `links_to` — URLs and links in the converted Markdown (`conversion.json` blocks keep the
  offsets); a link whose resolved target carries an anchor (`page#heading`) becomes
  `refers_to` with `to_section_key` set;
- `attaches` — container relationships known at ingest/convert time (e-mail attachments,
  archive members);
- `replies_to` — thread metadata (e-mail `In-Reply-To`/`References` headers, chat thread ids);
- `cites` — citation strings, mined primarily from PageIndex `references`-role sections: DOIs,
  arXiv ids, ISBNs, plus deployment-specific citation grammars configured per deployment,
  like the D38 converter routing table (e.g. case-law citations or standard numbers).

**Resolution — cheap-first (the D4 discipline).** A reference resolves to an ingested document
via exact keys first (source identity `(source_kind, source_ref)`; normalized URL ↔
`documents.source_uri`; DOI/arXiv id ↔ document metadata; `content_hash` for attachments), then
fuzzy title match (`pg_trgm` against `documents.title`, recall-first floor), and only the
ambiguous residue goes to a small-model rung ("is citation string X document Y?"). Below
threshold the row keeps `to_doc_id = NULL` — a cited-but-not-ingested reference: real
provenance, no graph edge. Extracted references are always `floating`; target *versions* and
*sections* are resolved at read time by a temporal join with the reading scope (D140 §6.2).

**Late binding.** Dangling references are not dead: when a new document is ingested, its
identity keys (source identity, URI, DOI/ids, title) are matched against unresolved crossrefs —
one indexed lookup on the ingest path — so earlier documents' references bind to it
retroactively. Pinned version keys and section keys need no binding step: they resolve at
read time. No periodic sweep; resolution rides the write path in both directions.

Extracted rows are idempotent on the source `version_id`, its representation and the
crossreferencer version (not on `content_hash`: an A→B→A version needs its own rows); versioned because the fuzzy rung is non-deterministic; the citation `context` snippet
is stored for audit. Every row belongs to a generation — one per source `version_id` and origin
(supplied: per accepted PUT; extracted: per representation and crossreferencer version) — and
only the active generation is visible; a new generation replaces it atomically, extracted ones
together with the D65 representation swap. Workers are idempotent on the generation. Execution
class (D52): deterministic first, one small-model rung for the extraction residue — LLM
spend scales with ambiguity, not volume (D4). A deployment that supplies references for a
source kind may disable extraction for that source kind by configuration.

## 5. Mounting — the corpus is the customer filesystem

Customers mount **corpusfs read-only**. Its document folders contain actual bytes,
not symlinks, pointer stubs or a union of E0 mounts. The engine writes through the
pipeline; PostgreSQL and immutable raw/artifact objects remain authority. K is a
standalone git repository, outside this filesystem; there are no K links or entity
folders in P3. API/CLI byte access and `source_open` retain unmounted parity.

**Storage.** Reuse `adapters/selfhost/minio.py`'s S3 adapter, configured by endpoint,
bucket, region/signing and credentials, with D147 per-bucket settings. Supported
targets are mountable.io (S3 bucket name = filesystem id), AWS S3, GCS through
`https://storage.googleapis.com`'s S3-compatible XML API using HMAC keys, and MinIO
for self-host/tests. GCS interoperability and listing/multipart support are
[documented in the analysis](../analysis/corpus_document_folders.md#provider-facts-checked-on-2026-11-13).
There is no native GCS adapter. Bucket provisioning belongs to the composing
profile/operator; the engine does not assume that generic `CreateBucket` is the
way to provision every provider's filesystem.

Keep immutable `ObjectStorePort.write_bytes` create-only for E0. Add a narrow
mutable capability in `ports/object_store.py`, implemented by the same adapters:

- `replace_stream(key, chunks, storage_class)`: create or atomically replace one
  complete object from bounded chunks; a small file supplies one chunk.
- `iter_keys(prefix)`: bounded-memory, fully paginated key enumeration; empty
  prefix means this deployment's whole corpus store, used only by reconcile and
  forget/restore repair, never ordinary publication. Error on incomplete/repeated
  continuation tokens. Recursive deletion uses path boundaries (`path/`, never a
  sibling such as `path-other/`).
- Idempotent exact `delete(key)`; missing keys succeed. Reuse this implementation
  in the existing object/projection purge capabilities and verify absence by
  listing/reading, rather than adding another deletion service.

No rename, directory API, distributed transaction, CAS, or mandatory provider-side
copy is needed. Copy across independently configured raw/artifact/corpus endpoints
by streaming GET to replacement upload. Use multipart upload for large objects,
complete only after successful input/hash validation and abort own failed uploads.
On repair/forget, enumerate, abort and verify matching abandoned uploads when the
provider serves `ListMultipartUploads`. If that listing returns `NotImplemented`/501,
leave unfinished parts to the qualified provider's cleanup policy and return
`multipart_uploads="provider_managed"` evidence plus a warning. This verifies
published-object absence without claiming unfinished parts were checked or erased
immediately. Mountable's gateway expires abandoned uploads after seven days;
provider qualification records the deployed expiry/sweep guarantee. Other listing
errors and all abort failures remain failures. No whole-video buffering or durable
worker-local state. Replacement accepts the expected SHA-256 for object metadata
and exposes a metadata read for digest verification. A caller-supplied guard runs immediately
before each provider mutation, including multipart create/part/complete requests;
cleanup may abort its own failed upload even after losing ownership. Every
provider request has bounded connect/read timeouts; one multipart part
is the largest upload unit. A qualified adapter provides complete-object
replacement and read/write/delete/list consistency; mount caches have a separate
lifetime. Existing raw create-only
preconditions must also be qualified on each endpoint, not silently weakened.

**Managed mount CLI contract.** `remember mount DIR` obtains a one-time mount
ticket from remember.dev for the selected deployment/project, then starts
`mountable mount --ticket-stdin --json DIR`. Pass the ticket **only on the child
process's stdin**, close stdin after writing, and never put it in argv, environment,
logs, shell history or a temporary file. Invoke the executable directly, propagate
its JSON result/exit failure, and report missing `mountable` clearly. `remember
unmount DIR` delegates to the installed mountable CLI's unmount operation, reports
its result, and does not request another ticket. The remember.dev control plane
creates the read-only mountable session; its authentication route, ticket format
and session lifecycle implementation belong to the cloud repo. These commands
are a thin client wrapper, not an engine mount scheduler or new truth surface.

**Self-host.** Mount the configured corpus bucket using any S3 FUSE tool with
read-only credentials/options and bounded stat/list/data caching. A local
filesystem adapter serves the same live directory tree; it uses atomic file
replacement and stores class bookkeeping outside reader paths. Provider access
logging covers corpus-original reads as well as raw reads. API raw access keeps
its existing audit path; a plain local filesystem cannot audit arbitrary file
reads. No separate raw/artifact mount is required or advertised as the normal
customer interface. Engine-controlled sessions and serving caches must participate
in D74 purge acknowledgement (§6.7); changing bucket objects alone cannot flush a
client's cached bytes.

## 6. The corpus filesystem — a mutable P3 tree (D155)

P3 is a derived view over E0 and PostgreSQL: it holds no independent testimony,
validity or knowledge. Ordinary maintenance changes affected folders and indexes
in place. **D7/D40 snapshot rebuild plus `latest` pointer swap is replaced for P3**;
full repair reconciles this same live tree, not another snapshot layout.

### 6.1 Folder contents: reader files, with an explicit boundary

```text
<corpus bucket root>/
  llms.txt
  _index.md
  email/
    _index.md
    atlas/
      _index.md
      kickoff--8e74c2b9056df3a8c04e716b/
        _index.md
        document.json
        original.eml
        document.md
        pageindex.json
        source_map.json          # when emitted
        media/                   # when reader assets exist
          diagram.png
  pdf/
    _index.md
    finance/annual-reports/
      _index.md
      acme-2023--9bf3e4c0c2d4861075a6ef82/
        ...
  audio/                         # empty types still have an index
  ...
```

There is exactly one published folder per live completed lineage. Optional topics
determine its physical path; shard index pages bound navigation (§6.3). The export
is an allowlist, never a recursive copy of the artifact prefix:

| In the folder | Why it belongs |
|---|---|
| `original<suffix>` | Byte-identical original of the displayed version, copied from its recorded raw URI; full media included. Choose a safe extension from the validated format registry/MIME, `.bin` on unknown format |
| `document.md` | Exact immutable Markdown bytes of the displayed representation; no P3 banners, frontmatter edits or offset changes |
| `pageindex.json` | Exact active structure-generation sidecar: sections, spans, roles and available summaries for reading at section grain |
| `source_map.json`, when present | Exact representation source map so a reader can relate Markdown spans to original pages, times or regions |
| `media/<asset-name>` | Every reader asset in the conversion inventory: extracted/page images, crops, thumbnails, keyframes, and timing-preserving transcript/annotation interchange that a reader/player uses, including assets not linked in Markdown |
| `document.json` | Generated public inventory: `doc_id`, stable suffix, displayed version/representation/structure ids, original MIME/hash, file hashes, relative reader-asset names and locators, derivation labels, coverage gaps/warnings and deterministic publication identity. This is a documented reader record, not a copy of engine `meta.json`/`conversion.json` |
| `_index.md` | Generated orientation and a member table linking these files, with source date, stored root summary (or explicit absence), derivation disclosure and publication state |

The projection's versioned asset-kind registry classifies each converter kind as
reader or pipeline. Reader kinds include `embedded_image`, `page_image`,
`keyframe`, `thumbnail`, `crop` and transcript/annotation interchange; pipeline
kinds include `provider_response`. An unknown kind fails publication until
classified, instead of accidentally publishing internals or silently dropping a
useful asset. Custom conversion routes supply the same classification. Validate
safe relative names and reserve generated filenames; every local asset link in
copied Markdown must resolve to a reader asset. No manufactured per-keyframe
lineages. Preserve converter asset paths so relative links stay valid.

**Out:** `blocks.json` (chunking grid), `conversion.json` (full route/execution
manifest), engine `meta.json`, conversion checkpoints, raw provider response JSON
(even under `media/`), supplied/extracted references NDJSON and generations,
work/retry logs, extraction/audit payloads, vectors, historical representations
and historical versions. These are engine state, not reader files; the useful
source map, disclosure and warnings are exposed explicitly above. K contents,
links and entity indexes are outside this projection.

### 6.2 One coherent version, independent of a newer upload

Select `documents.current_version_id` and that version's
`current_representation_id`, with its active structure generation: the newest
nondeleted version whose processing completed under the deployment's enabled
layers (D55/D65/D143). A title, type, topic, original and derivatives always refer
to that reading. P3 completion is a separate capability and cannot be a prerequisite
for committing this pointer, which would create a trigger cycle.

If processed v1 is followed by stored/processing/parked/failed v2, keep **all of
v1's folder**, including v1's original. When v2 completes, publish its whole
reading. A lineage with no completed version has no folder; its stored original
is accessible by version through the existing raw API/`source_open`. This
explicitly replaces D117's latest-stored-original fields in P3, but retains
store-and-park, resumability, raw availability and the rule that upload never
promotes processed currency. Identical-byte metadata observations that merely add
`document_names` (D134) do not rename the corpus folder.

Version deletion republishes the surviving served version, or removes the folder
if none survives. Lineage/source deletion removes it. Effective-time queries
still use version-addressed source handles; the filesystem is the served version,
not an as-of or all-editions archive.

### 6.3 Type first, optional topic, safe names and bounded navigation

Apply these rules **in order**, using existing stored source kind, byte-decided
MIME, format family and conversion route; never infer a type from an LLM summary
or an unchecked filename. A card or missing provider does not erase the known
format. These are the fixed top-level document types, each with an `_index.md`:

| Directory | Deterministic match |
|---|---|
| `email/` | Email source kind or email family (`message/rfc822`, validated MSG/email route) |
| `chat/` | Chat/message-thread source kind or conversation route, excluding email |
| `image/` | Image MIME or `image` family |
| `audio/`, `video/` | Stored `audio/*` or `video/*` MIME, respectively; both belong to the `media` family |
| `pdf/` | `application/pdf` |
| `spreadsheet/` | `spreadsheet` or `delimited` family, including CSV/TSV |
| `presentation/` | `presentation` family |
| `document/` | `text`, `markdown`, `html`, `word` (including RTF), `notebook` or `ebook` family |
| `data/` | `dataset`, `code`, `config`, `archive`, `log` or `other_text` family |
| `other/` | Anything unmatched, including unrecognized binary cards |

The shared `media` family has no audio/video subtype of its own. The engine
preserves each media format's MIME and ships the same generic `card` route for
both, so that route cannot distinguish them. Media without an `audio/` or
`video/` MIME falls to `other`, rather than guessing video.

Registry subtypes extend these matches, not the set of top-level types. An email
attachment ingested as its own lineage follows its own format; its email source
relationship alone does not turn a PDF attachment into an email. Source-kind
matches above mean the item itself is a message/thread, not merely fetched by an
email/chat connector. Type-policy changes are versioned P3 changes.

Below the type use the persisted root placement hint, or no topic for `null`, a
failed/degraded call, or invalid input. Accept at most four topic components.
Validate separators and reserved names after NFKC normalization. For slugging,
decompose each component with NFKD, lowercase, remove combining marks, and
transliterate common non-decomposing Latin letters: `ß→ss`, `æ→ae`, `œ→oe`,
`ø→o`, `ł→l`, `đ/ð→d`, `þ→th`. Keep ASCII letters/digits, replace other runs
with `-`, trim and cap at 48 bytes. Empty/reserved components, absolute paths,
`.`/`..`, slash/backslash inside a component, or excessive depth reject the hint
as a whole. Scripts with no ASCII form therefore reject the topic. A valid
empty/no-topic result is explicit `null`, not an error.
The model may intentionally choose no topic; it never chooses the type or slug.
No LLM calls occur in the publisher or repair.

The leaf name is `<title-slug>--<suffix>`. Use the selected version's D134 title,
then its filename, then the lineage title as fallback. The slug uses the same
normalization, a 60-byte cap and `untitled` fallback, including titles with no
ASCII form. For example, `Smlouva o dílo – Žluťoučký kůň 2024` becomes
`smlouva-o-dilo-zlutoucky-kun-2024`. Define
`corpus_suffix(doc_id) = sha256(doc_id.bytes).hexdigest()[:24]`, using the UUID's
16 canonical bytes. Its 24 lowercase hex characters carry 96 bits: the accidental
collision probability n(n−1)/(2 × 2^96) is below 6.4 × 10^-14 even for 100 million
lineages, comfortably beyond the required millions. No suffix allocation,
reservation, uniqueness table or collision-extension protocol is needed. The
unkeyed hash needs no retained secret for restore replay. It remains computable
from `doc_id` after tombstone or source-metadata scrub, and never enters a paid-work
identity. `doc_id` remains the durable handle; the suffix identifies every folder
of that document across title/topic/type moves.

Navigation **index pages**, not document folders, split when a member table
exceeds B entries (starting point B=150, a measured/versioned projection setting).
Use deterministic radix pages over each child's key (`d:<suffix>` for documents,
`t:<normalized-topic>` for topic branches), encoded into base32. Each index keeps
its pages under a reserved `~/` subtree (for example `pdf/~/mqq/_index.md`),
with recursive splits under the page's own `~/` subtree. This lets publication
list obsolete pages without enumerating sibling topic or document folders. Each split has
at most 32 next-character branches; collapse single-child prefixes, recursively
split overloaded pages and merge when membership falls to B or below. All rows,
counts and page boundaries are computed from DB membership.

Physical folders remain at `<type>/<optional-topic>/<title-slug>--<suffix>/`.
Index pages link to those folders or real topic indexes; splitting/merging pages
rewrites only navigation metadata, never moves originals/media. Removing one
document therefore cannot force surviving document copies to move during forget.
Object-store prefixes can have many children; browsing uses bounded member pages,
while listing and suffix search use pagination. Converter-established asset paths
inside a document are preserved; large asset tables paginate similarly.

### 6.4 Deterministic indexes and stable addressing

Every navigation directory, including root, has `_index.md`: directory identity,
subtree counts, parent/child links and bounded member pages for its logical
children. A document row includes `doc_id`, title, source/date, displayed version,
stored PageIndex root summary or “no summary”, and its relative folder link. Topic/page rows include
counts and child-index links. Document `_index.md` lists reader files and media;
large asset tables use bounded continuation pages referenced from that index.
Tables are sorted by normalized title then stable id (branches by name), with
Markdown cells and link components escaped. Root `llms.txt` lists the fixed types,
counts, reading steps, version/move semantics and suffix lookup instructions; it is
bounded by the type taxonomy. It is the only `llms.txt` in this tree.

Index rendering uses SQL metadata and stored summaries, **zero LLM**. There are no
directory summaries, facets, `documents/<doc_id>/`, `by-source/`, `by-time/`,
`entities/`, duplicate view stubs, K links or monolithic id-to-path file. The ladder is
`llms.txt → type/topic/shard _index.md → folder _index.md → document.md/original`.

Folder paths change only when their type, topic or title changes; index pagination
does not move them. Consumers store `doc_id` (and a version/representation handle for a citation).
Resolve through MCP `p3_path`; for offline browsing compute `corpus_suffix(doc_id)`
and run `find <mount> -path '*-<suffix>/*'`, substituting its 24 hex characters.
This searches the mounted tree and may cost a full listing; there is no `_paths/`
id index, alias directory or persistent path map. Verify the folder's `doc_id`
and displayed version in `document.json` before opening it as a particular edition.
Suffix lookup can find the served folder even when temporal search selects another
edition or no edition is in force; it does not change those temporal rules.

For MCP/API/SDK `p3_path`, derive the desired relative folder from current DB
inputs, and return it **only** when the selected version is served and the nullable
`documents.corpus_published_revision` matches the current desired export digest
(§6.5). Otherwise return `null`, including raw-only, unpublished, updating, deleted,
historical and failed publications. The digest covers selected version,
representation, active structure, reader inventory/hashes, title/type/topic,
desired path and builder policy; it is computed from existing authority, not a
new durable revision counter. `served_version` describes authority currency,
independently of P3 progress. MCP descriptions and the consumption skill teach
re-resolution after moves.

### 6.5 Incremental work, publication order and reader observations

`publish_corpus(doc_id)` is idempotent: each invocation recomputes the desired
folder and files from DB authority. Projection-relevant transactions clear the
nullable publication marker and enqueue this unlaned engine stage in the same
transaction as served version/representation, active structure/placement,
canonical title/type or deletion changes. No event is needed per chunk or model
call. Policy changes seed affected documents through the same mechanism. A
no-folder selection means removal, not a missing trigger.

Use only the existing work ledger: each committed trigger has a fresh event UUID
as its `corpus_publication` target id and `doc_id` in its handler payload. No
request table is added. This accommodates the existing unique key
`(deployment, target_kind, target_id, stage, component_version)`: reusing a succeeded
`document` target would suppress another publish, even if its diagnostic
`content_hash` changed. Delivery/retry reuses the event's processing row; a later
commit, including A→B→A, has a fresh row. The event carries no desired path or
revision and never overrides current authority. No attempt reset, lineage counter,
or per-update component version is needed.

Self-host D146's non-convert class and managed D147 `run_task(context,
processing_id)` invoke the same handler. It makes no model calls. Reuse D62/D67
announcements, janitor, leases, retries and DLQ; cloud delivery is only a wake-up.
Registering the engine stage in the cloud pushed-work class map is an integration
requirement, not a hosted scheduler or correctness authority. Inactive-policy work
cannot write; seed current-policy work instead.

One deployment-scoped PostgreSQL advisory lock serializes publication, including
index regeneration, normal removal, reconcile and forget's authorized internal
calls. Reuse caller lock ownership for internal calls. Re-read authority after
taking the lock. Keep the claim alive while streaming. Before each object write
or delete, confirm ownership of that advisory lock on the same PostgreSQL session
and that the work claim is still live; abort further mutations on loss. Check
before each multipart request too, so one bounded part is the largest upload
unit. Configure bounded S3 connect/read timeouts and disable automatic write
retries that could issue a new request after lock loss. No detached uploads
survive a handler.

A PostgreSQL session can die while its process still runs and an already-issued
object request can complete after a successor acquired the lock. The remaining
window is one in-flight request, not fenced external IO. This rare race can leave
stale complete bytes (or a missing object after a late delete) even after a newer
publication set its marker. This is accepted for ordinary publication: prevent
further requests cheaply and repair by digest, without a fence, journal or extra
coordination tables. It does not weaken D74's separate drain/barrier requirement
for erasure (§6.7). A lease expiry alone cannot prove IO stopped.

For each publish:

1. Clear the publication marker before any store mutation. Compute the desired
   path and inventory from DB authority. Read nullable
   `documents.corpus_paths text[]`, the small set of folder paths this document
   may currently have objects under. Before writing any object at a new path, add
   that path to the set and **commit on the advisory-lock session**. Ordinary publication lists only these
   document prefixes, never the entire store to find a suffix. For example, a
   title/topic move records both the old and desired folders before the first new
   object write. The field is cleanup state, not desired authority, a history or
   a navigation-membership cache; a null value means no recorded paths.
2. If a served reading exists, write its desired folder **files first**. Mark an
   existing folder `_index.md` as `updating`, stream the selected original and
   allowlisted derivatives, confirm hashes/complete-object writes, and remove
   obsolete files at that path. A multipart ETag is not a SHA-256 checksum; use
   upload checksums or streamed read-back as required. Write `document.json`, then
   the ready folder `_index.md`, with the same deterministic export identity.
3. Delete every other recorded folder, including partial unindexed copies; if no
   reading survives, delete all recorded folders. Missing objects succeed. Handle
   abandoned multipart uploads under §5's provider rule. Keep old paths recorded
   until their navigation has also been repaired: a crash after deleting the last
   old-folder object must still leave its ancestors discoverable on retry.
4. Regenerate only the affected old/new ancestors' `_index.md` chain, including
   their rendered radix pages, bottom-up, plus root `llms.txt`, **from the DB**.
   Membership, paths, counts and summaries come from current live served
   selections, not events, recorded cleanup paths or previous index contents.
   Stream DB selections for counts and retain members only for affected indexes.
   List each affected index's reserved `~/` subtree to remove obsolete pages;
   prune topic indexes absent from DB membership. Never enumerate unrelated
   object folders or render/HEAD-check the whole navigation tree. A fresh tree
   creates the fixed eleven type indexes once, since root links include empty
   types. Ordinary object work is proportional to this document's recorded
   folders plus its ancestors' indexes. Page splits/merges never move document
   paths or recopy unrelated originals/media.
5. After files, old-folder removal and navigation succeed, reduce `corpus_paths`
   to exactly `[desired_path]`, or null after removal. A crash at any earlier
   point leaves all possibly written paths recorded for the next publish or
   forget. Then re-read the desired digest in a DB transaction and set
   `corpus_published_revision` only if it still matches what was published.
   Otherwise leave it null and retain/enqueue work for current authority.
   Updating transactions and final marker writes serialize on the document row;
   neither can erase the other's wake-up. Delayed/reordered work always recomputes
   current state. No separate journal, publication-receipt table, path reservation
   or directory-membership table is kept.

Normal delete uses the same recorded-path removal synchronously before D135
reports success; an object/index failure reports failure and leaves ordinary
durable work for retry. Deletion and forget cannot be disabled with claims/facts
switches. Removal success is absence under every recorded path plus repaired
indexes, with the existing work row recording completion; it needs no separate
empty receipt. Full suffix discovery remains mandatory for forget/restore (§6.7)
and available through reconcile to recover anything missing from the DB set.

**What readers can observe.** Objects are replaced atomically, folders and trees
are not. A new folder can be partly visible to direct `ls`; moves can briefly
expose two copies, and same-path updates can mix complete old/new files while the
folder index says `updating`. DB-derived shared indexes can temporarily lead or
lag another document's files or counts. No cross-file or point-in-time media
snapshot is promised. Check the ready folder identity before/after a read, or use
immutable version-addressed source handles when exactness matters, including for
cached/ranged media reads. The marker certifies a successful publication of this
document's current inputs; it is not a whole-tree snapshot. Failed or interrupted
publication normally leaves it null, and the next affected publish or reconcile
regenerates shared indexes from authority. The marker means “these current inputs
were published once”, not that no late request from a dead publisher can change
those bytes. Digest-verifying reconcile repairs that accepted transient staleness;
a matching marker never skips verification.

### 6.6 Repair, readiness and processing layers

`run_projection(plane="p3")` reconciles this same live tree using the lock,
selectors, renderer and publisher above. Stream DB lineages and paged key listings;
delete unknown/orphan objects, render all current navigation indexes from the DB,
and prune obsolete index keys even if no document folder remains there. Share
one full-store discovery pass across the run; ordinary publication never does
this scan. Union discovered suffix folders with recorded `corpus_paths`, repair
each served document's set to its desired path after cleanup/navigation, and clear
sets for unselected/deleted documents after their objects are removed. This repairs
paths that an independently restored bucket or incomplete DB state missed. Match
the first complete `--<suffix>` document-folder component, never a similarly named
asset below it. Reconcile has a verify mode: compare each desired object's stored
SHA-256 metadata with its desired digest, rewriting mismatches and missing metadata (or
missing objects), including files whose document has a matching marker. Delete
orphans through the same publication code path. Existing originals/derivatives
whose paths and digest metadata match need not be recopied. Periodic verifying
reconcile bounds how long rare late-request damage persists. A retry
recomputes everything, with no progress reconstruction or snapshot registry.
Without concurrent authority changes, incremental maintenance and reconcile
converge to identical keys and bytes. Export identities are deterministic digests;
no generated timestamps, event UUIDs or random build ids enter reader files.

Readiness `p3` uses the same nullable-marker comparison as `p3_path`, per requested
served lineage/version. A matching marker means its publish completed files,
cleanup and affected-index regeneration for the current digest; a recent unrelated
publish proves nothing. Reconcile can verify inventories and repair store damage;
the marker does not certify an independently restored bucket, which must pass the
restore gate (§6.7). A selected historical edition reports `p3_version_not_served`;
a raw-only/parked selection is not ready; a missing/mismatching marker reports
`corpus_publication_incomplete`, with adapter/DLQ failures still visible in the
ordinary ledger. Aggregate `p3` requires all requested selections to match.
Deleted documents have no browse path; their removal is verified by the delete/
forget operation, not an applied empty-publication record. `version` may expose the
selected document's digest; unsupported global snapshot time/version fields are
null, including an aggregate over multiple publications. No new publication-time
columns are required. A missing local mount does not invalidate a remote publish.

P3 is a **sources-layer** projection with the same shape whether claims/facts are
on or off. Completion follows the served pointer under D143's enabled-layer rules;
a facts-setting change alone requires no P3 rebuild or filtering. K keeps its own
facts-layer gate. Suffixes, publication markers and export metadata enter no
converter, chunk, Selection, Claimify or embedding identity. Only the placement
output/schema generation changes to accept successful `null`, reusing stored
summaries and leaving unchanged text's paid-work identities intact (Rule 4).

### 6.7 Hard forget and restore: erase the document, regenerate indexes

D74's portable intent, admission/drain barrier, verification and restore replay
remain the privacy authority. The existing `doc_id` in intent is sufficient for
P3: its suffix is computable after source-metadata scrub, without historic titles,
paths, suffix reservations or a corpus-wide purge scope.

1. D74 stops ordinary claims and drains all publication/reconcile writes, including
   in-flight uploads. Engine-controlled mount sessions stop serving and acknowledge
   cache purge; a blocked API or expired lease alone does not stop external IO or
   mounted reads. D147 retains separately durable manifest storage.
2. After lifecycle/authority scrub, the authorized coordinator takes the publication
   lock, pages through corpus keys and deletes **only** folders with the forgotten
   document's suffix, including former moved folders and interrupted unindexed
   copies. Handle abandoned multipart uploads under §5's provider rule. Clear that
   document's publication marker and purge its controlled serving/cache copies.
   Never empty the corpus store or recopy surviving documents' originals/derivatives.
3. Regenerate affected navigation `_index.md` and root `llms.txt` from scrubbed DB
   authority. Enumerate navigation-index keys too: rebuild/prune them against DB
   membership to cover index-only remnants whose old folder was already deleted
   before a crash. This conservative metadata pass may revisit all shared indexes;
   it does not reset document folders or require a historic path list. Verify no
   matching folder, forgotten title/summary/member entry or controlled serving
   copy remains; report unfinished-upload coverage under §5's provider rule.
   Clear `corpus_paths` after object cleanup and navigation repair; its friendly
   paths must not survive a completed forget. Always do the full suffix scan even
   when the recorded set is empty, including on restore replay.
   Reopen only after every D74 store acknowledges erasure within that scope.
   Any error leaves the barrier closed and retries the same idempotent procedure.
4. Before a restore serves traffic or controlled mounts, re-honor **every** portable
   manifest, even one locally marked complete. Scrub restored authority, repeat
   suffix deletion in a separately restored bucket and regenerate indexes; verify
   again. Batched manifests may share the paged scan and index regeneration. A local
   clean marker never skips an independently restored store or cache.

The corpus store must have object versioning off or enumerate, purge and verify
all retained versions of matching document objects and rewritten/pruned indexes
during erasure.

Backups remain outside active stores under D74/D75 and cannot serve without replay.
Managed session/cache acknowledgement is an adapter obligation, implemented by
the cloud repo; operator-managed FUSE mounts must stop/remount for forget.
Arbitrary downloaded client bytes remain outside the library-controlled boundary.

## 7. End-to-end (one document)

1. Ingest `acme-2023.pdf` under its stable lineage; write immutable raw bytes and
   record their URI/hash. Its corpus suffix is computed from `doc_id`, not allocated.
2. Convert through the PDF OCR route: immutable Markdown, source map, images and
   private engine manifests. Structure persists PageIndex/roles/summaries and the
   optional topic `["finance", "annual-reports"]`; crossref persists private state.
3. Enabled E1/E2/E3 work completes. The transaction serving this version clears
   its publication marker and enqueues ordinary `publish_corpus` work. Sources-only
   deployments use their own completion boundary.
4. The self-host worker or managed pushed task recomputes DB inputs, commits the
   desired folder into `corpus_paths`, lists only recorded document folders and
   writes files to
   `pdf/finance/annual-reports/acme-2023--9bf3e4c0c2d4861075a6ef82/`. It deletes other
   recorded folders of this document, regenerates their ancestors/root from the DB,
   reduces the set to the desired folder and sets the nullable marker after success. Every object records its expected content digest;
   requests are bounded and each mutation checks the same lock session. `p3` is
   ready; `p3_path` resolves that folder. A late request after DB-session loss may
   transiently replace newer bytes despite its matching marker; verifying
   reconcile compares digest metadata and restores the newer reading.
5. A customer mounts read-only and opens Markdown, images or `original.pdf` in
   place. A newer stored upload leaves the completed folder intact until served
   and published. A type/topic/title change moves it; re-resolve `doc_id` via MCP
   or suffix search.
6. Normal deletion removes that document's folders and regenerates indexes. Hard
   forget uses the same per-document erasure under D74's barrier, with scrubbed DB
   authority and all-store verification; independent restores replay before serving.

## 8. Decisions and measurements

D36–D39 govern the E0 chain/structure; D55/D65 govern source and representation
identity; D117 governs retained originals and parked conversion; D143 governs
enabled processing; D155 replaces P3 layout, maintenance and customer mounting.
The ordered, independently mergeable [implementation plan](../plans/corpus_document_folders.md)
includes MinIO, forget/restore and Rule-4 proofs. No product code or deployment
migration is specified by this design amendment.

Measure conversion fidelity, structure-route quality, intentional no-topic rate,
nav hops/tokens to find a document, B/page churn, P3 lock wait/publication lag,
streamed media memory/transfer cost, suffix-scan cost and erasure/reconcile duration.
These measurements tune policy/operational settings; they do not reopen the original-copy rule,
served-version choice or erasure guarantees.
