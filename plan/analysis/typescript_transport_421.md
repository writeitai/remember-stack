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
[Undici's fetch source](https://github.com/nodejs/undici/blob/main/lib/web/fetch/index.js):
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
The public fetch injection remains available for proxy/custom-CA compositions,
but it is a caller-owned adapter contract: it must preserve manual redirects,
honor cancellation and transmit once per invocation. Ordinary Node fetch is not
a conforming injection because of its 421 behavior. The default adapter is the
reference implementation tested by actual server request counts.

This changes the proposed native-fetch default, not the D140 no-write-replay or
changed-host single-read-replay requirements. No extra runtime dependency is
needed. Node's TLS defaults apply; httpx proxy and SSL_CERT_FILE environment
semantics are not claimed.
