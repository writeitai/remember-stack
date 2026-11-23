# Error reporting contract: analysis

Status: analysis (non-binding). Decision: [D158](../../decisions.md). Binding
design: [error reporting contract](../designs/error_reporting_contract_design.md).
Date: 2026-11-23.

## 1. The question

How should the engine expose its failures so that any operator can send them
to the error tracker they already use, without the engine shipping code for
each vendor? And which current code paths hide failures today?

Owner requirements (2026-11-23):

1. The OSS engine contains no PostHog code. Self-hosters must be able to
   connect Sentry, PostHog or other common tools.
2. Use the simplest generic hook that lets common tools plug in, document it
   in the engine docs, and let remember.dev's runtime plug PostHog into the
   same hook.
3. Failures come out as exceptions or ERROR logs with a stable code. They
   are never swallowed or turned into bare return values.
4. Content restriction is controlled by an environment variable. The default
   is metadata only; setting it enables full detail for testing and
   debugging.

## 2. What exists today (main `2d7855bc`, v0.21.1)

- **Python logging.** 37 modules use `logging.getLogger(__name__)` under the
  `rememberstack.*` namespace. There are 25 `.error`/`.exception` calls and 36
  warnings. No log record carries a stable code; the message text is the
  only identifier.
- **Worker telemetry seam.** `TelemetryPort` (`ports/telemetry.py`, D61)
  exports one structured event per worker run and the real exception object
  on failure. The default sink writes JSON lines to stdout.
- **Sentry-protocol adapter** (`adapters/sentry.py`). Opt-in with
  `REMEMBERSTACK_SENTRY_DSN` for Sentry, GlitchTip or Bugsink. Metadata only:
  the message becomes `[redacted]`, and locals, breadcrumbs, request bodies
  and extras are removed. Worker exceptions are captured through
  `TelemetryPort` with `stage`, `lane` and `processing_id` tags. The SDK's
  default logging integration also turns other ERROR records into events;
  the adapter drops its duplicate of the worker record.
- **Docs.** [`self-hosting/observability`](../../website/src/app/docs/self-hosting/observability/page.mdx)
  documents container logs, the Sentry variables and the cost export.
- **`observability` extra** (installed in the release image): `sentry-sdk`,
  `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http`.

So the pieces exist. Missing are a stable code on every reported failure,
a path for tools that don't speak the Sentry protocol, a content switch, and
reporting for the sites in §5 that swallow failures.

## 3. Options for the hook

| Option | What a self-hoster does | Covers | Engine cost |
|---|---|---|---|
| A. **Python logging records** (ERROR + `exc_info` + `error_code` field) as the one contract, with built-in sinks | Set a DSN (Sentry protocol) or an OTLP endpoint; an embedding app attaches any `logging.Handler` | Sentry, GlitchTip, Bugsink; every OTLP backend (Grafana, Datadog, Honeycomb, New Relic, Elastic, PostHog Logs); anything with a Python logging handler | Small: codes at ~20 sites, one OTLP handler, one switch |
| B. Extend `TelemetryPort` to all failures | Implement a port adapter in Python | Only embedders | Every failure site needs the port injected; API, MCP and profile code have no port today |
| C. OpenTelemetry-native (spans/events) | Run an OTel collector | OTLP backends | Larger: tracing model, context propagation; the engine doesn't trace today |
| D. Vendor SDKs (PostHog, Datadog, …) | Pick one | One vendor each | Violates requirement 1; grows forever |

**Choice: A.** Python logging is the one interface every error tool already
reads:

