# Implementation review: D91 PR3 maintain ledger

**Reviewer:** Codex (GPT-5.6-sol)
**Date:** 2026-08-13
**PR:** #276
**Commit:** 1779771c

## Verdict
REQUEST_CHANGES

The migration and the basic ledger shape are close to the binding design, but the target commit is not safe to merge. The completion/enqueue race can deadlock, reclaim can fail a live replacement attempt by treating an earlier attempt's heartbeat as current, forced intent is lost on coalesce, and maintain completion does not prove that its processing row belongs to its unit. The target also fails the repository's required Pyright check.

Validation performed against `1779771c`:

- `uv run ruff check` on the changed Python paths passed.
- `uv run pyright src/ benchmarks/ --pythonversion 3.13` failed with 16 errors in `spine/p1_maintain.py`.
- The focused pytest invocation collected the new PostgreSQL cases, but all nine were skipped locally because `REMEMBERSTACK_DATABASE_URL` was not configured. The findings below therefore come from the pinned diff, SQL lock analysis, and the checked-in test logic rather than a local PostgreSQL execution.

## Blocking findings

1. **`complete_maintain_p1` and enqueue acquire the same locks in opposite orders, so the exact rerun/completion race can deadlock.** In `src/rememberstack/spine/p1_maintain.py:286-303`, completion executes `_SELECT_UNIT`, whose SQL at lines 472-479 includes `FOR UPDATE`, and only then takes `_ENQUEUE_LOCK`. Enqueue does the reverse at lines 103-126: advisory xact lock first, then `_SELECT_OPEN_UNIT ... FOR UPDATE`. One session can therefore hold the unit row while waiting for the advisory lock as the other holds the advisory lock while waiting for the unit row. PostgreSQL detects this cycle by aborting one transaction; it does not make the operation atomically succeed. That can reject the enqueue that was supposed to set `rerun_requested`, or abort completion and leave the row running. PostgreSQL's documented defense is consistent lock ordering: [Explicit Locking, Deadlocks, and Advisory Locks](https://www.postgresql.org/docs/current/explicit-locking.html). Do an unlocked lookup only to derive the key, acquire the enqueue advisory lock, then re-read the unit `FOR UPDATE`; add a two-connection test that overlaps enqueue and completion rather than calling them sequentially.

2. **Reclaim treats a heartbeat from attempt A as liveness evidence for attempt B and can consequently reclaim a live B.** `_SELECT_RUNNING_UNITS` (`spine/p1_maintain.py:482-494`) does not project or compare `u.claimed_attempt`. `_unit_is_reclaimable` treats every non-null `last_heartbeat_at` as belonging to the currently running attempt, and `reclaim_stale` skips the table-lock probe whenever that column is non-null (`:217-236`). A concrete failure is: A leaves a stale heartbeat; reclaim fails A; B is claimed on the same processing row; before B publishes a new heartbeat, another scan observes B's current `ps.attempts` together with A's stale timestamp and calls `fail(expected_attempt=B)`. The attempt fence then correctly authorizes failure of B, even if B holds the live table lock. The existing stale-A complete/fail test does not exercise this second reclaim. Bind heartbeat validity to `claimed_attempt = ps.attempts`; a mismatched or unstamped heartbeat must not enter the heartbeat-stale arm. Claim-start stamping should reset/publish B's heartbeat atomically before that timestamp can be trusted. Add the replacement-attempt/live-lock regression test.

3. **Forced enqueue intent is discarded whenever the request coalesces.** `_gate_skip` accepts `reason == 'admin_force'` or `request.force`, but the pending/failed and running branches (`spine/p1_maintain.py:127-154`) update only unit reason/request time or `rerun_requested`; they do not OR `force` into durable work state. The atomic successor hardcodes `"force": False` at lines 377-381. Thus an accepted forced heavy request can coalesce onto a non-forced pending unit, or set rerun on a running unit, and the eventual handler/successor sees `force=false`; with the heavy gate off it will skip the operation the administrative override requested. Preserve force monotonically in the pending processing payload and in durable rerun state so an atomic successor inherits it. Test pending-force and running-force coalesces while the heavy gate is off.

4. **The maintain completion fence does not bind `processing_id` to `unit_id` or even to the maintain stage.** `complete_maintain_p1_on` locks the unit supplied by the caller, but `_COMPLETE_FENCED` (`spine/p1_maintain.py:497-504`) updates solely by `processing_id`, `status='running'`, and `attempts=expected_attempt`. A mismatched request can mark an unrelated running processing row succeeded and then write the requested unit/create its successor. Attempt equality is an ownership fence only after ledger identity is established. The fenced predicate (or a preceding locked select) must additionally require `target_id = unit_id`, `target_kind = 'p1_maintain_unit'`, `stage = 'maintain_p1_index'`, the fixed component version, and matching deployment. Add a negative cross-unit/cross-stage test.

5. **The target commit fails the required static gate and its convenience constructor raises at runtime.** `P1MaintainCatalog.from_engine` calls `WorkLedger(engine=engine)` at `spine/p1_maintain.py:96`, but `WorkLedger.__init__` requires explicit `WorkLedgerSettings`; calling this path raises `TypeError`. Pyright also rejects the `object`/`dict(...)` typing in `_insert_successor` and `_unit_is_reclaimable`, producing 16 errors total. Fix the constructor and give the row helpers a real mapping/row-mapping type; the full repository Pyright command must be green.

6. **Retryable reclaim drops the queue announcement required by the ledger contract.** `WorkLedger.fail` explicitly returns the scheduled time for the caller to announce, but `P1MaintainCatalog.reclaim_stale` ignores the return value and has no queue port (`spine/p1_maintain.py:230-239`). The binding reclaim algorithm requires an unlaned `QueueRoute` announcement after a retryable failure. Without it, a delivery backend has no retry notification and progress depends on fallback polling. Accept a queue port or return complete announcement data, and announce only after the fail transaction commits; add a fake-port assertion for retry and no announcement for dead-letter.

