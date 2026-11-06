## Verdict: APPROVED

**Reviewed HEAD:** `707ede2ed064e8a69b6d46ce5fd3daeb6600fcc6` on `design/typescript-client-parity`. The working tree is clean. I compared it with my round-6 HEAD `1f980e4c` and the approved baseline `c2d302a6`.

All three blocking items and all four smaller items from round 6 are fixed, and the four documents agree with each other. Nothing remaining blocks implementing the amended default adapter. Five small items below should be fixed before merge. The first is the only one that is a real parity gap.

### Round-6 corrections, checked one by one

| Round-6 item | Where it is now | Result |
|---|---|---|
| **1. Shared lookups and close** | Design `:98-101`, `:340-350`; analysis `:49-55` | Fixed. Shared lookups run on separate process-owned agents, or on caller-owned agents or transports. Pending lookups are only shared between clients using the same transport objects. Completed successes are shared across transports. Closing a client cancels only that client's own waiters with `AbortError`. It never counts as a move or network failure and never refreshes or clears the shared cache. A closed client refuses later calls and never recreates its agents (`:89-91`). |
| **2. Proxy and CA route** | Design `:43-53`, `:79-91`, §7 row `:337`; inventory `:50`; analysis `:29-35`, `:57-63` | Fixed. The `fetch` option is gone everywhere. The constructor takes a `transport` (an SDK request interface) or borrowed `agents: {http?, https?}`; close never destroys borrowed agents. The SDK's own agents deliberately ignore environment proxies, including `NODE_USE_ENV_PROXY`. `NODE_EXTRA_CA_CERTS`, `--use-system-ca` and an explicit caller CA agent are all documented. The analysis now records the unchosen alternative, Undici's lower-level `request` API with `EnvHttpProxyAgent`. |
| **3. Total deadline** | Design `:213-219`, §7 row `:338`; analysis `:65-68` | Fixed. The design says plainly that this differs from httpx's per-phase timeout, gives the 50 MB at 5 Mbit/s example (about 80 s), says to raise `timeoutMs` for large uploads, and forbids replaying a write whose outcome is unknown. |
| **4. Socket reuse after a 421** | Design `:95-97`, test gate `:289` | Fixed. |
| **5. Idle connection limit** | Design `:93-95` | Fixed. Idle sockets retire after 4 s, which is below the engine's 5 s keep-alive. This is kept separate from the deadline for active requests. |
| **6. Wording** | Design `:199-208`, `:82-83` | Fixed. Every adapter is called at most once per write, and the SDK's own adapter sends exactly once. The design states that the SDK cannot detect a caller transport that sends twice. The re-send is attributed to the Fetch standard, with the `deleteDocument` example. Response buffering has no byte limit, the same as Python. |
| **7. Evidence and tests** | Analysis `:17`, `:42-47`; test gate `:289-290`; `decisions.md:6708` | Fixed. The source link is pinned to Undici v7.8.0, Node 22.0.0 is recorded, the tests cover both HTTP and HTTPS, and D140 links the analysis note. |

The inventory change from the approved baseline is only the one-line `typescriptOptions` addition. Commit `707ede2e` correctly puts back the key order that `af4191bc` had changed. The parity analysis marks its old transport section as superseded at `:234`. The delivery plan and the website contain no stale `fetch` option.

### Checks I ran

All on loopback with synthetic data. Scripts are in `/tmp/claude-421-repro-r7/`, and Node 22.0.0 was downloaded to `/tmp/node-v22.0.0`.

