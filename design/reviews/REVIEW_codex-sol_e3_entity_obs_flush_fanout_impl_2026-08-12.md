# Implementation review: D90 entity-grain observation flush fan-out

**AGENT:** `codex-sol`
**Date:** 2026-08-12
**PR:** #265
**Branch:** `feat/d90-entity-obs-flush-fanout` @ `ab16d241`
**Binding design:** `plan/designs/e3_entity_obs_flush_fanout_design.md`

## Verdict

**REQUEST_CHANGES**

## Summary

The version-qualified identity is implemented in the right shape: migration
`p9_10_0031` gives each `(deployment, version, normalizer, subject entity)` a
distinct `unit_id`, and processing rows target that id rather than the bare
canonical entity (`src/rememberstack/spine/migrations/versions/p9_10_0031_entity_obs_flush_fanout.py:22-37`,
`src/rememberstack/spine/work_ledger.py:986-1034`). That closes the D12
cross-version identity collision from the design review. The barrier's
succeeded-count query also pins the fan-out component version, so an ordinary
missing processing row or DLQ does not count as succeeded
(`src/rememberstack/spine/work_ledger.py:1686-1710`). The entity handler does
not call the version-wide staging clear; its current clear is scoped to one
version and entity.

Those good pieces are not enough to merge. The first real fan-out query is
invalid on the supported PostgreSQL version, the handler discards the loaded
entity-global merge and applies only its claimed version, and the required D43
late-arrival re-split is absent. Readiness, connector-cycle lifecycle, and hard
forget were not extended to the new durable membership. The zero-chunk path is
also internally inconsistent with the active component version and already
fails an existing test. Finally, generation exclusivity is only one-way and
the completion path can manufacture `barrier_complete` without the required
materialized version-state marker.

## Blockers

### B1 — The fan-out selector calls `min(uuid)`, which PostgreSQL 16 does not provide

`_SELECT_STAGING_ENTITIES_FOR_FANOUT` computes `min(s.doc_id)` even though
`normalize_observation_staging.doc_id` is `uuid`
(`src/rememberstack/spine/work_ledger.py:1599-1611`). PostgreSQL resolves the
aggregate before returning rows, so this fails for both non-empty and empty
staging; `_enqueue_entity_obs_flush_fanout` never reaches either unit
materialization or `empty_complete`.

