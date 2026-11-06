## Verdict: CHANGES REQUESTED at `d10d94860fa54aea4598113417143aa174974e2a`

Both round-3 blockers are fixed, and the new tests genuinely catch regressions. But I found one more concrete Python-fidelity mismatch in the same issuer status handling. It isn't new in this commit: it has been there since `bf8272a6`, and rounds 1–3 missed it.

### Remaining blocker: issuer metadata errors report the HTTP status, Python reports 0
- **Where:** `packages/typescript-client/src/issuer.ts:117` raises `IssuerError({statusCode:response.status,…})` for any metadata response that isn't 200. Python (`src/remember/issuer.py:288-290`) raises `IssuerError` without a status, so `status_code` is 0. Round 3's metadata row came from this same line. `d10d9486` only took 3xx off this path.
- **Effect:** both clients ran on identical synthetic responses (mock transports in both):

| Metadata response | Python | TypeScript |
|---|---|---|
| 201, 204, 401, 404, 500, via `fetchIssuerMetadata` | `IssuerError` (0) | `IssuerError` (that status) |
| same five, via `account.get` | `IssuerError` (0) | `IssuerError` (that status) |
| same, via project resolution | `ProjectResolutionError` (0) | `ProjectResolutionError` (0), matches |

  All other metadata branches match: malformed JSON, wrong issuer, invalid model, missing account endpoint, connection error.
- **Why it blocks:** design §7 (`typescript_client_design.md:329`) says anything not in the adaptation table must match Python. `:384` says to compare class, code and status. Round 3 treated the same mismatch for 3xx as a blocker.
- **Smallest fix:**
  1. Drop `statusCode:response.status` on line 117.
  2. Add Python-recorded fixtures for non-200 metadata through the metadata, account and project paths. Today no test pins this either way.
- **I tried the fix in a scratch copy:** all 10 cases then match Python, and the full suite still gives 351 tests, 343 pass, 0 fail, 8 skips.

### Blocker 2 (3xx handling): fixed
- **Code:** `sendSameOrigin` now treats every status from 300 to 399 as a redirect. That is the same range as httpx 0.28.1's `is_redirect`, which I read from the installed source. It cancels the body first, then throws `IssuerError` (status 0) when there's no Location.
- **Against Python over a real loopback server:** 54 of 54 scenarios matched across account, metadata and project, with TypeScript on its default Node transport and Python on real httpx. Each match covers error class, status and code (or the full success result) plus the exact requests the server saw. The scenarios:
  - 300, 301, 302, 303, 304, 305, 306, 307, 308 and 399 with no Location;
  - an empty Location;
  - 300, 303, 304 and 399 with a same-origin Location;
  - a cross-origin Location;
  - three redirects, which succeed, and four, which are refused.

  The redirect limit and same-origin rule are unchanged.
- **The comparison catches real differences:** the code from `7a6d0dd8` differs from Python in 44 of those 54 scenarios.
- **Body cancellation:** I checked this at runtime. Every 3xx body is cancelled before the refusal or the next request; a 304 has no body to cancel; the final response's body stays unread. No test covers this; removing the cancel passes everything. Not blocking.

### The new tests and fixtures are genuine
- **Python changes caught by the fixture drift check (7 of 7):**
  - returning the response when there's no Location;
  - following only 301/302/303/307/308;
  - leaving out 304;
  - leaving out 300;
  - passing the 3xx status into the error;
  - changing the redirect method;
  - changing the default "rate limited" text, which closes round 3's non-blocking gap.
- **TypeScript changes caught by the tests (7 of 7):**
  - the old status set;
  - the old return when Location is missing;
  - excluding 399;
  - excluding 300;
  - passing the 3xx status into the error;
  - changing the redirect method;
  - changing the TypeScript default 429 text.
- **The fixture generator uses real Python code:** the actual `AccountApi`, `fetch_issuer_metadata` and `resolve_project` against mock transports. It reads no environment or files and clears both caches between cases.
- **The diff from `7a6d0dd8` is exactly what you described:** the generated JSON only gains lines (573 added, 0 removed), and nothing changed under `.github`, `src`, `contracts` or `website`.

### Gates I ran at this head
- **Node test suites:** 351 tests on both Node 24.0.2 and 22.0.0, run one after the other: 343 pass, 0 fail, 8 skips that only run on Windows.
- **Other checks:**
  - typecheck;
  - `check:generated`;
  - `check:parity`;
  - `check:package` (ESM, CommonJS and declarations);
  - ruff, ruff format and pyright (0 errors) on the generator.
- **Docs:** the existing text is still accurate; it says nothing about 3xx responses without a Location.

### CI at `d10d9486`
- **Passing:**
  - SDK Linux lanes: 351 tests, 343 pass, 8 skips;
  - SDK Windows lanes: 351 of 351 pass, 0 skips;
  - all three engine compatibility lanes;
  - Docs;
  - all main CI jobs except one.
- **Still pending:** Integration (workers).
- **Failing:** CLA.

### Merge conditions
1. Fix the metadata status blocker above, with Python-recorded fixtures.
2. Re-run all CI on the new head and get a fresh approval.
3. CLA assent is still pending; I didn't touch it.

Everything else (round 3's unaffected scope, the round 4 workflow approval, and this commit's 3xx fix) holds.

### Process
- **Read-only:** I changed no source, git, GitHub, CLA or npm state, and sent no messages. The worktree is clean, apart from ignored build output.
- **Scratch work:** all code changes were made in `/tmp/opus5r-scratch` and restored afterwards. The comparison scripts are in `/tmp/opus5r-diff`.
- **Isolation and keys:** I inherited `REMEMBER_CONFIG_DIR` everywhere and used only synthetic keys.
- **Stray process:** one of my mutation scripts had a shell typo and started a stray Python process. I stopped it and rebuilt the scratch copy; it never touched the worktree.
