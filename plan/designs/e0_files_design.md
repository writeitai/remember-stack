# E0 — Files / Document Layer + the Corpus Filesystem (P3)

How a raw input file becomes a **structured document** (E0), and how documents are organized into
a **mountable corpus filesystem** that agents browse (P3, a projection). Decisions **D36–D40**.
Companion working analysis: `_feature_planning/e0/` (Claude + Codex). Numbers/choices here are
starting points to measure, not committed constants (CLAUDE.md).

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

## 2. Storage layout — GCS holds bodies, Postgres holds the index

Two buckets per deployment (storage is per-deployment, like entity spaces, D16):

- **raw** — `gs://rememberstack-<dep>-raw/<doc_id>/<content_hash>/original.<ext>` — immutable source-of-truth
  bytes (D1). Strict per-deployment IAM. **Mounted read-only, but off the navigation path**
  (D51): P3 and the Markdown never *promote* raw — stubs and `document.md` frontmatter carry an
  explicit raw pointer, so reaching an original is always a deliberate act (following a link),
  never a browse default. The point is whole-file media: for a video, an MP3, a photo *input*,
  the original **is** the artifact a multimodal harness needs — conversion yields only a lossy
  transcript/description. Three guardrails replace the old never-mounted rule: (1) **storage
  class routes by mime** (per-deployment config, like the D38 converter router): media likely
  to be read by agents (video/audio/images) → standard/nearline; text/office originals kept
  only for audit and re-conversion → archive (this kills the grep-the-archive cost bug at the
  source); (2) **data-access audit logging on the bucket is mandatory** — the audit property
  of the old rule came from logging, not from unmountedness, and a gcsfuse read is a GCS read;
  (3) originals may carry what conversion strips (EXIF, tracked changes, embedded metadata) —
  acceptable because a deployment is **one trust domain** (D50): every agent that reaches the
  mount is trusted with the deployment's content; data with a different trust boundary belongs
  in a separate deployment, never behind an in-library filter.
- **artifacts** — `gs://rememberstack-<dep>-artifacts/<doc_id>/<content_hash>/<representation_id>/`
  holding `document.md` (clean Markdown — the immutable coordinate system everything
  references by offset, D57), `pageindex.json`, `conversion.json` (source map + route
  manifest + converter metadata), `blocks.json` (the blockizer's block sequence, D57),
  `meta.json`, and **`media/`** — the path's **representation segment (D65)** exists because
  one content object can own several conversion generations (a better ASR re-reads the same
  bytes): each is an immutable `document_representations` row, a re-conversion lands *beside*
  the old reading (whose coordinate system historical claims still resolve against), never
  over it, and the version's `current_representation_id` points at the live one — the
  document's *derived* media: figures extracted from documents, video keyframes, crops,
  thumbnails, referenced from `document.md` by relative links. Standard storage. This is the
  per-document material an agent *reads*; it is reachable from the corpus filesystem (§6).
  Media matters because conversion is lossy exactly where a source is visual: agents are
  pointed **Markdown-first**, but a multimodal harness must be able to open the referenced
  image directly from the browse path. **Whole-file media originals** (a video, an MP3, a
  photo input) are *not* duplicated into `media/` — they are served from the raw mount (above)
  via the explicit raw pointer; `media/` holds only what conversion *derived*. **The
  canonical-text rule (D65):** all text eligible for extraction, search, and grounding lives
  in `document.md` — a transcript is the *body* of a recording's document.md, never (only) a
  sidecar. `media/` may additionally hold a `.vtt`/JSON **interchange** copy of a transcript
  (timing-preserving, provenance-linked, for players and external tools), but text that
  exists *only* in a sidecar is invisible to the blockizer, E2, P1, and D32 grounding — it
  does not exist as testimony.

(`content_hash` = sha256 of the raw bytes — the canonical *byte* identity, deduplicated in
`content_objects` and used in the path; the *logical document* identity is the lineage's
`(source_kind, source_ref)` — D55.)

Canonical objects are **ID-addressed** (`doc_id` + `content_hash`), never title-addressed — titles
change, collide, and contain hostile characters. Human-readable names live only in the corpus
filesystem projection (§6), which points back at these stable paths.

**The Postgres / GCS split (D37).** Postgres is the E-plane *ledger*; GCS is the *blob store*. The
rule is precise:

> **Postgres never stores document *body* text or Markdown bodies. It stores compact, query-critical
> metadata** — document identity, versions, processing state, artifact URIs, hashes, costs, and the
> *section index* (titles, paths, roles, spans, summaries). Bodies live in GCS.

This keeps Postgres lean (1M document bodies would bloat it for nothing) and puts the text where it
belongs — in the mountable artifact store. `documents`:

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
idempotency, never a value tier, per D25). **A changed file from a watched source is a new
*version* of its lineage** (D55): connectors debounce rapid edits to one ingested version per
stability window; unchanged chunks of the new version **reuse** their prior extraction and
embeddings via the content-addressed keys (D56), so the cost of a version is proportional to
the edit, not the document — full design: `evidence_lifecycle_design.md` §2/§6.

