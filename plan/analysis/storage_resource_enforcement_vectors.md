# Storage resource authority: reachable growth and funding

**Status: non-binding analysis; no price, waiver, new customer cap or billing
quantity is selected.** This closes the source/dataflow investigation behind
[the engine proposal](../proposals/storage_mutation_capture_contract.md).
Numerical reservation formulas are distinguished from proof that resource
exposure and retention obey those formulas.

## 1. Inspected source and authority

Engine source remains the recorded pin `fb4a5f5a` versus accepted main
`149def9e`. UMC source below is accepted main `87ae18aa` (the inspected runtime
upload file and migration 0104 are byte-identical in the later financial draft
`5af0d6ae`). Provider behavior is inspected at fsit `8b71c600`; its binding D63
filesystem-ID correction merged as `6f0fa797` / #566, without implementation or
live capture qualification. IDs are canonical 26-character lowercase unpadded
base32; path/alias framing uses the decoded sixteen bytes. Organisation and
engine UUIDs keep their separate UUID32 type. No synthetic UUID alias is used.

Relevant exact source anchors, all read locally without live/provider calls:

| Repository | Source and meaning |
| --- | --- |
| UMC | `runtime/src/umc_runtime/uploads.py`: constants, `RawUploads.part_url`, `mount_uploads`, `_reserve`, `discard_stale_uploads`: signed lengths, lease timing, registration and cleanup. |
| UMC | `runtime/src/umc_runtime/sweeper.py::sweep_pool/run_forever`: hourly cleanup selects active deployments only; failures and busy keys retry. `tenants.py::build_context` composes both API and worker from the per-bucket S3 adapter. |
| UMC | `billing/money.py::storage_growth_bound_bytes/reserve_processed_input_with_storage/settle_processed_input`: integer policy, atomic bundle, failure release. `billing/storage_service.py::_eligible_holds/_plan_allocations`: current success/processing-sequence eligibility. Prefix these paths with `src/ultimate_memory_cloud/`. |
| UMC | `migrations/versions/0104_d94_processed_input_rates.py::_storage_policies`, not just historical migration 0076: current D94 source/profile maxima. |
| UMC | `scripts/validate_billing_rate_evidence.py::EXPECTED_STORAGE_POLICY/_validate_inherited_floors` and `design/analysis/evidence/development-rate-v4-floor-2026-09-21.json`: historical unchanged-charge revalidation, not a measurement of the new resource domain. |
| UMC | `design/designs/project-metered-billing.md` §4.4 and `storage-on-mountable.md` §§2–4, 7–8: quantity, coverage, exclusions, holds, four stores, ownership and the current conflicting counter-sum instruction. |
| fsit | `internal/gateway/ledger.go::Assigned/Claim/Seal/MarkDeleted`, `grpc.go::AssignVolume`, `http.go::ChunkProxy.upload`: native per-filesystem capacity reservation, capped body and uncertain outcomes. |
| fsit | `internal/gateway/s3_multipart.go::uploadPart/recordPart/sweep/finishAbort`, `watcher.go::purgeAccountingRows`: repeatable parts, logical abort/expiry and accounting cleanup. |
| fsit | `api/fsit_api/routes_access.py::new_filesystem_id/_create_filesystem`, `settings.py`, `billing/usage.py::snapshot`, `billing/prices.py::_line`: literal IDs, configured quotas, supplier snapshot and rounding. |
| engine | `workers/e0.py`, `spine/document_catalog.py::_MARK_VERSION_FAILED`, `spine/managed_metering.py::_materialize_outcomes`, object/Git/P3/forget writers enumerated in the companion analysis: retention is not the same as processing success. |

Full paths above are source locators, not a claim that either draft or accepted
code is live. The fresh local PostgreSQL inventory remains the only native
allocation experiment; it is not a live Project footprint.

## 2. Current bounds: what they enforce and what they do not

