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
- `manifest.json`, created **last** as the durable completion barrier.

Each create is immutable; replay compares the exact existing canonical bytes
and conflicts on a change. Reads name exact keys without listing. Completion
precedes delivery; the receiver's independently durable complete ACK grants
no money or processing-delivery authority. Runtime transport and restricted
GCS persistence belong to the managed job, not this engine library.

Initial checkpoint: closed decoder/framing, exact-set page iteration and the
required persistence port are implemented. Full typed current managed profile,
source classification, resource matching, component assembly, original-view/
provider orchestration, immutable completion and replay remain in progress.
No live capture or qualified managed result is claimed.
