# Implementation review: D91 PR3 maintain ledger

**Reviewer:** Claude (claude-opus)
**Date:** 2026-08-13
**PR:** #276
**Commit:** 1779771c

Binding design: `plan/designs/p1_lance_maintenance_design.md` at 87079b4b
(branch `design/d91-p1-lance-maintenance`), §5.4–§5.6, §6.3, §15 PR3 row.

Validation performed against a local PostgreSQL (docker, partman image):

- `src/tests/spine/test_p1_maintain_ledger.py` — 9/9 passed (~2m09s)
- `src/tests/spine/test_migrations.py` — 6/6 passed (~2m35s), including the
  fresh downgrade→re-upgrade lifecycle and the full catalog-contract verify
- `ruff check src/` clean; `pyright` clean on the touched modules

Note on scope: during this review the branch gained follow-up commit
`37300332 fix(p1): take enqueue lock before unit FOR UPDATE`. This review is
pinned to 1779771c as requested; where 37300332 already resolves a finding it
is marked **[fixed at 37300332]**. The test runs above executed on the branch
tip, but 37300332 touches only `src/rememberstack/spine/p1_maintain.py` — the
migration, catalog, and test files are identical at both commits, and neither
blocking finding below is reachable by the current single-threaded tests, so
the results apply to 1779771c equally.

## Verdict

**REQUEST_CHANGES** at the reviewed commit 1779771c.

Both blocking findings are already resolved on the branch by 37300332. With
that commit included, this PR is **APPROVE_WITH_NITS**, with the items under
"Other required corrections" tracked for this PR or explicitly assigned to
PR4 in the PR description.

## Blocking findings

### B1. Lock-order inversion between `complete_maintain_p1_on` and `enqueue` — deadlock in the rerun race **[fixed at 37300332]**

`src/rememberstack/spine/p1_maintain.py` (at 1779771c): the enqueue path takes
the coalesce advisory xact lock **first** and then row-locks the open unit
(`_ENQUEUE_LOCK`, then `_SELECT_OPEN_UNIT … FOR UPDATE OF u, ps`).
`complete_maintain_p1_on` did the opposite: `_SELECT_UNIT … FOR UPDATE` on the
unit row first, then `_ENQUEUE_LOCK`.

Failure scenario — the exact race the design's atomic completion exists to
close (§5.5.3): the completion transaction row-locks unit U; a concurrent
enqueue for the same `(root, table, mode)` acquires the advisory lock and
blocks on U's row lock (the running unit matches `_SELECT_OPEN_UNIT`'s
`status IN ('pending','failed','running')` predicate); the completion
transaction then blocks on the advisory lock. Advisory locks participate in
PostgreSQL's deadlock detection
(<https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-DEADLOCKS>),
so after `deadlock_timeout` one transaction aborts with a serialization error
instead of hanging — but that means the completion (or the racing enqueue)
fails spuriously in precisely the window §5.5.3 makes binding
("take enqueue xact lock … **then** SELECT unit FOR UPDATE"). A completion
abort at the PR4 runner would then convert a successful maintain into a
retryable fail, burning an attempt.

The fix in 37300332 (peek the unit without a lock to learn the immutable
`(root, table, mode)`, take the advisory lock, then re-select the unit
`FOR UPDATE`) is correct: the three key columns never change on a unit row, so
the unlocked peek cannot read a stale key.

### B2. `P1MaintainCatalog.from_engine` constructs `WorkLedger` without its required `settings` **[fixed at 37300332]**

At 1779771c, `from_engine` called `WorkLedger(engine=engine)`, but
`WorkLedger.__init__` requires the keyword-only `settings: WorkLedgerSettings`
(`src/rememberstack/spine/work_ledger.py:95`). Any caller of the exported
classmethod (re-exported via `rememberstack.spine.__init__`) got an immediate
`TypeError`. No test exercises `from_engine`, which is how it slipped through.
Fixed at 37300332 by passing `WorkLedgerSettings()`.

## Other required corrections

### R1. Enqueue treats a *reclaimable* running unit as open, diverging from the binding coalesce rule

