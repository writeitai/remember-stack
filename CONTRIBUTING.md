# Contributing to RememberStack

Thank you for helping improve RememberStack. Keep changes focused: for substantial work,
open an issue first so the scope and design boundary are clear before implementation.

## Development

The repository uses `uv` and supports Python 3.12 through 3.14.

```bash
uv sync --locked --python 3.14
uv run ruff check src/
uv run ruff format --check src/
uv run pyright src/ --pythonversion 3.14
uv run pytest src/tests -q
```

Run the smallest relevant test set while iterating, then the complete checks appropriate
to the change before requesting review. User-facing behavior changes must update the
corresponding documentation in the same pull request.

## Engine OpenAPI export

Run from the repository root with the locked development environment. The
exporter composes the guarded engine API in process. It needs no running
server, PostgreSQL instance, object store or provider credentials.

```bash
git rev-parse HEAD
uv run --locked python scripts/export_openapi.py -o openapi.json
uv run --locked pytest src/tests/surfaces/test_openapi_export.py -q
```

Record the revision from the first command with your verification result.
The test compares the checked-in schema with a fresh export and with the
shipped self-host route inventory. It checks audience tags, bearer security,
representative request and response models, and early rejection of missing
and invalid credentials on each protected route. The `Integration (surfaces)`
CI job runs the test when surface code changes.

When OpenAPI changes, regenerate the checked-in TypeScript contracts from
`packages/typescript-client` using `node scripts/npm.mjs run generate`
after installing locked dependencies with `node scripts/npm.mjs ci`. The
TypeScript client workflow runs `node scripts/npm.mjs run check:generated`
to check both the engine export and SDK artifacts for drift.

The same workflow runs `uv run --locked python scripts/check_client_api.py`.
It resolves the tagged engine inventory against recorded Python and TypeScript
requests, real `remember` commands, and the shared MCP catalogue. CLI and MCP
cases invoke the normal entry points using offline HTTP success and refusal
fixtures. The checker validates observed parameters, bodies and successful
replies against the fresh OpenAPI schemas. `check:parity` regenerates the SDK
fixtures in memory, and the Node suite replays them against the TypeScript client.

The per-operation fingerprints in `scripts/client_api_contract.json` include
parameters, request body, success and error responses, security and referenced
schemas. If a route or shape changes, review the affected clients and extend the
behavior cases, regenerate the applicable artifacts, then refresh the fingerprints
with `uv run --locked python scripts/check_client_api.py --write`.

Every tagged route must be reached by the SDKs, by a `remember` command
(`CLI_COMMANDS`) and by an MCP tool (each tool's `http_route`), or appear in
`SDK_EXCLUSIONS`, `CLI_EXCLUSIONS` or `MCP_EXCLUSIONS` in
`scripts/check_client_api.py` with a one-line reason. A new route fails the
check until it is mapped or excluded. Keep the exclusion lists short: exclude a
route only when it genuinely does not belong on that surface.

- The process liveness route, the optional operator connector API and cost
  export are outside the tagged inventory. The SDK's optional connector
  methods retain their separate conformance cases.
- Both SDKs, the CLI and MCP use the GET adjacent-chunks route for bounded
  windows. The equivalent POST form is available through HTTP.
- MCP uses its own `tools/list` for discovery instead of `GET /operations`.
- MCP's `search_claims` and `search_chunks` declare the POST search routes and
  send the same search as GET only when no `documents` or `time` filter is given.
- `remember mcp` reads `GET /deployment` itself to choose which tools to list;
  no tool returns it, as `pipeline_readiness` already reports the build
  revision and model bindings.

## Object-store integration tests

D155's object operations run the same contract against local files and **real
MinIO**. Compose's ordinary object store is SeaweedFS; it is not the MinIO gate.
Start a disposable MinIO container (Docker, curl and Python 3.11+ required):

```bash
.github/ci/start-minio.sh
REMEMBERSTACK_TEST_MINIO_ENDPOINT_URL=http://127.0.0.1:59000 \
  uv run pytest src/tests/adapters/test_minio_object_operations.py -q
# Publication also needs the disposable PostgreSQL database configured as
# REMEMBERSTACK_DATABASE_URL. Run all database suites sequentially:
REMEMBERSTACK_TEST_MINIO_ENDPOINT_URL=http://127.0.0.1:59000 \
  uv run pytest src/tests/adapters/test_minio_corpus_publication.py \
    src/tests/adapters/test_minio_corpus_lock_loss.py -q
# After testing:
docker rm --force rememberstack-minio-test
```

The helper builds a local image from MinIO's pinned, checksum-verified upstream
GitHub release binary because the old upstream registry images are unavailable.
It publishes only on loopback and uses disposable test credentials. To use an
existing MinIO service, set `REMEMBERSTACK_TEST_MINIO_ENDPOINT_URL` and optionally
`REMEMBERSTACK_TEST_MINIO_ACCESS_KEY` / `REMEMBERSTACK_TEST_MINIO_SECRET_KEY`
(defaults: `rememberstack` / `rememberstack_test`). Each test provisions and removes
its own randomly named bucket, including unfinished multipart uploads.

As with the PostgreSQL tests, missing connection configuration skips this suite.
An explicitly configured unavailable service, wrong provider or invalid credential
fails the tests. The adapters CI lane starts MinIO and sets the endpoint explicitly,
so this gate cannot silently skip there. Other S3-compatible providers require
separate qualification; MinIO results alone do not qualify them.

The local adapter keeps class records and temporary files in a sibling directory
`.<root-name>.metadata`, outside the reader tree. Keep that directory on the same
filesystem as the object root for atomic replacement; include it with local
storage when moving or backing up class records.

## Contributor agreement

RememberStack is currently distributed under the Apache License, Version 2.0. Before a
human-authored pull request can merge, its author must accept the
[RememberStack Contributor License Agreement](CLA.md).

The agreement is a license, not a copyright assignment: You keep ownership of Your
Contribution. It gives WriteIt.ai s.r.o. the rights needed to maintain and distribute
RememberStack, including a bounded ability to change the outbound license. Any such
license must continue to make source available and permit free self-hosting as defined
in the agreement.

The pull-request template contains the required assent checkbox. The `CLA` job fails
until the exact box is checked, and that status is required in `main` protection, so a
failure blocks merge. A later agreement version requires fresh assent and does not
retroactively expand an earlier grant.

If Your employer or another entity owns the work, only accept on its behalf if You are
authorized to bind it, and identify that entity in the pull-request field. The assent
covers only work that the accepting individual or entity owns or is authorized to
license. Split contributions into separate pull requests when copyrightable work from
another author is not covered by that authority. Identify third-party material and its
license in the pull request; do not represent third-party work as Your Contribution.

## Pull requests

- Keep each pull request limited to one coherent change.
- Explain the behavior change and the verification performed.
- Preserve unrelated user changes and generated artifacts.
- Do not include secrets, private data, or material You cannot license.
- Expect maintainers to request changes that reduce complexity or scope.

Project names and logos are governed separately by the
[RememberStack Trademark Policy](TRADEMARKS.md).