**UMC measurement bundle.** Migration 0104 selects `storage-bound-d94-v4` and
`engine_release = remember>=0.19.0`. It defines storage-policy rows for
sources up to 5 GiB and standard/claims/sources profiles for text and scan.
Actual processing-rate availability remains a separate gate; defining a
storage-policy row does not activate a draft claims/sources rate. Text keeps
`16 MiB + 4*source_bytes + 64*native_characters`, with maximum native characters
5 GiB. Scan uses `16 MiB + 4*source_bytes + 262144*pages`, maximum one million
pages. The maxima are 365,088,997,376 and 283,635,613,696 bytes respectively.
`storage_growth_bound_bytes` checks arithmetic, maxima and positivity before
`reserve_usage_bundle` reserves independent processing/storage holds together.
The storage hold is still keyed by the processing measurement and lasts
96 hours 5 minutes; the processing hold lasts 24 hours.

The historical v4 evidence/script still records the predecessor's 10,000,000
source/native limits and `>=0.13.0,<0.14.0` range. These are **not** the current
0104 limits. A history/expiry/digest check on those records does not prove the
current per-resource/attempt/lifetime bounds. Conversely the broad current
`>=0.19.0` registry label is not an exact build/schema/writer qualification.
The calculation looks up class/profile and computes the bound; it does not
observe each resulting object, index, failed copy or provider cleanup tail.

**UMC upload capability.** Direct ingest transport accepts at most 100,000,000
bytes in one request. Multipart uses a 5-GiB file maximum, 64-MiB parts (at most
80), each URL signed for its exact length and valid six hours. This really
bounds one part's payload and the listed part numbers. It is not a single-use
URL or a count/lifetime bound on all replacements. `POST /uploads` reserves
and releases a request spend lease *before* creating the upload/returning URLs.
Completion reserves again and commits/releases that request lease. Neither
lease is the later processed-input storage hold. A funded/allowed Project is
still required at start; this analysis does not claim an unfunded start skips
other admission guards. Issued capabilities can nevertheless outlive that
request, its released lease and a later money/storage park.

**Provider capacity.** `Assigned` locks the filesystem and checks
`retained_bytes + pending_bytes + reservation <= byte_quota`; `Claim` is
single-use for one assigned chunk. `AssignVolume` caps the hint at 64 MiB and
reserves another 2 KiB of multipart framing. The proxy caps the transmitted
body at that reservation. S3/Files uses 8-MiB pieces, at most four concurrent
nonempty puts per filesystem / sixteen per gateway. Metadata admits at most
9,999 chunks/file and 4 MiB inline content. These are distinct protocol and
buffer/capacity bounds, not a UMC monetary/storage-growth permission.

Filesystem source defaults are 100 GiB / one million entries; actual serving
values may be configured differently. UMC provisioning sends only `name` to
the managed filesystem request, not an explicit byte/entry/spend/mutation
limit. D102 §8 deliberately sets no remember.dev supplier spend cap. Current
rows/configuration and all admitted representation paths must therefore be
qualified before treating any default as the managed bound. Optional native
supplier spending caps are real when set, but are neither per-mutation
customer holds nor a new cap chosen by this analysis.

**Cleanup.** Completed unregistered `uploads/` objects older than 24 hours are
walked hourly, under a non-waiting per-key lock, for **active** deployments.
Registered/young/locked keys stay; failures retry. A blocked Project waits for
reactivation or separately requested filesystem deletion. Standard raw ingest
also writes before its catalog transaction, outside `uploads/`; the upload
sweeper is not a proof of cleanup for those losing writes. A durable pre-write
intent must retain their exact identities even if no version/work row commits.

Provider open multipart uploads become aborting after seven days; hourly
sweep retries deletion. `committing` with an unknown outcome keeps chunks;
failed abort cleanup remains retryable. Part replacement creates a separately
aborting record for the old part. The watcher's one-day deleted-row / one-hour
pending-row purge releases **accounting rows**, not proof of physical needle,
tier, backup or replica reclamation. The 24-hour, seven-day and one-day values
must not be substituted for a hard physical-retention bound. D102's live/
backup deletion windows apply after actual deletion, not from upload start or
customer money park.

## 3. Reachable growth and exact disposition/funding vectors

These are paths admitted by source, not claims of live executions or a cash
invoice. Every path must have one bounded resource authority before growth;
which bytes become D55 customer quantity remains a separate exact cut.

