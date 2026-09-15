# Normalizer prompt: name nested observation, relation, and EntityRef fields

This analysis records one measured Gemma/Vertex nested-object stall after the
root both-array instruction was already in the prompt, and the six-line
instruction that completed the same input. It is non-binding. It does not add
a numbered architecture decision, a new category, a window, a timeout, a
token cap, an automatic retry, a schema change, or a provider fallback. The
existing `NormalizationResponse` contract already has these nested fields.

## The question

The normalizer already asks for observations and relations. PR #408 already
says both arrays must be present, using `[]` when a kind has no output. The
Python types already declare every nested field. The strict JSON schema
already requires every declared property, including nullable
`EntityRef.surface`. The prompt prose still says to set `surface` when the
claim spelling differs from the canonical name, and does not name the other
nested fields. On one live Gemma/Vertex streaming call the model wrote an
observation subject `name`. At capture time it had not supplied `surface` or
closed the object and had accumulated trailing whitespace. Should the prompt
name every existing nested field in plain words, including `surface=null`
when the spelling matches?

## What the evidence is, and what it is not

R6 live conv-42 processing on Gemma/Vertex (processing-only, all 29 sessions)
wrote an observation subject after `name=Nate`. Capture at 2026-09-15
02:50:48.376734 UTC showed 25,564 characters, of which 25,201 were trailing
whitespace. At capture time the object had not supplied `surface` or closed.
That in-flight capture is not a terminal provider result. The original exact
input was SHA-256
`cf9d74d936c27b735ddbcb5b22742428d4e4a9990618644a63e9b5c2e3416835`
(5,792 bytes). That prompt already contained PR #408's both-array sentence.

The same input completed in earlier R5. This is not a deterministic failure
of that unique input, not a universal fix, not corpus reliability, and not a
provider-internal cause.

A diagnostic outside ingestion used the identical original input and the
unchanged `NormalizationResponse` schema, with only this six-line instruction
inserted immediately before `SOURCE TIMESTAMP:` (the root both-array sentence
stayed):

```text
Each observation contains context_refs, statement, subject, and uses_claim_window.
Each relation contains context_refs, object, predicate, subject, and uses_claim_window.
Every entity reference in subject, object, or context_refs contains both name and surface.
Use surface=null when the claim spelling matches the canonical name; otherwise use the exact claim spelling.
Use context_refs=[] when there are no context references. uses_claim_window is always true or false.
Include every field, even when its value is null or an empty array.
```

Three sequential repetitions on that one unique input all completed, each
with 1,502 input tokens, 136 output tokens, and USD 0.0003069:

| Repetition | Elapsed |
| --- | --- |
| 1 | 3.699973 s |
| 2 | 3.320138 s |
| 3 | 4.339743 s |

Each typed object had one observation and an empty relations array. The
first two receipts record typed output only; they do not independently prove
raw wire presence of every required field. The third records the raw
completed body before Pydantic and verifies all required root, item, and
entity-reference fields (three context refs). Diagnostic input SHA-256
`f4fca37d80b0e4147a832df8b1e54239a3165137a970c75414e6ade2ea5e32b3`
(6,333 bytes — the original 5,792-byte prompt plus the nested instruction).
The raw subject used `surface=null`; the model still copied matching surfaces
on other context refs. That is observed completion text, not a writer-semantics
change. Generic hobbies appearing as context refs are a separate quality
concern and are not this field-format fix.

Durable receipts:
[nested-field probes](https://github.com/writeitai/ultimate-memory-cloud/blob/2f64281c/design/analysis/locomo-conv42-gemma-normalizer-nested-probes-20260915.json)
(UMC `2f64281c`). Parent processing notes:
[conv-42 processing](https://github.com/writeitai/ultimate-memory-cloud/blob/2f64281c/design/analysis/locomo-conv42-gemma-full-processing-20260914.md).

This is evidence for this one unique input. Three successes on that input are
not corpus reliability. It is not proof that every provider hang, whitespace
pad, or incomplete structured output is a missing nested-field-name problem,
and it does not claim a benchmark score improvement.

Related but distinct measurements stay where they are:
[normalizer_output_format_20260915.md](normalizer_output_format_20260915.md),
[t4_output_format_20260915.md](t4_output_format_20260915.md),
[fact_adjudication_output_format_20260915.md](fact_adjudication_output_format_20260915.md),
[vertex_processing_limits_20260914.md](vertex_processing_limits_20260914.md),
[gemma_fallback_subsections_20260915.md](gemma_fallback_subsections_20260915.md),
and
[vertex_streamed_completion_20260915.md](vertex_streamed_completion_20260915.md).

## The existing contract

`NormalizationResponse` already has two lists, `relations` and
`observations`. Each observation already has `context_refs`, `statement`,
`subject`, and `uses_claim_window`. Each relation already has
`context_refs`, `object`, `predicate`, `subject`, and `uses_claim_window`.
Every entity reference already has `name` and `surface`. `surface` may be
null; it is still required on the strict wire schema.

Vertex and OpenRouter adapters use the shared strict-schema transformation
in which every declared property is required and defaults are stripped
(`test_strict_schema_closes_every_nested_object_and_removes_defaults`). This
run is Gemma on Vertex; the shared transformation is not an OpenAI backend.
Pydantic still defaults a missing `surface` to `None` when a Python caller
constructs an `EntityRef`. That convenience is not the requested wire form.

The meaning and temporal contracts are unchanged: preserve the source
proposition; keep win / participation / enjoyment distinct; `SOURCE
TIMESTAMP` is when the source spoke; `uses_claim_window` applies only to the
assertion it belongs to. Empty `context_refs` remains valid as `[]`.
Incomplete JSON remains an honest generate failure. The worker must not treat
a truncated subject-name stream as a finished result.

## Alternatives considered

- **Leave the prompt unchanged.** The schema already requires nested fields,
  including nullable `surface`. The root both-array sentence is already
  present. At capture time this call still lacked `surface` and object
  closure and had accumulated trailing whitespace. Leaving the prompt would
  keep that measured stall.
- **Change the schema** (required-without-default Pydantic fields, extra
  wrappers, a different response type). The diagnostic succeeded with the
  existing schema plus the six-line instruction. A schema change is out of
  scope and unneeded for this measurement.
- **Inject the full JSON schema into the prompt, or add a generic
  renderer.** That would duplicate nested schema text and change every
  processing request. It has not been measured. This concrete field-name
  instruction does not need that machinery.
- **Timeout, token cap, automatic retry, or another provider.** Those would
  paper over an incomplete object. They are not this change. Parent owns
  runtime operations. Slow observation or provider elapsed time must not
  become a timeout or cancellation policy here.
- **Treat the truncated stream as a finished result.** Incomplete JSON must
  remain a generate failure. An in-flight capture with trailing whitespace
  is not a completed empty-surface object.

The chosen path is the measured six-line instruction in the existing prompt,
placed immediately before `SOURCE TIMESTAMP:` as in the successful
diagnostic, with the same wording and newlines so the live proof does not
need another paid call. The root both-array sentence stays.

## What follows if accepted

The normalizer generation appends `:nested-fields-1` so the new prompt is
the cache key. Observation-flush pins that embed that generation roll with
it. Fact-adjudicator and resolver prompt generations themselves are
unchanged. The LoCoMo protocol identity rolls from Full-v34 to Full-v35;
adapter keys, variants, and fingerprints roll together. Dataset, models,
budgets, retrieval, answer, judge, and scoring stay the same. Stores
ingested under v34 are not this protocol. No quality or cost improvement is
claimed from the wording.
