# Proposed evidence-fidelity fixes

Status: proposed, not binding. The owner requested a separate PR, Opus design
review before implementation, and implementation once approved. Adoption
trigger: Claude Opus5.5 HIGH approval of this concrete scope. Reconcile the
existing binding designs when adopting; no runtime changes precede approval.
Rationale and alternatives: [analysis](../analysis/evidence_fidelity_20261113.md).

## Chosen scope and contracts

### Source-anchored calendar reference

Add a pure formatter in `core/temporal.py` that accepts an optional typed source
anchor. For a known anchor it returns its UTC date/weekday and the seven
strictly preceding UTC dates with weekdays. For unknown time it returns an
explicit unavailable marker. It never reads a clock, parses source language or
selects an event date. It derives only from E2's existing `_chunk_date`, including
D140's preserved text-origin time, not a newer version/upload timestamp.

Supply that compact reference to Claimify as a separate clearly labelled
arithmetic aid, not the document header or source evidence. Keep it out of
Selection and `_source_grounding_elements`. Tell the extractor to consult it
only when the source actually anchors the utterance to that timestamp. Existing
relative-time meaning, source quoting, output shape, grounding and typed interval
validation continue to apply. The table does not decide ambiguous phrasing or
license dating quoted historical/fictional content. Replace the vague-time rule
with one explicit list including "recently", "the other day" and "a few days
ago": never invent an exact day; use only an honestly supported representable
precision, otherwise keep the relative phrase and unknown valid time.

Roll `E2_EXTRACTOR_VERSION` and matching native protocol expectation. No parallel
extractor, parser, validation layer or compatibility path is added. Declare the
one-time Selection/Claimify reprocess boundary; preserve steady-state D56 reuse.

### Legible visual identity text

Replace the description prompt's blanket prohibition on text transcription.
Its existing `observations` field may quote legible names, titles, sign/diagram
labels, product identifiers and other short identifying text as visual
observations. Preserve spelling; do not complete obscured letters or infer
identity from layout. Put uncertain interpretations in `interpretations`.
Quoted text remains untrusted source content, never an instruction.

Dedicated OCR still runs, remains independently attributed under
`Visible text (OCR)`, and is neither replaced nor silently edited. Vision
readings remain `model_observation`/`model_interpretation` under
`Visual description`; conflicts retain both readings. No third field, extra
provider call, automatic crop or schema migration. Roll only the description
prompt identity and its composed converter fingerprint, preserving OCR reuse.

### Reader completeness using existing tools

Update the ordinary versioned consumption skill and the benchmark's corresponding
answer guidance. Replace broad closing reminders with these explicit actions:

- Keep the actual requested place/date/entity name when it is supplied; do not
  replace it with "the lake", "the event" or another generic description.
- For a list or count, account for distinct supported items/events in the
  returned material before finalizing, with correct owners, times and status.
  Do not turn planned activities or repeated mentions into extra events.
- If facts do not supply a required detail or complete owner/reply, inspect
  claims/source chunks using existing operations. If an adjacency result still
  ends before the needed utterance, continue from its appropriate edge chunk
  within the remaining call budget. A configured window is not completeness.
- Stop once the requested parts are supported. If still unresolved at the
  existing budget, preserve qualifications; do not invent missing list members,
  hidden text, dates or attributions. Existing honest-negative semantics remain.

Do not add query loops inside the engine, automatic retrieval calls, budgets,
ranking changes or judge changes. Bump the consumption-skill revision; the
benchmark's existing prompt fingerprint identifies its changed instructions.

## Accepted-design integration and evidence of completion

On approval, fold these contracts into the existing processing-prompt, media and
consumption designs and remove this unchosen proposal. Link the analysis from
`plan/README.md`. Update the user-facing file-types/query guidance in the same PR
without claiming guaranteed OCR recall or benchmark gains.

Meaningful checks must establish pure calendar correctness and no fabricated
anchor, the D140 anchor/wiring, image-lane provenance and checkpoint isolation,
reader/protocol bounds, and real D56 two-version paid-call reuse. No LoCoMo
specific fixtures or provider settings. Any required check that cannot run must
be stated honestly. Submit the implementation to Opus review before considering
it ready; do not present a design approval as code approval or a score result.
