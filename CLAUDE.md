# CLAUDE.md — working agreement for this repo

RememberStack is open memory infrastructure for AI agents. Its implementation and binding design
corpus live together in this repository. The planning hierarchy is described in `README.md` —
read it first:

- `plan/requirements/` — *what* the system must do (highest abstraction, mostly bullets)
- `plan/designs/` — *how* it works (binding architecture)
- `plan/analysis/` — *why we believe things* (research, drill-downs; may be messy/superseded)
- `plan/plans/` — *in what order* to build it (sequencing)
- `decisions.md` (root) — the architecture decision log (D1, D2, …), the canonical record

When editing any of these, three rules are **non-negotiable**.
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

The engine's ingestion is built so that **a new version of a document is re-processed only
where it changed**. Unchanged parts reuse their previous chunk keys, embeddings and extracted
claims; only the edited chunks, plus at most their immediate neighbours in the same section,
are re-chunked, re-embedded and re-extracted. Example: in a 200-section contract, rewording
one paragraph of § 12 must not touch § 1–11 or § 13–200. This is a core product promise —
extraction is the dominant ingestion cost — and it has been broken by changes that looked
unrelated. Treat it as an invariant on every change, in code, designs and reviews:

- **Know what the reuse key contains** (D56, refined by D140): the chunk's own block hashes,
  its previous/next same-section neighbour hashes, the stable header facts (title, document
  kind, language, file name — D134 — and the chunk's text-origin date, D140), and the
  extractor and structurer versions. Embeddings carry forward on the content and
  embedding-input hashes (D80). Chunk boundaries are anchor-stabilized, whole-block runs
  (D58), so an edit cannot shift boundaries elsewhere in the document.
- **Do not put anything version-specific into those inputs.** A per-version date, banner,
  counter, timestamp, generated ID or renumbered heading in the header facts or chunk text
  changes every key and re-extracts the whole document on every version. Dates belong in
  D140 text-origin/effective-period metadata, never in the keyed text.
- **Any change to chunking, the block grid, the reuse key, header facts, text-origin, the
  embedding-input policy, or the extractor/structurer/parser version strings is a reuse
  boundary.** Call it out explicitly in the design and PR (what re-processes, once, and
  why), and get it accepted — never let it happen as a side effect.
- **Prove it with a test.** A change that can affect ingestion must keep (or add) a
  regression test that ingests v1 of a multi-section document and then v2 with one
  paragraph changed, and asserts that only that paragraph's chunk and its immediate
  same-section neighbours get new extractions and embeddings, while every other chunk
  reuses its claims and vector.
- **The same applies to anything that feeds the engine**: connectors and converters must
  produce byte-identical output for unchanged content across versions (same title and file
  name for every version of a document, deterministic rendering).
- **Reviewers check this rule explicitly** on every PR that touches ingestion, chunking,
  E1/E2, header facts, converters or version handling.

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
