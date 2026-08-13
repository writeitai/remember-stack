# Dual-design review: P1 Lance bulk writes and two-layer maintenance

**Reviewer:** Codex (`gpt-5.6-sol`)
**Date:** 2026-08-13
**Branch:** `feat/d90-entity-obs-flush-fanout` at `4f56a985`
**Scope:** `plan/analysis/p1_lance_maintenance_analysis.md` and
`plan/designs/p1_lance_maintenance_design.md`, checked against the requested P1,
ledger, backfill, queue, compose, and prior-analysis sources

## Verdict

**REQUEST_CHANGES**

## Summary

The analysis identifies the production failure correctly, and the design makes
the right two primary decisions: replace the per-fact `update` loop with a
matched-only batched merge, and separate frequent `optimize()` work from full
IVF retraining. The partial merge shape is safe when implemented exactly as
specified: with the repository-pinned LanceDB 0.34.0, a list-of-dicts source
containing only the three join keys and mutable metadata preserves omitted
`label` and `vector` columns, while omitting
`when_not_matched_insert_all()` prevents null-vector skeleton rows. This matches
the official update guidance, which warns that missing columns become null on
**insert**, not on the partial matched update used here.

The design is not implementation-ready as a worker, however. Its chosen lane
conflicts with D67's current routing vocabulary; its requested
`_expected_components` registration would make every document version report a
missing maintenance stage; an idle ordinary worker has no execution edge on
which to self-seed; the coalescing schema cannot enforce the stated invariant
and can lose requests arriving during a running unit; and neither the five-second
writer budget nor the heartbeat/reaper contract can be implemented around the
current synchronous Lance calls. Heavy maintenance is also permitted to race
light maintenance and continuous writers with retries as the only progress
mechanism. Those gaps undermine the durable two-layer model even though the
storage-level split is sound.

No P0 issue was found. The blocking findings below are P1/P2 production and
contract issues. This review uses repository evidence and public LanceDB
documentation only; the reported BEAM host timings are treated as the authors'
incident observations, not independently verified measurements or private
comparisons.

## Blocking issues

### P1.1 — The proposed route is inconsistent with both lane and readiness contracts

Section 5.5.1 assigns `lane = steady` and allows `backfill`, and §5.5.4 requires
adding the stage to `_expected_components`
(`plan/designs/p1_lance_maintenance_design.md:290-300,405-410`). These are not
neutral wiring details in the current implementation:

- `ProcessingLane` is explicitly the two-value vocabulary for Plane-E work
  (`src/rememberstack/model/queue.py:47-51`). The existing route gate treats
  scheduled P/K work as unlaned and every other stage as laned
  (`src/rememberstack/spine/catalog_contract.py:276-302`). The prior worker map
  classifies P1 compaction as a scheduled P1 job, not a document-processing
  Plane-E stage (`plan/analysis/workers.md:100-103,318-322`). The design neither
  follows that contract nor explicitly amends it.
- `_expected_components()` is consumed as the exact per-document-version
  readiness stage set (`src/rememberstack/profiles/selfhost.py:512-515`,
  `src/rememberstack/spine/readiness.py:188-219`). Its ordinary query loads only
  `target_kind = 'document_version'` rows
  (`src/rememberstack/spine/readiness.py:259-268`). A
  `p1_maintain_unit` row can therefore never satisfy that entry; adding
  `maintain_p1_index` as directed would make every version's readiness false.
- The current self-host worker loop hard-codes `lane=steady` for every supported
  continuous route (`src/rememberstack/profiles/selfhost.py:537-577`). An unlaned
  maintenance decision needs an explicit profile change, not only a new enum and
  handler.

Before D91 is binding, choose and document one route model. If maintenance stays
a scheduled P1 aggregate, make it unlaned, update the worker-loop construction,
and keep its component generation out of per-version `_expected_components`.
If D91 intentionally makes it a laned stage, amend the D67 lane semantics and
define why `backfill` promotion and route budgets are meaningful for maintenance.
In either case, provide a separate deployment-maintenance health/readiness
surface rather than inserting a deployment unit into version readiness.

