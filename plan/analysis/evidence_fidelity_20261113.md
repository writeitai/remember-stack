# Evidence fidelity: dates, visible labels and complete answers

Non-binding analysis, 2026-11-13. This supports the companion
[proposal](../proposals/evidence_fidelity_20261113.md); it is not acceptance or
proof of implemented improvement.

## Problem and observed evidence

The completed LoCoMo audit examined all155 remaining incorrect items against
saved tool responses, source turns and selected original pixels. After Opus
review, its primary triage was47 answerer errors,5 reader-coverage gaps,
2 demonstrated ingestion/conversion defects,33 concrete question/reference
conflicts,5 likely judge rejections and63 mixed/ambiguous cases. These labels
are not a corrected score or exhaustive causal decomposition. The detailed
private audit is [cloud PR1009](https://github.com/writeitai/ultimate-memory-cloud/pull/1009).
This proposal addresses demonstrated mechanisms, not benchmark labels.

Three mechanisms have ordinary uses outside conversational evaluation:

1. An extracted relative weekday was off by a week despite a correct source
   timestamp. Elsewhere vague "the other day" became an invented precise day.
2. A park-map title was clearly legible in original pixels but absent from both
   its OCR output and its visual description. Search could not retrieve that
   omitted name. The vision prompt explicitly prohibits transcribing visible
   text, so omission is partly consistent with our own instructions.
3. Some reader responses named the required place or listed the required items,
   but the answer omitted them. In other cases fact-only stopping or a bounded
   adjacent-chunk read ended before the needed owner/reply. A successful
   `window=2` read is a two-chunk neighborhood, not an exchange-completeness
   guarantee. These gaps do not by themselves prove a SQL/index/ranking fault.

## Current contracts and implementation inspected

- `src/rememberstack/workers/e2.py`, `_CLAIMIFY_PROMPT`, `_chunk_date`,
  `_bundle_text` and `_source_grounding_elements`: relative time is model-resolved
  against the preserved source/text-origin timestamp; source passages remain
  verbatim; date fields must agree with the date written into a claim.
- `src/rememberstack/core/temporal.py`: canonical UTC interval arithmetic exists.
  The proposed calendar aid consumes a typed timestamp, not natural language;
  it is not a second date parser or validity representation.
- `src/rememberstack/adapters/converters/image_ocr_description.py`: two independent
  required lanes, byte-identical pixels, separately attributed results and
  independently keyed durable lane checkpoints. `_DESCRIPTION_PROMPT` currently
  forbids text transcription. `DESCRIPTION_PROMPT_VERSION` keys the description
  lane independently from OCR.
- `src/rememberstack/core/consumption_skill.py` and
  `benchmarks/locomo/protocol.py`: source fallback, adjacency and completeness
  guidance already exist. Changes should make the stopping/action criteria
  concrete instead of adding another generic warning paragraph.
- D56/D140 reuse in `CLAUDE.md`, `e1_chunks_design.md`,
  `effective_time_and_section_references_design.md`; D107/D120 temporal meaning;
  D65/D115 media provenance in `media_design.md`; D51 consumption guidance.

## Alternatives and tradeoffs

**More candidates or an LLM reranker** cannot restore an omitted image title or
repair an already invented date. It might reduce distraction when evidence has
arrived, but that benefit was not established. It also adds cost or larger
contexts. No such change is selected.

**A natural-language temporal parser or post-hoc date validator** would require
scope/anchor selection, languages, ambiguity rules and agreement with immutable
claim text. A seven-day arithmetic reference is smaller: the existing extractor
still chooses whether the source supports a date, while code supplies exact
calendar facts. It reduces arithmetic work but does not guarantee comprehension.

**More OCR calls, automatic crops or a third vision lane** add model calls,
transforms and new reuse behavior. The existing vision call already sees the
original pixels. Letting it quote legible identity labels as explicitly visual
observations is the smallest change. Dedicated OCR remains independent and is
never silently replaced or corrected.

**Larger default adjacency windows or automatic unbounded reading** increase
context and cost for every query. Teach the existing reader to continue from a
returned edge chunk only when a required owner/reply/detail is still missing,
within the existing call budget. A full result list remains bounded; never call
it exhaustive merely because it used the maximum k.

## Costs, reuse and rollout

No additional ingestion model calls are introduced per normal attempt. A compact
calendar reference adds at most eight dated weekday rows plus explanatory text
when a source anchor exists; measure the actual rendered token delta. Its cost
is `affected Claimify calls × added input tokens × configured input rate`.
Vision may return more identifying text within its existing4,096-token and
16,000-character limits. Do not infer semantic quality from mocked tests.
Reader follow-up can consume existing tool/model-call allowance only when needed;
no allowance or top-k increases are part of this scope.

Changing extraction instructions rolls `E2_EXTRACTOR_VERSION`. Because this
existing generation participates in Selection and Claimify identities, an
explicit new-generation reprocess can repeat both stages once. That cost is a
real rollout boundary, not an undisclosed edit-related regression. Under the
new stable generation, unchanged sections must retain paid reuse on the next
version. Embedding/chunker generations are unchanged.

Changing the vision prompt rolls `DESCRIPTION_PROMPT_VERSION`, the description
checkpoint and the composed image-converter identity. Existing OCR checkpoints
remain reusable. Existing representations/readings remain immutable; they are
not relabelled or automatically reingested. The old full benchmark stays frozen.
A new campaign claiming the new extraction/image profile would need to request
those new products explicitly; answering old stores cannot demonstrate the new
upstream behavior. This PR does not launch that campaign.

## Boundaries and validation

The fixes operate entirely inside the OSS engine and its ordinary consumption
skill; the benchmark uses the same advice. No question IDs, reference answers,
named dataset entities, new providers, SQL changes, judge changes or score
adjustments belong in runtime logic or tests. Source contents stay untrusted.
Calendar aids cannot supply names, events or facts, cannot anchor embedded
historical/fictional speech, and are excluded from the grounding text union.

Use ordinary examples from notices, schedules, product labels and maps. Verify
calendar arithmetic over all anchor weekdays, month/year/leap boundaries,
UTC-offset and unknown-date cases, and the preserved D140 text-origin anchor.
Check original pixel bytes and OCR provenance survive, and that only the
changed description lane repeats after a prompt revision. Run real PostgreSQL
D56 two-version reuse proofs in an isolated test database, alongside pure
text-origin, extraction, converter, consumption-skill and protocol checks.
Provider doubles prove wiring/reuse, not that a real model now answers better.
