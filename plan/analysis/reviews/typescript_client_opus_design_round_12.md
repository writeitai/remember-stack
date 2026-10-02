**CHANGES REQUESTED at `1e111347d6b6ebf890bbac2c7b870dde9af9bd28`**

The corrected explanation is right, and so is every behavioral claim. One cited source path is wrong, and you asked me to verify the paths, so that is the blocker. It's a one-line fix.

### Blocker: wrong server source path
`plan/analysis/typescript_client_parity.md:250` cites `src/remember/http_api.py`, but that file doesn't exist. The server's validator is in `src/rememberstack/surfaces/http_api.py:139-146`.

The wrong path is misleading, not just broken. `src/remember/` is the Python client package, and it has its own `_require_utc` in `src/remember/models.py:32`, which is the client's validator for its UTC-only fields. A reader who goes to `src/remember/` and searches for `_require_utc` lands on that one. They could then conclude that the Python client enforces UTC, which is the opposite of what this section says.

**Fix:** change the path to `src/rememberstack/surfaces/http_api.py`.

### Recommended in the same edit (not blocking): older-version expectations
`.github/workflows/compatibility-matrix.yml:25` tests three server versions: `0.15.0`, `0.16.0` and `candidate`. The §7 test column (`typescript_client_design.md:339`) only says "v0.16 accepts offsets", and the analysis says the same.

Every release before v0.17.1 uses plain `datetime` for these fields, so it accepts offsets at the boundary. That includes v0.15.0, which the matrix also tests, and v0.17.0. Something like "pre-v0.17.1 releases (matrix: 0.15.0, 0.16.0) accept offsets at the boundary" would give implementers an expectation for all three matrix versions.

### Verified as correct
- **Python client:** `src/remember/client.py:480, 626-628, 659-661` send `.isoformat()` unchanged, so naive values stay naive and offsets are preserved.
- **Server boundary:** `_require_utc` rejects values with no timezone or a nonzero offset, with "must be timezone-aware UTC (end it with Z)". `UTCInstant` is applied to:
  - `/lookup/relations` `valid_at` (`:529`)
  - `GraphNeighborhoodRequest` `valid_at`/`believed_at` (`:336-337`)
  - `GraphPathRequest` `valid_at`/`believed_at` (`:359-360`)
- **Version history:** commit `78660f99` is contained in `v0.17.1` and `v0.17.2`, not in `v0.17.0` or `v0.16.0`. At `v0.15.0`, `v0.16.0` and `v0.17.0` these fields are plain `datetime`.
- **Test path and wording:** `src/tests/surfaces/test_http_api_robustness.py` exists.
  - Lookup: `:180-190` sends naive and `+02:00` and expects 422; `:193-198` expects `Z` to succeed.
  - Graph: `:251-264` covers only `/graph/path`, with naive and `-05:00` giving 422.
  - The doc's phrase "UTC lookup and graph/path boundary cases" is therefore accurate.
- **OpenAPI:** in `openapi.json`, all five fields are `anyOf[{format: date-time, type: string}, null]`, with no UTC constraint.
- **Error class:** the design maps 422 to plain `MemoryApiError` (`:372`, with no 422-specific subclass). That matches Python (`src/remember/errors.py:17`), so "+02:00 → 422 `MemoryApiError`" is consistent.
- **§7 row:** it now says what round 11 asked for:
  - naive values are refused locally;
  - offsets are forwarded and the current server refuses non-UTC values with 422, as it does for Python;
  - the candidate server accepts `Z`;
  - older servers accept offsets.

  The analysis also states that no UTC-only client check was added.

### Scope
I reviewed `87131356..1e111347` only. The approvals from rounds 7, 8 and 10 still cover the rest of the design, and round 11 accepted the chosen adaptation. Everything here was read-only, using `git show` / `grep`. I didn't run any code; I relied on round 11's runtime checks plus the source and tests. I made no changes to files, git, GitHub, the CLA, npm or production, and used no credentials. HEAD hasn't changed and the working tree is clean.
