# Retained-resource classification and independent storage admission

**Status: analysis, not binding design or implementation permission.** The
question is how this complete single-deployment engine can describe its
retained resources and admit resource growth without treating a successful
processing receipt as storage truth. Billing policy belongs to a deployment
operator; E/K/P meaning, completeness and mutation correctness remain here.

## Source boundary

This investigation starts from accepted engine main
`149def9e8ba0a7d7404292efc5af267d6ea167b5` (2026-11-11). The recorded managed
runtime instead pins engine `fb4a5f5a4cc2eb415df3a8f5b7908bb506bf58d0`.
Accepted source is not a claim that the newer code is deployed. The two pins
must be compared explicitly when qualifying a classifier.

UMC's accepted D55 §4.4 includes live E/K/P, graph/indexes, canonical objects,
artifacts, K and remaining P3 while excluding backups, audit, temporary
rebuilds, failed copies, caches and serving replicas. Its D102 four-filesystem
usage sum does not resolve those exclusions. Its merged, **non-binding**
[storage analysis](https://github.com/writeitai/ultimate-memory-cloud/blob/ac2da736/design/analysis/storage-launch-current-terms-2026-11-11.md)
and [capture proposal](https://github.com/writeitai/ultimate-memory-cloud/blob/ac2da736/design/proposals/project-storage-capture-capability.md)
ask for this engine deliverable alongside a provider capture contract. They
are inputs, not authority to change engine or commercial behavior.

The binding engine boundaries are [D60/D61](../../decisions.md#d60-the-library-boundary--this-repo-is-the-complete-single-deployment-memory-system-the-humanoperations-layer-is-a-separate-product),
`CLAUDE.md` Rules 2–4 and the current E0, PostgreSQL, projection and hard-forget
designs. The contract must work without a cloud control plane, cover the whole
intended engine, and leave all existing content/reuse identities unchanged.

## Concrete gaps visible in source

- [`ObjectStorePort`](../../src/rememberstack/ports/object_store.py) creates
  immutable bytes with a caller-supplied D51 `storage_class`. That field is
  hot/cold provider routing, not a canonical/serving/staging retention class.
  It carries no storage mutation or resource authority identity.
- [`ProjectionCatalog`](../../src/rememberstack/spine/projection.py) records
  building, failed, published and superseded prefixes. A prefix/row count is
  neither a complete per-object byte manifest nor proof that every P3 object
  contains unique data rather than a serving copy.
- [`ManagedMeterCatalog._materialize_outcomes`](../../src/rememberstack/spine/managed_metering.py)
  obtains the successful processing sequence on a separate connection before
  its later outcome insert. A maximum sequence cannot prove that all prior
  outcomes are committed. Card/no-op paths also cannot be covered by assuming
  every storage write has a processing outcome.
- PostgreSQL's relation sizes describe allocated relation storage. They do not
  provide a historical MVCC count of logical live customer bytes. A complete
  engine catalog must identify actual relations, attached indexes, TOAST and
  forks without double-counting; it cannot decide an operator's bloat policy
  from a table-name prefix.

## What must be closed before a binding contract

The source audit must produce a closed, versioned relation/object inventory,
not a list of examples. Every engine-owned relation must have one purpose
class; indexes and TOAST must resolve through their actual owning relation.
Unknown or changed schema objects invalidate qualification rather than being
silently included or omitted. Provider object identity, aliases and resource
staging must be matched to engine references; a nullable size or incomplete
listing does not become zero.

There are two separate questions: what the engine retains, and which measured
components an operator is permitted to charge. The engine owns the former.
A commercial adapter cannot label E/K/P truth or decide that a failed write
never existed. An operator's mapping may select only a qualified subset and
must retain the real cost of excluded resources. This analysis chooses no
price, waiver, minimum, new upload/churn limit or customer allocation.

## Candidate direction and alternatives

The [proposal](../proposals/storage_mutation_capture_contract.md) gives each
resource-increasing operation an identity and resource authority before its
first write. Immutable storage dispositions are separate from processing
outcomes. A short engine mutation boundary and a provider continuity witness
capture an exact disposition/catalog set without waiting for model work.

A processing-sequence watermark is insufficient for the concrete sequence
allocation gap and for cards. Summing four provider counters conflates
canonical references with staging and copies. Counting all PostgreSQL files
conflates core data with instrumentation, rebuilds and dead space. Inventing a
second release coordinator would not solve any of these measurement problems.

The provider's proposed logical inventory and separate resource observations
remain an explicit interface dependency. The current provider fence is not
silently promoted into that contract. The [local schema experiment](storage_inventory/README.md)
now closes the observed relation/index/TOAST/fork set for the two inspected pins.
Populated-byte/maintenance qualification and the object writer/classification
contract below remain distinct from that observed schema closure. No production
measurement or customer bill is claimed.

## Reuse and validation boundary

Resource identities are out-of-band metadata. They must never enter document
bytes, titles, source references, chunk/header inputs, content hashes,
Selection/Claimify identities or embedding policy/model keys. No D56 reuse
boundary, converter generation or processing version is changed by this work.
The known reuse gaps listed in `CLAUDE.md` remain known gaps. Implementation
qualification must retain the two-version/provider-call tests, including
packing and D122-window cases; design acceptance is not that test result.

No service, schema, credential, live resource or public documentation behavior
has changed. This draft records source analysis only.


## Allocated physical bytes are the existing quantity

The old UMC implementation is explicit: `canonical_storage/database.py`
`_postgres_meter_function_statement` locks public ordinary/partitioned tables
in SHARE mode, then sums `pg_total_relation_size` of ordinary/materialized
relations except eleven names and their direct children. Its caller in
`canonical_storage/storage_collector.py` refuses any schema except
`p9_34_0055`. Source inspected at UMC `f0693c92`; these two files are unchanged
through `f82d13e4`. Its name `physical-public-relations` is accurate: allocated
heap/index/TOAST space includes dead/free allocated pages. It does not subtract
bloat, count visible tuples or reconstruct logical historical bytes.

The candidate keeps allocated relation bytes visible as that same kind of
quantity. Replacing it with a live-row estimate would change semantics and
requires a separate explicit operator billing decision; this analysis does not
make one. A relation purpose classification must also reconcile D55's “live”
and audit/failed/rebuild exclusions. For example `resolution_decisions` is
truth-defining provenance, whereas a dedicated cost ledger is instrumentation;
excluding every table called a ledger would destroy that distinction. Mixed
current/replay/failed rows in one heap cannot be assigned exact physical bytes
by `pg_column_size` or row count. The engine reports the actual allocation and
purpose; no commercial adapter invents a physical subtraction.

The [complete local catalog](storage_inventory/README.md) found the same 98
root names and 1,989 catalog objects for both pins. Public physical table totals
were 10,051,584 bytes, with 24,576 separate sequence bytes. The old exclusion
arithmetic yields 9,551,872 bytes but does not pass its own schema gate. All
numbers describe a disposable fresh schema, not an invariant minimum charged
footprint. Capturing a larger catalog does not prove that customer-usable time
or excluded supplier staging is funded.

## Closed object classes and their current writers

These rows partition *purposes*, not customer charges. An object's class also
needs its durable state and exact references; a bucket or prefix alone does
not say whether a write committed, failed or is awaiting cleanup.

| Resource and writer | Engine class and reference rule |
| --- | --- |
| E0 `ingest` / `ingest_observed` raw originals (`workers/e0.py`) | One retained raw reference per `content_objects` row, shared by versions. The first retained key wins. A second key with identical bytes is a distinct staged copy until `_release_unretained` proves deletion; a matching content hash does not mean the provider stored only one copy. |
| Direct `uploads/<opaque-id>` before `ingest_stored` | Unregistered staging with its own resource obligation. Registration may retain that exact key, deduplicate/delete it, refuse/delete it or fail with bytes still present. Engine registration currently happens after upload and cannot prove pre-upload authority. |
| Conversion `document.md`, `blocks.json`, `conversion.json`, `meta.json`, source map and derived assets | Representation artifacts referenced by immutable representation/generation records. A write precedes the catalog commit; incomplete or losing attempt objects remain staged until separately reconciled. A card is a real retained representation even with no processing charge. |
| Structure `pageindex.json` (`workers/e0.py`) | Derived navigation artifact associated with the selected structure generation; old retained generations and failed/unpublished material need their actual reference/state, not “latest prefix” inference. |
| Supplied reference NDJSON (`spine/references.py`) | Immutable source-owned canonical reference input; stored before the locked generation transaction, so an uncommitted/losing write is independently staged. |
| Conversion-lane checkpoints (`workers/lane_checkpoints.py`) | Private paid-work reuse artifacts, not searchable representation authority. They survive retry and may contain source data; hard forget purges the nominated prefix. Reusability is not a free-storage or commercial-inclusion decision. |
| K planner/reflection/writer session transcripts | Immutable audit/session material, including failure transcripts, at configured artifact prefixes; references are recorded by K work catalogs. They are not K page truth or provider-free data. |
| Plane-K repository (`adapters/selfhost/git.py`) | Current authored/compiled bodies, curation sidecars and retained reachable history are repository truth; pack compression and shared Git objects are counted once by actual repository object identity, not once per checkout/path. Histories requiring D74 erasure remain retained until erasure is verified. |
| K driver checkouts, writer/planner workspaces, Git transfer/pack temporary files | Work/rebuild copies distinct from repository truth. Their filesystem exposure and cleanup are operator resources. The ordinary repository port currently supplies no exact footprint/capture witness. |
| Durable P3 snapshot objects + `MANIFEST.json` (`workers/p3.py`) | Projection origin: generated navigation/stubs plus copies of source/representation material. A manifest maps names to hashes, not sizes or “unique bytes”; `building/failed/published/superseded` comes from `projection_snapshots`. All P3 is reconstructable, but that alone does not decide D55's “remaining P3” mapping. |
| P3 local `.staging-*`, served trees and `.snapshot-version` (`adapters/selfhost/mounts.py`) | Rebuild/serving replicas and marker metadata. The same content can have a durable origin and multiple serving copies; do not collapse their supplier cost or charge the origin twice. |
| Dedicated forget object/filesystem root + `forget_manifests` PG materialization | Independently durable privacy-control intent. Portable manifests survive ordinary data restores. The PG row is a materialization, not another independent erasure authority. Temporary append files and re-honor/rebuild work keep their own resource class. |
| Local `.storage-class` companions, mount raw-read audit, converter/mmap/spool temporary files | Adapter routing metadata, audit and scratch respectively. D51 hot/cold is placement, not this purpose class. None may be silently mistaken for source content. |
| PostgreSQL WAL, catalog/system allocation, spill, extension shared memory/external files; provider chunks/deletion tails; backups/replicas | Substrate/resource exposure outside the classified retained relation/object sum. Absence from that sum never asserts zero cost or physical reclamation. |

The source search covers the actual `ObjectStorePort.write_bytes` producers
(E0, P3, reference inputs, lane checkpoints, K planner/writer transcripts and
forget stores), Git publication, local mount publication, conversion/workspace
writes and purge/forget paths. New engine writers must add a class and admission
path; they are not permitted by a broad bucket allow-list. Adapter-specific
filesystem metadata and external resources need their own qualified inventory.

## What the observed catalog does not settle

The engine can close actual semantic purpose and ownership, but cannot infer a
commercial mapping for a mixed relation, an allocation's dead space or P3 origin
from a name. A qualified populated capture must show all actual index access
methods (btree, GIN, GiST, HNSW and BM25), TOAST and all forks, including
concurrent maintenance and side resources. SHARE blocks ordinary DML and
conflicting maintenance; a snapshot alone does not freeze physical allocation.
New DDL/partitions and any extension/background writer require the same
qualified mutation boundary or a refused capture. Adding another catalog row
to the sample while measuring would also be a measurement side effect; future
capture instrumentation belongs in a separately classified store.

These remaining qualifications are acceptance conditions, not permission to
ship a partial meter or an invented new customer price/limit. The complete
engine contract must work for all E/K/P writers and self-host adapters, with
the operator's physical quantity and cost profile explicitly reconciled.


## Resource enforcement and current managed funding

The [resource/funding vectors](storage_resource_enforcement_vectors.md) extend
this analysis with actual UMC and provider admission/cleanup source. They do
not equate a temporary request spend lease, a processing growth hold, a
filesystem quota, a supplier spending cap or cleanup scheduling with durable
storage authority. Canonical retained raw data and excluded staged/failed
copies must stay distinct even when processing fails. D55/D102 reconciliation and measurable pre-enable gates are now specified as
non-binding proposal work; binding acceptance and real qualification remain
separate gates. The current 0104 chain, rather than the historical v4 seed,
defines the inspected text/scan source and quantity maxima.
