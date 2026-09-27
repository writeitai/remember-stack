# Fact-adjudication new-fact names: F2 collision and unknown F2

This analysis records the R7 fact-application translate failures, a later
typed control on one captured input, and the lean prompt clarification that
completed three parent diagnostics. It is non-binding. It does not add a
numbered architecture decision, a new category, a date window, a timeout, a
token cap, an automatic retry, a schema change, or a provider fallback. The
existing `PromptFactDecision` contract, handle mapping, and writer operations
stay the same.

## The question

The ordinary fact adjudicator already asks the model to choose a supplied
F-name or to declare a new-fact name such as `win` or `N1`. New-fact names
must not reuse reserved `F`/`C`/`A`/`E`/`S`/`T`/`W` handles. The translator
rejects reserved-prefix new-fact names and unknown F-names without guessing.

R7 conv-42 Gemma/Vertex processing passed earlier normalizer blockage, then
dead-lettered entity drain units after three attempts. Saved errors are
`new-fact handle F2 collides with a supplied name` and `unknown fact handle
F2`. Provider calls for those attempts succeeded with known usage. Should
the prompt add a short N1 naming rule and two complete JSON examples that
distinguish attaching to a supplied fact from declaring `N1`, without
weakening validation or changing the schema?

## What the evidence is, and what it is not

Saved original-run tracebacks: `/tmp/conv42-gemma-full-run/r7-errors.jsonl`.
Those traces name the rejected handle and translator path. They are **not**
the raw or typed completion from the live failed call.

- Collision path: `translate_prompt_decision` at the `new_facts` reserved-prefix
  check. `PromptFactDecision` had already parsed. `new_facts[].handle` was
  `F2`. `_typed_kind("F2")` is `F` because the name matches
  `^([FCAESTW])([1-9]\d*)$`. The translator then raises. That check is the
  reserved pattern, not membership in this attempt’s fact table.
- Unknown path: `translate_prompt_decision` at `response.target` through
  `_fact_reference`. Declared reserved names have already been rejected before
  this lookup. The unknown-F2 traceback therefore implies that F2 was neither
  supplied in the mapping nor declared as a new fact. It does not establish
  the rest of that answer.

Three pending applications captured locally
(`/tmp/conv42-gemma-full-run/r7-fact-reference-inputs-0345.json`; private
source-bearing, not committed) each had **one** stored fact (presented as F1)
and an incoming assertion with a **different** statement. Inventing F2 as
“the next fact” continues F1/C1/A1 numbering. One of those prompts is 7,357
bytes, SHA-256
`5df55e593f65dc497e7d6a9ee5b2169564831a63c00b0c6b36eb00c70b21985f`, and
matches a completed provider receipt at 03:39:51 UTC.

