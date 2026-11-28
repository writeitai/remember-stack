# Split oversized chunks before embedding (D162)

**Status** Proposed. [Analysis](../analysis/split_oversized_chunks_analysis.md).

## 1. Check the complete embedding input

CHUNK and EMBED_CHUNK share one qualified binding through handoff and retry,
matching P1's active model and dimensions. Pin the tokenizer and vocabulary,
model, route, fallbacks and request framing. Qualify full delivery on every
reachable route, counting the complete D80 render (normalized body, header
and separators) plus framing. Check per-input and aggregate token, item and
byte limits. One nonblank Unicode scalar must fit with the largest D80 header
and framing. Hosted Qwen3-Embedding-8B on OpenRouter `nebius/bf16` starts at
**16,384 tokens** for qualification. The benchmark binding requires at least
**22,528 tokens**. Keep it active through outstanding work. Use [D80's maintenance cutover](e1_embedding_input_policy.md#45-migration-triggers-and-vector-reuse)
for incompatible changes.

CHUNK checks new legacy candidates for input and singleton-request fit. Fitting
candidates keep their spans. Render against final order and count, rechecking
fit when a one-to-many split changes the D80 header.
EMBED_CHUNK verifies fit before reuse or dispatch. Preserve ordered batches
of inputs needing vectors, subdividing only to satisfy request limits.

## 2. Split only failing candidates

First cut at changes in nonblank text's extraction eligibility, using
E0 block eligibility inside blocks and D133 labels in gaps (unlabelled
gap text is eligible). Attach separator whitespace to the preceding piece
and leading whitespace to the first. Preserve the whitespace's converter
labels. For size cuts, prefer block seams, then recorded structural seams
such as turn, page or table-row boundaries, then sentence, line and
whitespace seams, then Unicode scalar boundaries. Take the legal seam
nearest the midpoint in the first available tier, breaking ties at the
earlier offset. Keep CRLF together and nonblank text on both sides.
Repeat the full fit check and cuts until every piece fits. Concatenating
the pieces reproduces the candidate's exact bytes.

Pieces use UUIDv4, consecutive document ordinals, exact half-open E0 spans
within the same section, and legacy whitespace token counts. Store the first
and last intersected block ordinals inclusive, or the preceding block in
both fields for a gap-only piece. Ordinals may repeat across pieces. Clip
source map and converter-origin ranges to each piece. Section references and
history use the chunk containing the first block's start.
[Current section-entry lookup](../../src/rememberstack/spine/section_history.py#L737-L752).

Use the existing key builders on the final grid. A piece's hash covers its ordered
exact block fragments and gap bytes under a fixed split discriminator.
Selection and Claimify give each nonblank gap-only window a label for its
exact span even beside a block-backed neighbour. Ground Claimify's target
keeps with exact spans and D80 typed locations. D122 takes the preceding
eight Selection producers in final document order, including empty results.
[Passage labels](../../src/rememberstack/core/source_passages.py#L124-L168) ·
[D122 producer order](../../src/rememberstack/workers/extraction_references.py#L56-L76).

## 3. Version-wide reuse gate

`document_versions.has_split_chunks` starts false, including for legacy rows.
CHUNK sets it if any piece in the final grid came from a split. Serialize
D140 resolution and the atomic grid and flag write for the same version.
Once true, it stays true across the version's generations and representations.

All four lookups require a false flag on both versions. Check the receiver
using any pending CHUNK grid too. Filter donors before ranking matches.

| Stage | Existing lookup to filter |
|---|---|
| E1 vector carry-forward | [`ChunkCatalog.carry_forward_sources` / `_SELECT_CARRY_FORWARD`](../../src/rememberstack/spine/chunk_catalog.py#L422-L440), called by [EMBED_CHUNK](../../src/rememberstack/workers/e1.py#L281-L289) |
| E2 Selection | [`SelectionCatalog.prior`](../../src/rememberstack/spine/selection_catalog.py#L102-L138), called by [`_reuse_prior_selection`](../../src/rememberstack/workers/e2.py#L655-L674) |
| E2 Claimify | [`ClaimCatalog.prior_extracted_chunk` / `_SELECT_PRIOR_EXTRACTED`](../../src/rememberstack/spine/claim_catalog.py#L425-L448), called by [`_reuse_prior_claimify`](../../src/rememberstack/workers/e2.py#L817-L841) |
| D140 text origin | [`ChunkCatalog.text_origin_matches` / `_SELECT_TEXT_ORIGIN_MATCHES`](../../src/rememberstack/spine/chunk_catalog.py#L332-L346), called by [`ChunkHandler._text_origins`](../../src/rememberstack/workers/e1.py#L193-L217) |

Every chunk of a split version runs fresh E1/E2 under existing skip rules
and takes its version's `source_modified_at` or `published_at` (`NULL` if undated).
Committed results replay by chunk ID. Unsplit versions keep today's keys,
ranking and remapping, with byte-identical rows for equal inputs, allocations
and eligible donors.

## 4. Rollout

Add the version flag and ledger replay eligibility compatibly under D160.
Pause STRUCTURE claims and settle running attempts. Fence CHUNK and
EMBED_CHUNK `dead_letter` to `pending` transitions in PostgreSQL across all
callers and lanes. Serialize the fence with in-flight replays. With the
outgoing workers and binding, drain all legacy jobs of both stages to
terminal status, including delayed jobs, retries, leases, saved-grid CHUNK
handoffs and replays committed before the fence. Mark remaining legacy E1
dead letters `replay_blocked` while fenced. PostgreSQL keeps them terminal.
Retire previous-release CHUNK, EMBED_CHUNK, Selection and Claimify workers
and section-entry readers. Turn on split writes and fit enforcement together,
then resume admission and eligible replay.
[Replay](orchestration_design.md#6-dead-letter-operations) ·
[D160](packaging_distribution_design.md#61-migration-compatibility).

## 5. Terminal failure

Each nonblank input needs a finite, nonzero vector of the configured
dimension. `empty_body` is the successful skip. Isolate failed inputs as
needed. Local fit failure and confirmed per-input size rejection are terminal
(`embedding.input_too_large`). Other confirmed deterministic rejections use
`embedding.input_rejected`. Record chunk ID, rendered hash and binding.
Throttling, ambiguous 429s, outages and unusable responses take bounded
retries. Committed batches remain durable.

Keep the chunker version, work keys and persisted grids. Pre-cutover E1
dead letters stay pipeline-failed. Changed bytes at the same D55 source
identity create a successor. Identical D55 ingest returns the existing
version. Submitting the same bytes afresh requires D159 forget and re-add
with `allow_forgotten=true`.
[Version registration](../../src/rememberstack/spine/document_catalog.py#L221-L250) ·
[D159 re-ingest](archive_and_forget_design.md#36-re-ingest-guard-and-adding-a-document-back).

## 6. Acceptance

Replay frozen LoCoMo Full-v45 and BEAM E0 artifacts, settings and prior reuse
state offline. Remove the target rows and completed work from the candidate
so CHUNK packs and checks fit. Replay UUIDs and clocks, and use saved replies
only for exact ordered requests including routing and framing. Require zero
splits and zero live model calls. Compare every column of `chunks` and
`chunk_search` against the saved target checkpoint, plus D122 producer order
and stamps. Record fixture digests and the replay command.

Test deterministic cuts, gap-only extraction and grounding, exact bytes and
provenance, header growth, section entry and D122 order. In a dated lineage,
count E1 and both E2 calls and check dates and claim IDs through unsplit →
split → unsplit. Exercise matching keys for a distant intact chunk, including
same-date E2 matches and empty results. The split target runs fresh. The next
version skips the split donor, taking an older eligible match or running fresh
at its own date. Retain D56 packing and D122 coverage for ordinary edits.

Rehearse cutover with an above-cap legacy grid, CHUNK retry after grid
write and EMBED_CHUNK in backoff. Test replay on both sides of the fence,
including from the outgoing CLI. Refusals preserve the row and announce no
work. Eligible post-cutover work can replay. Test typed rejection versus
bounded retries, identical re-ingest and an edited successor.