PostgreSQL 16's built-in `min` types do not include `uuid`; UUID merely has the
usual comparison operators. See the official
[PostgreSQL 16 aggregate-function table](https://www.postgresql.org/docs/16/functions-aggregate.html)
and [UUID functions/operators](https://www.postgresql.org/docs/16/functions-uuid.html).

The fix should not stringify and minimize the claim-origin doc id. Load the
target version's authoritative `doc_id` from the barrier/version coordinate and
group only the entity plus `min(asserted_at)`. This matters under D56 reuse:
staging carries the origin claim's `doc_id`, which need not be the document that
owns the target version. Add a real PostgreSQL fan-out test; source inspection
cannot detect function-resolution errors.

### B2 — The entity handler does not perform the binding entity-global merge

The handler correctly loads all eligible staged rows for the subject entity in
the global key order (`src/rememberstack/workers/e3.py:765-776`,
`src/rememberstack/spine/fact_catalog.py:719-739`). It then abandons that set:
`unit_assertions` filters rows back to the claimed unit's `version_id` and
`normalizer_version`, and only that subset is passed to D43
(`src/rememberstack/workers/e3.py:778-810`). The earlier `assertions` tuple is
used only as a truthiness check.

The entity transaction lock prevents simultaneous writes, but it does not make
lease acquisition order equal source order. Two units for the same entity can
therefore apply version A then version B even when their assertions interleave
as `A@t1, B@t2, A@t3`. This is the exact per-unit ordering strategy rejected by
D90 §§5.5/5.5.1.

Drain all eligible rows in `(asserted_at NULLS LAST, claim_id, statement)` order
under one entity lock. Because the rows can belong to several version slices,
the apply API must delete each applied staging row (or all drained slices) in
the same durable write; the current single-version `clear_staging` dictionary
cannot implement the global drain safely. Only the claimed processing row
should be completed after that stream.

### B3 — The required D43 late-arrival re-split is absent

This PR does not change `observation_adjudication.py`. Evidence collapse still
attaches a later same-statement claim to the open observation
(`src/rememberstack/spine/observation_adjudication.py:197-226`), and the forward
supersede path only caps that observation and inserts the incoming successor
(`src/rememberstack/spine/observation_adjudication.py:498-540`). It never walks
post-boundary evidence or re-materializes a later reassertion.

Consequently, after unit A has completed `{t1:A, t3:A}`, later unit B `{t2:B}`
still yields `A[t1,t2), B[t2,inf)` and loses the required `A[t3,inf)` slice.
Entity-global merge fixes co-present units but cannot fix this staggered case.
D90 §5.5.3 makes re-splitting a binding co-requisite, not an optional safety
net. Implement it in D43 and add the exact staggered acceptance test before
shipping.

### B4 — Readiness, connector-cycle lifecycle, and forget do not know about D90

No D90 change exists in `readiness.py`, `lifecycle.py`, or `forget.py`.

- Public readiness loads version-target processing rows and has derived
  aggregates only for D84 chunks and D88 claims
  (`src/rememberstack/spine/readiness.py:81-156`,
  `src/rememberstack/spine/readiness.py:228-237`). A non-empty D90 version has
  no version-target observation row, so the active `OBS_FLUSH_VERSION` remains
  `missing` forever. `obs_flush_version_state.empty_complete` is also ignored.
- Connector-cycle finalization waits on version, chunk, and claim rows only
  (`src/rememberstack/spine/lifecycle.py:1024-1070`). It can therefore open a
  false lifecycle barrier while an entity unit is pending/running/failed or
  dead-lettered.
- Hard forget deletes staging but neither deletes/scrubs
  `obs_flush_entity_units` nor `obs_flush_version_state`, and its verification
  query never checks them (`src/rememberstack/spine/forget.py:1249-1253`,
  `src/rememberstack/spine/forget.py:1491-1560`). Membership retains version,
  doc, entity, and content-hash coordinates after forget. A retained unit can
  also reconstruct from membership after its ledger payload is nulled, succeed
  as an empty no-op, and enqueue downstream work for the forgotten version.

Implement the design's membership-derived status with honest terminal times,
make cycle finalization wait on every unit including DLQ, and scrub/verify units,
state, and their exact processing rows by forgotten version. A shared canonical
entity in another version must remain runnable.

### B5 — The zero-chunk/empty-extract path is broken against the active generation

Both direct empty paths were changed to enqueue a document-version row at
`OBS_FLUSH_LEGACY_VERSION`
(`src/rememberstack/workers/e1.py:629-641`,
`src/rememberstack/workers/e2.py:1063-1081`). The composed readiness contract,
however, expects the new `OBS_FLUSH_VERSION`
(`src/rememberstack/profiles/selfhost.py:886-900`). The legacy row therefore
cannot satisfy active observation-flush readiness and it bypasses the D90
durable empty signal.

This is already red in the repository suite:
`test_extract_follow_up_zero_chunks_enqueues_obs_flush` expects the active
component version (`src/tests/workers/test_chunk_level_extract.py:121-148`).
Route true-empty versions through an atomic `empty_complete` + supersession +
`embed_claim` operation, or bind a coherent, explicitly temporary legacy
readiness precedence. The current half-switch is neither.

### B6 — Legacy/fan-out exclusivity and mixed-image safety are not enforced

Fan-out checks for an already non-terminal legacy row
(`src/rememberstack/spine/work_ledger.py:886-903`,
`src/rememberstack/spine/work_ledger.py:1673-1683`), but the reverse guard is
missing: the legacy handler never refuses a version whose D90 state or units
already exist (`src/rememberstack/workers/e3.py:738-747`,
`src/rememberstack/workers/e3.py:830-923`). Replaying a legacy DLQ after fan-out,
or racing a legacy enqueue with materialization, can therefore run both paths
for the same version.

There is also no capability gate for rolling deployment. Claims are selected by
deployment/stage/lane, without target-kind or component-version filtering
(`src/rememberstack/spine/work_ledger.py:1274-1287`), so an old stage-only
worker can claim a new entity-unit row. Bind a stop/drain/restart or capability
transition, and make the legacy handler fail closed once membership/state exists.

### B7 — Completion does not require or load the authoritative version state

`complete_entity_obs_flush` trusts all coordinates supplied by the handler,
marks the given processing row succeeded, counts units, and then upserts
`barrier_complete` (`src/rememberstack/spine/work_ledger.py:442-477`). The
readiness helper checks only unit counts; it does not require a materialized
`obs_flush_version_state` row (`src/rememberstack/spine/work_ledger.py:1039-1067`).
Thus a missing version-state marker plus a complete-looking unit set is treated
as ready, and completion recreates the missing marker directly as
`barrier_complete`. That defeats the durable fan-out marker whose purpose is to
distinguish a complete expected set from orphaned rows.

Load the unit and version state inside the completion transaction using the
processing row's target, validate deployment/component/version membership, and
require the state to be `materialized` before the anti-join can open. Enqueue
coordinates and `content_hash` must come from that stored state/membership, not
from the caller's copy.

## Nits

- `load_unapplied_obs_staging_for_entity` joins every observation-processing
  generation for a unit and filters only `p.status <> 'dead_letter'`; it does
  not pin `p.component_version` (`src/rememberstack/spine/fact_catalog.py:730-738`).
  A second-generation row can duplicate staging or make a fan-out DLQ appear
  eligible through a different non-DLQ row. Pin the literal D90 component.
- The entity handler passes `work.content_hash` into the completion barrier
  even though the loaded membership contains the authoritative stored value
  (`src/rememberstack/workers/e3.py:811-826`). Use the membership/state value.
- The three D90 indexes created by `p9_10_0031` are absent from
  `EXPECTED_INDEXES`, so schema verification proves the tables and constraint
  counts but not the required index definitions
  (`src/rememberstack/spine/catalog_contract.py:172-246`).
- The unused global `assertions` tuple and the adjacent comments claiming a
  global apply obscure the actual version-local behavior
  (`src/rememberstack/workers/e3.py:765-797`). Remove them once the real drain is
  implemented.

## Test gaps

The new D90 test file has five source-introspection/model-field checks and no
behavioral test (`src/tests/workers/test_e3_entity_obs_flush_fanout.py:13-58`).
It would pass even though the fan-out SQL cannot execute and the handler applies
the wrong row set. The minimum D90 matrix from the design is largely absent:

- real PostgreSQL fan-out for zero, one, and three entities, including the
  migration upgrade/downgrade and exact index definitions;
- two versions sharing one canonical entity with distinct unit ids;
- co-present global order, tied/undated assertions, and same-claim multiple
  statements;
- the staggered `{t1:A,t3:A}` then `{t2:B}` late-arrival re-split;
- 2/3 succeeded, 3/3 succeeded exactly once, missing processing row, missing
  version state, failed row, and DLQ barrier behavior;
- a real two-connection same-entity single-flight test and last-unit barrier
  race;
- no version-wide clear, partial retry, and evidence idempotency;
- zero-chunk and zero-claim empty completion through readiness;
- legacy non-terminal block, legacy replay refusal after fan-out, and the
  mixed-image negative path;
- readiness, connector-cycle DLQ waiting, and shared-entity hard forget.

Verification performed on this checkout:

```text
uv run pytest -q src/tests/workers
1 failed, 119 passed, 90 skipped
  failed: test_extract_follow_up_zero_chunks_enqueues_obs_flush

uv run pytest -q src/tests/spine/test_migrations.py
1 passed, 5 skipped
  PostgreSQL lifecycle cases skipped: REMEMBERSTACK_DATABASE_URL unavailable

uv run ruff check <changed Python files>
1 error: I001 at src/rememberstack/workers/e1.py:631

uv run pyright <changed implementation files>
14 errors, all in the new entity path at src/rememberstack/workers/e3.py:768-822

git diff --check main...HEAD
clean
```

The behavioral blockers above remain independently merge-blocking after the
Ruff, Pyright, and existing-test failures are repaired.
