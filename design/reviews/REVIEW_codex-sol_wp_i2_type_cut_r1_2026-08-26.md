# PR 311 review — WP-I.2 hard type cut

**Verdict: Request changes**

## Findings

### [P0] Vacate the mention view before dropping its source columns

`p9_14_0035_drop_entity_type.py:123-124` drops `mentions.emitted_type` and `mentions.type_confidence`, but the migration only replaces `memory_v1.entities_current`. The existing private view `v_memory_mention_current_content` still selects both base columns (`p9_01_0022_memory_v1_query_space.py:636-637`), and `memory_v1.mentions_live` depends on that view. PostgreSQL will therefore abort the upgrade with a dependent-object error at the first mention-column drop.

Replace `v_memory_mention_current_content` first, preserving its output positions as `NULL::text` / `NULL::real` so its dependents survive, then drop the authority columns. The downgrade must restore that definition after restoring the base columns. Replacing `entities_current` before dropping `entities.type` is the right order; the same cutover is missing for mentions.

### [P0] The dropped `predicate_signatures` table still sits on live ingest and deployment paths

The migration drops `predicate_signatures`, but both E3 entry paths query it before normalization (`workers/e3.py:217` and `:302` through `FactCatalog.predicate_signatures()`), while `_normalize_claim()` merely discards the result. Every normalize job will fail with `relation "predicate_signatures" does not exist` before reaching name-only resolution. This also means `test_works_for_between_people_is_not_dropped` does not prove the production behavior: it calls `_normalize_claim()` directly and bypasses the failing handler setup.

Fresh deployment bootstrap and extension-pack installation are broken for the same reason: `deployment_bootstrap.py:122-133,172-183,264-271` and `extension_packs.py:197-205` still insert/select the dropped table. Remove the D18 reads/writes and their manifest/result plumbing before dropping the table, then add a migrated-database test that runs the real handler and persists `works_for(Person, Person)`.

### [P1] A successful name-only resolve crashes while constructing its typed output

`QueryEngine.resolve()` now selects only `entity_id` and `canonical_name`, but `query_engine.py:295-300` still reads `row["type"]`, and `model/envelope.py:222-231` still requires `EntityCandidate.type`. Thus any exact hit raises `KeyError` before an envelope is returned. The checked-in operation result schema likewise still declares `EntityCandidate.type` required.

Remove the output field and regenerate the surface manifest. Also update remaining callers: `benchmarks/locomo/retrieval.py:644-660` still accepts and passes `entity_type` to the SDK, which is one of the current Pyright failures.

### [P1] P2 removes `Entity.type`, but graph readers and PG hydration still require it

The P2 writer correctly removes the node property, but `surfaces/graph_queries.py:156-173` still projects `b.type`, and `_path_from_row()` / the two-entity path builder still pass `type=` to `GraphNode` (`:633` and `:666`). Those queries either fail binding against the new snapshot or fail Pydantic validation because `GraphNode` now forbids the extra field.

The confirmation query has the analogous base-table failure: `query_engine.py:3722-3724` selects `subject.type` and `object.type` after the migration drops those columns. Neighborhood and multi-hop reads therefore need to be cut over together with the P2 schema.

### [P1] The head catalog contract still requires objects the migration deletes

Even after fixing the DDL dependency above, readiness cannot accept the migrated schema. `catalog_contract.py:221` still requires `ix_entities_type`, and `EXPECTED_CONSTRAINT_COUNTS` at `:349-356` was not adjusted for the removed entity FK/NOT NULL constraint and the dropped signature table. The lifecycle test is stale too: `test_migrations.py:618-624` still expects 73 tables and `predicate_signatures` in the empty-table inventory.

Update the expected index/table inventories and recompute the PostgreSQL 18 constraint counts from a real head migration. Otherwise `verify_schema()` rejects the exact schema this PR creates.

### [P1] Vacated public columns are still advertised as real, filterable types

