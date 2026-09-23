# Stable source references shared during document extraction

**Status:** D122, accepted 2026-09-14; binding when merged.
**Analysis:** [lean processing](../analysis/lean_processing_contracts.md).
This explicitly amends D119's extraction-context boundary, D56 reuse inputs and
D84 extraction scheduling. It preserves D119 multi-span grounding and D102's
existing validated document-local entity-resolution replay.

## Problem and decision

A document can introduce a tournament in chunk 2 and discuss “that tournament”
in chunk 7. Independent extraction cannot see beyond its local neighbors, and
a shared list populated in worker-completion order would make results unstable.
Use the two existing extraction calls, Selection and Claimify, with a stable
boundary between them. Selection identifies useful propositions and source-backed
referents. Save its complete output once. After all required Selection results
are present for a representation, Claimify runs in parallel using a bounded set
of those references. There is no third extraction call or serial chunk pipeline.

A reference identifies something the source discusses: a person, company, work
or particular event. It is not yet a globally resolved entity. No mandatory type
field, event registry, date authority or fuzzy alias shortcut is introduced.

## Source reference cards

Extend Selection's existing closed output with a bounded list of referents
introduced by selected or contextual source text it is permitted to see. Each
contains a short descriptive name and provided passage references supporting it.
Local aliases may be included only when the shown source establishes them.
The same grounding checks as D119 map these references to exact source ranges.
Generated summaries and section orientation cannot justify a reference.

Only definitions with at least one target-chunk body passage are published by
that Selection unit; its allowed neighbor evidence may clarify them. This avoids
every neighbor publishing duplicate introductions. Do not merge cards by equal
name/date/participants, and do not treat the same supporting passage as identity:
one paragraph can introduce both a person and a tournament. Preserve distinct
emitted cards and cap them as whole cards. A particular unnamed tournament is
eligible when source context identifies it; a bare unqualified “tournament” is
not an identity proof.
Ambiguous references may remain unresolved.

The card shown to Claimify includes its descriptive label and exact supporting
passages. Labels are orientation, not evidence. Temporary card and passage IDs
are scoped to that request and never become entity IDs or claim text. Claimify
writes a self-contained human-readable claim, preserving the original attribution
and qualifiers, and cites every source passage required to establish its referent.

## Bounded context policy

Each target may receive cards whose owning Selection units lie within the previous
eight source chunks of the same representation, in addition to existing local
context. The region can cross section boundaries; this is an explicit D119
amendment. Never cross document versions, representations or documents.

Initial versioned bounds are four published cards per Selection result, eight
cards per Claimify request and 4,096 total characters of card text including
support passages. They are explicit operating bounds to measure, not an optimality
claim. Rank eligible cards by exact source/name-token overlap with target source
text, then nearest owning source chunk, then stable card ordinal. No extra model
or vector search. Zero overlap may still admit a nearby card needed for a pronoun.
Limit by whole cards; do not trim away evidence and retain its unsupported label.
Record cap/ambiguity losses through existing extraction diagnostics. No distant
introduction outside the region is promised to be available.

The claim still belongs to a selected target proposition. A card may clarify
that proposition; it cannot resurrect a dropped proposition from another chunk
or smuggle an entire event profile into evidence. D119's total claim-span bound
still applies. If all needed support will not fit, do not drop a citation and
accept the claim; account for the failure. Multi-span provenance resolves each
range separately, never one bounding range across unrelated text.

## Persistence, scheduling and retries

Use one source-owned Selection-result store and the existing work ledger/barrier
pattern. Store the full validated response, including zero-proposition/zero-card
results, its generation and stable source-input basis. Normalize temporary labels
to validated source descriptors before persistence; preserve exact text and
range/origin information needed for occurrence remapping.

Matching prior-version inputs reuse the frozen Selection output; retries never
regenerate a committed response. Uncommitted concurrent calls may race, but only
one accepted result becomes authoritative through existing transaction/CAS
patterns. Do not add a second queue or distributed cache. Associate results with
each consuming chunk occurrence so position changes do not corrupt evidence.

