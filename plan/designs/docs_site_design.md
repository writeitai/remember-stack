# Public Documentation Site (Design)

How the project's public, human-first documentation is built, hosted, and kept truthful as
the system is implemented. Binding design for decision **D66**. The site lives in this
repository at `website/`; its authoring conventions and target page map live in
`website/README.md` (kept next to the code an author touches, not here).

> **Reading this cold.** The site is a **static documentation website** — prose pages a
> developer reads in a browser — separate from three things it must not be confused with:
> the *design corpus* under `plan/` (full-scope binding architecture, written for
> implementers), the *consumption skill* (D51 — agent-facing instructions teaching a harness
> to use a running deployment), and the *README* (the repo's front door). The docs site is
> the layer for a human evaluating or operating the system. Vocabulary: **MDX** is Markdown
> that may embed React components, and with Next.js's `@next/mdx` each `page.mdx` file *is*
> a routed page of the site; a **static export** means the build emits plain HTML/CSS/JS
> files servable by any file host — no server process.

## 1. The stack and RememberStack visual contract

The site replicates the documentation module already built for **loopy-loop** (which itself
lifted the pattern from the orchestra repo's `247agents.io` docs, modeled on how Next.js's
own documentation is authored). The decisions were argued and adversarially reviewed there
(`loopy-loop:design/designs/documentation-site.md`, PR #59 incl. Codex + Antigravity review
fixes: race-safe search, Radix-dialog mobile sidebar accessibility, WCAG AA palette
adjustments, `next/link` internal routing, static preview); this repo inherits them as a
package rather than re-deriving them:

- **Next.js (App Router) + `@next/mdx`** — every `website/src/app/docs/**/page.mdx` is a
  route; content is Markdown-first with components available when a page needs one.
- **Tailwind v4 + `@tailwindcss/typography`**, themed to the same RememberStack
  visual system as `remember.dev`: ink `#101f4a`, secondary ink `#5b6684`,
  coral `#ee5b44`, teal `#3e9b8e`, cream `#f5ebd0`, line `#e7eaf1`, soft line
  `#f1f3f8`, and surface `#fafbfd`. **Inter** is the body face and **Space
  Grotesk** the display face. The exact semantic mapping lives in
  `website/src/app/globals.css`; body-sized text stays ink or secondary ink
  while lower-contrast coral and teal are accents.
- The public lockup uses the canonical color **network-graph mark** plus the
  Space Grotesk `RememberStack` wordmark, coral rule, and
  `docs.remember.dev` subline. The vector is kept locally at
  `website/public/brand/mark.svg`; its pinned provenance is the managed-cloud
  source at
  `writeitai/ultimate-memory-cloud@d492d204e1ecae874234fc2879296679e73b8780`,
  `fe/public/brand/mark.svg`. The legacy dual-ring mark and a `CLOUD` tag are
  prohibited in OSS chrome.
- **`remark-gfm` + `rehype-slug` + `rehype-pretty-code`** (Shiki) for tables, heading
  anchors, and code highlighting.
- **Pagefind + `cmdk`** for ⌘K keyboard search: Pagefind indexes the *built* HTML as a
  post-build step and serves the index as static assets — search with no backend, keeping
  the whole site self-hostable.
- **`output: 'export'`** — a fully static site in `website/out/`; `npm run dev` for
  authoring, `npm run build && npm run preview` for the production build with live search.
- A **hand-maintained navigation array** (`src/lib/docs/navigation.ts`) is the single
  source of truth for sidebar order and prev/next pagination — deliberate, so page order is
  an editorial decision, not a filesystem accident.

The brand assets and tokens are copied rather than loaded from the managed-cloud
origin. That keeps the static artifact self-contained and makes a visual-identity
change an explicit, reviewable repository change. The evidence, rejected
runtime-link alternative, accessibility constraints, operational consequences,
and recovery behavior are recorded in
`plan/analysis/docs_visual_identity_alignment.md`.

## 2. Location and hosting

- **In this repository, under `website/`** — docs version with the code they describe; a PR
  that changes behavior can change its docs page in the same diff (the mechanism §3 makes
  mandatory). The app has its own `package.json`/toolchain and is never published to PyPI —
  it is a delivery artifact beside D62's three (repo, package, images), not part of the
  library.
- **GitHub Pages at canonical `docs.remember.dev`** (D66/D76) via
  `.github/workflows/docs-deploy.yml`: pushes to `main` touching `website/**` build
  (Next export + Pagefind index) and deploy; PRs run the build as a check only.
  Pages + custom domain require a one-time provisioning step (Pages source = GitHub
  Actions; custom domain bound in Settings; DNS-only `CNAME` in the `remember.dev` zone)
  — the committed `public/CNAME` records intent but does not bind the domain; the
  checklist lives in `website/README.md`.
- **The apex has a separate job.** `remember.dev` is the canonical product and
  managed-cloud home. Cloud `/docs/**` describes the managed offering;
  `docs.remember.dev` describes the open-source engine. This separation is one
  RememberStack identity with explicit content authority, not separate product
  brands. Rationale, rejected alternatives, failure behavior, and rollback:
  `plan/analysis/docs_domain_ownership_reconciliation.md`.
- **One trust boundary note (Rule 3):** the site is part of the open-source deliverable —
  hosting is repo-local (Pages), no private cloud project is an authority for it, and
  nothing correctness-determining lives there. A future hosting change may serve the same
  static artifact differently, but it does not change the canonical domain or move product
  truth outside this repository.

The implementation contract is exact: GitHub Pages binds
`docs.remember.dev`; authoritative DNS publishes a DNS-only `CNAME` from that
hostname to `writeitai.github.io`; GitHub terminates TLS and HTTPS enforcement is
enabled only after its certificate is approved. An unresolved record, pending
certificate, missing `/_next/` asset, or missing Pagefind index is a failed
cutover, not a reason to proxy the site through the managed cloud. Rollback
restores the preceding Pages custom domain and removes or disables the
`docs.remember.dev` record; the `remember.dev` product origin is never changed.

## 3. The truthfulness contract — how docs stay current through implementation

The failure mode of every docs site is drift: the code moves, the prose doesn't. Two rules,
enforced at the same places work already lands:

1. **Same-PR docs.** Any PR changing user-facing behavior (CLI, API/MCP, configuration,
   mounts, connectors, deployment, the consumption skill) updates the affected `page.mdx`
   in that PR — bound in `CLAUDE.md` (the standing agent contract) and in the roadmap's
   work-package execution rules (`plan/plans/roadmap.md` §6), which is what implementing
   agents actually read.
2. **Docs describe what ships.** A docs page documents behavior that exists on `main` — a
   reader will run what it says. The full-scope intent lives in `plan/`; the split is the
   same claims-vs-facts honesty the system itself enforces: `plan/` is the design's
   statement, the docs site is what is currently true of the artifact. Unshipped subsystems
   appear only on the `/docs/project-status` page, which says plainly what exists and what
   is designed. Pages are created when their subject ships (the target information
   architecture mapping routes → shipping phases lives in `website/README.md`); empty
   placeholder pages are prohibited — a stub that says nothing erodes trust in every other
   page.

The seed content (Introduction, Concepts, Architecture, Project Status) is derived from the
README, `concepts.md` §0, and `overall_design.md` — the material that is true *now*, before
any feature ships.

## 4. Non-goals

- **No versioned docs** (v1/v2 switchers): the site documents `main`; releases are young
  enough that one current version is the honest offering. Revisit only if maintained
  release branches ever diverge in behavior.
- **No docs SaaS / external search service** — self-hostability is the constraint that
  chose Pagefind; and **no server-rendered features**: everything must survive
  `clone → npm run build → serve out/`.
- **No API-reference generation in this design** — when the assured-operation registry ships
  (`retrieval_design.md` §4 renders MCP tools from its closed registry rows), the reference pages
  should render from the same registry; that mechanism belongs to the retrieval surfaces,
  and the docs site consumes its output as ordinary MDX/data.

## References

Decision: **D66** (this design). Precedent: `loopy-loop` PR #59 and its
`design/designs/documentation-site.md` (stack decisions 1–6 argued there). Authoring
conventions + target IA: `website/README.md`. Delivery-artifact context: D62
(`packaging_distribution_design.md`). The agent-facing counterpart surface: the consumption
skill, D51 (`retrieval_design.md` §8) — docs are for humans, the skill is for agents; they
must agree but never merge.
