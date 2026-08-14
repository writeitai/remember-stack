# Design re-review (r2) — D93 P1 Lance bulk writes and two-layer maintenance

**Reviewer:** claude-opus
**Date:** 2026-08-13
**Round:** 2 (after dual REQUEST_CHANGES: claude-opus + codex-sol)
**Branch:** `feat/d90-entity-obs-flush-fanout` (docs untracked)
**Under review:**
`plan/designs/p1_lance_maintenance_design.md` (revised),
`plan/analysis/p1_lance_maintenance_analysis.md` (revised, non-binding)
**Prior reviews:**
`design/reviews/REVIEW_claude-opus_p1_lance_maintenance_design_2026-08-13.md` (B1–B12, N1–N11),
`design/reviews/REVIEW_codex-sol_p1_lance_maintenance_design_2026-08-13.md` (P1.1–P1.6, P2.1, nits 1–7)
**Re-verified against:** `src/rememberstack/adapters/selfhost/lance.py`,
`src/rememberstack/spine/work_ledger.py`,
`src/rememberstack/spine/catalog_contract.py`,
`src/rememberstack/spine/backfill.py`,
`src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py`,
`src/rememberstack/profiles/selfhost.py`, `src/rememberstack/profiles/selfhost_forget.py`,
`src/rememberstack/adapters/selfhost/queue.py`, `src/rememberstack/ports/p1_index.py`,
`compose.yaml`, plus fresh live probes of pinned `lancedb==0.34.0`

## Verdict

**APPROVE_WITH_NITS**

All twelve blocking items from my first review (B1–B12) are closed, and all
eleven nits (N1–N11) are absorbed. The structural problems are gone: the
maintenance unit is now bound to the physical `(lance_root, table, mode)` grain,
the stage is unlaned with `backfill` forbidden for a stated reason,
`_expected_components` is explicitly excluded with the readiness reason, the
false "mirror processing_state discipline" claim is deleted and replaced with a
real stage-scoped reclaim, skip-unchanged is binding design content with the
delete-and-reinsert property stated, and §5.3.1 is a genuine per-table index
matrix. §5.5.1 now states one protocol instead of four.

Six items remain open. Two of them (R1, R2) are **binding statements that are
wrong as written**, in sections that were rewritten to close prior P0/P1 items —
each is a one-to-three-sentence correction, not a reopened decision. They should
be fixed in the design text **before** D93 is entered in `decisions.md`, not
during implementation. The rest are P2/nits.

## Disposition of prior blocking items (B1–B12)

