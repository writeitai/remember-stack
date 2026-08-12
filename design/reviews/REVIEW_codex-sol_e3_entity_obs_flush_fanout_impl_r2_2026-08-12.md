# Implementation re-review: D90 entity-grain observation flush fan-out

**Agent:** `codex-sol`
**Date:** 2026-08-12
**PR:** #265
**Branch:** `feat/d90-entity-obs-flush-fanout` @ `8fbfbdd3`
**Binding design:** `plan/designs/e3_entity_obs_flush_fanout_design.md`
**Prior implementation reviews:**
`REVIEW_codex-sol_e3_entity_obs_flush_fanout_impl_2026-08-12.md`,
`REVIEW_claude-opus_e3_entity_obs_flush_fanout_impl_2026-08-12.md`

## Verdict

**REQUEST_CHANGES**

The `APPROVE_WITH_NITS` threshold is not met. The absorb closes the PostgreSQL
UUID aggregate failure and adds useful readiness/forget scaffolding, but the two
binding multi-version apply failures are unchanged: co-present versions are
still applied one version at a time, and D43 still cannot re-split a later
reassertion after a reverse arrival. Those paths silently produce the wrong
observation history while every unit succeeds. Connector cycles can also still
finalize before D90 work, and both direct-empty paths remain permanently
not-ready under the active generation.

## Prior-blocker disposition

| Prior issue | R2 status | Evidence |
| --- | --- | --- |
| PostgreSQL `min(uuid)` in fan-out | **Closed in code** | `_SELECT_STAGING_ENTITIES_FOR_FANOUT` now uses ordered `array_agg(uuid)[1]` (`work_ledger.py:1599-1610`). A real PostgreSQL execution was unavailable locally. |
| Entity-global merge-apply | **Open — blocker** | The global load is still filtered to the claimed `(version_id, normalizer_version)` before apply (`e3.py:768-811`). |
| D43 late-arrival re-split | **Open — blocker** | No implementation change exists in `observation_adjudication.py`; the forward cap still ends at insertion of the incoming successor (`observation_adjudication.py:498-540`). |
| Readiness / lifecycle / forget integration | **Partial** | Readiness and new-table forget deletes were added. Readiness has empty/generation errors; lifecycle is untouched; exact unit processing rows are not selected through membership before membership deletion. |
| Zero-chunk / empty-extract path | **Open — blocker** | The failing assertion was changed to expect the legacy version, but E1/E2 still bypass D90 version state (`e1.py:628-654`, `e2.py:1065-1087`). |
| Legacy/fan-out exclusivity and mixed-image safety | **Open** | Fan-out refuses active legacy work, but the legacy handler has no reverse membership/state guard and claiming remains stage-only (`e3.py:739-748,831-924`; `work_ledger.py:1274-1287`). |
| Authoritative version-state completion | **Open** | Completion still trusts the caller barrier, counts units without requiring `materialized`, and upserts `barrier_complete` (`work_ledger.py:425-518,1039-1067`). |
| Branch test/lint failure | **Partial** | Worker tests now pass, but Ruff and Pyright remain red in the new entity handler. |

## Blockers

### B1 — The entity-global stream is still discarded in favor of per-version apply

`load_unapplied_obs_staging_for_entity` returns all materialized, non-DLQ rows
for the subject entity in the binding total order (`fact_catalog.py:719-739`).
The handler constructs that global `assertions` tuple, uses it only as a
truthiness check, reconstructs `unit_assertions` filtered to the claimed
version, and passes only that slice to D43 (`e3.py:768-811`). The database entity
lock serializes writers, but it cannot make lease order equal source order.

The binding counterexample therefore still fails:

- unit A stages `t1:A, t3:A`;
- unit B for the same entity stages `t2:B`, with `t1 < t2 < t3`;
- A running first collapses `t3:A` as evidence on the open A row;
- B then caps A at `t2` and opens B;
- both units succeed with `A[t1,t2), B[t2,inf)` instead of
  `A[t1,t2), B[t2,t3), A[t3,inf)`.

This is silent cross-version history loss, not a BEAM-only performance residual.
Apply the complete ordered stream while holding the entity lock and retire each
staging row by its own staging key in the same durable write.

### B2 — The binding late-arrival re-split remains absent

Even after B1 is fixed, a unit that materializes after a peer already succeeded
needs D90 §5.5.3. The current exact-statement path only adds evidence to an open
row (`observation_adjudication.py:197-226`), while the forward supersede path caps
the old row and inserts the incoming row (`observation_adjudication.py:498-540`).
It never finds evidence on the capped row whose `asserted_at` is after the cap
boundary and never re-materializes that later slice.

Thus A `{t1:A,t3:A}` completing before B `{t2:B}` produces the same incorrect
two-slice history as B1. Implement the D43 re-split and pin the exact staggered
acceptance case before merge.

### B3 — Empty observation paths and the new readiness aggregate are still wrong

There are three independent failures in the absorbed readiness path.

