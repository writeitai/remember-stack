# LoCoMo current-surface cutover analysis

**Status:** non-binding implementation analysis, 2026-08-07  
**Question:** how should the full LoCoMo run measure the clean-cutover system now
shipping on `main`?

## Context

Before this cut, the LoCoMo harness offered `RS-LoCoMo-Full-v9` and
`RS-LoCoMo-Full-v9-strong`. Both were intentionally pinned to the retired
20-recipe catalog. D83 removed those adapters before release because there are
no users requiring compatibility. The shipping intent surface is now exactly
`resolve_entity`, `question_context`, and `current_context`, whose public
descriptors and implementation chains are members of the checked-in
`memory_v1` surface manifest.

Running v9 against the current deployment is therefore impossible without
either restoring obsolete tools or weakening a benchmark drift guard. Either
choice would measure a system other than the one on `main`.

The owner has directed that the next paid run measure the current system,
authorized discarding the prior benchmark compatibility path, and confirmed
that the benchmark deployment does not need to preserve previous ingested
state or backups.

## Options considered

### Keep v9 and restore the old recipes

Rejected. This reverses the clean pre-release cut and measures an API that the
product no longer ships.

### Keep both historical and current protocols in executable code

Rejected. Historical artifacts already identify their protocol and repository
revision. Keeping dead catalog construction and branching in the active runner
adds maintenance without helping the requested measurement.

### Rename v9 while silently accepting the live catalog

Rejected. A name change without a precise surface pin can score the wrong
deployment after catalog or implementation drift.

### Replace the executable protocol with one current-surface protocol

Chosen. `RS-LoCoMo-Full-v10` uses Luna for both answer and judge seats, pins the
checked-in `surface_manifest_hash`, and verifies the deployment's discovery
response before ingestion and again before answering. It also compares the
actual public recipe list with the canonical three descriptors. The manifest is
the durable run identity;
the direct descriptor comparison includes hashes computed from the live
registry chains and catches registry/bootstrap drift at the exact surface the
agent consumes.

## Consequences

- The harness has one protocol and no weak/strong or v9 compatibility branch.
- A fresh run starts from an empty live document set and rejects any
  deduplicated ingest. Before every upload it proves that live lineage and
  current-version coordinates equal its durable ingest checkpoints, which also
  makes an interrupted upload safely resumable. Before answering it proves the
  complete prepared sample against those same coordinates. Old benchmark
  databases and backups are not inputs and need not be retained.
- The answer prompt mentions only capabilities the three current operations
  provide.
- The run remains revision-stamped and refuses a deployment built from a
  different commit.
- A v10 result describes the current system but is not numerically comparable
  to v9: the answer model, prompt, and available tool surface differ.
- Dataset bytes, question selection, ingestion rendering, budgets, scoring,
  and the Luna judge remain unchanged.

## Sources inspected

- `decisions.md`, D78 and D83.
- `plan/designs/locomo_benchmark_design.md`, especially §§2, 2.2, and 4.
- `plan/designs/open_query_space_design.md`, §6.
- `plan/analysis/open_query_space_clean_cutover.md`.
- `benchmarks/locomo/{model,protocol,runner}.py`.
- `src/rememberstack/spine/query_space/memory_v1_manifest.json`.
- `src/rememberstack/spine/recipes.py` and
  `src/rememberstack/surfaces/recipe_surface.py`.
