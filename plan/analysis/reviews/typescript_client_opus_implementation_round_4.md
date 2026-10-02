## Verdict: APPROVED for the 7a6d0dd8 delta only

This covers only `.github/workflows/compatibility-matrix.yml`, the one changed file. It is not approval to merge PR #503. Merging still needs the separate full round-3 approval, every CI check green, and the CLA resolved by the contributor. I did not accept, override or touch the CLA.

I didn't run the Python launcher at the end of your message, because it would start another `claude` process. I'm already Opus 5.5 at xhigh, so I did the review directly. Everything was read-only: no edits, commits, pushes, docker runs or credential access. `REMEMBER_CONFIG_DIR` was inherited, and none of the cleared connection variables were present.

### Startup fix: correct
- **The old loop could never succeed.** All three engines set `openapi_url=None` in `build_api`: v0.15.0 at `http_api.py:381`, v0.16.0 at `:403`, and the candidate at `src/rememberstack/surfaces/http_api.py:494`. So polling `/openapi.json` was a guaranteed 404.
- **The new readiness check is stronger, not just working.** `/fixture/recordings` is defined only by the fixture (`.github/ci/temporal-boundary.py:77-80`). Nothing else can satisfy it: not the engine API on :8000, and not any other server that happened to hold :8001.
- **Polling it doesn't touch what's being tested.** It's a GET that only reads `_CALLS`, so the probe's exact `deepEqual` check on the recordings (`temporal-boundary.mjs:25-26`) still holds.
- The retry limit, the failure exit and the probe invocation are all unchanged.

### All three real CI boundary probes pass
Run 36963800558 ran on exact head `7a6d0dd8…`, triggered by `pull_request`, and succeeded:

| Engine | Fixture ready after | `Z` inputs | `+02:00` inputs |
|---|---|---|---|
| 0.15.0 | 3 refused connections | all 3 methods returned the 418 sentinel | all 3 methods returned the 418 sentinel |
| 0.16.0 | 4 refused connections | all 3 methods returned the 418 sentinel | all 3 methods returned the 418 sentinel |
| candidate | 4 refused connections | all 3 methods returned the 418 sentinel | all 3 methods refused with 422 |

- The step exited 0 in each job, so the exact parsed-offset recording assertions passed too.
- The step that exercises every TypeScript method is unchanged and passed in all three jobs.

### Triggers and permissions: valid, nothing weakened
- **Valid YAML.** `actionlint` is clean, and the file parses to `pull_request: None`, which means GitHub's default PR event types. The run itself fired on the `pull_request` event.
- **No authorization change.** It is still `pull_request`, not `pull_request_target`, with `permissions: contents: read`, no secrets, and the same `fail-fast: false` matrix. Fork PRs get a read-only token.
- **Nothing else changed.** A diff of `packages/`, `src/`, `.github/ci/`, `scripts/` and `contracts/` against 301ebd0c is empty.

### Running on every PR is the right call
- **The old filters missed the engine itself.** They covered `src/remember/**` (the Python client) but not `src/rememberstack/**`. That is the engine API source the candidate image is built from. They also missed the `Dockerfile`, `pyproject.toml`, `uv.lock` and `.github/ci/*`. A server-side API change could drift away from the TypeScript client without the matrix ever running.
- **It matches the design.** D140 (`decisions.md:6699`) and gate 7 in `plan/designs/typescript_client_design.md:319-324` say real released-engine compatibility gates the client. Running on every PR is simpler and sounder than keeping a hand-maintained path list.
- **It's cheap.** Each job took about 2.5–3 minutes.

### Non-blocking notes
1. **The matrix runs on every PR, but GitHub doesn't require it to pass.** Branch protection on `main` requires only `CLA` and `PR gate`. `PR gate` (`ci.yml:531-534`) depends only on `changes`, `quality`, `unit`, `contract-smoke` and `engine-image-arm64`. So it doesn't wait for the matrix or the integration lanes; it was already green while `Integration (workers)` was still running. A failing matrix is currently caught only by the "all CI green" merge rule. This predates the PR. Removing the path filters is what makes it safe to add the three `Client vs Engine (…)` checks as required later; that's a repo-admin decision outside this PR.
2. **Fixture crashes won't show in the failure output.** On a readiness timeout, `docker logs remember-engine` prints the API process's output. The fixture runs through `docker exec --detach`, so its own traceback wouldn't appear. This also predates the delta. Writing the fixture's output to a file and printing it on failure would fix it.
3. **Optional:** with no `concurrency:` group, superseded PR pushes keep running. That's acceptable at this cost.

### Limits of this review
- I didn't re-run anything locally (no docker). Pass evidence comes from the GitHub logs of run 36963800558.
- For the TypeScript client and Docs checks I only confirmed SUCCESS in PR #503's check rollup at this head; I didn't open their logs.
- When I checked, `Integration (workers)` was still in progress and `CLA` was failing. Both must be resolved before merge.
- I didn't review the SDK, source or docs content; that's frozen and belongs to the concurrent full round-3 review.
