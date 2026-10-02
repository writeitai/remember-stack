# TypeScript Remember client

**Status:** owner-selected direction; binding upon approved PR merge. No npm publication claim.
**Date:** 2026-10-02. **Decision:** D141.
**Analysis:** [full Python inventory and alternatives](../analysis/typescript_client_parity.md).

## 1. Problem, decision and ownership

JavaScript applications need the same memory client as Python applications
without a Python subprocess. A generated HTTP client alone cannot supply the
configuration, project routing, response validation, file ingestion and
readiness behavior of `remember.Client`.

Publish one client library named `@rememberdev/client`, sourced from
`writeitai/remember-stack`, under `packages/typescript-client`. The package
provides `MemoryClient`, `Client`, `RememberClient` (an alias), `AccountApi`,
`resolveConnection`, the Python client's exported model types and typed errors.
The issuer owns its account API; this repository owns memory HTTP and client
behavior. The public provider contract contains only issuer metadata,
ResolvedProject and an object-shaped whoami response, never a private account
API schema. Neither SDK owns memory truth.

CLI and MCP are separate library/distribution boundaries, intended coordinates
`@rememberdev/cli` and `@rememberdev/mcp`; they depend on this client, not vice
versa. The client package contains no `bin`, MCP host/transport, interactive
login, token minting or credential-writing workflow. Its open-query dispatcher
is a client method and can be called by an MCP library without requiring one.
Building those executable packages is a separate deliverable; client completion
does not require them. Connector implementations are also independent packages; the SDK retains
the Python connector HTTP methods.

## 2. Full client parity contract

The normative inventory is [typescript_client_parity.json](typescript_client_parity.json),
extracted from Python source at `2cde3baf`.
It includes constructors, exact arguments/defaults, public methods, root exports,
resolve_connection and QueryResultDict properties. The published 0.17.2 tag is
`dd0c78015099cdd84ab5f3501744d6e225125989`; it is historical baseline evidence,
not the complete target. The analysis table is explanatory, not normative.
Implementation checks regenerate the inventory from same-revision Python and
fail when an entry lacks an implemented mapping or documented adaptation.

An injected `client` is a caller-owned HttpClient request adapter: it supplies
its own base URL/headers, skips SDK connection/routing, and cannot be combined
with apiKey/baseUrl/project/transport/agents settings. A `transport` injection
implements a distinct HttpTransport interface requiring absolute URLs and SDK-provided
headers, retaining SDK connection/routing. HttpClient requests require relative paths;
HttpTransport requests require absolute URLs so accidental injection mix-ups fail type checking. It replaces Python's lower-level
transport option; callers must provide a conforming single-send implementation in both modes,
not a Fetch-standard implementation. Optional
`agents: {http?, https?}` accepts caller-owned Node HTTP/HTTPS agents and reuses
the SDK's single-transmission adapter for proxy/custom-CA configuration; it is
mutually exclusive with client/transport injection. Missing agents are created
and owned by the SDK. Constructor timeoutMs still bounds injected calls, while
the injected adapter owns any additional timeout and its lifecycle. Export
`version` as the npm package version and `pythonCompatibility` as source
SHA/baseline, not a false Python-version identity. Query results retain rows,
columns and truncated properties.

All public client methods are covered, including assured operations; SQL and
saved-query execution/discovery; primitive search, resolve, hydration,
transcripts and graph operations; deployment build information; pipeline
readiness and waiting; binary/path ingestion; document listing, metadata search
and deletion; all four connector methods; `fromEnv`; and `account.whoami/get`.
Connector methods work against a deployment composing that port and propagate
its refusal otherwise. They do not add `/connectors` to the standard exported
deployment profile. No method returns a fabricated success for an absent API.

