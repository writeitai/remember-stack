# A strict JSON schema can collapse a provider allowlist to one host

**Status:** analysis (non-binding).
**Date:** 2026-09-02.
**Evidence:** LoCoMo `conv-48` ingestion, protocol `RS-LoCoMo-Full-v21`,
`z-ai/glm-5.3-flash`, host `umc-locomo-bench-01`, deployment
`a5754c13-a721-4b85-9dfb-652f3fe94a46`.

> **Read this if you are pinning providers.** The headline is not "429s
> happen". It is that `provider.only` plus strict structured outputs can
> silently leave exactly **one** eligible provider, after which no amount of
> `sort` or `allow_fallbacks` helps.

## What happened

A conv-48 ingestion completed extraction cleanly (119/119) and then
dead-lettered **28 `adjudicate_observations` items**, each after exhausting
its attempts. All 28 carried the same payload:

```json
{"error":{"message":"Provider returned error","code":429,
  "metadata":{
    "provider_name":"DeepInfra",
    "provider_error_code":"engine_overloaded",
    "limit_source":"upstream_provider_shared_pool",
    "is_byok":false}}}
```

The allowlist was `z-ai,novita,deepinfra,gmicloud`. Every failure named
DeepInfra; the other three produced none.

## The actual root cause

The engine sends **every** chat completion with
`response_format: {type: json_schema, strict: true}`. Of the four allowed
providers, only DeepInfra advertises strict structured-output support for
this model:

| Provider | `structured_outputs` | `response_format` |
| --- | --- | --- |
| **DeepInfra** | **true** | true |
| Z.AI | false | true |
| Novita | false | true |
| GMICloud | false | true |

So the effective pool was **one provider**, not four. `sort` had nothing to
reorder and `allow_fallbacks` had nowhere to fall back to. Each engine retry
returned to the same overloaded host.

Isolated empirically — same allowlist, same `sort: throughput`, one
parameter at a time:

| Request shape | Result |
| --- | --- |
| `max_tokens: 32000` only | OK, served by Novita |
| `reasoning: {effort: high}` only | OK, served by Novita |
| **strict `json_schema` only** | **429 from DeepInfra** |

A trivial request with the identical provider block routes to Z.AI or Novita
and never to DeepInfra. Add the schema and it pins to DeepInfra every time.

## A wrong diagnosis worth recording

The first explanation was: OpenRouter's default routing weights price,
DeepInfra was the cheapest **and** slowest of the four (measured
\$0.0001003 at 30.8 tok/s against Z.AI's \$0.0001581 at 43.9), so it won
selection on every call and every retry.

That story fit every observation and was still wrong. It predicted that
`sort: throughput` would fix the problem. It did not: after deploying the
sort and replaying all 28 dead letters, all 28 failed again on DeepInfra.
The correlation between "cheapest" and "the one that failed" was a
coincidence of DeepInfra also being the only schema-capable host.

The lesson is about method rather than routing: **a hypothesis that explains
the failure is not the same as one that predicts the fix.** The replay was
what falsified it, and the one-parameter-at-a-time isolation is what found
the real cause.

## What `allow_fallbacks` does and does not do

Still true, and still worth knowing: OpenRouter documents `allow_fallbacks`
as allowing backups when the primary is *"unavailable"* without enumerating
which HTTP codes qualify, and a provider-returned 429 is surfaced as
`"Provider returned error"` rather than triggering a re-route. Do not rely
on it for rate-limit failover.

But that was not what broke this run. Even perfect 429 failover would have
had nowhere to go.

## What to do instead

1. **Check capability, not just identity, when building an allowlist.** Query
   `/models/{id}/endpoints` and intersect `supported_parameters` with what the
   caller actually sends. An allowlist of four that supports one is a
   single point of failure wearing a disguise.
2. **Widen the allowlist to schema-capable providers** for this model
   (Fireworks, BaseTen, Cloudflare and others advertise it).
3. **Bring your own key** for the pinned provider (`is_byok: true`) to swap
   the shared pool for dedicated limits.
4. **Treat concurrency as part of routing.** The same allowlist ran 1,842
   calls with one failure at 1 worker per stage, and collapsed at the
   reference topology (extract 8 / normalize 6 / adjudicate 4 / embed 2).
   Extraction survived only because it ran before the scale-up.

`chat_provider_sort` remains a reasonable setting and is retained — biasing
toward throughput is sensible when there is genuinely more than one eligible
host. It simply cannot help when the eligible set has one member.
