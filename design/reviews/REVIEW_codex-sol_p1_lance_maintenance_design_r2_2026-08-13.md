# Re-review: P1 Lance bulk writes and two-layer maintenance

**Reviewer:** Codex (`gpt-5.6-sol`)  
**Date:** 2026-08-13  
**Branch:** `feat/d90-entity-obs-flush-fanout` at `4f56a985`  
**Scope:** revised `plan/designs/p1_lance_maintenance_design.md`, companion
analysis, both first-round reviews, and affected code under
`src/rememberstack`

## Verdict

**REQUEST_CHANGES**

The revision closes the route/readiness, hot-path timeout, and rollout blockers,
and it materially improves the unit grain, index coverage, self-seed edge, purge
serialization, and skip-unchanged contract. It is not yet safe to approve: the
new reclaim protocol can fence out no live owner and can let an old attempt
complete a newer attempt; the running-enqueue edge can still be lost between the
handler's last flag read and the generic ledger completion transaction; and the
heavy-versus-writer policy still assumes, rather than guarantees, a quiet commit
window. These are direct continuations of first-review P1.2, P1.4, and P1.5.

The external checks below use only public, primary documentation. The BEAM host
measurements remain author-reported incident evidence.

## First-review disposition

| Prior item | Status | Re-review result |
| --- | --- | --- |
| **P1.1 — route/lane/readiness contract** | **addressed** | The stage is bindingly unlaned, non-null lanes are forbidden, the profile gets a `lane=None` branch, and `_expected_components` is explicitly excluded (`design` §§1.5, 5.5.5–5.5.6). |
| **P1.2 — self-seeding/coalescing protocol** | **partial** | The idle-loop execution edge, control table, enqueue lock, and `rerun_requested` exist, but the rerun decision is not atomic with ledger completion; a request can still be lost. |
| **P1.3 — five-second writer bound** | **addressed** | The unenforceable timeout knob is removed and writers are enqueue-only; synchronous optimize/rebuild is forbidden under the writer lease (`design` §§1.7, 5.4). |
| **P1.4 — heartbeat/stale-running recovery** | **partial** | A stage-scoped reclaim is now required, but `started_at` age alone is not proof of death, the transition shown violates the current ledger constraints, and completions are not attempt-fenced. |
| **P1.5 — maintenance concurrency/progress** | **partial** | The table/root grain and light-versus-heavy serialization are addressed. Eventual heavy progress against continuous writers remains an expectation backed only by finite retries. |
| **P1.6 — rollout safety gates** | **addressed** | Master/heavy gates default off; metrics and same-PR docs land before auto-enable; rollback behavior and light-then-heavy soak are specified. |
| **P2.1 — complete per-table index contract** | **partial** | Entities, `facts.fact_id`, and the `facts.kind` type are now explicit, but the matrix still omits existing prefilter columns, and PR1 precedes its mandatory facts join index. |

## Remaining blocking issues

### P1.4 (partial) — Reclaim can steal a live operation and an old attempt can complete the new one

The design declares that Lance calls may run for multiple hours, sets the stale
cutoff to two hours, deliberately provides no heartbeat, and reclaims solely on
`started_at` age (`plan/designs/p1_lance_maintenance_design.md:412,533-569`). A
healthy heavy call that crosses the cutoff is therefore indistinguishable from
a dead worker.

The resulting race is unsafe against the current ledger:

1. attempt A is still executing while holding the table lock;
2. the idle tick changes its row from `running` to retryable and attempt B claims
   the **same** `processing_id`, then waits for the table lock;
3. A returns and calls `WorkLedger.complete(processing_id=...)`;
4. `_COMPLETE` checks only `processing_id` plus `status='running'`, so A marks
   B's attempt succeeded (`src/rememberstack/workers/base.py:261-262,354-357`;
   `src/rememberstack/spine/work_ledger.py:225-246,1434-1439`).

Idempotent Lance operations do not repair corrupted ledger ownership. The
illustrative reclaim SQL is also not executable as written: a `failed` row must
have `defer_reason='retry_backoff'`, and `failed` is forbidden when
`attempts >= max_attempts`; the update sets neither the defer reason nor
`not_before` and does not dead-letter exhausted work
(`p0_02_0002_infrastructure_registries.py:84-102`).

Bind an ownership/fencing protocol before approval. At minimum, claim must mint
or expose an attempt token, and complete/fail/reclaim must compare it atomically;
reclaim must perform the exact retry-or-dead-letter transition, including
`not_before`, wake-up, and attempt exhaustion. It also needs a liveness signal
that cannot classify a legitimate multi-hour call as dead (for example, an
independent heartbeat, or a session lock kept through the fenced completion).
Acceptance must cover a **live** operation crossing the cutoff and the stale
worker returning after a replacement claim, not only a killed process.

### P1.2 (partial) — `rerun_requested` is still a lossy edge

