# Error reporting contract: analysis

Status: analysis (non-binding). Decision: [D158](../../decisions.md). Binding
design: [error reporting contract](../designs/error_reporting_contract_design.md).
Date: 2026-11-23. Code references are main `2d7855bc` (v0.21.1).

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

## 2. What exists today

- **Python logging.** Modules log on `logging.getLogger(__name__)` under
  `rememberstack.*`. There are 18 `.error`/`.exception` calls and no stable
  codes: the message is the only identifier.
- **Worker telemetry seam.** `TelemetryPort` (`ports/telemetry.py`, D61)
  exports one structured event per worker run, and the exception object on
  failure. The default sink writes JSON lines to stdout.
- **Sentry-protocol adapter** (`adapters/sentry.py`). Opt-in with
  `REMEMBERSTACK_SENTRY_DSN`; works with Sentry, GlitchTip and Bugsink.
  Metadata only: the message becomes `[redacted]`, and locals, breadcrumbs,
  bodies and extras are removed. Events arrive by two paths:
  - worker exceptions through `TelemetryPort`, tagged with `stage`, `lane`
    and `processing_id`;
  - every other ERROR record through the SDK's logging integration.

  To avoid duplicates, `before_send` drops every record from
  `rememberstack.workers.base`. That also drops failures that never take
  the `TelemetryPort` path, such as the terminal-failure finalizer at
  `workers/base.py:671`.
- **HTTP API.** `ProviderCallError` becomes a quiet 503
  (`surfaces/http_api.py:793`). An exception no handler maps is logged only
  by the server (`uvicorn.error`).
- **MCP.** The in-process tool server logs unexpected failures through
  `_internal_error` (`surfaces/mcp.py:568`) without a code. The
  `remember mcp` dispatcher (`src/remember/mcp_engine.py`) is part of the
  client CLI: it runs on the user's machine and calls the HTTP API, which
  reports server-side failures itself.
- **Docs.** [`self-hosting/observability`](../../website/src/app/docs/self-hosting/observability/page.mdx)
  documents container logs, the Sentry variables and the cost export.
- **The `observability` extra**, which the release image installs, holds
  `sentry-sdk`, `opentelemetry-sdk` and
  `opentelemetry-exporter-otlp-proto-http`.

## 3. Options for the hook

| Option | What a self-hoster does | Covers | Engine cost |
|---|---|---|---|
| A. **Python logging with coded records**, plus one engine-owned handler that builds an allowlisted report and hands it to sinks | Set a Sentry DSN or an OTLP endpoint; embedders pass any callable | Sentry, GlitchTip, Bugsink; every OTLP backend (Grafana, Datadog, Honeycomb, New Relic, Elastic, PostHog Logs); any tool an embedder can call | One handler, two small sinks, codes at the audited sites |
| B. Stock vendor handlers on the root logger (Sentry `LoggingIntegration`, OTel `LoggingHandler`) | Same variables | Same | Smallest code, but a stock handler copies extras, file paths, messages and formatted tracebacks, so the metadata rule can't hold. The root logger also admits dependency and exporter logs (OTel 1.44 `LoggingHandler`, [source](https://github.com/open-telemetry/opentelemetry-python/blob/v1.44.0/opentelemetry-sdk/src/opentelemetry/sdk/_logs/_internal/__init__.py#L539-L563)) |
| C. Extend `TelemetryPort` to every failure | Implement a port adapter | Embedders only | Every failure site needs the port injected; the API, MCP and profile code have none |
| D. Vendor SDKs (PostHog, Datadog, …) | Pick one | One vendor each | Violates requirement 1 and grows forever |

**Choice: A.** Python logging is the one interface every tool already reads.
One engine-owned handler converts a coded record into an allowlisted
**error report**:

- code, logger name, exception type, stack frames (basename, function,
  line) and a few allowlisted tags;
- the message and extras only in `full` detail.

The sinks never see the raw record, so the metadata rule holds no matter
which sink is used. It also gives Sentry a single path, which removes the
dropped-duplicate problem.

