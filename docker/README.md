# PostgreSQL image release evidence

`Dockerfile.postgres` is the only supported database image definition. It pins
the PostgreSQL 19 prerelease base (release candidate 1, `19rc1-trixie`) by
digest, pins PGDG extension package versions, and builds pg_textsearch from a
checksum-verified source archive (no local patch: v1.5.1 supports PostgreSQL 19
upstream, D142).

The RC 1 headers no longer include `<limits.h>` indirectly, and pg_textsearch
v1.5.1 uses `INT_MAX` without including it (upstream issue
[timescale/pg_textsearch#586](https://github.com/timescale/pg_textsearch/issues/586),
open on 2026-11-06). The build adds `-include limits.h` to the server's own
preprocessor flags instead of changing the source; drop the flag once a pinned
release includes the header itself.

pgvector is pinned to the PGDG build `0.8.6-1.pgdg13+2`, which PGDG rebuilt
against the Beta 4 headers; that is the tested pin (PGDG also publishes
`0.8.7-1.pgdg13+1`). Between Beta 4 and RC 1 the
index access-method interface (`access/amapi.h`) is unchanged, and the one
changed index-vacuum struct (`IndexVacuumInfo`, a new `is_autovacuum` flag)
moves only fields pgvector 0.8.6 does not read (checked against the
`postgresql-server-dev-19` 19~beta4 and 19~rc1 packages and the v0.8.6 source,
2026-11-06). pg_partman's background worker uses only interfaces whose headers
are unchanged between Beta 3 and RC 1.

Exact pgvector and pg_partman Debian versions resolve from PGDG's all-versions
`trixie-pgdg-archive` repository, not the rotating current-version index. PGDG
documents that archive as the daily-updated repository containing every
released package version; retrieved 2026-08-27 from
<https://apt-archive.postgresql.org/>.

## pg_textsearch provenance

- source revision: `fa142d049d08eac45a263f5abc250c8877ae076e` (tag `v1.5.1`)
- source archive SHA-256:
  `445a546810cbc17e15d05ff8a391f1b693eab382a2ef6efbf72e1c37fa0c48d0`
- PostgreSQL 19: supported upstream since v1.5.0 as best effort with
  non-blocking CI ([README at the pinned revision](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/README.md#postgresql-version-compatibility)),
  so this image's own integration suite is the PostgreSQL 19 gate; the former
  local compatibility patch is retired (retrieved 2026-10-21)
- license determination: the pinned revision's `LICENSE` identifies **The
  PostgreSQL License** (`PostgreSQL` identifier), and its `NOTICE` says the
  extension is licensed under that license; unchanged at v1.5.1 (same
  checksums), retrieved 2026-10-21
- license SHA-256:
  `d33de21a123ce25b41722a5d10750984cb9c844c4d9b01add9e1b31f3ff452e5`
- notice SHA-256:
  `ff70cf4336c579957368a71c6b6b66ee8954011deef2b3d2c7a11f931080851d`
- license evidence: both files are checksum-verified during the build and
  copied to `/usr/share/doc/pg_textsearch/` in the runtime image

The build writes SHA-256 values for the installed shared object, control file,
and SQL migration files to
`/usr/share/doc/pg_textsearch/artifacts.sha256`. Because that file is produced
inside each build platform, it is also the per-architecture binary record.

## Release matrix

For each PostgreSQL beta, RC, and GA candidate, build both `linux/amd64` and
`linux/arm64`. Record the immutable image digest and the embedded artifact
manifest for each architecture, then run migrations, extension smoke tests,
the pg_textsearch regression suite, graph correctness tests, and the complete
RememberStack test suite. Managed deployment remains blocked unless amd64
passes; self-host release remains blocked unless both architectures pass.

The tag workflow mechanically publishes the two-platform manifest and attaches
`postgres-image-digests.json`, containing its immutable digest plus both child
digests, to the GitHub release. The release-contract checker fails if that job,
either required platform, or the digest evidence artifact disappears.

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