The Selection barrier uses the expected chunks of the immutable representation
and pinned generations. Create Claimify work idempotently and transactionally
once all results exist. Reused results count as complete. A terminal missing or
invalid Selection result cannot be silently treated as zero references. Existing
failure/retry policy remains visible. Claim normalization begins only after the
existing extraction-completion barrier, now covering Claimify.
Successful Selection with no kept propositions may still publish grounded cards
for other chunks. Its own Claimify completes deterministically without a model
call. Empty representations follow the existing empty completion path.

## Reuse, including negative dependencies

Selection's stable reuse basis is its existing own/neighbor source context,
header and generation. Claimify's effective basis adds the ordered stable
Selection-input bases of every owning unit in its eligible preceding region,
including units that emitted no cards, and the reference-policy generation.
Those Selection bases include any neighbor source used to define their cards.

Hashing only cited/admitted cards is incorrect: an edited chunk may introduce a
competing referent even though the old cited card is unchanged. Hashing the entire
document is unnecessarily broad. A fixed source region captures both positive
and negative dependencies while bounding the spread of invalidation.

Do not include absolute offsets, document-version IDs, temporary labels or freshly
generated summaries in content identity. Identical stable inputs must reuse the
same frozen Selection decision, not call the model to obtain new cards. For each
new occurrence remap all selected evidence into its allowed source units, including
reference-card passages. Reject ambiguous repeated-text remapping; never take the
first global substring match. Existing D56 same-claim-ID reuse and D119 occurrence
evidence stay mandatory. Header timestamp changes retain their documented broader
invalidation; shifting chunk boundaries can also change actual input context.

## Relationship to E3 and entity resolution

> **Amended by D134.** Every Selection request also receives one **self card** for the
> document being processed, outside the card caps above. A claim citing the self card
> carries a structured document-self marker and binds to the document entity without
> the resolution cascade — the only card for which choosing it bypasses resolution.
> Authority: [`document_subject_entity_design.md`](document_subject_entity_design.md).

The grounded, self-contained claim carries the referent into normalization. E3
may emit the ordinary subject/object and D123's context references from that
claim. It resolves them through the current resolver, including D102's validated
same-document replay. Choosing a source-local card does not bypass resolution.
There is no new durable card-to-global-entity alias registry in this design.
Source-local card handles, global entity UUIDs and D121 prompt handles have
different scopes and cannot substitute for one another.

## Deletion, rollout and acceptance

Selection outputs/cards are source-owned content. Include them, their occurrence
links and pending work in hard-forget deletion and verification. Deletion and
publication must use existing forget coordination so a late Selection/Claimify
answer cannot resurrect erased text. Multi-version reuse does not exempt source
copies from erasure. No additional long-lived profile or quote cache.

Coordinate schema/component generations with D119. Use the project's clean-store
upgrade refusal where populated stores cannot satisfy new generation contracts;
do not add a conversion framework or reset any existing store. Regenerate all
affected manifests/protocol pins and update shipped processing documentation.

Required tests: worker-order permutations; a chunk-2 introduction used in chunk 7;
two similar/same-date tournaments; source-local aliases; no-reference results;
cross-section evidence; source reference forgery; changed/competing introductions;
an empty reference producer becoming nonempty; edits outside the eligible region;
pure offset movement; ambiguous repeated passages; 500-version claim reuse and
provider-call counts; barrier restart; forget during Selection/Claimify; and
unchanged attribution. Provider-free tests establish these invariants, not model
coreference quality. Measure added prompt tokens and source-to-ready latency.

## Costs and rejected alternatives

One durable intermediate and one barrier are necessary to share the existing
Selection output deterministically. Cards add prompt tokens and the barrier can
delay a fast chunk behind a slow Selection unit. Keep these costs visible.
An E3-only roster cannot help Claimify resolve the distant introduction. E0
summary calls do not consistently see original source and would broaden unrelated
contracts. A mutable discovered-so-far list makes outcomes timing-dependent.
A whole-document list increases ambiguity, prompt size and reuse invalidation.
The bounded Selection-to-Claimify design is chosen over those alternatives.
