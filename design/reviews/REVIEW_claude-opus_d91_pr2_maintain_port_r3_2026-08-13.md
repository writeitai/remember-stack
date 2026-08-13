# Implementation re-review: D91 PR2 maintain port (r3)

**Reviewer:** Claude (`claude-opus` review series)
**Date:** 2026-08-13
**PR:** #275 (`feat/d91-pr2-maintain-port`, stacked on #271 at `2c0c2b71`)
**Commit:** `45b40d19` ("fix(p1): bound maintain lock wait with try-lock poll")
**Prior reviews:** `REVIEW_codex-sol_d91_pr2_maintain_port_2026-08-13.md` (r1, at
`1d098943`), `REVIEW_codex-sol_d91_pr2_maintain_port_r2_2026-08-13.md` and
`REVIEW_claude-opus_d91_pr2_maintain_port_2026-08-13.md` (both at `e1ccd552`)
**Design:** D91 `plan/designs/p1_lance_maintenance_design.md` §5.3 / §5.7 rule 5 /
§15 PR2, read on `design/d91-p1-lance-maintenance` at `87079b4b` (PR #270, still
open at that commit)

## Verdict

**REQUEST_CHANGES**

The lock protocol is now genuinely done: the bounded try-lock wait, the
unlock-invalidate lifecycle, the affected-tables-only purge, and five real
PostgreSQL proofs all check out by execution, and the previously failing
backfill test now passes 4/4 against a live database. Every prior blocking
finding about locks (P1.1, P1.2, P1.4, B1, B2) is closed with committed tests.

Two new deterministic defects block the merge, both introduced or exposed by
the two fix commits under review:

1. The vector-type guard added to close P1.3 compares against the wrong type
   string, so **every explicit ensure after the first destructively retrains a
   healthy IVF_FLAT index** — reproduced against the shipped adapter.
2. The new lock test file is **absent from the CI test inventory**, so the
   Quality job (and with it the PR gate) fails deterministically at this head —
   confirmed in the public CI run and reproduced locally.

Both fixes are small. The remaining P2.2/R1 residue (ensure retry counts,
finalizer sequence assertions, wrong-type/FTS repair tests) should ride the
same round.

## Requested-change disposition

