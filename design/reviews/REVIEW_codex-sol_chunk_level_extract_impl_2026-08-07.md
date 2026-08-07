# Implementation review: D84 chunk-level extract

## Verdict

**Reject.**

The basic work-grain change is pointed in the right direction: E1 emits one
`extract_claims` row per chunk, E2 keeps Claimify at one target chunk, replay and
D56 reuse remain in the handler, and normalize keeps its version-level ledger
identity. The implementation also correctly attempts to make chunk success and
the normalize barrier one ledger transaction.

The merge is not safe yet. The completion transaction does not serialize
concurrent barrier checks, so two last chunks can both succeed without either
enqueueing normalize. The barrier and readiness aggregates use the wrong chunk
set, connector-cycle finalization treats a dead-lettered chunk as done, and the
per-chunk path multiplies full-representation work into quadratic database and
object-store traffic. These are production correctness and D84-scale failures,
not optional follow-up tuning.

## P0 findings

None.

## P1 findings

### P1.1 — Concurrent final completions can still miss normalize permanently

`complete_chunk_extract` marks only the current row succeeded and then runs a
normal aggregate read in the same transaction
(`src/rememberstack/spine/work_ledger.py:270-305`). That is atomic with respect
to this row, but it does not serialize transactions completing different chunks.

Under PostgreSQL `READ COMMITTED`, the failure is:

1. Chunks A and B have both committed their extraction output; their work rows
   are still `running`.
2. Transaction A updates A to `succeeded`; transaction B updates B to
   `succeeded`.
3. Each transaction runs `_BARRIER_READY_CHUNKS` before the other commits. A
   sees B's old `running` version and B sees A's old `running` version.
4. Both barrier checks return false and both transactions commit. All chunk rows
   are now succeeded, but no later edge is guaranteed to re-run the barrier.

The normalize unique key only prevents duplicate inserts; it cannot recover a
missed insert. This is the central race called out by the accepted design, and
putting the read after the update in the same transaction is not sufficient to
close it.

Serialize completion per representation before updating the current work row,
for example by locking a stable representation row or taking a transaction-level
advisory lock keyed by the representation. The second finalizer must begin its
status transition/recheck only after the first commits. Add a real PostgreSQL
test with two connections held at the barrier so both output writes precede both
completion attempts; exactly one version-targeted normalize row must exist after
both return.

### P1.2 — The barrier counts every historical chunk generation, not the generation E2 processed

E2 loads chunks for `(representation_id, self._chunker_version)`
(`src/rememberstack/workers/e2.py:248-254`), and E1 fans out exactly that tuple.
`ExtractChunkBarrier` does not carry the chunker version
(`src/rememberstack/workers/base.py:44-56`), while both barrier queries count all
rows with the representation id and no generation predicate
(`src/rememberstack/spine/work_ledger.py:902-939`).

Historical chunk grids can coexist: `chunks` records `chunker_version`, is
partitioned append-only, and has no uniqueness constraint limiting a
representation to one grid
(`src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py:539-567`).
After a parameter/version bump repacks an existing representation, the barrier's
expected count includes old chunks for which the new fan-out created no work.
Every new chunk can succeed and normalize will still never be enqueued.

Carry the exact chunker generation in the work/barrier contract and constrain
both expected and ready sets to it. Prove a representation containing old and
new chunk generations opens the barrier using only the new expected set.

### P1.3 — The readiness aggregate mixes representations and mishandles zero chunks

`_EXTRACT_CHUNK_STATUS` joins chunks to a version only by `version_id`
(`src/rememberstack/spine/readiness.py:195-223`). A version is explicitly allowed
to retain multiple immutable representations while
`current_representation_id` identifies the live one
(`src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py:365-411`).
The aggregate therefore requires succeeded work for obsolete representations
and every historical chunk generation, rather than the current representation's
active grid required by the design.