Methods take a named options object, use camelCase argument names and return
Promises. JSON response fields preserve the API's snake_case names. Export all
client model types without copying engine/server dependencies. UUIDs are
strings; dates on the wire are ISO strings, while date options can also accept
Date objects. Arrays represent Python tuples. QueryResult has the same `rows`
property; Python's extra dictionary wrapper is unnecessary in JavaScript.
`close()` and async disposal release SDK-owned pending requests, not a
caller-owned injected transport. Every handwritten function has JSDoc/docstrings.

The package supports Node.js 22 and newer with import and require entry points
and declaration files. Local paths and stored login files are part of its
scope. Browser support is not advertised: privileged API keys belong on the
application backend. The default adapter uses Node HTTP/HTTPS requests with
per-client engine agents: one transmission per invocation, no automatic
redirects/retries, and full-response buffering under the operation deadline.
Buffering is time-bounded, not subject to an invented byte cap; Python also
buffers responses without such a cap. Default SDK agents never use environment
proxies, including NODE_USE_ENV_PROXY. Node 22.0 lacks the proxyEnv agent option;
explicit injection makes proxy selection deterministic across supported versions. For proxies supply caller-owned agents
(e.g. an http.Agent subclass configured by the operator) or a conforming request transport.
Node TLS defaults apply, including NODE_EXTRA_CA_CERTS; --use-system-ca applies
only on Node versions supporting that flag. A caller-owned HTTPS agent can
supply explicit ca material. HTTP_PROXY/HTTPS_PROXY/NO_PROXY and SSL_CERT_FILE
are not read by the SDK. Close destroys only SDK-owned engine agents, cancels
this client's waiters/requests as AbortError, refuses subsequent calls with AbortError and never
recreates agents. Caller-owned agents/transports remain open.

SDK-owned agents retire idle sockets after at most four seconds, a starting value to
measure, below the engine's five-second keep-alive. Active transfers remain
controlled by operation deadlines, not an idle-pool expiry. The 421 status means a router or load balancer says this connection cannot serve
the requested host. After a 421, retire
that socket before returning the buffered response so a later request cannot
reuse a connection which refused the destination. HTTPS follows the same
single-transmission and retirement rules. Shared discovery uses separate
process-owned agents (or borrowed caller-owned agents/transports); no single
client close destroys those resources, and idle discovery sockets do not keep
the process alive. Shared work has its own finite deadline.

## 3. Connection, routing and secret handling

