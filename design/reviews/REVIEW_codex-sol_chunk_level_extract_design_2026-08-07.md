# Adversarial review: D84 chunk-level extract

## Verdict

**Accept with changes.**

The primary decision is sound: `extract_claims` should be leased at chunk grain,
and the existing ledger unique key can address those jobs without adding a new
target enum (`plan/designs/chunk_level_extract_design.md:23-35`,
`src/rememberstack/model/processing.py:16-29`,
`src/rememberstack/spine/work_ledger.py:586-600`). This is the right way to make
multiple E2 replicas shorten one large-document drain while keeping
`processing_state` authoritative.

The design is not implementation-ready as written. Its barrier cannot currently
satisfy both the output-marker and dead-letter contracts; existing version-grain
readiness consumers would ignore the new child jobs; the stated rolling upgrade
protects new workers from old rows but not old workers from new rows; and the
specified per-chunk read/fan-out path introduces scale costs large enough to
defeat the BEAM motivation. These are contract gaps, not reasons to reject chunk
grain.

## P0 findings

None.

## P1 findings

### P1.1 — The barrier is not atomic with chunk completion, so it must either open early or miss the last completion

The design defines terminal extraction as whatever
`chunk_already_extracted` means (`plan/designs/chunk_level_extract_design.md:103-119`),
but also requires a `dead_letter` processing row to hold the barrier closed. Those
are different authorities. `chunk_already_extracted` checks claims, decisions, or
occurrence links, not `processing_state` (`src/rememberstack/spine/claim_catalog.py:29-46`,
`src/rememberstack/spine/claim_catalog.py:198-213`). Extraction output commits in
its own catalog transaction (`src/rememberstack/workers/e2.py:434-441`,
`src/rememberstack/spine/claim_catalog.py:173-195`); only after the handler returns
does the runner mark the work row succeeded and atomically enqueue its returned
follow-ups (`src/rememberstack/workers/base.py:189-190`,
`src/rememberstack/workers/base.py:258-260`,
`src/rememberstack/spine/work_ledger.py:225-246`).

That ordering creates two bad implementations:

- An output-only barrier can observe a sibling's committed claim/decision marker
  while that sibling row is still `running`. It can enqueue normalize even if the
  sibling later fails or dead-letters in post-extraction/barrier work, contrary to
  the dead-letter policy.
- A ledger-status barrier run inside the handler sees its own row as `running`.
  If two last chunks finish concurrently, each can see the other or itself as
  unfinished and neither returns normalize. There is no later edge guaranteed to
  re-run the barrier.

`ON CONFLICT` protects only against duplicate normalize rows; it does not make the
read-before-enqueue predicate correct. The race test at
`plan/designs/chunk_level_extract_design.md:195-205` therefore does not yet have a
specified invariant to test.

Before implementation, define one spine transaction that (1) marks the current
chunk work row succeeded, (2) verifies that the complete expected chunk set has a
matching E2 work row, (3) requires every such row to be succeeded and every chunk
to have E2 output/reuse evidence, and (4) inserts the version-targeted normalize
row with `ON CONFLICT DO NOTHING`. A dead letter then blocks mechanically. A crash
after output but before ledger completion remains recoverable: replay skips the
LLM through the existing output marker and retries this final transaction. The
ordinary handler-return path is not enough; the ledger completion API needs an
extract-specific conditional-finalization operation (or an equivalent durable
barrier mechanism).

### P1.2 — Existing version-scoped readiness and lifecycle barriers will ignore chunk work

The design says existing `processing_state` aggregates already power readiness
and need only documentation (`plan/designs/chunk_level_extract_design.md:128-134`).
The implementation is not a stage-only aggregate. Pipeline readiness selects only
`target_kind = 'document_version'` and keys each expected stage by the version id
(`src/rememberstack/spine/readiness.py:68-105`,
`src/rememberstack/spine/readiness.py:147-155`). Once E2 rows target chunks,
`extract_claims` will be reported `missing` forever even after every chunk
succeeds.

