## Verdict: CHANGES REQUESTED (three small Python-parity fixes)

I reviewed head `a8db0470641d7b4b60e6ac11ec2d1829a5c864b3` in full, not just the fixes. 16 of the 17 prior-round items are fully fixed. The 17th, readiness-timeout mapping, is only partly fixed. Every gate passes on Node 22.0.0 and 24, and CI is green apart from the CLA.

Three remaining differences from Python block approval. The design says anything not listed in its §7 adaptation table must match Python, and these aren't listed. One of them is the unfinished part of a round-1 blocker, and a test now pins the wrong value. Each fix is small. Nothing else blocks.

### Must fix

1. **The readiness timeout maps to the wrong `status_code`.** `mapError(new TimeoutError())` returns `transport_error`, retryable, with `status_code: null` (`src/tool-errors.ts:41`). Python's `map_error(TimeoutError(...))` returns `status_code: 0`; I ran it at this revision. No design text allows `null` here; the only timeout rule in the design (§7, `RequestTimeoutError`) also says `statusCode=0`. `tests/routing.test.mjs:113` asserts `null`, so the test locks in the wrong value. The shared Python fixture has no case for this mapping, which is how it slipped through.

2. **Account errors decode a different message from Python.** `AccountApi.get` uses the engine's error decoder (`src/client.ts:316`); Python uses `_error_detail` (`src/remember/client.py:1400`). I ran both on the same responses:

   | Account response | Python `detail` | TypeScript `detail` |
   | --- | --- | --- |
   | 403 `{"detail":{"code":"forbidden","message":"Key revoked"}}` | `Key revoked` | `{"code":"forbidden","message":"Key revoked"}` |
   | 403 `{"detail":"x","extra":1}` | `x` | `deployment API returned a malformed error envelope` |
   | 400 on account path `/query/foo` | the message | `deployment API returned a malformed structured error` |

3. **The rate-limit message ignores the error code.** Python builds the 429 message as `str(message or code or "rate limited")` (`client.py:1393`); `src/http.ts:153` drops the `code` step and ignores non-string messages. The default `rate limited` text now matches.

   | 429 body `detail` | Python | TypeScript |
   | --- | --- | --- |
   | `{"code":"concurrency_limited"}` | `concurrency_limited` | `rate limited` |
   | `{"code":"x","message":""}` | `x` | empty string |
   | `{"message":42}` | `42` | `rate limited` |

### Lower severity (fix, or document as an adaptation)

- **Raw transport errors leak from account calls.** With an injected `HttpTransport`, a network exception on the account endpoint comes back as a plain `Error` (`src/client.ts:315`). Engine calls wrap the same failure as `MemoryApiError(0)`, and Python does too. As a result `mapError` gives `internal_error` instead of `transport_error`. The default transport is not affected.
- **Timestamps without a time-zone offset are refused.** `validAt`/`believedAt` strings like `2026-01-01T10:00:00` fail locally (`src/client.ts:30`). Python sends them unchanged, and the API schema accepts them. Refusing is defensible, but it isn't in §7 or the docs.
- **Docs gaps** (the docs-truth and llms checks pass):
  - Neither SDK page mentions the new Windows exception: an explicit signed key works with no URL and no stored file. The TS page says only "Windows refuses automatic file reads" (`typescript-sdk/page.mdx:88`). The Python page tells users to supply a key *and* an engine URL (`python-sdk/page.mdx:1199-1203`), which is now incomplete because this PR changed Python too.
  - The TS page doesn't say that `timeoutMs`/`pollIntervalMs` of zero, NaN or Infinity are refused, unlike Python's single poll at `timeout=0`.
- **Redirect without a Location header.** `sendSameOrigin` raises `IssuerError` for a 3xx with no `Location`. httpx treats that as an ordinary response, so Python returns it. This is a very rare case.

### Prior-round items

