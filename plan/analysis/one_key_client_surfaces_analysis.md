# One key, one tool catalogue — engine-side analysis (D136)

**Status:** analysis supporting D136. Non-binding; the binding contract is
[one_key_client_surfaces_design.md](../designs/one_key_client_surfaces_design.md).
**Date:** 2026-09-23.
**Companion:** the remember.dev side of the same product decision is designed
in `writeitai/ultimate-memory-cloud` (branch `design/one-key-one-mcp`, "one key,
one MCP endpoint"). That design owns organisations, members, billing, the
account tools, the OAuth authorization server and the hosted endpoint
`https://remember.dev/mcp`. This analysis covers only what the engine and the
`remember` Python package must provide.

## 1. The product decision this implements

On 2026-09-23 the owner approved one credential for every client surface of
remember.dev: the Python SDK, the `remember` CLI, hosted MCP and plain HTTP.
The credential is a **signed key**: a token whose contents (the *claims*) are
signed with the issuer's private key, so anyone holding the issuer's published
public keys can check it offline without asking the issuer. The claims say
which key it is, which user it acts for, which projects it covers (one,
several, or every project of an organisation), a default project, the
permission set (`memory:read`, `memory:write`, `account:read`,
`account:manage`), and when it was issued and expires. A revocation list
withdraws keys before they expire.

The same decision fixes one hosted MCP endpoint (`https://remember.dev/mcp`)
and one set of memory tools, *defined once in the open-source `remember`
package*, which that endpoint imports. Every memory tool takes an optional
`project` argument. Nobody depends on the current credential shapes, so no
migration or backward compatibility is owed. The decision itself is not
re-opened here; this document works out what it requires from the engine and
from the `remember` package, and why each engine-side choice was made.

## 2. Current state (2026-09-23, `main` at 12e9990e)

### 2.1 Three sources of MCP tool definitions inside this repository

An MCP server advertises its tools in `tools/list`: each tool has a name, a
plain-language description the agent reads to decide when to call it, and an
input schema (a JSON Schema describing the arguments). Today the memory tools
come from three places:

| Tools | Defined in | How a server gets them |
| --- | --- | --- |
| `ingest`, `pipeline_readiness` | `src/remember/mcp_memory_tools.py` (static dicts, argument parsers, error mapping) | imported |
| the seven SQL query tools (`query_sql`, `explain_sql`, `describe_query_space`, `search_query_space`, `list_saved_queries`, `describe_saved_query`, `run_saved_query`) | `src/remember/query_sandbox/mcp_tools.py` | imported, but only advertised after `GET /query/space` returns a recognisable discovery identity |
| the four assured operations (`resolve_entity`, `facts_context`, `claims_and_sources_context`, `combined_context`) | engine registry (`src/rememberstack/spine/assured_operations.py`, `src/rememberstack/model/assured_operations.py`) | fetched at runtime from `GET /operations`; a 404 is treated as "no operations" |

`delete_document` is being added by PR #456 (D135) in
`remember/mcp_memory_tools.py`.

The local in-process server (`src/rememberstack/surfaces/mcp.py`,
`OperationMcpServer`) and the remote server (`src/remember/remote_mcp.py`,
`RemoteOperationMcpServer`, used by `remember mcp`) both assemble these three
sources. `src/rememberstack/surfaces/mcp_memory_tools.py` and
`surfaces/remote_mcp.py` are re-export shims. So *inside* this repository the
definitions cannot drift between the two servers — but there is no single
module a third host can import to get all of them, and the three gating rules
(always / 404-means-absent / discovery-identity probe) are separate code paths.

### 2.2 The hosted server drifted because it had its own copies

remember.dev shipped hosted MCP on 2026-09-22 (cloud commit `a1b614fd`,
`https://remember.dev/app/api/mcp`). Its tools are a hand-written `TOOLS`
dictionary in `ultimate_memory_cloud/mcp_routes.py`. It first shipped with the
stale v0.16 operation names, and on current cloud `main` it still differs:

- `ingest` takes only `text` and `filename`, and the forwarder
  (`mcp_forward.py`) always sends `mime=text/markdown`. The engine tool also
  has `content_base64`, `mime`, `title`, the lineage fields
  (`source_kind`/`source_ref`/`source_modified_at`/`versioning_mode`/`source_version_ref`)
  and the long operational description (asynchronous pipeline, readiness
  polling).
- descriptions and argument bounds are rewritten by hand;
- the seven SQL query tools are absent;
- one connection is one project ("add the server twice").

Every one of these is the kind of drift a shared import removes.

### 2.3 `remember mcp` is stdio-only and engine-only

`src/remember/remote_mcp.py` implements a minimal newline-delimited JSON-RPC
loop over stdin/stdout (*stdio transport*: the agent starts `remember mcp` as a
child process and talks to it over its standard streams). It always calls an
engine's HTTP API through `MemoryClient`. There is no Streamable HTTP transport
(the MCP transport where a server is reached at a URL over HTTP POST), although
`plan/designs/unified_remember_distribution_design.md` §2 and §3.2 list
"stdio & Streamable HTTP" without specifying it. There is no way to point
`remember mcp` at a remote MCP server.

### 2.4 Credentials and login

- `src/remember/credentials.py`: `credentials.json` version 1 carries a legacy
  flat shape (`api_url`, `token_host`, `access_token`, `token_prefix="umc_dp"`)
  *and* the D108 structure (`control_plane` session, `projects` map of
  per-project data-plane tokens, `active_project_id`). Defaults hard-code
  `https://api.remember.dev`, which is not a live host; remember.dev's API is
  served at `https://remember.dev/app/api` today (`client.py`
  `DEFAULT_BASE_URL`).
- `src/remember/device_login.py`: a JSON variant of the device grant (D92 §6.2)
  with an `audience` of `deployment` or `control`; the result is either a
  `umc_dp_…` deployment token bound to one project or a `umc_cp_…` control
  token. Revocation uses `/v1/api-tokens/self` or `/v1/control-tokens/self`
  depending on the prefix.
- `src/remember/cli.py`: `remember login --audience deployment|control`
  (alias `--control-plane`), `logout`, `whoami`, `switch`; `_resolved_token_host`
  defaults to `https://api.remember.dev`.
- `src/remember/setup.py`: always writes a local stdio entry (`<launcher> mcp`)
  into each harness; for cloud it requires a stored data-plane URL or
  `--url https://<project>.dp.remember.dev`, and it defaults the token host to
  `https://api.remember.dev`.

### 2.5 The SDK and the CLI resolve the environment differently

| Setting | SDK (`remember.Client`, `client.py`) | CLI (`CliClientEnv`, `credentials.py`) |
| --- | --- | --- |
| Credential | `api_key=` → `authorization=` → `settings` → `REMEMBER_API_KEY` → `REMEMBERSTACK_API_AUTHORIZATION` → then, through `ClientSettings`, `REMEMBER_API_KEY`/`REMEMBER_TOKEN`/`REMEMBER_API_AUTHORIZATION`/`REMEMBERSTACK_API_AUTHORIZATION` | `--token` → `REMEMBER_TOKEN`/`REMEMBER_API_AUTHORIZATION`/`REMEMBERSTACK_API_AUTHORIZATION` → credential file. **`REMEMBER_API_KEY` is not read.** |
| URL | argument → settings → `REMEMBER_DATA_PLANE_URL` → `REMEMBER_API_URL` → `REMEMBERSTACK_API_URL` → `http://127.0.0.1:8000` | `--api-url` → same three variables → credential file |
| Account client | `CloudClient` reads `REMEMBER_CLOUD_TOKEN`, `REMEMBER_CLOUD_ORG`, `REMEMBER_CLOUD_URL` | same |

So `export REMEMBER_API_KEY=…` — the variable the public docs teach — works in
Python and silently does nothing for `remember ingest`. Nine variable names
exist for two settings, and two further names for a third credential.

### 2.6 The engine perimeter already verifies signed tokens

`src/rememberstack/adapters/managed/signed_token_auth.py` (`SignedTokenAuth`)
verifies an EdDSA/Ed25519 JWT against a JWKS (a JSON document listing public
keys by id, `kid`) passed inline in `REMEMBERSTACK_SELFHOST_API_SIGNING_KEYS`,
with revoked ids in `REMEMBERSTACK_SELFHOST_API_REVOKED_CREDENTIAL_IDS`
(`profiles/selfhost.py`). It:

- strips exactly one `umc_dp_` routing prefix;
- requires `aud`, `exp`, `iat`, `nbf`, `jti`, `sub`, `scope`, with **exactly
  one** audience equal to this deployment's id (`strict_aud`), so one token
  can never be valid at two deployments;
- does not check `iss`;
- maps `scope` ∈ {`read`, `ingest`, `write`} to `PerimeterScope`
  (`model/auth.py`); an unknown scope is refused;
- derives `CredentialKind` from the subject (`dpcred:<jti>` = machine,
  anything else = browser credential for a person).

`src/rememberstack/surfaces/route_scope.py` maps every route to the scope it
needs (reads enumerated, `POST /ingest` = ingest, everything else = write;
`POST /operations/{name}` decides from the descriptor's `mutates`). The port is
already the right *shape* for the one key; its claim contract is not (single
audience, `scope` string, no issuer, a commercial prefix).

## 3. Questions and alternatives

### 3.1 Where the tool definitions live

- **A. Each host keeps its own copy, guarded by a contract test.** Rejected.
  The hosted server is in another repository and deploys on its own schedule;
  a test there can only compare against whatever `remember` version it pins,
  and hand-edited copies are exactly how the v0.16 names shipped.
- **B. Every host renders dynamically from the deployment (`GET /operations`
  and friends).** Rejected as the source of truth. A multi-project host must
  answer `tools/list` *before* a project is chosen (the `project` argument is
  on the tool, not on the connection), so it cannot ask one deployment. Only
  four of the fourteen tools are served dynamically today anyway, and three
  discovery mechanisms would remain.
- **C. A static JSON artifact published with each release.** Rejected: the
  argument parsers, error mapping and path-ingest guard are code; a schema
  file alone reintroduces a second implementation of them in every host.
- **D. One importable, versioned public module in the `remember` package.**
  Chosen. It is dependency-light already (the base wheel carries only
  `httpx`, `pydantic`, `pydantic-settings`), so any Python host can import it.
  The engine's registry and `GET /operations` then *project* the catalogue
  rather than define it, and deployments advertise which catalogue tool
  versions they serve so a host can hide tools a given deployment cannot run.

### 3.2 Static definitions versus the dynamic `GET /operations`

`GET /operations` carries fields that are engine concerns (result schema,
result contract, output grain, answer intent, implementation-plan hash) as
well as the agent-facing name, description, input schema and `mutates`. If
both the catalogue and the registry wrote the agent-facing fields, they would
drift. The resolution: the catalogue owns the agent-facing fields; the
registry imports them and adds the engine fields; a test asserts byte
equality. `GET /operations` stays (the SDK and `remember operations` use its
result contracts), but no host renders tools from it.

Version skew is real because the `remember` client is installed separately
from the engine. Two options were considered: render a tool only when host
and deployment report the same per-tool version, or advertise a compatible
range per tool. The first is enough, because the per-tool version rises only
on an incompatible change: additive changes keep the number and therefore
hide nothing. A range would add machinery with no case that needs it.

### 3.3 The `project` argument without a control plane in the engine

D50 makes one engine one trust domain serving one deployment, and CLAUDE.md
Rule 3 forbids assuming a multi-tenant control plane. The options:

- **Engine accepts `project` and routes.** Rejected: the engine would need a
  notion of other deployments.
- **One MCP connection per project** (current hosted behaviour). Rejected by
  the product decision (one entry, one key).
- **A routing argument that the *host* resolves and removes.** Chosen. The
  catalogue defines the argument's schema and wording once, so every host
  words it identically; the engine's own API never sees it and keeps refusing
  unknown arguments. A local `remember mcp` that serves one engine refuses a
  call that names a project (silently ignoring it could answer from the wrong
  memory). A multi-engine mode for `remember mcp` was considered and left
  out: no stated need, and the hosted server already routes by project.

### 3.4 How agents that cannot use a remote MCP server get the one key

Some harnesses accept only locally launched (stdio) servers, and some remote
configurations cannot carry a header from an environment variable.

- **Require remote MCP everywhere.** Rejected; it excludes those harnesses.
- **`remember mcp` resolves the project's data-plane host from the key and
  calls the engine directly.** Viable and it keeps memory content off the
  shared account service, but it serves a different tool set (no account
  tools) and duplicates the host's project routing in the client. It remains
  available: setting an explicit engine URL selects exactly this mode.
- **A stdio→remote bridge.** Chosen as the default for hosted keys: `remember
  mcp` relays JSON-RPC between stdio and the remote endpoint, adding the key as
  a bearer header. The agent sees exactly the hosted tool set. (Rewriting the
  remote `ingest` tool to accept local paths was considered and left out:
  agents already send bodies as `text` or `content_base64`.)

The bridge is generic: any remote MCP URL plus any bearer key, not a
remember.dev special case.

### 3.5 Where signed keys are verified

- **Token exchange**: the SDK trades the key at the account service for a
  short-lived per-deployment credential. Rejected for the SDK/CLI path: it puts
  the account service on every client's start-up path and exposes a second
  credential type to callers — the split the decision removes. (The hosted MCP
  server may still derive per-call credentials internally; that is the cloud's
  implementation detail and uses the same port.)
- **The engine verifies the key itself.** Chosen. The existing signed-token
  port changes its claim contract: an issuer check, complete claim sets per
  credential `kind`, an `aud` that says what kind of place the credential is
  for (`org:<tenant>` for keys, this deployment's id for derived session
  credentials — so an OAuth token minted for the hosted MCP server, whose
  `aud` is that server, can never be replayed at a deployment), a `projects`
  coverage claim (an explicit list of at most 20, or `"org:*"`), and a
  `permissions` array. The engine learns no organisation or member model: it
  compares the key's `org` and `aud` with one opaque "issuer tenant" id the
  operator configures on the deployment, so a key can cover projects created
  after it was minted. (Earlier drafts used operator-configured group
  audiences, then coverage without `aud`; both were replaced by this shape,
  agreed with the cloud design.)

The cost is honest: a leaked multi-project key is valid at several
deployments until revoked, which the old strict single audience prevented.
The mitigations are the revocation document with a bounded staleness (§3.6),
per-key permissions, and single-project keys for callers who want the old
containment.

A direct path also means no host meters request volume, so the perimeter
enforces per-key and per-deployment rate and in-flight limits (429 with
`Retry-After`). Counters are kept in process per API replica rather than in
Postgres: a shared counter would add a database write to every request and
make a slow database refuse healthy reads. The cost — limits multiply by the
replica count — is documented and handled by configuration.

### 3.6 Revocation freshness

Today the revocation list is static configuration pushed by the operator. A
long-lived user key needs faster withdrawal, and an unsigned list can be
replaced by an older one. The design therefore accepts revocation only as a
signed document bound to the deployment's audience and carrying a strictly
increasing sequence; the engine persists the last sequence it accepted, so
neither a replayed older document nor a restart can roll revocation back. A
maximum age measured from the document's issue time gives a true worst case
(a revoked key stops within that age plus clock leeway, even if every fetch
fails), and short-lived credentials whose whole lifetime is shorter than the
bound do not depend on revocation. The document's `active_kids` list makes key
rotation an actual revocation: credentials signed by a retired generation are
refused even while the old public key is still published.

### 3.7 Authorisation for the self-hosted HTTP transport

When `remember mcp` serves Streamable HTTP in front of one engine, it can
either hold its own engine credential (like stdio) or forward each caller's
bearer to the engine. Holding its own credential would make every caller
act with that one credential's authority and hide who acted from the engine's
audit. The MCP authorization guidance warns against *token passthrough* when
the MCP server is a separate resource with its own authority; here the HTTP
listener is only a transport adapter in front of exactly one engine, the
bearer's audience is that engine, and the engine perimeter remains the only
authority. The design therefore forwards the caller's bearer and holds no
credential of its own, binds to loopback by default, validates `Origin`, and
refuses a non-loopback bind in front of an engine that accepts
unauthenticated requests.

### 3.8 Login stores one key

The current device flow asks for an audience and stores one of two token
types. With one key, login asks for nothing but the issuer, and the issuer's
consent page decides projects and permissions. Standard OAuth metadata
(RFC 8414) lets the CLI discover the device-authorization, token and
revocation endpoints instead of hard-coding paths, which is what makes the
flow usable against any issuer and removes the hard-coded
`https://api.remember.dev`. The endpoint paths themselves belong to the cloud
design; this repository names only the discovery mechanism and the default
issuer identifier `https://remember.dev`.

### 3.9 Environment variables

The divergence in §2.5 is fixed by one resolver used by both the SDK and the
CLI, and one variable per setting: `REMEMBER_API_KEY`, `REMEMBER_API_URL`,
`REMEMBER_PROJECT`, `REMEMBER_MCP_URL`, `REMEMBER_ISSUER`,
`REMEMBER_CONFIG_DIR`. Both read the stored credential file after the
environment. (A first draft kept the file CLI-only for D92's reason — an
embedded library must not pick up a machine credential and send it to a host
its caller never named. Review settled that one precedence for both is the
better UX and that the reason is met more precisely by the stored-key origin
rule: a stored key goes only to its issuer, its advertised MCP endpoint, its
recorded URL, or an issuer-resolved deployment. `CloudClient` is folded into
`remember.Client` as an account namespace for the same one-client reason.)
The aliases are removed rather than kept, because
nobody depends on them and each alias is a place where the two resolvers
could diverge again.

## 4. Library-boundary check (CLAUDE.md Rule 3, D60/D61)

- The engine gains no organisation, member, billing or project-directory
  model. Project identifiers and the tenant id are opaque strings compared
  for equality; permissions it
  does not understand (`account:*`) are ignored because they authorise other
  services.
- Every new engine input is operator configuration on an existing declared
  port (the auth perimeter): issuer identifier, key set, revocation source,
  this deployment's project identifier and issuer-tenant id, admission
  limits. Any operator can run a conforming issuer; a self-hoster
  who runs none keeps the shared-secret bearer.
- The `project` argument is resolved by hosts; the engine API never sees it.
- The client package's `--cloud` convenience names one default issuer
  (`https://remember.dev`). That is a default, not a dependency: every protocol
  step goes through discovery and `--issuer` selects any other issuer, and
  nothing that determines correctness depends on it.
- The bridge is a generic MCP relay; it knows nothing about remember.dev's
  account tools and passes them through unchanged.

## 5. Sources

- MCP specification 2025-11-25, "Transports" (stdio, Streamable HTTP,
  `Mcp-Session-Id`, `Origin` validation) and "Authorization" (RFC 8414
  metadata, token-passthrough guidance): https://modelcontextprotocol.io/specification/2025-11-25
  (read 2026-09-23).
- RFC 7519 (JWT), RFC 7517 (JWK), RFC 8037 (EdDSA in JOSE), RFC 8414 (OAuth
  authorization server metadata), RFC 8628 (device authorization grant),
  RFC 7009 (token revocation).
- Harness support for remote MCP entries changes frequently; the setup
  design therefore probes per harness and falls back to the stdio bridge
  rather than relying on a fixed support table.