More seriously, connector cycle finalization promises to wait while observed
lineages are extracting (`src/rememberstack/spine/lifecycle.py:521-531`), but its
SQL also joins only document-version work and tests only
`pending/running/failed` rows (`src/rememberstack/spine/lifecycle.py:1011-1026`).
On the new primary path, the version-level embed row can be succeeded while all
chunk E2 rows are still running; the cycle query sees no unresolved version row
and may finalize early. A legacy coordinator that succeeds immediately after
fan-out has the same problem. This can violate the D55 support-swap barrier, not
just misreport a dashboard.

Inventory and update every consumer that assumes all plane-E stages share the
version target. At minimum, readiness and connector-cycle finalization need an
explicit version/representation aggregate over the expected chunk set, including
retryable failures and dead letters. Freeze zero-chunk semantics as well. Add
acceptance tests proving that readiness remains false and a sync cycle cannot
finalize while any child is pending, running, retrying, or dead-lettered, then
turns ready after replay and successful completion.

### P1.3 — The claimed rolling upgrade is safe in only one direction

The compatibility section handles old version-level rows claimed by a new image
(`plan/designs/chunk_level_extract_design.md:136-144`,
`plan/analysis/chunk_level_extract_analysis.md:160-173`). It does not handle new
chunk rows claimed by an old E2 image during a rolling deployment. The design
keeps the same stage and `E2_EXTRACTOR_VERSION`
(`plan/designs/chunk_level_extract_design.md:25-30`,
`plan/designs/chunk_level_extract_design.md:193`), and workers claim by stage and
lane, not by an image capability. The old `ExtractClaimsHandler` does not inspect
`target_kind`; for any claimed row it loads all chunks and loops the whole
representation (`src/rememberstack/workers/e2.py:244-272`). Its required payload
fields are also present on the new chunk jobs.

Consequently, once an upgraded E1 or upgraded legacy coordinator emits chunk
rows, an old E2 replica can claim one and run the document-serial path. Many such
claims can concurrently call Claimify for the same chunks before output markers
land, causing duplicate provider spend and duplicate evidence/decision writes.
The assertions that mixed-image deploy is mitigated and UMC auto-deploy needs no
coordination (`plan/analysis/chunk_level_extract_analysis.md:186-197`,
`plan/designs/chunk_level_extract_design.md:163-169`) are therefore false.

Specify a two-sided rollout gate. Safe examples are a coordinated stop/drain and
ordered replacement of every E2 worker before any producer can emit chunk rows,
or a two-phase compatibility rollout in which chunk production/legacy
coordination remains disabled until all extract workers advertise support. A
nominal first release that already converts legacy rows is not a safe first
phase: it can produce chunk work while old E2 replicas still exist. UMC must
enforce and verify the minimum compatible worker revision before activating the
new grain, and the design needs rollback behavior while chunk rows already
exist.

### P1.4 — The specified handler turns bounded neighbour context into document-scale work per chunk

The design explicitly instructs each chunk job to load all chunks for the
representation (`plan/designs/chunk_level_extract_design.md:94-101`). The current
catalog materializes every chunk (`src/rememberstack/spine/chunk_catalog.py:80-99`)
and `chunk_source` materializes every current section
(`src/rememberstack/spine/chunk_catalog.py:28-51`). The current object-store port
can only read a whole object (`src/rememberstack/ports/object_store.py:9-15`), and
the E2 handler reads the complete Markdown document (`src/rememberstack/workers/e2.py:249-257`).
Yet the actual Claimify bundle and grounding union use only the target plus the
immediately adjacent same-section chunks (`src/rememberstack/workers/e2.py:660-682`,
`src/rememberstack/workers/e2.py:686-719`).

The existing version-serial handler pays those full reads once. The proposed path
pays them N times: N chunk jobs materialize N chunk rows, all section rows, and the
whole document. At the design's own BEAM 10M scale of about 100k chunks, that is
up to 10^10 chunk-row materializations and repeated reads of a roughly
multi-tens-of-megabytes object. A barrier that lists and individually probes all
chunk ids after every completion adds another potentially quadratic path. This
is an immediate Postgres/object-store/pgBouncer risk for self-host and UMC and can
erase the latency benefit the decision is intended to create.

