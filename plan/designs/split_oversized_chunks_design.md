# Split oversized chunks before embedding (D162)

**Status** Proposed. [Analysis](../analysis/split_oversized_chunks_analysis.md).

## 1. Check the complete embedding input

CHUNK and EMBED_CHUNK use the same qualified provider binding through
handoff and retry, matching the active P1 model and dimensions. Pin the
tokenizer code and vocabulary, model, route and fallbacks, and request framing.
Qualify the largest permitted input for full delivery without truncation on
every reachable route, counting the complete D80 render (normalized body,
header and separators) plus provider framing. Qualify aggregate item, token
and byte limits too. The binding must fit one nonblank Unicode scalar with
the largest D80 header and framing. Hosted Qwen3-Embedding-8B on OpenRouter
`nebius/bf16` starts at **16,384 tokens** for qualification. The benchmark
binding requires **at least 22,528 tokens**.
Keep that binding active while admitted work can still run. Use
[D80's maintenance cutover](e1_embedding_input_policy.md#45-migration-triggers-and-vector-reuse)
for incompatible binding changes.

With D162 enabled, CHUNK checks legacy whole-block candidates for input and
singleton-request fit. With legacy-only reuse state, an all-fit grid gives
byte-identical `chunks` and `chunk_search` rows for equal inputs and
allocations. Fitting candidates keep their spans and legacy row builder.
Derive count, order and extraction windows from the final grid. A change
from one to multiple chunks can change D80 headers, so check again after
that transition. EMBED_CHUNK verifies fit before reuse or dispatch. Keep
the legacy ordered batches of inputs that need vectors, subdividing only
to meet request limits.

## 2. Split only failing candidates

Within a failing candidate, cut at changes in nonblank text's extraction
eligibility. Use E0 block eligibility within blocks and D133 labels in gaps
(unlabelled gap text is eligible). Attach separator whitespace to the
preceding piece, and keep leading whitespace with the first.
For size cuts, prefer block seams, then recorded structural seams such as
turn, page or table-row boundaries, then sentence, line and whitespace
seams. Use the legal seam nearest the midpoint in the first available tier,
breaking ties at the earlier offset. Fall back to Unicode scalar boundaries.
Keep CRLF together and put nonblank text on both sides. Repeat for pieces
that fail the full rendered fit check. Concatenating the pieces reproduces
the candidate's exact bytes.

Each piece uses the legacy ID allocator (UUIDv4) and its own consecutive
document ordinal and half-open character span in the same section and E0
Markdown. Store the first and last intersected block ordinals, inclusive.
For a gap-only piece use the preceding block ordinal in both fields.
Store `token_count` in legacy whitespace tokens.
Clip source-map and converter-origin ranges to the piece. Block ordinals
can repeat across pieces. Section references and history take the entry
chunk whose character span contains the first block's start.
[Legacy row builder](../../src/rememberstack/workers/e1.py#L694-L731).

Keep the existing hash builders and key versions. A split piece's content
hash covers the ordered exact block fragments and gap bytes with a fixed
split discriminator. Use that hash for the piece's own Selection and D140
keys and vector reuse. Matching split inputs can carry paid results forward
under D56 and D119.

Hash the final same-section neighbour windows and their converter origins
for both Selection and D140.
For a window running from the exact start of its first block to the end of
its last, with whitespace-only gaps, try both the legacy ordered-block hash
and the exact split hash to match across a cut. Look up same-lineage
candidates by date-free key. If either occurrence has a split neighbour,
use the donor's saved grid and E0 artifacts to compare the actual consumed
neighbour slots. Require matching presence, verbatim Markdown including
gaps, ordered relative passage boundaries and canonical converter origins.
Equal block and origin hashes can hide a changed gap.

Before writing both keys, use the neighbour hashes of the first verified
match in [D140 order](effective_time_and_section_references_design.md#5-reuse-across-versions-text-origin-time).
With no eligible origin, use the hashes of the newest verified earlier
occurrence whose Selection key matches using the version's own date or
unknown date. Otherwise use the final grid's content hashes, including
the split hash for a changed split window. Break remaining ties by ascending
date-free key. Use the same neighbour hashes in both keys.

Apply the same check to the actual Selection donor, including empty
results, and to Claimify's matched target and D122 producers. Skip unequal
donors and apply D119 remapping to the verified donor. D122 uses the final
document order and the previous eight Selection producers, including frozen
empty results.

In Selection and Claimify, label each nonblank gap-only window by its exact
span even when a neighbour has clipped blocks. Keep Claimify origins tied
to target keeps and use exact spans and typed location elements for grounding.
[Legacy label fallback](../../src/rememberstack/core/source_passages.py#L124-L168) ·
[D122 ordering](../../src/rememberstack/workers/extraction_references.py#L40-L76).

### Rollout

Pause STRUCTURE claims and settle running attempts to fence admission to
legacy CHUNK. Keep the outgoing workers and binding until every legacy
CHUNK and EMBED_CHUNK job is terminal in the durable ledger across all
lanes. Include delayed jobs, leases, retries and CHUNK replays of already
written grids. Retire previous-release CHUNK, EMBED_CHUNK, Selection and
Claimify workers and section-entry readers. Enable split writes and
EMBED_CHUNK fit enforcement together, then resume admission.
[D160](packaging_distribution_design.md#61-migration-compatibility).

## 3. Terminal failure and the next version

Every nonblank input requires a finite, nonzero vector of the configured
dimension. The successful skip is `empty_body`. A failed local input fit
check or confirmed per-input provider size rejection is terminal
(`embedding.input_too_large`). Use `embedding.input_rejected` for other
confirmed deterministic input rejections. Isolate the input if necessary
and record its chunk ID, rendered input hash and binding. Throttling,
ambiguous 429s, transient outages and unusable responses take bounded
retries. Committed successful batches remain durable.

Keep the chunker version, work keys and persisted grids. Already
dead-lettered versions stay pipeline-failed. Changed bytes at the same
D55 source identity create a successor that can process independently.
Identical re-ingest returns the existing version in ordinary D55 ingest.
To submit the same bytes afresh, complete D159 forget and re-add them with
`allow_forgotten=true`.
D140's existing origin lookup can match saved chunks of a failed predecessor.
[Version registration](../../src/rememberstack/spine/document_catalog.py#L221-L250) ·
[D159 re-ingest](archive_and_forget_design.md#36-re-ingest-guard-and-adding-a-document-back).

## 4. Acceptance

Use frozen LoCoMo Full-v45 and BEAM E0 artifacts, settings and prior reuse state.
Start the candidate run with the target grid's chunk and search rows and
completed E1 jobs absent. Run CHUNK through packing and fit before embedding.
Use the saved target rows as the comparison reference. Replay UUIDs and
clocks at allocation. Match the complete ordered provider requests,
including routing and framing, and use only matching saved replies.
Fail if the benchmark binding would change any request, even when each
input fits alone. Require zero splits and zero model calls. Compare all
columns of `chunks` and `chunk_search`, including nulls, and compare D122
producer order and stamps. Record fixture digests and the replay command.

Test structural and scalar cuts, labelled gaps and blank separators, exact
reconstruction and provenance, and a header growing after a cut. Exercise
a gap-only target beside block-backed neighbours through Selection,
Claimify and grounding, the section-entry handle and D122's prior eight.
Rehearse the rollout with a legacy grid above the incoming ceiling, a CHUNK
retry after its grid write, and pending and backed-off EMBED_CHUNK work.
Verify a backed-off job holds the gate closed even with no active leases.
Verify legacy dispatch or committed-vector recovery completes before cutover
and fresh work uses the shared binding after cutover. Test typed terminal
rejection versus bounded throttling and invalid-vector retries.

With two dated versions, count calls and compare claim IDs for unchanged
whole and split ranges on both sides of an edit. Include three same-section
list items where an edit to the first causes D162 to restore the second
item's original whole-block window. Put a relative date in the third item.
Verify it keeps both keys and its origin date with zero new Selection calls.
Repeat with a restored two-paragraph neighbour whose old HTML comment gap
became equal-length whitespace. Keep block and converter-origin hashes
equal. Require the target to use the split neighbour hash in both keys,
take the new version's date and make a fresh Selection call even if the
old result has no cards. When candidate donors share a key, verify that
an unequal window cannot win either lookup. The Claimify key follows the
D122 producer window. Cover anchors and mark the existing D122
cross-section reuse gap as an expected failure.
Verify that identical ingest returns the failed version and an edited
successor publishes with the matching origin.