- **Node 22.0.0 two-send repro: confirmed independently.** It bundles Undici 6.11.1. Fetch with `redirect:'manual'` reached the server **twice** for a byte POST, a JSON POST, a GET, a DELETE and a bodyless POST, and once for a stream-body POST. `node:http` reached it exactly once in every case.
- **Source pinning.** Node 24.0.2 reports Undici 7.8.0. The 421 retry branch is at `lib/web/fetch/index.js:1641` in v7.8.0, and Node v22.0.0's bundled copy has the same branch. The only 421 retry in Undici 7.8.0's `lib/` is in fetch. Its lower-level client fails the request that was in flight when the connection closes and never re-sends it, so the analysis's Undici `request` claim holds.
- **4-second idle retirement works on Node 22.0.0 and 24.0.2.** With `new http.Agent({keepAlive:true, timeout:4000})`, a socket left idle for 4.5 s was replaced by a new connection. A **6 s active response still succeeded**, provided the adapter does not treat the request's `timeout` event as a failure.
- **421 retirement can be implemented, but the obvious code does not do it.** I checked by connection ID on the server side, for HTTP and HTTPS on both Node versions:
  - Destroying the socket in `res.on('end')` fails. Node puts the socket back in the pool before that listener runs, so the next request **reused** the connection, whether it came later or was already queued.
  - Overriding the agent's `keepSocketAlive` works for a later request but fails for a request **already queued** on the same agent.
  - Two techniques worked in every case and kept the 421 body: `res.prependListener('end', …destroy)`, and setting `req.shouldKeepAlive=false` in the response handler.
  - `req.reusedSocket` reported `false` for a queued request that was actually on the reused connection, so tests cannot rely on that flag.

### Non-blocking items (fix before merge)

1. **Low-Medium: an injected `client` now differs from Python in a way §7 doesn't record.**
   - The approved baseline said an injected client owns its own timeout. `:52-53` now says the constructor `timeoutMs` "still bounds injected calls".
   - Python ignores `timeout` when a `client` is injected (`src/remember/client.py:153-161`). Its `close()` also does nothing for an injected client, so later calls keep working. TypeScript refuses calls after close (`:89-91`).
   - §7 says anything missing from its table must match Python, so both differences need a clause in the timeout row (`:338`) and the close row (`:324`), each with a test. The alternative is to go back to the baseline wording.
2. **Low: make the test gates at `:287-290` specific enough to catch the traps above.**
   - Retirement after a 421 must be tested with a request already queued on the same agent, and checked by connection identity on the server, not by `reusedSocket`.
   - Idle retirement must also show that an active response slower than 4 s still succeeds.
3. **Low (Rule 1): the design never explains why the SDK's own agents ignore proxy environment variables.**
   - `:83-85` and the analysis at `:52-53` state the choice but not the reason. One sentence would cover it, for example: Node's own `proxyEnv` agent option doesn't exist on Node 22.0, and an explicit caller agent makes proxy use deterministic.
   - "An operator-configured proxy agent" should name a real example that sends each request once, such as an `http.Agent` subclass.
4. **Low: the two injection options have the same type but different URL rules.** Both `client` and `transport` are typed `HttpClient` (`:46-47`, inventory `:50`). One takes relative URLs and supplies its own base URL and headers; the other takes absolute URLs with SDK headers. A distinct type name would let the compiler catch a mix-up. Also make the send-once, cancellation and return-redirects obligation at `:202-204` explicitly cover both modes. "Neither mode uses a Fetch-standard implementation" (`:48`) promises something about caller code that the SDK cannot enforce, so it should be stated as the caller's obligation.
5. **Nits:**
   - The D140 evidence link sits as a loose paragraph after the entry (`decisions.md:6708`). Move it into the "Companion design/evidence" bullet, and add a clause in the Decision bullet saying fetch is excluded and the default transport is a single-send Node HTTP adapter.
   - Replace "as an initial policy" (`:93`) with "a starting value to measure" (Rule 2).
   - Explain 421 in plain words where §4 first uses it: the 421 status code means a router or load balancer saying this connection cannot serve this host.
   - Name the error class a closed client throws on later calls.

### Scope

I made no edits to the source tree, GitHub, the CLA or npm. The only things I wrote were the scratch scripts and the Node 22.0.0 binary under `/tmp`.
SessionEnd hook [/Users/jpuc/.config/iterm2/cc-status] failed: Hook cancelled
