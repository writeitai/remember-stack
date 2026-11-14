# PostgreSQL 18 reference server (2026-11-14)

## Problem and owner constraints

D98 adopted PostgreSQL 19 Beta 3 to use SQL/PGQ property graphs. Beta 4
removed that feature; the owner's 2026-11-06 RC 1 amendment replaced the
one-hop graph query with ordinary joins. The remaining engine uses no
PostgreSQL 19 feature. Running a release candidate now buys no capability,
and pg_textsearch 1.5.1 needs a forced `<limits.h>` include with RC 1 headers.
The owner chooses one stable major, PostgreSQL 18, with no dual-version
support or compatibility shims. There are no user databases to preserve.

## Alternatives and evidence

- **Stay on 19.** Retains prerelease server/catalog risk and the compiler
  workaround without providing SQL/PGQ. Rejected.
- **PostgreSQL 18.** Supports the current relational graph and extensions.
  Keep pgvector 0.8.6, pg_partman 5.5.0 and pg_textsearch 1.5.1 pinned;
  change only their server target. Accepted as D157.
- **Support both.** Adds guards, image and test combinations with no user
  need. Rejected. Revisit a major only through a new reviewed decision.

Primary sources retrieved 2026-11-14:

- [PostgreSQL 18.6 release notes](https://www.postgresql.org/docs/release/18.6/)
  date the release to 2026-08-13.
- [18.7 release notes](https://www.postgresql.org/docs/release/18.7/)
  date it to 2026-11-12. It fails the owner's two-week minimum age.
  Therefore 18.6 is the newest eligible minor, not the newest published one.
- [Official image record on 2026-10-28](https://github.com/docker-library/repo-info/blob/e99222a981c35f94ad85de0959a7869ea8e39b80/repos/postgres/remote/18.6-trixie.md)
  records index `sha256:979bccfde35445db618c65107d27d08c6c21339d8dc3ad4c830c6633947be0e4`
  and the amd64/arm64 builds. Both the index and children meet the age floor.
- [PGDG all-versions archive](https://apt-archive.postgresql.org/) supplies
  the same pinned PG18 package versions: pgvector `0.8.6-1.pgdg13+2` and
  pg_partman `5.5.0-1.pgdg13+1`. Build headers match server
  `18.6-1.pgdg13+2` exactly.
- [pg_textsearch pinned source](https://github.com/timescale/pg_textsearch/tree/fa142d049d08eac45a263f5abc250c8877ae076e)
  supports PG18. Its archive, license and notice checksums stay unchanged.
- [PostgreSQL 18 constraint catalog](https://www.postgresql.org/docs/18/catalog-pg-constraint.html)
  already includes first-class NOT NULL constraints (`contype = 'n'`);
  their presence is not a PG19 dependency. The built image exposes
  `btree_gist` 1.8 rather than PG19's 1.9; the native capture guard must
  use the actual PG18 version.

## Failure, recovery and boundaries

Fresh PostgreSQL 18 storage runs the complete existing Alembic chain. A PG19
physical directory cannot become PG18 storage by changing its image, and
`pg_upgrade` does not downgrade. Development stores are recreated and replayed.
Any managed data-preservation plan belongs to the operator and must qualify a
logical export/import or source replay independently; this change supplies no
PG19 conversion mechanism. Graph, P1 and least-privilege capture gates keep
refusing an unexpected server major. No ingestion policy, provider prompt,
content identity, embedding key or extraction/reuse generation changes.

Current operating contracts live in the [graph design](../designs/p2_graph_design.md)
and [P1 design](../designs/postgres_p1_search_projection_design.md).
Build and suite evidence belongs in [docker/README.md](../../docker/README.md)
and the PR, not in historical benchmark receipts.

## Observed lock-timeout difference

The complete PG18 run exposed a runtime dependency on server error prose in
`spine/profile_refresher.py`. A real advisory-lock timeout reports SQLSTATE
`57014`, message `canceling statement due to statement timeout`, and no error
context on 18.6. The old check also required the phrase `advisory lock`, so
both the shared identity-epoch and exclusive evidence-lock tests returned an
ordinary database failure instead of typed profile contention.

Use SQLSTATE plus the identity of the two fixed advisory-lock statements.
Their only work is the engine-owned lock operation; a timeout there means
that bounded acquisition did not finish. The existing negative proof sends
an unrelated `pg_sleep` statement through the helper and must still receive
an ordinary database failure. Parsing localized server prose is rejected.
Switching to immediate try-locks or adding another timeout policy is also
unnecessary: keep the existing bounded wait and its retry semantics.

This restores the current profile-publication contract on 18. It changes no
reuse input or generation. Evidence workers retain their committed fact work
and handle disposable profile contention without replaying paid inference.