## Other required corrections

1. **Honor the configured reclaim floor and deployment scope.** `reclaim_min_s` is defined at `spine/p1_maintain.py:51` but never read. Every call scans all running maintain rows, and `_SELECT_RUNNING_UNITS` has no `deployment_id` predicate even though reclaim is defined per deployment. Rate-floor scans and restrict the query to the caller's deployment; this also makes the retry route attribution unambiguous.

2. **Do not coalesce a new request onto a running row that is already reclaimable.** `_SELECT_OPEN_UNIT` returns every running row and enqueue always sets `rerun_requested`; it never applies the binding fresh-heartbeat/reclaim criteria. A stale run is not an open unit under §5.5.1. Reclaim it under the attempt fence or treat it as stale before deciding whether to mark rerun/create fresh work. Add the stale-running enqueue case.

3. **Expand the PostgreSQL acceptance tests to prove the concurrency and CHECK edges, not only the sequential happy paths.** The current rerun test sets the flag and completes in sequence, so it cannot expose the lock inversion. The reclaim test exercises a retry below `max_attempts`, but does not explicitly prove the `attempts == max_attempts` branch becomes `dead_letter` without violating the failed-row CHECKs. Add: simultaneous enqueue/coalesce; enqueue-vs-complete; retryable fail state (`failed`, `retry_backoff`, due time); attempt-exhausted reclaim (`dead_letter`, null defer reason); select-to-fail completion race continuing to later rows; and the attempt-B heartbeat case above.

4. **Tie deferred-successor delivery to its future `not_before`.** The successor insert is correctly atomic, but a future-dated insert does not trigger an immediate due notification, and `complete_maintain_p1` exposes only `EnqueueOutcome`, not a route/due announcement. Ensure the caller can announce the successor with `lane=None` and the exact committed due time after completion commits.

5. **Clear `rerun_requested` on terminal `skip_successor` completion.** Leaving it true on a succeeded/awaiting-operator unit is not operationally open, but it makes the durable row claim that an unconsumed rerun remains. Terminal completion should consume or explicitly record why it discarded that flag.

## Nits

1. `_BUMP_REQUESTED` and `_MARK_RERUN` use `position(:reason IN reason)` as if `reason` were a token set. Substrings such as `schedule` and `schedule_admin` can be mistaken for duplicates. Store reasons structurally or split/compare complete tokens.

2. `P1MaintainEnqueueRequest` accepts any non-empty `lance_root_key`, while coalescing and advisory-lock identity depend on canonical spelling. Either canonicalize at the catalog boundary or make the API accept the root path and derive the key there; otherwise path aliases can bypass coalescing.

3. The default-off test explicitly injects `maintenance_enabled=False` and `heavy_enabled=False`; add a small test of an unmodified `P1MaintainSettings()`/working `from_engine` path so environment/default regressions are caught directly.

## What is correct

- Migration `p9_11_0032` adds exactly the two required enum values without adding a readiness component, creates one-unit-per-table/mode storage, and keys durable stats by `(lance_root_key, table_name)` rather than deployment. The required heartbeat, rerun, attempt, operator, change-mass, defer-counter, and writer-gate fields are present.
- The migration uses ordinary constraints plus a non-unique key index; it correctly avoids trying to express ledger-status openness as a partial index on another table. PostgreSQL permits partial-index predicates to use only columns of the indexed table: [PostgreSQL Partial Indexes](https://www.postgresql.org/docs/current/indexes-partial.html).
- The catalog inventory additions are internally consistent: two tables, one index, six CHECKs, one foreign key, and two primary keys produce the updated `c=60`, `f=129`, and `p=71` counts. Inline migration comments are materialized by `apply_ddl`, and both tables have table comments.
- `PipelineStage`, `ProcessingTarget`, `EXPECTED_TABLES`, `EXPECTED_INDEXES`, `DECISION_OBJECTS`, and `UNLANED_STAGES` are updated. Enqueue uses `lane=None`, and the spine rejects a concrete lane for `maintain_p1_index`.
- Both settings gates are declared default-off, light/heavy gating is separated, and the initial created work carries mode, table, force, reason, diagnostic content hash, and the requested `not_before`.
- The normal coalesce shapes are right: the advisory xact lock serializes the key; pending/retryable-failed work is updated rather than duplicated; running work sets `rerun_requested`; dead-letter/succeeded work is not considered open; and there is no status-derived partial unique index.
- Subject to the lock-order and identity corrections above, successor creation is in the same transaction as attempt-fenced success, uses a fresh unit/target ID, preserves the root/table/mode identity, and does not use generic `WorkLedger.complete`.
- `WorkLedger.fail(expected_attempt=...)` locks the row before deciding retry versus dead-letter, checks status and attempt, uses `failed + retry_backoff + not_before` below the attempt limit, and uses `dead_letter + defer_reason NULL` at exhaustion. This is CHECK-safe and rejects stale fail calls. `complete_maintain_p1` likewise rejects an old attempt number once a replacement attempt is running.
- Reclaim is maintain-stage/target/version scoped, carries the observed attempt into `fail`, catches select-to-fail terminal races per row, never reclaims a fresh heartbeat, and performs/release-probes the shared table advisory lock on the no-heartbeat wall-clock arm. That wall-clock behavior matches the binding live-table-lock defense once heartbeat ownership is fixed.
- PR4 worker/handler/compose/readiness-component work is not present, which is correct for this PR's scope.
