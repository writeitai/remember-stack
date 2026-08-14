# Requirements v3

What we want from the system. Highest level of abstraction — capabilities, properties, and
constraints; the *how* lives in `plan/designs/`. Supersedes `requirements_v2.md` (root).
Decision rationale: `decisions.md`. Open items: `questions.md`.

## Vision

- A general-purpose memory system that ingests millions of heterogeneous documents and
  distills them into progressively more abstract, navigable knowledge layers.
- Consumed primarily by AI agents, with full human auditability.
- Still valuable at a million documents — scale is a requirement, not an aspiration.
- Delivered as an **open-source library**: the complete, fully self-hostable single-deployment
  memory system — everything that determines what the memory believes and whether it can be
  trusted. A commercial cloud operates fleets of deployments and adds the human layer (web UI,
  orgs/SSO, billing); those are **non-goals of the library**, and correctness is never gated
  behind the commercial offering (D60).

## System structure: three planes (D14)

The system is organized as three planes; the plane determines trigger model, source of
truth, and rebuild semantics. L-numbers from earlier drafts survive as shorthand.

### Plane E — Evidence (per-document; relational spine is the source of truth)

- **E0 — Files** *(formerly L0)*: every input tracked and preserved (raw bytes kept immutably);
  normalized to a common text form via a **configurable conversion module** (OCR, audio/video
  **transcription** with speaker attribution, image **description** where needed — media are
  first-class inputs, D65); per-document **structure** (hierarchy + section roles + summaries)
  extracted, plus a **placement hint** for the corpus filesystem; cross-references between
  documents (e.g. citations) tracked. Converted bodies live in object storage; the spine holds
  only metadata + the queryable structure. Evidence derived from media traces to the **exact
  place in the original** (page/region/time interval) and discloses **how mediated** it is
  (rendered speech vs the model's observation vs its interpretation); the consuming agent can
  always reach the raw original — the memory ingests the derivation, never replaces the source.
- **Watched sources & document versions** (D54–D56): sources may be **watched** (a Google
  Drive folder, a mailbox, a URL — polled on a cadence); an edited document is ingested as a
  **new version of the same logical document** (identity survives edits and renames), with
  prior versions preserved as dated testimony. Per-source semantics distinguish **archival**
  documents (every version stays independent testimony) from **living** documents (the latest
  version is the source's standing statement, and removing content **retracts** beliefs it
  solely supported — recorded and reversible, never silent). Confidence signals count **distinct
  sources**, never versions, re-processings, or repetition. **Deleting a document** (at the
  source or by an operator) removes its contribution — facts it alone supported are closed,
  recorded and reversibly, while facts other documents also support live on. Re-ingesting an
  edited document must cost **proportional to the edit, not the document**.
- **E1 — Chunks** *(formerly L1)*: retrieval-sized units that preserve their surrounding
  context and trace back to the exact source document and position.
- **E2 — Claims** *(formerly L2)*: atomic, **verifiable** natural-language assertions,
  temporally classified; immutable and append-only; provenance always attached; entity
  mentions resolved to canonical entities. Non-verifiable material (unattributed opinion,
  advice, hypotheticals) is dropped at extraction-time Selection with an auditable ledger
  (D31/D34) — there is no fact/opinion/prediction claim typing. **Attributed stance is
  retained (D59)**: "X believes/said/opposes Y" is a verifiable proposition about X, kept and
  normalized to a stance observation on the holder — so "what does X think about Y, and did it
  change?" is answerable memory content.
- **E3 — Relations + Observations**: the fact layers, normalized from claims. **Relations** are
  distinct entity→entity facts `(subject, predicate, object)`; **observations** are facts about
  one entity whose object is a value or statement (a headcount, a revenue figure, an attributed
  stance — D43, D59), entity-anchored and untyped. Both carry bi-temporal validity and
  many-to-many evidence links to claims, and both are units of supersession and contradiction;
  only relations project to the graph.
- **Registries** (cross-cutting substrate of plane E — registries canonicalize, layers
  transform): canonical **entities** with aliases and resolution; governed **predicate**
  vocabulary with an escape hatch and periodic promotion.

### Plane K — Knowledge (aggregate, compiled, debounced; git is the source of truth)

One compilation mechanism, many scopes (D45–D47, refined by D73); K1 names the default scope
and K2 names additional purpose scopes. They are configuration, not separate machinery: a
deployment (including any user of the open-source library) defines its own scopes and layout.
There is no shipped K3 belief tier. Normative core principles are authored K2 content; the
system's evidence-qualified fact state remains in E3.
Two content kinds, one shared guarantee — **every K artifact records the evidence it rests
on** (citations), so staleness, deletion reach, and audit are mechanical, never guessed:

- **Compiled knowledge**: LLM-written pages derived from the evidence each page's recorded
  **routing rule** selects (entity / community / predicate / document-set keys — evaluated
  mechanically, chosen by an LLM planner); regenerated when stale (semantically reproducible);
  refreshed incrementally, never globally; body machine-owned — human input enters via
  per-page **curation** (pins / exclusions / corrections) that regeneration must honor and can
  never destroy.
- **Authored knowledge**: human/agent-authored first-class content (target states, designs,
  decisions); never auto-regenerated; cites the evidence it was based on and is **flagged for
  review when that evidence changes**.
- **K1 — General knowledge** *(formerly L3)*: the default scope — progressive-disclosure
  summaries (entity, topic, source pages) over the evidence.
- **K2 — Special-purpose scopes** *(formerly L4)*: pluggable purpose scopes (people profiles,
  business planning, as-is/to-be migration tracking, a personal operating doctrine, …);
  multiple scopes coexist and each anchors its vocabulary in a shared model page. A scope may
  mix compiled support material with authored principles, decisions, and stances. Normative
  content is promoted and changed only by its accountable author, while citations and watches
  keep it connected to changing evidence (D73).

### Plane P — Projections (derived, no authority; rebuilt on schedule)

- **P1 — Search indexes**: vector/FTS indexes over chunks, claims, and relation fact labels,
  plus **media segments** (cross-modal embeddings over images, video keyframes, and audio
  segments, so media are discoverable by what they *show and sound like*, not only by what
  their transcript/description happened to mention — D65; the media embedding model is
  per-deployment configuration, and its absence is reported as a stated capability boundary);
  fully rebuildable from the spine.
- **P2 — Graph** *(formerly L6)*: relationships between entities; bi-temporal; supports
  as-of queries; ontology starts small and evolves by governance; fully rebuildable from the
  spine.
- **P3 — Corpus filesystem**: the corpus organized as a navigable **directory tree**, materialized
  to object storage and **mounted read-only** so agentic workers can browse the memory on their
  filesystem; built from document placement hints + entities/relations (K pages are
  cross-linked, never a structural input — P3 stays rebuildable from the E spine, D40 refined);
  fully rebuildable. Agents read the curated hierarchy and drill into the source documents.

## Knowledge lifecycle

- New information **supersedes** old information without destroying it — validity windows
  close; nothing is silently deleted.
- **Contradictions are surfaced**, never silently resolved.
- **Authored knowledge is never silently undermined**: human/agent-authored pages record the
  evidence they were based on and are flagged for review when that evidence changes (D46).
- **Two time axes** everywhere knowledge lives: when a fact was true in the world, and when
  the system learned/believed it.
- **Time-travel**: reconstruct both "what was true at T" and "what did we believe at T".
- **Deletion cascade**: removing an input must propagate through every derived layer; hard
  delete supported where required.

## Retrieval

- **Primary consumers are agentic coding harnesses** (Claude Code, Codex, OpenCode, …). The
  consumption surface is designed for how harnesses actually work: they are exceptionally good
  at filesystem work, so the surface is **filesystem-first** wherever a filesystem can carry
  the capability.
- Exposed as **API, CLI, MCP server, and mounted filesystems** — the mounted read-only
  surfaces are: the **corpus filesystem** (P3), the **E0 document artifacts** (Markdown +
  per-document structure + derived media), the **E0 raw originals** (off the navigation path —
  reached only via explicit pointers; audit-logged), and the **K plane** (the knowledge repo).
  Agents browse the memory as files (`ls`/`cat`/`grep` over navigable trees).
- **Markdown-first, originals reachable**: the per-document Markdown is the primary
  agent-facing form and what navigation points to — but agents must be able to ingest source
  media directly (figures and derived imagery from the artifacts; **whole-file originals** —
  video, audio, photos — from the raw mount via explicit pointers), because conversion is
  lossy exactly where sources are non-textual.
- **Environment-adaptive, with a precedence rule**: everything readable is available through
  both the mounts and the API/CLI (full parity — some environments cannot mount). When mounts
  are available, agents are instructed to **prefer the filesystem for everything a filesystem
  can do** (navigate, read, grep) and reserve API/CLI for query-engine capabilities that have
  no filesystem equivalent (semantic search, graph traversal, temporal as-of, hydration).
  When mounts are unavailable, API/CLI carry everything.
- **A shipped consumption skill**: the system ships agent-facing instructions + reference
  documentation (a skill, versioned with the system) that teaches a cold agent the memory
  model — planes, the fact-vs-evidence grains, freshness semantics, contradiction handling —
  plus the mount layout and the precedence rules. A consumer harness must be able to use the
  memory well *without* a human explaining it.
- **Trust model (a scope boundary): one trust domain per deployment.** Content-level
  authorization and per-user scoping are **non-goals of the library** — every agent that can
  reach a deployment's API or mounts is trusted with everything in it. Isolation is achieved
  by **deployment separation** (fully independent instances — the deployment model), never by
  content filtering inside one deployment; perimeter security (who can reach the surfaces at
  all) is deployment infrastructure. (D50; `retrieval_design.md` §9.)
- Search modes: lexical (FTS/BM25), semantic, structured (filters, exact IDs), file-level
  (greps over the mounted corpus filesystem), graph (neighborhood, paths, as-of).
- Per-layer and cross-layer search; everything filterable; every result carries exact IDs and
  hydrates down to provenance (claim → evidence → source document).
- Named search recipes on top of composable primitives.
- **No LLM calls on the core search path** — query latency is retrieval-bound.
- Full flexibility is the goal: agents choose strategies, the system does not impose one.
- **The query surface must make the claim/fact temporal split explicit to agents.** Claims have
  **no temporal supersession** — they are immutable evidence (what a source asserted, including the
  validity interval *it* asserted), never closed or invalidated. **All supersession / current-fact
  validity lives on the fact layers — relations and observations.** So the querying system must
  surface, and its API/recipe naming must enforce, the distinction: a fact as-of query (over
  relations or observations) answers *"what does the system currently believe held at T"* (it honors
  supersession); a claim query answers *"what did sources assert"* (evidence, possibly stale,
  contradictory, or later superseded at the fact level). An agent must never read a claim's validity
  as the system's current belief, and "is this still true?" must route through relations and
  observations, never through claims. (See D3, D6, D41, D43, D49.)

## Operational properties

- **Single source of truth for validity** — validity/invalidation state lives in exactly one
  place; all other stores hold derived, rebuildable projections.
- **Split source of truth**: deterministic layers rebuildable from the relational spine. The
  plane-K git repo is a source of truth backed up as such — with its **irreducible core being
  human-authored content** (authored pages + curation); compiled pages are semantically
  regenerable from the spine plus their recorded compile inputs (D45/D46).
- **Rebuildability is exercised**, not assumed (periodic rebuild as the normal sync path
  and/or drills).
- **Idempotent processing**: every worker re-runnable, keyed by content hash + processing
  version.
- **Versioned processing**: prompt, model, and embedding versions recorded for every derived
  artifact.
- **Cost discipline**: cheap-first cascades — deterministic checks before small models before
  frontier models; LLM spend scales with ambiguity, not volume; per-layer cost metering and
  budgets.
- **Cost-proportional extraction**: extraction effort must not be wasted on junk. Junk is filtered
  **in-call at claim extraction** (proposition-level verifiability — opinion / boilerplate / intro /
  references dropped where they are cheapest to identify), and redundant *facts* collapse into one
  relation with an evidence count rather than re-paying per duplicate; exact-duplicate inputs are a
  no-op re-ingest (idempotency). Nothing is silently dropped — every input stays in the immutable
  originals and is always re-extractable. (No separate pre-extraction value/salience gate; see D25.)
- **Failures never disappear**: bounded retries, then dead-letter with recorded status.
- **Self-hostable**: one deployment runs completely on self-managed infrastructure through the
  provider ports (D61), with every correctness capability included; a runnable self-host stack
  is part of the open-source deliverable (D60).
- **Freshness**: plane E processes promptly per document; planes K and P are
  debounced/scheduled — staleness bounded by an explicit, configurable cadence.
- **Observable and recoverable, without an OSS operations platform**: typed telemetry and
  CLI/admin inspection cover every pipeline; the portable-state definition, fail-closed restore
  order, and non-resurrection contract cover the sources of truth. Operators move bytes with
  native tools. Dashboard backends, alerts, HA, and physical backup schedules/retention are
  deployment-operator or cloud responsibilities (D60/D61/D75).

## Fixed choices & the reference deployment (D61)

**Fixed engine choices** — the system's identity, never abstracted behind ports:

- Relational spine and derived P1 search projection: **Postgres 17/18** with
  **pgvector** (semantic indexes) and **pg_textsearch** (BM25). **pgvectorscale**
  is the accepted measured scale upgrade for the vector index, not a second
  store or automatic default (D94). Graph: **LadybugDB**.
- Document structure: **PageIndex**. Chunking: **semchunk**. Claim extraction: **Claimify**
  principle.
- Plane K compilation: **Codex / OpenCode** as the planner/writer agents over a **single git
  repo**, orchestrated by the manifest-driven compile driver — one automated committer,
  dependency-ordered compiles; no concurrent sessions editing shared files (D45).

**Deployment substrate — reached through provider ports** (D61: object store, task queue/scheduler
with bounded retries (max 2) + rate limiting + dead-letter, mount publication, K git remote,
model/embedding providers, telemetry export, auth perimeter), each port with exactly two maintained
adapters:

- **Reference deployment** (the fixed production profile; also what the cloud offering runs):
  Postgres on **Hetzner**; workers on **GCP Cloud Run jobs** via **Cloud Tasks**; **GCS** buckets
  with **gcsfuse** mounts.
- **Self-host profile**: S3-compatible object store (e.g. MinIO), Postgres-backed queue, local
  directory mounts, any git remote, BYO model keys.

## Code

- Python, typed as strictly as practical (Pydantic, TypedDict, enums, Literal).
- Pyright for type checking; pytest for unit tests; Alembic for schema migrations.
- **All configuration enters through pydantic-settings** (`BaseSettings`) — environment
  variables are read in exactly one place, as typed, validated settings objects. **Direct
  `os.environ` / `os.getenv` access is banned** (enforced by lint — ruff `TID251`); an
  exception requires a per-line ignore with a reason.
- **Secrets are typed `SecretStr`/`SecretBytes`** in settings — never plain `str` — so they
  cannot leak into logs, reprs, or tracebacks; unwrap (`.get_secret_value()`) only at the
  call site that actually needs the value, never store the unwrapped form.
- Docstrings, comments, well-structured modules.
