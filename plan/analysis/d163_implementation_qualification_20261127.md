# D163 implementation qualification

D163 changes the physical lookup order for current-belief evidence and native
fact source membership. P1 ranking, its HNSW stream, candidate depth, final
ordering, and text and embedding inputs use the same code. The archive-aware
presentation expressions come from the immutable `p9_55_0076` definition.

[Decision D163](../../decisions.md#d163-drive-fact-eligibility-from-its-own-evidence) ·
[Qualification contract](../designs/shared_retrieval_eligibility_design.md#91-d163-fact-path-qualification)

## Run the oracle

Use an isolated restore of each retained conv-26, conv-30, conv-47 and
hosted-shaped store. Verify backup identity on its recorded PostgreSQL version.
For a PostgreSQL 19 source, provision an empty PostgreSQL 18 reference with the
same revision, extension versions and partition ranges. Install its
database-bound query and graph roles. The stage command saves the reference as
`<database>_ddl` and restores the database under its original name. Specify
PostgreSQL 18 client binaries with `--pg-dump` and `--pg-restore` if needed.

```bash
PYTHONPATH=src:. uv run python -m benchmarks.d163_staged_restore stage \
  --source "$RESTORED_SOURCE_LIBPQ_DSN" --reference "$PG18_REFERENCE_LIBPQ_DSN" \
  --archive schema.dump
REMEMBERSTACK_DATABASE_URL="$PG18_REFERENCE_URL" uv run alembic upgrade p9_55_0076
PYTHONPATH=src:. uv run python -m benchmarks.d163_staged_restore preflight \
  --database "$PG18_REFERENCE_LIBPQ_DSN" --query-dsn "$PG18_QUERY_DSN"
```

Stage copies writable columns with text COPY between pre-data and post-data,
including the source's matching `public.part_config` rows and sequence
state. It verifies counts and key-ordered hashes (including vectors and
current pointers), the pointer targets, and the database ACL. Preflight
checks the registered partition parents, controls and intervals at the
baseline and queries fact history through the database's actual query login.
Each query connection must see the uniquely marked session that verified the
database. This detects crossed connections even between physical clones that
share the database name, login name and system identifier.

Stop the prepared PostgreSQL 18 cluster and make two physical copies,
preserving HNSW indexes, roles, and timestamps. Use the same role and server
settings in both copies. Keep writers stopped. Set `$BASELINE_URL` and
`$TARGET_URL` to their SQLAlchemy connection URLs. Set
`$BASELINE_QUERY_DSN` and `$TARGET_QUERY_DSN` to the libpq connection strings
for the database-bound query logins.

```bash
previous="$(mktemp -d)"
git archive 178d72daa src/rememberstack | tar -x -C "$previous"
REMEMBERSTACK_DATABASE_URL="$TARGET_URL" uv run alembic upgrade head
PYTHONPATH="$previous/src:." uv run python -m benchmarks.d163_qualification capture \
  --database "$BASELINE_URL" --query-dsn "$BASELINE_QUERY_DSN" \
  --case case.json --output baseline.json --timeout 180
PYTHONPATH=src:. uv run python -m benchmarks.d163_qualification capture \
  --database "$TARGET_URL" --query-dsn "$TARGET_QUERY_DSN" \
  --case case.json --output target.json --timeout 180
PYTHONPATH=src:. uv run python -m benchmarks.d163_qualification compare \
  --database "$TARGET_URL" --baseline baseline.json --target target.json
```

Each case has `deployment_id`, `embedding_model`, a fixed numeric `vector`,
and `evaluated_at` with a timezone offset. For scoped comparisons supply
`entity_ids` and the subset `ranking_entity_ids`. Supply a `predicate` to
exercise filtered nomination and final confirmation.
The case can specify `time` (the public current, at, overlap or history
arguments), `believed_at` for the ledger check, and distinct `candidate_keys`
as `[kind, UUID]` pairs. Omit keys to compare every attested fact embedding
in the deployment. `batch_sizes` also compares fixed P1 batches of those sizes
and their complete final confirmation rows. `k` defaults to 50 and the native
read uses the corresponding default nomination depth. Keep captures private
because they include complete public view rows, labels and other store data.

The capture records the native broad read's HNSW batches before deduplication
and its batch-gate SQL rows. For each broad or configured scoped native read,
it records every column from each executed final confirmation statement before
the result cap, including the SQL `coverage` and `nomination_rank`. Each row
keeps its statement number so differences between batches are visible. The
capture also removes SQL caps for keyed, default scoped and filtered scoped
nomination and replays confirmation to check agreement on those inputs.
Filtered confirmation receives the nominated IDs and the same predicate.
For each native read the capture records ordered final facts, contradiction
member SQL rows before the member cap, and the returned contradiction blocks
with ordered co-members and both member counts.

Compare requires the expected runtime manifest at each schema head and matching
effective sandbox JIT settings. It checks ordered raw batches, ordered final
facts with their contradiction blocks, the fixed-instant ordered BEAM rows,
and the typed outcomes of the shipped BEAM statements. It runs `EXCEPT ALL`
in both directions over the SQL cuts, including native final confirmation,
contradiction membership and all five broad sandbox views. The comparison
counts repeated rows and ignores incidental order in those broad reads.

The broad export runs as the query login and uses the recorded diagnostic
`--timeout` to read every row before the response caps. It reads the installed
`facts_current` definition from the catalog and replaces its statement clock
with the case instant in the diagnostic SQL. This fixes both membership and
the returned `evaluated_at` value. The BEAM replay uses the same pinned
definition. The aggregate keeps the shipped `ORDER BY 2 DESC LIMIT 25` so the
comparison checks the rows in the order BEAM consumes them, including the
predicate selected when a tie spans the cutoff. The fact sample uses
`fact_label AS label` at the same instant. Capture also runs the shipped SQL
under the interactive limits at the live statement clock and records the
typed outcomes. The broad-view checks record executed plans, including
planning time and buffer use. A canceled plan reports its typed error.
Compare checks completion and truncation on both sides and requires a plan
for each completed broad read. Matching statement timeouts still require
equal complete exports. Regenerate both captures for the corrected BEAM replay.

[Oracle and latency harness](../../benchmarks/d163_qualification.py) ·
[Staged restore](../../benchmarks/d163_staged_restore.py)

## Run the latency gate

On each PostgreSQL 18 stratum, run the target with 2 vCPU and 4 GB, fixed
vectors, and no concurrent readers. Every qualification case specifies
`"batch_sizes": {"observation": 804, "relation": 535}`. Supply current
scoped anchors below the raw-row budget. Before timing, the gate counts chunks
in the case deployment and checks monthly and default leaves on both `chunks`
and `chunk_claims`. It verifies that both batches contain the required number
of distinct facts with nonzero embeddings for the specified model and current
fact input policy. With supplied keys, it takes the first declared number of
IDs per kind. Each batch must return an eligible fact with supporting evidence,
and a native scoped read must return at least one eligible fact before timing.
The report records the observed scale and all three control IDs.
Run with sparse statistics and again after `ANALYZE`, retaining the JSON,
which includes the executed custom and generic plans, their buffer counts
and planning times.

```bash
PYTHONPATH=src:. uv run python -m benchmarks.d163_qualification latency \
  --database "$TARGET_URL" --query-dsn "$TARGET_QUERY_DSN" --case case.json \
  --stratum hosted --plans --samples 40 --output latency.json
```

The hosted stratum uses 812 chunks and ten leaves including the default.
Use `--stratum 300k` or `3m` for exactly 300,000 or 3,000,000 chunks with ten
leaves. Use `120-leaves` for 120 monthly leaves plus the default. Keep the
case JSON and corpus fixed, add the leaves on a copy of the ten-leaf store,
and pass the ten-leaf latency report to the new run.

```bash
PYTHONPATH=src:. uv run python -m benchmarks.d163_qualification latency \
  --database "$PARTITION_URL" --case case.json --stratum 120-leaves \
  --ten-leaf-reference latency.json --plans --samples 40 --output latency-121.json
```

The gate checks both runs' chunk counts and ordered batch IDs. The reports
include a hash per batch of its raw evidence links and linked claim occurrence
coordinates. The harness times SQL without EXPLAIN and includes the native
operation at k=50, candidate depth 200.
Each standalone batch sample uses a fresh P1 ranked-read transaction on
`--database`, the P1 connection URL. The timer starts after connection setup
and includes SQL planning and execution. The report's `p1_settings` records
the inherited JIT thresholds and parallel worker limits. Use the deployment's
P1 connection settings. The stratum gate checks the declared p95 limits and,
with the query login, records broad query-space reads. The native measurements
use mention-backed endpoints.
The `sandbox` operation measures those reads separately. Pass the previous
release on `PYTHONPATH` and its login to measure the same SQL on the reference.
Use the deployment's query-login settings. The report's `sandbox_settings`
records the JIT settings visible to that login, including the active `jit`
flag and cost thresholds.
Both releases capture the shipped BEAM queries with their SQL limits and
40-row request cap. The original `label` query returns a typed
`execution_error`. The sandbox report times `fact_label AS label` separately
for diagnosis. Warm-ups and samples share the same interactive statement-second
quotas as the shipped BEAM calls. The harness waits for quota before starting a
sample timer. It records quota and concurrency refusals in `admission_failures`
and computes percentiles from admitted requests.

On a disposable fixture seeded by `seed_corpus`, the following adds unrelated
chunks and copies fixed fact and evidence cases. The 1,200-relation population
allows the native cursor to reach its cutoff at depth 200. The fixed P1 batch
still uses 535 relations and 804 observations. Timing cases use distinct
fixed vectors within each fact kind so ties across the whole index do not
force an ANN scan to its bound.

```bash
REMEMBERSTACK_DATABASE_URL="$TARGET_URL" PYTHONPATH=src:. uv run python \
  -m tests.spine.d163_scale_fixture --case case.json --chunks 300000 \
  --relations 1200 --analyze
```

Grow a fresh `seed_corpus` fixture for the scoped boundary. This mode adds
relations from the seed hub to distinct objects, each made visible by a live
document bridge. It copies the prototype's evidence to each relation.

```bash
REMEMBERSTACK_DATABASE_URL="$SCOPE_URL" PYTHONPATH=src:. uv run python \
  -m tests.spine.d163_scale_fixture --case scope-case.json --chunks 812 \
  --scope-rows 50000 --analyze
PYTHONPATH=src:. uv run python -m benchmarks.d163_qualification scope \
  --database "$SCOPE_URL" --case scope-case.json --output scope-50000.json
```

The gate counts exactly 50,000 raw rows in the requested scope and requires
at least 25,000 distinct visible endpoints. The timed native read must return
50 facts and complete within its operation deadline. Repeat the fixture
command with `--scope-rows 50001`, then run `scope` with
`--expect-scope-boundary --output scope-50001.json`. This run verifies 50,001
raw rows and requires the `entity_scope_too_broad` response.

## Migration and review notes

The three revisions replace the memory survivor, replace the graph projection,
then add the keyed claim lookup. The migration test observes the published
revision after each DDL commit in both directions. It runs the old saved-query
HTTP route through the query login and the one-hop guard through the graph
role. A retry log barrier lets a second old reader run after SQLSTATE
`55P03` while the first reader still holds its lock. The test uses the shipped
configuration and the sandbox's 250 ms interactive lock limit. It also
exercises the setup registry transaction in both directions, verifying that
the old hash and active state remain visible until the commit and that the
same saved-query version resumes after revalidation.

The collector test installs the previous helper bodies, runs the first
incoming revision and checks both collector admission errors. It then
completes the migration, installs the current helpers and runs a real MinIO
capture with the synthetic profile. The upgrade guide documents when to
pause and resume capture. The paired oracle covers stored endpoints
that resolve to one requested ranking anchor inside a larger eligible set.
It compares coverage, distance and ordered top-k in both scoped paths, and
counts repeated nominations and multiple evidence links for both fact
kinds. The split-link scenario gives source membership and in-scope
support to separate links in both kinds. The separate corrupt-store test
proves that a foreign redirect cannot round-trip back to a valid survivor.
The binding P1 search design already points both §8.3 and §8.5 at the
candidate-bound source checks and the shared visibility rule.

[Migration qualification](../../src/tests/spine/test_d163_migrations_pg.py) ·
[Semantic and plan checks](../../src/tests/spine/test_d163_eligibility_pg.py) ·
[Collector handoff](../../src/tests/adapters/test_minio_storage_capture.py) ·
[Upgrade guide](../../website/src/app/docs/self-hosting/upgrades/page.mdx) ·
[P1 confirmation contract](../designs/postgres_p1_search_projection_design.md#83-nominate-confirm-continue-d142)

## Local evidence

Before the round-one oracle changes, physically cloned PostgreSQL 18.6 clusters compared
`178d72daa` at `p9_55_0076` with D163 at `p9_58_0079`. Counts below are
per side. A gate row may appear in several cuts, and repeated nomination
ranks count separately.

| Synthetic case | Uncapped keyed eligible IDs | Default scoped | Filtered scoped | Gate rows across all cuts | Ordered raw HNSW rows | Difference |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Current | 11 | 11 | 7 | 73 | 20 | 0 |
| At the effective-period boundary | 12 | 12 | 8 | 82 | 20 | 0 |
| Overlap | 13 | 13 | 9 | 87 | 20 | 0 |
| History | 14 | 14 | 9 | 93 | 20 | 0 |
| 812 chunks, 2,625 observations, 535 relations | 3,149 | 3,149 | 528 | 16,582 | 400 | 0 |
| Total | 3,199 | 3,199 | 561 | 16,917 | 480 | 0 |

The wide case's fixed batch cuts kept 802 of 804 observations and 528 of 535
relations on each side. The four nomination paths returned identical ordered
top-k on all five cases, including the merged-anchor winner at k=1. Small-case
captures include the earlier belief pin, cleared mode, retraction, unready
edition, deleted origin, replaced reading, source/time split and corrupt
claim coordinates. Corrupt foreign redirect rejection was checked separately.
The D159 pending and deferred review regressions matched the public and native
support-state expressions before archive, after archive, and after unarchive.

The staged PostgreSQL 18.6 transfer matched hashes for 99 tables, including
`public.part_config`, and passed pointer and database ACL checks. Baseline
preflight found all seven partition registrations and read 18 fact-history
rows through the real login. Stock `SelfHostProfile.setup` published the old,
new, and restored old hashes on a disposable deployment with synthetic stores
and zero provider calls. Section backfill reported missing object bodies in
that setup smoke. The migration regression exercises atomic visibility and
saved-query revalidation for the same active version in each direction.

The sandbox runs in this section used the earlier harness's JIT-off override.
On the wide synthetic pair, both BEAM queries with valid columns completed.
Five warm sandbox samples gave old/new p95 of 0.234/0.565 s for the aggregate
and 0.264/0.508 s for the sample. The original `label` returned
`execution_error` on both. The unfiltered `fact_claim_evidence_live` read hit
the five-second statement limit on both releases (5.046/5.049 s). The other
four unfiltered reads completed with the same returned row counts.

The same completion and row-count pattern held for the 812- and
300,000-chunk timing fixtures as chunk count grew with the fact cohort held
fixed. Both releases returned `statement_timeout` for the unfiltered fact
evidence read in both fixtures.

On the 812-chunk fixture, growing observation evidence from 5,244 to 62,840
links and relation evidence from 2,403 to 28,517 preserved the same outcomes
and returned row counts on both releases. Growing to 121 leaves with that
fanout caused both unfiltered `facts_current` reads to hit the same sandbox
deadline. The combined sequence also reached the BEAM sample quota on both
releases. In isolation, the corrected sample returned 40 rows on each
release, with old/new p95 of 0.731/0.798 s over three warm samples.

The full unfiltered exports matched 15,757 rows per side with zero
`EXCEPT ALL` differences across the five views. At the diagnostic 201-row cap,
executed plans for fact evidence used 58,397 versus 12,768 shared buffer hits.
Evidence lineage used 58,765 versus 13,136. Facts current used 44,719 versus
48,903. The public output and the sandbox deadline behavior were checked
separately.

The earlier scope fixture reused the prototype endpoints for every clone. Its
native read returned 50 facts at 50,000 raw rows in 20.456 s on the separate
test VM. At 50,001 raw rows it returned `entity_scope_too_broad` in 0.077 s.

### Round-one oracle rerun

The updated harness compared the current, at, overlap and history cases on
physical PostgreSQL 18.6 clones. Each side returned 508 rows across 68 SQL
cuts with zero `EXCEPT ALL` differences. The raw HNSW batches and ordered
native final facts matched for all four cases. The shipped BEAM statements
matched in row order and typed outcomes through the two database query
logins. The corrected sample completed on each release in the separate
sandbox run.

### Round-two oracle rerun

Restore preflight and target capture rejected crossed query DSNs on physical
PostgreSQL 18.6 clones with the same database name, query login and system
identifier. With the correct DSNs, current, at, overlap and history matched
588 rows per side across 80 SQL cuts, including 70 contradiction rows.
Ordered raw HNSW batches and final facts, returned contradiction blocks and
BEAM outcomes matched. Removing one captured contradiction row or changing
one reported member total independently failed comparison.

The first sandbox run with inherited `jit=on` reproduced a target-only BEAM
aggregate timeout. The planner costed the recursive redirect step as several
point lookups. The plan exceeded the login's JIT optimization threshold of
500,000, and a diagnostic execution spent about 10.2 seconds on compilation.
One initial seed and a unique redirect key bound each recursion iteration to
one row. Expressing that bound brought the aggregate's estimated cost from
595,807 to 62,827 on the synthetic fixture. With the role's original JIT
settings, both releases then completed all five broad view reads, the BEAM
aggregate and the corrected sample under the five-second sandbox deadline.

### Round-three oracle rerun

Current, at, overlap and history matched 672 rows per release across 92 SQL
cuts on physical PostgreSQL 18.6 clones. These included 154 rows from executed
native confirmation statements. The ordered raw batches, native top-k and
BEAM outcomes matched. A regression changed a single label in one native SQL
batch on a row beyond top-k. That cut reported one missing and one extra row.
The replay cuts and ordered results matched.

### Round-four oracle rerun

The four time modes matched 1,060 rows per release across 112 SQL cuts on
physical PostgreSQL 18.6 copies. The five broad views accounted for 388 of
those rows. All five interactive reads completed on both releases with
inherited `jit=on`, and both captures recorded executed plans and buffer
counts. A PostgreSQL regression changed only `evidence_lineage.source_handle`
while preserving the sandbox row count, the ordered native results and the
BEAM results. The broad cut reported missing and extra rows. A separate
duplicate in that cut was counted once as extra.

The latency CLI rejected a 14-chunk fixture presented as `300k` with 804
nonexistent observation IDs and 535 nonexistent relation IDs. After growing
the fixture to 812 chunks and ten leaves, it rejected the same IDs for
`hosted`. The PostgreSQL regression also checks a stale model stamp, a stale
policy stamp and an attested fact without eligible support.

The valid hosted-size synthetic run passed with inherited `jit=on` and 40
warm samples per workload. The 804-input observation batch returned 802
rows at 0.089 s p95. The 535-input relation batch returned 530 rows at
0.067 s p95. Native broad and scoped p95 were 2.83 s and 1.49 s. Both modes
completed all samples and returned 50 facts in their final samples. Both
custom and generic batch plans completed.

### PR #684 round-one verification

On a synthetic PostgreSQL 18.6 store, the previous release and D163 matched
240 rows per side across 28 SQL cuts. The fixed BEAM instant was one microsecond
before the facts' exclusive validity end. Both releases counted 24 facts at
that instant, and both counted zero at the shipped statement clock. The
original `label` query returned `execution_error`. Both fact diagnostics
returned zero rows at the exclusive end in the regression.

On the local 2-vCPU PostgreSQL 18.6 fixture, the 50,000 raw scope rows
produced 49,978 distinct visible endpoints. The native read completed in
6.87 s and returned 50 facts. Adding one relation gave exactly 50,001 raw
rows and returned `entity_scope_too_broad` in 0.043 s.

The matched 812-chunk runs used 10 and 121 leaves with the same 804 observation
and 535 relation inputs. Both ran 40 warm samples per workload on the local
2-vCPU, 2.8-GiB VM with JIT disabled. The 121-leaf observation and relation
batches finished at 0.289 s and 0.208 s p95. Native broad and scoped p95 were
1.523 s and 1.553 s, with 50 facts returned in both modes. The latency
regressions reject a wrong chunk count, different batch IDs, a changed
raw evidence stance and a known-empty scoped read before timing.

All seven interactive sandbox workloads completed 40 samples on the small
synthetic target fixture with JIT disabled and zero admission failures. The
quota regression uses the executor's admission accounting and checks that
wait time and a rejected sample stay outside the percentiles.

Ruff formatting and lint, Pyright, the import contracts and the test inventory
passed. The selected suites passed 196 unit tests and 183 PostgreSQL tests,
with the expected D122 reuse failure.

### PR #684 round-two verification

The paired replay keeps the shipped aggregate's count order and 25-row limit
at the fixed instant. A PostgreSQL fixture with 26 predicate groups exercises
a tie across the cutoff. The comparison rejects a different tied predicate
at row 25 even when the other rows and typed outcomes agree. Captures on
physical PostgreSQL 18.6 copies matched all ordered BEAM rows, ordered raw
HNSW batches and ordered native final facts across the four time modes.
The 112 SQL cuts contained 1,060 rows per release, with zero differences.

The scale rerun used PostgreSQL 18.6 on a 2-vCPU, 4-GiB ARM64 VM with
refreshed statistics. P1 inherited `jit=on`, `jit_above_cost=100000`,
`jit_inline_above_cost=500000`, `jit_optimize_above_cost=500000` and
`max_parallel_workers_per_gather=2`. The 300,000-chunk copy kept the 3M
fixture's facts, evidence, claim occurrences and fixed case. The 121-leaf
copy added empty monthly leaves to that 300,000-chunk workload. The harness
matched candidate IDs and evidence fingerprints. Each workload ran 40 warm
samples. Times are p50 / p95 seconds.

| Chunks and range leaves | Observation batch | Relation batch | Native broad | Native scoped |
| --- | ---: | ---: | ---: | ---: |
| 300,000, 10 | 0.785 / 0.814 | 0.793 / 0.821 | 1.709 / 2.029 | 0.348 / 0.375 |
| 3,000,000, 10 | 0.731 / 0.780 | 0.868 / 1.060 | 1.693 / 2.370 | 0.424 / 0.806 |
| 300,000, 121 | 3.225 / 4.321 | 3.098 / 3.812 | 6.124 / 7.255 | 1.203 / 1.244 |

All three strata met their targets. The 804 observation inputs and 535
relation inputs returned 802 and 532 eligible rows, respectively. Both
native modes completed all 40 timed calls on each stratum and returned 50
facts in their final samples.

In both plan modes, increasing from 300,000 to 3 million chunks held the
chunk and occurrence leaf loops at 16,070 and 4,020 for observations and
10,750 and 2,695 for relations. At 121 leaves the counts rose to 194,447 and
93,274 for observations and 130,075 and 62,312 for relations. Planning took
11.3–21.8 ms at ten leaves and 68.5–102.4 ms at 121 leaves. Across the
strata, the plans recorded 22,241–23,044 shared hits for observations and
15,032–15,566 for relations, with zero shared reads.

Ruff formatting and lint, Pyright, the import contracts and the test inventory
passed. The selected suites passed 176 benchmark tests and 101 PostgreSQL
tests, including the keyed-probe plan checks with sparse and refreshed
statistics.

### Synthetic latency

The round-three smoke run used 812 chunks and 121 leaves on a 2-vCPU, 3-GiB
VM. P1 inherited `jit=on` and `max_parallel_workers_per_gather=2`. Across
three warm samples, median wall time was 3.62 s for the 804-input observation
batch and 4.37 s for the 535-input relation batch. Both native modes returned
50 facts, and both custom and generic batch plans completed. Qualification
requires 40 warm samples on the specified 2-vCPU, 4-GB hardware.

The table below records standalone batch timings from the earlier harness with
JIT and parallel-worker overrides. Standalone latency qualification uses the
inherited settings recorded by the current harness.

PostgreSQL 18.6 on local ARM64 VMs with two vCPUs and 4 GB. Each cell reports
p50 / p95 seconds over 40 warm samples. The timing fixture used 804
observation and 535 relation inputs, with 802 and 532 eligible, respectively.
The 812-chunk read used sparse statistics after growth. The larger strata
used refreshed statistics. Every native read completed with 50 facts.

| Chunks and range leaves | Observation batch | Relation batch | Native broad | Native scoped |
| --- | ---: | ---: | ---: | ---: |
| 812, 10 | 0.081 / 0.116 | 0.045 / 0.055 | 2.127 / 2.587 | 0.392 / 0.497 |
| 300,000, 10 | 0.087 / 0.119 | 0.050 / 0.070 | 2.152 / 2.307 | 0.387 / 0.430 |
| 3,000,000, 10 | 0.074 / 0.076 | 0.041 / 0.046 | 2.016 / 2.085 | 0.367 / 0.389 |
| 3,000,000, 121 | 0.186 / 0.281 | 0.119 / 0.198 | 7.705 / 9.110 | 1.448 / 1.514 |

In the custom and generic batch plans, increasing from 300,000 to 3,000,000
chunks left chunk and occurrence leaf loops at 16,070 / 4,020 for observations
and 10,750 / 2,695 for relations. Planning at 300,000 chunks took 17–108 ms,
with 23,044 / 15,032 shared hits. The 3-million-chunk plans took 13–15 ms
and 22,241 / 15,032 shared hits. Both strata had zero shared reads.
With 121 leaves, chunk and occurrence leaf loops rose to 194,447 / 93,274
for observations and 130,075 / 62,312 for relations. Planning took 79–126 ms.
The plans used 21,439 / 14,497 shared hits and zero shared reads. Both native
reads completed within the existing 120-second operation deadline.

The round-one rerun used the 812-chunk synthetic fixture with mention-backed
endpoints. With 40 warm samples per workload, p95 was 0.078 s for 804
observation inputs, 0.044 s for 535 relation inputs, 0.686 s for the native
broad read and 0.710 s for the native scoped read. Both native modes completed
with 50 facts.

The executed negative probes touched one evidence leaf in both plan modes
with one or seventeen raw links. Both stored relation endpoints seeded
their own walk and the merged endpoint took its one keyed redirect.

The same-document regression grows the historical-origin and carried-evidence
documents to 20,000 total chunks. A preliminary run with ordinary
equality let an index on deployment and document filter out about 30,000
unrelated rows per carried relation. The regression supplies document bridges
for its endpoints and measures each history, carried and time-scope chunk
probe in custom and generic plans with sparse and refreshed statistics.

## Local quality commands

The PostgreSQL integration reruns use a disposable PostgreSQL 18 server
with `max_locks_per_transaction=256` and a local MinIO endpoint. The guard
and view suites each use their own empty `capture_guard_*` database through
`REMEMBERSTACK_STORAGE_GUARD_TEST_DATABASE_URL`.

Ruff format and lint, Pyright, imports, test inventory and the capture
fixture check passed. The unit inventory passed 3,956 tests with 11 skipped.
The isolated PostgreSQL and MinIO run passed all 214 selected tests, including
the migration chain, the saved-query cutover and the collector handoff. The
separate native collector guard and original-view suites passed 9 and 5
tests. The pending and deferred archive-review cases both passed.

The broader checkpoint inventory finished with 1,655 passed, 16 skipped and
2 expected failures. PostgreSQL lock-table exhaustion during `DROP SCHEMA`
caused 24 Work Ledger setup errors. The checkpoint also ran an early D163
assertion that read the private helper without qualifying `public`, which
returned `UndefinedTable` under the query role's search path. The final
214-test rerun passed Work Ledger and the corrected, schema-qualified
privilege assertion.

The round-one selection passed 270 unit tests and 129 PostgreSQL and MinIO
tests, with one expected D122 failure. Ruff formatting and lint, Pyright,
the import contracts and the test inventory passed.

The round-two selection passed 496 unit tests, 334 integration tests with
PostgreSQL 18.6 and MinIO, and 23 ingestion reuse tests. The D122 reuse
regression had its expected failure. Ruff formatting and lint, Pyright,
the import contracts, the test inventory and the capture fixture check passed.

The round-three harness and D163 eligibility selection passed all 18 tests
with PostgreSQL 18.6. This included the native-only mutation and the test that
reads inherited settings inside each timed P1 batch transaction. Ruff formatting
and lint, Pyright, the import contracts and the test inventory passed.

The round-four qualification, eligibility and migration selection passed all
25 tests on PostgreSQL 18.6. The LoCoMo protocol and runner selection passed
145 tests, including Full-v47's rejection of the D163 manifest. Ruff
formatting and lint, Pyright, the import contracts, the test inventory and
the capture fixture check passed.

The broader selection passed 281 tests outside MinIO with one expected D122
failure. The first MinIO run used `btree_gist` 1.9, which the native capture
guard rejected. All 14 MinIO tests passed on the stock image with
`btree_gist` 1.8, including the collector handoff.

```bash
uv run ruff format --check src/ benchmarks/
uv run ruff check src/ benchmarks/
uv run pyright src/ benchmarks/ --pythonversion 3.13
uv run lint-imports
uv run python .github/ci/check_test_inventory.py
PYTHONPATH=src:. uv run python scripts/generate_storage_capture_fixture.py --check
xargs uv run pytest -q --tb=short < .github/ci/unit-paths.txt
PGOPTIONS='-c jit=off' xargs uv run pytest -q --tb=short < .github/ci/integration-paths.txt
PGOPTIONS='-c jit=off' uv run pytest -q --tb=short \
  src/tests/spine/test_{migrations,per_database_migrations_pg,work_ledger,query_space_batch_a,d153_eligibility_pg,d163_eligibility_pg,d163_migrations_pg}.py \
  src/tests/adapters/test_{p1_ranked_pg,minio_storage_capture}.py \
  src/tests/surfaces/test_d140_scoped_retrieval.py
```

The guard and view checks used `uv run pytest -q --tb=short` on
`src/tests/spine/test_storage_capture_guard_pg.py` and
`src/tests/spine/test_storage_capture_view_pg.py` in separate databases.
The archive-review check used `uv run pytest -q --tb=short
src/tests/workers/test_lifecycle_reconciliation.py -k moot_review`.

## Remaining qualification on retained stores

No local restored database or backup archive was available for LoCoMo conv-26,
conv-30, conv-47 or the hosted-shaped store. The staged copy was exercised
between independent PostgreSQL 18.6 clusters. Transferring a PostgreSQL 19
backup remains unverified. Restore and verify each retained backup on its
recorded server version, stage any PostgreSQL 19 source, and run `preflight`,
paired `capture` and `compare` as above. Run `latency --stratum hosted` with
804 observation and 535 relation inputs, 40 warm samples and the query login
on the 2-vCPU / 4-GB hosted-shaped restore. Use `sandbox` on each release to
verify unfiltered completion, including `fact_claim_evidence_live`, under its
interactive deadline.
