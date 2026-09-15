# Vertex generate() receiving completions over the streaming route

This analysis records a proposed transport for Vertex structured generation. It
is non-binding. It does not add a transport switch, a retry framework, a
timeout, a token cap, or a numbered architecture decision. `generate()` stays
the existing synchronous `ModelProviderPort` method (D61). The UMC lab
contract in `design/designs/locomo-vertex-lab.md` §§5–6 (UMC D68) already
requires strict schema, synchronous `generate()`, and exact usage; it does
not require a non-streaming HTTP body. Callers still receive one validated
object plus exact usage, or an honest error.

The provider call, usage, and error types this change must keep are
`src/rememberstack/model/model_provider.py`: `GeneratedResponse`,
`ProviderCallUsage`, `ProviderCallError` (optional `usage` for later metering),
`ProviderInvalidResponseError`, and `ProviderAccountingError`.

## The question

The adapter currently POSTs `/chat/completions` and waits for the entire JSON
body before it sees a single token. Should that same call instead ask the
documented streaming route to send server-sent events (SSE), assemble the
complete text locally, and then validate and charge exactly as today?

## What the evidence is, and what it is not

A full processing-only conv-42 Gemma/Vertex run is in progress (engine
`0057e18f`, runner `9520a426`). Eight documents structured, 15 claims, then
further receipts. Since 23:52:25 UTC one non-streaming T4 entity-resolution
request waited for HTTP response headers. Read-only stack and database
inspection showed an HTTP wait, not a database lock.

The parent reconstructed that request from the database (1,139-byte prompt,
SHA-256 `9b6fe68c356bac49e0171e2156fdc70c50cb1d5773769783966833e15cfa6e6b`)
and matched the live frame. The same prompt and strict `T4Selection` schema
sent with `stream: true` finished in 1.269s (316 input tokens, 93 output
tokens, five cached tokens ignored by conservative pricing, USD 0.0001032)
with a valid `new` decision. A second diagnostic using the actual
non-streaming provider started at 00:08 UTC and was still pending at 00:19
UTC; that start time is not a stall timestamp.

The original non-streaming T4 call finished at 00:22:26.116936 UTC after
1801.3395596 seconds with `VertexProviderError`, HTTP 503, body code 503,
message "The service is currently unavailable.", status `UNAVAILABLE`, and
usage NULL. The reconstructed input SHA matched. The worker continued with
three `NormalizationResponse` calls (2.26s / 2.60s / 3.02s). Live R3 then
had 66 receipts and one unknown-usage 503. Durable recording continues in
[UMC PR 590](https://github.com/writeitai/ultimate-memory-cloud/pull/590).

That **suggests transport-path-dependent behavior** on this request: an
observable ~30-minute provider 503 on the non-streaming path versus 1.27s
for the same input over streaming. Two pending or failed non-streaming
calls and one successful stream do not prove causation or a general root
cause. They do not prove that streaming fixes every stall, hang, or invalid
structured output.

Earlier conv-42 notes already separated other failures from transport: the
4,096-token / 120s defaults in
[vertex_processing_limits_20260914.md](vertex_processing_limits_20260914.md),
and the fallback field-name measurement in
[gemma_fallback_subsections_20260915.md](gemma_fallback_subsections_20260915.md).
Those remain distinct. This note does not re-open prompt or schema changes.

## What the provider already documents

Google documents SSE streaming for Gemma 4 on the managed open-model page
(retrieved 2026-09-15):
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/google
which links
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/call-open-model-apis
(page last updated 2026-09-03). That API uses the same URL the adapter
already calls. `stream: true` returns SSE `data:` events, then
`data: [DONE]`. The documented last content event may carry `finish_reason`
and `usage` (`prompt_tokens`, `completion_tokens`). Content arrives in
`choices[0].delta.content`.

OpenAI-compatible servers often send a **usage-only terminal event**
instead: `choices` is an empty list and `usage` is on that last chunk,
enabled by `stream_options.include_usage`. The live T4 streaming receipt
used that flag and received usage in that empty-`choices` shape. The
assembler must accept both shapes. It must not invent token counts or a
dollar amount when no terminal usage arrives.

## Alternative: leave the non-streaming body wait in place

Keeping `stream: false` (today's path) avoids assembling SSE, keeps tests on
one JSON body, and matches the historical adapter. The cost of that
alternative is that this adapter still waits for response headers before it
can fail, validate, or record usage. The 1801s T4 call is that wait ending
in HTTP 503 with unknown usage. Leaving it also means the one measured
streaming success cannot be used by `generate()`.

That alternative stays available: do not merge this until the parent repeats
the real provider check. It is the right choice if a live streaming
`generate()` on this endpoint loses usage, truncates silently, or fails
requests that non-streaming completes.

Streaming is not a second product mode. It is the same completion, delivered
incrementally, still collected to one object before the port returns.

Rejected heavier alternatives (YAGNI):

- A selectable stream / non-stream setting, fallback, or automatic paid retry
  of the other path. Two transports become an untested matrix, and retry after
  bytes have arrived can bill twice.
- A new timeout, output cap, or "looks stuck" abort. PR #405 already removed
  unvalidated cutoffs. A stall remains an operational cancel, not an adapter
  heuristic.
- Changing the model, prompt, or T4 schema to dodge a hang. Prompt/protocol
  rolls need their own source contract; nothing here authorizes one.
- Streaming OpenRouter or other providers. Their accounting and retry rules
  differ. This is Vertex-only.

## The proposed transport

`VertexModelProvider.generate` keeps its signature, strict JSON schema,
`max_tokens` allowance (default 128,000), optional operator deadline (default
none), model pin, and temperature. The POST body adds `stream: true` and
`stream_options.include_usage: true`. HTTP 401/403 stay `VertexAccessError`.
HTTP 429 is still the only retry, and only before a 2xx stream is admitted.
Once headers say the stream has started, this call is not re-sent. Streamed
HTTP error bodies are read before the adapter inspects `response.text`. A
429 response is closed before the backoff sleep. If parsing or the network
fails after a usage event, that real usage is attached to the raised
`VertexProviderError`; missing usage stays missing.

Assembly rules:

- Concatenate string `delta.content` pieces. Ignore comments and empty SSE
  fields. Treat `data: [DONE]` as the end of the event stream, not as JSON.
- Accept usage on the last content event or on a usage-only event with empty
  `choices`.
- Keep `model` and `finish_reason` for the existing diagnosis string (length
  and digest only; never the completion text).
- Success still requires schema-valid JSON, `finish_reason` `stop`, and
  terminal usage that can be charged from the pinned price table. A
  `length` finish is truncated output and is an invalid response, not a
  success, even if the partial JSON happens to parse.
- An incomplete or malformed stream (connection drop, no finish, broken
  event) is a provider error. If complete usage was present, attach it to
  `ProviderCallError.usage`; if not, leave `usage` unset. Never write zeros.
- A finished stream that omits usage remains `ProviderAccountingError`, as
  today: a paid-looking completion the ledger cannot charge, and must not
  auto-retry.

Workers, ingest prompts, and other adapters are unchanged. Parent validates
the real Vertex endpoint before this is adopted.
