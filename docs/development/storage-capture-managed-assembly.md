# Managed capture assembly

This implementation leaf depends on the original native view in engine
PR #579 (`31b4fdf76`) and accepted D152. UMC's accepted managed capture
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

The concrete assembly is `rememberstack.spine.managed_storage_capture`:
`StorageCaptureCatalog(collector_connect, provider, stores, evidence_store,
profile, identity_key)` uses named arguments and invokes
`capture_once(request=...)`. It starts the owned provider transport before the
real native capture, waits for provider completion after short-guard release,
retains the same original native view while classifying, and stages private
indexes in a temporary operator SQLite spool. The full inventory is not held
in a Python collection. Exact D152 page bytes use compact sorted-key JSON,
`ensure_ascii=True`, including required defaults/nulls.

Its required provider port owns `capture(request)` as a context manager;
the pending operation supplies `complete()` and `iter_pages(manifest)` over
the sole accepted D63 wire. Runtime supplies that authenticated transport and
the four ordinary scoped S3 capabilities. No provider request/decoder,
native witness or persistence fallback is manufactured inside the engine.

The typed current profile/source mapping is in
`rememberstack.spine.storage_capture_profile`; the supervising agent's exact
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
scoped static checks pass. Real combined native/provider execution, current
qualified source/configuration/pins, targeted source review and current-head CI
remain required. No live capture or qualified managed result is claimed.
