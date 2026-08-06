# Open query space clean cutover analysis

*2026-08-06. Non-binding analysis for the pre-release surface cut. The binding
outcome is recorded in D83 and `plan/designs/open_query_space_design.md`.*

## Question

The accepted open-query design introduced `memory_v1`, nine open-query entry
points, three assured `Envelope` operations, and 17 `examples.*` saved queries,
but Batch F kept all 17 superseded recipe adapters during a measured
deprecation window. The premise behind that window is now false: the library
has no users or integrations requiring compatibility. Should the program still
carry the dual surface, and where should the retained operations get their
invariant logic?

## Sources inspected

- The binding surface and migration contract:
  `plan/designs/open_query_space_design.md` §§1–3, 8–11.
- The current 20-recipe seed catalog:
  `src/rememberstack/spine/recipes.py`, `CANONICAL_RECIPES` and
  `GRAPH_RECIPES`.
- The 17 non-tool replacements:
  `src/rememberstack/surfaces/query_sandbox/examples.py`, `EXAMPLE_QUERIES`.
- Shared recipe rendering and dispatch:
  `src/rememberstack/surfaces/recipe_surface.py`.
- Dual-surface-only counters:
  `src/rememberstack/surfaces/query_sandbox/audit.py`,
  `MigrationUsageCounters`.
- Host composition:
  `src/rememberstack/profiles/selfhost.py`.
- Retained-operation SQL:
  `src/rememberstack/surfaces/query_engine.py`, especially
  `_RESOLVE_T0_SQL`, `_CONFIRM_CURRENT_FACTS`,
  `_CONTRADICTION_MEMBERS_*`, and `_MULTI_HOP_EDGE_EVIDENCE`.
- The schema authorities and live-shape comparator:
  `src/rememberstack/spine/query_space/manifest.py`.
- The shipped bridge/body implementation record:
  `plan/implementation_notes/open_query_space_batch_c.md`.

## Findings

### Compatibility has cost but no beneficiary

The 17 demoted recipes already have complete `examples.*` replacements and are
not needed to preserve any deployed caller. Keeping them public would retain:

- 17 extra registry rows and MCP tools;
- graph-recipe host composition used only by those adapters;
- compatibility-call telemetry and a 180-day removal protocol;
- a benchmark catalog pinned to a product surface that is not intended to
  ship; and
- tests and documentation whose purpose is to keep both dialects alive.

That is not risk reduction when there is nobody to migrate. It is duplicate
product surface and duplicate invariant logic.

### The three retained operations are not compatibility adapters

`resolve_entity`, `question_context`, and `current_context` remain intentional
one-call operations. They package D49 `Envelope` grain separation, typed
negative/boundary information, evidence associations, and bounded defaults
that arbitrary SQL cannot promise. Removing them is a separate product-quality
question. Their registry-backed implementation can remain because three data
rows are a small, versioned descriptor authority; inventing a second operation
registry or renaming every transport would add churn without removing a defect.

### The actual correctness debt is authority duplication

Several retained paths predate `memory_v1` and reconstruct currentness from
raw tables:

- entity resolution walks `aliases` and `entities` itself instead of reading
  `entity_aliases_current` and `entities_current`;
- current-fact confirmation independently filters `relations` and
  `observations` and therefore can drift from `facts_current`;
- contradiction-member enrichment repeats raw fact membership; and
- retained graph enrichment confirms against visible history plus a partial
  current predicate instead of `graph_edges_current`.

These are instances of one error: a consumer guesses the invariants that an
accepted authority already compiles. The clean cut should replace these paths
with `memory_v1`, not build another helper abstraction that repeats the same
predicates.

### Two contract/implementation mismatches should be reconciled directly

The Batch C implementation uses an executor-side Lance bridge because trusted
PostgreSQL cannot call the local Lance store without an unsafe in-database
runtime. The binding design still describes `SECURITY DEFINER` projection
functions as if that bridge lived inside PostgreSQL.

The body store can verify the embedding-text hash and that the location prefix
is separated from source text. Its stored `chunk_content_hash` is a composition
of ordered block hashes and cannot be recomputed from the returned P1 body
alone. Claiming source-content-hash verification today is therefore an
imagined contract. The design should name what is actually reproducible and
retain the source hash as a returned coordinate until the store carries enough
material to verify it.

## Options

### Keep the migration window

Rejected. It protects no consumer, keeps two public semantics alive, and makes
the paid benchmark a release gate for deleting unused code.

### Rename all recipe transports to operation transports

Rejected for now. The public catalog will contain only the three assured
operations, but changing `/recipes`, SDK methods, CLI commands, database
terminology, and internal types at once does not strengthen authorization or
reduce the operation count. It is cosmetic churn. The word `recipe` can remain
an implementation/transport noun without reviving the 17 adapters.

### Clean pre-release cut

Selected:

1. seed and expose only the three assured operations;
2. retain the 17 patterns only as non-tool `examples.*` saved queries;
3. remove dual-surface deprecation, telemetry, and cutover-gate machinery;
4. make the retained live-result paths consume `memory_v1` authorities;
5. enforce checked-in manifest interface-shape equality before live SQL and
   confirmed-Cypher reads, while retaining the exact same-server definition
   comparator as a deploy/CI gate;
6. amend the bridge and body-verification prose to the shipped, enforceable
   contracts; and
7. keep any paid benchmark operator-invoked and treat it as optional quality
   evidence for the shipping surface, never as permission to remove adapters.

## Consequences and risks

- A caller written against one of the 17 recipe names will fail immediately.
  This is accepted because no such caller exists.
- Existing databases that were locally seeded may still contain old active
  rows. Bootstrap reconciles the closed recipe namespace to exactly the three
  assured operations, and registry reads enforce that same allowlist.
- LoCoMo Full-v9 remains an immutable historical protocol. A future benchmark
  over the shipping catalog requires a new protocol identity and catalog hash;
  this change does not run or silently rewrite the paid benchmark.
- The focused correctness risk is retained-operation output drift during the
  move to `memory_v1`. Exact membership, evidence-association, clock, D48, and
  D54 fixtures are the appropriate local gate; broad coverage belongs in CI.
- Dormant primitive code can be deleted where it has no remaining caller, but
  the cut should not rewrite working assured-operation internals merely to make
  the diff aesthetically complete.