Design §5.5.1 defines open-ness as pending / retryable-failed **or** "running
with a fresh heartbeat / **not past reclaim criteria**", and the §5.5.2
pseudocode marks `rerun_requested` only when a running unit exists "**and not
reclaimable**" — a reclaimable running unit is not open, and a fresh unit +
pending ledger row must be inserted (double-run is explicitly safe via the
table lock and idempotent Lance ops).

`P1MaintainCatalog.enqueue` sets `rerun_requested` on **any** running unit
(`src/rememberstack/spine/p1_maintain.py`, the `status == "running"` arm) with
no reclaimability check, even though the settings needed for that check are on
the catalog.

Failure scenario: a maintain claim dies; an enqueue arrives after the row is
past reclaim criteria but before `reclaim_stale` runs → only `rerun_requested`
is set. If the subsequent reclaim **dead-letters** the row (attempts exhausted;
`processing_state.max_attempts` defaults to 3 per `p0_02_0002`), the unit is
closed with the flag still set: `complete_maintain_p1` never runs for it, no
successor is created, and the enqueue caller was told `rerun_requested=True`
as if the work were scheduled. Nothing re-opens the table until PR4's
`ensure_maintain_due` next trips a threshold — for `ensure_indexes` /
admin-reason units there may be no threshold to trip.

Fix: either implement the design's branch (running **and** reclaimable → insert
a fresh unit; the ledger key is per-`unit_id`, so no conflict), or amend the
design section to bless the simpler "always rerun-flag a running unit"
semantics and document the dead-letter loss window plus its PR4 recovery. Code
and binding design must not disagree silently.

### R2. `reclaim_stale` neither re-announces rescheduled work nor returns enough for the caller to do it

Design §5.5.2's binding reclaim path calls `queue.announce(...)` for every
scheduled (retryable) reclaim. The insert trigger
(`tr_processing_state_initial_wake`) only fires on ledger **inserts**, and
`WorkLedger.fail`'s own docstring says "the caller re-announces it through the
queue port". `P1MaintainCatalog.reclaim_stale` does neither and returns only a
count, so the PR4 tick cannot announce without re-scanning.

