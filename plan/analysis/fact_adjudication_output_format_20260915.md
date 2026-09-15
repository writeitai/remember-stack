# Fact-adjudication prompt: name the nine existing output fields

This analysis records a prompt/schema contradiction in the ordinary fact
adjudicator. It is non-binding. It does not add a numbered architecture
decision, a new category, a window, a timeout, a token cap, an automatic
retry, a schema change, or a provider fallback. The existing
`PromptFactDecision` contract already has nine top-level fields.

This is **not** a reproduced Gemma/Vertex fact-adjudication failure. It is
not the measured T4 incomplete object in
[t4_output_format_20260915.md](t4_output_format_20260915.md). Parent audited
the remaining processing prompts at engine `1294e2b1` and selected this
correction because the prompt tells the model to omit a field that the
strict wire schema requires.

## The question

The fact prompt already describes identity, evidence, handles, dates, and
support moves. The Python response type and the strict JSON schema already
require nine top-level fields, including `window`. `window=null` means keep
the existing chosen dates. The prompt currently says “Omit/null window to
preserve dates,” which treats omission and null as equivalent. The strict
schema does not: `window` is required and may be null. Should the prompt
name all nine existing fields and say `window=null` for unchanged dates?

## What the evidence is, and what it is not

An offline call to the actual strict-schema builder for `PromptFactDecision`
returned these required fields: `target`, `stance`, `new_facts`, `window`,
`updates`, `support_moves`, `contradict_with`, `confidence`, and `rationale`.
`window` permits null and is still required. Pydantic defaults are a
convenience for Python callers; they do not mean the requested wire JSON may
omit the field. Parent audit:
[processing-output-format-audit-20260915.md](https://github.com/writeitai/ultimate-memory-cloud/blob/bdd7d464/design/analysis/processing-output-format-audit-20260915.md).

No captured live fact-adjudication stream is claimed here. The T4 and
normalizer stalls showed that a constrained decoder can emit some fields and
then pad whitespace without closing the object. That is context for why a
contradictory omit instruction is worth removing. It is not proof that this
prompt has already hung.

Other processing prompts (summaries, structure, selection, Claimify, labels,
profiles) stay unchanged in this delivery. The audit recorded possible naming
improvements there without a contradictory omission instruction or a captured
failure.

## The existing contract

`PromptFactDecision` already has:

| Field | Existing meaning |
| --- | --- |
| `target` | F-name or declared new-fact name that receives the incoming assertion |
| `stance` | `supports` or `contradicts` |
| `new_facts` | declared new facts; empty when none |
| `window` | grounded replacement for the target, or null to keep existing dates |
| `updates` | grounded replacements for other named facts; empty when none |
| `support_moves` | reassignment of older A-names; empty when none |
| `contradict_with` | incompatible distinct facts; empty when none |
| `confidence` | number from 0 to 1 |
| `rationale` | short non-empty explanation |

Nested shapes (`PromptNewFact`, `PromptGroundedWindow`, `PromptWindowUpdate`,
`PromptSupportMove`) are unchanged. Date arithmetic, attribution, evidence
attachment, and support-move rules are unchanged. Empty arrays remain valid.
`window=null` remains the preserve-dates form. Incomplete JSON remains a
generate failure.

## Alternatives considered

- **Leave the prompt unchanged.** The wire schema requires `window`. The
  prompt still says omit-or-null. That contradiction stays in the next
  processing stage after T4.
- **Remove Pydantic defaults or change the schema.** The diagnostic path for
  T4 succeeded without a schema change. This correction is the same kind of
  wording fix. Schema architecture is out of scope.
- **Inject the full JSON schema into every prompt, or add a generic
  renderer/provider hook.** That would duplicate nested schema text and
  change every processing request. It has not been measured. These two
  concrete field-name instructions do not need that machinery.
- **Timeout, token cap, automatic retry, or another provider.** Not this
  change. Parent owns runtime operations.

The chosen path is a short output-format section that names the nine existing
fields, uses `[]` when an array has no operations, and replaces
“Omit/null window” with `window=null` for unchanged dates. Plain field names
only; no JSON braces in the `.format` template.

## What follows if accepted

Both relation and observation application generations append an output-fields
marker so frozen answers from the old prompt are not presented as new
decisions. Derived observation-flush pins follow. This rolls in the same
Full-v34 protocol identity as the measured T4 clarification. Dataset, models,
budgets, retrieval, answer, judge, and scoring stay the same. Stores ingested
under v33 are not this protocol. No quality or cost improvement is claimed
from the wording.