Change the handler contract to bounded lookup: fetch the addressed chunk, its
ordinal predecessor/successor, and only target/ancestor section orientation
rows. Define a bounded body-read mechanism (range reads over the immutable
Markdown object, or a measured process cache with an explicit Cloud/worker
lifetime contract); do not leave whole-object-per-chunk as the default. Define
the barrier as one indexed/set-based predicate or an atomic counter/manifest,
not a Python list plus one `chunk_already_extracted` query per chunk. Require a
BEAM-scale query plan and load test before claiming 10M suitability.

### P1.5 — Fan-out durability and delivery amplification are incorrectly left as an implementation detail

The design allows either one transaction or chunked batches and defers the
choice until measurement (`plan/designs/chunk_level_extract_design.md:151-159`,
`plan/designs/chunk_level_extract_design.md:214-221`). The current chain contract
requires parent success and all follow-ups to commit atomically
(`src/rememberstack/spine/work_ledger.py:225-246`). Its implementation accepts a
fully materialized tuple and performs one enqueue call/SQL insert per child
(`src/rememberstack/spine/work_ledger.py:234-246`,
`src/rememberstack/spine/work_ledger.py:500-526`). Materializing and issuing
roughly 100k inserts serially in the embed completion transaction is not the
same design as durable paginated fan-out.

Splitting those inserts across transactions without a durable fan-out manifest
creates a crash window: the parent can succeed with only a prefix of child rows,
or remain retryable while already-created children run. The barrier cannot infer
missing work merely from the rows that happen to exist. Conversely, retaining
one insert per row also fires the current `AFTER INSERT` `pg_notify` once per
child (`src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py:110-125`).
The self-host listener consumes one notification and then drains against a
per-process token bucket (`src/rememberstack/adapters/selfhost/queue.py:108-136`);
100k unique notifications can therefore become a useless wake storm after the
bucket is empty.

Freeze one crash-safe fan-out protocol before coding. The simplest candidate is
a set-based `INSERT ... SELECT` of the full expected chunk set in the same spine
transaction that completes embed (or the legacy coordinator), plus a bounded
route-level wake strategy. If fan-out must be paginated, persist expected count,
cursor/completion state, and recovery ownership; the barrier must require the
fan-out manifest to be complete. Test a crash at every batch boundary and verify
no missing child can permit normalize.

## P2 findings

### P2.1 — The dead-letter policy mentions a skip operation that does not exist or have barrier semantics

The design says a dead letter blocks until replay succeeds "or ops deliberately
skips" (`plan/designs/chunk_level_extract_design.md:117-119`), while its failure
table names only replay (`plan/designs/chunk_level_extract_design.md:138-142`) and
D84 rejects automatic skipping (`decisions.md:3275-3277`). The ledger exposes
explicit dead-letter replay (`src/rememberstack/spine/work_ledger.py:324-396`),
but no reviewed operation that turns an E2 dead letter into an auditable,
barrier-satisfying extraction result. Merely changing `processing_state` to
`skipped` would still leave `chunk_already_extracted` false.

Make v1 replay-only and remove the skip wording, or specify a separate explicit
override with authorization, reason/audit record, readiness semantics, and an
output marker that downstream code can distinguish from successful extraction.
Expose the parent version/representation and remaining blocked-child count in
DLQ/readiness inspection so operators can find the impact without manually
joining payload JSON.

### P2.2 — The proposed UMC backlog metric is neither complete nor safe for scaling

The proposed metric counts only `pending` and `running`
(`plan/designs/chunk_level_extract_design.md:170-179`). `failed` is the normal
retryable/backoff state (`src/rememberstack/model/processing.py:32-40`), so an
outage can make unfinished work disappear from that metric. Conversely,
`pending` also includes budget-parked and future `not_before` work; counting it as
immediately runnable can scale replicas against an exhausted budget. The design
also says no public API is required while leaving UMC a choice between direct
database observation and an unspecified safe export
(`plan/designs/chunk_level_extract_design.md:128-134`,
`plan/designs/chunk_level_extract_design.md:170-176`). That is an aspiration, not
an integration contract.

