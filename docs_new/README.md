# docs_new — the new RememberStack and remember.dev documentation

This directory holds a complete rewrite of the documentation. It is written
from the code, not from the previous docs. It is not published yet. This
file is the authoring guide: every page follows it.

Background: `design/analysis/ideal-documentation-2026-09-22.md` and
`design/plan/docs-readiness-gaps.md` in `writeitai/ultimate-memory-cloud`.

---

## 1. Names

| Thing | Name in prose | Notes |
|---|---|---|
| The open-source memory engine | **RememberStack** | Repo `writeitai/remember-stack`, image `ghcr.io/writeitai/remember-stack`, env prefix `REMEMBERSTACK_*`. |
| The Python client, CLI and MCP server | **the `remember` package** / **the `remember` CLI** | `pip install remember`, `import remember`, `remember …`. |
| The managed cloud service | **remember.dev** | Always lower case, even at the start of a sentence; rephrase to avoid that. Never "remember.dev Cloud", "RememberStack Cloud", "the Cloud". |
| Running the engine yourself | **self-hosted** | Never "OSS mode", "local mode", "Core". |
| The company | WriteIt.ai | Legal and footer only. |

Never use "Ultimate Memory", "UMC", "RememberStack Core", "ugm". The
deprecated `rememberstack` PyPI package and `rememberstack` CLI are mentioned
only in `project/changelog.md` and in the one migration note in
`reference/python-sdk.md`.

The sentence every reader must meet early (the home page and the Quickstart
carry it): *RememberStack is the open-source memory engine. `remember` is its
Python client and CLI. remember.dev runs RememberStack for you.*

Domain terms are fixed. Once `project/glossary.md` defines a term, no page
uses a synonym:

document, version, source (`source_kind` + `source_ref`), claim, fact,
relation, observation, entity, predicate, evidence, contradiction,
corroboration, chunk, section, deployment, project (remember.dev only),
organisation (remember.dev only), assured operation, envelope, readiness.

**SQL wording.** Never call it "open SQL", "open query" or "raw SQL", even
though the code uses `open_query` and "open-query tools". It is not open
access to the database. Call it **SQL queries** (the feature) over **the
query space** (`memory_v1`: the prepared, read-only views and functions).
Every statement is parsed and validated against that query space before it
runs; anything outside it is rejected. Say this whenever the feature is
introduced. Code identifiers such as `open_query` stay as they are.

Never "memory item", "record", "node" (except in the graph reference),
"namespace", "memories" as a countable noun.

## 2. Page format

Plain Markdown with YAML front matter:

```markdown
---
title: Time
description: How RememberStack records when something was true, when a source said it, and when the memory learned it.
applies_to: [remember.dev, self-hosted]
---

# Time
```

`applies_to` is one of `[remember.dev, self-hosted]`, `[remember.dev]`,
`[self-hosted]`. The site renders it as a badge under the title.

Where setup differs between remember.dev and self-hosted, use content tabs,
remember.dev first:

```markdown
=== "remember.dev"

    ```bash
    export REMEMBER_API_URL=https://<deployment-id>.dp.remember.dev
    ```

=== "Self-hosted"

    ```bash
    export REMEMBER_API_URL=http://localhost:8000
    ```
```

Tabs only where the two genuinely differ (endpoint, token, how you get a
token, how you start the engine). After setup the code is identical, and
that sameness is the point: show it once, without tabs.

Callouts:

```markdown
!!! note
    Text.

!!! warning
    Text.
```

Links are relative Markdown links to `.md` files: `[Time](../concepts/time.md)`.
Anchors use the heading text in lower-kebab-case.

## 3. Voice

The model is the problem section of the remember.dev landing page:

> Close the chat and the work is gone. The next session opens on an empty
> window: the agent does not know which decision still holds, which file
> said it, or what changed overnight. You paste the same notes in again, or
> the model fills the gap.

Rules:

- Second person, present tense, active voice.
- Say what happens, then why it matters to the reader.
- One idea per paragraph. Short sentences. Concrete nouns.
- Clarity over brevity. Explain fully, but never pad.
- Lead with the plain-language meaning; keep the precise term in
  parentheses or code as an anchor.
- Examples use one running scenario where possible: a small product team's
  notes, specs and meeting transcripts (people: Dana, the product lead; Ravi,
  an engineer; project: the billing migration). Do not use real people's
  names.
- Say "not yet" plainly. "RememberStack does not ingest audio yet." No
  apology, no roadmap date.

