# RS-LoCoMo-Full-v22 setup

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
  --protocol full-v22 \
  --output .benchmark-runs/locomo-smoke
```

The harness validates the pinned bytes, renders session documents, and fingerprints the
eight-question smoke plan. `--protocol` is prepare-only; `full-v22` is the one
current-system protocol, and every later stage reads that immutable choice from
`run.json`. Do not run remote stages until reviewing
[`locomo_benchmark_design.md`](../../plan/designs/locomo_benchmark_design.md).

V17 must ingest into a fresh store built from the repository revision recorded
in `run.json`. Store backups protect interrupted work and allow restoration of
that exact run; they are not a license to carry ingestion across a changed
pipeline contract.

LoCoMo supplies session wall times without a timezone. The current adapter treats those values
as UTC in this adapter only, records `source_timezone_basis=assumed_utc` in
`documents.json`, discloses the assumption in each rendered document, and
forwards the aware timestamp to ingestion. RememberStack's general SDK and API
remain strict: arbitrary naive or non-UTC source timestamps are rejected.

V17 uses the ordinary public `testimony_context` operation as its first-recall path:
independent semantic and BM25 claim search plus live-confirmed semantic and
BM25 source-chunk search. The durable trace keeps each complete envelope. The
repeated answer-agent prompt removes rank-score bookkeeping and empty
containers so retrieved evidence does not get crowded out by audit metadata.
Freshness, hydration-drop counts, and meaningful default-valued fields remain
visible.

V17 requires the shortest phrase that fully names the requested entities or
values and forbids explanations or reasoning. Its `answer_word_cap` is a
persisted, fingerprinted protocol field, but the protocol leaves it unset: the
prompt renders no word-count sentence and the runner applies no
word-count guard. The two-retry malformed-completion allowance remains shared
across the answer loop, including a completion returned before the first tool
call.

If the trace contains only identity or metadata reads, v17 rejects terminal
`Unknown` until the agent attempts one ordinary content-bearing testimony,
fact, context, primitive, row-returning query, or P3 search/read. The guard uses
the existing call and cost budgets and records `unknown_guard_retries`; it does
not grant an extra retry budget.

V17 also fingerprints D100 entity resolution: one match-biased simple-model T4
call sees the complete bounded candidate snapshot and returns a supplied
candidate id or `new`. There is no insufficient-evidence result or
confidence-routed frontier call.

V22 fingerprints D107 WP-T.0a: the observation adjudicator and `claims_as_of`
compare canonical half-open bounds (a day is the whole calendar day, an
instant a non-empty point, adjacent units do not overlap); everything else is
v21's.

V21 fingerprints the D106 observation adjudicator: dated events with disjoint
resolved windows never collapse onto or supersede each other (they may only
contradict or stay distinct), a dated event is never `evidence` for an undated
statement (nor the reverse), open-ended windows stay unbounded, and the verdict
prompt shows when each statement was said and what time it is about. The
`adjudicate_observations` component version pins
that generation; the dataset, rendered documents, retrieval surface, answer
and judge prompts, budgets, and scoring are those of v20. V20 and v21 scores
are directional, not a one-variable comparison — the fact layer a v21 store
serves differs from a v20 store's.

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
requested version completed the exact composed stages, live-graph readiness passes, and the P3
publication began after that work completed. It also requires the deployment's exact prepared
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
plane: four assured operations, seven direct primitives, seven open-query
operations, and list/search/read over the ordinary P3 mount. It does not read
Postgres, MinIO, graph files, or internal handlers directly. Limits are
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

## Gemma 4 on Vertex as the answer agent (`full-v22-gemma-vertex`)

`full-v22-gemma-vertex` is a *variant* of `full-v22`, not a new benchmark
identity: every pin is identical -- ingestion bindings, prompts, tool catalog,
budgets, temperature, and the frozen Luna judge -- except that the answer
agent is `google/gemma-4-26b-a4b-it-maas`, Google's managed Gemma 4 26B-A4B
model on the Gemini Enterprise Agent Platform (formerly Vertex AI), and the
answer step is pinned as `DiscriminatedAnswerAgentStep`. Its scores therefore
compare answer agents over the same stores. The judge and the preflight
embedding stay on OpenRouter, so the OpenRouter key is still required; only
the answer-agent model name routes to Vertex.

The step shape differs for a measured reason. Vertex's constrained decoder
emits object keys in **alphabetical** order and demands every required key,
so under the flat `AnswerAgentStep` schema (`action`, `answer`,
`arguments_json`, `tool_name` in that order) Gemma is forced to fill `answer`
before `tool_name`, writes the tool name there, cannot close the object, and
pads whitespace until the output cap (observed 2026-09-04 on every flat
variant tried, including plain-string and type-array nullables). The
discriminated form is the same decision with the same field names as a
two-branch union -- `{action:"tool", tool_name, arguments_json}` or
`{action:"answer", answer}` -- so no branch has a key the model must invent,
and the model completes in a few tokens. The prompt is byte-identical to
v22; only `answer_schema_sha256` differs.

Prepare it explicitly; every later stage reads the immutable choice:

```bash
uv run --extra benchmark python -m benchmarks.locomo prepare \
  --dataset /absolute/path/locomo10.json \
  --tier smoke \
  --protocol full-v22-gemma-vertex \
  --output .benchmark-runs/locomo-gemma-smoke
