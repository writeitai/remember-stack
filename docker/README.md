# PostgreSQL image release evidence

`Dockerfile.postgres` is the only supported database image definition.
[D157](../decisions.md#d157-postgresql-18-is-the-reference-server) selects
PostgreSQL 18 as the sole reference server. Graph reads use ordinary joins
and bounded traversal; SQL/PGQ no longer justifies a prerelease dependency.

## PostgreSQL 18 base (2026-11-14)

The official image is `postgres:18.6-trixie`, pinned in both stages to index
`sha256:979bccfde35445db618c65107d27d08c6c21339d8dc3ad4c830c6633947be0e4`.
The server package is `18.6-1.pgdg13+2`; build headers and libpq-dev use
that exact version. The official
[2026-10-28 image record](https://github.com/docker-library/repo-info/blob/e99222a981c35f94ad85de0959a7869ea8e39b80/repos/postgres/remote/18.6-trixie.md)
records the index and both platform builds, over two weeks before selection:

| Platform | Base child digest |
|---|---|
| linux/amd64 | `sha256:f4b419e350fd98d4a8429603295208976dea4c8fb8326d818d346d3e29f1db6f` |
| linux/arm64 | `sha256:9a74990a5c776ff210361b67616b347750c66c36ea0dfbb4f575d19fcc3d8437` |

[18.6 was released 2026-08-13](https://www.postgresql.org/docs/release/18.6/).
[18.7 was released 2026-11-12](https://www.postgresql.org/docs/release/18.7/),
so 18.6 is the newest minor satisfying the owner's two-week age floor on
2026-11-14. Future updates select the newest eligible 18.x minor and official
image digest; no floating tag enters the build.

## Extension pins and provenance

The PGDG packages retain their previous pinned versions and source releases,
now built for PostgreSQL 18:

- `postgresql-18-pgvector=0.8.6-1.pgdg13+2`
- `postgresql-18-partman=5.5.0-1.pgdg13+1`
- bundled `pg_trgm`, `pgcrypto`, `btree_gist`, `unaccent`, `fuzzystrmatch`
  and `plpgsql` from the official server image

Exact PGDG versions resolve from the all-versions `trixie-pgdg-archive`
repository, rather than the rotating current index. PGDG describes that
archive at <https://apt-archive.postgresql.org/>; pins verified 2026-11-14.

pg_textsearch keeps the same checksum-verified upstream 1.5.1 source:

- source revision: `fa142d049d08eac45a263f5abc250c8877ae076e` (tag `v1.5.1`)
- source archive SHA-256:
  `445a546810cbc17e15d05ff8a391f1b693eab382a2ef6efbf72e1c37fa0c48d0`
- PostgreSQL 18 is supported by the
  [pinned source](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/README.md#postgresql-version-compatibility).
  No local patch or forced `<limits.h>` include is used. The former compiler
  flag addressed RC 1 headers; PostgreSQL 18 builds without it.
- license determination: the pinned revision's `LICENSE` identifies **The
  PostgreSQL License** (`PostgreSQL` identifier); `NOTICE` carries the same
  license. Both are checksum-verified and copied to
  `/usr/share/doc/pg_textsearch/` in the runtime image.
- license SHA-256:
  `d33de21a123ce25b41722a5d10750984cb9c844c4d9b01add9e1b31f3ff452e5`
- notice SHA-256:
  `ff70cf4336c579957368a71c6b6b66ee8954011deef2b3d2c7a11f931080851d`

The build writes SHA-256 values for the installed shared object, control file
and SQL migrations to `/usr/share/doc/pg_textsearch/artifacts.sha256` and
runs the complete upstream SQL regression suite against its exact server.
The manifest is produced per architecture, so it records each binary build.

## Release matrix

For every PostgreSQL image update, build both `linux/amd64` and `linux/arm64`.
Record immutable image digests and embedded artifact manifests; run migrations
from empty, extension smoke tests, upstream pg_textsearch regression tests,
graph correctness/repair, backup/restore and the complete RememberStack suite.
Managed deployment requires amd64 to pass; self-host releases require both.

The release workflow builds natively on each architecture and publishes
`ghcr.io/writeitai/remember-stack-postgres:18-<engine-version>`. It attaches
`postgres-image-digests.json`, containing the immutable manifest and both child
digests, to the GitHub release. The release-contract checker enforces the
publisher, platforms, tag scheme and evidence artifact. Between releases,
build the checkout's image with `docker compose up --build`.

## Historical PostgreSQL 19 release evidence

The following records describe the former server. They are retained as dated
evidence and do not qualify PostgreSQL 18 or prescribe an upgrade path.

### 0.19.0 (PostgreSQL 19 RC 1), 2026-11-07

`Dockerfile.postgres` is unchanged since 0.18.3, and its pins were re-verified:
- **Base image.** The `19rc1-trixie` amd64 and arm64 children are still the pinned ones, and
  there is no RC 2 or GA image.
- **PGDG packages.** pgvector `0.8.6-1.pgdg13+2` and pg_partman `5.5.0-1.pgdg13+1` resolve.
- **pg_textsearch.** `v1.5.1` is still the latest release, and the source archive checksum
  matches.

### 0.18.3 (PostgreSQL 19 RC 1), 2026-11-07

`Dockerfile.postgres` is unchanged since 0.18.2, and its pins were re-verified:
- **Base image.** The `19rc1-trixie` amd64 and arm64 children are still the pinned ones, and
  there is no RC 2 or GA image.
- **PGDG packages.** pgvector `0.8.6-1.pgdg13+2` and pg_partman `5.5.0-1.pgdg13+1` resolve.
- **pg_textsearch.** `v1.5.1` is still the latest release, and the source archive checksum
  matches.

### 0.18.2 (PostgreSQL 19 RC 1), 2026-11-07

`Dockerfile.postgres` is unchanged since 0.18.1, and its pins were re-verified:
- **Base image.** The `19rc1-trixie` tag's amd64 and arm64 children are still the pinned ones
  (`2683c853…` and `40ec4717…`). No RC 2 or GA image is published.
- **PGDG packages.** pgvector `0.8.6-1.pgdg13+2` and pg_partman `5.5.0-1.pgdg13+1` still
  resolve.
- **pg_textsearch.** `v1.5.1` is still the latest upstream release. It still resolves to
  `fa142d04…`, and the source archive checksum matches.

The release workflow rebuilds each architecture on a native runner and runs the regression
suite. CI does the same natively for arm64 on this release's pull request.

### 0.18.1 (PostgreSQL 19 RC 1), 2026-11-07

0.18.0 was not released. Its release run built arm64 under QEMU emulation on an amd64 runner.
There, the pg_textsearch `parallel_build` regression test failed twice: the expected
"launched 2 of 2 requested workers" notice was missing. Since 0.18.1, the release builds each
architecture on a native runner (`ubuntu-24.04-arm` for arm64), and CI builds this
Dockerfile natively on arm64 for pull requests. `Dockerfile.postgres` itself is unchanged
from the record below.

These pins were re-verified for this release, and none changed:
- **Base image.** The pinned index `sha256:f6bab621…` lists the same children as the
  `19rc1-trixie` tag's current index, `sha256:4b3e4b2e…`. Both have amd64
  `sha256:2683c85306de152af6475f669cec1b172a3bfb77f7928cfe46b8c6dd338aa7a5` and
  arm64 `sha256:40ec4717d7f11d1456c7ced89d4bcdb7a7b8727ddadf652b09765b4440cbbc4b`.
  Only the riscv64 child was rebuilt. The server package is `19~rc1-1.pgdg13+1`.
- **PGDG packages.** The pgvector and pg_partman versions pinned above resolve from the
  archive.
- **pg_textsearch.** The source archive, LICENSE and NOTICE checksums match. The pinned
  revision is tag `v1.5.1`.

Each architecture was built locally from this Dockerfile with no build cache:
- arm64 was built natively.
- amd64 was built under Rosetta emulation on an Apple Silicon host.

| | linux/amd64 | linux/arm64 |
|---|---|---|
| Local image ID (not pushed) | `sha256:0a99756fdb833ec133ee75ae2458b3da9f95c15b23976931b227667fb96e2a3d` | `sha256:8affa2dd4a8ae6724f1aa085591aa5f73ec00c8c31d51c417b3c6edd9d5d5441` |
| `artifacts.sha256` (file SHA-256) | `67e0e0b3bb6cdb5239f1a9b6abaf7ae55980499100beaa05be4da476baf6e0d9` | `6c2e3491af7c05daaf602f5d57d11a9a39b27ff91981f9ca7241f0c8605e21a5` |
| `pg_textsearch.so` | `302e2123c5cb464dccd744bd520cf0980e48e4aacc0179184e6bcf9540b7fd7a` | `b3c183a5741815d142d339240d426ff48ad7f7822f88e04f5628c62e70ab5e16` |
| pg_textsearch regression suite | 82/82 | 82/82 |

The control and SQL files are byte-identical on both architectures. The `.so` lines differ.
The arm64 `.so` checksum also came out the same in an earlier cached build.

The published registry digests are a separate record. The tag workflow writes them to
`postgres-image-digests.json` on the GitHub release.

Tests run against these images:
- **amd64 (Rosetta).** The migration, pgvector/HNSW, pg_textsearch/BM25, pg_partman,
  one-hop, traversal, readiness, ops, query-space and restore suites: 277 passed.
- **amd64 (native).** Main CI run 42303684449 at `0de5d1506` ran the workers, surfaces
  and adapters integration suites against this Dockerfile on Linux amd64, and they passed.
- **arm64.** The complete suite: 4479 passed, 6 skipped, 2 xfailed. One test failed
  only because it pinned the old `info.version` (`test_d143_openapi_additive`). The
  release PR fixes it, and it now passes.

The local 2026-08-26 arm64 experiment passed 71/71 upstream pg_textsearch SQL
regression tests. It is evidence for this patch, not a substitute for the
two-architecture release matrix. A broken local Docker daemon is likewise an
open execution gate, never a reason to waive the image build.
