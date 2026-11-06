## Verdict: CHANGES REQUESTED

**Reviewed HEAD:** `1f980e4c620bf723bbf48a7e24bcfec0c4609e48` (branch `design/typescript-client-parity`), compared against the approved baseline `c2d302a6`.

The amendment's main direction is sound and I'd approve it on its own. The default transport becomes Node's low-level `http`/`https` request API, sends each request exactly once, follows no redirects and retries nothing automatically, and treats ordinary Node fetch as non-conforming. I reproduced the motivating 421 behaviour independently. Fetch's automatic re-send on 421 comes from the Fetch standard itself, not a Node bug, so manual redirects can't turn it off.

Three gaps should be closed before implementing, because the amendment either creates them or makes them concrete. Four smaller items follow.

### Blocking

**1. Medium-High: closing one client can break other clients' shared host lookups.**
- **Where:** `plan/designs/typescript_client_design.md:73-76` and `:305-308`; `plan/analysis/typescript_transport_421.md:27-28`.
- **The conflict:** the amendment gives each client its own agents, and close destroys "those SDK-owned agents and in-flight work". But §7 keeps one process-wide cache for issuer and project lookups, promising that "each waiter may abort without cancelling shared work or poisoning other clients' result". The design never says which client's connection carries a shared lookup.
- **Evidence:** the current implementation sends shared lookups through the client that started them (`src/connection.ts:70-87`, `src/issuer.ts:104-120` in `/tmp/remember-ts-client`).
- **Failure scenario:** a web server builds one client per incoming request. Client A starts the first project lookup and B joins it. A's request is cancelled, so A calls `close()`. That destroys A's agent and kills the lookup in flight. B then fails with a network error, and the shared host-cache entry is cleared as if the deployment had moved.
- **Requested fix:**
  - Run shared in-flight lookups on a connection no single client's close can destroy, such as a process-owned SDK agent whose idle sockets don't keep the process alive.
  - Let clients join an in-flight lookup only if they use the same transport. Completed results can still be shared. Otherwise a client behind a proxy can inherit a failure caused by a client connecting directly.
  - Classify anything ended by close as cancellation (`AbortError`), never as a network failure. It must not clear the host cache or trigger a re-lookup.
  - A closed client refuses new calls and never recreates its agents.

**2. Medium: there is no conforming way to use a proxy, and per-client agents silently ignore Node's proxy settings.**
- **Where:** `:76` ("`fetch` injection supports tests/proxies"), `:175-182` and the §7 row at `:303`.
- **Why every fetch is ruled out:** the re-send on 421 is part of the Fetch standard, so every conformant fetch has it. I confirmed this on Node 26.7.0: fetch routed through Node's own environment proxy (`NODE_USE_ENV_PROXY=1`) delivered a bodyless DELETE twice (2 proxy hits, 2 origin hits). Requests without a body (GET, DELETE) can't use the stream-body workaround. So the injection the design names for proxies has no off-the-shelf conforming implementation. Callers would have to hand-write a fetch-shaped shim around `http.request`. Meanwhile `fetch: globalThis.fetch` still type-checks.
- **The silent opt-out:** an SDK-created `new http.Agent({keepAlive: true})` ignores `NODE_USE_ENV_PROXY`. On Node 26.7.0 a request through the global agent went via the proxy; one through a custom agent did not. It only goes via the proxy when the agent is built with `proxyEnv` (available from Node v22.21.0 and v24.5.0).
- **Parity:** Python's `transport: httpx.BaseTransport` (inventory `__init__`) never re-sends on 421. It is the conforming proxy route, and it honours `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`.
- **Requested fix:**
  - Provide a conforming proxy/CA route that reuses the SDK's own single-send adapter. For example, accept caller-owned `http.Agent`/`https.Agent` objects (which close leaves alone), or an SDK-defined transport interface instead of a fetch-typed option.
  - Decide explicitly what the SDK's own agents do about proxies: pass `proxyEnv` (httpx parity, version-dependent), follow `NODE_USE_ENV_PROXY` only, or never use a proxy.
  - Document `NODE_EXTRA_CA_CERTS` / `--use-system-ca` as the custom-CA route. I verified `NODE_EXTRA_CA_CERTS` works with a custom `https.Agent`: the request was refused without it and returned 200 with it.
  - Add the unchosen dependency option to the analysis: undici's lower-level `request` API with `EnvHttpProxyAgent`.