```

The Vertex binding uses no API key or service-account key. The adapter takes
short-lived access tokens from Google Application Default Credentials; on the
benchmark hosts an X.509 client certificate and root-only private key are
exchanged with Google STS (Workload Identity Federation). Export the workload
credential the same way the GCS backup path does, plus the project:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/etc/rememberstack/locomo-vertex/credentials.json
export GOOGLE_API_CERTIFICATE_CONFIG=/etc/rememberstack/locomo-vertex/certificate-config.json
export GOOGLE_API_USE_CLIENT_CERTIFICATE=true
export REMEMBERSTACK_VERTEX_PROJECT_ID=<the isolated lab project id>
# optional: REMEMBERSTACK_VERTEX_LOCATION (default global),
#           REMEMBERSTACK_VERTEX_MAX_COMPLETION_TOKENS (default 4096),
#           REMEMBERSTACK_VERTEX_PRICE_TABLE_USD_PER_MILLION (JSON; default pins Gemma 4 26B)
```

Accounting differs from OpenRouter and the run records say so by construction:
Vertex reports token counts but no charge, so every `cost_usd` for a Vertex
call is **computed** from the pinned price table (input $0.15, output $0.60
per million tokens, retrieved 2026-09-04), with every prompt token billed at
the full input rate and cached-token discounts ignored. The value can only
over-report. The adapter refuses to call any model without a pinned price and
refuses any reasoning-effort pin other than `none`; the variant deliberately
does not use Vertex-specific thinking controls, and silently dropping a pin
would misstate the protocol. The `--max-evaluator-cost-usd` stop threshold applies to the shared
ledger exactly as before; the project-level spend cap and guardian in the
cloud repository are the hard monetary boundary for this provider.

A Vertex `401`/`403` -- a revoked federated identity, a disabled API, or
unlinked billing, which is what the spend guardian does -- stops the stage
before the next checkpoint, like an exhausted OpenRouter balance. Any other
Vertex failure is one failed item after bounded retries for `429` queue-full
responses, which report no usage. `ingest` still runs the preflight: the chat probe now goes
to Vertex and proves the certificate, project, and model entitlement before
any upload.

## Sharded runs

Publication samples can run concurrently on independent hosts while preserving the required
per-sample deployment isolation. The harness accepts repeated `summarize --run` flags and
recomputes one full-manifest score from disjoint item records. See the
[`sharding/` operator guide](sharding/README.md) for balanced planning, the per-host driver,
collection, and merge validation.

Historical runs used earlier protocol identities. They are not executable
compatibility modes and are not comparable to the current protocol.
