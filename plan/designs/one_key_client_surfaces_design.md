# One key, one tool catalogue — engine-side client surfaces (Design)

**Status:** accepted design (D136). **Date:** 2026-09-23.
**Decision:** [D136](../../decisions.md#d136-one-signed-key-one-shared-mcp-tool-catalogue-and-a-bridging-remember-mcp).
**Analysis:** [one_key_client_surfaces_analysis.md](../analysis/one_key_client_surfaces_analysis.md).
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
5. **The engine perimeter's signed-key contract**: issuer, audiences,
   permissions, revocation (§7).
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
- **Audience** — the `aud` claim: which deployments the key may be used at.
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
   working tree a harness reads), never logged, never printed after login,
   and never sent to an origin other than its issuer or a deployment the
   caller named or the issuer resolved for it.
5. The SDK and the CLI resolve environment variables with one shared
   function. The CLI alone additionally reads the credential file.

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
   else `REMEMBER_MCP_URL`, else the `mcp_endpoint` of the stored key's
   issuer when the resolved key is a signed key and no engine URL was given
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
  metadata (§8.1). Key: `--api-key`, `REMEMBER_API_KEY`, or the credential
  file (§8.2). Both are required; a missing key is a start-up error that says
  how to get one (`remember login`).
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
- `--read-only` in bridge mode omits and refuses remote tools that the
  catalogue marks `memory:write`, and any remote tool the remote marks as
  non-read-only in its annotations. `--transport http` is refused in bridge
  mode (an HTTP client should connect to the remote URL directly).

## 6. `remember setup`

`remember setup` writes each detected harness's MCP entry. It chooses among
three entry shapes:

| Situation | Entry written |
| --- | --- |
| Hosted key (signed key whose issuer publishes an `mcp_endpoint`), harness supports remote MCP with OAuth sign-in, interactive use | **Remote entry, OAuth**: the endpoint URL only. The harness signs in through the browser; no secret is written anywhere. |
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
| `API_REVOKED_CREDENTIAL_IDS` / `API_REVOCATION_URL` | Revocation, inline id list or a URL serving the revocation document (§7.5). |
| `API_ACCEPTED_AUDIENCES` | Extra audience strings this deployment accepts, comma-separated. The deployment id is always accepted. |
| `API_KEY_PREFIXES` | Routing prefixes that may precede the JWS, e.g. `rmb_`. Empty = bare JWS only. |
| `API_KEY_REFRESH_S` | Refresh interval for fetched JWKS and revocation (starting value 60). |
| `API_REVOCATION_MAX_STALENESS_S` | Age after which a fetched revocation document is too old (starting value 300). |

Numbers are starting points to be measured, not constants.

### 7.2 Accepted token

- **Form**: optionally one prefix from `API_KEY_PREFIXES` (stripped exactly
  once), then a compact JWS. Header `alg` is `EdDSA`, `kid` names a key in the
  set; the key is selected by `kid`, never by trying keys.
- **Required claims**: `iss` (equals `API_KEY_ISSUER`), `sub`, `jti`
  (non-empty; the revocation handle), `aud` (a string or an array of strings),
  `iat`, `nbf`, `exp`, `permissions` (array of strings), `kind` (§7.3).
- **Audience**: accepted when at least one `aud` value equals this
  deployment's id or a value in `API_ACCEPTED_AUDIENCES`. Comparison is exact
  string equality; there are no wildcards. An operator expresses "every
  project in a group" by configuring the same opaque group audience (for
  example `org:<uuid>`) on each deployment of the group; the engine does not
  interpret it.
- **Unknown claims** (organisation, default project, endpoints) are ignored:
  they are the issuer's and the client's business.
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

### 7.5 Revocation

A revocation source is either the inline id list, or a URL serving a
**revocation document**: a JWS signed by a key in the same JWKS, header `typ`
`revocation+jwt`, with claims `iss` (the issuer), `iat`, `exp` (when the next
document is due) and `revoked` (array of `jti`). The signature lets the
document travel over any channel, including operator push.

- The engine refreshes fetched JWKS and revocation every `API_KEY_REFRESH_S`,
  keeping the last good copy on failure. A fetched document that fails to
  parse or verify is rejected and the last good copy kept.
- **Staleness bound.** When the newest good revocation document is older than
  `API_REVOCATION_MAX_STALENESS_S`, or past its own `exp`, the engine refuses
  every signed credential whose lifetime (`exp − iat`) exceeds that bound, and
  logs a structured error. Shorter-lived credentials do not depend on
  revocation and continue. The shared secret is unaffected.
- A JWKS fetched as `{"keys": []}` makes the adapter deny every signed
  credential, as the inline form does today.

### 7.6 Failure responses

Every refusal is `401` with no detail beyond "not authenticated" (bad
signature, unknown `kid`, wrong issuer, no accepted audience, expired,
revoked, stale revocation, unknown kind or memory permission), except a
valid credential lacking the needed scope, which is `403
insufficient_scope`. Logs record the reason class and `jti`, never the token.

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

