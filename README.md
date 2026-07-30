# RememberStack

[![CI](https://github.com/writeitai/remember-stack/actions/workflows/ci.yml/badge.svg)](https://github.com/writeitai/remember-stack/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/writeitai/remember-stack/python-coverage-comment-action-data/endpoint.json)](https://github.com/writeitai/remember-stack/tree/python-coverage-comment-action-data)

A memory system for AI agents, designed to ingest **millions** of heterogeneous documents and
distill them into progressively more abstract, navigable knowledge — while keeping everything
auditable by humans. Scale is a requirement, not an aspiration: it is meant to still be useful
at a million documents.

> **Pre-release software.** Phases 0–7 are implemented and tested. The public
> [`v0.2.0`](https://github.com/writeitai/remember-stack/releases/tag/v0.2.0) release is available
> from PyPI and GHCR; release automation, trusted publishing, tag protection, and bounded
> contributor-agreement enforcement are in place. The fresh-deployment Docker Compose profile
> is documented under
> [Self-host deployment](website/src/app/docs/deployment/page.mdx); it composes PostgreSQL,
> MinIO, the API, all ten continuous E/P1 routes, and explicit P2/P3 publication for one
> deployment. It remains pre-release software, not a production rollout. The build follows
> [plan/plans/roadmap.md](plan/plans/roadmap.md).

## TL;DR

Imagine pouring a million documents into a system and being able to ask it not just "where did
I read this?" but "what do we actually know, and what changed our mind?" That's the goal.

The design is organized as **three planes** — a useful mental model for the whole system:

| Plane | Plain-English meaning | What it holds | Can we rebuild it? |
|---|---|---|---|
| **E — Evidence** | *what we ingested* | Raw inputs broken down step by step: files → chunks → atomic claims → relations (facts) | No — it's the ground truth |
| **K — Knowledge** | *what we concluded* | LLM-distilled and human-editable summaries and beliefs, version-controlled like code | No — authored/curated |
| **P — Projections** | *how we reach it* | Search indexes, a knowledge graph, and a browsable filesystem, derived from the evidence spine | Yes — regenerate any time |

The one-line version: **E** is what we ingested, **K** is what we concluded, **P** is how we
reach it — and **P can always be rebuilt from E.**

Each plane breaks into a handful of layers:

**E — Evidence** *(per-document chain; Postgres is truth)*

| | What it is | Backed by | Holds |
|---|---|---|---|
| **E0** | Files / document layer | GCS (raw + artifacts) + Postgres | original bytes, markdown, per-doc section structure (PageIndex) |
| **E1** | Chunks | Postgres + Lance | retrieval-sized units with context prefixes |
| **E2** | Claims | Postgres | atomic, verifiable natural-language assertions (immutable) |
| **E3** | Relations + Observations | Postgres | **relations**: normalized `(subject, predicate, object)` entity↔entity facts (graph-projected); **observations**: untyped, entity-anchored non-graph facts (a value/statement about one entity) — both bi-temporal (D43) |

**K — Knowledge** *(LLM-compiled markdown; git is truth)*

| | What it is | Backed by | Holds |
|---|---|---|---|
| **K1** | General knowledge | git repo | progressive-disclosure summaries over the claims |
| **K2** | Special-purpose scopes | git repo | pluggable domains (people profiles, projects, a personal operating doctrine, …), mixing compiled support with authored principles |

Plane K is a **framework**, not fixed layers (D45–D47, refined by D73): one compile machine — an LLM
planner owning *structure*, LLM writers owning *content*, a deterministic driver owning
staleness, routing, and commits — over two page kinds: **compiled** (regenerated from the
evidence when it changes) and **authored** (human/agent commitments that are never rewritten,
only *alerted* when the evidence they cite changes). K1 plus any number of K2 scopes is the
shipped configuration; deployments define their own scopes ("knowledge structure is
configuration, not machinery"). Core principles and stances are authored K2 pages because an
evidence threshold cannot decide what a person or organization endorses.

**P — Projections** *(derived from the E spine; rebuildable, hold no source-of-truth; K pages
cross-link with P3 but are never a structural input — D40 refined)*

| | What it is | Backed by | Serves |
|---|---|---|---|
| **P1** | Search indexes | LanceDB | vector (semantic) + FTS/BM25 search over chunks, claims, relation + observation labels |
| **P2** | Graph | LadybugDB | neighborhood / path / as-of traversal over entities + relations |
| **P3** | Corpus filesystem | GCS directory tree | agents browsing the memory as a mounted filesystem (`ls`/`cat`/`grep`) |

A few ideas give the design its character:

- **Nothing is silently overwritten.** New information *supersedes* old information by closing a
  validity window rather than erasing it; contradictions are surfaced, not quietly resolved.
- **Two notions of time, everywhere.** When a fact was true in the world, and when the system
  learned it — so you can ask "what did we believe as of last March?"
- **Built for agents, auditable by humans.** Every conclusion traces back to the exact claims
  and source documents that support (or contradict) it.
- **Clear sources of truth.** The evidence spine lives in Postgres (original files in
  cloud storage), the distilled knowledge in a git repo, and the search and graph layers are
  derived, rebuildable projections on top.

## Open source and the cloud

This repository is the complete memory system, not a teaser for a hosted product (D60):

- **Everything that makes the memory trustworthy is here**, under Apache-2.0 — extraction, entity
  resolution, supersession, grounding and provenance, evals, budgets, dead-letter queues, deletion.
  If it affects correctness, it will never be paywalled.
- **The hosted offering runs this same code, unmodified.** What the cloud adds is operation
  (multi-tenant infrastructure, HA, upgrades, backups) and the human layer (web UI, SSO, teams,
  billing). The library's consumers are agents, and its agent surfaces — API, CLI, MCP, mounted
  filesystems — are the complete consumption story.
- **Self-hosting gives you the full engine for one deployment**, on your own infrastructure via the
  provider ports (D61): S3-compatible storage, a Postgres-backed queue, local mounts, your own git
  remote, your own model keys. Support is community-only.

For the full picture, start with [plan/designs/overall_design.md](plan/designs/overall_design.md).

## The `plan/` directory

All project planning lives in `plan/`, organized into four areas — three levels of abstraction
plus the research behind them:

- **`plan/requirements/`** — the highest level of abstraction: *what we want from the system*.
  Mostly bullet points. No technology choices, no architecture — just needs, constraints, and
  goals.
- **`plan/designs/`** — drill-downs into the architecture: *how a part of the system works*.
  Data models, store layouts, pipelines, trade-offs and decision rationale. Each design serves
  one area and traces back to the requirements it satisfies.
- **`plan/plans/`** — *bringing it all together*: concrete, ordered plans for building the
  system. Plans reference the designs (never duplicate them) and sequence the work — phases,
  dependencies, deliverables. Start at [plan/plans/roadmap.md](plan/plans/roadmap.md): the
  phase spine (0 foundations+harness → 1 walking skeleton → … → 8 competitive benchmarks),
  the technology stack, the gate register (which open decisions and spikes block which
  phase), and the work-package format coding agents execute against.
- **`plan/analysis/`** — the working material *behind* the designs: research reports,
  capability surveys (e.g. `ladybug_capabilities.md`), option explorations, worked explainers
  (e.g. `concepts.md`), external-review digests. Analyses are allowed to be messy,
  opinionated, and eventually superseded — they capture *why we believe things*. Designs
  distill analyses into the binding picture and cite them; nothing in `analysis/` is binding
  on its own.

Rule of thumb: requirements say **what**, designs say **how**, plans say **in what order**,
analysis says **why we think so**. A change should land at the highest level it applies to
and flow downward.

Beside the plan lives **`website/`** — the public documentation site
([remember.dev](https://remember.dev)), a self-contained Next.js/MDX static app
that documents the system *as it ships* (D66): user-facing changes update their docs page in
the same PR, and the full-scope design intent stays here in `plan/`.

## Document index

| Doc | Purpose |
|---|---|
| [loopy_loop_runbook.md](loopy_loop_runbook.md) | Operating the autonomous implementation loop (start/monitor/resume/stop) |
| [plan/requirements/requirements_v3.md](plan/requirements/requirements_v3.md) | Requirements (current) |
| [plan/designs/overall_design.md](plan/designs/overall_design.md) | Overall system design — **best place to start** |
| [plan/designs/registries_design.md](plan/designs/registries_design.md) | Entity resolution, ontology, governance, review, eval (D15–D24) |
| [plan/designs/e2_e3_claims_relations_design.md](plan/designs/e2_e3_claims_relations_design.md) | Claim extraction + relation normalization; why there is no value gate (D31–D35, D25) |
| [plan/designs/e0_files_design.md](plan/designs/e0_files_design.md) | E0 document layer + P3 corpus filesystem (D36–D40) |
| [plan/designs/p2_graph_design.md](plan/designs/p2_graph_design.md) | P2 graph layer design (formerly L6) |
| [plan/designs/k_layers_design.md](plan/designs/k_layers_design.md) | K plane: manifest-driven compiled + authored knowledge (D45–D47) |
| [plan/designs/retrieval_design.md](plan/designs/retrieval_design.md) | The query machine: primitives, recipes, envelope, mounts, consumption skill (D48–D51) |
| [plan/analysis/retrieval_scenarios.md](plan/analysis/retrieval_scenarios.md) | Retrieval stress battery S1–S63 — drives the retrieval design + the D22 golden set |
| [plan/analysis/objections.md](plan/analysis/objections.md) | Step-back critique O1–O6 with acceptance status |
| [plan/analysis/retrieval_review/](plan/analysis/retrieval_review/) | External adversarial review of the retrieval design (Codex) + reconciliation |
| [plan/designs/evidence_lifecycle_design.md](plan/designs/evidence_lifecycle_design.md) | Document versions, testimony currency, the counting rule, content-addressed reuse (D54–D56) |
| [plan/analysis/evidence_lifecycle/](plan/analysis/evidence_lifecycle/) | Parallel analyses (internal + Codex) + SYNTHESIS behind D54–D56; stress-test amendments |
| [plan/designs/e1_chunks_design.md](plan/designs/e1_chunks_design.md) | E1: blocks + blockizer, sections on the grid, chunk packing, reuse mechanics (D57–D58) |
| [plan/analysis/design_review_2026_07.md](plan/analysis/design_review_2026_07.md) | Second step-back review F1–F9 (post-D44) — K-plane build system, attributed stance, evidence inflation, … |
| [plan/analysis/entity_registry.md](plan/analysis/entity_registry.md) | Entity resolution, ontology (core+extensions), scope views |
| [plan/analysis/registry_research/](plan/analysis/registry_research/) | R1–R10 multi-agent research + SYNTHESIS (→ D17–D24) |
| [plan/analysis/entity_typing_research/](plan/analysis/entity_typing_research/) | Entity typing cascade options + SYNTHESIS (→ registries design) |
| [plan/analysis/value_gate_research/](plan/analysis/value_gate_research/) | O3 value-gate research + SYNTHESIS (gate mechanism rejected — see D25 / objections O3) |
| [plan/analysis/claimify_research/](plan/analysis/claimify_research/) | Claimify E2 research: de-contextualization + claim-level value selection + SYNTHESIS (→ D31–D35) |
| [plan/analysis/concepts.md](plan/analysis/concepts.md) | Explainer: claims vs. relations, evidence, bi-temporality |
| [plan/analysis/ladybug_capabilities.md](plan/analysis/ladybug_capabilities.md) | Verified LadybugDB capability findings |
| [plan/analysis/ladybug_translation_research/SYNTHESIS.md](plan/analysis/ladybug_translation_research/SYNTHESIS.md) | Postgres→LadybugDB translation (the `v_graph_*` projection contract, D44) |
| [plan/analysis/ladybug_query_semantics.md](plan/analysis/ladybug_query_semantics.md) | **LadybugDB query rulebook** — traversal-time vs post-hoc filtering, SHORTEST semantics, all engine quirks; read before writing any graph query |
| [plan/analysis/lance_indexing_maintenance.md](plan/analysis/lance_indexing_maintenance.md) | **LanceDB indexing rulebook** — nothing is automatic: index-set completeness, the unindexed tail, the mandatory optimize loop; read before writing any P1 table or query |
| [plan/analysis/p3_agent_navigation.md](plan/analysis/p3_agent_navigation.md) | P3 agent navigation — materialized tree vs index-only, the `_index.md` contract, facets/views/fan-out, why directory LLM summaries are rejected (→ e0 §6, F6) |
| [plan/plans/roadmap.md](plan/plans/roadmap.md) | Build order: phase spine, stack, gate register, WP format (phases 0–8) |
| [plan/designs/packaging_distribution_design.md](plan/designs/packaging_distribution_design.md) | Delivery artifacts, delivery-only task execution, enforced code architecture (D62) |
| [plan/designs/media_design.md](plan/designs/media_design.md) | Media (images/audio/video): converter routes, source locators, derivation disclosure, media search (D65) |
| [plan/analysis/media_handling/](plan/analysis/media_handling/) | Parallel analyses (internal + Codex) + SYNTHESIS behind D65 |
| [plan/designs/docs_site_design.md](plan/designs/docs_site_design.md) | Public docs site: in-repo Next.js/MDX static module + same-PR truthfulness contract (D66) |
| [decisions.md](decisions.md) | Architecture decision log (D1–D66) with rationale |
| [questions.md](questions.md) | Open questions to resolve before building |
| [RELEASING.md](RELEASING.md) | Maintainer setup, release procedure, and public-artifact verification |
