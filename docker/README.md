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
against the Beta 4 headers. No newer build exists. Between Beta 4 and RC 1 the
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

The local 2026-08-26 arm64 experiment passed 71/71 upstream pg_textsearch SQL
regression tests. It is evidence for this patch, not a substitute for the
two-architecture release matrix. A broken local Docker daemon is likewise an
open execution gate, never a reason to waive the image build.
