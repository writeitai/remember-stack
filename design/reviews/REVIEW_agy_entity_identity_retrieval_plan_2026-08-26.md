# Adversarial Implementation Plan Review: Entity Identity and Retrieval (D95–D97)

**Reviewer identity:** Antigravity (`agy`)  
**Date:** 2026-08-26  
**Files reviewed:**
- [`plan/plans/entity_identity_and_retrieval.md`](file:///Users/jpuc/code/moje/remember-stack/plan/plans/entity_identity_and_retrieval.md) (Primary WP Table)
- [`plan/designs/entity_identity_and_retrieval_design.md`](file:///Users/jpuc/code/moje/remember-stack/plan/designs/entity_identity_and_retrieval_design.md) (Binding Design)
- [`plan/analysis/entity_identity_and_retrieval_analysis.md`](file:///Users/jpuc/code/moje/remember-stack/plan/analysis/entity_identity_and_retrieval_analysis.md) (Problem & As-Built Analysis)
- [`decisions.md`](file:///Users/jpuc/code/moje/remember-stack/decisions.md) (D17, D18, D21, D86, D87, D95, D96, D97)
- [`src/rememberstack/spine/resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py)
- [`src/rememberstack/workers/e3.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py)
- [`src/rememberstack/model/relations.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/relations.py)
- [`src/rememberstack/model/resolution.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/resolution.py)
- [`src/rememberstack/core/core_manifest.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/core/core_manifest.py)
- [`src/rememberstack/surfaces/graph_queries.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/graph_queries.py)
- [`src/rememberstack/workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py)
- [`src/rememberstack/spine/projection.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/projection.py)
- [`src/rememberstack/eval/resolution.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/eval/resolution.py)
- [`src/rememberstack/spine/deployment_bootstrap.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/deployment_bootstrap.py)
- [`src/rememberstack/spine/forget.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/forget.py)
- [`src/rememberstack/surfaces/query_engine.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/query_engine.py)
- [`src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py)
- [`src/rememberstack/spine/migrations/versions/p0_02_0004_claims_facts_evidence.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p0_02_0004_claims_facts_evidence.py)
- [`src/rememberstack/spine/migrations/versions/p9_01_0022_memory_v1_query_space.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p9_01_0022_memory_v1_query_space.py)
- [`src/tests/spine/test_resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/spine/test_resolver.py)

---

## Verdict: Request changes

### Executive Summary

The architectural direction established by **D95–D97** (identity as real-world referent, total removal of entity type classes, and predicate-free default neighborhood retrieval) is sound, necessary, and accepted as frozen. However, the **implementation plan** ([`plan/plans/entity_identity_and_retrieval.md`](file:///Users/jpuc/code/moje/remember-stack/plan/plans/entity_identity_and_retrieval.md)) contains critical sequencing deadlocks and unaddressed as-built couplings that will halt an executing agent:

1. **P0 Schema Deadlock between WP-I.2 and WP-I.6:** WP-I.2 strips `type` from [`EntityRef`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/relations.py#L13-L20) and drops type from [`CascadeResolver._mint`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L431-L472), but the database migration making `entities.type` nullable is deferred to WP-I.6. Any code implementing WP-I.2 will immediately fail at runtime with a PostgreSQL `NotNullViolation` on `entities.type`.
2. **P0 T0 Chicken-and-Egg Guard Trap:** WP-I.1 populates [`generic_identifier_guard`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L79-L87) based on multi-entity counts, but without a pre-configured common-name list on cold start, the very first mint of a common name ("John") will permanently auto-merge all future Johns at T0, preventing a second entity from ever being minted.
3. **P0 Eval Harness Deadlock in WP-I.5:** [`CascadeResolver.judge_pair`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L183-L247) hardcodes `lemma_a == lemma_b -> True, "T0"`, and [`run_resolution_suite`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/eval/resolution.py#L118-L200) groups precision/recall curves by `entity_type`, asserting per-type floors. Once types are removed, the eval suite will crash or fail to execute.
4. **P1 Missing Profile Worker Architecture:** WP-I.4 tasks the system with refreshing `profile_summary` from observations, but no profile worker or queue handler exists in the codebase today (`refresh_profile` is an orphaned enum value in [`PipelineStage`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/queue.py#L16-L45)).
5. **P1 Unaccounted Query Space & Graph Breakages:** Dropping entity types breaks LadybugDB DDL in [`workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py#L103-L125), `_EXPORT_SQL["Entity"]` in [`spine/projection.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/projection.py#L449-L462), the `memory_v1.entities_current` view in migration `0022`, [`GraphNode`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/envelope.py#L373-L382), and the `typed_absence` primitive in [`surfaces/query_engine.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/query_engine.py#L4304-L4331).

The plan must be re-sequenced and expanded with explicit contracts before execution begins.

---

## Findings (P0 / P1 / P2)

### P0 Findings (Execution Blockers)

#### Finding P0.1: WP-I.2 vs WP-I.6 Schema Migration Deadlock
- **Claim Status:** **Observed** in [`p0_02_0003_entities_evaluation_e0_e1.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L18-L36) line 21 (`type text NOT NULL`) and line 33 (`FOREIGN KEY (deployment_id, type) REFERENCES entity_types`); **Observed** in [`resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L463-L472,L757-L763); **Observed** in [`plan/plans/entity_identity_and_retrieval.md`](file:///Users/jpuc/code/moje/remember-stack/plan/plans/entity_identity_and_retrieval.md#L32,L36).
- **As-Built vs Plan:** The plan places "Name-only `EntityRef`; drop type from mint; no type FK on insert" in **WP-I.2**, but places the Alembic schema migration ("Drop `entities.type` / signatures") in **WP-I.6**, which is sequenced *after* WP-I.2, WP-I.3, WP-I.4, and WP-I.5.
- **Impact:** In WP-I.2, removing `reference.type` from [`EntityRef`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/relations.py#L13-L20) causes [`CascadeResolver._mint`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L463-L472) to execute `_INSERT_ENTITY` without a type parameter. PostgreSQL will immediately reject the insert with `psycopg.errors.NotNullViolation: null value in column "type" of relation "entities" violates not-null constraint`.
- **Required Plan Revision:** Move the Alembic migration that alters `entities.type` (making it `NULL` or dropping the column and foreign key constraint) directly into **WP-I.2** (or a dedicated prerequisite **WP-I.2a**).

#### Finding P0.2: T0 Auto-Accept Chicken-Egg Trap on Cold Start
- **Claim Status:** **Observed** in [`resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L688-L699) (`_T0_EXACT` has `LIMIT 1`); **Observed** in [`p0_02_0003`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L79-L87) ([`generic_identifier_guard`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L79-L87) has no runtime writers); **Inference** on cold start behavior.
- **As-Built vs Plan:** WP-I.1 states "populate `generic_identifier_guard`", and WP-I.3 states "T0 auto-accept only under design §3.1". Design §3.1 requires auto-accept when there is exactly one hit and the lemma is not in `generic_identifier_guard`. On a fresh deployment, `generic_identifier_guard` is empty.
- **Impact:** When a common given name like "John" is first minted for Person A, `distinct_entity_count` is 1. When Person B ("John", different life) is later ingested, T0 checks `aliases`, sees exactly 1 active hit, checks `generic_identifier_guard`, sees nothing, and auto-merges Person B into Person A. Person B is never minted as a distinct entity; `distinct_entity_count` never increments to 2, and the guard table is never populated. T0 becomes permanently poisoned for common names.
- **Required Plan Revision:** WP-I.1 and WP-I.3 must explicitly contract:
  1. A static / configured `common_name_lemmas` blocklist in [`ResolverConfig`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/resolution.py#L47-L62) (seeded with common given names and tokens with length < 3) where T0 *never* auto-accepts, even on 1 hit.
  2. Runtime dynamic upsert to `generic_identifier_guard` whenever an entity is minted or re-clustered with a lemma already belonging to another entity.

#### Finding P0.3: `judge_pair` Lemma Equality Hardcode and Eval Suite Per-Type Floor Deadlock
- **Claim Status:** **Observed** in [`resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L201-L203); **Observed** in [`eval/resolution.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/eval/resolution.py#L137-L177); **Observed** in [`p0_02_0003`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L243) (`golden_pairs.entity_type text NOT NULL`).
- **As-Built vs Plan:** 
  1. [`CascadeResolver.judge_pair`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L201-L203) executes:
     ```python
     if lemma_a == lemma_b:
         return True, "T0"
     ```
     This makes it impossible for `judge_pair` to evaluate homonym non-matches (Father Jan Novák vs Son Jan Novák).
  2. [`run_resolution_suite`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/eval/resolution.py#L137-L177) iterates over golden pairs, keys results by `pair["entity_type"]`, and requires `curve["precision"] >= PRECISION_FLOOR` across every type stratum.
- **Impact:** In WP-I.5, adding same-name non-matches to the golden set will either be ignored by `judge_pair` (marking them as false-positives at T0) or crash `run_resolution_suite` once `entity_type` is removed.
- **Required Plan Revision:** WP-I.5 must explicitly deliver:
  - Removal of `if lemma_a == lemma_b: return True, "T0"` in [`CascadeResolver.judge_pair`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L183-L247), routing equal lemmas through T3/T4 context comparison unless the name is distinctive and context is unopposed.
  - Migration of [`golden_pairs.entity_type`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L243) to nullable/dropped.
  - Refactoring [`run_resolution_suite`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/eval/resolution.py#L118-L200) to compute a single deployment-wide precision/recall curve, satisfying the exit criterion in plan line 41 ("resolver thresholds measured as **one** curve (not per-type)").

---

### P1 Findings (Architectural Gaps & Coupling)

#### Finding P1.1: Missing Profile Refresher Worker Implementation
- **Claim Status:** **Observed** in [`model/queue.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/queue.py#L36) (`REFRESH_PROFILE = "refresh_profile"`); **Observed** search across `src/rememberstack/` yielding zero worker implementations; **Observed** in [`spine/projection.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/projection.py#L453) (`entities.profile_summary` is read but never written).
- **As-Built vs Plan:** WP-I.4 deliverables list "profile refresher + `_T4_PROMPT` + T3 upsert". The plan assumes there is an existing worker to modify. In reality, no worker handles `PipelineStage.REFRESH_PROFILE`.
- **Impact:** An executing agent will discover that the entire worker plumbing (triggering from E3 obs flush, loading salient observations by `evidence_count DESC`, synthesizing prose via micro-LLM, debouncing per entity, writing to `entities.profile_summary`, and updating `entities.embedding`) must be built from scratch.
- **Required Plan Revision:** Expand WP-I.4 scope to include the creation of `ProfileRefresherHandler` in `src/rememberstack/workers/profile.py`, registering it in the worker harness, and enqueuing `PipelineStage.REFRESH_PROFILE` from [`workers/e3.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py).

#### Finding P1.2: P2 LadybugDB Graph DDL, Parquet Export, and Manifest Hash Coupling
- **Claim Status:** **Observed** in [`workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py#L49-L99,L103-L125,L158-L166); **Observed** in [`spine/projection.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/projection.py#L449-L462).
- **As-Built vs Plan:** 
  1. [`workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py#L106-L108) defines `GRAPH_DDL`: `"CREATE NODE TABLE Entity(id UUID, type STRING, name STRING, ...)"`.
  2. [`workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py#L159-L166) defines `_TABLE_COLUMNS["Entity"]`: positional mapping expecting `type` at index 1.
  3. [`spine/projection.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/projection.py#L451) `_EXPORT_SQL["Entity"]` queries `SELECT e.entity_id AS id, e.type AS type, ...`.
  4. [`workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py#L96-L98) verifies `P2_PROJECTION_SCHEMA_SHA256` on snapshot publish.
- **Impact:** Dropping `type` from `entities` without updating P2 graph projection code will cause Parquet export column count mismatches, LadybugDB `COPY` failures, and validation aborts during snapshot publishing.
- **Required Plan Revision:** Ensure WP-I.6 explicitly lists [`workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py) and [`spine/projection.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/projection.py) as deliverables, updating `GRAPH_DDL`, schema contracts, Parquet mappings, and recomputing `P2_PROJECTION_SCHEMA_SHA256`.

#### Finding P1.3: `memory_v1` Open Query Space & `typed_absence` Breakage
- **Claim Status:** **Observed** in [`p9_01_0022_memory_v1_query_space.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p9_01_0022_memory_v1_query_space.py#L695-L722); **Observed** in [`surfaces/query_engine.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/query_engine.py#L4304-L4331); **Observed** in [`spine/query_space/memory_v1_manifest.json`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/query_space/memory_v1_manifest.json#L4090,L8486).
- **As-Built vs Plan:** The open query space view `memory_v1.entities_current` exposes `entity_type` (derived from `e.type`). [`surfaces/query_engine.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/query_engine.py#L4304-L4331) implements `_AGG_TYPED_ABSENCE` using `AND e.type = :entity_type`.
- **Impact:** Dropping `entities.type` invalidates the `memory_v1.entities_current` view and breaks the `typed_absence` aggregate query primitive.
- **Required Plan Revision:** WP-I.6 must include an Alembic migration for `memory_v1.entities_current`, update `memory_v1_manifest.json`, and deprecate or replace `typed_absence` in [`surfaces/query_engine.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/query_engine.py#L4304-L4331) with fact-text absence matching.

#### Finding P1.4: Deployment Bootstrapper and Core Manifest Signature Invariants
- **Claim Status:** **Observed** in [`core/core_manifest.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/core/core_manifest.py#L11-L24,L46-L53,L78-L91,L191-L200); **Observed** in [`spine/deployment_bootstrap.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/deployment_bootstrap.py#L63,L123,L214-L216); **Observed** in [`tests/spine/test_deployment_bootstrap.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/spine/test_deployment_bootstrap.py#L74).
- **As-Built vs Plan:** [`DeploymentBootstrapper.bootstrap_deployment`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/deployment_bootstrap.py#L214-L216) inserts 8 core entity types and 116 predicate signatures, returning `entity_types_count=8, predicate_signatures_count=116`.
- **Impact:** When entity types and predicate signatures are retired, bootstrap logic and numerous downstream tests (`test_deployment_bootstrap.py`, `test_deployment.py`, `test_component_versions.py`) will fail assertions.
- **Required Plan Revision:** WP-I.6 must update [`core_manifest.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/core/core_manifest.py), [`deployment_bootstrap.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/deployment_bootstrap.py), and `BootstrapDeploymentOutcome`.

#### Finding P1.5: `ResolverConfig` Model and Stored Version Invariant
- **Claim Status:** **Observed** in [`model/resolution.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/resolution.py#L47-L62); **Observed** in [`spine/resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L601-L606,L639-L655).
- **As-Built vs Plan:** [`ResolverConfig.thresholds_for`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/resolution.py#L59-L61) takes `entity_type: str`. [`seed_resolver_version`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L585-L616) writes `thresholds_by_type` JSON to PostgreSQL and enforces strict immutability.
- **Impact:** Once entity types are removed, [`ResolverConfig`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/resolution.py#L47-L62) must use a single `TypeThresholds` (or `ResolverThresholds`) curve for the entire deployment.
- **Required Plan Revision:** Update [`model/resolution.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/resolution.py) in WP-I.2/WP-I.3 to collapse `thresholds_by_type` into global cascade thresholds and bump `RESOLVER_VERSION`.

---

### P2 Findings (Cleanups, Versioning & Minor Gaps)

#### Finding P2.1: Normalizer Version Bump and Work Ledger Fanout Replay
- **Claim Status:** **Observed** in [`workers/e3.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py#L56-L58) (`E3_NORMALIZER_VERSION = "e3-normalize-2026.08a:temp0-1:unknown-type-gate-1:claim-fanout-1"`).
- **As-Built vs Plan:** The component version string explicitly encodes `:unknown-type-gate-1:`.
- **Impact:** Vacating D86 changes the normalization behavior and prompt. To respect D12 idempotency and avoid replay confusion, the version string must be bumped (e.g. `e3-normalize-2026.08b:temp0-1:name-only-1:claim-fanout-1`).
- **Required Plan Revision:** Include `E3_NORMALIZER_VERSION` bump in WP-I.2.

#### Finding P2.2: Omission of Hard Forget (D74) from WP Acceptance Tests
- **Claim Status:** **Observed** in [`spine/forget.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/forget.py#L1298,L1339); **Observed** in [`plan/designs/entity_identity_and_retrieval_design.md`](file:///Users/jpuc/code/moje/remember-stack/plan/designs/entity_identity_and_retrieval_design.md#L408-L409); **Gap** in [`plan/plans/entity_identity_and_retrieval.md`](file:///Users/jpuc/code/moje/remember-stack/plan/plans/entity_identity_and_retrieval.md).
- **As-Built vs Plan:** Design §11 explicitly requires: "Forget still purges profile, observations, and guard rows with the entity (D74)." The WP table in `entity_identity_and_retrieval.md` omits this test from all work package acceptance criteria.
- **Required Plan Revision:** Add D74 forget verification to WP-I.4 and the final test battery.

#### Finding P2.3: Public HTTP API & SDK `/resolve` Signature Deprecation
- **Claim Status:** **Observed** in [`surfaces/http_api.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/http_api.py#L230-L235) (`entity_type: str | None = None`); **Observed** in [`surfaces/sdk.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/sdk.py#L432); **Observed** in [`surfaces/operation_executor.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/operation_executor.py#L63).
- **As-Built vs Plan:** Design §9 specifies "drop `type?` from `resolve` primitive".
- **Impact:** While internal callers will drop `type`, public HTTP routes and SDK methods should either remove the parameter or mark it deprecated/ignored without failing callers.
- **Required Plan Revision:** Explicitly include [`http_api.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/http_api.py), [`sdk.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/sdk.py), and [`operation_executor.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/operation_executor.py) in WP-I.7.

---

## Detailed Answers to the 10 Required Questions

### 1. WP Order
**Is WP-I.1 → I.2 → I.3 → I.4/I.5, with I.6 after I.2 and I.7 after I.1, actually safe? What must move? What can parallelize?**

- **Not safe as written.** The plan has a circular dependency:
  - WP-I.2 drops `reference.type` from [`CascadeResolver._mint`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L431-L472), but the database schema migration that makes `entities.type` nullable is scheduled in WP-I.6.
  - WP-I.7 (retrieval) depends on WP-I.1, but WP-I.7 removes `type` from [`GraphNode`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/envelope.py#L373-L382) and the `resolve` primitive while P2 graph snapshot export and `memory_v1.entities_current` still expect `type` until WP-I.6.
- **What must move:**
  - The Alembic schema migration (making `entities.type` and `mentions.emitted_type` nullable, and dropping FK to `entity_types`) **must move into WP-I.2**.
  - P2 graph projection and `memory_v1` view updates must move to WP-I.6, and WP-I.7 must depend on WP-I.6.
- **What can parallelize:**
  - After WP-I.3 lands, **WP-I.4 (Profile Refresher)** and **WP-I.5 (Golden Set / Eval Suite)** can execute in parallel.
  - **WP-I.8 (Docs)** runs concurrently alongside every WP that changes user-visible behavior.

---

### 2. WP-I.2 vs WP-I.6 Split
**Name-only EntityRef and dropping `entities.type` NOT NULL / FK — can they split? What breaks if mint has no type before the migration?**

- **They cannot split in the current sequence.**
- If `EntityRef` loses `type` and `_mint` attempts to insert into `entities` before the migration:
  1. `_INSERT_ENTITY` violates the `NOT NULL` constraint on `entities.type`.
  2. `_SELECT_ENTITY_TYPE_EXISTS` throws `UnregisteredEntityTypeError` if called with `None` or an empty string.
  3. `ResolverConfig.thresholds_for` fails without an entity type string.
- **Fix:** WP-I.2 must execute the Alembic migration making `entities.type` and `mentions.emitted_type` nullable (and dropping the FK constraint) before modifying `EntityRef` and `_mint`.

---

### 3. T0 Second Mint & Schema Constraints
**Lemma advisory lock + unique aliases — will “same lemma, two entity_ids” hit a unique constraint? Spell the schema change if needed.**

- **Analysis of Schema (`p0_02_0003`):**
  - `aliases` table constraint (line 61): `UNIQUE (deployment_id, entity_id, normalized_lemma, provenance)`.
  - `entities` table constraint (line 32): `UNIQUE (deployment_id, entity_id)`.
  - Exact match index (line 71): `CREATE INDEX ix_aliases_lemma_exact ON aliases (deployment_id, normalized_lemma);` (non-unique btree).
- **Result:** Multiple distinct `entity_id`s **can already share the exact same `normalized_lemma`** without violating any database constraint. **No schema change on `aliases` is needed.**
- **Required Code Changes:**
  - In `resolver.py`, `_T0_EXACT` must remove `LIMIT 1` and `ORDER BY first_seen`. It must count/fetch all active matching entities.
  - If multiple entities match, T0 returns none / escalates to T1–T4.
  - If T4 adjudicates `no_match`, `_mint` creates `entity_2` with an identical alias row and writes a row to `resolution_exclusions (entity_id_low, entity_id_high, reason='T4 no-match')`.
  - The advisory lock `pg_advisory_xact_lock(hashtextextended(:key, 0))` on `f"{deployment_id}:lemma:{lemma}"` serializes concurrent transactions resolving the same lemma.

---

### 4. Profile Refresher (WP-I.4)
**Is there an existing worker to extend? How do salient observations get selected (count, recency, evidence_count)? Debounce? First mint has empty profile — does T0 auto-accept distinctive names before any observation exists (SAP good, first John dangerous)?**

- **Worker Status:** There is **no existing worker** to extend. `PipelineStage.REFRESH_PROFILE` is defined in `model/queue.py`, but no handler or worker loop exists. WP-I.4 must implement `ProfileRefresherHandler`.
- **Salient Observation Selection:**
  - Query: `SELECT statement, evidence_count, valid_from, valid_until FROM observations WHERE deployment_id = :d AND subject_entity_id = :entity_id AND invalidated_at IS NULL ORDER BY evidence_count DESC, ingested_at DESC LIMIT 5`.
  - Salient relations: top active relations by `evidence_count DESC`.
- **Debounce:** When E3 flushes observations for an entity in [`workers/e3.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py), an `EnqueueWork` task is emitted for `PipelineStage.REFRESH_PROFILE` on that `entity_id`.
- **Cold-Start / Empty Profile Behavior:**
  - On first mint, an entity has `profile_summary = NULL` and 0 observations.
  - **Distinctive Names (e.g. "SAP"):** Not on the common-name list, not guarded. When mention 2 arrives, T0 sees 1 hit and auto-accepts immediately. No profile needed.
  - **Common Names (e.g. "John"):** Present in the configured common-name list. T0 **refuses** to auto-accept even when count=1 and profile is empty. It escalates to T4, where the judge compares claim contexts, safely avoiding the homonym merge trap.

---

### 5. `judge_pair` & Golden Set (WP-I.5)
**What tests/fixtures exist? What must be added so same-name non-match is visible per tier?**

- **As-Built Fixtures:** [`SYNTHETIC_GOLDEN_PAIRS`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/eval/resolution.py#L30-L93) contains only 6 synthetic pairs (2 Organization, 4 Person). Zero homonym non-match pairs exist.
- **As-Built Code Defect:** [`CascadeResolver.judge_pair`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L201-L203) immediately returns `True, "T0"` if `lemma_a == lemma_b`.
- **Required Additions:**
  1. Rewrite `judge_pair` to pass equal-lemma pairs to T4 when context is provided.
  2. Add the full Design §8 golden suite to [`SYNTHETIC_GOLDEN_PAIRS`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/eval/resolution.py#L30-L93):
     - `("SAP", "SAP")` -> match (T0)
     - `("Jan Novák (father)", "Jan Novák (son)")` -> `no_match` (T4)
     - `("Java (programming language)", "Java (island in Indonesia)")` -> `no_match` (T4)
     - `("Alice (engineer in London)", "Alice (designer in NYC)")` -> `no_match` (T4)
     - `("Acme Corp (2020 in Prague)", "Acme Corp (2024 in Brno)")` -> match (T4 / profile update)
  3. Refactor [`run_resolution_suite`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/eval/resolution.py#L118-L200) to compute global precision and recall rather than grouping by `entity_type`.

---

### 6. Graph / P2 / Retrieve Consumers Dying with Types
**Node property `type`, Cypher, `resolve(type?)`, per-type thresholds — list every consumer that dies when types go away.**

The complete inventory of affected code symbols and files:
1. **P2 Graph Pipeline:**
   - [`workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py#L65): `P2_PROJECTION_SCHEMA["node_types"]["Entity"]["type"]`
   - [`workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py#L106): `GRAPH_DDL` (`CREATE NODE TABLE Entity(id UUID, type STRING, ...)`)
   - [`workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py#L161): `_TABLE_COLUMNS["Entity"]` positional tuple
   - [`workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py#L96): `P2_PROJECTION_SCHEMA_SHA256` validation hash
   - [`spine/projection.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/projection.py#L451): `_EXPORT_SQL["Entity"]` (`SELECT e.type AS type`)
2. **Graph Traversal & Model:**
   - [`surfaces/graph_queries.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/graph_queries.py#L156,L171): Cypher projection `b.type` and `GraphNode(..., type=...)`
   - [`model/envelope.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/envelope.py#L380): `GraphNode.type: str`
3. **Query Engine & Open Query Space:**
   - [`surfaces/query_engine.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/query_engine.py#L251,L3508): `resolve(..., entity_type: str | None = None)` and `_RESOLVE_T0_SQL`
   - [`surfaces/query_engine.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/query_engine.py#L4304-L4331): `_AGG_TYPED_ABSENCE`
   - [`spine/migrations/versions/p9_01_0022_memory_v1_query_space.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p9_01_0022_memory_v1_query_space.py#L698,L712): `memory_v1.entities_current.entity_type`
   - [`spine/query_space/memory_v1_manifest.json`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/query_space/memory_v1_manifest.json): Schema definitions for `entities_current.entity_type`
4. **Public APIs & SDK:**
   - [`surfaces/http_api.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/http_api.py#L232): `/resolve` parameter `entity_type`
   - [`surfaces/sdk.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/sdk.py#L432): `resolve(..., entity_type: str | None = None)`
   - [`surfaces/operation_executor.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/operation_executor.py#L63): Operation parameter mapping
5. **Resolver Models & Manifests:**
   - [`model/resolution.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/resolution.py#L26,L57-L61): `ResolutionCandidate.type`, `ResolverConfig.thresholds_by_type`
   - [`core/core_manifest.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/core/core_manifest.py#L59,L61): `entity_types` and `predicate_signatures`

---

### 7. D86 / Signatures / E3 Prompt Removal vs Replay
**Removal order vs replay of old `component_version` normalize work.**

- **Execution Steps:**
  1. Land Alembic schema migration dropping `NOT NULL` on `entities.type`.
  2. Update `E3` prompt ([`workers/e3.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py#L74-L92)) to request names only (no `REGISTRY TYPES` block).
  3. Remove `_generate_normalize_response` inner retry loop (D86) and `_signature_allows` gate in [`workers/e3.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py#L446-L460).
  4. Bump `E3_NORMALIZER_VERSION` to `e3-normalize-2026.08b:temp0-1:name-only-1:claim-fanout-1`.
- **Replay Safety:** In RememberStack's rebuild architecture (D7), re-derivation reads immutable raw claims and executes the registered normalizer version. Bumping the version string ensures work ledger rows from prior generations do not conflict with or corrupt the new un-typed normalization run.

---

### 8. `generic_identifier_guard` & Common-Name List Mechanics
**Who writes them, starting thresholds, chicken-egg with T0.**

- **Writers:**
  - **Dynamic Writer:** In `CascadeResolver._mint` (and background re-clustering), when an entity is minted whose lemma already belongs to an existing active entity, execute an upsert:
    ```sql
    INSERT INTO generic_identifier_guard (deployment_id, normalized_lemma, distinct_entity_count, is_downweighted, reason, evaluated_at)
    VALUES (:deployment_id, :lemma, :count, true, 'promiscuous_lemma', now())
    ON CONFLICT (deployment_id, normalized_lemma) DO UPDATE
      SET distinct_entity_count = EXCLUDED.distinct_entity_count,
          is_downweighted = true,
          evaluated_at = now();
    ```
  - **Static / Starting Guard:** `ResolverConfig` receives a seed set `common_names: frozenset[str]` (or pre-seeded database rows) for high-frequency Czech/English given names and strings shorter than 3 characters.
- **T0 Gate Contract:**
  `_T0_EXACT` auto-accepts **only if**:
  1. `matching_active_entity_count == 1`
  2. `lemma NOT IN config.common_names`
  3. `lemma` is NOT flagged in `generic_identifier_guard` with `is_downweighted = true`
  4. Candidate has no conflicting profile observations.

---

### 9. Missing Work Packages Inventory
**What WPs or critical tasks are missing from the plan?**

1. **Schema Migration Prerequisite:** A migration making `entities.type` and `mentions.emitted_type` nullable and dropping `predicate_signatures` must be established at WP-I.2, not delayed to WP-I.6.
2. **Profile Worker Implementation:** WP-I.4 must include full handler construction for `PipelineStage.REFRESH_PROFILE`.
3. **P2 Graph Snapshot & Manifest Rebuild:** Rebuilding `GRAPH_DDL`, Parquet mapping, and manifest checksums must be an explicit deliverable.
4. **Open Query Space (`memory_v1`) Migration:** Updating `memory_v1.entities_current` view and `memory_v1_manifest.json`.
5. **Deployment Bootstrap & Manifest Cleanup:** Updating [`deployment_bootstrap.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/deployment_bootstrap.py) and [`core_manifest.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/core/core_manifest.py) to remove the 8 entity types and 116 signatures.
6. **D74 Forget Verification:** Adding explicit tests in `tests/spine/test_forget_catalog.py` proving that forgetting an entity cleanly purges its profile, observations, and guard records without type references.

---

### 10. Acceptance Tests Alignment
**Are design §11 tests enough and placed on the right WP?**

- **Missing Test Placements in the Plan Table:**
  - Design §11 Item 9: *"Forget still purges profile, observations, and guard rows with the entity (D74)"* is completely missing from the WP table deliverables. It must be assigned to **WP-I.4** and **WP-I.6**.
  - Design §11 Item 8: *"Guard: a lemma linking many entities is down-weighted"* was placed on WP-I.1, but down-weighting in resolution cannot be verified until T0 logic is updated in **WP-I.3**.
  - Design §11 Item 7: *"Neighborhood with no predicates returns other:traveled neighbors"* was placed on WP-I.7, but requires untyped P2 graph rebuild from **WP-I.6**.

---

## WP-by-WP Review

### WP-I.1: Refuse bare head nouns, write source aliases, guard setup
- **Plan Goal:** Refuse bare head nouns; write source aliases on mint/match; populate `generic_identifier_guard`.
- **Verdict:** **Keep with amended contract.**
- **Deliverables to Add:**
  - E3 prompt update for bare head noun refusal (`game`, `app`, `the system`).
  - [`CascadeResolver._record`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L516-L568) and `_mint` inserting `alias_text = reference.name` with `provenance = 'source'` when seen.
  - Seeding the static common-name / too-short token list in [`ResolverConfig`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/resolution.py#L47-L62) to prevent the cold-start T0 trap.

---

### WP-I.2: Name-only EntityRef, schema migration, drop type from mint & E3
- **Plan Goal:** Name-only `EntityRef`; stop writing `emitted_type`; remove `_signature_allows` / D86 type path; drop type from mint.
- **Verdict:** **Split & Reorder (Merge Schema Migration).**
- **Deliverables to Add:**
  - **Alembic Migration:** `ALTER TABLE entities ALTER COLUMN type DROP NOT NULL`, drop FK constraint `entities_deployment_id_type_fkey`, drop table `predicate_signatures`.
  - Update [`model/relations.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/relations.py) to remove `type` from `EntityRef` and `ResolvedEntity`.
  - Remove `_signature_allows` and D86 retry loop from [`workers/e3.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py).
  - Bump `E3_NORMALIZER_VERSION`.

---

### WP-I.3: T0 reform, same-lemma second mint, resolution exclusions
- **Plan Goal:** T0 auto-accept only under design §3.1; same lemma may mint a second id; `resolution_exclusions` on T4 no-match.
- **Verdict:** **Keep.**
- **Deliverables to Add:**
  - Refactor `_T0_EXACT` in [`resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L688-L699) to evaluate exact match counts and check against `generic_identifier_guard` + common names.
  - In `_mint`, write negative edge to `resolution_exclusions` when minting after T4 candidate rejection.
  - Dynamically update `generic_identifier_guard` when multiple entities share a lemma.

---

### WP-I.4: Profile refresher, T4 prompt expansion, T3 profile embedding
- **Plan Goal:** Profile refresher from observations; T4 gets blurb + salient facts; T3 embeds name+profile; city/bank facts update profile not id.
- **Verdict:** **Keep (Expand Architecture Scope).**
- **Deliverables to Add:**
  - Implement `ProfileRefresherHandler` for `PipelineStage.REFRESH_PROFILE`.
  - Enqueue profile refresh work from observation flush in [`workers/e3.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py).
  - Update `_T4_PROMPT` in [`resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L50-L58) to include `CANDIDATE PROFILE` and `CANDIDATE FACTS`.
  - Update `_UPDATE_ENTITY_EMBEDDING` to embed `name + profile_summary + salient_facts`.
  - Verify D74 forget cleans profile summaries and vectors.

---

### WP-I.5: `judge_pair` reform, golden set §8 slice, single-curve eval
- **Plan Goal:** `judge_pair` lemma equality is not automatic match; land the §8 golden slice in D22 harness.
- **Verdict:** **Keep (Amend Eval Contract).**
- **Deliverables to Add:**
  - Remove hardcoded lemma match in [`CascadeResolver.judge_pair`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L201-L203).
  - Add Design §8 test pairs to [`SYNTHETIC_GOLDEN_PAIRS`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/eval/resolution.py#L30-L93).
  - Refactor [`eval/resolution.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/eval/resolution.py) to measure a single global curve across the deployment instead of per-type strata.

---

### WP-I.6: P2 Graph rebuild, `memory_v1` query space, bootstrap & manifest cleanup
- **Plan Goal:** Drop `entities.type` / signatures / unused `entity_types` use; `works_for` unconstrained by kinds.
- **Verdict:** **Refocus on Projections, Manifests & Query Space.**
- **Deliverables to Add:**
  - Update [`workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py) (`GRAPH_DDL`, Parquet mapping, `P2_PROJECTION_SCHEMA_SHA256`).
  - Update [`spine/projection.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/projection.py) (`_EXPORT_SQL["Entity"]`).
  - Update `memory_v1.entities_current` view and `memory_v1_manifest.json`.
  - Update [`core_manifest.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/core/core_manifest.py) and [`deployment_bootstrap.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/deployment_bootstrap.py).
  - Remove `typed_absence` from [`surfaces/query_engine.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/query_engine.py).

---

### WP-I.7: Default retrieval recipes, assured context, untyped neighborhood
- **Plan Goal:** Default recipes/assured context: lookup facts + `neighborhood` with empty predicates + fact-text search; do not require a predicate argument.
- **Verdict:** **Keep (Sequence after WP-I.6).**
- **Deliverables to Add:**
  - Update [`surfaces/graph_queries.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/graph_queries.py) to remove `type` from Cypher return and [`GraphNode`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/envelope.py#L373-L382).
  - Update `resolve` primitive in [`surfaces/query_engine.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/query_engine.py), [`http_api.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/http_api.py), and [`sdk.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/sdk.py) to drop `entity_type`.
  - Verify default retrieval: `resolve` -> `lookup` (relations + observations) -> `neighborhood(predicates=())` -> fact-text search.

---

### WP-I.8: Documentation updates
- **Plan Goal:** Same-PR website pages for any user-visible extract/resolve/retrieval change in the WP that ships it.
- **Verdict:** **Keep.**

---

## Suggested Revised Work Package Table

| WP | Goal | Reads | Depends | Deliverable | Acceptance |
|---|---|---|---|---|---|
| **WP-I.1** | Bare head noun refusal in E3; write source aliases on mint/match; configure common names & seed guard | design §4.3–4.5; analysis §3.1 | — | E3 prompt + resolver alias/guard writers + `ResolverConfig.common_names` | `game` not minted; `App`/`Application` can share an id; `ResolverConfig` includes common-name list; D66 docs if extract behavior is user-visible |
| **WP-I.2** | Alembic migration for `entities.type` / `emitted_type`; name-only `EntityRef`; remove D86 retry & signatures; bump `E3_NORMALIZER_VERSION` | design §4–5; D96 | WP-I.1 | Alembic migration + `relations.py` + E3 normalizer + resolver mint | `resolve` takes name only; `_INSERT_ENTITY` succeeds without type; no type FK on insert; unknown predicates map to D5 |
| **WP-I.3** | T0 candidate evaluation (not blind verdict); same-lemma second mint; write `resolution_exclusions` on T4 no-match; dynamic guard updates | design §3.1–3.2; D95 | WP-I.2 | `resolver.py` T0/mint/exclusions | Father/son golden row: two ids; distinctive SAP shorthand: one id; lemma advisory lock serializes races; exclusions row inserted |
| **WP-I.4** | `ProfileRefresherHandler` worker; T4 prompt with profile + salient facts; T3 embeds name+profile; D74 forget verification | design §3.3; D43; D74 | WP-I.3 | `workers/profile.py` + `_T4_PROMPT` + T3 upsert + forget tests | "is a bank" / "lives in Prague" appear in T4; two same-name vectors differ once profiles differ; "list banks" matches profile text; D74 forget purges cleanly |
| **WP-I.5** | `judge_pair` reform (no auto-lemma merge); single global P/R curve; land §8 golden slice in D22 harness | design §3.4, §8; D22 | WP-I.3 | `eval/resolution.py` + `judge_pair` + golden slice fixtures | False-merge vs false-split reported globally (single curve, not per-type); same-name non-match visible in eval report |
| **WP-I.6** | P2 Graph snapshot DDL/Parquet/manifest rebuild; `memory_v1.entities_current` view migration; `core_manifest.py` & bootstrap cleanup | design §5, §9; D96 | WP-I.2 | Alembic (`memory_v1`) + `workers/p2.py` + `core_manifest.py` + `deployment_bootstrap.py` | P2 snapshot builds without `type` property; `memory_v1.entities_current` valid; `works_for(Alice, Me)` persists; bootstrap succeeds |
| **WP-I.7** | Untyped `GraphNode` & Cypher; default retrieval recipes; drop `entity_type` from `resolve` primitive, HTTP API, and SDK | design §7; D97 | WP-I.6, WP-I.1 | `graph_queries.py` + `query_engine.py` + `http_api.py` + `sdk.py` | Hop by id returns `other:*` neighbors; observations load via lookup, not graph nodes; no `entity_type` parameter required |
| **WP-I.8** | Same-PR documentation updates for user-visible extract/resolve/retrieval changes | D66; website IA | with the WP it documents | `website/src/app/docs/**` | Docs describe shipped behavior accurately |

---

## Verification Audit Trail

### Directly Observed in Code
- `_T0_EXACT` in [`resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L688-L699) uses `LIMIT 1` ordered by `first_seen`.
- `judge_pair` in [`resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L201-L203) returns `True, "T0"` immediately when `lemma_a == lemma_b`.
- `_INSERT_ENTITY` in [`resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L757-L763) inserts into column `type`.
- `entities.type` in [`p0_02_0003`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L21) has `NOT NULL` and `FOREIGN KEY REFERENCES entity_types`.
- `aliases` in [`p0_02_0003`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L61) has `UNIQUE (deployment_id, entity_id, normalized_lemma, provenance)` (includes `entity_id`).
- `PipelineStage.REFRESH_PROFILE` exists in [`model/queue.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/queue.py#L36), but no handler or worker implementation exists in `src/rememberstack/`.
- `GRAPH_DDL` in [`workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py#L106) creates `Entity(id UUID, type STRING, ...)`.
- `_EXPORT_SQL["Entity"]` in [`spine/projection.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/projection.py#L451) selects `e.type AS type`.
- `memory_v1.entities_current` in [`p9_01_0022`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p9_01_0022_memory_v1_query_space.py#L712) selects `e.type`.
- `DeploymentBootstrapper` in [`spine/deployment_bootstrap.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/deployment_bootstrap.py#L214-L216) inserts 8 core entity types and 116 predicate signatures.
- `run_resolution_suite` in [`eval/resolution.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/eval/resolution.py#L137-L177) computes per-type precision/recall curves and checks per-type floors.

### Inferred from System Architecture
- Attempting to drop `EntityRef.type` in WP-I.2 without migrating `entities.type` in Postgres will crash all mint operations on `NotNullViolation`.
- In a cold deployment with an empty `generic_identifier_guard`, T0 will auto-merge distinct entities sharing a common name unless a common-name blocklist is checked.
- Bumping `E3_NORMALIZER_VERSION` is required to preserve D12 idempotency across re-derivation runs.
- Rebuilding the P2 graph snapshot without `type` will fail Parquet column validation unless `P2_PROJECTION_SCHEMA_SHA256` is updated in lockstep.

### Gaps Identified in Implementation Plan
- Deadlock between WP-I.2 and WP-I.6 regarding schema migrations.
- Missing `ProfileRefresherHandler` worker definition in WP-I.4.
- Lack of common-name cold start specification in WP-I.1 / WP-I.3.
- Incompatibility of `judge_pair` and `run_resolution_suite` with untyped homonyms in WP-I.5.
- Omission of P2 graph DDL, Parquet export, `memory_v1` view migration, and `typed_absence` retirement from WP deliverables.
- Absence of D74 hard forget verification from acceptance criteria.
