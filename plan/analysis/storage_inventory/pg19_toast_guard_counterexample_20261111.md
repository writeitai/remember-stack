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

The next implementation uses the existing PostgreSQL relation-lock primitive
for the complete native-owned relation set, including TOAST and indexes,
behind the fixed, owner-installed operation. A small internal native helper
can expose that primitive to the NOLOGIN definer while the collector retains
only EXECUTE on the closed operation. The helper is not a customer SQL API,
an all-writer permission framework, or permission to change ownership,
background policy, prices or customer limits. Exact closure, deadlines,+authority and in-guard physical sampling still need native qualification.
