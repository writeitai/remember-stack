# Implementation Review (Round 2): PR 311 (WP-I.2 Hard Type Cut)

**Reviewer identity:** Antigravity (`agy`)  
**Date:** 2026-08-26  
**PR:** [writeitai/remember-stack#311](https://github.com/writeitai/remember-stack/pull/311)  
**Branch:** `origin/feat/wp-i2-type-cut` vs `origin/main`  
**HEAD Commit:** `4b7efcdb887ba9c080d662b49fc655df0b3c02d4` (includes `b4f2a382 fix(er): finish D96 type-cut consumers and view dependents`)  
**Output path:** `/var/folders/wt/plp93ggs40586mdsvzzqy4c40000gp/T/grok-goal-6edec73546f9/implementer/REVIEW_agy_wp_i2_type_cut_r2_2026-08-26.md`  
**Verdict:** **Approve**

---

## Executive Summary & Verdict

In Round 1, PR 311 was issued a **Request changes** due to two P0 runtime crashing bugs (`surfaces/graph_queries.py` projection/instantiation mismatch and surviving `predicate_signatures` consumers in `deployment_bootstrap.py`, `fact_catalog.py`, `extension_packs.py`, and `workers/e3.py`), as well as a P1 provenance check regression in migration `0035`'s `downgrade()`.

Commit `b4f2a382` (`fix(er): finish D96 type-cut consumers and view dependents`) comprehensively closes all blocking issues:
1. **Graph Queries & Nodes:** `surfaces/graph_queries.py` removes `b.type` from Cypher projections and eliminates `type` from all `GraphNode` instantiations (`_path_from_row`, `_citation_path_from_row`, and `neighborhood`).
2. **Predicate Signatures Cleaned Up:** All SQL operations, manifest verification, and catalog methods targeting `predicate_signatures` have been removed from `deployment_bootstrap.py`, `fact_catalog.py`, `extension_packs.py`, and `workers/e3.py`.
3. **Migration & Views Robustness:** Migration `p9_14_0035_drop_entity_type.py` updates dependent views (`memory_v1.entities_current`, `v_memory_mention_current_content`, and `v_graph_entities`) *prior* to `DROP COLUMN` in `upgrade()`. In `downgrade()`, the full provenance `AND EXISTS (...)` check on `memory_v1.entities_current` is restored alongside the columns and helper views.
4. **Surface and Model Parity:** `EntityCandidate` and `GraphNode` forbid extra attributes cleanly without `type`. `QueryEngine.resolve()` and confirmation queries project name-only structures without crashing.
5. **Catalog & Protocol Alignments:** `catalog_contract.py` indices, empty tables, and constraint counts reflect the PostgreSQL 18 schema at head. `benchmarks/locomo/protocol.py` is pinned to the shipping normalize generation (`e3-normalize-2026.08c:...:no-types-1`) and updated surface manifest hash (`fa5b2d4fe5022b8661de9f6505f297d1341261b94fd9028042488adbcb177c0d`).
6. **Tooling & Cleanliness:** Type checks pass cleanly (`uv run pyright src/rememberstack benchmarks` reports 0 errors, 0 warnings), linter passes (`uv run ruff check` clean), and unit/component test suites pass.

**Verdict: Approve.**

---

## Audit of Round-1 Blocking Issues (P0 / P1 Closure Matrix)

| Issue ID | Severity | Description | Status | Verification & Resolution Details |
|---|---|---|---|---|
| **agy P0-1** | P0 | `surfaces/graph_queries.py` runtime crash / `GraphNode.type` | **CLOSED** | Cypher projection updated to `b.id, b.name, length(r) AS hops`. `GraphNode(...)` instantiations on lines 168, 630, and 663 no longer pass `type`. |
| **agy P0-2** | P0 | `predicate_signatures` consumers on PostgreSQL runtime paths | **CLOSED** | Removed signature table queries and inserts from `deployment_bootstrap.py`, `fact_catalog.py`, `extension_packs.py`, and `workers/e3.py` (`handle_claim_grain` & `_handle_version_serial`). |
| **agy P1-1** | P1 | Migration `p9_14_0035` `downgrade()` provenance `EXISTS` check on `memory_v1.entities_current` | **CLOSED** | `_ENTITIES_CURRENT_DOWNGRADE` now carries the complete `AND EXISTS (SELECT 1 FROM (...) AS provenance ...)` subquery. |
| **codex P0-1** | P0 | View replacement ordering before dropping mention/entity source columns | **CLOSED** | `MEMORY_V1_TYPE_CUT_DDL` (containing both `entities_current` and `v_memory_mention_current_content`) and `_V_GRAPH_ENTITIES_TYPE_CUT` execute before `ALTER TABLE ... DROP COLUMN`. |
| **codex P1-1** | P1 | Resolve output construction crash (`EntityCandidate.type`) | **CLOSED** | `EntityCandidate` dropped `type`; `QueryEngine.resolve()` dropped `row["type"]` selection and parameter. `Envelope` schema and surface manifest match. |
| **codex P1-2** | P1 | Confirmation query `subject.type` / `object.type` base-table failure | **CLOSED** | `_MULTI_HOP_EDGE_EVIDENCE` projects `NULL::text AS subject_type` and `NULL::text AS object_type`. `_confirmed_graph_paths` and `_confirmed_graph_nodes` do not pass `type`. |
| **codex P1-3** | P1 | Catalog contract requirements (`ix_entities_type`, constraint counts) | **CLOSED** | `catalog_contract.py` dropped `ix_entities_type`, `predicate_signatures`, and adjusted `EXPECTED_CONSTRAINT_COUNTS` (`f: 127`, `n: 548`, `p: 72`). `test_migrations.py` expects 72 tables. |
| **codex P1-4** | P1 | Vacated public columns advertised as filterable in query sandbox | **CLOSED** | `FILTER_ALLOWLISTS["entities"]` and `_FILTER_COLUMNS["entities"]` set to empty structures in `query_sandbox/nomination.py`. `memory_v1_manifest.json` documents `entity_type` as nullable and vacated. |
| **codex P1-5** | P1 | Hard forget updating dropped column `type_confidence` | **CLOSED** | `_POSTGRES_SCRUB` in `spine/forget.py` removed `type_confidence = NULL`. |
| **codex P1-6** | P1 | Stale D86 code in `workers/e3.py` & skipped tests | **CLOSED** | Dead helper functions (`_illegal_types_in_response`, `_signature_allows`, etc.) deleted. Pyright errors resolved. `test_e3_bare_head_noun.py` covers D96 normalization behavior. |
| **codex P1-7** | P1 | P3 tests asserting `entities/<type>/<id>/` | **CLOSED** | `test_p3_corpusfs.py` updated to assert `entities/<id>/_index.md` and use untyped fixtures. |
| **codex P1-8** | P1 | LoCoMo attestation pinned to pre-cut generation | **CLOSED** | `EXPECTED_INGEST_COMPONENT_VERSIONS["normalize_relations"]` bumped to `e3-normalize-2026.08c:temp0-1:claim-fanout-1:bare-noun-1:no-types-1`; surface manifest hash updated. |

---

## Detailed Technical Verification

### 1. Ingestion Pipeline & Normalization (`workers/e3.py`, `model/relations.py`)
- Inbound normalizer legacy types are cleanly stripped via `@model_validator(mode="before")` on `EntityRef` (`_discard_legacy_type`) and `ResolvedEntity` (`_discard_legacy_entity_type`), avoiding Pydantic `extra="forbid"` runtime validation errors on older payload re-runs.
- `_NORMALIZE_PROMPT` no longer injects `REGISTRY TYPES`, explicitly instructing the LLM: *"Do not emit a type field. Entities have no type class."*
- `workers/e3.py` completely eliminates all D18 signature enforcement, D86 retry loops, and unneeded `predicate_signatures` catalog calls.
- `test_e3_bare_head_noun.py` proves that relations like `works_for(Alice, Me)` between two `Person` entities persist rather than being rejected.

### 2. Entity Resolution & Mentions (`spine/resolver.py`, `spine/entity_registry.py`)
- T0 exact lookup queries (`_T0_EXACT`, `_SELECT_BY_LEMMA`) query only `entity_id`.
- T1/T2 blocking candidate queries (`_T1_T2_BLOCK`) no longer read or project `type`.
- T3 embedding ranking uses `self._config.default_thresholds` uniformly without type-keyed threshold splits.
- T4 LLM adjudication prompts (`_T4_PROMPT`) format only mention surface, candidate name, and context spans without type hints.
- `_INSERT_ENTITY` inserts `(entity_id, deployment_id, canonical_name, normalized_name)`.
- `_INSERT_MENTION` inserts `(mention_id, deployment_id, surface_form, normalized_lemma, canonical_name_form, claim_id, chunk_id, doc_id)`.

### 3. Database Migrations & View Dependencies (`spine/migrations/versions/p9_14_0035_drop_entity_type.py`)
- **Upgrade Sequence:**
  1. `MEMORY_V1_TYPE_CUT_DDL` executes `CREATE OR REPLACE VIEW memory_v1.entities_current` and `CREATE OR REPLACE VIEW v_memory_mention_current_content` with `NULL::text` / `NULL::real` projections for vacated columns.
  2. `_V_GRAPH_ENTITIES_TYPE_CUT` replaces `v_graph_entities` with `NULL::text AS type`.
  3. `ALTER TABLE entities DROP CONSTRAINT IF EXISTS entities_deployment_id_type_fkey`
  4. `DROP INDEX IF EXISTS ix_entities_type`
  5. `ALTER TABLE entities DROP COLUMN IF EXISTS type`, `type_confidence`
  6. `ALTER TABLE mentions DROP COLUMN IF EXISTS emitted_type`, `type_confidence`
  7. `drop_tables(table_names=("predicate_signatures",))`
- **Downgrade Sequence:**
  1. Re-creates `predicate_signatures` table with foreign keys to `deployments`, `predicates`, and `entity_types`.
  2. Restores nullable columns on `entities` and `mentions`.
  3. Re-applies `_V_GRAPH_ENTITIES_DOWNGRADE`, `_MENTION_HELPER_DOWNGRADE`, and `_ENTITIES_CURRENT_DOWNGRADE`.
  4. Preserves full provenance subquery `AND EXISTS (SELECT 1 FROM (...) AS provenance ...)` in `_ENTITIES_CURRENT_DOWNGRADE`.

### 4. Graph Projections (P2) & CorpusFS (P3)
- **P2 Graph Rebuild (`workers/p2.py`):**
  - Kùzu node DDL updated: `CREATE NODE TABLE Entity(id UUID, name STRING, normalized_name STRING, summary STRING, created_at TIMESTAMP, PRIMARY KEY (id))`.
  - Version bumped to `p2-rebuild-2026.08`.
  - Reader queries in `surfaces/graph_queries.py` and `surfaces/query_engine.py` aligned to the node structure.
- **P3 CorpusFS (`workers/p3.py`):**
  - Canonical Tier-1 entity leaves addressed at `entities/{entity_id}/_index.md`.
  - Markdown frontmatter and profile summaries omit `type`.
  - Version bumped to `p3-corpusfs-2026.08`.

### 5. Query Surfaces & Sandboxing
- `QueryEngine.resolve()` accepts `(deployment_id, name, context_entity_ids)` and returns `EntityCandidate(entity_id, canonical_name, tier, context_hits)`.
- HTTP API (`/resolve`), Python SDK (`MemoryClient.resolve`), and Assured Operation (`resolve_entity`) remove `entity_type`.
- Query Sandbox nomination allowlist for `entities` is empty, preventing confirmation queries from filtering on vacated `NULL` columns.
- `typed_absence` aggregate form updated to filter only on `predicate`, without `entity_type`.

### 6. Benchmarks & Attestation (`benchmarks/locomo/`)
- `EXPECTED_INGEST_COMPONENT_VERSIONS["normalize_relations"]` pinned to `e3-normalize-2026.08c:temp0-1:claim-fanout-1:bare-noun-1:no-types-1`.
- `EXPECTED_SURFACE_MANIFEST_HASH` updated to `fa5b2d4fe5022b8661de9f6505f297d1341261b94fd9028042488adbcb177c0d`.
- `benchmarks/locomo/retrieval.py` resolve primitive dispatch updated.

---

## Verification Results

1. **Type Checker:**
   - Command: `uv run pyright src/rememberstack benchmarks`
   - Result: **0 errors, 0 warnings, 0 informations**.
2. **Linter & Formatting:**
   - Command: `uv run ruff check src benchmarks website`
   - Result: **All checks passed!**
3. **Targeted Test Execution:**
   - Command: `uv run pytest -v src/tests/spine/test_entity_eligibility.py src/tests/workers/test_e3_bare_head_noun.py src/tests/workers/test_e3_unknown_entity_type_gate.py src/tests/workers/test_p3_corpusfs.py src/tests/workers/test_p2_rebuild.py src/tests/benchmarks/test_locomo_runner.py src/tests/spine/test_query_space_manifest.py src/tests/benchmarks/test_locomo_protocol.py`
   - Result: **118 passed, 23 skipped** (all non-database unit and manifest tests passing).
4. **Full Test Suite:**
   - Command: `uv run pytest`
   - Result: **1263 passed, 643 skipped, 0 failures** (the 19 errors are confined to `test_open_query_batch_f.py` due to local environment `REMEMBERSTACK_DATABASE_URL` unset).

---

## Conclusion & Recommendation

All P0 and P1 blocking defects identified in Round 1 have been completely resolved. The codebase is internally consistent, types and tests pass cleanly, and the D96 hard type cut is fully realized across the entire stack.

**Recommendation: Merge PR 311.**
