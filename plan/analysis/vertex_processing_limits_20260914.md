# Vertex processing allowance correction

The conv-42 Gemma processing experiment returned an invalid structuring response
at exactly the adapter default of 4,096 output tokens, then hit its 120-second
client timeout on another structuring call. These defaults came from a
short-answer benchmark assumption, not measured processing requirements.
The user explicitly rejected arbitrary cutoffs that break processing.

Use the documented Gemma output maximum of 128,000 tokens, instead of another
guessed task allowance. The [official endpoint specification](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/google/gemma-4-26b-a4b-it)
was inspected on 2026-09-14; it also lists 262,144 context tokens. Omitting
max_tokens was considered, but could silently inherit a provider default.
The total context constraint still applies. Do not claim unlimited generation.

The default client timeout becomes absent. An operator can still explicitly
configure a deadline or a different model's output allowance. No retry loop,
new queue, or automatic escalation is introduced. A stuck provider can wait
indefinitely and repetitive output can cost more; monitoring and cancellation
remain operational responsibilities. Invalid output still fails validation,
and unknown usage is never a fabricated zero charge. This correction cannot
prove or fix the suspected recursive-schema generation failure. The measured
fallback wire-field correction is
[gemma_fallback_subsections_20260915.md](gemma_fallback_subsections_20260915.md).

The separate UMC experiment runner turns its spend limit into a warning and
keeps independent work moving past missing usage or dead letters. Its accepted
contract is `design/designs/locomo-vertex-lab.md` in the UMC repository. The
engine's provider accounting and ordinary worker recovery contracts are unchanged.
