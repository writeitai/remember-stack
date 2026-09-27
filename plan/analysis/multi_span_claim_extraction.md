# Coherent claims with distributed evidence and version reuse

**Status:** analysis, 2026-09-14; supports D119. The user accepted coherent claim
extraction, multi-span evidence, bounded context and preservation of D56 reuse.
The acceptance does not assert a benchmark gain or authorize changing live stores.

## Evidence and problem

Fresh LoCoMo conv42 processing on D118/main cost $12.397460, versus $4.721789 on
v27. Adjudication accounted for 97.7% of the increase, so extraction changes alone
cannot be credited with fixing that entire regression. Extraction itself cost
$2.247369 and produced 1.70M billed output tokens; actual ingestion was Luna high,
contrary to the requested Vertex/Gemma processing configuration.

Read-only source/claim/fact inspection found 38 claims from one screenplay
passage, including disconnected “the story is about loss/identity/connection”
claims and a separate identification of the third work. Twenty-seven claims
repeated renderer participant-header text. Distinct tournament-win claims also
attached to weaker fact statements; that is a separate adjudication issue, not
something multi-span storage alone fixes. The user explicitly excluded answering
and retrieval optimization from this work.

Full dated evidence, arithmetic and independent analysis are recorded in UMC:
[processing audit](https://github.com/writeitai/ultimate-memory-cloud/blob/affc6c3c/design/analysis/locomo-v28-processed-data-audit-20260914.md),
[cost analysis](https://github.com/writeitai/ultimate-memory-cloud/blob/affc6c3c/design/analysis/locomo-ingestion-cost-quality-20260914.md),
[multi-span and reuse exploration](https://github.com/writeitai/ultimate-memory-cloud/blob/affc6c3c/design/analysis/multi-span-claim-extraction.md).
These are non-binding evidence; D119 below supplies the accepted engine contract.

## Current code inspected

- `workers/e2.py::_bundle_text`: target plus immediate same-section neighbors,
  deterministic header/location facts and bounded section summaries.
- `workers/e2.py` grounding: one primary substring inside the target; bounded
  added-context token membership; generated summaries are excluded as evidence.
- `model/claims.py::ClaimRecord`: one scalar anchor; `claim_catalog.py` and
  `model/occurrence_provenance.py`: reattachment of the same claim to another
  version through occurrence rows, resolving representation-specific locators.
- `workers/e1.py::_chunk_record`: reuse hashes own/neighbor content, deterministic
  header facts and generations. Character positions and model summaries are not
  keys. Header timestamps can already cause broad invalidation.

## Alternatives and rationale

One claim per source sentence preserves simple anchors but loses cross-sentence
referents and encourages costly fragmentation. One document-wide bounding span
hides which pieces actually support a rewrite. Removing provenance eliminates
inspection and safe reuse. Keeping many separate claims and combining them into
facts is valid for independent propositions, but unnecessary splitting also pays
for repeated normalization and obscures local relationships.

A short span list preserves exact grounding without a graph of evidence fragments.
The model chooses deterministic request-local references; the engine computes
positions. Source text remains immutable, claims can be coherent rewrites and
facts remain mutable interpretations. Same-version support avoids turning an
immutable source claim into a cross-document synthesis.

An independent analysis favored preserving the current safe application/reuse
contracts while reducing model-facing bookkeeping. It warned that batching changes
ordered effects and should not be smuggled into a provenance change. The accepted
scope follows that boundary. Granularity and context are separately measurable:
multi-span output does not itself grant access to farther passages.

## Risks and evaluation

Exact locations do not prove entailment. Several true passages can be combined
incorrectly across a topic switch. Review source-based fixtures with positive
coherence and negative same-word/different-referent cases. Large coherent claims
must not conflate independently dated/attributed propositions. Shared-event
assertions such as participation/enjoyment/winning remain distinguishable.

The feature must preserve D56 reuse from the outset. Disabling it would allow
500 small versions to create repeated claims and negate the economic motivation.
Test 500 versions without paid calls: unique claims, occurrences, extractions and
fact evidence counts separately. Modifications to supporting context must
invalidate reuse; position shifts must not. Header timestamp invalidation is an
existing conservative behavior to expose in tests/report, not silently remove.

Keep the two extraction calls, existing model ports, selected context bounds and
fact architecture. Compare new claim quality and downstream assertion volume
against retained LoCoMo evidence. A fresh paid benchmark is a separate execution
choice; implementation acceptance requires meaningful local/DB tests, not an
unsupported score or dollar promise.