The enqueue transaction correctly sets `rerun_requested` when it observes a
running unit, but successful completion is described as a handler-side flag
check followed by successor enqueue and ledger success
(`plan/designs/p1_lance_maintenance_design.md:501-523,573-591`). In the current
runner the handler returns first and `WorkLedger.complete()` runs afterward in
its own transaction (`src/rememberstack/workers/base.py:261-262,330-357`). An
enqueue after the handler's last flag read but before `_COMPLETE` sees
`running`, sets the flag, and returns; the current row then succeeds with no
successor. The promised post-write edge is lost.

Add a maintenance-specific completion/barrier transaction, analogous to the
existing specialized completion paths: under the same coalesce lock, lock the
unit and processing row, consume `rerun_requested`, atomically create the
successor (or reschedule), and mark the current processing row succeeded. The
acceptance test must force an enqueue between handler return and completion and
must test process death at the completion/successor boundary.

Also remove the proposed partial-index alternative at design lines 484-489. A
PostgreSQL partial-index predicate can use only columns of the indexed table; it
cannot filter `p1_maintain_units` by status in linked `processing_state`.
Choose the control-row/advisory-lock implementation, or add unit-local open
state that a real partial unique index can predicate on. See the official
[PostgreSQL partial-index contract](https://www.postgresql.org/docs/current/indexes-partial.html).

### P1.5 (partial) — Finite retries are not a heavy-maintenance progress contract

The revision serializes light and heavy work per physical table, which closes
half of the original issue. It explicitly leaves writers outside that lock,
however, gives `create_index` bounded retries followed by the ledger's finite
attempt budget, and says success is merely “expected once a quiet commit window
appears” (`plan/designs/p1_lance_maintenance_design.md:700-712`). The current
default ledger limit is three attempts
(`p0_02_0002_infrastructure_registries.py:87-88`). A continuously busy table can
therefore dead-letter every heavy unit without ever retraining. LanceDB's public
FAQ likewise warns that excessive concurrent writers can fail once limited
commit retries are exhausted
([LanceDB OSS FAQ](https://docs.lancedb.com/faq/faq-oss)).

Bind a terminal recovery policy that guarantees a maintenance window without
indefinitely extending `label_lock`: for example, defer heavy while durable
write-rate telemetry is above a threshold and reserve a bounded quiet window,
or use explicit writer backpressure/admin quiescence after repeated conflicts.
Define what happens after ledger attempt exhaustion. The acceptance test at
design line 956 currently permits “unit retries”; it must drive continuous
writes through retry exhaustion and prove eventual rebuild or a deliberate,
operator-visible quiescence state.

The lock owner also needs one binding seam for every caller. The handler takes
the advisory lock, while `BackfillFinalizer` may invoke the deployment-free port
directly and the purge adapter currently has no PostgreSQL handle. Specify the
lock-owning service/port so handler, finalizer, and hard-forget cannot implement
three different lock paths.

## Remaining non-blocking issue

### P2.1 (partial) — The “binding” index matrix is still incomplete and lands one PR late

The matrix says it covers current filter/join usage
(`plan/designs/p1_lance_maintenance_design.md:348-378`), but the current query
bridge applies these equality filters before top-k:

- claims: `doc_id`;
- chunks: `doc_id`, `source_kind`, `source_shape`, `section_role`.

They are declared in
`src/rememberstack/surfaces/query_sandbox/nomination.py:303-316` and passed to
Lance `.where(..., prefilter=True)` in
`src/rememberstack/adapters/selfhost/lance.py:556-628,634-724`; none appears in
the matrix. This contradicts both the stated matrix rule and LanceDB's guidance
to index every filtered or joined column
([LanceDB performance guidance](https://docs.lancedb.com/performance)). Add the
columns and binding types (`doc_id` BTREE; low-cardinality categorical fields
BITMAP unless measured cardinality says otherwise), or narrow the design's
claim that every filter column is indexed.

Finally, §5.2.1 makes `facts.fact_id` mandatory **before** large metadata merges,
while the PR plan ships the merge in PR1 and that index in PR2
(`plan/designs/p1_lance_maintenance_design.md:255-256,931-935`). Official
LanceDB documentation says an unindexed merge key falls back to a full column
scan, the dominant cost at scale. Move facts join-key ensure/index migration
into PR1 or make PR1 depend on PR2. The matched-only partial merge itself remains
sound: public LanceDB docs confirm that supplied columns alone are updated for
matched rows and omitted columns become null only for inserted rows
([LanceDB update/merge documentation](https://docs.lancedb.com/tables/update)).

## Approval gate

Approve after the design binds (1) attempt-fenced, liveness-aware reclaim, (2)
atomic maintain completion plus rerun successor creation, and (3) a terminal
heavy-progress policy under sustained writes. The index-matrix/PR-order issue is
non-blocking by original severity but should be corrected in the same revision.
