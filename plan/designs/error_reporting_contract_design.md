# Error reporting contract

**Status:** proposed for review (design PR); binding once D158 is accepted.
**Date:** 2026-11-23
**Decision log:** **D158**
**Analysis:** [error_reporting_contract_analysis.md](../analysis/error_reporting_contract_analysis.md)

## 1. Outcome

Every engine failure reaches the operator's error tracker with a stable code
that says what failed. A self-hoster connects Sentry, GlitchTip, Bugsink, or
any OpenTelemetry backend (Grafana, Datadog, Honeycomb, New Relic, Elastic,
PostHog Logs) with environment variables only. A program that embeds the
engine, such as remember.dev's runtime, attaches any Python logging handler.
The engine contains no code for a specific tracker beyond the existing
Sentry-protocol adapter and the standard OpenTelemetry exporter.

## 2. The contract

**Rule 1: a failure is raised or reported, never hidden.** Code that catches
an exception, or detects a failure without one, does exactly one of these:

- re-raises it (or raises a new exception chained `from` it); or
- logs it once at `logging.ERROR` on a `rememberstack.*` logger, with
  `exc_info` when an exception exists and `extra={"error_code": CODE}`.

A failure may additionally be returned as a value (an outcome, a per-item
failure code) when callers need it, but only together with the ERROR record.
`pass`, a bare counter or a `None` result alone is not allowed.

**Rule 2: expected outcomes are not errors.** Retries that will run again,
budget/no-route/admission parks, cache misses, a credential an adapter
declines, and client errors returned as 4xx are WARNING, INFO or DEBUG. A
degradation that drops paid work or a capability the user sees, such as a
structuring fallback or a missing summary, is an ERROR, even when the
document still succeeds.

**Rule 3: the code is stable.** `error_code` matches `[a-z][a-z0-9_]{2,63}`.
It is a literal at the log site, or the `code` attribute of an exception
class that defines one. A code names the failure, not the instance. Renaming
or removing one is a breaking change listed in the changelog.

**Rule 4: the message is a fixed sentence.** Put identifiers (ids, counts,
stage names) in `extra`, and never put document, query or prompt text there.
In the default `metadata` detail (§4) a sink drops the message. The code,
the exception type and the stack must be enough to find the failing site.

**Rule 5: the reporting path doesn't report itself.** A failure inside a
telemetry sink or generation recorder stays a WARNING; reporting it to the
same sink would loop. These sites are listed in the exemption list (§5).

Two boundaries catch everything else, and each logs on a `rememberstack.*`
logger:

- **Workers.** A failed item is already logged at ERROR by
  `workers/base.py` (`logger.exception`) and exported through `TelemetryPort`
  (D61). The record gains `error_code`: the exception's `code` when it has
  one, otherwise `worker_item_failed`. `stage` and `lane` go in `extra`.
- **HTTP API and MCP.** An exception no route handler maps is logged at
  ERROR with `error_code="api_unhandled_exception"` by one catch-all
  exception handler, which then returns the same 500 as today. Today only
  the server (`uvicorn.error`) logs these, outside `rememberstack.*`.

## 3. Connecting a tracker

| Tracker | Configuration | Mechanism |
|---|---|---|
| Sentry, GlitchTip, Bugsink (Sentry protocol) | `REMEMBERSTACK_SENTRY_DSN`, `_ENVIRONMENT`, `_SAMPLE_RATE` (unchanged) | Existing adapter. The SDK's logging integration sends ERROR records as events. The adapter adds `error_code` as a tag, beside the worker tags `stage`, `lane` and `processing_id`. |
| Any OpenTelemetry backend | Standard `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` (or `OTEL_EXPORTER_OTLP_ENDPOINT`), `OTEL_EXPORTER_OTLP_LOGS_HEADERS`, `OTEL_SERVICE_NAME` | New: when an endpoint is set, process start attaches an OTLP `LoggingHandler` for ERROR records on the root logger. `error_code` becomes a log attribute. Uses the already installed `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http`. |
| Anything else, from code | `logging.getLogger("rememberstack").addHandler(handler)` | Plain Python logging; nothing engine-specific. This is how remember.dev attaches PostHog. |

Process start means the same place that initializes Sentry today: the API,
the workers and `setup`. Both built-in sinks are off unless configured. A
failing sink never changes product outcomes.

## 4. Detail switch

`REMEMBERSTACK_ERROR_DETAIL` applies to both built-in sinks:

| Value | Sent |
|---|---|
| `metadata` (default) | Exception type, `error_code`, logger name, worker tags, stack frames (file basename, function, line). The message becomes `[redacted]`. No locals, source lines, breadcrumbs, request data or other extras. |
| `full` | Everything in `metadata` plus the message and the record's `extra` fields. Sentry also includes local variables. For testing and debugging. |

Any other value refuses to start. The switch doesn't change container logs,
which already carry full detail and are documented as content-bearing.
Embedders apply their own policy.

## 5. Enforcement in CI

- **Contract check** (a test that parses the source): every `except` handler
  in `src/rememberstack` must re-raise, log at ERROR with an `error_code`, or
  carry a `# error-contract: <reason>` comment. The comment is used for the
  exempt sites in the analysis (§5.3) and the "correct as is" sites (§5.2).
  Every `error_code` literal must match Rule 3's pattern.
- **Sink tests** use the existing fake Sentry SDK and a fake OTLP exporter.
  They raise an error whose message and locals contain a marker, and assert:
  - with `metadata`, the marker never reaches the fake transport, and type,
    code and frames do;
  - with `full`, the message arrives;
  - with no configuration, nothing is sent;
  - an invalid switch value refuses to start.

## 6. Fixes in scope

The eight "must report" sites in analysis §5.1 gain an ERROR record with the
listed code; their outcomes don't change. The two boundaries in §2 gain
their codes and the API catch-all handler. The storage-capture batch logs one
record per failed scope with that scope's failure code. That code is the same
value it already returns (v0.21.1, #622).

## 7. Documentation

[Observability](../../website/src/app/docs/self-hosting/observability/page.mdx)
gets these sections:

- the contract (ERROR records with `error_code`);
- connecting a tracker: the §3 table plus one example each for a Sentry DSN
  and an OTLP endpoint with headers;
- the detail switch;
- for embedders, the logging snippet.

[Configuration reference](../../website/src/app/docs/reference/configuration/page.mdx)
lists `REMEMBERSTACK_ERROR_DETAIL` and the OTel variables the engine reads.
`compose.yaml` passes the new variables through.

## 8. Not in scope

- Traces and spans: the engine doesn't trace requests, and model-call
  tracing stays benchmark-only.
- Vendor SDKs other than the existing Sentry adapter.
- Metrics.
- A central registry of codes: codes live at their sites, and the contract
  check enforces their format.
