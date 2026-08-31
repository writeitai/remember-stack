# UGM — implementation handoff

**Date:** 2026-07-20 · **Repo HEAD:** `b741031` (merge of PR #108) on `main` ·
**Working tree:** clean (only Jiri's personal `_claude_prompts/`, `.loopy_loop/`,
`.eval-banana/` files are modified — never touch or commit those).

This document hands the *implementation* role to Codex. Claude stays on as
**reviewer** (see §10). Read it top to bottom once; it is written so you can
pick up the next work package cold.

---

## 1. What this project is

`ugm` (Ultimate General Memory) is a layered, scale-oriented memory system for
AI agents (targets millions of documents). It is **design-first**: the complete
system was designed and reviewed before implementation. The binding design
lives in `plan/` (requirements → designs → analysis → plans) and `decisions.md`
(the D1…Dn decision log). Implementation follows `plan/plans/roadmap.md` — nine
phases, each realized WP by WP.

**Read `README.md` and `CLAUDE.md` first.** They are the contract.

---

## 2. Where we are

| Phase | State |
|---|---|
| 0 Foundations | complete, **except** carried WPs 0.4b / 0.4c / 0.6 (see §9) |
| 1 Walking skeleton | complete |
| 2 Truth machinery | complete |
| 3 Evidence lifecycle | complete |
| 4 Projections (graph + corpus FS) | complete |
| **5 Retrieval complete** | **WP-5.1–5.4 done (PRs #105–#108); 5.5, 5.6, 5.7 remain** |
| 6 Plane K | not started |
| 7 Scale & ops | not started |
| 8 Benchmarks | not started |

Phase-5 detail (all merged, CI-green):

- **WP-5.1** (#105) — remaining primitives: `fuse`, `rerank`, `transcript` (4
  subjects), `delta`, `pages_about`, enumerated `aggregate`, streaming `scan`.
  Pure rank math in `core/ranking.py`; the rest on `surfaces/query_engine.py`.
- **WP-5.2** (#106) — recipe registry: `model/recipes.py`,
  `core/recipe_linter.py` (grain bar), `spine/recipes.py` (`RecipeRegistry` +
  9 canonical recipes + `seed_canonical_recipes`), `surfaces/recipe_executor.py`.
- **WP-5.3** (#107) — the complete envelope: S23 contradiction co-members, D54
  `support` marker, S47 `parts`/composite discipline, S61 identity regime,
  `believed_at` horizons, `KFreshness`. Contract suite `test_envelope_contract.py`.
- **WP-5.4** (#108) — surfaces: `surfaces/recipe_surface.py` (shared render +
  dispatch), `surfaces/mcp.py`, API `/recipes` + `/recipe/{name}` + auth
  perimeter, `ugm query` CLI (HTTP client), `test_surfaces_parity.py`, plus
  `website/…/reference/{api,mcp}` + CLI docs.

**Next up: WP-5.5** (consumption skill + S58 eval). Detail in §8.

---

## 3. The three non-negotiable rules (from `CLAUDE.md`)

1. **Design docs are read cold** — by a future agent or a non-specialist human.
   *Explain, don't name.* State what a technique is, the problem it solves, why
   chosen, with a concrete example. Define jargon on first use.
2. **Design the FULL scope, never an MVP.** No "Phase 1 / v1 / for now / later /
   defer / MVP" framing in design or decision docs. Build-sequencing belongs in
   `plan/plans/`. Distinguish *simplification* (remove machinery unnecessary at
   any scale — keep it) from *deferral* (keep a piece tagged "build later" — MVP
   thinking, out). A genuine scope boundary is stated as a *non-goal*.
3. **The library boundary is binding (D60/D61).** This repo is the complete
   single-deployment memory system and *only* that. Never assume a web UI or a
   multi-tenant control plane exists (that's the separate cloud product). Agent
   surfaces (API/CLI/MCP/mounts) are the complete consumption story. Never place
   a correctness-determining mechanism outside this repo.

Plus **D66 (docs ship with code):** any PR that changes user-facing behavior
(CLI/API/MCP/config/mounts/connectors/deployment/skill) updates the affected
`website/src/app/docs/**/page.mdx` *in the same PR*, and keeps
`/docs/project-status` truthful. `page.mdx` documents what ships on `main`, not
aspirations. Add new pages to `website/src/lib/docs/navigation.ts`.

---

## 4. The working process (how one WP gets done)

1. **Branch** off `main`: `impl/wp-X.Y-<slug>`.
2. **Read the design** the WP cites (the "Reads" column in the plan table) plus
   `decisions.md` entries it references. For anything touching the graph engine
   (LadybugDB) or the vector index (LanceDB), **read the engine rulebooks in the
   repo first** — LLM knowledge of these is unreliable (see §7).
3. **Implement** full-scope, matching surrounding style. Keep SQL out of
   `workers`/`model`; only `profiles` compose adapters (§6).
4. **Gate locally** (all must be clean):
   - `uv run ruff check src` and `uv run ruff format src`
   - `uv run pyright src` — **this is the authoritative type check** (your
     editor's pyright may show false "unresolved import" noise; ignore it, trust
     `uv run pyright`).
   - `uv run lint-imports` — the 5 import contracts.
   - `uv run pytest src` against the running Postgres (see §5).
   - If `website/**` changed: `cd website && npm run typecheck && npm run build`.
5. **Codex/Claude review** the diff with scope-gating (fix genuine correctness
   bugs; don't gold-plate; overrule findings that violate the full-scope or
   explain-don't-name rules). Add a **regression test per fixed finding**.
6. **Update `plan/plans/phase-N-*.md`** — flip the WP's Status cell to
   `done (PR #NN; one-line what)` *in the WP's own PR*.
7. **Same-PR docs** per D66 if user-facing.
8. **Commit** (see message conventions below), push, open PR, **merge only on
   green CI** (both the `CI` workflow and, if website touched, the `Docs Site`
   workflow). One WP per PR. Merge commits (not squash) — matches history.
9. Standing authorization: **merging PRs on green CI is fine** ("keep moving
   forward"); **never commit to `main` directly**; never `git add` Jiri's
   personal files.

**Commit trailer** (every commit) and **PR body trailer** — copy exactly from
`CLAUDE.md`'s Git section (Co-Authored-By + Claude-Session for commits; the
"Generated with Claude Code" block for PR bodies). If Codex authors, adjust the
Co-Authored-By line to Codex accordingly, but keep the session/PR trailers.

---

## 5. Environment & tooling

- **Python** via `uv` (always `uv run <tool>`). Package: `ultimate_memory`,
  source under `src/ultimate_memory/`, tests under `src/tests/`.
- **Postgres** for tests: a docker container `ugm-simplify-pg` on **port 55433**.
  Tests read `UGM_DATABASE_URL`; export before running:
  ```
  export UGM_DATABASE_URL="postgresql+psycopg://postgres:ugm@localhost:55433/ugm_check"
  ```
  DB-backed tests `pytest.skip` if it is unset. The suite applies alembic
  `downgrade base` → `upgrade head` per module, so it is self-seeding.
- **Migrations:** alembic, `alembic.ini` at root, versions under
  `src/ultimate_memory/spine/migrations/versions/`. Naming `pN_MM_NNNN_slug.py`.
- **Settings:** pydantic-settings only — **`os.environ` is banned** (ruff
  TID251). Add a `BaseSettings` class with `env_prefix="UGM_"` (see
  `spine/settings.py`).
- **Website:** `website/` is Next.js + MDX, static-exported to GitHub Pages.
  `npm ci` once, then `npm run build`. The **Docs Site CI runs on PRs** that
  touch `website/**`, so a broken MDX/nav fails the PR — build locally first.
- **Full suite** currently: **625 tests green**, ~3.5 min.

---

## 6. Architecture map & import contracts

Layers (dependencies point downward):

```
model      pure pydantic values, no deps on other layers
core       pure domain logic (imports model only) — ranking, linters, blockizer, chunker
ports      Protocol seams (auth, object_store, model_provider, p1_index, …)
spine      Postgres authority: catalogs, registries, resolver, supersession, projection, recipes
llm        prompt/orchestration helpers
workers    pipeline handlers (E0/E1/E2/E3, P2 graph, P3 corpus, lifecycle) — MUST NOT import adapters
surfaces   agent-facing: query_engine, graph_queries, http_api, cli, mcp, recipe_surface/executor — MUST NOT import adapters
adapters   concrete providers (selfhost/*, gcp/*, testing/*) — ONLY profiles compose these
profiles   composition roots (currently near-empty)
eval       eval harness
```

**Import-linter (5 contracts, all must stay green):**
1. `model` imports no other ultimate_memory layer.
2. `core` depends only on `model`.
3. `workers` do not import `adapters`.
4. `surfaces` do not import `adapters`.
5. Only `profiles` compose `adapters` (everyone else forbidden, incl. indirect).

Consequence you WILL hit: a surface that needs a composed `QueryEngine` (which
needs adapters) cannot build one itself. The CLI's `ugm query` is therefore an
**HTTP client of the API** — that is deliberate, not a shortcut.

---

## 7. Gotchas that bit us (hard-won)

- **Engine rulebooks first.** LadybugDB (Kuzu fork) and LanceDB behave
  differently from what an LLM assumes. The P2 spike battery
  (`plan/analysis/p2_spike_battery.md`) pins six live engine constraints as
  canaries (ATTACH dead on enum/pg_partman; Parquet transport; NULL params can't
  bind in typed comparisons; plain variable-length match explodes so `SHORTEST`
  is load-bearing; `COPY` is positional; no list-comprehensions over path
  elements) plus a 30-hop recursion cap and Louvain-is-native (D72). There is
  also a recorded **intermittent `INT128` overflow** on `SHORTEST` under memory
  pressure — the graph reader retries once on a fresh connection, then degrades
  to a typed `boundary` (`surfaces/graph_queries.py`). Do not "fix" it by
  swapping constructs.
- **Seeding facts hits FK/CHECK constraints.** `entities.type` → `entity_types`
  (only registered types: Person, Organization, Project, Concept, … from the
  core manifest / Work pack). `relations.predicate` → `predicates` (works_for,
  works_on, part_of, knows_about, …). `resolution_decisions.method` has a CHECK
  forbidding `T1`/`T2` (use `T3`, `T4_small`, `T4_frontier`, `human`, `T0`).
  `knowledge_page_rules` needs a `plan_decision_id` whenever `artifact_id` is set.
  Copy the seeding patterns from `test_envelope_contract.py` /
  `test_retrieval_primitives.py`.
- **`support_withdrawn`** is an *open* `review_queue` row
  (`status IN ('pending','deferred')`, `item_kind='support_withdrawn'`,
  `candidate->>'fact_id'` = the fact). The envelope's `support=withdrawn` reads
  exactly that (see `_OPEN_SUPPORT_FLAGS`). Match the pending+deferred set.
- **Envelope timestamps are UTC-only** (`UTCDateTime`). Coerce/normalize any
  datetime to UTC before it reaches the envelope.
- **`freshness` / `as_of_*` fields are wall-clock per call** — when asserting two
  envelopes are equal, compare `model_dump(exclude={freshness, as_of_valid_at,
  as_of_believed_at})`.
- **FastAPI + ruff:** `Body(default=…)` in a param default trips ruff B008; use
  `Annotated[T, Body(default_factory=…)]`.
- Recipes are **rows, not code**. A recipe adds no capability — it is exactly its
  primitive chain, proved by replay-equivalence. The linter's op vocabulary
  (`core.KNOWN_OPS`) must equal the executor's runnable set
  (`surfaces.EXECUTABLE_OPS`) — a test enforces it.

---

## 8. The immediate next WP — WP-5.5 (consumption skill + S58)

**Plan row (`plan/plans/phase-5-retrieval-complete.md`):**
Consumption skill v1 (per-deployment rendered) + the S58 protocol as a
repeatable eval. **Reads:** retrieval §8; D51. **Depends:** WP-5.3 (done).
**Deliverable:** skill + S58 harness. **Acceptance:** S58 green with a cold
harness.

**What it is (retrieval §8, `plan/designs/retrieval_design.md`):** a shipped,
versioned skill that teaches an agent the one default motion —
**orient on K** (cheap pre-paid synthesis: `brief`, `pages_about`, or reading
the mounted repo) → **verify on the spine** (fact-grain lookups for anything
load-bearing) → **audit on evidence** (hydrate to claims/sources when stakes
demand). It teaches **filesystem-first** (prefer mounts for navigate/read/grep;
reserve API/CLI for what has no filesystem equivalent — search, graph, as-of,
hydration, transcripts, deltas) and the grain discipline (fact vs evidence).

**S58 = the "cold agent" protocol:** an agent handed *only* the skill and the
deployment's surfaces (mounts + API/CLI/MCP), with no prior context, must answer
a battery of questions correctly. Wire it as a **repeatable eval** in the eval
harness (see `eval/`, `EvalSuite`, and how the WP-1.7 skeleton canaries + the
`lifecycle` suite are registered — mirror that pattern). Acceptance is S58 green
with a cold harness.

**Read before starting:** retrieval_design.md §7 (surfaces, done) and §8 (the
skill); `k_layers_design.md` (what K/`brief`/`pages_about` orient on — note
Plane K itself is Phase 6, so the skill must degrade gracefully when no K pages
exist yet — that is a real *current* state, not a deferral); D51.

**Scope watch:** the skill is a real shipped artifact (probably a rendered
markdown/skill file per deployment) + the S58 eval. It is user-facing → D66 docs
(`/docs/mounts` and/or a skill page, and project-status). `pages_about`/`brief`
against an empty K layer return `known_empty` honestly — the skill teaches the
motion regardless.

Then **WP-5.6** (retrieval spikes: Lance scale, hub pagination, rerank weights,
envelope overhead, hydration batching, resolve context — recorded in eval_runs)
and **WP-5.7** (PyPI packaging of the client surface: base = SDK+CLI+MCP, extras
`[server]`/`[connectors-*]`/`[k]`; lineage-aware ingest; dist name decided in
`questions.md` §11a).

---

## 9. Remaining backlog (beyond Phase 5)

- **Carried Phase-0 WPs** (Phase-1-parallel, not exit-blocking):
  - **WP-0.4b** — GCP reference adapters (Cloud Tasks push + dispatch server,
    GCS store, gcsfuse publisher) + the **janitor sweep** (shared, port-agnostic;
    re-announces a killed delivery on both profiles).
  - **WP-0.4c** — compose self-host profile (postgres + minio + api + worker;
    `profiles/selfhost`) — the `docker compose up` quickstart. **This is also
    where the first real `profiles/` composition root gets built** — relevant if
    a later WP needs a composed entry point.
  - **WP-0.6** — golden-set labeling tooling (LLM-propose / human-adjudicate,
    circularity guard).
- **Phase 6** — Plane K (compiled + authored knowledge pages, triggers,
  compilation). Unblocks the K-dependent arms already stubbed honestly
  (`pages_about`, k_page transcripts, recompiled deltas return `known_empty`
  until K exists).
- **Phase 7** — scale & operations (backfill, load tests, budgets, hard-delete
  end-to-end). **Phase 8** — competitive benchmarks.

---

## 10. The review handoff (Claude as reviewer)

Going forward Codex implements; Claude reviews the diff of each WP branch before
merge. When you (Jiri) ask Claude to review, point it at the branch/PR and it
will:

- Review **only the diff vs `origin/main`**, scope-gated: report genuine
  **correctness** bugs (SQL/NULL/enum/binding, off-by-one, silent caps, contract
  violations like S23/D41/D48/D49), ranked by severity.
- Respect the rules: do **not** flag full-scope-not-MVP design as "missing"; do
  not flag "not wired to a surface yet" when that is a later WP; do not flag the
  intentional scope boundaries (CLI-as-HTTP-client, null `believed_at` horizons,
  MCP transport left to profiles).
- Verify the WP's own gates were run (ruff/pyright/import-linter/pytest, website
  build if touched) and that the plan Status cell + D66 docs were updated.

Every review finding that survives should become a regression test in the same
PR.

---

*End of handoff. The last thing Claude did was merge WP-5.4 (#108). Start WP-5.5
from §8.*
