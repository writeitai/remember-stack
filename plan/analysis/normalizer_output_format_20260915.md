# Normalizer prompt: both `observations` and `relations` arrays

This analysis records one measured Gemma/Vertex normalization failure and the
prompt sentence that completed the same input. It is non-binding. It does not
add a numbered architecture decision, a new category, a window, a timeout, a
token cap, an automatic retry, a schema change, or a provider fallback. The
existing `NormalizationResponse` contract already has both lists.

## The question

The normalizer already asks the model for zero or more relations and zero or
more observations. The Python response type and the strict JSON schema already
name both fields. On one live Gemma/Vertex streaming call the model wrote
observations, then never wrote `relations` or closed the object, and padded
whitespace until the provider ended the stream. Should the prompt say, in
plain words, that both arrays must be present?

## What the evidence is, and what it is not

R4 live conv-42 processing on Gemma/Vertex streaming produced observations,
then omitted the required `relations` field and the root JSON closure, then
emitted more than 25,000 whitespace characters. The original exact input was
SHA-256 `16ddbb7415e05ee53b29ebc5f57d903e4bce70586e776ec4e48fed20757b5c3b`
(5,650 bytes). The provider ended the stream incomplete after 406.0716
seconds, 30,078 characters, and no usage.

The pipeline later retried the same failing input. A read-only capture at
2026-09-15 01:09:14 UTC showed the identical SHA, observations, then
whitespace again (24,843 characters, 24,587 trailing). That strengthens
reproducibility for this unique input. It is still one input.

A diagnostic outside ingestion used the identical original input and the
unchanged `NormalizationResponse` schema, with only this sentence inserted
before `SOURCE TIMESTAMP`:

```text
OUTPUT FORMAT
Return one JSON object containing both "observations" and "relations". Both values must be arrays. Use [] when a kind has no output; never omit either field.
```

That call succeeded in 3.9215 seconds: 1,443 input tokens, 96 output tokens,
USD 0.00027405. The validated object had an empty relations array and one
observation. Durable receipt:
[locomo-conv42-gemma-normalization-format-probe-20260915.json](https://github.com/writeitai/ultimate-memory-cloud/blob/cdf2486d/design/analysis/locomo-conv42-gemma-normalization-format-probe-20260915.json)
(diagnostic input SHA-256
`f738b2315c3fa382c963127d6a0fcbaf471e06157418805ad485ce740710df87`,
5,823 bytes — the original 5,650-byte prompt plus the format sentence).
UMC evidence checkpoint `cdf2486d` on
[UMC PR 590](https://github.com/writeitai/ultimate-memory-cloud/pull/590)
records this lane.

This is evidence for this input. It is not proof that every provider hang,
whitespace pad, or incomplete structured output is a missing-field-name
problem, and it does not claim a benchmark score improvement.

Related but distinct measurements stay where they are:
[vertex_processing_limits_20260914.md](vertex_processing_limits_20260914.md),
[gemma_fallback_subsections_20260915.md](gemma_fallback_subsections_20260915.md),
and
[vertex_streamed_completion_20260915.md](vertex_streamed_completion_20260915.md).
Those do not re-open this prompt change.

## The existing contract

`NormalizationResponse` already has two lists, `relations` and
`observations`. Each may be empty. OpenAI-backed generation already sends a
strict JSON schema in which both properties are required and have no
defaults (`test_generation_uses_strict_schema_for_defaulted_response_fields`).
Pydantic still defaults a missing field to an empty tuple when validating a
complete object. That is not the live failure: the stream never produced a
complete object.

The meaning and temporal contracts are unchanged: preserve the source
proposition; keep win / participation / enjoyment distinct; `SOURCE
TIMESTAMP` is when the source spoke; `uses_claim_window` applies only to the
assertion it belongs to. Empty output remains valid. Incomplete JSON remains
an honest generate failure. The worker must not treat a truncated
observations-only stream as a finished empty-relations result.

## Alternatives considered

- **Leave the prompt unchanged.** The schema already requires both fields.
  On this input the constrained decoder still omitted `relations` and padded
  whitespace for minutes. Leaving the prompt would keep that measured stall.
- **Change the schema** (required-without-default Pydantic fields, extra
  wrappers, a different response type). The diagnostic succeeded with the
  existing schema plus one sentence. A schema change is out of scope and
  unneeded for this measurement.
- **Timeout, token cap, automatic retry, or another provider.** Those would
  paper over an incomplete object. They are not this change. Parent owns
  runtime operations.
- **Treat the truncated stream as a finished result.** The observed failure
  is incomplete JSON: observations, then no `relations` field, no root
  closure, then whitespace. That must remain a generate failure. A complete
  object with an empty relations array is valid and does not by itself mean
  assertions were discarded.

The chosen path is the measured sentence in the existing prompt, placed
before `SOURCE TIMESTAMP` as in the successful diagnostic. An extra
empty-output example is unnecessary: the sentence already says to use `[]`.

## What follows if accepted

The normalizer generation rolls so the new prompt is the cache key.
Observation-flush pins that embed that generation roll with it. The LoCoMo
protocol identity rolls from Full-v32 to Full-v33; adapter keys, variants,
and fingerprints roll together. Dataset, models, budgets, retrieval, answer,
judge, and scoring stay the same. Stores ingested under v32 are not this
protocol. No quality or cost improvement is claimed from the wording.
