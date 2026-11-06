# OpenRouter accounting failures and Flex rejection identity

Non-binding analysis, 2026-11-06. Issue #525 concerns three ingestion tasks
whose inline accounting was unusable and whose generation metadata returned
404. The stopped validation preserves its stores and diagnostics in UMC PR
#766. The original response bodies and generation IDs were not retained, so
their precise upstream cause is not established.

The adapter already polls existing metadata five times, at delays 0/1/2/3/5
seconds, without issuing another generation. Increasing that loop alone cannot
explain or resolve the missing original response. A 100-call synthetic probe
with four concurrent requests did not reproduce the failure. Another bounded
probe used the ingestion token allowance with sixteen concurrent requests;
another 100 calls also completed without an unusable response. These bounded
probes do not establish the original upstream cause.

The inspected retry classifier rejects every response containing an `id`, even
when an HTTP-200 error envelope has no usage or choices. OpenRouter documents
that such errors can carry a generation ID assigned before successful output.
OpenAI separately documents that a Flex Resource Unavailable rejection is not
charged. An ID alone therefore does not establish that this specific rejection
was billed. It also does not establish that every other error is unbilled.

The narrow remedy is to recognize the explicit Flex
capacity rejection before interpreting the response as successful accounting,
and let its existing bounded capacity-retry path run. Generic errors, partial
choices, positive or incomplete usage, missing usage on a completed response,
and exhausted metadata recovery must retain fail-closed behavior. No unknown
cost becomes zero and no price-tier fallback is introduced.

Separately, accounting failures must retain bounded generation identifiers,
the original inline parsing reason, metadata lookup count and final HTTP status
in the worker's durable error. Provider messages and completion content stay
out of ordinary logs. That diagnostic change helps reconcile a future genuine
accounting failure without regenerating an already-paid response. The existing
opt-in invalid-completion capture also retains the complete unaccounted response
as a private mode-0600 artifact; its default remains off. This preserves the
evidence needed to investigate an uncommon failure without guessing its cost.
A durable
paid-response replay subsystem is not needed to classify a known pre-generation
Flex rejection; it would require a separate contract if later evidence calls
for it.

Sources retrieved 2026-11-06:

- [OpenRouter non-streaming errors](https://openrouter.ai/docs/api_reference/errors-and-debugging#non-streaming-requests): an HTTP-200 error can contain an ID and no choices.
- [OpenAI Flex resource errors](https://developers.openai.com/api/docs/guides/flex-processing#resource-unavailable-errors): Resource Unavailable is uncharged and can be retried with backoff.
- [OpenRouter accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting): successful responses include usage automatically; existing generation IDs support asynchronous lookup.

No prompt, model, extraction identity, embedding policy, source representation
or reuse generation changes. Completed paid products keep their current reuse
contracts. Tests must show bounded retries for a confirmed unbilled rejection,
and exactly one generation for every response that might contain paid output.
