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
Its actual backend start, virtual transaction and `pg_current_snapshot()`
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
structure sidecars; supplied reference artifacts; projection trees; K page and
curation paths; K planner/compiler transcripts; and accepted portable forget
intents. Native content identities and locators use private fields with
suppressed repr/validation inputs. Trees, manifests, K origin/history and
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

The first checkpoint passed scoped Ruff and Pyright. Native execution,
snapshot/cut behavior and focused source regressions remain pending at this
checkpoint; no production function was installed. The existing optional
permissions exercise is outside this leaf and is not rerun or expanded.

PostgreSQL's [repeatable-read semantics](https://www.postgresql.org/docs/19/transaction-iso.html#XACT-REPEATABLE-READ)
define the first non-transaction-control statement as the view boundary;
its [locking contract](https://www.postgresql.org/docs/19/explicit-locking.html#LOCKING-TABLES)
distinguishes the extraction pin from writer exclusion. Both sources retrieved
2026-11-11. These semantics motivate the original-session implementation;
they do not substitute for native qualification.
