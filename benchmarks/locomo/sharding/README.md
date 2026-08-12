# Sharded LoCoMo runs

A full publication run must isolate every conversation in its own deployment. Before answering,
the harness proves that the deployment contains exactly that sample's documents, that all pipeline
stages completed, and that P2/P3 projections are fresh. Reusing one deployment across samples
would invalidate that guard. On one host, wiping the Compose volumes between ten conversations
makes the approximately two-hour conversations a roughly twenty-hour serial run.

That isolation also makes samples independent: multiple hosts can run disjoint sample sets under
the same protocol fingerprint, then the harness can merge their per-item records and recompute the
ordinary full-manifest score. Hosts must never process the same sample.

## Prerequisites and secrets

Every host needs:

- the same clean repository commit and protocol selection;
- the repository virtual environment with the `benchmark` extra;
- Docker with the Compose plugin and enough capacity for the scaled workers;
- the exact pinned `locomo10.json`;
- the ordinary Compose settings from `.env.example`, with real local values; and
- `REMEMBERSTACK_OPENROUTER_API_KEY` exported into the environment of both Compose and the
  benchmark CLI; and
- `REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID` exported so the driver can address the
  published P3 path (the value must match Compose).
- GNU tar with zstd support and the benchmark extra's Google Cloud Storage
  client configured through short-lived X.509 Workload Identity Federation;
  and
- `LOCOMO_BACKUP_DESTINATION` exported as the private bucket and base prefix
  (for example
  `gs://remember-stack-locomo-backups/runs`).
- `LOCOMO_GCP_PROJECT` exported as the bucket's GCP billing project.

Treat the OpenRouter key and X.509 client private key as secrets. Inject them
from the host's secret manager. Never put either value in this repository, a
shard plan, a command argument, or a log. The federated GCS principal must be
scoped to the benchmark bucket with object create/view access but no
overwrite/delete. A Google service-account JSON key is neither needed nor
permitted by the current organization policy.
Use an OpenRouter account cap as the hard monetary boundary; the harness cost
cap is a reported-spend stop threshold.

Build the image once from the checked-out revision on every host:

```bash
export REMEMBERSTACK_BUILD_REVISION
REMEMBERSTACK_BUILD_REVISION=$(git rev-parse HEAD)
docker compose build
```

The driver assumes the checkout, virtual environment, image, Compose configuration, dataset, and
environment variables already exist. It deliberately does not provision machines or distribute
secrets. It runs as root because Docker's volume mountpoints are root-owned,
and it verifies that the configured GCS bucket is readable before preparing or
running any paid sample. The preflight also writes one tiny create-only probe,
so a missing uploader grant fails before paid work. Any set of SSH-accessible
hosts works.

## 1. Plan balanced shards

`make_shards.py` uses only the Python standard library. It counts session documents and dialogue
turns for each sample, sorts conversations largest first, and assigns each one to the currently
lightest host.

Generate numbered shards:

```bash
.venv/bin/python benchmarks/locomo/sharding/make_shards.py \
  /data/locomo10.json \
  --shards 3 \
  --output /tmp/locomo-shards.json
```

Or make the JSON keys match SSH host aliases:

```bash
.venv/bin/python benchmarks/locomo/sharding/make_shards.py \
  /data/locomo10.json \
  --hosts bench-a bench-b bench-c \
  --output /tmp/locomo-shards.json
```

The output shape is:

```json
{
  "bench-a": ["conv-26", "conv-42", "conv-48"],
  "bench-b": ["conv-30", "conv-43", "conv-47"],
  "bench-c": ["conv-41", "conv-44", "conv-49", "conv-50"]
}
```

The exact assignment depends on the pinned dataset's document and turn counts; use the generated
file rather than the illustrative grouping above.

## 2. Provision hosts

On all three hosts, check out the same commit, create the virtual environment, place the pinned
dataset at an operator-controlled path, configure Compose, inject the OpenRouter key through the
environment, and build the stamped image. Keep the run directory outside the checkout if desired;
each host may use the same path because the files never share storage.

Do not copy a live Compose volume between hosts. Shards share only protocol identity and dataset
bytes, never a deployment, database, object store, or projection directory.

## 3. Run each shard

The driver accepts a comma-separated sample list, a run directory, and the dataset path. Start it
under `nohup` so loss of the SSH session does not kill the benchmark:

