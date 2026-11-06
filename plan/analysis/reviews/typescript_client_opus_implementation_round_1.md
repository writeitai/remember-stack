## Verdict: CHANGES REQUESTED

**HEAD:** the PR head moved twice while I was reviewing (892d8313 → 076dec7c → `76ea84b6a4bc44675804a840265b4642b3e84fff`). I tested 076dec7c in depth from a pinned snapshot, reviewed the later diff, and re-ran the full suite plus every reproduction below at **76ea84b6**. Line numbers refer to 76ea84b6, under `packages/typescript-client/`.

At 76ea84b6 the package's own suite passes 294/294 locally on Node 22.0.0 and 24. Typecheck, the generated-contract drift check (`check:generated`), the Python parity check (`check:parity`), the packed ESM/CommonJS consumer check and 107 Python client tests also pass. The problems below are what those checks miss.

### Blocking

1. **Required checks are not green.**
   - **Windows:** neither Windows job reaches the tests. Node 22.0.0 fails in `npm ci` and Node 24 fails in `check:generated`. So the design's Windows requirement (§6.6) has no evidence at all.
   - **Live engine:** the candidate engine job fails. At 076dec7c it was the connectors assertion; at 76ea84b6 `factsContext` gets a 503 "model provider unavailable".
   - **Merge gates:** CLA is failing, and the design PR #502 is still open.
2. **Unbounded memory leak.**
   - `src/validation.ts:174` hands Ajv a new schema object on every call. `:154` does the same, because `tool()` deep-copies the tool schema each time (`src/catalogue.ts:26`, `:90`). Ajv caches compiled schemas per object, so its cache grows forever.
   - Measured after full garbage collection: about 3.7 KB and 2 ms per `searchDocuments` call, and about 5.7 KB per `validateArguments`/`callOpenQuery` call. Heap went from 18 MB to 35 MB over 6,000 calls. A plain Ajv test confirms the cause: 1,000 compiles left 1,008 cache entries.
3. **Large floats are rejected.** `src/json.ts:14` refuses any whole-number value at or above 2^53, even when the server wrote it as a float (`1e+20`, `1.152921504606847e+18`). Python returns these as floats; TS fails the whole query response with `NumericPrecisionError`. The design only adapts *integers*.
4. **The drift checks have holes** (the design §6.3 says every inventory entry must be implemented and new entries must fail CI).
   - Root and support exports in the inventory are never compared with what the TS package actually exports. Removing `resolveConnection`, `isLoopback` and `requireSecureUrl` from `src/index.ts` passes every check (`tests/parity.test.mjs:29-37` only checks method names).
   - TS defaults that never appear on the wire aren't pinned. Changing `timeoutMs` (30000), the readiness `timeoutMs` (1800000) or `pollIntervalMs` (15000) passes every check.
5. **Differences from Python that the design's adaptation table (§7) doesn't record.** Each was reproduced against Python:
   - `lookupRelations`, `graphNeighborhood` and `graphPath` refuse a `+02:00` timestamp (`src/catalogue.ts:83-86`); Python sends it unchanged.
   - When the issuer is down or returns 503, `account.whoami/get` raise `AccountApiUnavailable` (`src/client.ts:306`); Python raises `IssuerError`.
   - `mapError` maps the readiness `TimeoutError` to `internal_error` (`src/tool-errors.ts:60`); Python gives a retryable `transport_error`.
   - A FastAPI-style list error `detail` becomes the text `"[object Object]"` (`src/http.ts:174`); Python keeps the readable list.
   - A signed key with an insecure issuer raises `IssuerError` (`src/connection.ts:65`, outside the try); Python raises `ProjectResolutionError`.
6. **Type declarations fail for strict Node projects.** `dist/index.d.ts:1669` uses `HeadersInit` (from `src/http.ts:8`), which only exists in the browser (DOM) types. A strict project using `lib: ES2022` plus `@types/node` without `skipLibCheck` gets error TS2304. `scripts/check-package.mjs` hides this by compiling with the DOM types added.
7. **Docs are wrong in places** (agent findings, which I re-checked):
   - The build step `uv sync --extra dev` fails, because `dev` is a dependency group, not an extra (`website/.../typescript-sdk/page.mdx:23`, `README.md:13`).
   - `not-built-yet/page.mdx:60-61` still says there is no TypeScript client.
   - The new docs check loops over the top-level keys of `schemas.json` (`scripts/check_docs_truth.py:94`), so it checks zero models.
   - An unescaped `|` breaks the method table at `page.mdx:103`.
   - The page describes connector management as working, but no engine profile serves it.
   - The Python Windows credential-file change has no docs update.

### Lower severity

- **Windows with a signed key:** an explicit signed key with no URL fails at construction whenever a credentials file exists (`src/credentials.ts:52`, `src/connection.ts:43`). This contradicts the design's "explicit keys remain usable". Python behaves the same way.
- **Concurrent requests after a move:** a read that gets a 421 after another request has already re-pinned the client is not retried at the new host (`src/connection.ts:112`). Python has the same logic.
- **Live-engine script:** `scripts/live-engine.mjs:46` counts any 404 on a served route as success, so a wrong request path would pass.
- **Smaller differences from Python:**
  - The rate-limit `detail` text differs (`src/http.ts:152`).
  - `waitForReadiness({timeoutMs:0})` refuses instead of polling once (`src/http.ts:92`).
  - An empty `REMEMBER_CONFIG_DIR` points to a different credentials file than Python's.
  - Network errors lose their cause; the detail is just "network request failed" (`src/http.ts:82`).
- **PR description is stale:** it still says "Twenty Node tests" and that validation is pending.

### Verified correct

- **421 handling:** a write is sent exactly once, and the connection is retired after a 421 (checked by counting raw TCP connections on Node 22.0.0 and 24).
- **No write replay** in Python either (also checked at the TCP level).
- **Credential files:** TS matches Python on nine validation cases, and reads files written by Python's own writer.
- **Security:** account paths refuse traversal in 13 encodings, and the API key never appears in six serialization paths.
- **Wire format:** request bodies for complex nested filters match Python.
- **Process exit:** an unclosed client doesn't keep the process alive.
- **Install claims** in the README and docs are honest about the package not being on npm.

### Scope

This PR builds no `@rememberdev/cli` or `@rememberdev/mcp` executables. The design (§1) and delivery plan (step 7) make them separate deliverables. If you need them before merge, that work is missing.

### Process incidents

- One script from my docs sub-agent ran without `REMEMBER_CONFIG_DIR` set, so the SDK tried to read your real `~/.config/remember/credentials.json`. It refused the file as "not a valid version-2 credential file" before any network call. Nothing was sent, and neither the agent nor I opened the file.
  - If Python accepts that file, TS has a compatibility bug. Please check it yourself, since I won't read it.
- The same agent briefly created a scratch file in the worktree's `website/` folder; it is deleted and `git status` is clean.
- I made no source, GitHub, CLA or npm changes. My scratch directories are `/tmp/tsrev`, `/tmp/tsrev2`, `/tmp/tsrev-scratch`, `/tmp/tsrev-mut` and `/tmp/tsrev-results`.