**3. Medium: the 30-second deadline semantics are an undocumented difference from Python.**
- **Where:** `:75` ("response buffering under the operation deadline"), `:186`, and §7 `:282-286`. §7 says anything not in the table must match Python.
- **The difference:** Python's `httpx.Client(timeout=30.0)` (`src/remember/client.py:166`) applies 30 seconds to each phase (connect, write, read, pool). That limits waiting between data chunks, not the total. The TypeScript design caps the whole call, including routing, upload and response body.
- **Failure scenario:** a 50 MB PDF ingest over a 5 Mbit/s uplink takes about 80 seconds. It succeeds in Python. In TypeScript it fails at 30 seconds, and if the upload had already landed the write's outcome is unknown.
- **Requested fix:** either switch to per-phase idle timeouts, or add a §7 row for the total-deadline adaptation with a test and guidance to raise `timeoutMs` for large ingests.

### Non-blocking (please address)

**4. Low-Medium: a connection that got a 421 is reused.** In my repro the next `node:http` request after a 421 ran on the same socket (`reusedSocket=true`). The engine itself never sends 421 (grep of `src/`), so it comes from routers or load balancers. There it means "this connection can't serve this host", and the Fetch standard's remedy is a fresh connection. The default adapter should destroy the socket after a 421 instead of returning it to the pool.

**5. Low: idle connection reuse is unbounded.** A `keepAlive` agent with no `timeout` keeps idle sockets indefinitely. The Node source also ignores the server's `Keep-Alive` timeout hint when the agent timeout is 0. The engine runs uvicorn with its default 5-second keep-alive (`src/rememberstack/profiles/selfhost.py:1580`) and sends no hint. httpx expires idle connections after 5 s and undici after 4 s. Because writes are never replayed, a server closing a stale connection becomes a user-visible failure of unknown outcome. Specify an idle limit below 5 s as a starting value to measure. Idle sockets already don't keep the process alive (Node unrefs them; source checked).

**6. Low: tighten the wording at `:175-182`.**
- Say exactly who guarantees what. Every adapter gets at most one adapter call per write. The default adapter also sends exactly once on the wire. For injected adapters, single sending is the caller's obligation and the SDK can't detect violations.
- Attribute the re-send to the Fetch standard (it applies to all conformant fetch implementations), with a one-line example: one `deleteDocument` means one DELETE reaches the server, even after a 421.
- State whether "bounded" buffering includes a byte cap. Python has none.

**7. Low: evidence and test gates.**
- The analysis measured only Node 24.0.2, but the minimum supported version is Node 22, which nobody measured. I added 26.7.0.
- It links undici's unpinned `main` branch. Pin it to the bundled undici version (7.8.0) or the Fetch standard section.
- The §6.4 request-counting tests should also cover HTTPS, Node 22, connection retirement after a 421, and close during a shared lookup.
- D140's evidence list in `decisions.md` could link the new analysis note.

### What I confirmed is coherent
- The rules against replaying writes and allowing one read retry only when the host URL changed are unchanged and still match D136 §8.3. The read-retry tests become satisfiable once each call is sent once.
- Engine requests still refuse redirects, and issuer/account requests still follow at most three same-origin redirects. Each redirect hop is a separate SDK adapter call.
- The lifetime rule "close only touches SDK-owned resources" matches §2 `:67`.
- No phasing language, no library-boundary issues, and no docs-site obligation for this design-only change.

### Checks performed
- Read `CLAUDE.md`, the design-corpus skill, the full design, the new analysis note, the D136 note and §8.3, the D140 entry, the parity analysis's runtime section, the inventory constructor entry and the Python client's constructor/close/replay code.
- Read the evidence test `http.test.mjs` and `/tmp/remember-ts-tests.log`: 124 passed, 7 failed. Six are the 421 cases (the first host received 2 requests, the test expected 1). The seventh is unrelated to transport: the method-coverage test fails because the implementation exposes `assertOpen` as a public method.
- Reproduced on loopback with synthetic data only (scripts in `/tmp/claude-421-repro/`):
  - On Node 24.0.2 and 26.7.0, fetch with `redirect:'manual'` reached the server twice after a 421 for a POST with a byte body, a POST with a JSON string, a GET, a DELETE and a POST with no body. A POST with a stream body reached it once.
  - `node:http` with a keep-alive agent reached it exactly once in every case, including on a reused socket.
  - Also verified: the proxy behaviour, the `NODE_EXTRA_CA_CERTS` behaviour and the Node agent source code cited above.
- Made no edits to either worktree, no GitHub writes, and no npm or CLA actions. While I was reviewing, someone else was modifying the implementation worktree at `/tmp/remember-ts-client`, so my reading of its `http.ts` reflects the fetch-based version as it was then.