```bash
nohup benchmarks/locomo/sharding/run_shard.sh \
  'conv-26,conv-42,conv-48' \
  /srv/locomo-runs/publication \
  /data/locomo10.json \
  > /srv/locomo-runs/publication.log 2>&1 &
```

Repeat with the generated list on `bench-b` and `bench-c`. The first invocation prepares the full
publication manifest locally. For every assigned sample the driver:

1. verifies that an existing store has a readable off-host backup receipt,
   then and only then runs `docker compose down --volumes`;
2. starts every service with extract-claims ×3, normalize-relations ×6, and
   embed-claim ×2, then records which sample owns the new volumes;
3. ingests only that sample with the isolated-deployment confirmation;
4. polls `processing_state` until no pending, running, or retryable failed rows remain, with a
   six-hour default budget, and stops immediately on a dead letter;
5. builds projections through the Compose `operations` profile;
6. publishes the ordinary P3 mount, then runs the complete-plane answer agent
   and judge with run-absolute caps; and
7. stops the stack; archives Postgres, MinIO, application state, forget
   manifests, run state, and the published mount root; uploads them to a unique
   immutable GCS prefix with CRC32C transport validation and create-only
   preconditions; checks object sizes; reads back the manifest; and writes a
   verified receipt.

Progress is emitted as UTC timestamped log lines. Sample stores are disposable
only *after* their remote receipt verifies. A backup, upload, comparison, or
read-back failure exits non-zero with the stack stopped and volumes intact.

The following environment variables tune the driver without changing its arguments:

The driver itself freezes the complete non-secret V13 ingest identity before
Compose starts: Luna generation seats, Qwen3-Embedding-8B vector seats, Nebius
embedding host, unset provider-order fallback, and the protocol's reasoning and
completion-token settings. Ambient values for those bindings are deliberately
overridden, and ingest verifies the resulting deployment map before upload.

| Variable | Default | Meaning |
| --- | ---: | --- |
| `LOCOMO_PYTHON` | `.venv/bin/python` | repository virtual-environment Python |
| `LOCOMO_TIER` | `publication` | prepared manifest tier |
| `LOCOMO_PROTOCOL` | `full-v13` | prepare-time protocol key |
| `LOCOMO_MOUNT_ROOT` | `$RUN_DIR/.mounts` | host/container-identical P3 mount root |
| `LOCOMO_MAX_DOCUMENTS` | `100` | per-sample ingest authorization |
| `LOCOMO_MAX_QUESTIONS` | `1540` | run-absolute answer item authorization |
| `LOCOMO_MAX_AGENT_CALLS` | `13860` | run-absolute answer-agent call ceiling |
| `LOCOMO_MAX_JUDGE_CALLS` | `1540` | run-absolute judge-call ceiling |
| `LOCOMO_MAX_EVALUATOR_COST_USD` | `1000` | shared reported-spend stop threshold |
| `LOCOMO_DRAIN_TIMEOUT_SECONDS` | `21600` | true-drain budget (six hours) |
| `LOCOMO_DRAIN_POLL_SECONDS` | `30` | ledger poll interval |
| `LOCOMO_BACKUP_DESTINATION` | **required** | private `gs://` bucket/base prefix |
| `LOCOMO_GCP_PROJECT` | **required** | GCP project used by the external-account Storage client |
| `LOCOMO_BACKUP_STAGING_ROOT` | `/var/lib/rememberstack-locomo-backups` | local staging retained on failure |
| `LOCOMO_COMPOSE_PROJECT` | `rememberstack` | Compose label used to resolve exactly four volumes |
| `LOCOMO_RUNNER_LOCK` | `/var/lock/rememberstack-locomo-shard.lock` | host-wide exclusive runner lock |
| `LOCOMO_GCP_CREDENTIALS_FILE` | `/etc/rememberstack/locomo-gcs/credentials.json` | public external-account configuration |
| `LOCOMO_GCP_CERTIFICATE_CONFIG_FILE` | `/etc/rememberstack/locomo-gcs/certificate-config.json` | public client-certificate path configuration |

The call ceilings cover the whole prepared publication manifest, not merely one sample. Lower
values must still satisfy the harness's run-absolute guards. If a command fails, leave the stack
and run directory in place for diagnosis. A restarted driver
refuses to wipe an incomplete sample with persisted records, because its live isolated deployment
may be the only safe way to resume that checkpoint.
Run the incomplete stage directly. A partial `ingest` resumes only after the
runner proves that the exact public live-lineage/visible-version join matches
its durable checkpoint. If that proof fails, leave the old run state
untouched, and inspect whether the sample has any answer or judge records. With
none, a new run directory may rerun only that sample and merge with the old
run. With any such record, restart every sample assigned to the old run
directory: the merger deliberately rejects a replacement for an
already-recorded sample. Never edit or force a checkpoint forward.

