# Implementation review: D93 ticker + writer change-mass

**Reviewer:** Codex (`gpt-5.6-sol`)  
**Date:** 2026-08-14  
**PRs:** #280, #283  
**Commit:** `813660f956ba6aeef3ed8d72d2ca92e82d5802e3`

## Verdict

REQUEST_CHANGES

The stack has the right overall decomposition, catalog identity, default-off
gates, and adapter callback seam. It is not yet a safe implementation of D93.
The writer hook over-counts idempotent/no-op upserts, the heavy defer and
`awaiting_operator` protocol is only schema, the ticker waits on its supposed
try-lock, ensure cannot detect ordinary index drift or the IVF row-gate
crossing, and heavy completion can erase concurrent writer change-mass. The
current fact writer also still reaches `create_index` while `label_lock` is
held.

## Blocking findings

1. **PR #283 — change-mass counts submitted rows, not rows whose vector
   actually changed.** Every successful `upsert_chunks`, `upsert_claims`,
   `upsert_facts`, and `upsert_entities` calls `_note_vector_rewrites` with all
   input texts (`lance.py:217-219`, `:325-327`, `:382-384`, `:1360-1362`). The
   hook then reports `len(texts)` and their full capped mass without comparing
   the existing vector/text or using a merge result (`:1439-1445`). Replaying
   an identical idempotent upsert therefore grows both durable counters and can
   trigger a heavy retrain without any semantic vector change. The new test at
   `test_lance_retrieval.py:511-548` proves that metadata merge does not invoke
   the hook, which is good, but does not cover the required no-op-upsert case.
   Determine the actually changed vector rows before/while merging and report
   only that subset; add repeat-identical coverage for all four tables.

2. **PR #283 — the sustained-write/conflict protocol and writer quiet gate are
   not implemented, so `awaiting_operator` is unreachable.** The migration
   adds `rate_defer_count`, `conflict_defer_count`, `first_defer_at`,
   `operator_state`, and `writer_gate`
   (`p9_12_0033_p1_lance_maintain.py:31-35`), but runtime code only reads
   `operator_state` (`p1_maintain_ticker.py:204-206`). There is no write-rate
   test, no pure rate-defer transition, no one-attempt conflict handling, no
   long defer, no counter/age escalation to `awaiting_operator`, and no code
   anywhere that reads `writer_gate`. A heavy conflict escapes
   `rebuild_vector_indexes` at `:169-171` instead of being recorded and deferred.
   Writers likewise do not re-read a per-table hold before each mutating batch,
   so an operator cannot create D93's supported quiet window. Implement the
   complete state machine and a composition-level optional gate callback (the
   Lance adapter must remain free of spine/Postgres imports), with rate,
   conflict, age-escalation, suppression, and force-after-quiet tests.

3. **PR #280 — the ticker does a bounded wait instead of a try-lock.**
   `_tick_table` calls the polling `hold_p1_table_maintain_locks` helper with a
   configurable timeout (`p1_maintain_ticker.py:130-141`); the setting defaults
   to 50 ms and forbids zero (`:56-57`). That helper repeatedly polls and sleeps
   until its deadline. D93 requires one `pg_try_advisory_lock` attempt and an
   immediate skip when purge or another ticker owns the table. Add a true
   single-attempt ticker API/test. Keep hard-forget on the existing bounded-wait
   wrapper; `LockingP1Purge` correctly has different semantics.

4. **PR #280 — ensure discovery stops after the first stats row.**
   `_needs_ensure` returns true only when no control row exists or `last_error`
   happens to contain the word `index` (`p1_maintain_ticker.py:180-193`).
   `TableMaintainStats` carries no contracted-index health, so a dropped index,
   wrong index type, or a table that grows from below 256 rows past the IVF gate
   is invisible after the first ensure stamp. In the gate-crossing case the
   ticker can leave the table permanently without IVF. Discover matrix health
   from `list_indices` on each eligible probe (or expose it in the stats
   contract), choose ensure first, and test both dropped/wrong-type repair and
   below-to-above-min-row growth through the ticker rather than only through the
   adapter.

