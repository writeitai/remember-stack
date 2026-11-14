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
The fixed provider admits up to 60 seconds of preparation. Each worker waits
on its first idle ordinary connection using autocommit native clock reads and
continuity checks, outside any guard or RR view, until the existing 10-second
native guard deadline fits. That same connection becomes the original guard;
the worker then opens its normal second reader. No third session, host-wall
clock, early view or later reconnection is used.

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
`STORAGE_CAPTURE_PAGE_MEMBER_LIMIT` in
`rememberstack.profiles.storage_capture_profile` is the current canonical
member bound used by both encoding and checking; the runtime imports this
actual value for its separately registered receiver profile.

The typed current profile/source mapping is in
`rememberstack.profiles.storage_capture_profile`; the supervising agent's exact
source-rule technical approval is recorded in
[the implementation analysis](../../plan/analysis/managed_storage_profile_20261111.md).
It is not production scope/clock/cost qualification. The assembler matches
native references, hash-bound conversion artifacts, D155 live corpus folders and
all provider items in both directions, preserving actual zero, failed raw
originals, excluded exposure and committed terminal members. Unknown ownership,
unsupported K/link/legacy projection state or missing evidence refuses.

Corpus classification requires the served reading's publication marker to equal
its current DB-input/default-policy digest and `corpus_paths` to contain exactly
that desired folder. Known original copies and reader derivatives count in
`p3_origin`, separately from canonical raw originals. Shared navigation counts
there too; incomplete/orphan folders are excluded staging. Unknown corpus keys
refuse completion. Corpus originals, derivatives and navigation have separate
native classes using the existing `projection_origin` purpose. Capture reads no
snapshot registry or mutable corpus manifest. The native guard and reader require
exact schema head `p9_52_0073`. The historical snapshot relation remains because
`/operations.latest_projections` still reads it; no migration or public API change
is part of this classification update.

Completed results expose `manifest`, `native_witness`, `provider_manifest` and
`iter_page_bytes()` from the required durable store. Lost delivery replays those
exact completed bytes without reopening a native/provider cut. The final
manifest is written only after all pages and original evidence are durable.
ACK checking and financial settlement remain independent existing contracts.

The checked implementation source is `52d6c2767`. Its actual composed native
run and exact quantities are recorded in the [dated validation receipt](storage-capture-managed-assembly-validation-20261111.json).
The run recorded two scopes at one cut, one provider invocation and the same
actual S3 provider. Canonical-object quantities were 0 and 18 bytes. It recorded
four distinct original native backends, zero ordinary sessions remaining,
an independent control-plane framing PASS and replay without another native
or provider cut. The receipt records earlier fixture failures
and the corrected forward cut instead of describing them as successful cuts.

The functional data covers zero/nonzero retained originals and failed/succeeded
terminal membership, with explicit native/provider K/P3 emptiness. It does not
qualify every representation branch, production SSH/GCS, or deployed
source/schema/image/runtime/clock/batch-cost pins. Those remain independently
accepted operator requirements; source/model construction grants no enablement.
