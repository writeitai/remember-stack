# D119 — Coherent claims, exact multi-span evidence, reusable versions

**Status:** accepted by the user, 2026-09-14; implementation must land through a
reviewed PR. Acceptance is not a claim that runtime support has shipped.
**Analysis:** [problem, evidence and alternatives](../analysis/multi_span_claim_extraction.md).
**Decision:** [D119](../../decisions.md#d119-coherent-claims-with-multi-span-evidence-and-version-reuse).

## 1. Meaning and scope

A claim is one coherent source-supported proposition. It need not occupy one
sentence or be split at each conjunction. Retain the identifiable subject,
referent, attribution and necessary qualifiers. For a passage that establishes
Joanna's third screenplay and its themes across several sentences, one claim
may say “Joanna's third screenplay explores loss, identity and connection.”
A passage about two independently dated events still yields distinct assertions.
Multiple exact evidence spans support the claim; they do not license combining
unrelated events or converting generated summaries into source testimony.

Claims remain immutable; facts remain mutable, with D118's one world-time window.
D31's separate Selection and Claimify calls remain. No extraction/normalization
fusion, adjudication batching, new fact categories, second date window, per-claim
checker model or query-time processing is introduced by this decision.

The context consists of the target chunk and the existing immediately previous
and next same-section chunks, source-derived header/location metadata and bounded
target/ancestor summaries for orientation. Summaries are never evidence. This
contract does not expand the context to whole documents or cross-section search.

## 2. Source passage references and ownership

The engine builds deterministic, request-local labels for exact source slices in
the permitted context, using existing blocks/passages and clipping intersections
where needed. Labels are assigned before inference; they are not inserted into
stored document bytes and never participate in content-reuse keys. The engine
retains the map to representation-specific character ranges. It verifies the
mapping against the immutable representation.

The model returns a standalone claim and a nonempty ordered list of supplied
source references, alongside existing attribution and temporal output. It does
not invent character offsets. One designated origin reference belongs to the
target chunk and overlaps a proposition accepted by Selection. The other
references can point to allowed neighboring body text. First-reference origin
is sufficient; do not introduce a graph of support roles.

The origin anchors work ownership and omission accounting. It does not imply
that all support lies within that one span. Context references cannot mark an
unrelated selected target proposition as handled or resurrect dropped content.
Retain D33 decisions for omissions and invalid outputs. Trusted renderer metadata
may assist interpretation but should not itself be repeatedly extracted as
new testimony; identify it by source/metadata boundaries, not a prose blacklist.

A source passage may contain several sentences. Exact means correct source
positions, not the smallest possible quote. Whole bounded passages are valid
references. Do not introduce a second model or heuristic sentence parser solely
to calculate offsets. Duplicate/unprovided references are rejected or normalized
unambiguously before persistence; no fabricated citation is accepted.

## 3. Grounding and coherent extraction

Every citation must resolve to supplied eligible source text in the same document
version/representation, be nonempty and in bounds, and match the actual source
slice. Deduplicate repeated ranges and order non-origin ranges canonically. Keep
separate disjoint intervals; do not replace them by a document-wide bounding box.

The reference list and cited-text size are bounded as part of the extraction
input/output contract. Eight cited passages, including the origin, is the initial
bounded choice; this threshold has not been established as optimal by measurement.
A cap or invalid reference must not
silently remove evidence while accepting the combined claim: the model may emit
independently meaningful claims, otherwise existing loss/rejection accounting
records the failure. Generation identity includes changed limits/semantics.

Source-backed metadata and relative-date resolution retain D41/D80's existing
provenance rules. Exact body citations do not replace the deterministic timestamp
anchor for “yesterday.” Generated section summaries cannot supply missing names,
numbers or evidence. `added_context` may remain a decontextualization diagnostic,
but cannot be a parallel authoritative body-citation representation.

Anchor checks establish provenance, not semantic entailment. Keep the source
faithfulness task and sampled/source-reviewed evaluation; do not treat the
presence of all words as proof of the combined assertion. Topic switches,
negation, speaker changes and independently dated propositions are discriminating
quality tests. Coherence must not turn “participated,” “enjoyed” and “won” into
interchangeable assertions about the same event.

## 4. Occurrence storage and atomic persistence

Store a short list of exact character ranges on the existing claim-occurrence
carrier (`chunk_claims`), with document version and representation pinned through
its owning chunk. A validated JSON array of `{char_start, char_end}` is the
selected simple representation. It is the authoritative list of supporting body
locations for that occurrence, including the origin. No separate fragment graph,
citation vector store or duplicate permanent quote store is required.

All ranges in one occurrence belong to that same immutable representation. A
claim can be reused across versions, but positions are always resolved for each
occurrence. Cross-document synthesis stays in the fact layer. If scalar claim
anchor fields remain for the immutable original extraction, document them as its
origin only; they must never masquerade as the complete evidence for a reused or
multi-span occurrence. Do not maintain independently writable competing accounts
of full evidence. API/model consumers that expose complete provenance must obtain
all spans from the selected occurrence; necessary contract/schema/docs updates
belong with implementation, not a reader optimization project.

Claims, their occurrence span lists, extraction decisions and derived provenance
land through the existing atomic catalog transaction. A failed list validation
cannot leave a claim appearing successfully grounded without its evidence.
Existing logical-FK/partition disciplines still apply; validation also enforces
tenant/deployment and representation ownership.

## 5. D56 version reuse remains mandatory

The existing extraction-input fingerprint retains its stable content inputs:
own chunk, consumed neighboring context, source-derived header facts and
extractor/structurer generations. It excludes absolute positions, document
version IDs, request-local labels and generated summary text. Existing
carried-forward structure/summary replay rules remain binding.

On an eligible earlier match within the same document lineage, reuse **the same
claim IDs**, not re-generated text, and attach a new occurrence with every span
resolved against the new representation. Remapping must account for repeated
identical passages using unambiguous source/block alignment. Do not use an
unqualified first substring match or copy old offsets.

Checking only the origin or selected quotes is insufficient: changed allowed
context can change a referent even if the cited text still occurs. Fingerprints
must cover the actual source context allowed by extraction. If support cannot be
resolved safely, run the ordinary accounted extraction path. Do not disable reuse
for multi-span claims as a general fallback or ship a full-document re-extraction
policy to avoid implementing occurrence remapping.

Reuse remains chunk-grained; an edited neighbor can invalidate otherwise unchanged
claims. Version occurrences and legitimate historical claim changes may grow;
unchanged extraction inputs must not multiply unique claims or provider calls.
Fact support remains counted by current document lineage, not by version count.

Header timestamp changes currently invalidate fingerprints and can affect relative
world-time. Preserve that semantic boundary; do not delete timestamp inputs just
to increase a reuse percentage. Tests and the implementation report must disclose
this existing broad-invalidation case. A finer timestamp policy would require its
own evidence/contract rather than being hidden inside this change.

## 6. Media provenance, lifecycle and recovery

Resolve every source span through the representation's derivation ranges and
source map. Preserve the union of actual locators at converter precision and
honest evidence mediation labels. Do not invent word-level audio positions or
label mixed model-description/direct-text evidence as wholly direct testimony.
Unrelated intervals between the spans must not affect provenance classification.

Reused occurrences recompute these labels/locators for their target representation.
Missing/corrupt declared provenance follows existing retry/dead-letter behavior;
it cannot be silently relabeled direct source content.

Normal version withdrawal preserves D55 currency semantics. Hard forget removes
all source occurrences, span metadata and any source-bearing prepared/decision/
transcript/projection copies according to D74. No in-flight extraction or stale
reuse result may republish forgotten evidence. Include secondary spans in reuse,
projection and forget tests. Same-version support keeps the dependency source-owned
without introducing cross-document claim authority.

A crash before the existing atomic commit leaves no successful partial extraction;
a retry after committed output reuses the stored result. Per-version span remapping
is deterministic and repeatable. No new background repair queue or autonomous
compensation framework is introduced.

## 7. Existing stores, generations and acceptance

Use the existing prelaunch clean-store policy rather than inventing inferred
multi-span evidence for old claims: a schema upgrade requiring new occurrence
support refuses populated claim stores that cannot satisfy the contract. Operators
must explicitly recreate/re-ingest those stores; the migration never destroys
one automatically. Empty stores and test fixtures upgrade normally. No live
benchmark or customer store may be wiped by implementation work.

Roll extractor/component and affected protocol/surface generations together.
Keep committed schema/manifest/client/documentation artifacts synchronized where
public provenance changes. Preserve existing answer/judge pins and unrelated
provider settings; this is a processing feature, not an evaluation-model change.

Required evidence includes source-grounded single/multi-span cases, ambiguous
references/topic switches, rejected fabricated references, origin/Selection
accounting, independently dated assertions, changed secondary support, media
mapping across disjoint spans, forget during work, and replay after commit.

A 500-version small-edit test must measure unique claim IDs, occurrence rows,
extraction calls and fact evidence counts separately. Unchanged inputs and pure
position shifts reuse; relevant context/secondary-span edits invalidate; timestamp
changes exercise the disclosed policy. Use deterministic model doubles for reuse
checks, including a provider that fails if incorrectly called. No paid benchmark
is required to prove cache behavior.

Cost and quality gains require separate processing evidence on source-labeled
LoCoMo fixtures. Neither fewer claims nor passing database tests alone establishes
semantic improvement. No dollar target or score gain is claimed by this decision.

## 8. Authority and supersession

This decision amends D31/D32's maximal decomposition and single complete-evidence
anchor assumptions, D56 occurrence remapping and D65 source-map aggregation for
multi-span occurrences. It retains D31's two model calls, D33 loss accounting,
D41 temporal semantics, D55 testimony currency, D56 content-based reuse, D74 forget,
D80 source-only grounding and D118 mutable facts. Broader context, cross-document
claim synthesis, extraction-stage fusion and adjudication batching are outside
this contract. Implementation-facing schema details must follow these semantics.
