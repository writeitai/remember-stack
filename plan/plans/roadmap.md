# Roadmap — Build Order for the Complete System

The master sequencing document: in what order the designed system gets built. This directory
is the **only** place phasing is allowed to exist (CLAUDE.md Rule 2 keeps it out of designs);
conversely, nothing here re-explains a design — **plans reference designs, never duplicate
them** (README). The designs say *how*; this directory says *in what order* and *when a piece
counts as done*.

**Who this is for:** coding agents (Claude Code / Codex / OpenCode) implementing the system,
and the human sequencing them. A work package is written so an agent can execute it after
reading only the documents named in its *Reads* column — never the whole corpus.

## 1. How to use this directory

- `roadmap.md` (this file): the phase spine, the technology stack, the gate register, the
  work-package format, and maintenance rules.
- `phase-N-<name>.md`: one file per phase — entry gates, exit criteria, and a table of work
  packages (WP-N.x).
- **Statuses** live in the phase files (`planned | in-progress | done | blocked(<gate>)`),
  one status column per WP; this file's spine table carries only per-phase status.
- When an open decision or spike resolves, it moves to `decisions.md` / the design's spike
  list as usual — the gate register here just flips; **plans never become a second registry**
  (`questions.md` remains the open-decisions register; link, don't copy).

## 2. Four principles (why the order is what it is)

1. **Tracer bullet first.** The system's highest risk is spine *integration*, not any single
   layer. Phase 1 builds one document end-to-end (bytes → blocks → chunks → claims → facts →
   search → answer) with everything minimal; each later phase thickens a *working* system.
2. **The eval harness precedes everything tunable.** Nearly every design defers its numbers
   to golden-set measurement (D17 thresholds, D35 recall, D43's adjudicator gate, D56 reuse
   rate). The harness (questions #14) is Phase 0, alongside the schema.
3. **Open decisions and spikes are explicit gates.** §5 consolidates them and maps each to
   the phase it blocks. "What must be decided or measured before phase N starts" is a lookup.
4. **Work packages are pointers with contracts.** One-line goal, a minimal reading list,
   dependencies, a deliverable, and acceptance criteria drawn from the designs' *own*
   contract tests (the S-battery, CI invariants, canaries). No implementation steps here.

## 3. Technology stack (binding for all phases)

From `requirements_v3.md` §Code + §Imposed constraints — consolidated here so no work package
needs to restate it:

- **Language:** Python, typed as strictly as practical — Pydantic models at boundaries,
  `TypedDict` / `enum` / `Literal` internally; **pyright** in CI at the strictest practical
  setting; no untyped public functions.
- **Testing:** pytest; the eval harness (Phase 0) is pytest-driven; the design-contract tests
  (grain CI, envelope invariants, canaries) run in the same CI as unit tests.
- **Migrations:** Alembic, generated against `postgres_schema_design.md` (the schema doc is
  the source of truth; migrations implement it, never fork it).
- **The engine — fixed everywhere, never abstracted (D61 anti-goal):** PostgreSQL 18
  (the spine and P1 search), LadybugDB (P2), the E/K/P data model, PageIndex (structure), semchunk
  (packing), Codex/OpenCode (K producers) with cross-family checkers (D53).
- **The substrate — reached only through the D61 ports**, each with a self-host and a GCP
  reference adapter: object store (MinIO/local ↔ GCS), task queue (**delivery-only** over
  `processing_state`: the pg `LISTEN/NOTIFY`+`SKIP LOCKED` shell ↔ Cloud Tasks push, one
  janitor for both — D62), mount publication (local dirs ↔ gcsfuse), K git remote, model
  providers, telemetry, auth perimeter. Vendor SDKs live only in `adapters/`; designs naming
  Cloud Tasks/GCS mean the port contract.
- **The reference deployment** (the production profile; what the cloud runs): Postgres on
  Hetzner + pgBouncer, GCP Cloud Run jobs via Cloud Tasks, GCS + gcsfuse.
- **Code architecture (D62, binding):** hexagonal layout
  (`model/core/spine/ports/adapters/llm/workers/surfaces/eval/profiles`), dependency arrows
  **enforced by import-linter in CI** (core is pure; SQL only in `spine/`; vendor SDKs only in
  `adapters/`); explicit constructor-injection profiles, no DI framework.
- **WP-0.1 scaffold conventions — resolved from merged evidence (2026-07-17):** these are
  repository-wide choices, not owner-input placeholders. [PR #39](https://github.com/writeitai/remember-stack/pull/39)
  (merge [`eccc693`](https://github.com/writeitai/remember-stack/commit/eccc693a16d3e32305f142f8f6e04273793996e0))
  established the scaffold and [PR #41](https://github.com/writeitai/remember-stack/pull/41)
  (merge [`ec5ce3a`](https://github.com/writeitai/remember-stack/commit/ec5ce3ac8e3ca3850ac0eab4e3bce7a8dc87d470))
  established the configuration/secrets convention:
  - **Package, dependency, and build management:** `uv` owns environment/dependency resolution
    and the committed lock ([`uv.lock`](../../uv.lock)); Hatchling is the build backend and
    package builder ([`pyproject.toml`](../../pyproject.toml)).
  - **Lint, format, typing, and tests:** Ruff supplies lint and formatting, Pyright supplies
    type checking, and pytest + pytest-cov supply tests and coverage. Their binding settings
    and locked development dependencies live in [`pyproject.toml`](../../pyproject.toml).
  - **Repository layout and names:** one Python distribution uses the `src` layout. D76 binds
    distribution/import `rememberstack`, CLI `remember`, product RememberStack, and canonical
    home `remember.dev`; tests use `test_*.py` under [`src/tests/`](../../src/tests/). D62's
    hexagonal package directories and import boundaries remain unchanged.
  - **CI provider:** GitHub Actions runs the locked environment on Python 3.12, 3.13, and 3.14, with
    Ruff lint/format, Pyright, pytest/coverage, and the combined coverage report defined in
    [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).
  - **Configuration and secrets:** runtime configuration enters only through typed
    `pydantic-settings` `BaseSettings` models; secrets use Pydantic `SecretStr`/`SecretBytes`;
    direct `os.environ`/`os.getenv` access is banned by Ruff `TID251`. The complete convention
    is in [`requirements_v3.md` §Code](../requirements/requirements_v3.md#code), with the
    direct-access enforcement in [`pyproject.toml`](../../pyproject.toml). No runtime settings
    object is claimed by this reconciliation; it records the merged convention and lint guard.

## 4. The phase spine

| Phase | Name | Builds | Keyed to designs | Status |
|---|---|---|---|---|
| 0 | Foundations + harness | scaffolding, migrations, tenancy, `processing_state`/`cost_ledger`, queues, **eval harness + golden-set tooling**, blockizer golden corpus | schema; orchestration §1–2; D22 | done (exit criteria met 2026-07-18; WP-0.4c completed 2026-07-22; WP-0.4b + WP-0.6 remain carried per the phase file) |
| 1 | Walking skeleton | one document end-to-end, everything minimal; 4 retrieval primitives + envelope core | e0, e1, e2_e3, observations, retrieval §2–3 | done (exit criteria met 2026-07-18; PRs #81-#87 — see the phase file) |
| 2 | Truth machinery | full ER cascade + registries + review queue; supersession; observation adjudication; thresholds measured | registries, e2_e3 §5, observations | done (exit criteria met 2026-07-19; PRs #88-#94 — see the phase file) |
| 3 | Evidence lifecycle | lineages/versions, Drive connector + sync cycles, currency + counting + reconciliation, chunk reuse, deletion grains, full PageIndex route | evidence_lifecycle, e1 §7, e0 | done (exit criteria met 2026-07-19; PRs #95-#99 — see the phase file) |
| 4 | Projections | P2 (spikes → views → rebuild → snapshots), P3 (tree + mounts incl. raw), communities | p2_graph, e0 §6, `p3_agent_navigation.md` | done (exit criteria met 2026-07-19; PRs #100-#104 — see the phase file) |
| 5 | Retrieval complete | full primitives + recipe registry, envelope contract CI, MCP/CLI, batch scan, **consumption skill + S58** | retrieval | done (exit criteria met 2026-07-20; PRs #105–#111 — see the phase file) |
| 6 | Plane K | planner/writer/driver, fact-sheet → prose bands, citations/staleness, authored + sidecars, triggers + subscriptions, K1 + K2 purpose scopes | k_layers | done (exit criteria met 2026-07-21; PRs #112–#117; former WP-6.7 removed by D73) |
| 7 | Operational correctness + portability | backfill/reprocessing, fixed scale batteries, configurable budgets, failure inspection/drills, hard-delete, release, portable restore | orchestration, packaging, schema §12–13 | done (exit criteria met 2026-07-23; PRs #120, #122–#124, #126, #128, #131; public `v0.1.0` release proof) |
| 8 | Competitive benchmarks | external benchmark harness, adapters, OSS baselines (Mem0/Graphiti), capability benchmark, published methodology + results | D22 (internal) + D78 + `phase-8` survey | in progress (WP-8.1 selection complete; WP-8.2 LoCoMo setup implemented, owner-reviewed smoke intentionally pending) |

**Identity/retrieval reform (D95–D97), not a new numbered phase.** Sequencing:
[`entity_identity_and_retrieval.md`](entity_identity_and_retrieval.md). Binding:
[`entity_identity_and_retrieval_design.md`](../designs/entity_identity_and_retrieval_design.md).
This reform of shipped Phase-2 ER + E3 + default retrieval does **not** reopen Phase 2
exit; it is a later train with its own WPs.

Sequencing calls already argued (see the phase files for the rest): **K after retrieval**
(agentic writers consume retrieval tools — build against a finished surface); **lifecycle
before projections** (P2/P3 views are simpler written against lineages than re-plumbed later).

## 5. Gate register

**Decision gates** (open items in `questions.md` that block a phase; resolve → new D-number
as usual):

| Gate | Blocks | What must be decided |
|---|---|---|
| stack conventions (§3; **resolved 2026-07-17**) | Phase 0 WP-0.1 | Closed by the merged scaffold in [PR #39](https://github.com/writeitai/remember-stack/pull/39) and configuration convention in [PR #41](https://github.com/writeitai/remember-stack/pull/41); §3 maps every former slot to its exact repository evidence. |
| public-release governance (`questions.md` §11a; **resolved 2026-07-23 → D77**) | first public tag, first outside PR | Closed: owner accepted the documented naming risk; the repository-native, self-hosting-bounded CLA and narrow trademark policy are present; immutable assent is enforced by the required `CLA` status on `main`. |
| #3 embedding model + dimension (**resolved** → D63) | Phase 1 entry | closed: `qwen3-embedding-8b` port default; conventional + prefix binds (e1 §5); stored dimension remains a D22 measurement |
| #4 LLM per stage (**extractor seat resolved** → D70) | Phase 2 (adjudicators), Phase 6 (K writers) | extraction default `gpt-5.6-luna` closed Phase 1's gate; remaining seats inherit the port-default principle, gated by their phases' measurements (D53 family split holds) |
| #7 PageIndex hosted vs self-hosted (**resolved** → D71: neither — a port-configured LLM seat + deterministic snap) | Phase 3 (full structure route) | closed: the snap guards any seat's output; no external tool dependency |
| #5 K3 "whose beliefs" (**resolved** → D73) | former Phase 6 WP-6.7 | closed by removing the shipped K3 tier; principles and stances are authored K2 content |
| #24 hard-delete end-to-end (**resolved** → D74) | Phase 7 WP-7.5 | closed: append-first portable manifest + fail-closed active-store purge/replay; provider backup operation stays outside OSS per D60 |

**D60 routing (not gates):** corpus mix, real budget ceilings, Postgres HA, observability
backends, backup schedules, fleet capacity, and vendor-topology tuning are deployment/operator or
`ultimate-memory-cloud` concerns. The OSS uses fixed scale profiles, configurable limits, typed
telemetry, and provider contracts; none requires owner input to begin Phase 7 or Phase 8.

**Spike gates** (measure-before-lock items; each design's spike section is authoritative —
this maps them to the phase that must run them, at entry or inside):

| Phase | Must run (design § with the authoritative list) |
|---|---|
| 0 | blockizer golden corpus bootstrap (e1 §10.2); golden-set labeling protocol (registries §11.1) |
| 1 | token budget baseline (e1 §10.1); E2 one-vs-two-call + bundle cost (e2_e3 §7); grounding safety (e2_e3 §7.3) |
| 2 | ER threshold curves per type, Czech/D-M recall, un-merge ripple, scale load-test (registries §11); observation adjudicator eval + hub cost (observations §7) |
| 3 | reuse hit-rate under A1–A3, conversion cost floor, connector identity rules, versioning-mode defaults, cross-cycle move gap, zero-support false-withdrawal (lifecycle §11; e1 §10.4) |
| 4 | the D44 P2 spikes (UUID PK, ATTACH throughput, merge-recursion gate, as-of path perf, retention, NULL timestamps — questions #20a); placement quality + P3 cadence (e0 §8); storage-class routing (e0 §8.6) |
| 5 | PostgreSQL-native P1 functional contracts, hub pagination, rerank weights, envelope overhead, hydration batching, `resolve` context ranking, the S58 protocol (retrieval §13) |
| 6 | rule-kind coverage, planner blast-radius bands, writer completeness eval, compile economics, git-history erasure (k_layers §11) |
| 7 | D23 partition/index profiles at ungated volume; provider-neutral batching under injected latency; dispatch semantics (k_layers §11.7) |
| 8 | benchmark landscape survey (the field moves; select at execution time — phase file WP-8.1) |

## 6. Work-package format (used by every phase file)

| Field | Meaning |
|---|---|
| **WP-N.x** | stable id; referenced in commits/PRs |
| **Goal** | one line; the *what*, never the *how* |
| **Reads** | the minimal binding sections an agent loads before starting (design §s, decision numbers) |
| **Depends** | WPs or gates that must be done first |
| **Deliverable** | the artifact (module, migration, worker, doc) |
| **Acceptance** | the design's own contract tests: scenario IDs (S-battery), CI invariants, canaries, spike numbers measured |

Rules for executing agents: read *only* the listed sections plus `concepts.md` §0; if a WP
seems to require deviating from a design, that is a **design change** — stop and raise it
(the design gets amended first, the WP second); every WP lands as a PR referencing its id.
A WP whose deliverable changes **user-facing behavior** (CLI, API/MCP, configuration,
mounts, connectors, deployment, the consumption skill) also updates the public docs site in
the same PR (`website/` — the same-PR rule and target page map live in `website/README.md`;
D66). Docs pages describe what the PR ships, never the unbuilt full scope.

## 7. Maintenance

Update statuses in phase files as work lands; flip gates here when decisions/spikes resolve;
when a phase completes, record its exit-criteria evidence (links to the passing eval runs) at
the top of its file. Re-sequencing is allowed and cheap — this directory is *supposed* to
churn; the designs are not.

## References

Designs: `plan/designs/` (index in `overall_design.md` §9). Decisions: `decisions.md`
(D1–D62). Open items: `questions.md`. Scenario battery: `plan/analysis/retrieval_scenarios.md`.
Worker inventory + execution classes: `plan/analysis/workers.md`, `orchestration_design.md`.
