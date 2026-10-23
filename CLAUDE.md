# CLAUDE.md — working agreement for this repo

RememberStack is open memory infrastructure for AI agents. Its implementation and binding design
corpus live together in this repository. The planning hierarchy is described in `README.md` —
read it first:

- `plan/requirements/` — *what* the system must do (highest abstraction, mostly bullets)
- `plan/designs/` — *how* it works (binding architecture)
- `plan/analysis/` — *why we believe things* (research, drill-downs; may be messy/superseded)
- `plan/plans/` — *in what order* to build it (sequencing)
- `decisions.md` (root) — the architecture decision log (D1, D2, …), the canonical record

When editing any of these, four rules are **non-negotiable** (Rule 4 also governs code,
tests and reviews).
Read [the design-corpus skill](.agents/skills/design-corpus/SKILL.md) before
creating or editing planning and decision documents. Its current-state rule
means `plan/designs/` must be reconciled when a design changes, and superseded
decisions must be marked inside their original entries.

## Rule 1 — Design docs must be understandable by both future agents AND humans

A design or decision document is read by people who were **not** in the conversation that
produced it — a future agent with no memory of this session, or a human implementer who is not
a specialist in the subject. Write for them.

- **Explain, don't just name.** Naming a technique ("HAC distance-cut", "nDR n=1",
  "transitive closure") is not explaining it. State, in plain language, *what it is, what
  problem it solves, and why we chose it* — with a concrete example where it helps. If a reader
  must already know the field (entity resolution, graph DBs, IR) to follow the doc, the doc is
  not finished.
- **The reasoning must live in the doc, not in someone's head.** Do not rely on the reader (or
  a future agent) re-deriving the rationale from domain knowledge. A terse decision-log entry
  may state the conclusion; the corresponding *design section* must make it self-contained.
- **Define jargon on first use; keep technical terms as anchors in parentheses** so an
  implementer can still find the precise method, but lead with the plain-English meaning.
- Match the surrounding style; prefer concrete examples over abstraction.

## Rule 2 — We design the FULL scope, not an MVP or a phased subset

This project designs the **complete intended system** (it targets millions of documents — scale
is a *requirement*, not a someday-goal). Design and decision docs describe that complete system.

- **No "Phase 1 / v1 / for now / later / defer / MVP" framing in design or decision docs.**
  Build-sequencing — what to implement first — is a separate concern that belongs in
  `plan/plans/`, never as a hedge inside a design.
- Distinguish two different moves, and keep only the first in design docs:
  - **Simplification** — removing machinery a simpler mechanism makes unnecessary *at any
    scale* (e.g. the extraction LLM already yields the entity type, so no separate typing
    cascade). This is correct full-scope design. Keep it.
  - **Deferral / phasing** — keeping a piece but tagging it "build later". This is MVP thinking;
    it does not belong in a design doc.
- A genuine **scope boundary** (something deliberately *not* in the system — e.g. rebuild-first
  graph sync, with incremental application a documented non-goal) is design content: state it as
  a *non-goal / documented alternative*, not as a future "phase".
- Numbers (thresholds, sizes, costs) are starting points to be measured, not committed
  constants — label them as such rather than as "v1 values".

## Rule 3 — The library boundary is binding (D60/D61)

This repo is the **complete single-deployment memory system — and only that**. It ships as open
source with a commercial cloud built around it; the boundary lives here so it cannot erode
silently. When writing any design or decision:

- **Never assume a web UI or a multi-tenant control plane exists.** They belong to the separate
  cloud product. Agent surfaces (API / CLI / MCP / mounted filesystems) must remain the complete
  consumption story.
- **Never place a correctness-determining mechanism outside this repo** or make it conditional on
  a commercial offering — extraction, resolution, supersession, grounding, evals, budgets, DLQ,
  and deletion are always fully here.
