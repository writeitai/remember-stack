# E1 — Blocks, Sections, Chunks (Design)

How a converted document becomes the units the system embeds, extracts from, and anchors
claims to — and how those units survive document edits. Binding design for decisions
**D57–D58**, building on D94 (PostgreSQL-native P1), D25 (no value gate), D32 (grounding offsets),
D38/D39 (conversion, PageIndex), D48–D50 (retrieval channels), D54–D56 (evidence lifecycle —
this design owns the reuse *mechanics* that `evidence_lifecycle_design.md` §6 defers here).
Shaped by a multi-round design discussion recorded in
`plan/analysis/evidence_lifecycle/stress_test_amendments.md` (objection A → amendments A1–A3).
Numbers are starting points to measure, not committed constants (CLAUDE.md).

> **Reading this cold (CLAUDE.md Rule 1).** E0 converts every input to a clean Markdown
> rendering (`document.md`) plus sidecars; **PageIndex** is the LLM-based structurer that
> builds a per-document section tree (titles, roles like `results`/`references`, summaries);
> **semchunk** is the deterministic token-budget text splitter the requirements impose;
> **E2** extracts claims from chunks; a claim's `source_span` must trace back through
> `document.md` to the source (grounding, D32); **D56 reuse** requires that when a watched
> document is edited, unchanged content is *not* re-extracted or re-embedded. "**Embedding
> dilution**" = a long text's embedding averages its meanings, so a two-sentence fact inside a
> large chunk matches pointed queries poorly — the reason naive systems use tiny chunks or
> sliding windows.

## 1. The three-layer model

One document, one ordered text, three views over it:

```
document.md = [b1][b2][b3][b4][b5][b6][b7][b8][b9][b10]      BLOCKS — deterministic atoms
sections    = [   §1 Intro    ][     §2 Results    ][§3 Refs] SECTIONS — PageIndex ranges
                 role=intro         role=results     role=refs   over blocks (LLM meaning)
chunks      = [c1: b1–b3][c2: b4–b5][c3: b6–b7][c4: b8][c5: b9–b10]  CHUNKS — token-budget
                                                              packings of whole blocks
```