### P1.2 — Self-seeding and coalescing are not an executable durable protocol

The design requires a worker with no pending work to seed a new light unit
hourly and requires concurrent writer/scheduler enqueues to coalesce
(`plan/designs/p1_lance_maintenance_design.md:347-374,391-403`). Neither behavior
follows from the proposed schema or the current worker lifecycle:

- An ordinary handler is called only after `claim_one()` returns a row. With no
  row, `Worker.run_one()` returns `NO_WORK`
  (`src/rememberstack/workers/base.py:216-263`), and the self-host loop merely
  waits and polls again (`src/rememberstack/adapters/selfhost/queue.py:108-136`).
  A handler therefore cannot implement the stated idle self-seed rule.
- `p1_maintain_units` as specified has no `status`, `open_unit`, or
  `rerun_requested` field and no uniqueness constraint
  (`plan/designs/p1_lance_maintenance_design.md:347-370`). Giving each unit a new
  UUID also makes every ledger identity distinct under the real unique key
  (`src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py:75-95`),
  so ordinary `enqueue_on(... ON CONFLICT DO NOTHING)` cannot coalesce them
  (`src/rememberstack/spine/work_ledger.py:1344-1357`).
- Coalescing into a **running** light unit by only bumping `requested_at` loses an
  edge: the request can arrive after that unit's Lance work has passed the
  relevant table, then the running ledger row succeeds with no required
  successor. The next hourly poll is not the promised post-write threshold
  response.

Bind an atomic state machine, including its database constraint/lock, for one
open unit per `(deployment, mode)` and for an enqueue that races a running unit.
A request observed after the unit's work snapshot needs a durable rerun marker
or successor created atomically with completion. Also put periodic seeding on a
real execution edge—an explicit scheduler/idle hook, or a perpetually scheduled
control row with a supported reschedule transition—and test process death at
each unit/ledger transaction boundary.

### P1.3 — The five-second write-path maintenance bound cannot be enforced

The design permits the adapter to call light optimize on the hot path, but says
work that would exceed `write_path_optimize_max_s = 5` must enqueue rather than
extend the label/embed lease
(`plan/designs/p1_lance_maintenance_design.md:45-48,261-288,504-510`). The current
call is synchronous: `_maintain_indexed_tail()` directly invokes
`table.optimize()` (`src/rememberstack/adapters/selfhost/lance.py:891-924`). The
pinned 0.34.0 `Table.optimize` API has no execution timeout or cancellation
parameter. Measuring elapsed time after the call cannot recover the already
blocked lease.

This is especially material for facts: `LabelFactsHandler` holds `label_lock`
from Phase L through every embed/upsert and the final metadata refresh
(`src/rememberstack/workers/p1.py:195-295`), and every fact upsert or metadata
refresh currently reaches `_maintain_indexed_tail`
(`src/rememberstack/adapters/selfhost/lance.py:242-311`). PR2 also proposes a
time-budget/enqueue behavior before PR3/PR4 provide the unit schema and enqueue
helper (`plan/designs/p1_lance_maintenance_design.md:625-633`).

For v1, bind writer paths to enqueue-only maintenance after the bulk write (or
to a pre-call decision based on a measured operation known to fit); do not start
an uninterruptible `optimize()` under `label_lock` and call it bounded. If an
inline optimize remains, the design needs a real isolation/cancellation
mechanism and a PR order in which its fallback enqueue already exists.

### P1.4 — Heartbeat and stale-running recovery are promised but not designed

Section 5.5.2 wraps synchronous optimize/reindex calls in a “heartbeat loop,” and
§9 says a stale heartbeat is reaped and requeued
(`plan/designs/p1_lance_maintenance_design.md:376-389,552-563`). A single handler
thread cannot update a 60-second heartbeat while blocked inside a multi-hour
`optimize()` or `create_index()`. More importantly, the generic ledger currently
has no lease expiry: claiming sets `processing_state.status='running'`, and only
normal handler return/exception changes it
(`src/rememberstack/spine/work_ledger.py:1386-1413`,
`src/rememberstack/workers/base.py:261-357`). A killed process leaves the row
running indefinitely. Updating only a unit-table heartbeat does not make that
row claimable.