| Trigger | Resource/disposition, not processing shorthand | Existing funding and required correction |
| --- | --- | --- |
| Ordinary new raw upload, then successful processing | Raw object commits before `record_upload`; later artifacts/PG/K/P3 writes have their own physical effects. | Request spend gate precedes ingress; measurement bundle precedes its paid provider work, not the first raw write. Add independent storage intent/authority first; keep the processing gate. |
| Raw object succeeds, catalog transaction fails | Object is staged/unreferenced; no canonical version need exist. | A released request lease is not retained-resource permission. Keep exact intent/key and outstanding obligation until verified registration/deletion; do not invent a version to make it chargeable. |
| Version recorded, conversion/processing later fails | `_MARK_VERSION_FAILED` changes status/error, not `content_objects.raw_uri`; canonical retained raw can remain. | Current `settle_processed_input` releases both holds on non-success. Keep processing failure economics, but let independent canonical-storage disposition determine storage funding/eligibility. Do not relabel all retained raw as an excluded failed copy. |
| Free card or exact-content no-op | Card can retain raw/representation/PG rows without a processing receipt. An exact no-op may be zero canonical growth; new names/metadata or duplicate keys still have effects. | No synthetic processing charge. Every actual growth still has storage authority; unchanged/no-processing never automatically releases unknown staging. |
| Multipart start; client never completes | Signed-length URLs create parts while the start lease is already released. No processing measurement or canonical object is required. | Preserve a durable outstanding storage/resource obligation before issuing URLs. Customer billable quantity does not become “declared size”; staging/cleanup COGS stays explicit. |
| Replay same signed part / replace a part | Same permitted part length can be uploaded repeatedly during validity; old parts move to abort cleanup. | Length, part-number and concurrent-buffer bounds are not a replacement-count proof. Qualify current aggregate quota/churn/time bounds or an enforced resource authority; do not silently add a retry cap or treat a URL as single-use. |
| Complete succeeds; registration transiently fails | A full raw object remains; retry can register it. Active-only cleanup may pause with the Project. | Request lease release does not clear the resource obligation. Retain exact completed-object identity and prove the permitted lifetime, including blocked/deletion states. |
| Registration deduplicates/refuses | It may delete the new extra object, but a previously registered key stays. | Require exact canonical unchanged/committed evidence plus cleanup evidence. A content hash, refusal code or task ACK alone does not prove zero retained/staged cost. |
| Worker retry, reused artifact or checkpoint | A known key may be reused; a conditional put can still upload before refusal. Losing artifacts, paid-work checkpoints and transcript/audit material can remain. | API and worker paths use the same storage protocol. Account actual replay/copy/transfer obligations; no paid reuse re-key or model rerun to manufacture a meter. |
| K publish/history/rebuild or P3 build/publication | Canonical Git state, temporary checkouts, projection origins, generated navigation, copied bodies and serving replicas are different resources; failure can retain prefixes. | Qualify repository/object/replica classes and peak overlap before growth. Existing processed-input coefficients are not a per-publication resource census. |
| Delete, D74 forget or restore re-honor | Canonical references can decrease while scrub/rebuild, durable manifests, new sanitized copies and backup tails still cost resources. | Keep independently provisioned operator safety authority; no customer-wallet veto or invented free erasure. Reuse normal erasure/work paths, not a recovery service. |
| Capture and evidence persistence | Catalog/disposition rows, pages, long read views, WAL and spool grow independently of customer content. | Separate bounded operator metadata/capture capacity, included once in COGS. Never fund an unbounded metadata catalog from its own newly written receipts. |
| Midnight/rate boundary, missing evidence or park | The same physical bytes can persist when usable coverage stops or customer-favouring missing days are not charged. | Preserve immutable day/rate holds, no retrospective pricing, and no estimated/historical charge. Profile must cover the real supplier obligation until transfer/deletion rather than assuming park freed bytes. |

## 4. The typed profile must prove enforcement, not merely carry numbers

