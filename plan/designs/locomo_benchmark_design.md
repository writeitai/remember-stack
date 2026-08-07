# LoCoMo full-system benchmark design

> **Status:** binding current-system protocol. Real provider execution remains
> operator-invoked; the owner authorized the full v11 run on 2026-08-07.

## 1. Acceptance boundary

The adapter is repository tooling around the public `MemoryClient`. Every real
run must satisfy these gates:

- exact dataset and manifests validate locally;
- the stock self-host profile composes all ten continuous handlers;
- P2/P3 can be built explicitly over the same stores the API reads;
- readiness is machine-verifiable through the public API;
- the answer agent can use the complete shipped read plane: assured operations,
  direct primitives, open query, P1/P2, and the published P3 mount;
- all tool calls, responses, model usage, costs, and failures checkpoint;
- pure and synthetic tests pass; and
- the operator supplies explicit execution, isolated-deployment, call-budget,
  and spend acknowledgements.

The 2026-08-07 authorization covers one fresh full v11 publication run. It does
not authorize an unbounded cost or a run against a different revision/surface.

## 2. Fixed protocol

```text
protocol                RS-LoCoMo-Full-v11
dataset commit           3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376
dataset SHA-256          79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4
categories               1, 2, 3, 4
answer-agent model       openai/gpt-5.6-luna
answer temperature       0
max tool calls/question  8
max agent calls/question 9
invalid-completion retry 2 additional attempts within the 9-call cap
answer reasoning effort  none
answer word cap          off (`None`)
judge model              openai/gpt-5.6-luna
judge temperature        0
judge reasoning effort   none
judge repetitions        1
primary metric           judge accuracy
secondary metric         official LoCoMo F1
diagnostic               coarse envelope-evidence session recall
```

The current `memory_v1` `surface_manifest_hash`, prompt and schema hashes,
adapter and repository revisions, manifests, rendered documents, model
identities, complete answer-tool catalog hash, and component generations are
stored. A change creates a new protocol version.

**v10 → v11 (2026-08-07 — complete retrieval plane):** V10 exposed only the
three assured operations to the answer agent even though it built P2/P3 and the
shipping system also exposed seven direct read primitives and nine open-query
operations. V11 replaces that subset with the complete read plane: the three
assured operations, seven direct primitives, nine open-query operations, and
bounded list/grep/read over the ordinary published P3 filesystem. P1 is
reachable through assured, primitive, and SQL search paths; P2 through full
Cypher; live PostgreSQL testimony/facts through primitives and SQL; and P3
through its mount. Writes, controls, raw originals, artifacts, internal-only
primitives, and absent Plane K are not answer tools. The exact 22 descriptors
and P3 limits are fingerprinted. Analysis:
`plan/analysis/locomo_full_retrieval_agent.md`; decision: D85.

**v9 → v10 (2026-08-07 — current-system clean cut):** D83 removed the retired
17 adapters and left exactly `resolve_entity`, `question_context`, and
`current_context` as the shipping intent surface. V10 replaces both executable
v9 variants; it does not preserve an old-catalog mode. Luna occupies both the
answer and judge seats. The ingest and answer stages check the deployment's
public `surface_manifest_hash` and exact canonical recipe descriptors. Ingest
does so before provider preflight or upload; answer does so before any question
call. Historical v9 artifacts remain self-describing, but v9 and v10
scores are not comparable. Analysis:
`plan/analysis/locomo_current_surface_cutover.md`.

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

**v5-strong variant (2026-07-29):** `RS-LoCoMo-Full-v5-strong` initially
changed only the answer agent to `openai/gpt-5.6-luna`. It existed because three smoke
passes on a healthy store (coarse evidence-session recall 0.5, with the gold
evidence at rank 1) scored only 1–2/8 with `openai/gpt-4o-mini`, which looped
past the tool-call limit or returned invalid responses. V9 retained that seat
distinction. V10 supersedes both executable variants and uses Luna.

**Reader retry and answer-effort pin (2026-07-29):** strong-agent smoke runs
showed a separate harness failure. After tool use, `openai/gpt-5.6-luna`
sometimes returned reasoning prose instead of the required JSON answer object.
That happened on 3 of 8 questions in one pass and 1 of 8 in another. With no
retry, each malformed completion permanently scored that question as zero.