Sources, retrieved 2026-11-23:

- Sentry's default integrations capture ERROR log records and unhandled
  exceptions in web frameworks and threads, each as its own event. The
  engine initializes the SDK with `default_integrations=False` and sends
  only its own events.
  [Sentry Python logging](https://docs.sentry.io/platforms/python/integrations/logging/);
  pinned SDK 2.66.1 default `event_level=ERROR`
  ([source](https://github.com/getsentry/sentry-python/blob/2.66.1/sentry_sdk/integrations/logging.py#L22-L23)).
- OpenTelemetry's standard exporter settings (`OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`,
  `_HEADERS`) are read by the OTLP HTTP exporter itself. The engine
  constructs that exporter, so the transport is HTTP/protobuf whatever
  `OTEL_EXPORTER_OTLP_PROTOCOL` says.
  [OTLP exporter specification](https://opentelemetry.io/docs/specs/otel/protocol/exporter/),
  [OTel Python zero-code](https://opentelemetry.io/docs/zero-code/python/).
- PostHog accepts OTLP logs at `/i/v1/logs` with the project token as
  bearer. That feeds PostHog **Logs**. PostHog **Error Tracking** issues
  need exception events (`$exception`), which an embedder sends with the
  PostHog SDK.
  [PostHog Python logs](https://posthog.com/docs/logs/installation/python),
  [Error Tracking](https://posthog.com/docs/error-tracking/issues-and-exceptions).

B fails the metadata rule. C reaches workers only. D is ruled out by the
owner.

## 4. Content switch

`REMEMBERSTACK_ERROR_DETAIL` = `metadata` (default) | `full` governs what the
handler puts in a report, for every sink:

- **`metadata`** keeps code, logger, exception type, frames and allowlisted
  tags. The message becomes `[redacted]`. There are no locals, source
  lines, breadcrumbs, request data or extras.
- **`full`** adds the formatted message, the exception's text and the
  record's extras, with values of secret-named keys masked. No local
  variables: the report carries what the code chose to log, which is enough
  for debugging and keeps both sinks identical.

Container logs are not affected; they are documented as content-bearing.
Embedders pass their own detail to the same handler.

Alternatives: a per-field allowlist (nobody needs it), or always-metadata
(blocks debugging, which the owner wants to allow).

## 5. Audit: where failures are hidden today

### 5.1 Scope

`ast` finds **190** `except` handlers in `src/rememberstack` whose own body
doesn't raise. Most catch input-shaped exceptions as normal control flow:
`ValueError`, `KeyError`, `TypeError`, `JSONDecodeError`, not-found and
refusal types. The contract governs handlers that catch a **failure class**:

- a bare `except`, `Exception` or `BaseException`;
- provider errors (`ProviderCallError` and its named subclasses,
  `ProviderAccountingError`);
- database errors (`DBAPIError`, `SQLAlchemyError`,
  `SQLAlchemyTimeoutError`, and any `psycopg.*` error);
- I/O errors (`OSError`, `TimeoutError`, `httpx.HTTPError`,
  `subprocess.TimeoutExpired`);
- `RuntimeError`, and engine errors named `*Unavailable` or
  `*UnavailableError`.

The work list has four parts:

- **89** failure-class handlers without a `raise` in their body (§5.2);
- **13** failure-class handlers that raise only on some branch and consume
  the failure on others (§5.3);
- all **18** existing `.error`, `.exception` and `.critical` calls, which
  gain codes (§5.4);
- **3** boundaries outside `except` blocks (§5.5).

Failures returned as values outside `except` blocks can't be found
mechanically. Reviews cover them, and the known one, storage capture's
per-scope result, is fixed here.

### 5.2 Failure-class handlers without a raise

| Disposition | Sites |
|---|---|
| Report (new ERROR + code) | 27 |
| Already ERROR; add code | 13 |
| Expected outcome; WARNING + code | 7 |
| Raised later | 13 |
| Exempt | 29 |
| **Total** | **89** |

#### Report (new ERROR + code)

| Site | Catches | Disposition |
|---|---|---|
| `adapters/vertex.py:561` | `ProviderAccountingError` | `provider_usage_unparsed`: usage reported but unreadable, so cost goes unrecorded |
| `profiles/storage_capture_batch.py:240` | `Exception` | per-scope ERROR with the declared refusal code (§4.4) |
| `profiles/storage_capture_batch.py:266` | `Exception` | per-scope ERROR with the declared refusal code (§4.4) |
| `profiles/storage_capture_batch.py:268` | `Exception` | per-scope ERROR with the declared refusal code (§4.4) |
| `surfaces/http_api.py:3142` | `SpendLeaseUnavailable` | `spend_lease_unavailable` (503) |
| `surfaces/http_api.py:3161` | `SpendLeaseRefused, SpendLeaseUnavailable` | `spend_lease_commit_failed`: commit after a 2xx; the response is unchanged |
| `surfaces/query_engine.py:493` | `DBAPIError, SQLAlchemyTimeoutError, TimeoutError` | timeouts: WARNING `query_budget_exceeded`; other database errors: ERROR `query_database_failed` |
| `surfaces/query_engine.py:1805` | `DBAPIError, SQLAlchemyTimeoutError, TimeoutError` | as above |
| `surfaces/query_engine.py:1834` | `DBAPIError, SQLAlchemyTimeoutError, TimeoutError` | as above |
| `surfaces/query_engine.py:1886` | `DBAPIError, SQLAlchemyTimeoutError, GraphBusyError, GraphHydrationError, TimeoutError` | as above |
| `surfaces/query_engine.py:1937` | `DBAPIError, SQLAlchemyTimeoutError, TimeoutError` | as above |
| `surfaces/query_sandbox/executor.py:917` | `psycopg.errors.ConfigurationLimitExceeded, psycopg.errors.DiskFull, psycopg.errors.OutOfMemory, psycopg.errors.TooManyConnections` | `ConfigurationLimitExceeded` stays the caller's disclosed cap; `DiskFull`, `OutOfMemory`, `TooManyConnections`: ERROR `query_database_resource_exhausted` |
| `surfaces/query_sandbox/executor.py:933` | `psycopg.OperationalError` | `query_database_unavailable`; same `pg_unavailable` result |
| `workers/base.py:485` | `Exception` | retry scheduled: WARNING `worker_item_retry`; no retry left (dead letter): ERROR `worker_item_failed` |
| `workers/e0.py:1701` | `ProviderCallError` | `structure_fallback` |
| `workers/e0.py:1715` | `Exception` | `structure_fallback` |
| `workers/e0.py:1777` | `ProviderInvalidResponseError, ValueError` | `skeleton_check_failed` |
| `workers/e0.py:1787` | `ProviderCallError` | `skeleton_check_failed` |
| `workers/e0.py:1791` | `Exception` | `skeleton_check_failed` |
| `workers/e0.py:1867` | `ProviderCallError` | `section_roles_failed` |
| `workers/e0.py:1875` | `Exception` | `section_roles_failed` |
| `workers/e0_summary.py:634` | `ProviderCallError` | `summary_failed` |
| `workers/e0_summary.py:644` | `Exception` | `summary_failed` |
| `workers/e0_summary.py:666` | `ProviderCallError` | `summary_failed` |
| `workers/e0_summary.py:680` | `Exception` | `summary_failed` |
| `workers/reconcile.py:751` | `Exception` | `profile_refresh_failed` |
| `workers/sync.py:98` | `Exception` | `sync_item_failed` |

#### Already ERROR; add code

| Site | Catches | Disposition |
|---|---|---|
| `adapters/managed/perimeter_trust.py:340` | `Exception` | `perimeter_trust_refresh_failed` |
| `profiles/selfhost.py:1152` | `Exception` | `section_index_backfill_failed` |
| `spine/new_work.py:81` | `Exception` | `new_work_announce_failed` |
| `spine/section_index_backfill.py:110` | `Exception` | `section_index_blocks_unreadable` |
| `spine/surface_cost.py:196` | `Exception` | `surface_cost_record_failed` |
| `surfaces/mcp.py:494` | `Exception` | `mcp_tool_failed` via `_internal_error` |
| `surfaces/mcp.py:510` | `Exception` | `mcp_tool_failed` via `_internal_error` |
| `surfaces/mcp.py:554` | `Exception` | `mcp_tool_failed` via `_internal_error` |
| `workers/base.py:257` | `Exception` | `worker_lease_renewal_failed` |
| `workers/base.py:451` | `NonRetryableHandlerError, ProviderAccountingError` | `worker_item_failed` (terminal) |
| `workers/base.py:671` | `Exception` | `worker_finalizer_failed` |
| `workers/base.py:692` | `Exception` | `provider_usage_unrecorded` |
| `workers/e3.py:484` | `ProviderInvalidResponseError` | `e3_claim_normalize_failed` (logged once, at `:492`) |

#### Expected outcome; WARNING + code

| Site | Catches | Disposition |
|---|---|---|
| `adapters/managed/perimeter_trust.py:315` | `Exception` | `perimeter_refresh_failed`; keeps the last good key set |
| `adapters/managed/perimeter_trust.py:328` | `Exception` | `perimeter_refresh_failed`; keeps the last accepted document |
| `spine/managed_metering.py:171` | `MeterReceiptUnavailable` | `meter_receipt_retry`; the receipt is retried. The sibling `MeterReceiptUnauthorized` branch at `:164` is ERROR `meter_receipt_unauthorized` (design §2.1) |
| `spine/managed_metering.py:423` | `MeterReceiptConflict, MeterReceiptUnavailable` | `MeterReceiptUnauthorized` (a `MeterReceiptUnavailable` subclass): ERROR `meter_receipt_unauthorized`; other `MeterReceiptUnavailable`: WARNING `meter_receipt_retry`; `MeterReceiptConflict`: ERROR `meter_receipt_conflict`. Replay continues with capped backoff in both cases |
| `surfaces/query_engine.py:1798` | `P1SearchUnavailableError` | `search_projection_unavailable`; disclosed boundary in the response |
| `surfaces/query_engine.py:3952` | `P1SearchUnavailableError` | `search_projection_unavailable`; the additive profile channel is skipped and fact search answers alone (no boundary in the response) |
| `surfaces/query_engine.py:4576` | `P1SearchUnavailableError` | `search_projection_unavailable`; disclosed boundary |

#### Raised later

| Site | Catches | Disposition |
|---|---|---|
| `adapters/codex_writer.py:75` | `subprocess.TimeoutExpired` | returned as a timed-out session; the knowledge driver records it and raises |
| `adapters/converters/image_ocr_description.py:465` | `Exception` | lane error kept in the outcome and raised by `_lane_failure` |
| `adapters/converters/image_ocr_description.py:485` | `MistralOcrProviderError, ProviderCallError` | same lane outcome, raised by `_lane_failure` |
| `adapters/converters/image_ocr_description.py:549` | `httpx.HTTPError` | same lane outcome, raised by `_lane_failure` |
| `adapters/converters/image_ocr_description.py:611` | `ProviderAccountingError` | same lane outcome, raised by `_lane_failure` |
| `adapters/converters/image_ocr_description.py:618` | `ConversionError, ProviderCallError` | same lane outcome, raised by `_lane_failure` |
| `adapters/converters/pdf.py:376` | `OSError` | stored on the writer and raised by its caller |
| `adapters/converters/time_limit.py:31` | `BaseException` | re-raised in the calling thread |
| `adapters/openrouter.py:422` | `Exception` | parse fallback; `_capture_invalid_completion` raises |
| `adapters/openrouter.py:1489` | `httpx.HTTPError, OpenRouterProviderError, ProviderAccountingError` | retry loop; raises the last error when exhausted |
| `adapters/selfhost/queue.py:210` | `BaseException` | re-raised by `run_forever` |
| `workers/knowledge_driver.py:482` | `Exception` | collected and raised (single error or `ExceptionGroup`) |
| `workers/knowledge_driver.py:510` | `Exception` | collected and raised with the compile errors |

#### Exempt

| Site | Catches | Disposition |
|---|---|---|
| `adapters/codex_writer.py:153` | `OSError, UnicodeError, ValueError` | missing declared output is rejected by the compiler, which records it in the ledger |
| `adapters/fsit_storage_capture_transport.py:271` | `ProviderScopeUnavailable` | draining a scope the provider already closed; the capture result carries the failure |
| `adapters/generation_recorder.py:262` | `Exception` | reporting path: generation recorder |
| `adapters/generation_recorder.py:269` | `Exception` | reporting path: generation recorder |
| `adapters/managed/composite_auth.py:50` | `Exception` | an adapter declining a credential is the expected path |
| `adapters/openrouter.py:535` | `Exception` | reporting path: generation record |
| `adapters/openrouter.py:618` | `OSError, TypeError, ValueError` | temporary-file cleanup |
| `adapters/openrouter.py:622` | `OSError` | temporary-file cleanup |
| `adapters/openrouter.py:826` | `OpenRouterProviderError` | optional provider-name enrichment; usage is still recorded |
| `adapters/postgres_p1.py:1907` | `P1SearchUnavailableError` | availability probe returns False |
| `adapters/selfhost/object_store.py:258` | `FileNotFoundError, OSError` | idempotent delete |
| `adapters/selfhost/telemetry.py:67` | `Exception` | reporting path: telemetry sink |
| `adapters/selfhost/telemetry.py:78` | `Exception` | reporting path: telemetry sink |
| `adapters/sentry.py:113` | `Exception` | reporting path: Sentry sink |
| `adapters/vertex.py:460` | `Exception` | reporting path: generation record |
| `adapters/vertex.py:589` | `httpx.HTTPError` | best-effort error preview; the call already failed |
| `core/content_detection.py:401` | `OSError, ValueError, zipfile.BadZipFile, RuntimeError` | input sniffing fallback |
| `spine/corpus_publication.py:292` | `DBAPIError` | cleanup: invalidates a broken connection |
| `spine/corpus_publication.py:303` | `DBAPIError` | cleanup: invalidates a broken connection |
| `spine/readiness.py:682` | `SQLAlchemyError` | readiness reports the reason to its caller |
| `spine/readiness.py:690` | `SQLAlchemyError` | cleanup: `RESET ROLE` |
| `surfaces/http_api.py:3168` | `SpendLeaseRefused, SpendLeaseUnavailable` | lease release after a non-2xx; the hold expires on its TTL (D42) |
| `surfaces/mcp.py:550` | `LayerUnavailableError` | a layer the deployment does not serve, returned as `layer_unavailable` (D143) |
| `surfaces/query_sandbox/executor.py:872` | `psycopg.errors.QueryCanceled, psycopg.errors.TransactionTimeout` | the caller's statement hit its disclosed timeout (`statement_timeout` result) |
| `surfaces/query_sandbox/executor.py:884` | `psycopg.errors.LockNotAvailable` | the caller's statement hit its disclosed lock timeout |
| `surfaces/query_sandbox/executor.py:895` | `psycopg.errors.InvalidParameterValue` | the caller's parameter was invalid |
| `surfaces/query_sandbox/executor.py:944` | `psycopg.Error` | SQLSTATE class `XX` or `58`: ERROR `query_database_internal_error`; any other class is the caller's SQL (exempt). Same `execution_error` result in both cases (design §2.1) |
| `workers/e0_summary.py:694` | `Exception` | cache corruption is a cache miss |
| `workers/e0_summary.py:724` | `Exception` | cache corruption is a cache miss |

### 5.3 Handlers that raise on only some branches

This includes handlers whose last statement is `raise` but which can
`return`, `continue` or `break` first (one, at `spine/fact_adjudication.py:501`).

| Site | Consumed branch | Disposition |
|---|---|---|
| `adapters/openrouter.py:1466` | Inline accounting failed with a generation id | Raised later: the usage poll raises when exhausted |
| `adapters/systemone.py:42` | Primary decision provider unavailable without usage | Expected: WARNING `systemone_backup_used`; the backup provider answers |
| `core/file_card.py:174`, `:229` | Unreadable image or archive bytes | Exempt: input classification |
| `surfaces/query_engine.py:1012`, `:1366`, `:2118`, `:2542`, `:2592`, `:2667`, `:2783` | The request deadline expired | Expected: WARNING `query_budget_exceeded`; a disclosed boundary in the response |
| `surfaces/query_sandbox/executor.py:118` | Type-name lookup failed within budget | Exempt: falls back to the type oid in a column label |
| `workers/e1.py:470` | A single chunk fails embedding (not an outage) and is skipped as poison | Report: ERROR `embedding_chunk_skipped` (`mixed`) |
| `spine/fact_adjudication.py:501` | Ends in `raise`, but with `fallback_to_prompt` set it returns the prompt adjudication first | Expected: WARNING `fact_adjudication_prompt_fallback` on the fallback branch (`mixed`) |

### 5.4 Existing ERROR calls

All 18 get an `error_code`:

| Site | Code |
|---|---|
| `adapters/managed/perimeter_trust.py:341` | `perimeter_trust_refresh_failed` |
| `adapters/managed/perimeter_trust.py:374` | `revocation_rollback_rejected` |
| `adapters/managed/perimeter_trust.py:399` | `revocation_document_stale` |
| `profiles/selfhost.py:1138` | `document_binding_rebuild_failed` |
| `profiles/selfhost.py:1153` | `section_index_backfill_failed` |
| `spine/managed_metering.py:157` | `meter_measurement_quarantined` |
| `spine/new_work.py:82` | `new_work_announce_failed` |
| `spine/section_index_backfill.py:111` | `section_index_blocks_unreadable` |
| `spine/surface_cost.py:162`, `:167`, `:197` | `surface_cost_deployment_mismatch`, `surface_cost_scope_missing`, `surface_cost_record_failed` |
| `surfaces/mcp.py:568` | `mcp_tool_failed` |
| `workers/base.py:258`, `:452`, `:487`, `:672`, `:697` | per the boundary table in the design (§2.1) |
| `workers/e3.py:492` | `e3_claim_normalize_failed`; the same code as its enclosing handler at `:484` (§5.2) |

### 5.5 Boundaries outside `except`

| Site | Today | Fix |
|---|---|---|
| `surfaces/http_api.py:793` `ProviderCallError` handler | Quiet 503 | ERROR `provider_call_failed`, same 503 |
| Unmapped exception in a route | Logged by `uvicorn.error` only | Catch-all handler: ERROR `api_unhandled_exception`, same 500 |
| `profiles/storage_capture_batch.py:142` `_failure_code` | Any code-shaped message, else the type name (`TimeoutError`) | Only an engine refusal's declared `code`, else `storage_capture_failed` |

### 5.6 Failures returned as values

Found by review; no scan finds these:

| Site | Today | Fix |
|---|---|---|
| `workers/e0_summary.py:619` (and the same result handling in its siblings) | A summary that passes the schema but is blank is metered and becomes `None` | ERROR `summary_failed`, same outcome |
| `profiles/storage_capture_batch.py` per-scope result | `None` with no report (v0.21.0) | Per-scope ERROR with the declared code (design §2.1) |

## 6. Cost of getting it wrong

A swallowed failure costs days: nightly storage capture failed for four
nights before anyone could name the reason, because the per-scope result was
`None`. Over-reporting costs noise. Keeping expected outcomes (retries,
disclosed query boundaries, parks, cache misses, client errors) at WARNING
keeps the tracker to real failures.