**Deletion / forget (cascade).** Normal removal tombstones the lineage, purges its unshared **raw +
artifacts** objects, ends its testimony currency, and reaches K through the citation tombstone path
(lifecycle §8; schema §13.1). Irreversible hard-forget is D74's separate fail-closed workflow: it
also scrubs retained source payloads, explicitly purges P1 and old P2/P3 snapshots, erases affected
K history, and records a portable restore barrier. A clean projection rebuild changes what is
served; it does not by itself erase immutable old bytes (`hard_forget_design.md`).

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
- **Router by input type** (per-deployment config): digital PDF → direct text extraction; scanned /
  complex PDF + images-that-are-documents → **OCR** (e.g. Mistral OCR / docling / marker); office /
  html / email → **markitdown**; plain text → passthrough. (This generalizes the common practice of
  *Mistral OCR for PDFs, markitdown for the rest* into a routing table.) **Media routes (D65),
  bound in `media_design.md` §2:** audio → **diarized ASR** (transcript as document.md, one
  block per speaker turn); video → ASR + **adaptive keyframes** + optional VLM shot notes;
  standalone image that is a *picture* → **VLM description** + OCR of visible text, behind a
  document-vs-picture discriminator (MIME alone cannot tell a scanned page from a photo).
  Media converters are versioned like every other — an ASR/VLM upgrade is a
  `converter_version` bump, flowing the processing-driven lifecycle ruleset
  (`evidence_lifecycle_design.md` §3).
- **Versioned** (`converter_version`): a converter or routing change re-converts the affected docs (a
  batch keyed by version), which rebuilds everything downstream — the D7 rebuildability discipline
  applied to the foundation.

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
keep as **context, never as facts**: they feed E1 context prefixes, agent navigation, and
"why-was-this-selected" explainability. They are versioned with the structurer. Crucially, the
*global* high-level picture of the corpus is the **K plane's** job (compiled knowledge over
relations/evidence), **not** these per-document summaries — so the high-level picture never depends
on summary quality, but the summaries usefully enrich local navigation.

**Placement hint (the extension).** The PageIndex output is **extended** with a `placement` field: a
proposed path for the document (and optionally key sections) within a *hypothetical directory tree*
of the whole corpus — e.g. `"/finance/annual-reports/2023/"` or
`"/research/transformers/attention/"`. This is a **per-document hint**, produced where the document's
content and structure are freshly understood. It is *advisory*: the authoritative, coherent tree is
materialized later by the projection (§6), which can reconcile, rename, and reorganize across the
whole corpus as it grows (a single document cannot know the global tree).

### 4.1 Scalable structure route (D79, 2026-07-27) — deterministic skeleton, bounded summaries, orientation-only consumption

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
  parallel and cached per section; the cache key is the ordered constituent block hashes,
  child-summary hashes, model, prompt, generation parameters, and summarizer version. This is
  the call shape where cheap models are reliable: bounded context, no arithmetic, no giant
  output.
