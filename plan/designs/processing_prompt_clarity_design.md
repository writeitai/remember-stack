# Clear processing instructions that preserve assertions

**Status:** D120, accepted by the user on 2026-09-14; binding when merged.
**Analysis:** [lean processing evidence](../analysis/lean_processing_contracts.md).
This amends the processing-prompt portions of D118 and E2/E3 contracts. It does
not change the fact schema, adjudication writer operations or retrieval semantics.

> **Output-format clarification (2026-09-15).** The normalizer prompt states
> that one JSON object must contain both `observations` and `relations`
> arrays, using `[]` when a kind has no output. That is the existing
> `NormalizationResponse` shape, not a new numbered decision. Meaning and
> temporal contracts are unchanged. Evidence:
> [normalizer output format](../analysis/normalizer_output_format_20260915.md).
>
> **T4 and fact output-format clarification (2026-09-15).** The T4 prompt
> states that one JSON object must contain all four existing fields:
> `candidate_id`, `confidence`, `decision`, and `rationale`, including nulls.
> That completed one captured Gemma/Vertex T4 input. The fact-adjudication
> prompt names all nine existing `PromptFactDecision` fields, uses `[]` when
> an array has no operations, and says `window=null` when no explicit date
> replacement is intended. That
> corrects a prompt/schema contradiction; it is not a reproduced
> fact-adjudication provider failure. Neither change is a new numbered
> decision. Meaning, identity policy, and temporal contracts are unchanged.
> Evidence: [T4 output format](../analysis/t4_output_format_20260915.md),
> [fact output format](../analysis/fact_adjudication_output_format_20260915.md).
>
> **Normalizer nested-field clarification (2026-09-15).** After the root
> both-array sentence, the normalizer prompt names every existing nested
> field on observations, relations, and entity references. It uses
> `context_refs=[]` when there are no context references, an explicit
> boolean `uses_claim_window`, and `surface=null` when the claim spelling
> matches the canonical name. That completed one captured Gemma/Vertex
> input that had already received the both-array sentence and, at capture
> time, was incomplete inside an observation subject after `name`. Python
> defaults still permit callers to omit nullable `surface`; the strict wire
> schema does not.
> This is not a new numbered decision. Meaning and temporal contracts are
> unchanged. Evidence:
> [normalizer nested fields](../analysis/normalizer_nested_fields_20260915.md).
>
> **Fact target-discipline clarification (2026-09-15).** When target is a
> supplied F-name, `new_facts` must be empty, and the incoming assertion's
> own placement is decided by target/stance alone, never by a `support_moves`
> entry. That addresses the R8 Vertex/Gemma dead-letters (declared-but-unused
> N-names, incoming support filed as a move) without touching the schema or
> the translator, which keep rejecting both shapes. Evidence:
> [reliability fixes](../analysis/fact_adjudication_reliability_fixes_20260915.md).
>
> **Fact new-fact reference clarification (2026-09-15).** The fact prompt
> already forbids reserved F/C/A/E/S/T/W names for new facts. A separate
> Gemma/Vertex control on one captured R7 input still named a new fact `F2`
> (`target=F2`, `new_facts` handle F2, assertion A1); the unchanged
> translator rejected it. Original live-run tracebacks did not retain that
> full output. The prompt now says to choose N1 rather than continue
> F-numbering, declare that name in `new_facts`, and use the same name as
> `target`. Two complete nine-field JSON examples show structure only:
> repeating supplied F1 with no other changes, or incoming A1 as a different
> proposition declared as N1. Other operations remain allowed. Invalid F
> references remain rejected. Schema and translator are unchanged. This is
> not a new numbered decision. Meaning, identity policy, and temporal
> contracts are unchanged. Evidence:
> [new-fact references](../analysis/fact_adjudication_new_fact_references_20260915.md).

## Problem and decision

Sources say things; extracted claims preserve them; normalized assertions express
one relation or observation; stored facts interpret supporting and contrary
testimony. A person or event is an entity. Those identities are distinct from
the identity of a proposition about them. Instructions must explain these
concepts in plain language before describing response fields.

“Nate won Tournament A,” “Nate participated in Tournament A” and “Nate enjoyed
Tournament A” concern the same entities but assert different things. A win may
imply participation, but recording only participation loses the result. Attaching
testimony must preserve the substantive incoming assertion, including attribution,
negation, degree, dates and distinguishing qualifiers.

## Adjudication contract

The model decides one incoming assertion using supplied source evidence and
candidate facts. It distinguishes repetition of a proposition, a different
proposition about the same referent, contrary testimony and correction of an
existing proposition. These are explanations of existing operations, not new
stored categories or an extra classification call.

| Incoming assertion and context | Existing fact | Required interpretation |
| --- | --- | --- |
| Took first place in the same tournament | Won that tournament | Compatible paraphrase can share the winning fact |
| Won that tournament | Enjoyed that tournament | Keep winning separately |
| Won that tournament | Participated in that tournament | Do not lose the stronger winning assertion |
| Participated in / enjoyed that tournament | Won that tournament | Compatible surrounding context, not positive evidence of the win; keep the weaker assertion separate |
| Corrects the same win's date from 5 November to 6 November | Won that tournament, chosen window 5 November | Attach and replace the chosen window with cited evidence. The candidate proposition is date-neutral; the writer cannot rewrite the stored statement |
| Won another tournament | Won the earlier tournament | Distinct winning fact even if wording/dates match |
| Source says Nate claimed to win | Nate won | Preserve attribution; the statement of a claim is not automatically an unqualified win |
| Lost the same tournament | Won that tournament | Handle contrary testimony explicitly using supported contradiction/correction operations |

