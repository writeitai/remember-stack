# Engine storage mutation and capture contract

**Status: non-binding proposal; no implementation permission.**
[Analysis and source boundary](../analysis/storage_classification_and_admission.md).
**Adoption trigger:** the exhaustive engine relation/object inventory and
PostgreSQL measurement method are qualified, provider capture/resource
interfaces are accepted, and affected binding engine designs are reconciled.
A managed operator must independently reconcile its billing terms and qualify
its cost floor. This proposal does not approve either commercial policy or
customer billing.

## 1. Engine-owned evidence, operator-owned resource permission

One deployment is one trust domain. The engine owns E/K/P semantics, complete
resource classification, immutable mutation/disposition evidence and the
admission barrier. A narrow provider port may grant or refuse a finite
resource allowance; it may not author claims/facts, bypass E0, relabel retained
objects or acknowledge a commit that the engine has not proved. A self-host
adapter supplies local operator resource authority; a commercial adapter may
bind it to prepaid funding. Neither makes memory correctness a paid feature.

The operator's price, wallet, rate and customer byte-class mapping are outside
this engine contract. Engine evidence describes purpose and reference identity,
not an authoritative `billable=true` flag. Unknown purpose or missing byte
coverage fails a capture, rather than defaulting into or out of a charge.

## 2. Admission before growth

A `storage_mutation_id` identifies one immutable intent independently of a
processing measurement, task attempt or document outcome. The engine records
the intent before issuing any direct-upload capability or performing an
object/database/K/P3 growth write. The resource authority binds the deployment,
intent digest, qualified storage/profile versions, conservative resource bounds
and validity/disposition conditions. A changed request under the same identity
conflicts. Replayed/reused content does not silently create another allowance.

All storage writers participate: ingestion, cards, worker artifacts, K writes,
projection builds/publication, retries, backfills, operator rebuilds and
privacy-control writes. Long processing does not retain a capture lock.
Safety-critical erasure/forget work has an explicit independently provisioned
operator resource path; an empty customer wallet cannot pretend the work is
free or erase the durable intent. Its full capacity/failure rule must be
reconciled with D74 before acceptance.

A capability already issued to a client can outlive the engine request.
Closing an engine gate does not revoke a presigned URL. Its staging remains
within durable resource authority until the provider proves the outcome and
cleanup. Logical byte bounds alone cannot certify provider part/chunk churn,
retained replacements or garbage collection. Unsupported provider enforcement
or an unknown outcome cannot be hidden by a successful processing receipt.

## 3. Independent storage dispositions

Every mutation records immutable disposition evidence covering canonical
commit, unchanged canonical state, or failure/in-flight staged material with
an unresolved resource obligation. A canonical commit may coexist with staged
cleanup; one scalar processing success/failure cannot erase that obligation.
Cleanup observations append evidence rather than rewriting earlier records.
Exact object/reference identities and complete sizes allow shared references
to count once while distinct copies remain separately classified.

Cards and unchanged inputs follow this same storage contract even when no new
processing measurement exists. Failed processing releases only its processing
obligation; retained canonical/staged resources keep their storage disposition.
Late/duplicate worker results cannot outrun the existing claim/forget guards
or consume another mutation's resource allowance.

## 4. A short capture, not a processing barrier

A capture closes the engine's relevant short mutations across its named
instant and matches a provider witness for the exact configured store set.
The provider must expose a complete logical object view, stable identity and
alias/type facts, plus separate staged/retained-resource observations. These
are **interface assumptions being designed by the provider**, not capabilities
claimed for the deployed adapter. A provider marker alone is not an engine cut.

The capture freezes the qualified PostgreSQL relation set, catalog/reference
state and exact storage-disposition set. It never waits for every admitted
model job or large upload to succeed: unfinished work has an explicit class
and still-held resource authority. The locking/maintenance method must prove
one consistent cut without deadlocking a remote call behind the same gate.
Timeout, unknown writer/object/relation, changed lifecycle/schema, incomplete
pages or lost continuity yields no completed evidence.

A completed manifest binds scope, cut identity/time witness, source/schema and
classification versions, provider witness digest, checked component records
and an exact bounded-page set of `(mutation_id, disposition_revision,
payload_digest)`. Completion and page/set digests prove coverage. An operator
acknowledges that exact set before settlement. A highest processing sequence
is neither the set nor proof for card/no-op/in-flight work. Instrumentation
must not change the measured footprint as a side effect; its resource cost
remains visible to the operator.

## 5. Complete acceptance boundary

The design must cover local/reference adapters and all engine writers, with
no alternative unguarded path. Required vectors include changed-schema/index/
TOAST closure, concurrent DML/maintenance, shared references and serving copies,
cards/no-ops, presigned staging through a cut, partial failure, stale leases,
forget/deletion priority, exact outcome-set holes/replay, and restart during
materialisation. Two dated document versions must preserve D56 content,
Selection/text-origin/claim and embedding reuse outside legitimate edit
boundaries. None of these runtime proofs has been run by this draft.

Binding adoption must update the affected E0, object/provider, PostgreSQL,
projection, orchestration and hard-forget designs and their decision links.
No accepted design is replaced by this proposal alone. Public docs describe
shipped behavior and therefore receive no aspirational route or command here.