- **Placement rides the root reduction.** The one-shot call also produced the D39 placement
  hint; that responsibility moves to the **document-level (root) summary call**, which sees
  exactly what placement needs — the title, source kind, and the child one-liners. On the
  degraded path (no summaries), placement is null and the P3 projection falls back to its
  type-based default, as it already must for failed structuring.
- **Consumption: orientation only, never load-bearing.** Summaries feed the E1
  context-prefix input (the prefix prompt gains the target + ancestor one-liners, size-capped)
  and enter the **E2 bundle** as the D31 "section path + summary" element — target summary
  plus ancestor one-liners, so a chunk in subchapter 8.2 sees the book's and chapter 8's
  lines. Two hard rules keep this sound:
  1. **A summary is never a grounding source.** Summaries are abstractive LLM text; if
     `added_context` could cite them, an invented phrase in a summary could be laundered into
     a claim through the membership-only D32 layer-2 check. The allowed `added_context`
     source kinds stay exactly `header|neighbour|prefix` (+ `hint` where designed) — **no
     `summary` kind exists**. The model may *read* summaries to choose an interpretation; any
     text it *adds* must still come from source-derived elements. (This re-states §4's
     "context, never as facts" as an enforced contract, and hardens the same rule already
     implicit for the prefix.)
  2. **Summaries are excluded from extraction-correctness inputs.** They do not enter
     `extraction_input_hash` (the same exclusion D56 already applies to LLM-derived context),
     so a re-summarization — including an ancestor summary changing because a sibling leaf
     was edited — never silently invalidates or re-extracts unchanged chunks. Better
     summaries improve *future* extractions; they never fan out into document-wide
     reprocessing.
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

The last E0 sub-worker records how documents point at each other — the raw material for the
`DOC_CROSSREF` graph edges (P2) and one source of the E2 bundle's entity hints
(design-review F7). Product: `document_crossrefs` rows `(from_doc_id, to_doc_id NULLABLE,
kind, context)`, kinds `cites | links_to | attaches | replies_to`.

**Extraction — deterministic per kind:**

- `links_to` — URLs and links in the converted Markdown (`conversion.json` blocks keep the
  offsets);
- `attaches` — container relationships known at ingest/convert time (e-mail attachments,
  archive members);
- `replies_to` — thread metadata (e-mail `In-Reply-To`/`References` headers, chat thread ids);
- `cites` — citation strings, mined primarily from PageIndex `references`-role sections: DOIs,
  arXiv ids, ISBNs, plus deployment-specific citation grammars (e.g. case citations in the law
  deployment — configured per deployment, like the D38 converter routing table).

**Resolution — cheap-first (the D4 discipline).** A reference resolves to an ingested document
via exact keys first (normalized URL ↔ `documents.source_uri`; DOI/arXiv id ↔ document
metadata; `content_hash` for attachments), then fuzzy title match (`pg_trgm` against
`documents.title`, recall-first floor), and only the ambiguous residue goes to a small-model
rung ("is citation string X document Y?"). Below threshold the row keeps `to_doc_id = NULL` —
a cited-but-not-ingested reference: real provenance, no graph edge (`v_graph_crossref` filters
nulls).

**Late binding.** Dangling references are not dead: when a new document is ingested, its
identity keys (URI, DOI/ids, title) are matched against unresolved crossrefs — one indexed
lookup on the ingest path — so earlier documents' citations bind to it retroactively. No
periodic sweep; resolution rides the write path in both directions.

Idempotent on `content_hash` + crossreferencer version (D12); versioned because the fuzzy rung
is non-deterministic; the citation `context` snippet is stored for audit. Execution class
(D52): deterministic first, one small-model rung for the residue — LLM spend scales with
ambiguity, not volume (D4).

## 5. Mounting — agents read the memory on their filesystem

A hard requirement: agentic workers get the memory **on their filesystem** and navigate it without
querying Postgres per step. `gcsfuse` mounts a GCS bucket as a filesystem on Cloud Run/GKE, so an
agent can `ls / cat / grep` the corpus. Three buckets are mounted **read-only** (writes always go
through the pipeline; Postgres stays the authority):

