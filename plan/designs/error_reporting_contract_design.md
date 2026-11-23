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
| Worker item fails and `ledger.fail()` schedules a retry (`workers/base.py:485`) | `worker_item_retry` | WARNING |
| Worker item fails with no retry left (`:485`, dead letter) or non-retryable (`:451`) | `worker_item_failed`, or the exception's declared `code` | ERROR |
| Expired lease requeued by the reaper (`:612`) | `worker_lease_expired_retry` | WARNING |
| Expired lease on the last attempt, dead-lettered (`:612`) | `worker_lease_expired` | ERROR |
| Worker terminal finalizer (`:671`), lease renewal (`:257`), usage persistence (`:692`) | `worker_finalizer_failed`, `worker_lease_renewal_failed`, `provider_usage_unrecorded` | ERROR |
| HTTP `ProviderCallError` handler (`surfaces/http_api.py:793`) | `provider_call_failed` (same 503) | ERROR |
| HTTP request failure: an exception that propagates out of the served ASGI app (FastAPI's server-error middleware sends the 500, then re-raises), caught by `wrap_asgi_reporting` (§3) | `api_unhandled_exception` | ERROR |
| Uncaught exception ending a process or thread (`sys.excepthook`, `threading.excepthook`, installed with the handler) | `process_unhandled_exception` | ERROR |
| API startup or shutdown failure, e.g. the cost-export listener failing to bind. `wrap_asgi_reporting` is applied **outermost**, to the exact callable Uvicorn serves, after every other wrapping including the cost-export listener (`profiles/selfhost.py:1288`) | `api_lifespan_failed` | ERROR |
| Expired-lease sweep: a requeue announcement fails (`workers/base.py:627`) | `worker_requeue_announce_failed`. The sweep reports it and continues with the remaining rows, so later dead letters are still finalized and reported | ERROR |
| Query sandbox: a `SandboxRejection` the bridge raised for an operational cause (`P1_UNAVAILABLE`, embedding provider failure; `surfaces/query_sandbox/bridge.py:369`), consumed at `executor.py:861` | `query_dependency_unavailable`; same wire result | ERROR |
| Query sandbox: every database error that becomes a query result, in every catch that produces one (`surfaces/query_sandbox/executor.py:837` through `:944`, and the type lookup at `:118`). Only these are not reported: a budget outcome (the existing `budget_exhausted` classifier: request-owned `57014` or `25P04`, or an admission timeout); `55P03` (lock not available) and configuration limits the sandbox already discloses; class `22` (data exception); and class `42` statement errors in the caller's text other than `42501` and `42P01`. Everything else is reported. That includes privilege (`42501`), an allowlisted relation that is missing (`42P01`), and connection, resource, operator-intervention, system and internal errors (`08`, `53`, `57` other than the budget, `58`, `XX`). It also includes every catalog, manifest and type-lookup failure | `query_database_failed` with a `sqlstate` tag; same wire result as today | ERROR |
| Query-space manifest check (`spine/query_space/manifest.py:962`, run at setup and readiness) | The check verifies that every relation and function the sandbox grammar allowlists exists with the expected signature. A missing engine-owned helper therefore fails readiness with `query_manifest_incomplete`, instead of surfacing later as an ambiguous `42883` in a caller's query | ERROR |
| Live graph: `GraphHydrationError` (traversal rows disagree with authoritative rows; `surfaces/graph_queries.py:77`), mapped to a quiet 503 at `surfaces/http_api.py:1289` | `graph_hydration_mismatch`; same 503 | ERROR |
| Live graph: `GraphBusyError` (bounded expansion slots exhausted; `graph_queries.py:73`) | `graph_capacity_exhausted`; same 503 | WARNING |
| Managed metering: the control plane rejects the producer credential (`MeterReceiptUnauthorized`) on the measurement path (`spine/managed_metering.py:164`) or the outcome path (`:423`) | `meter_receipt_unauthorized`; replay continues | ERROR |
| MCP tool unexpected failure (`_internal_error`, `surfaces/mcp.py:568`) | `mcp_tool_failed` (same wire response) | ERROR |
| Storage capture, per failed scope (`profiles/storage_capture_batch.py`) | the engine refusal's declared `code`, else `storage_capture_failed` | ERROR |

Worker reports carry the tags `stage`, `lane` and `processing_id`. Every
boundary passes the caught exception explicitly
(`exc_info=(type, value, traceback)`), so synchronous handlers run in a
thread pool keep their traceback.

Storage capture's declared codes are the fixed reason strings its refusals
already use: `NativeStorageCaptureRefused`, the managed capture refusals in
`profiles/managed_storage_capture.py`, `ProviderCaptureUnavailable` (e.g.
`provider_transport_lost`, `adapters/fsit_storage_capture_transport.py:378`)
and the batch admission errors. Each
of those exception classes gains a `code` attribute, set from the same
literal it puts in its message today, such as
`capture_writer_class_unknown`. The existing test stays: when the batch
transport fails, a scope's own earlier reason is kept.

## 3. The handler and the error report

`rememberstack.error_reporting` provides:

- **`ErrorReport`**, an immutable value built from one log record. It holds:
  - `code`, `level`, `logger`, `service`;
  - `exception_type` (or none);
  - `frames`: (file basename, function, line) for the traceback, or a single
    call-site frame when there is no exception;
  - `tags`, allowlisted: `stage`, `lane`, `processing_id`;
  - in `full` detail only: `message` (the formatted log message),
    `exception_text` (`str()` of the exception) and `extras` (the record's
    non-standard attributes). Values of keys whose name contains `token`,
    `secret`, `password`, `key`, `authorization` or `cookie` are replaced by
    `[masked]`, recursively through nested mappings and sequences. There are
    no local variables in either detail. Masking is by key; a credential
    embedded in message or exception text is not detected, which is why
    `full` is for synthetic data (§4).
- **`ErrorReportHandler(sinks, detail)`**, a `logging.Handler` at ERROR,
  attached to the `rememberstack` logger. It accepts records with or
  without an exception. A record without `error_code` gets
  `unclassified_error`; the CI check makes that a test failure for engine
  code. The handler builds one `ErrorReport` and calls each sink with it. A
  sink exception is caught and logged at WARNING on
  `rememberstack.error_reporting`, which the handler ignores.
- **`install_error_reporting(sink, *, detail="metadata", service,
  loggers=("rememberstack",))`**, the embedder entry point. It attaches one
  handler with the caller's sink, a callable that takes an `ErrorReport`, to
  each named logger, and installs the process and thread excepthooks. It
  also registers **`drain_error_reporting(timeout=2.0)`**. A sink is any
  callable. A sink object that also has `flush(timeout)` buffers reports,
  and the drain calls that flush on the calling thread with the timeout;
  no threads are started at exit. The drain runs from `atexit` (main
  thread) and before the engine's own `os._exit` paths, and embedders call
  it before theirs (remember.dev's convert route). The total wait is bounded
  by the one deadline, shared across sinks.
- **`wrap_asgi_reporting(app) -> app`**, a plain ASGI wrapper applied to
  the exact callable Uvicorn serves. It is used by the engine API and by
  embedders, including remember.dev's `SharedApi`, which owns its own
  lifespan and delegates to an inner FastAPI app. An exception that
  propagates out of a request is reported as `api_unhandled_exception` and
  re-raised. A lifespan startup or shutdown failure is reported as
  `api_lifespan_failed`. A route failure is counted once: FastAPI's
  server-error middleware sends the 500 and re-raises, so only the outer
  wrapper reports it. Nothing inside the app adds a second report.
  An embedder adds its own namespaces; remember.dev passes
  `("rememberstack", "umc_runtime")`.

Sinks never see the raw `LogRecord`, so what leaves the process is exactly
what `ErrorReport` allows.

## 4. Built-in sinks

Both are off unless configured. Process start (the API, workers, `setup`
and `meter-receipts`: everywhere Sentry is initialized today) installs the
handler with each configured sink.

| Tracker | Configuration | Sink |
|---|---|---|
| Sentry, GlitchTip, Bugsink | `REMEMBERSTACK_SENTRY_DSN`, `_ENVIRONMENT`, `_SAMPLE_RATE` (unchanged) | The SDK is initialized with `default_integrations=False` and no integrations, so it captures nothing by itself: no logging, framework, thread or excepthook capture. The sink sends one event per report directly on the adapter's client, `client.capture_event(event, scope=None)`. That bypasses the global, isolation and current scopes, so no ambient tags, contexts, user or breadcrumbs apply. Its `flush` is drained by `drain_error_reporting`. The event holds the exception type, frames, level, the `error_code` tag and the worker tags; in `full`, also the message, exception text and extras (as `extra`). `TelemetryPort` no longer sends exceptions to Sentry, and `before_send` no longer drops worker records. In `metadata`, `before_send` keeps its scrubbing as a backstop. |
| Any OpenTelemetry backend | Standard `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` (or `OTEL_EXPORTER_OTLP_ENDPOINT`), `OTEL_EXPORTER_OTLP_LOGS_HEADERS`, `OTEL_SERVICE_NAME` | One **daemon worker thread**, started when the sink is installed, exports reports from a bounded queue (100 reports; overflow is dropped and counted locally). It uses the OTLP HTTP exporter (HTTP/protobuf) on a `LoggerProvider(shutdown_on_exit=False)` with a `SimpleLogRecordProcessor`, so nothing else registers exit handlers. The caller's thread only enqueues, so a request never waits on the collector. `flush(timeout)` joins the queue with that deadline, and the daemon thread never holds the process open. A slow or stalled collector therefore can't stretch exit beyond the drain deadline. Attributes: `error.code`, `exception.type`, `code.frames` (basename:function:line lines) and the tags. In `full`, the body is the message, plus `exception.message` and one `extra.<name>` attribute per extra. No stock `LoggingHandler` and no root-logger attachment. |

`REMEMBERSTACK_ERROR_DETAIL` = `metadata` (default) | `full` sets the
handler's detail for the built-in sinks. Any other value refuses to start.
It does not change container logs, which already carry full detail and are
documented as content-bearing.

## 5. Enforcement in CI

**Contract check** (a test parsing `src/rememberstack` with `ast`). Every
`except` handler that catches a failure class (analysis §5.1) passes only
in one of these ways:

- its last statement is `raise` and no `return`, `continue` or `break`
  can leave the handler before it (nested functions excepted);
- a top-level statement of its body calls a reporter (`report_failure`, or
  `_internal_error`, which calls it), or calls `logger.error` or
  `logger.exception` with an `extra` dict literal containing
  `"error_code"`;
- the `except` line carries `# error-contract: <reason>`, where the reason
  starts with `raised later`, `expected`, `exempt` or `mixed`.

The first three alternatives count only when no `return`, `continue` or
`break` (nested functions excepted) can leave the handler before the
satisfying statement. Otherwise, as in any handler with a conditional
`raise`, the handler needs the comment. `mixed` is for a handler whose
branches end differently: e1's single-chunk skip, or the fact
adjudicator's prompt fallback. Each `mixed` site has a unit test asserting
every branch.

"Logger" means a module-level name bound to `logging.getLogger(...)`. Calls
like `deadline.error()` on other objects are not logging calls. The analysis rows marked "raised later", "expected" and "exempt"
(§5.2, §5.3) carry the comment; expected rows also log at WARNING with
their code.

Separately, every `.error`, `.exception` and `.critical` call in
`src/rememberstack` passes `error_code` (analysis §5.4), and every literal
code matches Rule 3.

**Handler and sink tests** raise an error whose message, exception text
and extras carry a marker, plus an extra named `api_token`. They feed it
through `ErrorReportHandler` into a fake Sentry transport and a fake OTLP
exporter, and assert on the serialized payload:

- `metadata`: the marker never appears; code, type and frames do.
- `full`: the message, exception text and extras appear in both sinks;
  `api_token` is `[masked]`.
- Not configured: nothing is sent.
- A record without an exception is reported with its call-site frame.
- A raising sink doesn't break the other sink or the caller.
- An invalid detail value refuses to start.
- A failed worker item yields exactly one Sentry event with its code and
  worker tags.
- An HTTP 500, both in a synchronous and in an async route, yields exactly
  one coded event with the route's traceback.
- An uncaught exception in a thread yields one `process_unhandled_exception`
  event.
- A subprocess that reports and then exits (normally, through `os._exit`,
  and through an API lifespan failure) delivers its event to the fake
  transport.
- Populated global, isolation and current Sentry scopes (tags, contexts,
  user, breadcrumbs) add nothing to the final payload.
- With the real OTLP exporter and a loopback collector that stalls or
  dribbles its response, a request that reports returns without waiting.
  A subprocess that reports and exits finishes within the drain deadline
  plus 0.5 seconds, measured on the real subprocess exit.
- A sandbox budget expiry (`57014` and `25P04` from the request's budget)
  is not reported; an unrelated cancellation, `42501` and `42P01` are.
- The manifest check fails readiness when an allowlisted function is
  missing.
- Through `wrap_asgi_reporting` on a real server: an outer request failure,
  an inner route failure and a shutdown failure each give exactly one
  event.
- An API whose cost-export listener fails to bind delivers
  `api_lifespan_failed` before the process exits.
- A nested `extras` mapping with an inner `api_token` arrives masked in both
  sinks.

The existing wire responses (HTTP 503/500, the MCP tool error) are
asserted unchanged.

## 6. Fixes in scope

Analysis §5 is the work list:

- §5.2: 25 sites gain a report, 13 existing ERROR logs gain a code, 7
  expected outcomes log at WARNING with a code, 6 sandbox catches follow
  the sandbox rule, and 38 ("raised later" and exempt) get their
  `# error-contract:` comment;
- §5.3: 14 handlers that raise on only some branches;
- §5.4: all 18 ERROR calls gain codes;
- §5.5: three boundaries outside `except`;
- §5.6: failures returned as values that review found;
- the boundaries in §2.1 not covered above: lifespan, sweep continuation,
  sandbox operational rejections and internal errors, and meter credential
  rejection;
- the storage-capture failure code comes only from a declared refusal
  `code` attribute, else `storage_capture_failed`. Arbitrary exception text
  can no longer become a code;
- `run_storage_capture_batch` takes an optional `on_scope_failure(code)`
  callback, called the moment a scope fails, before the batch waits for
  its peers. An embedder that must hand failures across a process boundary
  streams them from there.

The list is what the scan and review found at design time. A site found
later, during implementation or afterwards, follows the same rules in the
same PR and is added to the analysis. It does not reopen this design.

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
