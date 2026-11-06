# Flex admission rejection and an approved standard-Luna fallback

Non-binding analysis, 2026-11-06. The owner authorized normal Luna when Flex is
unavailable, while preserving the existing Luna/Jev reasoning roles.

The post-#526 conv-47 attempt captured the missing response. It has exactly
two top-level keys: `id` and `error`. The error code is **502**, the message is
"Flex processing is temporarily unavailable. Please try again later or use
standard processing.", and `error.metadata.error_type` is `provider_unavailable`.
There are no choices or usage. The previously implemented 429/503 classification
was too narrow for this gateway normalization. The actual captures are retained
privately with the stopped deployment; tests use a redacted generation ID and
contain no benchmark question or source material.

Recognize this explicit uncharged admission message across the adapter's
existing transient HTTP codes. Merely receiving 502 or an ID is insufficient:
generic failures with IDs and any response carrying choices/usage still need
accounting reconciliation and cannot be blindly regenerated.

The owner's fallback is an execution-policy change, not a different model.
Use `openai/flex` first, then `openai`, with both an order and an identical hard
allowlist. OpenRouter's `only` field prevents other hosts entering the route;
`allow_fallbacks: true` permits moving within that approved set. The local bounded
rotation likewise prunes both lists when a known rejection is returned. Require
the two configured lists to contain exactly the same unique endpoints so no
hidden unapproved tail can appear. Either setting on its own retains its current
behavior. Keep the existing data-collection restriction.

The LoCoMo profile's stored endpoint tuple and worker environment record this
policy. They participate in the run fingerprint, so existing single-Flex runs
cannot silently resume under a changed pricing policy. No new executable legacy
protocol is added. Native image-description requests use the same ordered
approved pair, while the caption-based LoCoMo adapter still does not exercise
native OCR. Embeddings and Jev are unchanged.

The normal endpoint can cost more than Flex; charge/report provider-returned
usage and preserve that cost, rather than assuming the Flex rate. No prompt,
model, extraction identity, vector, or component generation changes in the text
pipeline. Native image-description settings already include their provider list
in the lane fingerprint: explicitly changing that list recomputes that lane on
its next conversion, while unchanged OCR checkpoints remain reusable. This
benchmark consumes flattened dataset captions, so it does not exercise that
native-image boundary. Unchanged deployment configuration does not invalidate
completed paid products.

Sources retrieved 2026-11-06:

- [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection): `only` restricts the approved set and `order` determines preference.
- [OpenRouter service tiers](https://openrouter.ai/docs/guides/features/service-tiers): a base slug does not opt into an unrequested closed-model priority tier; an explicit Flex tier slug admits Flex.
- [OpenAI Flex admission errors](https://developers.openai.com/api/docs/guides/flex-processing#resource-unavailable-errors): Resource Unavailable is uncharged; standard processing is an allowed retry strategy when its price is accepted.
