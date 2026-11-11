# A native index guard for the measured PostgreSQL build

**Status: completed disposable primitive qualification, not a qualified engine
capture profile or a deployed capability.** This follows the
[heap SHARE counterexample](pg19_bm25_heap_share_counterexample_20261111.md).
The [sanitised result](pg19_bm25_native_guard_20261111.json) records the actual
observations. No production database, customer data, provider, payment or live
grant was touched. Each owned scratch container was removed.

## Existing runtime authority matters

The current UMC provisioning implementation reassigns engine objects and
extension functions from `umc_admin` to `umc_runtime`, then restores the
database owner. Source:
`src/ultimate_memory_cloud/provisioning/database.py`, `_REASSIGN_TO_RUNTIME`,
accepted UMC main `8023cc00`; exact blob
`a68592e97c9c3a5700b74209cb53a1cc89442812`. This existing ownership supports
ordinary engine maintenance. It must not be silently replaced with an
unqualified new runtime ownership model.

The disposable experiment applied that exact reassignment SQL to a fixture
table, BM25 index and extension. The actual `umc_runtime` LOGIN had
`rolsuper=false`, `rolcreatedb=false` and `rolcreaterole=false`, and owned both
the index and `bm25_spill_index(text)`. Revoking PUBLIC and its own EXECUTE
made the function call fail with `42501`. The owner could then grant EXECUTE
back to itself. Its spill again grew the index from **401,408 to 729,088 bytes**
while another session retained a verified heap ShareLock. A self-revoked
function ACL therefore does not enforce exclusion against this existing
owner. This is a source-equivalent ownership test, not a deployed credential
or full engine role audit.

## The smallest tested index exclusion uses existing SQL

Using the same cached PostgreSQL 19rc1 / pg_textsearch 1.5.1 image as the first
counterexample, the ordinary runtime owner executed:

```sql
BEGIN;
SET LOCAL lock_timeout='500ms';
SET LOCAL statement_timeout='2s';
LOCK TABLE public.capture_fixture IN SHARE MODE NOWAIT;
-- This is the index's CURRENT tablespace, not a requested storage move.
ALTER INDEX public.capture_fixture_bm25 SET TABLESPACE pg_default;
SELECT pg_relation_size('public.capture_fixture_bm25');
-- Keep this same transaction alive while inspecting the other session.
```

`pg_locks` confirmed an AccessExclusiveLock on the index. Its OID, filenode,
tablespace and catalog tuple `xmin` were identical before, during and after
the transaction. A concurrent actual runtime LOGIN attempting
`bm25_spill_index` failed with `55P03` under a 500 ms lock timeout. The measured
index remained **1,122,304 bytes** throughout that refused attempt. The guard
was then committed. This qualifies the no-move case with the fixture's actual
default tablespace; an implementation must derive and qualify the current
tablespace rather than accept a caller-authored destination.

An independent original repeatable-read, read-only transaction acquired an
AccessShare heap pin and its first `pg_current_snapshot()` while the guard
held. After guard release, another session inserted a row and spilled the
index. The original view still saw **6,000 rows**, whereas the live view saw
**6,001**, but a later physical-size query in that original view reported
**2,113,536 bytes**. Thus the physical byte values must be spooled while the
short guard holds; a retained snapshot does not preserve old file allocation.
During the index AccessExclusiveLock, ordinary row-query planning can itself
wait on that index. The tested snapshot-acquisition query was the pure
`pg_current_snapshot()` operation, not later row enumeration.

## An ordinary collector can invoke a fixed operation

A second fixture used a dedicated NOLOGIN, NOSUPERUSER, NOCREATEDB,
NOCREATEROLE definer. It inherited the existing engine-owner role and had
MAINTAIN only on the three catalog relations below. The collector was a
separate LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOINHERIT role. It had
only database CONNECT, private-schema USAGE and EXECUTE on this fixture's
fixed SECURITY DEFINER operation. The function's search path was
`pg_catalog`; its object names were fixed inside the function:

```sql
LOCK TABLE pg_catalog.pg_class, pg_catalog.pg_namespace, pg_catalog.pg_proc
  IN SHARE MODE NOWAIT;
LOCK TABLE public.capture_fixture IN SHARE MODE NOWAIT;
ALTER INDEX public.capture_fixture_bm25 SET TABLESPACE pg_default;
```

The actual collector LOGIN successfully invoked the operation and returned
its unchanged `session_user=capture_collector`,
`current_user=capture_guard_owner` and the measured index size. The ordinary
runtime owner's concurrent spill again refused with `55P03`. The collector's
`SET ROLE capture_guard_owner` and direct customer-table SELECT both refused
with `42501`. The direct SELECT permission test ran after releasing the index
guard, so planner lock contention was not mistaken for a permission denial.
`has_table_privilege(...,'SELECT')` was false while the guard held as well.

These are disposable fixture role names and a fixed fixture operation, not an
installer, deployed authority or generic SQL executor. The definer is
privileged by its explicit ownership inheritance and catalog MAINTAIN; the
collector is not granted that membership or an owning password.

## Implementation consequences and remaining scope

An existing SQL index lock is a viable primitive for this build; neither a
new C extension nor an all-writer permission framework is justified by the
observed BM25 failure. The native operation still needs the exact engine
schema/deployment/profile closure, all admitted roots, indexes, TOAST and
forks, absolute deadline and finite transaction bounds, actual original-view
and clock proof, and immutable in-guard physical samples. Other access
methods, sequences, background writers and extension side resources are not
qualified by this one-index experiment. Native schema/function mutation
exclusion and minimal ordinary collector authority must remain enforced.

The original counterexample's cached image/source pins and scratch-only
invocation restrictions apply. The fixed statements above and first
counterexample provide the rerunnable two-session primitive; no production
DSN, password, free-form table API or automatic live grant is supplied.
Prices, customer limits, reuse identities, component mapping and financial
policy are unchanged.