The answer reader now retries that same model call up to two times, for three
attempts total. A retry uses the ordinary agent-call ledger: it consumes both
the nine-call per-question allowance and the run's `max_agent_calls` ceiling.
If either allowance is exhausted, the item keeps the same terminal failure it
would have recorded before. In v5–v7, tool-selection calls before any recipe
result and judge calls did not receive this retry. Each answer record stores
`reader_attempts`; the run summary adds `total_reader_retries` without changing
the existing failure categories.

The same smoke work found that setting Luna's reasoning effort to `none` in the
benchmark process environment reduced malformed answers from 3/8 to 1/8 and
raised the score from 1/8 to 3/8. Environment-only configuration was not
reproducible because two runs with identical `run.json` files could behave
differently. The strong variants therefore pinned `answer_agent_reasoning_effort`
to `none` and sent it explicitly on every answer-agent call, including tool
selection and final reading. V10 keeps the Luna `none` pin.
Engine worker seats still use the existing environment map; this override is
only on benchmark answer requests.

Both stable protocol names and CLI keys remain unchanged, but both fingerprints
rolled on 2026-07-29 because the retry budget and effort choice are now part of
protocol identity. All earlier runs were smoke-tier diagnostics, so no
published result is invalidated.

**v5 → v6 (2026-07-30 — temporal ingestion contract):** the source dataset
provides session wall times without any timezone. Earlier adapters kept the
literal timestamp in document text but omitted structured `source_modified_at`.
After E2 began requiring an absolute document-header timestamp for relative-date
arithmetic, that combination produced `date unknown` bundles.

V6 parses the pinned timestamp grammar during local preparation, assigns UTC as
an explicit adapter convention, persists `source_timezone_basis=assumed_utc`,
discloses the assumption in rendered text, and passes the aware timestamp through
`MemoryClient.ingest()`. Invalid timestamps fail before remote work. This changes
rendered documents, ingestion metadata, and derived claim time, so both weak and
strong protocols receive v6 identities and new fingerprints. V5 results are not
comparable.

**v6 → v7 (2026-07-30 — independent retrieval channels):** the ordinary OSS
query path gains independent semantic and BM25 nomination over claims, plus
semantic and BM25 search over source chunks. Every chunk is confirmed against
the current ready Postgres version and representation before its P1 text enters
an evidence envelope. `question_context` fuses projection-only nominations
first, confirms each fused list exactly once, and returns claims and chunks as
separately typed evidence.

The answer prompt now selects `question_context` first for ordinary recall and
requires it before `Unknown`. Durable tool records retain complete raw
envelopes, while the repeated reader prompt omits rank-score bookkeeping and
empty containers; it does not cap or discard retrieved evidence or
freshness. The tool catalog, prompt, reader rendering, adapter version, and protocol
fingerprints changed, so v6 and v7 results are not comparable.

**v7 → v8 (2026-07-31 — answer-stage correctness):** a fresh conv-47 re-score
under v7 scored 91/150 and exposed two protocol constraints that turned usable
model work into forced zeroes. First, the six-word final-answer cap caused 7
terminal `answer_invalid_response` failures. Another 19 judged misses had gold
answers longer than six words, including multi-entity enumerations that could
not be named completely within the cap. The estimated cost was 13–26 questions
out of 150. V8 instead requires the shortest phrase that fully names the
requested entities or values, permits at most twenty words, and forbids
explanations or reasoning. The runner enforces the same twenty-word boundary.

Second, 23/150 questions (about 15%) failed with `answer_reader` before any tool
call: `reader_attempts: 0`, an empty tool trace, and “completion content is not
JSON.” The larger v7 answer prompts averaged about 22,800 input tokens per
question, making this first-step failure common enough to bias the score. V8
applies the existing two-retry malformed-completion allowance before the first
tool call as well as at the reader position. The allowance is shared across the
whole answer loop; every attempt consumes the normal per-question call count,
run-wide call ceiling, and evaluator-cost ceiling. Plain provider outages are
still terminal and are not retried. `reader_attempts` remains limited to calls
after tool results, while `first_step_retries` counts additional pre-tool calls;
the run summary totals both signals separately. The prompt, adapter identity,
runner behavior, and protocol fingerprints changed, while the public tool
catalog did not, so v7 and v8 results are not comparable.