[D136 §8](one_key_client_surfaces_design.md#8-client-connection-and-issuer-protocol)
is the shared issuer/credential/routing authority. This section makes its
TypeScript application explicit; §7 identifies every adaptation. D141 amends
D136 retry/error acceptance for both clients to prevent ambiguous writes.


Constructing a client performs no network I/O. Resolve each setting with the
Python precedence: explicit options, `REMEMBER_API_KEY/PROJECT/API_URL/ISSUER`,
then version-2 stored credentials. Include REMEMBER_MCP_URL/mcpUrl for
resolveConnection consumers; empty connection environment values are unset.
Connection and config-directory environment names, including REMEMBER_CONFIG_DIR
and XDG_CONFIG_HOME, are case-insensitive, matching Python settings.
Empty REMEMBER_CONFIG_DIR resolves to credentials.json in the current directory;
empty XDG_CONFIG_HOME resolves to remember/credentials.json there. These exact
paths let TypeScript read the file Python CLI writes in the same environment. Normalize
bare or Bearer-prefixed keys and refuse line breaks. No retired aliases.
Use `REMEMBER_CONFIG_DIR`, otherwise `$XDG_CONFIG_HOME/remember`, otherwise
`~/.config/remember`, and the same `credentials.json` shape as Python. Read
only when required by precedence; argument or environment key and URL bypass a stale file.
Refuse symlinks, non-regular files, and files readable by other users on POSIX;
on Windows refuse automatic stored-file reads, as current Python does; explicit
keys remain usable: explicit key plus URL bypasses the store, and an explicit or environment
signed key on Windows routes through its issuer without reading an unavailable
store when no URL is supplied. Apply the signed-key exception to Python too;
POSIX keeps ordinary independent-setting precedence. A future ACL-aware implementation requires a separate design. Read through
an opened handle with no-follow and nonblocking flags and inspect that handle to avoid a check/read
race. Malformed files fail with a useful error without including secret data.

Recognize compact JWS keys and the Python letters-only prefix form. Decode
claims only for routing, never authorization; the API verifies the signature.
Signed-shaped but malformed keys fail, never become localhost shared secrets.
Without a signed key or configured URL, use `http://127.0.0.1:8000`.

Fetch issuer OAuth metadata and require matching issuer identity. Endpoints
must share its origin, including account and project resolution endpoints.
Issuer and discovered deployment URLs require HTTPS, except literal loopback
or localhost HTTP; reject credentials in URLs. A project resolution must
identify a project covered by the key. Cache by issuer, key fingerprint and
project, for 600 seconds; concurrent first resolutions share one request.
Pin the resolved project ID on first use so a changed default cannot redirect
an existing client's traffic to another project. Refresh the deployment
mapping after cache expiry and apparent movement.

A file-loaded key may go only to its recorded self-hosted engine origin,
issuer, or issuer-resolved deployment. Explicit/environment keys follow the
caller's configured destination. No redirected request may leak a bearer to
another origin. Issuer/account requests permit at most three same-origin
redirects with their method preserved; engine requests refuse redirects.
Account paths are relative paths, not absolute URLs or traversal paths.
Errors, debug output and fixtures must not expose credentials or request bodies.

## 4. Transport, errors and validation

Additional safety adaptations are intentional: issuer-advertised authenticated
endpoints must stay on the issuer origin even though Python currently permits
a secure cross-origin endpoint, and an existing client refreshes its mapping
on cache expiry without changing its pinned project identity. These differences
have explicit conformance dispositions rather than being reported as exact
wire parity.

Use a per-client request transport: no global OpenAPI token/base mutation.
All request paths/methods and generated DTOs derive from the committed schema,
with a named, source-backed exception for optional connector routes. Generate types
with pinned `openapi-typescript-codegen` 0.30.0 after a checked OpenAPI 3.1 →
3.0 type-only projection: const becomes a single-value enum, null branches
become nullable unions, and contentMediaType binary strings become binary
format. Keep the original 3.1 schema for runtime validation. Generate named
request metadata directly from original paths/methods. Facade transport and
binary uploads are handwritten; generated fetch/core/services are not shipped.
Use option objects and union types. Generation is a development step, never a publish-time
network dependency. Custom routing/error behavior wraps generated request
metadata rather than copying the API's request/response definitions.

Successful responses are runtime-validated against exported JSON schemas, not
merely cast to TypeScript. Export supplementary Python-client model schemas
for contracts absent from the served OpenAPI (including connector descriptors
and SDK discovery models). Use the same Python source as the parity inventory.
Use JSON Schema draft 2020-12. Fill model defaults recursively, return all
defaulted output properties as present and type them non-optional. Inputs
accept schema-described JSON types, not Pydantic lax numeric-string coercions.
Cross-field checks cover ContextBundle child grains, temporal interval order,
evidence-span bounds, evidence returned<=total, document query/cursor exclusion,
UTC-only fields and nested connector secret keys. Shared adversarial fixtures
exercise these; JSON Schema generation alone is insufficient.

`MemoryApiError` includes HTTP status (0 for network failure), detail, code
and an optional response. `RateLimited` carries the server's Retry-After hint,
without retrying. Match the published query-error code/status map and refuse
malformed structured errors. Account/project/unavailable/stored-key failures
use their named error subclasses. `PipelineDeadLettered` holds stage identities
and the last validated report. ConnectorNotFoundError remains exported. Constructor/resolveConnection and
its redacting Connection type are public APIs; no secret-holding object has
an unredacted inspect/toJSON representation.

No generic automatic retry is permitted. A key-routed eligible read may re-resolve on
network failure, 421 or a non-engine 404, and retry once only if the URL changed.
Never replay ingest, connector creation, mutating operations or DELETE,
including on 421. Refresh the host mapping after a write network failure/421
without replay; surface the original error. For reads, use the source-owned
_READ_ROUTES table in `src/rememberstack/surfaces/route_scope.py` (not the
separate spend-gate table in http_api.py), not HTTP method alone: POST readiness, graph and queries
remain eligible. Timeout and caller abort never trigger retry or re-resolution.
The Fetch standard automatically repeats reusable requests after 421; manual
redirects cannot disable that behavior. The default therefore uses Node HTTP/HTTPS requests,
not global fetch. The SDK invokes an adapter at most once per write, and its default/supplied-agent
adapter transmits exactly once. Both injected client and transport modes must transmit once
per invocation, honor cancellation and return redirects without following them;
the SDK cannot detect violations inside a caller-owned transport. The retired
fetch option is removed rather than retained as a nonconforming fallback. One
`deleteDocument` sends one DELETE to the engine, including after a 421. The
universal no-write-replay contract applies at the wire, not merely to adapter
call counts. See
[the measured 421 transport evidence](../analysis/typescript_transport_421.md).
Engine error envelopes do not trigger moved-host refresh. A failed refresh
surfaces the original failure rather than silently selecting a fallback.

Provide a finite request timeout (30 seconds by default) and AbortSignal.
Cancellation covers issuer discovery, routing, requests and polling waits.
The HTTP timeout is a total operation deadline including routing, upload and
response buffering, unlike httpx's per-phase idle timeout. Raise timeoutMs for
large/slow uploads (a 50 MB upload at 5 Mbit/s exceeds the default 30 seconds).
Expiry after an upload may have arrived leaves an unknown write outcome; never
replay it automatically.
Readiness waits poll immediately, including after `created=false`, validate
every report, continue through retry-scheduled `failed` stages and stop on
`dead_letter`. Default wait timeout is 1800 seconds and interval 15 seconds;
these are overridable starting values. The deadline bounds network and sleep,
and timeout preserves the last report. `requireP3` matches Python's requirement
set. Input version IDs are required and validated before HTTP.

Caller cancellation produces an exported `AbortError` class, preserving the
signal's reason as its cause. SDK request deadlines produce `RequestTimeoutError`;
readiness deadlines produce the SDK's `TimeoutError` with the last report.
These are distinct classes, even when a caller's signal reason is a DOMException
named TimeoutError. None permits moved-host retry. Optional retryable/requestId
diagnostics are populated only by valid structured query errors; other routes
keep Python's ordinary HTTP status/detail behavior.

## 5. Uploads, queries and input contracts

Binary ingestion sends `application/octet-stream` to the ordinary ingest
endpoint with the filename, converter MIME and lineage query fields. File
ingestion infers converter MIME from the actual path before any display-name
override; bytes infer it from their required filename. Reuse the Python known
MIME mapping as generated data; unknown types use a deterministic portable
mapping from the fixed mime-db version, then `application/octet-stream` when
unknown, not a host-specific conversion promise.
Validate paired source kind/ref, UTC modified/effective times, ordered effective periods, snapshot-only periods,
and lineage requirements for revisions, version keys and living mode before sending. Never replay an upload to hide a
failed acknowledgement or progress uncertainty.

Keep Python's GET for unfiltered claims/chunks searches and POST for document-
filtered or time-scoped searches. A coordinated Python/TypeScript POST-only change is a
separate API-client change, not hidden language drift. Names, property/query-
space terms and ingest metadata also travel in query parameters in this API;
this client does not claim to keep all customer content out of request URLs. Preserve repeated query parameters where
the API requires them, omission/default behavior, and encoded path segments.
Validate saved-query identifier rules and open-query arguments against their
source-owned definitions, including unknown fields and boolean-vs-integer
distinctions. `callOpenQuery` covers all seven SDK dispatch names without
shipping an MCP server.

D140 added eight methods to the full client surface: `sectionHistory`,
`sectionHistoryRequest`, `documentReferences`, `documentReferencesRequest`,
`setReferences`, `referenceGenerations`, `setEffectivePeriods` and
`clearEffectiveTime`. Match Python request validation, default time scopes,
section-key encoding, NDJSON reference replacement, generation status and
error behavior. All new request/response models and support constants are part
of the same normative inventory. Symbolic default constants resolve against
`pythonSourceRevision`; fixture/default gates compare their actual values. Ingest carries version keys and effective
periods; claims/chunks/document searches and claims context carry time scopes.
No writes gain retry permission from these additions.

## 6. Drift detection and validation gates

Checks must answer different questions; one regenerate-and-diff is not enough.

1. **API versus exported schema.** Run the engine's offline exporter into a
   temporary path and compare with root `openapi.json`; its existing tests
   retain explicit served-profile scope. No running server or Postgres needed
   just to generate the schema. Optional connector schemas are identified
   separately and never imply standard-profile availability.
2. **Schema versus generated artifacts.** Export SDK-only schemas, MIME/error
   constants and public inventory from current Python source; compare committed
   artifacts, generate TypeScript into a temporary directory, then compare.
   All nullable/union constructs must survive generation and consumer typing
   tests. CI never writes branch source as its source of truth.
3. **Python surface versus facade.** An AST-derived manifest includes exported
   types/errors/functions and public methods, arguments/defaults and signatures.
   Every inventory entry has an implementation or a reviewed runtime-language
   adaptation. Unsupported new entries fail CI. Static names alone are not
   sufficient: a request/response matrix invokes every mapped method and checks
   defaults, optional arguments and destination against Python recordings.
4. **Behavior parity.** Run Python and TypeScript against shared deterministic
   fixtures/recording transports. Cover every method, malformed responses,
   query-error statuses, polling, input validation, lineage, file MIME,
   permission failures, routing expiry/movement, secret origins, redirects,
   ambiguous writes, cancellation and POSIX credential permissions. Include
   D140 time-scoped searches, section history default history/offsets, valid
   slash/colon key encoding and invalid-key refusals, reference NDJSON bytes
   with null omission/trailing LF/empty sets, and UTC effective-period validation.
   GET versus PUT on the same references path must have different replay
   eligibility. Count all three new writes on 421 and connect failures. Every
   adaptation in §7 has explicit tests; fixture comparison normalizes date
   instants, UUID case and query-parameter ordering, but preserves repeated
   values, JSON bodies/defaults and Boolean query encodings.
   Include local HTTP server tests of the default adapter and conforming injections;
   count received requests to detect automatic transport replays, not just mocked calls.
   Cover HTTP and HTTPS on the minimum Node version, 421 retirement with a request
   already queued on the same agent, verified by server connection identity rather
   than reusedSocket; idle retirement, including an active response over four seconds; caller-agent ownership and close during a shared discovery lookup.
5. **Issuer/provider compatibility.** Publish public JSON Schemas for the
   consumed issuer metadata fields, ResolvedProject and object-shaped whoami.
   `account.get` returns unknown JSON and whoami returns a JSON object, matching
   issuer-agnostic Python. No cloud-specific DTOs or private schemas are copied.
   Engine CI checks its source/generated provider contracts offline. The private
   cloud repo runs a one-way check on PRs/main/daily: its own exported response
   schemas and issuer metadata handler must satisfy the provider contract at
   both pinned release and engine main. Fetching those public artifacts needs
   no secrets; failures fail the check. Test required fields and compatible
   types, allowing provider additions. This has no two-repo snapshot deadlock.
6. **Distributed package.** Typecheck, build and install the packed tarball into
   clean ESM and CommonJS consumers on supported Node versions. Verify exported
   declarations, runtime dependencies, no missing files or Python requirement,
   no executables, and no source credentials. Run Linux/Windows checks for
   platform-sensitive file and configuration behavior.
7. **Released-engine compatibility.** Add the TypeScript client to the existing
   compatibility-matrix workflow against 0.15.0, 0.16.0 and candidate. Run all
   methods each deployment profile actually serves; absent/new features have
   explicit unsupported expectations rather than invented successes. Current
   candidate covers the full source inventory. No claim that older engines
   serve post-release document/deletion/readiness features is implied.


## 7. Language and safety adaptations

Anything absent from this table must match current Python behavior. Options
use camelCase; nested schema-owned input types retain snake_case fields. Nested
dates accept ISO strings, not Date; top-level Date options become UTC ISO.
Timeout API units are milliseconds: timeoutMs=30000, wait timeoutMs=1800000,
pollIntervalMs=15000. Fixtures normalize equivalent UTC encodings.

| Python behavior | TypeScript behavior and reason | Required test |
| --- | --- | --- |
| Synchronous methods/context manager; close leaves an injected client usable | Promises; close/async disposal. A closed SDK facade refuses later calls with AbortError even with injected client; borrowed resources remain open | Cleanup, borrowed ownership and calls after close |
| UUID/datetime/tuple wrappers | UUID/ISO strings and arrays; rows/columns/truncated remain properties | Typed result/default fixtures |
| General lookup/graph datetime options accept naive datetime objects despite the published date-time annotation | Require an ISO datetime with Z or numeric offset, raising InputValidationError before HTTP; preserve nonzero offsets. The SDK asserts the published RFC3339 format to avoid implicit local time. Current servers refuse naive and non-UTC instants with 422; offsets are forwarded so non-UTC values receive that server error as in Python | Naive local refusal for lookupRelations/graphNeighborhood/graphPath; unchanged +02:00 wire fixtures; candidate +02:00 gives 422 MemoryApiError and Z succeeds, while pre-v0.17.1 releases (matrix: v0.15/v0.16) accept offsets; original-format checks |
| Pydantic lax coercion | JSON Schema types, no numeric-string/bool coercion; fill defaults | Invalid types and complete outputs |
| Arbitrary-size integers | Refuse unsafe integer values before JSON.parse rounding or sending; NumericPrecisionError. Query parameters above MAX_SAFE_INTEGER require an explicit SQL string/cast | 2^53 boundary responses/parameters |
| Method-based replay and write replay on 421/connect errors | _READ_ROUTES-derived read eligibility; never write replay. Refresh for next call only | POST readiness and 421/network writes |
| Pin never refreshes merely on TTL expiry | Refresh pinned project mapping every 600 seconds; never changed default | Expiry and concurrency |
| Secure cross-origin metadata endpoints allowed | Issuer-origin authenticated endpoints required | Hostile metadata |
| Arbitrary account path strings | Relative paths without traversal/absolute origin | Encoded/double-encoded traversal |
| Wait deadline checked between polls, including one poll for zero timeout | Request/readiness timeouts and poll intervals must be finite, greater than zero and at most 2,147,483,647 ms. InputValidationError refuses invalid values before HTTP. A zero in-flight deadline cannot complete Python's single poll; pipelineReadiness is the one-poll API. Larger values overflow Node timers to 1 ms. Valid deadlines also bound calls/sleep; TimeoutError retains the last report | Zero/negative/nonfinite/upper-bound refusal, accepted maximum, slow HTTP and cancellation |
| MCP validate_arguments path_ingest/settings implicitly reads host files/environment | validateArguments uses an explicit injected PathBodyResolver; no resolver refuses path; separate MCP package owns settings and Python-equivalent root/regular-file/size checks | Resolver opt-in, disabled path, empty/oversize body; host security fixtures required before MCP release |
| TypeScript-only cancellation/precision errors | mapError reports AbortError as cancelled (no HTTP status, not retryable), NumericPrecisionError as local_backend_error (not retryable), reusing published codes | Structured error fields and no automatic retry |
| Unknown MIME uses host database | Known Python map plus fixed mime-db version; octet-stream for unknown | Known/unknown/name override |
| Windows file mode rejects stored credentials | Refuse automatic stored files. Argument or environment key plus URL bypasses the file; argument or environment signed keys without a URL route via their issuer without file reads, in Python too. Unsigned keys without a URL refuse an existing file; absent file retains localhost default | Windows argument/environment keys, present/absent file, signed/unsigned cases |
| HTTP_PROXY/HTTPS_PROXY/NO_PROXY and SSL_CERT_FILE supported by httpx | SDK-owned agents never use environment proxies, including NODE_USE_ENV_PROXY; Node TLS defaults/NODE_EXTRA_CA_CERTS apply; supplied agents or a conforming request transport handle custom proxy/CA | Agent ownership, HTTPS/custom-CA and documented settings |
| httpx per-phase idle timeout; constructor timeout ignored with injected client | timeoutMs bounds every operation including injected calls, routing/upload/body reads; raise it for large/slow uploads | Injected deadline, slow continuous-response deadline and distinct readiness timeout |

Use process-wide issuer/project caches keyed by issuer, SHA-256 key fingerprint
and project; a client pins project identity separately. Completed successful results may be reused across transports. In-flight issuer
and project resolutions additionally key by transport identity: clients using
different proxies/adapters do not inherit each other's pending failures. Default
SDK discovery has one process-owned transport; borrowed agent pairs share a
discovery transport only when the identical HTTP/HTTPS agent objects are used;
a custom transport shares pending work only by object identity. Shared
resolutions have their own bounded timeout. Each waiter may abort or close
without cancelling shared work or poisoning other clients' result. Close-ended
operations are cancellation, never movement/network errors; they neither
refresh nor clear shared results. SDK code never logs keys, bodies or
URLs containing metadata. Connection secrets are private and inspect/toJSON
redact them; deliberate authorization access is explicit.

### Exception mapping

| Python exception | TypeScript exception/fields | Hierarchy |
| --- | --- | --- |
| MemoryApiError | MemoryApiError: statusCode, detail, code, response, optional retryable/requestId | Error |
| RateLimited | RateLimited: retryAfter (seconds) | MemoryApiError |
| ProjectResolutionError, StoredKeyRefused, AccountApiUnavailable, IssuerError | Same named classes | MemoryApiError |
| PipelineDeadLettered | PipelineDeadLettered: deadLettered, report | Error, outside MemoryApiError |
| ConnectorNotFoundError | ConnectorNotFoundError | Error, outside MemoryApiError |
| ValueError, Pydantic ValidationError, SandboxRejection invalid_parameter | InputValidationError, code=invalid_parameter | TypeError |
| CredentialError | CredentialError | InputValidationError |
| TimeoutError from wait | TimeoutError: report (last report or undefined) | Error |
| Caller cancellation | AbortError: cause is signal reason | Error |
| HTTP request deadline | RequestTimeoutError: statusCode=0 | MemoryApiError |
| Unsafe JSON integer | NumericPrecisionError on response; InputValidationError code=numeric.precision on input | Respectively MemoryApiError and TypeError |

Compare failure class/code/status/fields, not Python dictionary repr messages.
Query failures accept the exact public code/message shape at its bound status,
or that shape plus correctly typed retryable/request_id diagnostic fields.
Malformed fields, unknown codes and mismatched statuses fail closed. Diagnostics
are carried, never interpreted as automatic-retry permission.

### Support exports for executable libraries

Public client exports include redacting Connection/resolveConnection,
StoredCredentials type and credential path/read helpers, issuer metadata/secure
URL helpers, and the complete D136 memory-tool catalogue generated from
`remember.mcp_tools`: all 18 current definitions, permissions, versions, routes,
annotations and input schemas, plus render/lookup, pure argument validators and
error shapes/mapping. A future catalogue addition fails drift checks until
covered. The seven open-query names are only the SDK dispatch subset; an MCP
host can filter every catalogue tool by its memory:read/memory:write permission.
The normative inventory's supportExports records exact source signatures,
Connection return/fields, credential/issuer read helpers, and all 34 current
`remember.mcp_tools.__all__` names with explicit TypeScript names or a separate
MCP-library disposition. Backend protocols, host settings and the five
host-execution handlers live in that separate MCP library; the base client
supplies their complete catalogue/validation/error dependencies, not host I/O.
`validateArguments({name, arguments, pathResolver?, maxBodyBytes?})` returns a
Promise of parsed arguments. It has no `pathIngest`/`settings` parameters and
never loads REMEMBERSTACK_MCP settings. Text/base64 bodies are resolved and
validated inside the client. An ingest path requires a caller-injected
`PathBodyResolver({path, filename?, mime?, maxBodyBytes?})` returning
`Promise<{content: Uint8Array, filename: string, mime: string}>`; without one,
refuse with ToolArgumentError code invalid_arguments, matching Python's
path_ingest=False unknown-path refusal. Base validation checks the
returned body's non-emptiness and capability limit. This opt-in resolver is
the explicit exception to pure validation, not SDK-owned filesystem I/O.
The separate MCP library owns one resolver implementing Python `_memory.py`
`_resolve_path_body`: configured roots only, resolved-path/symlink containment,
regular file, pre-read size and bounded read, served cap or local resource
cap, and actual-path MIME inference before display-name override. Its separate
binding design and security parity fixtures are release gates; hosts reuse
that resolver and catalogue instead of redefining tools or path guards.
Ordinary Client.ingestFile keeps its own caller-directed local-file contract.
The MCP release contract must inject a path resolver only for stdio when tools
are rendered with pathIngest=true; HTTP hosts never inject one. Catalogue
rendering and validation must agree on that mode, with parity fixtures.
mapError maps RequestTimeoutError to transport_error (statusCode=0, retryable
hint), exactly like Python MemoryApiError(status_code=0). Cancellation uses
the existing cancelled code to describe the caller stopping work, without an
HTTP status or retry hint; unsafe response precision uses local_backend_error.

No MCP host or interactive login is pulled into the base library.
The CLI library owns device login/logout, credential writing/locking and the
pending-revocation journal. It consumes exported schemas/security read helpers
rather than reaching into client internals. MCP depends only on public client
and catalogue APIs. Their full designs and reviews are mandatory build-order
gates, recorded separately; this PR does not implement those libraries.

## 8. Alternatives, operational consequences and release

One active package avoids engine/cloud selection mistakes and lets both use
the same facade. Handwritten DTOs, a generated-only API, a separate SDK repo,
and bundling CLI/MCP/connectors are rejected for the reasons in the analysis.
Node-only support makes the full local-file/credential contract honest. The
chosen generator is unmaintained; locked dependencies, deterministic generation
and union/nullable tests are the accepted cost, not permission to substitute
the FE generator silently.

There is no new hosted component, server dependency or memory correctness
implementation. The package adds lightweight runtime schema validation and
developer/CI build tooling. A failed request fails visibly; a caller can decide
whether to retry. It never switches tenant or treats stored keys as portable.
Schema drift is corrected in a PR with source revisions and tests; it does not
trigger automatic deployment, registry publication or API changes.

Keep npm version independent from Python, with explicit Python baseline and
schema hashes in release metadata. Releases are immutable. Before publication
verify the actual organization ownership, package contents, registry publisher
identity, reviewed source commit and all drift/parity gates. Publication uses
npm trusted publishing or operator-managed credentials; no secret values in
git. Merge/build can finish before publisher configuration exists, but docs
must label local/package installation honestly until registry availability is
verified. This work requests PR merges, not an unreviewed npm upload.
