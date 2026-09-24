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
- **Audience and coverage** — every credential carries `aud` (which kind of
  place it is for: an issuer tenant, or one deployment) and `projects` (which
  projects it covers: an explicit list, or `"org:*"` for every project of its
  tenant).
- **Issuer tenant** — the issuer's own grouping of deployments (for
  remember.dev, an organisation). The engine knows only one opaque tenant id,
  configured by the operator; it has no model of what a tenant is.
- **Deployment** — one running engine (one trust domain, D50). A **target**
  is one deployment a host can route to.

### 1.2 Simplicity rule

Every mechanism here exists because a stated requirement or a security
invariant needs it. Deliberately left out (not deferred — not part of the
system): local multi-target routing in `remember mcp`, a compatibility range
per tool version, prefix configuration, inline key-set or revocation
configuration, per-tool path-ingest rewriting in the bridge, and routing
hints (`api_url`, `default_project`) inside keys.

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

- `memory_tools()` and `tool(name)`.
- `render_tools_list(tools, *, project: ProjectArgument | None, path_ingest: bool, read_only: bool)`
  — the MCP `tools/list` entries. `path_ingest` controls whether `ingest`
  offers the local `path` body source (only `remember mcp` in engine mode,
  which runs on the caller's machine, sets it; remote hosts and the bridge
  never offer `path`);
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

### 3.4 Tool versions

The client package and the engine are released together but installed
separately, so a host's catalogue and a deployment can differ.

- A tool's `tool_version` rises whenever its input schema admits a call that
  an engine serving the previous version cannot handle — a new argument,
  optional ones included, a widened type or bound, a new enum value — and
  whenever an existing call could fail or change meaning (a removed or renamed
  argument, a newly required argument, a narrowed type or bound, a changed
  result shape). Description-only edits need no bump. Because hosts render a
  tool only at an equal version (below), an agent never sees an argument the
  deployment would reject.
- Each deployment reports what it serves in `GET /deployment` (readable with
  `memory:read`; PR #455 adds the route to the read table), in a new `tools`
  object mapping tool name to `tool_version`, e.g. `{"facts_context": 3, …}`.
  The seven query tools appear only when the open query facade is composed,
  and `delete_document` only when deletion is composed.
- A host renders a catalogue tool for a deployment only when the deployment
  lists it with the **same** `tool_version`. Otherwise the tool is omitted and
  `remember doctor` names it and the fix (upgrade `remember`, or upgrade the
  engine).
- A host serving several deployments renders its catalogue without knowing
  the target in advance; when the call's target does not serve the tool at the
  same version it returns the tool error `tool_unavailable_on_target` naming
  the tool, the target and both versions.

This replaces the three current gates (static write tools, `GET /operations`
404, the `GET /query/space` identity probe) with one advertised set.

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

`render_tools_list(project=True)` adds it. Only a host that serves more than
one deployment renders it — in this system, a remote host such as
remember.dev's; `remember mcp` in engine mode serves exactly one engine.

### 4.2 Who resolves it

The argument is **host-resolved routing**, not part of any memory operation:

1. The host reads `project`, resolves it to one target (for remember.dev: the
   key's `projects` and the issuer's default project), and **removes it** from
   the arguments.
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
| `remember mcp`, engine mode | no | refused: `project_routing_unavailable`, "this server serves one deployment" |
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
2. **Engine mode** otherwise: it serves the catalogue against one engine's
   HTTP API, with connection resolution from §8.2.

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
  - A request whose `Origin` header is present and is not the listener's own
    origin is refused with `403` (defence against DNS rebinding, where a web
    page reaches a loopback server through a hostname it controls).
  - **Authorisation is the engine's.** The listener forwards the caller's
    `Authorization` header to the engine unchanged and holds no credential of
    its own, so the engine perimeter decides every call and records the real
    caller (analysis §3.7). A call without a bearer is forwarded without one.
  - It binds to loopback by default. With a non-loopback `--bind`, start-up
    probes the engine without a credential and refuses to start if the engine
    answers a read: an unauthenticated engine is never exposed this way. TLS
    is terminated by the operator's proxy; the listener does not implement it.
  - Per-request `tools/list` is not filtered by the caller's permissions (the
    listener does not know them); a call the engine refuses returns
    `insufficient_permission`.
