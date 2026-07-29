# LoCoMo full-system benchmark design

> **Status:** binding setup for WP-8.2. Implementation and synthetic tests are allowed; no real
> LoCoMo/API/provider run is authorized by this document.

## 1. Acceptance boundary

The adapter is repository tooling around the public `MemoryClient`. Before the owner walkthrough:

- exact dataset and manifests validate locally;
- the stock self-host profile composes all ten continuous handlers;
- P2/P3 can be built explicitly over the same stores the API reads;
- readiness is machine-verifiable through the public API;
- the answer agent uses only registry-rendered public recipes;
- all tool calls, envelopes, model usage, costs, and failures checkpoint;
- pure and synthetic tests pass; and
- no real benchmark or provider call occurs.

WP-8.2 remains in progress until an owner-authorized eight-question smoke finishes.

## 2. Fixed protocol

```text
protocol                RS-LoCoMo-Full-v5
dataset commit           3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376
dataset SHA-256          79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4
categories               1, 2, 3, 4
answer-agent model       openai/gpt-4o-mini
answer temperature       0
max tool calls/question  8
max agent calls/question 9
judge model              openai/gpt-5.6-luna
judge temperature        0
judge repetitions        1
primary metric           judge accuracy
secondary metric         official LoCoMo F1
diagnostic               coarse evidence-session recall
```

The tool catalog hash, prompt and schema hashes, adapter and repository revisions, manifests,
rendered documents, model identities, and component generations are stored. A change creates a
new protocol version.

**v2 → v3 (2026-07-26, before any scored run):** `AnswerAgentStep.arguments`
became `arguments_json`, a JSON-object-encoded string, because compliant strict
providers reject a free-form object schema outright (§2.4). The answer prompt
changed accordingly. No v2 score ever existed.

**v3 → v4 (2026-07-27, before any scored run):** recipe descriptors gained
when-to-use guidance; `claims_hybrid_rrf` hydrates ranked claim text (not
scores alone); the answer-agent prompt adds loop guards (no identical
tool+arguments retries; switch tools after a useless result; try a claims
search before "Unknown"). Prompt, tool-catalog hash, and descriptors all
changed, so the protocol version bumps. No v3 score is comparable.