5. **PR #280, made race-critical by PR #283 — retrain is stamped successful
   without validating the report, and its reset can discard concurrent writer
   mass.** `_run_locked` always calls `_stamp_success(..., operation="retrain")`
   after the port returns (`p1_maintain_ticker.py:169-176`). The port can return
   `skipped="below_min_rows"` or `skipped="no_vector_column"`
   (`lance.py:1049-1080`), yet the ticker still stamps `last_heavy_at`, advances
   the baseline, and clears both counters. In addition, `_stamp_success` passes
   the pre-operation snapshot (`p1_maintain_ticker.py:266-284`) and the SQL
   resets the *current* counter values to zero (`:375-382`). Writers deliberately
   remain outside the maintain lock; a writer hook committed while a long
   retrain is running can therefore be erased. Inspect the returned table
   outcome, stamp post-operation stats only for an actual successful train, and
   preserve increments after the heavy decision (for example with an
   operation-generation fence or atomic subtraction of the captured due
   counters). Add skipped-train and deterministic concurrent-bump tests.

6. **PR #283 — two heavy discovery paths do not implement the binding policy.**
   When `last_heavy_row_count` is null, `_needs_retrain` substitutes the current
   live row count but never persists the required record-current baseline
   (`p1_maintain_ticker.py:218-225`). Consequently row-growth can never fire
   until some other trigger first causes a retrain. The final raw
   `unindexed_rows / row_count` check (`:237-239`) can also trigger a multi-hour
   retrain without the required preceding successful compact; on a table below
   the 100,000-row light threshold, 15% unindexed immediately takes the heavy
   path. Persist baseline initialization, preserve the per-table frac/mass/
   growth thresholds, and make the unindexed-ratio signal explicitly a
   post-compact observation.

7. **PR #283 stack-level writer correction — fact writes still call
   `create_index` under `label_lock`.** `LabelFactsHandler` holds `label_lock`
   across both `upsert_facts` and `update_fact_metadata`
   (`workers/p1.py:202`, `:278-294`). Those adapter methods synchronously call
   scalar/bitmap join-index ensure (`lance.py:385-394`, `:404`, `:427-432`),
   whose missing-index path calls `table.create_index` (`:1242-1261`). This
   violates the explicit no-maintenance-on-the-label-path rule even though
   write-path `optimize()` has been removed. This behavior predates commit
   `813660f9`, but #283 is the writer integration layer and the completed stack
   must move index creation to setup/finalizer/ticker or to an explicit
   pre-label-lock preparation step.

## Other required corrections

1. **PR #280 — expose the gates through Compose while keeping them false by
   default.** `P1MaintainSettings` correctly defaults both gates to false, but
   the shared Compose environment (`compose.yaml:13-50`) does not pass
   `REMEMBERSTACK_P1_MAINTAIN_MAINTENANCE_ENABLED` or
   `REMEMBERSTACK_P1_MAINTAIN_HEAVY_ENABLED`, and `.env.example` does not
   document them. The standard `worker-maintain-p1` service is therefore an
   always-disabled sleeper with no normal rollout switch. Add explicit
   `${...:-false}` pass-through and operator-facing documentation.

2. **PR #280 — keep the ticker alive and record operation failures.** Only the
   stats probe is caught (`p1_maintain_ticker.py:145-151`). Ensure, compact, or
   retrain exceptions escape the tick; `run_p1_maintain_ticker` has no per-tick
   guard (`profiles/selfhost.py:644-646`), so an ordinary Lance error terminates
   the continuous process instead of recording `last_error` and retrying on a
   later tick. Heavy commit conflicts need the specialized behavior in blocker
   2; other operation errors still need durable error/logging behavior without
   silently killing the loop.

3. **PR #280 — use durable stats first and finish the required observability.**
   The ticker probes live Lance at the start of every table tick and only then
   reads PostgreSQL, rather than consulting `p1_lance_table_stats` first and
   probing on the stale/missing cadence. There is no `maintain_probe_min_s` and
   no implementation of the required fragment/unindexed/duration/conflict/
   deferred/`awaiting_operator` metrics or structured operation logs. The run
   loop currently discards every `TickOutcome`.

