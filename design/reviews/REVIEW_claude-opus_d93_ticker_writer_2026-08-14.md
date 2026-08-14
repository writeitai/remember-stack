# Implementation review: D93 ticker + writer change-mass

**Reviewer:** Claude (Fable 5)
**Date:** 2026-08-14
**PRs:** #280 (`feat/d91-pr3-maintain-ticker` — ticker + `p1_lance_table_stats`), #283 (`feat/d93-pr4-writer-change-mass` — writer change-mass hooks + per-table heavy policy)
**Commit:** 813660f956ba6aeef3ed8d72d2ca92e82d5802e3 (HEAD of `feat/d93-pr4-writer-change-mass`)

Binding references: `plan/designs/p1_lance_maintenance_design.md` (D93, ticker amendment
2026-08-14), `decisions.md` § D93. Verification performed: full read of the design and D93;
diff review of both PRs against `origin/main`; adapter tests run locally against pinned
`lancedb==0.34.0` (8 passed); spine proofs run against a real Postgres 16
(`test_p1_maintain_ticker.py`, `test_p1_maintain_lock.py`, `test_migrations.py` — 16 passed);
probed `Table.list_indices()` on the pinned engine to confirm `IndexConfig.num_unindexed_rows`
exists (it does — `maintenance_stats` is sound).

## Verdict

**REQUEST_CHANGES**

The write-path half of PR4 is genuinely good: the change-mass hook is an optional callback
injected by the profile (no spine import in the adapter), it fires only on the four vector
upsert paths, metadata-only merges provably do not bump it, and PR #283 correctly replaces
PR #280's naive `changed_rows > 0` retrain trigger with the binding per-table
frac/mass/growth policy. The locks, gates, catalog contract, and migration are right.