The concrete endpoint paths are the issuer's (for remember.dev, the cloud
design's). The `remember` package ships one default issuer identifier,
`https://remember.dev`, used only by `remember login`/`setup --cloud` when no
other issuer is given. Metadata is cached per process; an issuer whose
metadata is unavailable yields a clear error naming the URL.

### 8.2 One resolver, one precedence

`remember.connection.resolve_connection()` is used by `remember.Client` and
every CLI command. The CLI passes its flags as the explicit values and enables
the credential-file step; the SDK never reads the file.

| Setting | 1. explicit | 2. environment | 3. credential file (CLI only) | 4. derived | 5. default |
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
to the file's `api_url` origin, to the issuer, or to a data-plane URL the
issuer resolved for that key. If an explicit engine URL points elsewhere, the
stored key is not attached and the command says so. A key the caller passed
explicitly or through the environment is sent where the caller directed.

### 8.3 Resolving the data-plane host from a key

When no engine URL is explicit and the key is a signed key, the client reads
its claims **without verifying them** — only to route; the engine verifies —
and resolves the target:

1. If the key carries an `api_url` claim (an issuer may include it for a
   single-project key) and no project was requested, use it.
2. Otherwise call `GET {remember_project_endpoint}?project=<project or default>`
   with the key as bearer. The response is
   `{"project": "<id>", "name": "<name>", "api_url": "https://…", "audience": "<aud value>"}`.
   The client checks that `audience` is one of the key's `aud` values and that
   `api_url` is `https` (or loopback `http`), caches the answer for the process
   lifetime, and talks to `api_url` directly.
3. Failure is explicit: an unknown project, a project outside the key, or an
   unreachable issuer raises `ProjectResolutionError` (CLI exit 1) naming the
   project and issuer. The client never falls back to localhost for a signed
   key.

`remember.Client(api_key=..., project=...)` therefore works against
remember.dev without the caller knowing any host; SDK and CLI traffic goes to
the deployment directly, never through the account service. `Client` without
a key still reaches a self-hosted engine at `REMEMBER_API_URL` or localhost.

The account-side client (`CloudClient`) takes the same key
(`CloudClient(api_key=…)`), finds the issuer from `iss`, and reads the
organisation from the key; the account API it calls is defined by the cloud
design.

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
  fsync and symlink refusal. `extra="forbid"`; version 1 files are refused
  with "run `remember login` again".
- A second login revokes the previous key first, keeping the existing
  pending-revocation journal for revocations that cannot be confirmed.
- `remember logout` revokes through `revocation_endpoint` and unlinks; the
  table of outcomes in the metering design §6.4 stays (2xx or already-dead →
  unlink, 5xx/network → keep the file, exit 1).
- `remember switch <project>` sets `default_project` locally after resolving
  it once (§8.3) to prove the key covers it. No new credential is minted.
- `remember whoami` prints the issuer, key id, expiry, default project and
  permissions from the key's claims, then the issuer's account view when the
  key has `account:read`.

## 9. Failure behaviour summary

| Situation | Behaviour |
| --- | --- |
| Host catalogue tool not served by the deployment at a compatible version | Omitted from `tools/list` (single target) or `tool_unavailable_on_target` (multi-target); `remember doctor` names it |
| `project` sent to a single-target host | `project_routing_unavailable` |
| Unknown `project` on a multi-target local host | `unknown_project` with valid names |
| Bridge: no key | Start-up error pointing to `remember login` |
| Bridge: `401` from remote | JSON-RPC error, re-login hint; bridge keeps running |
| Bridge: cross-origin redirect | Error; key not sent |
| HTTP transport: non-loopback bind in front of an unauthenticated engine | Refuses to start without `--allow-unauthenticated-engine` |
| HTTP transport: bad `Origin` | `403` |
| Engine: key with no accepted audience / wrong issuer / revoked | `401` |
| Engine: revocation document stale | Long-lived signed keys `401`; short-lived credentials and shared secret unaffected |
| Engine: valid key lacking permission | `403 insufficient_scope` |
| Client: project resolution fails | `ProjectResolutionError` / exit 1; no localhost fallback |
| Stored key with an explicit foreign URL | Key not attached; message says so |
| Credential file version 1 | Refused; "run `remember login` again" |

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
  redirect refusal; `http` non-loopback refusal; key absent from all output.

`remember setup`:
- the four entry shapes per harness; no literal key in any written file;
  probe uncertainty falls back to the stdio bridge; dry-run masks the key.

Perimeter (`signed_token_auth.py`):
- wrong `iss`, missing `permissions`/`kind`, unknown `kind`, `service` with a
  foreign `sub` → refused;
- `aud` as string and as array; accepted via deployment id and via a
  configured group audience; no match → refused; no wildcard matching;
- permission mapping table, including ignored `account:*` and refused unknown
  `memory:*`; no memory permission → `403`;
- prefix stripped once from the configured set only;
- fetched JWKS/revocation: refresh, last-good retention, invalid signature on
  the revocation document, staleness bound refusing long-lived keys only;
- audit actor ids `keycred:`/`browsercred:`/`dpcred:`.

Client:
- one resolver: the precedence table above for SDK and CLI, run as one
  parametrised test over both entry points; removed variable names have no
  effect;
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
