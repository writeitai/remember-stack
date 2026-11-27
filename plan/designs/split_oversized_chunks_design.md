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

Before activating D162 for ordinary ingestion or replay, stop legacy CHUNK
and EMBED_CHUNK workers from claiming work. Wait for their in-flight
handlers to exit, including reclaimed attempts. Resume those routes with
workers that apply the write checks in §3.

Ordinary CHUNK pins the active qualification on its work row before packing
and passes it to EMBED_CHUNK in the handoff. Both stages honor that pin on
retries and reclaims, including after configuration changes. Unpinned embed
work already queued at cutover pins the active qualification at its first
D162 attempt and checks the persisted grid under it. A worker must be able
to honor the pin before writing a grid or calling the provider.

CHUNK uses the existing section-bounded, anchored packer. Check each
candidate's full D80 input, including normalized body, header, separators
and provider framing. Fit also requires that the input fit alone in a request.
If every candidate fits, the rows are byte-identical to the legacy output
with the same inputs and allocations. Fitting spans keep their exact bytes
and use the legacy row path. When a split changes the final count, order or
extraction window, compute the dependent fields from the final sequence.
EMBED_CHUNK checks the exact inputs again before reuse or dispatch and
batches within the qualified request limits.

## 2. Split only failing candidates

Each piece stays in its candidate's section. Respect eligibility changes
in labelled ranges, including gaps. Within a failing range, choose the
legal seam nearest the character midpoint in the first available tier.
Prefer block boundaries, then recorded structural seams such as turns,
pages and table rows, then sentence, line and whitespace seams in that
order. Break ties at the earlier offset. Fall back to a Unicode scalar
boundary. Pin the seam rules, keep CRLF together and require nonblank
text on both sides. Attach separator whitespace to a neighbouring piece.
The pieces concatenate to the original candidate bytes.

Once a split is required, check the pieces under D80's multi-chunk rules.
Even if the new header would let the original singleton fit, make the cut.
Render and recursively split failing pieces, then verify each final input
with the final chunk count. Every cut shortens the remaining range.

Assign consecutive document ordinals in source order. Store each piece's
half-open code-point offsets into immutable E0 Markdown and the ordinals of
the first and last intersected blocks, inclusive. Use the preceding block
ordinal for both fields if the slice lies entirely between blocks. Pieces
can share a block ordinal. The character spans determine coverage. Clip the
source map and converter-origin ranges to the exact slice. D119 passage
labels use these ranges, including a label for a nonblank gap-only window.

