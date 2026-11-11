# The SQL index guard still leaves TOAST maintenance unfenced

**Status: completed disposable counterexample, not a live service probe.**
The [previous ordinary-role result](pg19_bm25_native_guard_20261111.md)
establishes a useful SQL index lock, but does not close the physical relation
set. This experiment tested the TOAST storage that the real engine catalog
contains for 345 physical tables. The
[sanitised result](pg19_toast_guard_counterexample_20261111.json) records the
observed states and bytes. No production database, customer data or live
grant was touched; all owned scratch containers were removed.

Use the same explicit disposable cached PG19rc1 / pg_textsearch 1.5.1 image
and fixture as the preceding experiment. Add incompressible, toasted rows and
delete them before the guard starts; the experiment used eight rows, each a
concatenation of 10,000 distinct MD5 strings. Ordinary autovacuum was disabled
only on this fixture to make the explicit maintenance experiment repeatable:

```sql
ALTER TABLE capture_fixture SET
  (autovacuum_enabled=false, toast.autovacuum_enabled=false);
INSERT INTO capture_fixture
SELECT 7000+i,
  (SELECT string_agg(md5(g::text),'') FROM generate_series(1,10000) g)
FROM generate_series(1,8) i;
DELETE FROM capture_fixture WHERE id>7000;
```

In session A, retain the verified heap ShareLock and public BM25 index
AccessExclusiveLock from the preceding SQL experiment. In a separate local
administrator session, resolve the TOAST table through `reltoastrelid`,
measure it, and execute native `VACUUM` on that exact TOAST relation under
500 ms lock and three-second statement timeouts. This is the native
maintenance path, not a customer query or proof that the runtime LOGIN can
name a TOAST table directly.

The vacuum **succeeded** while both parent/public-index locks remained held.
TOAST total allocation changed from **2,711,552 to 65,536 bytes**. Heap SHARE
plus a public index lock therefore cannot supply the whole physical quantity.
The original RR view does not freeze these files either.

The ordinary owner separately attempted SQL `LOCK TABLE` on its TOAST table
and a no-move `ALTER INDEX` on its TOAST index; both refused with `42501` in
the fixture. Adding a private view did not repair the missing lock: the fixed
collector function succeeded, but an explicit `pg_locks` check showed no
ShareLock on the TOAST heap. The pinned PostgreSQL
[LOCK implementation](https://github.com/postgres/postgres/blob/REL_19_RC1/src/backend/commands/lockcmds.c)
skips TOAST kinds when traversing a view. Its
[autovacuum implementation](https://github.com/postgres/postgres/blob/REL_19_RC1/src/backend/postmaster/autovacuum.c)
also schedules TOAST relations independently. Sources retrieved 2026-11-11;
neither a view nor an assumption about a parent-only vacuum is exclusion.

The successful follow-up retains existing SQL: reapply the TOAST option's
actual value through its public parent, or RESET that option when absent.
This holds the required TOAST maintenance lock. A new C helper and image
rebuild are unnecessary; see the ordinary-role validation below. Exact
closure, deadlines, authority and in-guard physical sampling still need full
native qualification. No customer SQL API, all-writer permission framework,
ownership change, background policy, price or customer limit follows.

## Existing SQL also fences TOAST maintenance

In the same fixed NOLOGIN definer function, the ordinary collector invoked
`ALTER TABLE public.capture_fixture RESET(toast.autovacuum_enabled)` when that
option was absent. Native `pg_locks` showed a retained
ShareUpdateExclusiveLock on the TOAST heap. Native TOAST VACUUM and runtime
BM25 spill both refused with `55P03`; the collector's direct table read and
owner-role assumption still refused with `42501`.

This is a logical settings no-op, **not** a catalog-write no-op. The observed
TOAST catalog row `xmin` changed from 671 to 692 inside the guard, while its
OID, filenode and NULL options remained the same. Rolling the guard back
restored `xmin=671` and the exact original options. The source
[ATExecSetRelOptions](https://github.com/postgres/postgres/blob/REL_19_RC1/src/backend/commands/tablecmds.c)
opens the TOAST relation with the command's lock mode, updates both catalog
rows, and closes the TOAST relation with NoLock. Source retrieved 2026-11-11.
Explicit options must be SET to their actual existing value; absent options
must be RESET. Do not add a default or silently disable background work.

The guard samples allocation before its rollback release. The aborted catalog
writes still have WAL, invalidation and cleanup cost; rollback does not make
collection free. Their populated-profile pause and I/O cost remains an
operator qualification requirement, not a new customer charge or proven floor.
