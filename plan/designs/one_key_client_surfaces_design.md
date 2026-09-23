# One key, one tool catalogue — engine-side client surfaces (Design)

**Status:** accepted design (D136). **Date:** 2026-09-23.
**Decision:** [D136](../../decisions.md#d136-one-signed-key-one-shared-mcp-tool-catalogue-and-a-bridging-remember-mcp).
**Analysis:** [one_key_client_surfaces_analysis.md](../analysis/one_key_client_surfaces_analysis.md).
**Build order:** [plan/plans/one_key_client_surfaces.md](../plans/one_key_client_surfaces.md).
**Companion (cloud side):** the remember.dev "one key, one MCP endpoint" design
in `writeitai/ultimate-memory-cloud` (branch `design/one-key-one-mcp`). It owns
organisations, members, billing, account tools, key minting and consent, the
OAuth authorization server, and the hosted endpoint `https://remember.dev/mcp`.
Nothing here depends on it existing.

**Amends:** D92 and D108 (credentials, login, `remember setup`, environment),
[unified_remember_distribution_design.md](unified_remember_distribution_design.md)
§§2–4, [request_path_metering_and_cost_export_design.md](request_path_metering_and_cost_export_design.md)
§6, [packaging_distribution_design.md](packaging_distribution_design.md) §2 and
§8 item 5, [unified_documentation_and_query_space_design.md](unified_documentation_and_query_space_design.md)
§2.1 and §3.5. Those documents now point here for these topics.

---

## 1. What this design gives a reader

One user-visible credential — a **signed key** — works with every client
surface. This design specifies what the open-source engine and the `remember`
package provide so that works, for remember.dev and equally for any other
operator who runs a conforming key issuer:

1. **One tool catalogue** (`remember.mcp_tools`): the memory tools' names,
   descriptions, input schemas, argument validation and error envelopes,
   defined once and imported by every MCP host (§3).
2. **The `project` routing argument**, defined once, resolved by hosts, never
   seen by the engine (§4).
3. **`remember mcp`**: engine mode over stdio or Streamable HTTP, and bridge
   mode that relays stdio to a remote MCP endpoint with a key (§5).
4. **`remember setup`** choosing between a remote entry, a stdio bridge and a
   self-hosted entry (§6).
5. **The engine perimeter's signed-key contract**: issuer, project coverage,
   permissions, revocation, and direct-path admission limits (§7).
6. **SDK and CLI with one key**: host resolution from the key, `remember
   login` storing one key, and one environment precedence for both (§8).

### 1.1 Terms

- **MCP (Model Context Protocol)** — the JSON-RPC protocol agents use to list
  and call tools. An **MCP host** here means any server that renders memory
  tools to agents: `remember mcp`, the engine's in-process MCP server, or
  remember.dev's hosted server.
- **stdio transport** — the agent launches the server as a child process and
  exchanges newline-delimited JSON-RPC messages on its standard input/output.
- **Streamable HTTP transport** — the agent reaches the server at a URL; each
  JSON-RPC message is an HTTP `POST`, and the server may answer with JSON or a
  server-sent-event stream. A session is identified by the `Mcp-Session-Id`
  header.
- **Signed key** — a JSON Web Token (JWT): a compact string of three
  base64url parts (header, claims, signature). The signature is made with the
  issuer's private key and checked with its public keys, published as a
  **JWKS** (JSON Web Key Set, a list of public keys each named by a `kid`).
- **Issuer** — the service that mints keys (remember.dev's account service, or
  any operator's equivalent). Identified by an HTTPS URL, the `iss` claim.
- **Coverage** — which deployments a key may be used at: the `projects`
  claim, either an explicit list of project identifiers or the
  organisation-wide marker `"org:*"` together with the `org` claim.
- **Issuer tenant** — the issuer's own grouping of deployments (for
  remember.dev, an organisation). The engine knows only one opaque tenant id,
  configured by the operator; it has no model of what a tenant is.
- **Deployment** — one running engine (one trust domain, D50). A **target**
  is one deployment a host can route to.

## 2. Invariants

1. The engine serves one deployment and never learns about organisations,
   members, billing or other deployments (D50, D60, CLAUDE.md Rule 3).
2. Every agent-facing tool definition exists once, in `remember.mcp_tools`.
   A host may add tools of its own (remember.dev's account tools); it never
   redefines a memory tool.
3. The engine perimeter is the only authority on what a credential may do at
   a deployment. Hosts may refuse earlier; they never grant more.
4. A key is never written into a project-local file (anything under the
   working tree a harness reads), never logged, and never printed after
   login. A key from the credential file is sent only to its issuer, to the
   issuer-advertised MCP endpoint, to the file's recorded engine URL, or to a
   data-plane URL the issuer resolved for it (§8.2); any other destination
   requires a key the caller supplied explicitly.
5. The SDK and the CLI resolve their settings with one shared function and
   one precedence: explicit argument, then environment, then the stored
   credential file.

## 3. The tool catalogue: `remember.mcp_tools`

### 3.1 Contents

`remember.mcp_tools` is a public, documented, semantically versioned module
of the `remember` package. It carries no engine imports and performs no I/O
at import time, so any Python MCP host can import it with the base install.

It defines one `ToolDefinition` per memory tool:

| Field | Meaning |
| --- | --- |
| `name` | The MCP tool name. |
| `description` | The text agents read, verbatim. |
| `input_schema` | JSON Schema of the arguments (`additionalProperties: false`). |
| `family` | `write` (`ingest`, `delete_document`), `readiness` (`pipeline_readiness`), `assured` (the four operations), `source` (`source_open`), `query` (the seven SQL tools). |
| `permission` | `memory:read` or `memory:write` — what a caller needs (§7.4). |
| `mutates` | Whether the tool changes memory. |
| `tool_version` | Integer, raised on any incompatible change to the tool (§3.4). |
| `http_route` | The engine route the tool calls (method and path template). |

The catalogue contains exactly the memory tools the binding designs expose
over MCP: `ingest`, `pipeline_readiness`, `delete_document`,
`resolve_entity`, `facts_context`, `claims_and_sources_context`,
`combined_context`, `source_open`, `query_sql`, `explain_sql`,
`describe_query_space`, `search_query_space`, `list_saved_queries`,
`describe_saved_query`, `run_saved_query`. (`delete_document` is D135's tool,
PR #456; `source_open` is D115's, [media_design.md §4a](media_design.md), and
returns MCP content blocks — text, image or audio — rather than one JSON text
block.) A new MCP-exposed memory tool is added here, never in a host. Saved queries, including the
`examples.*` set, stay reachable only through `run_saved_query` (D83/D87); the
catalogue never mints them as top-level tools.

The module also exports, as the single implementation every host uses:

- `CATALOG_VERSION` — `"<major>.<minor>"`, and `catalog_digest()` — SHA-256 of
  the canonical JSON of all definitions, used in tests and diagnostics.
- `memory_tools()` and `tool(name)`.
- `render_tools_list(tools, *, project: ProjectArgument | None, path_ingest: bool, read_only: bool)`
  — the MCP `tools/list` entries. `path_ingest` controls whether `ingest`
  offers the local `path` body source (only a host that can read the caller's
  filesystem sets it: the local `remember mcp`, never a remote host);
  `read_only` omits tools whose `permission` is `memory:write`; `project`
  adds the routing argument of §4.
- `validate_arguments(name, arguments)` — the parsers now in
  `mcp_memory_tools.py` and `query_sandbox/mcp_tools.py` (including the
  path-ingest root allowlist and resource guard, and base64 decoding).
- `map_error(...)` and `error_result(...)` — the structured tool error
  envelope (`{"error": {"status_code", "code", "detail"}}`) for HTTP and
  transport failures, so every host reports failures identically.
- MCP tool annotations on every rendered tool: `readOnlyHint: true` for
  `memory:read` tools and `false` for `memory:write` tools, and
  `destructiveHint: true` for `delete_document`. Hosts must not alter them;
  the bridge's read-only mode relies on them (§5.3).
- `ToolBackend` — the protocol a host implements to execute a validated call
  (in process, over the engine HTTP API via `MemoryClient`, or by forwarding).

`remember.mcp_memory_tools`, `remember.query_sandbox.mcp_tools` and the
`rememberstack.surfaces.mcp_memory_tools` / `surfaces.remote_mcp` re-export
shims are folded into this module; there is one import path.

### 3.2 Who imports it

- The engine's in-process MCP server (`rememberstack/surfaces/mcp.py`).
- `remember mcp` in engine mode (§5.2).
- The engine's assured-operation registry, for the agent-facing fields of the
  four operations (§3.3).
- remember.dev's hosted server, which pins a `remember` release and renders
  the catalogue with `project` set and `path_ingest=False`, beside its own
  account tools.

### 3.3 How `GET /operations` relates to the catalogue

`GET /operations` keeps serving full `ToolDescriptor`s for the four assured
operations, because the SDK and `remember operations list|run` use its
engine-only fields: result schema, result contract, output grain, answer
intent and implementation-plan hash. Its `name`, `description`,
`input_schema`, `mutates` and `version` are **generated from the catalogue**
by the registry — the same values, not a parallel definition. A test asserts
equality (§10). No MCP host renders tools from `GET /operations`.

### 3.4 Versioning and compatibility

The client package and the engine are released together but installed
separately, so a host's catalogue and a deployment can differ.

- A tool's `tool_version` rises when an existing call could fail or change
  meaning: a removed or renamed argument, a newly required argument, a
  narrowed type or bound, or a changed result shape. Adding an optional
  argument or editing description wording does not raise it; it changes
  `catalog_digest()` and `CATALOG_VERSION`'s minor number.
- Each deployment reports what it serves in `GET /deployment` (readable with
  `memory:read`; PR #455 adds the route to the read table), in a new `tools` object:
  `{"catalog_version": "1.4", "tools": {"facts_context": {"version": 3, "accepts_from": 2}, …}}`.
  `accepts_from` is the oldest `tool_version` whose calls the deployment still
  executes correctly.
- A host renders a catalogue tool for a deployment only when the deployment
  lists it and `accepts_from ≤ host tool_version ≤ version`. Otherwise the
  tool is omitted and `remember doctor` names it with the fix ("upgrade
  `remember`" or "the deployment predates this tool").
- A multi-deployment host (§4) renders the tools of its own catalogue without
  knowing the project in advance; when a call's target deployment does not
  serve the tool at a compatible version it returns the tool error
  `tool_unavailable_on_target` naming the tool, the target and the versions.

This replaces the three current gates (static write tools, `GET /operations`
404, the `GET /query/space` identity probe) with one advertised set. Whether
the open query facade is composed is reported the same way: the seven query
tools appear in `tools` only when it is.

## 4. The `project` routing argument

### 4.1 Definition

The catalogue defines one optional argument that hosts add to every memory
tool when they serve more than one target:

```json
"project": {
  "type": "string",
  "minLength": 1,
  "maxLength": 200,
  "description": "Which project's memory to use, by id or name. Omit it to use the default project."
}
```

`render_tools_list(project=ProjectArgument(names=…))` adds it; when the host
knows a closed set of target names (a local multi-target `remember mcp`), it
also emits an `enum` so the agent sees the choices. Hosts that serve one
target do not render it.

### 4.2 Who resolves it

The argument is **host-resolved routing**, not part of any memory operation:

1. The host reads `project`, resolves it to one target (its own rule: a
   configured name, or — for remember.dev — the key's project set and default
   project), and **removes it** from the arguments.
2. The host validates the remaining arguments with
   `validate_arguments` and executes the call against that target.
3. The engine's HTTP API and its in-process MCP server never accept
   `project`; unknown arguments stay a validation error. The engine therefore
   remains a single-deployment system, and routing can never change what an
   engine call means.

### 4.3 Behaviour per host

| Host | Renders `project` | A call with `project` |
| --- | --- | --- |
| Engine in-process MCP server | no | refused: `invalid_arguments` (unknown argument) |
| `remember mcp`, engine mode, one target | no | refused: `project_routing_unavailable`, "this server serves one deployment; configure targets to route by project" |
| `remember mcp`, engine mode, several targets (§5.2.2) | yes, with `enum` of target names | routed; an unknown name → `unknown_project` listing the valid names; omitted → the configured default target |
| `remember mcp`, bridge mode | whatever the remote renders | forwarded unchanged; the remote host resolves it |
| remember.dev hosted server | yes | resolved by the cloud design |

A call is refused rather than silently answered from the default target,
because an answer from the wrong memory looks correct to the agent.

## 5. `remember mcp`

### 5.1 Modes

`remember mcp` runs in exactly one of two modes, chosen at start:

1. **Bridge mode** when a remote MCP URL is configured: `--remote-url URL`,
   else `REMEMBER_MCP_URL`, else the `remember_mcp_endpoint` of the resolved
   key's issuer when the key is a signed key and no engine URL was given
   explicitly (§8.2).
2. **Engine mode** otherwise: it serves the catalogue against one or more
   engines' HTTP APIs, with connection resolution from §8.2.

An explicit engine URL (`--api-url` or `REMEMBER_API_URL`) always selects
engine mode, which is how a caller keeps memory content on a direct path to
the deployment. Giving both an explicit engine URL and a remote MCP URL is a
start-up error.

### 5.2 Engine mode

#### 5.2.1 Transports

- **stdio** (default). As today, rebuilt on `remember.mcp_tools`.
- **Streamable HTTP**: `remember mcp --transport http [--bind 127.0.0.1:8765]`,
  endpoint path `/mcp`.
  - `POST /mcp` accepts one JSON-RPC message; responses are
    `application/json`. The server has no server-initiated messages, so
    `GET /mcp` returns `405`. `DELETE /mcp` ends a session.
  - `initialize` returns an `Mcp-Session-Id`; later requests must carry it;
    an unknown or expired session gets `404`, which tells the client to
    initialize again. Sessions hold no memory state, only protocol state.
  - Requests whose `Origin` header is present and not in `--allow-origin`
    are refused with `403` (defence against DNS rebinding, where a web page
    reaches a loopback server through a hostname it controls).
  - **Authorisation is the engine's.** The listener forwards the caller's
    `Authorization` header to the engine unchanged and holds no credential of
    its own, so the engine perimeter decides every call and records the real
    caller (analysis §3.7). A call without a bearer is forwarded without one.
  - It binds to loopback by default. A non-loopback `--bind` requires
    `--allow-remote`, and start-up then probes the engine without a
    credential: if the engine answers a read, the listener refuses to start
    unless `--allow-unauthenticated-engine` is also given. TLS is terminated
    by the operator's proxy; the listener does not implement it.
  - Per-request `tools/list` is not filtered by the caller's permissions (the
    listener does not know them); a call the engine refuses returns
    `insufficient_permission`.
- `--read-only` keeps its meaning in both transports: write-permission tools
  are omitted and refused locally.

#### 5.2.2 Several self-hosted targets

A self-hoster with several engines can declare named targets in the
credential directory's `targets.toml`:

```toml
default = "work"

[targets.work]
api_url = "https://memory.work.example"
key_env = "REMEMBER_KEY_WORK"      # the key is read from this variable

[targets.home]
api_url = "http://127.0.0.1:8000"
key_env = "REMEMBER_KEY_HOME"
```

Keys are referenced by environment variable name, never stored in this file.
With two or more targets, engine mode renders `project` with the target names
and routes each call (§4.3). With none, it serves the single connection from
§8.2.

### 5.3 Bridge mode

Bridge mode relays MCP between the agent's stdio and a remote Streamable
HTTP endpoint. It is generic: any HTTPS MCP URL and any bearer key.

- **Configuration.** URL: `--remote-url`, `REMEMBER_MCP_URL`, or the issuer
  metadata's `remember_mcp_endpoint` (§8.1). Key: `--api-key`,
  `REMEMBER_API_KEY`, or the credential file (§8.2). Both are required; a
  missing key is a start-up error that says how to get one (`remember login`).
- **Stored-key binding.** A key taken from the credential file is attached
  only when the remote URL's origin equals the origin of the
  `remember_mcp_endpoint` advertised in that key's issuer metadata (fetched
  from the stored `issuer`, over HTTPS, same-origin redirects only). For any
  other remote URL — including one set by a changed `REMEMBER_MCP_URL` — the
  bridge refuses to start unless a key was supplied explicitly
  (`--api-key` or `REMEMBER_API_KEY`), and says why. A long-lived
  multi-project key therefore cannot be redirected to another host by an
  environment change alone.
- **Relay.** Each JSON-RPC message from stdin is `POST`ed to the URL with
  `Authorization: Bearer <key>`, `Accept: application/json, text/event-stream`
  and the session header once issued. JSON responses and event-stream
  messages are written to stdout in order. Notifications are forwarded; the
  bridge answers nothing itself except as below.
- **Tool list.** Passed through unchanged, including tools the bridge does not
  know (remember.dev's account tools). One exception, **path ingest**: when
  `REMEMBERSTACK_MCP_INGEST_ROOTS` is configured locally and the remote's
  `ingest` schema equals the catalogue's remote rendering at a compatible
  version, the bridge substitutes the local rendering (which adds `path`).
  A call using `path` is then read locally under the same root allowlist and
  resource guard as engine mode, and forwarded as `content_base64` with the
  file name and guessed `mime`. Otherwise `path` is not offered.
- **Session loss.** A `404` on a session re-runs `initialize` with the
  agent's original parameters once, then retries the message once.
- **Failures.** `401` → JSON-RPC error "the key was rejected (expired,
  revoked, or not valid for this endpoint); run `remember login`". `403` →
  the server's message. Network failures and `5xx` → a JSON-RPC error for
  that request; the bridge keeps running.
- **Safety.** The URL must be `https`, or `http` on a loopback address.
  Redirects are followed only to the same origin; a cross-origin redirect is
  an error, because following it would hand the key to another host. The key
  never appears in logs or error text.
- **`--read-only` fails closed.** In bridge mode it passes through only
  remote tools annotated `readOnlyHint: true`; every other remote tool —
  annotated `false`, unannotated, or unknown to the catalogue (including
  account tools) — is omitted from `tools/list` and a call to it is refused
  locally without being forwarded. A catalogue tool whose remote annotation
  disagrees with the catalogue's `permission` is also refused.
- `--transport http` is refused in bridge mode (an HTTP client should connect
  to the remote URL directly).

## 6. `remember setup`

`remember setup` writes each detected harness's MCP entry. It chooses among
three entry shapes:

| Situation | Entry written |
| --- | --- |
| Hosted key (signed key whose issuer publishes a `remember_mcp_endpoint`), harness supports remote MCP with OAuth sign-in, interactive use | **Remote entry, OAuth**: the endpoint URL only. The harness signs in through the browser; no secret is written anywhere. |
| Hosted key, headless use (`--headless`, or CI detected), harness supports a remote entry whose bearer header can reference an environment variable | **Remote entry, key header**: the URL plus `Authorization: Bearer ${REMEMBER_API_KEY}` in the harness's variable-reference syntax. The literal key is never written. |
| Hosted key, harness lacks remote support or cannot reference a variable in a header | **Stdio bridge**: `<launcher> mcp` with `REMEMBER_MCP_URL=<endpoint>` in the entry's environment; the key is read at run time from `REMEMBER_API_KEY` or the credential file. |
| Self-hosted engine (`--self-hosted`, or an explicit `--api-url`) | **Stdio engine mode**: `<launcher> mcp` with `REMEMBER_API_URL`; or, with `--mcp-url URL` naming a self-hoster's own `remember mcp --transport http` listener, a remote entry to it. |

Rules:

- The capability of each harness to take remote entries is determined by a
  per-harness probe in the configurator (config format version, CLI flag
  support). The table above is the selection rule; the probe decides which
  row applies, and uncertainty falls to the stdio bridge, which works
  everywhere.
- Launcher resolution (absolute `remember` or `uvx` path) is unchanged from
  [unified_remember_distribution_design.md §4.3](unified_remember_distribution_design.md).
- `--cloud` means "use the issuer": with no stored key it runs `remember login`
  first. `--issuer URL` selects a non-default issuer.
- `--dry-run` prints the entries with the key replaced by `***`.

## 7. Engine perimeter: the signed-key contract

This is the claim contract of the D61 auth-perimeter port's signed-credential
adapter (`src/rememberstack/adapters/managed/signed_token_auth.py`). It is
operator-generic: any issuer that produces this shape works. The shared-secret
adapter (`HashedBearerAuth`) is unchanged, and the composite
(`composite_auth.py`) still tries both.

### 7.1 Configuration

| Setting (`REMEMBERSTACK_SELFHOST_…`) | Meaning |
| --- | --- |
| `API_KEY_ISSUER` | Required when signed keys are enabled. The exact `iss` value accepted. |
| `API_SIGNING_KEYS` / `API_SIGNING_KEYS_URL` | The JWKS, inline or fetched from a URL (one of the two). Ed25519 public keys only, as today. |
| `API_REVOCATION_DOCUMENT` / `API_REVOCATION_URL` | The signed revocation document (§7.5), inline (operator push) or fetched from a URL. The plain id list `API_REVOKED_CREDENTIAL_IDS` is removed: an unsigned list carries no sequence or audience binding. |
| `API_KEY_PROJECT_ID` | The identifier keys and revocation documents use for this deployment. Default: the deployment id. |
| `API_KEY_TENANT_ID` | The issuer tenant this deployment belongs to (for remember.dev, the organisation id). Unset → organisation-wide keys are refused. |
| `API_ADMISSION_*` | Direct-path admission limits (§7.6). |
| `API_KEY_PREFIXES` | Routing prefixes that may precede the JWS, e.g. `rmb_`. Empty = bare JWS only. |
| `API_KEY_REFRESH_S` | Refresh interval for fetched JWKS and revocation (starting value 60). |
| `API_REVOCATION_MAX_STALENESS_S` | Age, measured from the document's `iat`, after which the accepted revocation document is too old (starting value 300). |

Numbers are starting points to be measured, not constants.

### 7.2 Accepted token

- **Form**: optionally one prefix from `API_KEY_PREFIXES` (stripped exactly
  once), then a compact JWS. Header `alg` is `EdDSA`, `kid` names a key in the
  set; the key is selected by `kid`, never by trying keys.
- **Required claims**: `iss` (equals `API_KEY_ISSUER`), `sub`, `jti`
  (non-empty; the revocation handle), `projects`, `iat`, `nbf`, `exp`,
  `permissions` (array of strings), `kind` (§7.3). `org` is required when
  `projects` is `"org:*"`.
- **Coverage.** The key is accepted at this deployment when either
  - `projects` is an array of strings and one element equals
    `API_KEY_PROJECT_ID` (exact string equality), or
  - `projects` is exactly the string `"org:*"`, `API_KEY_TENANT_ID` is
    configured, and the key's `org` equals it exactly.
  Anything else — no match, an empty list, another string, `"org:*"` without
  `org` or on a deployment with no tenant configured — is refused. There are
  no other wildcards or patterns. The organisation-wide form lets a key cover
  projects created after it was minted; the engine compares one opaque tenant
  string and learns nothing else about organisations.
- The registered `aud` claim is not used to decide coverage for keys.
- **Unknown claims** (default project, endpoints, names) are ignored: they are
  the issuer's and the client's business.
- **Clock leeway** 30 s on `exp`/`nbf`, as today.

### 7.3 Credential kind and audit

`kind` states what the credential is, for audit only (it never decides
authority):

| `kind` | Meaning | `sub` | `CredentialKind` / audit actor |
| --- | --- | --- | --- |
| `key` | A long-lived key a person created (console or `remember login`). | the person's id | `KEY`, `keycred:<jti>`; subject = `sub` |
| `session` | A short-lived credential for a person's interactive session (browser, OAuth access token). | the person's id | `BROWSER`, `browsercred:<jti>`; subject = `sub` |
| `service` | A short-lived credential an issuer derives for its own calls. | must equal `dpcred:<jti>` | `DEPLOYMENT`, `dpcred:<jti>`; subject none |

An unknown `kind`, or a `service` whose `sub` does not name its own `jti`, is
refused. `CredentialKind` gains `KEY` with marker `keycred:`.

### 7.4 Permissions → engine scopes

`AuthenticatedContext.scope` becomes `scopes`, a set of `PerimeterScope`
values; `covers()` is true when the set contains `WRITE` or the required
scope. The mapping:

| Permission | Engine scope | Allows |
| --- | --- | --- |
| `memory:read` | `READ` | every route enumerated as a read in `route_scope.py`, including `GET /deployment` (added by PR #455), `GET /documents`, readiness, search, graph, SQL, and non-mutating assured operations |
| `memory:write` | `WRITE` | everything, including `POST /ingest`, document deletion (D135) and connector changes |
| `memory:ingest` | `INGEST` | only `POST /ingest` (the narrow browser-upload credential; issuers mint it only for derived credentials) |
| `account:*`, and any permission without the `memory:` prefix | — | ignored (another service's authority) |
| any other `memory:…` value | — | the credential is refused: a permission this build cannot bound is not treated as a narrower one |

A valid credential with no memory permission authenticates but is denied
every route with `403 insufficient_scope`. The route table in
`route_scope.py` and `operation_scope()` are unchanged. Trusted ingest
attribution (D101 and its 2026-09-03 amendment) still requires `WRITE`.

The catalogue's `permission` for each tool must equal the scope the engine
requires for that tool's `http_route` (tested, §10), so a host that refuses
early refuses exactly what the engine would.

### 7.5 Revocation and key rotation

**The revocation document.** Revocation reaches the engine only as a
**revocation document**: a JWS signed by an active key of the same JWKS,
header `typ` `revocation+jwt`, with claims:

| Claim | Meaning |
| --- | --- |
| `iss` | The issuer; must equal `API_KEY_ISSUER`. |
| `aud` | This deployment: must equal `API_KEY_PROJECT_ID`. A document for another deployment is rejected, so documents cannot be replayed across deployments. |
| `seq` | A non-negative integer the issuer increases on **every** newly issued document for that `aud`, including the heartbeat re-issue each refresh interval when nothing changed. |
| `iat`, `exp` | When it was issued, and when the next document is due. |
| `revoked` | Array of `jti` values refused despite a valid signature. |
| `active_kids` | The signing-key ids (`kid`) whose credentials remain valid. |

The signature, audience and sequence let the document travel over any channel
— fetched from `API_REVOCATION_URL` or pushed inline — without that channel
being trusted.

**Acceptance and cache.**
- **First document.** When the deployment has no accepted document yet
  (nothing persisted), it accepts the first document whose signature verifies
  against a key in the JWKS and whose `iss` and `aud` match.
- **Later documents** are accepted only if the signature, `iss` and `aud`
  verify, the signing `kid` is in the currently accepted document's
  `active_kids`, and `seq` is strictly greater than the last accepted `seq`. An equal `seq` with identical content
  is a no-op; an equal `seq` with different content, or a lower `seq`, is
  rejected and logged as a rollback attempt.
- The last accepted `seq` and document are persisted in the deployment's
  spine (a one-row perimeter-state table) and loaded at start-up, so a
  restart cannot be used to roll back to an older document. The in-memory
  copy is the cache every request reads; the refresh loop replaces it
  atomically.
- The engine fetches `API_REVOCATION_URL` (and `API_SIGNING_KEYS_URL`) every
  `API_KEY_REFRESH_S`. A failed fetch, or a document that fails any check,
  keeps the last accepted document.
- The issuer must publish a new document (higher `seq`, fresh `iat`) at least
  every `API_KEY_REFRESH_S`, even when nothing changed; that is what keeps a
  healthy deployment inside the staleness bound.

**Staleness bound and the true worst case.** When `now − iat` of the accepted
document exceeds `API_REVOCATION_MAX_STALENESS_S` (S), or the document is past
its `exp`, the engine refuses every signed credential whose lifetime
(`exp − iat` of the credential) exceeds S, and logs a structured error.
Shorter-lived credentials do not depend on revocation and continue; the shared
secret is unaffected. Because the accepted document was issued before any
revocation it does not contain, a key revoked at time *r* stops working at
this deployment no later than **r + S + 30 s** (the clock leeway), whatever
happens to fetches or to the issuer's publication delay: either a document
listing it is accepted sooner, or the older document goes stale at
`iat + S ≤ r + S`. A deployment configured with signed keys but no
revocation source accepts only credentials whose lifetime is at most S.

**Key rotation as revocation.** `active_kids` is the rotation valve; a
retiring rotation is expressed only by removing the `kid` from it. A
credential whose header `kid` is not in the accepted document's
`active_kids` is refused even if that key is still present in the JWKS. To
retire a signing-key generation — including after a suspected compromise —
the issuer publishes a document without that `kid`; every credential signed
by it stops working within the same bound, and a later JWKS removes the key.
Without a revocation document there is no `active_kids` restriction beyond
the JWKS itself.

- A JWKS fetched as `{"keys": []}` makes the adapter deny every signed
  credential, as the inline form does today.

### 7.6 Direct-path admission limits

SDK and CLI traffic reaches the engine directly, not through any host that
could meter it, so the perimeter itself bounds how much one key and one
deployment can ask for. After authentication and before the route runs:

| Limit | Starting value (configurable) | Setting (`REMEMBERSTACK_SELFHOST_API_ADMISSION_…`) |
| --- | --- | --- |
| Requests per key id (`jti`) | 120 per minute, burst 30 | `KEY_RATE_PER_MIN`, `KEY_BURST` |
| In flight per key id | 8 | `KEY_IN_FLIGHT` |
| Requests per deployment | 600 per minute | `DEPLOYMENT_RATE_PER_MIN` |
| In flight per deployment | 32 | `DEPLOYMENT_IN_FLIGHT` |

- **Mechanism.** A token bucket (a counter that refills at the configured rate
  up to the burst size; each request takes one token) for rates, and a
  counting semaphore for in-flight requests. Per-key limits apply to every
  signed credential, keyed by `jti`; the shared-secret bearer has no key id
  and is bounded by the per-deployment limits only. `GET /healthz` is exempt.
  An in-flight slot is released when the response finishes, errors, or the
  client disconnects.
- **Refusal.** `429` with the error envelope code `rate_limited` or
  `concurrency_limited` and a `Retry-After` header in whole seconds: the time
  until the bucket holds a token, or 1 for an in-flight refusal. The SDK
  raises `RateLimited` carrying `retry_after` and does not retry by itself;
  MCP hosts return the same code as a tool error.
- **Where the counters live: in process, per API replica.** Each API process
  keeps its own buckets and semaphores in memory. Rejected alternative:
  Postgres-backed counters, which add a write to every request on the hot
  path, contend on a few hot rows for a busy key, and turn a slow database
  into refused requests even for reads that would have succeeded. The cost
  of the in-process choice is that limits are per replica: with N API
  replicas behind a load balancer the effective ceilings are N times the
  configured values. An operator running N replicas sets each value to the
  intended total divided by N; the self-host profile runs one API replica,
  where configured and effective limits coincide.
- These limits bound request volume only. Spend (D91 request-path metering,
  the spend lease) and the D74 admission barrier are unchanged and still
  apply after this check.

### 7.7 Failure responses

Every refusal is `401` with no detail beyond "not authenticated" (bad
signature, unknown `kid`, wrong issuer, not covering this deployment, expired,
revoked, stale revocation, unknown kind or memory permission), except a
valid credential lacking the needed scope, which is `403
insufficient_scope`, and an admission refusal, which is `429` with
`Retry-After` (§7.6). Logs record the reason class and `jti`, never the token.

## 8. SDK and CLI with one key

### 8.1 Issuer metadata

A signed key's `iss` is the issuer's base URL. The client reads the issuer's
OAuth authorization-server metadata (RFC 8414) from
`{iss}/.well-known/oauth-authorization-server`, using these fields:

| Field | Used by |
| --- | --- |
| `device_authorization_endpoint`, `token_endpoint` | `remember login` (RFC 8628 device grant) |
| `revocation_endpoint` | `remember logout` (RFC 7009) |
| `jwks_uri` | diagnostics (`remember doctor`) |
| `remember_mcp_endpoint` | bridge mode and `remember setup` |
| `remember_project_endpoint` | data-plane host resolution (§8.3) |
| `remember_account_endpoint` | base URL of the issuer's account API, used by `remember.Client.account` and `remember whoami` (§8.3, §8.4) |

The concrete endpoint paths are the issuer's (for remember.dev, the cloud
design's). The `remember` package ships one default issuer identifier,
`https://remember.dev`, used only by `remember login`/`setup --cloud` when no
other issuer is given. Metadata is cached per process; an issuer whose
metadata is unavailable yields a clear error naming the URL.

### 8.2 One resolver, one precedence

`remember.connection.resolve_connection()` is used by `remember.Client`
(and `MemoryClient`) and by every CLI command, with one precedence: explicit
argument, then environment, then the stored credential file, then values
derived from the key. The CLI passes its flags as the explicit values. The
SDK reads the same stored file as the CLI; what keeps an embedded library
from sending a machine credential somewhere its caller never named is the
stored-key origin rule below, not a refusal to read the file.

| Setting | 1. explicit | 2. environment | 3. credential file | 4. derived | 5. default |
| --- | --- | --- | --- | --- | --- |
| Key | `api_key=` / `--api-key` | `REMEMBER_API_KEY` | `key` | — | none |
| Engine URL | `base_url=` / `--api-url` | `REMEMBER_API_URL` | `api_url` | from the key (§8.3) | `http://127.0.0.1:8000` when the key is not a signed issuer key |
| Project | `project=` / `--project` | `REMEMBER_PROJECT` | `default_project` | key claim `default_project` | — |
| Remote MCP URL | `--remote-url` | `REMEMBER_MCP_URL` | — | issuer metadata | — |
| Issuer | `--issuer` | `REMEMBER_ISSUER` | `issuer` | key claim `iss` | `https://remember.dev` (login/setup only) |
| Config directory | — | `REMEMBER_CONFIG_DIR` | — | `$XDG_CONFIG_HOME/remember` | `~/.config/remember` |

A key may be given bare or as `Bearer <key>`. The earlier names
(`REMEMBER_TOKEN`, `REMEMBER_API_AUTHORIZATION`, `REMEMBERSTACK_API_AUTHORIZATION`,
`REMEMBER_DATA_PLANE_URL`, `REMEMBERSTACK_API_URL`, `REMEMBER_TOKEN_HOST`,
`REMEMBER_CONTROL_PLANE_URL`, `REMEMBERSTACK_TOKEN_HOST`,
`REMEMBER_CLOUD_TOKEN`, `REMEMBER_CLOUD_ORG`, `REMEMBER_CLOUD_URL`) are not
read.

**Stored-key origin rule.** A key taken from the credential file is sent only
to the file's `api_url` origin, to the issuer, to the issuer-advertised
`remember_mcp_endpoint` origin (§5.3), or to a data-plane URL the issuer
resolved for that key. If an explicit engine URL or a remote MCP URL points
elsewhere, the stored key is not attached and the SDK raises (CLI: exits 2)
saying an explicit key is required for that destination. A key the caller
passed explicitly or through the environment is sent where the caller
directed.

### 8.3 Resolving the data-plane host from a key

When no engine URL is explicit and the key is a signed key, the client reads
its claims **without verifying them** — only to route; the engine verifies —
and resolves the target:

1. If the key carries an `api_url` claim (an issuer may include it for a
   single-project key) and no project was requested, use it.
2. Otherwise call `GET {remember_project_endpoint}?project=<project or default>`
   with the key as bearer. The response is
   `{"project": "<id>", "name": "<name>", "api_url": "https://…", "audience": "<the deployment's API_KEY_PROJECT_ID>"}`.
   The client checks that `audience` is in the key's `projects` list (or that
   the key is organisation-wide, `"org:*"`) and that
   `api_url` is `https` (or loopback `http`), and talks to `api_url` directly.
3. **Cache and moved deployments.** The resolved `api_url` is cached per
   (issuer, key id, project) in the process for a bounded time (starting
   value 10 minutes) and re-resolved when it expires. It is also invalidated
   immediately when a request to it fails with a connection error (DNS
   failure, refused or reset connection, TLS failure), with `421 Misdirected
   Request`, or with a `404` whose body is not the engine's error envelope
   (the host no longer serves that deployment). The client then re-resolves;
   if the issuer returns a different `api_url` it retries the request once
   there, and if it returns the same URL (or resolution fails) it surfaces the
   original error. One retry, never a loop. The retry is safe for every
   catalogue call: reads are side-effect free, a repeated identical ingest is
   a no-op at the engine (D55), and deletion is idempotent (D135). The `api_url`
   claim path (step 1) follows the same invalidation, falling through to step 2.
4. Failure is explicit: an unknown project, a project outside the key, or an
   unreachable issuer raises `ProjectResolutionError` (CLI exit 1) naming the
   project and issuer. The client never falls back to localhost for a signed
   key.

`remember.Client(api_key=..., project=...)` therefore works against
remember.dev without the caller knowing any host; SDK and CLI traffic goes to
the deployment directly, never through the account service. `Client` without
a key still reaches a self-hosted engine at `REMEMBER_API_URL` or localhost.

**Account calls live on the same client.** `CloudClient` is removed.
`remember.Client` exposes an `account` namespace (`client.account.…`) that
calls the key's issuer's account API with the same key; the issuer is found
from `iss` and the API location from the issuer metadata's
`remember_account_endpoint`. When the key has no
issuer (a self-hosted shared secret) or the metadata has no
`remember_account_endpoint`,
`client.account` raises `AccountApiUnavailable`; memory calls are unaffected.
The account API's operations and their permissions are defined by the issuer
(for remember.dev, the cloud design); the engine has no account surface.

### 8.4 `remember login`, `logout`, `switch`

- `remember login [--issuer URL]` runs the RFC 8628 device grant against the
  issuer's metadata endpoints. It prints the user code and verification URL,
  polls at the given interval (honouring `slow_down`, a 30 s cap, `Retry-After`,
  same-origin redirects only, `Ctrl-C` exit 130 with nothing written), and
  receives one key. Which projects and permissions the key covers is chosen on
  the issuer's consent page; the CLI sends no audience. The D92 JSON variant of
  the grant and `--audience`/`--control-plane`/`--token-host` are removed.
- The key is written to `credentials.json` version 2:

  ```json
  {
    "version": 2,
    "issuer": "https://remember.dev",
    "key": "<secret>",
    "key_id": "<jti>",
    "expires_at": "2027-09-23T10:00:00+00:00",
    "default_project": null,
    "api_url": null
  }
  ```

  `api_url` is set only by `remember setup --self-hosted --api-url …`, where
  `key` is the self-hosted shared secret and `issuer` is absent. The file is
  created `0600` in a `0700` directory with the existing atomic write, lock,
  fsync and symlink refusal. `extra="forbid"`; a file that is not this
  shape is refused with "run `remember login`".
- **A second login journals, replaces, then revokes.** Order:
  1. mint the new key;
  2. durably write the **old** key's secret, issuer and key id to the
     existing pending-revocation journal (owner-only file, atomic write,
     fsync of file and directory) **before** touching `credentials.json`;
  3. durably replace `credentials.json` with the new key (atomic replace,
     fsync of file and directory);
  4. revoke the old key through `revocation_endpoint`, and remove its journal
     entry only when the issuer confirms (2xx, or `401`/`404` = already dead).

  A crash at any point leaves either the old file plus a journal entry for a
  still-valid old key (harmless: the next run retries or discards it once it
  sees the old key still in `credentials.json`), or the new file plus a
  journalled old key that will be revoked — never an old key that nobody will
  revoke. An unconfirmed revocation stays journalled and is retried by every
  later CLI start and by `remember logout`, and the command reports it. If
  step 3 fails, the new key is revoked immediately (or journalled if that
  fails), the old key's journal entry is removed, and the old file stays.
- `remember logout` revokes through `revocation_endpoint` and unlinks:
  2xx or already dead (`401`/`404`) → unlink, exit 0; 5xx or network
  failure → keep the file, exit 1; no file → exit 0. It also retries any
  journalled revocations.
- `remember switch <project>` sets `default_project` locally after resolving
  it once (§8.3) to prove the key covers it. No new credential is minted.
- `remember whoami` prints the issuer, key id, expiry, default project and
  permissions from the key's own claims, locally. Only when the key has
  `account:read` and the issuer metadata has a `remember_account_endpoint` does it add the
  issuer's account view (a host call). It never calls the engine: a
  memory-only key gets the local claim summary and nothing more, and the
  engine has no identity endpoint.

## 9. Failure behaviour summary

| Situation | Behaviour |
| --- | --- |
| Host catalogue tool not served by the deployment at a compatible version | Omitted from `tools/list` (single target) or `tool_unavailable_on_target` (multi-target); `remember doctor` names it |
| `project` sent to a single-target host | `project_routing_unavailable` |
| Unknown `project` on a multi-target local host | `unknown_project` with valid names |
| Bridge: no key | Start-up error pointing to `remember login` |
| Bridge: `401` from remote | JSON-RPC error, re-login hint; bridge keeps running |
| Bridge: cross-origin redirect | Error; key not sent |
| Bridge: stored key with a remote URL outside the issuer's `remember_mcp_endpoint` origin | Refuses to start; explicit key required |
| Bridge `--read-only`: remote tool not annotated `readOnlyHint: true` | Omitted and refused locally |
| Engine: revocation document with lower `seq`, other audience, or retired signer | Rejected; last accepted document kept; logged |
| Engine: credential signed by a `kid` absent from `active_kids` | `401` |
| Client: deployment moved (connection failure, `421`, non-engine `404`) | Re-resolve; retry once if the URL changed |
| Re-login: revocation of the old key unconfirmed | New key kept; old key journalled and retried |
| HTTP transport: non-loopback bind in front of an unauthenticated engine | Refuses to start without `--allow-unauthenticated-engine` |
| HTTP transport: bad `Origin` | `403` |
| Engine: key not covering this deployment / wrong issuer / revoked | `401` |
| Engine: per-key or per-deployment admission limit reached | `429` with `Retry-After` |
| Engine: revocation document stale | Long-lived signed keys `401`; short-lived credentials and shared secret unaffected |
| Engine: valid key lacking permission | `403 insufficient_scope` |
| Client: project resolution fails | `ProjectResolutionError` / exit 1; no localhost fallback |
| Stored key with an explicit foreign URL | Key not attached; message says so |
| Credential file not in the version-2 shape | Refused; "run `remember login`" |

## 10. Tests to add

Catalogue (`remember.mcp_tools`):
- the catalogue lists exactly the fifteen tools of §3.1; `examples.*` never appear;
- `render_tools_list` output is identical for the in-process server, engine
  mode and bridge path-substitution for the same options (golden file keyed by
  `catalog_digest()`);
- `path_ingest=False` removes `path` from `ingest`; `read_only=True` removes
  `ingest` and `delete_document`; `project` adds the argument with the
  catalogue text;
- import test: the module imports with only the base dependencies installed
  and performs no network or file I/O.

Engine consistency:
- `GET /operations` name/description/input_schema/mutates/version equal the
  catalogue's for all four operations;
- for every catalogue tool, `permission` equals the scope `route_scope`
  requires for its `http_route` (and `operation_scope` for operations);
- `GET /deployment` `tools` lists the query tools only when the open query
  facade is composed, and `delete_document` only when deletion is composed;
- engine HTTP and in-process MCP reject a `project` argument.

`remember mcp`:
- engine mode: version gating (served newer compatible, older compatible,
  incompatible both ways);
- single target refuses `project`; multi-target routes, rejects unknown
  names, uses the default;
- HTTP transport: session issuance and `404` on unknown session, `405` on
  `GET`, `Origin` refusal, bearer forwarded unchanged and never stored,
  loopback default, non-loopback refusal against an unauthenticated engine;
- bridge: verbatim relay of JSON and event-stream responses; unknown remote
  tools passed through; path ingest substitution only under configured roots
  and matching schema; `404` session recovery; `401` message; cross-origin
  redirect refusal; `http` non-loopback refusal; key absent from all output;
  stored key refused for a non-advertised remote origin and accepted with an
  explicit key; `--read-only` refuses unannotated, `false` and unknown tools.

`remember setup`:
- the four entry shapes per harness; no literal key in any written file;
  probe uncertainty falls back to the stdio bridge; dry-run masks the key.

Perimeter (`signed_token_auth.py`):
- wrong `iss`, missing `permissions`/`kind`, unknown `kind`, `service` with a
  foreign `sub` → refused;
- coverage: explicit `projects` list match and miss; `"org:*"` accepted only
  with matching `org` and a configured `API_KEY_TENANT_ID`, refused without
  `org`, with another `org`, or with no tenant configured; no other patterns;
- first revocation document accepted on a fresh deployment; heartbeat
  documents advance `seq`;
- admission: per-key and per-deployment rate and in-flight limits return
  `429` with a correct `Retry-After`; in-flight slots are released on
  success, error and client disconnect; `/healthz` is exempt;
- permission mapping table, including ignored `account:*` and refused unknown
  `memory:*`; no memory permission → `403`;
- prefix stripped once from the configured set only;
- revocation documents: lower `seq`, equal `seq` with different content,
  wrong `aud`, signer not in `active_kids` → rejected; persisted `seq`
  survives restart; staleness bound refuses long-lived keys only and the
  worst case `r + S + leeway` holds with fetches failing; a `kid` dropped from
  `active_kids` is refused while still in the JWKS;
- audit actor ids `keycred:`/`browsercred:`/`dpcred:`.

Client:
- one resolver: the precedence table above for SDK and CLI, including the
  stored file, run as one parametrised test over both entry points; removed
  variable names have no effect; `CloudClient` no longer exists and
  `client.account` raises `AccountApiUnavailable` without an issuer;
- host cache: TTL expiry, invalidation on connection failure / `421` /
  non-engine `404`, single retry only when the URL changed;
- re-login ordering: crash after step 1, 2 or 3 leaves a usable key and a
  journalled revocation; `whoami` makes no engine call;
- host resolution: `api_url` claim, project endpoint, audience mismatch,
  non-https refusal, no localhost fallback for signed keys;
- stored-key origin rule;
- login/logout/switch against a fake RFC 8414 + 8628 + 7009 issuer;
  version-1 file refusal.

## 11. Non-goals

- The engine does not mint keys, run an OAuth authorization server, or know
  users, organisations, members, billing or project directories. A
  self-hoster without an issuer uses the shared-secret bearer.
- The engine does not route between deployments; routing is a host concern.
- `remember mcp` does not implement account tools; in bridge mode it relays
  whatever the remote serves.
- The Streamable HTTP listener does not terminate TLS or implement OAuth; it
  sits behind the operator's proxy and defers authorisation to the engine.
- Token exchange from a key to per-deployment credentials is not part of the
  client; an issuer may do it internally for its own calls.