The zero-chunk branch is also internally inconsistent: SQL reports `succeeded`
when `count(c.chunk_id) = 0`, but `max(p.finished_at)` is `NULL`
(`src/rememberstack/spine/readiness.py:200-212`). The readiness model requires a
terminal status and a non-null `finished_at`
(`src/rememberstack/spine/readiness.py:135-143`), so an empty representation
directly enqueued to normalize by E1 can never become pipeline-ready.

Aggregate only the current representation and its active chunker generation.
Define a durable terminal timestamp for the vacuous/zero-chunk path, such as the
successful upstream completion that atomically enqueued normalize, and add
readiness tests for old representations, old grids, partial child states,
dead-letter/replay, and zero chunks.

### P1.4 — Connector cycles finalize while a chunk is dead-lettered

The new cycle predicate blocks only `pending`, `running`, and `failed` chunk
rows (`src/rememberstack/spine/lifecycle.py:1027-1038`). A dead letter therefore
stops the normalize barrier but makes the connector cycle eligible for
finalization. The finalizer can evaluate absence/retraction before that
version's replacement testimony exists, violating the D55 support-swap
guarantee.

Treat `dead_letter` as unresolved for the cycle barrier, or define a separate
audited lossy-cycle transition that explicitly acknowledges the missing
extraction before finalization. Normal replay success should unblock the same
cycle. Test pending, running, retryable failed, and dead-lettered children; none
may finalize until terminal success (or the explicit lossy policy) is recorded.

### P1.5 — The hot path is quadratic and has no supporting representation index

Each chunk job loads all current sections in `chunk_source`, materializes every
chunk in `chunks_for_embedding`, linearly searches that tuple, and normally reads
the entire Markdown object (`src/rememberstack/workers/e2.py:248-314`). Each
completion then scans/counts the representation twice and probes output evidence
for every chunk (`src/rememberstack/spine/work_ledger.py:522-544`,
`src/rememberstack/spine/work_ledger.py:902-939`). The schema has indexes for
document, version, reuse key, and section, but none beginning with
`representation_id`/`chunker_version`
(`src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py:564-567`).

For N chunks this produces O(N²) chunk-row materialization/barrier work and N
whole-object reads. At the design's BEAM 10M estimate of roughly 100k chunks,
this is not a viable implementation of the feature's stated purpose.

The fan-out also materializes 100k `EnqueueWork` models and `WorkLedger.complete`
issues one insert per child in one transaction
(`src/rememberstack/workers/e1.py:646-664`,
`src/rememberstack/spine/work_ledger.py:225-246`). Every created row fires the
per-row `queue_wake` trigger, producing a wake storm even though workers already
drain by route.

Add a bounded catalog operation that fetches the target chunk plus only the
neighbours/section orientation needed by `_bundle_text`; provide a bounded body
read or an explicitly measured cache; add the compound indexes used by those
lookups and the barrier; and make completion readiness O(1) amortized or a
single indexed/set-based operation. Fan-out should be set-based or use a durable
manifest with bounded batches and bounded route wakes. Require an `EXPLAIN`/load
proof at the 100k-chunk shape before making the runbook scaling claim.

### P1.6 — The documented rolling deployment remains unsafe in code

The producer starts emitting chunk-targeted rows immediately, using the same
stage and extractor component version as before
(`src/rememberstack/workers/e1.py:646-660`). Workers claim by stage and lane, not
by a chunk-grain capability. During a rolling update an old E2 replica can claim
one of these rows and execute its version-serial handler against the entire
representation. The runbook/design instruction to deploy one image revision and
keep the mixed window short documents the hazard but does not prevent it.

Use a two-phase rollout gate: deploy chunk-aware E2 everywhere while producers
still emit legacy rows, verify capability, then enable chunk fan-out. A full
stop/drain/replace is also safe. The enabling mechanism must have rollback
semantics for chunk rows already queued; elapsed time in a mixed-image window is
not a correctness control.

## P2 findings

