# Source-backed entity context for fact nomination

**Status:** D123, accepted 2026-09-14; binding when merged.
**Analysis:** [lean processing](../analysis/lean_processing_contracts.md).
**Dependencies:** D120 assertion preservation, D121 concise inputs and D122
source reference context. D118's fact application and temporal authority remain.

## Problem and chosen representation

Knowing which tournament a claim discusses can help find an earlier winning fact.
It cannot establish that winning and enjoying that tournament are the same fact.
Use ordinary resolved entities as nomination context, without an event taxonomy
or event ID column on every fact. The mechanism also supports a company, person
or creative work when it is a source-grounded referent of an assertion.

Extend the existing normalization response for each assertion with a bounded
list of ordinary `EntityRef` context references. They must be explicit referents
in the grounded claim, not entities guessed from unrelated profiles. The existing
normalizer call emits them. There is no additional dedicated event extractor,
profile summarizer, or semantic-checker stage. Ordinary entity resolution remains
authoritative and may invoke its existing model tier for a newly supplied context
reference; that resolver cost is measured, not bypassed. Four additional
references is the initial generation-pinned bound. Bound the first four original
array slots, resolve them normally, then deduplicate and exclude the canonical
subject or object while keeping those original ordinals. Equal names are not
identity; aliases of one entity keep the first resolved id. Record truncation
from the frozen list; auxiliary overflow must not cause schema rejection of the
whole normalization response, discard an otherwise valid assertion or prove
novelty. Document the cap clearly in the provider schema.

The subject remains the correct subject. “Joanna said Nate won Tournament A”
remains attributed to Joanna; Nate and Tournament A can be context. Relations'
object endpoints automatically contribute context and need not be duplicated.
No mandatory event type, bare-noun exception or name/date identity shortcut.

## Frozen outputs and resolved bindings

Freeze references with the existing normalization output. Resolve them through
the ordinary contextual resolver/D102 replay and persist source-owned resolved
bindings on the assertion application. Use one small application-to-entity
junction with stable reference ordinals and validating resolver decisions. Do not
introduce an independent mutable document alias registry.

Stage an application only when its recorded context result is complete under
the pinned generation. Provider/retry failures follow the normal ledger policy;
do not silently publish a different subset depending on worker timing. A valid
empty list is allowed. The application and source claim supply ownership;
canonical redirects are read under existing identity coordination.
Publish the application, complete binding set and version staging rows atomically.
Published bindings are immutable and retries reuse the first complete set. No
application is admission-eligible with half-written bindings. Enforce same-
deployment ownership for application, entity and validating resolver decision.

A fact's context set is derived from applied applications with supports stance
whose claims are current testimony, not
copied onto the fact. Support moves therefore change the association through
the existing support pointer. Contradiction-only applications do not establish
positive context membership. Source deletion removes its bindings and contribution.
Merge/unmerge and invalidation must not leave a stale canonical cache; re-read
bindings and canonical membership under the existing snapshot protocol.
Canonicalize and deduplicate context entities, excluding the fact/incoming
application's own canonical subject, including for self-relations. Withdrawal
removes positive context contribution; eligible history retains baseline reach.

## Candidate selection and novelty

Retain the current same-canonical-subject, same-fact-plane editable domain.
Compute the existing baseline nomination unchanged: up to 20 facts, exact
triple/statement first, then full-text rank, including zero-ranked fallback and
completed world intervals. Independently nominate up to eight additional facts
in that domain whose supporting applications share an incoming context entity.
Exclude all baseline IDs before applying the additional-eight limit.
Union and deduplicate; the maximum is 28. Rank shared-context candidates first
for presentation, with exact match, full-text rank and stable fact ID as ties.

These initial bounds are generation-pinned and must be measured. Expansion is
deliberately conservative: it cannot displace a target the baseline would have
provided. It is a quality/coverage change, not a claimed token saving. D121 pays
for removing redundant prompt material. Reducing the fallback requires separate
measured candidate-recall evidence and a design amendment, not an undocumented
optimization by the implementer.

An empty context search is not a new-fact authorization. The deterministic
novelty path is available only when the ordinary same-subject/same-plane domain
contains no eligible candidate. Dates and entity overlap are never hard identity
filters. Mistaken event splits and unlinked observations retain baseline reach.
No cross-plane targets, cross-subject writes or new event-based work ordering.

## Model context and date authority

Present context entities with their source-grounded names and the supplied claim
evidence. Use D121 handles/text factoring to avoid repeating event descriptions.
Do not pull an unrelated mutable profile or graph neighborhood outside the
prepared snapshot and use it as date evidence. This design adds no cross-subject
profile retrieval or opposite-plane editable/context facts.

Events remain ordinary entities. If source testimony says a tournament ran
3–5 November, a supported observation that it took place can carry that existing
world window. A winning assertion can instead carry the final's date. Do not
fabricate a took-place observation solely to populate a profile and do not
automatically propagate event duration to every connected assertion.

Profiles remain deterministic derived fact descriptions. Existing repair refreshes
them when their supporting facts change. They hold no independent chosen date.
Dates do not enter stable entity IDs. D120 examples explicitly separate shared
event identity from repetition/correction of a particular assertion.

## Snapshot safety, deletion and recovery

Include context binding values, canonical identities and relevant membership in
the full validation snapshot/fingerprint. Nomination is rerun after acquiring
ordered locks and before applying an answer. New supporting applications, support
moves, identity changes, withdrawal and forget can change the context set; stale
decisions retry under D118. Preserve existing global/identity/entity/claim/fact/
application lock order and never hold these locks during provider inference.
The fingerprint includes binding values, resolved canonical membership, support
stance and the claim currency used for selection, not only selected fact IDs.

Include every reference description actually shown in source-consumption and
forget inventories. New junction rows cascade or are explicitly deleted by
source-owned applications, with verification queries and pending input scrubbing.
No background fact-context repair service or permanent event evidence cache.

## Acceptance and operational costs

Test relation/observation parity; unchanged attributed subjects; same event with
different assertions; repeated wins; corrected dates; same-date distinct events;
mistaken event splits; zero-rank paraphrases; baseline target preservation; empty
context with nonempty baseline; support moves; contradiction-only support; merge/
unmerge; deletion during inference; late responses and context membership changes.
Measure nomination recall and rendered total tokens separately, with observation
coverage explicit. Do not claim a semantic model improvement from mocked decisions.

Use indexed application/context joins and bounded row payloads. Membership hashes
may stream relevant rows as D118 already does. Context resolution and expanded
candidates can add cost; measure them in the implementation report. Roll affected
normalizer/adjudicator generations and protocol artifacts coherently with D119–122.
Do not change provider defaults, evaluator settings or existing benchmark stores.

Rejected alternatives are per-fact event columns requiring repair, moving facts
to event subjects, event-only nomination, unsupported smaller fallback, event
taxonomies and a model-generated profile call. All add authority or lose evidence
that the chosen generic source-backed application context can preserve.
