# Clear processing instructions that preserve assertions

**Status:** D120, accepted by the user on 2026-09-14; binding when merged.
**Analysis:** [lean processing evidence](../analysis/lean_processing_contracts.md).
This amends the processing-prompt portions of D118 and E2/E3 contracts. It does
not change the fact schema, adjudication writer operations or retrieval semantics.

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
| Corrects the same win's date from 5 November to 6 November | Won on 5 November | Reconsider that fact's window with cited evidence |
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