| Prior finding | Disposition at `45b40d19` |
| --- | --- |
| **P1.1** — hard-forget lock wait unbounded | **CLOSED.** `pg_try_advisory_lock` poll with deadline (`p1_maintain_lock.py:64-76`); purge locks only tables with nominated IDs (`p1_locked_purge.py:46-61`); timeout raises `P1MaintainLockTimeout`, which the worker loop fails `retryable=True` (`workers/base.py:290-300`). Proven by `test_purge_times_out_on_held_table_then_proceeds_after_release` on real PostgreSQL. |
| **P1.2** — release failure could leak session locks into the pool | **CLOSED.** Unlock checks the boolean result (`_unlock_result_ok`, `p1_maintain_lock.py:31-33`); any release error or failed commit triggers `connection.invalidate()` before re-raising (`p1_maintain_lock.py:95-97`). Proven by `test_failed_first_release_invalidates_and_frees_keys`: after an injected unlock failure, both keys are free from a second session. |
| **P1.3** — ensure trusted caches / accepted wrong vector type | **PARTIAL → new blocker N1.** Cache invalidation (fixed at `e1ccd552`) holds. The added type check (`lance.py:1156`) compares the wrong string constant, so it misclassifies *healthy* IVF_FLAT indexes as wrong-type and retrains them on every ensure. The r1/r2-required wrong-type and FTS repair regressions are still absent — exactly the tests that would have caught this. |
| **P1.4** — lock root could silently differ from the adapter's estate | **CLOSED.** Mandatory `lance_root` on finalizer and purge wrapper (unchanged since `e1ccd552`); custom-root key proofs now committed (`test_p1_maintain_lock.py:236-242`, `test_backfill.py:270-283`). |
| **P2.1** — finalizer held the whole estate | **CLOSED in code** (one table lock per ensure+rebuild triple, `backfill.py:133-145`, since `e1ccd552`). Test residue remains: the rewritten fake counts only `ensures`, not the rebuild calls or the held key (see required correction C2). |
| **P2.2** — stats not a truthful before/after/retry report | **PARTIAL.** Before-snapshots are now stamped on `rebuild_vector_indexes` and `rebuild_text_indexes` (`lance.py:1046-1049`, `1077-1080`) — that half is fixed. `_create_index_with_retry` still returns `None` (`lance.py:1209-1234`), so `conflicts_retried` is structurally 0 for ensure (required correction C1). |
| **B1** — stale finalizer test fails in CI | **CLOSED, verified.** `_RecordingIndexMaintenance.ensure_search_indexes` counts calls; the barrier test asserts 0 before drain and 4 after. `src/tests/spine/test_backfill.py`: **4 passed** against a fresh CI-image PostgreSQL (was 1 failed at `e1ccd552`). |
| **B2** — §5.7 rule 5 bounded wait on the forget path | **CLOSED.** Same mechanism as P1.1; the bound is configurable (`LockingP1Purge(lock_timeout=…)`, default 30 s), the timeout is a distinct exception, and the generic worker exception path makes it a retryable forget-step failure, which is what rule 5 demands. |
| **R1** — committed lock/purge regression tests | **MOSTLY CLOSED.** New `src/tests/spine/test_p1_maintain_lock.py` delivers five real-PostgreSQL proofs: blocked-acquire timeout frees earlier keys; injected unlock failure still ends with all keys free (invalidate path); purge-vs-holder bounded failure then success after release; facts-only purge ignores a held chunks lock; custom-root key derivation. Still absent: same-instance dropped-FTS repair and wrong-vector-type ensure tests (fold into N1's fix, correction C3). |
| **R2** — rebuild before-stats and retry counts | **PARTIAL.** Before-snapshots done on all four operations; ensure-path retry propagation still missing (correction C1). |

## Blocking findings

### N1 — Ensure destructively retrains a healthy IVF_FLAT vector index on every call

`_ensure_matrix_indexes` (`lance.py:1150-1158`) decides whether the contracted
vector index already exists with:

```python
if any(index.index_type == "IVF_FLAT" for index in vector_indexes):
    continue
self._build_vector_index(table=table, replace=bool(vector_indexes))
```

`"IVF_FLAT"` is the spelling `Table.index_stats()` returns. The objects being
inspected here come from `Table.list_indices()`, which on the pinned LanceDB
0.34.0 reports the camel-case form. Verified directly against the pinned
dependency:

```text
list_indices:            'IvfFlat'   ['vector']  vector_idx
index_stats.index_type:  'IVF_FLAT'
```

The comparison can therefore **never** be true. Any table at or above the
min-row gate that already has a healthy IVF_FLAT index takes the fall-through
branch with `replace=True` — a full IVF retrain. Reproduced with the shipped
`LanceChunkIndex` (min-row gate lowered to 1, one chunk row, counting
`_build_vector_index` invocations across three consecutive
`ensure_search_indexes(tables=("chunks",))` calls):

```text
after first ensure,  vector builds (replace flags): [False]
after second ensure, vector builds (replace flags): [False, True]
after third ensure,  vector builds (replace flags): [False, True, True]
vector indices: [('IvfFlat', ['vector'])]
```

Every ensure after the first is a destructive retrain. Consequences against the
binding design:

- §5.3's ensure contract is explicit: "List indices first; create only if
  missing (or wrong type for known misbuilds); **no** destructive replace of a
  healthy index." This violates it on all four tables.
- §5.3 schedules ensure at deploy, backfill end, **every maintain tick**, and
  before large metadata merges. Once PR4 wires the maintain handler, each light
  tick on a BEAM-scale chunks table triggers a full multi-hour IVF retrain —
  outside the entire §5.7 rule 3 heavy-policy machinery (write-rate defer,
  conflict defer, `awaiting_operator`), which only governs the *heavy* path.
- Within this PR's shipped scope, `BackfillFinalizer` and
  `build_search_indexes()` now train IVF **twice per table** per invocation
  (ensure retrains, then `rebuild_vector_indexes` retrains again).

The scalar/FTS branches are unaffected — I verified `list_indices()` reports
`'BTree'`, `'Bitmap'`, and `'FTS'`, matching the strings used by
`_ensure_typed_index` and `_create_index_with_retry`. The defect is confined to
the one vector comparison introduced by `d2d80c42`.

No committed test catches this: the build-twice test's vector assertion
(`test_lance_retrieval.py:587`) accepts "`IVF_FLAT`" **or** any vector-column
index, and since `list_indices()` never returns `"IVF_FLAT"`, its first branch
is dead code — the assertion passes on the wrong behavior too.

**Required change:** compare against the `list_indices()` spelling (`"IvfFlat"`),
or normalize both sides so the two public API spellings cannot diverge again.
Land the r1/r2-required regressions with it: (a) a second ensure on a healthy
IVF_FLAT table performs **no** vector build (count `_build_vector_index` calls
or assert the table version does not advance); (b) a wrong-type vector index
(e.g. IVF_HNSW family) **is** replaced by IVF_FLAT; and tighten
`test_lance_retrieval.py:587` to the exact `list_indices()` type string.

### N2 — New test file missing from the CI test inventory; Quality job fails deterministically

`src/tests/spine/test_p1_maintain_lock.py` is not listed in
`.github/ci/integration-paths.txt` (or any inventory). The Quality job's first
step enforces that every test file is inventoried, and the public CI run for
this head (`45b40d19`, run 31717406727) failed at exactly that step, skipping
ruff/pyright and failing the PR gate. Reproduced locally:

```text
python3 .github/ci/check_test_inventory.py
test inventory check FAILED:
test files not in any inventory:
  src/tests/spine/test_p1_maintain_lock.py
```

**Required change:** add `src/tests/spine/test_p1_maintain_lock.py` to
`.github/ci/integration-paths.txt`. Integration is the correct bucket: the
module skips without `REMEMBERSTACK_DATABASE_URL`, and the Integration
(workers) job runs all of `src/tests/spine` with a live database, so the five
proofs will actually execute in CI once inventoried.

## Other required corrections

### C1 — Ensure still discards create-index retry counts (P2.2/R2 residue)

`_create_index_with_retry` (`lance.py:1209-1234`) returns `None`, so
`ensure_search_indexes` reports `conflicts_retried=0` even after successful
retries; only `optimize_tables` reports real counts. Both prior reviews
required propagating this before PR3 makes the report shape durable in
`p1_lance_table_stats` (§5.6). Return the attempt count and aggregate it per
table in the ensure report, with a forced-retry assertion.

### C2 — Finalizer test does not assert the per-table call sequence (P2.1/B1 residue)

The rewritten fake counts only `ensure_search_indexes` calls
(`test_backfill.py:125-128`), and the barrier test asserts `ensures == 4`
(`test_backfill.py:267`). A finalizer that silently dropped
`rebuild_vector_indexes`/`rebuild_text_indexes` — the heavy work the barrier
exists to schedule — would still pass. Both r2 reviews required ordered
per-table recording. Record all three port calls and assert the ordered triple
(ensure → rebuild_vector → rebuild_text) for each of the four tables;
asserting the held lock key per triple remains the ideal.

### C3 — Missing wrong-type and FTS repair regressions (R1 residue)

Only the dropped-BTree repair test exists. Add the same-instance dropped-FTS
repair test and the wrong-vector-type test — the latter folds directly into
N1's fix and would have caught it.

## Nits

- `raise release_errors[0]` in the lock helper's cleanup
  (`p1_maintain_lock.py:97`) can replace a propagating body exception (the
  original survives only as `__context__`). Carried from r2; acceptable, worth
  explicit chaining when convenient.
- The trailing `raise RuntimeError("optimize retries exhausted")`
  (`lance.py:1282`) is unreachable — the last attempt re-raises inside the
  loop. Carried from r2; a one-line comment would stop future readers hunting
  for the path.
- The permissive entity vector assertion (`test_lance_retrieval.py:587`) is
  subsumed by N1's required change.
- The separate CLA check remains failing, as at both prior reviews; external
  to the code.

## What is fixed and verified

Environment: local darwin; fresh PostgreSQL container from the exact CI image
digest (`ghcr.io/dbsystel/postgresql-partman@sha256:bf9d2331…`), CI
user/database, `REMEMBERSTACK_DATABASE_URL` exported; alembic migration to
head performed by the backfill fixture itself.

- **Bounded lock wait (P1.1/B2):** `_TRY_ACQUIRE` is `pg_try_advisory_lock` —
  the non-blocking session-lock operation in PostgreSQL's public advisory-lock
  function table — polled against a monotonic deadline with positive-timeout
  validation. `test_lock_timeout_releases_earlier_keys` proves a blocked
  second acquire raises within the bound (< 2 s asserted) and the
  already-acquired first key is freed while the blocker still holds its own.
- **Unlock-invalidate (P1.2):** unsuccessful (`false`/`NULL`) unlock results
  and unlock exceptions both feed `release_errors`; any entry invalidates the
  pooled connection. The injected-failure test proves both session locks end
  free from a second session — the invalidation genuinely terminates the
  backend session rather than returning a lock-owning connection to the pool.
- **Purge interaction (§5.7 rule 5):** purge takes locks only for tables with
  nominated rows (`test_purge_does_not_wait_on_unaffected_table`), fails
  within the bound while a peer holds the table lock without touching Lance
  (`index.calls == []`), and proceeds after release. The timeout propagates
  through `HardForgetHandler.honor()` to the worker's generic exception
  handler, which records a retryable failure — the "fail forget step with
  retry" clause.
- **B1:** `uv run pytest -q src/tests/spine/test_backfill.py` → **4 passed**
  (97 s) on real PostgreSQL; the drain-barrier test asserts refusal leaves
  `ensures == 0` and success leaves `ensures == 4`.
- **Lock proofs:** `uv run pytest -q src/tests/spine/test_p1_maintain_lock.py`
  → **5 passed** (10.9 s).
- **Adapter suites:** `uv run pytest -q src/tests/adapters/test_lance_retrieval.py
  src/tests/adapters/test_selfhost_purge.py` → **15 passed** (21.5 s),
  including the ensure-repairs-dropped-scalar and build-twice regressions.
- **Rebuild before-stats (P2.2, first half):** both rebuild operations stamp
  `row_count_before` / `unindexed_rows_before` / `num_fragments_before` /
  `num_small_fragments_before` from a pre-operation snapshot.
- **Quality tooling:** `ruff check`, `ruff format --check`, and `pyright` all
  clean on the touched implementation and test files locally (CI's Quality
  failure is solely the N2 inventory step).
- **Public state:** PR #275 open at `45b40d19` targeting
  `feat/d91-pr1-bulk-metadata`; design PR #270 open at `87079b4b`. At review
  time: Unit, Contract smoke, Integration (adapters), Compose quickstart, Path
  filters green; Quality and PR gate failing (N2); Integration (workers) and
  Integration (surfaces) in progress.

Sources: repository code at `45b40d19`, the pinned LanceDB 0.34.0 executed
locally, and public LanceDB / PostgreSQL / SQLAlchemy documentation only. No
private repository or non-public source was used.

## Final assessment

The lock story — the hardest part of this PR — is finished and proven: bounded
waits, leak-free release, one shared key namespace, and real-database coverage
for the failure paths reviewers had to hand-verify in earlier rounds. What
blocks the merge now is one wrong string constant that turns the
carefully-specified non-destructive ensure into a repeating full retrain, and
one missing inventory line that keeps CI red. Fix N1 with its regression pair,
add the N2 inventory entry, and close out C1–C3 in the same round; nothing
structural remains.