**v4 → v5 (2026-07-27, issue #156):** the `identity_as_of` recipe descriptor
became honest about its recent-first transcript bound (default 40 rows,
truncation signalled in the envelope) and gained an optional `limit`
parameter so a truncated history is recoverable through the public surface.
The tool-catalog hash changed, so the protocol version bumps — the v4 smoke
scores (glm-4.7-flash arm) were taken against the pre-truncation catalog and
are not directly comparable.

### 2.1 Why v2+ uses a stronger judge

`RS-LoCoMo-Full-v1` used `openai/gpt-4o-mini` for both the answer agent and the judge. The
judge is replaced with `openai/gpt-5.6-luna` in v2 and the protocol version is bumped
accordingly; nothing else changes, and v1 numbers are not comparable to v2 numbers.

Rationale. The judge is the cheapest component to strengthen: one call per question against
nine agent calls, so raising its tier changes total run cost by a small fraction. Grading
fidelity is worth that cost because the judge's verdict *is* the primary metric, and a judge at
the same tier as the agent it grades gives no headroom for catching a plausible-but-wrong answer.

This is a judgement about protocol design, not a measured claim: no leniency measurement for
either judge model has been run here. If the judge's strictness is ever asserted as a result
rather than a design choice, it needs its own experiment — for example, scoring a set of
deliberately incorrect answers with both models and reporting the acceptance rates.

The answer agent deliberately stays on `openai/gpt-4o-mini`. It is the component under
measurement alongside retrieval, and keeping it at the commodity tier keeps the comparison
against baselines honest and cheap. Judge and answer models must never be the same family tier
by accident: a stronger judge grading a weaker agent is the intended asymmetry.

### 2.2 Provenance: the serving image, not the checkout

`repository_revision` is read with `git rev-parse HEAD` in the directory the CLI
runs from. The work, however, is done by containers, and Compose resolves a
**published image unless explicitly told to build** — so a checkout at one commit
can drive an engine built from another. Observed 2026-07-25 on a fresh host: the
file tree was at a development branch while the running engine was the released
`0.1.0` image, which predates the ten-stage pipeline. Eight workers exited
immediately; had they not, the run would have recorded a commit that never
produced its numbers.

The image therefore carries its own source revision. `Dockerfile` accepts a
`REMEMBERSTACK_BUILD_REVISION` build argument and bakes it in, and the self-host
profile reports it — together with its model bindings — from `GET /deployment`,
which needs no version ids and so can be read *before* any work is submitted.

The revision is checked at **both** boundaries, because they answer different
questions. At **ingest** it binds the code that will *process* the corpus; at
**answer** it binds the code that *serves* it. Checking only the latter leaves a
hole: process under the wrong image, fail at answer, rebuild to the prepared
revision without re-ingesting, and the answer stage then passes over data that
other code produced.

An **unstamped image is a hard stop, not a warning**: "unknown" is not evidence of
agreement, and a benchmark that cannot name the code it measured has no claim on
reproducibility (WP-8.6). Build with:

```bash
REMEMBERSTACK_BUILD_REVISION=$(git rev-parse HEAD) docker compose build
```

Released images are stamped by the release workflow with the tag's commit, so a
run against a published release is verifiable in exactly the same way.

### 2.3 Provider preflight before ingestion

Ingest makes no provider calls, so a bad credential stays invisible until the
first pipeline stage needs a model — after documents are uploaded, and then only
as per-stage dead-letters that read like partial progress. This was observed
directly: a run configured with the `.env.example` placeholder key ingested all
nineteen sessions successfully and then failed every model-calling stage with
HTTP 401.

The ingest stage therefore runs a preflight after its authorization guards and
before any upload: one structured chat call on the answer-agent model and one
embedding call on the **deployment's** `chunk_embedding` binding, read from
`GET /deployment` rather than from the CLI host's environment, so the check
covers the model the pipeline will actually use. A probe that returns `ok=false`
fails as loudly as a transport error — reachable is not the same as usable. A
failure raises `ProviderPreflightError` and no document is sent. The preflight is
skipped when every session is already ingested, since a full resume has no
upload left to protect. The cost is two trivial calls;
the alternative is discovering the same fact after a full ingest and a retry
budget per stage.

This is benchmark-scoped. The same failure would meet any new self-hoster
following the quickstart, and an engine-side check at `setup` time is a
worthwhile follow-up, but it is deliberately not part of this protocol change.

### 2.4 Provider schema compliance is not guaranteed

Requests set `"strict": true` on a JSON schema, but the provider does not
reliably honour it. Two distinct violations were observed in real runs against
`deepseek/deepseek-v4-flash` through OpenRouter:

- **Out-of-vocabulary enum value.** Selection returned `drop_boilerplate`, which
  is not one of the twelve declared `outcome` values.
- **Schema ignored entirely.** Fact labelling returned the bare sentence
  `Caroline knows about advocacy.` as plain text instead of a JSON object, with
  `finish_reason: stop` — a clean completion that simply did not use the schema.
- **Strict mode enforced only by some providers.** The same request shape is
  accepted by the providers serving the extraction models but rejected with
  HTTP 400 by Azure serving `gpt-4o-mini`, which requires every schema object
  closed (`additionalProperties: false`). A free-form object is therefore
  unrepresentable under strict mode — the reason `AnswerAgentStep` carries tool
  arguments as a JSON-encoded string (v3).
- **String contamination.** At temperature 0, `gpt-4o-mini` was observed
  appending a sentence period inside the `arguments_json` string, after the
  closing brace. The agent loop parses the first complete JSON object and
  records the trailing text on the trace row (§7).
- **2026-07-29 — `z-ai/glm-5.2` cap exhaustion and mitigation.** OpenRouter
  requests sent no `max_tokens`, so production E1/E2 calls exhausted the
  provider default on reasoning: `finish_reason='length'`, `content=null`, and
  `reasoning_present=True`, or truncated JSON (`Unterminated string`), produced
  dead-letters. The adapter now sends a 32,000-token reasoning-plus-content
  budget by default; the provider account cap remains the monetary boundary.

Neither is reproducible on demand: the same fact-label prompt succeeded on 24
consecutive retries after failing once, and three prompt variants each returned
valid JSON 8 times out of 8. The rate has **not** been measured, so no claim is
made about how often it happens.

The consequence for this protocol is that a schema is a request, not a
constraint. Adapters must fail with a diagnosable error rather than a bare
decode failure, and any invariant that matters must be expressible *within* the
schema — which is why Selection's verdict and drop reason were collapsed into a
single enum rather than paired by a validator.

An unusable completion is therefore reported with provider metadata: finish
reasons, whether content was null or blank and its length, reasoning and refusal
flags, an error code, and for non-JSON content a length and digest. Model output
can restate customer material and these strings reach `processing_state.last_error`
and the logs, so the text itself is never included.

Whether such failures should be retried inside the adapter is **open**. Retrying
was implemented and then withdrawn: it was built on the assumption that an empty
completion is a transient, and the failure actually observed is non-empty
content, so the retry never applied to it. The work ledger already grants each
item several attempts; whether that is sufficient needs a measured failure rate
first.

## 3. Ingestion mapping

Each conversation runs in a clean isolated deployment. Each session is one immutable Markdown
document. Every turn is rendered:

```text
[D1:3 | 1:56 pm on 8 May, 2023] Caroline: ...
```

Image URLs are not fetched. Dataset captions and image queries are included only with explicit
derived-data labels. Session summaries and event summaries are never ingested.

```text
source_kind       locomo
source_ref        <dataset-commit>/<sample-id>/<session-id>
versioning_mode   snapshot
source_version_ref <dataset-commit>
```

The dataset has no timezone, so its literal timestamp stays in text and `source_modified_at` is
omitted.

## 4. Runtime composition

### Continuous services

`docker compose up` starts one process for each implemented steady route:

```text
convert
structure
chunk
embed_chunk
extract_claims
normalize_relations
adjudicate_supersession
embed_claim
reconcile
label_relation
```

All use the same deployment ID, PostgreSQL ledger, MinIO stores, OpenRouter adapter, and Lance
root. One route per process preserves the existing queue/rate-limit design; no workflow engine
is introduced.

### Aggregate projections

P2 and P3 rebuild after all selected session versions are E/P1-ready:

```bash
docker compose --profile operations run --rm projections
```

The one-shot service builds P2 into the snapshot bucket and P3 into the corpusfs bucket. It does
not run on every document and does not remain resident.

P3 publication is a deployment-integrity requirement in this protocol, not an answer channel.
The remote `MemoryClient` answer agent cannot browse a local P3 mount, and the ordinary recipe
registry has no filesystem operation. Results must not attribute answer quality to P3
navigation. A future mount-enabled LoCoMo harness is a separately named protocol.

### Plane K

The benchmark records that the stock profile has no K planner/writer runtime. `pages_about`
remains available and honest, but an empty result is not reported as K coverage. A later K-enabled
LoCoMo run needs explicit routing rules, repository/runtime fingerprints, K settlement in
readiness, and a new protocol name.

## 5. Lifecycle ordering and readiness

Normalization fans out as:

```text
normalize_relations
  ├── embed_claim
  └── adjudicate_supersession
        └── reconcile
              └── label_relation
```

This ensures labels enter P1 only after supersession and testimony reconciliation. A
no-claims document still creates the no-op terminal rows, so readiness has one deterministic
shape.

`POST /readiness?require_projections=true` receives a bounded JSON list of version IDs. The
response contains:

- every expected stage and exact component version;
- its status and completion time;
- P2/P3 version and publication time;
- a Boolean requiring every stage to be `succeeded`/`skipped`;
- a Boolean requiring both projection builds to begin after the latest requested terminal stage;
- every non-secret ingestion/query model binding.

The answer command refuses a false report and checkpoints a true one. The old
`--confirm-index-ready` flag is removed.

## 6. Public tool surface

The self-host setup seeds the normal canonical recipes plus P2 recipes. `resolve_entity` is
canonical because UUID-addressed fact tools otherwise cannot be used by a remote recipe-only
agent. P2 recipes are seeded only by profiles that compose `GraphQueries`.

The protocol hashes the exact descriptor list returned by `GET /recipes` and refuses a mismatch.
This prevents an added, removed, or changed tool from silently changing the benchmark.

No benchmark tool reads Postgres, Lance, MinIO, or internal handlers directly.

## 7. Answer loop

For each question:

1. Render the frozen answer-agent prompt with question, public tool descriptors, and prior trace.
2. Ask for strict `AnswerAgentStep`.
3. For `action="tool"`, validate the name against the catalog, decode
   `arguments_json` by taking its first complete JSON object (trailing text is
   recorded on the trace row, not discarded silently — see §2.4), and call
   `MemoryClient.run_recipe()`.
4. Append arguments, latency, and the complete envelope.
5. For `action="answer"`, require at least one tool call and at most six words.
6. Stop at eight tools or nine model calls; exhaustion is a visible wrong, not a retry.
7. Checkpoint the terminal answer or failure.

The agent is instructed to orient, verify current facts, and audit evidence while respecting
grain, validity, freshness, truncation, typed negatives, and hydration drops. It receives no gold
answer, evidence IDs, summaries, or outside retrieval.

Loop guards in the frozen answer prompt (v4): never repeat a tool call with the
same tool and the same arguments; if a tool yields nothing useful, switch tools
rather than retrying it; before answering "Unknown", try `claims_verbatim` or
`claims_hybrid_rrf` at least once. These are prompt discipline, not harness
enforcement — the harness still only bounds call counts.

Evidence claims found anywhere in the trace are de-duplicated in first-seen order for the coarse
session diagnostic. This diagnostic remains separate from the primary score.

## 8. Commands

Local preparation:

```bash
uv run --extra benchmark python -m benchmarks.locomo prepare \
  --dataset /absolute/path/locomo10.json \
  --tier smoke \
  --output .benchmark-runs/locomo-smoke
```

Per isolated sample:

```bash
uv run --extra benchmark python -m benchmarks.locomo ingest \
  --run .benchmark-runs/locomo-smoke \
  --sample conv-26 \
  --max-documents 19 \
  --execute \
  --confirm-isolated-deployment conv-26

docker compose --profile operations run --rm projections

uv run --extra benchmark python -m benchmarks.locomo answer \
  --run .benchmark-runs/locomo-smoke \
  --sample conv-26 \
  --max-questions 8 \
  --max-agent-calls 72 \
  --max-evaluator-cost-usd 1.00 \
  --execute

uv run --extra benchmark python -m benchmarks.locomo judge \
  --run .benchmark-runs/locomo-smoke \
  --sample conv-26 \
  --max-judge-calls 8 \
  --max-evaluator-cost-usd 2.00 \
  --execute
```

The limits are run-absolute across resumed sample commands. The harness never creates or destroys
the deployment.

## 9. State and failure rules

`run.json`, manifests, and rendered document hashes are immutable. `state.json` is atomically
replaced after each ingestion, readiness checkpoint, answer, and judge.

Transport errors, invalid tool decisions, schema failures, provider accounting failures, step
exhaustion, and missing records remain explicit and score zero. Successfully parsed provider
usage is added to the shared answer/judge ledger. A call that crosses the CLI reported-spend
threshold is recorded as a failure and stops the run. Later unanswered items remain explicit
zero-scored missing records unless the operator resumes with an explicitly higher threshold.
Provider-side account limits remain the hard monetary boundary because a process can die after
billing but before checkpointing.

## 10. Pre-run checklist

- Clean git revision equals `run.json`.
- Local dataset hash and manifest validate.
- One fresh deployment is dedicated to exactly one conversation.
- Explicit ingestion model IDs are set; no rotating model router.
- All ten workers are running.
- Every prepared session has an ingest record.
- P2/P3 one-shot build completed.
- Public readiness is true; current serving-process model bindings are reviewed as
  configuration, not processing-time provenance.
- Public recipe catalog hash matches.
- Account/provider hard limits and the CLI reported-spend stop threshold are acceptable.
- No claim is made that K ran.
- Raw artifacts and failures will be retained for publication review.
