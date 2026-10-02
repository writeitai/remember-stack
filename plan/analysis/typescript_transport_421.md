# TypeScript transport evidence: HTTP 421 replay

Status: non-binding implementation research for D140. Retrieved and measured
2026-10-02. This note supplies evidence for the transport amendment; the accepted
contract remains `plan/designs/typescript_client_design.md`.

The initial implementation used Node 24.0.2 global `fetch` with
`redirect: 'manual'`. A loopback HTTP server returned 421 after recording the
complete body. Five separate SDK calls (ingest, document deletion, connector
creation, connector pause and an assured operation) each reached that server
twice even though the SDK never called its adapter again. A read-only query POST
also reached the first host twice before the SDK's single changed-host replay.
The executable tests are `packages/typescript-client/tests/http.test.mjs` in
implementation PR503; their server-side request counts detect transport replays.

This matches the Fetch HTTP-network-or-cache behavior implemented in
[Node 24.0.2's bundled Undici 7.8.0 fetch source](https://github.com/nodejs/undici/blob/v7.8.0/lib/web/fetch/index.js):
a 421, on the first connection fetch, repeats when the body is absent or has a
reusable source. Manual redirects do not control this branch. A stream body can
avoid that condition for some POSTs, but GET requests cannot carry such a body,
and a caller's fetch adapter can have additional retry behavior. The stream
workaround does not satisfy the complete bounded-replay contract.

Use Node's low-level [HTTP request API](https://nodejs.org/api/http.html#httprequesturl-options-callback)
and HTTPS equivalent for the default adapter. Create one request per SDK adapter
call, buffer its response under the SDK's operation deadline, and never add
transport retries or automatic redirects. Per-client HTTP/HTTPS agents provide
connection reuse; close destroys only those SDK-owned agents and in-flight work.
Replace the fetch-shaped injection with an SDK request transport interface and
optional caller-owned Node HTTP/HTTPS agents. Supplied agents reuse the SDK's
single-send adapter for custom proxy/CA configuration and are never destroyed
by client close. Caller transports must return redirects, honor cancellation
and transmit once per invocation. Fetch-standard implementations are unsuitable
because of their 421 behavior; no legacy fetch option remains. The default adapter is the
reference implementation tested by actual server request counts.

This changes the proposed native-fetch default, not the D140 no-write-replay or
changed-host single-read-replay requirements. No extra runtime dependency is
needed. Node's TLS defaults apply; httpx proxy and SSL_CERT_FILE environment
semantics are not claimed.

Opus independently reproduced two arrivals on Node 24.0.2 and 26.7.0 for byte/JSON
POSTs, GET, DELETE and bodyless POST, and one arrival using node:http with a
keep-alive agent. The supervising agent also reproduced two arrivals on the
minimum Node 22.0.0 (bundled Undici 6.11.1). Node 24.0.2 reports Undici 7.8.0.
The first amendment review is recorded in
`plan/analysis/reviews/typescript_client_opus_design_round_6.md`.

Shared discovery cannot borrow the first client's owned engine agent: closing
that client would cancel other waiters. Use process-owned default discovery
agents, or borrowed caller-owned agents/transports. Pending work is grouped by
transport identity; completed successful mappings remain issuer/key/project
cache entries. Default owned agents explicitly ignore environment proxies;
NODE_EXTRA_CA_CERTS remains a Node TLS mechanism. Active operation deadlines
and four-second idle socket retirement are separate policies.

The unchosen dependency alternative is Undici's lower-level request API with
EnvHttpProxyAgent. It avoids Fetch-standard status retries when used without
retry interceptors and offers proxy environment support. It adds a runtime
transport dependency and its own lifecycle/version surface; the dependency-free
Node adapter with borrowed-agent/transport injection satisfies the chosen
contract. A Fetch stream-body workaround remains rejected because bodyless
methods still replay and ownership/cancellation semantics are not repaired.

The total HTTP operation deadline is an intentional language adaptation from
httpx's per-phase idle timeout, now named in D140's adaptation table. Large or
slow ingests need a raised timeoutMs; automatic write replay remains forbidden
when expiry leaves the outcome unknown.