| Item | Status | Evidence |
| --- | --- | --- |
| AJV memory leak | Fixed | Compiled validators are cached against stable schema objects. Running all 85 fixture methods and 36 tool validations: 4,840 calls per batch over three batches retained under ~21 KB after garbage collection (noise). |
| Large floats vs integers | Fixed | `1e+20`, `1.152921504606847e+18` and `12345678901234567890.0` are accepted. Whole-number values of 2^53 or more are refused, positive and negative. |
| Export and default drift | Fixed | `check-surface` checks all inventory exports, support functions, types, fields and members, plus the three millisecond defaults. Five mutation tests prove changes fail. |
| `+02:00` timestamps | Fixed | Sent unchanged in the query string and request body. |
| Issuer down during account calls | Fixed | Metadata 503 or network failure → `IssuerError`; account endpoint 503 → `MemoryApiError(503)`, as in Python. |
| Readiness `TimeoutError` mapping | **Partly fixed** | Code and retryable flag are correct; `status_code` is not (must-fix 1). |
| Readable FastAPI error detail | Fixed (engine path) | A list `detail` becomes readable JSON text. |
| Insecure issuer error class | Fixed | `ProjectResolutionError` on engine calls, `IssuerError` on account calls; both match Python. |
| Strict Node consumers, no DOM types | Fixed | My own NodeNext consumer with `lib: ES2022` only and `@types/node@22.19.15` compiles cleanly. |
| Docs, docs-truth, llms | Fixed | Both checks and the cloud truth check pass; the five round-1 doc errors are corrected. Gaps listed above. |
| Windows explicit signed key | Fixed (TS + Python) | Windows CI lanes ran 308/308 including this test. I also simulated `win32` on macOS across 9 precedence cases; results match the PR502 `f1befcb4` text. |
| Read 421'd after another request re-pinned | Fixed | Four concurrent calls: all three reads retried at the new host; the write was sent exactly once. |
| Live-engine paths | Fixed | The script now records the actual native request paths. On 0.15.0, 0.16.0 and candidate there are no unexpected paths and no invented successes. Connectors are explicitly unsupported everywhere; document routes and adjacent chunks are unsupported on 0.15.0/0.16.0. |
| Network error cause | Fixed | `cause.code === 'ECONNREFUSED'` is kept. |
| Default rate-limit message | Fixed | Code fallback still differs (must-fix 3). |
| Empty config directories | Fixed | TS and Python give identical paths: `.`, `credentials.json`, `remember`. |
| Zero/non-finite timeouts | Matches PR502 `f1befcb4` | Zero, NaN and Infinity are refused for `timeoutMs`, readiness `timeoutMs` and `pollIntervalMs`. |

### Other evidence

- **Tests on Node 24.0.2:** 308 tests, 307 pass, 1 Windows-only skip.
- **Tests on Node 22.0.0:** six combined runs were clean (307 pass, 1 skip, about 10 s), and all 10 files also pass individually.
  - My first run hung in Node's own test runner while the Node 24 suite was running alongside it. It never reproduced, and CI is clean, so I'm noting it, not counting it as a defect.
- **Gates on both Node versions:** typecheck, `check:generated`, `check:parity` and `check:package` all pass. The package check installs the real tarball into separate ESM and CommonJS consumers.
- **Python:** 105 focused client tests and 40 route-scope tests pass.
- **On-the-wire checks (Node 22.0.0, real loopback server):**
  - A write that gets a 421 is sent once, and later requests never reuse that connection.
  - The API key never appears in `inspect` (including hidden and raw modes) or `JSON.stringify` output.
- **CI at this head:** all four SDK lanes (Linux/Windows × Node 22.0.0/24) pass; the Windows lanes ran 308/308. The 0.15.0, 0.16.0 and candidate matrix, the main CI workflow (10 jobs) and the docs build all pass. CLA fails, which is the known dependency.

### Merge conditions

1. Fix the three must-fix items. Add Python-recorded fixtures for the builtin `TimeoutError` mapping, the account error detail, and the 429 message fallbacks.
2. Fix the lower-severity items, or add them to the design's §7 table and the docs.
3. Get approval of design PR #502 at `f1befcb4` or later (round 8 is reviewing).
4. Get CLA assent.
5. Re-run all CI on the new head and get a fresh implementation approval.

The PR stays a draft until then. The CLI/MCP executables and connector implementations are separate deliverables, and the connector methods are correctly tested as unsupported facades.

### Process

- I changed no source, git, GitHub, CLA or npm state. The worktree is clean at `a8db0470`.
- Scratch work is in `/tmp/tsr3/`: a read-only snapshot, two working copies, probes and consumers.
- `REMEMBER_CONFIG_DIR` stayed set in every subprocess, with all connection variables unset. I never opened that directory or any real credential. I used no subagents.
- I stopped only my own hung Node 22.0.0 test process.