| # | Prior issue | Status | Where closed / residual |
| --- | --- | --- | --- |
| **B1** (P0) | Maintenance unit deployment-scoped; maintained objects are not | **Addressed** | §1.4 makes physical grain binding; §5.5.1 keys units on `(lance_root_key, table_name, mode)`; §5.6 keys stats on `(lance_root_key, table_name)` and forbids `deployment_id` in the growth key; §5.7 rule 1 keys the lock on `(lance_root_key, table_name)`; §4.2 states shared-root multi-deployment as a non-goal; §12 records the rejected shape. `deployment_id` retained for routing/attribution only, as asked. |
| **B2** (P0) | `_expected_components` wiring makes every version permanently not-ready | **Addressed** | §5.5.5 binds "**do not add**" with the per-version readiness reason; §5.5.1 states the `component_version` is logical-only and needs no `pipeline_component` enum value; §5.5.5 names `_SUPPORTED_WORKER_STAGES` + `_handler` as the changes that *are* required, and routes maintenance health to §8 metrics instead; §12 and K5 record it. Verified: `_expected_components()` at `profiles/selfhost.py:877` is consumed per `document_version`. |
| **B3** (P0) | Lane left open; `lane=backfill` deadlocks the drain barrier | **Addressed** | §1.5 binds unlaned; §5.5.1 `lane = NULL always`; §5.5.5 binds `UNLANED_STAGES += maintain_p1_index` and `worker_loop` passing `lane=None` rather than hardcoded `STEADY`; §5.5.6 states the deadlock reason and makes a non-null lane a hard error. Verified accurate: `lane_is_valid` is `(lane is None) == (stage in UNLANED_STAGES)` (`catalog_contract.py:296-302`) and is enforced at `enqueue_on` via `_require_valid_lane` (`work_ledger.py:1265`), so the "hard error" claim holds once the stage is added. |
| **B4** (P0) | §9 rests on a non-existent reaper; coalesce-on-`running` turns one crash into permanent silent stall | **Addressed in substance — see R1 for a defect in the mechanism** | The false premise is deleted. §5.5.2 takes *both* options I offered: coalesce on `pending`/retryable `failed` only, `rerun_requested` for running races, an explicit stated idempotency argument for why a double-run is safe, **and** a stage-scoped `reclaim_stale_maintain` bound to ship with D93 (§4.2 scopes the general reaper out; §9 and §12 are consistent; §15 puts reclaim in PR3, before the PR4 worker). The convergence argument holds. The **SQL sketch** implementing it does not — R1. |
| **B5** (P1) | `delete_unverified=True` becomes a corruption hazard once a second process maintains the dataset | **Addressed** | §5.7 rule 5 names the corruption precondition (not just disk), binds purge to acquire the same table maintain lock, permits `cleanup_older_than=0` + `delete_unverified` only while holding it, states the bounded-wait/fail-with-retry behaviour, and notes `ForgetInProgressError` gates only *new* claims; §6.2 and §9 repeat it; §12 records the rejected "drop `delete_unverified`". Residual: which layer takes the lock — R3. |
| **B6** (P1) | §5.5.1 is a transcript of a decision, not a decision (Rule 1) | **Addressed** | §5.5.1 is now one identity table + one payload + one unit table, with no self-argument. The `content_hash` uniqueness confusion is gone from the body and correctly parked in §12 ("`content_hash` is not in the ledger unique key"). |
| **B7** (P1) | Pervasive `v1` / phase framing (Rule 2) | **Addressed** | `grep -nE "\bv1\b\|v1\.1\|MVP\|Phase 1\|for now\|defer(red\|ral)"` over design + analysis returns zero phase hedges. Surviving "later" instances are genuine non-goals or documented alternatives in the shape Rule 2 permits (§1.5 stage split, §4.2 estate-wide reaper, §12 S3). §5.4 numbers are labelled "measure; not sacred". |
| **B8** (P1) | Batching fixes commits/fragments but not tail growth; churn never modelled | **Addressed** | §1.2 makes skip-unchanged binding and says why ("load-bearing for unindexed-tail growth, not polish"); §3 and §5.2.1 state the delete-and-reinsert property explicitly; §5.2.2 defines the comparison set and the "if every candidate is skipped, do not open a merge at all" rule; §5.2.2.3 states the churn budget the §5.4 thresholds assume; K2 and §16 carry it. |
| **B9** (P1) | `ensure_search_indexes()` made binding without an index set | **Addressed** | §5.3.1 is a complete per-table column → index-type matrix with roles, an explicit "as-built gaps this matrix closes" list (`facts.fact_id`, entities, `facts.kind` call-order dependence), min-row gates, and the `Table.stats()` fragment source. §5.3 flags the entities behaviour change at the backfill barrier as reviewed rather than discovered. Matrix re-verified against `lance.py` — accurate except two wording nits (R7, R8). |
| **B10** (P2) | Duplicate join keys now fail the whole batch | **Addressed** | §5.2.1 binds dedupe on `(deployment_id, kind, fact_id)` with last-write-wins tie-break, states the engine's hard error, and §16 adds the test. |
| **B11** (P2) | Migration list omits the executable catalog contract | **Addressed** | §6.3 lists `EXPECTED_TABLES`, per-contype `EXPECTED_CONSTRAINT_COUNTS`, named indexes, comment counts, and `verify_schema_absent`; §15 PR3 validates "Migration + catalog verify"; §5.5.1 answers the `pipeline_component` question (deliberately not registered). Verified: `EXPECTED_ENUMS` (`catalog_contract.py:37`) lists enum *types* only, so enum-value additions still need no contract edit — the design's claim is right. |
| **B12** (P2) | No same-PR docs row (D66) | **Addressed** | §15 PR4 carries "**docs** (`website/src/app/docs/**` deployment/configuration/troubleshooting/project-status) same PR (D66)"; §11 item 2 restates the obligation. |