| | Blocks | Sections | Chunks |
|---|---|---|---|
| produced by | the **blockizer** (deterministic, ours — §2) | PageIndex (**LLM** — may redraw on re-run) | the packer (deterministic — §4) |
| carry | **identity**: hashes, offsets, source back-pointers | **meaning**: hierarchy, roles, summaries, placement | **the unit**: embedding, extraction, claim anchoring |
| used for | reuse diff (D56), the grounding chain, edit locality | chunk boundaries, E2 role signal, P1 role filter, P3 placement | P1 index rows, E2 bundles, `claims.chunk_id` |
| in identity/reuse keys? | **yes — exclusively** | **never** (non-deterministic) | derived (a chunk's key *is* its block-hash sequence) |

In motion: someone edits paragraph `b5`. Only `b5`'s hash changes; the block diff aligns
everything else; chunk `c2` (b4–b5) is the only chunk whose block sequence changed → it alone
re-extracts and re-embeds; `c1/c3/c4/c5` carry forward with their claims, embeddings, and
stored context untouched; reconciliation (D54) touches only the facts `c2` evidenced. A
structurer re-run may redraw sections without threatening any of that — sections never
participate in identity; they only decide where *future* packing may cut.

## 2. Blocks and the blockizer (D57)

**What a block is.** The smallest *structural* unit of the rendered document — one
block-level element in the CommonMark sense: a paragraph, a heading, a list item, a table
(atomic, however large), a code fence, a figure caption, a block quote. Typical documents
yield roughly one block per paragraph — tens to a few hundred per document.

**How blocks are produced — the corrected causality.** Converters are heterogeneous
(Mistral OCR returns per-page Markdown in JSON with no textbox layer exposed; markitdown
returns plain Markdown; docling/PyMuPDF-class tools expose richer structure) — so blocks are
**not** part of the converter contract. The contract splits:

1. **Converters produce `document.md` + a source map + derived assets + a manifest** (refines
   D38; generalized by D65 — the *page map* is the paper case of the source map). Mistral OCR:
   concatenate `pages[].markdown`, recording each page's char-range — the page map falls out
   of concatenation. markitdown: Markdown, no source map (emails/HTML have no pages — the
   locator is nullable). Media routes (ASR, VLM description — `media_design.md` §2) emit
   time-range / image-region locators the same way. Tools that expose real layout use it to
   render *better-segmented Markdown* (true paragraph breaks, correctly fenced tables) —
   structure informs rendering; it never bypasses the next step.
2. **One shared `blockizer` — deterministic, ours, versioned (`blockizer_version`) — derives
   the block sequence from `document.md`**, on a **pinned parser profile: GFM** (GitHub
   Flavored Markdown — CommonMark + the table extension; "pipe tables" are GFM, not core
   CommonMark): blank-line paragraphs, headings, fenced code, tables as atomic blocks, list
   items. The **exact parser library + version + enabled-extension set is pinned per
   `blockizer_version`**, with a fixed normalization order (join hard-wrapped lines per the
   grammar's paragraph rules → Unicode NFC → collapse internal whitespace → hash). Because two
   spec-compliant parsers can still segment edge cases differently, determinism is
   **regression-tested, never assumed**: a **golden corpus with expected block-hash sequences
   runs in CI per `blockizer_version`** — drift is a version bump, not a silent change (Codex
   review F13). The same code path regardless of which converter produced the Markdown — **no
   per-converter block-semantics drift is possible**, because no per-converter block code
   exists. Heterogeneity is confined to Markdown *quality*, where it already lived (e0:
   conversion quality gates everything).

**The block record** (in the `blocks.json` sidecar, one per document version):

```
{ ordinal: 17,                        // position in the document sequence
  type: paragraph | heading | table | list_item | code | figure_caption | quote,
  char_start: 4812, char_end: 5203,   // slice of document.md — the block's text IS this slice
  source_locator: { … },              // provenance, best-effort (typed union, see below)
  block_hash: sha256(normalized text) }
```

**Provenance is tiered, and that is acceptable.** The load-bearing offsets for grounding
(D32) are *into `document.md`* — exact by construction, since we wrote the file. The *source*
back-pointer degrades by converter capability, expressed as the typed **`SourceLocator`
union** (D65 — the **normative schema, field conventions, and pinning rules live in
`media_design.md` §4**; this is the shape for orientation):

```
SourceLocator =
  | { kind: page,         page, bbox?,                       precision: page | region }
  | { kind: source_range, start_offset, end_offset,          precision: exact | approximate }
  | { kind: image_region, region,                            precision: image | region }
  | { kind: time,         start_ms, end_ms, track?,          precision: word | segment | shot }
  | { kind: video_region, start_ms, end_ms, region?,
      keyframe_asset_id?,                                    precision: segment | shot | frame }
```

Page-grain from page maps (Mistral), bbox where a tool exposes it, time ranges from ASR
segments, `source_range` for pageless formats (HTML/email), absent only where genuinely
unmappable. Locators are pinned via their carrier record to the document **version and
representation** whose bytes/reading they index — never a lineage or a P3 path — and
**precision-honest** (the `precision` field says what the tool actually delivered; word
timing is never fabricated by interpolating characters across a segment). The locator is
audit/navigation metadata, never a correctness dependency.

**Determinism, scoped precisely:** blocks = f(`document.md`, `blockizer_version`), and
`document.md` = f(bytes, `converter_version`). Same bytes through the same toolchain →
identical blocks — exactly what the D56 version-to-version diff needs, since a lineage's
versions are converted by the deployment's current router config. The known cost (already the
D7/D38 rule): a converter swap or blockizer bump changes block hashes document-wide and
forfeits reuse across that boundary. **Rule: the conversion route is pinned per lineage**
until a deliberate version bump — the router never silently picks different converters for
different versions of one lineage.

**Imperfection tolerance — why blocks can carry identity.** Suppose the converter's Markdown
merges two paragraphs and the blockizer yields one block where two belong. Nothing breaks:
grounding offsets stay exact, chunks pack fine, extraction sees the same text. The only cost
is **diff locality** (an edit to either paragraph invalidates the merged block) — slightly
coarser reuse, never wrongness. Block boundaries must be *deterministic and reasonably
local*, not semantically perfect — a far lower bar than sections, which is precisely why
blocks and not sections carry identity.

**Storage.** `document.md` is **clean Markdown only** — no block markup, no IDs; it is the
immutable, content-hash-addressed **coordinate system** everything references by offset
(claims' `source_span`s, block spans, section spans, chunk spans), mounted read-only for
agents (D51). `blocks.json` sits beside it in the version's artifact directory (D37/D55).
Blocks are deliberately **not Postgres rows** (~10⁸ substrate rows nobody queries
individually); the spine gets only derived keys (§8) — the same bodies-in-GCS/keys-in-PG
split as D37.

## 3. Sections on the block grid (D57)

PageIndex **creates meaning independently** — it reads `document.md` and emits the section
tree; it neither needs nor sees blocks. Being an LLM tool, its proposed boundaries can land
anywhere — including mid-paragraph — and chunks are runs of *whole blocks* that must never
cross a section, so unsnapped boundaries would make the two constraints unsatisfiable. Its
output is therefore **normalized onto the block grid**:
a deterministic post-step snaps every section boundary to a block boundary, and sections are
*persisted as block ranges* (`blocks 14–31`), never raw char spans. Four reasons:

1. **Forced by constraint consistency.** "Chunks are runs of whole blocks" ∧ "chunks never
   cross sections" is satisfiable only if sections are unions of whole blocks.
2. **LLM output needs a deterministic normalization target.** PageIndex spans can overlap,
   gap, or drift; snapping is where they are sanitized into a well-formed partition (cover,
   nesting, no mid-block cuts). LLM proposes, a deterministic layer disposes — the system's
   standing pattern.
3. **Semantically almost free.** Real sections start at headings/paragraph breaks — block
   boundaries. Where it bites (a run-in heading OCR'd into its first paragraph), the error
   originated in conversion quality, and the cost is coarser role granularity, never
   wrongness.
4. **Stability and cheap bookkeeping.** Block-ordinal ranges survive re-rendering, make
   section membership an integer range lookup, and compose with the edit diff.

**The snap algorithm** (deterministic and total — every malformed input has a defined
outcome; Codex review F14):

1. **Snap starts**: each proposed section start snaps *backward* to the start of the block
   containing it; a start before block 0 clamps to block 0.
2. **Order and dedupe**: sort siblings by (snapped start, then *longer proposed span first*,
   then PageIndex emission order as the final tie-break); siblings sharing a snapped start
   collapse into one (the longer span wins; the loser's blocks follow the winner).
3. **Partition siblings**: each sibling ends where the next begins (overlaps resolved by the
   ordering above; gaps between siblings belong to the *parent's* direct content, never to a
   child); the last sibling ends at its parent's end.
4. **Repair nesting**: child ranges clip to their parent's range; a child left empty after
   clipping is pruned; zero-length sections are pruned.
5. **Root coverage**: the synthetic root always spans the whole block sequence — every block
   ends up in exactly one deepest section; roles/titles inherit from the nearest surviving
   ancestor.

A document never fails structuring — malformed PageIndex output degrades to a coarser but
well-formed partition. The algorithm is pure (proposed spans + block grid in; section tree
out), so it regression-tests beside the blockizer's golden corpus.

**The direction invariant — never flip it:** sections are *expressed in* block coordinates;
blocks are never derived *from* sections. The moment LLM-drawn boundaries influence where
blocks fall, the identity layer is poisoned and reuse collapses. One-way street:
converter → blockizer → blocks; PageIndex → snap → sections; both → packer → chunks.

**Per-source variability** (already the e0 §4 contract, finished here): every document gets a
section structure unconditionally — a paper gets a rich tree doing real work (boundaries,
`references` role for Selection, placement); an email degrades to the synthetic root with
blocks ≈ paragraphs, and the machinery is uniform.

## 4. Chunks: non-overlapping runs of whole blocks (D58)

**Packing rules.** A chunk is an ordered run of **whole blocks within one section**, packed
by **semchunk** (the imposed constraint, kept as the packer) to the token budget:

- **Never split a block** — with two edge rules: an oversized *atomic* block (a big table)
  becomes its own oversized chunk rather than being split mid-row; a pathological giant
  paragraph falls back to deterministic sentence-splitting.
- **Never cross a section boundary** (§3 makes this well-defined).
- **No overlap — rejected outright**, three reasons in order of severity: (1) overlap
  **double-extracts** — the same sentence in two chunks yields duplicate claims *within one
  generation*, re-polluting the evidence counting D54 just fixed, and doubles extraction cost
  on overlapped regions; (2) near-duplicate vectors bloat P1 and add ranking noise; (3)
  window boundaries are offset arithmetic — *any* edit shifts every downstream window,
  destroying D56 reuse entirely. Where overlap has a real virtue (context continuity across a
  boundary), the E2 bundle already provides it explicitly (±N neighbor chunks) without paying
  identity or extraction costs.
- **Anchor-stabilized boundaries** (amendment A2) — the algorithm is bound; only its numbers
  are spikes (Codex review F15). **Anchor predicate**: block `b` is an anchor iff
  `uint64(block_hash) mod M == 0` *and* ≥ `min_gap` tokens accumulated since the last anchor
  (suppresses clusters); `M` targets a mean spacing of a few chunk budgets. **Packing**:
  semchunk packs to budget as usual, but a chunk boundary is *forced* before every anchor —
  packing after an anchor is independent of everything before it, so an early edit perturbs
  boundaries only to the next anchor. **Sparse anchors** (> `max_gap` tokens without one):
  budget-only packing in that stretch — graceful degradation to plain semchunk, worst case is
  today's behavior. **Dense anchors**: `min_gap` caps forced boundaries so no chunk is
  pathologically small. Oversized atomic blocks are their own chunks regardless of anchors.
  Parameters (`M`, `min_gap`, `max_gap`, budget) live in `chunker_version`; their *values* are
  spike 3. Load-bearing for sectionless documents (the synthetic-root case), where sections
  provide no containment.
- **Determinism:** chunks = f(blocks, sections, budget, anchors, `chunker_version`) — a
  chunker bump is a cheap repack of existing atoms (no re-conversion, no re-blockize), though
  it does re-key reuse (a deliberate version boundary, like any D7 version bump).
- The **token budget** is an eval number, not an argument: recall@k per size class on the
  golden set (spike 1). Starting point: mid-hundreds of tokens.

**Chunk identity:** a chunk's content key *is* its block-hash sequence —
`chunk_content_hash = hash(ordered block hashes)` — which is what makes §7's reuse rule a
sequence comparison rather than a semantic judgment.

## 5. Embedding granularity — multi-granularity by architecture, not tiny chunks (D58)

The dilution concern is real: in a **chunks-only** RAG system, a two-sentence fact inside a
1,500-token chunk is poorly findable, and the standard mitigations (tiny chunks, sliding
windows) exist because chunks are the only semantic index. **This system is not chunks-only,
and that changes the conclusion:**

- **The claims channel is the needle index.** P1 embeds every claim — atomic,
  *decontextualized*, self-contained by construction (D31). That is precisely the ideal
  fine-grained retrieval unit small-chunk strategies approximate, except cleaner: an LLM
  already isolated and de-referenced each fact. The needle-recall burden rides on claims (and
  relation/observation labels), not on chunk size.
- **The chunk channel is the passage index**: verbatim context, "what did the surrounding
  text say," agent reading material. BM25/FTS catches exact-phrase needles regardless of
  embedding dilution (S52); RRF fuses the channels (D9).
- Therefore chunks are sized for **passage coherence**, not needle recall — moderate,
  block-aligned, no overlap, no heroics.
- **Role-filtered P1 defaults**: ranked search joins each chunk to its section role;
  default search recipes exclude `references / nav / boilerplate / legal` chunks from the
  semantic channel. This is **retrieval-side filtering of what was indexed** — everything is
  still extracted (D25 untouched) and reachable by explicit filter; it just stops a paper's
  bibliography from polluting passage search.

**The embedding-model product path (D63 + D80).** The embedder is per-deployment provider-port
configuration (D61). The **shipped product path is conventional only** (`texts → vectors`) so
models stay **interchangeable** under version-scoped re-embed migration. The default model
remains **`qwen/qwen3-embedding-8b`** via OpenRouter (self-hosted weights as the second
adapter). D94 pins the reference output to 1,536 dimensions so ordinary
pgvector float32 HNSW can index it; a different dimension is a maintenance
migration, not a runtime knob.

**How conventional embeddings get document location (D80 — normative detail in
`e1_embedding_input_policy.md`).** E1 does **not** run a per-chunk LLM “where this sits”
completion on the default path. Instead:

1. **Location facts** (typed, deterministic from spine + connectors) are resolved per chunk.
2. A **versioned embedding-input policy** (pure function) selects `body_only` or
   `location_header` and builds **embedding text** (optional bounded deterministic header +
   body).
3. Headers are **conditional** — collision reduction for multi-context docs; omitted when they
   would dominate short **message_atom** bodies (location still available as facts + P1
   scalars).
4. **D79 summaries** are orientation-only: not default embedding text; not grounding sources.
5. **Vector reuse** keys on `embedding_text_hash + policy_version + embedder_generation`
   (content-hash-only vector copy is insufficient when location participates in the string).
6. **Execution:** prepare stamps per chunk; embed in bounded durable batches; representation
   readiness barrier — not one document-level all-or-nothing location+embed transaction.

**Contextual embedding models** (document-native / late-chunking APIs) are a **documented
product non-goal** under D80 (interchangeability). Historical design text that treated them as
a fully designed alternate remains non-operative for product work.

Everything else in this design (blocks, sections, packing, claims-as-needle-index) is
invariant.

## 6. Extraction batching — decoupling cost from granularity (D58)

Chunk *granularity* and E2 *call count* are decoupled: E2 **batches a section's contiguous
chunks into one call** where budget allows — the bundle (header, section path, neighbors) is
shared per call, and claims still anchor per-chunk via their source spans. Extraction cost
then scales with *tokens*, not chunk count, freeing chunk size to be chosen for
embedding/passage quality alone (§5). **Batching preserves D31's structure and the ledger
discipline** (refines D31; Codex review F9): the *two-call shape* (Selection, then the fused
decontextualize + decompose + ground call) applies to the batch window exactly as to a single
chunk — the window is the extraction unit, the calls are still two; and bookkeeping stays
per-chunk — the batch computes once, then **commits per-chunk `processing_state` rows** (each
chunk's `extraction_input_hash` marks done/failed independently, so a retry re-runs only the
incomplete chunks) and bills the batch's `cost_ledger` rows to the claiming chunk's processing
row (batch id in the call context; a batch never crosses a document or lane, so document- and
lane-level accounting stay exact). Batch size is a spike; batching is an implementation knob, never an identity
change.

## 7. Reuse mechanics (D56 bound here; amendments A1–A3)

The lifecycle design owns the *contract* (cost ∝ the edit); this section owns the mechanism:

- **A1 — block-hash diff alignment.** On a new document version: blockize, then align old and
  new block sequences by LCS/diff over block hashes (`git diff` at paragraph grain). A chunk
  is **reusable iff its constituent block-hash sequence is unchanged**, regardless of where
  it moved — offsets never participate.
- **A2 — anchor-stabilized packing** (§4) keeps the diff local for long/sectionless
  documents.
- **A3 — Derived context is carried forward under replay discipline, not regenerated when
  inputs are unchanged.** For **extraction**, reused chunks keep prior extraction outcomes
  when `extraction_input_hash` matches; unchanged regions keep the prior version's structure
  under `structurer_version`. LLM output must not enter identity keys (D7 replay-not-recall).
  Consequently the **extraction reuse key contains only stable components**:
  `extraction_input_hash = hash(own block hashes + neighbor block hashes + stable header
  facts + extractor_version + structurer_version)` — where **stable header facts** are the
  deterministic document metadata the E2 bundle feeds the extractor: title, source kind,
  source-modified/published date, language (from `documents`/`document_versions`; never
  LLM-derived) — **no LLM *output* participates in the
  key** (refines D56's original sketch, which had let the section path and prefix in — a key
  no re-run would ever match, the ~0 %-reuse hazard named in the stress test). Including
  `structurer_version` — a stable config string, not LLM output — closes the context-drift
  gap (Codex review F10): within a lineage, carried-forward structure *is* the binding context
  of reused claims, so section roles cannot drift under them silently; and a **deliberate
  structurer bump** (which may reclassify sections — a `body` → `references` role change
  alters what Selection keeps) is a re-extraction boundary by key construction.
- **Passage embedding reuse (D80 refinement).** Passage identity is the composite
  `(embedding_input_policy_version, embedding_text_hash, embedder_generation)`. Provider
  re-embed is required when the hash or embedder generation changes; a policy-only bump that
  yields the same hash may **restamp** the exactly matching vector during an explicit
  unready maintenance operation without a provider call (never leave policy version and
  vector identity disagreeing on one row).
  Content-hash-only vector copy is **incorrect** when embedding text includes location.
  See `e1_embedding_input_policy.md` §4.5.
- A chunk whose *neighbors* changed re-extracts even though its own text didn't (the bundle
  changed → the input hash changed) — correct by construction.

## 8. What reaches Postgres

Blocks stay in `blocks.json`; the spine gets derived keys only (D37 discipline):

- `chunks`: `version_id`, `block_start`/`block_end` ordinals, `chunk_content_hash` (= hash of
  the block-hash sequence), `extraction_input_hash` (§7), `section_id`, offsets; **D80**
  location-facts snapshot ref / bounded optional header, `embedding_text_hash`,
  `embedding_input_policy_version` and current embedder stamps. The normalized body and
  vector live in the sole `chunk_search` sidecar; exact body remains in the artifact
  (D37/D94). Detail: `e1_embedding_input_policy.md` §7.
- `document_sections`: `block_start`/`block_end` ordinals (the grid representation, §3);
  char spans derived.
- `document_representations` (D65): `blocks_uri` + `blockizer_version` live on the immutable
  representation row; chunks and sections carry `representation_id` — the reading whose block
  grid and offsets they index.

## 9. Decision interactions

| Decision | Effect |
|---|---|
| D38 (conversion) | **refined**: converter contract = `document.md` + source map + derived assets + manifest (D65); `blocks[]` moves out of converters into the blockizer; route pinned per lineage |
| D65 (media) | **composes**: media routes emit the same contract; block provenance is the typed `SourceLocator` union (§2); `media_design.md` |
| D39 (PageIndex) | **refined in representation**: sections persist as block ranges (snap rule §3); the tool, roles, summaries, placement hints unchanged |
| D25 (no gate) | **untouched**: everything is extracted; the role filter is retrieval-side (§5) |
| D32 (grounding) | **strengthened**: one immutable coordinate system (document.md) with block-grain back-pointers; provenance tiers named honestly |
| D54–D56 (lifecycle) | **completed**: the reuse mechanics deferred by lifecycle §6 are bound here (A1–A3); the D56 key is corrected to stable components only |
| D8/D9/D48–D50/D94 (retrieval) | **composes**: claims = needle channel, chunks = passage channel; role and `source_shape` filters join normalized authority; one current search attestation, no copied generation scalars |
| D63/D80 (embed path) | **D80 binds** conventional interchangeable embedders + embedding-input policy; contextual non-goal |
| requirements (semchunk) | **honored**: semchunk survives as the packer over blocks |
| D7/D12/D33 | **followed**: blockizer/chunker versioned; embedding-text hashes are replayed state; all derivations deterministic or ledgered |

## 10. Spikes (measure before locking)

1. **Token budget** — recall@k per chunk-size class on the retrieval golden set (D22).
2. **Blockizer segmentation quality per converter route** — paragraph fidelity of Mistral
   OCR / markitdown / docling Markdown; where merged-block coarseness concentrates.
3. **Anchor criterion** — anchor density vs boundary stability trade-off (hash criterion,
   e.g. modulo target) on real edit patterns.
4. **Reuse hit-rate under A1–A3** (moved from lifecycle §11) — block-grain, on a real watched
   corpus; the number that validates the whole D56 economy.
5. **Structurer snap distortion** — how often PageIndex boundaries land mid-block (conversion
   quality signal); structurer re-run stability across versions.
6. **Oversized-block handling** — frequency and retrieval behavior of atomic-oversize chunks
   (tables); whether tables need type-specific embedding treatment.
7. **E2 batch size** — quality vs cost of multi-chunk extraction calls (attention dilution in
   very long calls vs bundle-overhead savings).
7a. **Oversized-block constants** — the token threshold above which an atomic block becomes
   its own oversized chunk, and the deterministic sentence-splitter (library + version, pinned
   like the blockizer) for the pathological-giant-paragraph fallback.
8. **Embedding-input-policy quality (D63/D80/D94)** — model default remains
   `qwen3-embedding-8b` (conventional) and the reference dimension is fixed at
   1,536. The existing golden set may still compare body-only vs compact/full
   location-header contribution and message-atom filtering; engine migration
   does not introduce a new benchmark gate. See
   `e1_embedding_input_policy.md` §10.

## References

Decisions: **D57–D58** (this design), **D80** (embedding input policy), D7, D8, D12, D25, D32,
D33, D37, D38, D39, D51, D54–D56, D63. Discussion record:
`plan/analysis/evidence_lifecycle/stress_test_amendments.md` (objection A → A1–A3);
`plan/analysis/e1_context_prefix_efficiency/` (D80 analysis + reviews). Adjacent designs:
`e1_embedding_input_policy.md` (normative embedding input), `e0_files_design.md` (conversion,
artifacts), `evidence_lifecycle_design.md` (the reuse contract, currency, counting),
`e2_e3_claims_relations_design.md` (bundles, grounding), `retrieval_design.md` (channels),
`postgres_schema_design.md` §6–§8. Requirements: §Plane E (E1), §Imposed constraints
(semchunk).
