# Luna 6, Jev and independent LoCoMo judging

Status: implementation analysis, 2026-10-23. The benchmark is user-paused;
this change does not authorize ingestion, answering, grading or deployment.

The retained campaign used Luna 5.6 for core ingestion, Jev 1.13.0 for
adjudication, Luna 6 for cached image descriptions, Luna 6 Pro for answering
and GPT-6 Sol for grading. A future configuration must describe the roles
explicitly rather than silently relabel those stores as Luna 6 ingestion.

## Requested behavior

Prepare the current Luna 6/Flex protocol: ingestion uses lower reasoning,
answering and grading use high, and fact adjudication uses Jev. Keep the
existing scorer's semantic policy. Prepare an independently fingerprinted
Mem0-like scorer over saved answers; its output must not replace canonical
grades or trigger answer generation. Neither scorer may use reference answers
to guide the answering agent. Do not add a reranker.

The answering policy should check the distinct evidence needs of the question
and inspect adjacent source turns when a response depends on its preceding
question. These rules apply to all questions, without IDs or expected answers.
General OCR/extraction changes are a separate engine concern and require their
own source-provenance and paid-reuse analysis.

## PR #447: retain and reject deliberately

[PR #447](https://github.com/writeitai/remember-stack/pull/447), inspected at
`88f7bb34be6d3e904fd9d57d54aa099d65d3d5fe`, supplies useful explicit Flex routing,
answer/judge effort pins, configurable client timeouts and stable readiness
identity comparison. The replacement uses a new current full-v39 identity and removes superseded
execution branches. Historical artifacts keep their original identities and
remain available for read-only inspection; they are not runnable defaults.
Its judge-policy change is not part of this replacement: the user asked to
retain the existing scorer. Domain-specific allergy wording is also excluded.
The replacement must cover provider configuration, serialized run identity,
shard ingestion environment and backup/runtime metadata consistently.

## Alternatives and failure behavior

Changing the global engine model defaults would affect ordinary deployments
and unrelated protocol variants. An explicit protocol keeps that choice in
benchmark configuration. Flex is a hard `openai/flex` allowlist, with no
silent standard-price escape. Capacity errors remain visible and bounded by
the existing adapter's retry policy. High effort is request-local for the two
evaluation seats; a per-model ingestion map must not force high effort on all
calls merely because both seats use Luna.

Independent grading reads a frozen input snapshot and records its answer
hashes, policy/version, upstream reference, prompt hash, model, effort, routing,
usage and outcomes separately. A changed input or grading identity must require
a new output directory. Each terminal record is checkpointed before another
call. Failures remain distinguishable from incorrect answers. Model calls
require an explicit execution flag and a reported-cost ceiling.

The Mem0-compatible policy accepts one correct list item, dates within fourteen
days, durations within fifty percent, and broad paraphrases. Optional evidence
can increase acceptance; it must not make grading stricter. These differences
must accompany any score comparison, rather than be described as equivalent
metrics. Original images are not implicitly available to the text-only judge.

## Sources

Primary documentation retrieved 2026-10-23:

- [OpenAI Luna 6 model contract](https://developers.openai.com/api/docs/models/gpt-6-luna):
  preserve the requested model and distinguish reasoning effort from Pro mode.
  OpenRouter's adapter contract, not direct OpenAI tool-call assumptions,
  governs this repository's requests.
- [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)
  and [service tiers](https://openrouter.ai/docs/guides/features/service-tiers):
  tier-suffixed provider slugs explicitly select Flex; a hard allowlist avoids
  ordinary marketplace fallback.
- [TypeSafe System One](https://docs.typesafe.ai/api/system-one): the response
  exposes model and usage; engine choice and the TypeSafe model belong in
  non-secret provenance.
- [Mem0 judge source at a pinned revision](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/benchmarks/locomo/prompts.py#L207-L303).
  The companion scorer will paraphrase its policy and retain this attribution.

No score improvement is established by offline code tests. Required review is
Claude Code with Opus 5.5 at high effort, as explicitly requested by the owner.

## Image-description default and paid reuse

The owner explicitly requested Luna for visual processing too. Both the native
image converter default and Compose now select `openai/gpt-6-luna`; the LoCoMo
profile pins low vision effort and the Flex allowlist. Mistral remains the
dedicated OCR lane. The old Gemini default is not retained as a fallback.

The configured vision model already joins the converter and lane checkpoint
identity. Changing that default therefore re-keys affected default-configured
image conversions when processed under the new code. Explicit model overrides
remain explicit. Effort and routing join the vision identity when configured;
unchanged successful OCR is independently reusable. No global E0/E1/E2 version
is bumped by these configuration changes, and no retained store is reprocessed
by editing configuration. Offline checkpoint tests count both lane calls.

The new named protocol also pins the current main chunker generation with
eligibility cuts. Historical run artifacts retain their recorded generations. Their execution
branches are retired; current execution uses one full-v39 profile. These
identities must not be silently exchanged while restoring old stores.

## Retiring executable historical protocols

The owner's 2026-10-23 question exposed a stale chunker pin: D138 changed the
shipping chunker at commit `449c58cc3` on 2026-09-27 while the benchmark still
expected the older generation. The engine had one implementation, but a
manually copied benchmark table drifted. An all-component comparison with
`profiles/selfhost.py:_expected_components` now catches that mismatch in CI.

The replacement has one executable identity, `full-v39`. Superseded model/provider
protocol branches, shard environment branches and their tests/documented commands
are removed. Read-only validation uses saved run metadata instead of resolving
an old executable protocol. Source hashes, saved generations, answers and original
grades remain unchanged. Shared provider adapters used elsewhere are not protocol
fallbacks and remain available to their actual callers.