The proposal's typed resource envelope/profile defines fields and validation.
A permitted value is a finite conservative bound derived from an existing
admissible profile and enforced at a named boundary. It is not a new customer
limit. An absent/unknown witness cannot be represented as zero or approved by
using the old linear formula as a catch-all. A resource/adapter configuration
change invalidates the matching profile, without a new rollout coordinator.

Both actual object adapters are inspected: `LocalFSObjectStore` creates its
`.storage-class` marker and body separately and exposes exact purge/verify;
`MinIOObjectStore` performs create-only S3 writes and counts transfer before
the request, so refused duplicate puts still have exposure. Their local FS
and S3 semantics need separate qualification. Managed API and worker both
compose the S3 adapter through `tenants.build_context(worker=...)`. D61's
complete self-host/reference contract remains required: a reference capability
not present in this checked-out implementation is not claimed built or
qualified just because a port/design names it.

## 5. D55/D102 amendment required before implementation

| Current binding clause | Proposed reconciliation; not enacted here |
| --- | --- |
| D55 §4.4 D48 journal inventory; D102 §8 sum filesystem usage via management key | Replace both old producer mechanics with qualified engine classification plus the scoped D63 provider witness. Preserve four checked components and existing integer daily quantity; do not put management keys in an ordinary collector. Provider logical inventory and separate overlapping counters have different roles. |
| One growth hold per `new_version` measurement; failures/no-ops release it | Make storage authority/growth identity independent of processing receipt. Keep real processing prices/success rules; captured canonical storage can remain eligible after processing failure, while staged/failed/rebuild/cached/audit copies stay classified/excluded under D55. A hold bound is never actual customer bytes. |
| Successful growth holds selected by highest processing commit sequence | ACK/allocate the exact captured storage intent/authority/disposition set. Preserve immutable holds, idempotency, deterministic allocation, no debt, unused-headroom release and explicit operator-loss behavior. |
| Daily/current-month base/growth funding | Establish verified initial storage authority before write-enable; carry/transfer obligations at the correct day/rate/month using existing serializable money and immutable holds. Do not reprice old authority or replay the old cross-month false-shortfall plan. Missing/failed historic cuts stay missing. |
| Included live E/K/P, canonical objects/artifacts, K, remaining P3; excluded audit/failed/rebuild/cache/replica bytes | Accept an explicit algorithm/profile mapping from engine purposes and actual physical owners to those clauses. Keep allocated-relation semantics, with no guessed bloat subtraction. Mixed heaps and P3 origin versus copied serving material need an explicit interpretation or physical separation, not an engine `billable=true` label. |
| Customer-usable coverage, 48-hour missing-data rule, operator-cost floor | Keep existing customer quantity/rounding and fair missing-day treatment. Show supplier/capture/metadata/staging/cleanup/parked cost coverage under that same quantity. No new minimum duration/charge, waiver or cross-meter allocation follows implicitly. |

This technical amendment can remove the wrong coupling without charging for
failed processing or counting declared upload bounds as bytes. If the actual
admitted profile cannot meet the current quantity/exclusion/cost contract,
that is a separate explicit operator decision, not permission to disguise the
profile as a price-compatible implementation.

## 6. Measurable pre-enable gates

1. Pin exact engine/schema/classifier/index/adapter/provider builds and actual
   store IDs, quotas, grants, admitted representations and concurrency. Close
   every writer and mode for API, worker, LocalFS/S3 and required reference
   adapters. A source default or broad `>=` version is not the profile.
2. Trace each admitted mutation from durable intent and exact native authority
   through every costly marker/body/object/PG/Git/copy/metadata write. Inject
   failures before/after each commit and duplicate response. Refusal must occur
   before an uncovered write; no authority ID can fund another intent.
3. Measure populated canonical PG, every admitted index/TOAST/fork, object/K/P3
   allocation, temporary overlap, evidence metadata, chunks/parts, read/write
   transfer and resource-time. Prove their finite enforced envelope, including
   URL replays, replacement churn, crashes, blocked Projects and cleanup errors.
   Peak logical objects alone do not bound physical exposure.
4. Prove each native cleanup/reclamation boundary or keep the obligation
   outstanding. Exercise a completed orphan beyond 24 hours while blocked,
   seven-day pending/unknown multipart, failed deletion, accounting-row purge,
   and live/backup tails. Do not age a test clock then call that elapsed live
   retention or a supplier physical deletion receipt.