Consequence is latency, not lost work — `_CLAIM_SELECT` polls due rows by
`not_before <= now()` — but the binding text is explicit, and a reclaimed row
silently waiting out a poll interval defeats the point of reclaiming promptly.
Minimal PR3-shaped fix: call `self._ledger.wake(processing_id=…)` after each
scheduled fail (the `wake` primitive exists precisely "for retry, replay, and
janitor paths"), or return `(processing_id, scheduled)` tuples so the PR4
caller can announce through the queue port.

### R3. `reclaim_min_s` is defined but enforced nowhere

`P1MaintainSettings.reclaim_min_s` (design knob `maintain_reclaim_min_s`,
§5.4) exists, and the §5.5.2 pseudocode places the rate floor **inside**
`reclaim_stale_maintain`. `reclaim_stale()` never reads it — every call does a
full scan plus up to one advisory-lock probe per heartbeat-less row. If the
floor is deliberately deferred to the PR4 tick, say so in a comment on the
setting and in the PR description; otherwise enforce it here (e.g. a
`last_reclaim_scan_at` marker or an in-process monotonic floor).

### R4. The fenced dead-letter arm — the actual "CHECK-safe" boundary — has no test

§15 PR3 validation names "CHECK-safe fail path". The dangerous boundary is a
reclaim at `attempts == max_attempts`: a naive `status='failed'` write there
violates `CHECK (status <> 'failed' OR attempts < max_attempts)`. The code
handles it (`fail` routes to `_FAIL_DEAD_LETTER_FENCED`, which sets
`dead_letter` with `defer_reason = NULL`), but no test drives a maintain row to
attempt exhaustion through `reclaim_stale`. Add one: claim/expire/reclaim three
times (or lower `max_attempts` on the row), assert the final state is
`dead_letter`, that the CHECKs held, and that a subsequent enqueue **creates a
fresh unit** (dead_letter is not open, §5.5.1). That last assertion also pins
the R1 recovery story.

## Nits

- **N1.** `EXPECTED_TABLES` and `EXPECTED_INDEXES` insertions in
  `catalog_contract.py` are not at their alphabetical positions
  (`p1_*` entries sort after `observations`, and `ix_p1_maintain_units_key`
  after `ix_obsevidence_claim`). `_compare` is set-based so nothing breaks,
  but the lists read as sorted everywhere else.
- **N2.** `_BUMP_REQUESTED` / `_MARK_RERUN` dedupe reasons with
  `position(:reason IN reason)` — substring matching, so a new reason that is
  a substring of an existing one (e.g. `admin` vs `admin_force`) is silently
  dropped. Diagnostic-only field; exact-token matching or a jsonb array would
  be cleaner.
- **N3.** `_unit_is_reclaimable` compares DB-written `started_at` /
  `last_heartbeat_at` against the app clock (`datetime.now(tz=UTC)`), and
  `fail(not_before=…)` converts an absolute time to a backoff via the app
  clock. Clock skew between worker hosts and PostgreSQL shifts reclaim
  cutoffs; evaluating staleness in SQL against `now()` would remove the
  sensitivity. (The generous default floors make this cosmetic today.)
- **N4.** `_COMPLETE_FENCED` matches on `processing_id + running + attempts`
  only — nothing binds the processing row to `unit_id` or to
  `stage = 'maintain_p1_index'`. The design's pseudocode is the same, and all
  callers pass `ClaimedWork`-derived pairs, but one extra predicate
  (`AND target_id = :unit_id AND stage = 'maintain_p1_index'`) would make a
  mispaired call fail closed instead of succeeding foreign work while
  spawning a maintain successor.
- **N5.** No test constructs `P1MaintainSettings()` bare and asserts both
  gates are `False`. The class defaults are visibly off, but a one-line test
  freezes the contract and would also catch a stray
  `REMEMBERSTACK_P1_MAINTAIN_*` variable leaking into a test environment.
- **N6.** `skip_successor=True` completion leaves `rerun_requested` set on the
  closed unit. Harmless (closed units never match `_SELECT_OPEN_UNIT`), but
  it is stale state on a terminal row; clearing it in the same UPDATE is one
  line.
- **N7.** `_gate_skip` lets `force=True` bypass the `maintenance_enabled`
  master gate; the §5.5.2 pseudocode bypasses it only for
  `reason == admin_force`. This is intent-compatible (§5.4 exempts
  finalizer/admin paths from gating) but broader than written — worth one
  sentence in the docstring so the wider bypass reads as chosen, not
  accidental.
- **N8.** The `deferred_successor_not_before` arm of `complete_maintain_p1`
  (pure rate-defer successor, reason `deferred_heavy`) is implemented but
  untested in PR3; PR5 owns the policy, but a direct unit test of the
  successor's `not_before` snapshot here would be cheap.

## What is correct

- **Unlaned stage, end to end.** `maintain_p1_index` added to the Python
  `PipelineStage`, the SQL enum (additive `IF NOT EXISTS`), `UNLANED_STAGES`,
  and the queue-port contract test. `lane_is_valid` enforcement at the enqueue
  path is proven both ways: a steady-lane maintain enqueue raises
  `LaneRouteError`, and an unlaned `claim_one` claims the unit
  (`test_unlaned_maintain_rejects_a_lane`,
  `test_running_enqueue_sets_rerun_and_complete_inserts_successor`). The
  component version is a fixed string not registered in the component
  registry, per §5.5.1.
- **Gates default off.** `P1MaintainSettings` defaults `maintenance_enabled`
  and `heavy_enabled` to `False` under `REMEMBERSTACK_P1_MAINTAIN_*`;
  continuous enqueue returns typed skip results (`maintenance_disabled`,
  `heavy_disabled`); light-with-master-gate-on and admin-force bypass both
  behave per §5.4 and are tested.
- **Coalesce implementation matches the binding.** No partial unique index on
  `p1_maintain_units` filtered by ledger status — correctly impossible, since
  a partial-index predicate may only reference the indexed table's columns
  (<https://www.postgresql.org/docs/current/indexes-partial.html>); the DDL
  index `ix_p1_maintain_units_key` is plain. Open-ness is computed through the
  advisory xact lock + `SELECT … FOR UPDATE` join exactly as §5.5.1 requires;
  a pending coalesce bumps `requested_at`, ORs the reason, optionally lowers
  `not_before` (only on pending/failed rows, never raising it), and provably
  inserts no second unit (`test_pending_coalesce_does_not_create_a_second_unit`).
- **Ledger identity per §5.5.1.** `target_kind p1_maintain_unit`,
  `target_id = unit_id`, fixed component version, diagnostic content hash in
  the specified format, `lane NULL`, payload `{mode, table, force, reason}`.
  Fresh unit ids keep the ledger unique key conflict-free; successors reuse
  nothing.
- **`rerun_requested` + atomic successor.** The completion transaction fences
  the succeed (`status='running' AND attempts=:expected_attempt`), consumes
  the flag, and inserts successor unit + pending ledger row **in the same
  transaction**; the successor is announced by the schema-owned insert
  trigger on commit. The race (enqueue between claim and completion) is
  tested and the successor carries reason `rerun`, `force=false`.
- **Attempt fence, both directions.** `complete_maintain_p1` and `fail`
  (`expected_attempt=`) are compare-and-transition on
  `(status='running', attempts)`; zero rows → `WorkNotRunningError`. The
  pre-UPDATE `SELECT … FOR UPDATE` in `fail` makes the fence race-free, and
  the fenced retry arm can only produce CHECK-legal rows
  (`defer_reason='retry_backoff'`, `attempts < max_attempts`). The PR3
  acceptance test forces stale attempt A's complete **and** fail while B runs
  — both rejected, B then completes
  (`test_stale_attempt_cannot_complete_or_fail_replacement`).
- **Reclaim liveness split per §5.5.2.** Heartbeat present → staleness by
  `heartbeat_stale_mult × heartbeat_s`, no table-lock probe (tested with the
  lock deliberately held). No heartbeat → wall-clock floor **plus** the
  `pg_try_advisory_lock` probe, released immediately, so a live owner whose
  heartbeat thread died is not stolen (tested both held and released). The
  select→fail race is handled per row (`WorkNotRunningError` caught,
  loop continues), and a row re-claimed between scan and fail is protected by
  the attempt fence, not just the status check.
- **Lock-key consistency.** `lance_root_key()` and
  `p1_table_maintain_lock_key()` both canonicalize through `Path.resolve()`,
  so the probe in reclaim and the PR2 holder derive identical advisory keys
  from either the raw root or the stored key.
- **Migration and catalog counts.** Enum values are additive with
  `IF NOT EXISTS`; downgrade drops only the two tables (enum values remain,
  documented). Constraint deltas are exactly right: +6 CHECK (3 per table),
  +1 FK (`p1_maintain_units → deployments`), +2 PK, +0 unique —
  `EXPECTED_CONSTRAINT_COUNTS {c:60, f:129, p:71, u:35, x:1}`; table count
  69→71; `DECISION_OBJECTS["D91"]` maps the two tables and the index. Both
  tables carry `COMMENT ON TABLE`, and the inline `--` column comments are
  materialized into real catalog column comments by the migration helper, so
  the contract's comment checks pass. Verified end-to-end by the passing
  downgrade→re-upgrade inventory test at head `p9_11_0032`.
- **`p1_lance_table_stats` schema matches §5.6**: keyed by
  `(lance_root_key, table_name)` with no deployment in the key, carries the
  authoritative escalation counters (`rate_defer_count`,
  `conflict_defer_count`, `first_defer_at`, `operator_state`) and
  `writer_gate` with CHECK-constrained vocabularies, ready for PR4/PR5
  writers without further DDL.
- **CI wiring.** New integration test registered in
  `.github/ci/integration-paths.txt`; compose e2e head assertion bumped to
  `p9_11_0032`.
- **Scope discipline.** No worker, handler, heartbeat thread, or compose
  changes smuggled in; `claimed_attempt` stamping and heartbeat writes are
  correctly left to the PR4 handler while the schema and fences they need all
  land here.