The design's open question says the shared heartbeat/reaper may ship outside the
D91 core (`plan/designs/p1_lance_maintenance_design.md:595-605`), which conflicts
with §1's statement that failure contracts are required for ship and with the
multi-hour heavy-work design. Bind the heartbeat executor (separate thread or
process/connection), stale threshold, fencing/ownership token, atomic
running-to-retry transition, and interaction with a still-live Lance operation.
Alternatively split work into bounded per-table units with durable checkpoints,
but a process killed during one synchronous call still needs fenced recovery.
Add kill/restart acceptance; an ordinary raised-exception retry test is not
sufficient.

### P1.5 — Heavy maintenance has no progress-guaranteeing concurrency contract

Section 5.7 makes a PostgreSQL advisory lock optional for heavy jobs, lets light
maintenance proceed concurrently, and explicitly keeps writers outside the lock
(`plan/designs/p1_lance_maintenance_design.md:443-468`). That lock can prevent
two heavy units for a deployment, but it cannot serialize heavy against light or
against the writes whose commits invalidate a long index-build snapshot.
“Retryable commit conflict → bounded backoff” provides a failure policy, not a
progress guarantee: under continuous ingest, all eight retries can conflict,
and each full retraining attempt can repeat the expensive work.

The current adapter reinforces why extraction alone is insufficient:
`_build_vector_index()` calls `create_index()` directly, while only the scalar/FTS
helper, merge, and optimize paths have bounded retry wrappers
(`src/rememberstack/adapters/selfhost/lance.py:864-956,1046-1093`). The design
does require adding create-index retries, but it does not define how a heavy run
ever obtains a quiet commit window. Its acceptance suite covers concurrent
upsert + optimize, not concurrent upsert + heavy rebuild
(`plan/designs/p1_lance_maintenance_design.md:639-652`).

Bind one maintenance serialization scope per physical Lance table/root (not
only per deployment), so light and heavy cannot rewrite the same table at once.
Then bind the heavy-versus-writer policy: a bounded maintenance window with
writer quiescence/backpressure, a measured retry/cooldown and deferral protocol,
or another mechanism that guarantees eventual commit without indefinitely
blocking label work. Test continuous writes throughout a heavy rebuild, retry
exhaustion, and recovery without duplicate/corrupt index state.

### P1.6 — The rollout activates maintenance before its required safety gates

The design says observability and failure contracts are ship requirements (§1.8)
and describes a light-first, then-heavy rollout
(`plan/designs/p1_lance_maintenance_design.md:57-58,574-581`). The ordered PR plan
does the opposite operationally:

- PR4 adds a continuously self-seeding compose worker before PR6 adds required
  metrics and runbook material.
- PR5 enables heavy policy before PR6 adds conflict/duration/disk visibility.
- No settings knob disables the worker, disables self-seed, or restricts allowed
  modes during the light-only phase; the listed knobs are thresholds, not rollout
  gates (`plan/designs/p1_lance_maintenance_design.md:261-280,625-635`).
- PR7 performs the BEAM soak only after heavy is already implemented/enabled.

Reorder metrics and the runbook ahead of automatic activation, add explicit
`maintenance_enabled`/`heavy_enabled` (or equivalent) gates with safe defaults,
and define rollback behavior for pending/running units and an in-progress Lance
operation. PR1 can and should remain independent; the maintenance worker should
not self-start merely because its compose service exists.

### P2.1 — The “all tables” index contract omits the as-built entity gap and no index matrix is binding

The design names `chunks`, `claims`, `facts`, and `entities` as the default table
set and says `build_search_indexes()` remains “ensure + heavy for all present
tables” (`plan/designs/p1_lance_maintenance_design.md:232-259,302-313`). It also
says all other writers already use `_upsert` and need only stop relying solely on
process-local tail maintenance (§5.2.3). That description is false for entities:

- `upsert_entities()` calls `_upsert()` but does not ensure scalar/vector indexes
  and does not call `_maintain_indexed_tail`
  (`src/rememberstack/adapters/selfhost/lance.py:958-973`).
- `build_search_indexes()` handles chunks, claims, and facts only; there is no
  entity branch (`src/rememberstack/adapters/selfhost/lance.py:799-833`).
- The facts branch also does not currently ensure `fact_id`, status, or time
  indexes, although ordinary fact writes create most of those
  (`src/rememberstack/adapters/selfhost/lance.py:271-280,829-833`).

The companion rulebook explicitly requires a per-table complete index matrix
(`plan/analysis/lance_indexing_maintenance.md:101-105,171-176`). Add that matrix
to D91: exact vector, FTS, BTREE, and BITMAP columns for each table; behavior for
missing/legacy columns; min-row gates; and ensure-versus-replace semantics. Add
entity-heavy and upgraded-store acceptance. Otherwise an implementation that
only extracts today's private methods can ship a nominal “all tables” worker
while entities remain permanently indexless.

## Non-blocking nits

1. Strengthen PR1 acceptance to snapshot every non-metadata field before and
   after a multi-batch update—especially exact `vector` contents/dimension and
   `label`—and assert unchanged rows plus missing-key behavior. The proposed
   partial matched-only dict merge passes this check on pinned LanceDB 0.34.0,
   but the test should prevent an implementation from first aligning input to
   the full table schema with nulls. Use the returned merge counts to derive
   `metadata_miss` rather than assuming every source row matched.
2. Update the stale port docstring: `FactIndexPort.upsert_facts` says “by
   fact_id,” while the adapter and design correctly use
   `(deployment_id, kind, fact_id)`
   (`src/rememberstack/ports/p1_index.py:66-76`,
   `src/rememberstack/adapters/selfhost/lance.py:242-249`).
3. Bind `create_index(..., replace=True)` for heavy rebuild and a list-before-create
   no-replace behavior for ensure. “Create/rebuild” is otherwise easy to
   implement with the wrong destructive/idempotent semantics.
4. `MaintainReport` needs per-table before/after stats, skipped reason, and
   completed operation—not only rows/unindexed/duration/conflicts—so a min-row
   skip or unavailable fragment metric is distinguishable from successful
   maintenance (`plan/designs/p1_lance_maintenance_design.md:484-502`).
5. Define how an existing deployment initializes `last_heavy_row_count`. A null
   baseline must deterministically mean rebuild-now, record-current-index, or
   wait-for-admin; otherwise the 25% growth trigger is not reproducible after
   rollout.
6. Fragment count after light maintenance is not, by itself, a reason to retrain
   IVF, and an unindexed tail left by conflicting writers is primarily a light
   maintenance/progress signal. Keep heavy triggers tied to train quality/row
   growth (or measured recall), rather than letting fragment debt blur the
   design's otherwise good layer separation.
7. A “filesystem consistent copy” backup needs a quiesce/snapshot procedure.
   Copying the named volume while writers or compaction are committing is not a
   defined consistent backup (`plan/designs/p1_lance_maintenance_design.md:469-482`).

## Strengths

- The incident trace is accurate. `LabelFactsHandler` embeds in batches, stamps
  references after each Lance upsert, then refreshes all affected fact metadata
  while still holding `label_lock`
  (`src/rememberstack/workers/p1.py:202-295`). The adapter currently performs one
  `table.update` commit per metadata row
  (`src/rememberstack/adapters/selfhost/lance.py:283-311`).
- The chosen writer fix has the right identity and missing-row semantics:
  `(deployment_id, kind, fact_id)`, matched update only, no insert. It also keeps
  mutable eligibility scalars on the existing full Phase-E upsert payload
  (`src/rememberstack/adapters/selfhost/lance.py:242-270`).
