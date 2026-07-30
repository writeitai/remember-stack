# RS-LoCoMo-Full-v5 setup

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
  --protocol full-v5 \
  --output .benchmark-runs/locomo-smoke
```

The harness validates the pinned bytes, renders session documents, and fingerprints the
eight-question smoke plan. `--protocol` is prepare-only: choose `full-v5` (the
default) or `full-v5-strong` there, and every later stage reads that immutable
choice from `run.json`. Do not run remote stages until reviewing
[`locomo_benchmark_design.md`](../../plan/designs/locomo_benchmark_design.md).

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

The stock Compose deployment now includes all ten continuous E/P1 workers. After ingesting one
isolated conversation and waiting for them to settle, publish the aggregate projections once:

```bash
docker compose --profile operations run --rm projections
```

The `answer` command then calls the public readiness endpoint. It refuses to run unless every
requested version completed the exact composed stage generations and both P2/P3 builds began
after that work completed. It also refuses a changed public recipe catalog. There is no manual “index ready”
acknowledgement.

Readiness also records the API process's current non-secret model configuration for operator
review. Those values are not processing-time provenance; freeze one Compose environment for the
run and retain the provider/cost artifacts.

The primary protocol uses a bounded answer agent over normal public recipes, not hard-coded claim
search. Limits are run-absolute: allow up to nine agent calls per selected question and one judge
call per answer. The shared evaluator-cost value is a reported-spend stop threshold: a completed
call can cross it, is recorded, and stops the run. Use the provider account cap as the hard
monetary boundary. If that leaves later questions unanswered, they remain visible as zero-scored
missing records; resuming them requires an explicitly higher threshold.

After at least one recipe result, an answer-agent completion that is not a valid
JSON answer step is retried at most twice. Those attempts consume the same
nine-call per-question and run-absolute call budgets; they are not extra calls
outside the cap. Each item records `reader_attempts`, and the summary records
`total_reader_retries`. Tool-selection failures before retrieval and judge
failures are not retried.

The strong protocol pins answer-agent reasoning effort to `none` on every answer
call. The default `full-v5` protocol sends no per-call effort field for its
non-reasoning `gpt-4o-mini` answer agent. Ambient OpenRouter effort-map settings
therefore cannot change either prepared protocol's answer behavior.

P3 is built and freshness-checked as part of the ordinary deployment, but the remote recipe
agent has no filesystem mount. This protocol therefore does not attribute answer quality to P3
navigation. A future mount-enabled protocol needs a new fingerprint and name.

## Sharded runs

Publication samples can run concurrently on independent hosts while preserving the required
per-sample deployment isolation. The harness accepts repeated `summarize --run` flags and
recomputes one full-manifest score from disjoint item records. See the
[`sharding/` operator guide](sharding/README.md) for balanced planning, the per-host driver,
forensic dumps, collection, and merge validation.

No real benchmark has been run as part of the setup implementation.
