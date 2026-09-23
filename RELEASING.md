# Releasing RememberStack

The `Release` workflow publishes one version to PyPI and GHCR, then creates a GitHub release
containing the Python distributions, the same version-pinned `compose.yaml`, the generated
`openapi.json`, and the example environment as `default.env.example` (GitHub's public asset
name for the source `.env.example`). It accepts only tags exactly matching
`vMAJOR.MINOR.PATCH`.

## One-time owner setup

The one-time owner setup is complete:

1. D77 records explicit acceptance of the preliminary naming risk. `CLA.md`, the trademark policy,
   pull-request template, and metadata-only `CLA` workflow are present. The emitted `CLA` status is
   a required `main` check with administrator enforcement.
2. The GitHub repository is `writeitai/remember-stack`. Update each existing clone if needed:

   ```bash
   git remote set-url origin git@github.com:writeitai/remember-stack.git
   ```

   Under D108, the canonical PyPI distribution is `remember` (providing the `remember` CLI launcher
   and the `remember` Python client package with backward-compatible `Client` and `CloudClient` shims),
   published from this repository starting with `v0.17.0`. The container image package is `ghcr.io/writeitai/remember-stack`.
3. The GitHub environment `pypi` requires an owner review, so a tag cannot publish to PyPI without
   explicit approval.
4. The PyPI account uses two-factor authentication and has an active
   [Trusted Publisher](https://docs.pypi.org/trusted-publishers/)
   with these exact values:

   | Field | Value |
   |---|---|
   | PyPI project name | `remember` |
   | GitHub owner | `writeitai` |
   | Repository | `remember-stack` |
   | Workflow | `release.yml` |
   | Environment | `pypi` |

   This publisher replaced the retired `writeitai/ultimate-memory-cloud` / `release-remember.yml`
   / `pypi-remember` publisher after the `0.17.0` upload succeeded. Do not restore a second
   publisher for the same package.
5. The active `Protect release tags` ruleset restricts creation, update, and deletion of tags
   matching `v*` to repository administrators. The workflow token cannot create these tags.

No PyPI password or long-lived API token belongs in GitHub secrets. The workflow requests a
short-lived OpenID Connect credential and grants `id-token: write` only to the PyPI job.

## Cutting a release

Prepare a normal pull request that updates both `project.version` in `pyproject.toml` and the
GHCR tag in `compose.yaml`. Update release-facing documentation in the same pull request. The
contract check rejects drift:

```bash
uv run python scripts/check_release_contract.py --tag v0.17.0
```

That release pull request also refreshes the PostgreSQL foundation pins in
`Dockerfile.postgres`: use the reviewed PostgreSQL 19 prerelease/GA base digest,
pin the intended PGDG pgvector and pg_partman package versions, and pin the
pg_textsearch source revision plus source, compatibility-patch, license, and
notice checksums. Build the Dockerfile for both architectures, record each
embedded artifact manifest and immutable image digest, and run the extension
and graph release matrix before tagging. The tag workflow then rebuilds and
publishes `ghcr.io/writeitai/remember-stack-postgres:19beta3-VERSION` for both
architectures and attaches `postgres-image-digests.json` to the GitHub release;
UMC consumes the recorded manifest digest, never that human-readable tag.
Updating the foundation pins remains a manual, reviewable patch cadence;
publishing and digest capture are mechanical and mutable database-image tags
are not used.

After that pull request is merged and `main` is green, tag its exact merge commit:

```bash
git switch main
git pull --ff-only
git tag -a v0.17.0 -m "Remember 0.17.0"
git push origin v0.17.0
```

An administrator must push the tag at the exact verified source commit before
the GitHub-release job runs. A `main` push may start the workflow before the
tag exists; if the job then fails with the tag-creation rule, create the
annotated tag at that immutable source SHA and rerun only the failed jobs.
Never move an existing release tag.

The workflow validates the tag, runs the release test suite, builds the wheel and source
distribution, and publishes `remember==0.17.0` plus
`ghcr.io/writeitai/remember-stack:0.17.0` and the multi-architecture PostgreSQL
foundation. It creates the GitHub release only after both registries accept
their artifacts and the PostgreSQL manifest proves amd64 plus arm64 digests.

PyPI and GHCR do not support an atomic cross-registry transaction. Never reuse a published
version after a partial failure: fix the cause, complete the missing publish when safe, or cut the
next patch version.

## `0.17.0` cutover record (2026-09-23)

- Source commit: `78fee141484729be3763fe886c4d7cff595c09e7` (PR #446).
  The owner-created annotated `v0.17.0` tag resolves to that commit.
- [Release run 35788878236](https://github.com/writeitai/remember-stack/actions/runs/35788878236)
  completed on attempt 3. Attempt 1 exposed the missing `remember` PyPI
  trusted publisher; attempt 2 published both Python distributions but the
  workflow token could not create the administrator-protected tag. The owner
  added the publisher and tag, then resumed the same release.
- PyPI has [`remember==0.17.0`](https://pypi.org/project/remember/0.17.0/)
  wheel and source archive. The one-time
  [`rememberstack==0.17.0`](https://pypi.org/project/rememberstack/0.17.0/)
  forwarder was also published, then the `rememberstack` project was archived
  rather than deleted. Its 24 releases and existing pins remain available;
  new uploads are blocked and PyPI search no longer promotes the name.
- The [GitHub release](https://github.com/writeitai/remember-stack/releases/tag/v0.17.0)
  is titled `remember 0.17.0` and attaches only canonical `remember` Python
  files alongside deployment inputs and image receipts. Application image:
  `sha256:df9ca49f6249b8a848f429a1688324467615ce7818de37dfc19e8c589678b587`.
  PostgreSQL multi-architecture manifest:
  `sha256:59ed1414da1f5f366b7e1c5f0f6028a9bc99a15183debbd6d52d3b4a94062684`
  (amd64 and arm64 receipts attached).
- The PyPI `remember` project now lists only this repository's `release.yml`
  / `pypi` trusted publisher. No long-lived publishing token was created.

## GHCR visibility

The `remember-stack` container package was made public after the `v0.1.0` image push, so later
versions in the same package support anonymous Compose pulls without another visibility step. A
new package namespace would default to private and require the same one-time review. GitHub warns
that a public package cannot be made private again.

The image carries standard OCI source labels generated from the repository metadata, which links
the package back to this repository. Docker Hub is intentionally not a second publication target.

## Verify the public artifacts

Run these checks from a clean machine or temporary directory:

```bash
uvx --from remember==0.17.0 remember --version
docker pull ghcr.io/writeitai/remember-stack:0.17.0
gh release download v0.17.0 --repo writeitai/remember-stack \
  --pattern compose.yaml --pattern default.env.example --pattern openapi.json
found=$(jq -r '.info.version' openapi.json) || {
  echo "cannot read openapi.json" >&2
  exit 1
}
[ "$found" = "0.17.0" ] || {
  echo "openapi.json is version $found, expected 0.17.0" >&2
  exit 1
}
cp default.env.example .env
docker compose --env-file .env up --no-build --pull always --detach --wait
curl --fail http://localhost:8000/healthz
docker compose --env-file .env down --volumes
```

The final command deletes the disposable verification deployment and its volumes.
