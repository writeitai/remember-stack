# Error reporting contract

**Status:** proposed for review (design PR); binding once D158 is accepted.
**Date:** 2026-11-23
**Decision log:** **D158**
**Analysis:** [error_reporting_contract_analysis.md](../analysis/error_reporting_contract_analysis.md)

## 1. Outcome

Every unexpected engine failure becomes either an exception or one ERROR log
record with a stable code, through standard Python `logging`. That is the
engine's whole obligation. **The engine owns no delivery**: no tracker sink,
no queue, no exit drain. Whoever runs or embeds the engine attaches their
tracker to Python logging, which every common error tool reads: Sentry,
PostHog and OpenTelemetry. remember.dev's runtime attaches PostHog this way.

## 2. The contract

**Rule 1: an unexpected failure is raised or logged, never hidden.** Code
that catches one, or detects one without an exception, does exactly one of
these:

- re-raises it, or raises a new exception chained `from` it;
- logs it once at ERROR on a `rememberstack.*` logger:
  `logger.error(msg, exc_info=True, extra={"error_code": CODE, ...})`, or
  `logger.exception(...)` with the same `extra`.

A failure may also be returned as a value (an outcome, a per-item code),
but only together with that log record. `pass`, a bare counter or a `None`
result alone is not allowed.

**Rule 2: expected outcomes are not errors.** These log at WARNING with an
`error_code`:

- retries that will run again;
- disclosed query boundaries;
- budget, no-route and admission parks.

Cache misses, credential declines and 4xx client errors are not logged as
failures. A degradation that drops paid work or a capability the user sees,
such as a structuring fallback or a missing summary, is an ERROR even when
the document succeeds.

**Rule 3: the code is stable.** `error_code` matches `[a-z][a-z0-9_]{2,63}`
and is a literal at the call site. It can also be the `code` attribute of
an engine exception class that declares one. Renaming or removing a code is
a breaking change listed in the changelog.

**Rule 4: the message is a fixed sentence.** Identifiers (ids, counts,
stage, lane, `sqlstate`) go in `extra`; document, query or prompt text never
goes in the message. A handler that sends only the exception type, the code
and the stack then still identifies the failure.

The record carries what Python logging always carries: message, exception
and traceback, logger name, call site, and the `extra` fields. What a
handler forwards is the handler owner's policy. remember.dev, for example,
forwards metadata only (UMC D107). The engine has no detail switch.

### 2.1 Boundaries the engine logs at

| Boundary | Code | Level |
|---|---|---|
| Worker item fails and a retry is scheduled (`workers/base.py:485`) | `worker_item_retry` | WARNING |
| Worker item fails with no retry left (`:485`) or non-retryable (`:451`) | `worker_item_failed`, or the exception's declared `code` | ERROR |
| Expired lease requeued / dead-lettered by the reaper (`:612`). A failed requeue wake (`:627`) is logged and the sweep continues with the remaining rows | `worker_lease_expired_retry` (WARNING), `worker_lease_expired` (ERROR), `worker_requeue_announce_failed` (ERROR) | as listed |
| Worker finalizer (`:671`), lease renewal (`:257`), usage persistence (`:692`) | `worker_finalizer_failed`, `worker_lease_renewal_failed`, `provider_usage_unrecorded` | ERROR |
| HTTP `ProviderCallError` handler (`surfaces/http_api.py:793`) | `provider_call_failed`; same 503 | ERROR |
| HTTP route exception with no mapped handler: a catch-all exception handler logs, then returns the same 500 | `api_unhandled_exception` | ERROR |
| API lifespan (startup/shutdown) failure, e.g. the cost-export listener failing to bind: the API's lifespan logs, then re-raises | `api_lifespan_failed` | ERROR |
| MCP tool unexpected failure (`_internal_error`, `surfaces/mcp.py:568`) | `mcp_tool_failed`; same tool error | ERROR |
| Query sandbox: any database error that becomes a query result (`surfaces/query_sandbox/executor.py:118`, `:837`–`:944`), with `sqlstate` in `extra`. The one exemption: the request's own deadline has passed when the error is caught (judged by time), which is the disclosed budget outcome | `query_database_failed`; same wire result | ERROR |
| Query sandbox: an operational `SandboxRejection` from the bridge (`P1_UNAVAILABLE`, embedding provider failure; `bridge.py:369`) | `query_dependency_unavailable`; same wire result | ERROR |
| Live graph: `GraphHydrationError` (`graph_queries.py:77`, quiet 503 at `http_api.py:1289`); `GraphBusyError` (`:73`) | `graph_hydration_mismatch` (ERROR); `graph_capacity_exhausted` (WARNING) | as listed |
| Managed metering: producer credential rejected (`MeterReceiptUnauthorized`, `spine/managed_metering.py:164`, `:423`) | `meter_receipt_unauthorized`; replay continues | ERROR |
| Storage capture, per failed scope (`profiles/storage_capture_batch.py`) | the refusal's declared `code`, else `storage_capture_failed` | ERROR |

