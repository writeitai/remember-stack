# Managed capture assembly

This implementation leaf uses the original native view merged in engine
PR #579 (`5841ea7d3`) and accepted D152. UMC's accepted managed capture
amendment (PR #952, merge `2c1a3ecc`) §§1,3 and D55 §4.4 supply the commercial
byte bases and exclusions; the engine owns native classification. This file
records implementation status, not permission to enable managed charging.

The one engine Python D63 decoder is
`rememberstack.adapters.fsit_storage_capture`. It consumes FSIT's exact
provider-owned request, item, page-result, native-witness and complete-manifest
wire; it never decodes filer metadata or scans a provider filesystem.
Authoritative source is FSIT PR #579 at
`bc8482abddbf392e448fa60b598c6dd520ccc17f`:
`internal/capture/{identity,item_wire,page,records,witness,artifact_publication}.go`.
The provider's actual synthetic native artifact vector and existing Go page/
path vectors pin cross-language framing. Synthetic wire validity gives no
scope, clock or native origin authority.

The required operational persistence port is
`rememberstack.ports.storage_capture.StorageCaptureEvidenceStore`. All bytes
must survive the ephemeral job and Project deletion, in operator
`audit_operations` storage outside the measured customer set. There is no
default or in-memory implementation. Exact capture-derived keys are:

- `pages/<canonical_decimal_ordinal>.json`;
- `native-witness.json`, `native-allocations.json`, `profile.json`;
- `provider-manifest.json`;
- `provider-pages/<canonical_decimal_ordinal>.json`, retaining original item
  evidence independently of the provider's 72-hour expiry;
- `manifest.json`, created **last** as the durable completion barrier.

Each create is immutable; replay compares the exact existing canonical bytes
and conflicts on a change. Reads name exact keys without listing. Completion
precedes delivery; the receiver's independently durable complete ACK grants
no money or processing-delivery authority. Runtime transport and restricted
GCS persistence belong to the managed job, not this engine library.

The public operation is
`rememberstack.profiles.storage_capture_batch.capture_storage_batch`, including
when only one Project is selected. It requires a tuple of
`ManagedStorageCaptureInput` values, the raw `provider_transport`, and explicit
`max_scopes` and `maximum_frame_bytes`. Use the same `max_scopes` from the
independently qualified run profile as the due-Project query. There is no
default or separately invented customer limit. Mixed cell/T, duplicate Project,
empty and oversized batches refuse before provider launch or a native pause.
Replay reads exact completed evidence without opening another cut.

For pending scopes, the operation launches one complete provider `requests[]`
envelope, then runs one native Project worker per admitted scope. Each worker
owns/finally-closes its two original collector sessions, waits for provider
completion only after short-guard release, retains the original native view
while classifying, and stages private indexes in a temporary operator SQLite
spool. The full inventory is not held in a Python collection. Exact D152 page
bytes use compact sorted-key JSON, `ensure_ascii=True`, including required
defaults/nulls. Failure exits the raw provider context before joining workers,
so cancellation closes blocking I/O and then waits for native session cleanup.
The fixed return shape is a tuple of `ManagedStorageCaptureResult` values in
input order: each has `request` and `completed`, with explicit `None` for an
unavailable Project. A Project failure closes its native sessions and drains
only its original provider group; other scopes keep their original evidence.
Transport/protocol loss cancels the shared stream. A successful worker closes
its native view, then waits for the full original provider EOF and process exit
before publishing durable completion. No retry or substitute cut is created.

`rememberstack.adapters.fsit_storage_capture_transport` owns the single closed
canonical manifest/page/result-frame decoder and ordered group consumption.
Its required raw transport port is
`RawProviderCaptureTransport.open(*, stdin: bytes)`, returning a context manager
whose process supplies `read_line(*, maximum_bytes: int) -> bytes | None` and
`check_success() -> None`. Start/write stdin before yielding, return an LF-ended
bounded line or `None` only at EOF, check the original child exit, and always
cancel/close/reap on context exit. Runtime supplies finite I/O deadlines and
the fixed authenticated SSH command, without another provider truth parser.

For every selected Project, construct a `ManagedStorageCaptureInput` with
`request=profile.request(cut_at_unix_us=...)`, `profile`, `collector_connect`,
`stores` (exact `raw`, `artifacts`, `corpus`, `forget` capabilities),
`evidence_store`, `identity_key` (existing meter key) and `provider_grant` (the
existing finite provider capture grant), all as named arguments. The native
connection factory opens a fresh idle ordinary collector connection at the
direct TLS PostgreSQL target, never a transaction-pooling endpoint. Operational
ports used by different Project workers must support those concurrent calls;
one Project's native connections are never shared with another worker.

The profile builder accepts `native: NativeCaptureProfile`,
`bindings: ManagedStoreBindings`, `managed_runtime_source_digest`,
`provider_profile_digest`, and `provider_clock_profile_digest`.
Bindings contain actual deployment UUID32, PostgreSQL database, provider
organisation UUID32, literal cell, and exactly four distinct filesystem
bindings in `artifacts`, `corpus`, `forget`, `raw` role order. Load independently
accepted exact source/schema/image/clock documents and bounds; generating
these models never establishes their qualification. No native witness or
persistence fallback is manufactured inside the engine.

The typed current profile/source mapping is in
`rememberstack.profiles.storage_capture_profile`; the supervising agent's exact
source-rule technical approval is recorded in
[the implementation analysis](../../plan/analysis/managed_storage_profile_20261111.md).
It is not production scope/clock/cost qualification. The assembler matches
native references, hash-bound conversion artifacts, immutable P3 manifests and
all provider items in both directions, preserving actual zero, failed raw
originals, excluded exposure and committed terminal members. Unknown ownership,
unsupported K/link/legacy projection state or missing evidence refuses.

Completed results expose `manifest`, `native_witness`, `provider_manifest` and
`iter_page_bytes()` from the required durable store. Lost delivery replays those
exact completed bytes without reopening a native/provider cut. The final
manifest is written only after all pages and original evidence are durable.
ACK checking and financial settlement remain independent existing contracts.

Current checkpoint: source mapping, resource/component assembly, original-view/
provider orchestration and durable completion/replay code are written and
scoped static checks pass. The changed native seed projection passed the same
five normal original-view cases against actual migrations and an ordinary
collector on an owned cached PostgreSQL 19rc1 scratch database (8.07 seconds);
40 focused encoding/resource/provider-vector/store tests also pass, including
two-scope byte ordering, local scope failure and protocol-loss cancellation. These synthetic protocol cases
establish no native scope or cut. These
checks do not execute a combined provider/native capture. That real execution, current
qualified source/configuration/pins, targeted source review and current-head CI
remain required. No live capture or qualified managed result is claimed.