- **Cloud-facing needs are met only through the declared provider ports (D61)**, and no extension
  point may let a consumer bypass an invariant (ingestion always writes through E0; review always
  appends reversible verdicts; a control plane is never an authority for E/K/P truth).

## Rule 4 — A new version costs only its edit (D56)

**The requirement (owner, binding).** When a new version of a document arrives, paid work —
embedding and extraction model calls — happens only where the document changed. Unchanged
sections must reuse their embeddings and extracted claims. Example: rewording one paragraph
of § 12 of a 200-section contract must not re-embed or re-extract § 1–11 or § 13–200.
Extraction is the dominant ingestion cost, and this promise has been broken before by
changes that looked unrelated, so check it on every change, in code, designs and reviews.

**How the engine implements it today.** E1 deterministically re-packs every new
representation into new chunk rows (fresh `chunk_id`s — that part is cheap); what carries
over are content identities and the paid products keyed on them
([e1_chunks_design §4 and §7](plan/designs/e1_chunks_design.md)):

- **Packing (D58).** Chunks are whole-block runs packed per deepest-owner section, so an
  edit never moves chunk boundaries in another section. Inside the edited section, a
  length change can shift later boundaries until the next accepted content anchor (a block
  whose hash satisfies `uint64(block_hash) mod anchor_modulus == 0`, at least
  `anchor_min_gap_tokens` after the previous one) or the section's end.
- **Embeddings (D80).** A vector carries forward from an earlier version or an earlier
  processing generation of the same version of one
  lineage when `chunk_content_hash`, `embedding_text_hash` (the rendered D80 input:
  location header + body, so it depends on all D80 location facts: title, source kind and
  shape, section title, path and role, chunk count, and message metadata), the policy
  generation and the embedding model all match. Neighbours are not part of this key.
- **Selection (D56, D140).** `extraction_input_hash` = own block hashes + previous/next
  *same-section* neighbour hashes + header facts (`header_title()`, `source_kind`, the
  chunk's text-origin date, `language`, `file_name`; plus the eligibility policy for
  ineligible chunks) + canonical converter-origin hashes for the target and those
  same-section neighbors + extractor and structurer versions. Origin coordinates are
  relative to their consumed window, never absolute document offsets. D140's text origin keeps the date
  stable for unchanged chunks across dated versions; it is looked up through
  `reuse_identity_hash`, which is the same identity without the date plus
  `REUSE_IDENTITY_VERSION` and `blockizer_version`.
- **Claimify (D122).** `claimify_input_hash` = the target's Selection hash + the Selection
  hashes of the **previous eight chunks in document order** + `REFERENCE_POLICY_VERSION`
  ([document_reference_context_design](plan/designs/document_reference_context_design.md)).
- **Span remapping (D119).** Reused cards and claims are re-anchored by relative offset and
  exact substring match; a failed remap re-extracts.

