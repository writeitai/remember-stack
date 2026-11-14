# Original native storage view

This implementation leaf follows accepted D152
([binding contract](../../plan/designs/storage_capture_consumer_design.md) §2,
§4–5). It retains a real PostgreSQL read-only repeatable-read transaction and
the allocation records obtained under the existing fixed native guard. It
does not implement a complete classified capture, provider decoder, immutable
completion store, financial receiver or deploy command. Loading a profile
model does not qualify or authenticate its source, schema or UTC error bound.

The operator installs `install_native_capture_catalog` from
`rememberstack.spine.storage_capture_catalog` with the already provisioned
guard definer/collector roles. The installer requires that same fixed guard
boundary; it creates no LOGIN, membership, broad table read grant or secret.
Its fixed read operation uses the actual collector LOGIN and admits only an
original read-only repeatable-read transaction.

The engine runtime uses:

```python
from rememberstack.spine.storage_capture_view import NativeStorageCaptureCatalog

catalog = NativeStorageCaptureCatalog(connect=collector_connect, profile=profile)
with catalog.open_view(request=request) as view:
    witness = view.witness
    allocations = view.allocation_sample
    for reference in view.iter_store_references():
        ...
    for terminal in view.iter_terminal_members():
        ...
```

`collector_connect` returns a fresh ordinary collector connection; it never
supplies an owning role or arbitrary role override. The profile is independently
accepted operator configuration, not customer request data. The adapter checks
the request's native/schema profile identities before opening transactions.
Provider capture must be armed independently before entering this context;
no provider materialisation, callback or processing work runs under the guard.

Entry opens both sessions before acquiring locks, finishes configuration
transactions, acquires the fixed guard and immutable relation/fork sample,
then brackets the reader's **first data SELECT** with the original guard's
native clock reads. The reader pins every extraction relation with AccessShare.
Its actual backend PID/server start, virtual transaction and `pg_current_snapshot()`
identity remain attached to every bounded page. A virtual transaction is used
because a read-only transaction need not have an assigned permanent XID. The
wire witness retains the native virtual identity and a SHA256 identity of the
original snapshot text; that complete original text remains in the retained
engine view, never a borrowed/exported snapshot.

Entry waits only until the native clock proves the full configured UTC-error
interval around T elapsed, then checks that the original guard transaction is
still alive, rolls it back and obtains a definite native release bracket.
Only then does it yield the retained reader. Allocations never get resampled
after release. Host monotonic readings detect native clock discontinuities;
they do not establish T or replace independently qualified native UTC error.
Loss, a missed cut, an expired transaction or an unproved release refuses the
attempt. Context exit closes both original sessions; it has no background
task, reconnect, re-lock or historical continuation.

The allocation digest uses ASCII
`rememberstack.native_relation_allocations.v1`, NUL, then compact sorted-key
ASCII-escaped UTF-8 JSON. The complete document has `contract`, UUID32
`deployment_id`, `schema_head`, `allocated_fork_bytes`,
`physical_table_total_bytes` and the exact ordered `relations` array from the
fixed native sample. Every actual zero fork remains in that array. This
private schema must be pinned by the qualified native profile.

`iter_store_references` reads actual catalog locator seeds without live/current
or success-only filters: raw objects; all five representation URI fields;
structure sidecars; supplied reference artifacts; K page and
curation paths; K planner/compiler transcripts; and every native forget
intent, including `preparing`. A forget seed carries its actual UUID with
`locator_kind=journal_intent`; the qualified journal adapter derives its
concrete key and proves stored presence. PostgreSQL alone cannot assert the
ObjectStore or LocalFS journal layout. Native content identities and locators use private fields with
suppressed repr/validation inputs. Manifests, K origin/history and
unmatched store resources still require the engine's qualified store profile
and complete provider evidence. A locator seed is neither a provider item nor
a publishable storage member. Missing lengths remain unknown; reference
lengths are never summed into PostgreSQL allocation.

`iter_terminal_members` reads every actually committed outcome in that same
view, scoped through its native measurement. It does not filter either
outbox's delivery state or use a sequence maximum/range. The measurement UUID
is the member identity, and the existing canonical v3 body determines the
receipt digest. Post-cut rows and ACK changes cannot alter this original view.
Both iterators use fixed keyset pages of at most 1,024 rows, with no later
pool or caller-supplied SQL.

`iter_corpus_documents` uses the same original view and page bound. It returns
each document's recorded cleanup folders, including deleted/unselected rows,
and its nullable served-reading inputs. The classifier recomputes the current
D155 publication revision before expanding reader-file references. Paths alone
do not prove publication; no snapshot registry is consulted. All selector joins
are pinned with AccessShare during acquisition. Both helpers require exact
schema head `p9_52_0073`.

The first native run exposed a real integration boundary: the guard's native
index exclusions hold AccessExclusive on `deployments_pkey`. Reading public
deployment/configuration rows under that guard tried to read the locked index
and refused with `55P03`. The current path acquires the first snapshot and
extraction pins using native session metadata only. It materialises the native
deployment and store roots **after release through that same snapshot**, then
checks deployment and unchanged session/transaction/snapshot identities before
yielding. It neither disables indexes nor weakens the existing guard. Backend
identity uses the actual PID/server-start pair; it requires no extra stats
privilege for a definer to inspect the collector's session.

The [native component receipt](storage-capture-original-view-validation-20261111.json)
pins exact source/test blobs. Five native cases passed in **7.76 seconds** on
the cached PostgreSQL 19rc1 image with real migrations and deployment bootstrap.
They observed 1,030 original references across bounded pages and 1,900 immutable
allocated-relation records. The original-view acquisition bracket was 5,137
microseconds in this synthetic fixture, not a populated-load pause bound.
Post-cut DML grew current table allocation while captured bytes/references
remained unchanged, and extraction DDL refused with `55P03`. Failed
representation references and preparing native forget UUID intents remained
visible. Actual committed terminal rows
survived post-cut ACK/deletion changes; a lower sequence committed afterward
did not enter the original set. Their digests matched the existing v3 bodies.
A native reader timeout refused with `25P04`; context cancellation closed both
sessions; past cuts and previously acquired views refused.

Ruff/format, Pyright and test-inventory checks pass. The 22 existing pure
encoding cases also pass. Hosted lanes do not configure the separate native
scratch URL, so skipped tests there do not establish these native facts.
Independent review and current-head CI remain required before merge. No
production function was installed. The existing optional permissions exercise
is outside this leaf and was not rerun or expanded.

To rerun only this leaf, use an owned cached-image disposable container and an
initially empty database named `capture_guard_...`, following the normal
[guard fixture setup](storage-capture-validation-2026-11-11.md). Set only the
separate `REMEMBERSTACK_STORAGE_GUARD_TEST_DATABASE_URL` to that explicit
scratch target and run:

```bash
python -m pytest src/tests/spine/test_storage_capture_view_pg.py -q
```

The fixture owns that fresh database for this file. Use a fresh target for a
separate guard-suite run; this command does not collect or execute its optional
permissions case. Remove only the owned scratch container afterward.

PostgreSQL's [repeatable-read semantics](https://www.postgresql.org/docs/19/transaction-iso.html#XACT-REPEATABLE-READ)
define the first non-transaction-control statement as the view boundary;
its [locking contract](https://www.postgresql.org/docs/19/explicit-locking.html#LOCKING-TABLES)
distinguishes the extraction pin from writer exclusion. Both sources retrieved
2026-11-11. These semantics motivate the original-session implementation;
they do not substitute for native qualification.
