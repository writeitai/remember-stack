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
silently promoted into that contract. PostgreSQL physical-byte qualification
and the actual schema/object closure are still under investigation in this
draft; no completed inventory or production measurement is claimed.

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