What blocks: the *policy* half of PR4 (§15: "heavy policy + `awaiting_operator` + writer
gate") is largely absent — the escalation columns shipped in the migration are dead, a
retrain commit conflict kills the ticker process instead of recording a defer, the ticker's
`ensure` operation becomes unreachable the moment a writer bumps stats, and one retrain
trigger fires heavy work where the design mandates compact-first. Plus a same-PR docs (D66)
violation on the new CLI/compose/env surface.

## Blocking findings

### B1 (PR #283) — `awaiting_operator`, defer counters, and the writer gate are unimplemented

Design §15 assigns PR4 "Writer change-mass bump + heavy policy + `awaiting_operator` +
writer gate". Migration `p9_12_0033` (PR #280) ships `rate_defer_count`,
`conflict_defer_count`, `first_defer_at`, `operator_state`, and `writer_gate`, but no code
ever writes them:

- `operator_state` is only **read** (`p1_maintain_ticker.py:223` skips retrain while
  `awaiting_operator`); nothing ever sets it. The escalation ladder of §5.7 rule 3
  (`heavy_rate_defer_escalate_n` = 12, `heavy_conflict_defer_escalate_m` = 3,
  `heavy_defer_age_escalate_h` = 24h) does not exist. D93 point 5 ("escalate to durable
  `awaiting_operator` rather than silent thrash", K16) is the honesty contract of the whole
  heavy design; as shipped it can only be entered by hand-written SQL.
- There is no write-rate defer at all: `heavy_defer_write_rate` has no knob and no check, so
  retrain fires *during* sustained writes — precisely the regime §5.7 rule 3 says to skip
  ("Preferred: skip retrain this tick while write rate exceeds the threshold";
  `last_maintain_enqueue_at` is maintained by the writer hook but never consulted).
- A `create_index` commit conflict after a full train is not recorded as one
  `conflict_defer` — it propagates as an unhandled exception (see B2). The single-attempt
  rule is honored (`_build_vector_index`, `lance.py:1328-1342`, has no retry loop — good),
  but "record the conflict on the stats row and try again on a later tick" (D93 §1.11) is
  missing entirely.
- `writer_gate` (`run`/`hold`) is never read by any P1 writer. §5.6 is explicit: "live P1
  writers that mutate this table must re-read the gate **each batch**." Without it there is
  no supported quiet-window path (§5.7 rule 3, operator action 1), so an operator confronted
  with a stuck heavy has no sanctioned tool except scaling down compose services.

Consequence when `heavy_enabled=true`: the trigger side of heavy policy is live but the
defer/escalation side is not — the worst half-implementation. Under continuous writers the
ticker will attempt multi-hour trains at the worst time, crash on conflict (B2), and never
surface the durable operator-visible state the design promises. Acceptance test §16
("Sustained high write + heavy → `awaiting_operator` on stats; compact still allowed") is
unimplementable against this code.

If the intent was to split this into a later PR, that is a sequencing change to §15 that
needs the author to say so and the design/PR plan to record it — as reviewed, PR #283 claims
the PR4 slot and does not fill it.

### B2 (PR #280; aggravated by #283) — any Lance/Postgres error kills the ticker process

`run_p1_maintain_ticker` (`profiles/selfhost.py:644-646`) is:

```python
while True:
    ticker.tick()
    time.sleep(settings.poll_s)
```

Inside `tick()`, only the `maintenance_stats` probe is guarded
(`p1_maintain_ticker.py:145-151`); the `ensure_search_indexes`, `optimize_tables`, and
`rebuild_vector_indexes` calls are not, and `tick()`/the runner add nothing. So a disk-full
during compact, a retryable-conflict `RuntimeError` escaping the adapter's bounded retries,
a post-train commit conflict (single-attempt path, so *every* conflict escapes), or a
transient Postgres failure in `_stamp_success` terminates the process. `compose.yaml`
defines no restart policy on the `x-app` anchor, so `worker-maintain-p1` then stays dead
until an operator notices.

The design's failure table (§9) requires the opposite: "Disk full → **Fail the tick**; log
critical; do not spin", "Crash mid-optimize or mid-`create_index` → … next ticker tick
retries". The advisory lock itself is released correctly on the way out (the context manager
in `p1_maintain_lock.py` unwinds, and release failures invalidate the pooled connection), so
this is purely a liveness/containment gap — but it converts the designed "record one
conflict and leave" behavior into "die on first conflict". A secondary effect: one table's
failure aborts the whole pass, so the remaining tables in `P1_MAINTAIN_TABLES` are skipped
that tick.

Fix: wrap the per-table op in `_run_locked` (record `last_error` via the existing
`_stamp_error`, return a `skip`/`error` outcome), and wrap `tick()` in the runner so the
loop always reaches `sleep()`.

### B3 (PR #280 heuristic; made live by PR #283) — the ticker's `ensure` operation becomes unreachable once any stats row exists

`_needs_ensure` (`p1_maintain_ticker.py:180-193`) does not inspect indexes. It returns True
only when (a) no `p1_lance_table_stats` row exists yet, or (b) the stored `last_error`
happens to contain the substring `"index"`. The D93 trigger table binds ensure to real
discovery: "A contracted index is missing, or vector IVF min-row gate (256) is newly crossed
and no vector index exists — Ticker `list_indices`", and §5.3.1: "Ensure re-evaluates the
[min-row] gate on every maintain tick."

PR #283 turns this from a latent weakness into the common case: the writer hook
(`record_p1_vector_rewrites`) creates the stats row on the **first worker upsert**, and it is
wired unconditionally (not gated by `maintenance_enabled`). In any real deployment, all four
rows will exist long before an operator flips the gate on, so the ticker's first enabled tick
already sees `stored is not None` → ensure never runs. The branch's own test demonstrates
this inadvertently: in `test_vector_rewrite_plus_heavy_gate_retrains` the bump precedes the
first tick and the tick goes straight to retrain.

Concrete failures:
- A table that crosses the 256-row IVF gate after its stats row exists never gets a vector
  index from the ticker. Write-path ensure covers scalar/FTS but never builds vector
  indexes, so with `heavy_enabled=false` (the designed soak configuration: "compact/ensure
  only", §11) ANN search stays brute-force indefinitely.
- A dropped or wrong-type index is never repaired by the ticker (the port's
  `_ensure_matrix_indexes` handles both correctly — it is just never called).

Fix: make the ensure decision consult the port, not the stats row — e.g. extend
`TableMaintainStats` with per-matrix index presence/type (the adapter already calls
`list_indices()` in `maintenance_stats`), or simply call `ensure_search_indexes` every tick:
it is list-first and cheap when healthy, which is exactly why the design puts ensure on
"deploy, backfill end, maintain tick" cadence.

### B4 (PR #283) — the unindexed-ratio retrain trigger fires without a prior compact

§5.4.1 trigger 4 is: "**after a successful compact**, `unindexed/total ≥
heavy_rebuild_unindexed_ratio`" — a train-quality proxy that may fire only when a light pass
has already folded what it can. The implementation (`p1_maintain_ticker.py:237-239`) checks
the raw ratio whenever compact is not currently due. With defaults, a table with 500k rows
and 80k unindexed (16% ratio, below the 100k `optimize_unindexed_rows` compact threshold)
goes straight to a multi-hour `create_index(..., replace=True)` where a seconds-to-minutes
`optimize()` would have folded the tail into the existing index. That is the exact waste the
compact-first priority rule exists to prevent ("do not start a multi-hour IVF train on a
table that still needs a cheap compact").

Minor aggravator: the ratio numerator is `max(num_unindexed_rows)` across **all** indexes
(`maintenance_stats`, `lance.py:1118-1124`), so an FTS backlog can trip a *vector* retrain.

Fix: gate the ratio branch on evidence of a recent successful light pass (e.g. last
operation was compact, or `last_light_at` fresh and `last_unindexed_rows` still ≥ ratio), or
run compact instead when the ratio trips without one. Dormant behind `heavy_enabled=false`,
but heavy-trigger correctness is this PR's core deliverable, so blocking.

### B5 (PR #280; #283 inherits) — same-PR docs rule (D66) not met

`git diff origin/main...HEAD -- website/` is empty. PR #280 adds a user-facing CLI
subcommand (`rememberstack self-host maintain-p1`), a compose service
(`worker-maintain-p1`), and a new settings namespace
(`REMEMBERSTACK_P1_MAINTAIN_*`, including the two enable gates operators must eventually
flip). CLAUDE.md/D66: "Any PR that changes user-facing behavior — CLI commands, API/MCP
surface, configuration, mounts, connectors, deployment … updates the affected
`website/src/app/docs/**/page.mdx` in the same PR … and keeps `/docs/project-status`
truthful." The deployment page's service list, the CLI reference, and project-status all
need updating (documenting what ships: gates default off, ticker inert until enabled).
PR #283's env-driven thresholds and the writer hook behavior belong in the same pages once
B1's knobs exist.

## Other required corrections

### R1 (PR #280 → fixed in #283) — merge the stack together

PR #280 as it stands retrains on `changed_rows > 0` — one vector rewrite anywhere trips a
full IVF retrain once `heavy_enabled=true`, which is exactly the thrash D93 forbids
("Retrain … per-table frac/mass/growth"). PR #283 replaces it correctly. Do not merge #280
without #283 (or fold the `_needs_retrain` fix down into #280); nothing should exist on
`main` where enabling heavy triggers per-rewrite retrains.

### R2 (PR #283) — no baseline record-current initialization

§5.4 rules: "if `last_heavy_row_count` is null and a vector index already exists, set
baseline to current `count_rows()` **without** rebuilding (record-current)." Not
implemented. `_needs_retrain` substitutes live `stats.row_count` as the frac denominator
(reasonable) and correctly keeps the growth trigger dormant while the baseline is null — but
the baseline is then only ever stamped by a successful retrain, so on a long-lived store the
growth trigger stays dead until the first heavy fires via frac/mass, and the frac
denominator drifts with current table size instead of the trained size. Stamp the baseline
(record-current) when a vector index exists and `last_heavy_row_count` is null.

### R3 (PR #283) — per-table heavy thresholds are constants, not knobs

`HEAVY_CHANGED_ROW_FRAC`, `HEAVY_CHANGE_MASS`, `HEAVY_ROW_GROWTH_PCT`,
`HEAVY_UNINDEXED_RATIO` (`p1_maintain_ticker.py:26-44`) and `CHANGE_MASS_CHAR_CAP`
(`lance.py:46-52`) are module constants. §5.4 lists all of these as settings knobs, and both
the design and D93 stress the numbers are "starting points to be measured" on BEAM. As
shipped, tuning during PR5 soak requires a code change and redeploy. The values themselves
match the design table exactly, and the binding *ordering* (chunks strictest) is preserved —
only the configurability is missing. Move them into `P1MaintainSettings` (and a small
adapter-side settings hook or constructor argument for the caps).

### R4 (PR #280) — the ticker is a black box: outcomes discarded, no logs, no metrics

`run_p1_maintain_ticker` drops the `TickOutcome` tuple on the floor; there is no structured
log line and none of the §8 signals (`p1_lance_optimize_duration_ms`,
`p1_lance_rebuild_duration_ms`, `p1_lance_unindexed_rows`, `p1_lance_deferred_heavy`,
`p1_lance_awaiting_operator`, …) exist. §8 is explicit: "Ship metrics **before** enabling
the ticker in production", and §11 makes metrics a precondition of step 4
(`maintenance_enabled=true`). Gates default off, so this does not corrupt anything today —
but the PR5 soak cannot begin until at least structured logs (operation, table, duration,
skip reason — the data is already in `TickOutcome`/`MaintainReport`) and the §8 gauges land.
Flagging as required-before-enablement rather than blocking this merge.

## Nits

1. **Dead code from the write-path-optimize removal.** `_maintain_indexed_tail`
   (`lance.py:1271-1298`), its `_mutations_since_optimize` state (line 187), and the
   `_INDEX_OPTIMIZE_MUTATIONS` / `_INDEX_OPTIMIZE_TAIL_ROWS` constants have no callers left.
   Delete them — a dormant write-path `optimize()` helper is an invitation for the exact
   regression D93 K8 forbids. (The guard test `test_fact_writes_do_not_call_optimize`
   protects facts writes only.)
2. **Stale D91 references in PR #280's additions.** `profiles/selfhost.py:629` ("Run the
   D91 Lance ticker") and the CLI help at `:893` ("run the D91 Lance maintain ticker") point
   at the wrong decision — D91 is request-path metering on `main`. Same for the pre-existing
   headers in `p1_maintain_lock.py:1`, `model/p1_maintain.py:1`, `p1_locked_purge.py:1`, and
   the "D91 PR1" comments in `lance.py` (main-side; fine as a follow-up sweep, but the two
   lines this PR adds should say D93 now).
3. **Replayed upserts count as change-mass.** `_note_vector_rewrites` counts every row of
   every successful upsert batch, so an idempotent replay (crash-retry of the same batch)
   double-counts toward retrain. The binding rule ("increment only when the Lance vector
   column is written") is technically met — a matched merge does physically rewrite the
   vector — but §5.4.1 also forbids "no-op upserts". Comparing incoming vectors against
   stored ones would be disproportionate; document the approximation in the hook docstring
   instead.
4. **Writer hook failure fails the worker batch.** If the Postgres bump in
   `record_p1_vector_rewrites` raises after a successful Lance write, the worker unit fails
   and retries (double Lance write — idempotent; double mass — see nit 3). Acceptable since
   the worker needs Postgres to complete its unit anyway, but a `try/except` + log (counters
   are advisory discovery, not truth) would be more proportionate.
5. **Hook not gated by `maintenance_enabled`.** §5.4's knob table scopes the master gate to
   "writer enqueue"; the implementation bumps counters unconditionally. Accruing history
   while maintenance is off is arguably the better behavior (heavy has real debt to see when
   first enabled) at the cost of one Postgres row-upsert per worker batch forever — but it is
   a deviation; document the choice.
6. **`_UPSERT_SUCCESS` insert branch:** `CASE WHEN :clear_mass THEN 0 ELSE 0 END` — both
   arms are 0; write the literal. Also `_read_stats_row` runs twice per table
   (`_needs_ensure` + `_needs_retrain`); one read would do.
7. **Change-mass accrued during a retrain is zeroed by its success stamp.** Writers stay
   outside the lock (correct), so rewrites landing during a multi-hour train are counted,
   then wiped by `clear_mass`. The design's letter ("reset only after a successful retrain")
   is met; capturing the counter values at train start and decrementing by those would be
   more faithful to "change since the trained snapshot". Fine to leave; worth a comment.
8. **Ticker probes Lance every tick** instead of the durable-stats-first + `maintain_probe_min_s`
   floor of §5.4.1. At four tables and `poll_s=60` the cost is trivial and the effect matches
   the design's 60s probe floor; noting only for the record.
9. **Test gaps** (beyond the B1-dependent §16 case): no test that a manually-set
   `awaiting_operator` suppresses retrain, none for the mass or growth triggers or the
   chunks-strictest ordering, none for `heavy_enabled=false` with mass over threshold, and
   the ticker tests share `p1_lance_table_stats` state across tests (module-scoped schema,
   per-test truncate only touches `deployments`) — they pass in any order today, but the
   coupling is fragile.

## What is correct

Verified against the design, D93, and the eight strictness points, with tests run locally:

1. **Metadata-only updates never increment change-mass.** The hook is attached only to the
   four vector upsert paths (`lance.py:217, 325, 382, 1360`); `update_fact_metadata` keeps
   its dedupe → skip-unchanged (`fact_metadata_scalars_differ`) → batched matched-only
   `merge_insert` pipeline and never touches the hook.
   `test_vector_upsert_notifies_change_mass_metadata_does_not` proves both directions,
   including the exact mass (`min(len(text), cap)`), and passes.
2. **Retrain policy (post-#283) is per-table frac/mass/growth + ratio**, with the binding
   values and the binding chunks-strictest ordering; `awaiting_operator` (if set) and the
   24h floor are honored; the floor correctly does not block a first-ever heavy;
   compact-first priority is an `elif` chain matching §5.4.1's "at most one op per locked
   tick", and the "compact this tick, re-evaluate retrain next tick" behavior falls out
   naturally.
3. **Try-lock semantics.** The ticker holds `pg_try_advisory_lock` polling for only
   `lock_try_ms=50ms` — an effective try-lock; a held lock yields `skip/lock_busy`
   (`test_busy_table_lock_skips_without_waiting` passes against real Postgres). Writers
   never take the maintain lock anywhere; `merge_insert` conflict handling stays on the
   writer side with bounded jittered retries.
4. **Forget bounded-waits the same lock.** `LockingP1Purge` waits up to 30s on the identical
   key material (`p1-lance-maintain:<resolved root>:<table>` — one hash function, one
   namespace across ticker/purge/finalizer), locks only tables with nominated ids in fixed
   sorted order, raises `P1MaintainLockTimeout` so the forget step retries, and
   `delete` + `optimize(cleanup_older_than=0, delete_unverified=True)` is reachable only
   under that held lock. The ticker additionally skips the whole estate while a forget
   manifest is open.
5. **Gates default off and are enforced.** `maintenance_enabled=False`, `heavy_enabled=False`
   in `P1MaintainSettings`; a disabled ticker returns pure skips without touching the port
   (`test_gates_off_skip_every_table`); heavy is separately gated in the op chooser.
6. **Catalog contract and migration.** `p1_lance_table_stats` added → 72 tables; constraint
   deltas exact (+3 CHECK, +1 PK, no FKs — deployment correctly absent from the key);
   `DECISION_OBJECTS["D93"]` added while D91 stays the metering objects; inline column
   comments are materialized into real `COMMENT ON COLUMN` by `apply_ddl`; CI asserts head
   `p9_12_0033`; migration lifecycle tests pass against Postgres 16. The stats schema
   carries every §5.6 field, including the escalation and gate columns (awaiting B1's code).
7. **Adapter boundary.** `lance.py` imports no spine/SQLAlchemy/Postgres; the hook is an
   optional `Callable[[str, int, float], None]` defaulting to `None`, injected only by the
   worker profile. Read-only compositions (API search index, purge index, the ticker's own
   port instance) correctly omit it; all Lance-writing workers (chunks, claims, facts,
   entity resolver) share the hooked instance. Root-key identity (`Path.resolve()`) matches
   across hook, ticker, and lock.
8. **No write-path optimize/create_index-heavy under `label_lock`.** Write paths do only
   design-sanctioned list-first scalar/FTS ensure; vector `create_index` exists solely in the
   port's ensure/rebuild; heavy uses `replace=True` with exactly one train attempt per tick
   (no 8× retry loop); light `optimize` keeps the short bounded retry it is allowed.
   `maintenance_stats` correctly uses `Table.stats().fragment_stats` and per-index
   `num_unindexed_rows` (verified present on pinned `lancedb==0.34.0`).
9. **Change-mass text is the embedded text per table** — chunks/claims `text`, facts `label`,
   entities `canonical_name` (the resolver embeds exactly `reference.name`), with the binding
   per-table caps; counters accumulate atomically via upsert-add and reset only on retrain
   success; `last_heavy_row_count` stamps the pre-op snapshot.
10. **The rejected-alternative boundary is respected**: no `maintain_p1_index` stage, no
    ledger units, no reclaim/heartbeat, ticker absent from `_expected_components`/
    `UNLANED_STAGES`; compose runs it as a command (`maintain-p1`), not `worker --stage`;
    lock release failures invalidate the pooled connection so a leaked session lock cannot
    outlive its process silently.
