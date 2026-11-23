# Error reporting contract

**Status:** proposed for review (design PR); binding once D158 is accepted.
**Date:** 2026-11-23
**Decision log:** **D158**
**Analysis:** [error_reporting_contract_analysis.md](../analysis/error_reporting_contract_analysis.md)

## 1. Outcome

Every engine failure reaches the operator's error tracker with a stable code
that says what failed:

- **Self-hosters** connect Sentry, GlitchTip, Bugsink, or any OpenTelemetry
  backend (Grafana, Datadog, Honeycomb, New Relic, Elastic, PostHog Logs)
  with environment variables only.
- **Embedders**, such as remember.dev's runtime, pass a callable that
  receives each error report.

The engine contains no tracker-specific code beyond the existing
Sentry-protocol adapter and the standard OpenTelemetry exporter.

## 2. The contract

The contract applies to the server-side engine (`rememberstack.*`). The
`remember` client package runs on users' machines and reports to its caller;
the server reports what it saw.

**Rule 1: a failure is raised or reported, never hidden.** Code that catches
a failure, or detects one without an exception, does exactly one of these:

- re-raises it, or raises a new exception chained `from` it;
- reports it once with `report_failure(logger, code, **tags)`. This logs at
  ERROR on a `rememberstack.*` logger with `extra={"error_code": code,
  ...tags}`, and with `exc_info` when an exception is active. A direct
  `logger.error` or `logger.exception` with the same `extra` is equivalent.

A failure may additionally be returned as a value (an outcome, a per-item
code) when callers need it, but only together with the report.

**Rule 2: expected outcomes are not errors.** These log at WARNING with an
`error_code`:

- retries that will run again;
- disclosed query boundaries (budget timeouts, an unavailable search
  projection);
- budget, no-route and admission parks.

Cache misses, credential declines and 4xx client errors are not reported
at all. A degradation that drops paid work or a capability the user sees,
such as a structuring fallback or a missing summary, is an ERROR even when
the document succeeds.

**Rule 3: the code is stable.** `error_code` matches `[a-z][a-z0-9_]{2,63}`
and is a literal at the call site. It can also be the `code` attribute of
an engine exception class that declares one. A code names the failure, not
the instance. Renaming or removing one is a breaking change listed in the
changelog.

**Rule 4: the message is a fixed sentence.** Identifiers (ids, counts,
stage) go in tags; document, query or prompt text never does. The code,
exception type and stack, or the call site when there is no exception,
must be enough to find the failing line.

**Rule 5: the reporting path doesn't report itself.** A failure inside a
sink or a generation recorder logs at WARNING on a logger the handler
ignores.

### 2.1 Boundaries

| Boundary | Code | Level |
|---|---|---|
| Worker item, retry scheduled (`workers/base.py:485`) | `worker_item_retry` | WARNING |
| Worker item, dead letter or non-retryable (`:451`) | `worker_item_failed`, or the exception's declared `code` | ERROR |
| Worker terminal finalizer (`:671`), lease renewal (`:257`), usage persistence (`:692`) | `worker_finalizer_failed`, `worker_lease_renewal_failed`, `provider_usage_unrecorded` | ERROR |
| HTTP `ProviderCallError` handler (`surfaces/http_api.py:793`) | `provider_call_failed` (same 503) | ERROR |
| HTTP unmapped exception (new catch-all handler, same 500) | `api_unhandled_exception` | ERROR |
| MCP tool unexpected failure (`_internal_error`, `surfaces/mcp.py:568`) | `mcp_tool_failed` (same wire response) | ERROR |
| Storage capture, per failed scope (`profiles/storage_capture_batch.py`) | the engine refusal's declared `code`, else `storage_capture_failed` | ERROR |

Worker reports carry the tags `stage`, `lane` and `processing_id`.

## 3. The handler and the error report

`rememberstack.error_reporting` provides:

- **`ErrorReport`**, an immutable value built from one log record. It holds:
  - `code`, `level`, `logger`, `service`;
  - `exception_type` (or none);
  - `frames`: (file basename, function, line) for the traceback, or a single
    call-site frame when there is no exception;
  - `tags`, allowlisted: `stage`, `lane`, `processing_id`;
  - in `full` detail only, `message` and `extras`.
- **`ErrorReportHandler(sinks, detail)`**, a `logging.Handler` at ERROR,
  attached to the `rememberstack` logger. It accepts records with or
  without an exception. A record without `error_code` gets
  `unclassified_error`; the CI check makes that a test failure for engine
  code. The handler builds one `ErrorReport` and calls each sink with it. A
  sink exception is caught and logged at WARNING on
  `rememberstack.error_reporting`, which the handler ignores.