- the **corpus filesystem** bucket (§6) — the navigable hierarchy agents browse *first*,
- the **artifacts** bucket — the per-document material they *drill into* (linked from the tree), and
- the **raw** bucket — originals, **off the navigation path** (§2, D51): reached only by following
  an explicit raw pointer from a stub or `document.md` frontmatter (whole-file media ingestion,
  verification, re-OCR debugging, legal provenance); mandatory data-access audit logging; storage
  class routed by mime so browse-pattern reads never hit archive-class retrieval fees.

(The K repo is the fourth mounted surface of the system overall — a read-only checkout — but it is
plane K's concern, not E0's; see `retrieval_design.md` §7.)

**Mount mechanics (not a normal POSIX FS).** A `gcsfuse` mount is object storage behind a filesystem
facade — directories are inferred from object name prefixes, and symlinks/hard-links are not
first-class. So the corpus filesystem is built from **real generated files**, not links: every leaf
is a small generated Markdown **stub** with frontmatter (`doc_id`, `artifact_uri`, `content_hash`,
`section_path`; for media documents additionally `raw_uri` — the mount-relative path to the
original — plus duration and preview links into the artifact `media/` folder, D65: the browse
path shows what the file *is* before anyone opens 2 GB, and never materializes whole raw media
or per-keyframe pseudo-documents in the tree) and a relative pointer to the artifact, so `cat`
shows orientation and the agent follows `artifact_uri` for the full body. Mount config: **read-only**, `--implicit-dirs` (so prefix
folders list correctly), with stat/list caching tuned for the rebuild cadence. (gcsfuse semantics:
https://cloud.google.com/storage/docs/cloud-storage-fuse/overview.)

## 6. The corpus filesystem — P3, a projection — D40

**We build a canonical corpus filesystem.** It is a real, materialized **GCS bucket laid out as a
directory tree** that organizes the whole corpus for agent navigation. It is a **P-plane projection**
(P3): **derived, holding no source-of-truth, no validity state**, and discardable/rebuildable. It is
"canonical" only in the sense of being *the published navigable view* — never an independent truth.

**Rebuild semantics — snapshot + pointer-swap, like P2 (D7).** P3 is a **full snapshot rebuild** by
default: the projection worker builds the whole tree into
`gs://rememberstack-<dep>-corpusfs/snapshots/<version>/`, validates it, then atomically swaps the `latest`
pointer; agent mounts read `latest`. Incremental tree maintenance is permitted only as an *internal
optimization* that produces the same validated snapshot — never as authority and never a separate
mutable state. This is the same rebuild-first discipline as P2.

**Rebuild inputs (Postgres-anchored).** The tree *structure* is built from **Postgres** (the
placement hints D39, plus entities/relations that define topic/entity folders) **+ the GCS
artifacts** (for the leaf stubs). It **cross-links to** K-plane pages by reference but does **not**
take K as a structural input — so P3 stays rebuildable from the E spine + artifacts, consistent with
P1/P2 (it does not depend on the non-reproducible K git repo for its shape).

```
gs://rememberstack-<dep>-corpusfs/snapshots/<version>/
  finance/annual-reports/2023/
    acme-10k-2023.md            # generated stub: frontmatter (doc_id, artifact_uri, content_hash)
    _index.md                   # generated: what's here, summaries, links down + across (+ to K)
    llms.txt                    # generated agent-navigation manifest
  research/transformers/attention/
    attention-is-all-you-need.md
    _index.md
  entities/organization/acme/
    _index.md                   # Acme: profile + the docs/sections evidencing facts about it
```

- Folders come from placement hints reconciled across the corpus (topics, sources, time) and from
  entity/relation structure (`entities/...`); leaves are **generated stub files** pointing at the
  per-document artifacts (the agent drills from the tree into the Markdown + per-doc structure).
- Each level carries a generated **`_index.md` / `llms.txt`** so an agent reads orientation before
  contents (the navigation-manifest pattern). Where an `_index.md` links to a **K page**, it
  carries that page's freshness state alongside the link (`compiled_at`, stale?, open review
  flags) — the **browse-path half of the D49 reader-facing flag surface** (the query engine's
  envelope is the other half, `retrieval_design.md` §5): an agent must be able to see "this
  page has unresolved evidence-change flags" *before* reading it, on either path.
