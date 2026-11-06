**APPROVED at `3f73cfd1f249605e61f919d2b65758161b40f3d0`**

Both round-12 items are fixed, and the only change since round 12 is this commit (3 files).

**The blocker (source path) is fixed.**
- `plan/analysis/typescript_client_parity.md:250` now cites `src/rememberstack/surfaces/http_api.py`. In that file, `_require_utc` is at `:139` and `UTCInstant` at `:146`. It's applied at `:336-337` (neighborhood), `:359-360` (path) and `:529` (`/lookup/relations` `valid_at`).
- The wrong path `src/remember/http_api.py` doesn't exist. The only place it still appears is the round-12 report, which quotes it on purpose.

**The recommendation (older versions) is fixed.**
- Analysis `:266-267` and design `:339` now say "pre-v0.17.1 releases (matrix: v0.15.0/v0.16.0) accept offsets". That matches the compatibility-matrix workflow (`0.15.0`, `0.16.0`, `candidate`, `.github/workflows/compatibility-matrix.yml:25`) and design `:320`.
- I checked the history at each release tag:
  - At `v0.15.0`, `v0.16.0` and `v0.17.0`, the lookup and graph request fields are plain `datetime`, with no `UTCInstant`.
  - `v0.17.1` and `v0.17.2` add `_require_utc`/`UTCInstant` on `/lookup/relations` and on the graph request models.
  - Commit `78660f99` is contained only in `v0.17.1` and `v0.17.2`.
  - The plain `datetime` fields still in the current file are the internal `GraphQueryPort` method signatures, not the HTTP boundary, so they don't affect this claim.

**Prior approvals still stand.** Since the final-approval commit `ff95446c`, only the two review files, the analysis section and the one §7 table row have changed. Rounds 11 and 12 reviewed the timestamp adaptation, and the approvals from rounds 7, 8 and 10 cover the rest of the design. `git diff --check` reports no whitespace errors and the working tree is clean.

**Optional nit (not blocking):** analysis `:252` still says "v0.16.0 used plain datetime fields". That's true, but it's narrower than the new sentence; "earlier releases (v0.15.0–v0.17.0)" would read more consistently. I found no markdown lint config, and over-80-character lines already appear throughout the file, so line length isn't a problem.

I only read files, using `git show`, `grep` and `ls-tree`. I ran no code and made no changes to files, git, GitHub, the CLA, npm or production, and used no credentials.

Separately, the Google Calendar and Google Drive connectors need to be authorized in your claude.ai connector settings before they can be used. This review didn't need them.
