# Adversarial Implementation Review: PR 311 (WP-I.2 Hard Type Cut)

**Reviewer identity:** Antigravity (`agy`)  
**Date:** 2026-08-26  
**PR:** [writeitai/remember-stack#311](https://github.com/writeitai/remember-stack/pull/311)  
**Branch:** `origin/feat/wp-i2-type-cut` vs `origin/main`  
**Commits:**
- `a802ebb3` (`feat(er): D96 hard type cut on ingest, P2/P3, and resolve`)
- `cc485c5d` (`fix(er): replace entities_current before dropping type column`)

**Review target:** Implementation across:
- [`benchmarks/locomo/protocol.py`](file:///Users/jpuc/code/moje/remember-stack/benchmarks/locomo/protocol.py)
- [`src/rememberstack/adapters/postgres_p1.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/adapters/postgres_p1.py)
- [`src/rememberstack/model/envelope.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/envelope.py)
- [`src/rememberstack/model/relations.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/relations.py)
- [`src/rememberstack/model/resolution.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/resolution.py)
- [`src/rememberstack/spine/assured_operations.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/assured_operations.py)
- [`src/rememberstack/spine/catalog_contract.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/catalog_contract.py)
- [`src/rememberstack/spine/entity_registry.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/entity_registry.py)
- [`src/rememberstack/spine/knowledge.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/knowledge.py)
- [`src/rememberstack/spine/migrations/versions/p9_14_0035_drop_entity_type.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p9_14_0035_drop_entity_type.py)
- [`src/rememberstack/spine/projection.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/projection.py)
- [`src/rememberstack/spine/query_space/memory_v1_manifest.json`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/query_space/memory_v1_manifest.json)
- [`src/rememberstack/spine/query_space/source_definitions.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/query_space/source_definitions.py)
- [`src/rememberstack/spine/resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py)
- [`src/rememberstack/surfaces/http_api.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/http_api.py)
- [`src/rememberstack/surfaces/operation_executor.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/operation_executor.py)
- [`src/rememberstack/surfaces/query_engine.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/query_engine.py)
- [`src/rememberstack/surfaces/sdk.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/sdk.py)
- [`src/rememberstack/workers/e3.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py)
- [`src/rememberstack/workers/p2.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p2.py)
- [`src/rememberstack/workers/p3.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/p3.py)
- [`src/tests/benchmarks/test_locomo_runner.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/benchmarks/test_locomo_runner.py)
- [`src/tests/spine/test_entity_eligibility.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/spine/test_entity_eligibility.py)
- [`src/tests/spine/test_migrations.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/spine/test_migrations.py)
- [`src/tests/spine/test_query_space_manifest.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/spine/test_query_space_manifest.py)
- [`src/tests/surfaces/test_open_query_batch_f.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/surfaces/test_open_query_batch_f.py)
- [`src/tests/workers/test_e3_bare_head_noun.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/workers/test_e3_bare_head_noun.py)
- [`src/tests/workers/test_e3_claim_normalize_fanout.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/workers/test_e3_claim_normalize_fanout.py)
- [`src/tests/workers/test_e3_unknown_entity_type_gate.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/workers/test_e3_unknown_entity_type_gate.py)
- [`website/src/app/docs/ingestion/pipeline/page.mdx`](file:///Users/jpuc/code/moje/remember-stack/website/src/app/docs/ingestion/pipeline/page.mdx)

**Output path:** `/var/folders/wt/plp93ggs40586mdsvzzqy4c40000gp/T/grok-goal-6edec73546f9/implementer/REVIEW_agy_wp_i2_type_cut_2026-08-26.md`  
**Verdict:** **Request changes**

---

## Executive Summary & Verdict

PR 311 executes the D96 hard type cut across ingest, resolution, query surfaces, and projection pipelines. Much of the core mechanical work is well-structured:
- `entities.type`, `entities.type_confidence`, `mentions.emitted_type`, and `mentions.type_confidence` are removed from the database schema and models.
- Inbound normalizer legacy types are discarded via `@model_validator(mode="before")` on `EntityRef` and `ResolvedEntity`.
- `memory_v1.entities_current` projects `NULL::text AS entity_type` and `NULL::real AS type_confidence` to avoid breaking downstream query-space dependents.
- P2 rebuild Kùzu DDL and schema bumped to `p2-rebuild-2026.08` without `type`.
- P3 CorpusFS bumped to `p3-corpusfs-2026.08` and Tier 1 canonical paths updated to `entities/{entity_id}`.
- `resolve` primitive drops `entity_type` parameter from HTTP API, SDK, and operation executor.

However, the branch contains **two P0 runtime-crashing defects** and **one P1 migration regression** that must be resolved before merge:
1. **P0 (Runtime Crash / Schema Mismatch in Graph Queries):** `surfaces/graph_queries.py` was not updated. It projects `b.type` in Cypher against Kùzu (where `type` was dropped from `Entity`), and instantiates `GraphNode(..., type=...)` which immediately raises a Pydantic `ValidationError` (`extra_forbidden`) on `GraphNode`.
2. **P0 (PostgreSQL Runtime Crash in Bootstrap, FactCatalog, Normalization, Extension Packs):** Migration `p9_14_0035` drops the `predicate_signatures` table, but `spine/deployment_bootstrap.py`, `spine/fact_catalog.py`, `spine/extension_packs.py`, and `workers/e3.py` still execute SQL against `predicate_signatures`, crashing `DeploymentBootstrapper.bootstrap()` and `NormalizeRelationsHandler` on real PostgreSQL databases.
3. **P1 (Migration Downgrade Integrity Loss):** In `p9_14_0035_drop_entity_type.py`, `downgrade()` recreates `memory_v1.entities_current` without the critical provenance `AND EXISTS (...)` filter, corrupting query-space coordinate binding on downgrade.

**Verdict: Request changes.**

---

## P0 / P1 Blocking Issues

### P0-1: `surfaces/graph_queries.py` runtime crash and Cypher schema failure

- **Severity:** P0 (Runtime Crash)
- **Locations:**
  - [`src/rememberstack/surfaces/graph_queries.py:156`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/graph_queries.py#L156)
  - [`src/rememberstack/surfaces/graph_queries.py:168-175`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/graph_queries.py#L168-L175)
  - [`src/rememberstack/surfaces/graph_queries.py:630-635`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/graph_queries.py#L630-L635)
  - [`src/rememberstack/surfaces/graph_queries.py:663-668`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/surfaces/graph_queries.py#L663-L668)
- **Description:**
  1. `model/envelope.py` removed `type` from `GraphNode`:
     ```python
     class GraphNode(BaseModel):
         model_config = ConfigDict(frozen=True, extra="forbid")
         entity_id: UUID
         name: str
         hops: int = Field(ge=0)
     ```
  2. In `workers/p2.py`, Kùzu DDL for `Entity` table removed the `type` column:
     `"CREATE NODE TABLE Entity(id UUID, name STRING, normalized_name STRING, summary STRING, created_at TIMESTAMP, PRIMARY KEY (id))"`
  3. However, `surfaces/graph_queries.py` was left unedited:
     - Line 156 specifies `projection = "b.id, b.name, b.type, length(r) AS hops"`, which fails when executed on Kùzu because `b.type` does not exist.
     - Lines 168-175, 630-635, and 663-668 instantiate `GraphNode(..., type=...)`:
       ```python
       GraphNode(
           entity_id=cast("UUID", row[0]),
           name=cast("str", row[1]),
           type=cast("str", row[2]),  # <--- CRASH
           hops=cast("int", row[3]),
       )
       ```
       Because `model_config` has `extra="forbid"`, this immediately raises:
       ```
       pydantic_core._pydantic_core.ValidationError: 1 validation error for GraphNode
       type
         Extra inputs are not permitted [type=extra_forbidden, input_value=..., input_type=str]
       ```
- **Remediation:**
  - Update `surfaces/graph_queries.py`:
    - Change line 156 projection to `projection = "b.id, b.name, length(r) AS hops"`.
    - Adjust unpacking indexes so `row[0]` is `id`, `row[1]` is `name`, `row[2]` is `hops`.
    - Remove `type=...` from all `GraphNode` constructors in `_path_from_row` and `_citation_path_from_row`.
    - Alternatively or in addition, add a `@model_validator(mode="before")` on `GraphNode` to discard legacy `type` for backwards compatibility.

---

### P0-2: Dropped `predicate_signatures` table causes runtime crashes in Bootstrap, FactCatalog, Extension Packs, and E3

- **Severity:** P0 (Runtime Crash on PostgreSQL)
- **Locations:**
  - [`src/rememberstack/spine/deployment_bootstrap.py:122-134, 172-183, 216, 264-272, 444-446`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/deployment_bootstrap.py#L122-L134)
  - [`src/rememberstack/spine/fact_catalog.py:335-346, 630-634`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/fact_catalog.py#L335-L346)
  - [`src/rememberstack/spine/extension_packs.py:199-205`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/extension_packs.py#L199-L205)
  - [`src/rememberstack/workers/e3.py:217, 302`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py#L217)
- **Description:**
  Migration `p9_14_0035_drop_entity_type.py` executes:
  ```python
  drop_tables(table_names=("predicate_signatures",))
  ```
  However, multiple active runtime components still attempt to query or insert into `predicate_signatures`:
  1. `DeploymentBootstrapper.bootstrap()` executes `_insert_core_manifest` (line 265) and `_compare_core_manifest` (line 444) on `predicate_signatures`. This fails with:
     ```
     psycopg2.errors.UndefinedTable: relation "predicate_signatures" does not exist
     ```
  2. `FactCatalog.predicate_signatures()` executes `_SELECT_SIGNATURES` (`SELECT predicate, subject_type, object_type FROM predicate_signatures WHERE deployment_id = :deployment_id`), which fails with `UndefinedTable`.
  3. `NormalizeRelationsHandler.handle_claim_grain()` (line 217) and `_normalize_batch()` (line 302) in `workers/e3.py` invoke `self._facts.predicate_signatures(deployment_id=deployment_id)`. Even though `_normalize_claim` deletes the argument with `del signatures`, calling the method on `_facts` crashes before normalization can proceed.
  4. `extension_packs.py` executes `INSERT INTO predicate_signatures` on pack installation.
- **Remediation:**
  - Update `deployment_bootstrap.py`: remove `predicate_signatures` inserts, selects, and counts (or mark them unused per WP-I.2 spec: *"deployment_bootstrap.py / core_manifest.py type seed unused"*).
  - Update `fact_catalog.py`: make `predicate_signatures()` return `{}` or remove it; remove query against `predicate_signatures`.
  - Update `workers/e3.py`: remove `signatures = self._facts.predicate_signatures(...)` calls from `handle_claim_grain` and `_normalize_batch`.
  - Update `extension_packs.py`: remove insertion into `predicate_signatures`.

---

### P1-1: Migration `p9_14_0035` `downgrade()` omits provenance `EXISTS` check on `memory_v1.entities_current`

- **Severity:** P1 (Contract Violation on Migration Rollback)
- **Location:**
  - [`src/rememberstack/spine/migrations/versions/p9_14_0035_drop_entity_type.py:153-170`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/migrations/versions/p9_14_0035_drop_entity_type.py#L153-L170)
- **Description:**
  In `upgrade()`, `MEMORY_V1_TYPE_CUT_DDL` includes the necessary coordinate-binding filter:
  ```sql
  WHERE e.status = 'active'
    AND EXISTS (
      SELECT 1
      FROM (
        SELECT m.deployment_id, s.survivor_entity_id AS entity_id
        FROM mentions AS m
        ...
        UNION ALL
        SELECT d.deployment_id, s.survivor_entity_id
        FROM documents AS d
        ...
      ) AS provenance
      WHERE provenance.deployment_id = e.deployment_id
        AND provenance.entity_id = e.entity_id
    );
  ```
  In `downgrade()`, lines 153-170 redefine `memory_v1.entities_current` with only `WHERE e.status = 'active'`, omitting the `AND EXISTS (...)` provenance subquery entirely.
  If the migration is downgraded, `entities_current` returns entities lacking active provenance (such as soft-deleted documents or superseded mentions), violating the `memory_v1.entities_current` coordinate binding contract.
- **Remediation:**
  - Copy the exact provenance `AND EXISTS (...)` block from `MEMORY_V1_TYPE_CUT_DDL` into the `downgrade()` `CREATE OR REPLACE VIEW memory_v1.entities_current` statement (restoring `e.type` and `e.type_confidence` selection in the SELECT list).

---

## P2 / P3 Non-Blocking & Code Quality Observations

### P2-1: Dead Code and Skipped Test File in `workers/e3.py` & `test_e3_unknown_entity_type_gate.py`
- **Location:** [`src/rememberstack/workers/e3.py:535-600`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py#L535-L600) and [`src/tests/workers/test_e3_unknown_entity_type_gate.py:30`](file:///Users/jpuc/code/moje/remember-stack/src/tests/workers/test_e3_unknown_entity_type_gate.py#L30)
- **Details:**
  - Functions `_illegal_types_in_response`, `_illegal_types_in_relation`, `_signature_allows`, `_bounded_type_label`, and `_is_entity_type_fk_violation` are no longer called in production paths.
  - Notice that `_illegal_types_in_response` attempts to read `relation.subject.type`, which will raise `AttributeError` because `EntityRef` no longer has a `type` attribute.
  - `NormalizeRelationsHandler._normalize_claim` still takes `signatures`, `type_parents`, and `allowed_types` as arguments, only to execute `del signatures, type_parents, allowed_types`.
  - `test_e3_unknown_entity_type_gate.py` is skipped via `pytestmark = pytest.mark.skip(...)`. Per the plan, these should either be rewritten to assert type-discard behavior or removed.

### P2-2: `ports/p1_index.py` Protocol Signature Retains `entity_type`
- **Location:** [`src/rememberstack/ports/p1_index.py:270`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/ports/p1_index.py#L270)
- **Details:**
  `EntityIndexPort.search_entities_scored` retains `entity_type: str | None = None`. While `PostgresP1Index` documents that `entity_type` is ignored (D96), the port method parameter should be removed or deprecated to prevent confusion.

### P2-3: Fixture Error Handling in `test_open_query_batch_f.py`
- **Location:** [`src/tests/surfaces/test_open_query_batch_f.py:70`](file:///Users/jpuc/code/moje/remember-stack/src/tests/surfaces/test_open_query_batch_f.py#L70)
- **Details:**
  The `migrated()` fixture calls `load_database_settings().sqlalchemy_url()` without `try / except ValidationError: pytest.skip(...)`. When running tests in environments where `REMEMBERSTACK_DATABASE_URL` is unset, 19 tests in this file error during fixture setup rather than skipping gracefully.

---

## Acceptance Criteria Verification (WP-I.2 Plan)

| Acceptance Criterion (WP-I.2) | Status | Evidence & Notes |
|---|---|---|
| Drop `entities.type` (NOT NULL, FK, column) | **Met** | Removed in migration `p9_14_0035` and all ORM/SQL statements in resolver/registry. |
| Stop writing `mentions.emitted_type` | **Met** | Removed in migration `p9_14_0035` and insert statements in resolver/registry. |
| Drop `predicate_signatures` table | **Partial (P0)** | Dropped in migration `0035`, but callers in bootstrap/facts/e3/extension_packs were not updated, causing runtime crashes. |
| Discard legacy `type` on `EntityRef` & `ResolvedEntity` | **Met** | `_discard_legacy_type` and `_discard_legacy_entity_type` validators cleanly drop inbound types without `extra="forbid"` errors. |
| Bump `E3_NORMALIZER_VERSION` | **Met** | Bumped to `e3-normalize-2026.08c:temp0-1:claim-fanout-1:bare-noun-1:no-types-1`. |
| P2 Rebuild DDL / Parquet / Schema | **Partial (P0)** | `workers/p2.py` DDL and schema updated to `p2-rebuild-2026.08`, but `surfaces/graph_queries.py` query and `GraphNode` construction were missed. |
| P3 Tier-1 path `entities/<type>/<id>` → `entities/<entity_id>` | **Met** | Updated in `workers/p3.py`, version bumped to `p3-corpusfs-2026.08`. |
| P1 entity search SQL untyped | **Met** | `adapters/postgres_p1.py` query ignores `entity_type` filter. |
| `memory_v1.entities_current` NULL type projection | **Met in upgrade, Broken in downgrade (P1)** | `upgrade()` projects `NULL::text` / `NULL::real` with provenance check; `downgrade()` lost provenance check. |
| `resolve` primitive drops `entity_type` | **Met** | Dropped from HTTP API, SDK, `operation_executor.py`, and `query_engine.py`. |
| `typed_absence` aggregate form | **Met** | `_AGG_TYPED_ABSENCE` updated to query without entity type filter. |
| `works_for(Alice, Me)` between people persists | **Met** | Verified by `test_works_for_between_people_is_not_dropped` in `test_e3_bare_head_noun.py`. |
| Migration linear chain & lifecycle | **Met** | `p9_14_0035` correctly chained after `p9_13_0034` in `test_migrations.py`. |

---

## Action Items for PR Implementer

1. **Fix `surfaces/graph_queries.py`:**
   - Update Cypher projection in `neighborhood()`: remove `b.type`.
   - Remove `type=...` argument from `GraphNode(...)` calls on lines 171, 633, and 666.
2. **Clean up `predicate_signatures` consumers:**
   - Remove table queries and insertions from `spine/deployment_bootstrap.py`, `spine/fact_catalog.py`, `spine/extension_packs.py`, and `workers/e3.py`.
3. **Fix `p9_14_0035_drop_entity_type.py` `downgrade()`:**
   - Restore the `AND EXISTS (SELECT 1 FROM (...) AS provenance ...)` block to `memory_v1.entities_current` in the downgrade function.
4. **Clean up `workers/e3.py` dead code:**
   - Remove unused parameters (`signatures`, `type_parents`, `allowed_types`) from `_normalize_claim`.
   - Remove dead helper functions `_illegal_types_in_response`, `_illegal_types_in_relation`, `_signature_allows`, `_bounded_type_label`, and `_is_entity_type_fk_violation`.