An insufficient destination requires preserving the assertion separately through
the existing new-fact path. The current writer does not support arbitrary
rewriting of an existing fact's statement; the prompt must not promise it.
Neither equal text nor equal event/entity IDs authorizes automatic attachment.
The original assertions must be available for permitted support moves. Repeated
testimony for one result must not mint a new win merely because a source or date
spelling differs. All output operations retain D118 validation and receipts.

## Date language and examples

Prompts distinguish source reporting time, source-stated world dates, chosen fact
world dates and database belief timestamps. They never use “date” or “identity”
without a clear referent when more than one is present.

- A source published on 10 November can report a win on 5 November. Publication
  is not a fallback occurrence date when the world date is unknown.
- Raw source ends are inclusive. “3 November through 5 November,” day precision,
  becomes a stored window `[3 November 00:00 UTC, 6 November 00:00 UTC)`.
  Stored ends are already exclusive; do not advance 6 November again.
- A day represents a calendar day, not a precisely observed midnight instant.
  Month/year precision must not invent a precise day.
- A missing end does not establish ongoing status. Only explicitly supported
  `open` has that meaning. D118's partial-window shapes remain valid.
- Attaching evidence does not itself change chosen dates. Omitted replacement
  preserves them; an explicit supported replacement changes or clears them.
- A claim containing a 2019 hiring and a 1990 company founding does not assign
  the hiring window to both normalized assertions. `uses_claim_window` applies
  only to the particular assertion for which that source window is evidence.

Use existing canonical arithmetic and window validation; do not introduce a
parallel date parser or vocabulary. The exact enum/schema definitions are the
machine contract, with plain explanations next to their semantic use.

## Prompt organization and safety

Each affected prompt states purpose, inputs, decision rules, representative
contrasting examples and output requirements in that order where practical.
Use one consistent vocabulary; remove superseded/duplicated instructions instead
of accumulating exceptions. Extractor, normalizer and adjudicator definitions
must agree. Short input handles do not justify cryptic instructions.

The normalizer already returns one `NormalizationResponse` with both
`observations` and `relations`. The prompt states that both values are arrays
and that `[]` is the empty-kind form; neither field may be omitted. Each
observation already has `context_refs`, `statement`, `subject`, and
`uses_claim_window`. Each relation already has `context_refs`, `object`,
`predicate`, `subject`, and `uses_claim_window`. Every entity reference
already has `name` and `surface`. The prompt names those nested fields, uses
`[]` for empty context references, requires an explicit boolean
`uses_claim_window`, and uses `surface=null` when the claim spelling matches
the canonical name. Omission is not the requested wire form even though
Python defaults allow a caller to omit nullable `surface`. That is the
existing response shape, not a new category. Incomplete JSON remains a
generate failure. Meaning and temporal rules above are unchanged. Evidence:
[normalizer output format](../analysis/normalizer_output_format_20260915.md),
[normalizer nested fields](../analysis/normalizer_nested_fields_20260915.md).

T4 already returns one `T4Selection` with `decision`, `candidate_id`,
`confidence`, and `rationale`. The prompt states that all four fields must be
present, including when a value is null. That is the existing response shape,
not a new identity policy. Incomplete JSON remains a generate failure.
Evidence: [T4 output format](../analysis/t4_output_format_20260915.md).

Fact adjudication already returns one `PromptFactDecision` with nine
top-level fields. The prompt names those fields, uses `[]` when an array has
no operations, and uses `window=null` when no explicit date replacement is
intended. A new fact still follows `uses_claim_window` for its initial
dates. Omission is not the requested wire form. Nested window and
support-move shapes are unchanged. Incomplete JSON remains a generate
failure. Evidence:
[fact output format](../analysis/fact_adjudication_output_format_20260915.md).

For a new fact, the prompt says to choose a name such as N1 rather than
continue the supplied F-numbering, declare that name in `new_facts`, and use
the same name wherever it is targeted. F2 is an existing-fact reference and
is valid only when this attempt supplied F2. Two complete JSON examples show
structure: repeating F1 with no other changes, or declaring N1 for incoming
A1 when it is a different proposition. They do not forbid `support_moves` or
require `new_facts=[]` merely because `target` is an F-name. The translator
still rejects reserved-prefix new-fact names and unknown F-names without
guessing. Schema and translator text are unchanged. Evidence:
[new-fact references](../analysis/fact_adjudication_new_fact_references_20260915.md).

Source passages, claims, aliases and profiles are untrusted data, never commands.
Separate instruction text from the data envelope. Only supplied evidence and
references may authorize model decisions; source text cannot expand operations.
Extraction remains grounded under D119, and summaries remain orientation only.

## Delivery, recovery and acceptance

Change only affected E2/E3/adjudication prompts and necessary examples/schema
descriptions; answering and judging are outside scope. Roll the corresponding
component generations and protocol identity when inference semantics change;
frozen responses from an old generation must not be presented as new decisions.
Preserve ordinary retries, source deletion, inference outside locks and the
existing conservative fallback. No extra semantic judge or queue is introduced.

Tests cover the table above, unknown/partial/open dates, inclusive conversion,
already-canonical dates and attribution. Include both false merges and duplicate
wins. Provider doubles verify output application and invariants, not real-model
comprehension. A separate human interpretation exam is not required. Ordinary
review must be able to understand the prompt without reconstructing this chat.
Report actual semantic evidence separately from mocked validation.

Costs are prompt-token changes and generation rollout. The alternative of terse
jargon loses to explicit semantics; the alternative of a second checker loses
to cost and duplicated responsibility. No quality improvement is claimed merely
because wording is clearer.