### P2.1 — Chunk work identity is only partially validated

The handler ignores payload `chunk_id` and `version_id`, trusts payload
`representation_id`, and validates only that `work.target_id` occurs somewhere
in the loaded tuple (`src/rememberstack/workers/e2.py:246-299`). It does not
require:

- `payload.chunk_id == work.target_id`;
- `payload.version_id == source.version_id == chunk.version_id`;
- `work.deployment_id == source.deployment_id`; or
- the processing row's stage/component version to match the barrier's values.

Because source lookup is by representation UUID alone, a corrupt internal row
can read/extract one deployment's artifact while metering and completing work in
another deployment. Validate the whole coordinate before any object-store or
model call, and have `complete_chunk_extract` lock/read the processing row and
derive or verify barrier identity rather than trusting a worker-supplied DTO.

### P2.2 — The legacy coordinator cannot repair an already-complete set

The non-chunk path only fans out children (or directly normalizes zero chunks)
and never invokes the barrier (`src/rememberstack/workers/e2.py:259-280`). If all
child rows already succeeded but normalize is absent—for example after the
P1.1 race—the coordinator enqueues only duplicates and succeeds, leaving no
remaining recheck edge. The accepted behavior said legacy rows fan out and/or
fire the barrier.

Provide an explicit idempotent barrier recheck for coordinators/operators (using
the same serialized contract as chunk completion), so replay and mixed-path
recovery can converge without mutating succeeded child rows.

### P2.3 — The new tests do not exercise the implementation's risky paths

`src/tests/workers/test_chunk_level_extract.py:64-134` contains two pure unit
tests: fan-out shape and zero-chunk follow-up shape. It never constructs an E2
chunk claim, calls `complete_chunk_extract`, touches the barrier SQL, races
transactions, dead-letters/replays a child, checks readiness/lifecycle, tests a
legacy coordinator, or proves D56 reuse. Six of the seven acceptance cases in
the design therefore lack D84-specific coverage.

Add real PostgreSQL integration tests for every P1 repair. In particular, a
single-threaded mock of the barrier is not evidence for P1.1; the test must force
two database transactions to overlap.

### P2.4 — The branch fails the repository's required format check

`ruff check` passes, but `ruff format --check` reports that
`src/rememberstack/spine/work_ledger.py` and
`src/tests/workers/test_chunk_level_extract.py` would be reformatted. CI runs
this check as a required quality gate (`.github/workflows/ci.yml:59-64`). Format
the files before merge.

## Verification performed

- `test_chunk_level_extract.py`: **2 passed**.
- Relevant E1/E2/readiness/lifecycle test selection: **2 passed, 24 skipped** in
  this environment because `REMEMBERSTACK_DATABASE_URL` is not configured. The
  skipped tests are why the SQL findings above were also reviewed against
  PostgreSQL MVCC semantics and the repository's schema contracts.
- `ruff check src/ benchmarks/`: **passed**.
- `ruff format --check` on the changed implementation files: **failed** for the
  two files named in P2.4.
- Pyright on the changed implementation/test files: **0 errors**.
- Import-linter architecture contracts: **passed**.
- Test inventory check: **passed**.

## Required changes before re-review

1. Serialize per-representation completion/barrier evaluation and prove the
   two-finalizer race with concurrent PostgreSQL transactions.
2. Bind the barrier and readiness expected set to the exact representation and
   chunker generation; fix current-representation and zero-chunk readiness.
3. Keep connector cycles blocked on extract dead letters unless an explicit,
   audited lossy policy is invoked.
4. Replace the per-chunk full-representation/full-object and per-completion
   aggregate path with bounded, indexed operations; make 100k fan-out/wakes
   bounded and measured.
5. Add a real two-phase rollout gate or require a stop/drain/replace deployment.
6. Add the missing integration/race/recovery tests, strict coordinate
   validation, a coordinator barrier-repair edge, and make the format gate pass.
