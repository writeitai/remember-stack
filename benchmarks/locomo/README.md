# RS-LoCoMo-Full-v39 setup

One current execution profile uses Luna 6/Flex for ingestion and vision at low
reasoning, Luna 6/Flex for answering and judging at high reasoning, and Jev
for adjudication. Historical protocols are retained in saved run artifacts
and Git, not as runnable branches. No new benchmark score is claimed.

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
  --protocol full-v39 \
  --output .benchmark-runs/locomo-smoke
```

The harness validates the pinned bytes, renders session documents, and fingerprints the
eight-question smoke plan. `--protocol` is prepare-only; `full-v39` is the one
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

V17 uses the ordinary public `claims_and_sources_context` operation as its first-recall path:
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

V24 fingerprints D107 WP-T.4: extraction teaches all four temporal kinds,
open-ended state windows, and full source timestamps. Relative hour offsets
use the source instant; missing or unrepresentable times remain unknown.
The extractor generation and protocol identity roll; the fact schema is unchanged.

V23 fingerprints D107 WP-T.0b: the query space exposes the same canonical
half-open bounds as `memory_v1.canonical_bounds` and `claims_canonical`, and
`examples.claims_as_of` overlaps those bounds and counts unknown precision by
precision alone. Ingestion, adjudication, and the library `claims_as_of` path
are those of v22; the surface manifest hash rolls.

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
Postgres, the object store, graph files, or internal handlers directly. Limits are
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

## Retrieval-access ablations (development only)

The additive D124 runner reuses one completely processed Full-v39 sample and
compares four answer routes without re-ingesting it:

| Profile | Answer runtime | Available evidence |
| --- | --- | --- |
| `codex-p3` | native Codex subscription | local P3 filesystem only |
| `codex-p3-mcp` | native Codex subscription | local P3 plus read-only OSS MCP |
| `mcp` | OpenRouter Luna | read-only OSS MCP only |
| `mcp-p3` | OpenRouter Luna | read-only OSS MCP plus bounded P3-like tools |

This starts with OSS artifacts only. P3 is already an ordinary published
directory, so copy it from the benchmark host or restore it with the existing
OSS backup workflow; no UMC, RememberFS, FUSE mount, or new download service is
involved. For example, a normal copy preserves the required marker:

```bash
rsync -a bench-host:/absolute/published/mount/p3/ /local/conv-42-p3/
test -s /local/conv-42-p3/.snapshot-version
```

Run one profile into its own empty output directory. The P3 argument is required
only for profile names containing `p3`:

```bash
uv run --extra benchmark python -m benchmarks.locomo retrieval-ablation \
  --run /local/source-run \
  --sample conv-42 \
  --profile codex-p3 \
  --output /local/ablations/conv-42-codex-p3 \
  --p3-root /local/conv-42-p3 \
  --max-questions 20 \
  --max-agent-calls 40 \
  --max-judge-calls 40 \
  --max-evaluator-cost-usd 20 \
  --execute
```

Use the same command with a distinct output and another profile. The MCP
profiles need a worst-case nine answer-model calls per question, so use at least
`--max-agent-calls 200` for a 20-question run with interrupt headroom. These
ceilings are run-absolute authorizations, not first-N selectors; leave headroom
if you want an identical command to resume after an interrupted paid call.
Those profiles require the source deployment to be running and reachable
through the normal `REMEMBERSTACK_*` client environment. `mcp-*` answer calls
and every judge call use OpenRouter. `codex-*` answer calls use the operator's
existing `codex login` ChatGPT subscription and write a result-free action audit to
`codex-runtime-answer.jsonl`.

Native Codex is deliberately governed by the owner-selected instruction and
audit policy, not a hard filesystem/network security boundary. Inspect the
audit for parent traversal, writes, network use, or attempts to find evaluator
artifacts, then record its status:

```bash
uv run --extra benchmark python -m benchmarks.locomo \
  retrieval-ablation-review \
  --output /local/ablations/conv-42-codex-p3 \
  --status clean \
  --note "Reviewed every command and MCP action"
```

Run both Codex profiles under the same stable operator Codex configuration.
The current Codex SDK overlays the experiment MCP server but has no public
reset-to-empty option, so unrelated ambient tool schemas can remain visible;
any out-of-profile action fails the question and appears in the audit. The
runner does not copy or link ChatGPT subscription credentials into a separate
Codex home merely to hide those schemas.

Only compare `codex-p3` with `codex-p3-mcp`, and `mcp` with `mcp-p3`, as causal
access changes. Cross-runtime scores are directional because native Codex has
its own agent loop and shell semantics. A Codex result with `audit_status`
`pending` or `invalid` is not comparable. The canonical Full-v39 protocol and
its source state are never modified by this command.

## Sharded runs

Publication samples can run concurrently on independent hosts while preserving the required
per-sample deployment isolation. The harness accepts repeated `summarize --run` flags and
recomputes one full-manifest score from disjoint item records. See the
[`sharding/` operator guide](sharding/README.md) for balanced planning, the per-host driver,
collection, and merge validation.

Historical runs used earlier protocol identities. They are not executable
compatibility modes and are not comparable to the current protocol.