**Known gaps against the requirement** (not yet fixed; a design to close them is pending
the owner's decision — do not describe them as solved):

1. The D122 Claimify window crosses section boundaries, so an edit near the end of § 12 can
   re-run Claimify for up to eight following chunks in § 13 and beyond.
2. In long sections with few anchors, a length-changing edit can re-pack, and so
   re-extract and re-embed, the rest of that section.
3. The E0 section-summary cache key includes block ordinals and `node_path`, so inserting a
   block re-runs summary calls for every later section (summaries stay out of the
   extraction and embedding keys).
4. Model-based structure fallback and title-role classification are not deterministic; a
   different answer on a new version moves section ownership or roles, which changes
   packing, neighbours and embedding inputs.

**Rules for every change:**

- **No fabricated volatile data in keyed inputs**: no generated dates, banners, counters,
  timestamps, IDs or renumbering in header facts, titles, file names or chunk text. Real
  source content may change: a genuine rename of the title or file name is an intended
  boundary (D134), and real dates in the text stay.
- **No model output in an identity** (D56): summaries, prefixes, section paths and offsets
  never enter a reuse key; committed derived context is carried forward for unchanged
  regions.
- **Declare every reuse boundary.** Changing any of these re-keys some stage and must be
  stated in the design and PR (what re-processes, once, and why) and accepted, never left
  as a side effect: `BLOCKIZER_VERSION`, `CHUNKER_VERSION` and `ChunkerParams`,
  `E0_CONVERT_VERSION` and converter routes, `E0_STRUCTURE_VERSION`,
  `SKELETON_PARSER_VERSION`, `REUSE_IDENTITY_VERSION`,
  `EXTRACTION_ELIGIBILITY_POLICY_VERSION`, `E2_EXTRACTOR_VERSION` (includes
  `SECTION_ORIENTATION_VERSION`), `REFERENCE_POLICY_VERSION`,
  `EMBEDDING_INPUT_POLICY_VERSION`, `E1_EMBED_VERSION`, the embedding model, and
  `E3_NORMALIZER_VERSION` (reused claims keep their IDs but are normalized again).
- **Keep lineage identity stable.** Successive versions must arrive under the same
  connector-native `(source_kind, source_ref)` (D55); never append a revision or hash to
  `source_ref`. A one-shot upload is identified by its bytes, so an edited file becomes a new
  document and reuses nothing. Ingest a lineage's versions oldest first, one at a time, and
  keep their dates consistent: D140 only inherits a text-origin date that is no later than
  the new version's; an undated version inherits nothing, and nothing inherits from one.
- **Prove it with a test.** A change that can affect ingestion keeps or extends
  `src/tests/workers/test_reuse_lifecycle.py` and `test_d140_text_origin_pg.py`, or adds a
  test that ingests two versions of one lineage, with distinct source dates and stable names,
  of a document with untouched sections before and after an edited multi-chunk section. It
  counts provider calls per stage (Selection, Claimify, embedding). Chunks next to the
  edit legitimately get new Selection and origin identities; every untouched chunk outside
  the edited chunk's same-section neighbours must keep its content, Selection and
  text-origin identities and reuse the same `claim_id`s through `chunk_claims`, and every
  chunk whose embedding identity is unchanged causes no embedding call. Cover a
  packing-boundary case and a D122 window case. Cases that hit a known gap above are marked
  expected-failure naming the gap, never silently weakened.
- **Converters and connectors** must render unchanged content byte-identically across
  versions (deterministic output, exact whitespace), because the reuse keys and span
  remapping see their output.
- **Reviewers check this rule explicitly** on every PR that touches ingestion, chunking,
  E0/E1/E2, header facts, converters, connectors or version handling.

## Publish work in progress

This applies to the supervising agent and every delegated agent. Push a meaningful
checkpoint and open a draft PR early; do not keep substantial implementation only
in a local worktree while waiting for tests, reviews, or dependencies to finish.
Push subsequent coherent checkpoints and keep the PR description current about
what works, what remains incomplete, and which checks have actually run.

Draft publication is not merge approval. Complete the required integration,
validation, and reviews before marking the PR ready or merging. The supervising
agent remains responsible for the combined result.

## The docs site ships with the code (D66)

`website/` is the public documentation site (Next.js + MDX, statically exported to
GitHub Pages at `docs.remember.dev`). Two standing obligations when implementing:

- **Same-PR docs.** Any PR that changes user-facing behavior — CLI commands, API/MCP
  surface, configuration, mounts, connectors, deployment, the consumption skill — updates
  the affected `website/src/app/docs/**/page.mdx` in the *same PR* (creating the page per
  the target IA in `website/README.md` if it doesn't exist yet), and keeps
  `/docs/project/not-built-yet` truthful.
- **Docs describe what ships, designs describe the full scope.** A `page.mdx` documents
  behavior that exists on `main` — never aspirations (readers will run what it says). The
  full-scope intent lives in `plan/`; do not blur the two. Authoring conventions and the
  page template: `website/README.md`.

When in doubt on any rule, favor the version a stranger could read cold and fully understand,
describing the whole system.