Keeping compatibility columns in `memory_v1.entities_current` as always-NULL is workable, but the published contract must describe that state. It currently does the opposite:

- `query_space/catalog.py:668-697` declares `entity_type` non-null and still names `ix_entities_type` as an index used;
- the generated manifest documents `entity_type` as the canonical voted type and marks it non-null;
- `query_sandbox/nomination.py:53,117-129,351-356,530` still exposes `entity_type` as an entities filter/output, while the P1 nomination side silently ignores the filter. Confirmation then applies `entity_type = ...` to an always-NULL column and drops every result;
- the mention-side public columns are not vacated at all because their private source view is unchanged.

Mark retained compatibility columns nullable and explicitly vacated, remove all filter semantics, and regenerate the manifest. If the intended contract is to remove the public columns rather than retain compatibility positions, drop them coherently from every dependent view and contract instead.

### [P1] Hard forget still updates a dropped column

`spine/forget.py:1333-1345` executes `UPDATE entities ... type_confidence = NULL`. After this migration, every hard-forget request that reaches this statement fails with `column "type_confidence" does not exist`, interrupting the erasure workflow. Remove the assignment and cover hard forget against migrated head.

### [P1] The skipped/stale tests conceal the incomplete cut

`test_e3_unknown_entity_type_gate.py:30-32` skips the entire 12-test module rather than replacing D86 assertions with D96 proofs. Stale D86 helpers remain in production (`workers/e3.py:536-619`) and account for six Pyright errors. Database-backed P3 tests also still insert `entities.type` and assert `entities/<type>/<id>/` (`test_p3_corpusfs.py:67-71,354-357,434-435`), so they will fail under the migrated schema instead of proving the new path.

Rewrite, rather than blanket-skip, the D86 coverage. The P3 implementation itself does emit `entities/<id>/` (`workers/p3.py:211-213`), but its fixtures/assertions must be updated so CI proves that contract.

### [P1] LoCoMo attestation is pinned to the pre-cut ingest generation

`workers/e3.py` advances normalization to `e3-normalize-2026.08c:...:no-types-1`, while `benchmarks/locomo/protocol.py:72-74` still requires the old `2026.08a:...:unknown-type-gate-1` component. `_readiness_matches_protocol()` compares these maps exactly, so a run produced by this branch can never pass benchmark readiness. Update the attested generation along with the resolve dispatcher noted above.

## Checks performed

- `uv run pyright src/rememberstack benchmarks` — **failed with 10 errors**: stale `entity_type` SDK dispatch, three stale `GraphNode(type=...)` calls, and six stale D86 `EntityRef.type` accesses.
- `uv run pytest -q src/tests/spine/test_entity_eligibility.py src/tests/workers/test_e3_bare_head_noun.py src/tests/workers/test_e3_unknown_entity_type_gate.py` — **11 passed, 12 skipped** (the skipped tests are the entire D86 module).
- `uv run pytest -q src/tests/spine/test_query_space_manifest.py src/tests/benchmarks/test_locomo_runner.py` — **74 passed**; these repository-only checks do not exercise the actual migrated PostgreSQL dependencies/contracts.
- The real migration lifecycle test could not run locally because `REMEMBERSTACK_DATABASE_URL` is unset; Pytest skipped it.
- `uv run ruff check src/rememberstack benchmarks` and `git diff --check` passed.

## Requested-risk summary

- Mint SQL in both resolvers is name-only, but production E3 cannot reach it because it still queries `predicate_signatures`.
- `works_for` person-to-person passes the internal helper test, not the migrated production handler.
- `entities_current` is replaced before dropping entity columns; mention-view ordering remains broken.
- D86 tests are blanket-skipped and stale D86 code remains.
- P3 code uses `entities/<id>/`; its integration tests still require the old typed path/schema.
- Resolve input is name-only, but resolve output construction is still typed and crashes.
- Vacated compatibility columns, nullability/docs, and filtering semantics are inconsistent with the dropped authority columns.
