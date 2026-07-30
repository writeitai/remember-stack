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

## The docs site ships with the code (D66)

`website/` is the public documentation site (Next.js + MDX, statically exported to
GitHub Pages at `docs.remember.dev`). Two standing obligations when implementing:

- **Same-PR docs.** Any PR that changes user-facing behavior — CLI commands, API/MCP
  surface, configuration, mounts, connectors, deployment, the consumption skill — updates
  the affected `website/src/app/docs/**/page.mdx` in the *same PR* (creating the page per
  the target IA in `website/README.md` if it doesn't exist yet), and keeps
  `/docs/project-status` truthful.
- **Docs describe what ships, designs describe the full scope.** A `page.mdx` documents
  behavior that exists on `main` — never aspirations (readers will run what it says). The
  full-scope intent lives in `plan/`; do not blur the two. Authoring conventions and the
  page template: `website/README.md`.

When in doubt on any rule, favor the version a stranger could read cold and fully understand,
describing the whole system.