- **Composition with K (cross-link, not dependency):** the K plane (compiled understanding) is the
  *summarized* layer; P3 is the *navigable index over sources*. `_index.md` files **link to** relevant
  K pages and vice versa (understanding ↔ evidence) — complementary mounts, not duplicates, and P3's
  *structure* does not depend on K.
- **Reorganizable — within the path contract:** because it's a rebuilt projection, *view*
  subtrees reorganize as the corpus grows on the next rebuild — placement hints are inputs,
  never commitments. What may **not** move is the stable-leaf tier of the path contract below.

### The `_index.md` contract — what every index file contains

An index file is not a courtesy listing; it is the mechanism that makes the tree *cheaper to
navigate than to search*. Its content is bound, and **fully deterministic — assembled from
Postgres, zero LLM**:

1. **Directory identity line** — templated from the taxonomy: *"Emails — client
   correspondence, 2024–2026 · 1,284 documents in 14 subfolders."* Counts, time range, source
   facets: all SQL.
2. **The member table — one row per child, carrying each document's PageIndex root summary**
   (already stored in `document_sections`, D39 — surfacing it is free), plus date, source,
   and entity links. This is the load-bearing property of the whole tree: an agent reads
   *one* `_index.md` and knows what every file in the directory is about without opening any
   of them — navigation cost becomes O(index files read), not O(documents opened).
3. **Cross-links**: covering K pages (with the freshness/flag state above), sibling views of
   the same documents, parent/child indexes.

**Directory-level LLM summaries are a rejected alternative, not an omission.** The member
table already carries the directory's meaning, and where a directory-level *synthesis* is
genuinely wanted, that is a signal to create the covering **K page** (an ordinary planner
decision) — the layer built to keep synthesis current via citations. A P3-local LLM summary
would be a second, uncited understanding layer that drifts (§4: the global picture is K's
job, never per-document — or per-directory — summaries), and it would put an LLM call inside
an otherwise fully deterministic projection builder. `_index.md` links K; it never competes
with it.

### Structure rules — facets, views, fan-out, and the path contract

1. **The top level is configured, not emergent.** Placement hints reconcile *within* a
   deployment-declared facet skeleton (registry-style config): e.g. `by-type/` (emails,
   papers, contracts, notes…), `by-source/`, `by-topic/` (community-derived), `entities/`.
   Emergent top levels reshuffle as the corpus grows — exactly what path-holding consumers
   cannot tolerate. Facets are stable; their interiors reorganize.
2. **One document, many views — by stub duplication.** An email about Project Atlas belongs
   under `by-type/emails/…` *and* a project view. gcsfuse has no real symlinks; stubs are
   cheap generated pointers, so duplication is the mechanism — it is a projection, nothing
   is kept consistent by hand.