Use the existing chunk ID allocation with each piece's own span and
ordinal in the row. The current allocator is UUIDv4.
[Legacy row builder](../../src/rememberstack/workers/e1.py#L694-L731).
Build a piece's content key from its covered block fragments in source
order, including any nonblank gap text. Use the existing block hash and
content-key functions. Intact chunks retain their block hashes and legacy
key inputs wherever their extraction windows are unchanged. Use the final
consumed neighbour text and clipped provenance where a window changes. Use
D140 text origins and D119 evidence remapping. For Claimify reuse, compare the
current inputs against those that produced the donor's completion, even when
a split piece ran Selection fresh. [D122 reuse](document_reference_context_design.md#reuse-including-negative-dependencies).

An input past the provider's real limit could never embed. D162 writes
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
and drain their in-flight attempts. Hold that exclusion across handoff,
retries and restarts through the replacement CHUNK grid commit. Refuse
replay if the exclusion cannot be guaranteed.
D140 can inherit a text origin from an unpublished, embed-failed version.
Another CHUNK that reads while the old grid is absent can permanently give
unchanged text a later date. [D140 origin rule](effective_time_and_section_references_design.md#5-reuse-across-versions-text-origin-time).

Operator and automatic replay target the `embed_chunk` dead letter by
processing ID and observed attempt. The generic dead-letter replay entry
point routes `embedding.input_too_large` here. In one PostgreSQL transaction,
lock the version and relevant work rows and perform these checks and writes.

1. Verify the size error and failed attempt for a live version with no prior
   embed success, publication, started downstream work or downstream claims,
   occurrences, observations, relations or facts attributable to the version.
   Require evidence of privacy from ingestion through commit, such as an
   import isolated from readers and publication.
   E0's `ready` status can expose stored chunk IDs before embedding.
   Refuse on evidence of publication or uncertainty.
   [E0 status](../../src/rememberstack/spine/document_catalog.py#L827-L852) ·
   [SQL visibility](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L701-L719).
2. Require the active qualified fit check to fail for the rejected nonblank
   input on the stored grid. For a rebuilt grid, require a revised
   qualification that changes that input's fit decision from pass to fail.
   A provider rejection that still passes local fit stays terminal.
3. Delete partial `chunk_search` rows by the version's chunk IDs. Delete
   the chunk grid and any downstream ledger rows still pending with zero
   attempts. Reschedule the succeeded CHUNK row under its existing key.
   Grant the normal CHUNK attempt allowance above the consumed count, even
   if CHUNK succeeded on its last attempt. Store the failed embed attempt
   and selected qualification in the CHUNK payload. Announce CHUNK after
   commit. [Work key](../../src/rememberstack/spine/work_ledger.py#L1996-L2023).

CHUNK packs from E0 under the selected qualification. Its successful
attempt reopens the matched embed dead letter under the same qualification
in the handoff transaction. Preserve work IDs, attempt counts and spend.
Grant EMBED_CHUNK its normal bounded allowance of fresh attempts.
That handoff makes EMBED_CHUNK claimable. Repeated requests for the same
failed attempt schedule one replay. Subsequent work follows the ordinary
ingestion path.

## 4. Rejection and retry

A confirmed deterministic per-input rejection is a terminal typed error.
Store `embedding.input_too_large` for size and `embedding.input_rejected`
for another confirmed input rejection. Establish the cause from a local
fit check or a provider-specific error that identifies the input failure.
Isolate the rejected input from a batch when needed. For a size rejection,
record the rejected chunk ID, D80 input hash and fit qualification in the
dead-letter diagnostic. A rejected nonblank input fails EMBED_CHUNK.
Genuine throttling, ambiguous 429s and transient outages use bounded retries.
[Embed durability](orchestration_design.md#embed_chunk-durability-d80--minimum-contract).

## 5. Acceptance

Offline, pack the exact frozen LoCoMo Full-v45 and BEAM inputs at their
qualified benchmark ceiling. Use saved E0 artifacts, configuration and
reuse history. Replay recorded UUIDs and clock values only at allocation
and stored vectors only after checking the exact rendered inputs against
the saved hashes. Compare both `chunks` and `chunk_search` column for
column, in document order, including nulls. Recompute D122 stamps for
recorded completions and compare the ordered prior-eight producer IDs and
typed E2 location elements. Require zero splits and zero model calls.
Missing fixtures or digest mismatches fail the check. Record the baseline, digests and restore and
check commands with implementation.

Test structural and scalar cuts, labelled gaps, exact reconstruction and
provenance, full-input fit when a shorter body gets a larger header, and
terminal size rejection versus bounded throttling. At activation, verify
legacy workers cannot claim new work. Change the active qualification after
ordinary CHUNK pins it and verify CHUNK and EMBED_CHUNK keep the same pin
through handoff, retries and reclaims. Exercise replay with a
partial vector batch and CHUNK already succeeded on its last attempt. Use
the generic replay entry point. Cover repeat and stale requests, rollback,
and refusal for publication or downstream evidence. Resume reclaimed CHUNK
and EMBED_CHUNK attempts after recovery and require zero writes. A second
provider rejection under the same fit qualification must preserve partial
vectors and attempt limits. Verify recovery after a corrected fit decision.

Verify replay leaves the grid and ledger intact without enforceable lineage
exclusion. Pause replay after cleanup and attempt to process a dated successor,
including across replacement CHUNK retries and worker restarts. It must wait
until the replacement grid commits, then inherit the earlier origin for a
matching unchanged chunk.

With two dated versions, verify that untouched regions keep their claim IDs
and make zero embedding, Selection and Claimify calls when inputs match.
For equal hashes, verify a fresh Selection card blocks dependent reuse if it
changes the admitted inputs. A changed ninth-ranked card that stays outside
the cap must allow reuse without a Claimify call. Test a legacy V2 that
copied V1's completion under different admitted inputs. A V3 that matches
only V2's inputs must refuse the copy, with claims or a zero-claim transcript.
Equivalent admitted inputs after remapping and committed empty Selection
results must permit reuse from a verified donor.
Update the E1 acceptance checks with implementation.
[E1 check](../implementation_evals/eval_checks/e1_chunks_whole_blocks_no_overlap.yaml).