**Storage capture.** The declared codes are the fixed reason strings its
refusals already use: `NativeStorageCaptureRefused`, the managed capture
refusals (`profiles/managed_storage_capture.py:105`),
`ProviderCaptureUnavailable` (e.g. `provider_transport_lost`,
`adapters/fsit_storage_capture_transport.py:378`) and the batch admission
errors. Each class gains a `code` attribute set from the same literal.
Arbitrary exception text never becomes a code.

The batch entry point UMC calls, `capture_storage_batch`, gains an optional
`on_scope_failure(code)` callback. It is called the moment a scope's
failure is known, before the batch waits for peers or provider cleanup, so
a caller can stream codes across a process boundary. This is an API hook,
not delivery.

## 3. Connecting a tracker (documentation)

[Observability](../../website/src/app/docs/self-hosting/observability/page.mdx)
documents the contract, including the boundary table and the code format,
and shows a few lines of setup for each tool:

- **Sentry, GlitchTip, Bugsink.** The existing `REMEMBERSTACK_SENTRY_DSN`
  adapter is kept unchanged. Its SDK logging integration already sends
  ERROR records. The page also shows `sentry_sdk.init(dsn=...)` in a custom
  entrypoint, for those who run the engine their own way.
- **PostHog.** A short `logging.Handler` that calls the PostHog SDK's
  `capture_exception` with `error_code` as a property, attached to the
  `rememberstack` logger. The page also shows OTLP logs to PostHog Logs
  (`/i/v1/logs`), noting that this feeds Logs, not Error Tracking.
- **OpenTelemetry.** Run under `opentelemetry-instrument` (zero-code logs),
  or attach `opentelemetry.sdk._logs.LoggingHandler` to `rememberstack` in
  a few lines.

The examples attach to the `rememberstack` logger at ERROR level, and also
keep a stderr handler so container logs continue.

## 4. Enforcement in CI

**One rule** (a test parsing `src/rememberstack` with `ast`). Every `except`
that catches a failure class (analysis §5.1) must do one of these:

- contain a `raise`;
- contain a logging call at ERROR (`.error` or `.exception` on a module
  logger) whose `extra` includes `"error_code"`;
- call `_internal_error`, which logs that way;
- carry `# error-contract: <reason>` on the `except` line.

Separately, every `.error`, `.exception` and `.critical` logging call in
`src/rememberstack` passes `error_code`, and every literal code matches
Rule 3. Whether every branch of a handler is right is checked in review,
not by the rule.

Boundary tests assert the code and level for each row of §2.1, and that
wire responses are unchanged. That includes:

- a sandbox error before the deadline is logged, and one after it is not;
- a failed requeue wake doesn't stop the sweep;
- `on_scope_failure` fires before a peer scope finishes.

## 5. Work list

Analysis §5 is the implementation backlog: the handler tables, the
branch-raising handlers, the 18 ERROR calls and the boundaries. Per-site
precision is reviewed in the implementation PRs. A site found later follows
the same rules in that PR and is added to the analysis; it doesn't reopen
this design.

## 6. Not in scope

- Any delivery in the engine: sinks, queues, drains, exit hooks.
- A detail switch.
- Changes to the existing Sentry adapter.
- Traces, spans and metrics.
- A central code registry.
- Reporting from the `remember` client CLI.
