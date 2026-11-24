# Zero-data-retention routing for embeddings and image descriptions

Non-binding analysis, 2026-11-24. Binding result:
[provider rotation design §5](../designs/openrouter_provider_rotation_design.md#5-zero-retention-enforcement)
and the D127 retention clarification in [decisions.md](../../decisions.md).

## Problem

The owner decided on 2026-11-24 that remember.dev may send customer content
only to zero-data-retention (ZDR) model endpoints. A ZDR endpoint is one whose
host has agreed not to store prompts or outputs after answering the request.

The engine already enforces this for chat: `REMEMBERSTACK_OPENROUTER_ZDR=true`
adds `zdr: true` and `data_collection: deny` to every chat request's OpenRouter
`provider` object, and `REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_ONLY` is a hard
host allowlist. Embedding requests carry customer text too (every chunk and
claim is embedded), but before this change their `provider` object was only
one of:

- `{"order": [...], "allow_fallbacks": true}` from
  `REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER`. `order` is a
  preference, not a restriction, so a failure on the listed hosts lets
  OpenRouter fall back to any host serving the model. Production traffic for
  `qwen/qwen3-embedding-8b` did reach SiliconFlow, which is not a ZDR host,
  this way.
- `{"only": [x], "allow_fallbacks": false}` from the single
  `REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER` pin, which has no failover.

Neither carried `zdr` or `data_collection`, so the ZDR flag silently did not
cover embeddings.

## What OpenRouter documents (retrieved 2026-11-24)

From <https://openrouter.ai/docs/features/provider-routing>,
<https://openrouter.ai/docs/guides/features/zdr> and the embeddings guide:

- `only`: "List of provider slugs to allow for this request." With two or
  more allowed providers remaining, "default routing picks among them", and
  limiting providers "may significantly reduce fallback options". When no
  allowed provider can serve, the request fails rather than leaving the list.
- `allow_fallbacks` (default `true`): "Whether to allow backup providers when
  the primary is unavailable." With `order`, providers are tried in that order.
- `zdr`: "Restrict routing to only ZDR (Zero Data Retention) endpoints." The
  request flag is OR-ed with account-level ZDR settings, so it can only add
  enforcement.
- `data_collection: "deny"`: use only providers that do not collect user data.
- The embeddings endpoint accepts the same `provider` object (its guide's
  example sends `order`, `allow_fallbacks` and `data_collection`).

The documentation does not state in one sentence that fallback never leaves
`only`. This repository already relies on that reading for chat
(`openai/flex,openai`, see
[the Flex fallback analysis](openrouter-flex-standard-fallback-20261106.md))
and for image descriptions. The design below does not depend on it for the
retention guarantee: `zdr` and `data_collection` are filters on every
candidate, including fallbacks, so even a hypothetical escape from `only`
could only reach another ZDR endpoint.

Two account-level caveats stay outside the engine. OpenRouter's ZDR page says
self-serve accounts allow "Safety Retention" endpoints unless the account opts
out, and that ZDR traffic is screened for CSAM, with flagged content possibly
retained where law requires. The remember.dev OpenRouter account must
therefore also be set to ZDR-only; the request flag cannot turn that off.

## Options considered

1. **Extend `zdr` to embeddings only.** Fixes retention, but price and host
   control would still rely on `order`, which escapes to any ZDR host serving
   the model. Retention would be correct; cost would not be bounded.
2. **Make `embedding_provider_order` restrictive.** Changes the meaning of an
   existing setting that deployments use for price-bounded failover. Rejected:
   a silent behavior change.
3. **Add a hard embedding allowlist mirroring `chat_provider_only`, plus extend
   `zdr` to embeddings (chosen).** Same parsing, same "an order must name the
   same hosts exactly once" rule as chat. A valid existing configuration with
   ZDR off keeps its exact payloads when the new settings are unset. A
   deployment that already sets `REMEMBERSTACK_OPENROUTER_ZDR=true` gets
   `zdr: true` and `data_collection: deny` on its embedding requests too,
   without setting anything new; that is the intended fix. A malformed
   allowlist (a blank or non-string entry, an empty comma-separated entry, or
   a JSON scalar) is refused at startup instead of being read as unset; the
   existing shortlists keep their tolerant parsing.

For the allowlist on its own, chat sends `allow_fallbacks: false`, which pins
each request to the one host OpenRouter picks. For embeddings that would make
one host outage fail every batch, so with two or more hosts the allowlist sends
`allow_fallbacks: true`: OpenRouter picks among the listed hosts and fails over
only within them. With one host it sends `false`, matching the existing single
pin. This is the same rule the image-description converter already uses.

## Image descriptions

The image OCR + description converter posts image pixels to OpenRouter
`/chat/completions` through its own client and its own
`ImageDescriptionSettings` (env prefix `REMEMBERSTACK_IMAGE_DESCRIPTION_`). It
already sends `data_collection: deny` when its `provider_only` allowlist is
set, but had no ZDR flag. A boolean `REMEMBERSTACK_IMAGE_DESCRIPTION_ZDR`
adds `zdr: true` and `data_collection: deny` to every description attempt,
including the endpoint-rotation retries, and also when no allowlist is set.

This flag shipped separately in #635, which also records it in the route
version: turning it on refreshes the vision lane once and reuses a matching
OCR lane (declared in [media design](../designs/media_design.md)). An earlier
draft of this note proposed keeping the flag out of the fingerprint to avoid
that one-time refresh; the merged design chose the explicit, declared
refresh instead, and this PR follows it.

## Readiness bindings

`readiness.model_bindings` already discloses the embedding pin and order. The
allowlist is disclosed as `openrouter_embedding_provider_only`, but only when
it is set, so deployments and benchmark protocols that do not use it keep
byte-identical bindings and fingerprints. `zdr` stays out of the bindings, as
D127 already decided for chat: it is a transport restriction on the same
model, not a measurement input.

## Other content egress not covered by the OpenRouter flag

These adapters send customer content to providers other than OpenRouter, so
`provider.zdr` does not apply. Their retention terms are a per-vendor account
or contract question for the deployment owner:

- Mistral OCR (`adapters/converters/mistral_ocr.py`), image and PDF pages.
- TypeSafe SystemOne fact adjudication (`adapters/typesafe.py`).
- OpenAI Decisions backup (`adapters/openai_decisions.py`), default off.
- Vertex chat (`adapters/vertex.py`), when configured.
