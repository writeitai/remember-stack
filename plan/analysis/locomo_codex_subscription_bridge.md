# LoCoMo generation through a Codex ChatGPT subscription

**Status:** analysis; non-binding.
**Date:** 2026-09-06.
**Question:** can the LoCoMo answer and judge seats use the operator's Codex
ChatGPT login without copying an access token or issuing an API key, while
preserving the harness's staged execution and durable records?

## Constraints

- Authentication material must remain owned by Codex. The benchmark must not
  parse `~/.codex/auth.json`, copy its OAuth bearer token, or accept an OpenAI
  API key for these seats.
- The answer loop remains the existing RememberStack loop. Codex supplies one
  schema-constrained decision at a time; it does not receive native access to
  RememberStack tools, the benchmark corpus, or the host filesystem.
- Each call must retain token usage when the SDK returns it; the harness must
  never fabricate counters for a failure raised before usage is returned.
- Existing OpenRouter and Vertex protocols and historical run fingerprints
  must continue to load unchanged.
- Codex does not expose the OpenRouter protocol's temperature control, and the
  Codex `gpt-5.6-luna` seat does not support reasoning effort `none`. A run must
  not claim that either setting was honored.

## Sources inspected

Retrieved 2026-09-06:

- OpenAI's [Codex app-server documentation](https://developers.openai.com/codex/app-server/)
  defines the supported local integration boundary. It documents ChatGPT-managed
  authentication and refresh, ephemeral threads, per-turn `outputSchema`, and
  token-usage events.
- OpenAI's [Codex SDK documentation](https://learn.chatgpt.com/docs/codex-sdk)
  identifies the Python SDK as the supported automation wrapper around
  app-server and states that the package carries a pinned Codex runtime.
- OpenAI's [Codex authentication documentation](https://developers.openai.com/codex/auth/)
  describes browser-based `codex login` with a ChatGPT subscription.
- Team Harness commit
  [`c07e3ab`](https://github.com/writeitai/team-harness/tree/c07e3ab4b33018649e4f4ba6c79174da246ab973)
  was inspected as prior art. Its experimental coordinator reads the private
  auth JSON, decodes the JWT, extracts the account id, and calls an internal
  `chatgpt.com/backend-api` route directly. That proves the broad idea but not
  the safe boundary required here.

## Alternatives

### Read Codex auth and call the internal response route directly

This is the current Team Harness experiment. It is small and avoids launching
app-server, but the application becomes responsible for bearer-token handling,
JWT/account-id assumptions, refresh behavior, and an internal endpoint. An
expired token requires another manual login. Reject for LoCoMo.

### Invoke `codex exec` for every call

The CLI would own authentication and can enforce a subprocess timeout, but the
benchmark would need to parse a JSONL event stream and manage a temporary schema
file. That duplicates behavior already typed by the official SDK. Keep as a
fallback only if the SDK cannot provide a required control.

### Use the official Python SDK and app-server

This keeps OAuth and refresh inside Codex and exposes typed structured output,
account inspection, usage, ephemeral threads, sandboxing, and approval policy.
It is the selected boundary.

## Recommended contract

Add one additive protocol, `full-v24-codex-subscription`, with both evaluator
seats pinned to Codex's `gpt-5.6-luna`, reasoning effort `low`, and temperature
`null`. It retains the v24 prompts, schemas, tool loop, tool/call limits,
retrieval surface, judge rubric, and scoring. It is a distinct protocol rather
than a comparable execution of `full-v24` because provider controls differ.

The adapter creates a fresh ephemeral Codex thread for every model call. It:

1. asks app-server to read the account and requires account type `chatgpt`, so
   an API-key login cannot silently satisfy the bridge; app-server remains
   responsible for refreshing its own login when necessary;
2. uses a new empty temporary working directory, read-only sandbox, disabled
   network, and deny-all approval policy;
3. tells Codex to act only as a structured generator and rejects a completed
   turn if its item trace contains a command, file change, MCP call, web search,
   sub-agent, or other agent action;
4. supplies the Pydantic response model as the turn's JSON Schema, then validates
   the returned JSON again locally; and
5. records the turn-total input/output tokens and wall-clock latency.

The CLI composes providers per stage. `ingest` still uses OpenRouter for the
deployment embedding preflight and routes its chat probe through the pinned
answer provider; `answer` and `judge` use their individually pinned providers.
Consequently an already-ingested Codex-variant run can answer and judge with no
OpenRouter key on the evaluator machine.

## Accounting and operational consequences

ChatGPT subscription turns report tokens but no per-call USD charge. The
adapter therefore records `cost_usd=0` as the provider-reported marginal amount;
this does not mean the subscription seat is free. The existing evaluator-cost
ceiling cannot protect subscription quota. The run-absolute answer/judge call
ceilings, Codex service limits, and an operator-selected smoke/development tier
are the applicable bounds.

A live trivial schema probe on 2026-09-06 completed successfully but reported
10,179 input tokens for a one-sentence prompt, showing that Codex's agent
instructions add substantial context overhead. This makes the bridge useful for
development, answer-agent experiments, and using an already-paid subscription,
but not automatically preferable for a publication-scale benchmark. A complete
run should first measure quota consumption and latency on a smoke slice.

A real `conv-42/qa/0001` integration on the same date exercised the existing
LoCoMo answer loop against a processed PostgreSQL/P3 store through an SSH-local
API tunnel. Codex made one `answer_context` choice and one final-answer choice
(86,255 input and 101 output tokens in aggregate), then the independent Codex
judge completed (10,092 input and 16 output tokens). The answer named only one
of the two gold interests, so the judge correctly returned `WRONG`: transport,
schema, retrieval-loop, accounting, and judge integration succeeded, while this
single item scored zero. The first attempt also exposed and fixed the need to
apply the same closed/all-properties-required strict-schema normalization used
by the OpenRouter and Vertex adapters.

The SDK's synchronous `run` call does not expose a turn deadline. Ctrl-C closes
its app-server subprocess, but a stuck call has no benchmark-enforced 60-second
timeout. This is an accepted experimental limitation; adopt the `codex exec`
subprocess fallback or add a supported SDK cancellation deadline before making
this a default unattended publication path.

The same synchronous helper raises on a failed turn before returning its last
token-usage event. Such a call retains its real error message but has no usage
object in the benchmark ledger. Completed and canceled turns record total usage
whenever the SDK supplies both counters. Publication use would need the lower-
level event stream if failed-turn partial-token accounting becomes material.

No new high-level product decision is required: this is an additive benchmark
provider variant, not a production engine provider or a replacement for the
canonical LoCoMo protocol.