1. E1's zero-chunk path and E2's no-chunk legacy coordinator enqueue a
   `document_version` row at `OBS_FLUSH_LEGACY_VERSION`
   (`e1.py:628-654`, `e2.py:1065-1087`). The composed readiness contract expects
   `OBS_FLUSH_VERSION` (`selfhost.py:877-900`), and the D90 derived query reads
   only `obs_flush_version_state` plus units. Changing the unit test to expect
   the legacy constant makes the test green but leaves these versions `missing`
   forever.
2. The true D90 empty path does write `empty_complete`, but
   `_ENTITY_OBS_FLUSH_STATUS` reports its `finished_at` as `max(p.finished_at)`
   (`readiness.py:426-464`). An empty version has no unit processing rows, so
   this is NULL. `VersionPipelineReadiness.ready` requires every succeeded stage
   to have a non-NULL finish time (`readiness.py:187-218`), making
   `empty_complete` permanently not-ready despite its durable `completed_at`.
3. The state and membership joins do not pin `normalizer_version`
   (`readiness.py:448-460`). An older normalizer generation's
   `empty_complete`/`barrier_complete` state can therefore satisfy readiness for
   the currently composed normalizer generation.

Route direct-empty versions through the atomic D90 empty operation, use
`obs_flush_version_state.completed_at` for its terminal time, and bind both
state and units to the active normalizer generation.

### B4 — Connector-cycle finalization still ignores entity observation units

`_SELECT_READY_CYCLES` has guards for version work, D84 chunks, and D88 claims,
but none for `obs_flush_entity_units -> processing_state`
(`lifecycle.py:1024-1070`). A cycle can therefore finalize and reconcile while
an observation unit is pending, running, failed, or dead-lettered. This can run
the downstream cascade against an incomplete observation graph. Add the D90
membership-derived wait, including DLQ, as required by §5.8.

### B5 — Generation exclusivity and authoritative completion remain one-way

The fan-out helper refuses a non-terminal legacy row, but the legacy handler
does not refuse a version whose D90 state or membership already exists
(`work_ledger.py:886-903`; `e3.py:831-924`). A replay or mixed-image race can
therefore run legacy and entity paths for the same version. Since work claiming
filters by stage/lane rather than supported target kind/component generation
(`work_ledger.py:1274-1287`), an older stage-only worker can also claim a D90
entity row unless rollout is externally stop/drain/restart.

Separately, `complete_entity_obs_flush` marks the supplied `processing_id`
succeeded and evaluates coordinates supplied by the handler without loading the
processing row, unit, or version state in that transaction. Its readiness helper
does not require a `materialized` state row, and completion can create the
missing row directly as `barrier_complete` (`work_ledger.py:425-477,1039-1067`).
That defeats the durable expected-set marker. Load and validate the claimed
unit plus `materialized` state under the completion transaction, derive barrier
coordinates from them, and make the legacy path fail closed after fan-out.

## Nits and remaining coverage debt

- Forget now deletes `obs_flush_entity_units` and `obs_flush_version_state`, so
  the durable-coordinate retention problem is substantially closed. It deletes
  membership before resolving the corresponding entity-unit processing ids,
  however, and neither the scrub nor residue verifier checks those exact rows
  through membership (`forget.py:1249-1273,1460-1487,1718-1742`). Preserve unit
  ids long enough to scrub/verify their processing rows explicitly.
- The handler still uses `work.content_hash` and the module-level
  `OBS_FLUSH_VERSION` rather than the membership/claimed-row values, and silently
  drops a non-UUID `doc_id` (`e3.py:812-827`).
- `_SELECT_UNAPPLIED_OBS_STAGING_FOR_ENTITY` still does not pin the processing
  row's component version, so another generation can duplicate eligibility
  (`fact_catalog.py:719-739`).
- The three D90 indexes remain absent from `EXPECTED_INDEXES`
  (`catalog_contract.py:172-265`).
- D90 coverage remains five source-introspection tests. There is still no
  executable fan-out, barrier, co-present ordering, staggered ordering,
  readiness, lifecycle, shared-entity forget, retry, or concurrency test.

## Verification on `8fbfbdd3`

```text
uv run pytest -q src/tests/workers
120 passed, 90 skipped

uv run pytest -q src/tests/spine/test_pipeline_readiness.py \
  src/tests/spine/test_migrations.py src/tests/spine/test_forget_catalog.py
1 passed, 18 skipped
  PostgreSQL-backed cases skipped: REMEMBERSTACK_DATABASE_URL unavailable

uv run ruff check <reviewed D90 implementation/test files>
2 errors in src/rememberstack/workers/e3.py:
  I001 unsorted import block
  F401 unused typing.cast

uv run ruff format --check <reviewed D90 implementation/test files>
10 files already formatted

uv run pyright <reviewed D90 implementation files>
14 errors, all object-to-UUID/str typing failures in e3.py:769-823

git diff --check main...HEAD
fails only on trailing whitespace in the previously added Codex review artifact
```

The green non-PostgreSQL worker suite closes the earlier test regression, but it
does not execute any of the hard paths above. The existing source-inspection
tests would remain green if global ordering, empty readiness, the lifecycle
guard, or state authority were still absent—as they are at this HEAD.