4. **PR #280 — add real column comments to the migration/catalog contract.**
   `p9_12_0033_p1_lance_maintain.py` has one `COMMENT ON TABLE`; inline `--`
   annotations do not create PostgreSQL column comments. D93 requires table and
   column comments, while the catalog checker only enforces a global
   `commented_columns >= 300` floor and can miss this entire table. Add
   `COMMENT ON COLUMN` statements and an exact contract assertion for the D93
   relation.

5. **Both PRs — add acceptance coverage for the blocking paths.** Required
   tests are missing for true no-wait lock behavior, ticker index drift and IVF
   gate crossing, baseline initialization and row growth, compact-before-ratio,
   skipped heavy without reset, concurrent writer bump versus heavy reset,
   rate/conflict/age escalation, `awaiting_operator` suppression, writer-gate
   hold/release, and absence of every write-path `create_index`/`optimize` call
   under `label_lock`. The current eval-banana catalog also has no D93-specific
   acceptance check.

## Nits

1. **PR #280 — finish the D93 renumbering.** The new profile runner docstring
   and CLI help still call this the D91 ticker (`profiles/selfhost.py:629`,
   `:892-894`), while D91 is request-path metering. Rename the remaining D91
   ticker test fixtures/slugs as well.
2. **PR #283 stack — remove the dead `_maintain_indexed_tail` method and its
   process-local mutation constants/cache.** It is currently unreferenced, but
   its docstring still says maintenance stays on the write path
   (`lance.py:1271-1298`), directly contradicting D93 and inviting regression.
3. Prefer a frozen Pydantic model or frozen dataclass for `TickOutcome`,
   consistent with `MaintainReport` / `TableMaintainStats`, instead of a
   mutable ad-hoc record.

## What is correct

- **PR #280:** the schema/catalog identity is correct: migration
  `p9_12_0033` follows `p9_11_0032`, the catalog expects 72 tables, D93 maps to
  `p1_lance_table_stats`, and D91 remains mapped to the metering objects.
- **PR #280:** `p1_lance_table_stats` has the correct physical
  `(lance_root_key, table_name)` primary key, the four-table constraint, the
  default `writer_gate='run'`, and the nullable `awaiting_operator` state.
- **PR #280:** continuous maintain is a standalone `maintain-p1` ticker/Compose
  service, not a pipeline stage or ledger unit. No maintain claim, reclaim, or
  heartbeat machinery was introduced.
- **PR #280:** the master and heavy gates default false. The ticker skips an
  open forget, prioritizes ensure then compact then retrain, and runs at most
  one selected operation for a table in a tick.
- **PR #280 / existing lock integration:** hard-forget still bounded-waits on
  the same table advisory-lock namespace around delete plus
  `optimize(delete_unverified=True)`; writers do not take that lock.
- **PR #283:** the adapter boundary is sound: `LanceChunkIndex` accepts an
  optional callback and imports neither spine nor PostgreSQL. PostgreSQL wiring
  stays at the self-host composition boundary.
- **PR #283:** eligibility-only `update_fact_metadata` never invokes the vector
  hook. The per-table character caps and frac/mass/growth ordering are present,
  with chunks more sensitive than claims/facts; the old `changed_rows > 0`
  retrain trigger has been removed.
- The maintenance adapter correctly separates light `optimize()` from heavy
  IVF rebuild and uses `create_index(..., replace=True)` for the latter. No
  active writer call site invokes `optimize()`.
- The physical self-host path remains `SelfHostSettings.lance_root` with
  `/var/lib/rememberstack/lance` under the Compose `app-state` volume.

Validation run for this review:

- `uv run ruff check ...`, `ruff format --check ...`, and focused `pyright`:
  pass.
- Focused pytest across Lance retrieval, ticker, lock, and migration tests: 16
  passed, 14 skipped because PostgreSQL settings were unavailable; the skipped
  database-backed proofs were not treated as passing evidence.
- `eval-banana validate`: all 84 check definitions validated.