Banned: seamless, powerful, robust, leverage, unlock, supercharge,
cutting-edge, blazing, effortless, simply, just (as a softener), easily,
enterprise-grade, production-ready, revolutionary, game-changer, "in today's
world", "whether you're X or Y", "it's not just X, it's Y", "dive into",
"let's", emoji, exclamation marks. No sentence that could appear unchanged in
a competitor's docs.

## 4. Truth

- **Describe what exists on `main`.** Every parameter, default, limit, field
  and error named in a page must be checked in the code. Cite nothing that
  you did not see in code.
- **Code examples must be runnable** against the current `main` as written.
  Use only real method names, parameters and CLI flags.
- **remember.dev facts come from the cloud repository's code** (see §6), not
  from marketing copy.
- **remember.dev must not be described as having:** backups or restore,
  data export of any kind, an SLA or uptime figure, latency figures,
  multi-region or replication, SSO/SAML/SCIM, certifications, a free tier,
  "fully managed", "production-ready", "same API"/"drop-in compatible".
- **Availability** (whether sign-up is open) is stated in exactly one place,
  `cloud/overview.md`, as the placeholder `{{availability}}`. No other page
  says "beta", "alpha", "coming soon" or "now available".
- **Prices** appear only in `cloud/pricing.md`, using the placeholders
  `{{price.project_month}}`, `{{price.text_per_million_chars}}`,
  `{{price.storage_gib_month}}`. The page explains the three charges fully.
- **When you find a gap** (something the code exposes but does not serve, a
  default that will surprise a user, a disagreement between client and
  server, anything the docs have to work around), write the page around the
  current behaviour, and report the gap in the private gap report
  (`design/plan/docs-readiness-gaps.md` in writeitai/ultimate-memory-cloud).
  Never record product gaps, and especially never security findings, in
  this public repository.

## 5. Site map

```
docs_new/
├── index.md                          Why RememberStack                         both
├── start/
│   ├── what-is-a-memory-system.md    What is a memory system? (any reader)     both
│   ├── how-it-works.md               A five-minute tour                        both
│   ├── choose.md                     remember.dev or self-hosted?              both
│   ├── quickstart.md                 Quickstart (tabs)                         both
│   └── connect-your-agent.md         Connect your coding agent (MCP)           both
├── concepts/
│   ├── documents-and-sources.md      Documents, versions and sources           both
│   ├── claims.md                     Claims: what a source said                both
│   ├── facts.md                      Facts: what is held true                  both
│   ├── entities.md                   Entities and identity                     both
│   ├── time.md                       Time                                      both
│   ├── evidence.md                   Evidence and provenance                   both
│   ├── contradictions.md             Contradictions, corroboration, supersession both
│   ├── updating-sources.md           Updating a source: snapshot and living     both
│   ├── pipeline.md                   The pipeline and readiness                both
│   ├── architecture.md               What lives where                          both
│   ├── retrieval.md                  Retrieval: operations, search, graph, SQL both
│   └── reading-results.md            Reading a result                          both
├── guides/
│   ├── ingest-files.md               Ingest files                              both
│   ├── ingest-conversations.md       Ingest conversations and transcripts      both
│   ├── keep-sources-current.md       Keep a source up to date                  both
│   ├── wait-for-readiness.md         Wait until a document is queryable        both
│   ├── agent-context.md              Give an agent context                     both
│   ├── ask-about-the-past.md         Ask about the past                        both
│   ├── cite-sources.md               Cite the source of an answer              both
│   ├── unknowns-and-ambiguity.md     Handle unknowns and ambiguity             both
│   ├── sql.md                        Explore memory with SQL                   both
│   ├── saved-queries.md              Saved queries                             both
│   └── build-an-agent.md             Build a memory-backed agent               both
├── cloud/
│   ├── overview.md                   What remember.dev runs for you            remember.dev
│   ├── organisations-and-projects.md Organisations, projects and members       remember.dev
│   ├── tokens-and-sign-in.md         Tokens and sign-in                        remember.dev
│   ├── hosted-mcp.md                 Hosted MCP                                remember.dev
│   ├── pricing.md                    Pricing and credits                       remember.dev
│   ├── spend-controls.md             Spend caps and auto top-up                remember.dev
│   ├── limits.md                     Limits                                    remember.dev
│   ├── compatibility.md              What remember.dev serves                  remember.dev
│   ├── data-and-security.md          Data handling and security                remember.dev
│   ├── leaving.md                    Closing a project, deleting your account  remember.dev
│   └── support.md                    Support                                   remember.dev
├── self-hosting/
│   ├── requirements.md               Requirements                              self-hosted
│   ├── install.md                    Install with Docker Compose               self-hosted
│   ├── configuration.md              Configuration                             self-hosted
│   ├── models.md                     Models and providers                      self-hosted
│   ├── converters.md                 File formats and converters               self-hosted
│   ├── authentication.md             Authentication and scopes                 self-hosted
│   ├── scaling.md                    Scaling                                   self-hosted
│   ├── operating.md                  Operating the pipeline                    self-hosted
│   ├── troubleshooting.md            Troubleshooting                           self-hosted
│   ├── observability.md              Observability                             self-hosted
│   ├── upgrades.md                   Upgrades and migrations                   self-hosted
│   └── filesystem-views.md           Filesystem views                          self-hosted
├── reference/
│   ├── http-api/
│   │   ├── index.md                  Conventions: auth, scopes, errors, limits both
│   │   ├── ingest.md                 Ingest, readiness, documents              both
│   │   ├── operations.md             Assured operation routes                  both
│   │   ├── entities-and-facts.md     Resolve, lookup, hydrate, transcript      both
│   │   ├── search.md                 Search and adjacent chunks                both
│   │   ├── graph.md                  Graph                                     both
│   │   ├── query.md                  SQL queries                               both
│   │   └── deployment.md             Deployment info and health                both
│   ├── assured-operations.md         Assured operations                        both
│   ├── result-types.md               Result types                              both
│   ├── query-space.md                Query space memory_v1                     both
│   ├── python-sdk.md                 Python SDK                                both
│   ├── cli.md                        CLI                                       both
│   ├── mcp.md                        MCP tools                                 both
│   ├── configuration.md              Configuration variables                   self-hosted (client vars both)
│   ├── errors.md                     Errors and status codes                   both
│   └── cloud-api.md                  remember.dev API                          remember.dev
├── project/
│   ├── benchmarks.md                 Benchmarks and how we measure             both
│   ├── changelog.md                  Releases and changelog                    both
│   ├── not-built-yet.md              What is not built yet                     both
│   ├── glossary.md                   Glossary                                  both
│   └── contributing.md               Contributing, license, trademarks         self-hosted
└── llms.txt                          Index for agents
```

