# Heap SHARE does not freeze BM25 allocation

**Status: completed local counterexample, not a qualified capture profile.**
This records the disposable experiment run on 2026-11-11. No production
database, customer data, provider, payment or live service was touched.
The [sanitised result](pg19_bm25_heap_share_counterexample_20261111.json)
contains the actual observed sizes and SQL states.

The already cached image was
`sha256:e97ef047169b272734e95f660cca3a4de74c637d0b7c3a7c633122a8f04af43e`,
with PostgreSQL `19rc1 (Debian 19~rc1-1.pgdg13+1)` and pg_textsearch `1.5.1`.
It ran with `--pull=never`, `--network=none`, a temporary data directory and
`shared_preload_libraries=pg_textsearch`. The owned container was removed.
The engine image revision `fb4a5f5a4cc2eb415df3a8f5b7908bb506bf58d0`
pins pg_textsearch source `fa142d049d08eac45a263f5abc250c8877ae076e` in
`Dockerfile.postgres`. Twelve inspected upstream files matched that commit's
Git tree blob hashes. This was not a populated full engine schema test.

## Reproduce only in an explicit disposable database

Use a fresh cached-image scratch container, not an existing application DSN.
The setup and both sessions below used its local `postgres` administrator;
they do not establish what a deployed ordinary application LOGIN can execute.

```sql
CREATE EXTENSION pg_textsearch;
CREATE TABLE capture_fixture(id integer PRIMARY KEY, body text NOT NULL);
CREATE INDEX capture_fixture_bm25 ON capture_fixture USING bm25(body)
  WITH (text_config='english');
INSERT INTO capture_fixture
SELECT i, repeat('alpha beta gamma ',20) || md5(i::text)
FROM generate_series(1,3000) i;
```

In session A, leave this transaction open:

```sql
BEGIN;
LOCK TABLE capture_fixture IN SHARE MODE;
SELECT pg_backend_pid();
```

In session B, confirm A's granted `ShareLock` on `capture_fixture` using
`pg_locks` and its returned backend PID. Measure, invoke maintenance, measure
again, and confirm A still has that granted lock:

```sql
SELECT pg_relation_size('capture_fixture_bm25');
SET lock_timeout='500ms';
SET statement_timeout='2s';
SELECT bm25_spill_index('capture_fixture_bm25');
SELECT pg_relation_size('capture_fixture_bm25');
```

The spill succeeded while A retained its heap SHARE lock. The index grew from
**401,408 to 729,088 bytes**. A's granted heap lock was confirmed before and
after the spill. Roll back A and remove the scratch container afterward.

A separate transaction attempting
`LOCK TABLE capture_fixture_bm25 IN SHARE MODE` failed with SQLSTATE `42809`
(psql exit 3). SQL `LOCK TABLE` is therefore not an available index-lock
primitive in this tested build.

## What this establishes

Heap SHARE alone does not exclude an admitted maintenance entry that writes
the index independently of heap DML. Retaining an RR snapshot or AccessShare
pin cannot repair that physical-byte gap. A guard must qualify the actual
extension entry permissions and writer paths before reporting allocation at T.

The upstream
[maintenance API](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/access/compaction_api.c)
and [native compaction locks](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/segment/compaction.c)
(retrieved 2026-11-11) distinguish index-level maintenance admission from the
heap lock. Their presence is a reason to inspect the real source and ACLs,
not a proof that every runtime role can call them or that one particular
replacement guard is necessary.

The next qualification must inspect the existing engine writer roles,
extension ownership/EXECUTE grants, maintenance entry points and background
configuration. Prefer the smallest enforced existing path. Do not infer that
revoking one grant is sufficient, introduce a new native module by default,
or accept a cooperative advisory convention as physical exclusion. No guard,
customer quota, price, waiver or financial policy is selected by this finding.