3. **The two-tier path contract** (accepts design-review F6). Tier 1 — **stable,
   ID-addressed leaves that never move across rebuilds**: every entity at
   `entities/<type>/<entity_id>/`, every document lineage at one canonical per-doc path
   (lineage-anchored, D55 — a living document's path survives its content versions). These
   are the durable targets agents, K pages, and cross-links may store. Tier 2 — **view
   paths** (topic/source/time subtrees), documented as freely reorganizable; every view stub
   carries the canonical path in its frontmatter. Consumers needing durability hold Tier 1;
   browsing uses Tier 2.
4. **Bounded fan-out.** Directories shard deterministically (by date, alpha, source) above
   ~100–200 entries (a starting point to measure against real `ls`/gcsfuse listing
   behavior) — an unbounded directory is unbrowsable for an agent and slow to list; the
   member table keeps sharded levels cheap to traverse.

### The navigation ladder

The tree exists to make this the default motion — each step one `cat`, escalation to the
API only for what has no filesystem equivalent (the D51 precedence rule):

```
llms.txt (root orientation: facets, counts, where things live)
  → facet _index.md        (what kinds of things exist here)
    → directory _index.md  (member table: every file's one-line meaning)
      → stub               (doc orientation + canonical path + artifact pointer)
        → document.md      (+ pageindex.json for section-grain entry)
```

`grep -r` over stubs gives content-ish lookup (title + summary + entities are *in* the
stubs) with zero API calls. The consumption skill (`retrieval_design.md` §8) teaches this
ladder, and navigation joins the eval surface: an S58-style scenario — *find the document
answering X using only `ls`/`cat`/`grep` on the mount* — measured on hops-to-target and
tokens-read-to-target.

**Rejected alternative — index-files-only (no per-document stubs).** Publishing only
`_index.md` files pointing at ID-addressed artifacts saves roughly the stub writes — ~1M
Class-A GCS operations ≈ **$5 per full view rebuild** at a million documents — and in
exchange loses files-as-files (`ls`/`grep` over the corpus), the Tier-1 stable document
leaves, and the open-what-you-found ergonomics harnesses are built around. Not worth it at
any corpus size; recorded so the object-count worry doesn't re-litigate it.

**Why a projection, not E0 state:** the *organization of the corpus* is a function of the evolving
knowledge; freezing it as E0 state would lock an organization that should change as understanding
changes. Per-*document* structure (PageIndex) is intrinsic → E0; *cross-document* organization is
derived → P3.

## 7. End-to-end (one document)

1. Upload `acme-10k-2023.pdf` → **ingest**: raw bytes to the raw bucket, `content_hash`, a
   `documents` row.
2. **convert**: router picks OCR (scanned PDF) → `document.md` + `conversion.json` (offsets) to
   artifacts.
3. **structure**: PageIndex builds the tree (sections, roles, spans, summaries) + a `placement` hint
   `/finance/annual-reports/2023/`; `pageindex.json` to artifacts, `document_sections` rows to
   Postgres.
4. **crossref**: extract citations / document links.
5. → E1 chunks along sections → E2/E3 extract claims/relations.
6. On the next **P3 build**, the document appears at
   `/finance/annual-reports/2023/acme-10k-2023.md` in the mounted corpus filesystem, with a generated
   `_index.md`, cross-linked from `/entities/organization/acme/`.

## 8. Decisions & spikes

Decisions: **D36** (E0 = document layer of sub-workers, no renumber), **D37** (storage split +
Postgres-metadata rule + ID-addressed paths + read-only mount), **D38** (configurable conversion
module), **D39** (PageIndex structure: sidecar + PG index, structure-only, summaries kept, placement
hint), **D40** (P3 corpus-filesystem projection), **D65** (media routes + generalized contract +
canonical-text rule — binding home: `media_design.md`).

Open spikes (measure before committing):
1. **Conversion fidelity vs cost** — OCR is the expensive, quality-critical step; cheap-extract →
   OCR-on-failure fallback chain? Measure on real PDFs.
2. **When to run the PageIndex tool vs. a synthetic root.** Every doc gets a `document_sections`
   structure either way (§4); measure where running the full tool earns its cost vs. a synthetic
   root — an implementation-routing question, not a contract gap.
3. **Placement-hint quality** — how good is a per-document path guess, and how much does the P3 build
   reconcile/override? Measure tree coherence.
4. **P3 build cadence & scale** — rebuild-all vs incremental tree maintenance as the corpus
   grows. (Path stability is no longer this spike's question — it is the §6 two-tier path
   contract; what remains to measure: the fan-out sharding threshold and incremental-delta
   size per cycle.)
5. **doc_id scheme** — hash (collision-safe, opaque canonical paths) + readable names in P3.
6. **Raw storage-class routing** (D51) — measure the read patterns per mime class on a real
   corpus slice; set the standard/nearline/archive routing table and verify the mounted-read
   cost envelope (no archive-class retrieval fees on agent browse patterns).
7. **Citation-resolution precision/recall** (§4A) — exact-key coverage vs the fuzzy/LLM
   residue rate on a corpus slice; per-deployment citation grammars (law) — measure before
   trusting `cites` edges for navigation.