**Prior nits N1–N11: all absorbed.** N1 (§1.3/§5.3 deprecated `retrain`), N2
(§5.3/§8 `Table.stats()` as the named source, `num_small_fragments` preferred),
N3 (§5.2.3 one `stats()` call replaces the `list_indices`+`index_stats` loop),
N4 (§5.4 labels `optimize_unindexed_rows` a vendor rule of thumb and deletes
`optimize_mutations` as policy), N5 (§11 item 3 binds the PR1→PR4 interim), N6
(§5.5.3 notes the historical class name; §6.1 states the port is
deployment-free), N7 (§5.5.4 states the `ForgetInProgressError` claim gate), N8
(§16 adds preservation, dedupe, and merge-count tests with the
`test_lance_retrieval.py` anchor), N9 (Option A bound, Option B moved to §12),
N10 (analysis §1.4 now names all eleven compose workers), N11 (analysis §7 states
the verified partial-merge behaviour).

**Codex findings.** P1.1 (route), P1.2 (self-seed edge + coalesce state machine),
P1.3 (enqueue-only), P1.4 (heartbeat → reclaim), P1.6 (gates before activation),
P2.1 (index matrix) and nits 1–7 are all closed. P1.5 (heavy progress guarantee)
is closed on serialization scope — §5.7 rule 1 binds one exclusive lock per
physical table for light *and* heavy — but its second half, "how does a heavy run
ever obtain a quiet commit window", is answered by reusing a retry constant that
does not fit the operation: see **R2**.

---

## New / remaining open issues

### R1 (P1) — §5.5.2's reclaim SQL violates two live `processing_state` CHECK constraints, and `WorkLedger.fail()` already does this correctly

**Anchor:** design §5.5.2, the `reclaim_stale_maintain` SQL block.
**Code:** `p0_02_0002_infrastructure_registries.py:96-101`:

```sql
CHECK (status <> 'failed' OR attempts < max_attempts),
CHECK (
  (status = 'failed'  AND defer_reason = 'retry_backoff') OR
  (status = 'pending' AND (defer_reason IS NULL OR defer_reason IN ('scheduled','budget'))) OR
  (status NOT IN ('pending','failed') AND defer_reason IS NULL)
)
```

A `running` row has `defer_reason IS NULL` (third arm). The design's UPDATE sets
`status='failed'` and touches neither `defer_reason` nor `not_before`, so the
resulting row satisfies **no** arm — the statement raises a check violation and
the reclaim path never completes. Separately, a zombie killed on its final
attempt has `attempts = max_attempts` (`_CLAIM_START` increments on every claim,
`work_ledger.py:1402-1412`), so `status='failed'` also violates the first CHECK;
the correct terminal transition there is `dead_letter`, which the design does not
mention. That branch is a design decision, not a column-name detail — it decides
whether a repeatedly-crashing heavy rebuild retries forever or lands in the DLQ.

The design hedges with "implementation reuses the same fail/requeue path if one
exists". **It exists and it is exactly right:**
`WorkLedger.fail(processing_id, error, retryable)` (`work_ledger.py:632-675`)
asserts `status == 'running'`, and branches to `_FAIL_RETRY`
(`status='failed'`, `defer_reason='retry_backoff'`,
`not_before = now() + backoff`) when `attempts < max_attempts`, else to
`_FAIL_DEAD_LETTER`. It also documents the caller's obligation to re-announce
through the queue port.

**Required change.** Replace the hand-written UPDATE in §5.5.2 with: "select
stale `running` maintain rows, then call the existing
`WorkLedger.fail(..., retryable=True)` per row and re-announce via the queue
port; `fail()` already routes attempt-exhausted rows to `dead_letter`." State
what a dead-lettered maintain unit means for coalesce (per §5.5.2's own
definition of *open*, a `dead_letter` unit is not open, so the next tick enqueues
a fresh one — that is the right behaviour, and saying it closes the loop B4
opened). Delete the invalid SQL rather than leaving it as the binding sketch.