**v8 → v9 (2026-08-03 — Batch B retrieval and flag-gated answer cap):** the
ordinary public catalog adds `documents_about`, `claims_about`,
`claims_as_of`, and `chunk_neighbors`. That descriptor delta rolls the catalog
hash and both protocol identities. `answer_word_cap: int | None` is now an
explicit persisted and fingerprinted protocol field, rendered into the prompt
and enforced only when set. The operator-selected default is `None` for both
v9 registry entries, so the frozen prompt has no word-count sentence and the
runner has no word-count guard. The shortest-complete-phrase/no-explanation
instruction remains unconditional. V9 scores are not comparable to v8 or
earlier.

### 2.1 Model seats

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

V2 through the weak v9 variant deliberately kept the answer agent on
`openai/gpt-4o-mini` while Luna judged it. V10 and v11 instead measure the
owner-selected Luna agent against the current surface. Answer and judge
remain distinct typed roles because their prompts, schemas, budgets, and
accounting differ even though they use the same model.

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
provider-resolved chat model other than the pinned Luna identity also fails.
Each successfully parsed usage record checkpoints immediately and contributes
to the same positive finite run-absolute cost threshold used by answer and
judge; reaching it stops before another probe or an upload. A failure raises
`ProviderPreflightError` and no document is sent. The preflight is
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

Retries do not belong inside the shared adapter because engine workers already
own their work-ledger retry policy. The measured answer-reader failure above is
handled only in the benchmark answer loop, where its two additional attempts
are fingerprinted and charged to the existing agent-call budgets.

## 3. Ingestion mapping

Each conversation runs in a clean isolated deployment. Each session is one immutable Markdown
document. Every turn is rendered:

```text
[D1:3 | 1:56 pm on 8 May, 2023 | UTC assumed] Caroline: ...
```

Image URLs are not fetched. Dataset captions and image queries are included only with explicit
derived-data labels. Session summaries and event summaries are never ingested.

```text
source_kind       locomo
source_ref        <dataset-commit>/<sample-id>/<session-id>
source_modified_at <parsed session wall time with +00:00>
source_timezone_basis assumed_utc (run artifact and rendered disclosure)
versioning_mode   snapshot
source_version_ref <dataset-commit>
```

The dataset has no timezone. The adapter preserves the literal timestamp, parses its fixed
English month grammar without consulting process locale, and assigns UTC. This is an explicit
comparability convention rather than a claim about the speakers' civil timezone. The prepared
document manifest records `source_timezone_basis=assumed_utc`; the Markdown states that the
source timezone is absent and the adapter assumes UTC. Ingest passes the aware UTC value as
`source_modified_at`, giving E2's deterministic document header an absolute date and giving
derived claims their assertion-event time.

The public SDK does not gain a global fallback: ordinary naive datetimes remain invalid because
silently treating a known local wall time as UTC would corrupt it. The HTTP API and durable
`UTCDateTime` models enforce the same invariant, and E0 validates before writing raw bytes. A
wholly absent timestamp stays unknown; it is never replaced by ingestion time. A LoCoMo
timestamp that does not match the pinned grammar or is not a real calendar date fails local
`prepare` before any deployment or provider call. Loading a prepared run recomputes the parsed
timestamp, rendered bytes, and document manifest, so changing either the time or its assumption
prevents resume.

At the catalog boundary, an identical-byte observation may advance `source_version_ref` so
connector polling converges, but it cannot mutate or clear the existing version's
`source_modified_at`. The latter already participated in E2's header, extraction hash, and
derived claims. This preserves D55 version immutability and prevents metadata/extraction drift.
The change prevents future drift; it does not infer or repair timestamps for versions processed
by older code. V6's mandatory clean isolated deployment is the benchmark recovery boundary.

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

After the build, the ordinary self-host `mounts` command materializes the latest
registered P3 snapshot through `LocalMountPublisher`. The operator supplies its
P3 path to `answer`. The runner requires `.snapshot-version` to equal the P3
version in the readiness report before any question call. P3 is therefore both
an integrity requirement and an answer channel in v11; no benchmark-specific
object-store reader or HTTP endpoint exists.

### Plane K

The benchmark records that the stock profile has no K planner/writer runtime. A later
K-enabled LoCoMo run needs an explicit public operation, routing rules,
repository/runtime fingerprints, K settlement in readiness, and a new protocol
name.

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

## 6. Complete retrieval surface

