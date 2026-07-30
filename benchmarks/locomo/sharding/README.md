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
  benchmark CLI.

Treat the OpenRouter key as a secret. Inject it from the host's secret manager or export it in the
interactive session that starts `nohup`. Never put it in this repository, a shard plan, a command
argument, or a log. Use an OpenRouter account cap as the hard monetary boundary; the harness cost
cap is a reported-spend stop threshold.

Build the image once from the checked-out revision on every host:

```bash
export REMEMBERSTACK_BUILD_REVISION
REMEMBERSTACK_BUILD_REVISION=$(git rev-parse HEAD)
docker compose build
```

The driver assumes the checkout, virtual environment, image, Compose configuration, dataset, and
environment variables already exist. It deliberately does not provision machines or distribute
secrets. Any set of SSH-accessible hosts works.

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

1. preserves the previous sample with `pg_dumpall` (the first dump is skipped when the stack is
   declared fresh);
2. runs `docker compose down --volumes`, then starts every service with extract-claims ×3,
   normalize-relations ×6, and embed-claim ×2;
3. ingests only that sample with the isolated-deployment confirmation;
4. polls `processing_state` until no pending, running, or retryable failed rows remain, with a
   six-hour default budget, and stops immediately on a dead letter;
5. builds projections through the Compose `operations` profile; and
6. runs answer and judge with run-absolute caps.

The final sample also receives a forensic database dump. Dumps land under
`RUN_DIR/forensics/` with a private process umask; they can still contain sensitive database
content and must be handled as secrets. Progress is emitted as UTC timestamped log lines.

The following environment variables tune the driver without changing its arguments:

| Variable | Default | Meaning |
| --- | ---: | --- |
| `LOCOMO_PYTHON` | `.venv/bin/python` | repository virtual-environment Python |
| `LOCOMO_TIER` | `publication` | prepared manifest tier |
| `LOCOMO_PROTOCOL` | `full-v5` | prepare-time protocol key |
| `LOCOMO_MAX_DOCUMENTS` | `100` | per-sample ingest authorization |
| `LOCOMO_MAX_QUESTIONS` | `1540` | run-absolute answer item authorization |
| `LOCOMO_MAX_AGENT_CALLS` | `13860` | run-absolute answer-agent call ceiling |
| `LOCOMO_MAX_JUDGE_CALLS` | `1540` | run-absolute judge-call ceiling |
| `LOCOMO_MAX_EVALUATOR_COST_USD` | `1000` | shared reported-spend stop threshold |
| `LOCOMO_DRAIN_TIMEOUT_SECONDS` | `21600` | true-drain budget (six hours) |
| `LOCOMO_DRAIN_POLL_SECONDS` | `30` | ledger poll interval |
| `LOCOMO_FRESH_STACK` | `1` | set to `0` to dump an existing stack before the first wipe |

The call ceilings cover the whole prepared publication manifest, not merely one sample. Lower
values must still satisfy the harness's run-absolute guards. If a command fails, leave the stack
and run directory in place for diagnosis; do not wipe the only forensic state. A restarted driver
refuses to wipe an incomplete sample with persisted records, because its live isolated deployment
may be the only safe way to resume that checkpoint.

## 4. Collect run directories

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

## 5. Merge manually

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