- `--read-only` keeps its meaning in both transports: write-permission tools
  are omitted and refused locally.

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
  know (remember.dev's account tools), except as `--read-only` requires.
  Agents send file bodies as `text` or `content_base64`; the bridge does not
  read local paths.
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
| `DEPLOYMENT_ID` (existing) | This deployment's id: the `aud` of derived credentials and of revocation documents. |
| `API_KEY_ISSUER` | The exact `iss` accepted. Setting it enables signed credentials; the settings below are then required. |
| `API_KEY_TENANT_ID` | The issuer tenant this deployment belongs to (for remember.dev, the organisation id). Keys must carry `aud = org:<this>` and `org = <this>`. |
| `API_KEY_PROJECT_ID` | The issuer's project id for this deployment, matched against `projects`. Default: the deployment id. |
| `API_SIGNING_KEYS_URL` | Where the JWKS is fetched. Ed25519 public keys only, as today. |
| `API_REVOCATION_URL` | Where the signed revocation document (§7.5) is fetched. |
| `API_KEY_REFRESH_S` | R: refresh interval for both fetches (starting value 60). |
| `API_REVOCATION_MAX_AGE_S` | S: the maximum age of an accepted revocation document (starting value 3600 = 1 hour). |

Numbers here and in §7.6 are starting points to be measured, not constants.
The inline JWKS setting and the plain revoked-id list are removed.

### 7.2 Accepted credential

- **Precondition**: a fresh revocation document has been accepted (§7.5);
  without one every signed credential is refused.
- **Form**: `<prefix>_<JWS>`, where the prefix is letters only (remember.dev
  uses `rmb_`), so secret scanners such as GitHub push protection can
  recognise leaked keys. When the bearer does not start with a JWS header
  (`eyJ`), the engine strips everything up to and including the first `_`,
  then verifies the remainder; a prefix that is not letters only is refused.
  There is no prefix setting. Header `alg` is `EdDSA`, `kid` names a key in
  the fetched set and in the accepted revocation document's `active_kids`
  (§7.5); the key is selected by `kid`, never by trying keys.
- **Common rules**: `iss` equals `API_KEY_ISSUER`; `aud` is a single string;
  `jti` is non-empty (the revocation handle) and not in `revoked`; `exp`,
  `nbf`, `iat` are present, with 30 s clock leeway on `exp`/`nbf`;
  `permissions` is an array of strings (§7.4); `kind` is one of the values
  below. Claims not listed are ignored.
- **Per kind** — the complete claim sets, identical to the cloud design:

| `kind` | Required claims | `aud` must equal | `projects` must be | `org` must equal | `sub` |
| --- | --- | --- | --- | --- | --- |
| `key` | `iss, aud, sub, org, projects, permissions, kind, iat, nbf, exp, jti` | `org:<API_KEY_TENANT_ID>` | `"org:*"`, or a list of 1–20 project ids containing `API_KEY_PROJECT_ID` | `API_KEY_TENANT_ID` | the person's id |
| `session` (derived and browser credentials) | `iss, aud, sub, org, projects, permissions, kind, iat, nbf, exp, jti`; plus `src` on derived credentials only | this deployment's id | exactly `[API_KEY_PROJECT_ID]` | `API_KEY_TENANT_ID` | the person's id |
| `service` (generic machine credential) | `iss, aud, sub, permissions, kind, iat, nbf, exp, jti` | this deployment's id | — | — | exactly `dpcred:<jti>` |

  Anything else is refused with `401`: an `aud` of any other value (for
  example `https://remember.dev/mcp`, an OAuth token meant for the hosted MCP
  server), a list longer than 20, an empty list, another string in
  `projects`, a missing claim, or a claim of the wrong type. `src` (which
  host derived the credential, e.g. `mcp`) is required on credentials an issuer
  derives from a key for its own calls and absent on browser sessions; when
  present it is recorded in the audit context, and it never decides authority. `"org:*"` lets one key cover
  projects created after it was minted; the engine compares two opaque
  strings and learns nothing else about organisations.

### 7.3 Credential kind and audit

`kind` states what the credential is. It selects the claim rules of §7.2 and
the audit actor; what the credential may do is decided only by its
permissions (§7.4):

| `kind` | Meaning | `sub` | `CredentialKind` / audit actor |
| --- | --- | --- | --- |
| `key` | A long-lived key a person created (console or `remember login`). | the person's id | `KEY`, `keycred:<jti>`; subject = `sub` |
| `session` | A short-lived credential derived for one deployment from a person's key or sign-in (browser, hosted MCP calls). | the person's id | `BROWSER`, `browsercred:<jti>`; subject = `sub` |
| `service` | A short-lived machine credential for one deployment. Part of the generic contract whether or not a given issuer mints it. | must equal `dpcred:<jti>` | `DEPLOYMENT`, `dpcred:<jti>`; subject none |

`CredentialKind` gains `KEY` with marker `keycred:`.

### 7.4 Permissions → engine scopes

`AuthenticatedContext.scope` stays one `PerimeterScope`. The credential's
memory permissions map to it:

| Permission | Engine scope | Allows |
| --- | --- | --- |
| `memory:read` | `READ` | every route enumerated as a read in `route_scope.py`, including `GET /deployment` (added by PR #455), `GET /documents`, readiness, search, graph, SQL, and non-mutating assured operations |
| `memory:write` | `WRITE` | everything, including `POST /ingest`, document deletion (D135) and connector changes |
| `memory:ingest` | `INGEST` | only `POST /ingest` (the narrow browser-upload credential; only on `session` credentials) |
| `account:*`, and any permission without the `memory:` prefix | — | ignored (another service's authority) |
| any other `memory:…` value | — | the credential is refused: a permission this build cannot bound is not treated as a narrower one |

`memory:write` wins when present (it covers everything). Otherwise exactly one
of `memory:read` or `memory:ingest` must be present; a credential carrying
both without `memory:write` is refused, because issuers mint those narrow
credentials separately. A valid credential with no memory permission authenticates but is denied
every route with `403 insufficient_scope`. The route table in
`route_scope.py` and `operation_scope()` are unchanged. Trusted ingest
attribution (D101 and its 2026-09-03 amendment) still requires `WRITE`.

The catalogue's `permission` for each tool must equal the scope the engine
requires for that tool's `http_route` (tested, §10), so a host that refuses
early refuses exactly what the engine would.

### 7.5 Revocation and key rotation

**The revocation document** — identical in the cloud design. A JWS signed by a
key of the fetched JWKS, header `typ` `revocation+jwt`, with claims:

| Claim | Meaning |
| --- | --- |
| `iss` | The issuer; must equal `API_KEY_ISSUER`. |
| `aud` | This deployment's id. A document for another deployment is rejected, so documents cannot be replayed across deployments. |
| `seq` | An integer the issuer increases on **every** newly issued document for that deployment, including the heartbeat re-issue every R. |
| `iat` | When it was issued. |
| `exp` | `iat + S`. |
| `revoked` | Array of `jti` values refused despite a valid signature. |
| `active_kids` | The signing-key ids whose credentials remain valid. |

**Acceptance.**
- **First document**: a deployment with no accepted document accepts the
  first one whose signature verifies against the fetched key set and whose
  `iss` and `aud` match.
- **Later documents** must also be signed by a `kid` that is active in the
  previously accepted document and carry a `seq` strictly greater than the
  last accepted one. An equal `seq` with identical content is a no-op; a
  lower `seq`, or an equal one with different content, is rejected and logged
  as a rollback attempt.
- The last accepted document and its `seq` are persisted in the spine (a
  one-row perimeter-state table) and loaded at start-up, so a restart cannot
  roll revocation back. The in-memory copy is what requests read; the refresh
  loop replaces it atomically.
- The engine fetches the key set and the document every R (60 s starting
  value). A failed fetch or a rejected document keeps the last accepted one.
  The issuer re-issues the document every R.

**Fail closed without a fresh document** (identical in the cloud design). An
accepted document is fresh until `min(exp, iat + S)`. The engine refuses
**every** signed credential — `key`, `session` and `service` alike — until the
first document is accepted, and again whenever the accepted document is no
longer fresh (including one loaded from the spine at start-up that is already
stale), and logs a structured error each time. Only the shared-secret bearer
is unaffected. There is no exemption for short-lived credentials: one rule is
simpler to verify and cannot be gamed by a credential's own `exp`. A key
revoked at time *r* therefore stops working at this deployment by **r + S + leeway**
(leeway = the 30 s clock tolerance) whatever happens to fetches: either a document
listing it arrives sooner, or the last document without it was issued before
*r* and stops being fresh by *r + S*, after which nothing signed is accepted
until a newer document arrives.

S is one hour (R stays 60 s): a short account-service outage must not take
every data plane down, and an hour is the accepted ceiling on how long a
revoked key can outlive its revocation.

**Key rotation.** Retiring a signing-key generation is done only by removing
its `kid` from `active_kids`: every credential signed by it is refused from
the next accepted document on (within the same bound), even while the public
key is still in the JWKS; a later JWKS drops it. A JWKS of `{"keys": []}`
denies every signed credential.

### 7.6 Direct-path admission limits

SDK and CLI traffic reaches the engine directly, not through a host that could
meter it, so the perimeter bounds what one key and one deployment can ask for.
After authentication and before the spend lease and routing, for every
request including one to an unknown path:

| Limit | Starting value |
| --- | --- |
| Requests per credential (`jti`) | 120 per minute, burst 30 |
| In flight per credential | 8 |
| Requests per deployment | 600 per minute, burst 150 |
| In flight per deployment | 32 |

The four numbers are settings (`REMEMBERSTACK_SELFHOST_API_ADMISSION_KEY_PER_MINUTE`,
`…_KEY_IN_FLIGHT`, `…_DEPLOYMENT_PER_MINUTE`, `…_DEPLOYMENT_IN_FLIGHT`);
nothing else about admission is configurable. Each burst is derived, not set:
a quarter of its per-minute rate (15 seconds of traffic), which gives the 30
and 150 above.

- **Mechanism.** A token bucket per credential and one per deployment (a
  counter refilled at the rate up to the burst; each request takes one token),
  and a counting semaphore for in-flight requests. The shared-secret bearer
  has no `jti` and is bounded by the deployment limits only, as is every
  request to a deployment with no auth perimeter. `GET /healthz` is
  exempt. A request is admitted only when every check passes, and a refused
  request consumes no token. A slot is released when the response finishes,
  fails, or the client disconnects, but not before any synchronous handler
  or dependency (such as the D74 barrier check) already running on a worker
  thread returns: a disconnect cannot stop that thread, so its work still
  counts as in flight.
- **Refusal.** `429`, error code `rate_limited` or `concurrency_limited`, and
  `Retry-After` in whole seconds (time until a token is available; 1 for an
  in-flight refusal). The SDK raises `RateLimited` with `retry_after` and does
  not retry by itself; MCP hosts return the same code as a tool error.
- **In process, per API replica.** Counters live in each API process's
  memory. Postgres-backed counters were rejected: a write on every request,
  contention on hot rows, and a slow database refusing healthy reads. With N
  API replicas the effective ceilings are N times the numbers; an operator
  running N replicas divides by N. The self-host profile runs one replica.
- Spend metering (D91), the spend lease and the D74 admission barrier are
  unchanged and apply after this check: authentication, then admission, then
  the spend hold, then routing. An unauthenticated or refused request never
  places a hold.

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
| Project | `project=` / `--project` | `REMEMBER_PROJECT` | `default_project` | the issuer's default for the key (§8.3) | — |
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

1. Call `GET {remember_project_endpoint}` with the key as bearer and
   `?project=<id or name>` when a project was given (omitted, the issuer
   answers with its default project for the key). The response is
   `{"project": "<id>", "name": "<name>", "api_url": "https://…"}`. The
   client checks that `project` is in the key's `projects` (any project
   passes for `"org:*"`) and that `api_url` is `https` (or loopback `http`),
   then talks to `api_url` directly.
2. **Cache and moved deployments.** The answer is cached per (issuer, key id,
   project) for 10 minutes (starting value). It is invalidated at once when a
   request to `api_url` fails with a connection error (DNS failure, refused or
   reset connection, TLS failure), `421 Misdirected Request`, or a `404` whose
   body is not the engine's error envelope. The client then re-resolves: if
   the `api_url` changed it retries the request once there; otherwise, or if
   resolution fails, it surfaces the original error. One retry, never a loop.
   The retry is safe for every catalogue call: reads have no side effects, a
   repeated identical ingest is a no-op (D55), and deletion is idempotent
   (D135).
3. Failure is explicit: an unknown project, a project outside the key, or an
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
- **A second login: journal, mint, replace, revoke.** Order (identical in the
  cloud design):
  1. durably write the **old** key's secret, issuer and key id to the
     existing pending-revocation journal (owner-only, atomic write, fsync of
     file and directory);
  2. mint the new key (device grant);
  3. atomically replace `credentials.json` with the new key (fsync of file
     and directory);
  4. revoke the old key from the journal through `revocation_endpoint`;
     remove the entry only when the issuer confirms (2xx, or `401`/`404`
     meaning already revoked).

  | Crash or failure after step | On disk | What the next CLI start does |
  | --- | --- | --- |
  | 1 | old file; journal holds the old key | the entry names the key still in `credentials.json`, so it is discarded, not revoked |
  | 2 | same; the new key exists only at the issuer | as above; the unstored new key is orphaned and is listed, and revocable, in the issuer's key list until it expires |
  | 3 | new file; journal holds the old key | revokes the old key from the journal |
  | 4 (revocation unconfirmed) | new file; journal holds the old key | retries the revocation; `401`/`404` counts as done |

  A failed step 2 or 3 (not a crash) discards the step-1 entry; a failed step
  3 also revokes the new key (journalling it if that fails). Every CLI start
  and `remember logout` retry journalled revocations and report them.
- `remember logout` revokes through `revocation_endpoint` and unlinks:
  2xx or already dead (`401`/`404`) → unlink, exit 0; 5xx or network
  failure → keep the file, exit 1; no file → exit 0. It also retries any
  journalled revocations.
- `remember switch <project>` sets `default_project` locally after resolving
  it once (§8.3) to prove the key covers it. No new credential is minted.
- `remember whoami` prints the issuer, key id, expiry, projects and
  permissions from the key's own claims, and the locally chosen default
  project. Only when the key has
  `account:read` and the issuer metadata has a `remember_account_endpoint` does it add the
  issuer's account view (a host call). It never calls the engine: a
  memory-only key gets the local claim summary and nothing more, and the
  engine has no identity endpoint.

## 9. Failure behaviour summary

| Situation | Behaviour |
| --- | --- |
| Host catalogue tool not served by the deployment at the same version | Omitted from `tools/list` (single target) or `tool_unavailable_on_target` (multi-deployment host); `remember doctor` names it |
| `project` sent to a single-target host | `project_routing_unavailable` |
| Bridge: no key | Start-up error pointing to `remember login` |
| Bridge: `401` from remote | JSON-RPC error, re-login hint; bridge keeps running |
| Bridge: cross-origin redirect | Error; key not sent |
| Bridge: stored key with a remote URL outside the issuer's `remember_mcp_endpoint` origin | Refuses to start; explicit key required |
| Bridge `--read-only`: remote tool not annotated `readOnlyHint: true` | Omitted and refused locally |
| Engine: revocation document with lower `seq`, other audience, or retired signer | Rejected; last accepted document kept; logged |
| Engine: credential signed by a `kid` absent from `active_kids` | `401` |
| Client: deployment moved (connection failure, `421`, non-engine `404`) | Re-resolve; retry once if the URL changed |
| Re-login: revocation of the old key unconfirmed | New key kept; old key journalled and retried |
| HTTP transport: non-loopback bind in front of an unauthenticated engine | Refuses to start |
| HTTP transport: bad `Origin` | `403` |
| Engine: wrong `aud`, not covering this deployment, wrong issuer, revoked | `401` |
| Engine: per-key or per-deployment admission limit reached | `429` with `Retry-After` |
| Engine: no revocation document accepted yet, or accepted one older than `min(exp, iat + S)` | Every signed credential `401`; shared secret unaffected |
| Engine: valid key lacking permission | `403 insufficient_scope` |
| Client: project resolution fails | `ProjectResolutionError` / exit 1; no localhost fallback |
| Stored key with an explicit foreign URL | Key not attached; message says so |
| Credential file not in the version-2 shape | Refused; "run `remember login`" |

## 10. Tests to add

Catalogue (`remember.mcp_tools`):
- the catalogue lists exactly the fifteen tools of §3.1; `examples.*` never appear;
- `render_tools_list` output is identical for the in-process server and
  engine mode for the same options (golden file);
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
- engine mode: a tool is rendered only at an equal served `tool_version`;
- engine mode refuses `project`;
- HTTP transport: session issuance and `404` on unknown session, `405` on
  `GET`, `Origin` refusal, bearer forwarded unchanged and never stored,
  loopback default, non-loopback refusal against an unauthenticated engine;
- bridge: verbatim relay of JSON and event-stream responses; unknown remote
  tools passed through; no `path` ingest; `404` session recovery; `401` message; cross-origin
  redirect refusal; `http` non-loopback refusal; key absent from all output;
  stored key refused for a non-advertised remote origin and accepted with an
  explicit key; `--read-only` refuses unannotated, `false` and unknown tools.

`remember setup`:
- the four entry shapes per harness; no literal key in any written file;
  probe uncertainty falls back to the stdio bridge; dry-run masks the key.

Perimeter (`signed_token_auth.py`):
- wrong `iss`, missing `permissions`/`kind`, unknown `kind`, `service` with a
  foreign `sub` → refused;
- the §7.2 table, row by row: each kind accepted with its exact claim set;
  a `session` accepted both with `src` (derived) and without it (browser),
  with `src` recorded in the audit context when present;
  refused for a missing claim, `aud = https://remember.dev/mcp`, a key `aud`
  naming another tenant, a `session` `aud` naming another deployment,
  `projects` of 21 ids, an empty list, a list without this project, a string
  other than `"org:*"`, `org` mismatch, `session` `projects` with two ids,
  `service` with a foreign `sub`;
- first revocation document accepted on a fresh deployment; heartbeat
  documents advance `seq`;
- admission: per-key and per-deployment rate and in-flight limits return
  `429` with a correct `Retry-After`; in-flight slots are released on
  success, error and client disconnect (not before the handler thread
  returns); unknown paths are counted; `/healthz` is exempt; no spend hold
  is placed for an unauthenticated or refused request;
- permission mapping table, including ignored `account:*` and refused unknown
  `memory:*`; no memory permission → `403`;
- revocation documents: lower `seq`, equal `seq` with different content,
  wrong `aud`, signer not in `active_kids` → rejected; persisted `seq`
  survives restart; with no accepted document, or a stale one (including a
  stale one loaded at start-up), every signed kind is refused and the shared
  secret still works; the worst case `r + S + leeway` holds with fetches
  failing; a `kid` dropped from
  `active_kids` is refused while still in the JWKS;
- audit actor ids `keycred:`/`browsercred:`/`dpcred:`.

Client:
- permission mapping: `memory:read` + `memory:ingest` without write refused;
- one resolver: the precedence table above for SDK and CLI, including the
  stored file, run as one parametrised test over both entry points; removed
  variable names have no effect; `CloudClient` no longer exists and
  `client.account` raises `AccountApiUnavailable` without an issuer;
- host cache: TTL expiry, invalidation on connection failure / `421` /
  non-engine `404`, single retry only when the URL changed;
- re-login: each row of the §8.4 crash table; `whoami` makes no engine call;
- host resolution: project outside `projects` refused, any project for
  `"org:*"`, non-https refusal, no localhost fallback for signed keys;
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