The answer catalog is the exact union of:

- assured operations: `resolve_entity`, `question_context`, `current_context`;
- direct primitives: `resolve`, `lookup_relations`, `transcript_relation`,
  `lookup_observations`, `search_claims`, `search_chunks`, `hydrate_relation`;
- open query: `query_sql`, `explain_sql`, `query_cypher`, `explain_cypher`,
  `describe_query_space`, `search_query_space`, `list_saved_queries`,
  `describe_saved_query`, `run_saved_query`; and
- mounted P3: `p3_list`, `p3_search`, `p3_read`.

These are all read paths. Ingest, connector administration, projection builds,
readiness, raw originals, artifacts, internal-only primitive names, and Plane K
are absent. “Complete” means the agent may choose any shipped retrieval
channel; it does not mean every question must call every channel.

The protocol pins the checked-in `surface_manifest_hash`, verifies it against
`GET /query/space` before ingestion and again before answering, and requires
`GET /recipes` to equal the canonical three descriptors at both boundaries.
Each descriptor carries an `implementation_chain_hash` computed from the live
registry row, so equality covers the chain the executor will actually run, not
only its name and schema. This catches both implementation-contract drift and
registry bootstrap drift before remote processing or answer-model spend.

The nine open-query descriptors come from the same static authority used by the
MCP surface; the seven primitive descriptors adapt the exact public SDK
methods; and the three P3 schemas and their output limits are pinned by the
benchmark adapter. The canonical descriptor hash is part of `run.json`.
P3 listing visits at most 2,000 entries and returns at most 200. P3 search
visits at most 10,000 entries, reads at most 2,000 files / 8 MiB, and returns at
most 50 matches. P3 read accepts a start line no larger than 10,000,000 and
returns at most 400 lines from a file no larger than 256 KiB. These operative
limits are present in the hashed descriptors, not only in implementation
constants.

No benchmark tool reads Postgres, Lance, MinIO, graph files, or internal
handlers directly. Product reads go through `MemoryClient`; filesystem reads
stay inside the normal P3 mount.

## 7. Answer loop

For each question:

1. Render the frozen answer-agent prompt with the question, all 22 tool
   descriptors, and prior trace.
2. Ask for strict `AnswerAgentStep`.
3. For `action="tool"`, validate the name against the catalog, decode
   `arguments_json` by taking its first complete JSON object (trailing text is
   recorded on the trace row, not discarded silently — see §2.4), and dispatch
   it through `MemoryClient` or the bounded P3 adapter.
4. Append arguments, latency, and the complete JSON response. Assured and
   primitive responses retain their complete envelopes.
5. For `action="answer"`, require at least one tool call. The prompt requires
   the shortest phrase that fully names the requested entities or values and
   forbids explanations or reasoning. Enforce a numeric word cap only when the
   prepared protocol's `answer_word_cap` is set; v11 leaves it unset.
6. Retry a completion that cannot produce the required JSON step up to two
   times, including before the first tool call. The allowance is shared across
   the loop; every attempt counts toward the normal per-question, run-wide, and
   cost budgets. Plain provider outages are never retried.
7. Stop at eight tools or nine model calls; budget exhaustion is a visible wrong.
8. Checkpoint the terminal answer or failure. `reader_attempts` counts only
   reader-position attempts after tool results; `first_step_retries` counts
   additional calls made before any tool result.

The agent is instructed to choose the cheapest suitable channel: assured
operations for ordinary typed recall/current truth, direct primitives for
targeted evidence and audit, discovery before unfamiliar SQL/Cypher, SQL for
live composition and P1 search functions, Cypher for P2 graph questions, saved
queries for shipped patterns, and P3 for filesystem orientation/grep/read. It
must verify load-bearing snapshot findings against live fact/evidence paths and
respect grain, validity, freshness, truncation, typed negatives, and hydration
drops. It receives no gold answer, evidence IDs, summaries, or outside
retrieval.

Loop guards in the frozen answer prompt (v11): never repeat a tool call with the
same tool and the same arguments; if a tool yields nothing useful, switch tools
rather than retrying it; and try at least one content-bearing retrieval path
before answering "Unknown". These are prompt discipline, not harness enforcement
— the harness still only bounds call counts.

