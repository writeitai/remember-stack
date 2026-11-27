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

Stop the prepared PostgreSQL 18 cluster and make two physical copies,
preserving HNSW indexes, roles, and timestamps. Use the same role and server
settings in both copies. Keep writers stopped. Set `$BASELINE_URL` and
`$TARGET_URL` to their SQLAlchemy connection URLs.

```bash
previous="$(mktemp -d)"
git archive 178d72daa src/rememberstack | tar -x -C "$previous"
REMEMBERSTACK_DATABASE_URL="$TARGET_URL" uv run alembic upgrade head
PYTHONPATH="$previous/src:." uv run python -m benchmarks.d163_qualification capture \
  --database "$BASELINE_URL" --case case.json --output baseline.json --timeout 180
PYTHONPATH=src:. uv run python -m benchmarks.d163_qualification capture \
  --database "$TARGET_URL" --case case.json --output target.json --timeout 180
PYTHONPATH=src:. uv run python -m benchmarks.d163_qualification compare \
  --database "$TARGET_URL" --baseline baseline.json --target target.json
```

Each case has `deployment_id`, `embedding_model`, a fixed numeric `vector`,
and `evaluated_at`. For scoped comparisons supply `entity_ids` and the subset
`ranking_entity_ids`, plus a `predicate` to exercise filtered nomination.
The case can specify `time` (the public current, at, overlap or history
arguments), `believed_at` for the ledger check, and distinct `candidate_keys`
as `[kind, UUID]` pairs. Omit keys to compare every attested fact embedding
in the deployment. `k` defaults to 50. Keep the captures private. Complete
confirmation rows include labels and other store data.

The capture records the actual broad candidate batches before client
deduplication, actual batch-gate SQL rows, both scoped nomination paths and
the keyed path with their caps removed, and every final confirmation column.
It also checks nomination-to-confirmation agreement on the same candidates
and compares normal ordered top-k outputs. Compare requires identical ordered
raw batches, then runs `EXCEPT ALL` in each direction over the gate rows.
Use a diagnostic timeout sufficient for the previous gate to finish.

[Oracle and latency harness](../../benchmarks/d163_qualification.py) ·
[Staged restore](../../benchmarks/d163_staged_restore.py)

## Run the latency gate

On each PostgreSQL 18 stratum, run the target with 2 vCPU and 4 GB, fixed
vectors, and no concurrent readers. The hosted case specifies
`"batch_sizes": {"observation": 804, "relation": 535}`. Use scoped
anchors below the raw-row budget. Run with sparse statistics and again after
`ANALYZE`, retaining the JSON, which includes the executed custom and generic
plans, their buffer counts and planning times.

```bash
PYTHONPATH=src:. uv run python -m benchmarks.d163_qualification latency \
  --database "$TARGET_URL" --query-dsn "$TARGET_QUERY_DSN" --case case.json \
  --stratum hosted --plans --samples 40 --output latency.json
```

Use `--stratum 300k`, `3m`, or `120-leaves` for the other gates. The latter
uses 120 monthly leaves plus the default. The harness times SQL without
EXPLAIN and includes the native operation at k=50, candidate depth 200.
It checks the declared p95 limits and records the broad query-space reads.
Pass the previous release on `PYTHONPATH` and the baseline login to measure
the same broad SQL on the reference. The BEAM samples exercise both the
typed error for the original `label` and successful `fact_label AS label`.

On a disposable fixture seeded by `seed_corpus`, the following adds unrelated
chunks and leaves and copies fixed fact/evidence cases. Keep the case file
and fixed vector from the seed. This is useful for work amplification checks
with known counts.

```bash
REMEMBERSTACK_DATABASE_URL="$TARGET_URL" PYTHONPATH=src:. uv run python \
  -m tests.spine.d163_scale_fixture --case case.json --chunks 300000 --analyze
```

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
capture with the synthetic profile. The paired oracle covers stored endpoints
that resolve to one requested ranking anchor inside a larger eligible set.
It compares coverage, distance and ordered top-k in both scoped paths, and
counts repeated nominations and multiple evidence links for both fact
kinds. The split-link scenario gives source membership and in-scope
support to separate links in both kinds. The separate corrupt-store test
proves that a foreign redirect cannot round-trip back to a valid survivor.

[Migration qualification](../../src/tests/spine/test_d163_migrations_pg.py) ·
[Semantic and plan checks](../../src/tests/spine/test_d163_eligibility_pg.py) ·
[Collector handoff](../../src/tests/adapters/test_minio_storage_capture.py)
