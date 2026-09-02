# Price-first provider routing dead-letters long ingestion runs

**Status:** analysis (non-binding).
**Date:** 2026-09-02.
**Evidence:** LoCoMo `conv-48` ingestion, protocol `RS-LoCoMo-Full-v21`,
`z-ai/glm-5.3-flash`, host `umc-locomo-bench-01`, deployment
`a5754c13-a721-4b85-9dfb-652f3fe94a46`.

## What happened

A conv-48 ingestion completed extraction cleanly (119/119) and then
dead-lettered **28 `adjudicate_observations` items**, each after exhausting
all three engine attempts. Every one of the 28 carried the identical
provider payload:

```json
{"error":{"message":"Provider returned error","code":429,
  "metadata":{
    "provider_name":"DeepInfra",
    "provider_error_code":"engine_overloaded",
    "limit_source":"upstream_provider_shared_pool",
    "is_byok":false}}}
```

Three facts make this diagnosable rather than mysterious:

1. **It is upstream, not us.** `limit_source: upstream_provider_shared_pool`
   with `is_byok: false` means the ceiling is DeepInfra's own capacity,
   shared across every OpenRouter user without a personal key. It is not an
   OpenRouter account limit and not a spend cap.
2. **It is one provider, not four.** All 28 name DeepInfra. Z.AI, Novita and
   GMICloud produced zero failures under the same concurrency.
3. **It is not a model-quality problem.** `engine_overloaded` says "busy". A
   separate, single JSON-decode failure earlier in the run was retried and
   succeeded; glm-5.3-flash's structured-output adherence was not the issue.

## Why it concentrated on one host

Measured on one identical extraction-shaped call at `reasoning=high`:

| Provider | cost | throughput |
| --- | ---: | ---: |
| **DeepInfra** | **$0.0001003** | 30.8 tok/s |
| Novita | $0.0001246 | 40.0 tok/s |
| GMICloud | $0.0001333 | 39.5 tok/s |
| Z.AI | $0.0001581 | 43.9 tok/s |

DeepInfra was the **cheapest and the slowest**. OpenRouter's default routing
weights price heavily, so it was selected first on essentially every call —
and, critically, on every retry too. Cheapest and slowest is exactly the
combination that is most contended.

## The trap: `allow_fallbacks` does not cover this

The run already had `allow_fallbacks: true`. The reasoning at the time was
sound-sounding: `only` bounds the pool, so fallback moves *between* allowed
providers and can never leave them.

It did not rescue the run. OpenRouter's documentation defines
`allow_fallbacks` as *"whether to allow backup providers when the primary is
**unavailable**"* and **does not enumerate which HTTP codes count as
unavailable**. Empirically a provider-returned 429 is surfaced to the caller
as `"Provider returned error"` rather than treated as unavailability, so no
re-route happens.

**Do not assume `allow_fallbacks` gives you 429 failover.** It does not.

## What we did instead

Added `REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_SORT` (`provider.sort`:
`price | throughput | latency`) and set `throughput` for the benchmark.

Sorting was preferred over the obvious alternative — dropping DeepInfra from
the allowlist — because:

- Dropping a provider hard-codes *today's* congested host into a denylist.
  Tomorrow a different one is busiest and the same failure returns.
- Sorting by throughput moves load off whichever host is slow *at the time*,
  which is the actual property we care about.
- It preserves the operator's stated provider set rather than silently
  narrowing it.

## What this does not fix

Sorting biases selection; it does not guarantee failover. If every allowed
provider is overloaded at once, calls will still 429 and still dead-letter.
The durable fixes for that are a personal provider key (`is_byok: true`,
which buys dedicated rather than shared limits), lower per-stage worker
concurrency, or a broader allowlist.

Concurrency was a real contributor here: the failures appeared only after
scaling from 1 worker per stage to the reference topology (extract 8 /
normalize 6 / adjudicate 4 / embed 2). At 1 worker the same allowlist ran
1,842 calls with a single failure.

## Rule of thumb

For a long ingestion run against a shared-pool provider set: **sort by
throughput, not price**, and treat the number of concurrent workers as part
of the routing decision rather than an independent knob.