## 6. Sources

Read code, configuration, migrations, tests, `openapi.json`, `compose.yaml`,
`.env.example`, `Dockerfile*`, `decisions.md`, `plan/` and `design/`.
Prefer code over design: designs describe the full intended system, pages
describe what ships.

Do **not** read the previous documentation: `README.md` files, `docs/`,
`website/`, `*.mdx`, `llms*.txt`, `CONTRIBUTING.md`, `HANDOFF.md`.

remember.dev facts come from `/Users/jpuc/code/moje/ultimate-memory-cloud`:
`src/ultimate_memory_cloud/`, `fe/src/features/` (not the docs routes),
`fe/openapi.json`, `design/designs/`, `design/decisions.md`,
`growth_hacking/claim-card.md`. Do not read its `docs/`, `fe/public/docs`,
`fe/src/app/(public)/docs`, `README.md` or `llms*.txt`. That repository is
read-only for this work.

## 7. Known facts about remember.dev (verified 2026-09-22/23)

- One organisation holds billing and the team; each project gets one
  isolated deployment (its own database, storage, credentials, model key and
  hostname).
- Endpoint: `https://<deployment-id>.dp.remember.dev`. Traffic goes directly
  to the deployment.
- Deployment API tokens (`umc_dp_…`): created in the console under
  Settings → API Tokens; owner-only; shown once; 365-day default expiry,
  730 maximum; at most 20 active per deployment.
- `remember login` device flow (approved at `remember.dev/app/device`).
- Hosted MCP at `https://remember.dev/app/api/mcp` with OAuth; the user picks
  one project in the browser.
- The fleet currently runs engine v0.16.0 while `main` is v0.17.0. A
  migration to the `remember` package and a newer engine is in progress in
  parallel. Pages describe v0.17.0 behaviour; `cloud/compatibility.md` states
  the difference.
- Prepaid credits (1 credit = €1, tax excluded, no expiry); three charges:
  active project, processed text, stored data; reading is not charged;
  monthly hard cap; project sub-caps; auto top-up; parked work resumes after
  a purchase.
- Ingest body limit 100,000,000 bytes (413). Storage per project 2 GiB and
  100,000 objects.
- SQL queries (`/query/*`, the `memory_v1` query space and saved queries)
  are served on remember.dev (owner decision 2026-09-23). Document it as
  available on both.
- No data export (D80). No backup promise (D77). Diagnostics traces are on
  by default with an opt-out in Settings.
