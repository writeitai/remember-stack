## Verdict: CHANGES REQUESTED at `301ebd0cf04384dfad175ba1a2be4643af296ddb`

Two blockers remain. Everything else you asked me to check holds up when run.

### Blocker 1: the compatibility matrix fails at this exact head
All three lanes (0.15.0, 0.16.0, candidate) failed at `301ebd0c`. CI jobs: 110701376628 (0.15.0), 110701376441 (0.16.0), 110701376664 (candidate).

- **Cause:** the CI step's wait loop polls `http://127.0.0.1:8001/openapi.json` (`.github/workflows/compatibility-matrix.yml:196-200`). But `build_api` sets `openapi_url=None` (`src/rememberstack/surfaces/http_api.py:494`, and the same at v0.15.0). So the poll gets 404 thirty times and the step exits 1 before the probe runs. The failure is certain on every run, not flaky.
- **Already fixed in the next commit:** the PR head has moved to `7a6d0dd8`, which changes only this workflow. It polls `/fixture/recordings` instead and drops the PR path filter, so the matrix now runs on every PR. That also closes a gap I found: the two fixture files this PR adds weren't in the old path filter. All three matrix lanes pass at `7a6d0dd8`. I only read that commit's diff; I didn't review it in full.
- **The probe itself is sound.** I ran it on a private port, against current source and inside the real released images:

| Engine | `Z` | `+02:00` | Server's parsed value |
| --- | --- | --- | --- |
| candidate (current source) | reaches the 418 sentinel | 422 "UTC", nothing reaches the port | `+00:00` |
| 0.15.0 image | reaches the 418 sentinel | reaches the 418 sentinel | `+02:00` unchanged |
| 0.16.0 image | reaches the 418 sentinel | reaches the 418 sentinel | `+02:00` unchanged |

The fixture reuses each release's real request validation and adds none of its own. The original strict all-method live-engine script, `live-engine.mjs`, is byte-identical to the version approved in round 2, so nothing was weakened.

### Blocker 2: issuer/account handling of 3xx responses no longer matches Python
This regressed in `acd95c5f`. The round-2 report said "httpx treats [a 3xx without Location] as an ordinary response, so Python returns it". That's wrong for the locked httpx 0.28.1:
- httpx's `is_redirect` is true for any status from 300 to 399, whether or not there's a `Location` header.
- So Python's `send_same_origin` reaches `src/remember/issuer.py:191-192` and raises `IssuerError("… redirected without a Location")` with status 0.

TypeScript (`packages/typescript-client/src/issuer.ts:71-73`) now returns the response instead. It also only treats 301/302/303/307/308 as redirects; that part predates this round, but it's in the same lines. I ran both clients on identical responses:

| Path | Response | Python | TypeScript |
| --- | --- | --- | --- |
| account | 302 or 307, no Location | `IssuerError` (0) | `MemoryApiError` (302/307) |
| account | 304, no Location | `IssuerError` (0) | `MemoryApiError` (304) |
| account | 300 with a same-origin Location | follows it, succeeds | `MemoryApiError` (300) |
| metadata | 302 / 307 / 304, no Location | `IssuerError` (0) | `IssuerError` with the 3xx status |
| project resolution | 302 / 307 / 304, no Location | `ProjectResolutionError` (0) | `ProjectResolutionError` with the 3xx status |

Section 7 of the design doesn't list this as an adaptation, so Python's behavior binds. `tests/routing.test.mjs:146-150` currently asserts the wrong behavior.

**Smallest fix:**
1. In `sendSameOrigin`, return the response only when the status is outside 300–399.
2. Cancel the body, and throw `IssuerError({detail:'issuer redirected without a Location'})` when `Location` is missing (status 0, as at round 2).
3. Change the test to expect that.
4. Add Python-recorded fixtures to `scripts/export_typescript_parity.py`: account 302/304 without Location, account 300 with a same-origin Location, and metadata and project-resolution 3xx without Location.

### Everything else I checked holds up
- **Node test suites:** 335 tests on both Node 24.0.2 and 22.0.0, 327 pass, 0 fail, and 8 skips that only run on real Windows. I ran the two suites one after the other. Typecheck passes on both.
- **CI at `301ebd0c`:**
  - Both Windows lanes ran 335/335 with 0 skips; both Linux lanes pass.
  - Main CI passes, except Integration (workers), which was cancelled at this head; it passes at `7a6d0dd8`.
  - The docs build passes. CLA fails.
- **Generated-artifact and parity checks pass, and they catch real Python drift.** I changed Python in a scratch copy and confirmed each change fails a check:
  - the account error-detail order and the 429 code fallback;
  - the readiness timeout mapping and a method default;
  - a new `Client` method, a new root export and a tool description;
  - provider models with a hidden `field_validator`, an `Annotated` validator, `model_post_init` or a new pattern constraint.

  The 4 provider pytest cases pass, and the file is in the mandatory unit list.
- **Round-2 must-fixes:** all three are fixed and pinned by fixtures recorded from executed Python: the readiness timeout maps to `status_code` 0, account error details match, and the 429 message fallbacks match. Beyond the fixtures, 13 extra 429 bodies, 8 Retry-After headers and 11 extra account responses all matched Python except the 3xx row above.
- **Environment names:** 7 of 7 cases matched Python, run in real child processes. Lookup is case-insensitive, the last spelling wins, and that holds for the config-dir and `XDG_CONFIG_HOME` variables too.
- **Ingest deadline:** I blocked the local file read on a named pipe (FIFO). The deadline gives `RequestTimeoutError` after about 300 ms. A caller abort gives `AbortError` with the original reason, close gives `AbortError`, and no HTTP is sent. Same on both Node versions.
- **Package:** the tarball checks pass on Node 22 and 24. My own strict consumer (`lib: ES2022`, no DOM, full declaration checking) compiles with `@types/node` 22.19.15 and 24.3.0. The tarball has no `bin` and no credential-like strings.
- **Python:**
  - 278 focused tests pass.
  - ruff, ruff format, the import-architecture check and the test-inventory check pass.
  - Full pyright exits 0.
- **Docs:** the docs-truth checks (local and cloud) and the llms check pass. The new docs text on timers, Windows keys, environment names and the timestamp boundary matches what I measured.

### Not blocking
- Changing Python's default "rate limited" text doesn't fail any check, because no recorded 429 case reaches that default. TypeScript matches today; one fixture with `{"detail":{}}` or a non-JSON body would pin it.

### Merge conditions
1. Fix Blocker 2 on top of `7a6d0dd8`.
2. Re-run all CI on the new head and get a fresh approval.
3. CLA assent is still pending. That's a merge gate; I didn't accept it.

The PR should stay a draft until then.

### Process
- I changed no source, git, GitHub, CLA or npm state, and sent no messages. The worktree is clean at `301ebd0c`. I used no subagents.
- My scratch work is in `/tmp/opus3r`. My temporary containers and servers are removed.
- `REMEMBER_CONFIG_DIR` isolation was inherited everywhere, and I used only synthetic keys.
- Port 8001 was already held by someone else's fixture process (PID 71960, running from the worktree's `.venv`); I left it alone and used my own ports. Port 8000 is colima's SSH tunnel; I sent nothing to it.
