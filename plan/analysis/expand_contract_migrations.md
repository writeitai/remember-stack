# Keeping the running engine compatible during a migration

Non-binding analysis, 2026-11-26.

An operator can migrate the database while the previous engine still serves
requests and runs workers. D159 illustrates the break this can cause.
`p9_54_0075` removes `forget_manifests` and renames a `pipeline_stage`
value. `p9_55_0076` drops `documents.deleted_sync_cycle_id`. Code that
still refers to the old names can fail before the replacement starts. The
existing Alembic lock serializes migration writers within one database.
It does not make new schema objects compatible with old readers or writers.
[D159](../../decisions.md#d159-archive-and-forget-replace-delete-forget-hides-at-once-and-sweeps-until-clean) ·
[Forget revision](../../src/rememberstack/spine/migrations/versions/p9_54_0075_forget_records.py) ·
[Archive revision](../../src/rememberstack/spine/migrations/versions/p9_55_0076_archive.py) ·
[Migration runner](../../src/rememberstack/spine/migrations/env.py)

Keep migrating before switching code. Adding fields while old code still
works lets the application move to the new shape separately from cleanup.
Keep the old shape until the running release has stopped using it. A host
that skips the intermediate release has to account for the code it actually
runs. The overlap can temporarily keep a second copy of a field or index.
It does not reprocess documents or add paid calls to ingestion.

The contributor rule and ordinary change review are sufficient for this
scope. A table/column DROP or RENAME lint would flag safe removals too and
miss D159's enum rename, new constraints, and semantic changes. Running
old releases in CI or coordinating per-database cutovers adds machinery
without replacing the author's compatibility check. Use planned downtime
when preserving the old contract is impractical. Even compatible DDL can
block live queries while it holds a lock.
[PostgreSQL 18 ALTER TABLE](https://www.postgresql.org/docs/18/sql-altertable.html#SQL-ALTERTABLE-DESCRIPTION)
(retrieved 2026-11-26).