- The light/heavy distinction is correct and well grounded. Official LanceDB
  documentation says `optimize()` compacts, prunes, and incrementally updates
  existing vector/scalar/FTS indexes; it is not a replacement for deliberately
  rebuilding the IVF training/partition layout. See
  [Performance Tips](https://docs.lancedb.com/performance) and
  [Keeping Indexes Up to Date](https://docs.lancedb.com/indexing/reindexing).
- Keeping content rebuild/embedding migration separate from light maintenance
  and heavy index retraining preserves P1's rebuildable-projection boundary and
  matches `workers.md` §6.3.
- Unifying the backfill finalizer and continuous maintenance behind
  `P1IndexMaintenancePort` is the right seam. The current finalizer already has a
  correct drain barrier before invoking the one-method port
  (`src/rememberstack/spine/backfill.py:99-125`,
  `src/rememberstack/ports/p1_index.py:288-294`).
- The analysis correctly identifies the process-local nature of
  `_mutations_since_optimize`, the absence of a compose maintenance service, and
  the local `app-state`/`lance_root` storage topology
  (`src/rememberstack/adapters/selfhost/lance.py:75-80,891-918`,
  `compose.yaml:51-53,113-189,198-202`).
- Shipping PR1 independently is the right operational priority: it removes the
  O(rows) commit storm without waiting for the larger worker/control-plane
  design.

## Checklist against design contracts

| Contract | Assessment | Review |
| --- | --- | --- |
| Problem and as-built diagnosis | **Pass** | The per-row metadata loop, lock scope, process-local thresholds, one-shot backfill index build, compose gap, and storage path match the code. Incident magnitudes remain author-reported. |
| Batched metadata writes | **Pass with test nit** | Matched-only partial `merge_insert` on the exact triple is the correct v1 contract and reduces commits to O(batches). |
| Vectors/labels are never wiped | **Pass in design; acceptance incomplete** | Omitted target columns are preserved by the specified LanceDB 0.34 partial matched update, and missing targets are not inserted. Add an explicit before/after vector+label regression test. |
| Two-layer light/heavy model | **Partial** | `optimize` versus IVF/FTS rebuild is clearly separated, but heavy trigger semantics, baseline initialization, and all-table index coverage need closure. |
| Full per-table index set | **Fail** | No binding index matrix; the current entity table is omitted from both inline maintenance and `build_search_indexes()`. |
| Shared maintenance port/backfill barrier | **Pass** | Expanding the existing port and retaining the finalizer drain barrier is coherent. |
| D67 ledger identity and lane | **Fail** | A scheduled P1 aggregate is assigned Plane-E lanes without an explicit D67 amendment. |
| Recurrence and coalescing | **Fail** | The unit schema/unique key cannot enforce one open unit, the idle worker cannot self-seed, and a running coalesce can lose the new edge. |
| Lease, heartbeat, and crash recovery | **Fail** | No stale-running transition/fencing exists; a synchronous call cannot run the proposed same-thread heartbeat. |
| Writer/maintenance concurrency | **Fail** | Optional heavy-only locking plus retries does not serialize light/heavy or guarantee a heavy commit under continuous writes. |
| No heavy work on read path or under `label_lock` | **Partial** | Heavy placement is correct, but optional synchronous light optimize has no enforceable time bound and can still extend the label lease. |
| Observability | **Partial** | Required signals are well enumerated, but reporting shapes and fragment availability need definition, and rollout enables work before metrics land. |
| Compose/profile wiring | **Partial** | The missing service and shared volume are correctly identified; lane selection, self-seed execution, and readiness registration are not. |
| Rollout and rollback | **Fail** | PR1 is realistic; PR2-PR7 order lacks dependencies, activation gates, pre-enable telemetry, and in-flight rollback behavior. |
| Storage/backup | **Pass with nit** | `lance_root` and compose volume claims are correct; live filesystem-copy consistency needs an operational contract. |
| Public-source grounding | **Pass** | The external claims rely on official LanceDB performance, update, and reindexing documentation; no private-product comparison is used. |

## Final recommendation

Keep the storage-level decisions and ship the isolated PR1 bulk merge after the
vector/label preservation test is added. Do not bind D91 or enable the
maintenance worker until the lane/readiness model, scheduler/coalescing state
machine, bounded-execution/lease recovery, maintenance serialization, complete
index matrix, and gated rollout order are resolved.