5. Capture the populated joint T with the short guard and bounded read-only
   views. Prove exact set membership/ACK holes, lost views, clock error,
   partial pages, ID/alias mismatch, restart and both privacy isolation
   directions. Bound million-document extraction, peak memory/CPU/I/O, snapshot
   age and artifact lifetime under existing admissible load.
6. Run native org/Project cap and wallet cases through the existing money
   path: initial footprint, card/no-op, failed processing with retained raw,
   excluded staging, midnight/month/rate carry, concurrent intents, expiry,
   exact replay and missing-day recovery. No over-hold charge, debt, double
   use or free release of an unknown obligation. Processing reuse tests stay.
7. Produce a configuration-bound machine floor record at the existing
   `3.000000` credits/GiB-month and safety factor 3, with source-dated
   attributable costs and actual profile bounds. Include excluded resources,
   capture, backup, traffic, supplier rounding, failures and unused coverage.
   Historical inherited-floor metadata or provisional processing rates do not
   certify this new domain. No enablement on an incomplete floor.

## 7. Cost arithmetic and remaining decisions

Let B be the qualified daily billable GiB, u the customer-usable seconds and M
the month's seconds. The existing pre-rounding charge is `3*B*u/M`. Let S be
supplier-accounted retained GiB-seconds, kept distinct from physical bytes and
from pending reservations. At D102's recorded EUR 0.17/GiB-month reference,
its storage-only cost is `0.17*S/M` before supplier cent rounding; the existing
3x requirement needs `B*u >= 0.17*S` even before PostgreSQL, copies, capture,
backup, traffic, file counts and other allocated costs. This is a necessary
comparison under the recorded transfer-price basis, not a fresh quote/invoice
or a decision that internal accounting makes supplier resources free.

For example, the already-recorded #922 conditional vector of five GiB in seven
30-day supplier samples costs EUR 0.198333... before cent rounding, EUR 0.20
after the supplier storage line's upward rounding. Its 3x requirement is
EUR 0.60 before other costs. Even granting a whole month of usable coverage
and billing *all* 10,051,584 fresh migrated table bytes measured locally would
yield about 0.028084 credits at the existing rate. That fixture is **not** an
admissible live minimum/profile, and does not prove this combined vector is
reachable on the serving system. It shows why no floor may infer sufficient
coverage from “Postgres is nonempty”, a 5-GiB file limit or a seven-day sweep.

The source paths do permit issuance without a lasting start lease, failed
processing with retained canonical raw, and cleanup waiting on blocked state.
Complete native/profile evidence must establish which exposure/coverage
combinations are reachable. If a permitted vector has zero customer quantity
and positive attributable cost, no finite GiB unit price alone covers it.
Report the exact state and amount; do not silently count excluded staging,
charge missing days, invent minimum usage, assume cleanup, or allocate another
meter's revenue. Existing deliberate failure/missing-day economics must be
reconciled explicitly in the floor's scope and bounds.

Concrete decisions still required: the binding UMC interpretation/mapping for
mixed physical relations and P3 copies; acceptance of independent storage
hold/disposition eligibility while preserving processing terms; and, **only if
qualification fails under the present contract**, an owner disposition for
the exact uncovered vector (cost allocation, product/economic terms or scope).
This analysis selects none of those business-policy changes. Provider capture
acceptance and technical typed fields are not substitutes for that decision.

## 8. Focused route evidence added on 2026-11-11

A clean detached UMC `87ae18aa` checkout ran six existing upload tests against
the real route/app with fake perimeter/admission/spend lease/ingest and a local
in-memory fake S3; pinned engine `fb4a5f5a` was imported. **6 passed in 5.80s**:
signed part lengths/expiry; start reserve-then-release; refusal before S3;
old-unregistered cleanup while registered/young/locked keys stay; complete
pagination behind 250 registered objects; and cleanup failure isolation.
No PostgreSQL, Stripe, actual provider, native funding, physical retention,
clock-duration or margin proof is claimed. No test/source assertion changed.