### Existing unmarked stores

The wipe guard refuses a pre-existing Compose store with no live-sample marker.
This is deliberate: the runner cannot guess which conversation produced those
bytes. If an older host contains a known store, identify its sample and run:

```bash
.venv/bin/python benchmarks/locomo/sharding/store_backup.py record-live \
  --run-dir /srv/locomo-runs/publication \
  --sample conv-50

.venv/bin/python benchmarks/locomo/sharding/store_backup.py backup \
  --run-dir /srv/locomo-runs/publication \
  --mount-root /srv/locomo-runs/publication/.mounts \
  --sample conv-50 \
  --project "$LOCOMO_GCP_PROJECT" \
  --destination "$LOCOMO_BACKUP_DESTINATION"
```

The backup derives the deployment ID from that sample's completed ingest
checkpoints; it never trusts the current shell for stored identity. Do not
record a guessed sample ID. If store identity cannot be proven, leave the
volumes in place for inspection.

## 4. Restore one conversation

Keep the verified receipt with the collected run artifacts. On a checkout of
the manifest's recorded engine revision, with the ordinary Compose environment
and federated GCS identity configured, restore into explicit empty targets:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/etc/rememberstack/locomo-gcs/credentials.json
export GOOGLE_API_CERTIFICATE_CONFIG=/etc/rememberstack/locomo-gcs/certificate-config.json
export GOOGLE_API_USE_CLIENT_CERTIFICATE=true

.venv/bin/python benchmarks/locomo/sharding/store_backup.py restore \
  --receipt /srv/receipts/conv-50.json \
  --run-dir /srv/locomo-restores/conv-50 \
  --mount-root /srv/locomo-restores/conv-50-mounts \
  --compose-base-env /opt/remember-stack/.env \
  --start
```

Restore first re-verifies the remote manifest and receipt plus every archive's
size, GCS generation, and CRC32C, downloads to a new staging directory, checks
every SHA-256 and tar member, and refuses non-empty run, mount, or Docker-volume
targets. `--start` brings up Postgres, MinIO, setup, and the API after
extraction. It makes the saved deployment ID, revision, and non-secret model
and routing bindings authoritative over the operator-supplied secret-bearing
base env and parent shell; secrets are never copied into backup metadata.
Startup refuses an API image whose stamped revision differs from the manifest.
Absolute P3 snapshot pointers are rebased to the restored mount root, so
recovery does not depend on the source host's filesystem path.
Then assert that the sample has no unanswered items and run the ordinary answer
preflight with an invalid evaluator key to prove sample lineage, readiness, and
projection freshness without authorizing a paid model call.

## 5. Collect run directories

Create a host file on the collector with one SSH destination or configured alias per line:

```text
bench-a
bench-b
bench-c
```

Then collect the common remote run path and merge it:

```bash
benchmarks/locomo/sharding/collect_and_merge.sh \
  /tmp/locomo-hosts.txt \
  /srv/locomo-runs/publication \
  /data/locomo10.json \
  .benchmark-runs/locomo-publication-merged
```

The script prefers `rsync` and falls back to `scp`. It rewrites only the collected copies'
non-fingerprinted `dataset_path` to the collector's pinned dataset, then invokes the harness with
one `--run` flag per directory. It writes
`.benchmark-runs/locomo-publication-merged/summary.json` and prints that path plus a one-line
judge/official-F1 score. Use a new or empty collection directory so stale files cannot survive a
second collection.

## 6. Merge manually

Run merging without the collection helper when the run directories are already local:

```bash
.venv/bin/python -m benchmarks.locomo summarize \
  --run .benchmark-runs/host-a \
  --run .benchmark-runs/host-b \
  --run .benchmark-runs/host-c
```

All runs must have identical protocol name, protocol fingerprint, tier, dataset hash, manifest
hash, and item-ID hash. Samples with answer or judge records must be pairwise disjoint. The merger
combines item records and runs the ordinary scorer over the full manifest; it never adds partial
summary numbers. Missing manifest samples remain zero-scored and appear in
`missing_sample_ids`.