The answer agent sees a compact projection of each trace response: all facts,
claims, chunks, sources, timestamps, freshness, negatives, truncation, and
hydration-drop counts remain, while rank-score bookkeeping and empty containers
are omitted from envelopes. SQL/Cypher/discovery/P3 response content is not
silently reduced. Default-valued temporal and validity fields are retained
rather than being silently classified as noise. The complete response remains
in the durable `ToolCallRecord`.

Evidence claims found anywhere in the trace are de-duplicated in first-seen
order for the coarse session diagnostic. Because SQL, Cypher, and P3 do not
necessarily return typed envelope claims/chunks, the diagnostic is explicitly
envelope-evidence only. It remains separate from the primary score.

## 8. Commands

Local preparation:

```bash
uv run --extra benchmark python -m benchmarks.locomo prepare \
  --dataset /absolute/path/locomo10.json \
  --tier smoke \
  --protocol full-v11 \
  --output .benchmark-runs/locomo-smoke
```

`--protocol` exists only on `prepare`. The sole choice is `full-v11`; ingest,
answer, judge, and summarize read it from the prepared run and expose no
protocol override.

Per isolated sample:

```bash
uv run --extra benchmark python -m benchmarks.locomo ingest \
  --run .benchmark-runs/locomo-smoke \
  --sample conv-26 \
  --max-documents 19 \
  --max-evaluator-cost-usd 1.00 \
  --execute \
  --confirm-isolated-deployment conv-26

docker compose --profile operations run --rm projections

mkdir -p "$PWD/.benchmark-mounts"
docker compose --profile operations run --rm \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/.benchmark-mounts:$PWD/.benchmark-mounts" \
  projections mounts --root "$PWD/.benchmark-mounts"

uv run --extra benchmark python -m benchmarks.locomo answer \
  --run .benchmark-runs/locomo-smoke \
  --sample conv-26 \
  --p3-root "$PWD/.benchmark-mounts/$REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID/p3" \
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

Transport/server errors, invalid tool decisions, schema failures, provider accounting failures,
step exhaustion, and missing records remain explicit and score zero. Typed
caller-correctable parse, argument, allowlist, and saved-query-state failures,
plus rejected P3 arguments, are retained as failed tool results so the bounded
agent can correct its plan. Classification uses the public query error code for
both HTTP errors and HTTP-200 `QueryResult/v1` failures; auth, quota,
concurrency, schema drift, projection/store unavailability, transport, timeout,
resource, and execution failures remain terminal. Successfully parsed provider
usage is added to the shared answer/judge ledger. A call that crosses the CLI reported-spend
threshold is recorded as a failure and stops the run. Later unanswered items remain explicit
zero-scored missing records unless the operator resumes with an explicitly higher threshold.
Provider-side account limits remain the hard monetary boundary because a process can die after
billing but before checkpointing.

A missing, unreadable, or readiness-version-mismatched P3 mount is a terminal
pre-answer failure for every remaining item. The command checkpoints those
zeroes to preserve the denominator; a repaired mount therefore requires a
fresh prepared run and fresh ingestion rather than resuming over those records.

## 10. Pre-run checklist

- Clean git revision equals `run.json`.
- Local dataset hash and manifest validate.
- One fresh deployment is dedicated to exactly one conversation.
- Before each upload, the public join from `documents_live` to
  `document_versions_visible` on both `deployment_id` and `doc_id` contains
  exactly the checkpointed `(deployment_id, source_ref, doc_id, version_id)`
  tuples (therefore none on a fresh deployment). This uses visible-version
  identity because `documents_live.current_version_id` is a readiness pointer
  and remains null until processing publishes ready content. Every ingest
  response says a new version was created; before answering, the same exact
  checkpointed tuples equal the complete prepared sample.
- Explicit ingestion model IDs are set; no rotating model router.
- All ten workers are running.
- Every prepared session has an ingest record.
- P2/P3 one-shot build completed.
- The ordinary mount publisher completed and its P3 `.snapshot-version` equals
  readiness.
- Public readiness is true; current serving-process model bindings are reviewed as
  configuration, not processing-time provenance.
- Public surface hash, recipe descriptors, live implementation-chain hashes,
  nine open-query names, and the fingerprinted complete answer catalog match.
- Account/provider hard limits and the CLI reported-spend stop threshold are acceptable.
- No claim is made that K ran.
- Raw artifacts and failures will be retained for publication review.
