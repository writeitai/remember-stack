# Releasing RememberStack

The `Release` workflow publishes one version to PyPI and GHCR, then creates a GitHub release
containing the Python distributions, the same version-pinned `compose.yaml`, and the example
environment as `default.env.example` (GitHub's public asset name for the source
`.env.example`). It accepts only tags exactly matching `vMAJOR.MINOR.PATCH`.

## One-time owner setup

The one-time owner setup is complete:

1. D77 records explicit acceptance of the preliminary naming risk. `CLA.md`, the trademark policy,
   pull-request template, and metadata-only `CLA` workflow are present. The emitted `CLA` status is
   a required `main` check with administrator enforcement.
2. The GitHub repository is `writeitai/remember-stack`. Update each existing clone if needed:

   ```bash
   git remote set-url origin git@github.com:writeitai/remember-stack.git
   ```

   The readable hyphen belongs only to repository and container URLs; the product remains
   RememberStack and the Python distribution/import remain `rememberstack`. GitHub redirects
   ordinary repository and Git traffic after a rename, but the final name must be in place before
   configuring PyPI because the trusted identity includes the repository name.
3. The GitHub environment `pypi` requires an owner review, so a tag cannot publish to PyPI without
   explicit approval.
4. The PyPI account uses two-factor authentication and has a
   [pending Trusted Publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
   with these exact values:

   | Field | Value |
   |---|---|
   | PyPI project name | `rememberstack` |
   | GitHub owner | `writeitai` |
   | Repository | `remember-stack` |
   | Workflow | `release.yml` |
   | Environment | `pypi` |

   A pending publisher does not reserve the PyPI name. Configure it only after the repository
   rename and publish promptly once the release gates are clear.
5. The active `Protect release tags` ruleset restricts creation, update, and deletion of tags
   matching `v*` to repository administrators.

No PyPI password or long-lived API token belongs in GitHub secrets. The workflow requests a
short-lived OpenID Connect credential and grants `id-token: write` only to the PyPI job.

## Cutting a release

Prepare a normal pull request that updates both `project.version` in `pyproject.toml` and the
GHCR tag in `compose.yaml`. Update release-facing documentation in the same pull request. The
contract check rejects drift:

```bash
uv run python scripts/check_release_contract.py --tag v0.2.0
```

That release pull request also refreshes the PostgreSQL foundation pins in
`Dockerfile.postgres`: use the current patched PostgreSQL 18 base digest, pin the intended
`postgresql-18-partman` package version, and pin the pg_textsearch release plus both amd64 and
arm64 archive checksums. Build the Dockerfile for both architectures before tagging. This is the
manual, reviewable patch cadence; mutable database-image tags are not used.

After that pull request is merged and `main` is green, tag its exact merge commit:

```bash
git switch main
git pull --ff-only
git tag -a v0.2.0 -m "RememberStack 0.2.0"
git push origin v0.2.0
```

The workflow validates the tag, runs the release test suite, builds the wheel and source
distribution, and publishes `rememberstack==0.2.0` plus
`ghcr.io/writeitai/remember-stack:0.2.0`. It creates the GitHub release only after both
registries accept their artifact.

PyPI and GHCR do not support an atomic cross-registry transaction. Never reuse a published
version after a partial failure: fix the cause, complete the missing publish when safe, or cut the
next patch version.

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
uvx --from rememberstack==0.2.0 remember --version
docker pull ghcr.io/writeitai/remember-stack:0.2.0
gh release download v0.2.0 --repo writeitai/remember-stack \
  --pattern compose.yaml --pattern default.env.example
cp default.env.example .env
docker compose --env-file .env up --no-build --pull always --detach --wait
curl --fail http://localhost:8000/healthz
docker compose --env-file .env down --volumes
```

The final command deletes the disposable verification deployment and its volumes.
