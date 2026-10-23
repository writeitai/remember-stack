# Preserve source origins when evidence reaches extraction

Status: implementation analysis, 2026-10-23. This is general engine work, not
a LoCoMo answer heuristic. It depends on the current-profile cleanup in PR #514.
No retained benchmark data is reprocessed or repaired by this change.

## Problem and inspected contracts

Converters already label OCR, direct source text, visual observations and model
interpretations through D65 derivation ranges. E2 resolves those labels when
persisting claim occurrences, but its Selection/Claimify prompts receive plain
passages without those origins. Provenance arriving only after the model has
chosen a statement cannot help it avoid treating quoted fiction as the sender's
biography or overlooking a short authored note next to a long attachment.

The observed LoCoMo letter failure has eleven extracted OCR/script claims and
no claim for nearby authored dialogue. It motivates the investigation; it is
not an acceptance fixture or evidence that any proposed change already fixes
the score. The old campaign's Markdown also flattened attachment origins, so
the engine cannot reconstruct trustworthy converter provenance from that text.

Inspected: `workers/e2.py` Selection and Claimify rendering, occurrence-context
loading and token grounding; `core/source_passages.py`; frozen D122 cards and
their remapping; E1 Selection/text-origin identities; the text/image converters;
D119 media provenance, D65 conversion labels, D56/D140 reuse and existing tests.

## Chosen change

Pass the existing converter-origin labels into both extraction prompts, including
admitted D122 reference-card passages. Keep source text verbatim and labels
separate as interpretation metadata. A missing label stays unknown. Do not infer
OCR provenance from a heading or a benchmark marker, and do not make labels
additional source words accepted by token grounding.

When a target contains both mediated attachment content and direct source text,
repeat the direct source slices in a short focus block before the ordinary
catalog. This protects their visibility without hiding the attachment or adding
another paid model pass. Pure OCR documents keep all their useful names, dates
and assertions eligible. The prompt distinguishes a claim about a document from
a claim about the author and preserves speaker/quotation ownership and genuine
document dates. It must not treat a session/upload timestamp as an event date
inside fictional or quoted content merely because they share a chunk.

These are input and faithfulness improvements, not a deterministic guarantee
that every useful proposition will be retained. Existing Selection outcomes,+Claimify omission accounting and source-reviewed evaluation remain necessary.
No new inference model, reranker, entity whitelist, gold lookup, or question-ID
branch is introduced.

## Alternatives and costs

Suppressing OCR would lose useful contracts, receipts, scanned letters and
document dates. A LoCoMo-only rewrite of attachment text would miss the engine
contract. Separate Selection/Claimify calls for each origin would add paid work,
new partial-call checkpointing and merge/grounding failure paths; this change
keeps the existing one-Selection/one-Claimify workflow. Repeating short direct
source slices adds input tokens only for genuinely mixed targets; token/cost
effects must be measured, not presented as an established saving.

## Identity, failures and recovery

Origins now affect model interpretation, so their deterministic labels must join
both the date-free text-origin identity and the Selection input identity for the
actual target and consumed same-section neighbors. Hash canonical relative
intervals/kinds/modes, never absolute offsets, timestamps, request labels, section
summaries, model-produced card names or source-map pixel coordinates. Equivalent
adjacent label ranges normalize to the same identity. Editing another section
must not change an untouched chunk's provenance basis.

The E2 extractor generation changes once to declare the new prompt/input policy.
This is a paid extraction boundary when data is processed under that generation;
no automatic processing is launched. Packing, embedding policy/model and stored
source bytes do not change. Embedding reuse stays independent. D122's existing
previous-eight-Selection dependency remains; its known cross-section invalidation
gap is neither hidden nor claimed fixed here.

Missing/corrupt declared provenance fails before a model call. Unlabelled source
is rendered honestly as unknown rather than fabricated as direct testimony.
Occurrence provenance continues to come from the actual accepted source spans.
Failed grounding cannot be rescued by prompt labels. Reuse must check the same
origins the model consumed before reattaching prior claims/cards.

## Validation

Use unrelated mixed documents: authored notes surrounding quoted fiction, a
scanned invoice with real names/dates, visual observations versus interpretations,
unknown provenance and overlapping/mixed ranges. Assert rendered origins and
verbatim text, card provenance, unchanged grounding, and no added model call.
Use two dated versions of one lineage to prove unchanged sections retain their
Selection/text-origin identities, claim IDs and embeddings; a changed origin
must invalidate the affected Selection/context only. Count provider calls and
retain expected failures for the documented D122/packing gaps. No LoCoMo score
is established until a separately authorized evaluation is run.