- **`install_error_reporting(sink, *, detail="metadata", service)`**, the
  embedder entry point. It attaches one handler with the caller's sink, a
  callable that takes an `ErrorReport`.

Sinks never see the raw `LogRecord`, so what leaves the process is exactly
what `ErrorReport` allows.

## 4. Built-in sinks

Both are off unless configured. Process start (the API, workers, `setup`
and `meter-receipts`: everywhere Sentry is initialized today) installs the
handler with each configured sink.

| Tracker | Configuration | Sink |
|---|---|---|
| Sentry, GlitchTip, Bugsink | `REMEMBERSTACK_SENTRY_DSN`, `_ENVIRONMENT`, `_SAMPLE_RATE` (unchanged) | Builds one Sentry event per report: type, frames, level, `error_code` tag, the worker tags. The SDK's logging integration is set to `event_level=None` (breadcrumbs stay off). `TelemetryPort` no longer sends exceptions to Sentry and `before_send` no longer drops worker records, so each failure has exactly one path. `before_send` keeps its scrubbing as a backstop. |
| Any OpenTelemetry backend | Standard `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` (or `OTEL_EXPORTER_OTLP_ENDPOINT`), `OTEL_EXPORTER_OTLP_LOGS_HEADERS`, `OTEL_SERVICE_NAME` | Emits one OTel log record per report through a `LoggerProvider` and the OTLP HTTP exporter (both already in the image). Attributes: `error.code`, `exception.type`, `code.frames` (basename:function:line lines), the tags, and in `full` the message. No stock `LoggingHandler` and no root-logger attachment. |

`REMEMBERSTACK_ERROR_DETAIL` = `metadata` (default) | `full` sets the
handler's detail for the built-in sinks. Any other value refuses to start.
It does not change container logs, which already carry full detail and are
documented as content-bearing.

## 5. Enforcement in CI

**Contract check** (a test parsing `src/rememberstack` with `ast`). Every
`except` handler that catches a failure class (analysis §5.1) must do one
of these:

- contain `raise`;
- call `report_failure(...)`;
- call `logger.error` or `logger.exception` with an `extra` dict literal
  containing `"error_code"`;
- carry a `# error-contract: <reason>` comment on the `except` line. The
  reason starts with `raised later`, `expected` or `exempt`. This covers the
  analysis's "raised later", "expected" and "exempt" rows; expected rows
  also log at WARNING with their code.

Separately, every `.error`, `.exception` and `.critical` call in
`src/rememberstack` passes `error_code`, and every literal code matches
Rule 3.

**Handler and sink tests** raise an error whose message, locals and extras
carry a marker. They feed it through `ErrorReportHandler` into a fake
Sentry transport and a fake OTLP exporter, and assert:

- `metadata`: the marker never appears in the serialized payload; code,
  type and frames do.
- `full`: the message and extras appear.
- Not configured: nothing is sent.
- A record without an exception is reported with its call-site frame.
- A raising sink doesn't break the other sink or the caller.
- An invalid detail value refuses to start.
- A failed worker item yields exactly one Sentry event with its code and
  worker tags.

The existing wire responses (HTTP 503/500, the MCP tool error) are
asserted unchanged.

## 6. Fixes in scope

Analysis §5.2 and §5.3 are the work list:

- 23 sites gain a report;
- 13 existing ERROR logs gain a code;
- 8 expected outcomes log at WARNING with a code;
- 38 sites ("raised later" and exempt) get their `# error-contract:`
  comment;
- the three boundaries outside `except` (§5.3) change as listed;
- the storage-capture failure code comes only from a declared refusal
  `code` attribute, else `storage_capture_failed`. Arbitrary exception text
  can no longer become a code.

## 7. Documentation

[Observability](../../website/src/app/docs/self-hosting/observability/page.mdx)
gets these sections:

- the contract: ERROR records with `error_code`, and the boundary table;
- connecting a tracker:
  - Sentry: a DSN example;
  - OpenTelemetry: endpoint and headers examples, including PostHog Logs
    (`https://eu.i.posthog.com/i/v1/logs` with `Authorization=Bearer
    <project token>`). The page notes that this feeds PostHog Logs, not
    Error Tracking issues;
- the detail switch;
- for embedders, `install_error_reporting` with a minimal sink.

[Configuration reference](../../website/src/app/docs/reference/configuration/page.mdx)
lists `REMEMBERSTACK_ERROR_DETAIL` and the OTel variables the engine reads.
`compose.yaml` passes them through to every process that reports.

## 8. Not in scope

- Traces and spans: the engine doesn't trace requests, and model-call
  tracing stays benchmark-only.
- Vendor SDKs other than the existing Sentry adapter.
- Metrics.
- A central code registry: codes live at their call sites, and CI checks
  their format.
- Reporting from the `remember` client CLI.
