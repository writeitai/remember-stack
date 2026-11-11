# Storage capture checks

The initial implementation supplies the closed D152 record types and pure
canonical encoding/exact-membership checks. It does not collect native bytes,
authenticate a provider, qualify a storage profile, persist capture completion
or authorise a financial charge. Synthetic protocol fixtures are not evidence
of a real storage cut.

On 2026-11-11 the focused suite passed **22 tests in 0.58 seconds** under
Python 3.12.2 and Pydantic 2.13.4. It checks zero-byte reference preservation,
bounded streaming consumption, exact empty sets and ACK replay, missing-page
refusal, duplicate/reordered members, payload tampering, noncanonical unsigned
values and content-value suppression in validation errors. It also round-trips
the JSON manifest, independently matches the six recorded existing-v3 terminal
digest vectors and refuses a host-local timestamp without native timezone
evidence. The v3 helper preserves its existing plain hash, UUID/string forms,
nulls and JSON integers; it does not apply the new capture-domain encoding.

Run in the normal repository development environment:

```bash
uv run pytest src/tests/core/test_storage_capture.py -q
uv run ruff check src/rememberstack/model/storage_capture.py \
  src/rememberstack/core/storage_capture.py src/tests/core/test_storage_capture.py
uv run pyright src/rememberstack/model/storage_capture.py \
  src/rememberstack/core/storage_capture.py src/tests/core/test_storage_capture.py
```

Scoped Ruff/format and Pyright checks passed. The local worktree reused an
already installed engine environment with an explicit Python interpreter for
Pyright; no dependency or image download was needed. Full current-head CI and
the actual native/provider/profile tests remain delivery requirements. The
existing two-version/provider-call tests must still qualify D56 when the native
consumer is integrated with ingestion.

The separate native relation guard uses the existing PostgreSQL SQL lock
paths, including unchanged-value TOAST reloptions and unchanged-tablespace
index DDL. It installs only a fixed function using already provisioned
definer/collector roles; it creates no LOGIN, membership, secret, migration,
CLI, scheduler or API. Its supported schema/build, native deadline and
transaction bounds are explicit. Allocation is sampled while those locks
remain held; rollback release restores the logically unchanged catalog
options but does not erase the operator's WAL/cleanup cost.

The completed primitive counterexamples and ordinary-role observations are
tracked in [the inventory analysis](../../plan/analysis/storage_inventory/README.md).
The new full-engine native regression suite uses only
`REMEMBERSTACK_STORAGE_GUARD_TEST_DATABASE_URL`, refuses a database whose name
does not start `capture_guard_`, and requires that scratch database to start
empty. It never reads the normal application database setting or resets an
existing schema. An external disposable-container harness owns removal.
The fixed function now passed **nine native cases in 4.55 seconds** on the
cached PostgreSQL 19rc1 image after applying the actual engine migrations and
normal deployment bootstrap. The
[sanitised component receipt](../../plan/analysis/storage_inventory/pg19_relation_guard_native_validation_20261111.json)
pins the exact module/test blobs. It observed 350 tables, 860 public indexes,
345 owned TOAST heaps and 345 TOAST indexes, with no duplicate physical OIDs.
The 10,117,120 allocated fork bytes exactly matched the physical-table total.
Its native guard-through-sample duration is recorded in that receipt; this is
a fresh schema measurement, not populated-load pause, floor or clock proof.

The actual collector LOGIN could invoke the fixed operation but could neither
SELECT deployment data nor assume the definer role (`42501`). Concurrent
native BM25 spill and direct TOAST VACUUM refused (`55P03`). An independent
original read-only RR session acquired its first snapshot while the guard held.
Explicit existing TOAST options and absent options both survived rollback
exactly, including original catalog `xmin` values. A real 500 ms native
transaction timeout terminated the holder (`25P04`), released its locks and
restored catalog state. Busy writers, unknown roots, stale-view isolation,
missing native bounds, wrong actual LOGIN and owning collector authority
refused. Reinstallation removed a superseded explicit EXECUTE grant.

The definer's pre-provisioned privileges include inheritance of the actual
engine owner and PostgreSQL's `pg_read_all_settings`, plus the four explicit
catalog MAINTAIN grants installed for schema/function/extension exclusion.
The built-in settings privilege reads more than one setting; it belongs only
to this NOLOGIN definer, whose fixed function checks the active preload list
and returns no setting value. The collector has neither membership and gains
only private-schema USAGE and the fixed function's EXECUTE. The installer does
not create roles, membership or credentials. The fixture uses a separate app
migrator because REASSIGN OWNED from the cluster bootstrap role correctly
refuses protected system objects; it grants only its scoped runtime/collector
CONNECT rather than restoring PUBLIC CONNECT.

For a rerun, start an owned container from the receipt's cached image with
`--pull=never`, a tmpfs data directory, only a loopback published port,
`POSTGRES_HOST_AUTH_METHOD=trust`, a synthetic administrator and an empty
database named `capture_guard_...`; preload only `pg_textsearch`. Set the
separate test URL above to that explicit scratch target, then run:

```bash
uv run pytest src/tests/spine/test_storage_capture_guard_pg.py -q \
  -o junit_family=xunit1 --junitxml=/tmp/native-relation-guard.xml
```

Remove only that owned container afterward. The current hosted test lanes do
not set this separate input; these native results are explicit local evidence,
not an assertion that hosted skipped cases ran. Existing current-head CI is
still required. This is not a completed D152 T/clock/view witness, a classified
or chargeable capture, or a qualified populated profile. No role or function
has been installed live.

The function's installed search path is explicitly `pg_catalog, pg_temp`.
PostgreSQL searches temporary schemas for relations and types, not functions
or operators ([lookup rules](https://www.postgresql.org/docs/19/runtime-config-client.html#GUC-SEARCH-PATH),
retrieved 2026-11-11). Its
[SECURITY DEFINER guidance](https://www.postgresql.org/docs/19/sql-createfunction.html#SQL-CREATEFUNCTION-SECURITY)
puts the temporary schema last. The existing
`p9_02_0023_query_space_roles.py` migration already revokes PUBLIC database
privileges, and the normal migrated collector cannot create temporary objects.
The one focused regression deliberately grants TEMP only within its owned
scratch case, then restores it. A collector-owned temporary `pg_database`
view, with SELECT granted to the definer, invoked its marker function as that
definer before the path correction (`P0001`). With the corrected path, the
same case returns a real reconciled sample and never runs that marker.
The fix changes no production permission or ordinary collector interface.
