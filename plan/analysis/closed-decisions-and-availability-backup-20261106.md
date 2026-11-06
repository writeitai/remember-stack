# Closed resolver choices and an optional native decision backup

Non-binding analysis, 2026-11-06. The owner requested enforcing the resolver's
existing candidate-or-new contract and adding OpenAI Decisions as a backup
option for the decision provider. These are general engine changes; no benchmark
question, expected answer, retrieval policy or scorer is changed.

## Candidate selection

The resolver already permits `match` with a supplied entity ID or `new` with a
null ID. Its generic UUID response schema does not enumerate the snapshot's
actual IDs. One observed normalization failure selected an unsupplied UUID;
the post-response guard rejected it correctly. Enforce the same closed set in
the provider JSON schema and in local validation. Keep the explicit new-entity
outcome and the post-response guard. Do not reinterpret malformed matches as
new entities, which could multiply duplicates.

This enforces an existing contract rather than changing the meaning of a valid
decision. No prompt, extractor/chunker generation, embedding input or paid reuse
key changes. Previously accepted valid decisions remain valid. Only pending or
explicitly replayed unresolved work uses the stricter response schema.

## Provider backup

OpenAI Decisions is generally available on the native `/v1/decisions` endpoint
with `gpt-6-luna`. It accepts shared evidence and typed predicate, choice and
score questions. Its choice primitive returns a supplied value and a probability
distribution. The existing `SystemOnePort` is the substrate boundary; adapt
request/response shapes there, leaving adjudication policy in the spine.

Keep Jev primary and the backup opt-in, so an existing deployment or pinned
benchmark cannot silently change providers. Preserve the full option set and
question meaning. A valid low-confidence answer is a policy result, not an
availability failure and not a reason to shop for a different verdict. Evaluate
provider thresholds on labeled application cases rather than assuming equal
confidence values imply equal error rates. Preserve the serving provider/model
and measured usage. Never relabel native Decisions as an OpenRouter call.

Classify availability errors separately from invalid credentials, bad requests,
malformed answers and unaccounted responses. Unknown accounting must stay
unknown; do not mask an accounting failure with a second paid call. The selected
policy permits connection-establishment failures and documented TypeSafe 429/529
rejections after bounded retries, excluding any response with answers or usage.
Read timeouts and generic gateway errors remain ambiguous and do not authorize
the backup. This narrower policy is chosen over falling back on every error:
it preserves configuration failures and potentially billed responses for repair.
No direct OpenAI key is required for offline
mock-transport tests; live execution needs the native API credential, separate
from the existing OpenRouter balance.

The binding design fixes the native price basis, including implicit 30-minute
cache writes, cache reads, long context and regional processing. The adapter
retains reported tokens and computes costs with Decimal before persistence;
it does not round small calls to zero. The native API and OpenRouter's chat
Flex lane have separate prices and credentials. No live native probe or native
account access is claimed by mock-transport tests. The already prepared conv-47
run stays on its immutable 9266b8dd source and Jev-only policy.

Sources retrieved 2026-11-06:

- [OpenAI Decisions guide](https://developers.openai.com/api/docs/guides/decisions): types, fixed options, confidence, usage and native endpoint.
- [OpenAI Decisions reference](https://developers.openai.com/api/reference/resources/decisions/methods/create): request and response contract.
- [TypeSafe Choice](https://docs.typesafe.ai/primitives/choice): criteria and probability distribution.
- [TypeSafe confidence](https://docs.typesafe.ai/confidence): concentration is not an overall correctness guarantee; thresholds depend on the task.
- [TypeSafe errors](https://docs.typesafe.ai/api/errors): request errors and explicit rate/overload responses.
- [OpenAI pricing](https://developers.openai.com/api/docs/pricing): short/long input, cache read/write rates and regional multipliers.
