# T4 prompt: name all four existing JSON fields

This analysis records one measured Gemma/Vertex T4 failure and the prompt
sentence that completed the same input. It is non-binding. It does not add a
numbered architecture decision, a new category, a window, a timeout, a token
cap, an automatic retry, a schema change, a matching threshold, or an identity
policy. The existing `T4Selection` contract already has four fields.

## The question

T4 already asks the model for one binary match-or-new choice. The Python
response type and the strict JSON schema already name `decision`,
`candidate_id`, `confidence`, and `rationale`. On one live Gemma/Vertex
streaming call the model wrote `candidate_id` and `confidence`, then never
wrote `decision` or `rationale` or closed the object, and padded whitespace
until the provider ended the stream. Should the prompt say, in plain words,
that all four fields must be present?

## What the evidence is, and what it is not

R5 live conv-42 processing on Gemma/Vertex streaming produced:

```text
{
  "candidate_id": null,
  "confidence": 1.0
```

then omitted `decision`, `rationale`, and the root JSON closure, then emitted
whitespace. The original exact input was SHA-256
`1480aa369382057ff0b557937852b52ecb79a741f1085981f9ce78be908caa82`
(1,032 bytes). Capture at 2026-09-15 01:46:42 UTC showed 9,693 characters, of
which 9,648 were trailing whitespace. The original call later ended naturally
at 01:54:33 UTC after 568.923 seconds and 55,770 characters, still incomplete,
with no usage. Four following provider calls then returned 503 / backend
connection abort; those cluster errors are separate from this incomplete T4
object. Do not invent a retry policy from them.

A diagnostic outside ingestion used the identical original input and the
unchanged `T4Selection` schema, with only this instruction inserted
immediately before `MENTION:` (two newlines after the instruction):

```text
OUTPUT FORMAT
Return one JSON object with all four fields: candidate_id, confidence, decision, and rationale. decision is "match" or "new". confidence is a number from 0 to 1. rationale is a short explanation or null. Include every field, even when its value is null.
```

That call succeeded in 2.73246 seconds: 362 input tokens, 68 output tokens,
USD 0.0000951. The validated object had `decision=new` and all four fields.
Durable receipt:
[locomo-conv42-gemma-t4-format-probe-20260915.json](https://github.com/writeitai/ultimate-memory-cloud/blob/7299dbd4/design/analysis/locomo-conv42-gemma-t4-format-probe-20260915.json)
(diagnostic input SHA-256
`24021e783a7e53699fbb671f64b939051b8590efec976e07c16308bc80a45480`,
1,301 bytes — the original 1,032-byte prompt plus the format instruction).
The raw probe receipt reused an earlier normalization purpose label; the
durable receipt corrects the purpose to T4.

This is evidence for this input. It is not proof that every provider hang,
whitespace pad, or incomplete structured output is a missing-field-name
problem, and it does not claim a benchmark score improvement. It is also not
the fact-adjudication prompt/schema contradiction recorded separately in
[fact_adjudication_output_format_20260915.md](fact_adjudication_output_format_20260915.md).

Related measurements stay where they are:
[normalizer_output_format_20260915.md](normalizer_output_format_20260915.md),
[vertex_processing_limits_20260914.md](vertex_processing_limits_20260914.md),
[gemma_fallback_subsections_20260915.md](gemma_fallback_subsections_20260915.md),
and
[vertex_streamed_completion_20260915.md](vertex_streamed_completion_20260915.md).
The parent processing-prompt audit is
[processing-output-format-audit-20260915.md](https://github.com/writeitai/ultimate-memory-cloud/blob/bdd7d464/design/analysis/processing-output-format-audit-20260915.md).

## The existing contract

`T4Selection` already has four fields: `decision` (`match` or `new`),
`candidate_id` (required for match, null for new), `confidence` (0 to 1), and
`rationale` (short explanation or null). OpenAI-backed generation already
sends a strict JSON schema in which every property is required and defaults
are stripped (`test_strict_schema_closes_every_nested_object_and_removes_defaults`
includes `T4Selection`). The live failure never produced a complete object.

Match bias, candidate order, temperature 0.0, and T3 bands are unchanged.
Incomplete JSON remains an honest generate failure. The worker must not treat
a truncated `candidate_id`/`confidence` stream as a finished `new` or `match`.

## Alternatives considered

- **Leave the prompt unchanged.** The schema already requires all four fields.
  On this input the constrained decoder still omitted `decision` and
  `rationale` and padded whitespace for minutes. Leaving the prompt would keep
  that measured stall.
- **Change the schema** (required-without-default Pydantic fields, extra
  wrappers, a different response type). The diagnostic succeeded with the
  existing schema plus one instruction. A schema change is out of scope and
  unneeded for this measurement.
- **Timeout, token cap, automatic retry, or another provider.** Those would
  paper over an incomplete object. They are not this change. Parent owns
  runtime operations.
- **Treat the truncated stream as a finished result.** The observed failure
  is incomplete JSON. That must remain a generate failure.

The chosen path is the measured instruction in the existing prompt, placed
before `MENTION:` as in the successful diagnostic, with the same wording and
whitespace so the live proof does not need another paid call.

## What follows if accepted

The resolver generation rolls so T4 decisions under the new prompt do not
share D22 provenance with `resolver-2026.08g`. Thresholds and identity policy
do not change. The normalizer generation appends a T4-format marker so
pipeline work that embeds resolution is distinguished. The LoCoMo protocol
identity rolls from Full-v33 to Full-v34 together with the fact-adjudication
output-format correction. Adapter keys, variants, and fingerprints roll
together. Dataset, models, budgets, retrieval, answer, judge, and scoring
stay the same. Stores ingested under v33 are not this protocol. No quality or
cost improvement is claimed from the wording.
