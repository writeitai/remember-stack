# RS-LoCoMo-Full-v13 setup

This directory contains the unshipped full-system LoCoMo adapter. It does not vendor or
auto-download LoCoMo. Supply the exact pinned `locomo10.json` only after confirming its
CC BY-NC 4.0 terms.

Install the repository plus deterministic scorer dependencies:

```bash
uv sync --extra benchmark
```

The safe first command is local and makes no API or model call:

```bash
uv run --extra benchmark python -m benchmarks.locomo prepare \
  --dataset /absolute/path/locomo10.json \
  --tier smoke \
  --protocol full-v13 \
  --output .benchmark-runs/locomo-smoke
```

The harness validates the pinned bytes, renders session documents, and fingerprints the
eight-question smoke plan. `--protocol` is prepare-only; `full-v13` is the one
current-system protocol, and every later stage reads that immutable choice from
`run.json`. Do not run remote stages until reviewing
[`locomo_benchmark_design.md`](../../plan/designs/locomo_benchmark_design.md).

V13 must ingest into a fresh store built from the repository revision recorded
in `run.json`. Store backups protect interrupted work and allow restoration of
that exact run; they are not a license to carry ingestion across a changed
pipeline contract.

LoCoMo supplies session wall times without a timezone. The current adapter treats those values
as UTC in this adapter only, records `source_timezone_basis=assumed_utc` in
`documents.json`, discloses the assumption in each rendered document, and
forwards the aware timestamp to ingestion. RememberStack's general SDK and API
remain strict: arbitrary naive or non-UTC source timestamps are rejected.

V13 uses the ordinary public `testimony_context` operation as its first-recall path:
independent semantic and BM25 claim search plus live-confirmed semantic and
BM25 source-chunk search. The durable trace keeps each complete envelope. The
repeated answer-agent prompt removes rank-score bookkeeping and empty
containers so retrieved evidence does not get crowded out by audit metadata.
Freshness, hydration-drop counts, and meaningful default-valued fields remain
visible.

V13 requires the shortest phrase that fully names the requested entities or
values and forbids explanations or reasoning. Its `answer_word_cap` is a
persisted, fingerprinted protocol field, but the protocol leaves it unset: the
prompt renders no word-count sentence and the runner applies no
word-count guard. The two-retry malformed-completion allowance remains shared
across the answer loop, including a completion returned before the first tool
call.

Build the image from the revision under test — Compose otherwise serves the
published release image, and the harness refuses to run against an engine whose
stamped revision does not match the prepared run:

```bash
REMEMBERSTACK_BUILD_REVISION=$(git rev-parse HEAD) docker compose build
docker compose up --detach
```

`ingest` runs a provider preflight (one chat call, one embedding call) after its
authorization guards and before any upload, so a bad credential fails in seconds
instead of surfacing later as per-stage dead-letters.

The stock Compose deployment now includes all eleven continuous E/P1 workers. After ingesting one
isolated conversation and waiting for them to settle, publish the aggregate projections once:

```bash
docker compose --profile operations run --rm projections
```

The `answer` command then calls the public readiness endpoint. It refuses to run unless every
requested version completed the exact composed stage generations and both P2/P3 builds began
after that work completed. It also requires the deployment's exact prepared
`surface_manifest_hash`, the canonical four public operation descriptors, and the
fingerprinted complete answer catalog. Before each
upload, the exact public `documents_live` to `document_versions_visible` join
must equal the run's durable lineage/version checkpoints (empty on a new
deployment), and every ingest must create a new version. The version relation
is used because `documents_live.current_version_id` remains null until content
is ready. Before answering, those exact checkpointed tuples must equal the
complete prepared sample.
There is no manual “index ready” acknowledgement.

Readiness also records the API process's current non-secret model configuration for operator
review. Those values are not processing-time provenance; freeze one Compose environment for the
run and retain the provider/cost artifacts.

The primary protocol uses a bounded answer agent over the complete public read
plane: four assured operations, seven direct primitives, nine open-query
operations, and list/search/read over the ordinary P3 mount. It does not read
Postgres, Lance, MinIO, graph files, or internal handlers directly. Limits are
run-absolute: allow up to nine agent calls per selected question and one judge
call per answer. The shared evaluator-cost value is a reported-spend stop threshold: a completed
call can cross it, is recorded, and stops the run. Use the provider account cap as the hard
monetary boundary. If that leaves later questions unanswered, they remain visible as zero-scored
missing records; resuming them requires an explicitly higher threshold.

An answer-agent completion that is not a valid JSON step is retried at most
twice, whether it occurs before the first tool call or while reading retrieved
evidence. The two-retry allowance is shared across both positions. Every
attempt consumes the same nine-call per-question and run-absolute call budgets;
these are not extra calls outside the cap. Each item records reader-position
attempts in `reader_attempts` and pre-tool additional calls in
`first_step_retries`; the summary sums both signals separately. Plain provider
outages and judge failures are not retried.

The protocol pins Luna's reasoning effort to `none` for both answer and judge
calls and verifies the provider-reported model identity for both seats. Ambient
OpenRouter settings or model aliases therefore cannot silently change the
prepared protocol.

P3 is built, freshness-checked, and published through the ordinary self-host
mount adapter before answering:

```bash
mkdir -p "$PWD/.benchmark-mounts"
docker compose --profile operations run --rm \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/.benchmark-mounts:$PWD/.benchmark-mounts" \
  projections mounts --root "$PWD/.benchmark-mounts"
```

Pass the resulting
`.benchmark-mounts/$REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID/p3` path to `answer`
with `--p3-root`. The runner rejects a mount whose `.snapshot-version` differs
from readiness.

## Sharded runs

Publication samples can run concurrently on independent hosts while preserving the required
per-sample deployment isolation. The harness accepts repeated `summarize --run` flags and
recomputes one full-manifest score from disjoint item records. See the
[`sharding/` operator guide](sharding/README.md) for balanced planning, the per-host driver,
collection, and merge validation.

Historical runs used earlier protocol identities. They are not executable
compatibility modes and are not comparable to v13.
