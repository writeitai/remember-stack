# Explicit image detail for visual descriptions

The ordinary image converter submitted a valid5304×6630JPEG to Luna without
an image `detail` field. On2026-11-10, the provider rejected it with HTTP400:
its34,528 patches exceeded the30,000-patch limit. Native byte, dimension and
decompression bounds admitted the source correctly; those bounds do not
determine the provider's observation resolution. OCR had succeeded and its
independent checkpoint remains reusable. This failure occurs before any
benchmark question is answered and applies to arbitrary large photographs.

[OpenAI's image-input documentation](https://developers.openai.com/api/docs/guides/images-vision)
(retrieved2026-11-10) says an omitted detail field uses `auto`; its behavior
depends on the model. Some defaults preserve original resolution and reject
images above the patch limit. Explicit `high` selects the model's bounded
high-fidelity preprocessing. Exact limits remain provider/model dependent;
this setting is not a guarantee that every provider will accept every input.

Choose a configurable `high` default for the description lane. Both providers
still receive the same byte-identical source: the adapter neither crops nor
resamples the file. Dedicated OCR keeps its original-resolution input and
authority over transcription. `auto`, `low` and `original` remain explicit
provider-dependent configuration choices, not automatic error fallbacks.
Small visual details can be less clear under bounded preprocessing, so the
selected detail must be disclosed in the native converter profile and identity.

Local resizing would require another decoder/encoder policy and transform
provenance. Changing only byte ceilings would not solve a patch rejection.
Silently retrying at lower fidelity would obscure the actual processing
identity. An explicit request setting is smaller and reproducible.

Reuse boundary: adding detail to the description fingerprint invalidates old
description checkpoints once, even when an image's provider preprocessing
would happen to be identical. OCR fingerprints, originals, source lineage,
text chunking and extraction policies are unchanged. Retain old descriptions
and receipts as historical work; do not re-label them as high-detail output.
Actual returned charges must meter refreshed descriptions. The finite LoCoMo
image preparation may reuse its OCR checkpoints while making a fresh vision
reading under the new profile. The existing USD10 admission stop is ample
for the observed image costs; it is not a provider invoice guarantee.