A **separate** baseline control, not the original failed live call, used that
exact captured input with no application write (03:45:58 UTC; 3.783 s; 1,801
in / 138 out; USD 0.00035295). The typed output was `target=F2`,
`new_facts=[{handle: F2, assertion: A1}]`, `stance=supports`, `window=null`,
empty arrays otherwise, `confidence=1`. The rationale treated hobbies as a
distinct proposition from “loving watching classics”, then named the new fact
F2. The unchanged translator reproduced `new-fact handle F2 collides with a
supplied name`. Meaning was sensible; the identifier convention failed.
Durable safe receipt:
[fact-reference control](https://github.com/writeitai/ultimate-memory-cloud/blob/586ec3d1/design/analysis/locomo-conv42-gemma-fact-reference-control-20260915.json).

The existing prompt already said new facts need names such as `win` or `N1`,
not reserved F/C/A/E/S/T/W names. That sentence was not sufficient for this
answer. There was no complete JSON example of a new fact whose `handle`,
`target`, and `assertion` match.

This is **not** the measured T4 incomplete object, the normalizer both-array
omission, or the nested `surface` stall. It is **not** a translator bug:
reinterpreting `F2` as a new fact would accept invalid F references. It is
**not** evidence to change the schema, renderer numbering, normalizer,
resolver, caps, timeouts, or validation.

## The existing contract

Attempt-local names are rebuilt from the frozen snapshot: F1 is the first
supplied fact, C1 the first claim, A1 the first assertion. A new fact is a
name the model invents for this answer, declared in `new_facts`, used as
`target` (or a support-move target), with `assertion` pointing at a supplied
A-name whose content the new fact records. `N1` is such a name. `F2` is an
existing-fact reference and is valid only when this attempt supplied F2.

`translate_prompt_decision` already:

- rejects `new_facts` handles whose spelling is a reserved typed name
- looks up F-names only in `mapping.facts`
- translates declared non-reserved names into `FactReference(new_handle=…)`
  references, requiring a declaration in `new_facts`

Unknown F99, wrong-kind C1-as-target, and W-names stay rejected. Support
moves, window replacements, confidence, and stance are unchanged. A decision
may still attach the incoming assertion to F1 **and** declare N1 for another
assertion through `support_moves`. Empty `new_facts` is not required merely
because `target` is an F-name.

## Alternatives considered

- **Leave the prompt unchanged.** The control on the captured input still
  named F2 after the existing “win or N1” sentence.
- **Treat F2 as a new-fact name when it is missing from the mapping.** That
  silently reinterprets an invalid F reference. Rejected.
- **Change field descriptions or translator error text.** Parent diagnostics
  isolated the prompt. Schema bytes stay identical.
- **Disable handle validation, lower caps, add retries, or change the
  schema.** Out of scope. Parent owns runtime.
- **Inject the full JSON schema, or add a generic example framework.** Not
  measured, and not needed to show two complete objects.
- **Change renderer numbering or the normalizer/resolver.** No evidence
  those caused the F2 translate errors.

The chosen path is the parent-tested prompt template (SHA-256
`5f7e0a10003bbc0261fec53f55e735665643d6c6ec14beb0a0038e3add70df91`): after
“No guessed IDs or names.”, say to choose N1 rather than continue F-numbering,
declare that name in `new_facts`, and use the same name as `target`; F2 is an
existing-fact reference. After the nine-field OUTPUT FORMAT sentences, two
complete JSON examples show structure only: incoming assertion repeats F1 with
no other changes (`new_facts=[]`, `target=F1`), or incoming A1 is a different
proposition (`new_facts` handle N1, assertion A1, `target=N1`). Other
operations remain allowed. Examples do not claim that attaching to F1 is
usually correct.

## Parent diagnostic of that template

Three sequential Gemma/Vertex completions on the exact template, schema and
translator unchanged:

| Case | Bytes | Elapsed | Tokens in/out | USD | Target |
| --- | ---: | ---: | --- | ---: | --- |
| Joanna new proposition | 8,452 | 8.633 s | 2,083 / 99 | 0.00037185 | N1 |
| Nate new proposition | 8,440 | 2.591 s | 2,082 / 98 | 0.0003711 | N1 |
| Synthetic win repeat | 7,710 | 2.557 s | 1,922 / 93 | 0.0003441 | F1, no new facts |

Three cases are not corpus reliability. Durable receipts:
[fact-reference variants](https://github.com/writeitai/ultimate-memory-cloud/blob/9faadcea/design/analysis/locomo-conv42-gemma-fact-reference-variants-20260915.json).

## What follows if accepted

Relation and observation application generations append `new-fact-refs-1` so
frozen answers from the old prompt are not presented as new decisions.
Derived observation-flush pins follow. Normalizer, resolver, schema, and
translator stay the same. This rolls LoCoMo Full-v36. Dataset, models,
budgets, retrieval, answer, judge, and scoring stay the same. Stores ingested
under v35 are not this protocol. No corpus-level quality or cost improvement is claimed
from these three diagnostics.