### R2 (P1) — §5.7 rule 6 binds a sub-second retry constant to a multi-hour operation, so retry exhaustion re-does the whole rebuild up to 24 times

**Anchors:** design §5.7 rules 3 and 6 ("Reuse `_LANCE_COMMIT_RETRIES` + jitter
for optimize, merge, and **create_index**"), §6.2, §9 row "Concurrent writer
during optimize/rebuild".
**Code:** `lance.py:58` `_LANCE_COMMIT_RETRIES = 8`; `lance.py:933-937`
`_pause_before_retry` sleeps `min(0.05 * 2**attempt, 1.0) + jitter` — the entire
eight-attempt pause budget is **≈3.6 seconds**. `WorkLedgerSettings`
(`work_ledger.py:57-58`) then retries the unit with
`retry_backoff_base_s=2.0`, `retry_backoff_max_s=60.0`, under the default
`max_attempts = 3` (`p0_02_0002:...max_attempts smallint NOT NULL DEFAULT 3`).

That constant is correctly sized for a merge or a small `optimize`, where a
conflict costs milliseconds of lost work. A heavy `create_index` on a
design-scale `facts` table costs minutes to hours. Wrapping it in the same
policy means: conflict at minute 40 → sleep 0.05 s → **re-run the entire
rebuild** → conflict again → … eight times per claim, three claims per unit.
Worst case is ~24 full retrains and a day of CPU before the unit dead-letters,
under exactly the continuous-ingest condition §5.7 rule 3 says it expects. The
design acknowledges "eventual success is expected once a quiet commit window
appears" but binds a mechanism whose retries are ~0.05–1 s apart, i.e. a
mechanism that never waits for a quiet window.

This is the unresolved half of Codex P1.5. §5.7 rule 3 explicitly declines a
quiesce barrier, which is a defensible call — but then the retry policy has to
carry the whole progress argument, and this one cannot.

**Required change.** Split the retry policy by operation cost in §5.7 rule 6:
keep `_LANCE_COMMIT_RETRIES` + sub-second jitter for merge/optimize; give heavy
`create_index` its own small attempt count with a backoff proportional to the
observed rebuild duration (or 1 in-process attempt and let the ledger's
`not_before` backoff own re-scheduling, which is where a quiet window is
actually more likely). Either way, state the intended worst-case wasted work,
and add it to the §16 "Concurrent upsert + heavy" expectation — "eventually
succeeds or unit retries" does not currently distinguish this failure.

### R3 (P2) — Lock acquisition has three callers and no bound owner; `BackfillFinalizer` takes no lock at all

**Anchors:** §5.5.3 (handler acquires the lock), §5.7 rule 5 (purge acquires it),
§5.5.4 table ("Backfill finalizer | After drain: ensure + heavy via same port"),
§6.2 ("Purge path takes table maintain lock").
**Code:** `LanceChunkIndex.__init__(*, root: Path)` (`lance.py:74-79`) holds a
Lance connection and nothing else — it has no `Engine`, so
`_purge_table_rows` (`lance.py:1095-1106`) cannot take a Postgres advisory lock
where the design places it. `BackfillFinalizer.__init__` *does* hold an engine
(`backfill.py:100-107`) and calls `build_search_indexes()` directly on the
deployment-free port (`backfill.py:125`), outside any ledger unit — so under the
revised §5.3 (`build_search_indexes()` = ensure + heavy on all four tables) the
finalizer performs a full four-table IVF retrain with **no** table lock, able to
run concurrently with a light `optimize()` from the maintain worker (violating
§5.7 rule 1) or with a purge holding `delete_unverified=True` (re-opening B5).

The lock is implementable at every site — `selfhost_forget.py:60-90` composes the
forget handler with an `engine` in scope, and the finalizer already has one — so
this is a placement statement, not an architecture problem.

**Required change.** Name the layer that owns lock acquisition in §5.7 (one
sentence), and make it cover all three entry points: maintain handler, purge, and
`BackfillFinalizer.build_search_indexes`. If the lock stays outside the Lance
adapter, say that `_purge_table_rows`' `delete_unverified` call is only reachable
through a caller that holds it.

### R4 (P2) — `ensure_maintain_due` has no probe floor, and `queue_wake` is a single global channel

**Anchors:** §5.5.4 "Self-seed execution edge (binding): … each loop iteration of
`SelfHostWorkerLoop.drain_due` / `run_for` … calls `ensure_maintain_due`", whose
body probes `read_or_probe_stats(table)` for every present table.
**Code:** `_WAKE_CHANNEL = "queue_wake"` is one estate-wide channel
(`queue.py:28`), fired by an `AFTER INSERT` trigger on **every**
`processing_state` row (`p0_02_0002:114-126`). `run_for` re-enters `drain_due`
on every notification and on a `_fallback_poll_s = 30.0` timeout
(`queue.py:120-136`). So under ingest the maintain loop wakes once per enqueue
anywhere in the estate — hundreds per minute at BEAM cadence — and each wake
would run `reclaim_stale_maintain` plus a Lance stats probe on four tables.

`maintain_poll_hours = 1` governs *when a unit is enqueued*, not how often the
loop probes. The design's `read_or_probe_stats` name gestures at reading
durable §5.6 stats first but does not bind it, and §5.6 stats are only "updated
at end of each maintain unit" — so with gates on and no units running there is
nothing fresh to read.

**Required change.** Bind the cheap path: `ensure_maintain_due` reads
`p1_lance_table_stats` only, and probes Lance no more often than a stated
interval (a `maintain_probe_min_s` knob, or gate the probe on
`now() - last_light_at > maintain_poll_hours`). Also bind a floor on
`reclaim_stale_maintain` frequency for the same reason.

### R5 (P2) — The coalesce constraint's "partial unique index" alternative is not expressible in PostgreSQL

**Anchor:** §5.5.1, "**Unique open-unit constraint (coalesce)** … **or** a
partial unique index on `(lance_root_key, table_name, mode)` filtered to units
whose ledger status is `pending`/`failed`."

A partial index predicate may only reference columns of the indexed table.
`p1_maintain_units` as specified has no status column — the status lives on
`processing_state` — so this alternative cannot be built. The other stated
option (advisory xact lock / `SELECT … FOR UPDATE` inside
`enqueue_p1_maintain`) is implementable and sufficient. This matters because
Codex P1.2 specifically asked for the constraint to be bound, and offering an
impossible option invites an implementer to discover it in PR3.

**Required change.** Drop the impossible alternative, or make it possible by
giving `p1_maintain_units` its own `open boolean` / `state` column maintained in
the same transaction as the ledger transition, and then state which of the two
is binding.

### R6 (P2) — `maintain_running_stale_s = 7200` can be shorter than a legitimate heavy rebuild, and reclaim carries no fencing

**Anchors:** §5.4 (`maintain_running_stale_s` default 2h), §5.5.2 step 5 ("No
same-thread heartbeat … Staleness is wall-clock on `started_at`").

At the scale this design targets, a full IVF retrain of a multi-GB `facts` table
can legitimately exceed two hours. Reclaim then fails a *live* unit; a successor
is claimed by the (single-replica) maintain worker, which blocks on the table
advisory lock the live run still holds — consuming the only maintain slot — and
when the original finishes it calls `complete()` on a ledger row another attempt
now owns. Lance-side safety holds (the lock genuinely serializes, and the design's
idempotency argument covers the double-run), so this is queue-health and
ledger-attribution damage rather than corruption — but it is the predictable
steady state once heavy work is slower than the cutoff.

**Required change.** One paragraph in §5.5.2: state that
`maintain_running_stale_s` must exceed the measured p99 heavy duration (and that
§8's `p1_lance_rebuild_duration_ms` is how you set it), **or** gate reclaim on
the table advisory lock being unheld — a free lock is direct evidence the owner
is gone and needs no timer at all. The second is strictly better and costs one
`pg_try_advisory_lock` per stale candidate.

---

## Nits (non-blocking, no re-review needed)

- **R7 — §5.3.1 `chunks.deployment_id` role.** Listed as "tenant filter + join",
  but the chunks merge key is `["chunk_id", "policy_generation",
  "embedder_generation"]` (`lance.py:107`) — `deployment_id` is a filter column
  only. The BTREE is still correct; the role text is not.
- **R8 — "entities has no ensure path" is slightly overstated.** §5.3.1's
  as-built gaps say entities has no ensure path; `search_entities_scored`
  actually ensures `deployment_id` on the **read** path (`lance.py:777`).
  `upsert_entities` ensures nothing (`lance.py:958-973`) and
  `build_search_indexes` skips the table (`lance.py:829-833`) — both accurate.
  Worth one clause, and worth saying whether the read-path ensure survives once
  `ensure_search_indexes()` owns the matrix (it becomes an upgraded-store
  fallback, or it goes).
- **R9 — `content_hash` is `NOT NULL`.** §5.5.1's ledger identity table omits it;
  `processing_state.content_hash text NOT NULL` (`p0_02_0002:82`). Say what a
  maintain unit stamps there (a stable per-unit string is fine — it is carried
  "for diagnostics/replay" per the table comment, not for uniqueness).
- **R10 — `heavy_enabled` does not gate the backfill finalizer.** §5.5.3 skips
  heavy in the handler when `heavy_enabled=false`, but §5.5.4 routes the
  finalizer straight at the port, so a backfill finalize performs a full
  four-table retrain with the shipped default gates off. That is probably
  intended (the barrier exists to build indexes), but the gate's scope should be
  stated so nobody "fixes" it later.
- **R11 — worth one line in §5.3 and one test in §16:** today
  `build_search_indexes()` is **not** re-runnable. Verified on the pinned
  version: a second `create_index` on an existing index raises
  `LanceError(Index): Index name 'vector_idx' already exists … use replace=True`,
  and `_build_vector_index` (`lance.py:944-956`) passes no `replace`. The design's
  ensure(list-first) + heavy(`replace=True`) split fixes this — that is a real
  bug being closed, and "`build_search_indexes()` twice is a no-op then a clean
  retrain" is a cheap acceptance test.

---

## Verification log (fresh, this round)

Live probes against pinned `lancedb==0.34.0` / `lance-8.0.0`:

| Design claim | Result |
| --- | --- |
| §5.3 "`optimize(retrain=True)` is a deprecated no-op" | **Confirmed** — `Table.optimize` docstring: "`retrain: bool, default False` … This parameter is no longer used and is deprecated." |
| §5.3 "`Table.stats()` returns `fragment_stats.num_fragments` / `num_small_fragments`" | **Confirmed** — `{'total_bytes', 'num_rows', 'num_indices', 'fragment_stats': {'num_fragments', 'num_small_fragments', 'lengths': {...}}}`, public, no `pylance` needed. |
| §5.2.1 matched-only partial merge preserves `label` and `vector` | **Confirmed** — payload of `(fact_id, kind, status)` with `when_matched_update_all()` and no insert clause left `label='L'`, `vector=[1.0, 2.0]` intact. |
| §5.2.1 "an unmatched key is a silent no-op; derive `metadata_miss` from merge result counts" | **Confirmed** — 2-row payload, 1 matching key: `MergeResult(num_updated_rows=1, num_inserted_rows=0, num_deleted_rows=0, num_rows=1)`. |
| §5.3 heavy uses `create_index(..., replace=True)` | **Confirmed** — `replace` is a real parameter and composes with `config=IvfFlat(...)`; `replace=False` on an existing index hard-errors (see R11). |
| §5.3 heavy partition formula | **Matches as-built** — `_build_vector_index` already passes both `num_partitions=ceil(rows/8192)` and `target_partition_size=8192` (`lance.py:944-956`); `LANCE_TARGET_PARTITION_ROWS=8_192`, `_MIN_VECTOR_INDEX_ROWS=256` (`lance.py:45,51`). |

Repository claims re-checked: `lane_is_valid` / `UNLANED_STAGES`
(`catalog_contract.py:276-302`) and its enforcement at `enqueue_on`
(`work_ledger.py:1265`); `_CLAIM_SELECT` accepting `pending`/`failed` with
`not_before <= now()` and `attempts < max_attempts` (`work_ledger.py:1386-1400`);
`EXPECTED_ENUMS` listing types not values (`catalog_contract.py:37-67`);
`_expected_components` per-version consumption (`profiles/selfhost.py:877-906`);
`_SUPPORTED_WORKER_STAGES` and the `worker --stage` CLI shape
(`profiles/selfhost.py:52-64, 806-812`) matching §5.5.5's compose command;
`upsert_facts` join key and its ensure set, with **no** `fact_id` index
(`lance.py:242-281`); `build_search_indexes` covering chunks/claims/facts with
`kind` as Bitmap and no entities branch (`lance.py:799-833`);
`_maintain_indexed_tail`'s per-write `list_indices` + `index_stats` loop
(`lance.py:891-918`); `_purge_table_rows`' `delete_unverified=True`
(`lance.py:1106`). Every as-built statement in §2, §5.2.3, §5.3.1 and analysis
§1.4 checks out, subject to R7/R8.

## Checklist re-run

| # | Contract | r1 | r2 |
| --- | --- | --- | --- |
| 1 | Two-layer model complete | Pass | **Pass** |
| 2 | Bulk merge correctness (vectors/labels) | Pass | **Pass** (re-verified) |
| 3 | Batch semantics (dedupe, misses, failure) | Fail | **Pass** |
| 4 | Write amplification bounded end-to-end | Fail | **Pass** |
| 5 | Index set enumerated | Fail | **Pass** (R7/R8 wording) |
| 6 | One ledger protocol | Fail | **Pass** |
| 7 | Ledger grain matches physical objects | Fail | **Pass** |
| 8 | Lane / route valid | Fail | **Pass** |
| 9 | Readiness / profile wiring | Fail | **Pass** |
| 10 | Concurrency: writer ↔ maintain | Concern | **Pass** (§5.2.3 standing invariant added) |
| 11 | Concurrency: maintain ↔ purge | Fail | **Pass with concern** (R3: lock owner unnamed) |
| 12 | Crash / stuck-lease recovery implementable | Fail | **Concern** (protocol right, SQL invalid — R1) |
| 13 | `BackfillFinalizer` unified on the port | Pass (concern) | **Pass with concern** (R3, R10) |
| 14 | Migrations vs executable catalog contract | Fail | **Pass** |
| 15 | Rollout realistic | Concern | **Pass** (§11 item 3 binds the interim; gates default off) |
| 16 | PR plan realistic | Concern | **Pass** (reclaim now in PR3, before the PR4 worker) |
| 17 | Docs obligation (D66 same-PR) | Fail | **Pass** |
| 18 | Rule 1 (cold-reader legibility) | Fail | **Pass** |
| 19 | Rule 2 (full scope, no phasing) | Fail | **Pass** |
| 20 | Rule 3 (library boundary) | Pass | **Pass** |
| 21 | Analysis ↔ code accuracy | Pass (nit) | **Pass** |
| 22 | Heavy progress guarantee under ingest | — | **Concern** (R2) |
| 23 | Self-seed cost on the real execution edge | — | **Concern** (R4) |

## What would make this an APPROVE

Two text fixes, both inside sections that already exist:

1. **R1** — replace §5.5.2's reclaim UPDATE with `WorkLedger.fail(...,
   retryable=True)` + queue re-announce, and state the attempt-exhausted →
   `dead_letter` outcome and what it means for coalesce.
2. **R2** — stop binding `_LANCE_COMMIT_RETRIES` to heavy `create_index`; give
   heavy a retry/backoff sized to its own duration, and say the worst case.

R3–R6 are a sentence or two each and can land alongside; R7–R11 are editorial.
The decision itself — batched matched-only merge with skip-unchanged, light
`optimize` vs heavy `create_index`, a table-grained ledger-backed unlaned
maintenance worker, one exclusive lock per physical table, and three separate
job families — is sound and should go into `decisions.md` as D93 once R1 and R2
are corrected.
