# Lean processing: evidence behind D120–D123

**Status:** analysis, not authority. 2026-09-14. Scope is ingestion/processing;
retrieval and answering are excluded. The user authorized accepted designs and
Grok implementation for clear assertion-preserving prompts, concise adjudication
inputs, document references and event-based nomination. Grouped application is
an unchosen proposal. Multi-span extraction is the separate accepted D119 lane.

## Observations, hypotheses and constraints

The v28 conversation-42 run cost about $12.40 in processing, including $9.16 in
fact adjudication and about $0.04 in entity resolution. Adjudication inputs
averaged 26,561 tokens. These runs mistakenly used Luna high for processing;
future paid processing trials must verify Vertex/Gemma at each generation stage.
Changing provider rates alone does not meet the requested cost reduction.
No new paid trial is authorized by this implementation handoff.

The audit found a winning assertion assigned to enjoyment, and another retained
only as participation. Same tournament does not mean same assertion. Some
specific tournament entities already exist; the entity mechanism itself is not
absent. Most stored facts are observations without structured event references.
Event nomination therefore needs a path from source referents through normalized
assertions, not merely a better entity-name prompt.

Detailed source inventories and measured evidence, inspected 2026-09-14:

- [Processing audit](https://github.com/writeitai/ultimate-memory-cloud/blob/a56f95bf/design/analysis/locomo-v28-processed-data-audit-20260914.md).
- [Cost evidence](https://github.com/writeitai/ultimate-memory-cloud/blob/a56f95bf/design/analysis/locomo-ingestion-cost-quality-20260914.md).
- [Independent exploration](https://github.com/writeitai/ultimate-memory-cloud/blob/a56f95bf/design/analysis/event-context-and-fact-adjudication.md).

At engine baseline b2d32a5a, `spine/fact_adjudication.py` directly serializes the
prepared snapshot. Its phrase “same-event corrections normally attach to that
identity” is ambiguous about event versus fact identity. `fact_application_inputs.py`
nominates exact matches then full-text candidates, same subject and fact plane,
including zero-ranked fill. The source and support snapshots contain duplicate
text and administrative metadata. These observations justify semantic task
clarity and deterministic projection independently of event improvements.

## Alternatives and economics

Raising confidence thresholds would not address the observed 0.98-confidence
incorrect assignment. Another semantic checker/model call adds cost without
fixing an unclear primary task. A clear prompt with contrasting fixtures is the
selected correction; reference and operation validation remain deterministic,
but do not prove semantic equivalence.

Deleting evidence to shorten prompts risks hiding corrections and contradictions.
Lossless presentation of semantic content is selected before changing nomination.
Keep full validation inputs internally; remove bookkeeping only from the model
view. Short handles need a response adapter tied to the exact attempt. This is
more precise than telling a UUID response schema to return F1 informally.

Event-only nomination would make older unlinked observations and split event
identities unreachable. Event context is an additional priority signal with
broader nomination retained. No extra fact category, date authority or event
table is needed. Generic source-backed context references can also describe
companies, people and creative works without a classifier or regex taxonomy.

Grouping pending assertions promises repeated-context savings but changes retry
and receipt contracts. It remains in `design/proposals/`; individual prompt
projection does not depend on accepting grouping. Holding locks during provider
inference, permanent prompt caches and a separate event scheduler are rejected.

## Validation interpretation

Provider-free fixtures prove grounding, reference mapping, retries, snapshot
invalidation, source deletion and invariants. They do not prove that a real model
chooses the right fact. Model-semantic claims must identify the actual model,
input corpus and results; no invented measured saving. Benchmark candidate
coverage separately from end-to-end fact assignments. Count input/output/schema
tokens, retries, total calls and repair work, not just the shortest prompt.

The user does not require a separate human prompt-exam ceremony. Human-readable
instructions, documented contrasting examples and ordinary code/design review
are the accepted clarity standard.

## Independent analyses and resolution

Two independent read-only analyses inspected actual E2/E3 scheduling and candidate
contracts. The document-reference analysis found no existing pre-extraction
source-backed entity inventory: E0 summaries are not a safe substitute. The
selected D122 contract freezes existing Selection output, adds one barrier and
bounds preceding producer units. Its review required an explicit no-Claimify-call
path for zero kept propositions and empty-representation completion; incorporated.
E3-only reuse was rejected because it cannot inform chunk-7 extraction.

The event-nomination analysis recommended generic source-owned application bindings
rather than fact event columns, and preserving the baseline 20 plus up to eight
additional context candidates. This is deliberately coverage-first, not evidence
of lower token cost. Its draft review clarified positive membership/current
testimony, canonical-subject exclusion, auxiliary overflow without assertion loss,
excluding baseline IDs before the extra limit, atomic staging and full binding/
currency fingerprints. These were incorporated in D123. D121's review avoided a
new registry by putting the renderer version in the existing generation/fingerprint.

These are design reviews, not implementation or real-model acceptance. All
semantic/cost hypotheses still require the evidence distinguished above.