- Sentry's logging integration turns ERROR records into error events by
  default (`event_level`, default ERROR). Source: [Sentry Python logging
  integration](https://docs.sentry.io/platforms/python/integrations/logging/),
  retrieved 2026-11-23.
- OpenTelemetry's Python SDK exports `logging` records as OTLP log records,
  and its zero-code agent configures that handler automatically. Sources:
  [OTel Python zero-code](https://opentelemetry.io/docs/zero-code/python/)
  and [logs example](https://opentelemetry.io/docs/zero-code/python/logs-example/),
  retrieved 2026-11-23.
- PostHog accepts OpenTelemetry logs over OTLP at `/i/v1/logs`, authorized
  with the project token. Its Python SDK's `capture_exception` is how an
  embedding application turns a record into an error-tracking event.
  Sources: [PostHog Python logs](https://posthog.com/docs/logs/installation/python),
  [PostHog error tracking capture](https://posthog.com/docs/error-tracking/capture),
  retrieved 2026-11-23.

B fits workers but not the API, MCP or profile code, and every embedder
would need engine-specific glue. C is the right tool for traces, which
nobody asked for. D is ruled out by the owner.

### Built-in sinks under A

1. **Sentry protocol.** It already exists; keep it and add `error_code` as a
   tag.
2. **OTLP logs.** The SDK and exporter are already in the image. When the
   standard variable `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` (or
   `OTEL_EXPORTER_OTLP_ENDPOINT`) is set, the engine attaches an OTLP
   `LoggingHandler` for ERROR records. It reuses the standard OTel variables
   for endpoint, headers and protocol and adds no `REMEMBERSTACK_*` ones.
   Running under the zero-code agent would need `opentelemetry-distro` and an
   entrypoint change. Attaching the handler in-process is ~20 lines and
   works with the existing image.
3. **Embedders.** Any program that imports the engine (remember.dev's
   runtime) attaches its own `logging.Handler` to the `rememberstack`
   logger. That needs no engine code beyond the contract.

## 4. Content switch

The two built-in sinks run in the operator's own environment, but an error
tracker is usually a third-party SaaS. Today the Sentry adapter is always
metadata only, and container logs always carry full detail. One switch,
`REMEMBERSTACK_ERROR_DETAIL` = `metadata` (default) | `full`, governs both
built-in sinks:

- **`metadata`** sends the exception type, `error_code`, logger name, the
  allowlisted tags, and stack frames (file basename, function, line) without
  locals or source lines. The message becomes `[redacted]`.
- **`full`** sends the message, the record's extra fields and, for Sentry,
  local variables. Use it for testing and debugging.

It does not touch container logs, which are documented as
content-bearing. Embedders apply their own policy; remember.dev uses its own
switch with the same two values.

Alternatives: a per-field allowlist (more flexible, nobody needs it), or
always-metadata (blocks debugging, which the owner wants to allow).

## 5. Audit: where failures are hidden today

Method: every `except` handler in `src/rememberstack` that does not
re-raise, classified by reading the code (`git grep`, main `2d7855bc`). 45
handlers don't re-raise: 9 already log at ERROR, 12 log below ERROR, and 24
log nothing. Each one is below.

### 5.1 Must report (ERROR with a code), outcome unchanged

| Site | Today | Code to add |
|---|---|---|
| `profiles/storage_capture_batch.py:240,266,268` | Per-scope failure code returned; no log (the case that hid nightly capture failures) | one ERROR per failed scope, `error_code` = the returned failure code |
| `surfaces/mcp.py:494,510,554` | Returns a generic internal error to the MCP client; nothing logged | `mcp_tool_failed` |
| `workers/e0.py:1715` | Structuring failure falls back to a plain skeleton silently | `structure_fallback` |
| `workers/e0.py:1791` | Skeleton-check provider error recorded as an outcome only | `skeleton_check_failed` |
| `workers/e0.py:1875` | Section-role classification failure: `pass` | `section_roles_failed` |
| `workers/e0_summary.py:644,680` | Summary call failure: `pass` | `summary_failed` |
| `workers/sync.py:98` | Per-item sync failure counted only | `sync_item_failed` |
| `workers/reconcile.py:751` | Profile refresh after delete fails: WARNING | `profile_refresh_failed` |

### 5.2 Correct as is (not swallowed)

| Site | Why |
|---|---|
| `adapters/converters/time_limit.py:31`, `adapters/selfhost/queue.py:210` | Collected and re-raised in the calling thread |
| `workers/knowledge_driver.py:441,482,510`, `workers/knowledge_writer.py:247` | Recorded in the compilation ledger, then re-raised (single error or `ExceptionGroup`) |
| `adapters/converters/image_ocr_description.py:465` | Lane error kept in the outcome and raised by `_lane_failure` |
| `adapters/openrouter.py:422`, `adapters/typesafe.py:107` | A parsing fallback or retry loop that raises when exhausted |
| `surfaces/http_api.py:2880` | Turns an `HTTPException` into its HTTP response (a client error) |
| `workers/e0_summary.py:694,724` | Cache corruption is a cache miss by design |
| `adapters/managed/perimeter_trust.py:315,328` | Keeps the last good key set (WARNING); `_log_if_not_fresh` logs ERROR once data is stale |

### 5.3 Exempt, with a reason in code

| Site | Reason |
|---|---|
| `adapters/sentry.py:113`, `adapters/selfhost/telemetry.py:67,78`, `adapters/generation_recorder.py:262,269`, `adapters/openrouter.py:535`, `adapters/vertex.py:460` | Failure of the reporting path itself. Reporting it at ERROR to the same sink would loop or flood; it stays a WARNING |
| `adapters/managed/composite_auth.py:50` | An adapter declining a credential is the expected path (DEBUG) |
| `adapters/converters/office.py:312` | Malformed document metadata becomes a conversion warning shown to the user |

Bare-value failures outside `except` blocks aren't found by this scan. The
CI check in the design (§5 there) covers new code; the implementation PR
re-runs the scan.

## 6. Cost of getting it wrong

A swallowed failure costs days: nightly storage capture failed for four
nights before anyone could name the reason, because the per-scope result was
`None`. Over-reporting costs noise. The ERROR-only rule and the exemption
list keep expected outcomes (retries, parks, cache misses, client errors)
out of the tracker.