Define separate, deployment- and lane-scoped gauges: due queued work
(`status IN ('pending','failed')`, `not_before <= now()`, excluding budget
parking), running work, retry-backoff work with next due age, budget-parked work,
and dead letters. Define oldest-due age and the authenticated aggregate surface
UMC actually reads. If UMC autoscaling remains out of scope, narrow D84's claim
to "chunk rows make a future backlog metric possible" rather than saying the
current pending/running count is the correct signal.

### P2.3 — Work identity validation and diagnostic content identity need to be explicit

The payload duplicates `chunk_id`, but the handler contract only says to locate
the chunk matching `work.target_id` / payload `chunk_id`
(`plan/designs/chunk_level_extract_design.md:67-75`,
`plan/designs/chunk_level_extract_design.md:94-101`). Source lookup is currently
by payload representation alone and is not deployment-scoped
(`src/rememberstack/spine/chunk_catalog.py:162-174`). The new branch must reject
unless `payload.chunk_id == work.target_id`, the chunk belongs to the named
representation/version/chunker generation, and all belong to
`work.deployment_id`. Otherwise a corrupt/stale internal row can meter one target
while writing another coordinate.

The design also copies the document's `content_hash` to each chunk row
(`plan/designs/chunk_level_extract_design.md:77`), although the ledger schema
defines sub-document hashes as parent hash plus a target salt for diagnostics
and replay (`src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py:75-94`).
Choose and document a chunk-scoped diagnostic hash, preferably one tied to the
actual extraction input identity, while retaining `extraction_input_hash` as
D56's semantic reuse authority.

## Barrier and race acceptance contract

The implementation tests should force, rather than merely simulate, these
interleavings:

1. Two last chunks both finish output before either processing row completes;
   exactly one correct version-target normalize row appears after both success
   transitions, never before.
2. A chunk commits output, then its work attempt fails before ledger completion;
   normalize stays absent; replay makes no provider call and can open the
   barrier.
3. A chunk dead-letters with and without a previously committed output marker;
   both remain blocking under the stated ledger policy.
4. Fan-out crashes before insertion, after a proper prefix, and after all child
   inserts but before parent completion; recovery converges on the full expected
   set.
5. Legacy and new coordinators race to fan out the same chunks; the unique key
   produces one job per chunk and normalize retains the correct
   `DOCUMENT_VERSION` target and representation payload.
6. An old E2 worker is present while chunk production is attempted; the rollout
   gate prevents it from claiming new-grain work.
7. Readiness and connector-cycle finalization remain blocked by pending,
   retrying, and dead-lettered children and unblock after successful replay.
8. A 100k-chunk synthetic representation demonstrates bounded per-job reads,
   bounded fan-out memory, an acceptable fan-out transaction/query plan, and no
   per-row notification storm.

## What must change before implementation

1. Replace the handler-level `maybe_enqueue_normalize` sketch with an atomic
   ledger completion/barrier contract that checks both full fan-out coverage and
   successful output.
2. Update the design inventory for version-level consumers, specifically
   pipeline readiness and connector-cycle finalization, including zero-chunk and
   DLQ semantics.
3. Add a two-sided mixed-image rollout/rollback protocol; do not enable chunk
   production while any legacy E2 handler can claim the route.
4. Replace all-chunks/all-sections/whole-document per-job reads with a bounded
   target-plus-neighbours contract, and require a set-based barrier.
5. Freeze a crash-safe, BEAM-scale fan-out and wake-delivery protocol rather than
   leaving single-transaction versus batches open.
6. Make the v1 dead-letter policy unambiguously replay-only, or fully design the
   audited manual-skip path.
7. Correct the UMC metric and specify its observation surface, or explicitly
   defer operational autoscaling claims.
8. Require strict target/payload/tenancy validation and settle the chunk
   `content_hash` diagnostic contract.
9. Expand acceptance tests to cover the forced races, legacy rollout,
   downstream readiness, crash boundaries, and 100k-chunk load case above.

With those changes, D84's chunk-grain decision should proceed without changing
Claimify prompts or making normalize chunk-scoped.
