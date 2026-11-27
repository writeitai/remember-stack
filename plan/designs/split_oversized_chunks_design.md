# Split oversized chunks before embedding (D162)

**Status** Proposed. [Analysis](../analysis/split_oversized_chunks_analysis.md).

## 1. Check the complete embedding input

Pin the provider binding's model, dimensions, tokenizer code and vocabulary,
route and fallback settings, and request framing. Qualify a per-input token
ceiling that delivers the full input without truncation on every reachable
route. Qualify item, token and serialized byte limits for requests as well.
One nonblank Unicode scalar must fit with the largest D80 header and framing.
Start hosted qualification at **16,384 tokens** for Qwen3-Embedding-8B on
OpenRouter `nebius/bf16`. The benchmark binding requires **at least 22,528**.
[Embedding policy](e1_embedding_input_policy.md#4-embedding-input-policy).

Before activating D162 for ingestion or replay, stop legacy handlers for
CHUNK, EMBED_CHUNK, Selection (`extract_claims`) and Claimify
(`ground_claims`) from claiming on either lane, by polling or named
delivery. Drain their in-flight handlers, including reclaimed attempts,
then serve those routes with D162 handlers.

Ordinary CHUNK pins the active qualification on its work row before packing
and passes it to EMBED_CHUNK in the handoff. Both stages honor that pin on
retries and reclaims, including after configuration changes. Unpinned embed
work already queued at cutover pins the active qualification at its first
D162 attempt and checks the persisted grid under it. A worker must be able
to honor the pin before writing a grid or calling the provider.

Pin admission and P1 model publication hold the same deployment lock
through commit. Under that lock, admit a pin only when its model and
dimensions match the active P1 chunk semantic channel. After locking,
setup checks stored vectors and live pins in one snapshot and refuses an
incompatible switch before publishing. A pin stays live while either
stage can still run under it, including through handoff, backoff, budget
parking and reclaim. An outstanding recovery keeps its pin live even if
replacement CHUNK dead-letters. Re-admit the qualification when reopening
terminal work whose pin is no longer live, including at initial recovery in §3.
[P1 model changes](postgres_p1_search_projection_design.md#10-rebuild-and-incompatible-embedding-changes).

CHUNK uses the existing section-bounded, anchored packer. Check each
candidate's full D80 input, including normalized body, header, separators
and provider framing. Fit also requires that the input fit alone in a request.
If every candidate fits, the rows are byte-identical to the legacy output
with the same inputs and allocations. Fitting spans keep their exact bytes
and use the legacy row path. When a split changes the final count, order or
extraction window, compute the dependent fields from the final sequence.
EMBED_CHUNK checks the exact inputs again before reuse or dispatch. Batch
the inputs still needing vectors in document order using the legacy item
count. Subdivide oversized batches into consecutive groups that fit
the qualified request limits. The benchmark binding preserves the legacy
batches and request framing, so each frozen legacy request must fit as sent.

## 2. Split only failing candidates

Each piece stays in its candidate's section. Cut where the eligibility of
nonblank text changes. Use E0 block eligibility within blocks and the D133
label policy in gaps, defaulting to eligible for unlabelled gap text.
Put all separator whitespace at a cut on the preceding piece.
Leading whitespace stays with the first piece. Preserve separator bytes
and provenance, including labels that cover only whitespace.

Within a failing range, choose the legal seam nearest the character
midpoint in the first available tier. Prefer block boundaries, then
recorded structural seams such as turns, pages and table rows, then
sentence, line and whitespace seams in that order. Break ties at the
earlier offset. Fall back to a Unicode scalar boundary. Pin the seam
rules, keep CRLF together and require nonblank text on both sides.
The pieces concatenate to the original candidate bytes.

Once a split is required, check the pieces under D80's multi-chunk rules.
Even if the new header would let the original singleton fit, make the cut.
Render and recursively split failing pieces, then verify each final input
with the final chunk count. Every cut shortens the remaining range.

Assign consecutive document ordinals in source order. Store each piece's
half-open code-point offsets into immutable E0 Markdown and the ordinals of
the first and last intersected blocks, inclusive. Use the preceding block
ordinal for both fields if the slice lies entirely between blocks. Pieces
can share a block ordinal. The character spans determine coverage, including
the [entry chunk for a keyed section](effective_time_and_section_references_design.md#62-reading-references).
Clip the source map and converter-origin ranges to the exact slice. D119 passage
labels use these ranges. Label a nonblank gap-only window in Selection
and Claimify even when a neighbouring window has clipped blocks.

Use the existing chunk ID allocation with each piece's own span and
ordinal in the row. The current allocator is UUIDv4.
[Legacy row builder](../../src/rememberstack/workers/e1.py#L694-L731).
Build a piece's content key from its covered block fragments in source
order, including any nonblank gap text, using the existing hash functions.
Intact chunks keep their legacy key inputs wherever their extraction
windows are unchanged. Use the final neighbour text and clipped provenance
for changed windows. D140 text origins use their normal lookup.

Mark each piece as `split_piece` on its chunk row. A split target starts
fresh for embedding, Selection and Claimify unless that stage finds a
matching completed split donor under its existing reuse checks. Filter
donors before choosing a match, even when a whole chunk has identical keys.
This preserves D56 reuse when a later edit leaves the same split inputs.
Intact targets use the existing donor path. Retries on the same chunk ID
use its committed vector, frozen Selection and Claimify completion.
Ineligible pieces and Selection results with no keeps follow the usual
empty extraction path. Keep stable Selection hashes for the ordered D122
window. Verify the donor's admitted inputs on every Claimify reuse and
recheck grounding in the current occurrence for claims from split donors.
[D122 reuse](document_reference_context_design.md#reuse-including-negative-dependencies).

An input past the provider's real limit could never embed in full. D162 writes
one chunk grid per version under the existing chunker version and work
keys. A version that has reached pipeline READY keeps its grid and IDs
permanently. Every newly ingested version uses the fixed packer.

## 3. Recover an unpublished version

Successful `embed_chunk` is the prerequisite for `extract_claims` and
the claims and facts that follow it. After a size dead letter on the normal
path, readiness shows `embed_chunk` dead-lettered and expected downstream
stages missing with blocker diagnostics enabled.
[Claims handoff](../../src/rememberstack/spine/work_ledger.py#L266-L329) ·
[Readiness layers](../../src/rememberstack/spine/readiness.py#L113-L134) ·
[Readiness blockers](../../src/rememberstack/spine/readiness.py#L42-L87).

CHUNK inserts and EMBED_CHUNK vector and stamp writes, including skips,
lock their `processing_state` row in the data transaction. Require the
same running `(processing_id, attempt)` and hold the lock through commit.
Write each vector batch and its chunk stamps together. Recovery locks the
same work rows. A write from an older attempt either commits before cleanup
or finds that its attempt no longer owns the row.

Replay requires exclusive processing for the lineage. Before cleanup,
pause lineage intake, block other versions' work and replays from starting,
and drain their in-flight attempts. Keep that exclusion through retries
and restarts until replacement CHUNK hands off a durable grid to
EMBED_CHUNK or cancellation commits. Refuse replay if the exclusion
cannot be guaranteed.
D140 can inherit a text origin from an unpublished, embed-failed version.
Another CHUNK that reads while the old grid is absent can permanently give
unchanged text a later date. [D140 origin rule](effective_time_and_section_references_design.md#5-reuse-across-versions-text-origin-time).

Operator and automatic replay target the `embed_chunk` dead letter by
processing ID and observed attempt. The generic dead-letter replay entry
point routes `embedding.input_too_large` here. In one PostgreSQL transaction,
take the model-admission lock (§1), lock the version and relevant work rows,
and perform these checks and writes.

1. Verify the size error and failed attempt for a live version with no prior
   embed success, publication, started downstream work or downstream claims,
   occurrences, observations, relations or facts attributable to the version.
   Require evidence of privacy from ingestion through the recovery
   handoff or cancellation, such as an import isolated from readers and
   publication.
   E0's `ready` status can expose stored chunk IDs before embedding.
   Refuse on evidence of publication or uncertainty.
   [E0 status](../../src/rememberstack/spine/document_catalog.py#L827-L852) ·
   [SQL visibility](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L701-L719).
2. Admit the active qualification and require its fit check to fail for the
   rejected nonblank input on the stored grid. After rejection of a
   replacement grid, require a revised qualification that changes that
   input's fit decision from pass to fail.
   A provider rejection that still passes local fit stays terminal.
3. Delete partial `chunk_search` rows by the version's chunk IDs. Delete
   the chunk grid and any downstream ledger rows still pending with zero
   attempts. Reschedule the succeeded CHUNK row under its existing key.
   Grant the normal CHUNK attempt allowance above the consumed count, even
   if CHUNK succeeded on its last attempt. Record the outstanding recovery,
   failed embed attempt and selected qualification in the CHUNK payload.
   Announce CHUNK after commit.
   [Work key](../../src/rememberstack/spine/work_ledger.py#L1996-L2023).

CHUNK packs from E0 under the selected qualification. Retries reuse a
replacement grid already committed by that row. On success, reopen the
matched embed dead letter under the same qualification and release the
lineage hold in the handoff transaction. Preserve work IDs, attempt counts
and spend. Grant EMBED_CHUNK its normal bounded allowance of fresh
attempts. Repeated requests for the same failed embed attempt schedule one
recovery.

If replacement CHUNK dead-letters, retain the hold and pin. An operator
can replay the same CHUNK row through the dead-letter entry point by
processing ID and observed attempt. Reopen it once per failed attempt
under the same pin, with its normal bounded allowance above the consumed
count. Keep the matched embed dead letter in place and announce CHUNK
after commit.

An operator can cancel the dead-lettered replacement by that same
ID and attempt. Serialize cancellation with replay and handoff. In one
transaction, apply the existing version deletion lifecycle to tombstone
the failed version and repoint the lineage if it was current. Fence
further work claims and replays for that version, retain its ledger and
spend, and release the hold and pin on commit.
[Version deletion](effective_time_and_section_references_design.md#9-archive-and-forgetting).

## 4. Rejection and retry

Every nonblank D80 input requires a usable vector. Check that each input
has its own vector with the pinned dimension, finite stored components
and nonzero norm.
Only `empty_body` (blank after normalization) qualifies for a skip.
Missing or unusable vectors, genuine throttling, ambiguous 429s and
transient outages use bounded retries and dead-letter on exhaustion.

A confirmed deterministic per-input rejection is a terminal typed error.
Store `embedding.input_too_large` for size and `embedding.input_rejected`
for another confirmed input rejection. Establish the cause from a local
fit check or a provider-specific error that identifies the input failure.
Isolate the rejected input from a batch when needed. For a size rejection,
record the rejected chunk ID, D80 input hash and fit qualification in the
dead-letter diagnostic.
[Embed durability](orchestration_design.md#embed_chunk-durability-d80--minimum-contract).

## 5. Acceptance

Offline, use the exact frozen LoCoMo Full-v45 and BEAM inputs, saved E0
artifacts, settings and pre-dispatch reuse state. Replay recorded UUIDs
and clock values only at allocation. At the provider boundary, compare
the complete ordered request sequence with legacy, including ordered
input hashes and serialized framing, model, dimensions and routing.
Return each saved vector response only for its matching request. Fail if
a request differs or if the qualified limits would force a change to
any legacy request, even when its inputs fit individually.

Compare `chunks` and `chunk_search` column for column in document order,
including nulls. Legacy rows have `split_piece` null.
For recorded completions, recompute D122 stamps and compare the ordered
prior-eight producer IDs and typed E2 location elements. Require zero splits
and zero model calls. Missing fixtures or digest mismatches fail the check.
Record the baseline, digests and restore and check commands with implementation.

Test structural and scalar cuts, labelled gaps, exact reconstruction and
provenance and full-input fit when a shorter body gets a larger header.
Exercise a nonblank gap-only Selection target beside a block-backed neighbour.
Split a candidate with two eligible blocks separated by an ineligible
`\n\n` interval. Require eligible, nonblank pieces and exact reconstruction
with the separator's provenance. In section references and history, check
the first-block entry ID for a split section and its unsplit control.
Verify terminal size rejection, bounded failure for an empty provider vector,
and a successful `empty_body` skip. At activation, verify all four legacy
handlers are drained and both claim paths serve D162 handlers only.

Pin model A before any vectors land, then verify setup refuses model B
before and through the final vector commit. If B publishes first, A cannot
pin. Change a compatible qualification after CHUNK pins it and verify
CHUNK and EMBED_CHUNK keep that pin through handoff, retries and reclaims.
Use the generic replay entry point with a partial vector batch and CHUNK
already succeeded on its last attempt. Cover repeat and stale requests, rollback,
and refusal for publication or downstream evidence. Resume reclaimed CHUNK
and EMBED_CHUNK attempts after recovery and require zero writes. A second
provider rejection under the same fit qualification must preserve partial
vectors and attempt limits. Verify recovery after a corrected fit decision.

Verify replay leaves the grid and ledger intact without enforceable lineage
exclusion. Pause replay after cleanup and attempt to process a dated successor,
including across replacement CHUNK retries and worker restarts. Exhaust
replacement CHUNK with no vectors stored. While both rows are dead-lettered,
the successor still waits and setup refuses an incompatible model switch.
Replay that CHUNK dead letter and verify one bounded allowance under the
same pin, then earlier origin inheritance after handoff. Exercise explicit
cancellation from the dead letter and verify the tombstone, current-version
repoint, release of the hold and pin, and refusal of stale work or replay.

With two dated versions, verify that untouched regions keep their claim IDs
and make zero embedding, Selection and Claimify calls when inputs match.
Seed an equal-key whole-chunk donor for a split piece. Verify fresh
embedding and extraction, zero calls on retry, and carry-forward if a later
version leaves the split inputs unchanged.
For equal hashes, verify a fresh Selection card blocks dependent reuse if it
changes the admitted inputs. A changed ninth-ranked card that stays outside
the cap must allow reuse without a Claimify call. Test a legacy V2 that
copied V1's completion under different admitted inputs. A V3 that matches
only V2's inputs must refuse the copy, with claims or a zero-claim transcript.
Equivalent admitted inputs after remapping and committed empty Selection
results must permit reuse from a verified donor.
With equal keys, frozen decisions and remappable spans, rename a section
from `Orchard` to `Terrace`. Require fresh Claimify when a split donor's
only support for an addition is the old typed title. Keep the claim ID
and make zero Claimify calls when current evidence still supports it.
Update the E1 acceptance checks with implementation.
[E1 check](../implementation_evals/eval_checks/e1_chunks_whole_blocks_no_overlap.yaml).
